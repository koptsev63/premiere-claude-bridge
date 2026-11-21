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
    travel_glyph,
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


def tilt_frames(px_per_frame: int, seed: int = 4) -> list:
    """Content moves down `px_per_frame` each frame (camera tilts up)."""
    big = _texture(W, H + 100, seed)
    return [np.ascontiguousarray(big[50 - i * px_per_frame + 0:50 - i * px_per_frame + H, :]) for i in range(N)]


def tracking_frames(px_per_frame: int, seed: int = 6) -> list:
    """Background content moves LEFT (camera pans right) while a square stays fixed on screen."""
    bgs = pan_frames(-px_per_frame, seed)
    sq = np.clip(_texture(40, 40, 8).astype(int) + 60, 0, 255).astype(np.uint8)
    out = []
    for f in bgs:
        f = f.copy()
        f[70:110, 140:180] = sq
        out.append(f)
    return out


def flat_noise_frames(seed: int = 9) -> list:
    """Left half textured and still, right half flat grey with fresh noise each frame."""
    rng = np.random.default_rng(seed)
    base = _texture(W, H, 11)
    out = []
    for _ in range(N):
        f = base.copy()
        f[:, 160:] = np.clip(128 + rng.normal(0, 1.5, (H, 160)), 0, 255).astype(np.uint8)
        out.append(f)
    return out


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


def test_travel() -> None:
    check("pan right vs tilt up: direction turns",
          judge(side(pan_frames(-3)), side(tilt_frames(3))) == "direction turns",
          judge(side(pan_frames(-3)), side(tilt_frames(3))))
    t = side(tilt_frames(3))
    check("tilt up: travel points up", t.travel_y < -0.05 and abs(t.travel_x) < 0.05,
          f"({t.travel_x:.2f},{t.travel_y:.2f})")
    k = side(tracking_frames(3))
    check("tracking pan: camera labelled pan right", k.camera_label == "pan right", k.camera_label)
    check("tracking pan: subject fixed on screen is found", k.cx is not None and abs(k.cx - 0.5) < 0.15,
          f"cx={k.cx}")
    check("tracking pan: travel has the camera direction (right)", k.travel_x > 0.05,
          f"({k.travel_x:.2f},{k.travel_y:.2f})")
    check("tracking pan vs pan right continues",
          judge(k, side(pan_frames(-3, seed=5))) == "action continues")
    check("glyphs", travel_glyph(0.3, 0) == "→" and travel_glyph(0, -0.3) == "↑"
          and travel_glyph(-0.2, 0.2) == "↙" and travel_glyph(0, 0) == "still")


def test_texture_mask() -> None:
    s = side(flat_noise_frames())
    check("noisy flat region does not become the subject",
          s.cx is None or s.cx < 0.5, f"share={s.share:.3f} cx={s.cx}")
    check("still shot with a noisy flat half is static", s.camera_label == "static" and not s.moving,
          f"{s.camera_label} speed={s.pace:.3f}")


def test_eye_trace() -> None:
    a = SideMotion(cx=0.5, cy=0.5)
    a.share = 0.1
    near, mid = SideMotion(cx=0.55, cy=0.5), SideMotion(cx=0.75, cy=0.5)
    a2, far = SideMotion(cx=0.85, cy=0.2, share=0.1), SideMotion(cx=0.15, cy=0.85, share=0.1)
    size = (320, 180)
    check("eye stays", eye_trace(a, near, size)[1] == "eye stays")
    check("eye moves", eye_trace(a, mid, size)[1] == "eye moves", str(eye_trace(a, mid, size)))
    check("eye jumps", eye_trace(a2, far, size)[1] == "eye jumps", str(eye_trace(a2, far, size)))
    check("no centroid, no eye trace", eye_trace(a, SideMotion(), size) == (None, None))
    # 0.8.10: a far blob is an eye jump only between two real subjects
    small = SideMotion(cx=0.15, cy=0.85, share=0.03)
    check("far but tiny blob (a title fading in): unsure", eye_trace(a2, small, size)[1] == "eye unsure")
    edge = SideMotion(cx=0.03, cy=0.85, share=0.1)
    check("far blob on the frame edge (parallax): unsure", eye_trace(a2, edge, size)[1] == "eye unsure")
    loose = SideMotion(cx=0.15, cy=0.85, share=0.1, spread=0.3)
    check("far blob that wanders (smoke): unsure", eye_trace(a2, loose, size)[1] == "eye unsure")


def flicker_frames(seed: int = 21) -> list:
    """Generated-footage shimmer: a patch that twitches left and right, going nowhere."""
    bg = _texture(W, H, seed)
    sq = np.clip(_texture(60, 60, 22).astype(int) + 60, 0, 255).astype(np.uint8)
    out = []
    for i in range(N):
        f = bg.copy()
        x = 130 + (3 if i % 2 else -3)
        f[60:120, x:x + 60] = sq
        out.append(f)
    return out


def test_not_judged() -> None:
    """0.8.10, from an eyes-on audit of 14 flags (0 real): what must NOT be flagged."""
    right = side(subject_frames(4))
    fl = side(flicker_frames())
    check("flicker has low coherence", fl.cx is None or fl.subj_coherence < 0.6 or not fl.moving,
          f"coh={fl.subj_coherence:.2f} speed={fl.speed:.2f}")
    check("flicker against a real move is not a flip", judge(right, fl) != "direction flip", judge(right, fl))
    slow_r = SideMotion(cx=0.5, cy=0.5, share=0.1, vx=0.06, speed=0.06)
    slow_l = SideMotion(cx=0.5, cy=0.5, share=0.1, vx=-0.06, speed=0.06)
    slow_r.set_travel(); slow_l.set_travel()
    check("slow drift both ways: weak, not a flip", judge(slow_r, slow_l) == "weak opposite motion", judge(slow_r, slow_l))
    check("camera move against subject move: mixed, not a flip",
          judge(side(pan_frames(3)), side(subject_frames(4))) == "mixed motion",
          judge(side(pan_frames(3)), side(subject_frames(4))))
    card = [np.zeros((H, W), np.uint8) for _ in range(N)]
    for f in card:
        f[80:100, 100:220] = 255                                   # white title on black
    c = side(card)
    check("title on black is a card, not judged", c.flat and c.note == "dark or a card", c.note)
    fade = [np.clip(f.astype(int) * (1 - i / N), 0, 255).astype(np.uint8) for i, f in enumerate(subject_frames(4))]
    check("fade out is not judged", side(fade).flat and side(fade).note in ("fade", "dark or a card"), side(fade).note)
    flash = subject_frames(4)
    flash[4] = np.full((H, W), 250, np.uint8)
    check("flash frame is not judged", side(flash).flat and side(flash).note == "flash", side(flash).note)
    check("too short side says so", side(subject_frames(4)[:2]).note == "too short")


def test_duplicated_frames() -> None:
    """24 fps footage on a 60 fps timeline: repeated frames must not inflate the speed."""
    base = side(subject_frames(4))
    doubled = measure_side([f for f in subject_frames(4) for _ in (0, 1)], FPS * 2)
    check("frame-doubled side has the same speed", abs(doubled.speed - base.speed) < 0.2 * base.speed,
          f"{doubled.speed:.3f} vs {base.speed:.3f}")
    check("frame-doubled side reports the repeats", 0.4 < doubled.dup_ratio < 0.6, f"{doubled.dup_ratio:.2f}")
    check("clean side reports none", base.dup_ratio == 0.0)


def test_bursts() -> None:
    from core.motion import group_bursts
    g = group_bursts([5.0, 12.0, 12.04, 12.13, 12.21, 20.0])
    check("flash burst is one event", g == [(5.0, 5.0, 1), (12.0, 12.21, 4), (20.0, 20.0, 1)], str(g))
    check("normal cuts stay apart", group_bursts([1.0, 1.5, 2.0]) == [(1.0, 1.0, 1), (1.5, 1.5, 1), (2.0, 2.0, 1)])


def test_report_and_sheet() -> None:
    import os
    import tempfile
    cm = build_cut(1, 3.2, side(pan_frames(-3)), side(subject_frames(4)), (W, H))
    line = format_report([cm])
    check("report line", line.startswith("cut 1 @ 3.20s") and "pan right" in line and cm.verdict in line, line)
    check("report ends with the flag count", line.splitlines()[-1].startswith("flagged: "), line.splitlines()[-1])
    with tempfile.TemporaryDirectory() as d:
        import cv2
        fr = cv2.cvtColor(subject_frames(4)[0], cv2.COLOR_GRAY2BGR)
        out = contact_sheet([cm], [(fr, fr)], os.path.join(d, "s.jpg"))
        img = cv2.imread(out)
        check("sheet written", img is not None and img.shape[1] == 2 * W + 4, str(None if img is None else img.shape))


def test_cli_single_shot() -> None:
    """A file with no cuts and --sheet must exit 0 and write no sheet (it used to raise)."""
    import contextlib
    import io
    import os
    import tempfile

    import cv2

    from core.motion import main as motion_main
    with tempfile.TemporaryDirectory() as d:
        vid, sheet = os.path.join(d, "one.mp4"), os.path.join(d, "s.jpg")
        vw = cv2.VideoWriter(vid, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
        for f in pan_frames(-2) + pan_frames(-2):
            vw.write(cv2.cvtColor(f, cv2.COLOR_GRAY2BGR))
        vw.release()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = motion_main([vid, "--sheet", sheet])
        check("single shot exits 0", rc == 0, str(rc))
        check("single shot says no cuts", "no cuts" in out.getvalue(), out.getvalue())
        check("single shot writes no sheet", not os.path.exists(sheet) and "skipped" in err.getvalue(), err.getvalue())


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
    print("--- not judged ---")
    test_not_judged()
    test_duplicated_frames()
    test_bursts()
    print("--- travel + texture ---")
    test_travel()
    test_texture_mask()
    print("--- report ---")
    test_report_and_sheet()
    test_cli_single_shot()
    print(f"\n{_p} passed, {_f} failed")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
