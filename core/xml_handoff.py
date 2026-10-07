"""Hand a Premiere sequence to another machine / another NLE as FCP7 XML.

Field origin (07.10.2026): seven teaser timelines built in Premiere on 4K
originals had to reach an editor who works in DaVinci Resolve, on proxies.
Each rule below was a wrong frame in a real Resolve 21 import before it
became a line of code.

1. `<pathurl>` in the exported XML is an absolute path on the author's
   disk. `remap_paths()` rewrites every one to a package-relative layout,
   finding the file by basename (case-insensitive: Premiere keeps the
   original `.WAV` while the proxy on disk is `.wav`).
2. A 3840-px PNG title in a 1080p sequence sits at Scale 50 in Premiere.
   Resolve fits the still to the frame AND applies the XML scale on top:
   the title comes out half size. `fix_stills()` points the clip at a
   1920x1080 twin (`name_1080.png`) and rewrites the scale:
     - explicit 50  -> 100;
     - no motion filter at all (= 100 % of native 3840) -> a filter with 200;
     - any other value -> kept: that clip carries Premiere's "scale to
       frame size" flag, its value is already relative to the fitted frame
       (seen on a final title at 152; doubling it filled two screens).
3. Importing into Resolve: media into the Media Pool FIRST, then
   `ImportTimelineFromFile(xml, {"importSourceClips": False,
   "sourceClipsFolders": [root]})`. With `importSourceClips: True` and
   foreign paths the call returns None (the GUI shows a relink dialog; the
   API just fails). Import stills one by one: `ru_card1.png, ru_card2.png`
   in one ImportMedia call become an image sequence.
4. What FCP7 XML drops: caption tracks (ship SRT per timeline), custom
   audio fades (standard ones appear), audio effects, disabled clips
   (Resolve skips them, so its item count is lower by exactly those).

Pure text transforms, no NLE needed. The Resolve-side check lives in
`core/adapters/resolve_run.py`.
"""

from __future__ import annotations

import os
import re
import urllib.parse
from typing import Iterable

_FILTER = (
    "<filter><effect><name>Basic Motion</name><effectid>basic</effectid><effectcategory>motion</effectcategory>"
    "<effecttype>motion</effecttype><mediatype>video</mediatype><parameter><parameterid>scale</parameterid><name>Scale</name>"
    "<valuemin>0</valuemin><valuemax>1000</valuemax><value>{v}</value></parameter><parameter><parameterid>center</parameterid>"
    "<name>Center</name><value><horiz>0</horiz><vert>0</vert></value></parameter></effect></filter>"
)
_SCALE = re.compile(r"(<parameterid>scale</parameterid>\s*<name>Scale</name>\s*<valuemin>0</valuemin>\s*<valuemax>1000</valuemax>\s*<value>)([\d.]+)(</value>)")
_CLIP = re.compile(r"<clipitem id=.*?</clipitem>", re.S)
_FILEDEF = re.compile(r'<file id="([^"]+)">\s*<name>([^<]*)</name>.*?</file>', re.S)


def index_media(paths: Iterable[str]) -> dict[str, str]:
    """lowercased basename -> package-relative path. Raises on a basename collision."""
    idx: dict[str, str] = {}
    for p in paths:
        k = os.path.basename(p).lower()
        if k in idx and idx[k] != p:
            raise ValueError(f"basename collision: {idx[k]} / {p}")
        idx[k] = p
    return idx


def fix_stills(xml: str, src_width: int = 3840, src_height: int = 2160, suffix: str = "_1080", factor: float = 2.0,
               exts: tuple[str, ...] = (".png",)) -> tuple[str, int]:
    """Point oversized stills at their frame-size twins and fix the scale. Returns (xml, clips touched)."""
    ids = {m.group(1) for m in _FILEDEF.finditer(xml)
           if m.group(2).lower().endswith(exts) and f"<width>{src_width}</width>" in m.group(0)}
    n = 0

    def clip(m: re.Match) -> str:
        nonlocal n
        b = m.group(0)
        f = re.search(r'<file id="([^"]+)"', b)
        if not f or f.group(1) not in ids:
            return b
        n += 1

        def sc(x: re.Match) -> str:
            v = float(x.group(2))
            return x.group(1) + ("%g" % (v * factor if abs(v - 100.0 / factor) < 0.01 else v)) + x.group(3)

        b, k = _SCALE.subn(sc, b)
        if k == 0:
            tag = "</file>" if "</file>" in b else "/>"
            b = b.replace(tag, tag + _FILTER.format(v="%g" % (100 * factor)), 1)
        b = b.replace(f"<width>{src_width}</width>", f"<width>{int(src_width / factor)}</width>")
        b = b.replace(f"<height>{src_height}</height>", f"<height>{int(src_height / factor)}</height>")
        for e in exts:
            b = re.sub(r"(<name>|/)([^</]*?)" + re.escape(e) + r"(</name>|</pathurl>)",
                       lambda x: x.group(1) + x.group(2) + suffix + e + x.group(3), b)
        return b

    return _CLIP.sub(clip, xml), n


def remap_paths(xml: str, index: dict[str, str], prefix: str = "") -> tuple[str, list[str]]:
    """Rewrite every <pathurl> to file://localhost/<prefix><package path>. Returns (xml, missing basenames)."""
    missing: list[str] = []

    def fx(m: re.Match) -> str:
        name = os.path.basename(urllib.parse.unquote(m.group(1)))
        hit = index.get(name.lower())
        if hit is None:
            missing.append(name)
            return m.group(0)
        return "<pathurl>file://localhost/" + urllib.parse.quote(prefix + hit) + "</pathurl>"

    return re.sub(r"<pathurl>(.*?)</pathurl>", fx, xml), sorted(set(missing))


def disabled_clips(xml: str) -> list[str]:
    """Names of disabled clip items: the receiving NLE will drop them, expect its count lower by len()."""
    return [re.search(r"<name>([^<]*)</name>", b).group(1) for b in _CLIP.findall(xml) if "<enabled>FALSE</enabled>" in b[:400]]
