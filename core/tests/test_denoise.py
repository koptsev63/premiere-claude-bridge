"""Tests for core.denoise.build_denoise_filter (pure, no ffmpeg).

Run:  python -m pytest core/tests/test_denoise.py -q
  or: python -m core.tests.test_denoise
"""

from __future__ import annotations

import sys

from core.denoise import (
    DEF_AFFTDN_NF,
    DEF_AFFTDN_NR,
    DEF_HIGHPASS_HZ,
    build_denoise_filter,
)


# ---- helpers ----------------------------------------------------------------

def _stages(s: str) -> list[str]:
    """Split a filterchain string into individual filter tokens."""
    return [t.strip() for t in s.split(",") if t.strip()]


# ---- default chain ----------------------------------------------------------

def test_default_includes_highpass():
    f = build_denoise_filter()
    assert any(s.startswith("highpass=") for s in _stages(f)), f


def test_default_includes_afftdn():
    f = build_denoise_filter()
    assert any("afftdn" in s for s in _stages(f)), f


def test_default_includes_dynaudnorm():
    f = build_denoise_filter()
    assert any("dynaudnorm" in s for s in _stages(f)), f


def test_default_no_hum_notches():
    f = build_denoise_filter()
    assert "bandreject" not in f


def test_default_no_anlmdn():
    f = build_denoise_filter()
    assert "anlmdn" not in f


# ---- value injection --------------------------------------------------------

def test_highpass_value_injected():
    f = build_denoise_filter(highpass=100)
    assert "highpass=f=100" in f


def test_afftdn_nf_injected():
    f = build_denoise_filter(afftdn_nf=-30.0)
    assert "nf=-30.0" in f


def test_afftdn_nr_injected():
    f = build_denoise_filter(afftdn_nr=20.0)
    assert "nr=20.0" in f


def test_default_nf_and_nr_present():
    f = build_denoise_filter()
    assert f"nf={DEF_AFFTDN_NF}" in f
    assert f"nr={DEF_AFFTDN_NR}" in f


# ---- stage toggles ----------------------------------------------------------

def test_no_highpass_when_disabled():
    f = build_denoise_filter(highpass=None)
    assert "highpass" not in f


def test_no_highpass_when_zero():
    f = build_denoise_filter(highpass=0)
    assert "highpass" not in f


def test_no_normalize_when_disabled():
    f = build_denoise_filter(normalize=False)
    assert "dynaudnorm" not in f


def test_anlmdn_replaces_afftdn():
    f = build_denoise_filter(anlmdn=True)
    assert "anlmdn" in f
    assert "afftdn" not in f


def test_anlmdn_strength_injected():
    f = build_denoise_filter(anlmdn=True, anlmdn_strength=0.05)
    assert "anlmdn=s=0.05" in f


# ---- hum notches ------------------------------------------------------------

def test_hum_50_adds_three_notches():
    f = build_denoise_filter(hum=50)
    stages = _stages(f)
    notches = [s for s in stages if "bandreject" in s]
    assert len(notches) == 3, f"expected 3 notches, got {notches}"


def test_hum_50_correct_frequencies():
    f = build_denoise_filter(hum=50)
    for freq in (50, 100, 150):
        assert f"bandreject=f={freq}" in f, f"missing notch at {freq} Hz in {f}"


def test_hum_60_correct_frequencies():
    f = build_denoise_filter(hum=60)
    for freq in (60, 120, 180):
        assert f"bandreject=f={freq}" in f, f"missing notch at {freq} Hz in {f}"


def test_hum_notches_use_q_width():
    f = build_denoise_filter(hum=50)
    assert "width_type=q" in f


def test_no_hum_when_none():
    f = build_denoise_filter(hum=None)
    assert "bandreject" not in f


def test_no_hum_when_zero():
    f = build_denoise_filter(hum=0)
    assert "bandreject" not in f


# ---- filter ordering --------------------------------------------------------

def test_highpass_before_afftdn():
    f = build_denoise_filter()
    stages = _stages(f)
    hp_idx = next((i for i, s in enumerate(stages) if s.startswith("highpass")), None)
    fft_idx = next((i for i, s in enumerate(stages) if "afftdn" in s), None)
    assert hp_idx is not None and fft_idx is not None
    assert hp_idx < fft_idx


def test_afftdn_before_notches():
    f = build_denoise_filter(hum=50)
    stages = _stages(f)
    fft_idx = next((i for i, s in enumerate(stages) if "afftdn" in s), None)
    notch_idx = next((i for i, s in enumerate(stages) if "bandreject" in s), None)
    assert fft_idx is not None and notch_idx is not None
    assert fft_idx < notch_idx


def test_normalize_last():
    f = build_denoise_filter(hum=60)
    stages = _stages(f)
    norm_idx = next((i for i, s in enumerate(stages) if "dynaudnorm" in s), None)
    assert norm_idx == len(stages) - 1, f"dynaudnorm not last: {stages}"


# ---- minimal chain (all stages off except broadband) -----------------------

def test_minimal_chain_only_afftdn():
    f = build_denoise_filter(highpass=None, hum=None, normalize=False)
    stages = _stages(f)
    assert len(stages) == 1
    assert "afftdn" in stages[0]


# ---- return type ------------------------------------------------------------

def test_returns_string():
    assert isinstance(build_denoise_filter(), str)


def test_non_empty_string():
    assert len(build_denoise_filter()) > 0


# ---- standalone runner (matches existing test style) -----------------------

_p = _f = 0


def _check(name: str, cond: bool, detail: str = "") -> None:
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


def main() -> int:
    global _p, _f
    _p = _f = 0

    f_def = build_denoise_filter()
    stages = _stages(f_def)

    _check("default has highpass",
           any(s.startswith("highpass=") for s in stages), f_def)
    _check("default has afftdn",
           any("afftdn" in s for s in stages), f_def)
    _check("default has dynaudnorm",
           any("dynaudnorm" in s for s in stages), f_def)
    _check("default no bandreject",
           "bandreject" not in f_def, f_def)
    _check("highpass disabled",
           "highpass" not in build_denoise_filter(highpass=None), "")
    _check("normalize disabled",
           "dynaudnorm" not in build_denoise_filter(normalize=False), "")
    _check("anlmdn replaces afftdn",
           "anlmdn" in build_denoise_filter(anlmdn=True)
           and "afftdn" not in build_denoise_filter(anlmdn=True), "")
    f_hum = build_denoise_filter(hum=50)
    _check("hum 50: three notches",
           len([s for s in _stages(f_hum) if "bandreject" in s]) == 3, f_hum)
    _check("hum 50: 150 Hz notch present",
           "bandreject=f=150" in f_hum, f_hum)
    f_hum60 = build_denoise_filter(hum=60)
    _check("hum 60: 180 Hz notch present",
           "bandreject=f=180" in f_hum60, f_hum60)
    _check("nf injected",
           "nf=-30.0" in build_denoise_filter(afftdn_nf=-30.0), "")
    _check("minimal chain length",
           len(_stages(build_denoise_filter(
               highpass=None, hum=None, normalize=False))) == 1, "")

    print(f"\n{_p} passed, {_f} failed")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
