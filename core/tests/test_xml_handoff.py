"""Tests for core.xml_handoff. Run: python -m core.tests.test_xml_handoff
Hermetic. Every check is a wrong frame seen in a real Resolve 21 import on 07.10.2026."""

from __future__ import annotations

import sys

from core.xml_handoff import disabled_clips, fix_stills, index_media, remap_paths

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


def clip(cid, name, fid, path, w, scale=None, enabled="TRUE", full=True):
    flt = "" if scale is None else (
        "<filter><effect><name>Basic Motion</name><parameter><parameterid>scale</parameterid>\n<name>Scale</name>\n<valuemin>0</valuemin>\n"
        f"<valuemax>1000</valuemax>\n<value>{scale}</value></parameter></effect></filter>")
    fdef = (f'<file id="{fid}">\n<name>{name}</name><pathurl>file://localhost{path}</pathurl><media><video><samplecharacteristics>'
            f"<width>{w}</width><height>{w * 9 // 16}</height></samplecharacteristics></video></media></file>") if full else f'<file id="{fid}"/>'
    return f'<clipitem id="{cid}"><masterclipid>m</masterclipid><name>{name}</name><enabled>{enabled}</enabled>{fdef}{flt}</clipitem>'


XML = "<xmeml>" + "".join([
    clip("c1", "ru_card1.png", "file-1", "/Volumes/D/titles/ru_card1.png", 3840, 50),
    clip("c2", "intro.png", "file-2", "/Volumes/D/titles/intro.png", 3840, None),
    clip("c3", "final.png", "file-3", "/Volumes/D/titles/final.png", 3840, 152),
    clip("c4", "A005C112.MP4", "file-4", "/Volumes/D/proxy/A005C112.mp4", 1920, 100),
    clip("c5", "ru_card1.png", "file-1", "", 3840, 50, full=False),
    clip("c6", "1_004.WAV", "file-6", "/Volumes/D/proxy/1_004.WAV", 0, None, enabled="FALSE"),
]) + "</xmeml>"


def test_stills() -> None:
    print("xml_handoff - oversized stills")
    out, n = fix_stills(XML)
    check("four still clips touched (incl. the short file ref)", n == 4, str(n))
    check("explicit 50 becomes 100, twice", out.count("<value>100</value>") == 3, out)   # 2 stills + the camera clip's own 100
    check("no filter = native 100 % -> filter with 200", "<value>200</value>" in out)
    check("152 is kept (scale-to-frame clip)", "<value>152</value>" in out and "<value>304</value>" not in out)
    check("stills renamed to the 1080 twin, name and path", "<name>ru_card1_1080.png</name>" in out and "/titles/final_1080.png</pathurl>" in out)
    check("camera clip untouched", "<name>A005C112.MP4</name>" in out and "<width>1920</width>" in out)
    check("no 3840 left", "<width>3840</width>" not in out)


def test_paths() -> None:
    print("xml_handoff - pathurl remap")
    idx = index_media(["MEDIA/cam/A005C112.mp4", "MEDIA/cam/1_004.wav", "MEDIA/titles/ru_card1.png", "MEDIA/titles/intro.png"])
    out, missing = remap_paths(XML, idx, prefix="PKG/")
    check("case-insensitive hit for .WAV", "file://localhost/PKG/MEDIA/cam/1_004.wav" in out)
    check("missing file reported, url left alone", missing == ["final.png"] and "/Volumes/D/titles/final.png" in out, str(missing))
    try:
        index_media(["a/x.mov", "b/X.MOV"]); ok = False
    except ValueError:
        ok = True
    check("basename collision is an error", ok)


def test_disabled() -> None:
    print("xml_handoff - disabled clips")
    check("one disabled clip named", disabled_clips(XML) == ["1_004.WAV"], str(disabled_clips(XML)))


def main() -> int:
    for t in (test_stills, test_paths, test_disabled):
        t()
    print(f"\nxml_handoff: {_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
