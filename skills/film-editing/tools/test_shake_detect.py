"""Hermetic tests for shake-flag logic (no video decode).

Run:  python test_shake_detect.py
Mirrors the repo's no-pytest style. The estimate_shake() decoder is
exercised live in analyze_clips runs; here we pin the decision logic.
"""

import sys

from shake_detect import flag_stabilization_relative, needs_stabilization

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


def main():
    print("shake — absolute floor check")
    check("None is never flagged", needs_stabilization(None) is False)
    check("below floor not flagged", needs_stabilization(1.0, 3.0) is False)
    check("at/above floor flagged", needs_stabilization(9.0, 3.0) is True)

    print("shake — RELATIVE outlier flagging (the operational path)")
    check(
        "uniform footage -> stabilize nothing",
        flag_stabilization_relative([10, 10, 10, 10, 10]) == set(),
    )
    check(
        "single hard outlier -> only it",
        flag_stabilization_relative([5, 5, 5, 5, 50]) == {4},
    )
    check(
        "real doc spread -> only the worst, not blanket",
        flag_stabilization_relative(
            [5.631, 11.641, 19.26, 28.104, 13.82, 19.328]
        ) == {3},
        "handheld doc must not be blanket-stabilized",
    )
    check(
        "Nones tolerated, low values safe",
        flag_stabilization_relative([1, 2, None, 3]) == set(),
    )
    check(
        "sparse input falls back to floor",
        flag_stabilization_relative([100.0]) == {0},
    )

    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
