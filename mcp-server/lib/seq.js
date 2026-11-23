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

// The project and one sequence (by name or id, default the active one) with
// its marker count: the light read around a marker or Motion tool call.
export function actionContextJsx({ seq } = {}) {
  return `${LIB}
var p = app.project;
if (!p) return { error: "No project open" };
var s = __seq(${S(seq || "")});
return { project: p.name, sequence: s ? s.name : null, sequenceID: s ? s.sequenceID : null, markers: s ? s.markers.numMarkers : null };
`;
}

// ---- the light timeline read around an AI call ------------------------------
// Only what a diff needs: clip name, media path and four times. A media path is
// asked once per project item. Every property read is a hop to Premiere's UI
// thread (0.05 ms to one 60 Hz frame, measured on 26.0.2), and the panel blocks
// Premiere while it runs, so the read is bounded: the deadline is checked
// before every sequence, track and clip row, and a sequence cut by it is
// dropped whole (its id goes to `over`).
//
// o = { limit, skip, only, active, want, known }
//   default  the active sequence first, the others in project order only while
//            less than half of the limit is used; ids in `skip` are not read,
//            and a skipped active sequence means nothing is read
//   only     just these ids or exact names (plus the active one when `active`),
//            in that order; the others are not read and not missing
//   want     the read after a call: the ids in `want`, plus every sequence not
//            in `known` (the listing before)
// The answer: project (always), active, listed (the listing of sequences is
// complete), read (ids read whole, in order), seqs (id -> row), all (listed
// ids), complete (listed and nothing left unread), over (ids cut by the deadline),
// rows and ms (clip rows read and time spent when a sequence was cut: under 0.2
// rows per ms means Premiere was in its slow regime, not that the sequence is big).
export const STATE_READER = `
function __bstSeq(s, id, end, paths) {
  var tb = Number(s.timebase);
  var row = { name: s.name, sequenceID: id, fps: tb > 0 ? 254016000000 / tb : 25, markers: s.markers.numMarkers, video: [], audio: [] };
  for (var g = 0; g < 2; g++) {
    if (new Date().getTime() > end) return null;
    var kind = g ? "audio" : "video", coll = g ? s.audioTracks : s.videoTracks, nt = coll.numTracks;
    for (var ti = 0; ti < nt; ti++) {
      if (new Date().getTime() > end) return null;
      var clips = coll[ti].clips, n = clips.numItems, out = [];
      for (var ci = 0; ci < n; ci++) {
        if (new Date().getTime() > end) return null;
        var c = clips[ci], pi = null, nid = null;
        try { pi = c.projectItem; nid = pi ? pi.nodeId : null; } catch (e0) {}
        if (nid !== null && paths[nid] === undefined) { try { paths[nid] = pi.getMediaPath ? pi.getMediaPath() : ""; } catch (e1) { paths[nid] = ""; } }
        out[ci] = { name: c.name, path: nid === null ? "" : paths[nid], start: c.start.seconds, end: c.end.seconds, inPoint: c.inPoint.seconds, outPoint: c.outPoint.seconds };
        paths.__rows++;
      }
      row[kind].push({ kind: kind, index: ti, clipCount: n, clips: out });
    }
  }
  return row;
}
function __bstRead(o) {
  var p = app.project;
  if (!p) return null;
  var t0 = new Date().getTime(), end = t0 + o.limit, paths = { __rows: 0 }, missing = false, i, k;
  var res = { project: p.name, active: null, listed: false, complete: false, all: {}, read: [], seqs: {}, over: [], rows: 0, ms: 0 };
  var act = p.activeSequence, aid = act ? act.sequenceID : null;
  res.active = aid;
  if (!o.only && !o.want && aid !== null && o.skip && o.skip[aid]) return res;
  var list = p.sequences, n = list.numSequences, items = [];
  for (i = 0; i < n; i++) {
    if (new Date().getTime() > end) return res;
    var s = list[i], it = { s: s, id: s.sequenceID };
    if (o.only) it.name = s.name;
    items.push(it);
    res.all[it.id] = 1;
  }
  res.listed = true;
  var order = [];
  if (o.only) {
    var want = (o.active && aid !== null) ? [aid] : [], taken = {};
    for (k = 0; k < o.only.length; k++) want.push(o.only[k]);
    for (k = 0; k < want.length; k++) for (i = 0; i < items.length; i++) if (!taken[i] && (items[i].id === want[k] || items[i].name === want[k])) { taken[i] = 1; order.push(items[i]); }
  } else {
    for (k = 0; k < 2; k++) for (i = 0; i < items.length; i++) {
      var q = items[i];
      if ((q.id === aid) !== (k === 0)) continue;
      if (o.want ? (o.want[q.id] || (o.known && !o.known[q.id])) : !(o.skip && o.skip[q.id])) order.push(q);
      else missing = true;
    }
  }
  for (k = 0; k < order.length; k++) {
    var x = order[k], now = new Date().getTime();
    if (now > end || (!o.only && !o.want && x.id !== aid && now - t0 >= o.limit / 2)) { missing = true; break; }
    var row = __bstSeq(x.s, x.id, end, paths);
    if (!row) { res.over.push(x.id); missing = true; res.rows = paths.__rows; res.ms = new Date().getTime() - t0; break; }
    res.read.push(x.id);
    res.seqs[x.id] = row;
  }
  res.complete = !missing;
  return res;
}
function __bstRows(st, only) {
  var out = [];
  for (var i = 0; i < st.read.length; i++) {
    var id = st.read[i], q = st.seqs[id];
    if (only && !only[id]) continue;
    out.push({ name: q.name, sequenceID: q.sequenceID, fps: q.fps, markers: q.markers, video: q.video, audio: q.audio });
  }
  return out;
}
function __bstSame(x, y) {
  if (x.name !== y.name || x.markers !== y.markers) return false;
  for (var g = 0; g < 2; g++) {
    var kind = g ? "audio" : "video", tx = x[kind], ty = y[kind];
    if (tx.length !== ty.length) return false;
    for (var t = 0; t < tx.length; t++) {
      var cx = tx[t].clips, cy = ty[t].clips;
      if (cx.length !== cy.length) return false;
      for (var i = 0; i < cx.length; i++) {
        var p = cx[i], q = cy[i];
        if (p.start !== q.start || p.end !== q.end || p.inPoint !== q.inPoint || p.outPoint !== q.outPoint || p.name !== q.name || p.path !== q.path) return false;
      }
    }
  }
  return true;
}
// Only sequences read on both sides are compared, plus a sequence that was not
// listed before (added) and one read before that a complete listing after no
// longer has (removed).
function __bstChanges(b, a) {
  var diff = {}, read = [], i, id;
  for (i = 0; i < b.read.length; i++) {
    id = b.read[i];
    if (a.seqs[id]) { read.push(id); if (!__bstSame(b.seqs[id], a.seqs[id])) diff[id] = 1; }
    else if (a.listed && !a.all[id]) { read.push(id); diff[id] = 1; }
  }
  for (i = 0; i < a.read.length; i++) { id = a.read[i]; if (!b.seqs[id] && b.listed && !b.all[id]) { read.push(id); diff[id] = 1; } }
  return { project: a.project, read: read, before: __bstRows(b, diff), after: __bstRows(a, diff) };
}
`;

const jsList = (xs) => `[${(xs || []).map(S).join(", ")}]`;
const jsSet = (xs) => `{${(xs || []).map((x) => `${S(x)}: 1`).join(", ")}}`;

// For tools that take several panel calls: every sequence, or with `only` /
// `active` just the ones the tool is asked to touch. A target that does not
// exist is a complete read of "absent".
export function timelineStateJsx({ limitMs = 5000, only, active = false } = {}) {
  const targeted = (only && only.length) || active;
  return `${STATE_READER}
var st = __bstRead({ limit: ${Number(limitMs)}, only: ${targeted ? jsList(only) : "null"}, active: ${!!active} });
if (!st) return { error: "No project open" };
if (!st.complete) return { error: "timeline read over the limit", complete: false, project: st.project, over: st.over };
return { project: st.project, active: st.active, complete: true, read: st.read, sequences: __bstRows(st, null) };
`;
}

// Run a pr_eval_jsx body between two reads in ONE script. Premiere's interface
// waits while a script runs, so nothing else can land between the reads. The
// body stays on the first line, so an error's line number is the one the
// caller expects. The answer carries the script's own value as a string.
// The read after the call reads what the read before completed, plus the
// sequences that were not listed before.
export function trackedEvalJsx(body, { limitMs = 350, skip = [] } = {}) {
  const L = Number(limitMs);
  return `var __bt = __bstSafe({ limit: ${L}, skip: ${jsSet(skip)} }), __bv, __bs, __bf = false; try { __bv = (function(){ ${body} })(); __bs = (typeof __bv === 'undefined') ? '' : (typeof __bv === 'string' ? __bv : JSON.stringify(__bv)); } catch (__be) { __bf = true; __bs = JSON.stringify({ error: __be.toString(), line: __be.line }); }
${STATE_READER}
function __bstSafe(o) { try { return __bstRead(o); } catch (e) { return null; } }
var __bo = { __bridge_tracked: 1, value: __bs, threw: __bf, complete: false, read: [], over: [], slow: false };
function __bstSlow(r) { return r.over.length > 0 && r.rows < r.ms * 0.2; }
if (__bt) {
  __bo.project = __bt.project;
  __bo.over = __bt.over;
  if (__bstSlow(__bt)) __bo.slow = true;
  if (__bt.listed) {
    var __ba = __bstSafe({ limit: ${L}, want: __bt.seqs, known: __bt.all });
    if (__ba) {
      for (var __bi = 0; __bi < __ba.over.length; __bi++) __bo.over.push(__ba.over[__bi]);
      if (__bstSlow(__ba)) __bo.slow = true;
      if (__ba.project === __bt.project) { var __bc = __bstChanges(__bt, __ba); __bo.read = __bc.read; __bo.complete = __bt.complete && __ba.complete; __bo.before = __bc.before; __bo.after = __bc.after; }
    }
  }
}
return __bo;
`;
}

// One snapshot of every sequence in the project: the editor's journal
// reads these over time and diffs them. Compact rows, seconds, media paths.
// Bounded: the deadline is checked before every clip, and a snapshot over it
// is not returned at all (a part of a project would diff as removals).
export function snapshotJsx({ onlyOpen, limitMs = 20000 } = {}) {
  return `${LIB}
var p = app.project;
if (!p) return { error: "No project open" };
var __end = new Date().getTime() + ${Number(limitMs)}, __rows = 0, __over = false;
function __snapTracks(coll, kind) {
  var out = [];
  for (var i = 0; i < coll.numTracks && !__over; i++) {
    var t = coll[i], clips = [], n = t.clips.numItems;
    for (var j = 0; j < n; j++) {
      if (new Date().getTime() > __end) { __over = true; break; }
      clips.push(__clipRow(t.clips[j]));
      __rows++;
    }
    out.push({ kind: kind, index: i, name: t.name || "", clipCount: clips.length, clips: clips });
  }
  return out;
}
var list = p.sequences, seqs = [];
var act = p.activeSequence;
for (var i = 0; i < list.numSequences && !__over; i++) {
  var s = list[i];
  var info = __seqInfo(s);
  info.active = !!(act && act.sequenceID === s.sequenceID);
  info.video = __snapTracks(s.videoTracks, "video");
  info.audio = __snapTracks(s.audioTracks, "audio");
  try { var ip = s.getInPointAsTime().seconds, op = s.getOutPointAsTime().seconds; if (ip >= 0 && op > 0) { info.inPoint = ip; info.outPoint = op; } } catch (e) {}
  seqs.push(info);
}
if (__over) return { error: "snapshot over the limit", complete: false, rows: __rows };
return { project: p.name, projectPath: p.path, sequences: seqs, activeSequence: act ? act.name : null };
`;
}
