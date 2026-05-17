"""Tests for the NLE-neutral review loop.

Run:  python -m core.tests.test_review_loop

Expected values are computed by hand from the real example cutlist so the
deterministic Murch checks are pinned. No NLE, no ffmpeg run, no deps.
"""

from __future__ import annotations

import sys
from pathlib import Path

from core.cutlist import Cutlist
from core.review_loop import (
    CutlistPatch,
    ReviewLoop,
    analyze_cutlist,
    build_rough_cut_plan,
    watch_plan,
)

EXAMPLE = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "grave-stakes-teaser"
    / "cutlist_v3.json"
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


def test_analysis_pinned() -> None:
    print("deterministic Murch analysis (pinned to the example)")
    a = analyze_cutlist(Cutlist.load(EXAMPLE))
    # durations: [6,5,5,3,3,7,3,3,7,6,5,7] -> min 3, max 7
    check("shortest == 3", a.shortest == 3, str(a.shortest))
    check("longest == 7", a.longest == 7, str(a.longest))
    check("ratio == 2.333", a.ratio == 2.333, str(a.ratio))
    check("ratio ok (2-4x)", a.ratio_ok)
    check("no 4-in-a-row monotony", a.monotony_runs == [], str(a.monotony_runs))
    check(
        "exactly one beat-pacing flag",
        len(a.beat_pacing_flags) == 1,
        str(a.beat_pacing_flags),
    )
    check(
        "flag is the under-paced 'pit-followthrough'",
        "pit-followthrough" in a.beat_pacing_flags[0],
        a.beat_pacing_flags[0] if a.beat_pacing_flags else "",
    )
    check("not clean (has the flag)", not a.is_clean())


def test_watch_plan() -> None:
    print("watch plan")
    cl = Cutlist.load(EXAMPLE)
    plan = watch_plan(cl, media_dir="/footage")
    check("one watch cmd per cut", len(plan) == 12, str(len(plan)))
    check(
        "first cmd windows the first cut",
        "/footage/00118.MTS" in plan[0]
        and "--start 2" in plan[0]
        and "--end 8" in plan[0],
        plan[0],
    )


def test_rough_cut_plan() -> None:
    print("NLE-free ffmpeg rough-cut plan")
    cl = Cutlist.load(EXAMPLE)
    p = build_rough_cut_plan(cl, "/footage", "/tmp/rough.mp4")
    check("ffmpeg argv", p["argv"][0] == "ffmpeg")
    check("12 segments", len(p["segments"]) == 12)
    check(
        "concat filter for 12",
        "concat=n=12:v=1:a=1" in " ".join(p["argv"]),
    )
    check("maps v/a", "[v]" in p["argv"] and "[a]" in p["argv"])
    check("out path carried", p["out"] == "/tmp/rough.mp4")


def test_patch_immutable_and_validated() -> None:
    print("CutlistPatch — immutable + validated")
    cl = Cutlist.load(EXAMPLE)

    dropped = CutlistPatch(drop=[11]).apply(cl)
    check("drop removes one cut", len(dropped.cuts) == 11)
    check("input cutlist untouched", len(cl.cuts) == 12)

    rev = CutlistPatch(reorder=list(reversed(range(12)))).apply(cl)
    check("reorder keeps all cuts", len(rev.cuts) == 12)
    check(
        "reorder re-packs offsets from 0",
        rev.cuts[0].offset == 0.0,
        str(rev.cuts[0].offset),
    )
    check(
        "reordered result is valid",
        rev.validate() == [],
        str(rev.validate()),
    )

    marked = CutlistPatch(
        add_markers=[{"name": "NEW", "time": 1, "comment": "added"}]
    ).apply(cl)
    check("marker added", len(marked.markers) == 9)
    check("input markers untouched", len(cl.markers) == 8)

    raised = False
    try:
        # cut[6] is 00199 in24/out27/offset29; blow out -> overlaps next
        CutlistPatch(adjust={6: {"out": 80}}).apply(cl)
    except ValueError:
        raised = True
    check("invalid patch rejected", raised)


def test_review_loop_history() -> None:
    print("ReviewLoop — history + diff")
    cl = Cutlist.load(EXAMPLE)
    loop = ReviewLoop(cl)
    check("starts at version 0", len(loop.history) == 1)
    check("current is the input", len(loop.current.cuts) == 12)
    check("analysis available", loop.analysis.ratio == 2.333)

    loop.apply(CutlistPatch(drop=[0]))
    check("history grew", len(loop.history) == 2)
    check("current changed", len(loop.current.cuts) == 11)
    d = loop.diff(0, 1)
    check("diff tracks cut count", d["cuts"] == (12, 11), str(d["cuts"]))


def main() -> int:
    for fn in (
        test_analysis_pinned,
        test_watch_plan,
        test_rough_cut_plan,
        test_patch_immutable_and_validated,
        test_review_loop_history,
    ):
        fn()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
