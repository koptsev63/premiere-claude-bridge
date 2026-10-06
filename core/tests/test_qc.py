"""Tests for the render QC gate (math + report logic; the delivery check
builds its own tiny files with ffmpeg and skips when ffmpeg is absent).

Run:  python -m core.tests.test_qc
"""

from __future__ import annotations

import sys

from core.qc import QCFailure, QCReport, expected_display_size, \
    qc_delivery, qc_residual_shake

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


def test_delivery() -> None:
    print("qc - delivery integrity on synthetic files")
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path
    r = QCReport()
    qc_delivery("/nonexistent/never.mp4", r)
    check("a missing file fails, it is not skipped", not r.ok, r.text())
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("  SKIP  (ffmpeg/ffprobe not installed)")
        return
    with tempfile.TemporaryDirectory() as d:
        good, mute = Path(d, "good.mkv"), Path(d, "mute.mkv")
        base = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                "testsrc2=s=160x90:r=25:d=2"]
        tail = ["-c:v", "mpeg4", "-q:v", "5", "-g", "50"]
        made = subprocess.run(
            base + ["-f", "lavfi", "-i", "sine=frequency=440:duration=2"]
            + tail + ["-c:a", "pcm_s16le", str(good)], capture_output=True)
        subprocess.run(base + tail + [str(mute)], capture_output=True)
        if made.returncode != 0:
            print("  SKIP  (this ffmpeg build cannot make the test file)")
            return
        r = QCReport()
        qc_delivery(good, r, expect_size=(160, 90), expect_fps=25)
        check("a clean file passes every delivery check",
              r.ok and len(r.checks) == 4, r.text())
        r = QCReport()
        qc_delivery(good, r, expect_size=(1080, 1920), expect_fps=30)
        check("wrong size and wrong rate are both named",
              "frame size" in r.text() and "frame rate" in r.text()
              and not r.ok, r.text())
        r = QCReport()
        qc_delivery(mute, r)
        check("a silent file fails", not r.ok and "silent" in r.text(),
              r.text())
        r = QCReport()
        qc_delivery(mute, r, require_audio=False)
        check("...unless silence was asked for", r.ok, r.text())
        data = bytearray(good.read_bytes())
        mid = len(data) // 2
        data[mid:mid + 4000] = b"\x5a" * 4000
        broken = Path(d, "broken.mkv")
        broken.write_bytes(bytes(data))
        r = QCReport()
        qc_delivery(broken, r)
        check("a damaged stream fails on decode",
              not r.ok and "decodes" in r.text(), r.text())


def main() -> int:
    for fn in (
        test_expected_display_size,
        test_report_logic,
        test_residual_shake_empty,
        test_residual_shake_fails_closed,
        test_delivery,
    ):
        fn()
    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
