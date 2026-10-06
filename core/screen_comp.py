"""Screen replacement - put a clip inside a screen that is in the shot.

A phone, a monitor, an old television in the corner of a handheld plate:
the picture on it gets replaced in post. Three things make a screen insert
read as fake, and each one is a step here.

1. **The insert slides.** The plate is handheld, the insert is nailed to
   the frame. So the camera is tracked first: an ECC affine fit of every
   frame against frame 0, measured on the surroundings with the screen
   itself masked out (its content and its reflections are exactly what
   must not drive the track). The insert rides that transform.
2. **The plate changes colour.** Decode a Rec.709 file through a default
   path, composite in RGB, encode it back, and the whole plate - not just
   the screen - comes out slightly off, which is obvious the moment the
   shot is cut next to its untouched neighbours. All I/O here goes through
   ffmpeg pipes with the YCbCr matrix and range stated explicitly on the
   way in and on the way out (`accurate_rnd+full_chroma_int`), and the
   output is tagged. The gate measures the plate *outside* the screen
   before and after; it has to come back the same.
3. **The glass is gone.** A real screen is behind glass: it has
   reflections, its black is never black, it is darker towards the bezel,
   it lights the plastic around it, and a CRT bulges. `Look` carries those
   as numbers; `CRT` is the set used on the job this came from (a tube
   television in a vertical handheld shot, September 2026), `FLAT` turns
   them all off for a modern panel or for testing.

Where the screen is: a **quad** (four corner pixels in frame 0, clockwise
from top-left) and/or a **mask** PNG (white = glass, frame 0). The quad
gives geometry - the insert is warped into it, so a screen seen at an
angle works. The mask gives the edge - rounded corners, a hand over the
bezel. A mask alone uses its bounding box as the quad.

```bash
python -m core.screen_comp PLATE.mp4 CONTENT.mp4 OUT.mp4 --config screen.json
python -m core.screen_comp PLATE.mp4 CONTENT.mp4 OUT.mp4 \
    --quad 212,640,868,655,860,1150,205,1130 --look crt
```

```json
{"quad": [[212, 640], [868, 655], [860, 1150], [205, 1130]],
 "mask": "screen_mask.png", "grow_px": 4,
 "content_crop": [0, 140, 1920, 800],
 "track": true, "track_region": [80, 500, 920, 800],
 "look": {"preset": "crt", "glow": 0.3}, "audio": "content"}
```

The gate (`ScreenCompReport.assert_ok()` raises `ScreenCompFailure`):

| check                         | passes when                               |
|-------------------------------|-------------------------------------------|
| frame count                   | output has as many frames as the plate    |
| camera track held             | ECC failed on ≤ 2% of frames              |
| plate untouched outside       | mean abs error ≤ 2.5 levels (8-bit)       |
| no colour shift               | per-channel mean shift ≤ 1.0 level        |
| content landed in the screen  | mean change inside ≥ 4 levels             |
| output tagged                 | colour tags say bt709                     |

Honest boundaries:

- Thresholds are engineering defaults checked on synthetic footage. They
  are **not** calibrated on rejected deliveries the way `core.colorgate`
  is - treat a pass as "nothing is mechanically wrong", then look at it.
- The track is affine (drift, small rotation and zoom). It does not model
  parallax or a real change of perspective; on a move that large, track in
  a compositor. Nothing detects occlusion: a hand crossing the screen is
  painted over unless your mask excludes it, and the mask is static.
- The gate cannot tell a perfect track from a plausible wrong one. It
  catches ECC *failing*; an insert that drifts by two pixels passes.
  Watch the corners of the result.
- SDR Rec.709 (or 601 via `matrix=`) only. No HDR/HLG, no log.
- Content is conformed to the plate's frame rate and held on its last
  frame if it runs out. Picture is re-encoded (libx264), always.

Needs numpy and opencv (`pip install numpy opencv-python-headless`) plus
ffmpeg. They are imported on first use, so importing this module is free.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

MAX_TRACK_FAIL_SHARE = 0.02
MAX_OUTSIDE_ERR = 2.5
MAX_COLOUR_SHIFT = 1.0
MIN_INSIDE_CHANGE = 4.0
_FLAGS = "accurate_rnd+full_chroma_int+bitexact"


class ScreenCompFailure(RuntimeError):
    pass


class ScreenCompUnavailable(RuntimeError):
    """numpy / opencv / ffmpeg missing - see message for the fix."""


def _deps():
    try:
        import cv2
        import numpy as np
    except Exception as exc:  # noqa: BLE001
        raise ScreenCompUnavailable(
            "core.screen_comp needs numpy and opencv: "
            "pip install numpy opencv-python-headless "
            f"(import error: {exc!r})") from exc
    return np, cv2


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise ScreenCompUnavailable("ffmpeg not found on PATH")
    return exe


# ---- configuration ---------------------------------------------------- #

@dataclass
class Look:
    """What the glass does to the picture. All zero = a perfect flat panel."""

    fit: str = "fill"          # "fill" crops content to the screen, "fit" letterboxes
    # barrel factor; 0.07 is a visible CRT curve. Edge midpoints stay put,
    # the centre is magnified and the corners read past the content (black)
    # - a tube's corners are rounded, so give it a mask that says so.
    bulge: float = 0.0
    black_lift: float = 0.0    # a lit screen's black is never pure black
    vignette: float = 0.0      # darkening towards the corners (0-1)
    rim: float = 0.0           # darkening right at the bezel (0-1)
    rim_px: float = 22.0
    reflections: float = 0.0   # how much of the real glass highlights survive
    glow: float = 0.0          # light spilled onto the bezel and the body
    glow_px: float = 28.0
    feather_px: float = 1.5    # edge softness of the insert
    scanlines: float = 0.0     # depth of a 3-px line structure (0-1)
    bloom: float = 0.0         # highlight bleed
    chroma_px: int = 0         # horizontal R/B misregistration


FLAT = Look()
CRT = Look(bulge=0.07, black_lift=0.07, vignette=0.22, rim=0.45,
           reflections=1.0, glow=0.35, scanlines=0.22, bloom=0.9,
           chroma_px=2)
#: The same glass without the tube artefacts.
GLASS = Look(black_lift=0.07, vignette=0.22, rim=0.45, reflections=1.0,
             glow=0.18)
PRESETS = {"flat": FLAT, "crt": CRT, "glass": GLASS}


@dataclass
class ScreenSpec:
    quad: list[tuple[float, float]] | None = None   # TL, TR, BR, BL (frame 0)
    mask: str | None = None                         # PNG, white = glass
    grow_px: int = 0                                # dilate the mask edge
    content_crop: tuple[int, int, int, int] | None = None   # x, y, w, h
    track: bool = True
    track_region: tuple[int, int, int, int] | None = None   # x, y, w, h
    audio: str = "content"                          # content | plate | none


def look_from(value: Any) -> Look:
    """A preset name, a dict of overrides, or a dict with a `preset` key."""
    if value is None:
        return FLAT
    if isinstance(value, Look):
        return value
    if isinstance(value, str):
        if value not in PRESETS:
            raise ScreenCompFailure(
                f"unknown look {value!r}; choose from {sorted(PRESETS)}")
        return PRESETS[value]
    over = dict(value)
    base = look_from(over.pop("preset", "flat"))
    known = {f.name for f in fields(Look)}
    bad = sorted(set(over) - known)
    if bad:
        raise ScreenCompFailure(f"unknown look setting(s): {bad}")
    return replace(base, **over)


def load_config(path: str | Path) -> tuple[ScreenSpec, Look]:
    """Read a job config. A relative `mask` resolves next to the config."""
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    look = look_from(raw.pop("look", None))
    known = {f.name for f in fields(ScreenSpec)}
    bad = sorted(set(raw) - known)
    if bad:
        raise ScreenCompFailure(f"unknown config key(s): {bad}")
    spec = ScreenSpec(**raw)
    if spec.quad is not None:
        spec.quad = [tuple(float(v) for v in pt) for pt in spec.quad]
    if spec.mask and not Path(spec.mask).is_absolute():
        spec.mask = str(p.parent / spec.mask)
    _validate(spec)
    return spec, look


def _validate(spec: ScreenSpec) -> None:
    if spec.quad is None and not spec.mask:
        raise ScreenCompFailure("give the screen as a quad, a mask, or both")
    if spec.quad is not None and len(spec.quad) != 4:
        raise ScreenCompFailure("quad needs exactly 4 points: TL, TR, BR, BL")
    if spec.audio not in ("content", "plate", "none"):
        raise ScreenCompFailure("audio must be content, plate or none")


# ---- geometry (pure) -------------------------------------------------- #

def quad_from_mask(mask) -> list[tuple[float, float]]:
    """Bounding box of the white area, as corner pixels TL, TR, BR, BL."""
    np, _ = _deps()
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        raise ScreenCompFailure("the screen mask is empty (no white pixels)")
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    return [(float(x0), float(y0)), (float(x1), float(y0)),
            (float(x1), float(y1)), (float(x0), float(y1))]


def screen_size(quad: list[tuple[float, float]]) -> tuple[int, int]:
    """Pixel size of the flat screen buffer the quad is filled from."""
    def dist(a, b):
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
    tl, tr, br, bl = quad
    w = int(round(max(dist(tl, tr), dist(bl, br)))) + 1
    h = int(round(max(dist(tl, bl), dist(tr, br)))) + 1
    if w < 2 or h < 2:
        raise ScreenCompFailure(f"degenerate screen quad: {quad}")
    return w, h


def quad_homography(quad, size: tuple[int, int]):
    """3x3 matrix taking screen-buffer pixels to frame-0 plate pixels."""
    np, cv2 = _deps()
    w, h = size
    src = np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])
    return cv2.getPerspectiveTransform(src, np.float32(quad)).astype(np.float64)


def sampling_maps(size: tuple[int, int], content_size: tuple[int, int],
                  fit: str = "fill", bulge: float = 0.0):
    """Where each screen pixel reads the content from: (map_x, map_y, u, v).

    u, v are the pixel's position on the screen in -1..1. A positive
    `bulge` magnifies the centre the way curved glass does.
    """
    np, _ = _deps()
    if fit not in ("fill", "fit"):
        raise ScreenCompFailure(f"fit must be 'fill' or 'fit', not {fit!r}")
    sw, sh = size
    cw, ch = content_size
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    u = (xx + 0.5) / sw * 2 - 1
    v = (yy + 0.5) / sh * 2 - 1
    f = (1 + bulge * (u * u + v * v)) / (1 + bulge)
    u2, v2 = u * f, v * f
    screen_aspect, content_aspect = sw / sh, cw / ch
    cover_by_height = content_aspect >= screen_aspect
    if (fit == "fill") == cover_by_height:
        # content height spans the screen; width is cropped (fill) or
        # the whole width is shown with bars above and below (fit)
        vis_h = ch
        vis_w = ch * screen_aspect
    else:
        vis_w = cw
        vis_h = cw / screen_aspect
    mx = cw / 2 + u2 * vis_w / 2 - 0.5
    my = ch / 2 + v2 * vis_h / 2 - 0.5
    return mx.astype(np.float32), my.astype(np.float32), u, v


def edge_distance(local_mask):
    """Distance (px) of every screen pixel from the nearest edge of the glass."""
    np, cv2 = _deps()
    padded = np.pad((local_mask > 0).astype(np.uint8), 1)
    return cv2.distanceTransform(padded, cv2.DIST_L2, 5)[1:-1, 1:-1]


# ---- ffmpeg I/O ------------------------------------------------------- #

def probe(path: str | Path) -> dict[str, Any]:
    """Decoded geometry and rate. Rotation is honoured: a 1920x1080 stream
    tagged -90 is a 1080x1920 picture, and that is what ffmpeg hands over."""
    exe = shutil.which("ffprobe") or "ffprobe"
    r = subprocess.run(
        [exe, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate,color_space,color_transfer,"
         "color_primaries:stream_side_data=rotation", "-of", "json",
         str(path)], capture_output=True, text=True)
    try:
        st = json.loads(r.stdout)["streams"][0]
    except (ValueError, KeyError, IndexError):
        raise ScreenCompFailure(f"ffprobe found no video stream in {path}")
    w, h = int(st["width"]), int(st["height"])
    rot = 0
    for sd in st.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    if abs(rot) % 180 == 90:
        w, h = h, w
    return {"w": w, "h": h, "fps": st.get("r_frame_rate", "25/1"),
            "colour": (st.get("color_space"), st.get("color_transfer"),
                       st.get("color_primaries"))}


def count_frames(path: str | Path) -> int:
    """Video packet count - fast, and exact enough to pick sample frames."""
    exe = shutil.which("ffprobe") or "ffprobe"
    r = subprocess.run(
        [exe, "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0",
         str(path)], capture_output=True, text=True)
    try:
        return int(r.stdout.strip().split(",")[0])
    except ValueError:
        return 0


class FrameReader:
    """Frames as BGR uint8 arrays, YCbCr decoded with a stated matrix."""

    def __init__(self, path: str | Path, w: int, h: int, *,
                 fps: str | None = None, matrix: str = "bt709",
                 gray_scale: float | None = None) -> None:
        np, _ = _deps()
        self._np = np
        vf = []
        if fps:
            vf.append(f"fps={fps}")
        if gray_scale is not None:
            w, h = max(2, int(w * gray_scale)), max(2, int(h * gray_scale))
            vf += [f"scale={w}:{h}:flags=area", "format=gray"]
            self.shape, pix = (h, w), "gray"
        else:
            vf += [f"scale=in_color_matrix={matrix}:in_range=tv:"
                   f"flags={_FLAGS}", "format=bgr24"]
            self.shape, pix = (h, w, 3), "bgr24"
        self.size = w * h * (1 if gray_scale is not None else 3)
        self.proc = subprocess.Popen(
            [_ffmpeg(), "-v", "error", "-nostdin", "-i", str(path), "-an",
             "-vf", ",".join(vf), "-f", "rawvideo", "-pix_fmt", pix, "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def read(self):
        buf = self.proc.stdout.read(self.size)
        if len(buf) < self.size:
            return None
        return self._np.frombuffer(buf, self._np.uint8).reshape(self.shape)

    def close(self) -> None:
        try:
            self.proc.stdout.close()
        finally:
            self.proc.kill()
            self.proc.wait()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class FrameWriter:
    """BGR frames in, a tagged Rec.709 H.264 file out."""

    def __init__(self, out: str | Path, w: int, h: int, fps: str, *,
                 audio_from: str | Path | None = None, matrix: str = "bt709",
                 crf: int = 16, preset: str = "slow") -> None:
        cmd = [_ffmpeg(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt",
               "bgr24", "-s", f"{w}x{h}", "-r", fps, "-i", "-"]
        if audio_from:
            # The picture sets the length: pad the sound so a short track
            # cannot truncate the video, and let -shortest trim a long one.
            cmd += ["-i", str(audio_from), "-map", "0:v", "-map", "1:a?",
                    "-af", "apad", "-c:a", "aac", "-b:a", "192k", "-shortest"]
        # setparams, not only the output options: recent ffmpeg takes the
        # tags from the frames, and frames that came in as raw RGB carry
        # none - the file then ships with primaries and transfer unset.
        cmd += ["-vf", f"scale=out_color_matrix={matrix}:out_range=tv:"
                       f"flags={_FLAGS},format=yuv420p,"
                       f"setparams=colorspace={matrix}:"
                       f"color_primaries={matrix}:color_trc={matrix}:range=tv",
                "-colorspace", matrix, "-color_primaries", matrix,
                "-color_trc", matrix, "-color_range", "tv",
                "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                     stderr=subprocess.PIPE)

    def write(self, frame) -> None:
        self.proc.stdin.write(frame.tobytes())

    def close(self) -> None:
        self.proc.stdin.close()
        err = self.proc.stderr.read().decode(errors="replace")
        if self.proc.wait() != 0:
            raise ScreenCompFailure(f"encoder failed: {err[:300]}")


# ---- tracking --------------------------------------------------------- #

@dataclass
class Track:
    """Per-frame 2x3 affine: frame-0 plate pixels -> frame-k plate pixels."""

    matrices: Any = None
    failed: list[int] = field(default_factory=list)
    min_cc: float = 1.0

    def __len__(self) -> int:
        return 0 if self.matrices is None else len(self.matrices)

    def at(self, k: int):
        return self.matrices[min(k, len(self.matrices) - 1)]


def identity_track(n: int) -> Track:
    np, _ = _deps()
    return Track(np.tile(np.eye(2, 3, dtype=np.float64), (max(1, n), 1, 1)))


def track_camera(plate: str | Path, screen_mask, *,
                 region: tuple[int, int, int, int] | None = None,
                 scale: float = 0.5, guard_px: int = 25,
                 iterations: int = 100, eps: float = 1e-6) -> Track:
    """ECC affine of every plate frame against frame 0, screen excluded.

    `screen_mask` is a full-frame array (non-zero = screen, frame 0).
    `region` (x, y, w, h) restricts the fit to a rigid part of the scene -
    use it when most of the frame is something that moves on its own.
    """
    np, cv2 = _deps()
    info = probe(plate)
    w, h = info["w"], info["h"]
    with FrameReader(plate, w, h, gray_scale=scale) as rd:
        first = rd.read()
        if first is None:
            raise ScreenCompFailure(f"no frames decoded from {plate}")
        g0 = first.astype(np.float32)
        gh, gw = g0.shape
        use = np.zeros((gh, gw), np.uint8)
        if region:
            x, y, rw, rh = region
            use[int(y * scale):int((y + rh) * scale),
                int(x * scale):int((x + rw) * scale)] = 255
        else:
            use[:] = 255
        k = 2 * guard_px + 1
        grown = cv2.dilate((np.asarray(screen_mask) > 0).astype(np.uint8),
                           np.ones((k, k), np.uint8))
        use[cv2.resize(grown, (gw, gh), interpolation=cv2.INTER_NEAREST) > 0] = 0
        if int((use > 0).sum()) < 0.02 * gw * gh:
            raise ScreenCompFailure(
                "less than 2% of the frame is left to track on - widen "
                "track_region or shrink the screen mask")
        crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
                iterations, eps)
        m = np.eye(2, 3, dtype=np.float32)
        out = [np.eye(2, 3, dtype=np.float64)]
        track = Track()
        idx = 0
        while True:
            frame = rd.read()
            if frame is None:
                break
            idx += 1
            try:
                cc, m = cv2.findTransformECC(
                    g0, frame.astype(np.float32), m.copy(),
                    cv2.MOTION_AFFINE, crit, use, 5)
                track.min_cc = min(track.min_cc, float(cc))
            except cv2.error:
                track.failed.append(idx)      # hold the last good transform
            full = m.astype(np.float64).copy()
            full[:, 2] /= scale
            out.append(full)
    track.matrices = np.array(out)
    return track


# ---- compositing ------------------------------------------------------ #

@dataclass
class _Screen:
    """Everything about the screen that does not change per frame."""

    size: tuple[int, int]
    hom: Any            # buffer -> frame-0 plate
    alpha: Any          # buffer-space matte (0-1)
    rim: Any
    full_mask: Any      # frame-0 plate-space mask (uint8)


def _prepare(spec: ScreenSpec, look: Look, w: int, h: int) -> _Screen:
    np, cv2 = _deps()
    _validate(spec)
    mask = None
    if spec.mask:
        mask = cv2.imread(str(spec.mask), cv2.IMREAD_GRAYSCALE)
        if mask is None:
            raise ScreenCompFailure(f"cannot read screen mask {spec.mask}")
        if mask.shape != (h, w):
            raise ScreenCompFailure(
                f"mask is {mask.shape[1]}x{mask.shape[0]}, plate is {w}x{h}"
                " - the mask must be drawn on a decoded plate frame")
        mask = (mask > 127).astype(np.uint8)
        if spec.grow_px > 0:
            k = 2 * spec.grow_px + 1
            mask = cv2.dilate(mask, cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (k, k)))
    quad = spec.quad if spec.quad is not None else quad_from_mask(mask)
    size = screen_size(quad)
    hom = quad_homography(quad, size)
    sw, sh = size
    if mask is not None:
        local = cv2.warpPerspective(
            mask * 255, hom, (sw, sh),
            flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP) > 127
    else:
        local = np.ones((sh, sw), bool)
    dist = edge_distance(local)
    alpha = np.clip(dist / look.feather_px, 0, 1) if look.feather_px > 0 \
        else (dist > 0).astype(np.float32)
    rim = (1 - look.rim) + look.rim * np.clip(dist / max(look.rim_px, 1e-6), 0, 1)
    full = cv2.warpPerspective((alpha > 0).astype(np.uint8) * 255, hom, (w, h))
    return _Screen(size, hom, alpha.astype(np.float32),
                   rim.astype(np.float32), (full > 127).astype(np.uint8))


def _mat3(affine):
    np, _ = _deps()
    return np.vstack([affine, [0.0, 0.0, 1.0]])


def render_screen(content, plate_patch, maps, shade, look: Look):
    """One frame of screen picture (float BGR 0-1) in buffer space.

    `content` is the cropped content frame, `plate_patch` the real screen
    as the camera saw it (same buffer space), both float 0-1.
    """
    np, cv2 = _deps()
    mx, my = maps
    vig, rim, scan = shade
    if look.chroma_px:
        content = content.copy()
        content[..., 2] = np.roll(content[..., 2], look.chroma_px, 1)
        content[..., 0] = np.roll(content[..., 0], -look.chroma_px, 1)
    pic = cv2.remap(content, mx, my, cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    pic = look.black_lift + pic * (1 - look.black_lift)
    if look.scanlines:
        pic = pic * scan
    if look.bloom:
        pic = pic + cv2.GaussianBlur(np.clip(pic - 0.55, 0, 1), (0, 0), 9) \
            * look.bloom
    pic = pic * vig * rim
    if look.reflections:
        # keep what the real glass was doing: local highlights plus
        # anything close to white, screened over the new picture
        soft = cv2.GaussianBlur(plate_patch, (0, 0), 25)
        luma = cv2.cvtColor(plate_patch, cv2.COLOR_BGR2GRAY)[..., None]
        hl = np.minimum(np.clip(plate_patch - soft - 0.02, 0, 1) * 1.1
                        + np.clip(luma - 0.82, 0, 1) * 0.7, 0.45)
        hl = hl * look.reflections
        pic = 1 - (1 - np.clip(pic, 0, 1)) * (1 - hl)
        pic = pic + plate_patch * 0.06 * look.reflections
    return np.clip(pic, 0, 1)


def composite(plate: str | Path, content: str | Path, out: str | Path,
              spec: ScreenSpec, look: Look | str | dict | None = None, *,
              track: Track | None = None, matrix: str = "bt709",
              crf: int = 16, preset: str = "slow",
              check: bool = True) -> "ScreenCompReport":
    """Track, composite, encode, then gate the written file.

    Returns the report; call `.assert_ok()` on it before delivering.
    """
    np, cv2 = _deps()
    look = look_from(look)
    pinfo = probe(plate)
    w, h, fps = pinfo["w"], pinfo["h"], pinfo["fps"]
    scr = _prepare(spec, look, w, h)
    sw, sh = scr.size

    if track is None:
        track = track_camera(plate, scr.full_mask, region=spec.track_region) \
            if spec.track else None

    cinfo = probe(content)
    cx, cy, cw, ch = spec.content_crop or (0, 0, cinfo["w"], cinfo["h"])
    if cx < 0 or cy < 0 or cx + cw > cinfo["w"] or cy + ch > cinfo["h"]:
        raise ScreenCompFailure(
            f"content_crop {spec.content_crop} is outside the "
            f"{cinfo['w']}x{cinfo['h']} content frame")
    mx, my, u, v = sampling_maps(scr.size, (cw, ch), look.fit, look.bulge)
    vig = (1 - look.vignette * np.clip((u * u + v * v) / 2, 0, 1))[..., None]
    scan = (1 - look.scanlines * (0.5 - 0.5 * np.cos(
        np.arange(sh) * 2 * np.pi / 3.0)))[:, None, None].astype(np.float32)
    shade = (vig.astype(np.float32), scr.rim[..., None], scan)

    audio = {"content": content, "plate": plate, "none": None}[spec.audio]
    writer = FrameWriter(out, w, h, fps, audio_from=audio, matrix=matrix,
                         crf=crf, preset=preset)
    frames = held = 0
    last = None
    try:
        with FrameReader(plate, w, h, matrix=matrix) as rp, \
                FrameReader(content, cinfo["w"], cinfo["h"], fps=fps,
                            matrix=matrix) as rc:
            while True:
                pf = rp.read()
                if pf is None:
                    break
                cf = rc.read()
                if cf is None:
                    cf, held = last, held + 1
                if cf is None:
                    raise ScreenCompFailure(
                        f"no frames decoded from content {content}")
                last = cf
                aff = track.at(frames) if track is not None and len(track) \
                    else np.eye(2, 3)
                to_frame = _mat3(aff) @ scr.hom
                base = pf.astype(np.float32) / 255
                patch = cv2.warpPerspective(
                    base, to_frame, (sw, sh),
                    flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP) \
                    if look.reflections else None
                pic = render_screen(
                    cf[cy:cy + ch, cx:cx + cw].astype(np.float32) / 255,
                    patch, (mx, my), shade, look)
                layer = cv2.warpPerspective(pic, to_frame, (w, h),
                                            flags=cv2.INTER_LINEAR)
                la = cv2.warpPerspective(scr.alpha, to_frame, (w, h),
                                         flags=cv2.INTER_LINEAR)[..., None]
                if look.glow:
                    spill = cv2.GaussianBlur(layer * la, (0, 0),
                                             look.glow_px) * look.glow
                    base = 1 - (1 - base) * (1 - spill * (1 - la))
                comp = base * (1 - la) + layer * la
                writer.write((np.clip(comp, 0, 1) * 255 + 0.5).astype(np.uint8))
                frames += 1
    finally:
        writer.close()

    rep = ScreenCompReport(frames=frames)
    if held:
        rep.notes.append(f"content ran out {held} frame(s) early - its "
                         f"last frame was held")
    if not spec.track:
        rep.notes.append("tracking was off - the insert is locked to the "
                         "frame, correct only on a locked-off plate")
    if check:
        verify(plate, out, scr.full_mask, track, look=look, matrix=matrix,
               report=rep)
    return rep


# ---- the gate --------------------------------------------------------- #

@dataclass
class ScreenCompReport:
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    frames: int = 0
    outside_err: float | None = None
    colour_shift: float | None = None
    inside_change: float | None = None

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, ok, detail))

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(ok for _, ok, _ in self.checks)

    @property
    def failures(self) -> list[str]:
        return [f"{n}: {d}" for n, ok, d in self.checks if not ok]

    def assert_ok(self) -> None:
        """Call this before saying the shot is done."""
        if not self.checks:
            raise ScreenCompFailure("UNVERIFIED - the gate was not run")
        if not self.ok:
            raise ScreenCompFailure("; ".join(self.failures))

    def __str__(self) -> str:
        lines = [f"screen comp gate: {'PASS' if self.ok else 'FAIL'}  "
                 f"({self.frames} frames)"]
        for name, ok, detail in self.checks:
            lines.append(f"  {'ok  ' if ok else 'FAIL'} {name}  {detail}")
        lines += [f"  note {n}" for n in self.notes]
        return "\n".join(lines)


def judge(rep: ScreenCompReport, *, plate_frames: int, out_frames: int,
          track_failed: int, outside_err: float | None,
          colour_shift: float | None, inside_change: float | None,
          tags: tuple, matrix: str = "bt709") -> ScreenCompReport:
    """The verdict on numbers already measured."""
    rep.outside_err, rep.colour_shift = outside_err, colour_shift
    rep.inside_change = inside_change
    rep.add("frame count matches the plate", out_frames == plate_frames > 0,
            f"plate {plate_frames}, output {out_frames}")
    share = track_failed / plate_frames if plate_frames else 1.0
    rep.add("camera track held", share <= MAX_TRACK_FAIL_SHARE,
            f"ECC failed on {track_failed} of {plate_frames} frames")
    if outside_err is None or colour_shift is None:
        # Fail closed: a screen that covers the frame leaves nothing to
        # compare, and "could not check" is not a pass.
        rep.add("plate untouched outside the screen", False,
                "UNVERIFIED - no plate area left outside the screen")
    else:
        rep.add("plate untouched outside the screen",
                outside_err <= MAX_OUTSIDE_ERR,
                f"mean abs error {outside_err:.2f} levels, "
                f"limit {MAX_OUTSIDE_ERR}")
        rep.add("no colour shift on the plate",
                colour_shift <= MAX_COLOUR_SHIFT,
                f"largest channel shift {colour_shift:.2f} levels, "
                f"limit {MAX_COLOUR_SHIFT}")
    if inside_change is None:
        rep.add("content landed in the screen", False,
                "UNVERIFIED - the screen area is empty after tracking")
    else:
        rep.add("content landed in the screen",
                inside_change >= MIN_INSIDE_CHANGE,
                f"mean change {inside_change:.2f} levels, "
                f"floor {MIN_INSIDE_CHANGE}")
    rep.add("output tagged " + matrix,
            all(t == matrix for t in tags), f"tags {tags}")
    return rep


def verify(plate: str | Path, out: str | Path, screen_mask,
           track: Track | None = None, *, look: Look | None = None,
           matrix: str = "bt709", samples: int = 6,
           report: ScreenCompReport | None = None) -> ScreenCompReport:
    """Measure a written composite against its plate and run the gate.

    `screen_mask`: full-frame array, non-zero = screen in frame 0.
    """
    np, cv2 = _deps()
    rep = report or ScreenCompReport()
    look = look or FLAT
    pinfo, oinfo = probe(plate), probe(out)
    w, h = pinfo["w"], pinfo["h"]
    if (oinfo["w"], oinfo["h"]) != (w, h):
        rep.add("output has the plate's geometry", False,
                f"plate {w}x{h}, output {oinfo['w']}x{oinfo['h']}")
        return rep
    mask0 = (np.asarray(screen_mask) > 0).astype(np.uint8)
    guard = int(max(8, 3 * look.glow_px if look.glow else 8))
    k_out = np.ones((2 * guard + 1, 2 * guard + 1), np.uint8)
    k_in = np.ones((11, 11), np.uint8)

    expect = count_frames(plate)
    n_s = max(1, min(samples, expect))
    wanted = {int(expect * (i + 0.5) / n_s) for i in range(n_s)} or {0}
    outside, shifts, inside = [], [], []
    n_plate = n_out = 0
    with FrameReader(plate, w, h, matrix=matrix) as rp, \
            FrameReader(out, w, h, matrix=matrix) as ro:
        while True:
            a, b = rp.read(), ro.read()
            if a is None and b is None:
                break
            idx = max(n_plate, n_out)
            n_plate += a is not None
            n_out += b is not None
            if a is None or b is None or idx not in wanted:
                continue
            m = mask0
            if track is not None and len(track):
                m = cv2.warpAffine(mask0, track.at(idx), (w, h),
                                   flags=cv2.INTER_NEAREST)
            diff = b.astype(np.float32) - a.astype(np.float32)
            far = cv2.dilate(m, k_out) == 0
            core = cv2.erode(m, k_in) > 0
            if far.any():
                outside.append(float(np.abs(diff[far]).mean()))
                shifts.append(float(np.abs(diff[far].mean(axis=0)).max()))
            if core.any():
                inside.append(float(np.abs(diff[core]).mean()))
    return judge(
        rep, plate_frames=n_plate, out_frames=n_out,
        track_failed=len(track.failed) if track is not None else 0,
        outside_err=max(outside) if outside else None,
        colour_shift=max(shifts) if shifts else None,
        inside_change=min(inside) if inside else None,
        tags=oinfo["colour"], matrix=matrix)


# ---- CLI -------------------------------------------------------------- #

def _main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    def opt(name: str):
        if name in args:
            i = args.index(name)
            val = args[i + 1]
            del args[i:i + 2]
            return val
        return None

    cfg, quad, mask, look_name = (opt("--config"), opt("--quad"),
                                  opt("--mask"), opt("--look"))
    no_track = "--no-track" in args
    files = [a for a in args if not a.startswith("--")]
    if len(files) != 3 or not (cfg or quad or mask):
        print("usage: python -m core.screen_comp PLATE CONTENT OUT "
              "(--config screen.json | --quad x,y,x,y,x,y,x,y | --mask m.png)"
              " [--look flat|glass|crt] [--no-track]")
        return 2
    try:
        spec, look = load_config(cfg) if cfg else (ScreenSpec(), FLAT)
        if quad:
            v = [float(x) for x in quad.split(",")]
            if len(v) != 8:
                raise ScreenCompFailure("--quad needs 8 numbers")
            spec.quad = list(zip(v[0::2], v[1::2]))
        if mask:
            spec.mask = mask
        if look_name:
            look = look_from(look_name)
        if no_track:
            spec.track = False
        rep = composite(files[0], files[1], files[2], spec, look)
    except (ScreenCompFailure, ScreenCompUnavailable) as e:
        print(f"screen_comp: {e}", file=sys.stderr)
        return 1
    print(rep)
    return 0 if rep.ok else 1


if __name__ == "__main__":
    raise SystemExit(_main())
