// The log of what AI tool calls did to the timeline. A call that can change the
// edit is logged with the exact clips it changed rather than "something
// happened around this time". A failure here never reaches the tool: the call
// runs and answers as usual, and an effect that could not be read is logged as
// unknown (effects: null).
//
// Cost: a pr_eval_jsx that only reads touches nothing extra. One that may edit
// runs between two light reads inside a single script, so nothing else can land
// between them. On a project too big to read within the limit the call runs
// unread and its effect is logged as unknown.
//
// Local only: see lib/origin.js. The code of a pr_eval_jsx call is not stored.

import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdirSync, readdirSync, unlinkSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import { isReadOnlyJsx } from "./evalcode.js";
import * as J from "./seq.js";
import * as JR from "./journal.js";
import { appendAction, appendUnrouted, closeAction, markTrackingStarted as mark, openAction, readOldSessions, SERVERS_DIR, writeOldSessions } from "./origin.js";

// atomic     pr_eval_jsx: logged by runEval, inside one script
// structure  may change clips or sequences: read the timeline around it
// markers    changes the marker count: read just that
// appearance Motion and effects: real AI work, but a structural diff cannot see it
export const TRACKED_TOOLS = {
  pr_eval_jsx: "atomic",
  pr_import_captions: "structure",
  pr_make_sequence: "structure",
  pr_build_sequences: "structure",
  pr_delete_sequence: "structure",
  pr_add_marker: "markers",
  pr_add_markers: "markers",
  pr_set_motion: "appearance",
  pr_set_motion_keyframes: "appearance",
  pr_add_video_effect: "appearance",
};

// Everything else the server exposes. A new tool must be put in one of the two
// lists (a test checks), so an editing tool cannot go unlogged by accident.
export const NOT_TIMELINE_TOOLS = new Set([
  "pr_status", "pr_get_project_info", "pr_get_active_sequence", "pr_list_timeline", "pr_get_selected", "pr_get_playhead",
  "pr_set_playhead", "pr_export_ame", "pr_list_video_effects", "pr_ping", "pr_dialog_check", "pr_probe_media", "pr_import_media",
  "pr_list_markers", "pr_open_sequences", "pr_activate_sequence", "pr_get_timeline", "pr_get_inout", "pr_move_to_bin", "pr_list_bin",
  "pr_save_project", "pr_journal_snapshot", "pr_journal_report", "pr_share", "pr_update_bridge",
]);

const STATE_TIMEOUT_MS = 20_000;
const STATE_LIMIT_MS = 5_000; // a tool of several panel calls
const EVAL_READ_LIMIT_MS = 2_000; // one read inside a pr_eval_jsx: Premiere waits for it
const BIG_PROJECT_PAUSE_MS = 5 * 60 * 1000; // after a read over the limit, do not try again for a while
const CONTEXT_TIMEOUT_MS = 5_000;
const SCAN_EVERY_MS = 60_000;
const YOUNG_PROCESS_MS = 15_000; // a server this new may not have registered itself yet

const DEFAULT_ROOT = join(homedir(), ".premiere-bridge", "journal");

export const markTrackingStarted = (root = JR.JOURNAL_ROOT) => mark(root);

const newId = () => "a_" + randomUUID().slice(0, 12);
const quiet = async (fn) => { try { return await fn(); } catch { return null; } };

// ---- bridge sessions that run older code -------------------------------------
// A session opened before this version keeps running its old code and logs
// nothing. While one is alive, an edit with no logged call cannot be called
// "not via the bridge".

const execFileP = promisify(execFile);

export async function listBridgeServers() {
  const { stdout } = await execFileP("ps", ["-axo", "pid=,lstart=,command="], { maxBuffer: 8 * 1024 * 1024 });
  const out = [];
  for (const line of stdout.split("\n")) {
    const m = line.match(/^\s*(\d+)\s+(\w{3}\s+\w{3}\s+\d+\s+[\d:]+\s+\d{4})\s+(.*)$/);
    if (!m || !/\bnode\b.*mcp-server\/server\.js(\s|$)/.test(m[3])) continue;
    const started = Date.parse(m[2]);
    if (!Number.isNaN(started)) out.push({ pid: Number(m[1]), started });
  }
  return out;
}

const serverKey = (s) => `${s.pid}-${s.started}`;

export async function registerServer(root, { list = listBridgeServers, pid = process.pid } = {}) {
  const me = (await list()).find((s) => s.pid === pid);
  if (!me) return;
  mkdirSync(join(root, SERVERS_DIR), { recursive: true });
  writeFileSync(join(root, SERVERS_DIR, serverKey(me)), "");
}

export async function noteOldSessions(root, { list = listBridgeServers, now = () => new Date(), pid = process.pid } = {}) {
  const running = await list();
  const nowMs = now().getTime();
  let known;
  try { known = new Set(readdirSync(join(root, SERVERS_DIR))); } catch { known = new Set(); }
  const alive = new Set(running.map(serverKey));
  for (const k of known) if (!alive.has(k)) { try { unlinkSync(join(root, SERVERS_DIR, k)); } catch {} }
  const old = running.filter((s) => s.pid !== pid && !known.has(serverKey(s)) && nowMs - s.started >= YOUNG_PROCESS_MS);
  const rows = readOldSessions(root);
  const keyOf = (r) => `${r.pid}-${Date.parse(r.started)}`;
  const have = new Set(rows.map(keyOf));
  const seen = new Set(old.map(serverKey));
  let changed = false;
  for (const s of old) if (!have.has(serverKey(s))) { rows.push({ pid: s.pid, started: new Date(s.started).toISOString(), gone_by: null }); changed = true; }
  for (const r of rows) if (r.gone_by == null && !seen.has(keyOf(r))) { r.gone_by = new Date(nowMs).toISOString(); changed = true; }
  if (changed) writeOldSessions(root, rows);
  return rows;
}

const lastScan = new Map();
function scanSoon(root) {
  if (root !== DEFAULT_ROOT) return; // a journal kept elsewhere is a test or a copy
  const t = Date.now();
  if (t - (lastScan.get(root) || 0) < SCAN_EVERY_MS) return;
  lastScan.set(root, t);
  quiet(() => noteOldSessions(root));
}

// At server start: logging is on from here, and this process counts as logging.
export function startTracking(root = JR.JOURNAL_ROOT) {
  mark(root);
  if (root === DEFAULT_ROOT) quiet(async () => { await registerServer(root); await noteOldSessions(root); });
}

// ---- reading around a call ---------------------------------------------------

async function readState(run, scope, seq) {
  const structure = scope === "structure";
  const res = await run(structure ? J.timelineStateJsx({ limitMs: STATE_LIMIT_MS }) : J.actionContextJsx({ seq }), { timeoutMs: structure ? STATE_TIMEOUT_MS : CONTEXT_TIMEOUT_MS });
  if (!res || typeof res !== "object" || res.error) return null;
  return structure && !Array.isArray(res.sequences) ? null : res;
}

function structuralEffects(before, after) {
  const diff = JR.diffSnapshots(before, after);
  return diff.sequences.flatMap((s) => s.events.map((e) => ({ sequence: s.sequence, sequenceID: s.sequenceID, ...e })));
}

function markerEffects(before, after) {
  if (before.sequenceID !== after.sequenceID || before.markers == null || after.markers == null) return null;
  if (before.markers === after.markers) return [];
  return [{ sequence: after.sequence, sequenceID: after.sequenceID, type: "markers", kind: "sequence", from: before.markers, to: after.markers }];
}

// A tool of several panel calls leaves Premiere free between them. What changed
// in a sequence it was not asked to touch is somebody else's edit.
function onlyItsSequences(name, args, before, effects) {
  let targets = [];
  if (name === "pr_make_sequence" || name === "pr_delete_sequence") targets = [args?.name];
  else if (name === "pr_build_sequences") targets = (args?.spec?.sequences || []).map((s) => s.name);
  else if (name === "pr_import_captions") targets = [before?.active];
  targets = targets.filter(Boolean);
  if (!targets.length) return effects;
  return effects.filter((e) => targets.includes(e.sequence) || targets.includes(e.sequenceID));
}

function target(args) {
  const out = {};
  for (const k of ["track", "clip", "trackIndex", "clipIndex", "target"]) if (args?.[k] !== undefined) out[k] = args[k];
  return Object.keys(out).length ? out : undefined;
}

function writeAction(action, project, root) {
  if (project) appendAction(JR.journalDir(project, root), action);
  else appendUnrouted(root, action); // no project known: read back as "effect unknown" for all of them
}

const clipNames = (effects) => [...new Set((effects || []).filter((e) => e.kind === "video" || e.kind === "audio").map((e) => e.name).filter(Boolean))];
const seqNames = (effects) => [...new Set((effects || []).map((e) => e.sequence).filter(Boolean))];

const textOf = (result) => result?.content?.map((c) => c.text || "").join(" ") || "";
// The call never reached Premiere, so there is nothing to log.
const unreachable = (thrown, result) => /panel not connected|nothing is listening|no ".*" panel|debugger socket failed/i.test(thrown?.message || textOf(result));

// ---- pr_eval_jsx -------------------------------------------------------------

const bigProjectUntil = new Map(); // journal root -> time to try reading again

function envelope(raw) {
  if (typeof raw !== "string" || !raw.startsWith("{")) return null;
  try { const o = JSON.parse(raw); return o && o.__bridge_tracked === 1 ? o : null; } catch { return null; }
}

// Run prepared code and return the panel's raw answer, as the plain call would.
export async function runEval(source, prepared, { call, clientInfo, root = JR.JOURNAL_ROOT, now = () => new Date() }) {
  if (isReadOnlyJsx(source)) return call(prepared);

  const id = newId(), started = now();
  await quiet(() => openAction(root, { v: 1, id, t_start: started.toISOString(), tool: "pr_eval_jsx", scope: "structure", channel: "mcp" }));
  const read = now().getTime() >= (bigProjectUntil.get(root) || 0);
  let raw, thrown = null;
  try { raw = await call(J.trackedEvalJsx(prepared, { limitMs: EVAL_READ_LIMIT_MS, read })); } catch (e) { thrown = e; }
  const env = thrown ? null : envelope(raw);
  const neverRan = thrown ? unreachable(thrown) : !env && /^\{"error":"SyntaxError/.test(String(raw));

  if (!neverRan) await quiet(async () => {
    let effects = null;
    if (env?.overLimit) bigProjectUntil.set(root, now().getTime() + BIG_PROJECT_PAUSE_MS);
    if (env?.complete) effects = structuralEffects({ sequences: env.before }, { sequences: env.after });
    const ended = now();
    const info = await quiet(() => clientInfo?.());
    const failed = !!thrown || !env || !!env.threw;
    writeAction({
      v: 1, id, t_start: started.toISOString(), t: ended.toISOString(),
      ...(thrown ? { hung: true } : {}),
      tool: "pr_eval_jsx", scope: "structure", channel: "mcp", client: info?.name || "unknown", client_version: info?.version || "unknown",
      model: "unknown", // MCP does not tell the server which model is calling
      ok: !failed, project: env?.project || null, sequence: null, sequenceID: null,
      sequences: seqNames(effects), clips: clipNames(effects), effects,
    }, env?.project || null, root);
  });
  closeAction(root, id);
  scanSoon(root);

  if (thrown) throw thrown;
  return env ? env.value : raw;
}

// ---- every other tool --------------------------------------------------------

// Run one tool call and log what it did. Returns exactly what the handler returns.
export async function trackTool(name, args, { run, clientInfo, root = JR.JOURNAL_ROOT, now = () => new Date() }, handler) {
  const scope = TRACKED_TOOLS[name];
  if (!scope || scope === "atomic") return handler();
  const seq = typeof args?.seq === "string" ? args.seq : null;
  const id = newId(), started = now();
  if (scope !== "appearance") await quiet(() => openAction(root, { v: 1, id, t_start: started.toISOString(), tool: name, scope, channel: "mcp" }));
  const before = await quiet(() => readState(run, scope, seq));

  let result, thrown = null;
  try { result = await handler(); } catch (e) { thrown = e; }
  const failed = !!thrown || !!result?.isError;
  const timedOutCall = failed && /timed out|did not answer/i.test(thrown?.message || textOf(result));

  if (!(failed && !before && unreachable(thrown, result))) await quiet(async () => {
    let effects = null, project = before?.project || null, ctx = before, unscoped = false;
    if (scope === "appearance") {
      effects = []; // cannot change clips or their timing, whether it worked or not
    } else if (before && !timedOutCall) { // a failed call may have done part of its work: the diff shows which part
      const after = await quiet(() => readState(run, scope, seq));
      if (after) {
        if (scope === "structure") effects = onlyItsSequences(name, args, before, structuralEffects(before, after));
        else { effects = markerEffects(before, after); unscoped = before.sequenceID !== after.sequenceID; }
        ctx = after; project = after.project || project;
      }
    }
    if (!project) { const c = await quiet(() => readState(run, "markers", seq)); project = c?.project || null; ctx = ctx || c; }
    const ended = now();
    const info = await quiet(() => clientInfo?.());
    const single = scope === "structure" || unscoped ? null : ctx?.sequence || seq;
    writeAction({
      v: 1, id, t_start: started.toISOString(), t: ended.toISOString(),
      ...(failed && effects === null ? { hung: true } : {}),
      tool: name, scope, channel: "mcp", client: info?.name || "unknown", client_version: info?.version || "unknown",
      model: "unknown", // MCP does not tell the server which model is calling
      ok: !failed, project, sequence: single, sequenceID: scope === "structure" || unscoped ? null : ctx?.sequenceID || null,
      sequences: seqNames(effects), clips: clipNames(effects), target: target(args), effects,
    }, project, root);
  });
  closeAction(root, id);
  scanSoon(root);

  if (thrown) throw thrown;
  return result;
}

// pk.js can be run by a person or by an AI through a shell, and the bridge
// cannot tell which. Its edits are logged as "something happened here, caller
// unknown", so they stay unresolved instead of reading as "not via the bridge".
// A pk.js call is tied to no sequence: it may create, rename or switch them.
// It never starts tracking: only a running server of this version can vouch for that.
export async function trackCli(command, { run, root = JR.JOURNAL_ROOT, now = () => new Date() }, fn) {
  const id = newId(), started = now();
  await quiet(() => openAction(root, { v: 1, id, t_start: started.toISOString(), tool: "pk " + command, scope: "structure", channel: "pk_cli", caller: "unknown" }));
  const ctx = await quiet(() => readState(run, "markers", null));
  let out, thrown = null;
  try { out = await fn(); } catch (e) { thrown = e; }
  const failed = !!thrown || !!(out && (out.ok === false || out.error));
  if (!(failed && !ctx && unreachable(thrown, out))) await quiet(() => {
    const ended = now();
    writeAction({
      v: 1, id, t_start: started.toISOString(), t: ended.toISOString(),
      ...(failed ? { hung: true } : {}),
      tool: "pk " + command, scope: "structure", channel: "pk_cli", caller: "unknown", client: "unknown", client_version: "unknown", model: "unknown",
      ok: !failed, project: ctx?.project || null, sequence: null, sequenceID: null, sequences: [], clips: [], effects: null,
    }, ctx?.project || null, root);
  });
  closeAction(root, id);
  if (thrown) throw thrown;
  return out;
}

export const PK_EDIT_COMMANDS = new Set(["make-seq", "markers", "delete-seq", "build", "eval", "file"]);
