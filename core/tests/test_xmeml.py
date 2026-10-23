"""Tests for the FCP7 XML writer (pure text, parsed back with ElementTree).

Run:  python -m core.tests.test_xmeml
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET

from core.cutlist import Cut, Cutlist, Marker
from core.xmeml import xmeml_string

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


def main() -> int:
    cl = Cutlist(sequence_name="t", fps=24.0, cuts=[
        Cut(clip="/m/a.mp4", in_=0.0, out=8.11, offset=0.0, label="A"),
        Cut(clip="/m/b.mp4", in_=0.0, out=4.92, offset=8.11, label="B"),
        Cut(clip="/m/c.mp4", in_=0.5, out=5.5, offset=13.03, label="C"),
    ], markers=[Marker(name="hit", time=13.03, comment="boom")], total_duration_sec=18.03)
    xml = xmeml_string(cl, audio=[("/m/music.wav", 0.0, 17.0, 1.0)], dissolves={8.11: 0.5},
                       overlays=[("/m/title.png", 15.0, 18.03)])
    root = ET.fromstring(xml.split("\n", 2)[2])
    seq = root.find("sequence")
    check("sequence duration in frames", seq.findtext("duration") == str(round(18.03 * 24)), seq.findtext("duration"))
    vtracks = seq.findall("media/video/track")
    check("two video tracks (V1 + title)", len(vtracks) == 2, str(len(vtracks)))
    clips = vtracks[0].findall("clipitem")
    check("three clips on V1", len(clips) == 3)
    ends = [int(c.findtext("end")) for c in clips]; starts = [int(c.findtext("start")) for c in clips]
    check("no gaps: end[i] == start[i+1]", ends[0] == starts[1] and ends[1] == starts[2], f"{ends} {starts}")
    check("in/out in frames", clips[2].findtext("in") == "12" and clips[2].findtext("out") == "132")
    tr = vtracks[0].findall("transitionitem")
    check("one dissolve, centred on the cut", len(tr) == 1 and int(tr[0].findtext("start")) == starts[1] - 6 and int(tr[0].findtext("end")) == starts[1] + 6, str([t.findtext("start") for t in tr]))
    a = seq.findall("media/audio/track/clipitem")
    check("music clip offset 1 s = 24 frames", a[0].findtext("start") == "24" and a[0].findtext("out") == "408")
    check("pathurl absolute file://localhost", "file://localhost/m/a.mp4" in xml)
    check("marker kept", seq.find("marker").findtext("comment") == "boom")
    check("title on V2 spans to the end", vtracks[1].find("clipitem").findtext("end") == str(round(18.03 * 24)))
    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
