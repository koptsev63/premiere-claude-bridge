"""Audit a DaVinci Resolve timeline export (.drt) before rendering a grading pull.

Field origin (18.10.2026): an editor cut a festival teaser in Resolve on
proxies and sent a .drt. The colourist needed a "kolbasa" - the whole cut
re-linked to 4K S-Log3 camera originals, grade removed, ProRes 422 HQ.
Import plus relink looked clean and the frame-count matched to the frame,
yet ONE clip of 79 went out wrong three times in a row:

1. Resolve placed it with LeftOffset -1885: the pull carried a crowd shot
   instead of the orbit around the hero.
2. The "fix" trusted the .drt `<In>` value (191): a third, different shot.
3. The editor's real range was 17:48:20:17-17:48:23:13 = source frame 1884.
   Resolve had been closer (sign lost), and the editor's media-pool marks
   for that clip (MarkIn 1863 / MarkOut 1978) pointed there too.

A contact sheet every 7 s cannot see a wrong 72-frame shot. What catches it
is a per-clip comparison of two independent readings of the same cut - the
.drt text and what the NLE actually laid down - with a hard stop when they
disagree. Neither side is ground truth: the editor's source timecode is.
`audit()` reports, `AuditReport.assert_ok()` refuses, and `overrides` is
where the editor's answer goes.

Format notes, measured on Resolve 21.0.3 exports:
- a .drt is a zip: `SeqContainer/*.xml` holds `<Sm2TiVideoClip>` items with
  `<Name>`, `<Start>` (record frame, timeline start included), `<Duration>`
  and `<In>` (source frame);
- `<In>` may be `5509|80ffffffffffef3f`: integer part plus a little-endian
  IEEE double fraction (0.99999...) -> 5510. Truncating gives a false 1-frame
  drift on every such clip;
- `MediaPool/**/*.xml` holds `<Sm2MpVideoClip>` with `MarkInVideo` /
  `MarkOutVideo`: the LAST viewer marks per clip. Useful context in a
  disagreement, useless as a general check (36 of 57 camera uses in that
  cut fell outside their clip's marks).

Reversed and retimed clips (19.10.2026, found by the same editor): the one
bad clip was the only one he had played BACKWARDS. Resolve keeps speed in
`<MediaTimemapBA>` and `<In>` of a retimed clip counts CLIP time, not
source time. A plain clip carries a 9-byte stub (0x02 + double); a retimed
one a Qt map with `KeyframesBA` -> {"0": {X, Y}, "1": {X, Y}} in seconds
(X clip time, Y source time). Full reverse = (0, Ymax) -> (Xmax, 0): clip
frame k shows source frame n-1-k with n = Xmax * fps. For A004C086,
n = 2147, In 191, 72 frames: source 1955 down to 1884 - exactly the
editor's 17:48:23:13 - 17:48:20:17. `read_drt` now returns the FORWARD
source In and `kind="reverse"`; a grading pull carries the forward piece
(grading does not care about direction) and the editor flips it back.
Any other speed change is `kind="speed"` and always a stop until the
editor gives the source timecode.

Pure stdlib; the Resolve side (reading TimelineItem.GetLeftOffset and
re-laying a clip with MediaPool.AppendToTimeline, endFrame EXCLUSIVE) lives
in `core/adapters/resolve_run.py` callers.
"""

from __future__ import annotations

import re
import struct
import zipfile
from dataclasses import dataclass, field
from typing import Iterable

_TI = re.compile(r"<Sm2TiVideoClip .*?</Sm2TiVideoClip>", re.S)
_MP = re.compile(r"<Sm2MpVideoClip .*?</Sm2MpVideoClip>", re.S)


def _tag(block: str, name: str) -> str | None:
    m = re.search(r"<%s>([^<]*)<" % name, block)
    return m.group(1) if m else None


def parse_in(raw: str | None) -> int | None:
    """`'5509|80ffffffffffef3f'` -> 5510; `'191'` -> 191; empty -> None."""
    if not raw:
        return None
    ip, _, hx = raw.partition("|")
    v = float(ip)
    if len(hx) == 16:
        try:
            v += struct.unpack("<d", bytes.fromhex(hx))[0]
        except (ValueError, struct.error):
            pass
    return int(round(v))



def _qmap(b: bytes) -> dict:
    """Qt-serialised QVariantMap as Resolve writes it: version, count, UTF-16BE keys."""
    o = 4
    (n,) = struct.unpack_from(">I", b, o)
    o += 4
    d: dict = {}
    for _ in range(n):
        (kl,) = struct.unpack_from(">I", b, o)
        o += 4
        k = b[o:o + kl].decode("utf-16-be")
        o += kl
        (t,) = struct.unpack_from(">I", b, o)
        o += 5  # type + null flag
        if t == 6:
            (v,) = struct.unpack_from(">d", b, o)
            o += 8
        elif t == 2:
            (v,) = struct.unpack_from(">i", b, o)
            o += 4
        elif t == 1:
            v = b[o]
            o += 1
        elif t in (10, 12):
            (ln,) = struct.unpack_from(">I", b, o)
            o += 4
            raw = b"" if ln == 0xFFFFFFFF else b[o:o + ln]
            o += 0 if ln == 0xFFFFFFFF else ln
            v = raw if t == 10 else (_qmap(raw) if raw[:4] == b"\0\0\0\1" else raw)
        else:
            raise ValueError(f"timemap value type {t}")
        d[k] = v
    return d


def parse_timemap(raw: str | None) -> list[tuple[float, float]] | None:
    """`<MediaTimemapBA>` hex -> sorted (X clip s, Y source s) keyframes; None for a plain clip."""
    if not raw or len(raw) <= 18:
        return None
    try:
        kf = _qmap(bytes.fromhex(raw)).get("KeyframesBA")
    except (ValueError, struct.error):
        return None
    if not isinstance(kf, dict):
        return None
    return sorted((v["X"], v["Y"]) for v in kf.values() if isinstance(v, dict))


def retime(points: list[tuple[float, float]] | None, clip_in: int, duration: int,
           fps: int = 25) -> tuple[str | None, int]:
    """(kind, forward source In). kind None = plain, "reverse" = full -100 %, "speed" = anything else."""
    if not points or all(y == 0 for _, y in points):
        return None, clip_in                       # plain clip, or a still (titles carry Y = 0)
    if len(points) == 2 and points[0] == (0, 0) and abs(points[1][1] - points[1][0]) < 1e-6:
        return None, clip_in                       # identity written out in full
    if len(points) == 2 and points[0][0] == 0 and points[0][1] > 0 and points[1][1] == 0:
        n = int(round(points[1][0] * fps))
        head = n - 1 - clip_in                     # clip frame k shows source frame n-1-k
        return "reverse", head - duration + 1
    return "speed", clip_in

@dataclass(frozen=True)
class DrtClip:
    name: str
    start: int          # record frame, absolute (timeline start included)
    duration: int
    src_in: int | None  # FORWARD source frame; None for titles, generators, adjustment clips
    kind: str | None = None  # None, "reverse" (played backwards) or "speed" (any other retime)


@dataclass(frozen=True)
class PlacedClip:
    """What the NLE actually laid down (read from the live timeline)."""
    name: str
    start: int
    duration: int
    src_in: int


@dataclass
class Disagreement:
    name: str
    record: int          # relative to timeline start
    drt_in: int | None
    nle_in: int
    drt_duration: int
    nle_duration: int
    marks: tuple[int, int] | None

    def line(self) -> str:
        return (f"rec {self.record} {self.name}: drt In {self.drt_in}, NLE In {self.nle_in}, "
                f"dur {self.drt_duration}/{self.nle_duration}, editor marks {self.marks} "
                f"-> ask the editor for source TC")


@dataclass
class AuditReport:
    checked: int = 0
    disagreements: list[Disagreement] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    overridden: list[int] = field(default_factory=list)
    reversed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.disagreements and not self.unmatched

    def summary(self) -> str:
        head = (f"checked {self.checked}, disagree {len(self.disagreements)}, "
                f"unmatched {len(self.unmatched)}, editor overrides {len(self.overridden)}")
        return "\n".join([head] + [d.line() for d in self.disagreements] +
                         [f"unmatched {u}" for u in self.unmatched] +
                         [f"reverse {r}: the pull carries the forward piece, tell the editor to flip it back"
                          for r in self.reversed])

    def assert_ok(self) -> None:
        if not self.ok:
            raise DrtAuditFailure(self.summary())


class DrtAuditFailure(RuntimeError):
    """Raised instead of rendering a pull that does not match the editor's cut."""


def read_drt(path: str) -> tuple[list[DrtClip], dict[str, tuple[int, int]]]:
    """Video clips from SeqContainer and media-pool marks keyed by lower-case name."""
    clips: list[DrtClip] = []
    marks: dict[str, tuple[int, int]] = {}
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if not n.endswith(".xml"):
                continue
            x = z.read(n).decode("utf-8", "replace")
            if n.startswith("SeqContainer/"):
                for m in _TI.finditer(x):
                    b = m.group(0)
                    name, start, dur = _tag(b, "Name"), _tag(b, "Start"), _tag(b, "Duration")
                    if not (name and start and dur):
                        continue
                    d, src_in, kind = int(round(float(dur))), parse_in(_tag(b, "In")), None
                    if src_in is not None:
                        kind, src_in = retime(parse_timemap(_tag(b, "MediaTimemapBA")), src_in, d)
                    clips.append(DrtClip(name, int(round(float(start))), d, src_in, kind))
            elif n.startswith("MediaPool/"):
                for m in _MP.finditer(x):
                    b = m.group(0)
                    name, mi, mo = _tag(b, "Name"), _tag(b, "MarkInVideo"), _tag(b, "MarkOutVideo")
                    if name and mi and mo and mi.isdigit() and mo.isdigit():
                        marks[name.lower()] = (int(mi), int(mo))
    return clips, marks


def audit(drt: Iterable[DrtClip], placed: Iterable[PlacedClip], timeline_start: int,
          marks: dict[str, tuple[int, int]] | None = None,
          overrides: dict[int, int] | None = None) -> AuditReport:
    """Compare clip by clip. Match key = (record start, name without case).

    `overrides` maps record frame (relative to timeline start) -> source frame
    the editor confirmed; it replaces the .drt reading for that clip.
    """
    marks = marks or {}
    overrides = overrides or {}
    ref = {(c.start, c.name.lower()): c for c in drt}
    rep = AuditReport()
    for p in placed:
        c = ref.get((p.start, p.name.lower()))
        if c is None:
            rep.unmatched.append(f"{p.name} @ {p.start - timeline_start}")
            continue
        rep.checked += 1
        if c.src_in is None:
            continue
        rec = p.start - timeline_start
        want = c.src_in
        if c.kind == "reverse":
            rep.reversed.append(f"{p.name} @ {rec}: source {c.src_in}-{c.src_in + c.duration - 1}")
        if rec in overrides:
            want = overrides[rec]
            rep.overridden.append(rec)
        elif c.kind == "speed":
            want = None  # a speed ramp has no single source In: only the editor's TC settles it
        if p.src_in == want and p.duration == c.duration:
            continue
        rep.disagreements.append(Disagreement(p.name, rec, want, p.src_in, c.duration, p.duration,
                                              marks.get(p.name.lower())))
    return rep


def tc_to_frames(tc: str, fps: int = 25) -> int:
    h, m, s, f = (int(x) for x in re.split(r"[:;]", tc))
    return ((h * 60 + m) * 60 + s) * fps + f


def source_frame(editor_tc: str, clip_start_tc: str, fps: int = 25) -> int:
    """Editor says '17:48:20:17', the original starts at '17:47:05:08' -> 1884."""
    return tc_to_frames(editor_tc, fps) - tc_to_frames(clip_start_tc, fps)
