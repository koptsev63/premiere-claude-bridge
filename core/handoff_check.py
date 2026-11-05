"""Check an AAF or FCP7 XML before it goes to another NLE, and fix what can be fixed.

    check_aaf(path)            counts, edit rates, descriptors, empty tape names, dangling
                               references, media files missing on disk
    name_tapes(src, dst, hdr)  write the tape (reel) mobs' missing names
    check_xml(path)            sequences, clips, disabled clips, `-1` positions, file paths

Why tape names: a tape mob is the source reel of a file mob. Resolve writes every one with an
EMPTY name; Avid (and other importers that match by tape name) have nothing to link or sort by.
`name_tapes` gives each tape the reel name of its file's MXF header (``reel_name``), or the stem of
the media file when no header is at hand. That the empty name is what keeps a given AAF out of a
given Avid has NOT been confirmed: this is a check and a best-effort fix, not a guarantee.

What the XML check knows about Resolve's FCP7 export: clips next to a transition carry
``start``/``end`` of ``-1``; an importer must work the position out from the neighbours, so the
count is reported. Disabled clips are dropped by Resolve on import (`core.xml_handoff.disabled_clips`).

AAF needs `pyaaf2`; XML needs only the standard library.
"""

from __future__ import annotations

import collections
import os
import re
import shutil
import urllib.parse
import xml.etree.ElementTree as ET


# ------------------------------------------------------------------- AAF

def _tape_mobs(f):
    return [m for m in f.content.mobs if type(m).__name__ == "SourceMob" and type(m.descriptor).__name__ == "TapeDescriptor"]


def _locator(m):
    try:
        for loc in m.descriptor["Locator"].value:
            return urllib.parse.unquote(loc["URLString"].value.replace("file://", ""))
    except Exception:
        return None
    return None


def check_aaf(path: str, check_files: bool = True) -> dict:
    import aaf2
    f = aaf2.open(path, "r")
    try:
        ident = [(i["CompanyName"].value, i["ProductName"].value) for i in f.header["IdentificationList"].value]
        kinds = collections.Counter(type(m).__name__ for m in f.content.mobs)
        rates = collections.Counter()
        for m in f.content.mobs:
            for s in m.slots:
                rates[str(s.edit_rate)] += 1
        descs = collections.Counter(type(m.descriptor).__name__ for m in f.content.mobs
                                    if type(m).__name__ == "SourceMob" and m.descriptor is not None)
        ids = {str(m.mob_id) for m in f.content.mobs}
        dangling, outside = 0, 0
        tapes = {str(m.mob_id): m for m in _tape_mobs(f)}
        for m in f.content.mobs:
            if type(m).__name__ != "SourceMob" or type(m.descriptor).__name__ == "TapeDescriptor":
                continue
            for s in m.slots:
                seg = s.segment
                if type(seg).__name__ != "SourceClip":
                    continue
                if str(seg.mob_id) not in ids:
                    dangling += 1
                    continue
                t = tapes.get(str(seg.mob_id))
                if t is not None:
                    ts = [x for x in t.slots if x.slot_id == seg.slot_id]
                    L = getattr(ts[0].segment, "length", None) if ts else None
                    if not ts or (L is not None and seg.start + seg.length > L):
                        outside += 1
        empty = sum(1 for t in tapes.values() if not t.name)
        paths = [p for p in (_locator(m) for m in f.content.mobs if type(m).__name__ == "SourceMob" and type(m.descriptor).__name__ != "TapeDescriptor") if p]
        missing = [p for p in paths if check_files and not os.path.exists(p)]
        nonascii = sum(1 for m in f.content.mobs if m.name and any(ord(c) > 127 for c in m.name))
        res = {"writer": ident, "mobs": dict(kinds), "edit_rates": dict(rates), "descriptors": dict(descs),
               "tapes": len(tapes), "tapes_without_name": empty, "dangling_references": dangling,
               "tape_ranges_outside": outside, "media_paths": len(paths), "media_missing": len(missing),
               "non_ascii_mob_names": nonascii}
        notes = []
        if empty:
            notes.append(f"{empty} of {len(tapes)} tape mobs have no name: `name_tapes` can fill them")
        if dangling:
            notes.append(f"{dangling} references point at mobs that are not in the file")
        if len(rates) > 1:
            notes.append(f"several edit rates in one file: {sorted(rates)}")
        if missing:
            notes.append(f"{len(missing)} media files are not on this disk (first: {missing[0]})")
        res["notes"] = notes
        return res
    finally:
        f.close()


def name_tapes(src: str, dst: str, headers: dict | None = None) -> dict:
    """Copy `src` to `dst` and name its empty tape mobs. `headers`: path -> header (as in
    `core.avb_build.read_header`), used for ``reel_name``; otherwise the media file stem."""
    import aaf2
    shutil.copyfile(src, dst)
    f = aaf2.open(dst, "rw")
    named = collections.Counter()
    by_stem = {}
    try:
        mobs = {str(m.mob_id): m for m in f.content.mobs}
        for fm in list(f.content.mobs):
            if type(fm).__name__ != "SourceMob" or type(fm.descriptor).__name__ == "TapeDescriptor":
                continue
            path = _locator(fm)
            reel = None
            if headers and path in headers:
                reel = next((t.get("reel_name") for t in headers[path]["tracks"] if t.get("reel_name")), None)
            if not reel:
                reel = os.path.splitext(os.path.basename(path))[0] if path else (fm.name or None)
            if not reel:
                continue
            for s in fm.slots:
                seg = s.segment
                if type(seg).__name__ == "SourceClip" and str(seg.mob_id) in mobs:
                    t = mobs[str(seg.mob_id)]
                    if type(t.descriptor).__name__ == "TapeDescriptor" and not t.name:
                        t.name = reel
                        named[reel] += 1
        f.save()
    finally:
        f.close()
    return {"named": sum(named.values()), "distinct_names": len(named), "from_headers": bool(headers)}


# ------------------------------------------------------------------- XML

def check_xml(path: str, check_files: bool = True) -> dict:
    root = ET.parse(path).getroot()
    seqs = root.findall(".//sequence")
    clips = root.findall(".//clipitem")
    disabled = [c.findtext("name") for c in clips if (c.findtext("enabled") or "TRUE").upper() == "FALSE"]
    minus1 = sum(1 for c in clips if c.findtext("start") == "-1" or c.findtext("end") == "-1")
    files = {}
    for fe in root.iter("file"):
        p = fe.findtext("pathurl")
        if p:
            files[fe.get("id")] = urllib.parse.unquote(re.sub(r"^file://(localhost)?", "", p))
    missing = sorted({p for p in files.values() if check_files and not os.path.exists(p)})
    ids = [c.get("id") for c in clips if c.get("id")]
    dup = sorted({i for i in ids if ids.count(i) > 1})[:10]
    rates = collections.Counter(f"{r.findtext('timebase')}{'/NTSC' if (r.findtext('ntsc') or '').upper() == 'TRUE' else ''}"
                                for r in root.iter("rate") if r.findtext("timebase"))
    res = {"sequences": len(seqs), "clips": len(clips), "disabled_clips": len(disabled), "clips_with_minus1_position": minus1,
           "files": len(files), "files_missing": len(missing), "duplicate_clip_ids": dup, "timebases": dict(rates)}
    notes = []
    if disabled:
        notes.append(f"{len(disabled)} disabled clips: Resolve skips them on import (first: {disabled[0]})")
    if minus1:
        notes.append(f"{minus1} clips have start/end -1 (next to a transition): the importer works the position out")
    if missing:
        notes.append(f"{len(missing)} media files are not on this disk (first: {missing[0]})")
    res["notes"] = notes
    return res


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="check an AAF / FCP7 XML before hand-off")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("aaf")
    a1.add_argument("path")
    a1.add_argument("--no-files", action="store_true")
    a2 = sub.add_parser("name-tapes")
    a2.add_argument("src")
    a2.add_argument("dst")
    a2.add_argument("--hdr", help="header json (path -> header) for the reel names")
    a3 = sub.add_parser("xml")
    a3.add_argument("path")
    a3.add_argument("--no-files", action="store_true")
    a = ap.parse_args()
    if a.cmd == "aaf":
        out = check_aaf(a.path, not a.no_files)
    elif a.cmd == "xml":
        out = check_xml(a.path, not a.no_files)
    else:
        out = name_tapes(a.src, a.dst, json.load(open(a.hdr, encoding="utf-8")) if a.hdr else None)
    print(json.dumps(out, ensure_ascii=False, indent=1))
