// Sequence builders: ExtendScript (ES3) function bodies for the panel's
// evalRaw path and for pk.js. Every builder returns a body with an explicit
// top-level `return`; non-ASCII goes in as \uXXXX (see evalcode.jsxString).
//
// All of it was run live on Premiere 26.0.2 with a Russian UI (24.10.2026):
//   - createNewSequence(name, presetId) with an id that is not an installed
//     preset opens the "Создать эпизод" dialog and the bridge hangs. The
//     dialog-free path is item.setInPoint/setOutPoint + createNewSequenceFromClips
//     + getSettings/setSettings.
//   - Motion properties are found by localized displayName (RU and EN).
//   - Keyframe times (addKey / setValueAtKey) are MEDIA time: seconds from
//     the start of the source file, i.e. clip.inPoint + offset. The API
//     answers getValueAtTime consistently in whatever base you used, so only
//     a frame export shows the mistake (24.10.2026: a key "at 0" on a clip
//     with inPoint 9.98 s never played; the next key did, from the start).
//   - app.project.deleteSequence(seq), seq.projectItem.moveBin(bin),
//     seq.markers.deleteMarker(m), seq.setInPoint(sec) all exist and work.

import { jsxString as S } from "./evalcode.js";

export const TICKS_PER_SECOND = 254016000000;

// Localized component / property names: Russian first (Vladimir's install), English second.
export const NAMES = {
  motion: ["Движение", "Motion"],
  opacity: ["Непрозрачность", "Opacity"],
  scale: ["Масштаб", "Scale"],
  position: ["Положение", "Position"],
  rotation: ["Поворот", "Rotation"],
  anchor: ["Опорная точка", "Anchor Point"],
  uniform: ["Равномерный масштаб", "Uniform Scale"],
};

function namesJs() {
  const parts = [];
  for (const [k, v] of Object.entries(NAMES)) parts.push(`${k}: [${v.map(S).join(", ")}]`);
  return `{ ${parts.join(", ")} }`;
}

// Shared ES3 helpers. Pure JS apart from the `app` / `ProjectItemType` / `Time`
// globals Premiere provides, so the tests can run them against a fake DOM.
export const LIB = `
var TPS = 254016000000;
var NAMES = ${namesJs()};
function __has(list, name) { for (var i = 0; i < list.length; i++) if (list[i] === name) return true; return false; }
function __fpsTicks(fps) {
  fps = Number(fps);
  var ntsc = [[23.976, 10594584000], [29.97, 8475667200], [59.94, 4237833600], [47.952, 5297292000], [119.88, 2118916800]];
  for (var i = 0; i < ntsc.length; i++) if (Math.abs(fps - ntsc[i][0]) < 0.01) return String(ntsc[i][1]);
  return String(Math.round(TPS / fps));
}
function __ticksFps(ticks) { var t = Number(ticks); return t > 0 ? TPS / t : 0; }
function __isBin(item) { return item.type === ProjectItemType.BIN; }
function __childBin(parent, name) {
  for (var i = 0; i < parent.children.numItems; i++) {
    var c = parent.children[i];
    if (__isBin(c) && c.name === name) return c;
  }
  return null;
}
// "A/B/C" from the project root; creates missing levels when create is true.
function __binPath(path, create) {
  var cur = app.project.rootItem;
  if (!path) return cur;
  var parts = String(path).split("/");
  for (var i = 0; i < parts.length; i++) {
    if (!parts[i]) continue;
    var next = __childBin(cur, parts[i]);
    if (!next) {
      if (!create) return null;
      cur.createBin(parts[i]);
      next = __childBin(cur, parts[i]);
      if (!next) return null;
    }
    cur = next;
  }
  return cur;
}
function __treePath(item) { try { return item.treePath; } catch (e) { return ""; } }
function __mediaPath(item) { try { return item.getMediaPath ? item.getMediaPath() : ""; } catch (e) { return ""; } }
function __fsName(p) { try { return new File(p).fsName; } catch (e) { return String(p); } }
function __samePath(p, want) { return !!p && (p === want || __fsName(p) === want); }
function __walk(bin, fn, depth) {
  if (!bin || !bin.children) return null;
  for (var i = 0; i < bin.children.numItems; i++) {
    var c = bin.children[i];
    if (fn(c)) return c;
    if (__isBin(c) && depth < 12) { var r = __walk(c, fn, depth + 1); if (r) return r; }
  }
  return null;
}
function __byNode(id) { return __walk(app.project.rootItem, function (c) { return c.nodeId === id; }, 0); }
function __byMedia(path) {
  var want = __fsName(path);
  return __walk(app.project.rootItem, function (c) { return !__isBin(c) && __samePath(__mediaPath(c), want); }, 0);
}
function __byName(name, bin) {
  var start = bin || app.project.rootItem;
  return __walk(start, function (c) { return c.name === name; }, 0);
}
// ref: nodeId, absolute path, or item name (searched under bin, then the whole project).
function __item(ref, bin) {
  if (!ref) return null;
  var it = null;
  if (/^[0-9a-f]{6,}$/i.test(ref)) it = __byNode(ref);
  if (!it && String(ref).charAt(0) === "/") it = __byMedia(ref);
  if (!it && bin) it = __byName(ref, bin);
  if (!it) it = __byName(ref, null);
  if (!it && String(ref).charAt(0) === "/") it = __byName(String(ref).split("/").pop(), bin);
  return it;
}
function __seq(ref) {
  var list = app.project.sequences;
  if (!ref) return app.project.activeSequence;
  for (var i = 0; i < list.numSequences; i++) if (list[i].sequenceID === ref) return list[i];
  for (var j = 0; j < list.numSequences; j++) if (list[j].name === ref) return list[j];
  return null;
}
function __seqItem(seq) { try { return seq.projectItem; } catch (e) { return null; } }
function __sec(t) { try { var v = t.seconds; return (typeof v === "number") ? v : null; } catch (e) { return null; } }
function __ticks(t) { try { return String(t.ticks); } catch (e) { return null; } }
function __fps(seq) { return __ticksFps(seq.timebase); }
function __settings(seq) {
  var st = seq.getSettings();
  var ticks = st.videoFrameRate ? String(st.videoFrameRate.ticks) : String(seq.timebase);
  return { width: st.videoFrameWidth, height: st.videoFrameHeight, fps: __ticksFps(ticks), fpsTicks: ticks,
           par: st.videoPixelAspectRatio, audioSampleRateTicks: st.audioSampleRate ? String(st.audioSampleRate.ticks) : null };
}
function __clipRow(c) {
  var r = { name: c.name, start: __sec(c.start), end: __sec(c.end), inPoint: __sec(c.inPoint), outPoint: __sec(c.outPoint),
            duration: __sec(c.duration), startTicks: __ticks(c.start), endTicks: __ticks(c.end), mediaType: c.mediaType };
  try { r.nodeId = c.projectItem ? c.projectItem.nodeId : null; } catch (e) { r.nodeId = null; }
  try { r.path = c.projectItem ? __mediaPath(c.projectItem) : ""; } catch (e) { r.path = ""; }
  try { r.disabled = c.disabled; } catch (e) {}
  return r;
}
function __tracks(coll, kind) {
  var out = [];
  for (var i = 0; i < coll.numTracks; i++) {
    var t = coll[i], clips = [];
    for (var j = 0; j < t.clips.numItems; j++) clips.push(__clipRow(t.clips[j]));
    out.push({ kind: kind, index: i, name: t.name || "", clipCount: clips.length, clips: clips });
  }
  return out;
}
function __markers(seq) {
  var out = [], ms = seq.markers, m = ms.numMarkers ? ms.getFirstMarker() : null;
  while (m) {
    out.push({ t: __sec(m.start), end: __sec(m.end), name: m.name, comment: m.comments, type: m.type });
    m = ms.getNextMarker(m);
  }
  return out;
}
function __seqInfo(seq) {
  var info = { name: seq.name, sequenceID: seq.sequenceID, end: __sec(seq.end) !== null ? __sec(seq.end) : Number(String(seq.end)) / TPS,
               videoTracks: seq.videoTracks.numTracks, audioTracks: seq.audioTracks.numTracks, markers: seq.markers.numMarkers };
  try { var st = __settings(seq); info.width = st.width; info.height = st.height; info.fps = st.fps; info.fpsTicks = st.fpsTicks; }
  catch (e) { info.width = seq.frameSizeHorizontal; info.height = seq.frameSizeVertical; info.fps = __fps(seq); }
  var si = __seqItem(seq); if (si) { info.nodeId = si.nodeId; info.treePath = __treePath(si); }
  return info;
}
function __clip(seq, track, index, kind) {
  var coll = kind === "audio" ? seq.audioTracks : seq.videoTracks;
  if (track < 0 || track >= coll.numTracks) return null;
  var clips = coll[track].clips;
  if (index < 0 || index >= clips.numItems) return null;
  return clips[index];
}
function __component(clip, names) {
  for (var k = 0; k < clip.components.numItems; k++) { var cp = clip.components[k]; if (__has(names, cp.displayName)) return cp; }
  return null;
}
function __property(comp, names) {
  for (var m = 0; m < comp.properties.numItems; m++) { var pr = comp.properties[m]; if (__has(names, pr.displayName)) return pr; }
  return null;
}
function __motion(clip) {
  var comp = __component(clip, NAMES.motion);
  if (!comp) return null;
  return { comp: comp, scale: __property(comp, NAMES.scale), position: __property(comp, NAMES.position),
           rotation: __property(comp, NAMES.rotation), anchor: __property(comp, NAMES.anchor), uniform: __property(comp, NAMES.uniform) };
}
function __val(pr) { try { return pr ? pr.getValue() : null; } catch (e) { return null; } }
function __fpsOf(item) { try { return item.getFootageInterpretation().frameRate; } catch (e) { return null; } }
function __itemRow(c) {
  var r = { name: c.name, nodeId: c.nodeId, type: __isBin(c) ? "bin" : (c.type === ProjectItemType.CLIP ? "clip" : (c.type === ProjectItemType.FILE ? "file" : String(c.type))), treePath: __treePath(c) };
  if (__isBin(c)) { r.children = c.children.numItems; return r; }
  r.path = __mediaPath(c);
  if (r.path) { var f = __fpsOf(c); r.fps = f; r.videoDecoded = !!(f && f > 0.5); }
  else { var sq = __seq(c.name); if (sq && __seqItem(sq) && __seqItem(sq).nodeId === c.nodeId) { r.type = "sequence"; r.sequenceID = sq.sequenceID; } }
  return r;
}
`;

// ---- builders -------------------------------------------------------------

export function pingJsx() {
  return `${LIB}
var p = app.project, seq = p ? p.activeSequence : null;
var out = { version: app.version, build: app.build, project: p ? p.name : null, projectPath: p ? p.path : null,
            sequences: p ? p.sequences.numSequences : 0, activeSequence: null, t: new Date().getTime() };
if (seq) out.activeSequence = __seqInfo(seq);
return out;
`;
}

export function listBinJsx({ bin, recursive }) {
  return `${LIB}
var BIN = ${S(bin || "")};
var b = __binPath(BIN, false);
if (!b) return { error: "Bin not found: " + BIN };
var rows = [];
function dump(parent, depth) {
  for (var i = 0; i < parent.children.numItems; i++) {
    var c = parent.children[i], row = __itemRow(c); row.depth = depth; rows.push(row);
    if (${recursive ? "true" : "false"} && __isBin(c) && depth < 12) dump(c, depth + 1);
  }
}
dump(b, 0);
return { bin: BIN || "/", treePath: __treePath(b), count: rows.length, items: rows };
`;
}

// One media file. suppressUI=true: one mp4 per call went through live without
// a dialog (24.10.2026). The caller gates the codec and the disk before this.
export function importMediaJsx({ path, bin, reuse }) {
  return `${LIB}
var PATH = ${S(path)}, BIN = ${S(bin || "")}, REUSE = ${reuse === false ? "false" : "true"};
var f = new File(PATH);
if (!f.exists) return { error: "File not found: " + PATH };
var want = f.fsName;
if (REUSE) { var ex = __byMedia(want); if (ex) { var r0 = __itemRow(ex); r0.reused = true; return r0; } }
var target = __binPath(BIN, true);
if (!target) return { error: "Could not create bin " + BIN };
var seen = {};
for (var i = 0; i < target.children.numItems; i++) seen[target.children[i].nodeId] = true;
var ret = app.project.importFiles([want], true, target, false);
var found = null;
for (var j = 0; j < target.children.numItems; j++) {
  var c = target.children[j];
  if (seen[c.nodeId]) continue;
  if (__samePath(__mediaPath(c), want)) { found = c; break; }
  if (!found) found = c;
}
if (!found) found = __byMedia(want);
if (!found) return { error: "importFiles returned " + ret + " but no new item appeared", path: want };
var row = __itemRow(found); row.reused = false; row.importReturned = ret;
return row;
`;
}

// Sequence from one clip range, no dialog. `exists`: error | replace | skip.
export function makeSequenceJsx({ name, item, bin, inSec, outSec, width, height, fps, exists }) {
  return `${LIB}
var NAME = ${S(name)}, ITEM = ${S(item)}, BIN = ${S(bin || "")}, EXISTS = ${S(exists || "error")};
var IN = ${inSec == null ? "null" : Number(inSec)}, OUT = ${outSec == null ? "null" : Number(outSec)};
var W = ${width == null ? "null" : Number(width)}, H = ${height == null ? "null" : Number(height)}, FPS = ${fps == null ? "null" : Number(fps)};
var binItem = BIN ? __binPath(BIN, true) : app.project.rootItem;
if (!binItem) return { error: "Could not create bin " + BIN };
var item = __item(ITEM, binItem);
if (!item) return { error: "Project item not found: " + ITEM + " (import it first)" };
if (__isBin(item)) return { error: "Not a clip: " + ITEM };
var old = __seq(NAME);
if (old) {
  if (EXISTS === "skip") { var info0 = __seqInfo(old); info0.skipped = true; return info0; }
  if (EXISTS !== "replace") return { error: "Sequence already exists: " + NAME + " (exists=replace to rebuild, skip to keep)" };
  if (!app.project.deleteSequence(old)) return { error: "Could not delete the old sequence " + NAME };
}
if (IN !== null) item.setInPoint(IN, 4); else if (item.clearInPoint) item.clearInPoint(4);
if (OUT !== null) item.setOutPoint(OUT, 4); else if (item.clearOutPoint) item.clearOutPoint(4);
var seq = app.project.createNewSequenceFromClips(NAME, [item], binItem);
if (!seq) return { error: "createNewSequenceFromClips returned nothing for " + NAME };
try { if (item.clearInPoint) { item.clearInPoint(4); item.clearOutPoint(4); } } catch (e) {}
var st = seq.getSettings(), changed = false;
if (W !== null) { st.videoFrameWidth = W; changed = true; }
if (H !== null) { st.videoFrameHeight = H; changed = true; }
if (FPS !== null) { var fr = new Time(); fr.ticks = __fpsTicks(FPS); st.videoFrameRate = fr; changed = true; }
if (changed) seq.setSettings(st);
try { var si = __seqItem(seq); if (si && BIN) si.moveBin(binItem); } catch (e) {}
var info = __seqInfo(seq);
info.replaced = !!old;
info.video = __tracks(seq.videoTracks, "video");
info.audio = __tracks(seq.audioTracks, "audio");
var v0 = seq.videoTracks.numTracks ? seq.videoTracks[0].clips.numItems : 0;
var a0 = seq.audioTracks.numTracks ? seq.audioTracks[0].clips.numItems : 0;
info.check = { v1Clips: v0, a1Clips: a0, sourceFps: __fpsOf(item), sourceVideoDecoded: !!(__fpsOf(item) && __fpsOf(item) > 0.5) };
if (W !== null && info.width !== W) info.error = "width read back " + info.width + ", wanted " + W;
if (H !== null && info.height !== H) info.error = "height read back " + info.height + ", wanted " + H;
if (!v0 && info.check.sourceVideoDecoded) info.error = "no clip on V1 after createNewSequenceFromClips";
if (!v0 && !info.check.sourceVideoDecoded) info.error = "V1 is empty: Premiere decodes no video from this file (footage fps " + __fpsOf(item) + "); make an H.264 copy";
return info;
`;
}

// Motion on one clip. Position in px of the sequence frame by default, or
// normalized 0..1 with units "norm". Reads the values back.
export function setMotionJsx({ seq, track, clip, scale, posX, posY, rotation, anchorX, anchorY, units }) {
  const num = (v) => (v == null || v === "" ? "null" : Number(v));
  return `${LIB}
var SEQ = ${S(seq || "")}, T = ${Number(track) || 0}, C = ${Number(clip) || 0}, UNITS = ${S(units || "px")};
var SCALE = ${num(scale)}, PX = ${num(posX)}, PY = ${num(posY)}, ROT = ${num(rotation)}, AX = ${num(anchorX)}, AY = ${num(anchorY)};
var seq = __seq(SEQ);
if (!seq) return { error: "Sequence not found: " + (SEQ || "(active)") };
var cl = __clip(seq, T, C, "video");
if (!cl) return { error: "No clip at V" + (T + 1) + " index " + C + " in " + seq.name };
var mo = __motion(cl);
if (!mo) return { error: "Motion component not found on " + cl.name + " (names tried: " + NAMES.motion.join(", ") + ")" };
var st = __settings(seq), w = st.width, h = st.height, set = [];
function nx(v) { return UNITS === "norm" ? v : v / w; }
function ny(v) { return UNITS === "norm" ? v : v / h; }
if (SCALE !== null) { if (!mo.scale) return { error: "Scale property not found" }; mo.scale.setValue(SCALE, 1); set.push("scale"); }
if (PX !== null || PY !== null) {
  if (!mo.position) return { error: "Position property not found" };
  var cur = __val(mo.position) || [0.5, 0.5];
  mo.position.setValue([PX === null ? cur[0] : nx(PX), PY === null ? cur[1] : ny(PY)], 1); set.push("position");
}
if (ROT !== null) { if (!mo.rotation) return { error: "Rotation property not found" }; mo.rotation.setValue(ROT, 1); set.push("rotation"); }
if (AX !== null || AY !== null) {
  if (!mo.anchor) return { error: "Anchor Point property not found" };
  var ca = __val(mo.anchor) || [0.5, 0.5];
  mo.anchor.setValue([AX === null ? ca[0] : nx(AX), AY === null ? ca[1] : ny(AY)], 1); set.push("anchor");
}
var fresh = __motion(__clip(seq, T, C, "video"));
var pos = __val(fresh.position), anc = __val(fresh.anchor);
return { sequence: seq.name, track: T, clip: C, name: cl.name, set: set, width: w, height: h,
         scale: __val(fresh.scale), positionNorm: pos, positionPx: pos ? [pos[0] * w, pos[1] * h] : null,
         rotation: __val(fresh.rotation), anchorNorm: anc, anchorPx: anc ? [anc[0] * w, anc[1] * h] : null,
         positionKeyframed: fresh.position ? fresh.position.isTimeVarying() : null };
`;
}

// Keyframes on Motion: keys = [{t, scale?, posX?, posY?, rotation?}], t in
// seconds from the clip's start on the timeline; the builder adds the clip's
// inPoint because Premiere places keys in media time. `hold` = no interpolation.
export function setMotionKeyframesJsx({ seq, track, clip, keys, units, hold, clear }) {
  const rows = (keys || []).map((k) => ({
    t: Number(k.t) || 0,
    scale: k.scale == null ? null : Number(k.scale),
    posX: k.posX == null ? null : Number(k.posX),
    posY: k.posY == null ? null : Number(k.posY),
    rotation: k.rotation == null ? null : Number(k.rotation),
  }));
  return `${LIB}
var SEQ = ${S(seq || "")}, T = ${Number(track) || 0}, C = ${Number(clip) || 0}, UNITS = ${S(units || "px")};
var HOLD = ${hold ? "true" : "false"}, CLEAR = ${clear === false ? "false" : "true"};
var KEYS = ${JSON.stringify(rows)};
var seq = __seq(SEQ);
if (!seq) return { error: "Sequence not found: " + (SEQ || "(active)") };
var cl = __clip(seq, T, C, "video");
if (!cl) return { error: "No clip at V" + (T + 1) + " index " + C + " in " + seq.name };
var mo = __motion(cl);
if (!mo) return { error: "Motion component not found on " + cl.name };
var st = __settings(seq), w = st.width, h = st.height;
var inP = __sec(cl.inPoint) || 0; // keys live in media time
function nx(v) { return UNITS === "norm" ? v : v / w; }
function ny(v) { return UNITS === "norm" ? v : v / h; }
function clearKeys(pr) {
  if (!pr || !pr.isTimeVarying()) return;
  var ks = pr.getKeys() || [];
  for (var i = ks.length - 1; i >= 0; i--) { try { pr.removeKey(ks[i]); } catch (e) {} }
  try { pr.setTimeVarying(false); } catch (e2) {}
}
function putKey(pr, tClip, v) {
  var t = inP + tClip;
  if (!pr.isTimeVarying()) pr.setTimeVarying(true);
  pr.addKey(t);
  pr.setValueAtKey(t, v, 1);
  if (HOLD) { try { pr.setInterpolationTypeAtKey(t, 4, 1); } catch (e) {} } // 4 = kfInterpMode_Hold (5 is Bezier: checked live, Bezier still slid between shots)
}
var usesScale = false, usesPos = false, usesRot = false;
for (var i = 0; i < KEYS.length; i++) { if (KEYS[i].scale !== null) usesScale = true; if (KEYS[i].posX !== null || KEYS[i].posY !== null) usesPos = true; if (KEYS[i].rotation !== null) usesRot = true; }
if (usesScale && !mo.scale) return { error: "Scale property not found" };
if (usesPos && !mo.position) return { error: "Position property not found" };
if (usesRot && !mo.rotation) return { error: "Rotation property not found" };
if (CLEAR) { if (usesScale) clearKeys(mo.scale); if (usesPos) clearKeys(mo.position); if (usesRot) clearKeys(mo.rotation); }
var basePos = __val(mo.position) || [0.5, 0.5];
var count = 0;
for (var k = 0; k < KEYS.length; k++) {
  var key = KEYS[k];
  if (key.scale !== null) { putKey(mo.scale, key.t, key.scale); count++; }
  if (key.posX !== null || key.posY !== null) {
    var v = [key.posX === null ? basePos[0] : nx(key.posX), key.posY === null ? basePos[1] : ny(key.posY)];
    putKey(mo.position, key.t, v); basePos = v; count++;
  }
  if (key.rotation !== null) { putKey(mo.rotation, key.t, key.rotation); count++; }
}
var fresh = __motion(__clip(seq, T, C, "video"));
function dump(pr, isPos) {
  if (!pr || !pr.isTimeVarying()) return [];
  var ks = pr.getKeys() || [], out = [];
  for (var i = 0; i < ks.length; i++) { var v = pr.getValueAtKey(ks[i]); out.push({ t: __sec(ks[i]) - inP, tMedia: __sec(ks[i]), value: isPos && v ? [v[0] * w, v[1] * h] : v }); }
  return out;
}
return { sequence: seq.name, track: T, clip: C, name: cl.name, width: w, height: h, clipInPoint: inP, keysWritten: count,
         scaleKeys: dump(fresh.scale, false), positionKeysPx: dump(fresh.position, true), rotationKeys: dump(fresh.rotation, false) };
`;
}

// Markers in one call. markers = [{t, name, comment, duration?}]; clear removes the old ones first.
export function addMarkersJsx({ seq, markers, clear }) {
  const rows = (markers || []).map((m) => ({
    t: Number(m.t) || 0, name: String(m.name == null ? "" : m.name), comment: String(m.comment == null ? "" : m.comment),
    duration: m.duration == null ? null : Number(m.duration),
  }));
  return `${LIB}
var SEQ = ${S(seq || "")}, CLEAR = ${clear ? "true" : "false"};
var M = ${JSON.stringify(rows).replace(/[\u007f-￿]/g, (ch) => "\\u" + ch.charCodeAt(0).toString(16).padStart(4, "0"))};
var seq = __seq(SEQ);
if (!seq) return { error: "Sequence not found: " + (SEQ || "(active)") };
var removed = 0;
if (CLEAR) {
  var ms = seq.markers, m = ms.numMarkers ? ms.getFirstMarker() : null, all = [];
  while (m) { all.push(m); m = ms.getNextMarker(m); }
  for (var i = 0; i < all.length; i++) { if (ms.deleteMarker(all[i])) removed++; }
}
var before = seq.markers.numMarkers, made = 0;
for (var k = 0; k < M.length; k++) {
  var mk = seq.markers.createMarker(M[k].t);
  if (!mk) continue;
  if (M[k].name) mk.name = M[k].name;
  if (M[k].comment) mk.comments = M[k].comment;
  if (M[k].duration !== null) { try { mk.end = M[k].t + M[k].duration; } catch (x) {} }
  made++;
}
var after = seq.markers.numMarkers;
var out = { sequence: seq.name, requested: M.length, created: made, removed: removed, markersBefore: before, markersAfter: after };
if (after - before !== M.length) out.error = "marker count grew by " + (after - before) + ", expected " + M.length;
return out;
`;
}

export function listMarkersJsx({ seq }) {
  return `${LIB}
var seq = __seq(${S(seq || "")});
if (!seq) return { error: "Sequence not found: " + ${S(seq || "(active)")} };
return { sequence: seq.name, count: seq.markers.numMarkers, markers: __markers(seq) };
`;
}

// Open sequences as tabs (openSequence takes the sequenceID). The last one
// becomes active unless `activate` names another.
export function openSequencesJsx({ names, activate }) {
  return `${LIB}
var NAMES_ = ${JSON.stringify(names || []).replace(/[\u007f-￿]/g, (ch) => "\\u" + ch.charCodeAt(0).toString(16).padStart(4, "0"))};
var ACT = ${S(activate || "")};
var opened = [], missing = [];
for (var i = 0; i < NAMES_.length; i++) {
  var s = __seq(NAMES_[i]);
  if (!s) { missing.push(NAMES_[i]); continue; }
  var r = app.project.openSequence(s.sequenceID);
  opened.push({ name: s.name, sequenceID: s.sequenceID, returned: r });
}
if (ACT) { var a = __seq(ACT); if (a) app.project.openSequence(a.sequenceID); else missing.push(ACT); }
var act = app.project.activeSequence;
var out = { opened: opened, missing: missing, activeSequence: act ? act.name : null };
if (missing.length) out.error = "Not found: " + missing.join(", ");
return out;
`;
}

// Timeline of a named sequence (not only the active one), with the sequence
// in/out and work area: the render range after a manual trim.
export function getTimelineJsx({ seq, withMarkers }) {
  return `${LIB}
var seq = __seq(${S(seq || "")});
if (!seq) return { error: "Sequence not found: " + ${S(seq || "(active)")} };
var info = __seqInfo(seq);
info.video = __tracks(seq.videoTracks, "video");
info.audio = __tracks(seq.audioTracks, "audio");
var ip = null, op = null;
try { ip = seq.getInPointAsTime().seconds; op = seq.getOutPointAsTime().seconds; } catch (e) {}
var hasIO = ip !== null && ip >= 0 && op !== null && op > 0;
info.inPoint = hasIO ? ip : null; info.outPoint = hasIO ? op : null;
try { info.workArea = { enabled: seq.isWorkAreaEnabled(), start: seq.getWorkAreaInPointAsTime().seconds, end: seq.getWorkAreaOutPointAsTime().seconds }; } catch (e2) {}
var first = null, last = null;
for (var t = 0; t < seq.videoTracks.numTracks; t++) {
  var cs = seq.videoTracks[t].clips;
  for (var c = 0; c < cs.numItems; c++) { var s0 = __sec(cs[c].start), e0 = __sec(cs[c].end); if (first === null || s0 < first) first = s0; if (last === null || e0 > last) last = e0; }
}
info.clipsRange = first === null ? null : [first, last];
info.renderRange = hasIO ? [ip, op] : (first === null ? [0, info.end] : [first, last]);
if (${withMarkers ? "true" : "false"}) info.markerList = __markers(seq);
return info;
`;
}

export function deleteSequenceJsx({ name }) {
  return `${LIB}
var NAME = ${S(name)};
var seq = __seq(NAME);
if (!seq) return { error: "Sequence not found: " + NAME };
var id = seq.sequenceID, nm = seq.name, before = app.project.sequences.numSequences;
var r = app.project.deleteSequence(seq);
var after = app.project.sequences.numSequences;
var still = __seq(id);
if (still) return { error: "deleteSequence returned " + r + " but the sequence is still there", name: nm };
return { deleted: nm, sequenceID: id, sequencesBefore: before, sequencesAfter: after,
         activeSequence: app.project.activeSequence ? app.project.activeSequence.name : null };
`;
}

export function moveToBinJsx({ item, bin }) {
  return `${LIB}
var ITEM = ${S(item)}, BIN = ${S(bin || "")};
var target = __binPath(BIN, true);
if (!target) return { error: "Could not create bin " + BIN };
var it = __item(ITEM, null);
if (!it) { var sq = __seq(ITEM); if (sq) it = __seqItem(sq); }
if (!it) return { error: "Project item not found: " + ITEM };
var from = __treePath(it);
it.moveBin(target);
var row = __itemRow(it); row.from = from;
if (__treePath(it) === from && from.indexOf(target.name) < 0) row.error = "treePath did not change";
return row;
`;
}

export function saveProjectJsx() {
  return `${LIB}
var p = app.project; if (!p) return { error: "No project" };
var r = p.save();
return { saved: true, returned: r, path: p.path, name: p.name };
`;
}
