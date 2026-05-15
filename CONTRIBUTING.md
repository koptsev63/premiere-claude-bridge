# Contributing to premiere-claude-bridge

Thanks for considering a contribution. This project is built by and for film editors who happen to code, so the bar is "make it useful in real production work" — not "elegant abstraction for its own sake".

## Quick orientation

| If you want to… | Look here |
|---|---|
| Add a new MCP tool | [`mcp-server/server.js`](mcp-server/server.js) + matching ExtendScript in [`cep-extension/jsx/host.jsx`](cep-extension/jsx/host.jsx) |
| Encode a new editing principle | [`skills/film-editing/SKILL.md`](skills/film-editing/SKILL.md) (markdown, no code needed) |
| Add a genre-specific recipe | New folder under [`skills/`](skills/) — see [`skills/film-editing/SKILL.md`](skills/film-editing/SKILL.md) for the format |
| Add an analysis tool | [`skills/film-editing/tools/`](skills/film-editing/tools/) — Python, must run via the venv at `tools/.venv` |
| Improve `/watch` | Don't edit `skills/watch/` directly — it's vendored from upstream. Open a PR to [bradautomates/claude-video](https://github.com/bradautomates/claude-video) and we'll re-vendor. |
| Document a workflow | [`docs/`](docs/) or [`examples/`](examples/) |

## Development setup

```bash
git clone https://github.com/koptsev63/premiere-claude-bridge.git
cd premiere-claude-bridge

# MCP server
cd mcp-server && npm install && cd ..

# Analysis tools (Python venv to avoid polluting system)
cd skills/film-editing/tools
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd ../../..

# Run MCP server in standalone mode for testing
node mcp-server/server.js
# → listens for WebSocket on :9876, MCP stdio on stdin/stdout
```

## Style

**Code:**
- Node: keep `mcp-server/server.js` zero-dep beyond `@modelcontextprotocol/sdk` and `ws` and `zod`. No build step.
- Python: standard library + `pillow` + `opencv-python-headless` + `numpy` + `openai-whisper`. Add to `requirements.txt` if you introduce a new dep — and explain why in the PR description.
- ExtendScript: ES3 only (Adobe's JS engine). No `let`, no arrow functions, no `JSON` (we ship a polyfill in `host.jsx`).

**Markdown:**
- One `H1` per file (the title).
- Editing/film-jargon is fine in `skills/film-editing/SKILL.md` (the audience is editors). Keep the root `README.md` accessible to non-filmmakers.
- Russian / English bilingual is fine. Cite Walter Murch when relevant.

**Commits:**
- Conventional commits prefix where it makes sense: `feat`, `fix`, `docs`, `chore`, `refactor`.
- Body explains *why*, not *what* — the diff already shows what.
- Co-authored AI commits welcome — keep the `Co-Authored-By:` trailer if you used Claude/Cursor/Copilot.

## What I'm looking for

**Strongly want PRs for:**
- Genre skill packs (music videos, podcast cleanup, weddings, sports highlights, vertical Reels/Shorts)
- DaVinci Resolve and Final Cut Pro variants of the same bridge architecture
- Better audio analysis (silence detection for podcast cuts, music BPM for cut sync)
- Multicam audio-waveform sync
- Windows install path testing (currently macOS-tested-only)
- Translations of `skills/film-editing/SKILL.md` into your language

**Would consider but ask first via issue:**
- Anything that adds a heavy runtime dependency (PyTorch, CUDA, large models)
- A web UI (the value of this is "no UI, just prompt")
- A SaaS / hosted variant (out of scope for OSS core; bring it up if you want to fork)

**Politely decline:**
- "I rewrote it in [language]" — no reciprocal benefit unless there's a strong technical reason
- LLM-generated PRs that don't run, don't compile, or change unrelated whitespace

## Issue labels

| Label | Meaning |
|---|---|
| `good first issue` | Self-contained, well-scoped, no deep context needed |
| `help wanted` | I want this but won't get to it soon |
| `claim` | You're starting work — leave a comment, I'll assign |
| `bug` | Something broken |
| `enhancement` | Net-new feature |
| `documentation` | Docs only, no code |
| `skill-pack` | New skill (genre, workflow, tool) |
| `os:windows` / `os:linux` | OS-specific |

## Code of conduct

Be useful, don't be a jerk. The author is a working filmmaker, not a software bureaucrat — keep it human.

## Author

Vladimir Koptsev — film director, Barcelona. TG [@koptsev_AI](https://t.me/koptsev_AI) · koptsev63@gmail.com
