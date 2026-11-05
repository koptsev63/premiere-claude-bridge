"""Avid project settings: write a new `<name>.avp` + `<name> Settings.xml` from a real project's pair.

The bin (`.avb`, `core.avb_build`) is the project's content; the `.avp` is its settings and a
separate file. Only edits that keep every byte offset and length are made, so the
tag/length framing of the file cannot break:

* the project GUID (`PRJ_GUID`, `PRJ_GUID_V2`, 36 characters, both copies);
* the stamp in the header (`YYYY/MM/DD HH:MM:SS`, 19 characters);
* project format integers (`PRJ_*` / `ATN_*`, 4-byte little-endian), by name, only names
  that already exist in the template.

NOT touched, on purpose: the bin registry (one `ELIF` record per known bin, with absolute
paths of the machine the project was made on) and the slot table behind it. Its size is tied
to the record count in a way one sample cannot explain, so it is left exactly as the template
has it; Avid lists those bins as missing until they are found or removed there. The settings
XML is copied unchanged (audio mix, media creation, general, video display ...).

Format notes (decoded from two projects, Media Composer 25.12 and 23.3.1): a short header
(`Domain`, `AObjDoc`, stamp, `Media Composer <ver>`), then tagged sections written with the tag
reversed (`ATTR` -> `RTTA`, `FILE` -> `ELIF`, `Iprj` -> `jrpI`); an attribute is
`u32 type (1 int, 2 string) | u16 name length | name | value` (int: i32, string: u16 length + text).

Not opened in Avid.
"""

from __future__ import annotations

import re
import shutil
import struct
import time
import uuid
from pathlib import Path

GUID_KEYS = ("PRJ_GUID_V2", "PRJ_GUID")
_ATTR = re.compile(rb"(\x01\x00\x00\x00|\x02\x00\x00\x00)([\x01-\x40])\x00((?:PRJ|ATN)_[A-Z0-9_]+)")
_STAMP_AT = 24
_STAMP = re.compile(rb"\d{4}/\d\d/\d\d \d\d:\d\d:\d\d")

#: project formats seen in real projects: name -> {attribute: int}
PRESETS = {
    "hd-1080p24": {"PRJ_PROJ_TYPE": 10, "PRJ_TRUE24FPS": 1, "PRJ_HDRASTER_WIDTH": 1920, "PRJ_COLOR_SPACE": 8},
}


def read_attrs(data: bytes) -> dict:
    """Project attributes by name -> (offset of the value, type, value)."""
    out = {}
    for m in _ATTR.finditer(data):
        t = struct.unpack("<I", m.group(1))[0]
        name = m.group(3).decode()
        o = m.end()
        if t == 1:
            out[name] = (o, 1, struct.unpack("<i", data[o:o + 4])[0])
        else:
            n = struct.unpack("<H", data[o:o + 2])[0]
            out[name] = (o + 2, 2, data[o + 2:o + 2 + n].decode("latin1"))
    return out


def new_guid(like: str | None = None) -> str:
    """A time-based GUID; the clock/node tail of `like` (the template's) is kept, as one
    Avid install writes the same tail into every GUID it makes."""
    head = str(uuid.uuid1()).upper().split("-")[:3]
    tail = like.split("-")[3:] if like and like.count("-") == 4 else str(uuid.uuid1()).upper().split("-")[3:]
    return "-".join(head + tail)


def build_project(template_avp: str | Path, out_dir: str | Path, name: str,
                  template_settings: str | Path | None = None, preset: str | dict | None = None,
                  guid: str | None = None, stamp: float | None = None) -> dict:
    """Write `<out_dir>/<name>.avp` (+ `<name> Settings.xml`). Returns what was changed."""
    data = bytearray(Path(template_avp).read_bytes())
    if not data.startswith(b"\x06\x00DomainDJBO\x07\x00AObjDoc"):
        raise ValueError("not an Avid project file (header differs)")
    attrs = read_attrs(bytes(data))
    changes: dict = {}
    # project GUID, both copies
    like = next((attrs[k][2] for k in GUID_KEYS if k in attrs), None)
    guid = (guid or new_guid(like)).upper()
    if len(guid) != 36:
        raise ValueError("GUID must be 36 characters")
    for k in GUID_KEYS:
        if k in attrs:
            o, t, v = attrs[k]
            assert t == 2 and len(v) == 36, f"{k} is not a 36-character string"
            data[o:o + 36] = guid.encode()
            changes[k] = guid
    if not any(k in attrs for k in GUID_KEYS):
        changes["PRJ_GUID"] = "template has none: left as is"
    # header stamp
    st = time.strftime("%Y/%m/%d %H:%M:%S", time.localtime(stamp if stamp is not None else time.time()))
    if not _STAMP.fullmatch(bytes(data[_STAMP_AT:_STAMP_AT + 19])):
        raise ValueError("header stamp not where expected")
    data[_STAMP_AT:_STAMP_AT + 19] = st.encode()
    changes["stamp"] = st
    # format integers
    fmt = PRESETS[preset] if isinstance(preset, str) else (preset or {})
    for k, v in fmt.items():
        if k not in attrs or attrs[k][1] != 1:
            raise KeyError(f"{k} is not an integer attribute of the template")
        data[attrs[k][0]:attrs[k][0] + 4] = struct.pack("<i", int(v))
        changes[k] = int(v)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    avp = out_dir / f"{name}.avp"
    avp.write_bytes(bytes(data))
    res = {"avp": str(avp), "changes": changes, "registry_kept": len(re.findall(rb"ELIF", bytes(data)))}
    if template_settings:
        sx = out_dir / f"{name} Settings.xml"
        shutil.copyfile(template_settings, sx)
        res["settings"] = str(sx)
    verify_project(template_avp, avp, allowed=set(changes))
    return res


def verify_project(template_avp, built_avp, allowed=None) -> None:
    """Same length; every differing byte lies in the GUID, the stamp or a named integer."""
    a, b = Path(template_avp).read_bytes(), Path(built_avp).read_bytes()
    if len(a) != len(b):
        raise AssertionError(f"length changed: {len(a)} -> {len(b)}")
    attrs = read_attrs(a)
    ok = [range(_STAMP_AT, _STAMP_AT + 19)]
    for k in GUID_KEYS:
        if k in attrs:
            ok.append(range(attrs[k][0], attrs[k][0] + 36))
    for k, (o, t, _) in attrs.items():
        if t == 1:
            ok.append(range(o, o + 4))
    bad = [i for i in range(len(a)) if a[i] != b[i] and not any(i in r for r in ok)]
    if bad:
        raise AssertionError(f"{len(bad)} bytes changed outside the allowed fields, first at {bad[0]}")
    if allowed is not None:
        for k in allowed:
            if k in PRESETS.get("hd-1080p24", {}) and k not in read_attrs(b):
                raise AssertionError(f"{k} lost")
    ra, rb = read_attrs(a), read_attrs(b)
    if set(ra) != set(rb):
        raise AssertionError("attribute set changed")


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="new Avid project settings from a real project's pair")
    ap.add_argument("template_avp")
    ap.add_argument("out_dir")
    ap.add_argument("name")
    ap.add_argument("--settings", help="template Settings.xml to copy")
    ap.add_argument("--preset", choices=sorted(PRESETS))
    a = ap.parse_args()
    print(json.dumps(build_project(a.template_avp, a.out_dir, a.name, a.settings, a.preset), ensure_ascii=False, indent=1))
