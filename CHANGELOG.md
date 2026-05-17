# Changelog

## [Unreleased] — universal NLE core ([#6](https://github.com/koptsev63/premiere-claude-bridge/issues/6), in progress)

The editing brain is now NLE-agnostic. A cut is decided once as a `Cutlist`;
per-NLE adapters render that one cutlist into Premiere, DaVinci Resolve, or
Final Cut. Raw "AI controls Resolve" is already crowded — the differentiator
is the Murch operating system on top, not the driver underneath.

### Added — `core/`

- **`core/cutlist.py`** — the cutlist intermediate representation. Same JSON
  shape as `examples/grave-stakes-teaser/cutlist_v3.json`, formalized:
  validation (out>in, no overlap, no negatives) and **lossless
  OpenTimelineIO round-trip** (verified in-memory against the real example),
  plus structural reconstruction (gap-honoring) and a CLI
  (`validate`/`to-otio`/`from-otio`/`roundtrip`). `opentimelineio` is an
  *optional* dependency.
- **`core/capabilities.{json,py}`** — per-backend matrix
  (live_control, requires_paid_tier, round_trip_only, markers,
  triggered_export, native_otio, unavailable_features). Facts verified
  May 2026.
- **`core/adapters/`** — one verb set, three drivers:
  - `PremiereAdapter` compiles a cutlist to ExtendScript run via
    `mcp__premiere__pr_eval_jsx` (Premiere has no live API; the bridge is
    the link).
  - `ResolveAdapter` — direct official Python scripting API. Lazy, guarded
    bootstrap; raises `ResolveUnavailable` with an actionable hint instead
    of a cryptic ImportError. **Requires Resolve Studio** (external
    scripting is disabled in the free version).
  - `FcpxmlAdapter` — native FCPXML 1.10 writer (round-trip; no otio
    file-IO dependency).
  - Shared `apply_cutlist()` orchestration consults the matrix and degrades
    gracefully (round-trip backends get a project file, never live calls).
- **`core/review_loop.py`** — NLE-neutral self-review: deterministic Murch
  arithmetic (`analyze_cutlist`: §VII 2-4× ratio, §X monotony, beat-type
  pacing), `/watch` plan, NLE-free ffmpeg rough assembler, immutable
  validated `CutlistPatch`, `ReviewLoop` history/diff. Taste stays with the
  LLM by design; the harness only does the deterministic parts.
- **`core/tests/`** — dependency-free runner (`python -m core.tests`),
  values pinned to the real example. **90 checks: 90 passed, 0 failed,
  1 skipped** (the skip is documented below).
- `skills/film-editing/SKILL.md` §XVI documents the core + the review loop
  so the editing brain uses it.

### Known constraint

`.otio` **file** I/O needs Python 3.12 or 3.13. opentimelineio's JSON layer
raises `bad any cast` on CPython 3.14 (upstream otio C++ binding issue — it
cannot parse even its own builtin manifest). In-memory `to_otio`/`from_otio`
work on any Python with otio; the file helpers raise a clear
`OtioUnavailable` and the suite *skips* (does not fail) the file round-trip
on such interpreters while still hard-asserting the in-memory one.

## [0.2.0] - 2026-05-15

### Added — `skills/watch/` (vendored from [bradautomates/claude-video](https://github.com/bradautomates/claude-video) + extended)

Closes the "stop-frames only" honest limitation called out in v0.1. Lets Claude actually watch a clip:

- `yt-dlp` download (URL or local path)
- `ffmpeg` frame extraction (~30–100 frames auto-scaled to clip duration, 2 fps cap)
- Timestamped transcript via three Whisper backends with auto-fallback:
  1. **`local` (openai-whisper CLI)** — our extension on top of upstream. **No API key, runs offline, free.** Default model `medium`, override via `WATCH_LOCAL_WHISPER_MODEL=small|large-v3`. Tested on Hungarian field-recorded interview from Grave Stakes — produces real transcript where the v0.1 `tiny` model returned garbage.
  2. **Groq `whisper-large-v3`** (cloud, fastest, ~$0.0002/min)
  3. **OpenAI `whisper-1`** (cloud, slowest, ~$0.006/min)
- Section-focused mode via `--start`/`--end` flags
- New `--language` flag passed through to local backend (ISO-639-1 or English name; auto-detection is unreliable on noisy field audio)
- Frames + transcript handed back as multimodal input — Claude `Read`s each frame path

Full attribution preserved in `skills/watch/LICENSE`, `skills/watch/.claude-plugin/plugin.json`, and a new `skills/watch/ATTRIBUTION.md`. The `watch` skill is MIT-licensed and is **not** a fork — clean vendor copy. Upgrade path documented in ATTRIBUTION.

### Changed — `skills/film-editing/SKILL.md`

- New §XIV "Real video perception via the bundled `/watch` skill" — wires the new skill into the editing operating system with concrete recipes (decisive moment finding, interview transcription with proper Whisper backend, reference-trailer study)
- Updated cost-discipline note: use `analyze_clips.py` first to rank, `/watch` only the top 10–15 candidates

### Changed — root `README.md`

- New "Skills" section now lists both `film-editing/` and `watch/`
- Honest-limitations section softened: with `/watch` bundled, "Claude cannot watch clips" is no longer true. Remaining limits are sub-frame timing, micro-expression nuance, dramaturgy invention.

### Changed — `skills/film-editing/tools/`

- `horizon_detect.py` v2 — sky-ground segmentation (HSV mask + RANSAC line fit) replaces naive Hough-line averaging; falls back to length-weighted Hough when no sky visible. Validated on Grave Stakes 12-clip teaser cutlist.

### New examples

- `examples/grave-stakes-teaser/build-scripts/build_v4_final.py` — first turnkey final render (intro + outro + Kevin MacLeod music, 72s)
- `examples/grave-stakes-teaser/build-scripts/build_v5_brides_cigar.py` — v5 with Suno-generated Balkan brass track replacing the cliched MacLeod default

### Roadmap moved to v0.3

- OCR per clip via tesseract
- Face count + sentiment via mediapipe
- Optical-flow direction analysis for match-cut suggestions
- Auto silent-trim per clip
- Multicam audio-waveform sync
- Skill packs: trailer-bridge, reel-bridge, podcast-cut-bridge, interview-bridge

---

## [0.1.0] - 2026-05-04

### Initial release

Three-component bridge giving Claude programmatic control of Adobe Premiere Pro.

**Components:**
- `mcp-server/` — Node MCP server with 10 tools (status, project info, sequence info, timeline list, selected clips, playhead control, marker, AME export, eval-jsx escape hatch)
- `cep-extension/` — Adobe CEP panel running ExtendScript via CSInterface; live WS to MCP server on port 9876
- `skills/film-editing/` — Walter Murch's *In the Blink of an Eye* encoded as decision rules (Rule of Six, Blink theory, eye trace, decisive moment, Russian↔English terminology) + `tools/analyze_clips.py` clip-analysis pipeline

**Features:**
- Multi-instance-safe WS server: retries on EADDRINUSE every 3s
- Self-healing socket lookup via `wss.clients[0]` adoption
- ExtendScript JSON polyfill (Adobe never shipped JSON in their ES3 engine)
- Per-clip motion score, audio peak detection, 6-frame "motion strip" generation, HTML contact sheet
- Optional Whisper speech-to-text integration for dialogue clips

**Case study (`examples/grave-stakes-teaser/`):**
- 108 raw .MTS clips (4.4 GB) → fully logged in 12 minutes
- Three teaser sequences built (v1 rule-based, v2 Murch-aligned, v3 data-driven)
- Final 61-sec teaser with 12 cuts and 8 emotional-beat markers
- Reproducible: `report.json` + `cutlist` shipped

### Bugs fixed during Grave Stakes case study

These were all real failures discovered while building the first teaser end-to-end. The patches are in v0.1:

| Bug | Symptom | Fix |
|---|---|---|
| Multiple Claude sessions race to bind port 9876; only one wins, rest silently broken | `pr_status` returns `connected: false` even when panel shows green | `startWsServer()` retries on EADDRINUSE every 3s in `mcp-server/server.js` |
| Cached `panelSocket` goes stale after CEP panel reload | Same `connected: false`, manual Reconnect doesn't help | `getActiveSocket()` falls back to scanning `wss.clients` for any open WebSocket |
| ExtendScript ES3 has no native `JSON` object | All typed tools (`pr_get_project_info` etc.) error with `JSON is undefined`, only `pr_eval_jsx` partially works | Minimal JSON polyfill prepended to `host.jsx` |
| `pr_eval_jsx` description claims "last expression returned" but wrapper IIFE has no return | User-supplied expressions return empty string | Documented requirement: explicit `return` needed |

### Limitations (honest)

The bridge does not give Claude:
- Real-time motion perception (frames are stop-extracted, not played)
- Sub-frame timing intuition (Murch-level "8 frames late" is impossible)
- Take-by-take micro-expression evaluation
- Dramaturgy from raw footage (structure must be specified)

Position the tool as: senior assistant editor + automation, not director's editor.

## [Unreleased / Roadmap]

- OCR per clip via tesseract (T-shirt logos, on-screen signage)
- Face count + sentiment via mediapipe
- Optical-flow direction analysis for match-cut suggestions
- Auto silent-trim per clip
- Multicam audio-waveform sync
- Pro tier: Whisper auto-language detection + medium-model
- Skill packs: trailer-bridge, reel-bridge, podcast-cut-bridge, interview-bridge
