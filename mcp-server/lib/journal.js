// The editor's journal: snapshots of every sequence over time, the diff
// between two snapshots as editing decisions (cut, trim, move, slip, split,
// speed), and a report of the hand behind them (shot lengths, trim sizes,
// J/L cuts, tempo). Snapshots come live from the bridge (seq.js
// snapshotJsx) or from Premiere's Auto-Save folder (prproj.js); both have
// the same shape, so a day of autosaves reads like a day of watching.
//
// Structure only: timecodes, clip names, media paths. No frames, no sound.

import { mkdirSync, writeFileSync, readFileSync, readdirSync, existsSync, appendFileSync, statSync } from "node:fs";
import { join, basename } from "node:path";
import { homedir } from "node:os";
import { createHash } from "node:crypto";

export const JOURNAL_ROOT = process.env.PREMIERE_JOURNAL_DIR || join(homedir(), ".premiere-bridge", "journal");

export function slug(name) {
  return String(name || "project").replace(/\.prproj$/i, "").replace(/[\/\\:\s]+/g, "_").slice(0, 80) || "project";
}

export function journalDir(project, root = JOURNAL_ROOT) {
  return join(root, slug(project));
}

// ---- storage ---------------------------------------------------------------

function contentHash(snap) {
  const h = createHash("sha1");
  for (const s of snap.sequences || []) {
    h.update(s.sequenceID || s.name);
    for (const t of [...(s.video || []), ...(s.audio || [])]) {
      h.update(`|${t.kind}${t.index}`);
      for (const c of t.clips) h.update(`;${c.name}|${r3(c.start)}|${r3(c.end)}|${r3(c.inPoint)}|${r3(c.outPoint)}|${c.path}`);
    }
    h.update(`|m${s.markers}`);
  }
  return h.digest("hex").slice(0, 16);
}

const r3 = (v) => (v == null ? "" : Math.round(v * 1000) / 1000);

export function stamp(t) {
  return new Date(t).toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z");
}

// Save a snapshot unless its content equals the last saved one. Returns
// { saved, file, hash, previous } - previous is the last saved snapshot.
// `previous` is the nearest EARLIER snapshot (autosaves are imported after
// live snapshots and must diff against their own neighbour, not the newest).
export function saveSnapshot(dir, snap) {
  mkdirSync(join(dir, "snapshots"), { recursive: true });
  const hash = contentHash(snap);
  const st = stamp(snap.t || Date.now());
  const earlier = listSnapshots(dir).filter((f) => basename(f) < st);
  const previous = earlier.length ? { ...loadSnapshot(earlier[earlier.length - 1]), file: earlier[earlier.length - 1] } : null;
  if (previous && previous.hash === hash) return { saved: false, hash, previous, file: previous.file };
  const file = join(dir, "snapshots", `${st}-${hash}.json`);
  if (existsSync(file)) return { saved: false, hash, previous, file };
  writeFileSync(file, JSON.stringify({ ...snap, hash }));
  const latest = latestSnapshot(dir);
  writeFileSync(join(dir, "latest.json"), JSON.stringify({ file: latest.file, hash: latest.hash, t: latest.t }));
  return { saved: true, hash, previous, file };
}

export function listSnapshots(dir) {
  const d = join(dir, "snapshots");
  if (!existsSync(d)) return [];
  return readdirSync(d).filter((f) => f.endsWith(".json")).sort().map((f) => join(d, f));
}

export function loadSnapshot(file) {
  return JSON.parse(readFileSync(file, "utf8"));
}

export function latestSnapshot(dir) {
  const files = listSnapshots(dir);
  if (!files.length) return null;
  const file = files[files.length - 1];
  const snap = loadSnapshot(file);
  return { ...snap, file };
}

export function appendEvents(dir, events) {
  if (!events.length) return 0;
  mkdirSync(dir, { recursive: true });
  appendFileSync(join(dir, "events.jsonl"), events.map((e) => JSON.stringify(e)).join("\n") + "\n");
  return events.length;
}

export function readEvents(dir) {
  const f = join(dir, "events.jsonl");
  if (!existsSync(f)) return [];
  return readFileSync(f, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
}

// ---- diff ------------------------------------------------------------------

const near = (a, b, tol) => a != null && b != null && Math.abs(a - b) <= tol;

function flat(seq, kind) {
  const out = [];
  for (const t of seq[kind] || []) for (const c of t.clips) out.push({ ...c, track: t.index, kind });
  return out;
}

function srcKey(c) { return c.path || c.name || ""; }

function srcOverlap(a, b) {
  if (a.inPoint == null || b.inPoint == null) return srcKey(a) === srcKey(b) ? 0.001 : 0;
  return Math.max(0, Math.min(a.outPoint, b.outPoint) - Math.max(a.inPoint, b.inPoint));
}

// Decisions between two states of one sequence. tol = half a frame.
export function diffSequence(prev, next, { fps } = {}) {
  const tol = 0.5 / (fps || next.fps || prev.fps || 25);
  const frames = (s) => Math.round(s * (fps || next.fps || prev.fps || 25));
  const events = [];
  for (const kind of ["video", "audio"]) {
    const A = flat(prev, kind), B = flat(next, kind);
    const matches = new Map(); // b index -> a index
    const usedBy = new Map(); // a index -> [b indexes]
    B.forEach((b, bi) => {
      let best = -1, bestOv = 0;
      A.forEach((a, ai) => {
        if (srcKey(a) !== srcKey(b)) return;
        let ov = srcOverlap(a, b);
        if (ov <= 0 && !(a.inPoint == null)) return;
        if (a.track === b.track) ov += 1e-6;
        if (near(a.start, b.start, tol)) ov += 1e-6;
        if (ov > bestOv) { bestOv = ov; best = ai; }
      });
      if (best >= 0) { matches.set(bi, best); usedBy.set(best, [...(usedBy.get(best) || []), bi]); }
    });
    A.forEach((a, ai) => {
      const bs = usedBy.get(ai);
      if (!bs) { events.push(ev("removed", kind, a, null, frames)); return; }
      if (bs.length > 1) {
        const parts = bs.map((bi) => B[bi]).sort((x, y) => x.start - y.start);
        events.push({ ...ev("split", kind, a, parts[0], frames), parts: parts.length, cutsAt: parts.slice(1).map((p) => r3(p.start)) });
        return;
      }
      const b = B[bs[0]];
      events.push(...compare(a, b, kind, tol, frames));
    });
    B.forEach((b, bi) => { if (!matches.has(bi)) events.push(ev("added", kind, null, b, frames)); });
  }
  if (prev.markers != null && next.markers != null && prev.markers !== next.markers) {
    events.push({ type: "markers", kind: "sequence", from: prev.markers, to: next.markers });
  }
  return events;
}

function ev(type, kind, a, b, frames) {
  const c = b || a;
  return { type, kind, track: c.track, name: c.name, path: c.path, start: r3(c.start), end: r3(c.end),
    inPoint: r3(c.inPoint), outPoint: r3(c.outPoint), durationFrames: frames(c.end - c.start) };
}

function compare(a, b, kind, tol, frames) {
  const dStart = b.start - a.start, dEnd = b.end - a.end;
  const dIn = a.inPoint == null || b.inPoint == null ? 0 : b.inPoint - a.inPoint;
  const dOut = a.outPoint == null || b.outPoint == null ? 0 : b.outPoint - a.outPoint;
  const out = [];
  const base = () => ({ ...ev("changed", kind, a, b, frames), fromStart: r3(a.start), fromEnd: r3(a.end), fromIn: r3(a.inPoint), fromOut: r3(a.outPoint) });
  if (a.track !== b.track) out.push({ ...base(), type: "move_track", fromTrack: a.track });
  const sameStart = near(dStart, 0, tol), sameEnd = near(dEnd, 0, tol), sameIn = near(dIn, 0, tol), sameOut = near(dOut, 0, tol);
  if (sameStart && sameEnd && sameIn && sameOut) return out;
  const speedA = a.inPoint == null ? 1 : (a.outPoint - a.inPoint) / Math.max(a.end - a.start, 1e-9);
  const speedB = b.inPoint == null ? 1 : (b.outPoint - b.inPoint) / Math.max(b.end - b.start, 1e-9);
  if (!near(speedA, speedB, 0.02)) { out.push({ ...base(), type: "speed", fromSpeed: r3(speedA), toSpeed: r3(speedB) }); return out; }
  if (near(dStart, dIn, tol) && !sameStart) out.push({ ...base(), type: "trim_head", frames: frames(dStart), shortened: dStart > 0 });
  if (near(dEnd, dOut, tol) && !sameEnd) out.push({ ...base(), type: "trim_tail", frames: frames(-dEnd), shortened: dEnd < 0 });
  if (out.some((e) => e.type === "trim_head" || e.type === "trim_tail")) return out;
  if (near(dStart, dEnd, tol) && sameIn && sameOut && !sameStart) { out.push({ ...base(), type: "move", frames: frames(dStart) }); return out; }
  if (sameStart && sameEnd && near(dIn, dOut, tol) && !sameIn) { out.push({ ...base(), type: "slip", frames: frames(dIn) }); return out; }
  out.push({ ...base(), type: "changed", dStartFrames: frames(dStart), dEndFrames: frames(dEnd), dInFrames: frames(dIn), dOutFrames: frames(dOut) });
  return out;
}

// All sequences between two snapshots.
export function diffSnapshots(prev, next) {
  const byId = (snap) => { const m = new Map(); for (const s of snap.sequences || []) m.set(s.sequenceID || s.name, s); return m; };
  const A = byId(prev), B = byId(next);
  const out = { from: prev.t, to: next.t, sequences: [] };
  for (const [id, b] of B) {
    const a = A.get(id) || [...A.values()].find((s) => s.name === b.name);
    if (!a) { out.sequences.push({ sequence: b.name, sequenceID: id, events: [{ type: "sequence_added", kind: "sequence", clips: flat(b, "video").length }] }); continue; }
    const events = diffSequence(a, b);
    if (events.length) out.sequences.push({ sequence: b.name, sequenceID: id, events });
  }
  for (const [id, a] of A) if (!B.has(id) && ![...B.values()].some((s) => s.name === a.name)) out.sequences.push({ sequence: a.name, sequenceID: id, events: [{ type: "sequence_removed", kind: "sequence" }] });
  out.count = out.sequences.reduce((n, s) => n + s.events.length, 0);
  return out;
}

// Flatten a diff into journal rows.
export function eventRows(diff, meta = {}) {
  const rows = [];
  for (const s of diff.sequences) for (const e of s.events) rows.push({ t: diff.to, from: diff.from, sequence: s.sequence, sequenceID: s.sequenceID, ...e, ...meta });
  return rows;
}

// ---- the hand report -------------------------------------------------------

function quantile(sorted, q) {
  if (!sorted.length) return null;
  const pos = (sorted.length - 1) * q, lo = Math.floor(pos), hi = Math.ceil(pos);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

function stats(values) {
  const s = [...values].filter((v) => v != null && !Number.isNaN(v)).sort((a, b) => a - b);
  if (!s.length) return { n: 0 };
  const mean = s.reduce((a, b) => a + b, 0) / s.length;
  return { n: s.length, min: r3(s[0]), p10: r3(quantile(s, 0.1)), median: r3(quantile(s, 0.5)), mean: r3(mean), p90: r3(quantile(s, 0.9)), max: r3(s[s.length - 1]) };
}

const BUCKETS = [[0, 1, "<1s"], [1, 2, "1-2s"], [2, 4, "2-4s"], [4, 8, "4-8s"], [8, Infinity, ">8s"]];

// Shape of one sequence as it stands: shot lengths on V1, gaps, J/L cuts.
export function sequenceShape(seq) {
  const fps = seq.fps || 25;
  const v1 = (seq.video || []).find((t) => t.index === 0) || { clips: [] };
  const a1 = (seq.audio || []).find((t) => t.index === 0) || { clips: [] };
  const shots = v1.clips.map((c) => c.end - c.start);
  const hist = {};
  for (const [lo, hi, label] of BUCKETS) hist[label] = shots.filter((d) => d >= lo && d < hi).length;
  const gaps = [];
  for (let i = 1; i < v1.clips.length; i++) { const g = v1.clips[i].start - v1.clips[i - 1].end; if (g > 0.5 / fps) gaps.push(r3(g)); }
  const vCuts = v1.clips.slice(1).map((c) => c.start);
  const aCuts = a1.clips.slice(1).map((c) => c.start);
  const jl = [];
  for (const ac of aCuts) {
    const nearest = vCuts.reduce((best, vc) => (best == null || Math.abs(vc - ac) < Math.abs(best - ac) ? vc : best), null);
    if (nearest != null) { const off = Math.round((ac - nearest) * fps); if (Math.abs(off) >= 2 && Math.abs(off) <= fps * 3) jl.push(off); }
  }
  const duration = seq.end || (v1.clips.length ? v1.clips[v1.clips.length - 1].end : 0);
  return {
    sequence: seq.name, fps: r3(fps), width: seq.width, height: seq.height, duration: r3(duration),
    shotsV1: v1.clips.length, shotLength: stats(shots), shotLengthFrames: stats(shots.map((d) => Math.round(d * fps))), histogram: hist,
    cutsPerMinute: duration ? r3(Math.max(v1.clips.length - 1, 0) / (duration / 60)) : null,
    gapsV1: gaps.length, gapSeconds: gaps.slice(0, 20),
    videoTracksUsed: (seq.video || []).filter((t) => t.clipCount).length, audioTracksUsed: (seq.audio || []).filter((t) => t.clipCount).length,
    jlCuts: { count: jl.length, ofAudioCuts: aCuts.length, offsetsFrames: stats(jl.map(Math.abs)), jLeading: jl.filter((o) => o < 0).length, lTrailing: jl.filter((o) => o > 0).length },
  };
}

// What the events say about the hand: how much is trimmed and where,
// how often cuts move, how many razors, at what tempo.
export function handReport(events, snapshots) {
  const byType = {};
  for (const e of events) byType[e.type] = (byType[e.type] || 0) + 1;
  const heads = events.filter((e) => e.type === "trim_head"), tails = events.filter((e) => e.type === "trim_tail");
  const times = [...new Set(events.map((e) => e.t))].sort();
  let activeMinutes = 0;
  for (let i = 1; i < times.length; i++) { const d = (new Date(times[i]) - new Date(times[i - 1])) / 60000; if (d <= 30) activeMinutes += d; }
  const rep = {
    events: events.length, byType, snapshots: snapshots.length,
    period: times.length ? { from: times[0], to: times[times.length - 1], activeMinutes: Math.round(activeMinutes) } : null,
    editsPerActiveHour: activeMinutes ? r3(events.length / (activeMinutes / 60)) : null,
    trimHead: { n: heads.length, shortened: heads.filter((e) => e.shortened).length, framesAbs: stats(heads.map((e) => Math.abs(e.frames))) },
    trimTail: { n: tails.length, shortened: tails.filter((e) => e.shortened).length, framesAbs: stats(tails.map((e) => Math.abs(e.frames))) },
    moves: stats(events.filter((e) => e.type === "move").map((e) => Math.abs(e.frames))),
    splits: events.filter((e) => e.type === "split").length,
    removedShotFrames: stats(events.filter((e) => e.type === "removed" && e.kind === "video").map((e) => e.durationFrames)),
    addedShotFrames: stats(events.filter((e) => e.type === "added" && e.kind === "video").map((e) => e.durationFrames)),
    videoVsAudioEdits: { video: events.filter((e) => e.kind === "video").length, audio: events.filter((e) => e.kind === "audio").length },
    bySequence: {},
  };
  for (const e of events) rep.bySequence[e.sequence] = (rep.bySequence[e.sequence] || 0) + 1;
  return rep;
}

export function report(dir) {
  const files = listSnapshots(dir);
  const events = readEvents(dir);
  const latest = files.length ? loadSnapshot(files[files.length - 1]) : null;
  return {
    dir, snapshots: files.length, first: files.length ? loadSnapshot(files[0]).t : null, last: latest ? latest.t : null,
    project: latest ? latest.project : null,
    shapes: latest ? latest.sequences.map(sequenceShape) : [],
    hand: handReport(events, files),
  };
}

export function reportMarkdown(r) {
  const L = [];
  L.push(`# Editor's journal: ${r.project || basename(r.dir)}`);
  L.push(`${r.snapshots} snapshots, ${r.first || "-"} to ${r.last || "-"}; ${r.hand.events} decisions recorded.`);
  if (r.hand.period) L.push(`Active editing: ~${r.hand.period.activeMinutes} min, ${r.hand.editsPerActiveHour ?? "-"} decisions per active hour.`);
  L.push("");
  L.push("## Decisions");
  for (const [k, v] of Object.entries(r.hand.byType).sort((a, b) => b[1] - a[1])) L.push(`- ${k}: ${v}`);
  const th = r.hand.trimHead, tt = r.hand.trimTail;
  if (th.n) L.push(`- head trims: ${th.n} (${th.shortened} shortened), median ${th.framesAbs.median} frames, p90 ${th.framesAbs.p90}`);
  if (tt.n) L.push(`- tail trims: ${tt.n} (${tt.shortened} shortened), median ${tt.framesAbs.median} frames, p90 ${tt.framesAbs.p90}`);
  if (r.hand.moves.n) L.push(`- moves: ${r.hand.moves.n}, median ${r.hand.moves.median} frames`);
  if (r.hand.splits) L.push(`- razor splits: ${r.hand.splits}`);
  L.push("");
  L.push("## Sequences as they stand");
  for (const s of r.shapes) {
    L.push(`### ${s.sequence} (${s.width}x${s.height}, ${s.fps} fps, ${s.duration} s)`);
    L.push(`- V1 shots: ${s.shotsV1}, length median ${s.shotLength.median ?? "-"} s (p10 ${s.shotLength.p10 ?? "-"}, p90 ${s.shotLength.p90 ?? "-"}), ${s.cutsPerMinute ?? "-"} cuts/min`);
    L.push(`- histogram: ${Object.entries(s.histogram).map(([k, v]) => `${k} ${v}`).join(", ")}`);
    L.push(`- J/L cuts: ${s.jlCuts.count} of ${s.jlCuts.ofAudioCuts} audio cuts (J ${s.jlCuts.jLeading}, L ${s.jlCuts.lTrailing}), median offset ${s.jlCuts.offsetsFrames.median ?? "-"} frames`);
    if (s.gapsV1) L.push(`- gaps on V1: ${s.gapsV1}`);
    L.push(`- tracks used: V ${s.videoTracksUsed}, A ${s.audioTracksUsed}`);
  }
  return L.join("\n");
}

// Add a snapshot to the journal: save it, diff against the previous one,
// append the events. Returns what happened.
export function record(dir, snap, meta = {}) {
  const r = saveSnapshot(dir, snap);
  if (!r.saved) return { saved: false, file: r.file, hash: r.hash, events: 0 };
  let events = 0, diff = null;
  if (r.previous) {
    diff = diffSnapshots(r.previous, snap);
    events = appendEvents(dir, eventRows(diff, meta));
  }
  return { saved: true, file: r.file, hash: r.hash, events, diff };
}

export function fileMtime(path) {
  try { return statSync(path).mtime; } catch { return null; }
}
