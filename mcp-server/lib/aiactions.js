// The log of what AI tool calls did to the timeline. Each call that can change
// the edit reads the timeline before and after itself, so the log holds the
// exact clips it changed rather than "something happened around this time".
// A failure here never reaches the tool: the call runs and answers as usual,
// and an effect that could not be read is logged as unknown (effects: null).
//
// Local only: see lib/origin.js. The code of a pr_eval_jsx call is not stored.

import { randomUUID } from "node:crypto";
import * as J from "./seq.js";
import * as JR from "./journal.js";
import { appendAction, appendUnrouted, markTrackingStarted as mark } from "./origin.js";

// structure  may change clips or sequences: read the whole timeline around it
// markers    changes the marker count: read just that
// appearance Motion and effects: real AI work, but a structural diff cannot see it
export const TRACKED_TOOLS = {
  pr_eval_jsx: "structure",
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

const SNAPSHOT_TIMEOUT_MS = 20_000;
const CONTEXT_TIMEOUT_MS = 5_000;
const SETTLE_MS = 5 * 60 * 1000; // a call that failed or timed out may still land in Premiere afterwards

export const markTrackingStarted = (root = JR.JOURNAL_ROOT) => mark(root);

async function readState(run, scope, seq) {
  const structure = scope === "structure";
  const res = await run(structure ? J.snapshotJsx({}) : J.actionContextJsx({ seq }), { timeoutMs: structure ? SNAPSHOT_TIMEOUT_MS : CONTEXT_TIMEOUT_MS });
  if (!res || typeof res !== "object" || res.error) return null;
  return structure && !Array.isArray(res.sequences) ? null : res;
}

const quiet = async (fn) => { try { return await fn(); } catch { return null; } };

function structuralEffects(before, after) {
  const diff = JR.diffSnapshots(before, after);
  return diff.sequences.flatMap((s) => s.events.map((e) => ({ sequence: s.sequence, sequenceID: s.sequenceID, ...e })));
}

function markerEffects(before, after) {
  if (before.sequenceID !== after.sequenceID || before.markers == null || after.markers == null) return null;
  if (before.markers === after.markers) return [];
  return [{ sequence: after.sequence, sequenceID: after.sequenceID, type: "markers", kind: "sequence", from: before.markers, to: after.markers }];
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

const textOf = (result) => result?.content?.map((c) => c.text || "").join(" ") || "";
const timedOut = (thrown, result) => /timed out|did not answer/i.test(thrown?.message || textOf(result));
// The call never reached Premiere, so there is nothing to log.
const unreachable = (thrown, result) => /panel not connected|nothing is listening|no ".*" panel|debugger socket failed/i.test(thrown?.message || textOf(result));

// Run one tool call and log what it did. Returns exactly what the handler returns.
export async function trackTool(name, args, { run, clientInfo, root = JR.JOURNAL_ROOT, now = () => new Date() }, handler) {
  const scope = TRACKED_TOOLS[name];
  if (!scope) return handler();
  const seq = typeof args?.seq === "string" ? args.seq : null;
  const started = now();
  const before = await quiet(() => readState(run, scope, seq));

  let result, thrown = null;
  try { result = await handler(); } catch (e) { thrown = e; }
  const failed = !!thrown || !!result?.isError;
  const hung = failed && timedOut(thrown, result);

  if (!(failed && !before && unreachable(thrown, result))) await quiet(async () => {
    let effects = null, project = before?.project || null, ctx = before;
    if (scope === "appearance") {
      effects = []; // cannot change clips or their timing, whether it worked or not
    } else if (before && !hung) { // a failed call may have done part of its work: the diff shows which part
      const after = await quiet(() => readState(run, scope, seq));
      if (after) { effects = scope === "structure" ? structuralEffects(before, after) : markerEffects(before, after); ctx = after; project = after.project || project; }
    }
    if (!project) { const c = await quiet(() => readState(run, "markers", seq)); project = c?.project || null; ctx = ctx || c; }
    const ended = now();
    const info = await quiet(() => clientInfo?.());
    const clips = [...new Set((effects || []).filter((e) => e.kind === "video" || e.kind === "audio").map((e) => e.name).filter(Boolean))];
    const seqs = [...new Set((effects || []).map((e) => e.sequence).filter(Boolean))];
    const single = scope === "structure" ? null : ctx?.sequence || seq;
    writeAction({
      v: 1, id: "a_" + randomUUID().slice(0, 12), t_start: started.toISOString(), t: ended.toISOString(),
      ...(failed && effects === null ? { settle_until: new Date(ended.getTime() + SETTLE_MS).toISOString() } : {}),
      tool: name, scope, channel: "mcp", client: info?.name || "unknown", client_version: info?.version || "unknown",
      model: "unknown", // MCP does not tell the server which model is calling
      ok: !failed, project, sequence: single, sequenceID: scope === "structure" ? null : ctx?.sequenceID || null,
      sequences: seqs, clips, target: target(args), effects,
    }, project, root);
  });

  if (thrown) throw thrown;
  return result;
}

// pk.js can be run by a person or by an AI through a shell, and the bridge
// cannot tell which. Its edits are logged as "something happened here, caller
// unknown", so they stay unresolved instead of reading as "not via the bridge".
export async function trackCli(command, sequence, { run, root = JR.JOURNAL_ROOT, now = () => new Date() }, fn) {
  await quiet(() => mark(root));
  const started = now();
  const ctx = await quiet(() => readState(run, "markers", null));
  let out, thrown = null;
  try { out = await fn(); } catch (e) { thrown = e; }
  const failed = !!thrown || !!(out && (out.ok === false || out.error));
  if (!(failed && !ctx && unreachable(thrown, out))) await quiet(() => {
    const ended = now();
    writeAction({
      v: 1, id: "a_" + randomUUID().slice(0, 12), t_start: started.toISOString(), t: ended.toISOString(),
      ...(failed ? { settle_until: new Date(ended.getTime() + SETTLE_MS).toISOString() } : {}),
      tool: "pk " + command, scope: "structure", channel: "pk_cli", caller: "unknown", client: "unknown", client_version: "unknown", model: "unknown",
      ok: !failed, project: ctx?.project || null, sequence: sequence || null, sequenceID: null, sequences: [], clips: [], effects: null,
    }, ctx?.project || null, root);
  });
  if (thrown) throw thrown;
  return out;
}

export const PK_EDIT_COMMANDS = new Set(["make-seq", "markers", "delete-seq", "build", "eval", "file"]);
