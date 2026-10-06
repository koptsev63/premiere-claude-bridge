# Changelog

## [0.3.0] - 2026-10-06 — universal NLE core ([#6](https://github.com/koptsev63/premiere-claude-bridge/issues/6), in progress)

The editing brain is now NLE-agnostic. A cut is decided once as a `Cutlist`;
per-NLE adapters render that one cutlist into Premiere, DaVinci Resolve, or
Final Cut. Raw "AI controls Resolve" is already crowded — the differentiator
is the Murch operating system on top, not the driver underneath.

### Added - ripple variants of a hand-built sequence (06.10.2026)

Six versions of a director's hand-cut 377-s teaser were built in one
evening as *declensions* of his cut: ripple-delete a scene, ripple-insert
a block of new shots, remap his subtitles, keep every other frame. The
pieces that made it repeatable:

- **`core/ripple.py`** - plan compiler. Ops in ORIGINAL seconds of the base
  (`D` delete, `I` insert, `A` audio-only, `R` remove audio items) compile
  to builder order (all deletions first, then inserts in post-deletion
  coordinates, same-anchor inserts reversed so list order = screen order,
  audio placements in FINAL coordinates) plus a time map that remaps an
  SRT of the base. Defects it encodes: an inserted clip that later passed
  through `extract` made the sequence un-exportable ("low-level
  exception", two hours of bisecting); the finale room tone landed 67 s
  early when audio used post-deletion instead of final coordinates; a
  subtitle starting 0.24 s inside a deleted shot vanished whole. 24 tests.
- **`core/jsx/ripple_ops.jsx`** - the ExtendScript executor: clone the
  base, QE `extract` for deletions (API `move()` to the left leaves black
  frames), razor + drop <0.3-s audio slivers + move right + `overwriteClip`
  for inserts, audio-only overwrite, title-still swap with scale copy
  (3840-px PNG in a 1080p sequence = scale 50), caption track from SRT,
  built length asserted against the compiler's expectation (Premiere's
  `sequence.end` does not shrink after `extract`).
- **Hard rules 20-24** in `skills/film-editing/SKILL.md`: caption tracks
  are invisible to the API and do not ripple (hide the base's track before
  cloning; every visible caption track is burned by `exportAsMediaDirect`);
  AAC-in-mp4 music slivers kill the audio renderer (relink to PCM); clips
  imported from another project may carry a Rec.709 override and look
  grey; a music block for inserted shots is cut on the song's beat grid
  with the inserts' own sound removed.

### Added - direct runners, loudness, screen replacement (October 2026)

Tools that were built on jobs after the reel and lived outside the repo.
Ported with their job-specific parts turned into arguments; where the port
found a defect in the original, the defect is named below instead of being
carried over.

- **`mcp-server/pk.js`** - the direct path into Premiere. The MCP server owns
  one WebSocket port and the panel connects to whichever instance won the
  race for it, so with several Claude sessions open most of them see `panel
  not connected`. `pk.js` goes around: Chrome DevTools Protocol to the
  panel's CEF debug port (8088, from `cep-extension/.debug`), one
  `Runtime.evaluate` wrapping `evalScript` in a promise. `info`, `eval`,
  `eval -`, `file`, `targets`. No new dependency: Node 22+ uses the built-in
  WebSocket, older Node falls back to the `ws` already installed. Tested
  against a fake debug endpoint (target selection, the wrapper, exit codes,
  the closed-port message); **not** exercised against a live Premiere in
  this change. 18 tests.
- **`core/adapters/resolve_run.py`** - Resolve from a shell. `dump` (+
  `gaps()` for one-frame holes), `still` and `grab` for composited timeline
  frames as PNG, `exec` to run a script with `resolve`, `pm`, `project`,
  `timeline`, `mp`, `fps` preloaded. One behaviour change from the private
  tool: `grab` used to call `DeleteAllRenderJobs()` before and after, which
  wiped whatever the editor had queued. It now starts and deletes only the
  jobs it added. Stub-tested; not run against a live Resolve here. 36 tests.
- **`core/loudness.py`** - level the shots, then normalise. Gated RMS per
  shot pulled to the median (clamp ±9 dB), optional 12 ms declick at the
  joins, one static gain to the target and a 4x-oversampled limiter at the
  true-peak ceiling; picture is stream-copied. Gate: integrated within ±1 LU,
  true peak under the ceiling, spread between shots ≤ 2 dB. Two things
  changed on the way in. The original eased every shot back to unity gain at
  its edges, which keeps the jump audible exactly on the cut - on a synthetic
  three-take join 19 dB apart it left 3.1 dB of spread where the ±9 dB clamp
  accounts for 0.7; the gain now changes at the join and holds. And `alimiter` delays the signal by its look-ahead unless told
  to compensate: the first port landed the declick dip 5 ms late and the
  test caught it, hence `latency=1`. 48 tests.
- **`core/screen_comp.py`** - screen replacement. A clip composited into a
  screen in a handheld plate: ECC affine camera track against frame 0 with
  the screen masked out, content warped into a quad and/or a mask (the
  original was axis-aligned only), optional glass/CRT look (bulge, black
  lift, rim, kept reflections, glow, scanlines), all I/O through ffmpeg pipes
  with the YCbCr matrix and range stated both ways. The gate compares the
  written file with the plate: outside the screen it must come back the same
  (mean abs error ≤ 2.5 levels, channel shift ≤ 1), inside it must have
  changed. The gate earned its place on the first run: on current ffmpeg the
  `-color_primaries` / `-color_trc` output options are ignored for frames
  that arrive as raw RGB, so the file shipped tagged `bt709 / unset / unset`.
  Fixed with `setparams` in the filter chain. Thresholds are engineering
  defaults verified on synthetic footage, not field-calibrated. 69 tests.
- **`core/qc.py` → `qc_delivery()`** - the hard-fail half of the private
  acceptance script that `qc.py` did not have: the file decodes end to end
  with nothing on the error channel, has the promised size and frame rate,
  and carries an audio stream. 6 tests.

Not ported, on purpose: the job-passport preflight and the 3x3 proof sheet
(both are a schema for one kind of job; the generic remainder is `gaps()`
above, and thumbnail sheets are what hard rules 4 and 11 warn against); the
Resolve conform / b-roll / centring / master scripts built on that same
passport (run your own through `resolve_run exec`); the subject matte,
spot-removal and head-centring scripts (tied to one matting model and one
framing convention); the private colour check and ΔE scripts (already here
as `core.colorgate`); and the S-Log3 grading scripts (white-balance gains
and look tables are hard-coded per job - a general version needs a
neutral-patch input and a gate of its own first).

### Added — colour gate + human-in-the-loop round trip (August 2026)

Both modules come out of one production job: a vertical announcement reel,
cut in Premiere, conformed and graded in Resolve, delivered as sequences in
the director's own project. Everything here is calibrated on what he
rejected, not on what the literature suggests.

- **`core/colorgate.py`** — a grade now has to pass two gates. *Upper:*
  clipping (% luma > 250), oversaturation (% S > .85 at V > .5), skin Cr and
  skin saturation, each against the ungraded base with both a relative
  headroom and an absolute cap. *Lower:* mean CIE ΔE against the base, so a
  look nobody can see fails as loudly as a burnt one. Reproduces the
  director's own calls on the reel: Kodak 2383 at full strength 5.47%
  clipped and Fuji 3513 5.67% over a 1.59% base (ceiling 2.79%) — both
  rejected; the timid grade he "did not notice at all" measured ΔE 3.23 —
  rejected; the three looks he approved measured 5.57, 5.70 and 10.18 —
  passed. Hence the ΔE floor sits at 5.0, not at the textbook 2.5 (that
  threshold is for flat patches, and 3.23 was invisible on a moving
  picture). ffmpeg-only, numpy is an optional accelerator. 32 tests.
- **`core/prproj.py`** — the loop closes. A saved `.prproj` (gzipped XML) is
  parsed back into sequences, caption cues and picture cuts, so renders
  conform to the human's cut instead of the machine's original plan.
  Verified against the reel: 29 raw caption items → the 23 lines actually on
  screen, 16 of them retyped by the director, all matching the approved
  version exactly. Two traps are handled explicitly: a cue with no text
  block is *untouched*, not empty (Premiere keeps that text in the linked
  caption file), and a razor half is only a razor half when it starts on a
  picture cut — the naive "textless cue after an edited one" rule ate three
  whole lines before the cut list was brought in. Sidecar text resolves by
  order, never by overlap: transcript time and cut time are different
  clocks, and overlap slid every line one cue out of place. 32 tests.

### Fixed

- **`core/adapters/resolve.py` — one frame lost per clip.** `AppendToTimeline`
  treats `endFrame` as **exclusive**; the adapter passed `start + duration -
  1`, so every clip came up a frame short. Measured on the reel: 13 clips
  rendered 64.767s instead of 65.200s at 30fps — exactly 13 frames — and
  subtitles built for the true timeline drifted 0.43s by the end. The error
  scales with clip count, which is why it hides on short tests. Now covered
  by a stubbed-media-pool unit test.
- **`core/tests/test_denoise.py`** imported `pytest` without using it, so
  `python -m core.tests` died on a clean checkout — the aggregate runner is
  meant to have no pytest dependency. Suite now runs stdlib-only: 497 green.

### Added - OpenReel-inspired audio & edit-intelligence (June 2026)

Five self-contained modules. Algorithms (not code) ported from the MIT browser
editor OpenReel Video, each mapped to a real pain hit on the Дед / Grave Stakes
footage. All run on the ffmpeg already required (zero new pip deps); numpy is an
optional accelerator only.

- **`core/ducking.py`** - music auto-lowers under speech. Speech intervals come
  from our Whisper word timestamps (more precise than OpenReel's RMS guess) or
  from `core.silence`; an S-curve attack/release envelope drives a music gain
  that dips during dialog. Verified: -7.9 dB dip at reduction 0.6 (theory -8.0
  dB). Wired into `core.render.render_with_ducked_music()`, replacing the
  hand-balanced Grave Stakes mix. 20 tests.
- **`core/denoise.py`** - 3-stage cleanup (rumble high-pass, afftdn broadband,
  optional hum notch, level normalize) before speech-to-text, so Whisper
  mis-hears fewer words. ~15 dB suppression on pure noise. Wired into the
  Whisper path via `whisper.py --denoise`. 12 tests.
- **`core/asr_verify.py`** - the transcript second pass we wanted: flags low
  confidence, repetition loops, truncated tokens (the "Ско"->"скот" class),
  impossible timing, mixed Cyrillic/Latin garble; `apply_corrections()` fixes
  them. Wired into `subtitles.write_timeline_srt/ass(verify=, corrections=)`.
  66 tests.
- **`core/highlights.py`** - auto-pick best moments (energy + speech density,
  snapped to scene cuts) into a render-ready `Cutlist`, for auto short versions.
  Picks loud over quiet, verified. 16 tests.
- **`core/beats.py`** - beat/BPM detection (RMS flux, adaptive threshold, BPM
  voting) + `snap_cutlist_to_beats()` to cut a montage in time to music.
  Recovers 120 BPM on a click track. 28 tests.
- **`core/render.py`** - banding lesson baked in: always libx264 (never a
  hardware/VideoToolbox encoder, which banded the flat-black Grave Stakes title
  cards); optional `pix_fmt="yuv420p10le"` for gradient-heavy masters.

### Added — automated editing pipeline

End-to-end generation, dogfooded to produce **v5** on the Grave Stakes
footage (two contrasting Murch-clean teasers, see
`examples/grave-stakes-teaser/benchmark/`):

- **`skills/film-editing/tools/shake_detect.py`** — OpenCV phase-correlation
  camera-shake metric (no libvidstab). Honest calibration:
  `flag_stabilization_relative()` flags only outliers (median + k·MAD),
  never blanket — handheld doc energy is intentional. Wired into
  `analyze_clips.py`.
- **`core/cleanup.py`** — per-clip technical corrections from the analysis
  report: horizon leveling (always when flagged) + outlier-only
  stabilization. `ResolveAdapter.apply_corrections()` undoes tilt live via
  the scripting API; deshake stays in the ffmpeg path (Resolve's
  stabilizer isn't cleanly scriptable). 11 tests.
- **`core/value.py`** — the "meat" model: composite value
  (audio-peak + motion − conditional shake penalty) + an explicit
  human/LLM `meat_tag` override; `ValuePool` protects the strong material
  and reserves anchors for the emotional-center beats. 15 tests.
- **`core/variants.py`** — two contrasting strategies (DRIVE vs BREATH),
  both `is_clean` by construction; length-aware assignment (a clip too
  short for its beat is skipped *with a note* — physics > value >
  protection). 19 tests.
- **`core/probe.py`** — capability probe + `select_adapter()`;
  live-verified. **`core/conform.py`** — clip relink to real media.
- Full suite: **168 passed / 0 failed / 1 skipped**.

### Added — discovery + finishing (after assessing MIT peers video-use / claude-code-video-toolkit; ideas only)

- **`core/library.py`** — smart media library: tag + search footage by
  speech/tags/name, `find_lines()` a character's dialogue, `to_cutlist()`
  pulls matches into a new sequence. NO face recognition (by what's said).
- **`core/subtitles.py`** — transcript → SRT (deliverable) + styled ASS
  karaoke (2-word chunks, MarginV-90 safe-zone; approach credited to
  browser-use/video-use, MIT). `skills/watch` whisper now emits
  `--word_timestamps` so karaoke has real word times.
- **`core/overlays.py`** — lower-thirds / title / brand via ffmpeg
  drawtext; `from_markers()` auto-titles from the cutlist's beats.

### Audit (full static + dynamic pass)

pyflakes clean; removed dead code + the leftover `deshake` drift in
`cleanup.vf_chain` (stabilization is Resolve-side only). Secret scan of
tree + git history clean (no leaked keys/tokens in this public repo).
E2E verified through the committed core modules on real footage
(12/12 pipeline steps). Suite: **258 passed / 0 failed / 1 skipped**.

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
    scripting is disabled in the free version). **Verified end-to-end**
    against DaVinci Resolve Studio 21 Public Beta (macOS) on Python
    3.9/3.11/3.13: connect, project info, CreateEmptyTimeline, media
    import, clip placement, markers — frame math exact (14 s → frame 350
    @ 25 fps). Re-runnable by any Studio user via
    `python -m core.adapters.resolve_smoketest`.
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
  values pinned to the real example. **93 passed, 0 failed, 1 skipped**
  (the skip is documented below).
- `skills/film-editing/SKILL.md` §XVI documents the core + the review loop
  so the editing brain uses it.

### Known constraints (Python interpreter)

- `.otio` **file** I/O needs Python 3.12 or 3.13. opentimelineio's JSON
  layer raises `bad any cast` on CPython 3.14 (upstream otio C++ binding
  issue — it cannot parse even its own builtin manifest). In-memory
  `to_otio`/`from_otio` work on any Python with otio; the file helpers
  raise a clear `OtioUnavailable` and the suite *skips* (does not fail)
  the file round-trip there while still hard-asserting the in-memory one.
- The **Resolve adapter** needs Python ~3.9–3.13 for a live `connect()`:
  Resolve's `fusionscript` does not bind CPython 3.14 (the repo's analysis
  venv). The adapter imports cleanly on 3.14; only attaching needs a
  compatible interpreter and fails with a clear `ResolveUnavailable`.

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
