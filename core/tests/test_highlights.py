"""Tests for the pure scoring/selection logic in core.highlights.

Run:  python -m core.tests.test_highlights

No ffmpeg, no I/O - all pure Python. Exits 0 if all checks pass, 1 otherwise.
"""

from __future__ import annotations

import sys

from core.highlights import score_windows, select_highlights

_p = _f = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


# ---------------------------------------------------------------------------
# Helpers to build synthetic feature windows
# ---------------------------------------------------------------------------

def _win(start: float, end: float, rms_db: float, peak_db: float,
         speech_rate: float = 0.0, is_silent: bool = False) -> dict:
    return {
        "start": start,
        "end": end,
        "rms_db": rms_db,
        "peak_db": peak_db,
        "is_silent": is_silent,
        "speech_rate": speech_rate,
        "has_speech": speech_rate > 0,
    }


def _silent_win(start: float, end: float) -> dict:
    return _win(start, end, rms_db=-80.0, peak_db=-80.0, is_silent=True)


# ---------------------------------------------------------------------------
# score_windows tests
# ---------------------------------------------------------------------------

def test_score_windows() -> None:
    print("\n--- score_windows ---")

    # empty input
    result = score_windows([])
    check("empty features -> empty result", result == [], str(result))

    # all-silent -> all zero scores
    feats = [_silent_win(0, 5), _silent_win(5, 10), _silent_win(10, 15)]
    result = score_windows(feats)
    scores = [s for _, s in result]
    check("all silent -> all zero scores",
          all(s == 0.0 for s in scores), str(scores))

    # single loud window scores > 0
    feats = [_win(0, 5, rms_db=-10.0, peak_db=-5.0)]
    result = score_windows(feats)
    check("single loud window scores > 0",
          result[0][1] > 0.0, str(result))

    # louder window scores higher than quieter (energy-only, no speech)
    feats = [
        _win(0, 5, rms_db=-30.0, peak_db=-20.0),   # quiet
        _win(5, 10, rms_db=-10.0, peak_db=-5.0),   # loud
    ]
    result = score_windows(feats, w_energy=1.0, w_speech=0.0)
    check("louder window scores higher (energy-only)",
          result[1][1] > result[0][1], str([s for _, s in result]))

    # more speech scores higher when speech weight = 1
    feats = [
        _win(0, 5, rms_db=-20.0, peak_db=-10.0, speech_rate=0.5),
        _win(5, 10, rms_db=-20.0, peak_db=-10.0, speech_rate=3.0),
    ]
    result = score_windows(feats, w_energy=0.0, w_speech=1.0)
    check("denser speech scores higher (speech-only)",
          result[1][1] > result[0][1], str([s for _, s in result]))

    # mixed loud + silent: silent window always 0, loud window > 0
    feats = [
        _win(0, 5, rms_db=-10.0, peak_db=-5.0),
        _silent_win(5, 10),
        _win(10, 15, rms_db=-15.0, peak_db=-10.0),
    ]
    result = score_windows(feats)
    check("silent window excluded (score=0) in mixed list",
          result[1][1] == 0.0, str(result))
    check("non-silent windows have positive score in mixed list",
          result[0][1] > 0.0 and result[2][1] > 0.0, str(result))

    # all-equal non-silent -> all score 1.0 (they are all equally the best)
    feats = [
        _win(0, 5, rms_db=-20.0, peak_db=-10.0, speech_rate=1.0),
        _win(5, 10, rms_db=-20.0, peak_db=-10.0, speech_rate=1.0),
    ]
    result = score_windows(feats)
    scores = [s for _, s in result]
    check("all-equal non-silent -> scores all 1.0 (tied = best available)",
          all(s == 1.0 for s in scores), str(scores))

    # scores in [0, 1]
    import random
    random.seed(42)
    feats = [
        _win(i * 5, (i + 1) * 5,
             rms_db=random.uniform(-40, -5),
             peak_db=random.uniform(-30, -1),
             speech_rate=random.uniform(0, 4))
        for i in range(10)
    ]
    result = score_windows(feats)
    all_bounded = all(0.0 <= s <= 1.0 for _, s in result)
    check("all scores in [0.0, 1.0]", all_bounded,
          str([s for _, s in result]))


# ---------------------------------------------------------------------------
# select_highlights tests
# ---------------------------------------------------------------------------

def test_select_highlights() -> None:
    print("\n--- select_highlights ---")

    # no candidates -> empty
    scored: list = []
    chosen = select_highlights(scored, target_s=30)
    check("empty scored -> empty selection", chosen == [], str(chosen))

    # all-silent (score=0) -> nothing selected
    feats = [_silent_win(i * 5, (i + 1) * 5) for i in range(6)]
    scored = score_windows(feats)
    chosen = select_highlights(scored, target_s=30)
    check("all silent -> nothing selected", chosen == [], str(chosen))

    # chronological order: selection must be sorted by start
    feats = [
        _win(0, 5, rms_db=-10.0, peak_db=-5.0),
        _win(5, 10, rms_db=-5.0, peak_db=-2.0),   # loudest
        _win(10, 15, rms_db=-15.0, peak_db=-10.0),
        _win(15, 20, rms_db=-25.0, peak_db=-20.0),
    ]
    scored = score_windows(feats, w_energy=1.0, w_speech=0.0)
    chosen = select_highlights(scored, target_s=20, min_gap_s=0.0)
    starts = [w["start"] for w in chosen]
    check("chosen windows in chronological order",
          starts == sorted(starts), str(starts))

    # total duration <= target_s
    feats = [_win(i * 5, (i + 1) * 5, rms_db=-10 + i,
                  peak_db=-5 + i) for i in range(10)]
    scored = score_windows(feats, w_energy=1.0, w_speech=0.0)
    chosen = select_highlights(scored, target_s=15,
                               min_clip_s=2.0, max_clip_s=5.0, min_gap_s=0.0)
    total_dur = sum(w["end"] - w["start"] for w in chosen)
    check("total chosen duration <= target_s",
          total_dur <= 15.0 + 1e-6, f"total={total_dur:.2f}s")

    # anti-clumping: windows that are too close are rejected
    feats = [
        _win(0.0, 5.0, rms_db=-10.0, peak_db=-5.0),
        _win(1.0, 6.0, rms_db=-9.0, peak_db=-4.0),   # starts 1s after first
        _win(20.0, 25.0, rms_db=-20.0, peak_db=-15.0),
    ]
    scored = score_windows(feats, w_energy=1.0, w_speech=0.0)
    chosen = select_highlights(scored, target_s=20, min_gap_s=5.0,
                               min_clip_s=2.0, max_clip_s=5.0)
    # the window at 1.0 should be rejected (< 5s gap from the 0.0 window)
    clump_rejected = all(abs(a["start"] - b["start"]) >= 5.0
                         for i, a in enumerate(chosen)
                         for b in chosen[i + 1:])
    check("anti-clumping: close windows not both selected",
          clump_rejected, str([w["start"] for w in chosen]))

    # max_clip_s enforced
    feats = [_win(0, 20, rms_db=-5.0, peak_db=-2.0)]  # 20s window
    scored = score_windows(feats, w_energy=1.0, w_speech=0.0)
    chosen = select_highlights(scored, target_s=30,
                               min_clip_s=2.0, max_clip_s=8.0, min_gap_s=0.0)
    if chosen:
        dur = chosen[0]["end"] - chosen[0]["start"]
        check("max_clip_s respected (window clamped to 8s)",
              dur <= 8.0 + 1e-6, f"dur={dur:.2f}")
    else:
        check("max_clip_s test - no window selected (unexpected)", False)

    # loud > quiet: the loudest window should be selected over a quiet one
    # when target is shorter than the total available
    feats = [
        _win(0, 5, rms_db=-50.0, peak_db=-40.0),   # very quiet
        _win(5, 10, rms_db=-50.0, peak_db=-40.0),  # very quiet
        _win(10, 15, rms_db=-5.0, peak_db=-1.0),   # loud
        _win(15, 20, rms_db=-50.0, peak_db=-40.0), # very quiet
    ]
    scored = score_windows(feats, w_energy=1.0, w_speech=0.0)
    chosen = select_highlights(scored, target_s=6,
                               min_clip_s=2.0, max_clip_s=5.0,
                               min_gap_s=0.0)
    loud_start = 10.0
    check("loud window preferred over quiet when target is tight",
          any(abs(w["start"] - loud_start) < 1e-6 for w in chosen),
          str([w["start"] for w in chosen]))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def test_edit_points() -> None:
    """0.8.10: one cut list for every tool - a flash counts once, a dip to black counts."""
    import os
    import shutil
    import subprocess
    import tempfile

    from core.highlights import dip_cuts, edit_points, group_bursts, scene_cuts

    g = group_bursts([5.0, 12.0, 12.04, 12.13, 20.0])
    check("burst of hits is one event", g == [(5.0, 5.0, 1), (12.0, 12.13, 3), (20.0, 20.0, 1)], str(g))
    check("6-frame shots are real cuts, not a burst",
          [x[2] for x in group_bursts([1.6, 1.84, 2.08])] == [1, 1, 1])
    if not shutil.which("ffmpeg"):
        print("  SKIP  ffmpeg missing: dip and flash clips not built")
        return
    with tempfile.TemporaryDirectory() as d:
        base = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        src = lambda name, dur: ["-f", "lavfi", "-i", f"{name}=size=320x180:rate=25:duration={dur}"]  # noqa: E731
        dip = os.path.join(d, "dip.mp4")
        subprocess.run(base + src("testsrc2", 2) + src("smptebars", 2) + [
            "-filter_complex", "[0:v]fade=t=out:st=1.5:d=0.5[a];[1:v]fade=t=in:st=0:d=0.5[b];[a][b]concat=n=2:v=1[v]",
            "-map", "[v]", "-pix_fmt", "yuv420p", dip], check=True)
        hard = scene_cuts(dip)
        dips = dip_cuts(dip, hard)
        check("a dip to black has no hard cut", hard == [], str(hard))
        check("the dip is found near 2.0s", len(dips) == 1 and abs(dips[0] - 2.0) < 0.3, str(dips))
        check("edit_points includes the dip", len(edit_points(dip)) == 1, str(edit_points(dip)))
        flash = os.path.join(d, "flash.mp4")
        subprocess.run(base + src("testsrc2", 2) + [
            "-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='between(n,25,26)'",
            "-pix_fmt", "yuv420p", flash], check=True)
        pts = edit_points(flash)
        check("a two-frame flash is at most one edit point", len(pts) <= 1, f"{scene_cuts(flash)} -> {pts}")


def main() -> int:
    test_score_windows()
    test_select_highlights()
    test_edit_points()
    print(f"\n{_p} passed, {_f} failed")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
