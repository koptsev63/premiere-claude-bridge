#!/usr/bin/env python3
"""
horizon_detect.py — estimate the tilt angle of a clip's horizon.

Algorithm:
  1. Sample N frames evenly through the clip (default 5).
  2. For each frame: convert to gray, edge-detect (Canny), find lines via
     probabilistic Hough transform.
  3. Filter to "near-horizontal" lines (within ±20° of horizontal).
  4. Take the median angle of those lines.
  5. Aggregate across frames (median of medians) — robust to one bad frame.

Returns angle in degrees; positive = scene rotated clockwise relative to true
horizon (i.e. needs counter-clockwise rotation to level).

Usage:
  python horizon_detect.py /path/to/clip.MTS [N_frames]

Or import:
  from horizon_detect import estimate_tilt
  angle = estimate_tilt("clip.mp4", n_samples=5)

Pipeline integration:
  - Add `horizon_tilt_deg` to analyze_clips.py per-clip metadata.
  - When |tilt| > THRESH (default 0.5°), suggest ffmpeg rotate filter for
    encode-time correction:
       -vf "rotate={radians}:fillcolor=black:ow=rotw({radians}):oh=roth({radians})"
"""

import math
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


def extract_frame(src: Path, t_sec: float, scale_w: int = 960) -> Optional[np.ndarray]:
    """Pull one frame at t_sec via ffmpeg, decode with OpenCV."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
        tmp = Path(tf.name)
    try:
        r = subprocess.run([
            "ffmpeg", "-y", "-ss", str(t_sec), "-i", str(src),
            "-vframes", "1", "-vf", f"scale={scale_w}:-1",
            str(tmp)
        ], capture_output=True)
        if r.returncode != 0 or tmp.stat().st_size < 100:
            return None
        return cv2.imread(str(tmp))
    finally:
        tmp.unlink(missing_ok=True)


def frame_tilt(img: np.ndarray, max_deviation_deg: float = 20.0) -> Optional[float]:
    """
    Estimate tilt of one frame in degrees. Positive = clockwise tilt.
    Returns None if no usable horizontal-ish lines were found.
    """
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)

    # Probabilistic Hough — gives line segments
    lines = cv2.HoughLinesP(
        edges, rho=1, theta=np.pi / 180, threshold=80,
        minLineLength=img.shape[1] // 6, maxLineGap=10
    )
    if lines is None:
        return None

    angles_deg = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        if x2 == x1:
            continue  # vertical line, ignore
        a = math.degrees(math.atan2(y2 - y1, x2 - x1))
        # Normalize to [-90, 90]
        if a > 90:
            a -= 180
        elif a < -90:
            a += 180
        # Keep only near-horizontal lines (potential horizon candidates)
        if abs(a) <= max_deviation_deg:
            angles_deg.append(a)

    if not angles_deg:
        return None
    return float(np.median(angles_deg))


def clip_duration(src: Path) -> float:
    r = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(src)
    ], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def estimate_tilt(src_path, n_samples: int = 5) -> Optional[float]:
    """
    Median-of-medians estimate of horizon tilt across N evenly-spaced samples.
    Returns degrees (positive = clockwise tilt; needs CCW rotation to level).
    Returns None if no frames had usable horizontal lines.
    """
    src = Path(src_path)
    dur = clip_duration(src)
    if dur <= 0:
        return None
    times = [dur * (i + 0.5) / n_samples for i in range(n_samples)]
    per_frame = []
    for t in times:
        frame = extract_frame(src, t)
        a = frame_tilt(frame)
        if a is not None:
            per_frame.append(a)
    if not per_frame:
        return None
    return round(float(np.median(per_frame)), 2)


def correction_filter(angle_deg: float, threshold_deg: float = 0.5) -> Optional[str]:
    """
    Build an ffmpeg -vf rotate filter string to undo a tilt of `angle_deg`.
    Returns None if tilt is below threshold (no correction worthwhile).
    """
    if abs(angle_deg) < threshold_deg:
        return None
    rad = -math.radians(angle_deg)  # negate to undo
    # rotate filter expands canvas to fit rotated frame, fills new area black
    return f"rotate={rad:.6f}:fillcolor=black:ow=rotw({rad:.6f}):oh=roth({rad:.6f})"


def main():
    if len(sys.argv) < 2:
        print("usage: horizon_detect.py SRC [N_frames]")
        sys.exit(1)
    src = Path(sys.argv[1])
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    angle = estimate_tilt(src, n)
    if angle is None:
        print(f"{src.name}: no horizontal lines detected")
        sys.exit(0)
    direction = "clockwise" if angle > 0 else "counter-clockwise"
    print(f"{src.name}: tilt = {angle:+.2f}° ({direction})")
    flt = correction_filter(angle)
    if flt:
        print(f"  → suggested ffmpeg correction: -vf \"{flt}\"")
    else:
        print(f"  → within threshold (±0.5°), no correction needed")


if __name__ == "__main__":
    main()
