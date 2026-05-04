#!/usr/bin/env python3
"""
horizon_detect.py — estimate the tilt angle of a clip's horizon.

v2 algorithm (sky-ground segmentation, robust to man-made angled structures):

  1. Sample N frames evenly through the clip.
  2. For each frame:
     a. Convert to HSV.
     b. Build sky mask via brightness + saturation thresholds (sky is bright + low-sat).
     c. For each column, find the sky-ground boundary y-coordinate.
     d. Fit a line (RANSAC) through those boundary points → that line IS the horizon.
     e. Compute its angle from horizontal.
  3. If sky-ground segmentation yields too few boundary points (close shots,
     no sky visible), fall back to Hough lines weighted by length.
  4. Aggregate across frames using median (robust to outliers).

Returns angle in degrees; positive = scene rotated clockwise relative to true
horizon (i.e. needs counter-clockwise rotation to level).

Usage:
  python horizon_detect.py /path/to/clip.MTS [N_frames]

Or import:
  from horizon_detect import estimate_tilt
  angle = estimate_tilt("clip.mp4", n_samples=8)
"""

import math
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np


# ---------- ffmpeg helpers ----------

def extract_frame(src: Path, t_sec: float, scale_w: int = 960) -> Optional[np.ndarray]:
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


def clip_duration(src: Path) -> float:
    r = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(src)
    ], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


# ---------- detector v2: sky-ground segmentation ----------

def sky_ground_horizon(img: np.ndarray) -> Optional[float]:
    """
    Detect the sky-ground boundary line.
    Returns angle in degrees (positive = CW tilt) or None if sky not detected.
    """
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    H, S, V = cv2.split(hsv)

    # Sky heuristic: bright + low saturation, OR blue hue + decent brightness
    bright = V > 140
    desaturated = S < 80
    blue_hue = ((H >= 90) & (H <= 130)) & (V > 100)
    sky_mask = (bright & desaturated) | blue_hue

    # Restrict to upper 70% of frame (ground rarely "floats" at top)
    sky_mask[int(h * 0.7):] = False

    sky_pixels = sky_mask.sum()
    if sky_pixels < 0.05 * h * w:  # less than 5% of frame
        return None

    # For each column, find lowest sky pixel — that's the boundary
    boundary_pts = []  # (x, y)
    for x in range(0, w, 2):  # every 2nd column for speed
        col = sky_mask[:, x]
        idx = np.where(col)[0]
        if len(idx) > 5:  # need a meaningful sky chunk
            boundary_pts.append((x, int(idx[-1])))  # last sky pixel = top of ground

    if len(boundary_pts) < 30:
        return None

    pts = np.array(boundary_pts, dtype=np.float32)

    # RANSAC line fit (robust to trees, hills, occlusion)
    # Use cv2.fitLine with L2 distance
    try:
        # Manual RANSAC for transparency: try 200 random pairs, pick line with most inliers
        best_inliers = 0
        best_angle = None
        n = len(pts)
        rng = np.random.default_rng(42)
        for _ in range(200):
            i, j = rng.choice(n, size=2, replace=False)
            x1, y1 = pts[i]
            x2, y2 = pts[j]
            if abs(x2 - x1) < 5:
                continue
            slope = (y2 - y1) / (x2 - x1)
            intercept = y1 - slope * x1
            # Inliers: points within 6px of this line
            predicted_y = slope * pts[:, 0] + intercept
            distances = np.abs(pts[:, 1] - predicted_y)
            inliers = (distances < 6).sum()
            if inliers > best_inliers:
                best_inliers = inliers
                best_angle = math.degrees(math.atan(slope))
        if best_angle is None or best_inliers < 20:
            return None
        return float(best_angle)
    except Exception:
        return None


# ---------- detector fallback: Hough lines weighted by length ----------

def hough_tilt(img: np.ndarray, max_deviation_deg: float = 15.0) -> Optional[float]:
    """Fallback: weighted-median angle of long horizontal-ish line segments."""
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)

    h, w = edges.shape
    lines = cv2.HoughLinesP(
        edges, rho=1, theta=np.pi / 180, threshold=100,
        minLineLength=w // 4,  # only LONG lines (likely real references)
        maxLineGap=10
    )
    if lines is None:
        return None

    weighted = []  # (angle, length)
    for line in lines:
        x1, y1, x2, y2 = line[0]
        if x2 == x1:
            continue
        a = math.degrees(math.atan2(y2 - y1, x2 - x1))
        if a > 90: a -= 180
        elif a < -90: a += 180
        if abs(a) <= max_deviation_deg:
            length = math.hypot(x2 - x1, y2 - y1)
            weighted.append((a, length))

    if not weighted:
        return None

    # Weighted median: sort by angle, find midpoint by cumulative weight
    weighted.sort(key=lambda x: x[0])
    total = sum(w for _, w in weighted)
    cum = 0
    for angle, w in weighted:
        cum += w
        if cum >= total / 2:
            return float(angle)
    return float(weighted[-1][0])


# ---------- public API ----------

def estimate_tilt(src_path, n_samples: int = 8) -> Tuple[Optional[float], str]:
    """
    Returns (angle_degrees, method_used).
    method_used in: 'sky-ground', 'hough-fallback', 'no-detection'.
    """
    src = Path(src_path)
    dur = clip_duration(src)
    if dur <= 0:
        return None, "no-detection"
    times = [dur * (i + 0.5) / n_samples for i in range(n_samples)]

    sky_angles = []
    hough_angles = []
    for t in times:
        frame = extract_frame(src, t)
        if frame is None:
            continue
        sky = sky_ground_horizon(frame)
        if sky is not None:
            sky_angles.append(sky)
        else:
            h = hough_tilt(frame)
            if h is not None:
                hough_angles.append(h)

    if sky_angles:
        return round(float(np.median(sky_angles)), 2), "sky-ground"
    if hough_angles:
        return round(float(np.median(hough_angles)), 2), "hough-fallback"
    return None, "no-detection"


def correction_filter(angle_deg: float, threshold_deg: float = 0.4) -> Optional[str]:
    """ffmpeg rotate filter to undo tilt. None if below threshold."""
    if abs(angle_deg) < threshold_deg:
        return None
    rad = -math.radians(angle_deg)
    return f"rotate={rad:.6f}:fillcolor=black"


def main():
    if len(sys.argv) < 2:
        print("usage: horizon_detect.py SRC [N_frames]")
        sys.exit(1)
    src = Path(sys.argv[1])
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    angle, method = estimate_tilt(src, n)
    if angle is None:
        print(f"{src.name}: no horizon detected ({method})")
        sys.exit(0)
    direction = "clockwise" if angle > 0 else "counter-clockwise"
    print(f"{src.name}: tilt = {angle:+.2f}° ({direction}) [{method}]")
    flt = correction_filter(angle)
    if flt:
        print(f"  → suggested ffmpeg correction: -vf \"{flt}\"")
    else:
        print(f"  → within threshold (±0.4°), no correction needed")


if __name__ == "__main__":
    main()
