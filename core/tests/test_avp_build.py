"""Tests for the Avid project settings writer (core.avp_build).

Needs a real .avp as template: set AVP_TEMPLATE, else the build tests are skipped.
A tiny synthetic file covers the attribute reader.

Run:  python -m core.tests.test_avp_build
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
from pathlib import Path

from core.avp_build import build_project, new_guid, read_attrs, verify_project

_p = _f = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _p, _f
    if ok:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


def attr_int(name: str, v: int) -> bytes:
    return struct.pack("<I", 1) + struct.pack("<H", len(name)) + name.encode() + struct.pack("<i", v)


def attr_str(name: str, v: str) -> bytes:
    return struct.pack("<I", 2) + struct.pack("<H", len(name)) + name.encode() + struct.pack("<H", len(v)) + v.encode()


def test_reader() -> None:
    blob = attr_str("PRJ_GUID", "3B01FC9A-64C5-11F1-8001-00CAF86E0100") + attr_int("PRJ_HDRASTER_WIDTH", 1920) + attr_int("ATN_PROJ3_CS_FMT", -1)
    a = read_attrs(blob)
    check("reads strings and ints", a["PRJ_GUID"][2].startswith("3B01FC9A") and a["PRJ_HDRASTER_WIDTH"][2] == 1920 and a["ATN_PROJ3_CS_FMT"][2] == -1, str(a))
    g = new_guid("3B01FC9A-64C5-11F1-8001-00CAF86E0100")
    check("new GUID keeps the writer's tail and length", g.endswith("8001-00CAF86E0100") and len(g) == 36, g)


def test_build() -> None:
    tpl = os.environ.get("AVP_TEMPLATE") or ""
    if not tpl or not Path(tpl).exists():
        print("  skip build tests: no template .avp")
        return
    with tempfile.TemporaryDirectory() as d:
        r = build_project(tpl, d, "T", preset="hd-1080p24")
        a, b = Path(tpl).read_bytes(), Path(r["avp"]).read_bytes()
        check("same length as the template", len(a) == len(b))
        ra, rb = read_attrs(a), read_attrs(b)
        diff = {k for k in ra if ra[k][2] != rb[k][2]}
        check("only the GUID differs when the preset equals the template", diff == {"PRJ_GUID", "PRJ_GUID_V2"}, str(diff))
        check("both GUID copies agree", rb["PRJ_GUID"][2] == rb["PRJ_GUID_V2"][2] != ra["PRJ_GUID"][2])
        check("bin registry kept as is", r["registry_kept"] == a.count(b"ELIF"))
        r2 = build_project(tpl, d, "T2", preset={"PRJ_HDRASTER_WIDTH": 1280})
        check("format integer patched in place", read_attrs(Path(r2["avp"]).read_bytes())["PRJ_HDRASTER_WIDTH"][2] == 1280)
        try:
            build_project(tpl, d, "T3", preset={"PRJ_NOPE": 1})
            check("unknown attribute refused", False)
        except KeyError:
            check("unknown attribute refused", True)
        tampered = bytearray(b)
        tampered[2000] ^= 0xFF
        bad = Path(d) / "bad.avp"
        bad.write_bytes(bytes(tampered))
        try:
            verify_project(tpl, bad)
            check("verify catches a changed byte outside the allowed fields", False)
        except AssertionError:
            check("verify catches a changed byte outside the allowed fields", True)


def main() -> int:
    test_reader()
    test_build()
    print(f"\navp_build: {_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
