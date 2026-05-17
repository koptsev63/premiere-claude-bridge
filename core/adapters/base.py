"""The NLE adapter contract.

One verb set, implemented by every backend:

    connect()            -> open/attach to the NLE (no-op for round-trip)
    get_project_info()   -> dict describing the current project
    import_media(path)   -> make a source clip available, return a handle
    place_clip(...)      -> put a clip on the timeline at an offset
    add_marker(...)      -> timeline marker
    export(out, preset)  -> trigger a render (live backends only)
    watch_clip(path,...) -> hand a clip to the perception layer (/watch)

`apply_cutlist(cutlist)` is the high-level entry point. The default
implementation here orchestrates the verb set and consults the capability
matrix to degrade gracefully: a round-trip-only backend (Final Cut via
FCPXML) gets a project file written instead of live calls. Concrete
adapters override only what they must (e.g. Resolve imports an .otio
timeline wholesale; FCPXML just writes a file).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core import capabilities
from core.cutlist import Cutlist


@dataclass
class CutlistResult:
    """Structured outcome of apply_cutlist()."""

    backend: str
    mode: str                      # "live" | "round_trip"
    sequence_name: str
    clips_imported: int = 0
    cuts_placed: int = 0
    markers_added: int = 0
    project_file: str | None = None     # set in round_trip mode
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.mode == "round_trip":
            return (
                f"[{self.backend}] round-trip: wrote {self.project_file} "
                f"({self.cuts_placed} cuts, {self.markers_added} markers). "
                f"Open it in {self.backend} and export."
            )
        return (
            f"[{self.backend}] live: '{self.sequence_name}' — "
            f"{self.clips_imported} clips, {self.cuts_placed} cuts, "
            f"{self.markers_added} markers"
        )


class NLEAdapter(abc.ABC):
    """Abstract base. `BACKEND` must match a key in capabilities.json."""

    BACKEND: str = ""

    @property
    def capabilities(self) -> capabilities.Capabilities:
        return capabilities.get(self.BACKEND)

    # ---- primitive verbs (override in concrete adapters) --------------- #

    @abc.abstractmethod
    def connect(self) -> None:
        """Attach to the running NLE. No-op for round-trip backends."""

    @abc.abstractmethod
    def get_project_info(self) -> dict[str, Any]:
        ...

    @abc.abstractmethod
    def import_media(self, path: str) -> str:
        """Make `path` available as a source; return an opaque handle."""

    @abc.abstractmethod
    def place_clip(
        self,
        media_handle: str,
        src_in: float,
        src_out: float,
        timeline_offset: float,
        name: str = "",
    ) -> None:
        ...

    @abc.abstractmethod
    def add_marker(self, time: float, name: str, comment: str = "") -> None:
        ...

    def export(self, out_path: str, preset: str | None = None) -> str:
        """Trigger a render. Live backends override; others raise."""
        raise NotImplementedError(
            f"{self.BACKEND} cannot trigger an export "
            f"(triggered_export={self.capabilities.triggered_export})"
        )

    def export_project_file(self, cutlist: Cutlist, out_path: str) -> str:
        """Write a project file for a round-trip backend (override)."""
        raise NotImplementedError(
            f"{self.BACKEND} has no project-file exporter"
        )

    def watch_clip(
        self, path: str, start: float | None = None, end: float | None = None
    ) -> str:
        """Return a `/watch` invocation for this clip.

        Perception is NLE-independent (it reads media files), so the base
        builds the command the editing skill runs; adapters need not touch it.
        """
        cmd = ["python3", "skills/watch/scripts/watch.py", path]
        if start is not None:
            cmd += ["--start", str(start)]
        if end is not None:
            cmd += ["--end", str(end)]
        return " ".join(cmd)

    # ---- high-level orchestration (shared) ----------------------------- #

    def apply_cutlist(self, cutlist: Cutlist) -> CutlistResult:
        errs = cutlist.validate()
        if errs:
            raise ValueError(
                "cutlist is invalid:\n  - " + "\n  - ".join(errs)
            )

        caps = self.capabilities
        res = CutlistResult(
            backend=self.BACKEND,
            mode="round_trip" if caps.round_trip_only else "live",
            sequence_name=cutlist.sequence_name,
        )

        if caps.round_trip_only:
            out = str(
                Path.cwd() / f"{cutlist.sequence_name}.{self._project_ext()}"
            )
            res.project_file = self.export_project_file(cutlist, out)
            res.cuts_placed = len(cutlist.cuts)
            res.markers_added = (
                len(cutlist.markers) if caps.markers else 0
            )
            return res

        self.connect()
        handles: dict[str, str] = {}
        for cut in cutlist.cuts:
            if cut.clip not in handles:
                handles[cut.clip] = self.import_media(cut.clip)
                res.clips_imported += 1
        for cut in sorted(cutlist.cuts, key=lambda c: c.offset):
            self.place_clip(
                handles[cut.clip],
                cut.in_,
                cut.out,
                cut.offset,
                cut.label,
            )
            res.cuts_placed += 1
        if caps.markers:
            for m in cutlist.markers:
                self.add_marker(m.time, m.name, m.comment)
                res.markers_added += 1
        else:
            res.warnings.append(
                f"{self.BACKEND} reports no marker support; "
                f"{len(cutlist.markers)} markers skipped"
            )
        return res

    def _project_ext(self) -> str:
        return "xml"


class DryRunAdapter(NLEAdapter):
    """A no-op adapter that records every call.

    Used by the test suite and as the reference that proves the
    orchestration + capability gating without any NLE installed. Pass
    `backend=` to exercise a specific row of the capability matrix.
    """

    def __init__(self, backend: str = "premiere") -> None:
        self.BACKEND = backend
        self.calls: list[tuple[str, tuple]] = []

    def _log(self, name: str, *args) -> None:
        self.calls.append((name, args))

    def connect(self) -> None:
        self._log("connect")

    def get_project_info(self) -> dict[str, Any]:
        self._log("get_project_info")
        return {"backend": self.BACKEND, "dry_run": True}

    def import_media(self, path: str) -> str:
        self._log("import_media", path)
        return f"handle::{path}"

    def place_clip(
        self,
        media_handle: str,
        src_in: float,
        src_out: float,
        timeline_offset: float,
        name: str = "",
    ) -> None:
        self._log(
            "place_clip", media_handle, src_in, src_out, timeline_offset, name
        )

    def add_marker(self, time: float, name: str, comment: str = "") -> None:
        self._log("add_marker", time, name, comment)

    def export(self, out_path: str, preset: str | None = None) -> str:
        self._log("export", out_path, preset)
        return out_path

    def export_project_file(self, cutlist: Cutlist, out_path: str) -> str:
        self._log("export_project_file", cutlist.sequence_name, out_path)
        return out_path

    def count(self, verb: str) -> int:
        return sum(1 for name, _ in self.calls if name == verb)
