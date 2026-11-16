"""core.beats - musical beat and BPM detection for music-driven montage.

Honest boundary: this is energy-onset detection, not a full-blown MIR solver.
It works well on clear-cut rhythmic tracks (Balkan brass, EDM, hip-hop, march);
it can mis-fire on polyrhythmic, rubato, or heavily-reverbed material. When
confidence is low (< 0.5) treat the BPM as approximate and review snapped cuts
by ear before rendering.

Pure, unit-tested core (no ffmpeg):
  rms_frames(samples, win, hop)           - RMS energy per analysis frame
  onset_envelope(energies)                - half-wave flux (note attacks)
  adaptive_threshold(energies, sensitivity) - per-frame adaptive gate
  pick_onsets(energies, thresholds, min_gap_frames) - onset candidates
  estimate_bpm(onset_times, min_bpm, max_bpm) -> (bpm, confidence)

Percussive accents (editor rule, Denis 16.11.2026 - needs numpy):
  hpss(samples, sr)                       - median-filter harmonic/percussive split
  percussive_accents(samples, sr)         - [(t, strength)] onsets of the drums only
  free_spans(accents, duration, min_gap_s) - spans with no percussive accent
  beat_strengths(beats, accents, period)  - "strong" | "weak" | "fill" per beat
  strong_beats(beats, strengths)          - beats 1 and 3 of every bar

ffmpeg-backed:
  detect_beats(path, *, sr, sensitivity)  - full analysis, returns dict
  analyze_samples(samples, sr, ...)       - the same analysis on PCM in memory

Cutlist helper (pure given beats):
  snap_cutlist_to_beats(cutlist, beats, strengths=None) - snap cut offsets to
                                          the nearest beat (strong beats only
                                          when strengths are given)

CLI:  python -m core.beats AUDIO [--sensitivity 0.5] [--sr 22050]

Ported from OpenReel Video (MIT) packages/core/src/audio/beat-detection-engine.ts
"""

from __future__ import annotations

import array
import bisect
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


def onset_envelope(energies: list[float]) -> list[float]:
    """Half-wave-rectified first difference of the energy curve (onset flux).

    Raw RMS over 75%-overlapping windows is too smooth for peak-picking on
    continuous music: a sustained loud passage never produces the sharp local
    rise pick_onsets needs, so detection finds zero onsets (verified on a real
    Balkan brass track). The positive frame-to-frame increase isolates note
    attacks, which is what we actually want. Same idea as a spectral-flux onset
    envelope, kept in the energy domain to stay dependency-free.

    Returns a list the same length as `energies` (first element is 0.0).
    """
    if not energies:
        return []
    out = [0.0]
    for i in range(1, len(energies)):
        d = energies[i] - energies[i - 1]
        out.append(d if d > 0.0 else 0.0)
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
# Percussive accents (Denis, 16.11.2026: "accents come from the drums only")
# --------------------------------------------------------------------------- #
#
# The full-mix onset envelope above also fires on a string swell or a pad
# entry. An editor cuts on the drums: a cut that lands on a violin swell reads
# as "off", and a strings-only passage has no accent to cut on at all, so a
# cut there is free. We split the spectrogram into harmonic (horizontal
# ridges: sustained notes) and percussive (vertical ridges: hits) parts with
# the median-filter HPSS of Fitzgerald (2010) and pick onsets on the
# percussive part only. numpy is required; scipy.ndimage is used when present
# (fast median filter), otherwise a chunked numpy median does the same job.

HPSS_N_FFT = 1024
HPSS_HOP = 256
HPSS_KERNEL = 17          # median kernel, bins (percussive) and frames (harmonic)
ACCENT_FLOOR = 0.10       # accent must reach this share of the loudest hit
STRONG_ACCENT = 0.5       # accent at this share of the loudest = "strong hit"
FILL_MIN_ACCENTS = 3      # this many accents inside the last beat = bar-end fill
MIN_ACCENTS_FOR_TEMPO = 4  # fewer percussive accents: tempo from the full mix


def _stft_mag(x, n_fft: int = HPSS_N_FFT, hop: int = HPSS_HOP):
    """Magnitude STFT, shape (bins, frames), centred frames (frame k at k*hop)."""
    np = _np
    x = np.asarray(x, dtype=np.float32)
    pad = n_fft // 2
    x = np.pad(x, (pad, pad))
    if len(x) < n_fft:
        return np.zeros((n_fft // 2 + 1, 0), dtype=np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(x, n_fft)[::hop]
    win = np.hanning(n_fft).astype(np.float32)
    return np.abs(np.fft.rfft(frames * win, axis=1)).T.astype(np.float32)


def _median_along(S, k: int, axis: int):
    """Median filter of odd length k along one axis (edges reflect-padded)."""
    np = _np
    try:
        from scipy.ndimage import median_filter  # type: ignore
        size = (1, k) if axis == 1 else (k, 1)
        return median_filter(S, size=size, mode="reflect")
    except ImportError:  # pragma: no cover - depends on the environment
        pass
    h = k // 2
    A = S if axis == 1 else S.T
    P = np.pad(A, ((0, 0), (h, h)), mode="symmetric")  # = scipy "reflect"
    out = np.empty_like(A)
    step = 128
    for r in range(0, A.shape[0], step):
        win = np.lib.stride_tricks.sliding_window_view(P[r:r + step], k, axis=1)
        out[r:r + step] = np.median(win, axis=2)
    return out if axis == 1 else out.T


def hpss(samples, sr: int = DEF_SR, *, n_fft: int = HPSS_N_FFT,
         hop: int = HPSS_HOP, kernel: int = HPSS_KERNEL):
    """Harmonic/percussive split of a magnitude spectrogram (soft Wiener masks).

    Returns (harmonic_mag, percussive_mag), each shape (bins, frames), frame k
    centred at k * hop / sr seconds. Requires numpy.
    """
    if not _HAS_NUMPY:  # pragma: no cover
        raise RuntimeError("core.beats.hpss needs numpy (pip install numpy)")
    np = _np
    S = _stft_mag(samples, n_fft, hop)
    if S.shape[1] == 0:
        return S, S
    H = _median_along(S, kernel, axis=1)   # smooth along time  -> sustained
    P = _median_along(S, kernel, axis=0)   # smooth along freq  -> hits
    H2, P2 = H * H, P * P
    den = H2 + P2 + 1e-10
    return S * (H2 / den), S * (P2 / den)


def percussive_accents(
    samples,
    sr: int = DEF_SR,
    *,
    hop: int = HPSS_HOP,
    sensitivity: float = DEF_SENSITIVITY,
    floor: float = ACCENT_FLOOR,
) -> list[tuple[float, float]]:
    """Onsets of the percussive part only: [(time_s, strength 0..1)].

    Spectral flux of the log-compressed percussive spectrogram, peak-picked
    with the same adaptive gate as the full-mix detector, plus an absolute
    floor (`floor` x the loudest hit) so a quiet passage with no drums yields
    no accents instead of noise. Strength is relative to the loudest hit.
    """
    if not _HAS_NUMPY:  # pragma: no cover
        return []
    np = _np
    _, P = hpss(samples, sr, hop=hop)
    if P.shape[1] < 3:
        return []
    L = np.log1p(100.0 * P)
    flux = np.maximum(np.diff(L, axis=1), 0.0).sum(axis=0)
    flux = np.concatenate([[0.0], flux])
    peak = float(flux.max())
    if peak <= 0:
        return []
    env = [float(v) for v in flux]
    thr = adaptive_threshold(env, sensitivity)
    thr = [max(t, floor * peak) for t in thr]
    min_gap = max(1, round(_MIN_ONSET_GAP_S * sr / hop))
    frames = pick_onsets(env, thr, min_gap)
    # the last window runs into the zero padding: a sound cut off by the end
    # of the file splatters across the spectrum and reads as a hit there
    last = (len(samples) - HPSS_N_FFT) / sr
    return [(round(f * hop / sr, 3), round(env[f] / peak, 3)) for f in frames
            if f * hop / sr <= last]


def free_spans(
    accents: Sequence[float] | Sequence[tuple[float, float]],
    duration: float,
    min_gap_s: float = 1.5,
) -> list[tuple[float, float]]:
    """Spans with no percussive accent, at least min_gap_s long.

    A cut inside a free span is free: there is no drum hit for it to land on
    (a strings-only passage). Bounds are the accents around the gap (or 0 /
    duration at the ends), so a cut exactly on the bounding accent still
    counts as on-accent.
    """
    ts = sorted(_accent_times(accents))
    edges = [0.0, *ts, float(duration)]
    out = []
    for a, b in zip(edges, edges[1:]):
        if b - a >= min_gap_s:
            out.append((round(a, 3), round(b, 3)))
    return out


def beat_strengths(
    beats: Sequence[float],
    accents: Sequence[float] | Sequence[tuple[float, float]] = (),
    period: float | None = None,
    fill_min: int = FILL_MIN_ACCENTS,
) -> list[str]:
    """Label every beat of the grid: "strong" | "weak" | "fill".

    Beats are grouped in bars of 4 starting at beats[0] (the same phase as
    `downbeats = beats[::4]`). Beats 1 and 3 of a bar are strong, 2 and 4
    weak. Beat 4 becomes "fill" when it carries `fill_min`+ percussive
    accents AND more than any of beats 1-3 of the same bar (the drum break at
    the end of a bar; a steady hi-hat that is just as dense on every beat is
    not a fill). In a fast series you cut on 1 and 3 and let the fill run.
    """
    bl = list(beats)
    if not bl:
        return []
    if period is None:
        period = (bl[-1] - bl[0]) / (len(bl) - 1) if len(bl) > 1 else 0.5
    ts = sorted(_accent_times(accents))
    eps = period * 0.15

    def _count(b: float) -> int:
        return (bisect.bisect_left(ts, b + period - eps)
                - bisect.bisect_left(ts, b - eps))

    out = []
    for i, b in enumerate(bl):
        pos = i % 4
        if pos in (0, 2):
            out.append("strong")
            continue
        if pos == 3 and ts:
            n4 = _count(b)
            others = [_count(x) for x in bl[i - 3:i]]
            if n4 >= fill_min and n4 > max(others, default=0):
                out.append("fill")
                continue
        out.append("weak")
    return out


def strong_beats(beats: Sequence[float], strengths: Sequence[str]) -> list[float]:
    """The beats labelled "strong" (1 and 3 of every bar): fast-series targets."""
    return [b for b, s in zip(beats, strengths) if s == "strong"]


def _accent_times(accents) -> list[float]:
    """Accept [t, ...] or [(t, strength), ...]; return the times."""
    return [float(a[0]) if isinstance(a, (tuple, list)) else float(a)
            for a in accents]


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


def _beat_grid(onset_times: list[float], bpm: float, duration: float) -> list[float]:
    """Even tempo grid at `bpm`, phase-aligned to the first detected onset.

    For cutting to music we want regular BEAT times, not every note onset.
    Build a steady grid from the BPM and lock its phase to the first onset,
    extended back toward 0 so the whole track is covered.
    """
    if bpm <= 0 or not onset_times or duration <= 0:
        return []
    period = 60.0 / bpm
    t0 = onset_times[0]
    start = t0 - period * math.floor(t0 / period)
    beats: list[float] = []
    t = start
    while t <= duration + 1e-6:
        beats.append(round(t, 3))
        t += period
    return beats


def _grid_confidence(beats: list[float], onset_times: list[float],
                     period: float) -> float:
    """Fraction of grid beats with a detected onset within +-15% of a beat.

    An honest tempo-lock score: high when real note attacks land on the grid.
    Replaces OpenReel's onset-count ratio, which scored a correct BPM as "low"
    whenever music had more note onsets than beats (the normal case).
    """
    if not beats or not onset_times or period <= 0:
        return 0.0
    tol = period * 0.15
    ot = sorted(onset_times)
    hits = 0
    for b in beats:
        i = bisect.bisect_left(ot, b)
        if any(0 <= j < len(ot) and abs(ot[j] - b) <= tol for j in (i - 1, i)):
            hits += 1
    return round(hits / len(beats), 3)


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

    Returns the dict of `analyze_samples`.
    """
    samples = _read_pcm_mono(path, sr)
    return analyze_samples(samples, sr, sensitivity=sensitivity, win=win,
                           hop=hop, min_bpm=min_bpm, max_bpm=max_bpm)


def analyze_samples(
    samples,
    sr: int = DEF_SR,
    *,
    sensitivity: float = DEF_SENSITIVITY,
    win: int = DEF_WIN,
    hop: int = DEF_HOP,
    min_bpm: int = DEF_MIN_BPM,
    max_bpm: int = DEF_MAX_BPM,
) -> dict:
    """Beat analysis of mono PCM already in memory (no ffmpeg).

    Returns
    -------
    dict with keys:
      bpm           : float - estimated tempo (full-mix onsets, as before)
      confidence    : float in [0, 1] - tempo-lock score (onsets on the grid)
      beats         : list[float] - even beat grid at the BPM, 3 decimals
      downbeats     : list[float] - every 4th beat (assumes 4/4 time)
      onsets        : list[float] - raw full-mix note attacks (for inspection)
      accents       : list[(t, strength)] - percussive onsets only (cut targets)
      beat_strength : list[str] - "strong" | "weak" | "fill", one per beat
      free_spans    : list[(start, end)] - no percussive accent: cuts are free

    Tempo and grid phase come from the percussive accents when there are at
    least MIN_ACCENTS_FOR_TEMPO of them (a pad entering before the drums, or
    a string passage, no longer shifts or bends the grid), otherwise from the
    full-mix onsets as before. Without numpy, accents and
    free_spans are empty and every beat is labelled by bar position only.
    """
    if samples is None or len(samples) == 0:
        return {"bpm": 0.0, "confidence": 0.0, "beats": [],
                "downbeats": [], "onsets": [], "accents": [],
                "beat_strength": [], "free_spans": []}

    energies = rms_frames(samples, win, hop)
    # Detect on the onset-flux envelope, not raw RMS: continuous music is too
    # smooth for peak-picking on RMS directly (it yields zero onsets).
    flux = onset_envelope(energies)
    thresholds = adaptive_threshold(flux, sensitivity)
    min_gap_frames = max(1, round(_MIN_ONSET_GAP_S * sr / hop))
    onset_frames = pick_onsets(flux, thresholds, min_gap_frames)

    hop_s = hop / sr
    onset_times = [round(f * hop_s, 3) for f in onset_frames]

    accents = percussive_accents(samples, sr, sensitivity=sensitivity) \
        if _HAS_NUMPY else []
    accent_times = [t for t, _ in accents]

    pulse = accent_times if len(accent_times) >= MIN_ACCENTS_FOR_TEMPO \
        else onset_times
    bpm, _ = estimate_bpm(pulse, min_bpm, max_bpm)
    duration = len(samples) / sr
    beats = _beat_grid(pulse, bpm, duration)
    period = 60.0 / bpm if bpm > 0 else 0.0
    confidence = _grid_confidence(beats, pulse, period)
    downbeats = beats[::4]

    return {
        "bpm": bpm,
        "confidence": confidence,
        "beats": beats,
        "downbeats": downbeats,
        "onsets": onset_times,
        "accents": accents,
        "beat_strength": beat_strengths(beats, accents, period or None),
        "free_spans": free_spans(accents, duration,
                                 max(1.5, 2.0 * period)) if accents else
        ([(0.0, round(duration, 3))] if _HAS_NUMPY else []),
    }


# --------------------------------------------------------------------------- #
# Cutlist helper (pure given beats)
# --------------------------------------------------------------------------- #

def snap_cutlist_to_beats(cutlist: Cutlist, beats: list[float],
                          strengths: Sequence[str] | None = None) -> Cutlist:
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

    strengths : optional per-beat labels from `beat_strengths`. When given,
    only "strong" beats (1 and 3 of a bar) are snap targets: the rule for a
    fast series (Denis, 16.11.2026) - weak beats and the bar-end fill are
    skipped.
    """
    if strengths is not None:
        beats = strong_beats(beats, strengths)
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
