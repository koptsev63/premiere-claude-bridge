"""Tests for conform/relink. Hermetic — builds fake media in a tmpdir.

Run:  python -m core.tests.test_conform
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from core.conform import MediaResolver, conform_cutlist
from core.cutlist import Cut, Cutlist

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


def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00")
    return p


def test_resolve_and_report() -> None:
    print("conform — resolve, missing, ambiguous, immutability")
    with tempfile.TemporaryDirectory() as d:
        root_a = Path(d) / "A"
        root_b = Path(d) / "B"
        _touch(root_a / "00118.MTS")
        _touch(root_a / "00149.MTS")
        _touch(root_b / "00149.MTS")          # dup -> ambiguous
        abs_clip = _touch(Path(d) / "exact" / "hero.mov")

        cl = Cutlist(
            sequence_name="c",
            fps=25,
            cuts=[
                Cut(clip="00118.MTS", in_=0, out=2, offset=0, label="uniq"),
                Cut(clip=str(abs_clip), in_=0, out=1, offset=2, label="abs"),
                Cut(clip="00149.MTS", in_=0, out=1, offset=3, label="ambig"),
                Cut(clip="ZZZ.MTS", in_=0, out=1, offset=4, label="gone"),
            ],
        )
        r = MediaResolver([str(root_a), str(root_b)])
        out, rep = conform_cutlist(cl, r)

        check(
            "unique basename resolved to real path",
            out.cuts[0].clip == str(root_a / "00118.MTS"),
            out.cuts[0].clip,
        )
        check(
            "absolute existing path kept",
            out.cuts[1].clip == str(abs_clip),
        )
        check("missing ref reported", "ZZZ.MTS" in rep.missing)
        check("ambiguous ref reported", "00149.MTS" in rep.ambiguous)
        check(
            "ambiguous lists both candidates",
            len(rep.ambiguous.get("00149.MTS", [])) == 2,
        )
        check("report not ok (missing+ambiguous)", not rep.ok)
        check(
            "input cutlist NOT mutated",
            cl.cuts[0].clip == "00118.MTS",
        )
        check(
            "summary string sane",
            "resolved=" in rep.summary() and "missing=" in rep.summary(),
        )


def test_proxy_preference() -> None:
    print("conform — proxy substitution")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "orig"
        prox = Path(d) / "proxy"
        _touch(root / "00118.MTS")
        _touch(prox / "00118.mp4")            # same stem, lighter codec

        cl = Cutlist(
            sequence_name="p",
            fps=25,
            cuts=[Cut(clip="00118.MTS", in_=0, out=2, offset=0, label="x")],
        )
        r = MediaResolver([str(root)], proxy_dir=str(prox))

        out_no, rep_no = conform_cutlist(cl, r, prefer_proxy=False)
        check(
            "without prefer_proxy -> original",
            out_no.cuts[0].clip == str(root / "00118.MTS"),
        )
        check("no proxied entries", rep_no.proxied == {})

        out_px, rep_px = conform_cutlist(cl, r, prefer_proxy=True)
        check(
            "with prefer_proxy -> proxy file",
            out_px.cuts[0].clip == str(prox / "00118.mp4"),
            out_px.cuts[0].clip,
        )
        check("proxied recorded", "00118.MTS" in rep_px.proxied)
        check(
            "report ok (all resolved)",
            rep_px.ok,
            rep_px.summary(),
        )


def test_real_grave_stakes_optional() -> None:
    print("conform — real Grave Stakes folder (optional, env-dependent)")
    videos = Path("/Users/kopetan_kakao/Desktop/Grave stakes/Videos")
    example = (
        Path(__file__).resolve().parents[2]
        / "examples"
        / "grave-stakes-teaser"
        / "cutlist_v3.json"
    )
    if not videos.is_dir():
        print("  SKIP  (footage folder not present on this machine)")
        return
    cl = Cutlist.load(example)
    out, rep = conform_cutlist(cl, MediaResolver([str(videos)]))
    check(
        "all 12 example cuts conform to real files",
        rep.ok and len(rep.resolved) == 11,  # 00221.MTS used twice
        rep.summary(),
    )
    check(
        "every conformed clip is an existing absolute file",
        all(Path(c.clip).is_file() for c in out.cuts),
    )


def main() -> int:
    for fn in (
        test_resolve_and_report,
        test_proxy_preference,
        test_real_grave_stakes_optional,
    ):
        fn()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
