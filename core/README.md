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

## Audio & edit-intelligence modules

Algorithms ported from the MIT browser editor OpenReel Video, each tied to a
real pain on the Дед / Grave Stakes footage. ffmpeg-only (no new pip deps).

```bash
# auto-duck music under speech (replaces hand-balancing a music bed)
python -m core.ducking MUSIC OUT --video CUT.mp4 --reduction 0.5
# clean noisy audio before transcription (fewer Whisper mis-hearings)
python -m core.denoise IN OUT --asr
python skills/watch/scripts/whisper.py CLIP.mp4 --denoise   # same, in the watch flow
# verify a transcript, flag likely mis-hearings (exit 1 if any)
python -m core.asr_verify transcript.json
# auto-pick the best moments into a render-ready cutlist (short version)
python -m core.highlights CLIP.mov --target 75 [--transcript t.json]
# detect BPM / beats to cut a montage in time to music
python -m core.beats TRACK.wav
```

- `core/ducking.py` - speech-aware music ducking (S-curve envelope from Whisper
  word times or `core.silence`). Use `render.render_with_ducked_music()`.
- `core/denoise.py` - rumble / broadband / hum / normalize pre-pass for ASR.
- `core/asr_verify.py` - transcript second pass; `flag_suspects` +
  `apply_corrections`. Hook via `subtitles.write_timeline_srt(verify=...)`.
- `core/highlights.py` - energy + speech-density scoring, scene-cut snapping.
- `core/beats.py` - beat/BPM detection + `snap_cutlist_to_beats()`.

Encoder note: picture renders always use libx264. A hardware (VideoToolbox)
H.264 encoder banded the flat-black Grave Stakes title cards; libx264 does not.
For gradient-heavy masters pass `pix_fmt="yuv420p10le"` to `build_render_plan`.

## Finishing: the colour gate and the round trip

Two modules from the August 2026 reel job, where the machine cut, the human
recut, and the grade had to survive a director's eye.

```bash
# is this grade burnt — or is it invisible? (exit 1 = do not ship)
python -m core.colorgate BASE.mov GRADED.mp4 --frames 8 --upto 60
# what did the human actually approve in his project?
python -m core.prproj "reel.prproj" --srt subs.srt [--track N] [--json]
```

- `core/colorgate.py` - two-sided gate. Upper: clipping, oversaturation, skin
  Cr and saturation, each against the ungraded base with a relative headroom
  and an absolute cap. Lower: mean CIE ΔE (floor 5.0) so a look nobody can see
  fails as loudly as a burnt one. `check_grade()` measures, `judge()` decides
  on stats you already have, `assert_ok()` raises. numpy optional.
- `core/prproj.py` - reads a saved `.prproj` (gzipped XML): sequences, caption
  cues per track, picture cuts. `conform_captions()` is the one call — it
  picks the track the human worked on, stitches razor halves back against the
  cut list, and resolves untouched lines from the sidecar **by order**
  (transcript time ≠ cut time; overlap matching slides every line one cue
  out). Feed the result to `subtitles.to_srt/to_ass`.
