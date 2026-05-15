# Install — detailed walkthrough

The 4-step Quickstart in the [README](../README.md#quickstart) covers the happy path. This file is the long version: per-OS notes, troubleshooting, and what to do when something doesn't work.

---

## Supported environments

| Component | Tested | Likely-works | Won't work |
|---|---|---|---|
| **OS** | macOS 14+ (Sonoma, Sequoia) | Windows 11 + WSL for Python parts | Linux (Premiere is macOS/Windows only) |
| **Premiere Pro** | 2026 (v25), 2025 (v24) | 2024 (v23) | ≤ 2023 — CEP 12 host required |
| **Node.js** | 20.x, 22.x | 18.x | < 18 |
| **Python** | 3.11, 3.12, 3.14 | 3.10 | < 3.10 (tomllib & syntax) |
| **Claude client** | Claude Code 2.1+ | any MCP-compatible client | clients without `stdio` transport |

If you're on Linux: the analysis tools (`analyze_clips.py`, `horizon_detect.py`, `/watch`) work fine standalone — but the bridge itself talks to Premiere, and Premiere doesn't run on Linux.

---

## macOS (full setup)

```bash
# 1. System binaries
brew install node ffmpeg yt-dlp python@3.12

# 2. Clone
cd ~/Dev    # or wherever
git clone https://github.com/koptsev63/premiere-claude-bridge.git
cd premiere-claude-bridge

# 3. MCP server
cd mcp-server
npm install
cd ..

# 4. Python venv for analysis tools
cd skills/film-editing/tools
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd ../../..

# 5. Register with Claude Code — edit ~/.claude.json
# Add the "premiere" MCP server entry (see README Quickstart step 2)

# 6. Allow unsigned CEP extensions + symlink the panel
defaults write com.adobe.CSXS.11 PlayerDebugMode 1
defaults write com.adobe.CSXS.12 PlayerDebugMode 1
ln -sf "$(pwd)/cep-extension" \
  ~/Library/Application\ Support/Adobe/CEP/extensions/com.koptsev.claude-bridge

# 7. Restart Premiere → Window → Extensions → Claude Bridge
```

### macOS troubleshooting

**Panel doesn't appear under Window → Extensions:**
- Confirm `defaults read com.adobe.CSXS.12 PlayerDebugMode` returns `1`. If not, the `defaults write` didn't take — check you wrote both `CSXS.11` and `CSXS.12`.
- Restart Premiere fully (Quit, not just close window).
- If the symlink is broken: `ls -l ~/Library/Application\ Support/Adobe/CEP/extensions/com.koptsev.claude-bridge` should show `→ /your/path/cep-extension`.

**Panel says "Disconnected — retrying":**
- The MCP server isn't running. Check Claude Code's MCP status (`/mcp`) — there should be a "premiere" entry as ready.
- Port 9876 collision: the bridge auto-retries every 3s. If you have multiple Claude sessions open, only one wins.

**`pr_status` returns `connected: false` even when panel shows green:**
- Known race condition documented in v0.1 CHANGELOG. Click "Reconnect" in the panel header.

---

## Windows (PowerShell as Admin)

```powershell
# 1. System binaries (via winget or chocolatey)
winget install --id OpenJS.NodeJS.LTS
winget install --id Gyan.FFmpeg
winget install --id yt-dlp.yt-dlp
winget install --id Python.Python.3.12

# 2. Clone
cd $env:USERPROFILE\Dev
git clone https://github.com/koptsev63/premiere-claude-bridge.git
cd premiere-claude-bridge

# 3. MCP server
cd mcp-server; npm install; cd ..

# 4. Python venv
cd skills\film-editing\tools
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
cd ..\..\..

# 5. Register with Claude Code — edit %USERPROFILE%\.claude.json

# 6. CEP debug + symlink (admin shell required)
New-ItemProperty -Path "HKCU:\Software\Adobe\CSXS.11" -Name PlayerDebugMode -Value 1 -PropertyType String -Force
New-ItemProperty -Path "HKCU:\Software\Adobe\CSXS.12" -Name PlayerDebugMode -Value 1 -PropertyType String -Force
mklink /D "$env:APPDATA\Adobe\CEP\extensions\com.koptsev.claude-bridge" "$(Get-Location)\cep-extension"

# 7. Restart Premiere
```

### Windows troubleshooting

**`mklink` fails with "You do not have sufficient privilege":**
- PowerShell must be run as Administrator. Right-click → Run as Administrator.
- Or use `cmd /c mklink ...` instead of `mklink` directly in PowerShell.

**Python venv can't find ffmpeg/yt-dlp:**
- After `winget install`, you may need to restart your terminal (or reboot) for PATH to update.
- Verify: `where ffmpeg` and `where yt-dlp` should both return something.

---

## Verifying the install

After all 6 steps:

```bash
# 1. MCP server preflight
node mcp-server/server.js --check    # should exit 0

# 2. Analysis tools preflight
PATH="$(pwd)/skills/film-editing/tools/.venv/bin:$PATH" \
  python3 skills/watch/scripts/setup.py --check
# → exit 0 if local whisper installed (no API key needed)
# → exit 3 if missing API key but you don't care (use --whisper local)

# 3. Quick smoke test from Claude Code
# In a Claude Code session:
#   /mcp   ← should show 'premiere' as connected
# Then ask Claude: "Use pr_status to check the bridge."
```

---

## Optional: Whisper backends

`/watch` needs one of these to transcribe audio:

### `local` (recommended, no key, free, offline)
```bash
pip install -U openai-whisper
# Default model: medium (~1.5GB, downloaded on first run)
# Override: WATCH_LOCAL_WHISPER_MODEL=small  (244MB, faster, decent quality)
#           WATCH_LOCAL_WHISPER_MODEL=large-v3  (3GB, best, slower)
```

### `groq` (cloud, fastest, ~$0.0002/min)
```bash
# Get a key from https://console.groq.com/keys
mkdir -p ~/.config/watch
echo "GROQ_API_KEY=gsk_..." > ~/.config/watch/.env
chmod 600 ~/.config/watch/.env
```

### `openai` (cloud, slowest, ~$0.006/min, English-only quality)
```bash
echo "OPENAI_API_KEY=sk-..." >> ~/.config/watch/.env
```

`/watch` picks Groq → OpenAI → local in that order by default. Force one with `--whisper {groq,openai,local}`.

---

## Uninstall

```bash
# Remove CEP panel symlink
rm ~/Library/Application\ Support/Adobe/CEP/extensions/com.koptsev.claude-bridge   # macOS
# or rmdir on Windows

# Disable CEP debug mode
defaults delete com.adobe.CSXS.11 PlayerDebugMode    # macOS
defaults delete com.adobe.CSXS.12 PlayerDebugMode

# Remove MCP server entry from ~/.claude.json
# (delete the "premiere" key from "mcpServers")

# Delete the cloned repo
rm -rf ~/Dev/premiere-claude-bridge
```

---

## Still stuck?

Open an issue with:
- Your OS + Premiere version
- The output of `node mcp-server/server.js --check` (or whatever errored)
- Whether the panel shows green or red

I read every issue. — Vladimir
