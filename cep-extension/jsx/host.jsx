// host.jsx -- ExtendScript host functions called from CEP panel via CSInterface.evalScript()
// Every entry function returns a STRING (JSON.stringify or plain text).
// Errors are caught and returned as JSON {error: ...} string -- never throw to evalScript.

// ExtendScript (ES3) has no native JSON. Minimal polyfill covers what host.jsx needs.
if (typeof JSON === 'undefined') {
  JSON = {};
  var __ctrlRe = new RegExp('[\\u0000-\\u001f]', 'g');
  JSON.stringify = function (v) {
    if (v === null) return 'null';
    if (v === undefined) return undefined;
    var t = typeof v;
    if (t === 'string') {
      var esc = v.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n').replace(/\r/g, '\\r').replace(/\t/g, '\\t');
      esc = esc.replace(__ctrlRe, function (c) { return '\\u' + ('0000' + c.charCodeAt(0).toString(16)).slice(-4); });
      return '"' + esc + '"';
    }
    if (t === 'number') return isFinite(v) ? String(v) : 'null';
    if (t === 'boolean') return String(v);
    if (t === 'object') {
      if (v.constructor === Array || (typeof v.length === 'number' && typeof v.splice === 'function')) {
        var parts = [];
        for (var i = 0; i < v.length; i++) { var s = JSON.stringify(v[i]); parts.push(s === undefined ? 'null' : s); }
        return '[' + parts.join(',') + ']';
      }
      var pairs = [];
      for (var k in v) { if (v.hasOwnProperty(k)) { var val = JSON.stringify(v[k]); if (val !== undefined) pairs.push(JSON.stringify(k) + ':' + val); } }
      return '{' + pairs.join(',') + '}';
    }
    return undefined;
  };
  JSON.parse = function (s) { return eval('(' + s + ')'); };
}

var TICKS_PER_SECOND = 254016000000;

function __safe(fn) {
  try { return fn(); }
  catch (e) {
    return JSON.stringify({ error: (e && e.toString) ? e.toString() : String(e), line: e && e.line });
  }
}

function getStatus() {
  return __safe(function () {
    var p = app.project;
    var seq = p ? p.activeSequence : null;
    return JSON.stringify({
      app: "Premiere Pro",
      version: app.version,
      build: app.build,
      project: p ? { name: p.name, path: p.path } : null,
      activeSequence: seq ? { name: seq.name, id: seq.sequenceID } : null
    });
  });
}

function getProjectInfo() {
  return __safe(function () {
    var p = app.project;
    if (!p) return JSON.stringify({ error: "No project open" });
    var items = [];
    function walk(bin, depth) {
      if (!bin || !bin.children) return;
      for (var i = 0; i < bin.children.numItems; i++) {
        var c = bin.children[i];
        var entry = {
          name: c.name,
          type: c.type,
          treePath: c.treePath,
          nodeId: c.nodeId
        };
        try { entry.path = c.getMediaPath ? c.getMediaPath() : ""; } catch (e) { entry.path = ""; }
        items.push(entry);
        if (c.type === ProjectItemType.BIN && depth < 8) walk(c, depth + 1);
      }
    }
    walk(p.rootItem, 0);
    return JSON.stringify({
      name: p.name,
      path: p.path,
      itemCount: items.length,
      items: items
    });
  });
}

function getActiveSequenceInfo() {
  return __safe(function () {
    var s = app.project.activeSequence;
    if (!s) return JSON.stringify({ error: "No active sequence" });
    var ticksPerFrame = parseFloat(s.timebase);
    var fps = ticksPerFrame > 0 ? TICKS_PER_SECOND / ticksPerFrame : 0;
    var endTicks = parseFloat(s.end);
    return JSON.stringify({
      name: s.name,
      id: s.sequenceID,
      durationSeconds: endTicks / TICKS_PER_SECOND,
      durationTicks: s.end,
      fps: fps,
      width: s.frameSizeHorizontal,
      height: s.frameSizeVertical,
      videoTrackCount: s.videoTracks.numTracks,
      audioTrackCount: s.audioTracks.numTracks
    });
  });
}

function listTimeline() {
  return __safe(function () {
    var s = app.project.activeSequence;
    if (!s) return JSON.stringify({ error: "No active sequence" });
    function dump(coll, kind) {
      var out = [];
      for (var i = 0; i < coll.numTracks; i++) {
        var t = coll[i], clips = [];
        for (var j = 0; j < t.clips.numItems; j++) {
          var c = t.clips[j];
          var nodeId = null;
          try { nodeId = c.projectItem ? c.projectItem.nodeId : null; } catch (e) {}
          clips.push({
            name: c.name,
            inPointSec: c.inPoint.seconds,
            outPointSec: c.outPoint.seconds,
            startSec: c.start.seconds,
            endSec: c.end.seconds,
            durationSec: c.duration.seconds,
            type: c.type,
            mediaType: c.mediaType,
            nodeId: nodeId
          });
        }
        out.push({
          kind: kind,
          index: i,
          name: t.name || (kind + " " + (i + 1)),
          id: t.id,
          muted: t.isMuted ? t.isMuted() : false,
          clipCount: clips.length,
          clips: clips
        });
      }
      return out;
    }
    return JSON.stringify({
      sequenceName: s.name,
      video: dump(s.videoTracks, "video"),
      audio: dump(s.audioTracks, "audio")
    });
  });
}

function getSelected() {
  return __safe(function () {
    var s = app.project.activeSequence;
    if (!s) return JSON.stringify([]);
    var sel = s.getSelection();
    var out = [];
    for (var i = 0; i < sel.length; i++) {
      var c = sel[i];
      var nodeId = null;
      try { nodeId = c.projectItem ? c.projectItem.nodeId : null; } catch (e) {}
      out.push({
        name: c.name,
        mediaType: c.mediaType,
        startSec: c.start.seconds,
        endSec: c.end.seconds,
        inPointSec: c.inPoint.seconds,
        outPointSec: c.outPoint.seconds,
        nodeId: nodeId
      });
    }
    return JSON.stringify(out);
  });
}

function getCTI() {
  return __safe(function () {
    var s = app.project.activeSequence;
    if (!s) return JSON.stringify({ error: "No active sequence" });
    var t = s.getPlayerPosition();
    return JSON.stringify({
      seconds: t.seconds,
      ticks: t.ticks
    });
  });
}

function setCTI(seconds) {
  return __safe(function () {
    var s = app.project.activeSequence;
    if (!s) return JSON.stringify({ error: "No active sequence" });
    var ticks = String(Math.round(seconds * TICKS_PER_SECOND));
    s.setPlayerPosition(ticks);
    return JSON.stringify({ ok: true, seconds: seconds });
  });
}

function addSequenceMarker(seconds, name, comment) {
  return __safe(function () {
    var s = app.project.activeSequence;
    if (!s) return JSON.stringify({ error: "No active sequence" });
    var m = s.markers.createMarker(seconds);
    if (name) m.name = name;
    if (comment) m.comments = comment;
    if (m.setTypeAsComment) m.setTypeAsComment();
    return JSON.stringify({ ok: true, seconds: seconds, name: m.name || "" });
  });
}

function exportAME(outPath, eprPath) {
  return __safe(function () {
    var s = app.project.activeSequence;
    if (!s) return JSON.stringify({ error: "No active sequence" });
    if (!app.encoder) return JSON.stringify({ error: "app.encoder not available" });
    app.encoder.launchEncoder();
    var jobID = app.encoder.encodeSequence(
      s,
      outPath,
      eprPath,
      app.encoder.ENCODE_ENTIRE,
      1
    );
    app.encoder.startBatch();
    return JSON.stringify({ ok: true, jobID: jobID, outPath: outPath, eprPath: eprPath });
  });
}

// host.jsx loaded sentinel
"host.jsx ready";
