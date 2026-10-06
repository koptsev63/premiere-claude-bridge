"""Loudness - level the shots against each other, then hit the delivery target.

A cut assembled from several takes is several microphones' worth of
distance. Each take is fine on its own; in sequence the voice jumps at
every join, and a single `loudnorm` pass over the whole file does not fix
that - it moves the average and keeps the jumps. So this is two steps, in
this order:

1. **Level the shots.** Gated RMS per shot (samples under -50 dBFS are not
   speech and are not counted), every shot is pulled to the median and the
   correction is clamped to ±9 dB (anything further is a broken take, not
   a level problem). The gain changes exactly at the join and holds for
   the whole shot. The first field version eased every shot back to unity
   at its edges; that leaves the original jump audible right on the cut
   and skews the shot's own level - on a synthetic three-take join 19 dB
   apart it left 3.1 dB of spread (the clamp explains 0.7 of it), so it
   was not ported.
2. **Normalise the result.** One static gain to the integrated target,
   then a look-ahead limiter at the true-peak ceiling. Static on purpose:
   a single speaker pumped by a dynamic normaliser sounds processed. The
   limiter runs 4x oversampled, because a ceiling held on sample peaks is
   not a ceiling on true peaks.

Optional **declick**: a hard sample discontinuity at a join clicks, and
gain ramps do not remove it. A 12 ms dip to zero centred on the join does.
It is inaudible only because joins sit in pauses - if yours do not (music,
overlapping dialogue), pass `declick_ms=0`.

Then the gate, on the file that is actually being delivered:

| check              | passes when                                    |
|--------------------|------------------------------------------------|
| audio stream       | there is one                                   |
| integrated         | within ±1.0 LU of the target                   |
| true peak          | at or under the ceiling (+0.3 dB meter slack)  |
| shot spread        | loudest shot − quietest shot ≤ 2.0 dB          |

Targets are arguments, not law. `-14 LUFS / -1.0 dBTP` is the default
(the usual short-form reference); `-16 / -1.5` is the softer pair that
suits a lone talking head. Possible clicks at joins are reported as notes,
not failures: a large sample-to-sample step is also what loud treble
looks like, so it is a prompt to listen, not a verdict.

Honest boundaries: shot boundaries are yours to supply (seconds, from the
cut list) - nothing here detects them. Levelling is by RMS, which tracks
speech well and music badly; do not level a music bed with it. Picture is
stream-copied, never re-encoded, and no fades are applied - fades are an
editorial decision and belong in the timeline. Measurement is ffmpeg's
`loudnorm` analysis (EBU R128), not a certified meter.

ffmpeg-only. numpy is used when present; without it the RMS loop is pure
Python (about a second per minute of audio).

    python -m core.loudness IN OUT [--shots 0,5.6,11.2] [--lufs -14] [--tp -1]
    python -m core.loudness --check FILE [--shots ...]     # exit 1 = do not ship
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import tempfile
from array import array
from dataclasses import dataclass, field
from pathlib import Path

from core.ducking import envelope_to_volexpr

try:  # optional accelerator only
    import numpy as _np
except Exception:  # pragma: no cover - numpy is not required
    _np = None

TARGET_LUFS = -14.0
TARGET_TP = -1.0
#: The softer pair for a single speaker.
SOFT_LUFS, SOFT_TP = -16.0, -1.5
LUFS_TOLERANCE = 1.0
TP_SLACK = 0.3
MAX_SPREAD_DB = 2.0
MAX_GAIN_DB = 9.0
#: Width of the gain change at a join when declick is off.
RAMP_S = 0.010
DECLICK_MS = 12.0
#: Samples quieter than this (about -50 dBFS) are room, not voice.
GATE = 0.003
#: Sample-to-sample step that is worth a listen at a join.
CLICK_STEP = 0.12
_SR = 48000


class LoudnessFailure(RuntimeError):
    pass


# ---- pure math -------------------------------------------------------- #

def gated_rms_db(samples) -> float | None:
    """RMS in dBFS over samples louder than the gate; None if (nearly) silent."""
    if _np is not None:
        a = _np.asarray(samples, dtype=_np.float32)
        s = a[_np.abs(a) > GATE]
        if len(s) <= 100:
            return None
        return float(20 * _np.log10(_np.sqrt((s.astype(_np.float64) ** 2).mean()) + 1e-9))
    n = 0
    acc = 0.0
    for x in samples:
        if x > GATE or x < -GATE:
            acc += x * x
            n += 1
    if n <= 100:
        return None
    return 20 * math.log10(math.sqrt(acc / n) + 1e-9)


def shot_bounds(cuts: list[float], total_s: float) -> list[tuple[float, float]]:
    """Cut points (seconds, start of each shot) -> (start, end) per shot."""
    pts = sorted(c for c in cuts if 0 <= c < total_s)
    if not pts or pts[0] > 1e-6:
        pts.insert(0, 0.0)
    return [(a, b) for a, b in zip(pts, pts[1:] + [total_s]) if b > a]


def level_plan(levels: list[float | None],
               max_gain_db: float = MAX_GAIN_DB) -> tuple[float | None, list[float]]:
    """(target dB, gain per shot in dB). Silent shots get 0 dB."""
    valid = sorted(l for l in levels if l is not None)
    if not valid:
        return None, [0.0] * len(levels)
    mid = len(valid) // 2
    target = valid[mid] if len(valid) % 2 else (valid[mid - 1] + valid[mid]) / 2
    gains = [0.0 if l is None
             else max(-max_gain_db, min(max_gain_db, target - l))
             for l in levels]
    return target, gains


def gain_keyframes(shots: list[tuple[float, float]], gains_db: list[float],
                   ramp_s: float = RAMP_S,
                   declick_ms: float = DECLICK_MS) -> list[tuple[float, float]]:
    """Linear-gain keyframes: each shot holds its own gain, edge to edge.

    The gain changes *at the join* (over `ramp_s`, centred on it). With
    declick on, the join dips to zero instead, `declick_ms` wide in total.
    """
    if not shots:
        return []
    lin = [10 ** (g / 20) for g in gains_db]
    kf: list[tuple[float, float]] = [(shots[0][0], lin[0])]
    for i in range(len(shots) - 1):
        t = shots[i][1]
        room = min(shots[i][1] - shots[i][0],
                   shots[i + 1][1] - shots[i + 1][0]) / 2 - 1e-4
        if declick_ms > 0:
            half = max(1e-4, min(declick_ms / 2000.0, room))
            kf += [(t - half, lin[i]), (t, 0.0), (t + half, lin[i + 1])]
        else:
            half = max(5e-4, min(ramp_s / 2, room))
            kf += [(t - half, lin[i]), (t + half, lin[i + 1])]
    kf.append((shots[-1][1], lin[-1]))
    return kf


def parse_loudnorm(stderr: str) -> dict[str, float] | None:
    """The JSON block ffmpeg's loudnorm prints, as floats."""
    try:
        raw = json.loads(stderr[stderr.rindex("{"):stderr.rindex("}") + 1])
        return {"i": float(raw["input_i"]), "tp": float(raw["input_tp"]),
                "lra": float(raw["input_lra"])}
    except (ValueError, KeyError, TypeError):
        return None


def max_step(samples, at: int, radius: int = 240) -> float:
    """Largest sample-to-sample jump within `radius` samples of index `at`."""
    lo, hi = max(0, at - radius), min(len(samples), at + radius)
    seg = samples[lo:hi]
    if len(seg) < 2:
        return 0.0
    if _np is not None:
        return float(_np.abs(_np.diff(_np.asarray(seg, dtype=_np.float32))).max())
    return max(abs(b - a) for a, b in zip(seg, seg[1:]))


# ---- ffmpeg ----------------------------------------------------------- #

def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, **kw)


def has_audio(path: str | Path) -> bool:
    probe = shutil.which("ffprobe") or "ffprobe"
    r = _run([probe, "-v", "error", "-select_streams", "a:0",
              "-show_entries", "stream=codec_type", "-of", "csv=p=0",
              str(path)], text=True)
    return "audio" in r.stdout


def has_video(path: str | Path) -> bool:
    probe = shutil.which("ffprobe") or "ffprobe"
    r = _run([probe, "-v", "error", "-select_streams", "v:0",
              "-show_entries", "stream=codec_type", "-of", "csv=p=0",
              str(path)], text=True)
    return "video" in r.stdout


def decode_mono(path: str | Path, sr: int = _SR):
    """The whole track as mono float samples (numpy array, or array('f'))."""
    raw = _run([_ffmpeg(), "-v", "error", "-nostdin", "-i", str(path), "-vn",
                "-f", "f32le", "-acodec", "pcm_f32le", "-ar", str(sr),
                "-ac", "1", "-"]).stdout
    raw = raw[: len(raw) // 4 * 4]
    if _np is not None:
        return _np.frombuffer(raw, dtype=_np.float32)
    a = array("f")
    a.frombytes(raw)
    if sys.byteorder == "big":  # pragma: no cover
        a.byteswap()
    return a


def measure(path: str | Path) -> dict[str, float] | None:
    """Integrated loudness (LUFS), true peak (dBTP) and LRA of a file."""
    r = _run([_ffmpeg(), "-v", "info", "-nostdin", "-i", str(path), "-vn",
              "-af", "loudnorm=print_format=json", "-f", "null", "-"],
             text=True)
    return parse_loudnorm(r.stderr)


def shot_levels(samples, shots: list[tuple[float, float]],
                sr: int = _SR) -> list[float | None]:
    return [gated_rms_db(samples[int(s * sr):int(e * sr)]) for s, e in shots]


def spread_db(levels: list[float | None]) -> float:
    ok = [l for l in levels if l is not None]
    return max(ok) - min(ok) if len(ok) > 1 else 0.0


# ---- the gate --------------------------------------------------------- #

@dataclass
class LoudnessReport:
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    integrated: float | None = None
    true_peak: float | None = None
    levels: list[float | None] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, ok, detail))

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.checks)

    @property
    def failures(self) -> list[str]:
        return [f"{n}: {d}" for n, ok, d in self.checks if not ok]

    def assert_ok(self) -> None:
        """Call this before saying the sound is done."""
        if not self.ok:
            raise LoudnessFailure("; ".join(self.failures))

    def __str__(self) -> str:
        lines = [f"loudness gate: {'PASS' if self.ok else 'FAIL'}"]
        for name, ok, detail in self.checks:
            lines.append(f"  {'ok  ' if ok else 'FAIL'} {name}  {detail}")
        lines += [f"  note {n}" for n in self.notes]
        return "\n".join(lines)


def judge(integrated: float | None, true_peak: float | None,
          levels: list[float | None] | None = None,
          target_lufs: float = TARGET_LUFS, target_tp: float = TARGET_TP,
          max_spread_db: float = MAX_SPREAD_DB,
          lufs_tolerance: float = LUFS_TOLERANCE) -> LoudnessReport:
    """The verdict on numbers you already have."""
    rep = LoudnessReport(integrated=integrated, true_peak=true_peak,
                         levels=list(levels or []))
    if integrated is None or true_peak is None:
        # Fail closed: "could not measure" is not a pass.
        rep.add("loudness measured", False, "UNVERIFIED - ffmpeg loudnorm "
                "returned nothing (no audio, or ffmpeg missing)")
        return rep
    rep.add("integrated loudness on target",
            abs(integrated - target_lufs) <= lufs_tolerance,
            f"{integrated:.1f} LUFS, target {target_lufs:.1f} "
            f"±{lufs_tolerance:.1f}")
    rep.add("true peak under the ceiling",
            true_peak <= target_tp + TP_SLACK,
            f"{true_peak:.1f} dBTP, ceiling {target_tp:.1f}")
    if levels is not None:
        sp = spread_db(levels)
        shown = " ".join("-" if l is None else f"{l:.1f}" for l in levels)
        rep.add("shots sit at one level", sp <= max_spread_db,
                f"spread {sp:.1f} dB, limit {max_spread_db:.1f} ({shown})")
    else:
        rep.notes.append("no shot boundaries given - level between shots "
                         "was not checked")
    return rep


def check_delivery(path: str | Path, cuts: list[float] | None = None,
                   target_lufs: float = TARGET_LUFS,
                   target_tp: float = TARGET_TP,
                   max_spread_db: float = MAX_SPREAD_DB,
                   lufs_tolerance: float = LUFS_TOLERANCE) -> LoudnessReport:
    """Measure a finished file and run the gate. This is the call to use.

    `cuts` are shot start times in seconds on the delivered file.
    """
    if not has_audio(path):
        rep = LoudnessReport()
        rep.add("audio stream present", False, "the file has no audio stream")
        return rep
    m = measure(path) or {}
    levels = None
    clicks: list[tuple[float, float]] = []
    if cuts:
        samples = decode_mono(path)
        shots = shot_bounds(cuts, len(samples) / _SR)
        levels = shot_levels(samples, shots)
        for s, _ in shots[1:]:
            step = max_step(samples, int(s * _SR))
            if step > CLICK_STEP:
                clicks.append((round(s, 3), round(step, 2)))
    rep = judge(m.get("i"), m.get("tp"), levels, target_lufs, target_tp,
                max_spread_db, lufs_tolerance)
    rep.checks.insert(0, ("audio stream present", True, ""))
    if clicks:
        rep.notes.append(f"possible clicks at joins (s, step): {clicks[:6]} "
                         f"- listen before shipping")
    return rep


# ---- processing ------------------------------------------------------- #

def level_and_normalize(src: str | Path, out: str | Path,
                        cuts: list[float] | None = None,
                        target_lufs: float = TARGET_LUFS,
                        target_tp: float = TARGET_TP,
                        max_gain_db: float = MAX_GAIN_DB,
                        ramp_s: float = RAMP_S,
                        declick_ms: float = DECLICK_MS,
                        audio_bitrate: str = "256k") -> dict:
    """Level shots (if `cuts` given), normalise, write `out`.

    Picture, when `src` has one and `out` can hold it, is stream-copied.
    Returns what was done; run `check_delivery(out, cuts)` on the result.
    """
    src, out = str(src), str(out)
    if not has_audio(src):
        raise LoudnessFailure(f"no audio stream in {src}")
    info: dict = {"out": out, "shots": [], "target_db": None}
    with tempfile.TemporaryDirectory(prefix="loudness_") as tmp:
        stage = str(Path(tmp) / "leveled.wav")
        chain = [f"aresample={_SR}"]
        if cuts:
            samples = decode_mono(src)
            shots = shot_bounds(cuts, len(samples) / _SR)
            levels = shot_levels(samples, shots)
            target, gains = level_plan(levels, max_gain_db)
            info["target_db"] = target
            info["shots"] = [
                {"start": round(s, 3), "end": round(e, 3),
                 "level_db": None if l is None else round(l, 1),
                 "gain_db": round(g, 1)}
                for (s, e), l, g in zip(shots, levels, gains)]
            expr = envelope_to_volexpr(
                gain_keyframes(shots, gains, ramp_s, declick_ms))
            # volume evaluates once per audio frame: 16-sample frames give
            # the envelope 0.33 ms steps, fine enough for a 12 ms declick.
            chain += ["asetnsamples=n=16:p=0",
                      f"volume='{expr}':eval=frame"]
        r = _run([_ffmpeg(), "-y", "-v", "error", "-nostdin", "-i", src,
                  "-vn", "-af", ",".join(chain), "-c:a", "pcm_f32le", stage],
                 text=True)
        if r.returncode != 0:
            raise LoudnessFailure(f"levelling pass failed: {r.stderr[:300]}")

        before = measure(stage)
        if before is None:
            raise LoudnessFailure("could not measure loudness after levelling")
        info["before"] = before
        gain_db = target_lufs - before["i"]
        limit = max(0.0625, min(1.0, 10 ** (target_tp / 20)))
        video = has_video(src) and Path(out).suffix.lower() in (
            ".mp4", ".mov", ".mkv", ".m4v")
        ext = Path(out).suffix.lower()
        codec = ["-c:a", "pcm_s24le"] if ext in (".wav", ".aif", ".aiff") \
            else ["-c:a", "aac", "-b:a", audio_bitrate]

        def render(g: float) -> None:
            af = (f"volume={g:.3f}dB,aresample={_SR * 4},"
                  f"alimiter=limit={limit:.5f}:level=0:attack=5:release=50:latency=1,"
                  f"aresample={_SR}")
            cmd = [_ffmpeg(), "-y", "-v", "error", "-nostdin"]
            if video:
                cmd += ["-i", src, "-i", stage, "-map", "0:v:0", "-map",
                        "1:a:0", "-c:v", "copy", "-movflags", "+faststart"]
            else:
                cmd += ["-i", stage]
            res = _run(cmd + ["-af", af, *codec, out], text=True)
            if res.returncode != 0:
                raise LoudnessFailure(f"normalise pass failed: {res.stderr[:300]}")

        render(gain_db)
        after = measure(out)
        # The limiter takes a little loudness back when it works hard.
        # One correction pass; more would be chasing the meter.
        if after and abs(after["i"] - target_lufs) > 0.5:
            gain_db += target_lufs - after["i"]
            render(gain_db)
            after = measure(out)
        info.update(gain_db=round(gain_db, 2), after=after)
    return info


def _floats(s: str) -> list[float]:
    return [float(x) for x in s.split(",") if x.strip()]


def _main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    def opt(name: str, default=None):
        if name in args:
            i = args.index(name)
            val = args[i + 1]
            del args[i:i + 2]
            return val
        return default

    cuts = _floats(opt("--shots", "") or "")
    lufs = float(opt("--lufs", TARGET_LUFS))
    tp = float(opt("--tp", TARGET_TP))
    declick = float(opt("--declick-ms", DECLICK_MS))
    check_only = "--check" in args
    files = [a for a in args if not a.startswith("--")]
    if (check_only and len(files) != 1) or (not check_only and len(files) != 2):
        print("usage: python -m core.loudness IN OUT [--shots 0,5.6,11.2] "
              "[--lufs -14] [--tp -1] [--declick-ms 12]\n"
              "       python -m core.loudness --check FILE [--shots ...]")
        return 2
    if not check_only:
        try:
            info = level_and_normalize(files[0], files[1], cuts or None,
                                       lufs, tp, declick_ms=declick)
        except LoudnessFailure as e:
            print(f"loudness: {e}", file=sys.stderr)
            return 1
        for s in info["shots"]:
            print("  shot %7.2f-%-7.2f  %s dB  gain %+.1f dB" % (
                s["start"], s["end"], s["level_db"], s["gain_db"]))
        print(f"  static gain {info['gain_db']:+.1f} dB -> {info['out']}")
    rep = check_delivery(files[-1], cuts or None, lufs, tp)
    print(rep)
    return 0 if rep.ok else 1


if __name__ == "__main__":
    raise SystemExit(_main())
