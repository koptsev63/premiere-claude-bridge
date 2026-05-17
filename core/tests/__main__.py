"""Run every core test module: `python -m core.tests`.

Aggregates per-module main() return codes. No pytest dependency.
"""

from __future__ import annotations

import sys

from core.tests import (
    test_adapters,
    test_concrete_adapters,
    test_conform,
    test_cutlist,
    test_review_loop,
)

MODULES = [
    test_cutlist,
    test_adapters,
    test_concrete_adapters,
    test_review_loop,
    test_conform,
]


def main() -> int:
    rc = 0
    for mod in MODULES:
        print(f"\n=== {mod.__name__} ===")
        rc |= mod.main()
    print("\n" + ("ALL GREEN" if rc == 0 else "FAILURES PRESENT"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
