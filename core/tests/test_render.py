"""Tests for the anamorphic-safe render plan.

Run:  python -m core.tests.test_render
Pure — asserts the ffmpeg argv, no video decoded. These pin the
geometry guarantees so the horizontal-squish defect cannot regress.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from core.render import build_render_plan, segment_vf

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


@dataclass
class FakeCorr:
    stabilize: bool = False
    horizon_vf: str | None = None


SEGS = [
    {"src": "/f/a.MTS", "ss": 0, "to": 3, "clip": "/f/a.MTS"},
    {"src": "/f/b.MTS", "ss": 1, "to": 4, "clip": "/f/b.MTS"},
    {"src": "/f/c.MTS", "ss": 2, "to": 5, "clip": "/f/c.MTS"},
]


def test_normalize_always_unsquishes() -> None:
    print("render — every segment un-squishes anamorphic SAR")
    vf = segment_vf(None)
    check("applies pixel aspect (iw*sar)", "iw*sar" in vf, vf)
    check("ends square pixels (setsar=1 last)",
          vf.strip().endswith("setsar=1"), vf)
    check("locks 25 fps", "fps=25" in vf)
    check("fits 1920x1080", "1920:1080" in vf)


def test_no_ffmpeg_deshake_ever() -> None:
    print("render — ffmpeg deshake is NEVER used (Resolve does stabilization)")
    # Primitive deshake looked worse than the source; stabilization is
    # delegated to Resolve's engine via the adapter, not faked in ffmpeg.
    vf_stab = segment_vf(FakeCorr(stabilize=True))
    vf_plain = segment_vf(FakeCorr(stabilize=False))
    check("stabilize-flagged clip has NO deshake", "deshake" not in vf_stab)
    check("plain clip has NO deshake", "deshake" not in vf_plain)
    check("stabilize flag does not alter the ffmpeg chain",
          vf_stab == vf_plain, "stabilization must be Resolve-side only")


def test_horizon_rotate_appended() -> None:
    print("render — horizon vf appended when present")
    vf = segment_vf(FakeCorr(horizon_vf="rotate=-0.03:fillcolor=black"))
    check("rotate in chain", "rotate=-0.03:fillcolor=black" in vf)
    check("still ends setsar=1 (concat-safe)",
          vf.strip().endswith("setsar=1"))


def test_build_plan_shape() -> None:
    print("render — full ffmpeg argv shape")
    corr = {"/f/b.MTS": FakeCorr(stabilize=True),
            "/f/c.MTS": FakeCorr(horizon_vf="rotate=0.01:fillcolor=black")}
    argv = build_render_plan(SEGS, corr, "/tmp/out.mp4")
    j = " ".join(argv)
    check("ffmpeg invocation", argv[0] == "ffmpeg")
    check("one -i per segment", argv.count("-i") == 3)
    check("concat n=3 keeps audio in sync (a=1)", "concat=n=3:v=1:a=1" in j)
    check("maps + encodes audio", "-map [a]" in j and "-c:a aac" in j, j)
    check("NO ffmpeg deshake anywhere (Resolve stabilizes)",
          "deshake" not in j)
    check("tilted c.MTS chain has rotate",
          "rotate=0.01:fillcolor=black" in j)
    check("every segment forced setsar=1",
          j.count("setsar=1") >= 3 * 2)  # NORMALIZE + trailing, per seg
    check("output path carried", argv[-1] == "/tmp/out.mp4")


def test_empty_segments_rejected() -> None:
    print("render — empty segment list rejected")
    raised = False
    try:
        build_render_plan([], {}, "/tmp/x.mp4")
    except ValueError:
        raised = True
    check("ValueError on no segments", raised)


def test_audio_default_on_optional_off() -> None:
    print("render — audio on by default, picture-only on request")
    j_on = " ".join(build_render_plan(SEGS, {}, "/tmp/o.mp4"))
    check("default keeps audio (a=1 + aac)",
          "concat=n=3:v=1:a=1" in j_on and "-c:a aac" in j_on, j_on)
    check("per-segment audio normalized for clean concat",
          "aresample=48000" in j_on, j_on)
    j_off = " ".join(build_render_plan(SEGS, {}, "/tmp/o.mp4", audio=False))
    check("audio=False -> picture only (a=0, no aac)",
          "concat=n=3:v=1:a=0" in j_off and "-c:a" not in j_off, j_off)


def main() -> int:
    for fn in (
        test_normalize_always_unsquishes,
        test_no_ffmpeg_deshake_ever,
        test_horizon_rotate_appended,
        test_build_plan_shape,
        test_empty_segments_rejected,
        test_audio_default_on_optional_off,
    ):
        fn()
    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
