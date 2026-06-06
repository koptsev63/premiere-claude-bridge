"""core.beats - musical beat and BPM detection for music-driven montage.

Honest boundary: this is energy-onset detection, not a full-blown MIR solver.
It works well on clear-cut rhythmic tracks (Balkan brass, EDM, hip-hop, march);
it can mis-fire on polyrhythmic, rubato, or heavily-reverbed material. When
confidence is low (< 0.5) treat the BPM as approximate and review snapped cuts
by ear before rendering.

Pure, unit-tested core (no ffmpeg):
  rms_frames(samples, win, hop)           - RMS energy per analysis frame
  adaptive_threshold(energies, sensitivity) - per-frame adaptive gate
  pick_onsets(energies, thresholds, min_gap_frames) - onset candidates
  estimate_bpm(onset_times, min_bpm, max_bpm) -> (bpm, confidence)

ffmpeg-backed:
  detect_beats(path, *, sr, sensitivity)  - full analysis, returns dict

Cutlist helper (pure given beats):
  snap_cutlist_to_beats(cutlist, beats)   - snap cut offsets to nearest beat

CLI:  python -m core.beats AUDIO [--sensitivity 0.5] [--sr 22050]

Ported from OpenReel Video (MIT) packages/core/src/audio/beat-detection-engine.ts
"""

from __future__ import annotations

import array
import math
import struct
import subprocess
import sys
from dataclasses import replace
from typing import Sequence

from core.cutlist import Cut, Cutlist

# --------------------------------------------------------------------------- #
# Constants (mirror OpenReel defaults)
# --------------------------------------------------------------------------- #

DEF_SR = 22050         # analysis sample rate, Hz
DEF_WIN = 2048         # analysis frame size, samples
DEF_HOP = 512          # hop size between frames, samples
DEF_SENSITIVITY = 0.5  # 0.0 = strict (few onsets), 1.0 = loose (many onsets)
DEF_MIN_BPM = 60       # slowest tempo to consider
DEF_MAX_BPM = 200      # fastest tempo to consider
_MIN_ONSET_GAP_S = 0.100  # de-dupe window: ignore onsets closer than 100 ms

# Voting weights for BPM candidates derived from inter-onset intervals
_WEIGHT_FULL = 1.0   # interval is exactly one beat
_WEIGHT_DOUBLE = 0.5  # interval spans two beats (subdivided onset missed)
_WEIGHT_HALF = 0.3   # interval spans half a beat (double-time sub-division)


# --------------------------------------------------------------------------- #
# Pure helpers - usable without any audio I/O
# --------------------------------------------------------------------------- #

try:
    import numpy as _np  # optional fast path
    _HAS_NUMPY = True
except ImportError:  # pragma: no cover
    _HAS_NUMPY = False


def rms_frames(
    samples: "Sequence[float] | _np.ndarray",
    win: int = DEF_WIN,
    hop: int = DEF_HOP,
) -> list[float]:
    """RMS energy for each analysis frame.

    Parameters
    ----------
    samples : flat sequence of float audio samples (mono, any range)
    win     : frame length in samples
    hop     : hop between successive frame starts

    Returns
    -------
    list of float RMS values, one per frame.
    Length = max(0, (len(samples) - win) // hop + 1)
    """
    n = len(samples)
    if n < win:
        return []

    if _HAS_NUMPY:
        # Fast path: strided view - no copy for large buffers
        arr = _np.asarray(samples, dtype=_np.float32)
        n_frames = (n - win) // hop + 1
        shape = (n_frames, win)
        strides = (arr.strides[0] * hop, arr.strides[0])
        frames = _np.lib.stride_tricks.as_strided(arr, shape=shape,
                                                  strides=strides)
        rms = _np.sqrt(_np.mean(frames ** 2, axis=1))
        return [float(round(v, 6)) for v in rms]

    # Pure-Python slow path
    out: list[float] = []
    i = 0
    while i + win <= n:
        block = samples[i:i + win]
        mean_sq = sum(x * x for x in block) / win
        out.append(round(math.sqrt(mean_sq), 6))
        i += hop
    return out


def _moving_average(seq: list[float], k: int) -> list[float]:
    """Simple k-frame centred-ish moving average (causal: uses past frames)."""
    out: list[float] = []
    for i, v in enumerate(seq):
        lo = max(0, i - k + 1)
        window = seq[lo:i + 1]
        out.append(sum(window) / len(window))
    return out


def adaptive_threshold(
    energies: list[float],
    sensitivity: float = DEF_SENSITIVITY,
    local_half: int = 50,
    smooth_k: int = 5,
) -> list[float]:
    """Compute a per-frame adaptive onset threshold (OpenReel algorithm).

    For each frame i:
      local_window = energies[max(0,i-local_half) : i+local_half+1]
      med  = median(local_window)
      mean = mean(local_window)
      base = med + (mean - med) * (1 - sensitivity)
      threshold[i] = base * (1.5 - sensitivity * 0.5)

    Parameters
    ----------
    energies    : RMS energy sequence (output of rms_frames)
    sensitivity : float in [0.0, 1.0]; higher = lower threshold = more onsets
    local_half  : half-width of the local neighbourhood in frames (~+-50)
    smooth_k    : moving-average smoothing applied BEFORE thresholding

    Returns
    -------
    list of float thresholds, same length as energies
    """
    if not energies:
        return []

    smoothed = _moving_average(energies, smooth_k)
    thresholds: list[float] = []
    multiplier = 1.5 - sensitivity * 0.5

    if _HAS_NUMPY:
        arr = _np.array(smoothed, dtype=_np.float64)
        n = len(arr)
        for i in range(n):
            lo, hi = max(0, i - local_half), min(n, i + local_half + 1)
            window = arr[lo:hi]
            med = float(_np.median(window))
            mean = float(_np.mean(window))
            base = med + (mean - med) * (1.0 - sensitivity)
            thresholds.append(base * multiplier)
        return thresholds

    # Pure-Python median
    def _median(seq: list[float]) -> float:
        s = sorted(seq)
        mid = len(s) // 2
        return (s[mid] + s[mid - 1]) / 2.0 if len(s) % 2 == 0 else s[mid]

    n = len(smoothed)
    for i in range(n):
        lo, hi = max(0, i - local_half), min(n, i + local_half + 1)
        window = smoothed[lo:hi]
        med = _median(window)
        mean = sum(window) / len(window)
        base = med + (mean - med) * (1.0 - sensitivity)
        thresholds.append(base * multiplier)
    return thresholds


def pick_onsets(
    energies: list[float],
    thresholds: list[float],
    min_gap_frames: int,
) -> list[int]:
    """Detect onset frames from energy + threshold arrays.

    A frame i is an onset if:
      - energies[i] > energies[i-1]      (rising)
      - energies[i] >= energies[i+1]     (local peak)
      - energies[i] > thresholds[i]      (above adaptive gate)
      - energies[i] - energies[i-1] > thresholds[i] * 0.3  (significant rise)
      - at least min_gap_frames since the last accepted onset (de-dupe)

    Returns
    -------
    list of frame indices (int) where onsets occur
    """
    onsets: list[int] = []
    n = len(energies)
    last = -min_gap_frames

    for i in range(1, n - 1):
        e = energies[i]
        prev = energies[i - 1]
        nxt = energies[i + 1]
        thr = thresholds[i]

        rising = e > prev
        peak = e >= nxt
        above = e > thr
        sharp = (e - prev) > thr * 0.3
        spaced = (i - last) >= min_gap_frames

        if rising and peak and above and sharp and spaced:
            onsets.append(i)
            last = i

    return onsets


def estimate_bpm(
    onset_times: Sequence[float],
    min_bpm: int = DEF_MIN_BPM,
    max_bpm: int = DEF_MAX_BPM,
) -> tuple[float, float]:
    """Estimate BPM from onset timestamps via IOI voting.

    For every consecutive pair of onsets the inter-onset interval (IOI) is
    used to generate candidate BPM values at full-beat, double-beat, and
    half-beat ratios. Votes are accumulated into a histogram and the
    highest-voted in-range candidate wins.

    Confidence = 1 - abs(expectedBeats - actualBeats) / expectedBeats
    where expectedBeats = duration * bpm / 60.

    Returns
    -------
    (bpm: float, confidence: float)  - bpm rounded to 1 decimal place.
    (0.0, 0.0) if fewer than 2 onsets are given.
    """
    times = list(onset_times)
    if len(times) < 2:
        return 0.0, 0.0

    intervals = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    votes: dict[int, float] = {}

    def _vote(bpm_f: float, weight: float) -> None:
        b = round(bpm_f)
        if min_bpm <= b <= max_bpm:
            votes[b] = votes.get(b, 0.0) + weight

    for ioi in intervals:
        if ioi <= 0:
            continue
        bpm_full = 60.0 / ioi
        _vote(bpm_full, _WEIGHT_FULL)
        _vote(bpm_full * 2, _WEIGHT_DOUBLE)
        _vote(bpm_full * 0.5, _WEIGHT_HALF)

    if not votes:
        return 0.0, 0.0

    best_bpm = max(votes, key=lambda b: votes[b])
    duration = times[-1] - times[0]
    expected = duration * best_bpm / 60.0
    actual = float(len(times))
    confidence = max(0.0, 1.0 - abs(expected - actual) / max(expected, 1.0))

    return round(float(best_bpm), 1), round(confidence, 3)


# --------------------------------------------------------------------------- #
# ffmpeg-backed analysis
# --------------------------------------------------------------------------- #

def _read_pcm_mono(path: str, sr: int = DEF_SR) -> list[float]:
    """Decode audio to mono f32le PCM at sr Hz via ffmpeg pipe.

    Returns a flat list of float32 samples.
    """
    cmd = [
        "ffmpeg", "-nostdin", "-i", str(path),
        "-ac", "1",
        "-ar", str(sr),
        "-f", "f32le",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True)
    raw = result.stdout
    n = len(raw) // 4  # 4 bytes per float32
    samples = list(struct.unpack(f"{n}f", raw[:n * 4]))
    return samples


def detect_beats(
    path: str,
    *,
    sr: int = DEF_SR,
    sensitivity: float = DEF_SENSITIVITY,
    win: int = DEF_WIN,
    hop: int = DEF_HOP,
    min_bpm: int = DEF_MIN_BPM,
    max_bpm: int = DEF_MAX_BPM,
) -> dict:
    """Detect musical beats in an audio file using ffmpeg + pure-Python DSP.

    Parameters
    ----------
    path        : path to any audio/video file ffmpeg can decode
    sr          : analysis sample rate in Hz (22050 recommended)
    sensitivity : 0.0 (strict) to 1.0 (loose), default 0.5
    win, hop    : frame size and hop in samples
    min_bpm, max_bpm : BPM search range

    Returns
    -------
    dict with keys:
      bpm         : float - estimated tempo
      confidence  : float in [0, 1]
      beats       : list[float] - beat timestamps in seconds, 3 decimals
      downbeats   : list[float] - every 4th beat (assumes 4/4 time)
    """
    samples = _read_pcm_mono(path, sr)
    if not samples:
        return {"bpm": 0.0, "confidence": 0.0, "beats": [], "downbeats": []}

    energies = rms_frames(samples, win, hop)
    thresholds = adaptive_threshold(energies, sensitivity)
    min_gap_frames = max(1, round(_MIN_ONSET_GAP_S * sr / hop))
    onset_frames = pick_onsets(energies, thresholds, min_gap_frames)

    hop_s = hop / sr
    onset_times = [round(f * hop_s, 3) for f in onset_frames]

    bpm, confidence = estimate_bpm(onset_times, min_bpm, max_bpm)
    downbeats = onset_times[::4]

    return {
        "bpm": bpm,
        "confidence": round(confidence, 3),
        "beats": onset_times,
        "downbeats": downbeats,
    }


# --------------------------------------------------------------------------- #
# Cutlist helper (pure given beats)
# --------------------------------------------------------------------------- #

def snap_cutlist_to_beats(cutlist: Cutlist, beats: list[float]) -> Cutlist:
    """Return a new Cutlist with cut offsets snapped to the nearest beat time.

    Each cut's TIMELINE offset is moved to the nearest beat. The source in/out
    points are adjusted proportionally to keep the cut's duration sane (capped
    at the original duration; the in-point tracks the offset shift so the
    right visual moment is preserved).

    This is a PURE function - no I/O. Pass the 'beats' list from detect_beats.

    Parameters
    ----------
    cutlist : the edit to reposition
    beats   : list of beat timestamps in seconds (from detect_beats)

    Returns
    -------
    A new Cutlist (does not mutate the original). Cuts retain their labels and
    clips; only offsets (and matching in_/out) are adjusted.
    """
    if not beats:
        return cutlist

    def _nearest_beat(t: float) -> float:
        closest = min(beats, key=lambda b: abs(b - t))
        return closest

    new_cuts: list[Cut] = []
    for cut in cutlist.cuts:
        snapped_offset = round(_nearest_beat(cut.offset), 3)
        delta = snapped_offset - cut.offset
        # Shift the source window by the same delta so the visual is preserved
        new_in = round(cut.in_ + delta, 3)
        new_out = round(cut.out + delta, 3)
        # Clamp in_ to non-negative; adjust out to keep the same duration
        if new_in < 0:
            new_out -= new_in
            new_in = 0.0
        new_cuts.append(Cut(
            clip=cut.clip,
            in_=round(new_in, 3),
            out=round(new_out, 3),
            offset=snapped_offset,
            label=cut.label,
        ))

    # Repack to remove any gaps / overlaps introduced by snapping
    new_cuts.sort(key=lambda c: c.offset)
    repacked: list[Cut] = []
    timeline_cursor = new_cuts[0].offset if new_cuts else 0.0
    for i, c in enumerate(new_cuts):
        if i > 0:
            # Avoid overlap: advance cursor if previous cut bleeds past this offset
            timeline_cursor = max(timeline_cursor, repacked[-1].offset
                                  + repacked[-1].duration)
        actual_offset = round(timeline_cursor if i > 0 else c.offset, 3)
        repacked.append(Cut(
            clip=c.clip,
            in_=c.in_,
            out=c.out,
            offset=actual_offset,
            label=c.label,
        ))
        timeline_cursor = actual_offset + c.duration

    total = round(repacked[-1].offset + repacked[-1].duration, 3) \
        if repacked else cutlist.total_duration_sec

    return replace(cutlist, cuts=repacked, total_duration_sec=total)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print("usage: python -m core.beats AUDIO [--sensitivity 0.5] [--sr 22050]")
        return 1

    path = argv[1]
    sensitivity = DEF_SENSITIVITY
    sr = DEF_SR
    if "--sensitivity" in argv:
        sensitivity = float(argv[argv.index("--sensitivity") + 1])
    if "--sr" in argv:
        sr = int(argv[argv.index("--sr") + 1])

    result = detect_beats(path, sr=sr, sensitivity=sensitivity)
    bpm = result["bpm"]
    conf = result["confidence"]
    beats = result["beats"]
    downbeats = result["downbeats"]

    print(f"file:        {path}")
    print(f"BPM:         {bpm}")
    print(f"confidence:  {conf:.3f}  ({'high' if conf >= 0.7 else 'medium' if conf >= 0.4 else 'low'})")
    print(f"beats found: {len(beats)}")
    print(f"downbeats:   {len(downbeats)}")
    print()
    print("beat times (first 20):")
    for t in beats[:20]:
        mark = " <-- downbeat" if t in downbeats else ""
        print(f"  {t:8.3f}s{mark}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
