# `core/` — universal NLE core

The editing brain is **NLE-agnostic**. A cut is decided once as a *cutlist*;
per-NLE adapters render that one cutlist into Premiere, DaVinci Resolve, or
Final Cut. This is the v1.0 direction tracked in
[epic #6](https://github.com/koptsev63/premiere-claude-bridge/issues/6).

```
editing brain (skills/film-editing, /watch)   ← reasons about footage & cuts
        │  produces
        ▼
   Cutlist  ──→  OpenTimelineIO  ──→  Premiere | Resolve | FCPXML
 (core.cutlist)    (interchange)        (core.adapters.*)
```

## Layers

1. **NLE-agnostic editing brain** — `skills/film-editing/` (Murch's Rule of
   Six) + `skills/watch/` (perception). No editor knowledge. Unchanged.
2. **One cutlist** — `core/cutlist.py`. The same JSON shape already used in
   `examples/grave-stakes-teaser/cutlist_v3.json`, formalized, validated, and
   round-tripped losslessly through OpenTimelineIO (OTIO) — the industry
   interchange standard (Resolve reads/writes it natively; FCPXML has OTIO
   adapters; Premiere goes through this project's bridge).
3. **Thin per-NLE adapters** — `core/adapters/` (added incrementally):
   - Premiere — the existing CEP/ExtendScript bridge.
   - DaVinci Resolve — direct Python via the official scripting API
     (**requires Resolve Studio**; external scripting is disabled in the
     free version).
   - Final Cut — FCPXML round-trip (file exchange, not live control).

## `core/cutlist.py`

```bash
# validate a cutlist
python -m core.cutlist validate examples/grave-stakes-teaser/cutlist_v3.json

# cutlist <-> .otio
python -m core.cutlist to-otio   cutlist.json timeline.otio
python -m core.cutlist from-otio timeline.otio cutlist.json

# assert lossless: cutlist == from_otio(to_otio(cutlist))
python -m core.cutlist roundtrip examples/grave-stakes-teaser/cutlist_v3.json
```

Run the tests (no pytest dependency):

```bash
python -m core.tests          # full suite: 93 passed / 0 failed / 1 skipped
```

## Verifying the Resolve adapter

The Resolve path can't be CI-tested (no headless Resolve). Any Studio user
self-verifies in one command:

```bash
# read-only: connect + project info, changes nothing
python -m core.adapters.resolve_smoketest

# full: build a 3-clip timeline from real media (use a scratch project)
python -m core.adapters.resolve_smoketest --build "/path/to/footage"
```

**Verified end-to-end** May 2026 against **DaVinci Resolve Studio 21
Public Beta** (macOS), Python 3.9 / 3.11 / 3.13: connect, get_project_info,
CreateEmptyTimeline, media import, clip placement, markers — frame math
exact (a 14 s marker landed on frame 350 @ 25 fps). Requires
Preferences → System → General → "External scripting using" = Local.

## Dependency / Python note

`opentimelineio` is **optional**. Loading, validating and saving a cutlist
work without it. In-memory `to_otio`/`from_otio` work on any Python that can
import otio.

`.otio` **file** I/O additionally needs **Python 3.12 or 3.13**.
opentimelineio's JSON layer raises `bad any cast` on CPython 3.14 (an upstream
otio C++ binding issue — it can't parse even its own builtin manifest). The
code degrades gracefully: file helpers raise a clear `OtioUnavailable` with
this hint instead of a cryptic crash, and the test suite *skips* (does not
fail) the file round-trip on such interpreters while still hard-asserting the
in-memory lossless round-trip.

The **Resolve adapter** has its own interpreter constraint: Resolve's
`fusionscript` binds CPython ~3.9–3.13. The repo's analysis venv is 3.14 and
will **not** attach — run anything that drives Resolve with python3.13 /
3.11 / 3.9. The adapter still imports cleanly on 3.14; only a live
`connect()` needs a compatible interpreter, and it fails with a clear
`ResolveUnavailable` if the binding can't load.
