"""Per-NLE adapters.

Every adapter implements the same verb set over a `Cutlist`. The editing
brain never imports a specific NLE — it builds one cutlist and hands it to
whichever adapter is active.

    from core.adapters import get_adapter
    adapter = get_adapter("resolve")     # or "premiere" / "fcpxml"
    adapter.apply_cutlist(cutlist)

`get_adapter` is lazy so importing this package never imports DaVinci's
Python module or touches a running NLE.
"""

from __future__ import annotations

from core.adapters.base import CutlistResult, NLEAdapter, DryRunAdapter

__all__ = ["NLEAdapter", "DryRunAdapter", "CutlistResult", "get_adapter"]


def get_adapter(name: str, **kwargs) -> NLEAdapter:
    """Return an adapter instance for `name`, importing lazily.

    `name` is the selector ('premiere'|'resolve'|'fcpxml'|'dryrun').
    Extra kwargs are forwarded to the adapter constructor — e.g.
    get_adapter("dryrun", backend="resolve") for a dry-run adapter that
    reports the Resolve capability row.
    """
    if name == "premiere":
        from core.adapters.premiere import PremiereAdapter

        return PremiereAdapter(**kwargs)
    if name == "resolve":
        from core.adapters.resolve import ResolveAdapter

        return ResolveAdapter(**kwargs)
    if name == "fcpxml":
        from core.adapters.fcpxml import FcpxmlAdapter

        return FcpxmlAdapter(**kwargs)
    if name == "dryrun":
        return DryRunAdapter(**kwargs)
    raise KeyError(
        f"unknown adapter {name!r}; "
        f"known: premiere, resolve, fcpxml, dryrun"
    )
