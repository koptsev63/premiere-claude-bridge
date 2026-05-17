"""Dependency-free test runner for the cutlist IR.

Run:  python -m core.tests.test_cutlist

No pytest dependency on purpose — the analysis venv stays minimal.
Exits 0 if all checks pass, 1 otherwise. OTIO checks are skipped (not
failed) if opentimelineio is not installed, since it is an optional dep.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from core.cutlist import (
    Cut,
    Cutlist,
    Marker,
    OtioUnavailable,
    from_otio,
    read_otio,
    to_otio,
    write_otio,
)

EXAMPLE = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "grave-stakes-teaser"
    / "cutlist_v3.json"
)

_passed = 0
_failed = 0
_skipped = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


def skip(name: str, why: str) -> None:
    global _skipped
    _skipped += 1
    print(f"  SKIP  {name}  ({why})")


def test_load_and_validate() -> None:
    print("load + validate real example")
    cl = Cutlist.load(EXAMPLE)
    check("loads example", cl.sequence_name == "Teaser_v3_DataDriven")
    check("12 cuts parsed", len(cl.cuts) == 12, f"got {len(cl.cuts)}")
    check("8 markers parsed", len(cl.markers) == 8, f"got {len(cl.markers)}")
    check("fps 25", cl.fps == 25)
    check("example validates clean", cl.validate() == [], str(cl.validate()))


def test_dataclass_roundtrip() -> None:
    print("dataclass dict round-trip")
    cl = Cutlist.load(EXAMPLE)
    again = Cutlist.from_dict(cl.to_dict())
    check(
        "from_dict(to_dict()) is identity",
        again.to_dict() == cl.to_dict(),
    )


def test_validation_catches_bad() -> None:
    print("validation catches authoring bugs")
    reversed_cl = Cutlist(
        sequence_name="reversed",
        fps=25,
        cuts=[Cut(clip="a.mov", in_=5, out=2, offset=0, label="reversed")],
    )
    errs_rev = reversed_cl.validate()
    check(
        "flags out<=in",
        any("out" in e and "in" in e for e in errs_rev),
        str(errs_rev),
    )

    overlap_cl = Cutlist(
        sequence_name="overlap",
        fps=25,
        cuts=[
            Cut(clip="a.mov", in_=0, out=5, offset=0, label="A"),  # ends @5
            Cut(clip="b.mov", in_=0, out=4, offset=2, label="B"),  # starts @2
        ],
    )
    errs_ov = overlap_cl.validate()
    check("flags overlap", any("overlap" in e for e in errs_ov), str(errs_ov))


def test_otio_roundtrip() -> None:
    print("OTIO lossless round-trip")
    # In-memory conversion is the core guarantee — always a hard check.
    try:
        cl = Cutlist.load(EXAMPLE)
        recovered = from_otio(to_otio(cl))
        check(
            "from_otio(to_otio(cl)) == cl (in-memory)",
            recovered.to_dict() == cl.to_dict(),
            "metadata round-trip not lossless",
        )
    except OtioUnavailable as exc:
        skip("OTIO in-memory round-trip", str(exc).splitlines()[0])
        return

    # File I/O depends on otio's JSON layer, which is broken on some
    # bleeding-edge interpreters (e.g. CPython 3.14). Skip, don't fail.
    try:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "rt.otio"
            write_otio(cl, p)
            check(".otio file written", p.exists() and p.stat().st_size > 0)
            from_file = read_otio(p)
            check(
                "read_otio(write_otio(cl)) == cl (on disk)",
                from_file.to_dict() == cl.to_dict(),
            )
    except OtioUnavailable as exc:
        skip("OTIO .otio file round-trip", str(exc).splitlines()[0])


def test_otio_structural_reconstruction() -> None:
    print("OTIO structural reconstruction (no metadata)")
    try:
        import opentimelineio as otio  # noqa: WPS433
    except ModuleNotFoundError:
        skip("structural reconstruction", "opentimelineio not installed")
        return

    cl = Cutlist(
        sequence_name="struct",
        fps=25,
        cuts=[
            Cut(clip="x.mov", in_=2, out=8, offset=0, label="A"),
            Cut(clip="y.mov", in_=1, out=4, offset=10, label="B"),  # gap 6..10
        ],
        markers=[Marker(name="M1", time=3, comment="c")],
    )
    tl = to_otio(cl)
    # wipe our lossless metadata -> force structural path
    tl.metadata.clear()
    rec = from_otio(tl)
    check("2 cuts reconstructed", len(rec.cuts) == 2, f"got {len(rec.cuts)}")
    check(
        "gap honored (B offset == 10)",
        abs(rec.cuts[1].offset - 10.0) < 1e-6,
        f"got {rec.cuts[1].offset}",
    )
    check(
        "source in/out preserved",
        rec.cuts[0].in_ == 2 and rec.cuts[0].out == 8,
        f"got {rec.cuts[0].in_}/{rec.cuts[0].out}",
    )
    check("marker reconstructed", len(rec.markers) == 1 and rec.markers[0].time == 3)


def main() -> int:
    for fn in (
        test_load_and_validate,
        test_dataclass_roundtrip,
        test_validation_catches_bad,
        test_otio_roundtrip,
        test_otio_structural_reconstruction,
    ):
        fn()
    print(
        f"\n{_passed} passed, {_failed} failed, {_skipped} skipped"
    )
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
