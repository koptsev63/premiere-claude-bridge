"""Tests for the capability probe. Deterministic — injects port/results.

Run:  python -m core.tests.test_probe
"""

from __future__ import annotations

import socket
import sys

from core.probe import (
    NoBackendReachable,
    ProbeResult,
    probe_all,
    probe_fcpxml,
    probe_premiere,
    probe_resolve,
    select_adapter,
)

_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


def test_fcpxml_always_reachable() -> None:
    print("probe — fcpxml is always reachable (round-trip)")
    r = probe_fcpxml()
    check("reachable", r.reachable and r.backend == "fcpxml")
    check("line renders", r.line().startswith("OK "))


def test_premiere_port_logic() -> None:
    print("probe — premiere port heuristic (open vs closed)")
    # bind an ephemeral listening socket -> that port must read reachable
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    open_port = srv.getsockname()[1]
    try:
        r_open = probe_premiere(port=open_port)
        check("open port -> reachable", r_open.reachable, r_open.detail)
    finally:
        srv.close()
    # the now-closed port must read not reachable, no exception
    r_closed = probe_premiere(port=open_port)
    check("closed port -> not reachable", not r_closed.reachable)
    check("backend tagged", r_closed.backend == "premiere")


def test_resolve_probe_never_raises() -> None:
    print("probe — resolve probe is guarded + no import side effect")
    check(
        "no DaVinciResolveScript at import",
        "DaVinciResolveScript" not in sys.modules,
    )
    r = probe_resolve()  # must not raise regardless of environment
    check("returns ProbeResult", isinstance(r, ProbeResult))
    check("backend tagged", r.backend == "resolve")
    check("reachable is bool", isinstance(r.reachable, bool))
    check("detail non-empty", bool(r.detail))


def test_select_adapter_logic() -> None:
    print("probe — select_adapter (injected results, deterministic)")
    res = {
        "resolve": ProbeResult("resolve", False, "not running"),
        "premiere": ProbeResult("premiere", True, "port open"),
        "fcpxml": ProbeResult("fcpxml", True, "always"),
    }
    check(
        "prefers first reachable in order",
        select_adapter(["resolve", "premiere", "fcpxml"], res) == "premiere",
    )
    check(
        "respects a different order",
        select_adapter(["fcpxml", "premiere"], res) == "fcpxml",
    )
    none_res = {
        "resolve": ProbeResult("resolve", False, "x"),
        "premiere": ProbeResult("premiere", False, "y"),
        "fcpxml": ProbeResult("fcpxml", False, "z"),
    }
    raised = False
    try:
        select_adapter(["resolve", "premiere", "fcpxml"], none_res)
    except NoBackendReachable:
        raised = True
    check("raises when nothing reachable", raised)


def test_probe_all_shape() -> None:
    print("probe — probe_all covers all three backends")
    allr = probe_all()
    check(
        "three backends",
        set(allr) == {"resolve", "premiere", "fcpxml"},
        str(set(allr)),
    )
    check(
        "all are ProbeResult",
        all(isinstance(v, ProbeResult) for v in allr.values()),
    )


def main() -> int:
    for fn in (
        test_fcpxml_always_reachable,
        test_premiere_port_logic,
        test_resolve_probe_never_raises,
        test_select_adapter_logic,
        test_probe_all_shape,
    ):
        fn()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
