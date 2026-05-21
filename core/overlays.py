"""Overlays — lower-thirds / titles / brand marks burned with ffmpeg.

The other gap the IG "AI video stack" surfaced (idea from video-use, MIT;
our own lightweight implementation — no Remotion/Manim dependency, just
ffmpeg `drawtext`). Carries the project brand DNA (dark bg, lime accent
#C8FF00, white text) and can auto-build lower-thirds straight from a
cutlist's markers (HOOK / PIT / PAYOFF ...), so the same data drives both
the edit and the on-screen titles.

`Overlay` -> a timed text element. `filtergraph()` -> the ffmpeg -vf
chain (each overlay is one `drawtext` gated by enable=between(t,s,e)).
`burn()` renders it. Pure parts (filter building, marker mapping) are
unit-tested without ffmpeg.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# brand DNA
LIME = "0xC8FF00"
WHITE = "white"
DARKBOX = "0x08080c@0.6"

# a sane default font per-OS (drawtext needs a real file)
_FONTS = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def default_font() -> str | None:
    for f in _FONTS:
        if Path(f).exists():
            return f
    return None


def _esc(text: str) -> str:
    """Escape text for ffmpeg drawtext (colons, quotes, backslashes, %)."""
    return (text.replace("\\", "\\\\").replace(":", "\\:")
            .replace("'", "’").replace("%", "\\%"))


@dataclass
class Overlay:
    text: str
    start: float
    end: float
    kind: str = "lower_third"   # lower_third | title | brand
    fontfile: str | None = None

    def drawtext(self, video_w: int = 1920, video_h: int = 1080) -> str:
        t = _esc(self.text)
        ff = self.fontfile or default_font()
        font = f"fontfile='{ff}':" if ff else ""
        enable = f"enable='between(t,{self.start:g},{self.end:g})'"
        if self.kind == "title":
            return (f"drawtext={font}text='{t}':fontcolor={WHITE}:"
                    f"fontsize=72:x=(w-text_w)/2:y=(h-text_h)/2:"
                    f"box=1:boxcolor={DARKBOX}:boxborderw=24:{enable}")
        if self.kind == "brand":
            return (f"drawtext={font}text='{t}':fontcolor={LIME}:"
                    f"fontsize=28:x=w-text_w-48:y=48:{enable}")
        # lower_third (default): white text on a dark box, bottom-left,
        # with a lime accent rule via boxborder
        return (f"drawtext={font}text='{t}':fontcolor={WHITE}:fontsize=44:"
                f"x=64:y=h-180:box=1:boxcolor={DARKBOX}:boxborderw=20:"
                f"{enable}")


def filtergraph(overlays: list[Overlay], video_w: int = 1920,
                video_h: int = 1080) -> str:
    """Comma-joined drawtext chain for ffmpeg -vf (empty if none)."""
    return ",".join(o.drawtext(video_w, video_h) for o in overlays)


def from_markers(cutlist: Any, hold: float = 2.5,
                 kind: str = "lower_third") -> list[Overlay]:
    """Auto lower-thirds from a cutlist's markers: each marker's name
    (and comment) becomes an on-screen title held `hold` seconds from the
    marker time. Reuses the edit's own beat data."""
    out: list[Overlay] = []
    for m in getattr(cutlist, "markers", []):
        text = m.name + (f" — {m.comment}" if getattr(m, "comment", "") else "")
        out.append(Overlay(text=text, start=float(m.time),
                            end=float(m.time) + hold, kind=kind))
    return out


def burn(video: str, overlays: list[Overlay], out: str) -> str:
    vf = filtergraph(overlays)
    if not vf:
        raise ValueError("no overlays to burn")
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", video, "-vf", vf, "-c:a", "copy", out],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError("overlay burn failed:\n" + r.stderr[-1500:])
    return out


def _main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="python -m core.overlays")
    p.add_argument("video")
    p.add_argument("out")
    p.add_argument("--cutlist", help="auto lower-thirds from its markers")
    p.add_argument("--text", help="single overlay text")
    p.add_argument("--kind", default="lower_third")
    p.add_argument("--start", type=float, default=0.0)
    p.add_argument("--end", type=float, default=3.0)
    args = p.parse_args(argv)

    ovs: list[Overlay] = []
    if args.cutlist:
        from core.cutlist import Cutlist
        ovs = from_markers(Cutlist.load(args.cutlist))
    if args.text:
        ovs.append(Overlay(args.text, args.start, args.end, args.kind))
    print("burned", burn(args.video, ovs, args.out), f"({len(ovs)} overlays)")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_main())
