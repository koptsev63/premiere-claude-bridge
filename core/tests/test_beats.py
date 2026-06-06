"""Tests for beat detection logic (pure, no ffmpeg).

Run:  python -m core.tests.test_beats
"""

from __future__ import annotations

import math
import sys

from core.beats import (
    adaptive_threshold,
    estimate_bpm,
    pick_onsets,
    rms_frames,
    snap_cutlist_to_beats,
)
from core.cutlist import Cut, Cutlist

_p = _f = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


# --------------------------------------------------------------------------- #
# rms_frames
# --------------------------------------------------------------------------- #


def test_rms_frames_length() -> None:
    """Length formula: (n - win) // hop + 1 frames."""
    samples = [0.5] * 4096
    frames = rms_frames(samples, win=2048, hop=512)
    expected_len = (len(samples) - 2048) // 512 + 1
    check("rms length formula", len(frames) == expected_len,
          f"got {len(frames)}, expected {expected_len}")


def test_rms_frames_dc_signal() -> None:
    """RMS of a constant signal == abs(constant)."""
    samples = [0.3] * 8192
    frames = rms_frames(samples, win=2048, hop=512)
    ok = all(abs(v - 0.3) < 1e-4 for v in frames)
    check("rms dc signal == constant amplitude", ok,
          f"values: {frames[:3]}")


def test_rms_frames_silence() -> None:
    """RMS of zeros == 0."""
    samples = [0.0] * 8192
    frames = rms_frames(samples, win=2048, hop=512)
    check("rms silence == 0", all(v == 0.0 for v in frames),
          f"non-zero found: {[v for v in frames if v != 0.0][:3]}")


def test_rms_frames_too_short() -> None:
    """Fewer samples than win -> empty list."""
    frames = rms_frames([0.1] * 100, win=2048, hop=512)
    check("rms too short -> empty", frames == [], str(frames))


def test_rms_frames_sine() -> None:
    """RMS of a full-scale sine should be ~1/sqrt(2) ~ 0.707."""
    n = 44100
    samples = [math.sin(2 * math.pi * 440 * i / n) for i in range(n)]
    frames = rms_frames(samples, win=2048, hop=512)
    expected = 1.0 / math.sqrt(2)
    ok = all(abs(v - expected) < 0.02 for v in frames)
    check("rms sine ~= 1/sqrt(2)", ok,
          f"sample values: {frames[:3]}, expected ~{expected:.4f}")


# --------------------------------------------------------------------------- #
# adaptive_threshold
# --------------------------------------------------------------------------- #


def test_adaptive_threshold_length() -> None:
    energies = [0.1 * (i % 10) for i in range(200)]
    thr = adaptive_threshold(energies, sensitivity=0.5)
    check("adaptive_threshold same length", len(thr) == len(energies),
          f"got {len(thr)}")


def test_adaptive_threshold_empty() -> None:
    thr = adaptive_threshold([], sensitivity=0.5)
    check("adaptive_threshold empty -> []", thr == [], str(thr))


def test_adaptive_threshold_stricter_at_low_sensitivity() -> None:
    """Lower sensitivity -> higher threshold (more conservative gating)."""
    energies = [0.1 + 0.05 * math.sin(i) for i in range(200)]
    thr_low = adaptive_threshold(energies, sensitivity=0.1)
    thr_high = adaptive_threshold(energies, sensitivity=0.9)
    avg_low = sum(thr_low) / len(thr_low)
    avg_high = sum(thr_high) / len(thr_high)
    check("lower sensitivity -> higher avg threshold", avg_low > avg_high,
          f"avg_low={avg_low:.4f}, avg_high={avg_high:.4f}")


# --------------------------------------------------------------------------- #
# pick_onsets
# --------------------------------------------------------------------------- #


def test_pick_onsets_basic() -> None:
    """A sharp energy spike above threshold should register as one onset."""
    energies = [0.1] * 100
    # inject a spike at frame 50
    energies[49] = 0.11
    energies[50] = 0.9
    energies[51] = 0.11
    thresholds = [0.2] * 100
    onsets = pick_onsets(energies, thresholds, min_gap_frames=5)
    check("spike detected as onset", 50 in onsets, f"onsets={onsets}")


def test_pick_onsets_dedup() -> None:
    """Two spikes closer than min_gap_frames -> only first accepted."""
    energies = [0.1] * 100
    for i in [30, 31, 32]:
        energies[i] = 0.8
    thresholds = [0.2] * 100
    onsets = pick_onsets(energies, thresholds, min_gap_frames=20)
    check("de-dupe: max 1 onset per gap window", len(onsets) <= 1,
          f"onsets={onsets}")


def test_pick_onsets_no_false_positive_on_flat() -> None:
    """Flat signal should produce no onsets."""
    energies = [0.5] * 200
    thresholds = [0.3] * 200
    onsets = pick_onsets(energies, thresholds, min_gap_frames=10)
    check("flat signal -> no onsets", onsets == [], f"onsets={onsets}")


# --------------------------------------------------------------------------- #
# estimate_bpm - CORE SYNTHETIC TEST (specification requirement)
# --------------------------------------------------------------------------- #


def test_estimate_bpm_120_synthetic() -> None:
    """Perfectly periodic 120 BPM (0.5s between beats, 20 beats) -> ~120 BPM."""
    interval = 0.5          # 120 BPM
    onset_times = [i * interval for i in range(20)]
    bpm, confidence = estimate_bpm(onset_times, min_bpm=60, max_bpm=200)
    check("120 BPM synthetic: correct BPM",
          abs(bpm - 120.0) < 2.0,
          f"bpm={bpm}")
    check("120 BPM synthetic: high confidence",
          confidence >= 0.7,
          f"confidence={confidence}")


def test_estimate_bpm_100_synthetic() -> None:
    """Perfectly periodic 100 BPM (0.6s per beat, 30 beats) -> ~100 BPM."""
    onset_times = [i * 0.6 for i in range(30)]
    bpm, confidence = estimate_bpm(onset_times, min_bpm=60, max_bpm=200)
    check("100 BPM synthetic: correct BPM",
          abs(bpm - 100.0) < 2.0,
          f"bpm={bpm}")
    check("100 BPM synthetic: high confidence",
          confidence >= 0.6,
          f"confidence={confidence}")


def test_estimate_bpm_insufficient_onsets() -> None:
    """Fewer than 2 onsets -> (0.0, 0.0)."""
    bpm, conf = estimate_bpm([1.0])
    check("single onset -> 0 BPM", bpm == 0.0 and conf == 0.0,
          f"bpm={bpm}, conf={conf}")


def test_estimate_bpm_empty() -> None:
    bpm, conf = estimate_bpm([])
    check("empty onsets -> 0 BPM", bpm == 0.0 and conf == 0.0,
          f"bpm={bpm}, conf={conf}")


def test_estimate_bpm_range_clamp() -> None:
    """Onsets at 200 ms (300 BPM) with max_bpm=200 -> no valid candidate."""
    onset_times = [i * 0.2 for i in range(20)]
    bpm, conf = estimate_bpm(onset_times, min_bpm=60, max_bpm=200)
    # 300 BPM is out of range; half (150) and double (600) may or may not land
    # We just assert the returned BPM is in range or is 0 (no candidate)
    check("out-of-range BPM: result in range or zero",
          bpm == 0.0 or (60 <= bpm <= 200),
          f"bpm={bpm}")


# --------------------------------------------------------------------------- #
# snap_cutlist_to_beats
# --------------------------------------------------------------------------- #

def _make_cutlist() -> tuple[Cutlist, list[float]]:
    beats = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
    cuts = [
        Cut(clip="a.mp4", in_=0.0, out=0.45, offset=0.0, label="A"),
        Cut(clip="b.mp4", in_=2.0, out=2.45, offset=0.52, label="B"),
        Cut(clip="c.mp4", in_=5.0, out=5.45, offset=1.06, label="C"),
    ]
    cl = Cutlist(sequence_name="Test", fps=25, cuts=cuts)
    return cl, beats


def test_snap_snaps_offsets() -> None:
    """First cut offset snaps to nearest beat; subsequent cuts are repacked
    (they follow the previous cut end, so may not land exactly on a beat, but
    the initial snap of each cut's raw offset is towards a beat)."""
    cl, beats = _make_cutlist()
    snapped = snap_cutlist_to_beats(cl, beats)
    # Verify the first cut (index 0) is on a beat (it sets the anchor)
    first = snapped.cuts[0]
    nearest = min(beats, key=lambda b: abs(b - first.offset))
    check("snap: first cut offset is on a beat",
          abs(first.offset - nearest) < 0.001,
          f"offset={first.offset}, nearest_beat={nearest}")
    # Verify all offsets are non-negative and non-decreasing
    offsets = [c.offset for c in snapped.cuts]
    check("snap: offsets are non-decreasing",
          all(offsets[i] <= offsets[i + 1] for i in range(len(offsets) - 1)),
          f"offsets={offsets}")
    check("snap: all offsets non-negative",
          all(o >= 0.0 for o in offsets),
          f"negative offsets: {[o for o in offsets if o < 0]}")


def test_snap_preserves_count() -> None:
    cl, beats = _make_cutlist()
    snapped = snap_cutlist_to_beats(cl, beats)
    check("snap: same number of cuts",
          len(snapped.cuts) == len(cl.cuts),
          f"orig={len(cl.cuts)}, snapped={len(snapped.cuts)}")


def test_snap_preserves_durations() -> None:
    """Each cut's duration (out - in) should be unchanged by snapping."""
    cl, beats = _make_cutlist()
    snapped = snap_cutlist_to_beats(cl, beats)
    for orig, new in zip(cl.cuts, snapped.cuts):
        check(f"snap: duration preserved for {orig.label}",
              abs(orig.duration - new.duration) < 0.001,
              f"orig={orig.duration:.3f}, new={new.duration:.3f}")


def test_snap_no_overlap() -> None:
    """Snapped cuts must not overlap on the timeline."""
    cl, beats = _make_cutlist()
    snapped = snap_cutlist_to_beats(cl, beats)
    sorted_cuts = sorted(snapped.cuts, key=lambda c: c.offset)
    for a, b in zip(sorted_cuts, sorted_cuts[1:]):
        check(f"snap: no overlap between {a.label} and {b.label}",
              b.offset + 1e-6 >= a.timeline_end,
              f"{a.label} ends {a.timeline_end:.3f}, {b.label} starts {b.offset:.3f}")


def test_snap_empty_beats() -> None:
    """No beats -> return original cutlist unchanged."""
    cl, _ = _make_cutlist()
    snapped = snap_cutlist_to_beats(cl, [])
    check("snap: empty beats returns original",
          snapped.to_dict() == cl.to_dict())


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #


def main() -> int:
    print("--- rms_frames ---")
    test_rms_frames_length()
    test_rms_frames_dc_signal()
    test_rms_frames_silence()
    test_rms_frames_too_short()
    test_rms_frames_sine()

    print("--- adaptive_threshold ---")
    test_adaptive_threshold_length()
    test_adaptive_threshold_empty()
    test_adaptive_threshold_stricter_at_low_sensitivity()

    print("--- pick_onsets ---")
    test_pick_onsets_basic()
    test_pick_onsets_dedup()
    test_pick_onsets_no_false_positive_on_flat()

    print("--- estimate_bpm (synthetic beat clock) ---")
    test_estimate_bpm_120_synthetic()
    test_estimate_bpm_100_synthetic()
    test_estimate_bpm_insufficient_onsets()
    test_estimate_bpm_empty()
    test_estimate_bpm_range_clamp()

    print("--- snap_cutlist_to_beats ---")
    test_snap_snaps_offsets()
    test_snap_preserves_count()
    test_snap_preserves_durations()
    test_snap_no_overlap()
    test_snap_empty_beats()

    print(f"\n{_p} passed, {_f} failed")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
