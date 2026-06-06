"""Tests for audio ducking logic (pure functions only - no ffmpeg required).

Run:  python -m core.tests.test_ducking
Also runnable via pytest (each test_* function is a pytest-compatible assertion).
"""

from __future__ import annotations

import sys

from core.ducking import (
    DEF_ATTACK,
    DEF_FULL,
    DEF_HOLD,
    DEF_REDUCTION,
    DEF_RELEASE,
    duck_envelope,
    envelope_to_volexpr,
    merge_intervals,
)

_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


# ---------------------------------------------------------------------------
# merge_intervals
# ---------------------------------------------------------------------------

def test_merge_empty():
    assert merge_intervals([]) == []


def test_merge_no_overlap():
    ivs = [(0.0, 2.0), (5.0, 7.0)]
    result = merge_intervals(ivs, hold_s=0.1)
    assert result == [(0.0, 2.0), (5.0, 7.0)], f"got {result}"


def test_merge_gap_within_hold():
    # gap of 0.05s between 2.0 and 2.05 - smaller than hold=0.1 -> merge
    ivs = [(0.0, 2.0), (2.05, 4.0)]
    result = merge_intervals(ivs, hold_s=0.1)
    assert len(result) == 1, f"expected 1 merged interval, got {result}"
    assert result[0] == (0.0, 4.0), f"got {result}"


def test_merge_gap_outside_hold():
    # gap of 0.5s - larger than hold=0.1 -> keep separate
    ivs = [(0.0, 2.0), (2.5, 4.0)]
    result = merge_intervals(ivs, hold_s=0.1)
    assert len(result) == 2, f"expected 2 intervals, got {result}"


def test_merge_overlapping():
    ivs = [(1.0, 4.0), (2.0, 5.0)]
    result = merge_intervals(ivs, hold_s=0.0)
    assert result == [(1.0, 5.0)], f"got {result}"


def test_merge_unsorted_input():
    ivs = [(5.0, 7.0), (1.0, 2.0), (3.0, 3.05)]
    result = merge_intervals(ivs, hold_s=0.1)
    # (3.0,3.05) gap from (1.0,2.0) is 1.0s - separate; (3.0,3.05)+(5.0,7.0) gap 1.95 - separate
    assert result[0][0] < result[1][0], "result not sorted"


def test_merge_chain():
    # three intervals each within hold of next - should all merge
    ivs = [(0.0, 1.0), (1.05, 2.0), (2.05, 3.0)]
    result = merge_intervals(ivs, hold_s=0.1)
    assert len(result) == 1, f"expected 1 merged, got {result}"
    assert result[0] == (0.0, 3.0), f"got {result}"


# ---------------------------------------------------------------------------
# duck_envelope - monotonicity and plateau values
# ---------------------------------------------------------------------------

def _gain_at(kf: list[tuple[float, float]], t: float) -> float:
    """Linear interpolate keyframes at time t."""
    if not kf:
        return 1.0
    if t <= kf[0][0]:
        return kf[0][1]
    if t >= kf[-1][0]:
        return kf[-1][1]
    for i in range(len(kf) - 1):
        t0, g0 = kf[i]
        t1, g1 = kf[i + 1]
        if t0 <= t <= t1:
            if t1 == t0:
                return g0
            frac = (t - t0) / (t1 - t0)
            return g0 + frac * (g1 - g0)
    return kf[-1][1]


def test_envelope_no_intervals():
    kf = duck_envelope([], 10.0)
    assert all(abs(g - DEF_FULL) < 1e-6 for _, g in kf), \
        f"no intervals - all gains should be full: {kf}"


def test_envelope_far_from_speech_is_full():
    # speech at [4, 6], total=10. At t=0 and t=10 gain must equal full.
    kf = duck_envelope([(4.0, 6.0)], 10.0, reduction=0.5, attack=0.2, release=0.3)
    g0 = _gain_at(kf, 0.0)
    g10 = _gain_at(kf, 10.0)
    assert abs(g0 - DEF_FULL) < 1e-3, f"t=0 gain={g0} expected {DEF_FULL}"
    assert abs(g10 - DEF_FULL) < 1e-3, f"t=10 gain={g10} expected {DEF_FULL}"


def test_envelope_plateau_inside_speech():
    # speech at [3, 7], attack=0.1, release=0.25
    # deep inside [3.2, 6.8] gain should equal full*(1-reduction)
    kf = duck_envelope(
        [(3.0, 7.0)], 10.0,
        reduction=DEF_REDUCTION, attack=DEF_ATTACK, release=DEF_RELEASE,
    )
    duck_level = DEF_FULL * (1.0 - DEF_REDUCTION)
    for t in [3.5, 4.0, 5.0, 6.0, 6.5]:
        g = _gain_at(kf, t)
        assert abs(g - duck_level) < 0.02, \
            f"t={t}: gain={g:.4f} expected ~{duck_level:.4f}"


def test_envelope_monotone_attack():
    # during attack ramp [2.9, 3.0] gain must be strictly decreasing
    kf = duck_envelope(
        [(3.0, 7.0)], 10.0,
        reduction=0.5, attack=0.1, release=0.25,
    )
    times = [2.90 + i * 0.01 for i in range(11)]
    gains = [_gain_at(kf, t) for t in times]
    for i in range(len(gains) - 1):
        assert gains[i] >= gains[i + 1] - 1e-6, \
            f"attack ramp not monotone at t={times[i]:.3f}: {gains[i]:.4f} -> {gains[i+1]:.4f}"


def test_envelope_monotone_release():
    # during release ramp [7.0, 7.25] gain must be strictly increasing
    kf = duck_envelope(
        [(3.0, 7.0)], 10.0,
        reduction=0.5, attack=0.1, release=0.25,
    )
    times = [7.0 + i * 0.025 for i in range(11)]
    gains = [_gain_at(kf, t) for t in times]
    for i in range(len(gains) - 1):
        assert gains[i] <= gains[i + 1] + 1e-6, \
            f"release ramp not monotone at t={times[i]:.3f}: {gains[i]:.4f} -> {gains[i+1]:.4f}"


def test_envelope_keyframes_sorted():
    kf = duck_envelope([(2.0, 5.0), (7.0, 8.5)], 12.0)
    times = [t for t, _ in kf]
    assert times == sorted(times), f"keyframes not sorted: {times}"


def test_envelope_gains_bounded():
    kf = duck_envelope([(2.0, 5.0)], 10.0, reduction=0.5, full=1.0)
    duck_level = 1.0 * (1.0 - 0.5)
    for t, g in kf:
        assert duck_level - 1e-6 <= g <= 1.0 + 1e-6, \
            f"gain out of bounds at t={t}: {g}"


def test_envelope_multiple_intervals():
    # two speech segments - gain must dip twice
    kf = duck_envelope(
        [(1.0, 2.0), (5.0, 6.0)], 10.0,
        reduction=0.6, attack=0.05, release=0.1, hold=0.0,
    )
    duck_level = 1.0 * (1.0 - 0.6)
    g_middle1 = _gain_at(kf, 1.5)
    g_middle2 = _gain_at(kf, 5.5)
    g_between = _gain_at(kf, 3.5)
    assert abs(g_middle1 - duck_level) < 0.05, \
        f"first plateau at t=1.5: {g_middle1} expected ~{duck_level}"
    assert abs(g_middle2 - duck_level) < 0.05, \
        f"second plateau at t=5.5: {g_middle2} expected ~{duck_level}"
    assert g_between > duck_level + 0.1, \
        f"gain between intervals at t=3.5 should be near full: {g_between}"


# ---------------------------------------------------------------------------
# envelope_to_volexpr
# ---------------------------------------------------------------------------

def test_volexpr_empty():
    expr = envelope_to_volexpr([])
    assert expr == "1.0", f"got '{expr}'"


def test_volexpr_single():
    expr = envelope_to_volexpr([(0.0, 0.75)])
    assert "0.75" in expr or expr == "0.75", f"got '{expr}'"


def test_volexpr_is_string():
    kf = duck_envelope([(3.0, 7.0)], 10.0)
    expr = envelope_to_volexpr(kf)
    assert isinstance(expr, str), f"expected str, got {type(expr)}"
    assert len(expr) > 0


def test_volexpr_contains_between():
    kf = duck_envelope([(3.0, 7.0)], 10.0)
    expr = envelope_to_volexpr(kf)
    assert "between" in expr or "if" in expr, \
        f"expected piecewise expression, got '{expr[:80]}'"


def test_volexpr_no_em_dashes():
    kf = duck_envelope([(3.0, 7.0)], 10.0)
    expr = envelope_to_volexpr(kf)
    assert "—" not in expr and "–" not in expr, \
        "expression contains em/en dash (formatting bug)"


# ---------------------------------------------------------------------------
# Pytest entry points (also invoked by main() below)
# ---------------------------------------------------------------------------

_PURE_TESTS = [
    test_merge_empty,
    test_merge_no_overlap,
    test_merge_gap_within_hold,
    test_merge_gap_outside_hold,
    test_merge_overlapping,
    test_merge_unsorted_input,
    test_merge_chain,
    test_envelope_no_intervals,
    test_envelope_far_from_speech_is_full,
    test_envelope_plateau_inside_speech,
    test_envelope_monotone_attack,
    test_envelope_monotone_release,
    test_envelope_keyframes_sorted,
    test_envelope_gains_bounded,
    test_envelope_multiple_intervals,
    test_volexpr_empty,
    test_volexpr_single,
    test_volexpr_is_string,
    test_volexpr_contains_between,
    test_volexpr_no_em_dashes,
]


def main() -> int:
    """Run all pure tests via the repo custom runner (no pytest dependency)."""
    global _passed, _failed
    _passed = _failed = 0
    for fn in _PURE_TESTS:
        name = fn.__name__
        try:
            fn()
            check(name, True)
        except AssertionError as exc:
            check(name, False, str(exc))
        except Exception as exc:  # noqa: BLE001
            check(name, False, f"EXCEPTION: {exc}")
    print(f"\n{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
