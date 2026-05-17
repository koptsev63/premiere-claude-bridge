"""Final Cut adapter — FCPXML round-trip (no live API).

Final Cut Pro has no live scripting API, so this is a round-trip backend:
write a well-formed FCPXML project, the human opens it in Final Cut and
exports. `live_control=false`, `round_trip_only=true` in the matrix, so the
shared `apply_cutlist()` routes here via `export_project_file()` and never
makes a live call.

The default writer is **native** (deterministic string/ElementTree, no
third-party dependency) so the round-trip works on any Python — important
because opentimelineio's FILE I/O is broken on CPython 3.14. If otio file
I/O is available you can instead use `export_via_otio()` (otio ships an
fcpx_xml adapter).

Generates FCPXML 1.10: a `format`, one `asset` per unique source, an
`asset-clip` per cut placed at its `offset`, and `marker`s mapped onto the
clip whose timeline range contains them. This is a minimal *conform*
project (good for relinking + structure); exotic multi-format media may
need manual format tweaks in Final Cut.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr

from core.adapters.base import CutlistResult, NLEAdapter
from core.cutlist import Cutlist


def _rational(seconds: float, timebase: int) -> str:
    """FCPXML time value, e.g. 120/25s."""
    return f"{round(seconds * timebase)}/{timebase}s"


class FcpxmlAdapter(NLEAdapter):
    BACKEND = "fcpxml"

    # ---- verbs are inert: this backend is round-trip only ------------- #

    def connect(self) -> None:  # pragma: no cover - never called (matrix)
        raise RuntimeError(
            "fcpxml is a round-trip backend; apply_cutlist() writes a "
            "project file instead of connecting."
        )

    def get_project_info(self) -> dict[str, Any]:
        return {"backend": "fcpxml", "mode": "round_trip"}

    def import_media(self, path: str) -> str:  # pragma: no cover
        raise RuntimeError("fcpxml has no live import")

    def place_clip(self, *a, **k) -> None:  # pragma: no cover
        raise RuntimeError("fcpxml has no live timeline")

    def add_marker(self, *a, **k) -> None:  # pragma: no cover
        raise RuntimeError("fcpxml has no live timeline")

    def _project_ext(self) -> str:
        return "fcpxml"

    # ---- the round-trip writer ---------------------------------------- #

    def build_fcpxml(self, cutlist: Cutlist) -> str:
        tb = round(cutlist.fps)
        try:
            w, h = cutlist.resolution.lower().split("x")
        except ValueError:
            w, h = "1920", "1080"

        # one asset per unique source
        order: list[str] = []
        for c in cutlist.cuts:
            if c.clip not in order:
                order.append(c.clip)
        asset_id = {clip: f"r{i + 2}" for i, clip in enumerate(order)}

        total = cutlist.total_duration_sec or max(
            (c.timeline_end for c in cutlist.cuts), default=0.0
        )

        res: list[str] = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            "<!DOCTYPE fcpxml>",
            '<fcpxml version="1.10">',
            "  <resources>",
            f'    <format id="r1" name="FFVideoFormat{h}p{tb}" '
            f'frameDuration="1/{tb}s" width="{w}" height="{h}"/>',
        ]
        for clip in order:
            uri = Path(clip)
            src = uri.as_uri() if uri.is_absolute() else "file://" + clip
            res.append(
                f'    <asset id="{asset_id[clip]}" '
                f"name={quoteattr(uri.stem)} "
                f"src={quoteattr(src)} "
                f'start="0s" hasVideo="1" hasAudio="1" format="r1"/>'
            )
        res.append("  </resources>")

        res.append("  <library>")
        res.append("    <event name=" + quoteattr(cutlist.sequence_name) + ">")
        res.append(
            "      <project name=" + quoteattr(cutlist.sequence_name) + ">"
        )
        res.append(
            f'        <sequence format="r1" '
            f'duration="{_rational(total, tb)}" '
            f'tcStart="0s" tcFormat="NDF">'
        )
        res.append("          <spine>")

        for cut in sorted(cutlist.cuts, key=lambda c: c.offset):
            mk_xml = ""
            for m in cutlist.markers:
                if cut.offset <= m.time < cut.timeline_end:
                    local = cut.in_ + (m.time - cut.offset)
                    val = m.name + (f" — {m.comment}" if m.comment else "")
                    mk_xml += (
                        f'\n              <marker '
                        f'start="{_rational(local, tb)}" '
                        f'duration="1/{tb}s" '
                        f"value={quoteattr(val)}/>"
                    )
            clip_open = (
                f'            <asset-clip ref="{asset_id[cut.clip]}" '
                f"name={quoteattr(cut.label or Path(cut.clip).stem)} "
                f'offset="{_rational(cut.offset, tb)}" '
                f'start="{_rational(cut.in_, tb)}" '
                f'duration="{_rational(cut.duration, tb)}" '
                f'format="r1">'
            )
            res.append(clip_open + mk_xml)
            res.append("            </asset-clip>")

        res += [
            "          </spine>",
            "        </sequence>",
            "      </project>",
            "    </event>",
            "  </library>",
            "</fcpxml>",
        ]
        return "\n".join(res) + "\n"

    def export_project_file(self, cutlist: Cutlist, out_path: str) -> str:
        Path(out_path).write_text(self.build_fcpxml(cutlist))
        return out_path

    def export_via_otio(self, cutlist: Cutlist, out_path: str) -> str:
        """Alternative writer through otio's fcpx_xml adapter.

        Requires working otio FILE I/O (Python 3.12/3.13). Raises
        OtioUnavailable otherwise — callers fall back to the native writer.
        """
        from core.cutlist import _require_otio, to_otio

        otio = _require_otio()
        try:
            otio.adapters.write_to_file(
                to_otio(cutlist), out_path, adapter_name="fcpx_xml"
            )
        except Exception as exc:  # otio file layer broken on this Python
            from core.cutlist import OtioUnavailable

            raise OtioUnavailable(
                "otio fcpx_xml export failed in this environment; use the "
                "native build_fcpxml() (default) instead."
            ) from exc
        return out_path
