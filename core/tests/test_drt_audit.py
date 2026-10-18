"""Tests for the .drt audit. Hermetic: builds a fake .drt zip in a tmpdir.

Run:  python -m core.tests.test_drt_audit
"""

from __future__ import annotations

import os
import sys
import tempfile
import zipfile

from core.drt_audit import (
    DrtAuditFailure,
    PlacedClip,
    audit,
    parse_in,
    read_drt,
    source_frame,
    tc_to_frames,
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


def _ti(name: str, start: int, dur: int, inn: str | None) -> str:
    in_tag = f"<In>{inn}</In>" if inn is not None else ""
    return (f'<Sm2TiVideoClip DbId="x"><Name>{name}</Name><Start>{start}</Start>'
            f"<Duration>{dur}</Duration>{in_tag}</Sm2TiVideoClip>")


def _mp(name: str, mi: int, mo: int) -> str:
    return (f'<Sm2MpVideoClip DbId="y"><Name>{name}</Name><MarkInVideo>{mi}</MarkInVideo>'
            f"<MarkOutVideo>{mo}</MarkOutVideo></Sm2MpVideoClip>")


def _drt(path: str) -> None:
    seq = "<SeqContainer>" + "".join([
        _ti("title_card.png", 90056, 173, None),
        _ti("A005C065_QM.mp4", 93053, 56, "5509|80ffffffffffef3f"),
        _ti("A004C086_LG.mp4", 92981, 72, "191"),
        _ti("A005C121_SB.mp4", 92549, 72, "253"),
    ]) + "</SeqContainer>"
    pool = "<MpFolder>" + _mp("A004C086_LG.mp4", 1863, 1978) + "</MpFolder>"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("project.xml", "<Project/>")
        z.writestr("SeqContainer/abc.xml", seq)
        z.writestr("MediaPool/Master/MpFolder.xml", pool)


def test_parse_in() -> None:
    print("drt_audit - In field with a double fraction")
    check("plain integer", parse_in("191") == 191)
    check("fraction 0.99999 rounds up", parse_in("5509|80ffffffffffef3f") == 5510)
    check("empty -> None", parse_in(None) is None and parse_in("") is None)
    check("bad hex keeps the integer", parse_in("40|zz") == 40)


def test_read_and_audit() -> None:
    print("drt_audit - the 18.10.2026 case, clip by clip")
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "cut.drt")
        _drt(p)
        clips, marks = read_drt(p)
        check("four clips read", len(clips) == 4, str(clips))
        check("title has no source In", [c for c in clips if c.name.startswith("title")][0].src_in is None)
        check("marks read", marks.get("a004c086_lg.mp4") == (1863, 1978), str(marks))

        start = 90000
        # what Resolve laid down after import + relink to originals
        placed = [
            PlacedClip("title_card.png", 90056, 173, 0),
            PlacedClip("A005C065_QM.MP4", 93053, 56, 5510),     # case differs, fraction rounded: agree
            PlacedClip("A004C086_LG.MP4", 92981, 72, -1885),    # the wrong shot
            PlacedClip("A005C121_SB.MP4", 92549, 72, 253),
        ]
        rep = audit(clips, placed, start, marks)
        check("one disagreement", len(rep.disagreements) == 1, rep.summary())
        dz = rep.disagreements[0]
        check("record frame relative to timeline start", dz.record == 2981)
        check("both readings reported", dz.drt_in == 191 and dz.nle_in == -1885)
        check("editor marks attached", dz.marks == (1863, 1978))
        check("report not ok", not rep.ok)
        try:
            rep.assert_ok()
            check("assert_ok refuses", False)
        except DrtAuditFailure as e:
            check("assert_ok refuses", "ask the editor" in str(e))

        # trusting the drt In (191) would "agree" with a wrong re-lay - the override is the truth
        relaid_wrong = placed[:2] + [PlacedClip("A004C086_LG.MP4", 92981, 72, 191)] + placed[3:]
        rep_wrong = audit(clips, relaid_wrong, start, marks, overrides={2981: 1884})
        check("editor override catches the drt-trusting fix", len(rep_wrong.disagreements) == 1)

        relaid_right = placed[:2] + [PlacedClip("A004C086_LG.MP4", 92981, 72, 1884)] + placed[3:]
        rep_ok = audit(clips, relaid_right, start, marks, overrides={2981: 1884})
        check("editor override + right re-lay = ok", rep_ok.ok, rep_ok.summary())
        check("override counted", rep_ok.overridden == [2981])

        extra = placed + [PlacedClip("ghost.mp4", 99999, 10, 0)]
        check("clip missing from drt is unmatched", audit(clips, extra, start).unmatched == ["ghost.mp4 @ 9999"])

        short = placed[:3] + [PlacedClip("A005C121_SB.MP4", 92549, 71, 253)]
        check("duration drift caught", len(audit(clips, short, start).disagreements) == 2)


def test_timecode() -> None:
    print("drt_audit - editor timecode to source frame")
    check("tc_to_frames", tc_to_frames("00:00:01:05") == 30)
    check("drop-frame separator accepted", tc_to_frames("00:00:01;05") == 30)
    check("17:48:20:17 on a clip from 17:47:05:08 = 1884",
          source_frame("17:48:20:17", "17:47:05:08") == 1884)
    check("out point inclusive gives 72 frames",
          source_frame("17:48:23:13", "17:47:05:08") - source_frame("17:48:20:17", "17:47:05:08") + 1 == 72)


def main() -> int:
    test_parse_in()
    test_read_and_audit()
    test_timecode()
    print(f"\ntest_drt_audit: {_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
