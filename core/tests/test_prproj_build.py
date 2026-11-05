"""Tests for the Resolve -> .prproj builder.

The pure parts always run. The build itself needs a real Premiere project as
template; set PRPROJ_TEMPLATE or those
checks are skipped. Premiere is never started: output is checked
structurally and read back.

Run:  python -m core.tests.test_prproj_build
"""

from __future__ import annotations

import gzip
import os
import re
import sys
import tempfile
from pathlib import Path

from core.prproj_build import TPS, build, esc, frame_ticks, level_value, verify

_p = _f = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _p, _f
    if ok:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


SPEC = {
    "project": "T",
    "media": {
        "v": {"path": "/Volumes/X/cam/A_0001.mxf", "name": "A_0001.mxf", "bin": "Cam/Day1",
              "video": {"w": 1920, "h": 1080, "fps": 24, "frames": 1000}, "audio": None, "tc_ticks": 0},
        "m": {"path": "/Volumes/X/snd/boom.mxf", "name": "boom & co.mxf", "bin": "Snd", "video": None,
              "audio": {"channels": 1, "rate": 48000, "samples": 480000}, "tc_ticks": 0},
        "av": {"path": "/Volumes/X/IMG.MOV", "name": "IMG.MOV", "bin": "Cam/Day1",
               "video": {"w": 3840, "h": 2160, "fps": 25, "frames": 500},
               "audio": {"channels": 2, "rate": 48000, "samples": 960000}, "tc_ticks": 0},
    },
    "timelines": [{
        "name": "T 1", "fps": 24, "w": 1920, "h": 1080, "bin": "Sequences",
        "video": [[{"media": "v", "start": 0, "end": 100, "src_in": 2.0, "speed": 1.0, "name": "A"},
                   {"media": "av", "start": 100, "end": 200, "src_in": 1.0, "speed": 0.5, "name": "I",
                    "link": 1, "label": "flag", "tf": {"x": 0.52, "y": 0.5, "scale": 75.0, "rot": 0.0}}],
                  [{"media": "v", "start": 0, "end": 50, "src_in": 0, "speed": 1.0, "name": "A"}]],
        "audio": [{"kind": "mono", "items": [{"media": "m", "start": 0, "end": 100, "src_in": 2.0, "speed": 1.0, "name": "b", "gain_db": -6.0}]},
                  {"kind": "stereo", "items": [{"media": "av", "start": 100, "end": 200, "src_in": 1.0, "speed": 0.5,
                                                 "name": "I", "link": 1}]}],
        "hidden_video": [1], "hidden_audio": [],
    }],
}


def test_pure() -> None:
    check("24 fps tick", frame_ticks(24) == TPS // 24)
    check("23.976 uses the NTSC fraction", frame_ticks(23.976) == TPS * 1001 // 24000)
    check("escape", esc("a & <b>") == "a &amp; &lt;b&gt;")
    check("0 dB level is 0.1778", abs(level_value(0.0) - 0.177828) < 1e-5)
    check("+15 dB is the top of the scale", level_value(15.0) == 1.0 and level_value(40.0) == 1.0)
    check("-100 dB is silence", level_value(-100.0) == 0.0)
    good = '<P>\n\t<A ObjectID="1">\n\t\t<R ObjectRef="2"/>\n\t</A>\n\t<B ObjectID="2">\n\t</B>\n</P>\n'
    check("verify accepts a closed graph", not verify(good)["dangling_refs"])
    check("verify finds a dangling ref", verify(good.replace('ObjectRef="2"', 'ObjectRef="9"'))["dangling_refs"] == ["9"])
    check("verify finds a duplicate id", verify(good.replace('ObjectID="2"', 'ObjectID="1"'))["duplicate_ids"] == ["1"])


def test_build() -> None:
    tpl = os.environ.get("PRPROJ_TEMPLATE") or ""
    if not tpl or not Path(tpl).exists():
        print("  skip build tests: no template project")
        return
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "o.prproj"
        r = build(SPEC, tpl, out)
        check("closed graph, unique ids", not r["dangling_refs"] and not r["duplicate_ids"], str(r))
        check("item counts", r["video_items"] == 3 and r["audio_items"] == 2, str(r))
        x = gzip.open(out).read().decode()
        check("mono track is ChannelType 0", re.search(r"<AudioClipTrack .*?<ChannelType>0</ChannelType>", x, re.S) is not None)
        check("speed written", "<PlaybackSpeed>0.5</PlaybackSpeed>" in x)
        check("hidden track muted", x.count("<IsMuted>true</IsMuted>") == 1)
        check("name escaped once", "boom &amp; co.mxf" in x and "&amp;amp;" not in x)
        check("bins from folders", x.count("<BinProjectItem ") == 4, str(x.count("<BinProjectItem ")))
        ticks = 100 * frame_ticks(24)
        check("volume component on the gained clip only", x.count("<FilterMatchName>Internal Volume Mono</FilterMatchName>") == 1
              and "Internal Volume Stereo" not in x.split("<Sequence ")[1], "")
        check("level stored as 10^((dB-15)/20)", "0.08912509" in x)
        check("clip end in ticks", f"<End>{ticks}</End>" in x)


def main() -> int:
    test_pure()
    test_build()
    print(f"\nprproj_build: {_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
