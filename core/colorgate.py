"""Colour gate — a grade has to be visible, and it has to not be burnt.

An LLM cannot look at a grade. It can render four versions, thumbnail
them, and declare them all fine — which is exactly what happened on the
V1 reel in August 2026. Two "film look" versions went out with blown
highlights and orange skin; the director's verdict was blunt: *"варианты
два и три жёстко пережжённые, и ты не проверил себя скриншотами"* — and,
about the timid fourth one, *"я вообще не заметил, что ты что-то сделал"*.

So the gate has two sides, and a grade must pass both:

**Upper gate — is it burnt?** Against the ungraded base:

| check          | what it measures                        | ceiling                    |
|----------------|-----------------------------------------|----------------------------|
| `clipping`     | % of pixels with luma > 250             | `min(3.0, base + 1.2)`     |
| `oversat`      | % of pixels with S > .85 and V > .5     | `min(5.0, base + 3.0)`     |
| `skin_cr`      | mean Cr over skin-candidate pixels      | `min(165, base + 8)`       |
| `skin_sat`     | mean saturation of those pixels         | `min(0.52, base + 0.10)`   |

Ceilings are relative *and* absolute: a grade may not push much past the
base, and may not pass an absolute limit however hot the base already is.
Calibrated against that day's rejects — the Kodak 2383 and Fuji 3513
print LUTs at full strength measure 5.47% and 5.67% clipping over a 1.59%
base (ceiling 2.79%) and fail; everything that shipped stayed at or under
2.34%.

**Lower gate — can anyone see it?** Mean CIE ΔE (Lab) against the base,
floor 5.0. The textbook just-noticeable difference is ~2.5, but that is
for flat patches under lab light: on a moving picture it is invisible.
The grade the director could not see at all measured ΔE 3.23 — it would
have passed a textbook gate. Everything he approved measured 5.57 (pastel),
5.70 (teal/orange) and 10.18 (film), so the floor sits at 5.0 by his eye,
not by the literature.

Two grading rules the gate cannot check, learned the same day:

- **Never auto-white-balance on whole-frame statistics.** Averaging a
  frame that is mostly wall pulls the wall towards neutral and drags skin
  orange with it. Correct exposure at half strength (clamped 0.9–1.15)
  and leave the balance to a human eye.
- **Film-emulation LUTs (Kodak 2383, Fuji 3513) expect Cineon Log in.**
  Feeding Rec709 into them burns the highlights before any gate runs —
  the header of the .cube says which input it wants. If you must use one
  on Rec709, blend it at ≤55% and let the gate walk the strength down.

Zero new dependencies: frames come from the ffmpeg the repo already
requires; numpy is used when present and a pure-Python path (strided
sampling) runs when it is not.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:  # optional accelerator only
    import numpy as _np
except Exception:  # pragma: no cover - numpy is not required
    _np = None

#: Absolute ceilings — a grade may not cross these however hot the base is.
MAX_CLIPPING = 3.0
MAX_OVERSAT = 5.0
MAX_SKIN_CR = 165.0
MAX_SKIN_SAT = 0.52
#: Headroom a grade is allowed to add on top of the base.
HEAD_CLIPPING = 1.2
HEAD_OVERSAT = 3.0
HEAD_SKIN_CR = 8.0
HEAD_SKIN_SAT = 0.10
#: Below this mean ΔE the viewer does not see a look at all. Set by the
#: director's own verdict on a 3.23 grade ("I did not notice you did
#: anything"), not by the textbook 2.5 patch threshold.
MIN_DELTA_E = 5.0

_SAMPLE_W, _SAMPLE_H = 270, 480


class ColorGateFailure(RuntimeError):
    pass


@dataclass
class FrameStats:
    """What we measure on a frame (or the mean over sampled frames)."""

    clipping: float = 0.0   # % pixels with luma > 250
    oversat: float = 0.0    # % pixels with S > .85 and V > .5
    skin_cr: float = 0.0    # mean Cr of skin-candidate pixels (0-255)
    skin_sat: float = 0.0   # mean saturation of those pixels (0-1)
    skin_share: float = 0.0  # % of the frame that looked like skin

    def as_dict(self) -> dict[str, float]:
        return {
            "clipping": round(self.clipping, 3),
            "oversat": round(self.oversat, 3),
            "skin_cr": round(self.skin_cr, 2),
            "skin_sat": round(self.skin_sat, 4),
            "skin_share": round(self.skin_share, 2),
        }


def mean_stats(stats: list[FrameStats]) -> FrameStats:
    if not stats:
        return FrameStats()
    n = len(stats)
    skin = [s for s in stats if s.skin_share > 0] or stats
    return FrameStats(
        clipping=sum(s.clipping for s in stats) / n,
        oversat=sum(s.oversat for s in stats) / n,
        skin_cr=sum(s.skin_cr for s in skin) / len(skin),
        skin_sat=sum(s.skin_sat for s in skin) / len(skin),
        skin_share=sum(s.skin_share for s in stats) / n,
    )


# ---- pixel math ------------------------------------------------------- #

def _luma(r: int, g: int, b: int) -> float:
    return 0.299 * r + 0.587 * g + 0.114 * b


def frame_stats(rgb: bytes, stride: int = 4) -> FrameStats:
    """Statistics of one raw rgb24 frame.

    `stride` subsamples pixels on the pure-Python path; it does not change
    what is measured, only how many pixels are looked at.
    """
    if _np is not None:
        return _frame_stats_np(rgb)
    n = len(rgb) // 3
    if n == 0:
        return FrameStats()
    step = max(1, stride)
    clip = hot = skin_n = 0
    cr_sum = sat_sum = 0.0
    seen = 0
    for i in range(0, n, step):
        j = i * 3
        r, g, b = rgb[j], rgb[j + 1], rgb[j + 2]
        seen += 1
        if _luma(r, g, b) > 250:
            clip += 1
        mx, mn = max(r, g, b), min(r, g, b)
        sat = 0.0 if mx == 0 else (mx - mn) / mx
        if sat > 0.85 and mx / 255 > 0.5:
            hot += 1
        cr = 128 + 0.5 * r - 0.4187 * g - 0.0813 * b
        cb = 128 - 0.1687 * r - 0.3313 * g + 0.5 * b
        if 133 <= cr <= 173 and 77 <= cb <= 127:
            skin_n += 1
            cr_sum += cr
            sat_sum += sat
    return FrameStats(
        clipping=100.0 * clip / seen,
        oversat=100.0 * hot / seen,
        skin_cr=cr_sum / skin_n if skin_n else 0.0,
        skin_sat=sat_sum / skin_n if skin_n else 0.0,
        skin_share=100.0 * skin_n / seen,
    )


def _frame_stats_np(rgb: bytes) -> FrameStats:  # pragma: no cover - needs numpy
    a = _np.frombuffer(rgb, dtype=_np.uint8).astype(_np.float32)
    a = a[: (len(a) // 3) * 3].reshape(-1, 3)
    r, g, b = a[:, 0], a[:, 1], a[:, 2]
    n = len(a)
    if n == 0:
        return FrameStats()
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    mx, mn = a.max(axis=1), a.min(axis=1)
    sat = _np.where(mx == 0, 0.0, (mx - mn) / _np.maximum(mx, 1e-6))
    cr = 128 + 0.5 * r - 0.4187 * g - 0.0813 * b
    cb = 128 - 0.1687 * r - 0.3313 * g + 0.5 * b
    skin = (cr >= 133) & (cr <= 173) & (cb >= 77) & (cb <= 127)
    k = int(skin.sum())
    return FrameStats(
        clipping=float(100.0 * (luma > 250).sum() / n),
        oversat=float(100.0 * ((sat > 0.85) & (mx / 255 > 0.5)).sum() / n),
        skin_cr=float(cr[skin].mean()) if k else 0.0,
        skin_sat=float(sat[skin].mean()) if k else 0.0,
        skin_share=float(100.0 * k / n),
    )


def _srgb_to_lab(r: float, g: float, b: float) -> tuple[float, float, float]:
    def lin(c: float) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    rl, gl, bl = lin(r), lin(g), lin(b)
    x = (0.4124 * rl + 0.3576 * gl + 0.1805 * bl) / 0.95047
    y = 0.2126 * rl + 0.7152 * gl + 0.0722 * bl
    z = (0.0193 * rl + 0.1192 * gl + 0.9505 * bl) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def mean_delta_e(base: bytes, graded: bytes, stride: int = 8) -> float:
    """Mean CIE76 ΔE between two raw rgb24 frames of the same size."""
    n = min(len(base), len(graded)) // 3
    if n == 0:
        return 0.0
    if _np is not None:  # pragma: no cover - needs numpy
        return _mean_delta_e_np(base, graded, n)
    step = max(1, stride)
    total = seen = 0
    acc = 0.0
    for i in range(0, n, step):
        j = i * 3
        l1 = _srgb_to_lab(base[j], base[j + 1], base[j + 2])
        l2 = _srgb_to_lab(graded[j], graded[j + 1], graded[j + 2])
        acc += sum((x - y) ** 2 for x, y in zip(l1, l2)) ** 0.5
        seen += 1
    total = seen
    return acc / total if total else 0.0


def _mean_delta_e_np(base: bytes, graded: bytes, n: int) -> float:  # pragma: no cover
    def lab(buf: bytes):
        a = _np.frombuffer(buf, dtype=_np.uint8)[: n * 3]
        a = a.astype(_np.float32).reshape(-1, 3) / 255.0
        m = a <= 0.04045
        a = _np.where(m, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
        r, g, b = a[:, 0], a[:, 1], a[:, 2]
        x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
        y = 0.2126 * r + 0.7152 * g + 0.0722 * b
        z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
        xyz = _np.stack([x, y, z], axis=1)
        f = _np.where(xyz > 0.008856, _np.cbrt(xyz), 7.787 * xyz + 16 / 116)
        return _np.stack([
            116 * f[:, 1] - 16,
            500 * (f[:, 0] - f[:, 1]),
            200 * (f[:, 1] - f[:, 2]),
        ], axis=1)

    d = lab(base) - lab(graded)
    return float(_np.sqrt((d ** 2).sum(axis=1)).mean())


# ---- sampling --------------------------------------------------------- #

def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def duration(path: str | Path) -> float:
    probe = shutil.which("ffprobe") or "ffprobe"
    out = subprocess.run(
        [probe, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def grab_frame(path: str | Path, at: float,
               size: tuple[int, int] = (_SAMPLE_W, _SAMPLE_H)) -> bytes:
    """One raw rgb24 frame, decoded straight into memory (no PNG detour)."""
    w, h = size
    res = subprocess.run(
        [_ffmpeg(), "-v", "error", "-ss", f"{at:.3f}", "-i", str(path),
         "-frames:v", "1", "-vf", f"scale={w}:{h}", "-pix_fmt", "rgb24",
         "-f", "rawvideo", "-"],
        capture_output=True)
    return res.stdout


def sample_times(path: str | Path, frames: int = 8,
                 upto: float | None = None) -> list[float]:
    """Evenly spread sample points, skipping any tail you exclude.

    `upto` matters: a black tail card or a fade drags every statistic
    towards zero and hides a burnt picture in the middle.
    """
    dur = min(duration(path), upto) if upto else duration(path)
    if dur <= 0:
        return [0.0]
    return [dur * (i + 0.5) / frames for i in range(frames)]


def measure(path: str | Path, frames: int = 8,
            upto: float | None = None) -> FrameStats:
    times = sample_times(path, frames, upto)
    return mean_stats([frame_stats(grab_frame(path, t)) for t in times])


# ---- the gate --------------------------------------------------------- #

@dataclass
class ColorGateReport:
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    base: FrameStats | None = None
    graded: FrameStats | None = None
    delta_e: float = 0.0

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, ok, detail))

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.checks)

    @property
    def failures(self) -> list[str]:
        return [f"{n}: {d}" for n, ok, d in self.checks if not ok]

    def assert_ok(self) -> None:
        """Call this before saying the grade is done."""
        if not self.ok:
            raise ColorGateFailure("; ".join(self.failures))

    def __str__(self) -> str:
        head = "PASS" if self.ok else "FAIL"
        lines = [f"colour gate: {head}  (ΔE {self.delta_e:.2f})"]
        for name, ok, detail in self.checks:
            lines.append(f"  {'ok  ' if ok else 'FAIL'} {name}  {detail}")
        return "\n".join(lines)


def ceilings(base: FrameStats) -> dict[str, float]:
    """Per-check limits for this base — relative headroom, absolute cap."""
    return {
        "clipping": min(MAX_CLIPPING, base.clipping + HEAD_CLIPPING),
        "oversat": min(MAX_OVERSAT, base.oversat + HEAD_OVERSAT),
        "skin_cr": min(MAX_SKIN_CR, base.skin_cr + HEAD_SKIN_CR),
        "skin_sat": min(MAX_SKIN_SAT, base.skin_sat + HEAD_SKIN_SAT),
    }


def judge(base: FrameStats, graded: FrameStats, delta_e: float,
          min_delta_e: float = MIN_DELTA_E) -> ColorGateReport:
    """Both sides of the gate on already-measured statistics."""
    cap = ceilings(base)
    rep = ColorGateReport(base=base, graded=graded, delta_e=delta_e)
    rep.add("highlights not blown",
            graded.clipping <= cap["clipping"],
            f"{graded.clipping:.2f}% clipped, ceiling {cap['clipping']:.2f}% "
            f"(base {base.clipping:.2f}%)")
    rep.add("colour not oversaturated",
            graded.oversat <= cap["oversat"],
            f"{graded.oversat:.2f}%, ceiling {cap['oversat']:.2f}% "
            f"(base {base.oversat:.2f}%)")
    rep.add("skin not pushed red",
            graded.skin_cr <= cap["skin_cr"],
            f"Cr {graded.skin_cr:.1f}, ceiling {cap['skin_cr']:.1f} "
            f"(base {base.skin_cr:.1f})")
    rep.add("skin not oversaturated",
            graded.skin_sat <= cap["skin_sat"],
            f"S {graded.skin_sat:.3f}, ceiling {cap['skin_sat']:.3f} "
            f"(base {base.skin_sat:.3f})")
    rep.add("the look is actually visible",
            delta_e >= min_delta_e,
            f"ΔE {delta_e:.2f}, floor {min_delta_e:.2f}")
    return rep


def check_grade(base_path: str | Path, graded_path: str | Path,
                frames: int = 8, upto: float | None = None,
                min_delta_e: float = MIN_DELTA_E) -> ColorGateReport:
    """Measure both files and run the gate. This is the call to use."""
    times = sample_times(base_path, frames, upto)
    base_frames = [grab_frame(base_path, t) for t in times]
    graded_frames = [grab_frame(graded_path, t) for t in times]
    base = mean_stats([frame_stats(f) for f in base_frames])
    graded = mean_stats([frame_stats(f) for f in graded_frames])
    deltas = [mean_delta_e(a, b) for a, b in zip(base_frames, graded_frames)
              if a and b]
    delta_e = sum(deltas) / len(deltas) if deltas else 0.0
    return judge(base, graded, delta_e, min_delta_e)


def _main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        print("usage: python -m core.colorgate <base> <graded> "
              "[--frames N] [--upto SECONDS]")
        return 2
    frames = int(args[args.index("--frames") + 1]) if "--frames" in args else 8
    upto = float(args[args.index("--upto") + 1]) if "--upto" in args else None
    rep = check_grade(args[0], args[1], frames=frames, upto=upto)
    print(rep)
    return 0 if rep.ok else 1


if __name__ == "__main__":
    raise SystemExit(_main())
