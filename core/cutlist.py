"""Cutlist — the NLE-agnostic intermediate representation.

A *cutlist* is the single source of truth for an edit. The editing brain
(the `film-editing` skill, Murch's Rule of Six) produces one cutlist; the
per-NLE adapters render it into Premiere, DaVinci Resolve, or Final Cut.

The on-disk JSON shape is the one already used in
`examples/grave-stakes-teaser/cutlist_v3.json`:

    {
      "sequence_name": "Teaser_v3_DataDriven",
      "preset": "AVCHD 1080p25.sqpreset",
      "fps": 25,
      "resolution": "1920x1080",
      "total_duration_sec": 61,
      "cuts": [
        {"clip": "00118.MTS", "in": 2, "out": 8, "offset": 0, "label": "HOOK"},
        ...
      ],
      "markers": [
        {"name": "HOOK", "time": 0, "comment": "Establishing"},
        ...
      ]
    }

`in` / `out` are source-media seconds. `offset` is the clip's start position
on the timeline, in seconds. Cuts may have gaps between them (offset of cut N
greater than the end of cut N-1).

OpenTimelineIO (OTIO) is the interchange layer. `to_otio()` / `from_otio()`
round-trip a cutlist losslessly: the full original dict is stored under
`timeline.metadata["premiere_claude_bridge"]` so a cutlist -> OTIO -> cutlist
round-trip is exact, while the OTIO structure itself is also a real timeline
(clips + gaps + markers) that third-party tools like Resolve can import.

OTIO is an *optional* dependency. Loading, validating and saving a cutlist
work without it; only `to_otio` / `from_otio` / the `*-otio` CLI verbs need it.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

METADATA_KEY = "premiere_claude_bridge"


# --------------------------------------------------------------------------- #
# Data model (pure Python, no third-party dependency)
# --------------------------------------------------------------------------- #


@dataclass
class Cut:
    """One clip placed on the timeline."""

    clip: str          # source media (filename or path)
    in_: float         # source in-point, seconds
    out: float         # source out-point, seconds
    offset: float      # timeline start position, seconds
    label: str = ""    # human label for the beat

    @property
    def duration(self) -> float:
        return round(self.out - self.in_, 6)

    @property
    def timeline_end(self) -> float:
        return round(self.offset + self.duration, 6)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Cut":
        return cls(
            clip=str(d["clip"]),
            in_=float(d["in"]),
            out=float(d["out"]),
            offset=float(d.get("offset", 0.0)),
            label=str(d.get("label", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "clip": self.clip,
            "in": self.in_,
            "out": self.out,
            "offset": self.offset,
            "label": self.label,
        }


@dataclass
class Marker:
    """A timeline marker (Premiere comment marker / Resolve marker)."""

    name: str
    time: float        # timeline position, seconds
    comment: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Marker":
        return cls(
            name=str(d.get("name", "")),
            time=float(d["time"]),
            comment=str(d.get("comment", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "time": self.time, "comment": self.comment}


@dataclass
class Cutlist:
    """A complete, NLE-agnostic edit decision."""

    sequence_name: str
    fps: float
    cuts: list[Cut] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    resolution: str = "1920x1080"
    preset: str | None = None
    total_duration_sec: float | None = None

    # ---- (de)serialization --------------------------------------------- #

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Cutlist":
        return cls(
            sequence_name=str(d.get("sequence_name", "Sequence")),
            fps=float(d.get("fps", 25)),
            cuts=[Cut.from_dict(c) for c in d.get("cuts", [])],
            markers=[Marker.from_dict(m) for m in d.get("markers", [])],
            resolution=str(d.get("resolution", "1920x1080")),
            preset=(str(d["preset"]) if d.get("preset") is not None else None),
            total_duration_sec=(
                float(d["total_duration_sec"])
                if d.get("total_duration_sec") is not None
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"sequence_name": self.sequence_name}
        if self.preset is not None:
            out["preset"] = self.preset
        out["fps"] = self.fps
        out["resolution"] = self.resolution
        if self.total_duration_sec is not None:
            out["total_duration_sec"] = self.total_duration_sec
        out["cuts"] = [c.to_dict() for c in self.cuts]
        out["markers"] = [m.to_dict() for m in self.markers]
        return out

    @classmethod
    def load(cls, path: str | Path) -> "Cutlist":
        return cls.from_dict(json.loads(Path(path).read_text()))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n")

    # ---- validation ---------------------------------------------------- #

    def validate(self) -> list[str]:
        """Return a list of human-readable problems (empty == valid)."""
        errs: list[str] = []
        if self.fps <= 0:
            errs.append(f"fps must be > 0, got {self.fps}")
        if not self.cuts:
            errs.append("cutlist has no cuts")
        for i, c in enumerate(self.cuts):
            if c.out <= c.in_:
                errs.append(
                    f"cut[{i}] ({c.label or c.clip}): out ({c.out}) "
                    f"<= in ({c.in_})"
                )
            if c.in_ < 0 or c.offset < 0:
                errs.append(
                    f"cut[{i}] ({c.label or c.clip}): negative in/offset"
                )
        # overlap check (cuts are single-track; overlap == authoring bug)
        ordered = sorted(self.cuts, key=lambda c: c.offset)
        for a, b in zip(ordered, ordered[1:]):
            if b.offset + 1e-6 < a.timeline_end:
                errs.append(
                    f"overlap: '{a.label or a.clip}' ends at "
                    f"{a.timeline_end}s but '{b.label or b.clip}' starts at "
                    f"{b.offset}s"
                )
        for i, m in enumerate(self.markers):
            if m.time < 0:
                errs.append(f"marker[{i}] ({m.name}): negative time")
        return errs


# --------------------------------------------------------------------------- #
# OpenTimelineIO bridge (optional dependency)
# --------------------------------------------------------------------------- #


class OtioUnavailable(RuntimeError):
    """Raised when an OTIO operation is requested but otio is not installed."""


def _require_otio():
    try:
        import opentimelineio as otio  # noqa: WPS433 (intentional lazy import)
    except ModuleNotFoundError as exc:  # pragma: no cover - env dependent
        raise OtioUnavailable(
            "opentimelineio is not installed. It is an optional dependency:\n"
            "    pip install opentimelineio\n"
            "Loading/validating/saving a cutlist works without it; only "
            "OTIO conversion (and the Resolve/FCP adapters) need it."
        ) from exc
    return otio


def to_otio(cutlist: Cutlist):
    """Build a real OTIO Timeline from a cutlist.

    - one video track, clips placed at their `offset` (Gaps fill holes)
    - the original cutlist dict is stored in timeline metadata for a
      lossless cutlist -> OTIO -> cutlist round-trip
    - markers are also emitted as OTIO markers on the track so third-party
      tools (Resolve) see them
    """
    otio = _require_otio()
    fps = cutlist.fps

    def rt(seconds: float):
        return otio.opentime.RationalTime(round(seconds * fps), fps)

    timeline = otio.schema.Timeline(name=cutlist.sequence_name)
    timeline.global_start_time = otio.opentime.RationalTime(0, fps)
    # Store the original cutlist as a JSON *string* (a scalar) rather than a
    # nested dict: nested dicts passed through OTIO's Any bindings raise
    # "bad any cast" on some versions. A string scalar is always safe and
    # serializes cleanly through every OTIO adapter.
    timeline.metadata[METADATA_KEY] = json.dumps(cutlist.to_dict())

    track = otio.schema.Track(
        name="V1", kind=otio.schema.TrackKind.Video
    )
    timeline.tracks.append(track)

    cursor = 0.0
    for cut in sorted(cutlist.cuts, key=lambda c: c.offset):
        gap = round(cut.offset - cursor, 6)
        if gap > 1e-6:
            track.append(
                otio.schema.Gap(
                    source_range=otio.opentime.TimeRange(
                        start_time=rt(0), duration=rt(gap)
                    )
                )
            )
            cursor = cut.offset
        clip = otio.schema.Clip(
            name=cut.label or Path(cut.clip).stem,
            media_reference=otio.schema.ExternalReference(
                target_url=cut.clip
            ),
            source_range=otio.opentime.TimeRange(
                start_time=rt(cut.in_), duration=rt(cut.duration)
            ),
        )
        track.append(clip)
        cursor = round(cursor + cut.duration, 6)

    for m in cutlist.markers:
        mk = otio.schema.Marker(
            name=m.name,
            marked_range=otio.opentime.TimeRange(
                start_time=rt(m.time),
                duration=otio.opentime.RationalTime(1, fps),
            ),
        )
        # scalar string via __setitem__ — safe across OTIO versions
        mk.metadata["pcb_comment"] = m.comment
        track.markers.append(mk)
    return timeline


def from_otio(timeline) -> Cutlist:
    """Recover a Cutlist from an OTIO Timeline.

    If the timeline carries our metadata (it was produced by `to_otio`),
    the original cutlist is returned exactly. Otherwise the cutlist is
    reconstructed structurally from clips/gaps/markers — good enough to
    drive an adapter, though `preset` and exact labels may be lost.
    """
    otio = _require_otio()

    raw = (timeline.metadata or {}).get(METADATA_KEY)
    if raw:
        # stored as a JSON string by to_otio()
        if isinstance(raw, str):
            return Cutlist.from_dict(json.loads(raw))
        # tolerate a dict too (hand-authored OTIO / older files)
        if isinstance(raw, dict) and "cutlist" in raw:
            return Cutlist.from_dict(raw["cutlist"])

    fps = (
        timeline.global_start_time.rate
        if timeline.global_start_time is not None
        else 25.0
    )

    def secs(rational) -> float:
        return round(rational.value / rational.rate, 6)

    cuts: list[Cut] = []
    markers: list[Marker] = []
    video_tracks = [
        t for t in timeline.tracks if t.kind == otio.schema.TrackKind.Video
    ]
    for track in video_tracks:
        cursor = 0.0
        for item in track:
            dur = secs(item.source_range.duration)
            if isinstance(item, otio.schema.Gap):
                cursor = round(cursor + dur, 6)
                continue
            if isinstance(item, otio.schema.Clip):
                ref = item.media_reference
                target = getattr(ref, "target_url", None) or item.name
                in_ = secs(item.source_range.start_time)
                cuts.append(
                    Cut(
                        clip=target,
                        in_=in_,
                        out=round(in_ + dur, 6),
                        offset=cursor,
                        label=item.name or "",
                    )
                )
                cursor = round(cursor + dur, 6)
        for mk in track.markers:
            cmt = (mk.metadata or {}).get("pcb_comment", "")
            markers.append(
                Marker(
                    name=mk.name,
                    time=secs(mk.marked_range.start_time),
                    comment=cmt,
                )
            )

    return Cutlist(
        sequence_name=timeline.name or "Sequence",
        fps=fps,
        cuts=cuts,
        markers=markers,
    )


_OTIO_FILE_IO_HINT = (
    "opentimelineio is installed but its JSON serializer fails in this "
    "environment ('bad any cast'). This is a known opentimelineio issue on "
    "bleeding-edge CPython (e.g. 3.14): otio's C++ Any bindings can't even "
    "parse otio's own builtin manifest. In-memory conversion "
    "(to_otio/from_otio) works on any Python with otio; for .otio FILE I/O "
    "use Python 3.12 or 3.13, where opentimelineio ships working wheels."
)


def write_otio(cutlist: Cutlist, path: str | Path) -> None:
    """Serialize a cutlist to a .otio file.

    Falls back from the adapter layer to the low-level core serializer, and
    raises a clear ``OtioUnavailable`` (not a cryptic C++ error) if the
    installed otio's JSON layer is broken on this interpreter.
    """
    otio = _require_otio()
    timeline = to_otio(cutlist)
    try:
        otio.adapters.write_to_file(timeline, str(path))
        return
    except Exception:  # adapter manifest broken on this Python — try core
        pass
    try:
        text = otio.core.serialize_json_to_string(timeline)
    except Exception as exc:  # otio JSON layer broken in this env
        raise OtioUnavailable(_OTIO_FILE_IO_HINT) from exc
    Path(path).write_text(text)


def read_otio(path: str | Path) -> Cutlist:
    otio = _require_otio()
    try:
        return from_otio(otio.adapters.read_from_file(str(path)))
    except Exception:
        pass
    try:
        tl = otio.core.deserialize_json_from_string(Path(path).read_text())
    except Exception as exc:
        raise OtioUnavailable(_OTIO_FILE_IO_HINT) from exc
    return from_otio(tl)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m core.cutlist",
        description="Cutlist IR: validate and convert to/from OpenTimelineIO.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pv = sub.add_parser("validate", help="validate a cutlist JSON")
    pv.add_argument("json")

    pt = sub.add_parser("to-otio", help="cutlist JSON -> .otio")
    pt.add_argument("json")
    pt.add_argument("otio")

    pf = sub.add_parser("from-otio", help=".otio -> cutlist JSON")
    pf.add_argument("otio")
    pf.add_argument("json")

    pr = sub.add_parser(
        "roundtrip", help="assert cutlist == from_otio(to_otio(cutlist))"
    )
    pr.add_argument("json")

    args = p.parse_args(argv)

    if args.cmd == "validate":
        cl = Cutlist.load(args.json)
        errs = cl.validate()
        if errs:
            print("INVALID:")
            for e in errs:
                print(f"  - {e}")
            return 1
        print(
            f"OK: '{cl.sequence_name}' — {len(cl.cuts)} cuts, "
            f"{len(cl.markers)} markers, {cl.fps} fps"
        )
        return 0

    if args.cmd == "to-otio":
        write_otio(Cutlist.load(args.json), args.otio)
        print(f"wrote {args.otio}")
        return 0

    if args.cmd == "from-otio":
        read_otio(args.otio).save(args.json)
        print(f"wrote {args.json}")
        return 0

    if args.cmd == "roundtrip":
        original = Cutlist.load(args.json)
        recovered = from_otio(to_otio(original))
        if original.to_dict() == recovered.to_dict():
            print("LOSSLESS: cutlist == from_otio(to_otio(cutlist))")
            return 0
        print("MISMATCH after round-trip:")
        print("  original :", original.to_dict())
        print("  recovered:", recovered.to_dict())
        return 1

    return 2


if __name__ == "__main__":
    sys.exit(_main())
