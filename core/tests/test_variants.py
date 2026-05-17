"""Tests for the 2-variant builder.

Run:  python -m core.tests.test_variants
Hermetic — synthetic report + dict dur_lookup, no video, no NLE.
"""

from __future__ import annotations

import sys

from core.variants import STRATEGIES, build_variant, build_variants, ClipMeta

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


# 10 clips, strictly descending strength -> deterministic value order A..J
REPORT = []
for i, c in enumerate("ABCDEFGHIJ"):
    REPORT.append({
        "clip": f"{c}.MTS",
        "motion_score": 3.0 - i * 0.25,
        "audio_peak_db": -10.0 - i * 2.0,
        "audio_peak_time_sec": 20.0,
    })

DURS = {f"{c}.MTS": 60.0 for c in "ABCDEFGHIJ"}
DURS["C.MTS"] = 2.5  # high-value but short -> must land on a short beat


def test_both_variants_clean_and_distinct() -> None:
    print("variants — both built, both is_clean, contrasting")
    v = build_variants(REPORT, DURS.get, fps=25)
    check("both strategies present",
          set(v) == set(STRATEGIES), str(set(v)))
    check("drive is_clean", v["drive"]["is_clean"],
          str(v["drive"]["score"]))
    check("breath is_clean", v["breath"]["is_clean"],
          str(v["breath"]["score"]))
    td = v["drive"]["cutlist"].total_duration_sec
    tb = v["breath"]["cutlist"].total_duration_sec
    check("variants differ in total length (contrast)", td != tb,
          f"drive={td} breath={tb}")
    check("breath is the longer/slower one", tb > td, f"{tb} vs {td}")


def test_arc_anchors_and_validity() -> None:
    print("variants — arc, anchors at PIT/PAYOFF, valid windows")
    cl = build_variants(REPORT, DURS.get, fps=25)["drive"]["cutlist"]
    check("8-beat arc", len(cl.cuts) == 8, str(len(cl.cuts)))
    labels = [c.label.split("-")[0] for c in cl.cuts]
    check("arc order correct",
          labels == ["HOOK", "COMEDY", "ACTION", "ACTION", "ACTION",
                     "PIT", "STAKES", "PAYOFF"], str(labels))
    # A and B are the two strongest -> anchors at PIT (idx5), PAYOFF (idx7)
    check("strongest clip A is PAYOFF or PIT anchor",
          cl.cuts[5].clip in ("A.MTS", "B.MTS")
          and cl.cuts[7].clip in ("A.MTS", "B.MTS"),
          f"{cl.cuts[5].clip}/{cl.cuts[7].clip}")
    check("cutlist validates", cl.validate() == [], str(cl.validate()))
    check("offsets contiguous & monotonic",
          all(cl.cuts[i].offset <= cl.cuts[i + 1].offset
              for i in range(len(cl.cuts) - 1)))


def test_short_clip_lands_on_short_beat() -> None:
    print("variants — short clip routed to a short beat, window valid")
    cl = build_variants(REPORT, DURS.get, fps=25)["breath"]["cutlist"]
    for c in cl.cuts:
        if c.clip == "C.MTS":
            beat = c.label.split("-")[0]
            check("C.MTS (2.5s) placed on a short ACTION beat",
                  beat == "ACTION", beat)
            check("C.MTS window within its 2.5s duration & valid",
                  c.in_ >= 0 and c.out <= 2.5 and c.out > c.in_,
                  f"in={c.in_} out={c.out}")
            break
    else:
        check("C.MTS present (usable, high value)", False, "C not selected")


def test_too_short_clip_skipped_with_note() -> None:
    print("variants — clip too short for ANY beat is skipped + noted")
    durs = dict(DURS)
    durs["A.MTS"] = 0.8  # strongest but unusably short -> physics > value
    v = build_variants(REPORT, durs.get, fps=25)
    used = {c.clip for c in v["drive"]["cutlist"].cuts}
    check("A.MTS not used despite top value", "A.MTS" not in used)
    check("skip is reported in notes",
          any("A.MTS" in n and "skip" in n for n in v["drive"]["notes"]),
          str(v["drive"]["notes"]))
    check("variant still is_clean without it", v["drive"]["is_clean"])


def test_protected_all_present() -> None:
    print("variants — every protected clip is used")
    from core.value import ValuePool
    pool = ValuePool(REPORT)
    protected = {v.clip for v in pool.protected()}
    cl = build_variants(REPORT, DURS.get, fps=25)["drive"]["cutlist"]
    used = {c.clip for c in cl.cuts}
    missing = protected - used
    check("no protected clip dropped", not missing, str(missing))


def test_meat_tag_forces_inclusion() -> None:
    print("variants — meat-tagged weak clip is pulled in & anchored")
    v = build_variants(REPORT, DURS.get, fps=25,
                       meat_tags={"J.MTS": {"time": 5.0}})
    cl = v["drive"]["cutlist"]
    used = {c.clip for c in cl.cuts}
    check("meat-tagged J.MTS is used despite weak metrics",
          "J.MTS" in used, str(used))
    check("still is_clean with the override", v["drive"]["is_clean"])


def test_unknown_strategy() -> None:
    print("variants — unknown strategy rejected")
    raised = False
    try:
        build_variant("nope", [ClipMeta("x.MTS", 10, 5, 1.0)])
    except KeyError:
        raised = True
    check("KeyError on bad strategy name", raised)


def main() -> int:
    for fn in (
        test_both_variants_clean_and_distinct,
        test_arc_anchors_and_validity,
        test_short_clip_lands_on_short_beat,
        test_too_short_clip_skipped_with_note,
        test_protected_all_present,
        test_meat_tag_forces_inclusion,
        test_unknown_strategy,
    ):
        fn()
    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
