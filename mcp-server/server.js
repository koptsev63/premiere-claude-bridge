#!/usr/bin/env node
// premiere-claude-bridge: MCP server connecting Claude Code <-> Adobe Premiere Pro CEP panel
// Architecture:
//   Claude Code  <--stdio JSON-RPC-->  this server  <--WebSocket-->  CEP panel  <--evalScript-->  Premiere host.jsx

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import { isAbsolute, extname, basename } from "node:path";
import { z } from "zod";
import { PanelPort } from "./lib/port.js";
import { prepareEvalCode } from "./lib/evalcode.js";
import { resolveEffect, normalizeName, EN_TO_LOCAL } from "./lib/effects.js";
import { exportPresetProblem } from "./lib/presets.js";
import {
  importOneCaptionJsx, captionTrackJsx, listVideoEffectsJsx, addVideoEffectJsx, CAPTION_FORMATS,
} from "./lib/jsx.js";
import * as ops from "./lib/ops.js";
import http from "node:http";

const VERSION = "0.6.0";
const CEF_DEBUG_PORT = Number(process.env.PK_PORT || 8088);
const WS_HOST = "127.0.0.1";
const WS_PORT = Number(process.env.PREMIERE_BRIDGE_PORT || 9876);
const REQUEST_TIMEOUT_MS = 60_000;
// The panel retries its connection every 2 s; give it a few rounds after we take the port.
const PANEL_WAIT_MS = Number(process.env.PREMIERE_BRIDGE_PANEL_WAIT_MS || 8000);

let panelSocket = null;
let activeTools = 0; // tool calls in progress: while > 0 we do not hand the port over
const pending = new Map();

function log(...args) {
  const line = args.map((a) => (typeof a === "string" ? a : JSON.stringify(a))).join(" ");
  process.stderr.write(`[premiere-mcp] ${line}\n`);
}

const port = new PanelPort({
  host: WS_HOST,
  port: WS_PORT,
  version: VERSION,
  log,
  onConnection: attachConnectionHandler,
  isBusy: () => activeTools > 0 || pending.size > 0,
});

function attachConnectionHandler(ws) {
  log("CEP panel connected");
  if (panelSocket && panelSocket !== ws) {
    log("Replacing previous panel socket");
    try { panelSocket.close(); } catch {}
  }
  panelSocket = ws;

  ws.on("message", (data) => {
    let msg;
    try { msg = JSON.parse(data.toString()); }
    catch { log("invalid msg from panel:", data.toString().slice(0, 200)); return; }
    if (!msg.id) return;
    const p = pending.get(msg.id);
    if (!p) return;
    clearTimeout(p.timer);
    pending.delete(msg.id);
    if (msg.ok) p.resolve(msg.result);
    else p.reject(new Error(msg.error || "Unknown panel error"));
  });

  ws.on("close", () => {
    log("CEP panel disconnected");
    if (panelSocket === ws) panelSocket = null;
  });

  ws.on("error", (e) => log("WS client error:", e.message));
}

function getActiveSocket() {
  // Self-healing: prefer cached panelSocket if alive, otherwise scan wss.clients
  // for any open socket and adopt it. Avoids stale state after panel reload races.
  if (!port.bound) return null;
  if (panelSocket && panelSocket.readyState === 1) return panelSocket;
  for (const ws of port.wss?.clients || []) {
    if (ws.readyState === 1) {
      if (panelSocket !== ws) {
        log("Adopting live socket from wss.clients (stale panelSocket recovered)");
        panelSocket = ws;
      }
      return ws;
    }
  }
  return null;
}

// The session that calls a tool gets the panel: take the port if another
// bridge holds it, then wait for the panel to reconnect to us.
async function ensurePanel() {
  let sock = getActiveSocket();
  if (sock) return sock;
  await port.acquire();
  sock = getActiveSocket();
  if (sock) return sock;
  await port.waitForConnection(PANEL_WAIT_MS);
  sock = getActiveSocket();
  if (sock) return sock;
  throw new Error(
    `Premiere panel not connected: this session holds port ${WS_PORT}, but the panel did not ` +
    `connect within ${PANEL_WAIT_MS / 1000}s. In Premiere: Window > Extensions > Claude Bridge`
  );
}

async function callPanel(fn, args = [], { timeoutMs = REQUEST_TIMEOUT_MS } = {}) {
  const sock = await ensurePanel();
  return new Promise((resolve, reject) => {
    const id = randomUUID();
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(Object.assign(
        new Error(`Panel request "${fn}" timed out after ${timeoutMs}ms`),
        { code: "PANEL_TIMEOUT" }
      ));
    }, timeoutMs);
    pending.set(id, { resolve, reject, timer });
    sock.send(JSON.stringify({ id, fn, args }));
  });
}

function parseResult(raw) {
  if (typeof raw !== "string") return raw;
  try { return JSON.parse(raw); } catch { return raw; }
}

// Run a function body (with its own top-level `return`) through the panel.
async function runJsx(body, opts) {
  return parseResult(await callPanel("evalRaw", [body], opts));
}

function ok(data) {
  const text = typeof data === "string" ? data : JSON.stringify(data, null, 2);
  return { content: [{ type: "text", text }] };
}

function err(message) {
  const text = typeof message === "string" ? message : JSON.stringify(message, null, 2);
  return { isError: true, content: [{ type: "text", text }] };
}

const server = new McpServer({
  name: "premiere-claude-bridge",
  version: VERSION,
});

// Every tool counts as "in progress" for the port hand-over check.
function tool(name, description, schema, handler) {
  server.tool(name, description, schema, async (args) => {
    activeTools++;
    try { return await handler(args); }
    catch (e) { return err(e.message); }
    finally { activeTools--; }
  });
}

tool(
  "pr_status",
  "Check connection to Premiere Pro and return basic project info. Use this first to verify the bridge is alive. If another Claude session's bridge holds the panel, this session takes it over.",
  {},
  async () => {
    try { await ensurePanel(); }
    catch (e) {
      const diagnosis = await ops.dialogCheck(runJsx, { pingPort: cefPortAlive }).catch(() => null);
      return ok({
        connected: false,
        reason: e.message,
        waitingForClick: !!(diagnosis && diagnosis.blocked),
        hint: diagnosis ? diagnosis.message : "Open panel in Premiere: Window > Extensions > Claude Bridge",
        diagnosis,
      });
    }
    const data = parseResult(await callPanel("getStatus", []));
    ops.rememberBaseline().catch(() => {}); // a responsive Premiere: remember its windows for the dialog check
    return ok({ connected: true, waitingForClick: false, bridgePid: process.pid, ...data });
  }
);

tool(
  "pr_get_project_info",
  "Get the current Premiere project: name, file path, and a flat list of all project items (bins, clips, sequences) with nodeId for later reference.",
  {},
  async () => ok(parseResult(await callPanel("getProjectInfo", [])))
);

tool(
  "pr_get_active_sequence",
  "Get info about the active sequence: name, duration in seconds, frame rate, dimensions, track counts.",
  {},
  async () => ok(parseResult(await callPanel("getActiveSequenceInfo", [])))
);

tool(
  "pr_list_timeline",
  "List all video and audio tracks of active sequence with every clip on each track (name, in/out points, start/end times, duration).",
  {},
  async () => ok(parseResult(await callPanel("listTimeline", [])))
);

tool(
  "pr_get_selected",
  "Get currently selected clips on the timeline.",
  {},
  async () => ok(parseResult(await callPanel("getSelected", [])))
);

tool(
  "pr_get_playhead",
  "Get current playhead time (CTI) on the active sequence in seconds and ticks.",
  {},
  async () => ok(parseResult(await callPanel("getCTI", [])))
);

tool(
  "pr_set_playhead",
  "Move the playhead to a given time on the active sequence.",
  { seconds: z.number().describe("Target time in seconds (e.g. 12.5)") },
  async ({ seconds }) => ok(parseResult(await callPanel("setCTI", [seconds])))
);

tool(
  "pr_add_marker",
  "Add a marker on the active sequence at given time.",
  {
    seconds: z.number().describe("Time in seconds where to place the marker"),
    name: z.string().optional().describe("Marker name (label)"),
    comment: z.string().optional().describe("Marker comment"),
  },
  async ({ seconds, name, comment }) =>
    ok(parseResult(await callPanel("addSequenceMarker", [seconds, name || "", comment || ""])))
);

tool(
  "pr_export_ame",
  "Queue active sequence export to Adobe Media Encoder (non-blocking). Provide absolute output path and absolute path to a .epr preset file. Use a Match Source preset: ingest/proxy presets force their own frame size (a 1080x1920 sequence came out 1280x720) and are refused unless allowProxyPreset is true.",
  {
    outPath: z.string().describe("Absolute output path including filename and extension"),
    eprPath: z.string().describe("Absolute path to .epr Adobe Media Encoder preset (Match Source presets keep the sequence frame size)"),
    allowProxyPreset: z.boolean().optional().describe("Allow an IngestPresets/Proxy preset on purpose (default false)"),
  },
  async ({ outPath, eprPath, allowProxyPreset }) => {
    const problem = exportPresetProblem(eprPath);
    if (problem && !(allowProxyPreset && problem.kind === "proxy")) return err(problem.message);
    return ok(parseResult(await callPanel("exportAME", [outPath, eprPath])));
  }
);

tool(
  "pr_eval_jsx",
  "ESCAPE HATCH: run arbitrary ExtendScript code inside Premiere when no specific tool fits. Returns the value of the last expression (like eval), or of an explicit top-level `return` if the code has one. Objects are auto-stringified.",
  {
    code: z.string().describe(
      "ExtendScript (ES3) code. Either end with an expression (`app.project.name`) or use `return` at top level (`var s = app.project.activeSequence; return s.name;`). Both return the value."
    ),
  },
  async ({ code }) => {
    const prepared = prepareEvalCode(code);
    return ok(parseResult(await callPanel("evalRaw", [prepared.code])));
  }
);

// ---- captions -------------------------------------------------------------

const CAPTION_EXTS = new Set([".srt", ".scc", ".mcc", ".xml", ".dfxp", ".stl"]);

tool(
  "pr_import_captions",
  "Import caption files (.srt etc.) into the project ONE AT A TIME and, by default, put each on a new caption track of the active sequence. Safe path for a known hang: importFiles with several .srt and suppressUI=true froze Premiere on 'Import files'. Each file is a separate panel call with its own timeout; on a timeout the tool stops and says which file.",
  {
    paths: z.array(z.string()).min(1).describe("Absolute paths of caption files, imported in this order"),
    bin: z.string().optional().describe("Bin at project root to import into (created if missing). Default: project root"),
    createTracks: z.boolean().optional().describe("Create a caption track on the active sequence for each file (default true)"),
    startSeconds: z.number().optional().describe("Where the caption track starts on the sequence, seconds (default 0)"),
    format: z.enum(Object.keys(CAPTION_FORMATS)).optional().describe("Caption format (default subtitle)"),
    reuseExisting: z.boolean().optional().describe("If the file is already in the project, use that item instead of importing again (default true)"),
    perFileTimeoutSec: z.number().optional().describe("Seconds to wait for Premiere per file (default 60)"),
  },
  async ({ paths, bin, createTracks = true, startSeconds = 0, format = "subtitle", reuseExisting = true, perFileTimeoutSec = 60 }) => {
    const bad = [];
    for (const p of paths) {
      if (!isAbsolute(p)) bad.push(`${p}: not an absolute path`);
      else if (!CAPTION_EXTS.has(extname(p).toLowerCase())) bad.push(`${p}: not a caption file (${[...CAPTION_EXTS].join(" ")})`);
      else if (!existsSync(p)) bad.push(`${p}: file not found`);
    }
    if (bad.length) return err({ error: "Nothing imported", problems: bad });

    const timeoutMs = perFileTimeoutSec * 1000;
    const done = [];
    for (const path of paths) {
      const entry = { path };
      try {
        const imp = await runJsx(importOneCaptionJsx({ path, bin, reuse: reuseExisting }), { timeoutMs });
        if (!imp || imp.error) {
          entry.error = imp?.error || "empty answer from Premiere";
          done.push(entry);
          return err({ error: `Import failed on ${basename(path)}; later files were not touched`, done });
        }
        Object.assign(entry, imp);
        if (createTracks) {
          const tr = await runJsx(captionTrackJsx({ nodeId: imp.nodeId, startSeconds, format }), { timeoutMs });
          if (!tr || tr.error) {
            entry.trackError = tr?.error || "empty answer from Premiere";
            done.push(entry);
            return err({ error: `Imported ${basename(path)} but could not create its caption track; later files were not touched`, done });
          }
          entry.track = tr;
        }
        done.push(entry);
      } catch (e) {
        entry.error = e.message;
        done.push(entry);
        const hint = e.code === "PANEL_TIMEOUT"
          ? `Premiere did not answer within ${perFileTimeoutSec}s on ${basename(path)}. It may be waiting on a dialog ("Import files" / "Импорт файлов..."). Look at Premiere before retrying; later files were not touched.`
          : `Stopped on ${basename(path)}; later files were not touched.`;
        return err({ error: hint, done });
      }
    }
    return ok({ imported: done.length, done });
  }
);

// ---- effects --------------------------------------------------------------

let effectListCache = null;
async function videoEffectList() {
  if (effectListCache) return effectListCache;
  const list = await runJsx(listVideoEffectsJsx());
  if (!Array.isArray(list)) throw new Error(`Could not read the effect list: ${JSON.stringify(list)}`);
  effectListCache = list;
  return list;
}

tool(
  "pr_list_video_effects",
  "List video effect names exactly as this Premiere knows them (localized: a Russian UI has 'Черное и белое', not 'Black & White'). Pass these names to pr_add_video_effect.",
  { filter: z.string().optional().describe("Substring to filter by, any case") },
  async ({ filter }) => {
    const list = await videoEffectList();
    const en = {};
    for (const table of Object.values(EN_TO_LOCAL)) {
      for (const [k, v] of Object.entries(table)) if (list.includes(v)) en[v] = k;
    }
    const f = normalizeName(filter || "");
    const rows = list
      .filter((n) => !f || normalizeName(n).includes(f) || (en[n] && normalizeName(en[n]).includes(f)))
      .map((n) => (en[n] ? { name: n, en: en[n] } : { name: n }));
    return ok({ count: rows.length, effects: rows });
  }
);

tool(
  "pr_add_video_effect",
  "Add a video effect to clips of the active sequence and VERIFY it was added (components counted before and after). Takes an English or localized name; resolves it against this Premiere's own effect list, because getVideoEffectByName with an English name in a Russian UI returns an object and then silently adds nothing.",
  {
    effect: z.string().describe("Effect name, English ('Black & White') or as shown in Premiere ('Черное и белое')"),
    target: z.enum(["clip", "track", "selected"]).optional().describe("clip = one clip by index (default), track = every clip on the track, selected = selected video clips"),
    trackIndex: z.number().int().min(0).optional().describe("Video track, 0 = V1 (default 0)"),
    clipIndex: z.number().int().min(0).optional().describe("Clip on that track, 0-based, gaps not counted (as in pr_list_timeline). Default 0"),
  },
  async ({ effect, target = "clip", trackIndex = 0, clipIndex = 0 }) => {
    const list = await videoEffectList();
    const res = resolveEffect(effect, list);
    if (res.error) return err(res);
    const mode = target === "clip" ? "index" : target;
    const out = await runJsx(addVideoEffectJsx({ name: res.name, mode, track: trackIndex, clip: clipIndex }));
    if (!out || out.error) return err({ requested: effect, resolved: res.name, how: res.how, error: out?.error || "empty answer" });
    const failed = (out.results || []).filter((r) => !r.added);
    const report = { requested: effect, resolved: res.name, how: res.how, results: out.results };
    if (failed.length) {
      return err({ ...report, error: `Effect not added to ${failed.length} of ${out.results.length} clip(s): the component count did not change` });
    }
    return ok(report);
  }
);


// ---- sequences without a dialog (0.6.0) ------------------------------------
// Shared with pk.js through lib/ops.js: every command here is also
// `node pk.js <command>`. Field notes in lib/seq.js.

function cefPortAlive() {
  return new Promise((resolve) => {
    const req = http.get({ host: "127.0.0.1", port: CEF_DEBUG_PORT, path: "/json", timeout: 2000 }, (res) => {
      res.resume();
      resolve(res.statusCode === 200);
    });
    req.on("timeout", () => { req.destroy(); resolve(false); });
    req.on("error", () => resolve(false));
  });
}

// Panel timeouts usually mean a modal dialog: say which one, in Russian (the UI is Russian).
async function withDialogHint(fn) {
  try { return ok(await fn()); }
  catch (e) {
    const data = e.data || { error: e.message };
    if (e.code === "PANEL_TIMEOUT" || /timed out/i.test(e.message)) {
      try {
        const diagnosis = await ops.dialogCheck(runJsx, { pingPort: cefPortAlive });
        data.waitingForClick = diagnosis.blocked;
        data.hint = diagnosis.message;
        data.dialogs = diagnosis.dialogs;
      } catch {}
    }
    return err(data);
  }
}

const SEQ_REF = z.string().optional().describe("Sequence by exact name or sequenceID; default: the active sequence");

tool(
  "pr_ping",
  "Cheap health check: Premiere version, project name and path, number of sequences, active sequence settings (width, height, fps, markers). No side effects.",
  {},
  async () => withDialogHint(() => ops.ping(runJsx))
);

tool(
  "pr_dialog_check",
  "Is Premiere waiting for a click? A modal window (e.g. 'Создать эпизод', 'Несоответствие медиаданных') blocks every script: the bridge times out while the panel's debug port still answers. Returns blocked:true, the on-screen windows that look like a dialog, and a Russian message saying which button to press. Safe: looks, never clicks.",
  {},
  async () => ok(await ops.dialogCheck(runJsx, { pingPort: cefPortAlive }))
);

tool(
  "pr_probe_media",
  "Codec gate before an import, without touching Premiere: ffprobe each file and say whether Premiere 26 decodes its video (AV1/VP9/VP8 and 4:4:4 come in as sound only), the ffmpeg command for an H.264 edit copy of just the needed range, the copy's size estimate and whether the disk has room (2 GB minimum, 5 GB reserve after a copy).",
  {
    paths: z.array(z.string()).min(1).describe("Absolute paths"),
    start: z.number().optional().describe("Range start in seconds for the copy command (keep the copy small: a whole hour is ~10 GB)"),
    end: z.number().optional().describe("Range end in seconds"),
  },
  async ({ paths, start, end }) => ok({ results: ops.probe(paths, { start, end }) })
);

tool(
  "pr_import_media",
  "Import media files into a bin, one file per call to Premiere, with the codec and disk gate first (pr_probe_media rules). An unreadable file is NOT imported: the answer carries the ffmpeg command for an H.264 copy of the range; with convert=true the bridge runs that command itself and imports the copy. A file already in the project is reused by path. After the import the footage frame rate is read back: ~0 means Premiere decoded no video.",
  {
    paths: z.array(z.string()).min(1).describe("Absolute paths, imported in this order"),
    bin: z.string().optional().describe("Bin path from the project root, 'A/B' (created if missing). Default: root"),
    start: z.number().optional().describe("Range start (s) for the H.264 copy of an unreadable file"),
    end: z.number().optional().describe("Range end (s) for that copy"),
    convert: z.boolean().optional().describe("Run ffmpeg for unreadable files and import the copy (default false: only report the command)"),
    reuseExisting: z.boolean().optional().describe("Use the project item that already points at this file (default true)"),
  },
  async ({ paths, bin, start, end, convert = false, reuseExisting = true }) =>
    withDialogHint(() => ops.importMedia(runJsx, { paths, bin, start, end, convert, reuse: reuseExisting, log }))
);

tool(
  "pr_make_sequence",
  "Create a sequence from one clip range WITHOUT any dialog (createNewSequence with a preset id opens 'Создать эпизод' and hangs the bridge; this uses setInPoint/setOutPoint + createNewSequenceFromClips + setSettings). Imports the file first when `file` is given (codec and disk gate, reuse by path). Returns sequenceID, the settings read back, V1/A1 clip counts and clip borders. In/out snap to the source frame grid.",
  {
    name: z.string().describe("Sequence name (exact; Russian is fine)"),
    file: z.string().optional().describe("Absolute path of the source; imported into `bin` if not yet in the project"),
    item: z.string().optional().describe("Instead of file: project item by nodeId, absolute path or name"),
    in: z.number().optional().describe("Source in point, seconds (default: whole clip)"),
    out: z.number().optional().describe("Source out point, seconds"),
    width: z.number().int().optional().describe("Frame width, e.g. 1080 (default: the source's)"),
    height: z.number().int().optional().describe("Frame height, e.g. 1920"),
    fps: z.number().optional().describe("Frame rate: 29.97, 25, 30, 23.976, 59.94 ... (default: the source's)"),
    bin: z.string().optional().describe("Bin path 'A/B' for the sequence and the imported file (created if missing)"),
    exists: z.enum(["error", "replace", "skip"]).optional().describe("If a sequence with this name exists: error (default), replace (delete it and rebuild), skip (return it untouched)"),
    convert: z.boolean().optional().describe("For an unreadable source: make the H.264 copy of [in,out] and use it (default false)"),
  },
  async (a) => withDialogHint(() => ops.makeSequence(runJsx, { ...a, log }))
);

tool(
  "pr_set_motion",
  "Set Motion (Scale / Position / Rotation / Anchor Point) on one clip of a sequence and read the values back. Property names are matched in Russian and English ('Движение'/'Motion', 'Масштаб'/'Scale', 'Положение'/'Position'). Position is in pixels of the sequence frame by default (center of 1080x1920 = 540, 960); units='norm' takes 0..1.",
  {
    seq: SEQ_REF,
    track: z.number().int().min(0).optional().describe("Video track, 0 = V1 (default 0)"),
    clip: z.number().int().min(0).optional().describe("Clip index on that track, 0-based (default 0)"),
    scale: z.number().optional().describe("Scale in percent, e.g. 177.778 for a 1920x1080 source filling a 1080x1920 frame"),
    posX: z.number().optional(),
    posY: z.number().optional(),
    rotation: z.number().optional().describe("Degrees"),
    anchorX: z.number().optional(),
    anchorY: z.number().optional(),
    units: z.enum(["px", "norm"]).optional().describe("px (default) or norm"),
  },
  async (a) => withDialogHint(() => ops.setMotion(runJsx, a))
);

tool(
  "pr_set_motion_keyframes",
  "Keyframes on Motion for shot changes inside one clip: keys = [{t, scale?, posX?, posY?, rotation?}] with t in seconds from the clip's start on the timeline (checked live: addKey times are clip-relative). Existing keyframes of the touched properties are removed first unless clear=false. hold=true makes every key a hold (cut, no slide). Reads the keyframes back.",
  {
    seq: SEQ_REF,
    track: z.number().int().min(0).optional(),
    clip: z.number().int().min(0).optional(),
    keys: z.array(z.object({
      t: z.number(), scale: z.number().optional(), posX: z.number().optional(), posY: z.number().optional(), rotation: z.number().optional(),
    })).min(1),
    units: z.enum(["px", "norm"]).optional(),
    hold: z.boolean().optional().describe("Hold interpolation on every key (default false = linear)"),
    clear: z.boolean().optional().describe("Remove the property's old keyframes first (default true)"),
  },
  async (a) => withDialogHint(() => ops.setMotionKeyframes(runJsx, a))
);

tool(
  "pr_add_markers",
  "Add many sequence markers in one call: markers = [{t, name?, comment?, duration?}] (seconds). clear=true deletes the sequence's existing markers first. Verified by the marker count before and after.",
  {
    seq: SEQ_REF,
    markers: z.array(z.object({ t: z.number(), name: z.string().optional(), comment: z.string().optional(), duration: z.number().optional() })).min(1),
    clear: z.boolean().optional(),
  },
  async (a) => withDialogHint(() => ops.addMarkers(runJsx, a))
);

tool(
  "pr_list_markers",
  "List the markers of a sequence (t, end, name, comment, type).",
  { seq: SEQ_REF },
  async (a) => withDialogHint(() => ops.listMarkers(runJsx, a))
);

tool(
  "pr_open_sequences",
  "Open sequences as timeline tabs (by name or sequenceID); the last one, or `activate`, becomes the active sequence. Names that do not exist are reported in `missing`.",
  {
    names: z.array(z.string()).min(1),
    activate: z.string().optional().describe("Which of them to leave active (default: the last)"),
  },
  async (a) => withDialogHint(() => ops.openSequences(runJsx, a))
);

tool(
  "pr_activate_sequence",
  "Make a sequence the active one (opens its tab) and confirm app.project.activeSequence is it.",
  { name: z.string() },
  async (a) => withDialogHint(() => ops.activate(runJsx, a))
);

tool(
  "pr_get_timeline",
  "Timeline of a named sequence (not only the active one): settings, every track and clip with start/end/inPoint/outPoint in seconds and ticks, the sequence in/out, work area, clipsRange and renderRange. withMarkers adds the marker list.",
  { seq: SEQ_REF, withMarkers: z.boolean().optional() },
  async (a) => withDialogHint(() => ops.getTimeline(runJsx, a))
);

tool(
  "pr_get_inout",
  "Borders for a render after a manual trim: renderRange = the sequence in/out if set, else the span of the clips; plus each clip on V/A tracks with start, end, inPoint, outPoint (seconds) and its media path.",
  { seq: SEQ_REF },
  async (a) => withDialogHint(() => ops.getInOut(runJsx, a))
);

tool(
  "pr_delete_sequence",
  "Delete a sequence by exact name or sequenceID (app.project.deleteSequence) and confirm it is gone.",
  { name: z.string() },
  async (a) => withDialogHint(() => ops.deleteSequence(runJsx, a))
);

tool(
  "pr_move_to_bin",
  "Move a project item (clip by nodeId/path/name, or a sequence by name) into a bin path 'A/B' (created if missing). Returns the new treePath.",
  { item: z.string(), bin: z.string() },
  async (a) => withDialogHint(() => ops.moveToBin(runJsx, a))
);

tool(
  "pr_list_bin",
  "List a bin (path 'A/B' from the root; empty = root): name, nodeId, type (bin/clip/sequence), media path, footage fps and videoDecoded (false = Premiere sees no video in this file).",
  { bin: z.string().optional(), recursive: z.boolean().optional() },
  async (a) => withDialogHint(() => ops.listBin(runJsx, a))
);

tool(
  "pr_save_project",
  "Save the open project (app.project.save()).",
  {},
  async () => withDialogHint(() => ops.saveProject(runJsx))
);

tool(
  "pr_build_sequences",
  "Many sequences from one spec, no dialogs: per sequence import (gated) → create → Motion → Motion keyframes → markers; then open the finished ones as tabs and optionally save. Stops at the first failed sequence unless continueOnError; reports every step done. spec = {bin, width, height, fps, exists, open, activate, save, convert, continueOnError, sequences:[{name, file|item, in, out, motion:{scale,x,y,rotation,units}, motionKeys:[{t,scale,posX,posY}], hold, markers:[{t,name,comment,duration}], clearMarkers}]}",
  {
    spec: z.object({
      bin: z.string().optional(), width: z.number().int().optional(), height: z.number().int().optional(), fps: z.number().optional(),
      exists: z.enum(["error", "replace", "skip"]).optional(), open: z.boolean().optional(), activate: z.string().optional(),
      save: z.boolean().optional(), convert: z.boolean().optional(), continueOnError: z.boolean().optional(),
      sequences: z.array(z.object({
        name: z.string(), file: z.string().optional(), item: z.string().optional(), in: z.number().optional(), out: z.number().optional(),
        bin: z.string().optional(), width: z.number().int().optional(), height: z.number().int().optional(), fps: z.number().optional(),
        exists: z.enum(["error", "replace", "skip"]).optional(), convert: z.boolean().optional(),
        motion: z.object({ scale: z.number().optional(), x: z.number().optional(), y: z.number().optional(), rotation: z.number().optional(), units: z.enum(["px", "norm"]).optional() }).optional(),
        motionKeys: z.array(z.object({ t: z.number(), scale: z.number().optional(), posX: z.number().optional(), posY: z.number().optional(), rotation: z.number().optional() })).optional(),
        hold: z.boolean().optional(),
        markers: z.array(z.object({ t: z.number(), name: z.string().optional(), comment: z.string().optional(), duration: z.number().optional() })).optional(),
        clearMarkers: z.boolean().optional(),
      })).min(1),
    }),
  },
  async ({ spec }) => withDialogHint(async () => {
    const r = await ops.build(runJsx, spec, { log });
    if (!r.ok) throw Object.assign(new Error("build finished with errors"), { data: r });
    return r;
  })
);

// ---- start ----------------------------------------------------------------

const transport = new StdioServerTransport();
await server.connect(transport);
log(`MCP server ${VERSION} connected via stdio (pid ${process.pid})`);

// Take the port only if it is free. Never poll: a busy port belongs to
// another session until this one gets a tool call (see lib/port.js).
try {
  if ((await port.listen()) === "busy") {
    log(`Port ${WS_PORT} is held by another session; it moves here on this session's first tool call`);
  }
} catch (e) {
  log(`Could not bind port ${WS_PORT} at start: ${e.message} (will try again on the first tool call)`);
}

// When the client goes away, free the port at once instead of lingering.
function shutdown() {
  port.release();
  process.exit(0);
}
process.stdin.on("end", shutdown);
process.stdin.on("close", shutdown);
