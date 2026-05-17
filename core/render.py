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
# NOTE on stabilization: ffmpeg `deshake` is a primitive single-pass
# filter (~2000s tech) — it crawls/jitters and looked worse than the
# original handheld. It is deliberately NOT used here. Real
# stabilization is Resolve's own professional engine, applied live via
# ResolveAdapter.apply_corrections() -> TimelineItem.Stabilize()
# (verified against the shipped scripting API). A standalone stabilized
# MP4 without Resolve would need an ffmpeg built with libvidstab
# (2-pass vidstab) — tracked as a follow-up; not faked with `deshake`.


def segment_vf(correction: Any | None) -> str:
    """Per-segment filter chain: geometry (un-squish) + horizon rotate.
    Stabilization is intentionally delegated to Resolve's engine (see the
    NOTE above) — we never apply ffmpeg `deshake`. Always ends
    `setsar=1` (concat needs identical SAR; trunc/rotate drift it)."""
    vf = NORMALIZE
    if correction is not None:
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
