"""Tests for the rhythm gate.

Run:  python3 -m core.tests.test_rhythm_gate

The three approved cuts are pinned as shot-length lists measured on
2026-10-26 by ffmpeg scene detection (threshold 0.3) on the delivered files.
They must PASS; a machine-made monotone draft must FAIL. That pair is the
validation the gate's thresholds stand on.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from core.cutlist import Cutlist
from core.rhythm_gate import (
    Shot,
    brief_checklist,
    check,
    load_shots,
    main,
    monotony_runs,
    shots_from_bounds,
    shots_from_premiere,
    spread,
)

ROOT = Path(__file__).resolve().parents[2]
V4_CUTLIST = ROOT / "examples" / "grave-stakes-teaser" / "benchmark" / "v4_cutlist.json"

APPROVED = {
    "grave_stakes_teaser_v4": [5.24, 4.12, 2.6, 2.24, 3.16, 2.16, 6.16, 4.2, 7.0],
    "duality_trailer": [7.96, 1.54, 1.5, 1.5, 2.25, 2.12, 3.46, 7.21, 1.67, 1.79, 6.04, 3.38, 0.83,
                        1.75, 2.04, 10.38, 3.04, 4.83, 3.5, 3.08, 2.38, 2.42, 2.54, 2.42, 13.03],
    "grave_stakes_teaser_v7": [9.16, 7.28, 6.72, 7.36, 8.56, 5.84, 3.12, 3.52, 2.84, 43.76, 3.8, 2.88,
                               3.28, 2.76, 1.16, 2.32, 1.28, 2.28, 1.32, 2.88, 2.24, 2.44, 2.12, 2.32,
                               2.92, 26.0, 2.16, 1.88, 13.0, 3.04, 6.64, 13.4, 2.2, 2.4, 2.36, 3.4, 1.16,
                               0.96, 1.12, 0.88, 5.4, 3.08, 1.44, 2.6, 2.32, 1.92, 5.32, 3.2, 2.32, 1.44,
                               3.6, 3.6, 4.52, 2.72, 2.12, 2.68, 2.72, 2.08, 2.32, 31.52, 5.64],
}

_passed = 0


def ok(cond: bool, name: str) -> None:
    global _passed
    if not cond:
        print(f"FAIL {name}")
        sys.exit(1)
    _passed += 1
    print(f"ok   {name}")


def shots(durs: list[float]) -> list[Shot]:
    out, t = [], 0.0
    for d in durs:
        out.append(Shot(round(t, 3), d))
        t += d
    return out


def test_approved_cuts_pass() -> None:
    for name, durs in APPROVED.items():
        rep = check(shots(durs), "trailer", 25)
        ok(rep.passed and not rep.fails, f"approved {name} passes ({rep.fails})")


def test_monotone_draft_fails() -> None:
    # An auto-assembly that cuts every ~3 s: the classic machine draft.
    draft = [3.0, 3.04, 2.96, 3.0, 3.0, 3.04, 3.0, 2.96, 3.0, 3.08]
    rep = check(shots(draft), "teaser", 25)
    ok(not rep.passed, "monotone draft fails")
    ok(any("monotone cut" in f for f in rep.fails), "monotone draft: spread fail")
    ok(any("same-length shots in a row" in f for f in rep.fails), "monotone draft: run fail")


def test_run_inside_varied_cut_fails() -> None:
    durs = [6.0, 1.5, 2.0, 2.0, 2.04, 2.0, 7.0, 3.0, 9.0]
    rep = check(shots(durs), "trailer", 25)
    ok(spread(durs) >= 2.0, "varied cut has spread")
    ok(not rep.passed and len(monotony_runs(durs, 0.081)) == 1, "a 4-shot same-length run alone fails")


def test_other_genres_report_only() -> None:
    rep = check(shots([3.0] * 8), "documentary", 25)
    ok(rep.fails and rep.passed, "documentary: findings reported, exit stays 0")


def test_warnings() -> None:
    rep = check(shots([4.0, 0.12, 2.0, 6.0, 3.0, 1.2]), "trailer", 25)
    ok(any("flash shot" in w for w in rep.warns), "flash shot warned")
    ok(any("final shot" in w for w in rep.warns), "short final shot warned")
    ok(rep.passed, "warnings do not block")


def test_cutlist_beats() -> None:
    cl = Cutlist.load(V4_CUTLIST)
    s, kind, c = load_shots(str(V4_CUTLIST))
    ok(kind == "cutlist" and len(s) == 9, "cutlist loads 9 shots")
    ok(check(s, "teaser", 25, cutlist=c).passed, "v4 cutlist passes with beat labels")
    data = cl.to_dict()
    data["cuts"][0]["out"] = data["cuts"][0]["in"] + 1.0   # HOOK squeezed to 1 s
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "bad.json"
        p.write_text(json.dumps(data))
        s2, _, c2 = load_shots(str(p))
        rep = check(s2, "teaser", 25, cutlist=c2)
    ok(any("beat pacing" in f for f in rep.fails), "squeezed HOOK beat fails")


def test_premiere_dump() -> None:
    dump = {"video": [
        {"muted": False, "clips": [
            {"startSec": 0, "endSec": 4}, {"startSec": 4, "endSec": 6}, {"startSec": 6, "endSec": 12}]},
        {"muted": False, "clips": [{"startSec": 10, "endSec": 12}]},     # title on V2
        {"muted": True, "clips": [{"startSec": 1, "endSec": 2}]},       # muted: ignored
    ]}
    durs = [s.duration for s in shots_from_premiere(dump)]
    ok(durs == [4, 2, 4, 2], f"premiere dump merges cut points ({durs})")


def test_bounds_and_brief() -> None:
    ok([s.duration for s in shots_from_bounds([0, 2.5, 6.0])] == [2.5, 3.5], "bounds to shots")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "trailer.md"
        p.write_text("# T\n## 7. Ошибки\n- x\n## 8. Что может проверить машина\n- ASL\n- финал\n## 9\n- y\n",
                     encoding="utf-8")
        ok(brief_checklist(str(p)) == ["- ASL", "- финал"], "brief checklist section extracted")


def test_cli_exit_codes() -> None:
    ok(main([str(V4_CUTLIST), "--genre", "teaser"]) == 0, "cli: v4 exit 0")
    ok(main(["/nonexistent.json", "--genre", "teaser"]) == 2, "cli: unreadable source exit 2")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        fn()
    print(f"\n{_passed} passed")
