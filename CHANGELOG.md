# Changelog

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
