"""Capability probe — detect which NLEs are reachable, auto-select one.

Answers "what can I drive right now?" without the caller knowing any
NLE-specific detail. Each probe is guarded: it returns a structured
`ProbeResult` and never raises, so a missing/!running NLE is data, not a
crash. Nothing here imports an NLE module at import time.

    from core.probe import probe_all, select_adapter
    probe_all()                       # {'resolve': ProbeResult(...), ...}
    name = select_adapter(["resolve", "premiere", "fcpxml"])
    adapter = get_adapter(name)

CLI: `python -m core.probe`  ->  one line per backend.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass

from core import capabilities

PREMIERE_BRIDGE_PORT = 9876  # mcp-server WebSocket (see docs/architecture.md)


@dataclass
class ProbeResult:
    backend: str
    reachable: bool
    detail: str

    def line(self) -> str:
        mark = "OK " if self.reachable else "-- "
        return f"{mark} {self.backend:9s} {self.detail}"


def probe_fcpxml() -> ProbeResult:
    # Round-trip backend: always "reachable" — it only writes a file.
    return ProbeResult(
        "fcpxml", True, "round-trip writer always available (no live app)"
    )


def probe_premiere(port: int = PREMIERE_BRIDGE_PORT) -> ProbeResult:
    """Heuristic: is the bridge WebSocket port accepting connections?

    Authoritative Premiere status comes from the MCP tool
    `mcp__premiere__pr_status`; the Python core can only check the port.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port))
        return ProbeResult(
            "premiere",
            True,
            f"bridge port {port} open (confirm with pr_status)",
        )
    except OSError:
        return ProbeResult(
            "premiere",
            False,
            f"bridge port {port} closed — start the MCP server + CEP panel",
        )
    finally:
        s.close()


def probe_resolve() -> ProbeResult:
    """Try a guarded connect via the official scripting API."""
    try:
        from core.adapters.resolve import ResolveAdapter, ResolveUnavailable

        a = ResolveAdapter()
        try:
            a.connect()
        except ResolveUnavailable as e:
            first = str(e).splitlines()[0]
            return ProbeResult("resolve", False, first)
        try:
            info = a.get_project_info()
            proj = info.get("project")
        except Exception:  # noqa: BLE001
            proj = None
        return ProbeResult(
            "resolve",
            True,
            f"Studio reachable (project: {proj or 'none open'})",
        )
    except Exception as e:  # noqa: BLE001 - probe must never raise
        return ProbeResult("resolve", False, f"probe error: {e!r}")


_PROBES = {
    "resolve": probe_resolve,
    "premiere": probe_premiere,
    "fcpxml": probe_fcpxml,
}


def probe_all() -> dict[str, ProbeResult]:
    return {name: fn() for name, fn in _PROBES.items()}


class NoBackendReachable(RuntimeError):
    pass


def select_adapter(
    prefer: list[str] | None = None,
    results: dict[str, ProbeResult] | None = None,
) -> str:
    """Return the first reachable backend in preference order.

    `results` lets callers (and tests) inject probe outcomes instead of
    hitting the environment.
    """
    order = prefer or ["resolve", "premiere", "fcpxml"]
    res = results or probe_all()
    for name in order:
        r = res.get(name)
        if r and r.reachable:
            return name
    raise NoBackendReachable(
        "no NLE reachable:\n  "
        + "\n  ".join(res[n].line() for n in order if n in res)
    )


def _main(argv: list[str] | None = None) -> int:
    import sys

    res = probe_all()
    for name in ("resolve", "premiere", "fcpxml"):
        print(res[name].line())
        caps = capabilities.get(name)
        if caps.requires_paid_tier:
            print(f"     (requires {caps.requires_paid_tier})")
    try:
        print(f"\nauto-select -> {select_adapter(results=res)}")
        return 0
    except NoBackendReachable as e:
        print(f"\n{e}")
        return 1


if __name__ == "__main__":
    import sys

    sys.exit(_main())
