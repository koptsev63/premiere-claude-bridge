"""Tests for per-clip technical corrections (horizon + stabilization).

Run:  python -m core.tests.test_cleanup
Hermetic — a fake analysis report, no video, no NLE.
"""

from __future__ import annotations

import sys

from core.cleanup import Correction, corrections_for_cutlist, summary
from core.cutlist import Cut, Cutlist

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


def _cl():
    return Cutlist(
        sequence_name="t", fps=25,
        cuts=[
            Cut(clip="/foot/00100.MTS", in_=0, out=2, offset=0,  label="tilt"),
            Cut(clip="/foot/00101.MTS", in_=0, out=2, offset=2,  label="ok"),
            Cut(clip="/foot/00102.MTS", in_=0, out=2, offset=4,  label="s1"),
            Cut(clip="/foot/00103.MTS", in_=0, out=2, offset=6,  label="s2"),
            Cut(clip="/foot/00104.MTS", in_=0, out=2, offset=8,  label="s3"),
            Cut(clip="/foot/00105.MTS", in_=0, out=2, offset=10, label="shaky"),
            Cut(clip="/foot/zzz.MTS",   in_=0, out=2, offset=12, label="absent"),
        ],
    )


def _report():
    # 00100 tilted; 00105 a hard shake outlier; rest steady
    return [
        {"clip": "00100.MTS", "horizon_tilt_deg": 3.5,
         "horizon_correction_filter": "rotate=-0.061087:fillcolor=black",
         "shake_score": 9.0},
        {"clip": "00101.MTS", "horizon_tilt_deg": 0.1,
         "horizon_correction_filter": None, "shake_score": 9.5},
        {"clip": "00102.MTS", "horizon_correction_filter": None,
         "shake_score": 10.0},
        {"clip": "00103.MTS", "horizon_correction_filter": None,
         "shake_score": 9.0},
        {"clip": "00104.MTS", "horizon_correction_filter": None,
         "shake_score": 11.0},
        {"clip": "00105.MTS", "horizon_correction_filter": None,
         "shake_score": 80.0},
    ]


def test_horizon_leveling() -> None:
    print("cleanup — horizon leveling")
    corr = corrections_for_cutlist(_cl(), _report())
    c = corr["/foot/00100.MTS"]
    check("tilted clip gets horizon vf",
          c.horizon_vf == "rotate=-0.061087:fillcolor=black")
    check("rotate_deg undoes the tilt (-3.5)", c.rotate_deg == -3.5,
          str(c.rotate_deg))
    check("reason recorded", any("horizon" in r for r in c.reasons))
    ok = corr["/foot/00101.MTS"]
    check("near-level clip not corrected (vf None)",
          ok.horizon_vf is None and ok.rotate_deg == 0.0)


def test_relative_stabilization_only_outlier() -> None:
    print("cleanup — stabilize only the relative outlier (not blanket)")
    corr = corrections_for_cutlist(_cl(), _report())
    stabilized = [k for k, c in corr.items() if c.stabilize]
    check("exactly one clip stabilized", len(stabilized) == 1,
          str(stabilized))
    check("it is the 80-score outlier",
          corr["/foot/00105.MTS"].stabilize is True)
    check("a steady ~9-score clip is NOT stabilized",
          corr["/foot/00102.MTS"].stabilize is False)


def test_vf_chain_and_clean() -> None:
    print("cleanup — vf chain composition + clean clips")
    corr = corrections_for_cutlist(_cl(), _report())
    check("tilted clip vf chain = rotate only",
          corr["/foot/00100.MTS"].vf_chain()
          == "rotate=-0.061087:fillcolor=black")
    # force a clip that is BOTH shaky outlier and tilted
    cl2 = Cutlist(sequence_name="x", fps=25,
                   cuts=[Cut(clip="/f/A.MTS", in_=0, out=1, offset=0)])
    rep2 = [{"clip": "A.MTS", "horizon_tilt_deg": 2.0,
             "horizon_correction_filter": "rotate=-0.034907:fillcolor=black",
             "shake_score": 999.0}]
    a = corrections_for_cutlist(cl2, rep2)["/f/A.MTS"]
    check("deshake precedes rotate in chain",
          a.vf_chain() == "deshake=edge=clamp,"
                          "rotate=-0.034907:fillcolor=black",
          a.vf_chain() or "")
    absent = corr["/foot/zzz.MTS"]
    check("clip absent from report -> clean no-op",
          absent.clean and absent.vf_chain() is None)


def test_summary() -> None:
    print("cleanup — summary string")
    s = summary(corrections_for_cutlist(_cl(), _report()))
    check("summary mentions leveled + stabilized",
          "horizon-leveled" in s and "stabilized" in s, s)


def main() -> int:
    for fn in (
        test_horizon_leveling,
        test_relative_stabilization_only_outlier,
        test_vf_chain_and_clean,
        test_summary,
    ):
        fn()
    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
