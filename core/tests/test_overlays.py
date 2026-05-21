"""Tests for overlays (lower-thirds / title / brand). No ffmpeg.

Run:  python -m core.tests.test_overlays
"""

from __future__ import annotations

import sys

from core.cutlist import Cutlist, Marker
from core.overlays import LIME, Overlay, _esc, filtergraph, from_markers

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


def test_drawtext_timing_and_kinds() -> None:
    print("overlays — drawtext timing + per-kind styling")
    lt = Overlay("HOOK", 0.0, 2.5, "lower_third", fontfile="/f.ttf").drawtext()
    check("gated by enable=between", "enable='between(t,0,2.5)'" in lt, lt)
    check("lower_third bottom box", "y=h-180" in lt and "box=1" in lt)
    title = Overlay("BIG", 1, 3, "title", fontfile="/f.ttf").drawtext()
    check("title centered + large", "x=(w-text_w)/2" in title
          and "fontsize=72" in title)
    brand = Overlay("@koptsev", 0, 5, "brand", fontfile="/f.ttf").drawtext()
    check("brand uses lime accent + corner",
          LIME in brand and "x=w-text_w-48" in brand, brand)


def test_escaping() -> None:
    print("overlays — drawtext text escaping")
    check("colon escaped", _esc("a:b") == "a\\:b")
    check("percent escaped", _esc("90%") == "90\\%")
    check("apostrophe replaced", "’" in _esc("it's"))
    o = Overlay("Time: 10%", 0, 1, fontfile="/f.ttf").drawtext()
    check("escaped text inside drawtext",
          "Time\\: 10\\%" in o, o)


def test_filtergraph() -> None:
    print("overlays — filtergraph chaining")
    ovs = [Overlay("A", 0, 1, fontfile="/f.ttf"),
           Overlay("B", 1, 2, fontfile="/f.ttf")]
    fg = filtergraph(ovs)
    check("two drawtext joined by comma", fg.count("drawtext=") == 2
          and "," in fg)
    check("empty list -> empty string", filtergraph([]) == "")


def test_from_markers() -> None:
    print("overlays — auto lower-thirds from cutlist markers")
    cl = Cutlist(sequence_name="s", fps=25, cuts=[],
                 markers=[Marker("HOOK", 0.0, "establishing"),
                          Marker("PAYOFF", 28.5, "")])
    ovs = from_markers(cl, hold=2.0)
    check("one overlay per marker", len(ovs) == 2)
    check("name + comment combined",
          ovs[0].text == "HOOK — establishing", ovs[0].text)
    check("comment-less marker -> name only", ovs[1].text == "PAYOFF")
    check("timing from marker + hold",
          ovs[1].start == 28.5 and ovs[1].end == 30.5)
    check("no markers -> no overlays",
          from_markers(Cutlist("x", 25, cuts=[], markers=[])) == [])


def main() -> int:
    for fn in (test_drawtext_timing_and_kinds, test_escaping,
               test_filtergraph, test_from_markers):
        fn()
    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
