"""Tests for the concrete adapters (Resolve / Premiere / FCPXML).

Run:  python -m core.tests.test_concrete_adapters

Resolve cannot be end-to-end tested without Resolve Studio running, so we
test the *graceful guard* (connect raises a clear, actionable error and
nothing imports the Resolve module at import time). Premiere is tested via
the JSX it compiles. FCPXML is tested by parsing the project it writes.
No NLE, no third-party deps.
"""

from __future__ import annotations

import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from core.adapters.fcpxml import FcpxmlAdapter
from core.adapters.premiere import PremiereAdapter
from core.adapters.resolve import ResolveAdapter, ResolveUnavailable
from core.cutlist import Cutlist

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


def test_resolve_guard() -> None:
    print("Resolve adapter — graceful guard (no Studio here)")
    check(
        "import did not pull DaVinciResolveScript",
        "DaVinciResolveScript" not in sys.modules,
    )
    a = ResolveAdapter()
    check("instantiates without Resolve", a.BACKEND == "resolve")
    check(
        "capabilities say Studio required",
        a.capabilities.requires_paid_tier == "DaVinci Resolve Studio",
    )
    raised = ""
    try:
        a.connect()
    except ResolveUnavailable as exc:
        raised = str(exc)
    check("connect() raises ResolveUnavailable", bool(raised))
    check(
        "error message is actionable (mentions Studio)",
        "Studio" in raised and "External scripting" in raised,
        raised[:80],
    )
    # apply_cutlist must surface the same guarded failure, not a crash
    cl = Cutlist.load(EXAMPLE)
    guarded = False
    try:
        a.apply_cutlist(cl)
    except ResolveUnavailable:
        guarded = True
    check("apply_cutlist guarded too", guarded)


def test_premiere_jsx() -> None:
    print("Premiere adapter — compiles ExtendScript")
    cl = Cutlist.load(EXAMPLE)
    a = PremiereAdapter()
    jsx = a.build_jsx(cl)

    check("wrapped IIFE", jsx.startswith("(function(){") and jsx.rstrip().endswith("})();"))
    check(
        "sets active sequence name",
        'seq.name = "Teaser_v3_DataDriven";' in jsx,
    )
    check(
        "11 unique clip lookups",
        jsx.count("findItemsMatchingMediaPath") == 11,
        str(jsx.count("findItemsMatchingMediaPath")),
    )
    check(
        "12 insertClip calls",
        jsx.count("insertClip(") == 12,
        str(jsx.count("insertClip(")),
    )
    check(
        "8 markers created",
        jsx.count("createMarker(") == 8,
        str(jsx.count("createMarker(")),
    )
    check('returns "OK"', 'return "OK";' in jsx)
    # spot-check a known cut: 00118.MTS in=2 out=8 offset=0
    check(
        "first cut in/out/offset encoded",
        "setInPoint(2, 4)" in jsx
        and "setOutPoint(8, 4)" in jsx
        and "insertClip(pi1, 0)" in jsx,
    )


def test_fcpxml_roundtrip_backend() -> None:
    print("FCPXML adapter — writes a parseable project")
    cl = Cutlist.load(EXAMPLE)
    a = FcpxmlAdapter()
    res = a.apply_cutlist(cl)

    check("routed as round_trip", res.mode == "round_trip", res.mode)
    check("project_file set", bool(res.project_file))

    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "t.fcpxml"
        a.export_project_file(cl, str(out))
        check("file written", out.exists() and out.stat().st_size > 0)

        tree = ET.parse(out)
        root = tree.getroot()
        check("root is <fcpxml>", root.tag == "fcpxml")
        check("version attr present", root.get("version") == "1.10")
        assets = root.findall(".//resources/asset")
        clips = root.findall(".//spine/asset-clip")
        markers = root.findall(".//spine/asset-clip/marker")
        check("11 assets (unique sources)", len(assets) == 11, str(len(assets)))
        check("12 asset-clips", len(clips) == 12, str(len(clips)))
        check("8 markers total", len(markers) == 8, str(len(markers)))
        # offsets monotonic & in seconds-rational form
        offs = [c.get("offset") for c in clips]
        check(
            "offsets look rational (N/25s)",
            all(o and o.endswith("s") and "/" in o for o in offs),
            str(offs[:3]),
        )


def main() -> int:
    for fn in (
        test_resolve_guard,
        test_premiere_jsx,
        test_fcpxml_roundtrip_backend,
    ):
        fn()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
