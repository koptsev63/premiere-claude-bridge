"""Tests for core.motion on synthetic frames (no media files).

Run:  python -m core.tests.test_motion
"""

from __future__ import annotations

import sys

import numpy as np

from core.motion import (
    SideMotion,
    build_cut,
    camera_label,
    contact_sheet,
    eye_trace,
    format_report,
    judge,
    measure_side,
)

_p = _f = 0
FPS = 25.0
W, H = 320, 180
N = 9


def check(name: str, cond: bool, detail: str = "") -> None:
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


def _texture(w: int, h: int, seed: int) -> np.ndarray:
    import cv2
    rng = np.random.default_rng(seed)
    t = rng.random((h, w)).astype(np.float32)
    t = cv2.GaussianBlur(t, (0, 0), 2.0)
    t = (t - t.min()) / (t.max() - t.min())
    return (40 + 180 * t).astype(np.uint8)


def pan_frames(px_per_frame: int, seed: int = 1) -> list:
    """Textured background whose content moves `px_per_frame` right each frame."""
    big = _texture(W + 200, H, seed)
    out = []
    for i in range(N):
        off = 100 - i * px_per_frame          # content moves right when the window moves left
        out.append(np.ascontiguousarray(big[:, off:off + W]))
    return out


def subject_frames(px_per_frame: int, x0: int = 120, seed: int = 2) -> list:
    """Static textured background + a bright textured square moving right (positive)."""
    bg = _texture(W, H, seed)
    sq = _texture(40, 40, 7)
    sq = np.clip(sq.astype(int) + 60, 0, 255).astype(np.uint8)
    out = []
    for i in range(N):
        f = bg.copy()
        x = x0 + i * px_per_frame
        f[70:110, x:x + 40] = sq
        out.append(f)
    return out


def still_frames(seed: int = 3) -> list:
    bg = _texture(W, H, seed)
    return [bg.copy() for _ in range(N)]


def side(frames: list) -> SideMotion:
    return measure_side(frames, FPS)


def test_pan_sign() -> None:
    s = side(pan_frames(3))                       # content moves right = camera pans left
    check("content right: pan_x > 0", s.pan_x > 0.05, f"pan_x={s.pan_x:.3f}")
    check("content right: label pan left", s.camera_label == "pan left", s.camera_label)
    check("pan magnitude ~ 3px*25/320", abs(s.pan_x - 3 * 25 / 320) < 0.05, f"{s.pan_x:.3f}")
    s2 = side(pan_frames(-3))
    check("content left: label pan right", s2.camera_label == "pan right", s2.camera_label)
    check("pan has no subject", s2.cx is None, f"share={s2.share:.3f}")


def test_static() -> None:
    s = side(still_frames())
    check("locked-off shot is static", s.camera_label == "static" and s.cx is None and not s.moving,
          f"{s.camera_label} share={s.share:.3f}")
    check("camera_label thresholds", camera_label(0.0, 0.0, 0.0) == "static"
          and camera_label(0.0, 0.0, 0.5) == "push in" and camera_label(0.0, 0.2, 0.0) == "tilt up")


def test_subject() -> None:
    s = side(subject_frames(4))
    check("subject found", s.cx is not None, f"share={s.share:.3f}")
    if s.cx is not None:
        check("centroid near square", abs(s.cx - 0.5) < 0.15 and abs(s.cy - 0.5) < 0.15,
              f"({s.cx:.2f},{s.cy:.2f})")
        check("subject moves right", s.vx > 0.05, f"vx={s.vx:.3f}")
    check("camera static with subject", s.camera_label == "static", s.camera_label)


def test_flat() -> None:
    black = [np.zeros((H, W), np.uint8) for _ in range(N)]
    check("black side is flat", side(black).flat)
    grey = [np.full((H, W), 128, np.uint8) for _ in range(N)]
    check("flat grey side is flat", side(grey).flat)
    check("flat verdict", judge(side(black), side(still_frames())) == "flat")


def test_verdicts() -> None:
    right, left = side(subject_frames(4)), side(subject_frames(-4, x0=200))
    still = side(still_frames())
    check("same direction: action continues", judge(right, side(subject_frames(4, x0=60))) == "action continues")
    check("opposite direction: direction flip", judge(right, left) == "direction flip",
          judge(right, left))
    check("moving to still: cut on stop", judge(right, still) == "cut on stop")
    check("still to moving: cut into motion", judge(still, right) == "cut into motion")
    check("still to still", judge(still, side(still_frames(9))) == "still to still")
    check("speed jump", judge(side(subject_frames(2)), side(subject_frames(8))) == "speed jump",
          judge(side(subject_frames(2)), side(subject_frames(8))))
    check("pan follows pan: action continues",
          judge(side(pan_frames(3)), side(pan_frames(3, seed=5))) == "action continues")
    check("pan flip", judge(side(pan_frames(3)), side(pan_frames(-3, seed=5))) == "direction flip")


def test_eye_trace() -> None:
    a = SideMotion(cx=0.5, cy=0.5)
    near, mid, far = SideMotion(cx=0.55, cy=0.5), SideMotion(cx=0.75, cy=0.5), SideMotion(cx=0.05, cy=0.9)
    size = (320, 180)
    check("eye stays", eye_trace(a, near, size)[1] == "eye stays")
    check("eye moves", eye_trace(a, mid, size)[1] == "eye moves", str(eye_trace(a, mid, size)))
    check("eye jumps", eye_trace(a, far, size)[1] == "eye jumps", str(eye_trace(a, far, size)))
    check("no centroid, no eye trace", eye_trace(a, SideMotion(), size) == (None, None))


def test_report_and_sheet() -> None:
    import os
    import tempfile
    cm = build_cut(1, 3.2, side(pan_frames(-3)), side(subject_frames(4)), (W, H))
    line = format_report([cm])
    check("report line", line.startswith("cut 1 @ 3.20s") and "pan right" in line and cm.verdict in line, line)
    with tempfile.TemporaryDirectory() as d:
        import cv2
        fr = cv2.cvtColor(subject_frames(4)[0], cv2.COLOR_GRAY2BGR)
        out = contact_sheet([cm], [(fr, fr)], os.path.join(d, "s.jpg"))
        img = cv2.imread(out)
        check("sheet written", img is not None and img.shape[1] == 2 * W + 4, str(None if img is None else img.shape))


def main() -> int:
    print("--- camera ---")
    test_pan_sign()
    test_static()
    print("--- subject ---")
    test_subject()
    test_flat()
    print("--- verdicts ---")
    test_verdicts()
    test_eye_trace()
    print("--- report ---")
    test_report_and_sheet()
    print(f"\n{_p} passed, {_f} failed")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
