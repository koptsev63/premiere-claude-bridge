import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync, existsSync, appendFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import vm from "node:vm";
import * as JR from "../lib/journal.js";
import * as O from "../lib/origin.js";
import * as AI from "../lib/aiactions.js";
import * as S from "../lib/share.js";
import * as J from "../lib/seq.js";
import { journalDiff } from "../lib/ops.js";
import { isReadOnlyJsx, prepareEvalCode } from "../lib/evalcode.js";

const clip = (name, start, end, i, o) => ({ name, path: "/m/" + name, start, end, inPoint: i, outPoint: o, duration: end - start });
const seq = (clips, extra = {}) => ({ name: "S", sequenceID: "sid", fps: 25, width: 1920, height: 1080, markers: 0, end: 20,
  video: [{ kind: "video", index: 0, clipCount: clips.length, clips }], audio: [], ...extra });
const snap = (t, clips, extra) => ({ t, project: "x.prproj", sequences: [seq(clips, extra)] });
const A = clip("a.mp4", 0, 4, 10, 14), B = clip("b.mp4", 4, 10, 20, 26), C = clip("c.mp4", 10, 13, 30, 33);
const T = (m, s = 0) => `2026-11-22T10:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.000Z`;
const SINCE = T(0);

function effectsOf(before, after) {
  const d = JR.diffSnapshots({ sequences: [seq(before)] }, { sequences: [seq(after)] });
  return d.sequences.flatMap((s) => s.events.map((e) => ({ sequence: s.sequence, sequenceID: s.sequenceID, ...e })));
}
const action = (id, m, effects, extra = {}) => ({ v: 1, id, t_start: T(m), t: T(m, 1), tool: "pr_eval_jsx", scope: "structure", channel: "mcp", client: "c", model: "unknown", ok: true, sequence: null, sequenceID: null, effects, ...extra });
const rowsOf = (before, after, m1 = 1, m2 = 10) => JR.eventRows(JR.diffSnapshots(snap(T(m1), before), snap(T(m2), after)));
const attr = (rows, actions, opts = { since: SINCE }) => O.attributeEvents(rows, actions, opts);

function tempRoot() { return mkdtempSync(join(tmpdir(), "origin-")); }

test("an edit equal to a logged AI call is via_bridge_ai; another clip in the same window is not_via_bridge", () => {
  const aiTrim = [A, clip("b.mp4", 4.4, 10, 20.4, 26), C];
  const both = [A, clip("b.mp4", 4.4, 10, 20.4, 26), clip("c.mp4", 10, 12, 30, 32)];
  const rows = attr(rowsOf([A, B, C], both), [action("a1", 5, effectsOf([A, B, C], aiTrim))]);
  const by = Object.fromEntries(rows.map((r) => [r.name, r]));
  assert.equal(by["b.mp4"].origin, "via_bridge_ai");
  assert.deepEqual(by["b.mp4"].action_ids, ["a1"]);
  assert.equal(by["c.mp4"].origin, "not_via_bridge");
});

test("the same clip edited by an AI call and by something else: unresolved, never a guess", () => {
  const aiTrim = [A, clip("b.mp4", 4.4, 10, 20.4, 26), C];
  const thenMore = [A, clip("b.mp4", 4.8, 10, 20.8, 26), C];
  const [r] = attr(rowsOf([A, B, C], thenMore), [action("a1", 5, effectsOf([A, B, C], aiTrim))]);
  assert.equal(r.origin, "unresolved");
  assert.equal(r.origin_reason, "mixed_with_ai_action");
});

test("two AI calls on one clip add up to one net edit", () => {
  const s1 = [A, clip("b.mp4", 4.4, 10, 20.4, 26), C], s2 = [A, clip("b.mp4", 4.8, 10, 20.8, 26), C];
  const [r] = attr(rowsOf([A, B, C], s2), [action("a1", 3, effectsOf([A, B, C], s1)), action("a2", 5, effectsOf(s1, s2))]);
  assert.equal(r.origin, "via_bridge_ai");
  assert.deepEqual(r.action_ids.sort(), ["a1", "a2"]);
});

test("an AI call whose effect is unknown makes edits in its window unresolved", () => {
  const next = [A, clip("b.mp4", 4.4, 10, 20.4, 26), C];
  const [r] = attr(rowsOf([A, B, C], next), [action("a1", 5, null)]);
  assert.equal(r.origin, "unresolved");
  assert.equal(r.origin_reason, "ai_action_effect_unknown");
  const [late] = attr(rowsOf([A, B, C], next, 30, 40), [action("a1", 5, null)]);
  assert.equal(late.origin, "not_via_bridge", "outside the window of the call");
});

test("a call that ended before the starting snapshot is already in it and does not touch later edits", () => {
  const next = [A, B, clip("c.mp4", 10, 12, 30, 32)];
  const placed = action("setup", 0, effectsOf([A, B], [A, B, C]), { t_start: T(0, 58), t: T(0, 59) });
  assert.equal(attr(rowsOf([A, B, C], next), [placed])[0].origin, "not_via_bridge");
});

test("an autosave time is cut to whole seconds: a call in that last second still counts", () => {
  const next = [A, clip("b.mp4", 4.4, 10, 20.4, 26), C];
  const eff = effectsOf([A, B, C], next);
  const inside = action("a", 10, eff, { t_start: "2026-11-22T10:10:00.400Z", t: "2026-11-22T10:10:00.700Z" });
  assert.equal(attr(rowsOf([A, B, C], next), [inside])[0].origin, "via_bridge_ai");
  const live = rowsOf([A, B, C], next).map((r) => ({ ...r, t: "2026-11-22T10:10:00.123Z" }));
  assert.equal(attr(live, [inside])[0].origin, "not_via_bridge", "a live snapshot has an exact time");
});

test("a pk.js edit has an unknown caller: unresolved, scoped to its sequence", () => {
  const next = [A, clip("b.mp4", 4.4, 10, 20.4, 26), C];
  const cli = (sequence) => action("p1", 5, null, { channel: "pk_cli", caller: "unknown", tool: "pk eval", sequence });
  assert.equal(attr(rowsOf([A, B, C], next), [cli(null)])[0].origin_reason, "cli_caller_unknown");
  assert.equal(attr(rowsOf([A, B, C], next), [cli("S")])[0].origin_reason, "cli_caller_unknown");
  assert.equal(attr(rowsOf([A, B, C], next), [cli("other")])[0].origin, "not_via_bridge");
});

test("markers: a chain of AI calls matches, an extra marker from elsewhere does not", () => {
  const row = (from, to) => ({ t: T(10), from, from_t: T(1), sequence: "S", sequenceID: "sid", type: "markers", kind: "sequence", to });
  const m = (id, min, from, to) => action(id, min, [{ sequence: "S", sequenceID: "sid", type: "markers", kind: "sequence", from, to }]);
  assert.equal(attr([row(0, 5)], [m("a", 3, 0, 3), m("b", 5, 3, 5)])[0].origin, "via_bridge_ai");
  const mixed = attr([row(0, 6)], [m("a", 3, 0, 3), m("b", 5, 3, 5)])[0];
  assert.equal(mixed.origin, "unresolved"); assert.equal(mixed.origin_reason, "mixed_with_ai_action");
});

test("old journal: reads without errors, and nothing is called not_via_bridge", () => {
  const root = tempRoot();
  try {
    const dir = join(root, "old");
    mkdirSync(join(dir, "snapshots"), { recursive: true });
    writeFileSync(join(dir, "snapshots", "20260925T110000Z-aaaa.json"), JSON.stringify({ ...snap("2026-09-25T11:00:00.000Z", [A, B, C]), hash: "aaaa" }));
    writeFileSync(join(dir, "events.jsonl"), [
      { t: "2026-09-25T12:58:42.000Z", from: "2026-09-25T11:19:18.000Z", sequence: "S", sequenceID: "sid", type: "sequence_added", kind: "sequence", clips: 20, source: "autosave" },
      { t: "2026-09-25T13:00:00.000Z", sequence: "S", type: "markers", kind: "sequence", from: 1, to: 2 },
    ].map((e) => JSON.stringify(e)).join("\n") + "\n");
    const r = JR.report(dir);
    assert.equal(r.origin.counts.unresolved, 2);
    assert.equal(r.origin.counts.not_via_bridge, 0);
    assert.deepEqual(r.origin.unresolvedReasons, { before_tracking: 2 });
    assert.match(JR.reportMarkdown(r), /Origin of decisions/);
    assert.ok(!existsSync(join(dir, O.ACTIONS_FILE)), "reading a report creates nothing");
    // an edit from before the start stays unresolved even once tracking is on
    O.markTrackingStarted(root, new Date("2026-11-22T10:00:00Z"));
    assert.equal(JR.report(dir).origin.counts.not_via_bridge, 0);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("a window that starts before tracking stays unresolved; a Resolve journal is never not_via_bridge", () => {
  const next = [A, clip("b.mp4", 4.4, 10, 20.4, 26), C];
  assert.equal(attr(rowsOf([A, B, C], next, 1, 10), [], { since: T(5) })[0].origin_reason, "before_tracking");
  assert.equal(O.isTrackedHost("/j/film.resolve"), false);
  assert.equal(attr(rowsOf([A, B, C], next), [], { since: SINCE, trackedHost: false })[0].origin_reason, "host_not_tracked");
});

test("there is no value that claims a person made the edit", () => {
  assert.deepEqual(O.ORIGINS, ["via_bridge_ai", "not_via_bridge", "unresolved"]);
  assert.match(O.ORIGIN_NOTE, /does not prove a person/);
});

test("fate of an AI edit: kept (even if rippled), changed, removed", () => {
  const row = { type: "trim_head", kind: "video", track: 0, sequence: "S", sequenceID: "sid", name: "b.mp4", path: "/m/b.mp4", start: 4.4, end: 10, inPoint: 20.4, outPoint: 26, fromStart: 4, fromEnd: 10, fromIn: 20, fromOut: 26 };
  assert.equal(O.fateOf(row, snap(T(20), [A, clip("b.mp4", 4.4, 10, 20.4, 26), C])), "kept");
  assert.equal(O.fateOf(row, snap(T(20), [A, clip("b.mp4", 7, 12.6, 20.4, 26), C])), "kept", "only shifted by a ripple");
  assert.equal(O.fateOf(row, snap(T(20), [A, clip("b.mp4", 4.4, 9, 20.4, 25), C])), "changed");
  assert.equal(O.fateOf(row, snap(T(20), [A, clip("b.mp4", 4.4, 9.8, 20.4, 26), C])), "changed", "same source window, shorter on the timeline");
  assert.equal(O.fateOf(row, snap(T(20), [A, C])), "removed");
  assert.equal(O.fateOf(row, { sequences: [] }), "removed");
  const gone = { type: "removed", kind: "video", track: 0, sequence: "S", sequenceID: "sid", name: "b.mp4", path: "/m/b.mp4", start: 4, end: 10, inPoint: 20, outPoint: 26 };
  assert.equal(O.fateOf(gone, snap(T(20), [A, C])), "kept", "the removal stands");
  assert.equal(O.fateOf(gone, snap(T(20), [A, B, C])), "changed", "the material came back");
});

test("report: shares and fate; a broken log line does not break it", () => {
  const root = tempRoot();
  try {
    O.markTrackingStarted(root, new Date(SINCE));
    const dir = join(root, "x");
    const ai = [A, clip("b.mp4", 4.4, 10, 20.4, 26), C];
    const final = [A, clip("b.mp4", 4.4, 10, 20.4, 26), clip("c.mp4", 10, 12, 30, 32)];
    JR.record(dir, snap(T(1), [A, B, C]));
    O.appendAction(dir, action("a1", 5, effectsOf([A, B, C], ai)));
    appendFileSync(join(dir, O.ACTIONS_FILE), "{broken\n");
    JR.record(dir, snap(T(10), final));
    const r = JR.report(dir);
    assert.deepEqual(r.origin.counts, { via_bridge_ai: 1, not_via_bridge: 0, unresolved: 1 }, "a damaged line may hide a call, so the other edit is not called not_via_bridge");
    assert.deepEqual(r.origin.unresolvedReasons, { action_log_damaged: 1 });
    assert.equal(r.origin.shares.via_bridge_ai, 50);
    assert.equal(r.origin.aiFate.kept, 1);
    assert.equal(r.origin.actions.unreadableLines, 1);
    const md = JR.reportMarkdown(r);
    assert.match(md, /via the bridge, by an AI call: 1 \(50%\)/);
    assert.match(md, /does not prove a person/);
    const d = journalDiff({ dir });
    assert.deepEqual(d.sequences[0].events.map((e) => e.origin).sort(), ["unresolved", "via_bridge_ai"]);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("privacy: the journal file and the outgoing payload are the same with and without the action log", () => {
  const root = tempRoot(), home = tempRoot();
  try {
    const dir = join(root, "Film");
    JR.record(dir, snap(T(1), [A, B, C]));
    JR.record(dir, snap(T(10), [A, clip("b.mp4", 4.4, 10, 20.4, 26), C]));
    const state = { installId: "i", salt: "fixed-salt", sharingSince: SINCE, sent: {} };
    const journalBefore = readFileSync(join(dir, "events.jsonl"), "utf8");
    const without = S.collect(state, { root });
    O.markTrackingStarted(root, new Date(SINCE));
    O.appendAction(dir, action("a1", 5, effectsOf([A, B, C], [A, clip("b.mp4", 4.4, 10, 20.4, 26), C]), { client: "secret-client", model: "secret-model" }));
    O.appendUnrouted(root, action("a2", 6, null));
    JR.report(dir); JR.reportMarkdown(JR.report(dir)); journalDiff({ dir });
    assert.equal(readFileSync(join(dir, "events.jsonl"), "utf8"), journalBefore, "reports never write origin into events.jsonl");
    const withLog = S.collect(state, { root });
    assert.deepEqual(withLog, without);
    const wire = JSON.stringify(withLog);
    for (const k of O.LOCAL_ONLY_KEYS) assert.ok(!wire.includes(`"${k}"`), `${k} must not leave`);
    assert.ok(!wire.includes("secret-client") && !wire.includes("secret-model") && !wire.includes("via_bridge"));
  } finally { rmSync(root, { recursive: true, force: true }); rmSync(home, { recursive: true, force: true }); }
});

test("privacy: even a row that carries origin fields loses them before sending", () => {
  const out = S.anonymize({ type: "move", origin: "via_bridge_ai", origin_reason: "x", action_ids: ["a"], fate: "kept", client: "c", model: "m", channel: "mcp", caller: "unknown", effects: [{ a: 1 }] }, "salt");
  assert.deepEqual(out, { type: "move" });
});

// A fake Premiere: answers the two read scripts from a state the fake tool changes.
function fakeRun(state, { failReads = false } = {}) {
  return async (body) => {
    if (failReads) throw new Error("Panel request timed out");
    if (body.includes("__bstRead(")) return JSON.parse(JSON.stringify({ project: "x.prproj", active: "sid", complete: true, sequences: [seq(state.clips, { markers: state.markers })] }));
    return { project: "x.prproj", sequence: "S", sequenceID: "sid", markers: state.markers };
  };
}
const logged = (root) => readFileSync(join(root, "x", O.ACTIONS_FILE), "utf8").trim().split("\n").map((l) => JSON.parse(l));

test("a tool call logs time, tool, client, unknown model and the exact clips it changed", async () => {
  const root = tempRoot();
  try {
    const state = { clips: [A, B, C], markers: 0 };
    const answer = { content: [{ type: "text", text: "done" }] };
    const out = await AI.trackTool("pr_make_sequence", { name: "S", file: "/m/x" }, { run: fakeRun(state), root, clientInfo: () => ({ name: "claude-code", version: "9" }) }, async () => { state.clips = [A, clip("b.mp4", 4.4, 10, 20.4, 26), C]; return answer; });
    assert.equal(out, answer, "the tool's answer is untouched");
    const [a] = logged(root);
    assert.equal(a.tool, "pr_make_sequence"); assert.equal(a.client, "claude-code"); assert.equal(a.model, "unknown");
    assert.deepEqual(a.clips, ["b.mp4"]); assert.deepEqual(a.sequences, ["S"]);
    assert.equal(a.effects[0].type, "trim_head");
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("a call that changed nothing logs no effects; a marker tool logs its count change; motion is logged as not visible", async () => {
  const root = tempRoot();
  try {
    const state = { clips: [A, B, C], markers: 2 };
    const opts = { run: fakeRun(state), root, clientInfo: () => null };
    await AI.trackTool("pr_make_sequence", {}, opts, async () => ({ content: [] }));
    await AI.trackTool("pr_add_markers", { seq: "S" }, opts, async () => { state.markers = 5; return { content: [] }; });
    await AI.trackTool("pr_set_motion", { seq: "S", track: 0, clip: 1 }, opts, async () => ({ content: [] }));
    const [ro, mk, mo] = logged(root);
    assert.deepEqual(ro.effects, []); assert.equal(ro.client, "unknown");
    assert.deepEqual([mk.effects[0].from, mk.effects[0].to], [2, 5]);
    assert.deepEqual(mo.effects, []); assert.equal(mo.scope, "appearance"); assert.deepEqual(mo.target, { track: 0, clip: 1 });
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("a tool that is not an edit is not logged and costs Premiere nothing", async () => {
  const root = tempRoot();
  try {
    let reads = 0;
    await AI.trackTool("pr_list_timeline", {}, { run: async () => { reads++; return {}; }, root }, async () => ({ content: [] }));
    assert.equal(reads, 0);
    assert.ok(!existsSync(join(root, "x")));
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("a call that timed out may still land: effect unknown, for a while after it", async () => {
  const root = tempRoot();
  try {
    const state = { clips: [A, B, C], markers: 0 };
    const res = { isError: true, content: [{ type: "text", text: "Panel request timed out after 60000ms" }] };
    assert.equal(await AI.trackTool("pr_make_sequence", {}, { run: fakeRun(state), root }, async () => res), res);
    const [a] = logged(root);
    assert.equal(a.effects, null); assert.equal(a.ok, false); assert.equal(a.hung, true);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("a call that failed halfway logs the part it did", async () => {
  const root = tempRoot();
  try {
    const state = { clips: [A, B, C], markers: 0 };
    await AI.trackTool("pr_build_sequences", {}, { run: fakeRun(state), root }, async () => { state.clips = [A, B]; return { isError: true, content: [{ type: "text", text: "build finished with errors" }] }; });
    const [a] = logged(root);
    assert.equal(a.ok, false); assert.equal(a.effects[0].type, "removed"); assert.equal(a.hung, undefined);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("if the log cannot read the timeline the tool still runs, and a thrown error still reaches the caller", async () => {
  const root = tempRoot();
  try {
    const opts = { run: fakeRun({}, { failReads: true }), root };
    const res = { content: [{ type: "text", text: "ok" }] };
    assert.equal(await AI.trackTool("pr_make_sequence", {}, opts, async () => res), res);
    const line = JSON.parse(readFileSync(join(root, O.UNROUTED_FILE), "utf8").trim());
    assert.equal(line.effects, null);
    await assert.rejects(AI.trackTool("pr_make_sequence", {}, opts, async () => { throw new Error("boom"); }), /boom/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("no panel at all: nothing reached Premiere, nothing is logged", async () => {
  const root = tempRoot();
  try {
    const run = async () => { throw new Error("Premiere panel not connected: no"); };
    await assert.rejects(AI.trackTool("pr_make_sequence", {}, { run, root }, async () => { throw new Error("Premiere panel not connected: no"); }));
    assert.ok(!existsSync(join(root, O.UNROUTED_FILE)));
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("pk.js: an edit is logged with an unknown caller, its answer untouched", async () => {
  const root = tempRoot();
  try {
    const out = await AI.trackCli("markers", { run: fakeRun({ clips: [], markers: 0 }), root }, async () => ({ created: 1 }));
    assert.deepEqual(out, { created: 1 });
    const [a] = logged(root);
    assert.equal(a.channel, "pk_cli"); assert.equal(a.caller, "unknown"); assert.equal(a.effects, null); assert.equal(a.sequence, null, "a pk.js call may switch or rename sequences");
    assert.ok(!existsSync(join(root, O.TRACKING_FILE)), "only the server starts tracking");
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("every MCP tool is classified, so an editing tool cannot go unlogged", () => {
  const src = readFileSync(new URL("../server.js", import.meta.url), "utf8");
  const names = [...src.matchAll(/^tool\(\s*"(pr_[a-z_]+)"/gm)].map((m) => m[1]);
  assert.ok(names.length >= 30);
  const missing = names.filter((n) => !AI.TRACKED_TOOLS[n] && !AI.NOT_TIMELINE_TOOLS.has(n));
  assert.deepEqual(missing, []);
  for (const n of [...Object.keys(AI.TRACKED_TOOLS), ...AI.NOT_TIMELINE_TOOLS]) assert.ok(names.includes(n), `${n} is not a server tool`);
  assert.match(src, /aiLog\.trackTool\(name, args/);
});

test("the light context script: ES3, ASCII, finds a sequence by Russian name or the active one", () => {
  const body = J.actionContextJsx({ seq: "Эпизод" });
  assert.match(body, /^[\x09\x0a\x0d\x20-\x7e]*$/);
  assert.doesNotMatch(body, /=>|\blet\b|\bconst\b|`/);
  const mk = (name, id, n) => ({ name, sequenceID: id, markers: { numMarkers: n } });
  const seqs = { numSequences: 2, 0: mk("Другой", "s1", 1), 1: mk("Эпизод", "s2", 7) };
  const call = (code) => { const ctx = { app: { project: { name: "x.prproj", sequences: seqs, activeSequence: seqs[0] } } }; vm.createContext(ctx); return JSON.parse(JSON.stringify(vm.runInContext(`(function(){${code}})()`, ctx))); };
  assert.deepEqual(call(body), { project: "x.prproj", sequence: "Эпизод", sequenceID: "s2", markers: 7 });
  assert.equal(call(J.actionContextJsx({})).sequence, "Другой");
});

// ---- the light read and the review fixes --------------------------------------

// A small Premiere for running the real ExtendScript text.
function fakePremiere(clips, { extraSeq = false, delay } = {}) {
  const T0 = (v) => ({ get seconds() { if (delay) delay(); return v; } });
  const item = (c) => ({ nodeId: "n:" + c.path, getMediaPath: () => c.path });
  const toClip = (c) => ({ name: c.name, projectItem: item(c), start: T0(c.start), end: T0(c.end), inPoint: T0(c.inPoint), outPoint: T0(c.outPoint) });
  const track = (rows) => { const t = { clips: { numItems: rows.length } }; rows.forEach((c, i) => { t.clips[i] = toClip(c); }); t.clips.setEnd = (i, v) => { t.clips[i].end = T0(v); }; return t; };
  const mk = (name, id, rows) => ({ name, sequenceID: id, timebase: "10160640000", markers: { numMarkers: 0 }, videoTracks: { numTracks: 1, 0: track(rows) }, audioTracks: { numTracks: 0 } });
  const a = mk("S", "sid", clips), sequences = { numSequences: 1, 0: a };
  if (extraSeq) { sequences[1] = mk("Other", "sid2", [C]); sequences.numSequences = 2; }
  const context = { app: { project: { name: "x.prproj", sequences, activeSequence: a } } };
  vm.createContext(context);
  return { context, call: async (body) => vm.runInContext(`(function(){ try { var __r = (function(){ ${body} })(); return (typeof __r === 'undefined') ? '' : (typeof __r === 'string' ? __r : JSON.stringify(__r)); } catch(e){ return JSON.stringify({error: e.toString(), line: e.line}); } })()`, context) };
}
const EDIT = "app.project.activeSequence.videoTracks[0].clips.setEnd(1, 9); return 'done';";

test("read-only check: what only reads passes, anything that may edit does not", () => {
  for (const code of [
    "app.project.name",
    "var s = app.project.activeSequence; return s.name;",
    "var out = []; var c = app.project.activeSequence.videoTracks[0].clips; for (var i = 0; i < c.numItems; i++) out.push({ n: c[i].name, s: c[i].start.seconds }); return JSON.stringify(out);",
    "function row(c) { return c.name + ' remove()'; } return row(app.project.activeSequence);",
    "(function(){ return app.project.path; })()",
  ]) assert.equal(isReadOnlyJsx(code), true, code);
  for (const code of [
    "app.project.activeSequence.videoTracks[0].clips[0].remove(false, false)",
    "var c = clips[0]; c.end = t;",
    "c['end'] = t", "c['remove']()", "var f = c.remove; f();", "var k = 'remove'; var f = c[k]; f.call(c);",
    "eval('x')", "new Function('x')()", "app.enableQE(); qe.project.getActiveSequence()", "app.project.save()",
    "app.project.activeSequence = s", "s.name = 'x'", "c.start.seconds += 1", "c.start.seconds++", "++c.x", "delete c.x",
    "function g(){} g = c.remove; g();", "$.evalFile('/tmp/x.jsx')", "app.project.importFiles(['/a'])", "", "'unterminated",
  ]) assert.equal(isReadOnlyJsx(code), false, code);
});

test("a read-only eval goes to Premiere unchanged and logs nothing", async () => {
  const root = tempRoot();
  try {
    const sent = [];
    const out = await AI.runEval("app.project.name", "BODY", { call: async (b) => { sent.push(b); return "x.prproj"; }, root });
    assert.equal(out, "x.prproj"); assert.deepEqual(sent, ["BODY"]);
    assert.ok(!existsSync(join(root, O.OPEN_DIR)) && !existsSync(join(root, "x")));
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("an editing eval: same answer, exact effect, its code is not stored, the unchanged sequence is not carried", async () => {
  const root = tempRoot();
  try {
    const P = fakePremiere([A, B, C], { extraSeq: true });
    let raw;
    const out = await AI.runEval(EDIT, prepareEvalCode(EDIT).code, { call: async (b) => (raw = await P.call(b)), root, clientInfo: () => ({ name: "claude-code", version: "9" }) });
    assert.equal(out, "done");
    assert.doesNotMatch(raw.replace(/"value":"done"/, ""), /Other/);
    const [a] = logged(root);
    assert.equal(a.tool, "pr_eval_jsx"); assert.equal(a.client, "claude-code"); assert.equal(a.ok, true);
    assert.equal(a.effects.length, 1); assert.equal(a.effects[0].name, "b.mp4"); assert.equal(a.effects[0].end, 9);
    assert.ok(!readFileSync(join(root, "x", O.ACTIONS_FILE), "utf8").includes("setEnd"), "the code of the call is not stored");
    assert.deepEqual(existsSync(join(root, O.OPEN_DIR)) ? readdirSync(join(root, O.OPEN_DIR)) : [], []);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("an eval that throws after a partial edit answers as before and logs the part it did", async () => {
  const root = tempRoot();
  try {
    const P = fakePremiere([A, B, C]);
    const code = "app.project.activeSequence.videoTracks[0].clips.setEnd(1, 9); throw new Error('boom');";
    const out = JSON.parse(await AI.runEval(code, prepareEvalCode(code).code, { call: P.call, root }));
    assert.match(out.error, /boom/);
    const [a] = logged(root);
    assert.equal(a.ok, false); assert.equal(a.effects[0].name, "b.mp4");
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("a project too big to read in time: the edit still runs, effect unknown, no more reads for a while", async () => {
  const root = tempRoot();
  try {
    const wait = () => { const e = Date.now() + 3; while (Date.now() < e); };
    const many = Array.from({ length: 2000 }, (_, i) => clip(`m${i}.mp4`, i, i + 1, 0, 1));
    const P = fakePremiere(many, { delay: wait });
    const sent = [];
    const call = async (b) => { sent.push(b); return P.call(b); };
    assert.equal(await AI.runEval(EDIT, prepareEvalCode(EDIT).code, { call, root }), "done");
    assert.equal(logged(root)[0].effects, null);
    assert.equal(await AI.runEval(EDIT, prepareEvalCode(EDIT).code, { call, root }), "done");
    assert.match(sent[1], /^var __bt = null/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("a call that never reported holds its edits unresolved until a later call is answered", () => {
  const root = tempRoot();
  try {
    const dir = join(root, "x");
    O.openAction(root, { v: 1, id: "a_open", t_start: T(5), tool: "pr_eval_jsx", scope: "structure", channel: "mcp" });
    const next = [A, clip("b.mp4", 4.4, 10, 20.4, 26), C];
    const read = () => attr(rowsOf([A, B, C], next, 20, 30), O.readActions(dir).actions)[0];
    assert.equal(read().origin_reason, "ai_action_effect_unknown");
    O.appendAction(dir, action("later", 10, []));
    assert.equal(read().origin, "not_via_bridge", "the later answer proves the script had finished");
    assert.equal(attr(rowsOf([A, B, C], next, 6, 9), O.readActions(dir).actions)[0].origin_reason, "ai_action_effect_unknown");
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("a damaged or malformed log line blocks not_via_bridge around it and crashes nothing", () => {
  const root = tempRoot();
  try {
    const dir = join(root, "x");
    mkdirSync(dir, { recursive: true });
    const good = (id, m) => JSON.stringify(action(id, m, []));
    writeFileSync(join(dir, O.ACTIONS_FILE), [good("a", 2), '{"cut', "null", '{"id":"e","t":"' + T(3) + '","effects":5}', '{"id":"f","t":"' + T(3) + '","effects":[null]}', good("b", 8)].join("\n") + "\n");
    const { actions, unreadable } = O.readActions(dir);
    assert.equal(unreadable, 4);
    const next = [A, clip("b.mp4", 4.4, 10, 20.4, 26), C];
    assert.equal(attr(rowsOf([A, B, C], next, 3, 5), actions)[0].origin_reason, "action_log_damaged");
    assert.equal(attr(rowsOf([A, B, C], next, 20, 30), actions)[0].origin, "not_via_bridge");
    writeFileSync(join(dir, "events.jsonl"), JSON.stringify(rowsOf([A, B, C], next, 3, 5)[0]) + "\n");
    assert.doesNotThrow(() => JR.reportMarkdown(JR.report(dir)));
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("two sequences with one name are told apart by id", () => {
  const next = [A, clip("b.mp4", 4.4, 10, 20.4, 26), C];
  const eff = effectsOf([A, B, C], next).map((e) => ({ ...e, sequenceID: "copy" }));
  assert.equal(attr(rowsOf([A, B, C], next), [action("a", 5, eff)])[0].origin, "not_via_bridge");
  const gone = (id) => ({ t: T(10), from_t: T(1), sequence: "S", sequenceID: id, type: "sequence_removed", kind: "sequence" });
  const del = action("d", 5, [{ sequence: "S", sequenceID: "s1", type: "sequence_removed", kind: "sequence" }]);
  assert.equal(attr([gone("s1")], [del])[0].origin, "via_bridge_ai");
  assert.equal(attr([gone("s2")], [del])[0].origin, "not_via_bridge");
});

test("another clip in the same slot with the same times is not the AI's edit", () => {
  const ai = clip("ai.mp4", 13, 15, 0, 2), other = clip("other.mp4", 13, 15, 0, 2);
  const [r] = attr(rowsOf([A, B, C], [A, B, C, other]), [action("a", 5, effectsOf([A, B, C], [A, B, C, ai]))]);
  assert.notEqual(r.origin, "via_bridge_ai");
});

test("snapshots compared newest first are unresolved", () => {
  const next = [A, clip("b.mp4", 4.4, 10, 20.4, 26), C];
  const rows = rowsOf([A, B, C], next, 10, 1);
  assert.equal(attr(rows, [])[0].origin_reason, "no_baseline");
});

test("a journal kept outside its project folder cannot say not_via_bridge", () => {
  const root = tempRoot();
  try {
    O.markTrackingStarted(root, new Date(T(0)));
    const dir = join(root, "elsewhere");
    JR.record(dir, snap(T(1), [A, B, C]), { source: "live" });
    const r = JR.record(dir, snap(T(2), [A, clip("b.mp4", 4.4, 10, 20.4, 26), C]), { source: "live" });
    const d = JR.attributeDiff(dir, r.diff, "x.prproj");
    assert.equal(d.sequences[0].events[0].origin, "unresolved");
    assert.equal(JR.report(dir).origin.counts.not_via_bridge, 0);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("a marker tool during which the active sequence changed claims no sequence", async () => {
  const root = tempRoot();
  try {
    let n = 0;
    const run = async () => ({ project: "x.prproj", sequence: n++ ? "S2" : "S1", sequenceID: n > 1 ? "id2" : "id1", markers: 1 });
    await AI.trackTool("pr_add_marker", {}, { run, root }, async () => ({ content: [] }));
    const [a] = logged(root);
    assert.equal(a.effects, null); assert.equal(a.sequence, null); assert.equal(a.sequenceID, null);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("a tool of several steps does not claim an edit made meanwhile in another sequence", async () => {
  const root = tempRoot();
  try {
    const base = (clips) => ({ project: "x.prproj", active: "sid", complete: true, sequences: [seq(clips), { ...seq([C]), name: "Other", sequenceID: "sid2" }] });
    let after = false;
    const run = async () => JSON.parse(JSON.stringify(after ? { ...base([A, B]), sequences: [seq([A, B]), { ...seq([clip("c.mp4", 10, 12, 30, 32)]), name: "Other", sequenceID: "sid2" }] } : base([A, B, C])));
    await AI.trackTool("pr_make_sequence", { name: "S" }, { run, root }, async () => { after = true; return { content: [] }; });
    const [a] = logged(root);
    assert.deepEqual(a.sequences, ["S"]);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("fate is one to one and follows what the edit was", () => {
  const latest = (clips, extra) => ({ sequences: [seq(clips, extra)] });
  const row = (before, after) => rowsOf(before, after)[0];
  // a move that was undone
  const moved = clip("b.mp4", 5, 11, 20, 26);
  assert.equal(O.fateOf(row([A, B], [A, moved]), latest([A, B])), "changed");
  assert.equal(O.fateOf(row([A, B], [A, moved]), latest([A, moved])), "kept");
  // two copies of one fragment: only one can be the AI's clip
  const claimed = new Set();
  const copy = clip("b.mp4", 20, 26, 20, 26);
  assert.equal(O.fateOf(row([A], [A, B]), latest([A, B]), claimed), "kept");
  assert.equal(O.fateOf(row([A], [A, copy]), latest([A, B]), claimed), "changed");
  // an AI removal stands even if another take overlaps the source elsewhere
  const removed = rowsOf([A, B], [A])[0];
  assert.equal(O.fateOf(removed, latest([A, clip("b.mp4", 30, 34, 21, 25)])), "kept");
  assert.equal(O.fateOf(removed, latest([A, B])), "changed");
  // a sequence replaced by another with the same name
  assert.equal(O.fateOf({ type: "sequence_added", kind: "sequence", sequence: "S", sequenceID: "gone" }, latest([A])), "removed");
  // a split with one part gone
  const whole = clip("w.mp4", 0, 10, 0, 10), p1 = clip("w.mp4", 0, 4, 0, 4), p2 = clip("w.mp4", 4, 10, 4, 10);
  const split = rowsOf([whole], [p1, p2]).find((r) => r.type === "split");
  assert.equal(O.fateOf(split, latest([p1, p2])), "kept");
  assert.equal(O.fateOf(split, latest([p1])), "changed");
});

test("while a bridge session of older code runs, an unlogged edit is unresolved", async () => {
  const root = tempRoot();
  try {
    const dir = join(root, "x");
    const now = Date.parse(T(5));
    const running = [{ pid: 1, started: Date.parse(T(0)) }, { pid: 2, started: Date.parse(T(1)) }, { pid: 3, started: now - 2000 }];
    await AI.registerServer(root, { list: async () => running, pid: 2 });
    const rows = await AI.noteOldSessions(root, { list: async () => running, now: () => new Date(now), pid: 2 });
    assert.deepEqual(rows.map((r) => r.pid), [1], "this version and a just-started process are not counted");
    const next = [A, clip("b.mp4", 4.4, 10, 20.4, 26), C];
    const ctx = () => ({ since: SINCE, oldSessions: O.originContext(dir).oldSessions });
    assert.equal(attr(rowsOf([A, B, C], next, 6, 9), [], ctx())[0].origin_reason, "older_bridge_session");
    await AI.noteOldSessions(root, { list: async () => [running[1]], now: () => new Date(T(10)), pid: 2 });
    assert.equal(attr(rowsOf([A, B, C], next, 20, 30), [], ctx())[0].origin, "not_via_bridge", "after it closed");
    assert.equal(attr(rowsOf([A, B, C], next, 6, 9), [], ctx())[0].origin_reason, "older_bridge_session", "while it was open");
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("service folders in the journal root are not sent as projects", () => {
  const root = tempRoot();
  try {
    for (const d of O.SERVICE_DIRS) { mkdirSync(join(root, d), { recursive: true }); writeFileSync(join(root, d, "events.jsonl"), '{"t":"2026-11-22T10:00:00.000Z","type":"leak"}\n'); }
    assert.doesNotMatch(JSON.stringify(S.collect({ done: {} }, { root })), /leak/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
