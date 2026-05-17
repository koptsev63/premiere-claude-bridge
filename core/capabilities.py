"""Capability matrix — what each NLE backend can and cannot do.

The editing brain consults this to pick a strategy and degrade gracefully:
a live backend (Premiere/Resolve) gets driven directly; a round-trip-only
backend (Final Cut via FCPXML) gets a project file to import.

Facts verified May 2026 — see `capabilities.json` for sources/notes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_MATRIX_PATH = Path(__file__).with_name("capabilities.json")


@dataclass(frozen=True)
class Capabilities:
    backend: str
    display_name: str
    live_control: bool
    requires_paid_tier: str | None
    round_trip_only: bool
    markers: bool
    triggered_export: bool
    native_otio: bool
    unavailable_features: tuple[str, ...]
    notes: str

    def supports(self, feature: str) -> bool:
        """True if `feature` is usable on this backend.

        Known feature keys: live_control, markers, triggered_export,
        native_otio, plus anything listed in unavailable_features.
        """
        if feature in self.unavailable_features:
            return False
        return bool(getattr(self, feature, False))

    def requires_payment(self) -> bool:
        return self.requires_paid_tier is not None


@lru_cache(maxsize=1)
def _raw() -> dict[str, Any]:
    data = json.loads(_MATRIX_PATH.read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


@lru_cache(maxsize=None)
def get(backend: str) -> Capabilities:
    """Return the Capabilities for a backend ('premiere'|'resolve'|'fcpxml')."""
    table = _raw()
    if backend not in table:
        raise KeyError(
            f"unknown backend {backend!r}; known: {sorted(table)}"
        )
    d = table[backend]
    return Capabilities(
        backend=backend,
        display_name=d["display_name"],
        live_control=d["live_control"],
        requires_paid_tier=d["requires_paid_tier"],
        round_trip_only=d["round_trip_only"],
        markers=d["markers"],
        triggered_export=d["triggered_export"],
        native_otio=d["native_otio"],
        unavailable_features=tuple(d.get("unavailable_features", ())),
        notes=d["notes"],
    )


def backends() -> list[str]:
    return sorted(_raw())


def matrix() -> dict[str, Capabilities]:
    return {b: get(b) for b in backends()}
