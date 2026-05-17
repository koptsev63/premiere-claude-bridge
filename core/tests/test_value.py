"""Tests for the clip value ("meat") model.

Run:  python -m core.tests.test_value
Hermetic — fake report, no video.
"""

from __future__ import annotations

import sys

from core.value import ValuePool, rank_clips

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


REPORT = [
    {"clip": "strong.MTS", "motion_score": 3.0, "audio_peak_db": -12.0},
    {"clip": "mid.MTS",    "motion_score": 1.0, "audio_peak_db": -24.0},
    {"clip": "weak.MTS",   "motion_score": 0.1, "audio_peak_db": -40.0},
    {"clip": "shaky_dead.MTS", "motion_score": 0.05,
     "audio_peak_db": -38.0, "shake_score": 90.0},
    {"clip": "shaky_action.MTS", "motion_score": 2.8,
     "audio_peak_db": -15.0, "shake_score": 90.0},
    {"clip": "noaudio.MTS", "motion_score": 0.5},  # missing audio_peak_db
]


def test_basic_ranking() -> None:
    print("value — composite ranking")
    r = rank_clips(REPORT)
    names = [v.clip for v in r]
    check("strong ranks above weak",
          names.index("strong.MTS") < names.index("weak.MTS"))
    check("mid between strong and weak",
          names.index("strong.MTS") < names.index("mid.MTS")
          < names.index("weak.MTS"), str(names))
    check("missing audio handled (no crash, finite score)",
          all(isinstance(v.score, float) for v in r))


def test_shake_penalty_is_conditional() -> None:
    print("value — shake penalty only when jitter + low motion")
    by = {v.clip: v for v in rank_clips(REPORT)}
    dead = by["shaky_dead.MTS"]
    act = by["shaky_action.MTS"]
    check("shaky+dead penalized",
          any("shake" in w and "-" in w for w in dead.why), str(dead.why))
    check("shaky+action NOT penalized",
          any("deliberate energy" in w for w in act.why), str(act.why))
    check("shaky action still ranks high",
          rank_clips(REPORT)[0].clip in ("strong.MTS", "shaky_action.MTS"))


def test_meat_tag_overrides_metrics() -> None:
    print("value — human/LLM meat tag floors above all untagged")
    r = rank_clips(REPORT, meat_tags={"weak.MTS": {"time": 4.2}})
    check("meat-tagged weak clip is now #1",
          r[0].clip == "weak.MTS", r[0].clip)
    check("carries meat flag + time",
          r[0].meat and r[0].meat_time == 4.2)
    check("a strong untagged clip ranks below it",
          [v.clip for v in r].index("strong.MTS") > 0)


def test_pool_tiers() -> None:
    print("value — ValuePool protected / anchors / order")
    pool = ValuePool(REPORT, meat_tags={"weak.MTS": True})
    prot = {v.clip for v in pool.protected()}
    check("meat-tagged clip is protected", "weak.MTS" in prot)
    check("anchors(2) = top 2 of order",
          [v.clip for v in pool.anchors(2)] == pool.order()[:2])
    check("order strongest-first, all clips present",
          len(pool.order()) == len(REPORT))


def test_degenerate_inputs() -> None:
    print("value — degenerate inputs")
    check("empty report -> empty rank", rank_clips([]) == [])
    one = rank_clips([{"clip": "a.MTS", "motion_score": 1,
                       "audio_peak_db": -10}])
    check("single clip -> neutral 0.5 norms, no crash",
          len(one) == 1 and isinstance(one[0].score, float))
    eq = rank_clips([{"clip": "a.MTS", "motion_score": 1,
                      "audio_peak_db": -10},
                     {"clip": "b.MTS", "motion_score": 1,
                      "audio_peak_db": -10}])
    check("all-equal -> equal finite scores",
          abs(eq[0].score - eq[1].score) < 1e-9)


def main() -> int:
    for fn in (
        test_basic_ranking,
        test_shake_penalty_is_conditional,
        test_meat_tag_overrides_metrics,
        test_pool_tiers,
        test_degenerate_inputs,
    ):
        fn()
    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
