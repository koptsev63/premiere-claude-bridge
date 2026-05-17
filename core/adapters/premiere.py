"""Premiere adapter — emits the ExtendScript the existing bridge runs.

Premiere has no official desktop automation API, so the live link is this
repo's CEP panel + ExtendScript bridge. The MCP server already exposes
`pr_eval_jsx`. This adapter's job in the universal core is therefore to
compile a `Cutlist` into one ExtendScript program; Claude runs that string
through `mcp__premiere__pr_eval_jsx`.

So `connect()` is a no-op (the running MCP server + panel *is* the live
link). The verb methods accumulate JSX; `build_jsx(cutlist)` returns the
finished program. This keeps Premiere on the same verb contract as Resolve
without the Python process needing to speak WebSocket to the Node server.

ExtendScript shapes used (verified against docs/tools.md):
- find clip:   app.project.rootItem.findItemsMatchingMediaPath(name, true)[0]
- in/out:      pi.setInPoint(sec, 4) / pi.setOutPoint(sec, 4)   (4 = video+audio)
- place:       seq.videoTracks[0].insertClip(pi, offsetSeconds)
- marker:      seq.markers.createMarker(t); m.name=..; m.comments=..
"""

from __future__ import annotations

from typing import Any

from core.adapters.base import CutlistResult, NLEAdapter
from core.cutlist import Cutlist


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _num(x: float) -> str:
    """Clean numeric literal: 2.0 -> '2', 5.5 -> '5.5'."""
    return str(int(x)) if float(x).is_integer() else repr(float(x))


class PremiereAdapter(NLEAdapter):
    BACKEND = "premiere"

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._handles: dict[str, str] = {}
        self._n = 0

    # ---- verb set (emit JSX) ------------------------------------------ #

    def connect(self) -> None:
        # The MCP server + CEP panel is the live link; nothing to attach.
        pass

    def get_project_info(self) -> dict[str, Any]:
        # Real info comes from the bridge tool pr_get_project_info; the
        # adapter only declares how Premiere is reached.
        return {"backend": "premiere", "via": "mcp__premiere__pr_eval_jsx"}

    def _prepare_live(self, cutlist: Cutlist) -> None:
        self._lines = [
            "(function(){",
            "  var seq = app.project.activeSequence;",
            '  if (!seq) { return "ERR: no active sequence"; }',
            f'  seq.name = "{_esc(cutlist.sequence_name)}";',
        ]

    def import_media(self, path: str) -> str:
        self._n += 1
        var = f"pi{self._n}"
        self._lines.append(
            f'  var {var} = app.project.rootItem'
            f'.findItemsMatchingMediaPath("{_esc(path)}", true)[0];'
        )
        self._lines.append(
            f'  if (!{var}) {{ return "ERR: clip not found: '
            f'{_esc(path)}"; }}'
        )
        return var

    def place_clip(
        self,
        media_handle: str,
        src_in: float,
        src_out: float,
        timeline_offset: float,
        name: str = "",
    ) -> None:
        v = media_handle
        self._lines.append(
            f"  {v}.setInPoint({_num(src_in)}, 4); "
            f"{v}.setOutPoint({_num(src_out)}, 4); "
            f"seq.videoTracks[0].insertClip({v}, {_num(timeline_offset)});"
            + (f"  // {_esc(name)}" if name else "")
        )

    def add_marker(self, time: float, name: str, comment: str = "") -> None:
        self._lines.append(
            f"  (function(){{ var m = seq.markers.createMarker({_num(time)}); "
            f'm.name = "{_esc(name)}"; '
            f'm.comments = "{_esc(comment)}"; }})();'
        )

    # ---- compile ------------------------------------------------------ #

    def _finish(self) -> str:
        return "\n".join(
            self._lines + ['  return "OK";', "})();"]
        )

    def apply_cutlist(self, cutlist: Cutlist) -> CutlistResult:
        res = super().apply_cutlist(cutlist)
        self.jsx = self._finish()
        res.warnings.append(
            "run the generated ExtendScript via "
            "mcp__premiere__pr_eval_jsx (PremiereAdapter.jsx)"
        )
        return res

    def build_jsx(self, cutlist: Cutlist) -> str:
        """Compile a cutlist to a single ExtendScript program."""
        self.apply_cutlist(cutlist)
        return self.jsx
