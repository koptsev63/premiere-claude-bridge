// ripple_ops.jsx — build variants of an EXISTING sequence from a plan written by core/ripple.py.
//
// Run from a shell through the direct runner (Premiere does not see the shell environment, so the plan path
// goes through the persistent ExtendScript global):
//     node mcp-server/pk.js eval '$.global.RIPPLE_PLAN="/path/plan.json"'
//     PK_TIMEOUT=900000 node mcp-server/pk.js file core/jsx/ripple_ops.jsx
//
// Plan shape (core.ripple.compile_plan): { base_id, bin, titles: {oldBasename: newPath}, variants: [{key, name, rebuild, ops, srt, expected}] }
// ops arrive in builder order: rmaud -> del (desc) -> ins (desc, same anchor already reversed) -> aud.
//
// Field rules this file encodes (Grave Stakes, 06.10.2026; every one of them cost an export):
//  * Deletions use native QE extract (setInOutPoints + extract). The API alternative, TrackItem.move() to the LEFT,
//    leaves black frames at the clip's old place (render cache does not help).
//  * Inserts: razor every track at t, drop <0.3 s audio slivers at the cut, move everything from t to the right,
//    overwriteClip on the chosen video track. Audio of a linked clip lands on the audio track with the same index.
//    A sliver of AAC-in-mp4 music shorter than 0.3 s kills the audio renderer ("could not create audio renderer");
//    the real cure is relinking that project item to a PCM .mov (projectItem.changeMediaPath).
//  * Audio-only placements come LAST and in FINAL coordinates (the compiler did the arithmetic).
//  * Title stills: a 3840-px PNG replacing a 1920-px title in a 1080p sequence needs scale 50, not 100.
//  * Caption tracks are invisible to this API: they do not ripple with razor+move, and exportAsMediaDirect burns every
//    VISIBLE caption track. Hide the base's own caption track (eye icon) before cloning; the clone inherits the state.
//  * Imported clips coming from another project may carry a Rec.709 colour-space override and look grey:
//    copy the colour space of a native clip (setOverrideColorSpace(ref.getColorSpace())).
//  * sequence.end does not shrink after extract; export range end = max clip end over all tracks.
(function () {
  var PLAN = $.global.RIPPLE_PLAN || "";
  if (!PLAN) return "ERR: set $.global.RIPPLE_PLAN=\"/path/plan.json\" first (pk.js eval)";
  var P = app.project, T = 254016000000, L = [];
  app.enableQE();
  var f = new File(PLAN); f.encoding = "UTF-8"; f.open("r"); var R = eval("(" + f.read() + ")"); f.close();
  var FPS = R.fps || 25;

  function findItem(root, name) { for (var i = 0; i < root.children.numItems; i++) { var c = root.children[i];
    if (c.type == 2) { var r = findItem(c, name); if (r) return r; } else if (c.name == name) return c; } return null; }
  function seqById(id) { for (var i = 0; i < P.sequences.numSequences; i++) if (P.sequences[i].sequenceID == id) return P.sequences[i]; return null; }
  function seqByName(n) { for (var i = 0; i < P.sequences.numSequences; i++) if (P.sequences[i].name == n) return P.sequences[i]; return null; }
  function binBy(n) { for (var i = 0; i < P.rootItem.children.numItems; i++) { var c = P.rootItem.children[i]; if (c.type == 2 && c.name == n) return c; } return P.rootItem.createBin(n); }
  function tcs(sec) { var fr = Math.round(sec * FPS); var h = Math.floor(fr / (3600 * FPS)), m = Math.floor(fr % (3600 * FPS) / (60 * FPS)), s = Math.floor(fr % (60 * FPS) / FPS), ff = fr % FPS;
    function z(x) { return (x < 10 ? "0" : "") + x; } return z(h) + ":" + z(m) + ":" + z(s) + ":" + z(ff); }

  // ---- imports: only what the project does not have yet
  var BIN = binBy(R.bin || "ripple variants"), imp = [], seen = {};
  function need(p) { if (!p || seen[p]) return; seen[p] = 1; if (!findItem(P.rootItem, p.split("/").pop())) imp.push(p); }
  for (var k in R.titles) need(R.titles[k]);
  for (var v = 0; v < R.variants.length; v++) { var V = R.variants[v]; need(V.srt); for (var i = 0; i < V.ops.length; i++) if (V.ops[i].path) need(V.ops[i].path); }
  if (imp.length) P.importFiles(imp, true, BIN, false);
  L.push("imported " + imp.length);

  var touched = [];
  function put(track, item, i, o, t) { item.setInPoint(i + 0.001, 4); item.setOutPoint(o + 0.001, 4); track.overwriteClip(item, t); touched.push(item); }
  function allClips(s) { var a = []; for (var v = 0; v < s.videoTracks.numTracks; v++) { var tk = s.videoTracks[v]; for (var c = 0; c < tk.clips.numItems; c++) a.push(tk.clips[c]); }
    for (var v = 0; v < s.audioTracks.numTracks; v++) { var tk = s.audioTracks[v]; for (var c = 0; c < tk.clips.numItems; c++) a.push(tk.clips[c]); } return a; }
  function razorAll(q, s, t) { var tc = tcs(t); for (var v = 0; v < s.videoTracks.numTracks; v++) { try { q.getVideoTrackAt(v).razor(tc); } catch (e) {} }
    for (var v = 0; v < s.audioTracks.numTracks; v++) { try { q.getAudioTrackAt(v).razor(tc); } catch (e) {} } }
  function shiftFrom(s, t, d) { var a = allClips(s), b = []; for (var i = 0; i < a.length; i++) if (a[i].start.ticks / T >= t - 0.005) b.push(a[i]);
    b.sort(function (x, y) { return d > 0 ? y.start.ticks - x.start.ticks : x.start.ticks - y.start.ticks; });
    for (var i = 0; i < b.length; i++) b[i].move(d); return b.length; }
  function dropSlivers(s, t) { var n = 0; for (var v = 0; v < s.audioTracks.numTracks; v++) { var tk = s.audioTracks[v]; for (var c = tk.clips.numItems - 1; c >= 0; c--) { var ci = tk.clips[c]; var st = ci.start.ticks / T, en = ci.end.ticks / T;
    if (en - st < 0.3 && (Math.abs(en - t) < 0.005 || Math.abs(st - t) < 0.005)) { ci.remove(false, false); n++; } } } return n; }
  function del(q, s, op) { q.setInOutPoints(tcs(op.a), tcs(op.b)); var r = q.extract(); var sl = dropSlivers(s, op.a); return "del " + op.a + "-" + op.b + ": extract=" + r + ", slivers " + sl; }
  function ins(q, s, op) { var it = findItem(P.rootItem, op.item); if (!it) throw "no item " + op.item;
    razorAll(q, s, op.t); var sl = dropSlivers(s, op.t); var m = shiftFrom(s, op.t, op.o - op.i); put(s.videoTracks[op.vt], it, op.i, op.o, op.t);
    return "ins " + op.item + " @" + op.t + " d=" + (op.o - op.i).toFixed(2) + ", slivers " + sl + ", shifted " + m; }
  function aud(s, op) { var it = findItem(P.rootItem, op.item); if (!it) throw "no item " + op.item; s.audioTracks[op.track].overwriteClip(it, op.t); return "aud " + op.item + " @" + op.t + " A" + (op.track + 1); }
  function rmaud(s, op) { var tk = s.audioTracks[op.track], n = 0; for (var c = tk.clips.numItems - 1; c >= 0; c--) { var ci = tk.clips[c]; var st = ci.start.ticks / T, en = ci.end.ticks / T;
    if (st >= op.a - 0.005 && en <= op.b + 0.005) { ci.remove(false, false); n++; } } return "rmaud A" + (op.track + 1) + " " + op.a + "-" + op.b + ": removed " + n; }
  function scaleOf(clip) { for (var c = 0; c < clip.components.numItems; c++) { var cm = clip.components[c]; if (cm.matchName == "AE.ADBE Motion") return cm.properties[1].getValue(); } return null; }
  function setScale(clip, v) { for (var c = 0; c < clip.components.numItems; c++) { var cm = clip.components[c]; if (cm.matchName == "AE.ADBE Motion") { cm.properties[1].setValue(v, true); return; } } }
  function swapTitles(s) { var n = 0; if (!R.titles) return 0; for (var v = 0; v < s.videoTracks.numTracks; v++) { var tk = s.videoTracks[v]; var jobs = [];
      for (var c = 0; c < tk.clips.numItems; c++) { var cl = tk.clips[c]; var mp = ""; try { mp = cl.projectItem.getMediaPath(); } catch (e) {} var bn = mp.split("/").pop();
        if (R.titles[bn]) jobs.push({ st: cl.start.ticks / T, en: cl.end.ticks / T, sc: scaleOf(cl), nu: R.titles[bn].split("/").pop() }); }
      for (var j = 0; j < jobs.length; j++) { var jb = jobs[j]; var it = findItem(P.rootItem, jb.nu); if (!it) { L.push("no title " + jb.nu); continue; }
        put(tk, it, 0, jb.en - jb.st, jb.st); n++;
        for (var c = 0; c < tk.clips.numItems; c++) { var cl = tk.clips[c]; if (Math.abs(cl.start.ticks / T - jb.st) < 0.005) { var sc = (jb.sc === null || jb.sc >= 99) ? (R.titleScale || 50) : jb.sc; setScale(cl, sc); } } } }
    return n; }
  function maxEnd(s) { var mx = 0, a = allClips(s); for (var i = 0; i < a.length; i++) mx = Math.max(mx, a[i].end.ticks / T); return mx; }

  for (var v = 0; v < R.variants.length; v++) { var V = R.variants[v];
    var old = seqByName(V.name); if (old) { if (V.rebuild) { P.deleteSequence(old); L.push("rebuilt " + V.name); } else { L.push("exists, skipped " + V.name); continue; } }
    var ids = {}; for (var i = 0; i < P.sequences.numSequences; i++) ids[P.sequences[i].sequenceID] = 1;
    var B = R.base_id ? seqById(R.base_id) : seqByName(R.base_name); if (!B) { L.push("NO BASE"); break; } B.clone();
    var s = null; for (var i = 0; i < P.sequences.numSequences; i++) if (!ids[P.sequences[i].sequenceID]) s = P.sequences[i];
    if (!s) { L.push("clone failed " + V.name); continue; }
    s.name = V.name; try { s.projectItem.moveBin(BIN); } catch (e) { L.push("moveBin err " + e); }
    P.openSequence(s.sequenceID); var q = qe.project.getActiveSequence();
    if (R.autoToneMap) { try { var st = s.getSettings(); st.autoToneMapEnabled = true; s.setSettings(st); } catch (e) { L.push("tonemap err " + e); } }
    for (var i = 0; i < V.ops.length; i++) { var op = V.ops[i];
      try { if (op.op == "del") L.push(V.key + " " + del(q, s, op)); else if (op.op == "ins") L.push(V.key + " " + ins(q, s, op));
        else if (op.op == "aud") L.push(V.key + " " + aud(s, op)); else if (op.op == "rmaud") L.push(V.key + " " + rmaud(s, op)); }
      catch (err) { L.push("ERR " + V.key + " op#" + i + " " + op.op + " " + (op.item || "") + ": " + err); } }
    L.push(V.key + " titles swapped: " + swapTitles(s));
    if (V.srt) { try { var srt = findItem(P.rootItem, V.srt.split("/").pop()); s.createCaptionTrack(srt, 0, Sequence.CAPTION_FORMAT_SUBTITLE); } catch (err) { L.push("cap err " + err); } }
    var me = maxEnd(s); L.push(V.key + " " + V.name + ": built " + me.toFixed(2) + " expected " + V.expected + (Math.abs(me - V.expected) < 0.05 ? " OK" : " MISMATCH"));
  }
  for (var i = 0; i < touched.length; i++) { try { touched[i].clearInPoint(); touched[i].clearOutPoint(); } catch (e) {} }
  P.save();
  return L.join("\n");
})();
