"""Self-verify the Resolve adapter against a live DaVinci Resolve Studio.

The Resolve path cannot be CI-tested (no headless Resolve). This script
lets any Studio user confirm it in one command.

    # read-only: connect + project info, changes nothing
    python -m core.adapters.resolve_smoketest

    # full: also build a 3-clip timeline from real media (creates a
    # timeline + imports 3 clips into the CURRENT project — use a scratch
    # project; the timeline can be deleted afterwards)
    python -m core.adapters.resolve_smoketest --build "/path/to/footage" \
        --clips 00118.MTS 00149.MTS 00130.MTS

Requirements: DaVinci Resolve **Studio** running with a project open, and
Preferences > System > General > "External scripting using" = Local.

Python note: Resolve's `fusionscript` binds CPython ~3.9-3.13. The repo's
analysis venv is 3.14 and will NOT attach — run this with python3.13 /
3.11 / 3.9. Verified end-to-end May 2026 on Resolve Studio 21 Public Beta
(macOS), Python 3.9 / 3.11 / 3.13.
"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m core.adapters.resolve_smoketest")
    p.add_argument(
        "--build",
        metavar="MEDIA_DIR",
        help="also build a timeline from real media in this directory",
    )
    p.add_argument(
        "--clips",
        nargs="+",
        default=["00118.MTS", "00149.MTS", "00130.MTS"],
        help="filenames inside MEDIA_DIR to use for --build",
    )
    args = p.parse_args(argv)

    from core.adapters.resolve import ResolveAdapter, ResolveUnavailable

    a = ResolveAdapter()
    try:
        a.connect()
    except ResolveUnavailable as e:
        print("RESOLVE UNAVAILABLE\n" + str(e))
        return 3

    print("CONNECTED")
    print(json.dumps(a.get_project_info(), indent=2, default=str))

    if not args.build:
        print("\nread-only OK (pass --build MEDIA_DIR for the full test)")
        return 0

    from pathlib import Path

    from core.cutlist import Cut, Cutlist, Marker

    d = Path(args.build)
    cl = Cutlist(
        sequence_name="PCB_smoketest",
        fps=25,
        cuts=[
            Cut(clip=str(d / args.clips[0]), in_=2, out=8, offset=0, label="A"),
            Cut(clip=str(d / args.clips[1]), in_=4, out=9, offset=6, label="B"),
            Cut(clip=str(d / args.clips[2]), in_=1, out=6, offset=11, label="C"),
        ],
        markers=[
            Marker(name="START", time=0, comment="t0"),
            Marker(name="END", time=14, comment="t14"),
        ],
    )
    errs = cl.validate()
    if errs:
        print("cutlist invalid:", errs)
        return 4

    res = a.apply_cutlist(cl)
    print("\n" + res.summary())

    tl = a._project.GetCurrentTimeline()
    items = tl.GetItemListInTrack("video", 1) if tl else []
    markers = tl.GetMarkers() if tl else {}
    report = {
        "timeline": tl.GetName() if tl else None,
        "timeline_count": a._project.GetTimelineCount(),
        "clips_on_V1": len(items),
        "clip_names": [it.GetName() for it in items],
        "marker_frames": sorted(markers.keys()) if markers else [],
    }
    print(json.dumps(report, indent=2, default=str))
    ok = (
        report["clips_on_V1"] == 3
        and report["timeline"] == "PCB_smoketest"
        and report["marker_frames"] == [0, 350]  # 14s * 25fps
    )
    print("\nVERIFIED ✓" if ok else "\nMISMATCH — inspect report above")
    return 0 if ok else 5


if __name__ == "__main__":
    sys.exit(main())
