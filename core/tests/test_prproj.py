"""Tests for reading the human's edits back out of a saved .prproj.

Everything runs on a synthetic project built here — gzipped XML with the
same shapes Premiere writes, including the two traps that cost real time:
a razor half that carries no text block, and a sidecar whose timings sit
on transcript time instead of cut time.

Run:  python -m core.tests.test_prproj
"""

from __future__ import annotations

import base64
import gzip
import sys
import tempfile
from pathlib import Path

from core.prproj import (
    TICKS_PER_SECOND,
    Cue,
    conform_captions,
    cut_points,
    decode_block_text,
    fill_from_srt,
    merge_razor_halves,
    read_caption_tracks,
    read_sequence_names,
    read_video_tracks,
    read_xml,
    seconds_to_ticks,
    ticks_to_seconds,
)

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


# ---- fixture builders ------------------------------------------------- #

def _fb_string(s: str) -> bytes:
    """A FlatBuffer-style length-prefixed, null-terminated UTF-8 string."""
    b = s.encode("utf-8")
    return len(b).to_bytes(4, "little") + b + b"\x00"


def _block_payload(text: str) -> str:
    """Base64 blob shaped like Premiere's FormattedTextData."""
    buf = (b"\x30\x02\x00\x00" + b"\x00" * 8
           + _fb_string("AnimationType")
           + _fb_string("NotoSansAdlam-Regular")
           + _fb_string(text)
           + b"\x00\x00")
    return base64.b64encode(buf).decode()


def _caption_item(oid: int, start: float, end: float,
                  block: int | None) -> str:
    ref = (f'<BlockVector Version="1">'
           f'<BlockVectorItem Index="0" ObjectRef="{block}"/></BlockVector>'
           if block else "")
    return (
        f'<CaptionDataClipTrackItem ObjectID="{oid}" Version="3">'
        f'<DataClipTrackItem><ClipTrackItem><TrackItem Version="4">'
        f"<Start>{seconds_to_ticks(start)}</Start>"
        f"<End>{seconds_to_ticks(end)}</End>"
        f"</TrackItem></ClipTrackItem></DataClipTrackItem>{ref}"
        f"</CaptionDataClipTrackItem>"
    )


def _video_item(oid: int, start: float, end: float) -> str:
    # Premiere omits <Start> for an item at zero — the first clip of a cut.
    s = f"<Start>{seconds_to_ticks(start)}</Start>" if start else ""
    return (
        f'<VideoClipTrackItem ObjectID="{oid}" Version="8">'
        f'<ClipTrackItem><TrackItem Version="4">{s}'
        f"<End>{seconds_to_ticks(end)}</End>"
        f"</TrackItem></ClipTrackItem></VideoClipTrackItem>"
    )


def _track(tag: str, uid: str, oids: list[int]) -> str:
    items = "".join(
        f'<TrackItem Index="{i}" ObjectRef="{o}"/>'
        for i, o in enumerate(oids))
    return (f'<{tag} ObjectUID="{uid}" Version="1"><ClipItems>'
            f'<TrackItems Version="1">{items}</TrackItems>'
            f"</ClipItems></{tag}>")


def _project_xml() -> str:
    """One sequence: 2 picture clips cut at 10.0s, 3 caption items.

    Cue 2 (10.0–11.5) is the razor half: no text block, starting exactly
    on the picture cut. Cue 3 is an untouched line of its own.
    """
    blocks = (
        f'<Block ObjectID="901" Version="1">'
        f'<FormattedTextData Encoding="base64">'
        f'{_block_payload("first line\rsecond line")}'
        f"</FormattedTextData></Block>"
        f'<Block ObjectID="903" Version="1">'
        f'<FormattedTextData Encoding="base64">'
        f'{_block_payload("typed by hand")}'
        f"</FormattedTextData></Block>"
    )
    return (
        '<?xml version="1.0"?><PremiereData Version="3">'
        "<Sequence ObjectID=\"1\"><Name>Reel V1</Name></Sequence>"
        + _caption_item(801, 0.0, 10.0, 901)
        + _caption_item(802, 10.0, 11.5, None)
        + _caption_item(803, 11.5, 14.0, 903)
        + _track("CaptionDataClipTrack", "cap-uid", [801, 802, 803])
        + _video_item(701, 0.0, 10.0)
        + _video_item(702, 10.0, 14.0)
        + _track("VideoClipTrack", "vid-uid", [701, 702])
        + blocks
        + "</PremiereData>"
    )


def _write_project(tmp: Path, *, gzipped: bool = True) -> Path:
    path = tmp / ("fixture.prproj" if gzipped else "fixture_plain.prproj")
    data = _project_xml().encode("utf-8")
    path.write_bytes(gzip.compress(data) if gzipped else data)
    return path


SIDECAR = """1
00:00:00,000 --> 00:00:11,900
first line second line

2
00:00:11,900 --> 00:00:15,400
machine text for the last cue
"""


# ---- tests ------------------------------------------------------------ #

def test_ticks() -> None:
    print("prproj — Premiere's tick clock")
    check("one second is 254016000000 ticks", TICKS_PER_SECOND == 254_016_000_000)
    # A real value out of the V1 reel: 3 frames at 29.97, i.e. frame-snapped
    # rather than a round 0.1s — cue times are never exactly what you typed.
    check("ticks -> seconds", abs(ticks_to_seconds(25402870080) - 0.1) < 1e-3,
          str(ticks_to_seconds(25402870080)))
    check("seconds -> ticks is a string (ExtendScript rejects big floats)",
          seconds_to_ticks(0.1) == "25401600000",
          seconds_to_ticks(0.1))
    check("round trip", abs(ticks_to_seconds(seconds_to_ticks(8.73)) - 8.73) < 1e-6)


def test_block_decode() -> None:
    print("prproj — caption text out of the FlatBuffer blob")
    text = decode_block_text(_block_payload("Ну что, друзья\rбудущее наступило"))
    check("cyrillic survives", text == "Ну что, друзья\nбудущее наступило", str(text))
    check("carriage return becomes a newline", "\n" in (text or ""))
    check("metadata tokens are not mistaken for the line",
          "NotoSansAdlam" not in (text or ""))
    check("short line still wins over the font name",
          decode_block_text(_block_payload("До встречи.")) == "До встречи.",
          str(decode_block_text(_block_payload("До встречи."))))
    check("garbage decodes to nothing", decode_block_text("!!!!not base64") is None)
    check("empty blob decodes to nothing", decode_block_text("") is None)


def test_read_tracks() -> None:
    print("prproj — tracks, cues and picture cuts")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        path = _write_project(tmp)
        tracks = read_caption_tracks(path)
        check("one caption track", len(tracks) == 1, str(len(tracks)))
        cues = tracks[0].cues
        check("three raw cues (razor half included)", len(cues) == 3, str(len(cues)))
        check("two cues carry text typed in Premiere",
              len(tracks[0].edited) == 2, str(len(tracks[0].edited)))
        check("razor half has no text of its own", cues[1].text is None)
        check("timings come back in seconds",
              abs(cues[0].end - 10.0) < 1e-6, str(cues[0].end))
        vt = read_video_tracks(path)
        check("one video track, two clips", len(vt) == 1 and len(vt[0]) == 2,
              str(vt))
        check("missing <Start> reads as zero, not a dropped clip",
              vt[0][0][0] == 0.0, str(vt[0][0]))
        check("cut points include the 10s cut", 10.0 in cut_points(vt[0]),
              str(cut_points(vt[0])))
        check("sequence name", read_sequence_names(path) == ["Reel V1"],
              str(read_sequence_names(path)))
        plain = _write_project(tmp, gzipped=False)
        check("uncompressed projects parse too",
              "<PremiereData" in read_xml(plain))


def test_razor_merge() -> None:
    print("prproj — razor halves merge only on a picture cut")
    cues = [Cue(0.0, 10.0, "first"), Cue(10.0, 11.5), Cue(11.5, 14.0, "third")]
    merged = merge_razor_halves([Cue(c.start, c.end, c.text) for c in cues],
                                cuts=[0.0, 10.0, 14.0])
    check("29->23 shape: the orphan folds into the line it continues",
          len(merged) == 2, str(len(merged)))
    check("the merged cue keeps the longer duration",
          abs(merged[0].end - 11.5) < 1e-6, str(merged[0].end))
    # The regression this rule exists for: adjacency alone ate whole lines.
    untouched = [Cue(0.0, 10.0, "first"), Cue(10.0, 11.5), Cue(11.5, 14.0)]
    kept = merge_razor_halves(untouched, cuts=[0.0, 14.0])
    check("a textless cue away from any cut is left alone",
          len(kept) == 3, str(len(kept)))


def test_fill_and_conform() -> None:
    print("prproj — resolving untouched lines from the sidecar")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        path = _write_project(tmp)
        srt = tmp / "sidecar.srt"
        srt.write_text(SIDECAR, encoding="utf-8")

        ct = conform_captions(path, srt)
        check("razor half merged, two cues on screen", len(ct.cues) == 2,
              str(len(ct.cues)))
        check("index alignment, not overlap", ct.alignment.mode == "index",
              str(ct.alignment))
        check("the hand-typed line is never overwritten",
              ct.cues[1].text == "typed by hand", str(ct.cues[1].text))
        check("the human's own wording is reported as an anchor rewrite",
              ct.alignment.anchors_rewritten == 1, str(ct.alignment))
        check("the untouched line came from the sidecar",
              ct.cues[0].source == "project" and ct.alignment.filled == 0,
              str(ct.alignment))

        # counts disagree -> anchored fallback, still no wrong text
        cues = [Cue(0.0, 10.0, "first line second line"), Cue(10.0, 11.9),
                Cue(11.9, 15.4), Cue(15.4, 16.0)]
        al = fill_from_srt(cues, srt)
        check("falls back to anchoring when the counts differ",
              al.mode == "anchored", str(al))
        check("anchor found by text", al.anchors_kept == 1, str(al))
        check("the gap after the anchor is filled in order",
              cues[1].text == "machine text for the last cue", str(cues[1].text))
        check("nothing invented past the sidecar",
              cues[3].text is None and al.unresolved >= 1, str(al))


def main() -> int:
    test_ticks()
    test_block_decode()
    test_read_tracks()
    test_razor_merge()
    test_fill_and_conform()
    print(f"\nprproj: {_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
