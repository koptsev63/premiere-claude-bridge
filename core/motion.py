"""Motion at cuts - does the action carry across the edit?

An editor cuts on motion: a gesture or a camera move that starts in shot A
finishes in shot B, screen direction is kept, and the viewer's eye lands
where it was already looking (Murch's eye-trace). A still frame on each side
of a cut cannot show any of that. This module measures it with optical flow,
the same way `core.beats` + `core.rhythm_gate` measure cuts against music.
Numbers only, no model.

Method, per cut at time t:

1. Side A = the last `win` seconds before t, side B = the first `win` seconds
   after t. The frame nearest the cut on each side is shown on the sheet but
   not measured (scene detection jitters by a frame, dissolves smear the edge).
2. Every consecutive frame pair on a side goes through Farneback dense flow at
   an analysis width of 320 px (grayscale). The flow is sampled on a grid
   (every 8 px) and a similarity transform (translation + zoom + rotation) is
   fitted with RANSAC. That fit is the camera. What the fit does not explain
   (residual flow) is the subject: grid points whose residual is above
   max(0.5 px, 3 x median residual).
3. Pairs are averaged into a `SideMotion`; two sides are compared in
   `judge()`.

Conventions (all speeds are in frame widths per second, "fw/s"):

- `pan_x`, `pan_y`: how the IMAGE CONTENT moves, positive = content moves
  right / down. A camera that physically pans right makes content move left,
  so it has pan_x < 0 and the label "pan right" (labels are about the camera).
- `zoom_per_s`: scale change per second, positive = content grows (push in).
- Subject direction `vx, vy` is motion relative to the background, that is
  motion in the world, so a runner followed by a panning camera still has
  vx > 0 although he sits still in the frame.
- TRAVEL vector (`travel_x`, `travel_y`, fw/s, right/down positive) is the
  direction the viewer perceives things moving. With a subject (share >=
  0.02 and speed above the dead band) it is the subject's image velocity minus
  the background's image velocity, i.e. its motion relative to the scene: a
  tracking pan on a runner gives the runner's direction although he is still
  on screen. With no subject it is the camera direction, minus the background
  image velocity (-pan_x, -pan_y); a push or pull has no travel direction.
- Two moving sides are compared by the ANGLE between their travel vectors:
  cos >= 0.5 "action continues" (then a speed ratio outside 0.5..2 is a
  "speed jump"), cos <= -0.5 "direction flip", otherwise "direction turns"
  (e.g. a pan right cut to a tilt up).
- Textureless regions (sky, walls, fog) give noisy flow. A texture mask
  (cornerMinEigenVal, block 7) drops grid points that are below the frame's
  30th percentile AND below an absolute floor, both from the camera fit and
  from the subject residual.

Honest boundaries - the numbers are engineering defaults, not calibrated on a
corpus:

- Optical flow sees texture. Flat walls, fog, black frames, strong motion
  blur or a whip pan give no flow; a side that dark or flat is marked "flat"
  and not judged.
- The camera fit assumes one dominant rigid background. A big subject that
  fills the frame is taken for the camera; parallax and a rolling shutter
  leak into the "subject".
- The subject is a blob of residual motion, not an identified person. The eye
  trace is the distance between two blobs, a proxy for where the eye sits.
- The window is short on purpose (0.4 s). A slow move that is below the
  thresholds counts as still.

```bash
python3 -m core.motion VIDEO.mp4                      # scene-detect the cuts
python3 -m core.motion VIDEO.mp4 --cuts 5.24,9.36     # or name them
python3 -m core.motion VIDEO.mp4 --sheet sheet.jpg    # + contact sheet
python3 -m core.motion VIDEO.mp4 --json
```
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from typing import Optional

# --------------------------------------------------------------------------- #
# Constants (every threshold the verdict depends on)
# --------------------------------------------------------------------------- #

ANALYSIS_WIDTH = 320          # px, analysis and sheet width
GRID_STEP = 8                 # px between flow samples
DEF_WIN = 0.4                 # s per side
EDGE_GUARD_S = 0.05           # cuts closer than this to start/end are dropped
MIN_PAIRS = 2                 # a side needs at least this many flow pairs

FLAT_BRIGHTNESS = 20.0        # mean gray level (0-255) below this = black
DARK_MEDIAN = 12.0            # median gray below this = a card: title or logo on black
FLAT_TEXTURE = 3.0            # mean per-frame gray std below this = flat field
FLASH_WHITE = 235.0           # a frame this bright on a side = flash frame
FLASH_STEP = 60.0             # brightness jump between two frames of a side = flash
FADE_DELTA = 25.0             # brightness drift over the window = fade in / out
EFFECT_ZOOM = 0.5             # 1/s: a zoom faster than this is a transition effect
BURST_GAP = 0.15              # s: cuts closer than this (under 4 frames) are one flash / effect, not edits
DUP_DIFF = 0.3                # mean abs gray difference below this = the same frame again ...
DUP_REL = 0.25                # ... and below this share of the side's live (80th percentile) difference
DUP_LIVE = 0.2                # a side whose live difference is below this is simply static
VFR_TOLERANCE = 0.01          # avg vs nominal frame rate apart by more than this = variable rate

SUBJECT_MIN_PX = 0.5          # residual floor, px at analysis width
SUBJECT_MEDIAN_K = 3.0        # ... or this many times the median residual
SUBJECT_MIN_SHARE = 0.02      # below this share no centroid is reported

TEXTURE_BLOCK = 7             # cornerMinEigenVal block size
TEXTURE_PCT = 30.0            # drop points below this frame percentile ...
TEXTURE_FLOOR = 1e-4          # ... AND below this (gray/255 scale; sky ~6e-6, textured ~5e-4)
MIN_GRID_POINTS = 60          # fewer textured points than this: pair not measured
COS_SAME = 0.5                # travel vectors: cos >= this same direction
COS_OPPOSITE = -0.5           # ... <= this opposite; between = "direction turns"

STATIC_PAN = 0.03             # fw/s: below this a pan / tilt is "static"
STATIC_ZOOM = 0.08            # 1/s: below this a push / pull is "static"
MOVING_SPEED = 0.04           # fw/s: a side whose motion is below is "still"
SPEED_RATIO = (0.5, 2.0)      # speed B / speed A inside = same pace

COHERENT = 0.6                # per-pair directions must agree this much (0..1) to be motion
FLIP_MIN_SPEED = 0.10         # fw/s: both sides at least this fast before a flip is called
SUBJECT_MAX_SPREAD = 0.15     # frame units: centroid scatter across pairs of a real subject

EYE_STAYS = 0.15              # frame-diagonal units
EYE_JUMPS = 0.42
EYE_MIN_SHARE = 0.05          # both subjects at least this big before "eye jumps" is called
EYE_EDGE = 0.08               # a centroid this close to the frame edge is not an eye target

ARROW_PX_PER_FW = 1.5         # sheet arrow length: px per (fw/s * frame width)


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #


@dataclass
class SideMotion:
    flat: bool = False
    note: str = ""            # why a side is not judged: dark, flat field, flash, fade, effect, short
    pairs: int = 0
    pan_x: float = 0.0
    pan_y: float = 0.0
    zoom_per_s: float = 0.0
    camera_label: str = "static"
    travel_x: float = 0.0
    travel_y: float = 0.0
    share: float = 0.0
    cx: Optional[float] = None
    cy: Optional[float] = None
    vx: float = 0.0
    vy: float = 0.0
    speed: float = 0.0
    energy: float = 0.0
    brightness: float = 0.0
    texture: float = 0.0
    cam_coherence: float = 1.0    # 1 = every pair pans the same way, 0 = jitter
    subj_coherence: float = 1.0   # same for the subject's direction
    spread: float = 0.0           # scatter of the subject centroid across pairs, frame units
    dup_ratio: float = 0.0        # share of repeated frames taken out before measuring

    @property
    def kind(self) -> str:
        """What the travel vector comes from: "subject", "camera" or ""."""
        if self.subject_moving:
            return "subject"
        if self.camera_label.startswith(("pan", "tilt")) and self.camera_speed >= STATIC_PAN:
            return "camera"
        return ""

    @property
    def steady(self) -> bool:
        """True when the travel is one coherent move, not flicker (generated footage, grain, smoke)."""
        if self.kind == "subject":
            return self.subj_coherence >= COHERENT and self.spread <= SUBJECT_MAX_SPREAD
        if self.kind == "camera":
            return self.cam_coherence >= COHERENT
        return False

    @property
    def camera_speed(self) -> float:
        if self.camera_label == "static":
            return 0.0
        return max(math.hypot(self.pan_x, self.pan_y), abs(self.zoom_per_s) * 0.5)

    @property
    def subject_moving(self) -> bool:
        return self.cx is not None and self.speed >= MOVING_SPEED

    @property
    def moving(self) -> bool:
        return self.subject_moving or self.camera_speed >= STATIC_PAN

    @property
    def travel(self) -> float:
        return math.hypot(self.travel_x, self.travel_y)

    def set_travel(self) -> None:
        if self.subject_moving:
            self.travel_x, self.travel_y = self.vx, self.vy
        elif self.camera_label.startswith(("pan", "tilt")) and self.camera_speed >= STATIC_PAN:
            self.travel_x, self.travel_y = -self.pan_x, -self.pan_y
        else:
            self.travel_x = self.travel_y = 0.0

    @property
    def pace(self) -> float:
        return max(self.speed if self.subject_moving else 0.0, self.camera_speed)


@dataclass
class CutMotion:
    index: int
    t: float
    a: SideMotion
    b: SideMotion
    verdict: str
    eye_distance: Optional[float] = None
    eye_label: Optional[str] = None
    size: tuple = field(default=(ANALYSIS_WIDTH, 180))
    burst: int = 1            # scene-detect hits merged into this cut (flash, strobe, effect)
    t_end: Optional[float] = None

    @property
    def flagged(self) -> bool:
        """The two verdicts worth a second look. Everything else is a description."""
        return self.verdict == "direction flip" or self.eye_label == "eye jumps"


# --------------------------------------------------------------------------- #
# Deps and decoding
# --------------------------------------------------------------------------- #


def _deps():
    import cv2
    import numpy as np
    return cv2, np


def _open(path: str):
    cv2, _ = _deps()
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    return cap, fps, n


def _scaled(frame, width: int = ANALYSIS_WIDTH):
    cv2, _ = _deps()
    h, w = frame.shape[:2]
    if w == width:
        return frame
    return cv2.resize(frame, (width, max(2, round(h * width / w))), interpolation=cv2.INTER_AREA)


def _read_range(cap, start: int, count: int) -> list:
    """Decode `count` frames from index `start`, scaled to the analysis width."""
    cv2, _ = _deps()
    start = max(0, start)
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    out = []
    for _ in range(count):
        ok, fr = cap.read()
        if not ok:
            break
        out.append(_scaled(fr))
    return out


def is_vfr(path: str) -> bool:
    """True when the average and the nominal frame rate differ: frame index != time * fps."""
    import subprocess
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=r_frame_rate,avg_frame_rate", "-of", "csv=p=0", path],
                           capture_output=True, text=True, timeout=30)
        vals = []
        for part in r.stdout.strip().replace("\n", ",").split(","):
            if "/" in part:
                n, d = part.split("/")
                if float(d):
                    vals.append(float(n) / float(d))
        return len(vals) == 2 and min(vals) > 0 and abs(vals[0] - vals[1]) / max(vals) > VFR_TOLERANCE
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def _read_span(cap, t0: float, t1: float) -> list:
    """Frames whose timestamps fall in [t0, t1), as (seconds, frame) - for variable-rate files."""
    cv2, _ = _deps()
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t0 - 0.5) * 1000.0)
    out = []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        ts = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        if ts >= t1:
            break
        if ts >= t0:
            out.append((ts, _scaled(fr)))
        if len(out) > 400:
            break
    return out


def _side_fps(span: list, fallback: float) -> float:
    return (len(span) - 1) / (span[-1][0] - span[0][0]) if len(span) > 1 and span[-1][0] > span[0][0] else fallback


def cuts_from_media(path: str, threshold: float = 0.3) -> list[float]:
    """Scene cuts (ffmpeg select), minus any within 0.05 s of start or end."""
    from core.highlights import dip_cuts, scene_cuts

    cap, fps, n = _open(path)
    dur = n / fps if n else 0.0
    cap.release()
    cuts = scene_cuts(path, threshold=threshold)
    cuts = sorted(set(cuts + dip_cuts(path, cuts)))     # bursts are merged later, in analyze()
    return [c for c in cuts if c > EDGE_GUARD_S and (not dur or c < dur - EDGE_GUARD_S)]


# --------------------------------------------------------------------------- #
# Per-pair and per-side measurement
# --------------------------------------------------------------------------- #


def camera_label(pan_x: float, pan_y: float, zoom: float) -> str:
    """Dominant camera move, as a label about the camera (not the image)."""
    cands = [
        (abs(pan_x) / STATIC_PAN, "pan right" if pan_x < 0 else "pan left"),
        (abs(pan_y) / STATIC_PAN, "tilt down" if pan_y < 0 else "tilt up"),
        (abs(zoom) / STATIC_ZOOM, "push in" if zoom > 0 else "pull out"),
    ]
    score, label = max(cands)
    return label if score >= 1.0 else "static"


def _pair_motion(g0, g1) -> Optional[dict]:
    """Camera fit + subject on one frame pair. Units: px and px/frame at analysis size."""
    cv2, np = _deps()
    h, w = g0.shape
    flow = cv2.calcOpticalFlowFarneback(g0, g1, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    ys, xs = np.mgrid[GRID_STEP:h - GRID_STEP + 1:GRID_STEP, GRID_STEP:w - GRID_STEP + 1:GRID_STEP]
    xs = xs.ravel().astype(np.float32)
    ys = ys.ravel().astype(np.float32)
    eig = cv2.cornerMinEigenVal(g0.astype(np.float32) / 255.0, TEXTURE_BLOCK)
    e = eig[ys.astype(int), xs.astype(int)]
    keep = ~((e < np.percentile(eig, TEXTURE_PCT)) & (e < TEXTURE_FLOOR))
    if int(keep.sum()) < MIN_GRID_POINTS:
        return None
    xs, ys = xs[keep], ys[keep]
    f = flow[ys.astype(int), xs.astype(int)]                       # (N, 2)
    p = np.stack([xs - w / 2.0, ys - h / 2.0], axis=1).astype(np.float32)  # centred: translation = centre shift
    q = p + f
    m, _inl = cv2.estimateAffinePartial2D(p, q, method=cv2.RANSAC, ransacReprojThreshold=1.0)
    if m is None:
        return None
    s = float(math.hypot(m[0, 0], m[1, 0]))
    pred = p @ m[:, :2].T + m[:, 2] - p                              # camera flow per point
    res = f - pred
    mag = np.hypot(res[:, 0], res[:, 1])
    thr = max(SUBJECT_MIN_PX, SUBJECT_MEDIAN_K * float(np.median(mag)))
    sel = mag > thr
    n_sel = int(sel.sum())
    out = {
        "tx": float(m[0, 2]), "ty": float(m[1, 2]), "zoom": s - 1.0,
        "share": n_sel / len(mag), "n_sel": n_sel,
        "energy": float(np.hypot(f[:, 0], f[:, 1]).mean()),
        "w": w, "h": h,
    }
    if n_sel:
        wgt = mag[sel]
        out["cx"] = float((xs[sel] * wgt).sum() / wgt.sum() / w)
        out["cy"] = float((ys[sel] * wgt).sum() / wgt.sum() / h)
        out["vx"] = float(res[sel, 0].mean())
        out["vy"] = float(res[sel, 1].mean())
    return out


def _coherence(vecs: list) -> float:
    """|mean vector| / mean |vector|: 1 when every pair moves the same way, near 0 for jitter."""
    if not vecs:
        return 0.0
    mx = sum(v[0] for v in vecs) / len(vecs)
    my = sum(v[1] for v in vecs) / len(vecs)
    mean_len = sum(math.hypot(v[0], v[1]) for v in vecs) / len(vecs)
    return math.hypot(mx, my) / mean_len if mean_len > 1e-9 else 0.0


def measure_side(frames: list, fps: float) -> SideMotion:
    """Average the flow pairs of one side. `frames` are BGR/gray arrays at analysis width."""
    cv2, np = _deps()
    side = SideMotion()
    if len(frames) < MIN_PAIRS + 1:
        side.flat = True
        side.note = "too short"
        return side
    grays = [f if f.ndim == 2 else cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
    means = [float(g.mean()) for g in grays]
    side.brightness = float(np.mean(means))
    side.texture = float(np.mean([g.std() for g in grays]))
    # Sides that are not a shot in motion are not judged: a title on black, a flash, a fade.
    if side.brightness < FLAT_BRIGHTNESS or float(np.mean([np.median(g) for g in grays])) < DARK_MEDIAN:
        side.note = "dark or a card"
    elif side.texture < FLAT_TEXTURE:
        side.note = "flat field"
    elif max(means) > FLASH_WHITE or max(abs(b - a) for a, b in zip(means[:-1], means[1:])) > FLASH_STEP:
        side.note = "flash"
    elif abs(means[-1] - means[0]) > FADE_DELTA:
        side.note = "fade"
    if side.note:
        side.flat = True
        return side
    # Repeated frames (24 fps footage conformed to 30 or 60, frame-doubled generations) carry no
    # motion. Left in, they drop out of the averages and the speed comes out too high by the
    # conform ratio; taken out, the remaining pairs span more time, so the rate is scaled down.
    diffs = [float(np.abs(b.astype(np.int16) - a.astype(np.int16)).mean()) for a, b in zip(grays[:-1], grays[1:])]
    live = float(np.percentile(diffs, 80))
    dup_below = min(DUP_DIFF, DUP_REL * live)
    if live > DUP_LIVE and min(diffs) < dup_below:
        uniq = [grays[0]] + [g for g, d in zip(grays[1:], diffs) if d >= dup_below]
        side.dup_ratio = 1.0 - (len(uniq) - 1) / (len(grays) - 1)
        fps = fps * (len(uniq) - 1) / (len(grays) - 1)
        grays = uniq
    pairs = [r for r in (_pair_motion(a, b) for a, b in zip(grays[:-1], grays[1:])) if r]
    if len(pairs) < MIN_PAIRS:
        side.flat = True
        side.note = "no texture"
        return side
    k = fps / pairs[0]["w"]                       # px/frame -> frame widths / s
    side.pairs = len(pairs)
    side.pan_x = float(np.mean([p["tx"] for p in pairs])) * k
    side.pan_y = float(np.mean([p["ty"] for p in pairs])) * k
    side.zoom_per_s = float(np.mean([p["zoom"] for p in pairs])) * fps
    if abs(side.zoom_per_s) > EFFECT_ZOOM:
        side.flat = True
        side.note = "fast zoom or effect"
        return side
    side.cam_coherence = _coherence([(p["tx"], p["ty"]) for p in pairs])
    side.camera_label = camera_label(side.pan_x, side.pan_y, side.zoom_per_s)
    side.energy = float(np.mean([p["energy"] for p in pairs])) * k
    side.share = float(np.mean([p["share"] for p in pairs]))
    wsum = sum(p["n_sel"] for p in pairs)
    if side.share >= SUBJECT_MIN_SHARE and wsum:
        sub = [p for p in pairs if p["n_sel"]]
        side.cx = sum(p["cx"] * p["n_sel"] for p in sub) / wsum
        side.cy = sum(p["cy"] * p["n_sel"] for p in sub) / wsum
        side.vx = sum(p["vx"] * p["n_sel"] for p in sub) / wsum * k
        side.vy = sum(p["vy"] * p["n_sel"] for p in sub) / wsum * k
        side.speed = math.hypot(side.vx, side.vy)
        side.subj_coherence = _coherence([(p["vx"], p["vy"]) for p in sub])
        side.spread = float(math.hypot(np.std([p["cx"] for p in sub]), np.std([p["cy"] for p in sub])))
    side.set_travel()
    return side


# --------------------------------------------------------------------------- #
# Verdict
# --------------------------------------------------------------------------- #


def judge(a: SideMotion, b: SideMotion) -> str:
    if a.flat or b.flat:
        return "flat"
    am, bm = a.moving, b.moving
    if not am and not bm:
        return "still to still"
    if am and not bm:
        return "cut on stop"
    if bm and not am:
        return "cut into motion"
    if a.travel > 0 and b.travel > 0:
        cos = (a.travel_x * b.travel_x + a.travel_y * b.travel_y) / (a.travel * b.travel)
        if cos < COS_SAME and a.kind != b.kind:
            # A camera move against a subject move: world direction and screen direction
            # disagree by construction, so neither a flip nor a turn can be claimed.
            return "mixed motion"
        if cos <= COS_OPPOSITE:
            # A flip is only called on two clear, coherent moves. Slow drift and the flicker
            # of generated footage point every way at once and are not a screen direction.
            strong = min(a.travel, b.travel) >= FLIP_MIN_SPEED and a.steady and b.steady
            return "direction flip" if strong else "weak opposite motion"
        if cos < COS_SAME:
            return "direction turns"
    ratio = b.pace / a.pace if a.pace > 0 else float("inf")
    if not (SPEED_RATIO[0] <= ratio <= SPEED_RATIO[1]):
        return "speed jump"
    return "action continues"


def eye_trace(a: SideMotion, b: SideMotion, size: tuple) -> tuple[Optional[float], Optional[str]]:
    """Distance between the two subject centroids in frame-diagonal units."""
    if a.cx is None or b.cx is None:
        return None, None
    w, h = size
    d = math.hypot((a.cx - b.cx) * w, (a.cy - b.cy) * h) / math.hypot(w, h)
    label = "eye stays" if d < EYE_STAYS else ("eye moves" if d <= EYE_JUMPS else "eye jumps")
    if label == "eye jumps":
        # Only two real subjects make an eye jump: big enough, holding their place across
        # the window, and not a sliver at the frame edge (parallax, smoke, a title fading in).
        def target(s: SideMotion) -> bool:
            return (s.share >= EYE_MIN_SHARE and s.spread <= SUBJECT_MAX_SPREAD
                    and EYE_EDGE <= s.cx <= 1 - EYE_EDGE and EYE_EDGE <= s.cy <= 1 - EYE_EDGE)
        if not (target(a) and target(b)):
            label = "eye unsure"
    return d, label


def build_cut(index: int, t: float, a: SideMotion, b: SideMotion, size: tuple) -> CutMotion:
    d, lab = eye_trace(a, b, size)
    return CutMotion(index, t, a, b, judge(a, b), d, lab, size)


# --------------------------------------------------------------------------- #
# Analysis of a file
# --------------------------------------------------------------------------- #


def group_bursts(cuts: list[float], gap: float = BURST_GAP) -> list[tuple[float, float, int]]:
    """Scene-detect hits closer than `gap` are one flash or effect (see core.highlights)."""
    from core.highlights import group_bursts as _g
    return _g(cuts, gap)


def analyze(path: str, cuts: Optional[list[float]] = None, win: float = DEF_WIN,
            _keep_frames: Optional[list] = None) -> list[CutMotion]:
    """Measure motion on both sides of every cut. `cuts` in seconds, scene-detected if None."""
    if cuts is None:
        cuts = cuts_from_media(path)
    cap, fps, _n = _open(path)
    nwin = max(MIN_PAIRS + 1, round(win * fps))
    groups = group_bursts(cuts)
    out: list[CutMotion] = []
    vfr = is_vfr(path)
    try:
        for i, (t0, t1, n) in enumerate(groups, 1):
            if vfr:
                # Frame index is not time * fps here: read by timestamp, one side at a time.
                lo = groups[i - 2][1] if i > 1 else 0.0
                hi = groups[i][0] if i < len(groups) else float("inf")
                sa = _read_span(cap, max(lo, t0 - win - 0.06), t0 - 1e-3)
                sb = _read_span(cap, t1 - 1e-3, min(hi, t1 + win + 0.06))
                size = (sa[0][1].shape[1], sa[0][1].shape[0]) if sa else (ANALYSIS_WIDTH, 180)
                cm = build_cut(i, t0, measure_side([f for _, f in sa[:-1]], _side_fps(sa, fps)),
                               measure_side([f for _, f in sb[1:]], _side_fps(sb, fps)), size)
                if n > 1:
                    cm.burst, cm.t_end = n, t1
                    cm.verdict, cm.eye_distance, cm.eye_label = "flash or effect", None, None
                out.append(cm)
                if _keep_frames is not None:
                    _keep_frames.append((sa[-1][1] if sa else None, sb[0][1] if sb else None))
                continue
            idx0, idx1 = int(round(t0 * fps)), int(round(t1 * fps))
            # A window never reaches into the neighbouring shot: a 0.3 s shot is measured on
            # its own frames or not at all (flow across a second cut is noise, not motion).
            a_start = idx0 - 1 - nwin
            if i > 1:
                a_start = max(a_start, int(round(groups[i - 2][1] * fps)) + 1)
            b_count = nwin + 1
            if i < len(groups):
                b_count = min(b_count, int(round(groups[i][0] * fps)) - 1 - idx1)
            a_fr = _read_range(cap, a_start, idx0 - a_start) if idx0 > a_start else []
            b_fr = _read_range(cap, idx1, b_count) if b_count > 0 else []
            # the frame next to the cut on each side is for the sheet, not for the numbers
            size = (a_fr[0].shape[1], a_fr[0].shape[0]) if a_fr else (ANALYSIS_WIDTH, 180)
            cm = build_cut(i, t0, measure_side(a_fr[:-1], fps), measure_side(b_fr[1:], fps), size)
            if n > 1:
                cm.burst, cm.t_end = n, t1
                cm.verdict, cm.eye_distance, cm.eye_label = "flash or effect", None, None
            out.append(cm)
            if _keep_frames is not None:
                _keep_frames.append((a_fr[-1] if a_fr else None, b_fr[0] if b_fr else None))
    finally:
        cap.release()
    return out


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #


def travel_glyph(tx: float, ty: float) -> str:
    """8-sector arrow for a travel vector (image coordinates, y down), or "still"."""
    if math.hypot(tx, ty) < MOVING_SPEED * 0.5:
        return "still"
    ang = math.degrees(math.atan2(-ty, tx)) % 360
    return "→↗↑↖←↙↓↘"[int((ang + 22.5) // 45) % 8]


def _side_text(s: SideMotion) -> str:
    if s.flat:
        return f"flat ({s.note})" if s.note else "flat"
    cam = s.camera_label
    if cam != "static":
        if cam in ("push in", "pull out"):
            cam = f"{cam} {abs(s.zoom_per_s):.2f}/s"
        else:
            mag = abs(s.pan_x) if cam.startswith("pan") else abs(s.pan_y)
            cam = f"{cam} {mag:.2f} fw/s"
    subj = f"subj {s.speed:.2f} fw/s ({s.cx:.2f},{s.cy:.2f})" if s.cx is not None else "no subj"
    return f"cam {cam}, {subj}, travel {travel_glyph(s.travel_x, s.travel_y)}"


def format_report(cuts: list[CutMotion]) -> str:
    lines = []
    for c in cuts:
        eye = f" | {c.eye_label} {c.eye_distance:.2f}" if c.eye_label else ""
        burst = f" ({c.burst} hits to {c.t_end:.2f}s)" if c.burst > 1 else ""
        lines.append(
            f"cut {c.index} @ {c.t:.2f}s  A: {_side_text(c.a)} -> B: {_side_text(c.b)}"
            f"  | {c.verdict}{burst}{eye}"
        )
    if not lines:
        return "no cuts"
    flagged = [c for c in cuts if c.flagged]
    lines.append(f"flagged: {len(flagged)} of {len(cuts)}"
                 + ("  @ " + ", ".join(f"{c.t:.2f}s" for c in flagged) if flagged else ""))
    return "\n".join(lines)


def to_json(cuts: list[CutMotion]) -> str:
    return json.dumps([asdict(c) for c in cuts], indent=2)


# --------------------------------------------------------------------------- #
# Contact sheet
# --------------------------------------------------------------------------- #


def _draw_side(img, s: SideMotion) -> None:
    cv2, _ = _deps()
    h, w = img.shape[:2]
    if s.flat:
        cv2.putText(img, f"flat: {s.note}" if s.note else "flat", (6, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1, cv2.LINE_AA)
        return
    cv2.putText(img, s.camera_label, (6, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 160, 0), 1, cv2.LINE_AA)
    start = (int(s.cx * w), int(s.cy * h)) if s.cx is not None else (w // 2, h // 2)
    if s.cx is not None:
        cv2.circle(img, start, 10, (0, 0, 255), 2)
    ax, ay = s.travel_x * w * ARROW_PX_PER_FW, s.travel_y * w * ARROW_PX_PER_FW
    if math.hypot(ax, ay) > 2:
        cv2.arrowedLine(img, start, (int(start[0] + ax), int(start[1] + ay)), (0, 0, 255), 2, tipLength=0.3)


def contact_sheet(cuts: list[CutMotion], frames: list, out_path: str) -> str:
    """One row per cut: last frame of A | first frame of B, annotated, verdict above."""
    cv2, np = _deps()
    rows = []
    for c, (fa, fb) in zip(cuts, frames):
        if fa is None or fb is None:
            continue
        fa, fb = fa.copy(), fb.copy()
        _draw_side(fa, c.a)
        _draw_side(fb, c.b)
        h = fa.shape[0]
        if fb.shape[:2] != fa.shape[:2]:
            fb = cv2.resize(fb, (fa.shape[1], h))
        bar = np.zeros((22, fa.shape[1] * 2 + 4, 3), np.uint8)
        eye = f" | {c.eye_label} {c.eye_distance:.2f}" if c.eye_label else ""
        cv2.putText(bar, f"cut {c.index} @ {c.t:.2f}s  {c.verdict}{eye}", (4, 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        row = np.hstack([fa, np.full((h, 4, 3), 255, np.uint8), fb])
        rows.append(np.vstack([bar, row]))
    if not rows:
        raise RuntimeError("no frames for the sheet")
    cv2.imwrite(out_path, np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 88])
    return out_path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="core.motion", description="motion at cuts (optical flow)")
    ap.add_argument("video")
    ap.add_argument("--cuts", help="comma-separated cut times in seconds (default: scene detect)")
    ap.add_argument("--threshold", type=float, default=0.3, help="scene-detect threshold")
    ap.add_argument("--win", type=float, default=DEF_WIN, help="seconds measured each side of a cut")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--sheet", help="write a contact sheet jpg here")
    a = ap.parse_args(argv)
    # --cuts "" is an explicit empty list (the caller already looked and found a single shot)
    cuts = ([float(x) for x in a.cuts.split(",") if x.strip()] if a.cuts is not None
            else cuts_from_media(a.video, a.threshold))
    keep: list = []
    res = analyze(a.video, cuts, a.win, _keep_frames=keep if a.sheet else None)
    print(to_json(res) if a.json else format_report(res))
    if a.sheet:
        # A single shot has nothing to draw: say so instead of crashing the caller (0.8.10).
        if any(fa is not None and fb is not None for fa, fb in keep):
            print("sheet:", contact_sheet(res, keep, a.sheet), file=sys.stderr)
        else:
            print("sheet: skipped, no cuts to draw", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
