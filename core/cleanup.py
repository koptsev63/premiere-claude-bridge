"""Per-clip technical corrections — horizon leveling + stabilization.

The editing brain decides WHAT to cut; this decides how each chosen shot
must be *cleaned* before it lands: level a tilted horizon, stabilize a
genuinely broken shot. It is deterministic and data-driven — it reads the
analysis `report.json` (the interchange produced by
`skills/film-editing/tools/analyze_clips.py`), so `core/` never imports a
skills tool.

Honest editorial rule (baked in): stabilization is applied only to
relative outliers (median + k*MAD across the chosen clips), never blanket
— handheld documentary energy is intentional; over-smoothing kills it
(Murch: emotion/rhythm outranks technical polish). Horizon, by contrast,
is corrected whenever the analyzer flagged a tilt (a crooked frame is
almost always a defect).

`corrections_for_cutlist()` -> {clip_ref: Correction}. Consumers:
  * ffmpeg render  -> Correction.vf_chain()  (deshake,rotate=…)
  * ResolveAdapter -> Correction.rotate_deg  (clip RotationAngle)
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.cutlist import Cutlist

# floor below which a shake score is never an outlier worth fixing
SHAKE_FLOOR = 8.0
SHAKE_K = 2.5


@dataclass
class Correction:
    clip: str
    horizon_vf: str | None = None      # ffmpeg rotate=… string (or None)
    rotate_deg: float = 0.0            # for NLEs that rotate by degrees
    stabilize: bool = False
    reasons: list[str] = field(default_factory=list)

    def vf_chain(self) -> str | None:
        """ffmpeg -vf chain for this clip's segment, or None if clean."""
        parts: list[str] = []
        if self.stabilize:
            parts.append("deshake=edge=clamp")
        if self.horizon_vf:
            parts.append(self.horizon_vf)
        return ",".join(parts) if parts else None

    @property
    def clean(self) -> bool:
        return not self.stabilize and not self.horizon_vf


def _index_report(report: list[dict[str, Any]]) -> dict[str, dict]:
    """Index an analysis report by clip basename."""
    idx: dict[str, dict] = {}
    for r in report:
        name = r.get("clip") or r.get("name") or r.get("file") or ""
        idx[Path(str(name)).name] = r
    return idx


def _relative_shake_flags(scores: dict[str, float]) -> set[str]:
    """Outlier-only stabilization (median + k*MAD, above floor)."""
    vals = [(k, v) for k, v in scores.items() if v is not None]
    if len(vals) < 3:
        return {k for k, v in vals if v >= SHAKE_FLOOR}
    arr = [v for _, v in vals]
    med = statistics.median(arr)
    mad = statistics.median([abs(v - med) for v in arr]) or 1e-6
    cutoff = max(SHAKE_FLOOR, med + SHAKE_K * mad)
    return {k for k, v in vals if v >= cutoff}


def corrections_for_cutlist(
    cutlist: Cutlist,
    report: list[dict[str, Any]],
) -> dict[str, Correction]:
    """Map each cutlist clip to its technical Correction.

    `report` is the parsed analysis report.json. Clips absent from the
    report get a clean (no-op) Correction so callers can index safely.
    """
    idx = _index_report(report)

    shake_scores: dict[str, float] = {}
    for cut in cutlist.cuts:
        ref = Path(cut.clip).name
        rec = idx.get(ref)
        if rec and rec.get("shake_score") is not None:
            shake_scores[cut.clip] = float(rec["shake_score"])
    to_stabilize = _relative_shake_flags(shake_scores)

    out: dict[str, Correction] = {}
    for cut in cutlist.cuts:
        if cut.clip in out:
            continue
        ref = Path(cut.clip).name
        rec = idx.get(ref, {})
        c = Correction(clip=cut.clip)

        hv = rec.get("horizon_correction_filter")
        tilt = rec.get("horizon_tilt_deg")
        if hv:
            c.horizon_vf = hv
            if isinstance(tilt, (int, float)):
                c.rotate_deg = round(-float(tilt), 3)  # undo the tilt
                c.reasons.append(f"horizon {tilt:+.2f}° -> level")

        if cut.clip in to_stabilize:
            c.stabilize = True
            sc = shake_scores.get(cut.clip)
            c.reasons.append(f"shake outlier ({sc}) -> deshake")

        out[cut.clip] = c
    return out


def load_report(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict):  # tolerate {clips:[…]} or {…name:rec}
        if "clips" in data:
            return data["clips"]
        return list(data.values())
    return data


def summary(corr: dict[str, Correction]) -> str:
    lvl = sum(1 for c in corr.values() if c.horizon_vf)
    stab = sum(1 for c in corr.values() if c.stabilize)
    return (
        f"{len(corr)} clips: {lvl} horizon-leveled, "
        f"{stab} stabilized (outliers only), "
        f"{sum(1 for c in corr.values() if c.clean)} clean"
    )
