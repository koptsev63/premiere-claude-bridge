"""core.hits - loudness hits of a music track and "cut on the hit" verification.

Field origin (23.10.2026, Treasures Wanted pilot, Suno track): the editor had
laid 5-second generated clips end to end and let the music run underneath.
Vladimir heard it at once: "the music enters at second 12, and the cut to the
volcano after the big hit is off". Measured: the track carried a boom every
5.0 s (2.0, 7.1, 12.0, 17.0 ... 47.1), the loudest at 12.1 and 47.2, while
the cuts sat at 7.0, 9.4, 14.4 - between the hits. Beat tracking (core.beats)
does not catch this: the hits are not the beat grid, they are the loud
accents, and a listener forgives a cut only within about two frames of one.

Pure, unit-tested core (no ffmpeg):
  loudness_curve(samples, sr, hop_s)     -> (times, dB) short-window RMS loudness
  find_hits(times, db, *, min_gap_s, rise_db, floor_db) -> [(t, peak_dB)]
  loudest_near(times, db, t, window_s)   -> (t_peak, dB)
  cuts_vs_hits(cuts, times, db, *, window_s, tol_s) -> HitReport

ffmpeg-backed:
  analyze(path, sr=22050)                -> (times, dB) of the file's audio

A hit is a sharp rise: the loudness jumps by `rise_db` within 60 ms over the
previous 50 ms minimum and peaks above `floor_db`. Two hits closer than
`min_gap_s` count once (the loudest).
"""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass, field

HOP_S = 0.02          # loudness window, seconds (20 ms = half a frame at 24 fps)
TOL_S = 2 / 24        # two frames at 24 fps: beyond this a cut reads as "off"
WINDOW_S = 0.5        # how far around a cut we look for the hit it was meant for


def loudness_curve(samples: list[float], sr: int, hop_s: float = HOP_S) -> tuple[list[float], list[float]]:
    """RMS loudness in dBFS per non-overlapping window of hop_s seconds."""
    hop = max(1, int(sr * hop_s))
    times, db = [], []
    for i in range(0, len(samples) - hop + 1, hop):
        seg = samples[i:i + hop]
        rms = math.sqrt(sum(x * x for x in seg) / hop)
        times.append(i / sr)
        db.append(20 * math.log10(rms + 1e-9))
    return times, db


def find_hits(times: list[float], db: list[float], *, min_gap_s: float = 0.8,
              rise_db: float = 12.0, floor_db: float = -16.0) -> list[tuple[float, float]]:
    """Sharp loud accents: rise >= rise_db from the min of the previous 5 windows
    to the max of the next 3, peak above floor_db, de-duplicated by min_gap_s."""
    hits: list[tuple[float, float]] = []
    for i in range(5, len(db) - 3):
        peak = max(db[i:i + 3])
        rise = peak - min(db[i - 5:i])
        if rise >= rise_db and peak >= floor_db:
            k = i + db[i:i + 3].index(peak)
            t = times[k]
            if hits and t - hits[-1][0] < min_gap_s:
                if peak > hits[-1][1]:
                    hits[-1] = (t, peak)
            else:
                hits.append((t, peak))
    return hits


def loudest_near(times: list[float], db: list[float], t: float, window_s: float = WINDOW_S) -> tuple[float, float]:
    best = None
    for tt, d in zip(times, db):
        if abs(tt - t) <= window_s and (best is None or d > best[1]):
            best = (tt, d)
    return best if best else (t, float("-inf"))


@dataclass
class CutHit:
    cut: float
    hit: float
    delta: float          # cut - hit, seconds (negative = cut early)
    hit_db: float

    @property
    def ok(self) -> bool:
        return abs(self.delta) <= TOL_S


@dataclass
class HitReport:
    items: list[CutHit] = field(default_factory=list)

    @property
    def off(self) -> list[CutHit]:
        return [c for c in self.items if not c.ok]

    def assert_ok(self) -> None:
        if self.off:
            raise AssertionError("cuts off the hit: " + ", ".join(
                f"{c.cut:.2f}s (hit {c.hit:.2f}, {c.delta:+.2f})" for c in self.off))


def cuts_vs_hits(cuts: list[float], times: list[float], db: list[float], *,
                 window_s: float = WINDOW_S, tol_s: float = TOL_S) -> HitReport:
    """For every cut, the loudest moment within window_s and the offset to it."""
    rep = HitReport()
    for c in cuts:
        t, d = loudest_near(times, db, c, window_s)
        rep.items.append(CutHit(cut=c, hit=t, delta=round(c - t, 3), hit_db=d))
    return rep


def _read_pcm_mono(path: str, sr: int = 22050) -> list[float]:
    import array
    out = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(sr),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    a = array.array("f"); a.frombytes(out)
    return a.tolist()


def analyze(path: str, sr: int = 22050, hop_s: float = HOP_S) -> tuple[list[float], list[float]]:
    return loudness_curve(_read_pcm_mono(path, sr), sr, hop_s)


def _main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m core.hits AUDIO [cut_s ...]"); return 2
    times, db = analyze(argv[0])
    hits = find_hits(times, db)
    print("hits:", " ".join(f"{t:.2f}({d:.0f})" for t, d in hits))
    cuts = [float(x) for x in argv[1:]]
    if cuts:
        rep = cuts_vs_hits(cuts, times, db)
        for it in rep.items:
            print(f"  cut {it.cut:6.2f}  hit {it.hit:6.2f}  delta {it.delta:+.2f}  {'ok' if it.ok else 'OFF'}")
        return 1 if rep.off else 0
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv[1:]))
