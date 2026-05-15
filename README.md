# premiere-claude-bridge

> Control Adobe Premiere Pro from Claude Code (or any LLM with MCP support).
> Open-source. MIT-licensed. Includes Walter Murch's editing operating system as a skill.

```
Claude  ──stdio──▶  MCP server  ──WebSocket──▶  CEP panel  ──evalScript──▶  Premiere host.jsx
```

**What it does:** import clips, build sequences, set in/out points, place markers, run arbitrary ExtendScript, queue AME exports — all via natural-language prompts. Plus a logging pipeline that produces an HTML contact sheet of any folder of footage in minutes (motion score, audio peaks, 6-frame motion strips per clip).

**What it doesn't do:** fine-cut editing, dub-take selection, anything requiring real-time motion perception. See [Honest limitations](#honest-limitations).

---

## Demo

```
You:    "Open Premiere. Import all .MTS from ~/Desktop/Footage/.
        Create a 1080p25 sequence called 'Rough'. Place clips
        00118 (1-6s), 00149 (4-9s), 00130 (1-5s) on V1 in order."

Claude: ✓ Imported 108 clips into bin '01_Source_MTS'
        ✓ Created Rough — 1920x1080, 25fps, 3 V / 6 A tracks
        ✓ Placed 3 clips on V1, total 14 sec
```

→ See `examples/grave-stakes-teaser/` for a full case study (108 raw clips → 61-sec teaser, 7 minutes of work).

---

## Install

### Prerequisites

- macOS or Windows with Adobe Premiere Pro 2024+ installed
- Node.js 20+
- Claude Code (for the MCP integration)
- ffmpeg + Python 3.11+ (for the analysis tools)

### 1. Clone

```bash
git clone https://github.com/USERNAME/premiere-claude-bridge.git
cd premiere-claude-bridge
```

### 2. Install MCP server

```bash
cd mcp-server
npm install
```

### 3. Register with Claude Code

Add to your `~/.claude/mcp_servers.json`:

```json
{
  "premiere": {
    "command": "node",
    "args": ["/absolute/path/to/premiere-claude-bridge/mcp-server/server.js"]
  }
}
```

### 4. Install CEP panel

**macOS:**

```bash
# Allow unsigned CEP extensions
defaults write com.adobe.CSXS.11 PlayerDebugMode 1
defaults write com.adobe.CSXS.12 PlayerDebugMode 1

# Symlink the extension into the user's CEP directory
ln -s "$(pwd)/cep-extension" \
  ~/Library/Application\ Support/Adobe/CEP/extensions/com.koptsev.claude-bridge
```

**Windows (PowerShell as Admin):**

```powershell
New-ItemProperty -Path "HKCU:\Software\Adobe\CSXS.11" -Name PlayerDebugMode -Value 1 -PropertyType String -Force
New-ItemProperty -Path "HKCU:\Software\Adobe\CSXS.12" -Name PlayerDebugMode -Value 1 -PropertyType String -Force
mklink /D "$env:APPDATA\Adobe\CEP\extensions\com.koptsev.claude-bridge" "$(Get-Location)\cep-extension"
```

### 5. Open the panel in Premiere

In Premiere: **Window → Extensions → Claude Bridge**

Panel should show "Connected to Claude" in green.

### 6. (Optional) Install analysis tools

```bash
pip install pillow
# Whisper if you want speech-to-text on dialogue clips:
pip install openai-whisper
```

---

## Tools (MCP commands Claude can call)

| Tool | What it does |
|---|---|
| `pr_status` | Bridge health check + Premiere version + active project info |
| `pr_get_project_info` | List all bins + project items (with nodeId for reference) |
| `pr_get_active_sequence` | Sequence dimensions, fps, track count |
| `pr_list_timeline` | Full track-by-track clip dump (in/out, start/end, duration) |
| `pr_get_selected` | Currently selected clips on timeline |
| `pr_get_playhead` / `pr_set_playhead` | CTI control |
| `pr_add_marker` | Place marker at given time with name + comment |
| `pr_export_ame` | Queue export to Adobe Media Encoder with .epr preset |
| `pr_eval_jsx` | Escape hatch — run any ExtendScript code |

## Skills

### `film-editing/`

Encodes Walter Murch's *In the Blink of an Eye* as decision rules:
- The **Rule of Six** (Emotion 51% → Story 23% → Rhythm 10% → Eye-trace 7% → 2D 5% → 3D 4%)
- Blink theory, misdirection, idea cuts, dreaming in pairs, decisive moment
- Pacing tables for trailers/teasers/montage/interview/title cards
- Russian↔English terminology mapping

Plus:
- `tools/analyze_clips.py` — folder-of-clips → HTML contact sheet with motion scores, audio peaks, **horizon tilt detection**, 6-frame motion strips per clip. Optional Whisper speech-to-text.
- `tools/horizon_detect.py` — sky-ground segmentation + Hough fallback for per-clip tilt angle, with auto-correction filter generation.

### `watch/` *(vendored from [bradautomates/claude-video](https://github.com/bradautomates/claude-video), MIT)*

Closes the "stop-frame only" gap of the bridge. Lets Claude actually watch a clip:
- Downloads via `yt-dlp` (URL) or accepts local path
- Extracts **30–100 auto-scaled frames** (vs our 6-frame strip)
- Pulls a **timestamped transcript** — captions or Whisper API (Groq `whisper-large-v3` preferred — handles Hungarian, Russian, Spanish, etc.)
- Hands frames + transcript to Claude as multimodal input

When designing a teaser, the recommended flow is:
1. `analyze_clips.py` → cheap pass over the whole folder (motion + audio + horizon)
2. Pick 10–15 candidates from the HTML contact sheet
3. `/watch` only those candidates to verify decisive moments and pick precise in/out

See `skills/watch/ATTRIBUTION.md` for credit and `skills/film-editing/SKILL.md` §XIV for the integrated workflow.

---

## Honest limitations

The bridge gives Claude full programmatic control of Premiere. With the `watch` skill bundled, the previous "stop-frames only" limitation is **largely closed** — Claude can now extract 30–100 frames per clip and a real transcript. What remains:

- **Sub-frame timing intuition.** Murch-level "trim 8 frames" calls still need a human editor.
- **Micro-expression nuance.** Frames + transcript get you 80% of the way; the last 20% is taste.
- **Dramaturgy from nothing.** Structure must be specified — the skill won't invent the through-line.

Claude **can** (with both skills active):
- **Auto-log** large folders (108 clips analyzed in ~12 min on M1)
- **Watch any clip in detail** when needed (`/watch <path> --start ss --end ss+dur`)
- **Build structural assemblies** from your selects with rule-based pacing
- **Iterate cutlists** — 3 variants in seconds for you to pick
- **Read interview content** in any language (via Groq Whisper)
- **Study reference trailers** by URL and apply the structure to your material
- **Handle technical chores** — gap removal, horizon auto-level, audio normalization, marker placement, AME export

Position it as: senior assistant editor + automation, not director's editor.

---

## Architecture

See [`docs/architecture.md`](docs/architecture.md).

Notable design choices:
- WebSocket server in MCP process is **multi-instance safe** — if a previous Claude session holds the port, new instances retry every 3s until the holder dies. Without this, multiple Claude sessions would race-bind and silently break.
- ExtendScript host has a **JSON polyfill** (Adobe never shipped JSON in their ES3 engine; without the polyfill every typed tool fails).
- Self-healing socket lookup adopts live `wss.clients[0]` if the cached `panelSocket` goes stale after a CEP panel reload.

These were all real bugs found during the Grave Stakes case study. See `CHANGELOG.md`.

## Contributing

Add a skill: drop a `SKILL.md` into `skills/<your-skill>/`. The bridge core is generic — skills are how you encode editing knowledge. PRs welcome for genre-specific skill packs (trailer cutdowns, vertical reels, multicam sync, podcast cleanup, etc.).

## License

MIT — see [`LICENSE`](LICENSE).

## Status

🟡 **Beta.** Currently in private testing with ~20 invited editors. Public release planned 2 weeks after feedback iteration. To request a beta seat: see TG channel [@koptsev_AI](https://t.me/koptsev_AI).
