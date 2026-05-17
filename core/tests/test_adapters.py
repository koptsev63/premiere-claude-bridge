"""Tests for the capability matrix and the adapter orchestration.

Run:  python -m core.tests.test_adapters
Uses DryRunAdapter only — no NLE, no third-party deps.
"""

from __future__ import annotations

import sys
from pathlib import Path

from core import capabilities
from core.adapters import DryRunAdapter, get_adapter
from core.cutlist import Cut, Cutlist

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


def test_capability_matrix() -> None:
    print("capability matrix")
    res = capabilities.get("resolve")
    check(
        "resolve requires Studio",
        res.requires_payment()
        and res.requires_paid_tier == "DaVinci Resolve Studio",
        res.requires_paid_tier or "",
    )
    check(
        "resolve AI tools not scriptable",
        not res.supports("resolve_neural_engine_ai"),
    )
    check("resolve imports otio natively", res.supports("native_otio"))

    fcp = capabilities.get("fcpxml")
    check("fcpxml is round-trip only", fcp.round_trip_only)
    check("fcpxml has no live control", not fcp.supports("live_control"))
    check(
        "fcpxml cannot trigger export",
        not fcp.supports("triggered_export"),
    )

    pr = capabilities.get("premiere")
    check("premiere is live", pr.supports("live_control"))
    check("premiere needs no paid tier", not pr.requires_payment())
    check("3 backends known", set(capabilities.backends()) == {
        "premiere", "resolve", "fcpxml"
    }, str(capabilities.backends()))


def test_live_orchestration() -> None:
    print("live backend orchestration (DryRun premiere)")
    cl = Cutlist.load(EXAMPLE)
    a = DryRunAdapter(backend="premiere")
    res = a.apply_cutlist(cl)

    check("mode == live", res.mode == "live", res.mode)
    check("connect called once", a.count("connect") == 1)
    # example has 12 cuts, clip 00221.MTS used twice -> 11 unique imports
    check(
        "imported 11 unique clips",
        res.clips_imported == 11 and a.count("import_media") == 11,
        f"{res.clips_imported}/{a.count('import_media')}",
    )
    check(
        "placed 12 cuts",
        res.cuts_placed == 12 and a.count("place_clip") == 12,
        str(res.cuts_placed),
    )
    check(
        "added 8 markers",
        res.markers_added == 8 and a.count("add_marker") == 8,
        str(res.markers_added),
    )
    check("no warnings", res.warnings == [], str(res.warnings))


def test_round_trip_orchestration() -> None:
    print("round-trip backend orchestration (DryRun fcpxml)")
    cl = Cutlist.load(EXAMPLE)
    a = DryRunAdapter(backend="fcpxml")
    res = a.apply_cutlist(cl)

    check("mode == round_trip", res.mode == "round_trip", res.mode)
    check(
        "export_project_file called",
        a.count("export_project_file") == 1,
    )
    check("no live place_clip calls", a.count("place_clip") == 0)
    check("no connect call", a.count("connect") == 0)
    check("project_file set", bool(res.project_file), str(res.project_file))
    check(
        "markers counted (fcpxml supports markers)",
        res.markers_added == 8,
        str(res.markers_added),
    )


def test_invalid_cutlist_rejected() -> None:
    print("invalid cutlist is rejected before any NLE call")
    bad = Cutlist(
        sequence_name="bad",
        fps=25,
        cuts=[Cut(clip="a.mov", in_=9, out=1, offset=0, label="reversed")],
    )
    a = DryRunAdapter(backend="premiere")
    raised = False
    try:
        a.apply_cutlist(bad)
    except ValueError:
        raised = True
    check("ValueError raised", raised)
    check("no NLE calls leaked", a.calls == [], str(a.calls))


def test_get_adapter_dryrun() -> None:
    print("get_adapter factory")
    a = get_adapter("dryrun", backend="resolve")
    check("dryrun factory works", isinstance(a, DryRunAdapter))
    check("backend propagated", a.BACKEND == "resolve")
    raised = False
    try:
        get_adapter("nope")
    except KeyError:
        raised = True
    check("unknown backend -> KeyError", raised)


def main() -> int:
    for fn in (
        test_capability_matrix,
        test_live_orchestration,
        test_round_trip_orchestration,
        test_invalid_cutlist_rejected,
        test_get_adapter_dryrun,
    ):
        fn()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
