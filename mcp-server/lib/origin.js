// Where a journalled edit came from, as far as the bridge can know.
//
// via_bridge_ai   the edit equals what a logged AI tool call did to the timeline
// not_via_bridge  tracking was on and no logged call touched it. This is NOT
//                 "a person did it": another AI tool outside the bridge lands here too
// unresolved      the bridge cannot tell, and origin_reason says why
//
// There is deliberately no "human" value: the bridge cannot observe who sits
// at the keyboard.
//
// Local only. The action log lives in its own file, never in events.jsonl,
// because share.js sends events.jsonl line by line. Origin is worked out when
// a report is read and is never written back into the journal.

import { appendFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, dirname, join } from "node:path";

export const ORIGINS = ["via_bridge_ai", "not_via_bridge", "unresolved"];
export const ACTIONS_FILE = "ai-actions.jsonl";
export const UNROUTED_FILE = ".unrouted-ai-actions.jsonl"; // the project could not be asked: counts for every project
export const TRACKING_FILE = ".ai-tracking.json";
// Keys that must never leave the machine, even if a row ever carries them.
export const LOCAL_ONLY_KEYS = ["origin", "origin_reason", "action_ids", "fate", "client", "client_version", "model", "channel", "caller", "effects"];

const EPS = 0.005; // seconds: both sides are rounded to 3 decimals, a frame is at least 8 ms
const WHOLE_SECOND_MS = 1000; // an autosave time is cut to whole seconds: the file may hold up to a second more

// ---- storage ---------------------------------------------------------------

// The moment this machine started logging. Written once and never moved:
// an edit whose window starts earlier cannot be called "not via the bridge".
export function markTrackingStarted(root, now = new Date()) {
  mkdirSync(root, { recursive: true });
  try { writeFileSync(join(root, TRACKING_FILE), JSON.stringify({ since: new Date(now).toISOString() }), { flag: "wx" }); }
  catch (e) { if (e.code !== "EEXIST") throw e; }
}

export function trackingSince(dir) {
  try { return JSON.parse(readFileSync(join(dirname(dir), TRACKING_FILE), "utf8")).since || null; }
  catch { return null; }
}

export function appendAction(dir, action) {
  mkdirSync(dir, { recursive: true });
  appendFileSync(join(dir, ACTIONS_FILE), JSON.stringify(action) + "\n");
}

export function appendUnrouted(root, action) {
  mkdirSync(root, { recursive: true });
  appendFileSync(join(root, UNROUTED_FILE), JSON.stringify(action) + "\n");
}

function readLines(file) {
  let text;
  try { text = readFileSync(file, "utf8"); } catch { return { rows: [], bad: 0 }; }
  const rows = [];
  let bad = 0;
  for (const line of text.split("\n")) {
    if (!line) continue;
    try { rows.push(JSON.parse(line)); } catch { bad++; } // a line cut by a crash must not break the report
  }
  return { rows, bad };
}

export function readActions(dir) {
  const own = readLines(join(dir, ACTIONS_FILE));
  const unrouted = readLines(join(dirname(dir), UNROUTED_FILE));
  return { actions: [...own.rows, ...unrouted.rows.map((a) => ({ ...a, effects: a.scope === "appearance" ? [] : null, sequence: null, sequenceID: null }))], unreadable: own.bad + unrouted.bad };
}

// ---- matching --------------------------------------------------------------

const num = (v) => (v === "" || v == null ? null : Number(v));
const same = (a, b) => (a == null || b == null ? a == b : Math.abs(a - b) <= EPS);
const sameTimes = (a, b) => !!a && !!b && same(a.start, b.start) && same(a.end, b.end) && same(a.in, b.in) && same(a.out, b.out);
const stateAfter = (e) => ({ track: e.track, start: num(e.start), end: num(e.end), in: num(e.inPoint), out: num(e.outPoint) });
const stateBefore = (e) => ({ track: e.fromTrack ?? e.track, start: num(e.fromStart), end: num(e.fromEnd), in: num(e.fromIn), out: num(e.fromOut) });

// A clip edit as a step from one state to another (null = not on the timeline).
function step(e) {
  if (e.type === "added") return { from: null, to: stateAfter(e) };
  if (e.type === "removed") return { from: stateAfter(e), to: null };
  if (e.fromStart !== undefined) return { from: stateBefore(e), to: stateAfter(e) };
  return null;
}

const CLIP_KINDS = new Set(["video", "audio"]);

// Several AI calls on one clip show up as one net edit between two snapshots:
// follow the logged steps from the edit's first state to its last.
function chain(target, fx) {
  const steps = fx.map((f) => ({ f, s: step(f) })).filter((x) => x.s);
  const seen = new Set();
  const walk = (cur) => {
    for (let i = 0; i < steps.length; i++) {
      if (seen.has(i)) continue;
      const { s } = steps[i];
      const starts = cur === null ? s.from === null : sameTimes(s.from, cur);
      if (!starts) continue;
      if (target.to === null && cur === target.from && s.from.track !== cur.track) continue; // a removal names its track
      seen.add(i);
      const done = target.to === null ? s.to === null : sameTimes(s.to, target.to) && s.to.track === target.to.track;
      if (done) return [steps[i].f];
      if (s.to === null) continue;
      const rest = walk(s.to);
      if (rest) return [steps[i].f, ...rest];
    }
    return null;
  };
  return walk(target.from);
}

function sameSplit(a, b) {
  const ca = a.cutsAt || [], cb = b.cutsAt || [];
  return a.type === "split" && b.type === "split" && a.track === b.track && same(num(a.start), num(b.start)) && same(num(a.end), num(b.end))
    && ca.length === cb.length && ca.every((v, i) => same(v, cb[i]));
}

function markerChain(row, fx) {
  const used = [];
  let cur = row.from;
  for (const f of fx) {
    if (f.from !== cur) continue;
    used.push(f);
    cur = f.to;
    if (cur === row.to) return used;
  }
  return null;
}

// The logged effects that fully explain this edit, or null.
function explain(row, fx) {
  if (row.type === "markers") return markerChain(row, fx.filter((f) => f.type === "markers"));
  if (row.type === "sequence_added") { const m = fx.filter((f) => f.type === row.type && f.clips === row.clips); return m.length ? m : null; }
  if (row.type === "sequence_removed") { const m = fx.filter((f) => f.type === row.type); return m.length ? m : null; }
  const clipFx = fx.filter((f) => f.kind === row.kind);
  if (row.type === "split") { const m = clipFx.filter((f) => sameSplit(f, row)); return m.length ? m : null; }
  const target = step(row);
  return target ? chain(target, clipFx) : null;
}

function overlaps(a, b) {
  return a.track === b.track && Math.min(a.end, b.end) - Math.max(a.start, b.start) > EPS;
}

function sourceOverlaps(a, b) {
  if (a.in == null || b.in == null) return false;
  return Math.min(a.out, b.out) - Math.max(a.in, b.in) > EPS;
}

function statesOf(e) {
  const s = step(e);
  if (s) return [s.from, s.to].filter(Boolean);
  return e.start === undefined ? [] : [stateAfter(e)];
}

// The same clip was touched by a logged call, but the edit is not fully explained by it.
function touches(row, fx) {
  if (!CLIP_KINDS.has(row.kind)) return fx.some((f) => f.kind === row.kind && (row.type !== "markers" || f.type === "markers"));
  const mine = statesOf(row);
  return fx.some((f) => {
    if (f.kind !== row.kind) return false;
    const samePath = (f.path || f.name) === (row.path || row.name);
    return statesOf(f).some((b) => mine.some((a) => overlaps(a, b) || (samePath && sourceOverlaps(a, b))));
  });
}

const baseline = (r) => (typeof r.from_t === "string" ? r.from_t : typeof r.from === "string" ? r.from : null);
const seqMatch = (ref, e) => !!ref && (ref === e.sequence || ref === e.sequenceID);

// A call that ended before the starting snapshot is already part of it.
function inWindow(a, fromMs, toMs, toIso) {
  const start = Date.parse(a.t_start || a.t), end = Date.parse(a.settle_until || a.t);
  const slack = /:\d{2}(\.000)?Z$/.test(toIso) ? WHOLE_SECOND_MS : 0;
  return end >= fromMs && start <= toMs + slack;
}

function attributeOne(row, actions, sinceMs, trackedHost) {
  const verdict = (origin, origin_reason = null, used = []) => ({ ...row, origin, origin_reason, action_ids: [...new Set(used)] });
  if (!trackedHost) return verdict("unresolved", "host_not_tracked");
  const base = baseline(row), fromMs = base ? Date.parse(base) : NaN, toMs = Date.parse(row.t);
  if (Number.isNaN(fromMs) || Number.isNaN(toMs)) return verdict("unresolved", sinceMs == null ? "before_tracking" : "no_baseline");
  if (sinceMs == null || fromMs < sinceMs) return verdict("unresolved", "before_tracking");

  const near = actions.filter((a) => inWindow(a, fromMs, toMs, row.t));
  const fx = [];
  for (const a of near) for (const e of a.effects || []) if (seqMatch(e.sequenceID, row) || seqMatch(e.sequence, row)) fx.push({ ...e, _action: a.id });
  const used = explain(row, fx);
  if (used) return verdict("via_bridge_ai", null, used.map((f) => f._action));

  const blind = near.filter((a) => a.effects == null && (!(a.sequence || a.sequenceID) || seqMatch(a.sequence, row) || seqMatch(a.sequenceID, row)));
  if (blind.length) {
    const reason = blind.some((a) => a.channel !== "pk_cli") ? "ai_action_effect_unknown" : "cli_caller_unknown";
    return verdict("unresolved", reason, blind.map((a) => a.id));
  }
  if (touches(row, fx)) return verdict("unresolved", "mixed_with_ai_action", fx.map((f) => f._action));
  return verdict("not_via_bridge");
}

// Rows with origin added. A copy: the journal itself is never changed.
export function attributeEvents(rows, actions, { since = null, trackedHost = true } = {}) {
  const sinceMs = since ? Date.parse(since) : null;
  const ordered = [...actions].sort((a, b) => String(a.t_start || a.t).localeCompare(String(b.t_start || b.t)));
  return rows.map((r) => attributeOne(r, ordered, sinceMs, trackedHost));
}

// A Resolve project's edits go through other adapters that do not log yet.
export const isTrackedHost = (dir) => !/\.resolve$/.test(basename(dir));

// ---- what became of an AI edit ---------------------------------------------
// Judged by the source fragment and the clip's length, so a later ripple that
// only shifts a clip along the timeline does not count as a change to it.

export function fateOf(row, latest) {
  if (!latest || row.type === "markers" || row.type === "sequence_removed") return null;
  const seqs = latest.sequences || [];
  const seq = seqs.find((q) => row.sequenceID && q.sequenceID === row.sequenceID) || seqs.find((q) => q.name === row.sequence);
  if (row.type === "sequence_added") return seq ? "kept" : "removed";
  if (!CLIP_KINDS.has(row.kind)) return null;
  if (!seq) return row.type === "removed" ? "kept" : "removed";
  const key = row.path || row.name;
  const clips = (seq[row.kind] || []).flatMap((t) => t.clips.map((c) => ({ ...c, track: t.index }))).filter((c) => (c.path || c.name) === key);
  const wanted = stateAfter(row);
  const asState = (c) => ({ track: c.track, start: num(c.start), end: num(c.end), in: num(c.inPoint), out: num(c.outPoint) });
  const hasSource = wanted.in != null;
  const length = (s) => (s.start == null || s.end == null ? null : s.end - s.start);
  const exact = clips.some((c) => { const s = asState(c); return hasSource ? same(s.in, wanted.in) && same(s.out, wanted.out) && same(length(s), length(wanted)) : same(s.start, wanted.start) && same(s.end, wanted.end); });
  const near = clips.some((c) => { const s = asState(c); return hasSource ? sourceOverlaps(s, wanted) : overlaps(s, wanted); });
  if (row.type === "removed") return exact || near ? "changed" : "kept"; // the removed material came back
  if (exact) return "kept";
  return near ? "changed" : "removed";
}

// ---- the report block ------------------------------------------------------

const pct = (n, of) => (of ? Math.round((n / of) * 1000) / 10 : 0);

export const ORIGIN_NOTE = "not_via_bridge means no logged bridge call touched the edit. It does not prove a person made it: another AI tool used outside the bridge lands there too. via_bridge_ai says how an edit reached the timeline, not who decided to keep it.";

export function originReport(dir, events, latest) {
  const { actions, unreadable } = readActions(dir);
  const since = trackingSince(dir);
  const trackedHost = isTrackedHost(dir);
  const rows = attributeEvents(events, actions, { since, trackedHost });
  const counts = Object.fromEntries(ORIGINS.map((o) => [o, 0]));
  const unresolvedReasons = {}, fate = { kept: 0, changed: 0, removed: 0, notApplicable: 0 };
  for (const r of rows) {
    counts[r.origin]++;
    if (r.origin === "unresolved") unresolvedReasons[r.origin_reason] = (unresolvedReasons[r.origin_reason] || 0) + 1;
    if (r.origin === "via_bridge_ai") { const f = fateOf(r, latest); fate[f || "notApplicable"]++; }
  }
  const byTool = {}, clients = {}, models = {};
  for (const a of actions) {
    byTool[a.tool] = (byTool[a.tool] || 0) + 1;
    clients[a.client || "unknown"] = (clients[a.client || "unknown"] || 0) + 1;
    models[a.model || "unknown"] = (models[a.model || "unknown"] || 0) + 1;
  }
  return {
    trackingSince: since, trackedHost, decisions: rows.length, counts,
    shares: Object.fromEntries(ORIGINS.map((o) => [o, pct(counts[o], rows.length)])),
    unresolvedReasons, aiFate: fate,
    actions: {
      logged: actions.length,
      changedTimeline: actions.filter((a) => (a.effects || []).length > 0).length,
      effectUnknown: actions.filter((a) => a.effects == null).length,
      notVisibleInDiff: actions.filter((a) => a.scope === "appearance").length,
      unreadableLines: unreadable, byTool, clients, models,
    },
    note: ORIGIN_NOTE,
  };
}

const REASON_WORDS = {
  before_tracking: "before tracking started",
  host_not_tracked: "editing host not tracked yet",
  no_baseline: "no starting point",
  mixed_with_ai_action: "same clip touched by an AI call and something else",
  ai_action_effect_unknown: "an AI call whose effect could not be read",
  cli_caller_unknown: "a pk.js command, caller unknown",
};

export function originMarkdown(o) {
  const L = ["## Origin of decisions"];
  if (!o.trackedHost) L.push("This editing host is not tracked yet, so its decisions stay unresolved.");
  else if (!o.trackingSince) L.push("Tracking had not started when these decisions were recorded, so they stay unresolved.");
  else L.push(`Tracking since ${o.trackingSince}.`);
  L.push(`- via the bridge, by an AI call: ${o.counts.via_bridge_ai} (${o.shares.via_bridge_ai}%)`);
  L.push(`- not via the bridge: ${o.counts.not_via_bridge} (${o.shares.not_via_bridge}%)`);
  L.push(`- unresolved: ${o.counts.unresolved} (${o.shares.unresolved}%)`);
  for (const [k, v] of Object.entries(o.unresolvedReasons).sort((a, b) => b[1] - a[1])) L.push(`  - ${REASON_WORDS[k] || k}: ${v}`);
  if (o.counts.via_bridge_ai) L.push(`- what became of AI edits: kept ${o.aiFate.kept}, changed later ${o.aiFate.changed}, removed ${o.aiFate.removed}${o.aiFate.notApplicable ? `, not applicable ${o.aiFate.notApplicable}` : ""}`);
  if (o.actions.logged) L.push(`- AI calls logged: ${o.actions.logged} (changed the timeline ${o.actions.changedTimeline}, effect unknown ${o.actions.effectUnknown}, motion or effects not visible in a diff ${o.actions.notVisibleInDiff})`);
  L.push("");
  L.push(`Note: ${o.note}`);
  return L.join("\n");
}
