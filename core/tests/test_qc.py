"""Tests for the render QC gate (math + report logic, no ffmpeg).

Run:  python -m core.tests.test_qc
"""

from __future__ import annotations

import sys

from core.qc import QCFailure, QCReport, expected_display_size, \
    qc_residual_shake

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


def test_expected_display_size() -> None:
    print("qc — anamorphic display size (the squish-killer math)")
    check("1440x1080 SAR 4:3 -> 1920x1080 (un-squished)",
          expected_display_size(1440, 1080, "4:3") == (1920, 1080),
          str(expected_display_size(1440, 1080, "4:3")))
    check("already-square 1920x1080 SAR 1:1 unchanged",
          expected_display_size(1920, 1080, "1:1") == (1920, 1080))
    check("empty SAR treated as square",
          expected_display_size(1920, 1080, "") == (1920, 1080))
    check("N/A SAR safe",
          expected_display_size(1280, 720, "N/A") == (1280, 720))
    check("degenerate 0:1 SAR safe (no crash)",
          expected_display_size(1280, 720, "0:1") == (1280, 720))


def test_report_logic() -> None:
    print("qc — report ok / assert_ok / text")
    r = QCReport()
    r.add("a", True, "fine")
    check("all-pass -> ok", r.ok)
    r.assert_ok()  # must not raise
    r.add("b", False, "bad")
    check("any-fail -> not ok", not r.ok)
    raised = False
    try:
        r.assert_ok()
    except QCFailure as e:
        raised = "render QC failed" in str(e) and "FAIL  b" in str(e)
    check("assert_ok raises QCFailure with detail", raised)
    check("text() renders PASS/FAIL lines",
          "PASS  a" in r.text() and "FAIL  b" in r.text())


def test_residual_shake_empty() -> None:
    print("qc — residual shake with nothing stabilized")
    r = QCReport()
    qc_residual_shake("/nonexistent.mp4", [], r)
    check("empty stabilized list -> pass + note",
          r.ok and "no clips were stabilized" in r.text())


def test_residual_shake_fails_closed() -> None:
    print("qc — detector unavailable must FAIL (not silently pass)")
    import sys
    from unittest.mock import patch
    r = QCReport()
    # block the shake detector import -> verification must fail closed
    with patch.dict(sys.modules, {"shake_detect": None}):
        qc_residual_shake("/x.mp4", [(0.0, 2.0, 30.0)], r)
    check("unavailable detector -> NOT ok", not r.ok)
    check("flagged UNVERIFIED, not passed",
          "UNVERIFIED" in r.text(), r.text())


def main() -> int:
    for fn in (
        test_expected_display_size,
        test_report_logic,
        test_residual_shake_empty,
        test_residual_shake_fails_closed,
    ):
        fn()
    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
