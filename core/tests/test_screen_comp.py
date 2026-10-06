"""Tests for screen replacement.

All footage is synthetic and built in a temp dir: a textured wall with a
dark "screen" on it, filmed by a camera that drifts by a known number of
pixels per frame; the content is half red, half green, so a flipped,
shifted or mis-tracked insert shows up as the wrong colour at a known
pixel. Nothing outside the repo is read.

Skips cleanly without numpy/opencv, without ffmpeg, or without libx264.

Run:  python -m core.tests.test_screen_comp
"""

from __future__ import annotations

import io
import json
import math
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from core import screen_comp as sc
from core.screen_comp import (
    ScreenCompFailure,
    ScreenCompReport,
    ScreenCompUnavailable,
    ScreenSpec,
)

_p = _f = _s = 0

W, H, N = 320, 240, 16
QUAD = [(100.0, 65.0), (219.0, 65.0), (219.0, 154.0), (100.0, 154.0)]
RED, GREEN = (30, 30, 220), (40, 200, 40)          # BGR


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


def skip(why: str) -> None:
    global _s
    _s += 1
    print(f"  SKIP  ({why})")


def drift(k: int) -> tuple[int, int]:
    """How far the camera has moved (px) by frame k."""
    a = 2 * math.pi * k / N
    return round(6 * math.sin(a)), round(5 * (1 - math.cos(a)))


def _wall(np, cv2):
    rng = np.random.default_rng(7)
    small = rng.uniform(40, 200, (33, 42, 3)).astype(np.float32)
    wall = cv2.resize(small, (420, 330), interpolation=cv2.INTER_CUBIC)
    wall = np.clip(wall, 16, 235).astype(np.uint8)
    wall[110:200, 150:270] = 12                     # the dead screen
    return wall


def _write(path, frames, fps="25"):
    wr = sc.FrameWriter(path, frames[0].shape[1], frames[0].shape[0], fps,
                        crf=12, preset="ultrafast")
    for f in frames:
        wr.write(f)
    wr.close()


def _footage(tmp: Path, moving: bool = True):
    import cv2
    import numpy as np
    wall = _wall(np, cv2)
    plate_frames = []
    for k in range(N):
        dx, dy = drift(k) if moving else (0, 0)
        plate_frames.append(np.ascontiguousarray(
            wall[45 + dy:45 + dy + H, 50 + dx:50 + dx + W]))
    plate = tmp / ("plate.mp4" if moving else "static.mp4")
    _write(plate, plate_frames)
    card = np.zeros((54, 96, 3), np.uint8)
    card[:, :48], card[:, 48:] = RED, GREEN
    content = tmp / "content.mp4"
    if not content.exists():
        _write(content, [card] * N)
    return plate, content, plate_frames


def _frames(path) -> list:
    with sc.FrameReader(path, W, H) as rd:
        out = []
        while (f := rd.read()) is not None:
            out.append(f.copy())
    return out


def near(px, colour, tol=14) -> bool:
    return all(abs(int(a) - int(b)) <= tol for a, b in zip(px, colour))


# ---- runs anywhere ---------------------------------------------------- #

def test_missing_deps() -> None:
    print("screen_comp - missing dependencies say what to install")
    with patch.dict(sys.modules, {"cv2": None}):
        msg = ""
        try:
            sc.sampling_maps((10, 10), (10, 10))
        except ScreenCompUnavailable as e:
            msg = str(e)
    check("a clear error, not an ImportError", "opencv-python-headless" in msg,
          msg)


def test_config_and_report() -> None:
    print("screen_comp - config, looks and the verdict logic")
    check("unknown look name is refused", _raises(lambda: sc.look_from("vhs")))
    crt = sc.look_from({"preset": "crt", "glow": 0.1})
    check("a preset can be overridden per key",
          crt.glow == 0.1 and crt.bulge == sc.CRT.bulge)
    check("the preset itself is not mutated", sc.CRT.glow == 0.35)
    check("a typo in a look key is an error, not ignored",
          _raises(lambda: sc.look_from({"glwo": 1})))
    check("no look means a flat panel", sc.look_from(None) == sc.FLAT)
    with tempfile.TemporaryDirectory() as d:
        cfg = Path(d, "screen.json")
        cfg.write_text(json.dumps({
            "quad": [[1, 2], [9, 2], [9, 8], [1, 8]], "mask": "m.png",
            "content_crop": [0, 0, 4, 4], "track": False,
            "look": "glass", "audio": "none"}))
        spec, look = sc.load_config(cfg)
        check("config parses into a spec and a look",
              spec.quad[1] == (9.0, 2.0) and not spec.track
              and look == sc.GLASS, str(spec))
        check("a relative mask resolves next to the config",
              spec.mask == str(Path(d, "m.png")), str(spec.mask))
        cfg.write_text(json.dumps({"quad": [[0, 0]], "audio": "none"}))
        check("a 1-point quad is refused", _raises(lambda: sc.load_config(cfg)))
        cfg.write_text(json.dumps({"track": True}))
        check("no quad and no mask is refused",
              _raises(lambda: sc.load_config(cfg)))
        cfg.write_text(json.dumps({"quad": QUAD, "screeen": 1}))
        check("an unknown config key is refused",
              _raises(lambda: sc.load_config(cfg)))

    def verdict(**kw):
        base = dict(plate_frames=100, out_frames=100, track_failed=0,
                    outside_err=1.1, colour_shift=0.2, inside_change=40.0,
                    tags=("bt709", "bt709", "bt709"))
        base.update(kw)
        return sc.judge(ScreenCompReport(), **base)

    check("a clean composite passes", verdict().ok, str(verdict().failures))
    check("a dropped frame fails", not verdict(out_frames=99).ok)
    check("2 lost frames in 100 are tolerated", verdict(track_failed=2).ok)
    r = verdict(track_failed=5)
    check("5 in 100 are not", any("track" in f for f in r.failures),
          str(r.failures))
    r = verdict(colour_shift=6.0, outside_err=6.0)
    check("a shifted plate fails on both plate checks",
          len(r.failures) == 2 and all("plate" in f for f in r.failures),
          str(r.failures))
    check("an insert that changed nothing fails",
          any("landed" in f for f in verdict(inside_change=0.3).failures))
    check("untagged output fails",
          any("tagged" in f for f in verdict(tags=(None, None, None)).failures))
    r = verdict(outside_err=None, colour_shift=None)
    check("nothing to compare against fails closed",
          not r.ok and "UNVERIFIED" in r.failures[0], str(r.failures))
    check("assert_ok() raises on a failed gate",
          _raises(verdict(out_frames=1).assert_ok))
    check("a report whose gate never ran is not a pass",
          not ScreenCompReport().ok and _raises(ScreenCompReport().assert_ok))
    verdict().assert_ok()
    check("printable report carries the verdict",
          str(verdict()).startswith("screen comp gate: PASS"))


def _raises(fn) -> bool:
    try:
        fn()
    except (ScreenCompFailure, ScreenCompUnavailable):
        return True
    return False


# ---- needs numpy + opencv --------------------------------------------- #

def test_geometry() -> None:
    print("screen_comp - geometry")
    import numpy as np
    mask = np.zeros((40, 60), np.uint8)
    mask[10:30, 20:50] = 255
    q = sc.quad_from_mask(mask)
    check("mask bounding box as corner pixels",
          q == [(20.0, 10.0), (49.0, 10.0), (49.0, 29.0), (20.0, 29.0)], str(q))
    check("buffer size covers the corner pixels", sc.screen_size(q) == (30, 20),
          str(sc.screen_size(q)))
    check("an empty mask is an error",
          _raises(lambda: sc.quad_from_mask(np.zeros((4, 4), np.uint8))))
    hom = sc.quad_homography(q, (30, 20))
    corner = hom @ np.array([29.0, 19.0, 1.0])
    check("buffer corner lands on the quad corner",
          np.allclose(corner[:2] / corner[2], (49, 29), atol=1e-6), str(corner))

    mx, my, u, v = sc.sampling_maps((120, 90), (96, 54), "fill", 0.0)
    check("fill: screen centre reads the content centre",
          abs(mx[45, 60] - 48) < 1 and abs(my[45, 60] - 27) < 1,
          f"{mx[45, 60]} {my[45, 60]}")
    check("fill: full content height is used, sides are cropped",
          my[0, 0] < 0.5 and my[-1, 0] > 52.5 and mx[0, 0] > 10,
          f"{my[0, 0]} {my[-1, 0]} {mx[0, 0]}")
    fx, fy, _, _ = sc.sampling_maps((120, 90), (96, 54), "fit", 0.0)
    check("fit: full width is shown, bars above and below",
          fx[0, 0] < 0.5 and fy[0, 0] < -5, f"{fx[0, 0]} {fy[0, 0]}")
    tx, ty, _, _ = sc.sampling_maps((90, 160), (96, 54), "fill", 0.0)
    check("fill on a tall screen crops the sides harder, still full height",
          ty[0, 0] < 0.5 and tx[0, 0] > 30, f"{ty[0, 0]} {tx[0, 0]}")
    bx, _, _, _ = sc.sampling_maps((120, 90), (96, 54), "fill", 0.07)
    check("bulge magnifies the centre",
          abs(bx[45, 80] - 48) < abs(mx[45, 80] - 48),
          f"{bx[45, 80]} vs {mx[45, 80]}")
    check("bulge leaves the edge midpoints where they were",
          abs(bx[45, 0] - mx[45, 0]) < 0.3, f"{bx[45, 0]} vs {mx[45, 0]}")
    check("and pushes the corners past the content",
          bx[0, 0] < mx[0, 0], f"{bx[0, 0]} vs {mx[0, 0]}")
    check("a bad fit mode is refused",
          _raises(lambda: sc.sampling_maps((4, 4), (4, 4), "stretch")))
    d = sc.edge_distance(np.ones((9, 9), bool))
    check("edge distance: 1 at the border, largest in the middle",
          d[0, 0] == 1 and d[4, 4] == d.max() and d[4, 4] >= 5, str(d[4]))


def test_colour_io(tmp: Path) -> bool:
    print("screen_comp - colour survives the round trip")
    import numpy as np
    frame = np.zeros((H, W, 3), np.uint8)
    frame[:, :W // 2], frame[:, W // 2:] = RED, (128, 128, 128)
    path = tmp / "io.mp4"
    try:
        _write(path, [frame] * 4)
    except ScreenCompFailure as e:
        skip(f"this ffmpeg cannot encode libx264: {e}")
        return False
    back = _frames(path)[1]
    check("saturated red comes back within 3 levels",
          near(back[100, 60], RED, 3), str(back[100, 60]))
    check("mid grey comes back within 2 levels",
          near(back[100, 250], (128, 128, 128), 2), str(back[100, 250]))
    info = sc.probe(path)
    check("the file is tagged bt709",
          info["colour"] == ("bt709", "bt709", "bt709"), str(info["colour"]))
    check("probe reports decoded geometry", (info["w"], info["h"]) == (W, H))
    with sc.FrameReader(path, W, H, matrix="bt601") as rd:
        wrong = rd.read()
    off = max(abs(int(a) - b) for a, b in zip(wrong[100, 60], RED))
    check("the wrong matrix is a visible error - why it is stated explicitly",
          off > 8, f"only {off} levels off")
    check("frame count is read without decoding", sc.count_frames(path) == 4)
    return True


def test_track_and_composite(tmp: Path) -> None:
    print("screen_comp - the insert rides a drifting camera")
    import numpy as np
    plate, content, plate_frames = _footage(tmp)
    spec = ScreenSpec(quad=QUAD, audio="none")
    scr = sc._prepare(spec, sc.FLAT, W, H)
    track = sc.track_camera(plate, scr.full_mask, scale=1.0)
    worst = max(max(abs(track.at(k)[0, 2] + drift(k)[0]),
                    abs(track.at(k)[1, 2] + drift(k)[1])) for k in range(N))
    check("one transform per frame", len(track) == N, str(len(track)))
    check("the known drift is recovered to within 0.6 px", worst < 0.6,
          f"worst error {worst:.2f} px")
    check("no ECC failures, correlation high",
          not track.failed and track.min_cc > 0.9,
          f"{track.failed} {track.min_cc}")

    out = tmp / "comp.mp4"
    rep = sc.composite(plate, content, out, spec, "flat", track=track,
                       crf=12, preset="ultrafast")
    check("the gate passes", rep.ok, str(rep))
    rep.assert_ok()
    got = _frames(out)
    check("every plate frame was written", len(got) == N == rep.frames)
    k = 4                                   # the frame of largest sideways drift
    dx, dy = drift(k)
    check("left of the screen is red, right is green - not mirrored",
          near(got[k][110 - dy, 130 - dx], RED)
          and near(got[k][110 - dy, 190 - dx], GREEN),
          f"{got[k][110 - dy, 130 - dx]} {got[k][110 - dy, 190 - dx]}")
    edge_in = got[k][110 - dy, 104 - dx]     # 4 px inside the moved edge
    edge_out = got[k][110 - dy, 96 - dx]     # 4 px outside it
    check("the insert's edge moved with the camera",
          near(edge_in, RED) and near(edge_out, plate_frames[k][110 - dy, 96 - dx], 8),
          f"in {edge_in} out {edge_out} "
          f"plate {plate_frames[k][110 - dy, 96 - dx]}")
    err = np.abs(got[k][:40].astype(int) - plate_frames[k][:40].astype(int)).mean()
    check("the wall away from the screen is the plate's own", err < 2.5,
          f"mean abs error {err:.2f}")

    print("screen_comp - the gate catches what it claims to")
    nothing = sc.verify(plate, plate, scr.full_mask, track)
    check("the plate passed off as a composite fails",
          not nothing.ok and any("landed" in f for f in nothing.failures),
          str(nothing))
    shifted = tmp / "shifted.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(out), "-vf",
                    "eq=brightness=0.05", "-c:v", "libx264", "-preset",
                    "ultrafast", "-crf", "12", str(shifted)], check=True)
    bad = sc.verify(plate, shifted, scr.full_mask, track)
    check("a brightness shift on the plate fails",
          any("colour shift" in f for f in bad.failures), str(bad))
    short = tmp / "short.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(out),
                    "-frames:v", str(N - 3), "-c:v", "libx264", "-preset",
                    "ultrafast", str(short)], check=True)
    check("missing frames fail",
          any("frame count" in f for f in
              sc.verify(plate, short, scr.full_mask, track).failures))

    locked = tmp / "locked.mp4"
    rep2 = sc.composite(plate, content, locked, replace_track(spec), "flat",
                        crf=12, preset="ultrafast")
    px = _frames(locked)[k][110 - dy, 104 - dx]
    check("without tracking the insert stays put while the wall moves",
          not near(px, RED), str(px))
    check("...and the report says tracking was off",
          any("tracking was off" in n for n in rep2.notes), str(rep2.notes))


def replace_track(spec: ScreenSpec) -> ScreenSpec:
    return ScreenSpec(quad=spec.quad, audio=spec.audio, track=False)


def test_shapes_and_looks(tmp: Path) -> None:
    print("screen_comp - angled quad, mask edge, glass")
    import cv2
    import numpy as np
    plate, content, plate_frames = _footage(tmp, moving=False)
    skew = [(100.0, 65.0), (219.0, 80.0), (212.0, 150.0), (105.0, 154.0)]
    out = tmp / "skew.mp4"
    rep = sc.composite(plate, content, out,
                       ScreenSpec(quad=skew, audio="none", track=False),
                       crf=12, preset="ultrafast")
    f = _frames(out)[3]
    check("an angled quad is filled", near(f[110, 120], RED)
          and near(f[115, 200], GREEN), f"{f[110, 120]} {f[115, 200]}")
    check("outside the quad but inside its bounding box is still the plate",
          near(f[68, 214], plate_frames[3][68, 214], 8),
          f"{f[68, 214]} vs {plate_frames[3][68, 214]}")
    check("a locked-off plate passes without a track", rep.ok, str(rep))

    mask = np.zeros((H, W), np.uint8)
    cv2.ellipse(mask, (160, 110), (58, 43), 0, 0, 360, 255, -1)
    mpath = tmp / "mask.png"
    cv2.imwrite(str(mpath), mask)
    out = tmp / "mask.mp4"
    rep = sc.composite(plate, content, out,
                       ScreenSpec(mask=str(mpath), audio="none", track=False),
                       crf=12, preset="ultrafast")
    f = _frames(out)[3]
    check("a mask alone works: the middle is content", near(f[110, 140], RED),
          str(f[110, 140]))
    check("the mask's rounded corner keeps the plate",
          near(f[70, 104], plate_frames[3][70, 104], 8),
          f"{f[70, 104]} vs {plate_frames[3][70, 104]}")
    check("and the gate passes", rep.ok, str(rep))
    cv2.imwrite(str(mpath), mask[:100])
    check("a mask of the wrong size is refused, not resized",
          _raises(lambda: sc.composite(
              plate, content, tmp / "x.mp4",
              ScreenSpec(mask=str(mpath), audio="none", track=False))))

    out = tmp / "crt.mp4"
    look = {"preset": "crt", "glow_px": 6, "rim_px": 6}
    rep = sc.composite(plate, content, out,
                       ScreenSpec(quad=QUAD, audio="none", track=False), look,
                       crf=12, preset="ultrafast")
    f = _frames(out)[3].astype(int)
    check("the CRT look still passes the gate", rep.ok, str(rep))
    check("glass darkens towards the bezel",
          f[110, 130].sum() > f[110, 102].sum() + 30,
          f"{f[110, 130]} vs {f[110, 102]}")
    check("the screen lights the wall next to it",
          f[110, 96].sum() > plate_frames[3][110, 96].astype(int).sum() + 3,
          f"{f[110, 96]} vs {plate_frames[3][110, 96]}")
    check("crop outside the content frame is refused",
          _raises(lambda: sc.composite(
              plate, content, tmp / "x.mp4",
              ScreenSpec(quad=QUAD, content_crop=(90, 0, 40, 40),
                         audio="none", track=False))))
    print("screen_comp - sound follows the picture's length")
    for name, secs in (("long", 2.0), ("short", 0.2)):
        snd = tmp / f"content_{name}.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(content), "-f", "lavfi",
             "-i", f"sine=frequency=440:duration={secs}", "-map", "0:v",
             "-map", "1:a", "-c:v", "copy", "-c:a", "aac", str(snd)],
            check=True)
        out = tmp / f"snd_{name}.mp4"
        rep = sc.composite(plate, snd, out,
                           ScreenSpec(quad=QUAD, track=False), crf=12,
                           preset="ultrafast")
        kinds = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
             "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout
        check(f"{name} content audio: carried over, no frame lost or added",
              rep.ok and "audio" in kinds and sc.count_frames(out) == N,
              f"{rep} {kinds!r} {sc.count_frames(out)}")
    rep = sc.composite(plate, content, tmp / "silent.mp4",
                       ScreenSpec(quad=QUAD, track=False), crf=12,
                       preset="ultrafast")
    check("content with no audio stream is not an error", rep.ok, str(rep))

    cfg = tmp / "screen.json"
    cfg.write_text(json.dumps({"quad": QUAD, "track": False, "audio": "none"}))
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = sc._main([str(plate), str(content), str(tmp / "cli.mp4"),
                       "--config", str(cfg)])
    check("the CLI composites, prints the gate and exits 0 on a pass",
          rc == 0 and "screen comp gate: PASS" in buf.getvalue(),
          f"{rc} {buf.getvalue()[:80]}")


def main() -> int:
    test_missing_deps()
    test_config_and_report()
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
    except Exception as e:  # noqa: BLE001
        skip(f"numpy/opencv not installed: {e!r}")
    else:
        test_geometry()
        if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
            skip("ffmpeg/ffprobe not installed")
        else:
            with tempfile.TemporaryDirectory() as d:
                if test_colour_io(Path(d)):
                    test_track_and_composite(Path(d))
                    test_shapes_and_looks(Path(d))
    print(f"\nscreen_comp: {_p} passed, {_f} failed, {_s} skipped")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
