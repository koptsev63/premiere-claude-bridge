"""DaVinci Resolve adapter — direct Python via the official scripting API.

Unlike Premiere (no official desktop automation API → the CEP/ExtendScript
bridge), Resolve ships an official Python/Lua scripting API. This adapter
talks to it directly: no panel, no WebSocket, no ExtendScript.

HARD CONSTRAINT — Resolve **Studio** required.
    External scripting (what this adapter does) only works in DaVinci
    Resolve **Studio**. The free version restricts scripting to the
    internal console. Also enable:
        Resolve > Preferences > System > General >
        "External scripting using" = Local
    Resolve 20's neural-engine AI tools are NOT exposed to the scripting
    API (see capabilities.json → resolve.unavailable_features).

Nothing here imports the Resolve module at import time, so importing this
package never requires Resolve. `connect()` is where it is loaded; if it is
missing it raises `ResolveUnavailable` with a precise setup hint instead of
a cryptic ImportError.

API signatures verified May 2026 against the DaVinci Resolve scripting
reference (bootstrap, ProjectManager.GetCurrentProject,
MediaStorage.AddItemListToMediaPool, MediaPool.CreateEmptyTimeline /
AppendToTimeline / ImportTimelineFromFile, Timeline.AddMarker,
Project.SetRenderSettings / AddRenderJob / StartRendering).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from core.adapters.base import CutlistResult, NLEAdapter
from core.cutlist import Cutlist

# Standard install locations for the scripting module / fusion lib.
_DEFAULTS = {
    "darwin": {
        "api": "/Library/Application Support/Blackmagic Design/"
               "DaVinci Resolve/Developer/Scripting",
        "lib": "/Applications/DaVinci Resolve/DaVinci Resolve.app/"
               "Contents/Libraries/Fusion/fusionscript.so",
    },
    "win32": {
        "api": os.path.expandvars(
            r"%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting"
        ),
        "lib": os.path.expandvars(
            r"%PROGRAMFILES%\Blackmagic Design\DaVinci Resolve\fusionscript.dll"
        ),
    },
    "linux": {
        "api": "/opt/resolve/Developer/Scripting",
        "lib": "/opt/resolve/libs/Fusion/fusionscript.so",
    },
}

_SETUP_HINT = (
    "Could not attach to DaVinci Resolve via the scripting API.\n"
    "Checklist:\n"
    "  1. DaVinci Resolve **Studio** is installed (free version cannot do "
    "external scripting).\n"
    "  2. Resolve is running with a project open.\n"
    "  3. Preferences > System > General > 'External scripting using' = "
    "Local.\n"
    "  4. The scripting module is importable. If not, set:\n"
    "       RESOLVE_SCRIPT_API, RESOLVE_SCRIPT_LIB, and add "
    "$RESOLVE_SCRIPT_API/Modules to PYTHONPATH\n"
    "     (defaults are filled in automatically per-OS when unset)."
)


class ResolveUnavailable(RuntimeError):
    """Resolve scripting API is not reachable (see message for the fix)."""


def _platform_key() -> str:
    if sys.platform.startswith("darwin"):
        return "darwin"
    if sys.platform.startswith("win"):
        return "win32"
    return "linux"


def _load_resolve():
    """Import DaVinciResolveScript and return the Resolve app handle.

    Fills RESOLVE_SCRIPT_API / RESOLVE_SCRIPT_LIB / PYTHONPATH with the
    standard per-OS paths when unset, then imports lazily.
    """
    d = _DEFAULTS[_platform_key()]
    api = os.environ.get("RESOLVE_SCRIPT_API", d["api"])
    lib = os.environ.get("RESOLVE_SCRIPT_LIB", d["lib"])
    os.environ.setdefault("RESOLVE_SCRIPT_API", api)
    os.environ.setdefault("RESOLVE_SCRIPT_LIB", lib)
    mod_path = str(Path(api) / "Modules")
    if mod_path not in sys.path:
        sys.path.append(mod_path)

    try:
        import DaVinciResolveScript as dvr_script  # type: ignore
    except Exception as exc:  # noqa: BLE001 - any failure → actionable hint
        raise ResolveUnavailable(
            _SETUP_HINT + f"\n\n(import error: {exc!r})"
        ) from exc

    resolve = dvr_script.scriptapp("Resolve")
    if resolve is None:
        raise ResolveUnavailable(
            _SETUP_HINT + "\n\n(scriptapp('Resolve') returned None — "
            "Resolve is probably not running)"
        )
    return resolve


class ResolveAdapter(NLEAdapter):
    BACKEND = "resolve"

    def __init__(self) -> None:
        self._resolve = None
        self._project = None
        self._media_pool = None
        self._timeline = None
        self._fps = 25.0
        self._tl_start = 0

    # ---- verb set ------------------------------------------------------ #

    def connect(self) -> None:
        self._resolve = _load_resolve()
        pm = self._resolve.GetProjectManager()
        proj = pm.GetCurrentProject()
        if proj is None:
            raise ResolveUnavailable(
                _SETUP_HINT + "\n\n(no current project — open one in Resolve)"
            )
        self._project = proj
        self._media_pool = proj.GetMediaPool()

    def get_project_info(self) -> dict[str, Any]:
        if self._project is None:
            self.connect()
        p = self._project
        tl = p.GetCurrentTimeline()
        return {
            "backend": "resolve",
            "project": p.GetName(),
            "timeline": tl.GetName() if tl else None,
            "timeline_count": p.GetTimelineCount(),
        }

    def import_media(self, path: str) -> Any:
        if self._media_pool is None:
            self.connect()
        ms = self._resolve.GetMediaStorage()
        items = ms.AddItemListToMediaPool([path])
        if not items:
            # newer Resolve also exposes MediaPool.ImportMedia
            imp = getattr(self._media_pool, "ImportMedia", None)
            if imp is not None:
                items = imp([path])
        if not items:
            raise RuntimeError(f"Resolve could not import media: {path}")
        return items[0]

    def place_clip(
        self,
        media_handle: Any,
        src_in: float,
        src_out: float,
        timeline_offset: float,
        name: str = "",
    ) -> None:
        fps = self._fps
        start = round(src_in * fps)
        # endFrame is the last source frame (inclusive); duration = end-start+1
        dur_frames = max(1, round((src_out - src_in) * fps))
        clip_info: dict[str, Any] = {
            "mediaPoolItem": media_handle,
            "startFrame": start,
            "endFrame": start + dur_frames - 1,
            # recordFrame is an ABSOLUTE timeline frame. The timeline's
            # content begins at GetStartFrame() (e.g. 86400 @ 01:00:00:00),
            # so the cutlist offset must be added to that, not to 0.
            "recordFrame": self._tl_start + round(timeline_offset * fps),
        }
        ok = self._media_pool.AppendToTimeline([clip_info])
        if not ok:
            raise RuntimeError(
                f"AppendToTimeline failed for {name or 'clip'}"
            )

    def add_marker(self, time: float, name: str, comment: str = "") -> None:
        tl = self._project.GetCurrentTimeline()
        if tl is None:
            raise RuntimeError("no current timeline for markers")
        frame = round(time * self._fps)
        tl.AddMarker(frame, "Blue", name, comment or name, 1)

    def export(self, out_path: str, preset: str | None = None) -> str:
        p = self._project
        out = Path(out_path)
        if preset:
            p.LoadRenderPreset(preset)
        p.SetRenderSettings(
            {"TargetDir": str(out.parent), "CustomName": out.stem}
        )
        p.AddRenderJob()
        p.StartRendering()
        return str(out)

    # ---- live preparation hook ---------------------------------------- #

    def _prepare_live(self, cutlist: Cutlist) -> None:
        # Match the new timeline to the cutlist fps *before* creating it,
        # otherwise Resolve uses the project default (often 24) and every
        # frame number we compute is off.
        want = cutlist.fps
        want_s = str(int(want)) if float(want).is_integer() else str(want)
        try:
            self._project.SetSetting("timelineFrameRate", want_s)
        except Exception:  # noqa: BLE001 - non-fatal; we read the real rate
            pass

        tl = self._media_pool.CreateEmptyTimeline(cutlist.sequence_name)
        if tl is None:
            raise RuntimeError(
                f"CreateEmptyTimeline({cutlist.sequence_name!r}) failed"
            )
        self._timeline = tl

        # Use the timeline's ACTUAL rate + start frame for all math.
        try:
            self._fps = float(tl.GetSetting("timelineFrameRate"))
        except Exception:  # noqa: BLE001
            self._fps = float(cutlist.fps)
        try:
            # Timeline content starts at the timeline's start frame
            # (e.g. 86400 == 01:00:00:00 @ 24fps). recordFrame is ABSOLUTE,
            # so clips placed at frame 0 land an hour before the visible
            # start — that was the "empty timeline" bug.
            self._tl_start = int(tl.GetStartFrame())
        except Exception:  # noqa: BLE001
            self._tl_start = 0

    # ---- alternative: native OTIO import ------------------------------ #

    def apply_cutlist_via_otio(self, cutlist: Cutlist) -> CutlistResult:
        """Import the whole cut as one .otio (Resolve reads OTIO natively).

        Requires working otio FILE I/O (Python 3.12/3.13 — see
        core/cutlist.py). Raises OtioUnavailable otherwise; callers should
        fall back to the default verb-set apply_cutlist().
        """
        from tempfile import NamedTemporaryFile

        from core.cutlist import write_otio  # may raise OtioUnavailable

        self.connect()
        with NamedTemporaryFile(suffix=".otio", delete=False) as f:
            otio_path = f.name
        write_otio(cutlist, otio_path)
        tl = self._media_pool.ImportTimelineFromFile(otio_path)
        if tl is None:
            raise RuntimeError("ImportTimelineFromFile returned None")
        return CutlistResult(
            backend="resolve",
            mode="live",
            sequence_name=cutlist.sequence_name,
            cuts_placed=len(cutlist.cuts),
            markers_added=len(cutlist.markers),
            warnings=["imported via native OTIO (single timeline import)"],
        )
