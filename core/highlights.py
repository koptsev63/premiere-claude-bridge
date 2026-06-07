"""core.highlights - propose the best moments of footage for a short version.

Given a media file (and an optional Whisper transcript), this module:
1. Windows the audio into fixed-size segments (default 5s, configurable).
2. Extracts per-window features: rms_db, peak_db, is_silent, and if a
   transcript is provided speech_rate (words/sec) and has_speech.
3. Scores each window as a weighted combination of normalised energy and
   normalised speech density (silent windows score 0).
4. Greedily selects the top windows to fill a target total duration (default
   75s), keeping chronological order and enforcing a minimum gap so picks
   don't clump together.
5. Optionally snaps window boundaries to ffmpeg-detected scene cuts so every
   chosen moment starts and ends on a clean visual edit.
6. Returns a Cutlist of Cut objects that drops straight into the existing
   render path.

Honest boundary: this is a loudness + speech-density heuristic, not
semantic taste. A window scores high when it is LOUD and/or DENSE WITH
WORDS - not because the words are interesting. Pair it with the meaning
pass (read the transcript, remove the boring-but-loud bits); this tool
proposes a shortlist, not a finished cut.

Metric idea attributed to OpenReel Video (MIT)
(packages/core/src/audio/highlight-analyzer.ts) - per-window RMS/peak/
silence/speech-rate features. Scoring and selection logic are our own.

Pure, unit-tested core (no ffmpeg):
  score_windows(features, ...)    - [(window_dict, score)]
  select_highlights(scored, ...)  - chosen windows in time order
ffmpeg-backed:
  analyze(path, ...)              - list of window feature dicts
  scene_cuts(path, ...)           - [t, ...] cut timestamps
  auto_highlights(path, ...)      - Cutlist ready to render

CLI: python -m core.highlights CLIP [--target 75] [--transcript t.json]
                                     [--window 5] [--no-snap]
"""

from __future__ import annotations

import array
import json
import math
import re
import struct
import subprocess
import sys
from typing import Any

from core.cutlist import Cut, Cutlist

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEF_WINDOW_S = 5.0          # analysis window length (seconds)
DEF_HOP_S = 2.5             # hop / overlap between windows (seconds)
DEF_SILENCE_DB = -60.0      # peak below this -> window is silent
DEF_W_ENERGY = 0.6          # weight for RMS energy in composite score
DEF_W_SPEECH = 0.4          # weight for speech-rate density
DEF_TARGET_S = 75.0         # default teaser total duration
DEF_MIN_CLIP_S = 2.0        # minimum selected window duration
DEF_MAX_CLIP_S = 8.0        # maximum selected window duration
DEF_MIN_GAP_S = 3.0         # minimum gap between selected windows
DEF_SCENE_THRESH = 0.3      # ffmpeg scene-change threshold
SAMPLE_RATE = 16000         # PCM sample rate for energy extraction
_LABEL_PREFIX = "HIGHLIGHT"

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _linear_to_db(linear: float) -> float:
    """Convert a linear amplitude to dBFS (negative values below 0 dBFS)."""
    if linear <= 0.0:
        return -120.0
    return 20.0 * math.log10(linear)


def _normalize(values: list[float]) -> list[float]:
    """Min-max normalise a list of floats to [0, 1].

    When all values are equal (span < epsilon) every value is the best
    available, so they all get 1.0 rather than 0.0. This ensures that
    a single-window input or a perfectly-even set still scores non-zero
    and is therefore selectable.
    """
    lo = min(values)
    hi = max(values)
    span = hi - lo
    if span < 1e-9:
        return [1.0] * len(values)
    return [(v - lo) / span for v in values]


def score_windows(
    features: list[dict[str, Any]],
    *,
    w_energy: float = DEF_W_ENERGY,
    w_speech: float = DEF_W_SPEECH,
) -> list[tuple[dict[str, Any], float]]:
    """Assign a composite score to each analysis window (pure, no I/O).

    Parameters
    ----------
    features:
        List of dicts as returned by ``analyze()``. Each dict must have
        ``rms_db``, ``peak_db``, ``is_silent``. Optional keys:
        ``speech_rate`` (words/sec), ``has_speech``.
    w_energy:
        Weight (0-1) for normalised RMS energy.
    w_speech:
        Weight (0-1) for normalised speech density.

    Returns
    -------
    List of (window_dict, score) in input order.
    Silent windows always score 0.0 regardless of weights.
    Scores are in [0.0, 1.0].
    """
    if not features:
        return []

    rms_vals = [f["rms_db"] for f in features]
    speech_vals = [f.get("speech_rate", 0.0) for f in features]

    norm_rms = _normalize(rms_vals)
    norm_speech = _normalize(speech_vals)

    results: list[tuple[dict[str, Any], float]] = []
    for i, feat in enumerate(features):
        if feat.get("is_silent", False):
            results.append((feat, 0.0))
            continue
        score = w_energy * norm_rms[i] + w_speech * norm_speech[i]
        # cap to 1.0 if weights sum beyond 1
        score = min(score, 1.0)
        results.append((feat, round(score, 6)))

    return results


def select_highlights(
    scored: list[tuple[dict[str, Any], float]],
    *,
    target_s: float = DEF_TARGET_S,
    min_clip_s: float = DEF_MIN_CLIP_S,
    max_clip_s: float = DEF_MAX_CLIP_S,
    min_gap_s: float = DEF_MIN_GAP_S,
) -> list[dict[str, Any]]:
    """Greedy selection of highest-scoring windows to fill target_s (pure).

    Rules:
    - Sort by score descending, pick greedily while total < target_s.
    - Each window is clipped to [min_clip_s, max_clip_s].
    - Windows whose duration after clipping is < min_clip_s are skipped.
    - After selection, the chosen windows are sorted back to chronological order.
    - A window is rejected if its start is within min_gap_s of any already
      selected window's start (anti-clumping).

    Returns the chosen window dicts in chronological order.
    """
    # filter zero-score (silent) windows
    candidates = [(w, s) for w, s in scored if s > 0.0]
    # sort by score descending
    candidates.sort(key=lambda x: x[1], reverse=True)

    chosen: list[dict[str, Any]] = []
    chosen_starts: list[float] = []
    total = 0.0

    for window, _score in candidates:
        if total >= target_s:
            break

        raw_start = window["start"]
        raw_end = window["end"]
        dur = raw_end - raw_start

        # clamp duration
        dur = max(min_clip_s, min(dur, max_clip_s))
        if dur < min_clip_s:
            continue

        # anti-clumping: skip if too close to any already-chosen window
        too_close = any(abs(raw_start - cs) < min_gap_s
                        for cs in chosen_starts)
        if too_close:
            continue

        # accept
        w_copy = dict(window)
        w_copy["end"] = round(raw_start + dur, 3)
        chosen.append(w_copy)
        chosen_starts.append(raw_start)
        total += dur

    # restore chronological order
    chosen.sort(key=lambda w: w["start"])
    return chosen


# ---------------------------------------------------------------------------
# ffmpeg helpers - audio energy extraction (pure PCM, no librosa)
# ---------------------------------------------------------------------------


def _probe_duration(path: str) -> float:
    """Return media duration in seconds via ffprobe."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    try:
        return float((r.stdout or "0").strip())
    except ValueError:
        return 0.0


def _read_pcm_f32(path: str) -> array.array:
    """Decode audio to mono 16 kHz f32le PCM via ffmpeg, return array.array."""
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-i", path,
         "-ac", "1", "-ar", str(SAMPLE_RATE),
         "-f", "f32le", "-"],
        capture_output=True,
    )
    if not r.stdout:
        return array.array("f")
    # array of floats from raw bytes
    n_floats = len(r.stdout) // 4
    samples = array.array("f")
    samples.frombytes(r.stdout[: n_floats * 4])
    return samples


def _window_energy(
    samples: array.array,
    *,
    window_s: float,
    hop_s: float,
    duration: float,
) -> list[dict[str, Any]]:
    """Compute RMS and peak per window from f32 PCM samples.

    Returns list of dicts with: start, end, rms_db, peak_db, is_silent.
    """
    sr = SAMPLE_RATE
    win_n = int(window_s * sr)
    hop_n = int(hop_s * sr)
    n = len(samples)
    results: list[dict[str, Any]] = []

    offset = 0
    while offset < n:
        end_offset = min(offset + win_n, n)
        chunk = samples[offset:end_offset]
        if not chunk:
            break

        # RMS via sum of squares (pure Python)
        sum_sq = sum(v * v for v in chunk)
        rms_linear = math.sqrt(sum_sq / len(chunk))
        peak_linear = max(abs(v) for v in chunk)

        rms_db = round(_linear_to_db(rms_linear), 3)
        peak_db = round(_linear_to_db(peak_linear), 3)
        is_silent = peak_db < DEF_SILENCE_DB

        t_start = round(offset / sr, 3)
        t_end = round(min(end_offset / sr, duration), 3)

        results.append({
            "start": t_start,
            "end": t_end,
            "rms_db": rms_db,
            "peak_db": peak_db,
            "is_silent": is_silent,
        })

        if end_offset >= n:
            break
        offset += hop_n

    return results


def _enrich_with_transcript(
    windows: list[dict[str, Any]],
    transcript: dict[str, Any],
) -> list[dict[str, Any]]:
    """Add speech_rate (words/sec) and has_speech to each window dict.

    Uses Whisper-shaped transcript {"segments":[{start, end, text, words:[]}]}.
    Words are matched by their start timestamp falling within the window.
    Falls back to segment overlap when no word-level timestamps exist.
    """
    # collect all words with timestamps
    word_times: list[tuple[float, float]] = []  # (start, end)
    for seg in transcript.get("segments", []):
        for w in seg.get("words", []) or []:
            ws = w.get("start")
            we = w.get("end")
            if ws is not None and we is not None:
                word_times.append((float(ws), float(we)))

    if not word_times:
        # fall back: count words per overlapping segment
        segs = transcript.get("segments", [])
        for win in windows:
            ws, we = win["start"], win["end"]
            word_count = 0
            for seg in segs:
                ss, se = float(seg.get("start", 0)), float(seg.get("end", 0))
                # overlap
                overlap = max(0.0, min(we, se) - max(ws, ss))
                if overlap > 0:
                    text = seg.get("text", "")
                    n_words = len(text.split())
                    # prorate by overlap fraction
                    seg_dur = max(se - ss, 1e-9)
                    word_count += n_words * overlap / seg_dur
            dur = max(we - ws, 1e-9)
            win["speech_rate"] = round(word_count / dur, 3)
            win["has_speech"] = word_count > 0
        return windows

    # word-level path
    for win in windows:
        ws, we = win["start"], win["end"]
        count = sum(1 for wt_s, wt_e in word_times if ws <= wt_s < we)
        dur = max(we - ws, 1e-9)
        win["speech_rate"] = round(count / dur, 3)
        win["has_speech"] = count > 0

    return windows


# ---------------------------------------------------------------------------
# Public ffmpeg-backed functions
# ---------------------------------------------------------------------------


def analyze(
    path: str,
    *,
    window_s: float = DEF_WINDOW_S,
    hop_s: float | None = None,
    transcript: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Analyse audio, return per-window feature dicts.

    Each dict contains: start, end, rms_db, peak_db, is_silent,
    and (if transcript given) speech_rate, has_speech.

    No external deps beyond stdlib + ffmpeg in PATH.
    numpy is used if available (faster), else falls back to pure Python.
    """
    if hop_s is None:
        hop_s = window_s / 2.0

    duration = _probe_duration(path)
    if duration <= 0:
        duration = window_s  # best-effort fallback

    try:
        import numpy as np  # type: ignore
        _analyze_numpy = True
    except ImportError:
        _analyze_numpy = False

    if _analyze_numpy:
        windows = _window_energy_numpy(path, window_s=window_s,
                                       hop_s=hop_s, duration=duration)
    else:
        samples = _read_pcm_f32(path)
        windows = _window_energy(samples, window_s=window_s,
                                 hop_s=hop_s, duration=duration)

    if transcript is not None:
        windows = _enrich_with_transcript(windows, transcript)
    else:
        for w in windows:
            w.setdefault("speech_rate", 0.0)
            w.setdefault("has_speech", False)

    return windows


def _window_energy_numpy(
    path: str,
    *,
    window_s: float,
    hop_s: float,
    duration: float,
) -> list[dict[str, Any]]:
    """numpy-accelerated version of _window_energy (called only if numpy present)."""
    import numpy as np  # type: ignore

    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-i", path,
         "-ac", "1", "-ar", str(SAMPLE_RATE),
         "-f", "f32le", "-"],
        capture_output=True,
    )
    raw = r.stdout or b""
    n_floats = len(raw) // 4
    samples = np.frombuffer(raw[: n_floats * 4], dtype=np.float32)

    sr = SAMPLE_RATE
    win_n = int(window_s * sr)
    hop_n = int(hop_s * sr)
    n = len(samples)
    results: list[dict[str, Any]] = []
    offset = 0

    while offset < n:
        end_offset = min(offset + win_n, n)
        chunk = samples[offset:end_offset]
        if chunk.size == 0:
            break
        rms_linear = float(np.sqrt(np.mean(chunk ** 2)))
        peak_linear = float(np.max(np.abs(chunk)))
        rms_db = round(_linear_to_db(rms_linear), 3)
        peak_db = round(_linear_to_db(peak_linear), 3)
        is_silent = peak_db < DEF_SILENCE_DB
        t_start = round(offset / sr, 3)
        t_end = round(min(end_offset / sr, duration), 3)
        results.append({
            "start": t_start,
            "end": t_end,
            "rms_db": rms_db,
            "peak_db": peak_db,
            "is_silent": is_silent,
        })
        if end_offset >= n:
            break
        offset += hop_n

    return results


def scene_cuts(path: str, *, threshold: float = DEF_SCENE_THRESH) -> list[float]:
    """Detect scene cuts via ffmpeg select filter. Returns sorted list of timestamps."""
    cmd = [
        "ffmpeg", "-nostdin", "-i", path,
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-an", "-f", "null", "-",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    times: list[float] = []
    # showinfo prints "pts_time:X.XXX" in stderr
    for m in re.finditer(r"pts_time:([0-9.]+)", r.stderr or ""):
        times.append(round(float(m.group(1)), 3))
    return sorted(set(times))


def _snap_to_cuts(
    start: float,
    end: float,
    cuts: list[float],
    snap_radius_s: float = 1.0,
) -> tuple[float, float]:
    """Snap start/end to the nearest scene cut within snap_radius_s."""
    def nearest(t: float) -> float:
        if not cuts:
            return t
        best = min(cuts, key=lambda c: abs(c - t))
        if abs(best - t) <= snap_radius_s:
            return best
        return t

    return nearest(start), nearest(end)


def auto_highlights(
    path: str,
    *,
    target_s: float = DEF_TARGET_S,
    window_s: float = DEF_WINDOW_S,
    transcript: dict[str, Any] | None = None,
    snap_to_cuts: bool = True,
    snap_radius_s: float = 1.0,
    w_energy: float = DEF_W_ENERGY,
    w_speech: float = DEF_W_SPEECH,
    min_clip_s: float = DEF_MIN_CLIP_S,
    max_clip_s: float = DEF_MAX_CLIP_S,
    min_gap_s: float = DEF_MIN_GAP_S,
    clip: str | None = None,
    sequence_name: str = "Auto_Highlights",
    fps: float = 25.0,
) -> Cutlist:
    """Full pipeline: analyze -> score -> select -> snap -> Cutlist.

    Parameters
    ----------
    path:
        Input media file path.
    target_s:
        Desired total duration of the highlight reel (seconds).
    window_s:
        Analysis window size (seconds).
    transcript:
        Whisper-shaped transcript dict for speech-density scoring.
    snap_to_cuts:
        If True, detect scene cuts and snap chosen window boundaries to them.
    snap_radius_s:
        Maximum distance (seconds) to snap a boundary to a scene cut.
    w_energy, w_speech:
        Scoring weights (must sum <= 1.0; common sense defaults: 0.6 + 0.4).
    min_clip_s, max_clip_s:
        Minimum and maximum duration per chosen clip.
    min_gap_s:
        Minimum gap between chosen clips (anti-clumping).
    clip:
        Override the clip field in the returned Cut objects. Defaults to path.
    sequence_name, fps:
        Cutlist metadata.

    Returns
    -------
    Cutlist with one Cut per selected highlight, chronological, packed
    contiguously on the timeline starting at offset 0.
    """
    clip_ref = clip if clip is not None else path

    features = analyze(path, window_s=window_s, transcript=transcript)
    scored = score_windows(features, w_energy=w_energy, w_speech=w_speech)
    chosen = select_highlights(
        scored,
        target_s=target_s,
        min_clip_s=min_clip_s,
        max_clip_s=max_clip_s,
        min_gap_s=min_gap_s,
    )

    cuts_data = []
    if snap_to_cuts and chosen:
        cuts_ts = scene_cuts(path)
    else:
        cuts_ts = []

    for win in chosen:
        s, e = win["start"], win["end"]
        if cuts_ts:
            s, e = _snap_to_cuts(s, e, cuts_ts, snap_radius_s)
            # guard: ensure min duration after snap
            if e - s < min_clip_s:
                e = s + min_clip_s
        cuts_data.append((round(s, 3), round(e, 3)))

    # pack on timeline
    timeline_cuts: list[Cut] = []
    offset = 0.0
    for i, (s, e) in enumerate(cuts_data):
        dur = round(e - s, 3)
        timeline_cuts.append(Cut(
            clip=clip_ref,
            in_=s,
            out=e,
            offset=round(offset, 3),
            label=f"{_LABEL_PREFIX}_{i + 1:02d}",
        ))
        offset = round(offset + dur, 3)

    return Cutlist(
        sequence_name=sequence_name,
        fps=fps,
        cuts=timeline_cuts,
        total_duration_sec=round(offset, 3),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print("usage: python -m core.highlights CLIP "
              "[--target 75] [--transcript t.json] [--window 5] [--no-snap]")
        return 0

    path = argv[1]
    target_s = float(argv[argv.index("--target") + 1]) \
        if "--target" in argv else DEF_TARGET_S
    window_s = float(argv[argv.index("--window") + 1]) \
        if "--window" in argv else DEF_WINDOW_S
    snap = "--no-snap" not in argv

    transcript: dict[str, Any] | None = None
    if "--transcript" in argv:
        t_path = argv[argv.index("--transcript") + 1]
        with open(t_path) as fh:
            transcript = json.load(fh)

    print(f"Analysing: {path}")
    print(f"Target duration: {target_s}s | window: {window_s}s | "
          f"snap-to-cuts: {snap}")

    cl = auto_highlights(
        path,
        target_s=target_s,
        window_s=window_s,
        transcript=transcript,
        snap_to_cuts=snap,
    )

    total = 0.0
    for cut in cl.cuts:
        dur = cut.out - cut.in_
        total += dur

    print(f"\nSelected {len(cl.cuts)} moments, {total:.1f}s total:")
    for cut in cl.cuts:
        dur = cut.out - cut.in_
        print(f"  {cut.label:20s}  {cut.in_:7.2f}s -> {cut.out:7.2f}s  "
              f"({dur:.1f}s)  timeline_offset={cut.offset:.2f}s")

    errs = cl.validate()
    if errs:
        print("WARNING: Cutlist validation errors:")
        for e in errs:
            print(f"  {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
