/**
 * Frame-exact assembly for Premiere via the bridge (pr_eval_jsx / pk.js file).
 *
 * Why this file exists: placing clips at accumulated FLOAT seconds
 * (`overwriteClip(item, offsetSeconds)`) drifts. On a 25-cut reel it left
 * one-frame holes between clips - the director had to close them by hand
 * and called them "микропропуски". Ticks are integers; seconds are not.
 *
 * Rule: never compute the next offset yourself. After every append read the
 * sequence's own end (in ticks) and place the next clip exactly there.
 *
 * Fill CUTS with [inSeconds, outSeconds] pairs, set NAME, run the file.
 * Returns JSON with the built clip count and - the point of the exercise -
 * the gap report. Any gap at all is a failed build.
 */
(function () {
  var NAME = "SEQUENCE_NAME";
  var MEDIA = "/absolute/path/to/source.mov";
  var CUTS = [[0.0, 1.0]];                 // [[in, out], ...] in seconds
  var T = 254016000000;                    // ticks per second

  try {
    var root = app.project.rootItem;
    var f = root.findItemsMatchingMediaPath(MEDIA, true);
    if (!f || f.length === 0) {
      app.project.importFiles([MEDIA], true, root, false);
      f = root.findItemsMatchingMediaPath(MEDIA, true);
    }
    if (!f || f.length === 0) return "ERR: media not found: " + MEDIA;
    var pi = f[0], seq = null;

    for (var i = 0; i < CUTS.length; i++) {
      pi.setInPoint((CUTS[i][0] * T).toFixed(0), 4);
      pi.setOutPoint((CUTS[i][1] * T).toFixed(0), 4);
      if (i === 0) {
        seq = app.project.createNewSequenceFromClips(NAME, [pi], root);
      } else {
        // seq.end is a TICKS STRING - append exactly where the last clip
        // ended, so no float arithmetic can open a hole.
        seq.videoTracks[0].overwriteClip(pi, Number(seq.end) / T);
      }
    }

    // ---- mandatory gate: zero gaps, measured in ticks ---- //
    var v = seq.videoTracks[0], gaps = [], prev = null;
    for (var c = 0; c < v.clips.numItems; c++) {
      var cl = v.clips[c];
      var st = Number(cl.start.ticks), en = Number(cl.end.ticks);
      if (prev !== null && st !== prev) {
        gaps.push({ afterClip: c - 1, gapTicks: st - prev,
                    gapFrames: (st - prev) / (T / 30), atSec: prev / T });
      }
      prev = en;
    }
    return JSON.stringify({
      sequence: seq.name, clips: v.clips.numItems,
      durationSec: Number(seq.end) / T,
      gaps: gaps, ok: gaps.length === 0
    });
  } catch (e) {
    return "EXC: " + e.toString();
  }
})();
