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
- Travel direction used for the screen-direction rule: the subject's vx when
  a subject moves, otherwise the camera's physical direction (-pan_x).

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

FLAT_BRIGHTNESS = 12.0        # mean gray level (0-255) below this = black
FLAT_TEXTURE = 3.0            # mean per-frame gray std below this = flat field

SUBJECT_MIN_PX = 0.5          # residual floor, px at analysis width
SUBJECT_MEDIAN_K = 3.0        # ... or this many times the median residual
SUBJECT_MIN_SHARE = 0.02      # below this share no centroid is reported

STATIC_PAN = 0.03             # fw/s: below this a pan / tilt is "static"
STATIC_ZOOM = 0.08            # 1/s: below this a push / pull is "static"
MOVING_SPEED = 0.04           # fw/s: a side whose motion is below is "still"
H_DEADBAND = 0.02             # fw/s: horizontal travel below this has no sign
SPEED_RATIO = (0.5, 2.0)      # speed B / speed A inside = same pace

EYE_STAYS = 0.15              # frame-diagonal units
EYE_JUMPS = 0.35

ARROW_PX_PER_FW = 1.5         # sheet arrow length: px per (fw/s * frame width)


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #


@dataclass
class SideMotion:
    flat: bool = False
    pairs: int = 0
    pan_x: float = 0.0
    pan_y: float = 0.0
    zoom_per_s: float = 0.0
    camera_label: str = "static"
    share: float = 0.0
    cx: Optional[float] = None
    cy: Optional[float] = None
    vx: float = 0.0
    vy: float = 0.0
    speed: float = 0.0
    energy: float = 0.0
    brightness: float = 0.0
    texture: float = 0.0

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
    def travel_x(self) -> float:
        """Signed horizontal travel (right positive); see module docstring."""
        if self.subject_moving:
            return self.vx
        if self.camera_label in ("pan left", "pan right"):
            return -self.pan_x
        return 0.0

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


def cuts_from_media(path: str, threshold: float = 0.3) -> list[float]:
    """Scene cuts (ffmpeg select), minus any within 0.05 s of start or end."""
    from core.highlights import scene_cuts

    cap, fps, n = _open(path)
    dur = n / fps if n else 0.0
    cap.release()
    cuts = scene_cuts(path, threshold=threshold)
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


def measure_side(frames: list, fps: float) -> SideMotion:
    """Average the flow pairs of one side. `frames` are BGR/gray arrays at analysis width."""
    cv2, np = _deps()
    side = SideMotion()
    if len(frames) < MIN_PAIRS + 1:
        side.flat = True
        return side
    grays = [f if f.ndim == 2 else cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
    side.brightness = float(np.mean([g.mean() for g in grays]))
    side.texture = float(np.mean([g.std() for g in grays]))
    if side.brightness < FLAT_BRIGHTNESS or side.texture < FLAT_TEXTURE:
        side.flat = True
        return side
    pairs = [r for r in (_pair_motion(a, b) for a, b in zip(grays[:-1], grays[1:])) if r]
    if len(pairs) < MIN_PAIRS:
        side.flat = True
        return side
    k = fps / pairs[0]["w"]                       # px/frame -> frame widths / s
    side.pairs = len(pairs)
    side.pan_x = float(np.mean([p["tx"] for p in pairs])) * k
    side.pan_y = float(np.mean([p["ty"] for p in pairs])) * k
    side.zoom_per_s = float(np.mean([p["zoom"] for p in pairs])) * fps
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
    return side


# --------------------------------------------------------------------------- #
# Verdict
# --------------------------------------------------------------------------- #


def _sign(v: float) -> int:
    return 0 if abs(v) < H_DEADBAND else (1 if v > 0 else -1)


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
    sa, sb = _sign(a.travel_x), _sign(b.travel_x)
    if sa and sb and sa != sb:
        return "direction flip"
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
    return d, label


def build_cut(index: int, t: float, a: SideMotion, b: SideMotion, size: tuple) -> CutMotion:
    d, lab = eye_trace(a, b, size)
    return CutMotion(index, t, a, b, judge(a, b), d, lab, size)


# --------------------------------------------------------------------------- #
# Analysis of a file
# --------------------------------------------------------------------------- #


def analyze(path: str, cuts: Optional[list[float]] = None, win: float = DEF_WIN,
            _keep_frames: Optional[list] = None) -> list[CutMotion]:
    """Measure motion on both sides of every cut. `cuts` in seconds, scene-detected if None."""
    if cuts is None:
        cuts = cuts_from_media(path)
    cap, fps, _n = _open(path)
    nwin = max(MIN_PAIRS + 1, round(win * fps))
    out: list[CutMotion] = []
    try:
        for i, t in enumerate(cuts, 1):
            idx = int(round(t * fps))
            a_fr = _read_range(cap, idx - 1 - nwin, nwin + 1)   # last one = frame before the cut
            b_fr = _read_range(cap, idx, nwin + 1)              # first one = frame after the cut
            # the frame next to the cut on each side is for the sheet, not for the numbers
            size = (a_fr[0].shape[1], a_fr[0].shape[0]) if a_fr else (ANALYSIS_WIDTH, 180)
            cm = build_cut(i, t, measure_side(a_fr[:-1], fps), measure_side(b_fr[1:], fps), size)
            out.append(cm)
            if _keep_frames is not None:
                _keep_frames.append((a_fr[-1] if a_fr else None, b_fr[0] if b_fr else None))
    finally:
        cap.release()
    return out


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #


def _side_text(s: SideMotion) -> str:
    if s.flat:
        return "flat"
    cam = s.camera_label
    if cam != "static":
        if cam in ("push in", "pull out"):
            cam = f"{cam} {abs(s.zoom_per_s):.2f}/s"
        else:
            mag = abs(s.pan_x) if cam.startswith("pan") else abs(s.pan_y)
            cam = f"{cam} {mag:.2f} fw/s"
    subj = f"({s.cx:.2f},{s.cy:.2f})" if s.cx is not None else "none"
    return f"{cam}, subject {subj}"


def format_report(cuts: list[CutMotion]) -> str:
    lines = []
    for c in cuts:
        eye = f" | {c.eye_label} {c.eye_distance:.2f}" if c.eye_label else ""
        lines.append(
            f"cut {c.index} @ {c.t:.2f}s  A: {_side_text(c.a)} -> B: {_side_text(c.b)}"
            f"  | {c.verdict}{eye}"
        )
    return "\n".join(lines) if lines else "no cuts"


def to_json(cuts: list[CutMotion]) -> str:
    return json.dumps([asdict(c) for c in cuts], indent=2)


# --------------------------------------------------------------------------- #
# Contact sheet
# --------------------------------------------------------------------------- #


def _draw_side(img, s: SideMotion) -> None:
    cv2, _ = _deps()
    h, w = img.shape[:2]
    if s.flat:
        cv2.putText(img, "flat", (6, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1, cv2.LINE_AA)
        return
    cv2.putText(img, s.camera_label, (6, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 160, 0), 1, cv2.LINE_AA)
    if s.camera_label.startswith(("pan", "tilt")):
        # camera arrow: the camera's physical direction (opposite to the content)
        ax, ay = -s.pan_x * w * ARROW_PX_PER_FW, -s.pan_y * w * ARROW_PX_PER_FW
        c = (w // 2, h // 2)
        cv2.arrowedLine(img, c, (int(c[0] + ax), int(c[1] + ay)), (255, 160, 0), 2, tipLength=0.3)
    if s.cx is not None:
        p = (int(s.cx * w), int(s.cy * h))
        cv2.circle(img, p, 10, (0, 0, 255), 2)
        sx, sy = s.vx * w * ARROW_PX_PER_FW, s.vy * w * ARROW_PX_PER_FW
        if math.hypot(sx, sy) > 2:
            cv2.arrowedLine(img, p, (int(p[0] + sx), int(p[1] + sy)), (0, 0, 255), 2, tipLength=0.3)


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
    cuts = [float(x) for x in a.cuts.split(",") if x.strip()] if a.cuts else cuts_from_media(a.video, a.threshold)
    keep: list = []
    res = analyze(a.video, cuts, a.win, _keep_frames=keep if a.sheet else None)
    print(to_json(res) if a.json else format_report(res))
    if a.sheet:
        print("sheet:", contact_sheet(res, keep, a.sheet), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
