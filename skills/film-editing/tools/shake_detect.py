"""Camera-shake detector — sibling of horizon_detect.py.

ffmpeg here has no libvidstab, so shakiness is measured with OpenCV
(already a dependency). We sample frames, estimate the global
frame-to-frame translation via phase correlation, detrend it (a smooth
pan/tilt is intentional and fine), and measure the *high-frequency
residual* — that residual is camera shake, not deliberate movement.

`estimate_shake(path) -> (shake_score | None, method)`:
  shake_score ~ mean per-frame jitter in pixels on a 320px-wide proxy.
  Rough reading: < 1.5 steady, 1.5-3 mild, > 3 shaky (tune via
  `SHAKE_THRESHOLD`). None + reason if OpenCV/decoding unavailable.

`needs_stabilization(score)` applies the threshold.
`stabilize_filter()` returns the ffmpeg fix (built-in `deshake`, since
libvidstab isn't available) for the render path.
"""

from __future__ import annotations

import os
import subprocess
from typing import List, Optional, Tuple

SHAKE_THRESHOLD = float(os.environ.get("SHAKE_THRESHOLD", "3.0"))
_PROXY_W = 320


def _read_frames(src_path, n: int):
    """Decode n evenly-spaced grayscale proxy frames via ffmpeg pipe."""
    import numpy as np

    # duration
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(src_path)],
        capture_output=True, text=True,
    )
    try:
        dur = float(r.stdout.strip())
    except ValueError:
        return []
    if dur <= 0:
        return []

    step = dur / (n + 1)
    frames = []
    for i in range(1, n + 1):
        t = step * i
        p = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(src_path),
             "-frames:v", "1", "-vf", f"scale={_PROXY_W}:-2,format=gray",
             "-f", "rawvideo", "-"],
            capture_output=True,
        )
        if p.returncode != 0 or not p.stdout:
            continue
        # infer height from byte count (width known)
        n_bytes = len(p.stdout)
        h = n_bytes // _PROXY_W
        if h <= 0 or _PROXY_W * h != n_bytes:
            continue
        arr = np.frombuffer(p.stdout, dtype=np.uint8).reshape(h, _PROXY_W)
        frames.append(arr)
    return frames


def estimate_shake(
    src_path, n_samples: int = 24
) -> Tuple[Optional[float], str]:
    try:
        import cv2  # noqa: WPS433
        import numpy as np
    except ModuleNotFoundError:
        return None, "opencv-missing"

    frames = _read_frames(src_path, n_samples)
    if len(frames) < 4:
        return None, "insufficient-frames"

    win = cv2.createHanningWindow(
        (frames[0].shape[1], frames[0].shape[0]), cv2.CV_32F
    )
    shifts: List[Tuple[float, float]] = []
    for a, b in zip(frames, frames[1:]):
        fa = np.float32(a)
        fb = np.float32(b)
        try:
            (dx, dy), _ = cv2.phaseCorrelate(fa * win, fb * win)
        except cv2.error:
            continue
        shifts.append((dx, dy))
    if len(shifts) < 3:
        return None, "no-correlation"

    s = np.array(shifts, dtype=np.float64)  # (k, 2)
    # detrend: subtract a short moving average (deliberate pan is low-freq)
    k = max(3, len(s) // 4) | 1  # odd window
    pad = k // 2
    trend = np.empty_like(s)
    for c in range(2):
        padded = np.pad(s[:, c], pad, mode="edge")
        kernel = np.ones(k) / k
        trend[:, c] = np.convolve(padded, kernel, mode="valid")[: len(s)]
    residual = s - trend
    jitter = float(np.mean(np.sqrt((residual ** 2).sum(axis=1))))
    return round(jitter, 3), "phasecorr"


def needs_stabilization(
    shake_score: Optional[float], threshold: float = SHAKE_THRESHOLD
) -> bool:
    """Coarse ABSOLUTE check. Note: the magnitude of `shake_score` is
    footage-dependent — handheld documentary trips any low absolute
    threshold because subject motion + operator follow is *not* a defect.
    Prefer `flag_stabilization_relative()` for real decisions; this stays
    for callers that genuinely want a fixed floor."""
    return shake_score is not None and shake_score >= threshold


def flag_stabilization_relative(
    scores, floor: float = SHAKE_THRESHOLD, k: float = 2.5
):
    """Decide WHICH clips to stabilize relative to the whole pool.

    A clip is flagged only if it is a true outlier: shakier than
    median + k*MAD across the project AND above an absolute floor.
    Blanket-stabilizing handheld doc footage softens the cut and kills
    its energy — Murch keeps deliberate roughness; we only fix the
    genuinely broken shots. Returns a set of indices into `scores`.
    `scores` items may be None (treated as not-flagged).
    """
    import numpy as np

    vals = [(i, s) for i, s in enumerate(scores) if s is not None]
    if len(vals) < 3:
        return {i for i, s in vals if s is not None and s >= floor}
    arr = np.array([s for _, s in vals], dtype=float)
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med))) or 1e-6
    cutoff = max(floor, med + k * mad)
    return {i for i, s in vals if s >= cutoff}


def stabilize_filter(strength: str = "default") -> str:
    """ffmpeg fix for flagged clips. libvidstab is absent here, so we use
    the built-in `deshake` (no extra dependency)."""
    if strength == "strong":
        return "deshake=rx=64:ry=64:edge=clamp"
    return "deshake=edge=clamp"


if __name__ == "__main__":
    import sys

    for a in sys.argv[1:]:
        sc, m = estimate_shake(a)
        flag = needs_stabilization(sc)
        print(
            f"{a}: shake={sc} ({m}) "
            f"{'-> STABILIZE' if flag else 'steady'}"
        )
