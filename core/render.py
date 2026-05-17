"""Correct picture render — anamorphic-safe, correction-aware.

Born from a real defect: source `.MTS` is 1440x1080 **SAR 4:3**
(anamorphic — it must display 1920x1080). A naive `scale=1920:1080`
ignored the pixel aspect and shipped horizontally-squished faces; the
container DAR still read 16:9 so a thumbnail check missed it. This module
makes the correct geometry the only path, and bakes per-clip cleanup
(stabilization done with a zoom-crop so the compensation border is
off-screen, horizon rotation) into one tested plan builder.

`build_render_plan()` is pure (returns the ffmpeg argv) so the geometry
guarantees are unit-tested without decoding video. `render()` executes it
and is meant to be followed by `core.qc` before anything is called done.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

# Un-squish: apply pixel aspect (iw*sar -> true display width), drop to
# square pixels, fit into 1920x1080, lock 25 fps. Even widths for x264.
NORMALIZE = (
    "scale='trunc(iw*sar/2)*2':ih,setsar=1,"
    "scale=1920:1080:force_original_aspect_ratio=decrease,"
    "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25"
)
# Stabilize the RIGHT way: deshake, then zoom 12% and crop back so the
# wobbling motion-compensation edge is pushed off the frame.
STABILIZE = (
    "deshake=edge=clamp,"
    "scale='trunc(iw*1.12/2)*2':'trunc(ih*1.12/2)*2',crop=1920:1080"
)


def segment_vf(correction: Any | None) -> str:
    """Full per-segment filter chain. Always ends `setsar=1` because
    concat requires identical SAR and trunc/rotate can drift it."""
    vf = NORMALIZE
    if correction is not None:
        if getattr(correction, "stabilize", False):
            vf += "," + STABILIZE
        hv = getattr(correction, "horizon_vf", None)
        if hv:
            vf += "," + hv
    return vf + ",setsar=1"


def build_render_plan(
    segments: list[dict[str, Any]],
    corrections: dict[str, Any] | None,
    out_path: str,
) -> list[str]:
    """segments: [{src, ss, to, clip}]. corrections: {clip: Correction}.
    Returns the ffmpeg argv (picture only, no audio)."""
    if not segments:
        raise ValueError("no segments to render")
    corrections = corrections or {}
    argv = ["ffmpeg", "-y"]
    for s in segments:
        argv += ["-ss", str(s["ss"]), "-to", str(s["to"]), "-i", s["src"]]
    chains = []
    for i, s in enumerate(segments):
        c = corrections.get(s.get("clip"))
        chains.append(f"[{i}:v:0]{segment_vf(c)}[v{i}]")
    n = len(segments)
    fc = (";".join(chains) + ";"
          + "".join(f"[v{i}]" for i in range(n))
          + f"concat=n={n}:v=1:a=0[v]")
    argv += [
        "-filter_complex", fc, "-map", "[v]", "-r", "25",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", out_path,
    ]
    return argv


def render(
    segments: list[dict[str, Any]],
    corrections: dict[str, Any] | None,
    out_path: str,
) -> str:
    argv = build_render_plan(segments, corrections, out_path)
    r = subprocess.run(argv, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(
            "render failed:\n" + r.stderr[-2000:]
        )
    if not Path(out_path).exists():
        raise RuntimeError("render produced no output file")
    return out_path
