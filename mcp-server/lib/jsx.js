// ExtendScript sent by the newer tools through the panel's evalRaw path.
// They ship from the server, not from host.jsx, so they work with a panel
// that is already open: no reinstall, no panel reload.
// Every builder returns a function body with an explicit top-level `return`.

import { jsxString as S } from "./evalcode.js";

// Shared helpers, ES3.
const FIND = `
function __byNode(item, id, depth) {
  if (!item || !item.children) return null;
  for (var i = 0; i < item.children.numItems; i++) {
    var c = item.children[i];
    if (c.nodeId === id) return c;
    if (c.type === ProjectItemType.BIN && depth < 12) {
      var r = __byNode(c, id, depth + 1);
      if (r) return r;
    }
  }
  return null;
}
function __mediaPath(item) {
  try { return item.getMediaPath ? item.getMediaPath() : ""; } catch (e) { return ""; }
}
function __samePath(p, want) {
  if (!p) return false;
  try { return new File(p).fsName === want; } catch (e) { return false; }
}
function __byMedia(item, want, depth) {
  if (!item || !item.children) return null;
  for (var i = 0; i < item.children.numItems; i++) {
    var c = item.children[i];
    if (c.type === ProjectItemType.BIN) {
      if (depth < 12) { var r = __byMedia(c, want, depth + 1); if (r) return r; }
    } else if (__samePath(__mediaPath(c), want)) return c;
  }
  return null;
}
// With two projects open, app.project can be one project while
// app.project.activeSequence still names the other one's sequence (seen
// live 21.10.2026). Refuse instead of writing into the wrong project.
function __foreignSequence(seq) {
  var list = app.project.sequences;
  for (var i = 0; i < list.numSequences; i++) if (list[i].sequenceID === seq.sequenceID) return null;
  return { error: 'The active sequence "' + seq.name + '" is not in the active project "' + app.project.name +
    '" (several projects open). Open the target sequence of this project first, then retry.' };
}
function __childBin(parent, name) {
  for (var i = 0; i < parent.children.numItems; i++) {
    var c = parent.children[i];
    if (c.type === ProjectItemType.BIN && c.name === name) return c;
  }
  return null;
}
`;

// Import ONE caption file. suppressUI=false on purpose: importFiles with
// several .srt and suppressUI=true hung Premiere on "Импорт файлов..."
// (20.10.2026); one file at a time with the UI allowed went through.
export function importOneCaptionJsx({ path, bin, reuse }) {
  return `${FIND}
var PATH = ${S(path)}; var BIN = ${S(bin || "")}; var REUSE = ${reuse ? "true" : "false"};
var f = new File(PATH);
if (!f.exists) return { error: "File not found: " + PATH };
var want = f.fsName;
var root = app.project.rootItem;
if (REUSE) {
  var ex = __byMedia(root, want, 0);
  if (ex) return { nodeId: ex.nodeId, name: ex.name, reused: true };
}
var target = root;
if (BIN) {
  target = __childBin(root, BIN);
  if (!target) { root.createBin(BIN); target = __childBin(root, BIN); }
  if (!target) return { error: "Could not create bin " + BIN };
}
var seen = {};
for (var i = 0; i < target.children.numItems; i++) seen[target.children[i].nodeId] = true;
var ret = app.project.importFiles([want], false, target, false);
var found = null;
for (var j = 0; j < target.children.numItems; j++) {
  var c = target.children[j];
  if (seen[c.nodeId]) continue;
  if (__samePath(__mediaPath(c), want)) { found = c; break; }
  if (!found) found = c;
}
if (!found) return { error: "importFiles returned " + ret + " but no new item appeared", path: want };
return { nodeId: found.nodeId, name: found.name, reused: false };
`;
}

export const CAPTION_FORMATS = {
  subtitle: "CAPTION_FORMAT_SUBTITLE",
  "608": "CAPTION_FORMAT_608",
  "708": "CAPTION_FORMAT_708",
  teletext: "CAPTION_FORMAT_TELETEXT",
  open_ebu: "CAPTION_FORMAT_OPEN_EBU",
};

export function captionTrackJsx({ nodeId, startSeconds, format }) {
  const constName = CAPTION_FORMATS[format || "subtitle"];
  if (!constName) throw new Error(`Unknown caption format "${format}"`);
  return `${FIND}
var seq = app.project.activeSequence;
if (!seq) return { error: "No active sequence" };
var foreign = __foreignSequence(seq);
if (foreign) return foreign;
var it = __byNode(app.project.rootItem, ${S(nodeId)}, 0);
if (!it) return { error: "Project item not found: " + ${S(nodeId)} };
var fmt = Sequence[${S(constName)}];
if (typeof fmt === "undefined") return { error: "This Premiere has no Sequence." + ${S(constName)} };
var r = seq.createCaptionTrack(it, ${Number(startSeconds) || 0}, fmt);
if (r === false) return { error: "createCaptionTrack returned false", item: it.name };
return { ok: true, sequence: seq.name, item: it.name };
`;
}

export function listVideoEffectsJsx() {
  return `app.enableQE();
var l = qe.project.getVideoEffectList();
var out = [];
for (var i = 0; i < l.length; i++) out.push(String(l[i]));
return out;
`;
}

// Add a video effect by its exact localized name and prove it landed:
// components counted before and after on every target clip. The QE item is
// found by start ticks (QE lists gaps as type "Empty"; DOM clip index and QE
// item index differ). Ticks match exactly between the two APIs (checked on
// 26.0.2).
export function addVideoEffectJsx({ name, mode, track, clip }) {
  return `${FIND}
app.enableQE();
var NAME = ${S(name)}; var MODE = ${S(mode)}; var T = ${Number(track) || 0}; var C = ${Number(clip) || 0};
var seq = app.project.activeSequence;
if (!seq) return { error: "No active sequence" };
var foreign = __foreignSequence(seq);
if (foreign) return foreign;
var qseq = qe.project.getActiveSequence();
if (!qseq) return { error: "QE has no active sequence" };
var eff = qe.project.getVideoEffectByName(NAME);
if (!eff) return { error: "getVideoEffectByName gave nothing for " + NAME };
var targets = [];
for (var t = 0; t < seq.videoTracks.numTracks; t++) {
  if ((MODE === "index" || MODE === "track") && t !== T) continue;
  var clips = seq.videoTracks[t].clips;
  for (var c = 0; c < clips.numItems; c++) {
    if (MODE === "index" && c !== C) continue;
    if (MODE === "selected" && !clips[c].isSelected()) continue;
    targets.push([t, c]);
  }
}
if (!targets.length) return { error: "No target clips (mode " + MODE + ", track " + T + ", clip " + C + ")" };
var results = [];
for (var k = 0; k < targets.length; k++) {
  var tt = targets[k][0], cc = targets[k][1];
  var dom = seq.videoTracks[tt].clips[cc];
  var before = dom.components.numItems;
  var ticks = String(dom.start.ticks);
  var r = { track: tt, clip: cc, name: dom.name, startSec: dom.start.seconds, before: before };
  var qtrack = qseq.getVideoTrackAt(tt), qi = null;
  for (var i = 0; i < qtrack.numItems; i++) {
    var it = qtrack.getItemAt(i);
    if (it && it.type === "Clip" && String(it.start.ticks) === ticks) { qi = it; break; }
  }
  if (!qi) { r.added = false; r.error = "QE item not found at ticks " + ticks; results.push(r); continue; }
  qi.addVideoEffect(eff);
  var fresh = seq.videoTracks[tt].clips[cc];
  r.after = fresh.components.numItems;
  r.added = r.after > before;
  if (r.added) { try { r.component = fresh.components[r.after - 1].displayName; } catch (e) {} }
  results.push(r);
}
return { effect: NAME, results: results };
`;
}
