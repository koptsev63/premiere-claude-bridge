"""Read back what the human actually approved — parse a saved `.prproj`.

The bridge is good at proposing a cut. The editor then does what editors
do: nudges a boundary, retimes a caption, razors a line in two, deletes
the hook title that sat on his forehead. Until now that feedback was lost
— the machine kept rendering its own original plan.

This closes the loop:

    machine builds the sequence  →  human edits it in Premiere  →
    File > Save  →  `read_captions()`  →  every downstream render
    (grade, burn-in, deliverables) conforms to the human's version

Field origin (V1 reel, August 2026): 23 caption cues were built from a
Whisper transcript, the director then re-typed, re-timed and razored them
inside Premiere and deleted one title outright. Rendering the machine's
SRT after that would have shipped subtitles the director had already
rejected. Reading them back out of the saved project made the burn match
the approved cut exactly.

## File shape (verified against Premiere Pro 2025 projects)

A `.prproj` is gzipped XML (occasionally stored uncompressed — both are
handled). Caption cues live as:

    <CaptionDataClipTrackItem ObjectID="451" ...>
      <DataClipTrackItem><ClipTrackItem><TrackItem>
        <Start>25402870080</Start>       <- ticks, 254016000000 = 1 second
        <End>1016114803200</End>
      ...
      <BlockVector><BlockVectorItem Index="0" ObjectRef="631"/></BlockVector>
    </CaptionDataClipTrackItem>

    <Block ObjectID="631" ...>
      <FormattedTextData Encoding="base64">MAIAAAAAAABEMyIRDAAA...</Block>

`FormattedTextData` is a FlatBuffer. We do not pretend to parse it
formally — the payload we need (the run text) is stored as a
length-prefixed UTF-8 string (u32 little-endian length, then the bytes)
and is the *last* such string in the buffer; the earlier ones are
metadata tokens (`AnimationType`, the font name). Carriage returns
separate lines inside a cue.

## The two honest caveats

1. **A cue with no `Block` is not an empty cue.** Premiere only stores
   text in the project once the human types over it; otherwise it reads
   the text from the linked caption file. So `Cue.text is None` means
   "untouched", not "blank" — `fill_from_srt()` resolves those from the
   sidecar the sequence was built with.
2. **Razor halves look like empty cues.** Splitting a cue with the razor
   gives two items where only the left one carries the block. The right
   half starts exactly where the left one ends and continues the same
   line, so it resolves from the same source rather than being dropped.

Cut points of the picture itself are *not* parsed here: those come live
from the bridge (`pr_list_timeline`), which is cheaper and never stale.
"""

from __future__ import annotations

import base64
import gzip
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: Premiere's internal time base. One second of timeline time.
TICKS_PER_SECOND = 254_016_000_000

_ITEM_RE = re.compile(
    r'<CaptionDataClipTrackItem\b[^>]*ObjectID="(\d+)".*?'
    r"</CaptionDataClipTrackItem>", re.S
)
_TRACK_RE = re.compile(
    r"<CaptionDataClipTrack\b.*?</CaptionDataClipTrack>", re.S
)
_VIDEO_ITEM_RE = re.compile(
    r'<VideoClipTrackItem\b[^>]*ObjectID="(\d+)".*?</VideoClipTrackItem>',
    re.S,
)
_VIDEO_TRACK_RE = re.compile(
    r"<VideoClipTrack\b.*?</VideoClipTrack>", re.S
)
_TRACK_UID_RE = re.compile(r'<CaptionDataClipTrack\b[^>]*ObjectUID="([^"]+)"')
_TRACK_ITEM_RE = re.compile(r'<TrackItem Index="(\d+)" ObjectRef="(\d+)"')
_START_RE = re.compile(r"<Start>(-?\d+)</Start>")
_END_RE = re.compile(r"<End>(-?\d+)</End>")
_BLOCKREF_RE = re.compile(r'<BlockVectorItem[^>]*ObjectRef="(\d+)"')
_BLOCK_RE = re.compile(
    r'<Block ObjectID="(\d+)".*?<FormattedTextData[^>]*>(.*?)'
    r"</FormattedTextData>",
    re.S,
)
_SEQ_NAME_RE = re.compile(
    r"<Sequence\b.*?<Name>(.*?)</Name>", re.S
)


def ticks_to_seconds(ticks: int | str) -> float:
    return int(ticks) / TICKS_PER_SECOND


def seconds_to_ticks(seconds: float) -> str:
    """Ticks as the *string* ExtendScript wants (it rejects big floats)."""
    return str(int(round(seconds * TICKS_PER_SECOND)))


@dataclass
class Cue:
    """One caption cue as the human left it in the project."""

    start: float
    end: float
    text: str | None = None
    #: "project" = the human typed it here, "srt" = resolved from the
    #: sidecar, None = still unresolved.
    source: str | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        return {
            "s": round(self.start, 3),
            "e": round(self.end, 3),
            "t": self.text,
            "source": self.source,
        }


@dataclass
class CaptionTrack:
    """One caption track, cues in the project's own order.

    A project holds every sequence at once, and a sequence can carry more
    than one caption track (that is how an *editable* second track is
    built next to the imported one). Flattening them all into a single
    list mixes versions, so cues come back grouped per track.
    """

    uid: str
    cues: list[Cue] = field(default_factory=list)
    alignment: "Alignment | None" = None

    @property
    def span(self) -> tuple[float, float]:
        if not self.cues:
            return (0.0, 0.0)
        return (self.cues[0].start, self.cues[-1].end)

    @property
    def edited(self) -> list[Cue]:
        return [c for c in self.cues if c.source == "project"]


@dataclass
class ProjectRead:
    path: str
    sequences: list[str] = field(default_factory=list)
    tracks: list[CaptionTrack] = field(default_factory=list)

    @property
    def cues(self) -> list[Cue]:
        return [c for t in self.tracks for c in t.cues]

    @property
    def edited_cues(self) -> list[Cue]:
        """Cues the human actually retyped inside Premiere."""
        return [c for c in self.cues if c.source == "project"]

    def track_with_most_edits(self) -> CaptionTrack | None:
        """The track the human worked on — the one to conform renders to."""
        return max(self.tracks, key=lambda t: len(t.edited), default=None)


def read_xml(path: str | Path) -> str:
    """Return the project XML, whether it is gzipped (normal) or plain."""
    raw = Path(path).read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def _is_texty(s: str) -> bool:
    if not s or "\x00" in s:
        return False
    if not any(ch.isalpha() for ch in s):
        return False
    return all(ch.isprintable() or ch in "\r\n\t" for ch in s)


def _utf8_strings(buf: bytes, min_len: int = 3) -> list[tuple[int, int, str]]:
    """Every length-prefixed UTF-8 string in a FlatBuffer payload.

    Returns (start, end, text) so callers can reason about nesting: a
    length read *inside* a string's own bytes can decode as a valid
    shorter string, and that false hit always sits later in the buffer
    than the real one.
    """
    out: list[tuple[int, int, str]] = []
    n = len(buf)
    for i in range(0, max(0, n - 4)):
        ln = int.from_bytes(buf[i:i + 4], "little")
        if ln < min_len or ln > n - i - 4:
            continue
        try:
            s = buf[i + 4:i + 4 + ln].decode("utf-8")
        except UnicodeDecodeError:
            continue
        if _is_texty(s):
            out.append((i, i + 4 + ln, s))
    return out


def decode_block_text(b64: str) -> str | None:
    """Caption text out of one base64 `FormattedTextData` blob."""
    try:
        buf = base64.b64decode("".join(b64.split()), validate=False)
    except Exception:
        return None
    cands = _utf8_strings(buf)
    if not cands:
        return None
    # The run text is written last; everything before it is metadata
    # (AnimationType, the font name). Then prefer an enclosing candidate,
    # so a length byte landing inside the text cannot truncate it.
    best = max(cands, key=lambda c: (c[1], c[1] - c[0]))
    for c in cands:
        if c[0] < best[0] and c[1] >= best[1] and (c[1] - c[0]) > (best[1] - best[0]):
            best = c
    return best[2].replace("\r\n", "\n").replace("\r", "\n").strip()


def _blocks(xml: str) -> dict[str, str]:
    return {
        oid: text for oid, text in
        ((m.group(1), decode_block_text(m.group(2)))
         for m in _BLOCK_RE.finditer(xml))
        if text
    }


def _cue_items(xml: str) -> dict[str, Cue]:
    """Every caption item in the file, keyed by its ObjectID."""
    blocks = _blocks(xml)
    items: dict[str, Cue] = {}
    for m in _ITEM_RE.finditer(xml):
        chunk = m.group(0)
        s, e = _START_RE.search(chunk), _END_RE.search(chunk)
        if not s or not e:
            continue
        ref = _BLOCKREF_RE.search(chunk)
        text = blocks.get(ref.group(1)) if ref else None
        items[m.group(1)] = Cue(
            start=ticks_to_seconds(s.group(1)),
            end=ticks_to_seconds(e.group(1)),
            text=text,
            source="project" if text else None,
        )
    return items


def merge_razor_halves(cues: list[Cue], cuts: list[float],
                       tolerance: float = 0.06) -> list[Cue]:
    """Glue razor halves back into the single cue the viewer sees.

    Razoring a caption at a picture cut leaves two items carrying one
    line: the left keeps the text block, the right is an orphan Premiere
    still fills from the linked caption file. Read literally that orphan
    looks like a blank cue and would blank the line mid-sentence.

    **Adjacency alone cannot spot it** — in a caption track every cue
    butts against the next one, so "textless cue after a cue with text"
    swallows the rest of the track (measured on the V1 reel: 23 cues
    collapsed to 20, three lines eaten). The honest signal is the picture:
    a razor half starts *exactly on a cut*, because that is where the
    editor razored. With the cut list from the same project file the rule
    is exact — 6 halves found, 29 items resolving to the 23 lines on
    screen.
    """
    out: list[Cue] = []
    for cue in cues:
        prev = out[-1] if out else None
        on_cut = any(abs(cue.start - c) <= tolerance for c in cuts)
        if (prev is not None and cue.text is None and on_cut
                and abs(cue.start - prev.end) <= tolerance):
            prev.end = cue.end
            continue
        out.append(cue)
    return out


def read_video_tracks(path: str | Path, *,
                      xml: str | None = None) -> list[list[tuple[float, float]]]:
    """Picture items per video track, as (start, end) seconds.

    `<Start>` is omitted when an item sits at zero, which is exactly the
    first clip of a sequence — read it as 0 rather than skipping the clip.
    """
    xml = xml if xml is not None else read_xml(path)
    items: dict[str, tuple[float, float]] = {}
    for m in _VIDEO_ITEM_RE.finditer(xml):
        chunk = m.group(0)
        e = _END_RE.search(chunk)
        if not e:
            continue
        s = _START_RE.search(chunk)
        items[m.group(1)] = (
            ticks_to_seconds(s.group(1)) if s else 0.0,
            ticks_to_seconds(e.group(1)),
        )
    tracks: list[list[tuple[float, float]]] = []
    for m in _VIDEO_TRACK_RE.finditer(xml):
        refs = sorted(
            (int(i), r) for i, r in _TRACK_ITEM_RE.findall(m.group(0)))
        clips = [items[r] for _, r in refs if r in items]
        if clips:
            clips.sort()
            tracks.append(clips)
    return tracks


def cut_points(clips: list[tuple[float, float]]) -> list[float]:
    """Every boundary in a track's picture — where razors land."""
    pts = sorted({round(t, 3) for c in clips for t in c})
    return pts


def _cuts_for(span: tuple[float, float],
              tracks: list[list[tuple[float, float]]]) -> list[float]:
    """Pick the video track that belongs to the same sequence as a span.

    A project holds every sequence at once and this file format does not
    hand us the sequence graph cheaply, so: the track whose picture
    overlaps the caption span most. Pass `cuts=` explicitly (e.g. from
    `pr_list_timeline`) when you would rather not rely on that.
    """
    best, best_ov = [], 0.0
    a, b = span
    for clips in tracks:
        ta, tb = clips[0][0], clips[-1][1]
        ov = min(b, tb) - max(a, ta)
        if ov > best_ov:
            best, best_ov = clips, ov
    return cut_points(best) if best else []


def read_caption_tracks(path: str | Path, *,
                        xml: str | None = None) -> list[CaptionTrack]:
    """Caption cues grouped by the track that holds them."""
    xml = xml if xml is not None else read_xml(path)
    items = _cue_items(xml)
    tracks: list[CaptionTrack] = []
    claimed: set[str] = set()
    for m in _TRACK_RE.finditer(xml):
        chunk = m.group(0)
        uid_m = _TRACK_UID_RE.search(chunk)
        refs = sorted(
            ((int(i), r) for i, r in _TRACK_ITEM_RE.findall(chunk)),
        )
        cues = [items[r] for _, r in refs if r in items]
        claimed.update(r for _, r in refs)
        if cues:
            cues.sort(key=lambda c: (c.start, c.end))
            tracks.append(CaptionTrack(
                uid=uid_m.group(1) if uid_m else "", cues=cues))
    orphans = [c for oid, c in items.items() if oid not in claimed]
    if orphans:
        orphans.sort(key=lambda c: (c.start, c.end))
        tracks.append(CaptionTrack(uid="", cues=orphans))
    return tracks


def read_captions(path: str | Path, *, xml: str | None = None) -> list[Cue]:
    """Every caption cue in the saved project, in timeline order.

    Track-blind — use `read_caption_tracks()` when the project holds more
    than one sequence or a second editable caption track.
    """
    cues = [c for t in read_caption_tracks(path, xml=xml) for c in t.cues]
    cues.sort(key=lambda c: (c.start, c.end))
    return cues


def read_sequence_names(path: str | Path, *, xml: str | None = None) -> list[str]:
    xml = xml if xml is not None else read_xml(path)
    names, seen = [], set()
    for m in _SEQ_NAME_RE.finditer(xml):
        name = m.group(1).strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


@dataclass
class Alignment:
    """How the untouched cues were resolved against the sidecar."""

    mode: str = "none"      # "index" | "anchored" | "none"
    filled: int = 0         # cues that took their text from the sidecar
    anchors_kept: int = 0   # edited cues still matching the sidecar line
    anchors_rewritten: int = 0   # edited cues the human actually rewrote
    unresolved: int = 0


def _norm(s: str) -> str:
    return " ".join(s.split()).lower()


def fill_from_srt(cues: list[Cue], srt_path: str | Path) -> Alignment:
    """Resolve untouched cues from the sidecar the sequence was built with.

    **Not overlap-based.** The sidecar carries transcript time (with the
    pauses still in), the project carries cut time — on the V1 reel the
    same line sat at 9.20s in the sidecar and 8.73s on the timeline, and
    matching by overlap slid the text a full line out of place. Order is
    the reliable relation, so once razor halves are merged the cues and
    the sidecar lines correspond one to one.

    When the counts disagree (a cue deleted, another added) it falls back
    to anchoring on the cues the human typed himself and filling the gaps
    between them in order.
    """
    from core.subtitles import parse_srt  # local: keeps the import graph flat

    segs = parse_srt(Path(srt_path).read_text(encoding="utf-8")).get(
        "segments", [])
    if not segs or not cues:
        return Alignment(unresolved=sum(1 for c in cues if not c.text))

    if len(cues) == len(segs):
        al = Alignment(mode="index")
        for cue, seg in zip(cues, segs):
            line = str(seg.get("text", "")).strip()
            if cue.text:
                if _norm(cue.text) == _norm(line):
                    al.anchors_kept += 1
                else:
                    al.anchors_rewritten += 1
            else:
                cue.text, cue.source = line, "srt"
                al.filled += 1
        return al

    al = Alignment(mode="anchored")
    anchors: dict[int, int] = {}
    si = 0
    for ci, cue in enumerate(cues):
        if not cue.text:
            continue
        for j in range(si, len(segs)):
            if _norm(str(segs[j].get("text", ""))) == _norm(cue.text):
                anchors[ci], si = j, j + 1
                al.anchors_kept += 1
                break
        else:
            al.anchors_rewritten += 1
    bounds = sorted(anchors.items())
    for k, (ci, sj) in enumerate(bounds):
        nci, nsj = (bounds[k + 1] if k + 1 < len(bounds)
                    else (len(cues), len(segs)))
        gap_cues = [c for c in cues[ci + 1:nci] if not c.text]
        gap_segs = segs[sj + 1:nsj]
        for cue, seg in zip(gap_cues, gap_segs):
            cue.text = str(seg.get("text", "")).strip()
            cue.source = "srt"
            al.filled += 1
    al.unresolved = sum(1 for c in cues if not c.text)
    return al


def read_project(path: str | Path, srt: str | Path | None = None, *,
                 merge: bool = True) -> ProjectRead:
    """The whole round trip: saved project (+ its sidecar) → resolved cues.

    Without `srt` the untouched cues come back with `text=None` — that is
    the file's own truth, since Premiere keeps their text in the linked
    caption file rather than in the project.
    """
    xml = read_xml(path)
    tracks = read_caption_tracks(path, xml=xml)
    vtracks = read_video_tracks(path, xml=xml) if merge else []
    for track in tracks:
        if merge:
            track.cues = merge_razor_halves(
                track.cues, _cuts_for(track.span, vtracks))
        if srt:
            track.alignment = fill_from_srt(track.cues, srt)
    return ProjectRead(
        path=str(path),
        sequences=read_sequence_names(path, xml=xml),
        tracks=tracks,
    )


def conform_captions(path: str | Path, srt: str | Path | None = None, *,
                     track: int | None = None,
                     cuts: list[float] | None = None) -> CaptionTrack:
    """What the human approved, ready to render — the one call to use.

    Picks the caption track he actually worked on, glues razor halves back
    together against the picture cuts, resolves the untouched lines from
    the sidecar. Feed the result to `subtitles.to_srt/to_ass` and the burn
    matches his cut instead of the machine's original plan.
    """
    xml = read_xml(path)
    tracks = read_caption_tracks(path, xml=xml)
    if not tracks:
        return CaptionTrack(uid="")
    ct = tracks[track] if track is not None else max(
        tracks, key=lambda t: len(t.edited))
    ct.cues = merge_razor_halves(
        ct.cues,
        cuts if cuts is not None
        else _cuts_for(ct.span, read_video_tracks(path, xml=xml)),
    )
    if srt:
        ct.alignment = fill_from_srt(ct.cues, srt)
    return ct


def _main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: python -m core.prproj <project.prproj> "
              "[--srt sidecar.srt] [--track N] [--json]")
        return 2
    path = args[0]
    srt = args[args.index("--srt") + 1] if "--srt" in args else None
    pick = int(args[args.index("--track") + 1]) if "--track" in args else None
    pr = read_project(path, srt)
    tracks = [pr.tracks[pick]] if pick is not None else pr.tracks
    if "--json" in args:
        print(json.dumps(
            {"sequences": pr.sequences,
             "tracks": [{"uid": t.uid, "cues": [c.to_dict() for c in t.cues]}
                        for t in tracks]},
            ensure_ascii=False, indent=2))
        return 0
    print(f"{Path(path).name}: {len(pr.sequences)} sequences "
          f"({', '.join(pr.sequences)}), {len(pr.tracks)} caption tracks")
    for i, t in enumerate(pr.tracks):
        a, b = t.span
        print(f"  track {i}: {len(t.cues):3d} cues, {len(t.edited):3d} edited "
              f"in Premiere, {a:.2f}–{b:.2f}s")
    for t in tracks:
        print(f"\n--- track {pr.tracks.index(t)} ---")
        for c in t.cues:
            flag = {"project": "*", "srt": " ", None: "?"}[c.source]
            text = (c.text or "").replace("\n", " / ")
            print(f"  {flag} {c.start:7.2f} → {c.end:7.2f}  {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
