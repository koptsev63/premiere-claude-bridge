"""Tests for the colour gate (pixel math + verdicts, no ffmpeg).

The verdict cases are the real August 2026 numbers off the V1 reel, so
this suite fails the day the gate stops reproducing the director's own
calls: two burnt LUT grades out, one invisible grade out, three shipped
looks in.

Run:  python -m core.tests.test_colorgate
"""

from __future__ import annotations

import sys

from core import colorgate as cg
from core.colorgate import (
    ColorGateFailure,
    FrameStats,
    ceilings,
    frame_stats,
    judge,
    mean_delta_e,
    mean_stats,
)

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


def solid(r: int, g: int, b: int, pixels: int = 4096) -> bytes:
    return bytes([r, g, b]) * pixels


def half(a: bytes, b: bytes) -> bytes:
    """Two solid halves in one frame — for share-of-frame checks."""
    return a[: len(a) // 2] + b[len(b) // 2:]


def test_frame_stats() -> None:
    print("colorgate — what a frame measures")
    white = frame_stats(solid(255, 255, 255))
    check("blown white reads as 100% clipped", white.clipping == 100.0,
          str(white.clipping))
    grey = frame_stats(solid(128, 128, 128))
    check("mid grey clips nothing", grey.clipping == 0.0, str(grey.clipping))
    check("mid grey is not oversaturated", grey.oversat == 0.0)
    red = frame_stats(solid(255, 0, 0))
    check("pure red reads as oversaturated", red.oversat == 100.0,
          str(red.oversat))
    check("pure red is not counted as skin", red.skin_share == 0.0,
          str(red.skin_share))
    skin = frame_stats(solid(200, 150, 130))
    check("a skin tone is detected as skin", skin.skin_share == 100.0,
          str(skin.skin_share))
    check("skin Cr lands in the human range",
          133 <= skin.skin_cr <= 173, str(skin.skin_cr))
    mixed = frame_stats(half(solid(200, 150, 130), solid(30, 30, 30)))
    check("half a frame of skin reads as ~50% skin",
          49 <= mixed.skin_share <= 51, str(mixed.skin_share))
    check("empty frame does not crash", frame_stats(b"") == FrameStats())


def test_paths_agree() -> None:
    print("colorgate — numpy path and pure-python path agree")
    frame = half(solid(240, 200, 180), solid(20, 40, 60))
    fast = frame_stats(frame)
    np_saved = cg._np
    try:
        cg._np = None                      # force the dependency-free path
        slow = frame_stats(frame, stride=1)
        slow_de = mean_delta_e(frame, solid(0, 0, 0), stride=1)
    finally:
        cg._np = np_saved
    fast_de = mean_delta_e(frame, solid(0, 0, 0))
    check("clipping matches", abs(fast.clipping - slow.clipping) < 0.5,
          f"{fast.clipping} vs {slow.clipping}")
    check("skin Cr matches", abs(fast.skin_cr - slow.skin_cr) < 0.5,
          f"{fast.skin_cr} vs {slow.skin_cr}")
    check("ΔE matches", abs(fast_de - slow_de) < 0.5,
          f"{fast_de} vs {slow_de}")


def test_delta_e() -> None:
    print("colorgate — ΔE, the 'is it visible at all' side")
    same = solid(120, 130, 140)
    check("identical frames measure zero", mean_delta_e(same, same) == 0.0)
    black_white = mean_delta_e(solid(0, 0, 0), solid(255, 255, 255))
    check("black vs white is ~100", 99 <= black_white <= 101,
          str(black_white))
    subtle = mean_delta_e(solid(120, 130, 140), solid(122, 130, 139))
    check("a one-step nudge stays under the floor", subtle < cg.MIN_DELTA_E,
          str(subtle))
    check("mean over an empty list is safe", mean_stats([]) == FrameStats())


def test_ceilings() -> None:
    print("colorgate — ceilings are relative and absolute at once")
    cool = ceilings(FrameStats(clipping=1.59, oversat=0.16,
                               skin_cr=145.0, skin_sat=0.30))
    check("headroom over a clean base", abs(cool["clipping"] - 2.79) < 1e-9,
          str(cool["clipping"]))
    hot = ceilings(FrameStats(clipping=4.0, oversat=6.0,
                              skin_cr=170.0, skin_sat=0.60))
    check("a hot base cannot license a hotter grade",
          hot["clipping"] == cg.MAX_CLIPPING and hot["oversat"] == cg.MAX_OVERSAT,
          str(hot))
    check("skin caps hold too",
          hot["skin_cr"] == cg.MAX_SKIN_CR and hot["skin_sat"] == cg.MAX_SKIN_SAT,
          str(hot))


# The real base of the V1 reel, measured over 8 frames.
BASE = FrameStats(clipping=1.59, oversat=0.16, skin_cr=145.0,
                  skin_sat=0.30, skin_share=12.0)


def test_field_verdicts() -> None:
    print("colorgate — reproduces the director's own calls (V1 reel)")
    kodak = judge(BASE, FrameStats(clipping=5.47, oversat=3.65, skin_cr=148.6,
                                   skin_sat=0.31), 16.04)
    check("Kodak 2383 at full strength is rejected", not kodak.ok)
    check("and it is rejected for the highlights",
          any("highlights" in f for f in kodak.failures), str(kodak.failures))
    fuji = judge(BASE, FrameStats(clipping=5.67, oversat=3.84, skin_cr=150.0,
                                  skin_sat=0.31), 14.84)
    check("Fuji 3513 at full strength is rejected", not fuji.ok)

    timid = judge(BASE, FrameStats(clipping=1.62, oversat=0.22, skin_cr=148.2,
                                   skin_sat=0.30), 3.23)
    check("the grade he could not see is rejected", not timid.ok)
    check("rejected by the lower gate only",
          timid.failures and all("visible" in f for f in timid.failures),
          str(timid.failures))

    shipped = [
        ("film", FrameStats(clipping=2.04, oversat=0.26, skin_cr=150.2,
                            skin_sat=0.31), 10.18),
        ("teal/orange", FrameStats(clipping=2.34, oversat=0.20, skin_cr=147.6,
                                   skin_sat=0.30), 5.70),
        ("pastel", FrameStats(clipping=0.33, oversat=0.10, skin_cr=144.4,
                              skin_sat=0.29), 5.57),
    ]
    for name, stats, de in shipped:
        rep = judge(BASE, stats, de)
        check(f"the {name} look he approved passes", rep.ok,
              str(rep.failures))

    burnt_skin = judge(BASE, FrameStats(clipping=1.0, oversat=0.2,
                                        skin_cr=170.0, skin_sat=0.55), 8.0)
    check("orange skin is caught even when highlights are fine",
          not burnt_skin.ok and any("skin" in f for f in burnt_skin.failures),
          str(burnt_skin.failures))


def test_report() -> None:
    print("colorgate — the report is a hard gate, not a suggestion")
    bad = judge(BASE, FrameStats(clipping=9.0, oversat=0.2, skin_cr=145.0,
                                 skin_sat=0.30), 9.0)
    raised = ""
    try:
        bad.assert_ok()
    except ColorGateFailure as exc:
        raised = str(exc)
    check("assert_ok() raises on a failed grade", bool(raised))
    check("the message says what to fix", "clipped" in raised, raised[:60])
    good = judge(BASE, FrameStats(clipping=2.0, oversat=0.2, skin_cr=147.0,
                                  skin_sat=0.31), 8.0)
    good.assert_ok()  # must not raise
    check("assert_ok() is silent on a good grade", good.ok)
    check("printable summary carries the verdict and ΔE",
          "PASS" in str(good) and "8.00" in str(good), str(good))


def main() -> int:
    test_frame_stats()
    test_paths_agree()
    test_delta_e()
    test_ceilings()
    test_field_verdicts()
    test_report()
    print(f"\ncolorgate: {_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
