"""Tests for music-hit detection and cut-on-hit checks (pure, no ffmpeg).

Run:  python -m core.tests.test_hits
"""

from __future__ import annotations

import math
import sys

from core.hits import TOL_S, cuts_vs_hits, find_hits, loudest_near, loudness_curve

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


def _synth(sr=8000, dur=12.0, hits=(2.0, 7.0), quiet=0.01, loud=0.5):
    """Quiet noise floor with 80 ms loud bursts at the given times."""
    n = int(sr * dur)
    s = [quiet * math.sin(i * 0.3) for i in range(n)]
    for h in hits:
        a = int(h * sr)
        for i in range(a, min(n, a + int(0.08 * sr))):
            s[i] = loud * math.sin(i * 1.7)
    return s


def main() -> int:
    sr = 8000
    samples = _synth(sr=sr)
    times, db = loudness_curve(samples, sr)
    check("curve length", abs(len(times) - 12.0 / 0.02) <= 1, str(len(times)))
    hits = find_hits(times, db, floor_db=-20)
    check("two hits found", len(hits) == 2, str(hits))
    check("hit times", all(abs(h[0] - e) < 0.03 for h, e in zip(hits, (2.0, 7.0))), str(hits))

    t, d = loudest_near(times, db, 7.3)
    check("loudest near 7.3 is the 7.0 hit", abs(t - 7.0) < 0.03, f"{t}")

    rep = cuts_vs_hits([2.02, 7.3], times, db)
    check("cut on the hit passes", rep.items[0].ok, str(rep.items[0]))
    check("cut 0.3 s late fails", not rep.items[1].ok, str(rep.items[1]))
    check("off list", [c.cut for c in rep.off] == [7.3], str(rep.off))
    try:
        rep.assert_ok(); raised = False
    except AssertionError as e:
        raised = "7.30s" in str(e)
    check("assert_ok raises with the offender", raised)
    check("tolerance is two frames at 24 fps", abs(TOL_S - 2 / 24) < 1e-6)

    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
