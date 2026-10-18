"""Tests for shot match (Lab maths, match rules, .cube baking, ffmpeg filter).

Hermetic - synthetic colours only, numpy only. The real-ffmpeg case is
skipped when ffmpeg is not installed.

Run:  python -m core.tests.test_shotmatch
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from core import shotmatch as sm
from core.shotmatch import (
    CubeCheck,
    MatchTargets,
    ShotMatchFailure,
    ShotParams,
    ShotStats,
)

_passed = 0
_failed = 0
_skipped = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


def _rng() -> np.random.Generator:
    return np.random.default_rng(7)


# The field numbers from the module docstring.
def _field_stats() -> list[ShotStats]:
    return [
        ShotStats(0, "cam", L=48, a=2.0, b=-4.4, C=11.3, group="clipA"),
        ShotStats(1, "cam", L=52, a=3.0, b=-5.0, C=11.3, group="clipA"),
        ShotStats(2, "cam", L=50, a=1.0, b=-4.0, C=11.3, group="clipB"),
        ShotStats(3, "ai", L=50, a=0.5, b=1.3, C=10.4),
        ShotStats(4, "ai", L=50, a=0.5, b=1.3, C=20.0),
        ShotStats(5, "drone", L=55, a=6.0, b=11.4, C=14.9),
        ShotStats(6, "title"),
    ]


def test_lab_maths() -> None:
    print("shotmatch - sRGB <-> Lab")
    x = _rng().random((20000, 3))
    rt = sm.lab_to_rgb(sm.rgb_to_lab(x))
    err = float(np.abs(rt - x).max())
    check("round trip error < 1e-4 on random colours", err < 1e-4, f"{err:.2e}")

    white = sm.rgb_to_lab(np.array([1.0, 1.0, 1.0]))
    check(
        "white -> L 100, a and b ~ 0",
        abs(white[0] - 100) < 0.01 and abs(white[1]) < 0.01 and abs(white[2]) < 0.01,
        str(white),
    )
    black = sm.rgb_to_lab(np.array([0.0, 0.0, 0.0]))
    check("black -> L 0", abs(black[0]) < 1e-6, str(black))
    red = sm.rgb_to_lab(np.array([1.0, 0.0, 0.0]))
    check(
        "sRGB red -> L 53.24, a 80.09, b 67.20 (within 0.05)",
        abs(red[0] - 53.24) < 0.05
        and abs(red[1] - 80.09) < 0.05
        and abs(red[2] - 67.20) < 0.05,
        str(red),
    )
    grey = sm.rgb_to_lab(np.full((4, 4, 3), 0.5))
    check("shape preserved on images", grey.shape == (4, 4, 3))
    check(
        "mid grey is neutral",
        float(np.abs(grey[..., 1:]).max()) < 0.01,
        str(grey[0, 0]),
    )
    wild = sm.lab_to_rgb(np.array([50.0, 127.0, -127.0]))
    check("out-of-gamut Lab is clipped to 0..1", wild.min() >= 0 and wild.max() <= 1)


def test_measure() -> None:
    print("shotmatch - measure")
    flat = np.full((8, 8, 3), 0.5, dtype=np.float32)
    L, a, b, C = sm.measure(flat)
    check("flat grey: L ~53, a = b = 0, C = 0", abs(L - 53.39) < 0.1 and abs(a) < 0.01 and C < 0.01, str((L, a, b, C)))

    warm = np.full((8, 8, 3), (0.6, 0.5, 0.35), dtype=np.float32)
    _, wa, wb, wc = sm.measure(warm)
    check("warm patch measures b > 0 and chroma > 0", wb > 10 and wc > 10, str((wa, wb, wc)))

    # shadows and highlights are excluded: a bright blue strip must not move b
    img = np.concatenate([warm, np.zeros((8, 8, 3), np.float32), np.ones((8, 8, 3), np.float32)])
    _, _, ib, _ = sm.measure(img)
    check("black and white pixels are outside the midtone window", abs(ib - wb) < 1e-3, f"{ib} vs {wb}")

    both = sm.measure([warm, warm])
    check("list of frames accepted", abs(both[2] - wb) < 1e-6)

    black = sm.measure(np.zeros((4, 4, 3), np.float32))
    check("all-black falls back to all pixels (no NaN)", all(np.isfinite(v) for v in black))

    # chroma is the mean of per-pixel chroma, not the chroma of the mean
    split = np.concatenate(
        [np.full((4, 8, 3), (0.7, 0.4, 0.4), np.float32), np.full((4, 8, 3), (0.4, 0.6, 0.4), np.float32)]
    )
    _, sa, _, sc = sm.measure(split)
    check("chroma survives opposing a values", sc > abs(sa) + 5, f"a={sa} C={sc}")

    try:
        sm.measure([])
        check("empty frame list raises", False)
    except ValueError:
        check("empty frame list raises", True)


def test_match_params() -> None:
    print("shotmatch - match rules")
    P = sm.match_params(_field_stats())
    check("title -> None", P[6] is None)
    check("every non-title shot has params", all(P[i] is not None for i in range(6)))

    # camera: same group shares one a/b correction, different group differs
    check("same clip -> same da/db", P[0].da == P[1].da and P[0].db == P[1].db)
    check("other clip -> own correction", P[2].db != P[0].db)
    mean_b = (-4.4 + -5.0) / 2
    check("group db uses the group mean", abs(P[0].db - (2.0 - mean_b)) < 1e-9, str(P[0]))
    check("cool camera shot is pushed warm (db > 0)", P[0].db > 5)
    check("camera chroma gain >= 1 (never desaturates)", all(1.0 <= P[i].cg <= 1.3 for i in (0, 1, 2)))

    # AI: no a/b move, chroma only tamed above the target
    check("ai at/below target chroma: identity", P[3] == ShotParams(0.0, 0.0, 1.0), str(P[3]))
    check("ai above target: no a/b move", P[4].da == 0 and P[4].db == 0)
    check("ai above target: chroma tamed within 0.9..1.0", 0.9 <= P[4].cg < 1.0, str(P[4]))

    # drone: db <= 0, cg 0.92, da 0
    check("drone: warmth tamed, never pushed warmer", P[5].db < 0 and P[5].da == 0)
    check("drone: cg 0.92", P[5].cg == 0.92)

    # clipping bounds on extreme inputs
    ext = [
        ShotStats(0, "cam", a=-80, b=-90, C=1.0, group="x"),
        ShotStats(1, "cam", a=90, b=95, C=90.0, group="y"),
        ShotStats(2, "drone", a=0, b=-90, C=10),
        ShotStats(3, "drone", a=0, b=90, C=10),
        ShotStats(4, "ai", a=0, b=0, C=500.0),
        ShotStats(5, "ai", a=0, b=0, C=0.0),
    ]
    E = sm.match_params(ext)
    check("cam da clipped to +-4", E[0].da == 4.0 and E[1].da == -4.0, str((E[0], E[1])))
    check("cam db clipped to +-10", E[0].db == 10.0 and E[1].db == -10.0)
    check("cam cg clipped to 1.0..1.3", E[0].cg == 1.3 and E[1].cg == 1.0, str((E[0], E[1])))
    check("drone db clipped to -5..0", E[2].db == 0.0 and E[3].db == -5.0, str((E[2], E[3])))
    check("ai cg floor 0.9 and C=0 safe", E[4].cg >= 0.9 and E[5].cg == 1.0, str((E[4], E[5])))

    # custom targets are honoured
    T = sm.match_params(_field_stats(), MatchTargets(a=0.0, b=0.0, C=11.3))
    check("custom targets change the result", T[0].db != P[0].db)
    check("cam already on target chroma: cg 1.0", T[2].cg == 1.0, str(T[2]))

    # camera shot without a group is its own group
    solo = sm.match_params([ShotStats(0, "cam", a=0.8, b=-6.0, C=12.5), ShotStats(1, "cam", a=0.8, b=6.0, C=12.5)])
    check("ungrouped cam shots do not share a correction", solo[0].db == 8.0 and solo[1].db == -4.0, str(solo))

    try:
        ShotStats(0, "banana")
        check("unknown kind rejected", False)
    except ValueError:
        check("unknown kind rejected", True)


def test_apply() -> None:
    print("shotmatch - apply")
    x = _rng().random((5000, 3)).astype(np.float32)
    ident = sm.apply(x, ShotParams(), "none")
    check("look none + zero params is identity", float(np.abs(ident - x).max()) < 1e-5)
    check("output is float32 within 0..1", ident.dtype == np.float32 and ident.min() >= 0 and ident.max() <= 1)

    check("params None (title) returns input untouched, any look", all(np.array_equal(sm.apply(x, None, k), x) for k in sm.LOOKS))

    img = np.full((6, 6, 3), (0.45, 0.42, 0.52), dtype=np.float32)  # cool shot
    warm = sm.apply(img, ShotParams(0.0, 8.0, 1.0), "none")
    b0 = sm.measure(img)[2]
    b1 = sm.measure(warm)[2]
    check("db +8 moves the midtone b up by ~8", 6.5 < b1 - b0 < 8.5, f"{b0:.2f} -> {b1:.2f}")

    # corrections are weighted to midtones: black stays black, white stays white-ish
    ends = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]], dtype=np.float32)
    moved = sm.apply(ends, ShotParams(4.0, 10.0, 1.0), "none")
    check("black is not tinted by a/b offsets", float(np.abs(moved[0]).max()) < 1e-3, str(moved[0]))

    desat = sm.apply(img, ShotParams(0.0, 0.0, 0.5), "none")
    check("cg < 1 lowers chroma", sm.measure(desat)[3] < sm.measure(img)[3])

    vivid = np.full((4, 4, 3), (0.6, 0.45, 0.4), dtype=np.float32)
    c0 = sm.measure(vivid)[3]
    look = {k: sm.apply(vivid, ShotParams(), k) for k in sm.LOOKS}
    check("look A raises chroma", sm.measure(look["A"])[3] > c0)
    check("look B lowers chroma", sm.measure(look["B"])[3] < c0)
    check("look C lowers chroma strongly (~-35%)", sm.measure(look["C"])[3] < 0.8 * c0)
    check("the three looks differ from each other", len({look[k].tobytes() for k in "ABC"}) == 3)

    blackB = sm.apply(np.zeros((1, 3), np.float32), ShotParams(), "B")
    check("look B fades the blacks (lifted floor)", float(blackB.min()) > 0.01, str(blackB))
    blackC = sm.apply(np.zeros((1, 3), np.float32), ShotParams(), "C")
    check("look C keeps black at zero", float(blackC.max()) < 1e-4, str(blackC))

    try:
        sm.apply(x, ShotParams(), "Z")
        check("unknown look rejected", False)
    except ValueError:
        check("unknown look rejected", True)


def test_cube() -> None:
    print("shotmatch - .cube bake and check")
    size = 17
    text = sm.bake_cube(lambda v: v, size=size)
    lines = text.splitlines()
    data = [ln for ln in lines if ln and ln[0] in "0123456789"]
    check("header carries LUT_3D_SIZE", f"LUT_3D_SIZE {size}" in lines)
    check("size^3 data lines", len(data) == size ** 3, str(len(data)))
    check("first data line is black", data[0].split() == ["0.000000"] * 3)
    check("R varies fastest", data[1].split() == [f"{1 / (size - 1):.6f}", "0.000000", "0.000000"], data[1])
    check("then G", data[size].split() == ["0.000000", f"{1 / (size - 1):.6f}", "0.000000"], data[size])
    check("then B", data[size * size].split() == ["0.000000", "0.000000", f"{1 / (size - 1):.6f}"], data[size * size])
    check("last data line is white", data[-1].split() == ["1.000000"] * 3)

    # identity cube reproduces input (look none, zero params)
    zero = ShotParams()
    idtext = sm.bake_cube(lambda v: sm.apply(v, zero, "none"))
    s, table = sm.parse_cube(idtext)
    x = _rng().random((3000, 3))
    err = float(np.abs(sm.lookup_cube(table, x) - x).max())
    check("identity cube (none, zero params) reproduces input", err < 2e-4 and s == 33, f"{err:.2e}")

    # baked cube vs apply, per look, with params from the field numbers
    P = sm.match_params(_field_stats())
    for i, label in ((0, "cam"), (3, "ai"), (5, "drone")):
        for look in sm.LOOKS:
            fn = lambda v, p=P[i], k=look: sm.apply(v, p, k)  # noqa: E731
            cc = sm.check_cube(sm.bake_cube(fn), fn)
            check(
                f"cube ~ apply: {label} / look {look}",
                cc.ok and cc.mean_err < 1 / 255,
                cc.summary(),
            )

    # a cube that does not match must fail the check
    strong = ShotParams(4.0, 10.0, 1.3)
    fn = lambda v: sm.apply(v, strong, "A")  # noqa: E731
    bad = sm.check_cube(sm.bake_cube(lambda v: v), fn)
    check("identity cube vs strong look is rejected", not bad.ok, bad.summary())
    try:
        bad.assert_ok()
        check("assert_ok raises on a bad cube", False)
    except ShotMatchFailure:
        check("assert_ok raises on a bad cube", True)
    good = CubeCheck(size=33, samples=10, max_err=0.002, p99_err=0.002, mean_err=0.0005)
    try:
        good.assert_ok()
        check("assert_ok passes a good cube", True)
    except ShotMatchFailure:
        check("assert_ok passes a good cube", False)

    # parse errors are loud
    try:
        sm.parse_cube("LUT_3D_SIZE 3\n0 0 0\n")
        check("truncated cube rejected", False)
    except ShotMatchFailure:
        check("truncated cube rejected", True)

    with tempfile.TemporaryDirectory() as d:
        out = sm.write_cube(Path(d) / "luts" / "A_00.cube", text)
        check("write_cube creates dirs and writes text", out.read_text() == text)


def test_ffmpeg_filter() -> None:
    print("shotmatch - ffmpeg filter")
    plain = sm.ffmpeg_lut_filter("/tmp/luts/A_00.cube")
    check("plain path", plain == "lut3d=file='/tmp/luts/A_00.cube':interp=tetrahedral", plain)
    colon = sm.ffmpeg_lut_filter("C:/luts/a.cube")
    check("colon escaped", "C\\:/luts/a.cube" in colon and "C:/" not in colon, colon)
    quote = sm.ffmpeg_lut_filter("/tmp/it's/a.cube")
    check("single quote closes, escapes and reopens", "it'\\\\\\''s" in quote, quote)
    back = sm.ffmpeg_lut_filter("C:\\luts\\a.cube")
    check("backslash doubled", "C\\:\\\\luts\\\\a.cube" in back, back)
    check("always tetrahedral", all(f.endswith(":interp=tetrahedral") for f in (plain, colon, quote, back)))
    check("accepts Path", sm.ffmpeg_lut_filter(Path("/x/y.cube")).startswith("lut3d=file='/x/y.cube'"))


def test_ffmpeg_roundtrip_optional() -> None:
    global _skipped
    print("shotmatch - real ffmpeg accepts the escaped filter")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        _skipped += 1
        print("  SKIP  (ffmpeg not installed)")
        return
    text = sm.bake_cube(lambda v: v, size=3)
    with tempfile.TemporaryDirectory() as d:
        odd = Path(d) / "a:b" / "it's"
        odd.mkdir(parents=True)
        cube = sm.write_cube(odd / "id.cube", text)
        r = subprocess.run(
            [
                ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=16x16:d=0.04",
                "-vf", sm.ffmpeg_lut_filter(cube), "-frames:v", "1", "-f", "null", "-",
            ],
            capture_output=True,
            text=True,
        )
        check("ffmpeg loads a cube from a path with ':' and \"'\"", r.returncode == 0, r.stderr[-200:])


def main() -> int:
    for fn in (
        test_lab_maths,
        test_measure,
        test_match_params,
        test_apply,
        test_cube,
        test_ffmpeg_filter,
        test_ffmpeg_roundtrip_optional,
    ):
        fn()
    print(f"\n{_passed} passed, {_failed} failed, {_skipped} skipped")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
