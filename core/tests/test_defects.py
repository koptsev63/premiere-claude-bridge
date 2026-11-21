"""Tests for core.defects on tiny synthetic clips built with ffmpeg lavfi.

Run:  python3 -m core.tests.test_defects
Skips cleanly (exit 0) when ffmpeg / ffprobe are missing.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from core.defects import (
    check,
    dhash,
    hamming,
    is_card,
    main as cli_main,
    merge_cuts,
    stutter_share,
)

_p = _f = 0
SIZE = "320x180"
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p"]
LIVE = f"testsrc2=size={SIZE}:rate=25"
LIVE_B = f"mandelbrot=size={SIZE}:rate=25"
CARD = f"color=c=black:size={SIZE}:rate=25,drawbox=x=110:y=60:w=100:h=60:color=white:t=fill"


def check_(name: str, cond: bool, detail: str = "") -> None:
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


def ff(out: Path, *args: str) -> Path:
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", *args, str(out)]
    subprocess.run(cmd, check=True)
    return out


def kinds(rep, level: str) -> list:
    return [f.kind for f in rep.findings if f.level == level]


def summary(rep) -> str:
    return "; ".join(f"{f.level}:{f.kind}:{f.message}" for f in rep.findings)


# --------------------------------------------------------------------------- #
# Unit
# --------------------------------------------------------------------------- #


def test_units() -> None:
    black = np.zeros((90, 160), np.uint8)
    black[30:60, 50:110] = 255
    check_("card: white box on black", is_card(black))
    flat = np.full((90, 160), 128, np.uint8)
    flat[40:50, 60:100] = 20
    check_("card: logo on flat gray", is_card(flat))
    rng = np.random.default_rng(1)
    noise = rng.integers(0, 256, (90, 160), dtype=np.uint8)
    check_("card: noise is live picture", not is_card(noise))
    check_("dhash: same frame distance 0", hamming(dhash(noise), dhash(noise)) == 0)
    check_("dhash: different frames far", hamming(dhash(noise), dhash(flat)) > 6)
    check_("merge: flash pair is one cut", merge_cuts([1.0, 1.08, 3.0, 0.01], 5.0) == [1.0, 3.0])
    d = np.array([3.0, 0.1] * 10)
    check_("stutter share: half duplicates", abs(stutter_share(d) - 0.5) < 1e-9)
    check_("stutter share: static shot ignored", stutter_share(np.full(20, 0.1)) is None)


# --------------------------------------------------------------------------- #
# Synthetic clips
# --------------------------------------------------------------------------- #


def test_clips(tmp: Path) -> None:
    # 1. live shot whose last frame is cloned for 1 s -> freeze FAIL, cloned-frame message
    p = ff(tmp / "freeze.mp4", "-f", "lavfi", "-t", "2", "-i", LIVE_B,
           "-vf", "tpad=stop_mode=clone:stop_duration=1", *ENC)
    rep = check(str(p), cuts=[])
    fz = [f for f in rep.fails if f.kind == "freeze"]
    check_("freeze: cloned tail fails", bool(fz), summary(rep))
    check_("freeze: says segment longer than source", any("longer than its source" in f.message for f in fz),
           summary(rep))

    # 2. static white box on black for 2 s -> a card: note, no fail
    p = ff(tmp / "card.mp4", "-f", "lavfi", "-t", "2", "-i", CARD, *ENC)
    rep = check(str(p), cuts=[])
    check_("card: no fail", not rep.fails, summary(rep))
    check_("card: freeze listed as note", "freeze" in kinds(rep, "note"), summary(rep))

    # 3. same segment twice with another shot between -> repeat WARN
    a = ff(tmp / "a.mp4", "-f", "lavfi", "-t", "1", "-i", LIVE, *ENC)
    b = ff(tmp / "b.mp4", "-f", "lavfi", "-t", "1", "-i", LIVE_B, *ENC)
    lst = tmp / "aba.txt"
    lst.write_text(f"file '{a}'\nfile '{b}'\nfile '{a}'\n")
    p = ff(tmp / "aba.mp4", "-f", "concat", "-safe", "0", "-i", str(lst), *ENC)
    rep = check(str(p), cuts=[1.0, 2.0])
    rp = [f for f in rep.findings if f.kind == "repeat"]
    check_("repeat: A-B-A warns", any(f.level == "warn" for f in rp), summary(rep))
    check_("repeat: names both times", any("0.00" in f.message and "2.00" in f.message for f in rp),
           summary(rep))

    # 4. 12 fps source conformed to 24 fps by frame duplication -> stutter
    p = ff(tmp / "stutter.mp4", "-f", "lavfi", "-t", "3", "-i", f"mandelbrot=size={SIZE}:rate=12",
           "-vf", "fps=24", *ENC)
    rep = check(str(p), cuts=[])
    check_("stutter: 12->24 fps warns", "stutter" in kinds(rep, "warn"), summary(rep))
    check_("stutter: every shot stutters -> file fail", "stutter" in kinds(rep, "fail"), summary(rep))

    # 5. non-square pixels -> geometry FAIL
    p = ff(tmp / "sar.mp4", "-f", "lavfi", "-t", "1", "-i", LIVE_B, "-vf", "setsar=4/3", *ENC)
    rep = check(str(p), cuts=[])
    check_("geometry: SAR 4:3 fails", "geometry" in kinds(rep, "fail"), summary(rep))
    check_("geometry: header DAR", abs(rep.dar - 320 * 4 / 3 / 180) < 0.01, str(rep.dar))
    # anamorphic storage that lands on 16:9 (HDV 1440x1080 at SAR 4:3) is a warning, not a fail
    p = ff(tmp / "ana.mp4", "-f", "lavfi", "-t", "1", "-i", LIVE_B, "-vf", "scale=240:180,setsar=4/3", *ENC)
    rep = check(str(p), cuts=[])
    check_("geometry: anamorphic 16:9 warns, does not fail",
           "geometry" in kinds(rep, "warn") and "geometry" not in kinds(rep, "fail"), summary(rep))

    # 6. sine cut without fade -> audio WARN; with afade out -> no warn
    for name, af, want in (("cut", "anull", True), ("fade", "afade=t=out:st=1.5:d=0.5", False)):
        p = ff(tmp / f"audio_{name}.mp4", "-f", "lavfi", "-t", "2", "-i", LIVE_B,
               "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
               "-af", af, *ENC, "-c:a", "aac", "-shortest")
        rep = check(str(p), cuts=[])
        got = "audio" in kinds(rep, "warn")
        check_(f"audio: {name} -> warn={want}", got == want, summary(rep))

    # 7. clean two-shot clip with a faded sound -> zero findings
    lst = tmp / "ab.txt"
    lst.write_text(f"file '{a}'\nfile '{b}'\n")
    p = ff(tmp / "clean.mp4", "-f", "concat", "-safe", "0", "-i", str(lst),
           "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
           "-af", "afade=t=out:st=1.5:d=0.5", *ENC, "-c:a", "aac", "-shortest")
    rep = check(str(p))
    check_("clean: zero findings", not rep.findings, summary(rep))
    check_("clean: scene detect finds the cut", any(abs(c - 1.0) < 0.1 for c in rep.cuts), str(rep.cuts))

    # CLI exit codes
    check_("cli: clean exit 0", cli_main([str(p)]) == 0)
    check_("cli: freeze exit 1", cli_main([str(tmp / "freeze.mp4"), "--cuts", ""]) == 1)
    check_("cli: missing file exit 2", cli_main([str(tmp / "nope.mp4")]) == 2)


def main() -> int:
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("  SKIP  ffmpeg / ffprobe not found")
        return 0
    print("--- units ---")
    test_units()
    print("--- synthetic clips ---")
    with tempfile.TemporaryDirectory() as d:
        test_clips(Path(d))
    print(f"\n{_p} passed, {_f} failed")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
