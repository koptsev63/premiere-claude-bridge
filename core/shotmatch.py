"""Shot match - pull mixed-source shots towards one colour point, bake it to LUTs.

The field problem (a festival teaser, October 2026): one cut mixes three
sources that do not belong together.

| source             | Lab b (yellow-blue) | mean chroma C |
|--------------------|---------------------|---------------|
| Sony camera clips  | about -4.4 (cool)   | 11.3          |
| AI-generated shots | about +1.3          | 10.4          |
| DJI drone shot     | about +11.4 (warm)  | 14.9          |

Cutting between them reads as three different films. A global look on top
does not fix that, it only paints the mismatch a different colour. So the
match is done per shot, in CIE Lab, *before* any look:

1. `measure()` - mean L, a, b and chroma of one shot over its midtone
   pixels. Shadows and highlights are excluded on purpose: they carry noise
   and clipping, not the colour of the scene.
2. `match_params()` - offsets (`da`, `db`) and a chroma gain (`cg`) per shot
   towards common targets (a 0.8, b 2.0, C 12.5). The rules differ by source:
   camera shots of one clip share one a/b correction (the clip's mean, so a
   cutaway to the same take never jumps), AI shots are left alone unless they
   are hotter than the target, the drone only gets its warmth tamed (never
   pushed warmer) and a fixed chroma pull. Title cards get `None` and are
   never touched.
3. `apply()` - the correction, weighted to the midtones (a smoothstep from
   L 3 to 25 in, from L 85 to 100 out at 60%), followed by one of three
   looks: "A" matched pop, "B" film print, "C" dry deadpan, or "none".

The cut is rendered by ffmpeg, not by Python. Applying the Lab maths per
pixel in Python took about 60x longer over the 8466 frames of the teaser, so
each shot x look is baked once into a 33^3 .cube (`bake_cube()`) and applied
with `lut3d=interp=tetrahedral` per segment (`ffmpeg_lut_filter()`). The
error against the per-pixel maths stayed under one 8-bit code value over the
frames of the teaser. Measured here on footage-like colours the mean error is
about 0.1 code values; isolated pixels in the shadow toe and at gamut-clip
kinks reach 3-8, which no 33^3 lattice can follow. `check_cube()` repeats the
measurement in numpy (trilinear lookup) so a bad bake fails loudly before it
reaches a render: `CubeCheck.assert_ok()` raises, like the colour gate does.

Zero dependencies beyond numpy. sRGB <-> Lab (D65) is implemented here,
vectorised, so no OpenCV or PIL is needed. Frames are float32 RGB in 0..1,
shape (..., 3), sRGB-encoded (display referred, Rec709 gamma treated as
sRGB).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np

KINDS = ("cam", "ai", "drone", "title")
LOOKS = ("none", "A", "B", "C")

#: Midtone window used by `measure()` (Lab L).
MID_L_LO = 15.0
MID_L_HI = 85.0

# sRGB (D65) primaries -> XYZ, white point derived from the same matrix so
# that white maps to a = b = 0 exactly.
_M_RGB2XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ],
    dtype=np.float64,
)
_M_XYZ2RGB = np.linalg.inv(_M_RGB2XYZ)
_WHITE = _M_RGB2XYZ.sum(axis=1)  # XYZ of RGB (1, 1, 1)
_EPS = 216.0 / 24389.0
_KAPPA = 24389.0 / 27.0


class ShotMatchFailure(RuntimeError):
    pass


# --------------------------------------------------------------------------
# sRGB <-> CIE Lab (D65)
# --------------------------------------------------------------------------

def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB 0..1, shape (..., 3) -> Lab (L 0..100, a/b about -128..127)."""
    x = np.clip(np.asarray(rgb, dtype=np.float64), 0.0, 1.0)
    xyz = _srgb_to_linear(x) @ _M_RGB2XYZ.T / _WHITE
    f = np.where(xyz > _EPS, np.cbrt(xyz), (_KAPPA * xyz + 16.0) / 116.0)
    L = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    """Lab -> sRGB 0..1, shape (..., 3). Out-of-gamut values are clipped."""
    lab = np.asarray(lab, dtype=np.float64)
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    fy = (L + 16.0) / 116.0
    fx = fy + a / 500.0
    fz = fy - b / 200.0
    f = np.stack([fx, fy, fz], axis=-1)
    f3 = f ** 3
    xyz = np.where(f3 > _EPS, f3, (116.0 * f - 16.0) / _KAPPA)
    # Y has its own linear branch, keyed on L rather than on f^3
    y_lin = np.where(L > _KAPPA * _EPS, fy ** 3, L / _KAPPA)
    xyz = np.concatenate([xyz[..., :1], y_lin[..., None], xyz[..., 2:]], axis=-1)
    lin = (xyz * _WHITE) @ _M_XYZ2RGB.T
    return _linear_to_srgb(lin)


# --------------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------------

@dataclass
class ShotStats:
    """What one shot measures as. `group` is the clip name for camera shots."""

    index: int
    kind: str                      # "cam" | "ai" | "drone" | "title"
    L: float = 0.0
    a: float = 0.0
    b: float = 0.0
    C: float = 0.0                 # mean chroma, sqrt(a^2 + b^2) per pixel
    group: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown shot kind {self.kind!r}; known: {KINDS}")


def measure(
    rgb_frames: np.ndarray | Iterable[np.ndarray],
) -> tuple[float, float, float, float]:
    """Mean (L, a, b, C) over the midtone pixels of one or more frames.

    `rgb_frames` is one float32 RGB array (H, W, 3) or an iterable of them
    (sizes may differ). Midtone means Lab L between `MID_L_LO` and
    `MID_L_HI`; if a shot has no such pixels (pure black card) all pixels are
    used. C is the mean of per-pixel chroma, not the chroma of the mean a/b:
    a shot with saturated reds and greens that cancel out is still colourful.
    """
    if isinstance(rgb_frames, np.ndarray) and rgb_frames.shape[-1] == 3:
        frames: Sequence[np.ndarray] = [rgb_frames]
    else:
        frames = list(rgb_frames)
    if not frames:
        raise ValueError("measure() needs at least one frame")
    lab = np.concatenate(
        [rgb_to_lab(np.asarray(f, dtype=np.float32)).reshape(-1, 3) for f in frames]
    )
    mid = (lab[:, 0] >= MID_L_LO) & (lab[:, 0] <= MID_L_HI)
    if mid.any():
        lab = lab[mid]
    chroma = np.hypot(lab[:, 1], lab[:, 2])
    return (
        float(lab[:, 0].mean()),
        float(lab[:, 1].mean()),
        float(lab[:, 2].mean()),
        float(chroma.mean()),
    )


# --------------------------------------------------------------------------
# Match rules
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class MatchTargets:
    a: float = 0.8
    b: float = 2.0
    C: float = 12.5


@dataclass(frozen=True)
class ShotParams:
    da: float = 0.0     # Lab a offset at full midtone weight
    db: float = 0.0     # Lab b offset
    cg: float = 1.0     # chroma gain applied to a and b


def _clip(x: float, lo: float, hi: float) -> float:
    return float(min(max(x, lo), hi))


def match_params(
    stats: Sequence[ShotStats],
    targets: MatchTargets = MatchTargets(),
) -> dict[int, ShotParams | None]:
    """Per-shot correction towards `targets`. Title cards map to None.

    - cam:   da = clip(0.8 * (Ta - a), -4, 4), db = clip(Tb - b, -10, 10),
             with a/b the mean of the shot's group (same clip name), so one
             clip gets one cast correction. cg pulls chroma towards Tc and is
             clipped to 1.0..1.3 (never desaturates a camera shot).
    - ai:    no a/b move. Chroma is only tamed when above Tc:
             cg = clip(1 - 0.25 * (1 - Tc / C), 0.9, 1.08).
    - drone: da = 0, db = clip(0.4 * (Tb - b), -5, 0) (cool a warm shot down,
             never push it warmer), cg = 0.92.
    """
    groups: dict[str, list[ShotStats]] = {}
    for s in stats:
        if s.kind == "cam" and s.group is not None:
            groups.setdefault(s.group, []).append(s)

    out: dict[int, ShotParams | None] = {}
    for s in stats:
        if s.kind == "title":
            out[s.index] = None
        elif s.kind == "cam":
            members = groups.get(s.group, [s]) if s.group is not None else [s]
            a = sum(x.a for x in members) / len(members)
            b = sum(x.b for x in members) / len(members)
            out[s.index] = ShotParams(
                da=_clip(0.8 * (targets.a - a), -4.0, 4.0),
                db=_clip(1.0 * (targets.b - b), -10.0, 10.0),
                cg=_clip(1 + 0.7 * (targets.C / max(s.C, 3.0) - 1), 1.0, 1.3),
            )
        elif s.kind == "ai":
            cg = (
                _clip(1 - 0.25 * (1 - targets.C / max(s.C, 3.0)), 0.9, 1.08)
                if s.C > targets.C
                else 1.0
            )
            out[s.index] = ShotParams(0.0, 0.0, cg)
        elif s.kind == "drone":
            out[s.index] = ShotParams(
                0.0, _clip(0.4 * (targets.b - s.b), -5.0, 0.0), 0.92
            )
        else:  # pragma: no cover - guarded by ShotStats.__post_init__
            raise ValueError(f"unknown shot kind {s.kind!r}")
    return out


# --------------------------------------------------------------------------
# Applying a correction + look
# --------------------------------------------------------------------------

def _smooth(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    t = np.clip((x - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _bump(x: np.ndarray, amount: float) -> np.ndarray:
    """Sine S-curve on 0..1 luminance, zero at both ends and at the middle."""
    return np.clip(
        x + amount * np.sin(np.pi * (x - 0.5) * 2) * 0.5 * (x * (1 - x)) * 4, 0, 1
    )


def apply(
    rgb: np.ndarray,
    params: ShotParams | None,
    look: str = "none",
) -> np.ndarray:
    """Match correction plus look on float RGB 0..1, shape (..., 3).

    `params=None` (a title card) returns the input untouched, whatever the
    look. Returns float32 clipped to 0..1.

    Looks (Lab, after the match):
    - "A" matched pop: gentle S-curve (0.10), warm lift in the upper mids,
      chroma +15%.
    - "B" film print: stronger S-curve (0.22) onto faded blacks (L 4..97),
      warm highlights, cool-neutral shadows, chroma -10%.
    - "C" dry deadpan: contrast x1.18 around mid grey, chroma -35%.
    """
    if look not in LOOKS:
        raise ValueError(f"unknown look {look!r}; known: {LOOKS}")
    x = np.asarray(rgb, dtype=np.float32)
    if params is None:
        return x
    lab = rgb_to_lab(x)
    L, A, B = lab[..., 0].copy(), lab[..., 1].copy(), lab[..., 2].copy()

    w = _smooth(L, 3, 25) * (1 - 0.4 * _smooth(L, 85, 100))
    A = (A + params.da * w) * params.cg
    B = (B + params.db * w) * params.cg

    if look == "A":
        L = _bump(L / 100, 0.10) * 100
        B = B + 1.5 * _smooth(L, 30, 80)
        A, B = A * 1.15, B * 1.15
    elif look == "B":
        L = 4 + _bump(L / 100, 0.22) * 93
        hi = _smooth(L, 50, 95)
        sh = 1 - _smooth(L, 5, 35)
        B = B + 6.0 * hi - 2.5 * sh
        A = A + 1.5 * hi - 1.0 * sh
        A, B = A * 0.9, B * 0.9
    elif look == "C":
        L = np.clip(0.5 + (L / 100 - 0.5) * 1.18, 0, 1) * 100
        A, B = A * 0.65, B * 0.65

    return np.clip(lab_to_rgb(np.stack([L, A, B], axis=-1)), 0.0, 1.0).astype(
        np.float32
    )


# --------------------------------------------------------------------------
# .cube baking, checking, ffmpeg
# --------------------------------------------------------------------------

def _grid(size: int) -> np.ndarray:
    """(size^3, 3) RGB lattice, R fastest, then G, then B (.cube order)."""
    g = np.linspace(0.0, 1.0, size, dtype=np.float32)
    bb, gg, rr = np.meshgrid(g, g, g, indexing="ij")
    return np.stack([rr, gg, bb], axis=-1).reshape(-1, 3)


def bake_cube(
    fn: Callable[[np.ndarray], np.ndarray],
    size: int = 33,
    title: str = "shotmatch",
) -> str:
    """Sample `fn` (rgb -> rgb, float 0..1, shape (N, 3)) on a size^3 lattice
    and return the .cube text: LUT_3D_SIZE header, R fastest, then G, then B.
    """
    if size < 2:
        raise ValueError("cube size must be >= 2")
    out = np.clip(np.asarray(fn(_grid(size)), dtype=np.float64), 0.0, 1.0)
    if out.shape != (size ** 3, 3):
        raise ValueError(f"fn returned shape {out.shape}, expected {(size ** 3, 3)}")
    lines = [
        f'TITLE "{title}"',
        f"LUT_3D_SIZE {size}",
        "DOMAIN_MIN 0.0 0.0 0.0",
        "DOMAIN_MAX 1.0 1.0 1.0",
    ]
    lines += [f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in out]
    return "\n".join(lines) + "\n"


def write_cube(path: str | Path, text: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def parse_cube(text: str) -> tuple[int, np.ndarray]:
    """Parse a 3D .cube into (size, table[b, g, r, 3])."""
    size = 0
    rows: list[list[float]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        head = line.split()[0]
        if head == "LUT_3D_SIZE":
            size = int(line.split()[1])
        elif head in ("TITLE", "DOMAIN_MIN", "DOMAIN_MAX", "LUT_1D_SIZE"):
            continue
        else:
            rows.append([float(v) for v in line.split()[:3]])
    if size < 2:
        raise ShotMatchFailure("cube has no LUT_3D_SIZE")
    if len(rows) != size ** 3:
        raise ShotMatchFailure(
            f"cube has {len(rows)} data lines, expected {size ** 3}"
        )
    return size, np.asarray(rows, dtype=np.float64).reshape(size, size, size, 3)


def lookup_cube(table: np.ndarray, rgb: np.ndarray) -> np.ndarray:
    """Trilinear lookup of rgb (..., 3) in a table[b, g, r, 3]."""
    size = table.shape[0]
    p = np.clip(np.asarray(rgb, dtype=np.float64), 0.0, 1.0) * (size - 1)
    i0 = np.minimum(np.floor(p).astype(int), size - 2)
    f = p - i0
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
    out = 0.0
    for db in (0, 1):
        for dg in (0, 1):
            for dr in (0, 1):
                wgt = (
                    (fr if dr else 1 - fr)
                    * (fg if dg else 1 - fg)
                    * (fb if db else 1 - fb)
                )
                out = out + wgt * table[b0 + db, g0 + dg, r0 + dr]
    return out


#: p99 error may be this many tolerances (shadow toe and clip kinks).
OUTLIER_FACTOR = 4.0


@dataclass
class CubeCheck:
    """Does a baked cube reproduce the function it was baked from?

    Errors are absolute channel errors on a 0..1 scale. A cube passes when
    the mean error is within `tolerance` (1/255 by default) and the 99th
    percentile within `OUTLIER_FACTOR` x tolerance; the worst single sample
    is reported as `max_err` but does not decide, because it always sits in
    the shadow toe or on a gamut-clip kink.
    """

    size: int
    samples: int
    max_err: float
    p99_err: float
    mean_err: float
    tolerance: float = 1 / 255

    @property
    def ok(self) -> bool:
        return (
            self.mean_err <= self.tolerance
            and self.p99_err <= OUTLIER_FACTOR * self.tolerance
        )

    def summary(self) -> str:
        def f(v: float) -> str:
            return f"{v * 255:.3f}/255"

        return (
            f"size={self.size} samples={self.samples} mean_err={f(self.mean_err)} "
            f"p99_err={f(self.p99_err)} max_err={f(self.max_err)} "
            f"tolerance={f(self.tolerance)}"
        )

    def assert_ok(self) -> None:
        """Raise instead of letting a bad bake reach a render."""
        if not self.ok:
            raise ShotMatchFailure(
                "cube does not reproduce its source: " + self.summary()
            )


def check_cube(
    text: str,
    fn: Callable[[np.ndarray], np.ndarray],
    samples: int = 4096,
    seed: int = 0,
    tolerance: float = 1 / 255,
    natural: bool = True,
) -> CubeCheck:
    """Compare a trilinear lookup of the cube with `fn` on random colours.

    `natural=True` draws footage-like colours (a random grey plus up to
    +-0.15 per channel); `natural=False` draws uniformly from the whole RGB
    cube, which is dominated by gamut corners no real shot contains.
    """
    size, table = parse_cube(text)
    rng = np.random.default_rng(seed)
    if natural:
        grey = rng.random((samples, 1))
        pts = np.clip(grey + (rng.random((samples, 3)) - 0.5) * 0.3, 0, 1)
    else:
        pts = rng.random((samples, 3))
    pts = pts.astype(np.float32)
    want = np.clip(np.asarray(fn(pts), dtype=np.float64), 0.0, 1.0)
    err = np.abs(lookup_cube(table, pts) - want).max(axis=-1)
    return CubeCheck(
        size=size,
        samples=samples,
        max_err=float(err.max()),
        p99_err=float(np.percentile(err, 99)),
        mean_err=float(err.mean()),
        tolerance=tolerance,
    )


def ffmpeg_lut_filter(cube_path: str | Path) -> str:
    """`lut3d=file='...':interp=tetrahedral` with the path escaped for the
    ffmpeg filtergraph (two parsing levels: backslash, quote and colon).
    """
    p = str(cube_path)
    p = p.replace("\\", "\\\\").replace("'", "'\\\\\\''").replace(":", "\\:")
    return f"lut3d=file='{p}':interp=tetrahedral"
