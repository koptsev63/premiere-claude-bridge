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
    parse_timemap,
    read_drt,
    retime,
    source_frame,
    tc_to_frames,
)

# <MediaTimemapBA> copied from the editor's real export (Resolve 21.0.3, 19.10.2026)
TM_REVERSE_A004 = "0000000100000006000000080059004d006100780000000600405573341d68dd0f000000080058004d00610078000000060040557851eb851eb8000000100055006e0069007100750065004900640000000a000000004800640034006200650031006600620038002d0036003200330030002d0034006200330037002d0038006200370036002d00350065006400340034003100300063003500350064003900000020004c00610073007400560061006c006900640059004f00660066007300650074000000060040557851eb851eb800000016004b00650079006600720061006d00650073004200410000000c000000017400000001000000020000000200310000000c00000000a700000001000000070000000c0069006e0074006500720070000000020000000000000000080059004f00750074000000060000000000000000000000000600590049006e0000000600000000000000000000000002005900000006000000000000000000000000080058004f00750074000000060000000000000000000000000600580049006e00000006000000000000000000000000020058000000060040557851eb851eb80000000200300000000c00000000a700000001000000070000000c0069006e0074006500720070000000020000000000000000080059004f00750074000000060000000000000000000000000600590049006e000000060000000000000000000000000200590000000600405573341d68dd0f000000080058004f00750074000000060000000000000000000000000600580049006e00000006000000000000000000000000020058000000060000000000000000000000000c0044006200540079007000650000000a00000000140053006d003200540069006d0065004d00610070"
TM_REVERSE_ROBOT = "0000000100000006000000080059004d0061007800000006004023333333333334000000080058004d0061007800000006004023333333333334000000100055006e0069007100750065004900640000000a000000004800380033006100630031003400630033002d0033003200660063002d0034003400340036002d0061006500660034002d00340033003200350032003700330036003400620066003600000020004c00610073007400560061006c006900640059004f006600660073006500740000000600402333333333333400000016004b00650079006600720061006d00650073004200410000000c000000017400000001000000020000000200310000000c00000000a700000001000000070000000c0069006e0074006500720070000000020000000000000000080059004f00750074000000060000000000000000000000000600590049006e0000000600000000000000000000000002005900000006000000000000000000000000080058004f00750074000000060000000000000000000000000600580049006e00000006000000000000000000000000020058000000060040233333333333340000000200300000000c00000000a700000001000000070000000c0069006e0074006500720070000000020000000000000000080059004f00750074000000060000000000000000000000000600590049006e0000000600000000000000000000000002005900000006004023333333333334000000080058004f00750074000000060000000000000000000000000600580049006e00000006000000000000000000000000020058000000060000000000000000000000000c0044006200540079007000650000000a00000000140053006d003200540069006d0065004d00610070"
TM_PLAIN = "02405ac00000000000"
TM_STILL = "0000000100000004000000080058004d00610078000000060040ed4c0000000000000000100055006e0069007100750065004900640000000a000000004800350063003000360031003000650061002d0062003900640063002d0034003800620037002d0039006600630031002d00630034006100370035003900630066003800360031006600000016004b00650079006600720061006d00650073004200410000000c000000017400000001000000020000000200310000000c00000000a700000001000000070000000c0069006e0074006500720070000000020000000000000000080059004f00750074000000060000000000000000000000000600590049006e0000000600000000000000000000000002005900000006000000000000000000000000080058004f00750074000000060000000000000000000000000600580049006e00000006000000000000000000000000020058000000060040ed4c00000000000000000200300000000c00000000a700000001000000070000000c0069006e0074006500720070000000020000000000000000080059004f00750074000000060000000000000000000000000600590049006e0000000600000000000000000000000002005900000006000000000000000000000000080058004f00750074000000060000000000000000000000000600580049006e00000006000000000000000000000000020058000000060000000000000000000000000c0044006200540079007000650000000a00000000140053006d003200540069006d0065004d00610070"

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


def _ti(name: str, start: int, dur: int, inn: str | None, tm: str = "") -> str:
    in_tag = f"<In>{inn}</In>" if inn is not None else ""
    tm_tag = f"<MediaTimemapBA>{tm}</MediaTimemapBA>" if tm else ""
    return (f'<Sm2TiVideoClip DbId="x"><Name>{name}</Name><Start>{start}</Start>'
            f"<Duration>{dur}</Duration>{in_tag}{tm_tag}</Sm2TiVideoClip>")


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


def test_reverse() -> None:
    print("drt_audit - a reversed clip, 19.10.2026 (the editor found the cause)")
    pts = parse_timemap(TM_REVERSE_A004)
    check("reverse map decoded", pts is not None and len(pts) == 2 and pts[0][0] == 0 and pts[1][1] == 0, str(pts))
    check("plain clip stub -> None", parse_timemap(TM_PLAIN) is None)
    check("still keeps Y = 0 -> not retimed", retime(parse_timemap(TM_STILL), 90011, 173) == (None, 90011))
    check("A004C086 In 191 x 72 reversed = source 1884..1955 (editor's TC)",
          retime(pts, 191, 72) == ("reverse", 1884), str(retime(pts, 191, 72)))
    check("robot shot In 15 x 42 reversed = source 183..224",
          retime(parse_timemap(TM_REVERSE_ROBOT), 15, 42) == ("reverse", 183))
    check("speed ramp is its own kind", retime([(0.0, 0.0), (10.0, 5.0)], 40, 25) == ("speed", 40))
    check("identity map written out = plain", retime([(0.0, 0.0), (10.0, 10.0)], 40, 25) == (None, 40))
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "rev.drt")
        seq = "<SeqContainer>" + _ti("A004C086_LG.mp4", 92981, 72, "191", TM_REVERSE_A004) + \
              _ti("ramp.mp4", 93053, 25, "40", "") + "</SeqContainer>"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("SeqContainer/a.xml", seq)
        clips, _ = read_drt(p)
        a = [c for c in clips if c.name.startswith("A004")][0]
        check("read_drt returns the forward source In", a.src_in == 1884 and a.kind == "reverse", str(a))
        rep = audit(clips, [PlacedClip("A004C086_LG.mp4", 92981, 72, 1884), PlacedClip("ramp.mp4", 93053, 25, 40)],
                    90000)
        check("forward re-lay of a reverse passes and is reported", rep.ok and len(rep.reversed) == 1, rep.summary())
        check("report tells to flip it back", "flip it back" in rep.summary())


def main() -> int:
    test_parse_in()
    test_read_and_audit()
    test_timecode()
    test_reverse()
    print(f"\ntest_drt_audit: {_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
