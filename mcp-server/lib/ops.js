// Operations shared by the MCP server and pk.js. Each takes `run`, an
// executor that sends an ExtendScript function body to Premiere and resolves
// with the parsed answer: run(body, { timeoutMs }) -> object | string.
// A result with `.error` is a Premiere-side refusal; ops throw it as
// BridgeError so both front ends report it the same way.

import { existsSync } from "node:fs";
import { isAbsolute, basename, resolve } from "node:path";
import * as J from "./seq.js";
import { gate, runFix, shellLine, MIN_FREE_BYTES } from "./mediagate.js";
import { diagnose, rememberBaseline } from "./dialog.js";

export class BridgeError extends Error {
  constructor(data) {
    super(typeof data === "string" ? data : data.error || JSON.stringify(data));
    this.data = typeof data === "string" ? { error: data } : data;
  }
}

function must(res, what) {
  if (res == null || res === "") throw new BridgeError(`empty answer from Premiere (${what})`);
  if (typeof res === "string") {
    try { res = JSON.parse(res); } catch { throw new BridgeError({ error: `${what}: unexpected answer`, raw: res.slice(0, 500) }); }
  }
  if (res && res.error) throw new BridgeError(res);
  return res;
}

export const DEFAULT_TIMEOUT_MS = 60_000;
const IMPORT_TIMEOUT_MS = 120_000;

export async function ping(run) {
  return must(await run(J.pingJsx(), { timeoutMs: 8000 }), "ping");
}

// Status with the "waiting for a click" diagnosis when Premiere does not answer.
export async function status(run, { pingPort } = {}) {
  try {
    const p = await ping(run);
    await rememberBaseline().catch(() => {}); // a responsive Premiere: remember its windows for the next dialog check
    return { connected: true, waitingForClick: false, ...p };
  } catch (e) {
    const diag = await diagnose({ pingPort, pingScript: () => run("return 1;", { timeoutMs: 3000 }) });
    return { connected: false, waitingForClick: diag.blocked, reason: e.message, diagnosis: diag };
  }
}

export async function dialogCheck(run, { pingPort } = {}) {
  return diagnose({ pingPort, pingScript: () => run("return 1;", { timeoutMs: 3000 }) });
}

export function probe(paths, { start, end } = {}) {
  return paths.map((p) => {
    const g = gate(p, { start, end });
    return { ...g, fixCmdLine: g.fixCmd ? shellLine(g.fixCmd) : null };
  });
}

// Import with the gate. convert=true runs the ffmpeg copy for unreadable
// files (range start/end) and imports the copy instead.
export async function importMedia(run, { paths, bin, start, end, convert = false, reuse = true, log } = {}) {
  const results = [];
  for (const p of paths) {
    if (!isAbsolute(p)) throw new BridgeError(`${p}: not an absolute path`);
    if (!existsSync(p)) throw new BridgeError(`${p}: file not found`);
  }
  for (const p of paths) {
    const g = gate(p, { start, end });
    const entry = { path: p, codec: g.codec, fps: g.fps, width: g.width, height: g.height, duration: g.duration, gate: g.ok ? "ok" : g.reason };
    if (!g.diskOk) throw new BridgeError({ error: `Nothing imported: ${g.reason}`, freeBytes: g.freeBytes, done: results });
    let file = p;
    if (!g.ok) {
      entry.fixCmd = shellLine(g.fixCmd);
      entry.copyPath = g.copyPath;
      if (!convert) {
        entry.imported = false;
        entry.error = `${g.reason}. Run the command in fixCmd (or pass convert=true), then import ${basename(g.copyPath)}`;
        results.push(entry);
        throw new BridgeError({ error: `Not imported: ${basename(p)} is ${g.codec}, Premiere would take sound only`, fixCmd: entry.fixCmd, copyPath: g.copyPath, done: results });
      }
      if (!g.copyDiskOk) throw new BridgeError({ error: `Cannot make the H.264 copy: ${g.reason}`, done: results });
      if (!existsSync(g.copyPath)) {
        if (log) log(`ffmpeg: ${shellLine(g.fixCmd)}`);
        await runFix(g.fixCmd, { log });
      }
      entry.converted = true;
      file = g.copyPath;
    }
    const imp = must(await run(J.importMediaJsx({ path: file, bin, reuse }), { timeoutMs: IMPORT_TIMEOUT_MS }), `import ${basename(file)}`);
    Object.assign(entry, { imported: true, item: imp });
    if (imp.path && imp.videoDecoded === false && g.codec) {
      entry.error = `Premiere decodes no video from ${basename(file)} (footage fps ${imp.fps}); it will be sound only. Make an H.264 copy: ${shellLine(gate(file, { start, end }).fixCmd || J_fix(file, g))}`;
    }
    results.push(entry);
  }
  return { imported: results.filter((r) => r.imported).length, results };
}

function J_fix(file, g) {
  // a readable codec that Premiere still refused: same recipe, no range
  return ["ffmpeg", "-i", file, "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "aac", file.replace(/\.[^.]+$/, "") + "-h264.mp4"];
}

export async function makeSequence(run, opts) {
  const { name, file, item, bin, convert = false, log } = opts;
  if (!name) throw new BridgeError("make_sequence: name is required");
  let ref = item;
  let imported = null;
  if (file) {
    const abs = resolve(file);
    const r = await importMedia(run, { paths: [abs], bin, start: opts.in, end: opts.out, convert, reuse: true, log });
    imported = r.results[0];
    if (imported.error) throw new BridgeError({ error: imported.error, imported });
    ref = imported.item.nodeId;
  }
  if (!ref) throw new BridgeError("make_sequence: file or item is required");
  const res = must(await run(J.makeSequenceJsx({
    name, item: ref, bin, inSec: opts.in, outSec: opts.out, width: opts.width, height: opts.height, fps: opts.fps, exists: opts.exists,
  }), { timeoutMs: IMPORT_TIMEOUT_MS }), `make_sequence ${name}`);
  if (imported) res.source = { path: imported.path, codec: imported.codec, converted: !!imported.converted, nodeId: ref };
  return res;
}

export async function setMotion(run, opts) {
  return must(await run(J.setMotionJsx(opts)), "set_motion");
}

export async function setMotionKeyframes(run, opts) {
  if (!Array.isArray(opts.keys) || !opts.keys.length) throw new BridgeError("set_motion_keyframes: keys[] is required");
  return must(await run(J.setMotionKeyframesJsx(opts)), "set_motion_keyframes");
}

export async function addMarkers(run, opts) {
  if (!Array.isArray(opts.markers) || !opts.markers.length) throw new BridgeError("add_markers: markers[] is required");
  // one call per 400 markers keeps each evalScript short
  const chunks = [];
  for (let i = 0; i < opts.markers.length; i += 400) chunks.push(opts.markers.slice(i, i + 400));
  let total = null;
  for (let i = 0; i < chunks.length; i++) {
    const r = must(await run(J.addMarkersJsx({ seq: opts.seq, markers: chunks[i], clear: opts.clear && i === 0 })), "add_markers");
    total = total ? { ...r, requested: total.requested + r.requested, created: total.created + r.created, removed: total.removed, markersBefore: total.markersBefore } : r;
  }
  return total;
}

export async function listMarkers(run, opts) {
  return must(await run(J.listMarkersJsx(opts)), "list_markers");
}

export async function openSequences(run, { names, activate }) {
  if (!names || !names.length) throw new BridgeError("open_sequences: names[] is required");
  return must(await run(J.openSequencesJsx({ names, activate })), "open_sequences");
}

export async function activate(run, { name }) {
  if (!name) throw new BridgeError("activate: name is required");
  const r = must(await run(J.openSequencesJsx({ names: [name] })), "activate");
  const opened = r.opened[0];
  const byId = opened && opened.sequenceID === name; // the ref may be a sequenceID
  if (r.activeSequence !== name && !(byId && r.activeSequence === opened.name)) {
    throw new BridgeError({ error: `activeSequence is "${r.activeSequence}" after opening "${name}"`, ...r });
  }
  return r;
}

export async function getTimeline(run, opts) {
  return must(await run(J.getTimelineJsx(opts || {})), "get_timeline");
}

// The render range after a manual trim: sequence in/out if set, else the
// span of the clips; plus each V1/A1 clip's borders in seconds.
export async function getInOut(run, opts) {
  const t = await getTimeline(run, opts);
  const clips = (coll) => coll.map((tr) => tr.clips.map((c) => ({ track: tr.index, name: c.name, start: c.start, end: c.end, inPoint: c.inPoint, outPoint: c.outPoint, path: c.path }))).flat();
  return {
    sequence: t.name, sequenceID: t.sequenceID, width: t.width, height: t.height, fps: t.fps, end: t.end,
    inPoint: t.inPoint, outPoint: t.outPoint, workArea: t.workArea, clipsRange: t.clipsRange, renderRange: t.renderRange,
    video: clips(t.video), audio: clips(t.audio),
  };
}

export async function deleteSequence(run, { name }) {
  if (!name) throw new BridgeError("delete_sequence: name is required");
  return must(await run(J.deleteSequenceJsx({ name })), "delete_sequence");
}

export async function moveToBin(run, { item, bin }) {
  if (!item) throw new BridgeError("move_to_bin: item is required");
  return must(await run(J.moveToBinJsx({ item, bin })), "move_to_bin");
}

export async function listBin(run, opts) {
  return must(await run(J.listBinJsx(opts || {})), "list_bin");
}

export async function saveProject(run) {
  return must(await run(J.saveProjectJsx(), { timeoutMs: IMPORT_TIMEOUT_MS }), "save_project");
}

// One spec, many sequences: import (gated), create, motion, keyframes,
// markers, open as tabs, optional save. Stops at the first failed sequence
// unless continueOnError; every finished step is reported either way.
//   spec = { bin, width, height, fps, exists, open, activate, save, convert,
//            sequences: [{ name, file|item, in, out, motion:{scale,x,y,rotation,units},
//                          motionKeys:[{t,scale,posX,posY}], markers:[{t,name,comment}], clearMarkers }] }
export async function build(run, spec, { log } = {}) {
  const out = { sequences: [], opened: null, saved: null, ok: true };
  const seqs = spec.sequences || [];
  if (!seqs.length) throw new BridgeError("build: spec.sequences is empty");
  for (const s of seqs) {
    const row = { name: s.name, steps: [] };
    try {
      const made = await makeSequence(run, {
        name: s.name, file: s.file, item: s.item, bin: s.bin || spec.bin, in: s.in, out: s.out,
        width: s.width ?? spec.width, height: s.height ?? spec.height, fps: s.fps ?? spec.fps,
        exists: s.exists || spec.exists || "error", convert: s.convert ?? spec.convert ?? false, log,
      });
      row.sequenceID = made.sequenceID;
      row.steps.push({ step: "make_sequence", width: made.width, height: made.height, fps: made.fps, v1: made.check.v1Clips, a1: made.check.a1Clips, replaced: made.replaced, skipped: !!made.skipped, source: made.source });
      if (made.skipped) { row.skipped = true; out.sequences.push(row); continue; }
      if (s.motion) {
        const m = await setMotion(run, { seq: s.name, track: 0, clip: 0, scale: s.motion.scale, posX: s.motion.x, posY: s.motion.y, rotation: s.motion.rotation, units: s.motion.units });
        row.steps.push({ step: "set_motion", scale: m.scale, positionPx: m.positionPx });
      }
      if (s.motionKeys && s.motionKeys.length) {
        const k = await setMotionKeyframes(run, { seq: s.name, track: 0, clip: 0, keys: s.motionKeys, units: (s.motion && s.motion.units) || "px", hold: s.hold });
        row.steps.push({ step: "set_motion_keyframes", keysWritten: k.keysWritten, positionKeysPx: k.positionKeysPx, scaleKeys: k.scaleKeys });
      }
      if (s.markers && s.markers.length) {
        const mk = await addMarkers(run, { seq: s.name, markers: s.markers, clear: s.clearMarkers });
        row.steps.push({ step: "add_markers", created: mk.created, markersAfter: mk.markersAfter });
      }
      row.ok = true;
    } catch (e) {
      row.ok = false;
      row.error = e.data || { error: e.message };
      out.ok = false;
      out.sequences.push(row);
      if (!spec.continueOnError) break;
      continue;
    }
    out.sequences.push(row);
  }
  const names = out.sequences.filter((r) => r.ok).map((r) => r.name);
  if (spec.open !== false && names.length) {
    try { out.opened = await openSequences(run, { names, activate: spec.activate }); }
    catch (e) { out.opened = e.data || { error: e.message }; out.ok = false; }
  }
  if (spec.save && out.ok) {
    try { out.saved = await saveProject(run); } catch (e) { out.saved = e.data || { error: e.message }; out.ok = false; }
  }
  return out;
}

export { MIN_FREE_BYTES, rememberBaseline };

// ---- the editor's journal (0.7) ---------------------------------------------
// Snapshots of every sequence, diffs as editing decisions, a report of the
// hand. Live through the bridge, or from Premiere's Auto-Save folder.

import * as JR from "./journal.js";
import { snapshotFromProject } from "./prproj.js";
import { readdirSync, statSync } from "node:fs";
import { join as pjoin, extname as pext } from "node:path";

export async function journalSnapshot(run, { dir, note, source = "live" } = {}) {
  const snap = must(await run(J.snapshotJsx({}), { timeoutMs: IMPORT_TIMEOUT_MS }), "journal snapshot");
  snap.t = new Date().toISOString();
  snap.source = source;
  if (note) snap.note = note;
  const d = dir || JR.journalDir(snap.project);
  const r = JR.record(d, snap, { source });
  return { dir: d, project: snap.project, sequences: snap.sequences.length, ...r, diff: r.diff ? summarizeDiff(r.diff) : null };
}

function summarizeDiff(diff) {
  return { from: diff.from, to: diff.to, count: diff.count, sequences: diff.sequences.map((s) => ({ sequence: s.sequence, events: s.events.map((e) => ({ type: e.type, kind: e.kind, name: e.name, start: e.start, frames: e.frames, parts: e.parts })) })) };
}

// Import .prproj files (or a folder of autosaves) in time order.
export function journalImport(paths, { dir } = {}) {
  const files = [];
  for (const p of paths) {
    const st = statSync(p);
    if (st.isDirectory()) for (const f of readdirSync(p)) { if (pext(f).toLowerCase() === ".prproj") files.push(pjoin(p, f)); }
    else files.push(p);
  }
  const snaps = files.map((f) => ({ f, snap: snapshotFromProject(f, { mtime: statSync(f).mtime }) })).sort((a, b) => a.snap.t.localeCompare(b.snap.t));
  if (!snaps.length) throw new BridgeError("journal import: no .prproj files found");
  // One journal per project: an Auto-Save folder holds every project ever opened.
  const done = [], dirs = {};
  let events = 0;
  for (const { f, snap } of snaps) {
    const d = dir || JR.journalDir(snap.project);
    dirs[snap.project] = d;
    const r = JR.record(d, snap, { source: snap.source, file: f });
    events += r.events;
    done.push({ project: snap.project, file: f, t: snap.t, saved: r.saved, events: r.events, sequences: snap.sequences.length });
  }
  return { dirs, files: files.length, snapshotsSaved: done.filter((x) => x.saved).length, events, done };
}

export function journalReport({ dir, project, markdown } = {}) {
  const d = dir || (project ? JR.journalDir(project) : null);
  if (!d) throw new BridgeError("journal report: dir or project is required");
  const r = JR.report(d);
  return markdown ? { dir: d, markdown: JR.reportMarkdown(r), report: r } : r;
}

export function journalDiff({ dir, project, a, b } = {}) {
  const d = dir || (project ? JR.journalDir(project) : null);
  if (!d) throw new BridgeError("journal diff: dir or project is required");
  const files = JR.listSnapshots(d);
  if (files.length < 2 && !(a && b)) throw new BridgeError(`journal diff: fewer than two snapshots in ${d}`);
  const A = JR.loadSnapshot(a || files[files.length - 2]), B = JR.loadSnapshot(b || files[files.length - 1]);
  return JR.diffSnapshots(A, B);
}

export function journalList({ dir, project } = {}) {
  const d = dir || (project ? JR.journalDir(project) : null);
  if (!d) throw new BridgeError("journal ls: dir or project is required");
  return { dir: d, snapshots: JR.listSnapshots(d).map((f) => { const s = JR.loadSnapshot(f); return { file: f, t: s.t, source: s.source, sequences: s.sequences.length }; }), events: JR.readEvents(d).length };
}

// Poll: a snapshot every `everySec` while Premiere answers, plus one on
// each save of the project file. Stops on `stop()` or after `maxMinutes`.
export async function journalWatch(run, { dir, everySec = 120, maxMinutes = 0, onEvent, log } = {}) {
  const start = Date.now();
  let lastMtime = null, polls = 0, saved = 0, events = 0, projectPath = null, d = dir;
  const say = (m) => { if (log) log(m); };
  while (true) {
    polls++;
    try {
      const r = await journalSnapshot(run, { dir: d, source: "live" });
      d = r.dir;
      if (r.saved) { saved++; events += r.events; say(`snapshot ${r.file.split("/").pop()} (+${r.events} decisions)`); if (onEvent) onEvent(r); }
      const p = must(await run("return app.project ? app.project.path : null;", { timeoutMs: 8000 }), "path");
      if (typeof p === "string") {
        projectPath = p;
        const mt = JR.fileMtime(p);
        if (mt && lastMtime && mt > lastMtime) { say(`project saved at ${mt.toISOString()}`); const rs = await journalSnapshot(run, { dir: d, source: "on-save" }); if (rs.saved) { saved++; events += rs.events; } }
        lastMtime = mt;
      }
    } catch (e) {
      say(`premiere not answering: ${e.message}`);
    }
    if (maxMinutes && Date.now() - start > maxMinutes * 60000) break;
    await new Promise((r) => setTimeout(r, everySec * 1000));
  }
  return { dir: d, projectPath, polls, snapshotsSaved: saved, events };
}
