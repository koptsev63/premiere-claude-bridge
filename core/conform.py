"""Conform / relink — resolve cutlist clip references to real media.

A cutlist is portable: `clip` may be a bare name ("00118.MTS") authored on
another machine or NLE. Before any backend can build it, each reference has
to point at a file that actually exists here. That is *conforming*.

`MediaResolver` walks one or more media roots, indexes by basename, and
optionally prefers a proxy directory (same stem, lighter codec) for fast
offline editing. `conform_cutlist()` returns a NEW cutlist with `clip`
rewritten to resolved absolute paths plus a structured report
(resolved / proxied / missing / ambiguous) so nothing fails silently.

No NLE, no third-party dependency — pure pathlib. This is the unglamorous
piece that makes a cut built once relink everywhere (OTIO media
references, Resolve/Premiere/FCP alike).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

from core.cutlist import Cutlist

_DEFAULT_EXTS = (
    ".mts", ".mp4", ".mov", ".m4v", ".mxf", ".avi",
    ".braw", ".r3d", ".wav", ".aif", ".aiff",
)


@dataclass
class ConformReport:
    resolved: dict[str, str] = field(default_factory=dict)   # ref -> path
    proxied: dict[str, str] = field(default_factory=dict)    # ref -> proxy
    missing: list[str] = field(default_factory=list)
    ambiguous: dict[str, list[str]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.missing and not self.ambiguous

    def summary(self) -> str:
        return (
            f"resolved={len(self.resolved)} proxied={len(self.proxied)} "
            f"missing={len(self.missing)} ambiguous={len(self.ambiguous)}"
        )


class MediaResolver:
    """Index media roots by basename; resolve a clip ref to a real file."""

    def __init__(
        self,
        roots: list[str] | None = None,
        proxy_dir: str | None = None,
        extensions: tuple[str, ...] = _DEFAULT_EXTS,
    ) -> None:
        self.roots = [Path(r) for r in (roots or [])]
        self.proxy_dir = Path(proxy_dir) if proxy_dir else None
        self.exts = tuple(e.lower() for e in extensions)
        self._index: dict[str, list[Path]] = {}
        self._proxy_index: dict[str, Path] = {}
        self._build()

    def _eligible(self, p: Path) -> bool:
        return p.is_file() and p.suffix.lower() in self.exts

    def _build(self) -> None:
        for root in self.roots:
            if not root.is_dir():
                continue
            for p in root.rglob("*"):
                if self._eligible(p):
                    self._index.setdefault(p.name, []).append(p)
        if self.proxy_dir and self.proxy_dir.is_dir():
            for p in self.proxy_dir.rglob("*"):
                if p.is_file():
                    # index proxies by source STEM (codec/ext may differ)
                    self._proxy_index.setdefault(p.stem, p)

    def resolve(self, ref: str) -> tuple[str | None, list[str]]:
        """Return (resolved_path | None, candidates).

        Resolution order: exact existing path → unique basename match in
        roots. Multiple matches → ambiguous (returns None + candidates).
        """
        rp = Path(ref)
        if rp.is_absolute() and rp.is_file():
            return str(rp), [str(rp)]
        hits = self._index.get(rp.name, [])
        if len(hits) == 1:
            return str(hits[0]), [str(hits[0])]
        if len(hits) > 1:
            return None, [str(h) for h in hits]
        return None, []

    def proxy_for(self, resolved_path: str) -> str | None:
        if not self._proxy_index:
            return None
        stem = Path(resolved_path).stem
        p = self._proxy_index.get(stem)
        return str(p) if p else None


def conform_cutlist(
    cutlist: Cutlist,
    resolver: MediaResolver,
    prefer_proxy: bool = False,
) -> tuple[Cutlist, ConformReport]:
    """Rewrite clip refs to resolved paths. Returns (new_cutlist, report).

    The input cutlist is never mutated.
    """
    cl = copy.deepcopy(cutlist)
    report = ConformReport()
    cache: dict[str, str | None] = {}

    for cut in cl.cuts:
        ref = cut.clip
        if ref in cache:
            resolved = cache[ref]
        else:
            resolved, candidates = resolver.resolve(ref)
            if resolved is None and len(candidates) > 1:
                report.ambiguous[ref] = candidates
            cache[ref] = resolved

        if resolved is None:
            if ref not in report.ambiguous and ref not in report.missing:
                report.missing.append(ref)
            continue

        final = resolved
        if prefer_proxy:
            px = resolver.proxy_for(resolved)
            if px:
                final = px
                report.proxied[ref] = px
        report.resolved[ref] = resolved
        cut.clip = final

    return cl, report


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    p = argparse.ArgumentParser(
        prog="python -m core.conform",
        description="Relink a cutlist's clips to real media on this machine.",
    )
    p.add_argument("cutlist")
    p.add_argument(
        "--roots", nargs="+", required=True, help="media search directories"
    )
    p.add_argument("--proxy", help="proxy directory (same-stem substitution)")
    p.add_argument(
        "--prefer-proxy",
        action="store_true",
        help="use proxy media where available",
    )
    p.add_argument("--out", help="write conformed cutlist JSON here")
    args = p.parse_args(argv)

    cl = Cutlist.load(args.cutlist)
    resolver = MediaResolver(args.roots, args.proxy)
    conformed, report = conform_cutlist(
        cl, resolver, prefer_proxy=args.prefer_proxy
    )

    print(report.summary())
    for ref in report.missing:
        print(f"  MISSING   {ref}")
    for ref, cands in report.ambiguous.items():
        print(f"  AMBIGUOUS {ref} -> {len(cands)} matches")
    if args.out and report.ok:
        conformed.save(args.out)
        print(f"wrote {args.out}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    import sys

    sys.exit(_main())
