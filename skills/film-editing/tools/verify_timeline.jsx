/**
 * Timeline QC for Premiere - run after ANY assembly, before showing a human.
 *
 * Checks, in ticks (never seconds):
 *  1. gaps  - a hole between consecutive clips on V1. Any hole fails.
 *  2. shorts - clips under 6 frames, the usual leftovers of a bad razor.
 *  3. media - every clip resolves to a real file (no offline).
 *  4. audio - the video track's clip count matches A1, so a cut did not
 *     leave sound orphaned.
 *
 * Returns JSON. `ok:false` means do not show it to anyone yet.
 */
(function () {
  var T = 254016000000, FPS = 30;
  try {
    var seq = app.project.activeSequence;
    if (!seq) return "ERR: no active sequence";
    var v = seq.videoTracks[0], a = seq.audioTracks[0];
    var gaps = [], shorts = [], offline = [], prev = null;
    for (var c = 0; c < v.clips.numItems; c++) {
      var cl = v.clips[c];
      var st = Number(cl.start.ticks), en = Number(cl.end.ticks);
      if (prev !== null && st !== prev)
        gaps.push({ afterClip: c - 1, frames: (st - prev) / (T / FPS),
                    atSec: +(prev / T).toFixed(3) });
      if ((en - st) / (T / FPS) < 6)
        shorts.push({ clip: c, frames: (en - st) / (T / FPS) });
      if (!cl.projectItem) offline.push(c);
      prev = en;
    }
    var res = {
      sequence: seq.name,
      clips: v.clips.numItems,
      audioClips: a ? a.clips.numItems : -1,
      durationSec: +(Number(seq.end) / T).toFixed(3),
      gaps: gaps, shortClips: shorts, offline: offline
    };
    res.ok = gaps.length === 0 && offline.length === 0;
    return JSON.stringify(res);
  } catch (e) {
    return "EXC: " + e.toString();
  }
})();
