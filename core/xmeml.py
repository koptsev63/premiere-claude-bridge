"""core.xmeml - write a Cutlist as FCP7 XML (xmeml v4) that Premiere Pro imports.

Field origin (23.10.2026, Treasures Wanted pilot): the cut was rendered by
ffmpeg from a JSON edit list, but delivery to Vladimir is a montage project,
not an mp4 (film-editing hard rule, mandate 15.08.2026). Premiere was not
running, so no panel: a text FCP7 XML is the hand-off. Everything is in
frames at the sequence rate (no fractional seconds: the "micro-gap" lesson).

  write_xmeml(cutlist, path, *, width, height, audio=[...], dissolves={...},
              overlays=[...])
  xmeml_string(...)  - same, returns the XML text

audio:     [(file, in_s, out_s, offset_s), ...] clips on A1 (music parts)
dissolves: {timeline_second: duration_s} centre-aligned cross dissolves on V1
overlays:  [(png, start_s, end_s), ...] stills on V2 (title cards)
markers:   from the cutlist (sequence markers, comment = marker.comment)
"""

from __future__ import annotations

import os
import urllib.parse
from pathlib import Path
from xml.sax.saxutils import escape

from core.cutlist import Cutlist


def _fr(sec: float, fps: float) -> int:
    return int(round(sec * fps))


def _pathurl(p: str) -> str:
    return "file://localhost" + urllib.parse.quote(os.path.abspath(p))


def _rate(fps: float) -> str:
    tb = int(round(fps))
    ntsc = "TRUE" if abs(fps - tb) > 0.01 else "FALSE"
    return f"<rate><timebase>{tb}</timebase><ntsc>{ntsc}</ntsc></rate>"


_SEEN: set = set()


def _file(fid: str, path: str, fps: float, w: int, h: int, has_audio: bool, still: bool = False,
          src_dims: tuple[int, int] | None = None, src_dur_frames: int | None = None) -> str:
    """24.10.2026 (podcast shorts, Premiere 26 «Сбой при импорте файла» without a message): a <file> is defined
    once per sequence and referenced by id afterwards; the file block names the SOURCE dimensions and its
    duration in frames, not the sequence dimensions."""
    if fid in _SEEN:
        return f"<file id=\"{fid}\"/>"
    _SEEN.add(fid)
    fw, fh = src_dims or (w, h)
    media = f"<video><samplecharacteristics>{_rate(fps)}<width>{fw}</width><height>{fh}</height><pixelaspectratio>square</pixelaspectratio></samplecharacteristics></video>"
    if has_audio:
        media += "<audio><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics><channelcount>2</channelcount></audio>"
    dur = f"<duration>{src_dur_frames}</duration>" if src_dur_frames else ""
    return (f"<file id=\"{fid}\"><name>{escape(os.path.basename(path))}</name><pathurl>{_pathurl(path)}</pathurl>"
            f"{_rate(fps)}{dur}<media>{media}</media></file>")


def _motion(scale: float | None) -> str:
    if not scale:
        return ""
    return ("<filter><effect><name>Basic Motion</name><effectid>basic</effectid><effectcategory>motion</effectcategory>"
            "<effecttype>motion</effecttype><mediatype>video</mediatype>"
            f"<parameter><parameterid>scale</parameterid><name>Scale</name><valuemin>0</valuemin><valuemax>1000</valuemax><value>{scale:g}</value></parameter>"
            "</effect></filter>")


def xmeml_string(cl: Cutlist, *, width: int = 1920, height: int = 1080,
                 audio: list[tuple[str, float, float, float]] | None = None,
                 dissolves: dict[float, float] | None = None,
                 overlays: list[tuple[str, float, float]] | None = None,
                 motion_scale: dict[int, float] | None = None,
                 src_info: dict[str, tuple[int, int, float]] | None = None) -> str:
    """motion_scale: {cut_index: percent} adds a Basic Motion scale filter to that V1 clip
    (24.10.2026, podcast shorts: 1920x1080 source inside a 1080x1920 sequence, scale 177.78)."""
    fps = cl.fps
    motion_scale = motion_scale or {}
    src_info = src_info or {}  # {path: (width, height, duration_s)} of the real media files
    _SEEN.clear()
    def _si(p):
        i = src_info.get(p)
        return (None, None) if not i else ((i[0], i[1]), _fr(i[2], fps))
    audio = audio or []; dissolves = dissolves or {}; overlays = overlays or []
    total = cl.total_duration_sec or max([c.timeline_end for c in cl.cuts] + [o[2] for o in overlays] + [a[3] + a[2] - a[1] for a in audio])
    T = _fr(total, fps)
    files: dict[str, str] = {}
    def fid(p):
        if p not in files: files[p] = f"file-{len(files) + 1}"
        return files[p]
    v1 = []
    for i, c in enumerate(cl.cuts):
        s, e = _fr(c.offset, fps), _fr(c.timeline_end, fps)
        ci, co = _fr(c.in_, fps), _fr(c.out, fps)
        # centre-aligned dissolve INTO this clip: Premiere expects the transitionitem before the clipitem
        d = dissolves.get(round(c.offset, 3))
        if d and i > 0:
            hd = _fr(d / 2, fps)
            v1.append(f"<transitionitem><start>{s - hd}</start><end>{s + hd}</end><alignment>center</alignment>{_rate(fps)}"
                      "<effect><name>Cross Dissolve</name><effectid>Cross Dissolve</effectid><effecttype>transition</effecttype>"
                      "<mediatype>video</mediatype><effectcategory>Dissolve</effectcategory></effect></transitionitem>")
        f = fid(c.clip); first = f not in [x for x in files.values() if x != f and False]
        v1.append(f"<clipitem id=\"v{i + 1}\"><name>{escape(c.label or os.path.basename(c.clip))}</name>{_rate(fps)}"
                  f"<start>{s}</start><end>{e}</end><in>{ci}</in><out>{co}</out>"
                  f"{_file(f, c.clip, fps, width, height, has_audio=any(a[0] == c.clip for a in audio), src_dims=_si(c.clip)[0], src_dur_frames=_si(c.clip)[1])}"
                  "<sourcetrack><mediatype>video</mediatype><trackindex>1</trackindex></sourcetrack>"
                  f"{_motion(motion_scale.get(i))}</clipitem>")
    v2 = []
    for j, (png, a, b) in enumerate(overlays):
        s, e = _fr(a, fps), _fr(b, fps)
        v2.append(f"<clipitem id=\"t{j + 1}\"><name>{escape(os.path.basename(png))}</name>{_rate(fps)}"
                  f"<start>{s}</start><end>{e}</end><in>0</in><out>{e - s}</out>"
                  f"{_file(fid(png), png, fps, width, height, has_audio=False, still=True, src_dims=_si(png)[0])}"
                  "<sourcetrack><mediatype>video</mediatype><trackindex>1</trackindex></sourcetrack></clipitem>")
    a1 = []
    for k, (wav, ain, aout, off) in enumerate(audio):
        s = _fr(off, fps); e = s + _fr(aout - ain, fps)
        a1.append(f"<clipitem id=\"a{k + 1}\"><name>{escape(os.path.basename(wav))}</name>{_rate(fps)}"
                  f"<start>{s}</start><end>{e}</end><in>{_fr(ain, fps)}</in><out>{_fr(aout, fps)}</out>"
                  f"{_file(fid(wav), wav, fps, width, height, has_audio=True, src_dims=_si(wav)[0], src_dur_frames=_si(wav)[1])}"
                  "<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack></clipitem>")
    mk = "".join(f"<marker><name>{escape(m.name)}</name><comment>{escape(m.comment)}</comment><in>{_fr(m.time, fps)}</in><out>-1</out></marker>"
                 for m in cl.markers)
    tracks_v = f"<track>{''.join(v1)}</track>" + (f"<track>{''.join(v2)}</track>" if v2 else "")
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE xmeml>\n<xmeml version=\"4\"><sequence id=\"seq-1\">"
            f"<name>{escape(cl.sequence_name)}</name><duration>{T}</duration>{_rate(fps)}"
            f"<media><video><format><samplecharacteristics>{_rate(fps)}<width>{width}</width><height>{height}</height>"
            "<pixelaspectratio>square</pixelaspectratio><fielddominance>none</fielddominance></samplecharacteristics></format>"
            f"{tracks_v}</video><audio><format><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics></format>"
            f"<track>{''.join(a1)}</track></audio></media>{mk}</sequence></xmeml>\n")


def write_xmeml(cl: Cutlist, path: str | Path, **kw) -> str:
    xml = xmeml_string(cl, **kw)
    Path(path).write_text(xml, encoding="utf-8")
    return str(path)
