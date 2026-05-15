# Architecture

Three processes, four hops, one timeline.

```
Claude Code  ──stdio JSON-RPC──▶  MCP server  ──WebSocket──▶  CEP panel  ──evalScript──▶  Premiere host.jsx
   (you)                         (Node, port 9876)         (HTML in Premiere)            (ES3 ExtendScript)
```

![architecture](img/architecture.png)

---

## The three components

### 1. `mcp-server/` — Node, stdio + WebSocket

Spoken to by Claude Code over stdio (JSON-RPC). Spawns a WebSocket server on `127.0.0.1:9876` that the CEP panel connects to. Translates each MCP tool call into a JSX expression, sends it over WS, awaits the response, translates back to MCP-format result.

**Key files:**
- `server.js` — single-file implementation, ~250 lines
- `package.json` — three deps: `@modelcontextprotocol/sdk`, `ws`, `zod`

**Notable bits:**
- Port-collision retry: if a previous Claude session holds 9876, `startWsServer()` re-tries every 3s until the holder dies (handles multiple Claude Code sessions running in parallel)
- Self-healing socket lookup: if `panelSocket` goes stale after CEP reload, `getActiveSocket()` adopts whatever live `wss.clients[0]` is available
- Per-call timeout: 60s default, configurable via `REQUEST_TIMEOUT_MS`

### 2. `cep-extension/` — HTML panel inside Premiere

Adobe's CEP (Common Extensibility Platform) lets you ship an HTML+JS panel that runs as part of Premiere's UI. Ours is minimal: a single panel with a status indicator + log view.

**Key files:**
- `CSXS/manifest.xml` — declares panel ID, supported Premiere versions (CEP 12, host PPRO 25-99)
- `index.html` — panel UI
- `js/main.js` — WebSocket client; on every message from the MCP server, `cs.evalScript(jsx)` → returns the result back over WS
- `js/CSInterface.js` — Adobe's stock SDK for CEP↔ExtendScript bridge
- `jsx/host.jsx` — ExtendScript functions exposed to the bridge (the actual Premiere automation)

**Notable bits:**
- The panel auto-reloads `host.jsx` from disk on every reconnect → no Premiere restart required for JSX changes (just hit "Reconnect" in the panel)
- Manual JSON polyfill prepended to `host.jsx` because Adobe's ES3 engine has no native `JSON`
- Panel exposes Chrome DevTools at `http://localhost:8088` (configured in `.debug`) — invaluable when debugging WS issues

### 3. `skills/` — markdown + Python tooling

These are the "brains" Claude uses on top of the bridge. They're not consumed by the MCP server directly — they're consumed by Claude when it's deciding *what* to do.

| Skill | What it gives Claude |
|---|---|
| `film-editing/` | Walter Murch's editing operating system + analysis tools |
| `watch/` | Real video perception (vendored from `bradautomates/claude-video`) |

The bridge is generic. Every editing decision lives in markdown (`SKILL.md`), so non-coders can extend it.

---

## Data flow for one tool call

```
1. User types in Claude Code:
     "Use pr_status to check the bridge"

2. Claude Code calls MCP tool `pr_status` (registered when MCP server started)
     → JSON-RPC over stdio: {"method":"tools/call","params":{"name":"pr_status"}}

3. mcp-server/server.js receives the call, looks up the handler:
     server.tool("pr_status", ..., async () => {
       const data = parseResult(await callPanel("getStatus", []));
       return ok({ connected: true, ...data });
     });

4. callPanel("getStatus") sends JSON over WebSocket :9876:
     {"id": "uuid", "fn": "getStatus", "args": []}

5. cep-extension/js/main.js receives the WS message, builds JSX:
     const jsx = "getStatus()";
     cs.evalScript(jsx, (raw) => { ws.send({id, ok: true, result: raw}); });

6. host.jsx executes inside Premiere:
     function getStatus() {
       return JSON.stringify({
         app: "Premiere Pro",
         version: app.version,
         project: app.project ? {name: app.project.name, ...} : null,
         ...
       });
     }

7. Result string travels back: host.jsx → CEP panel → WS → mcp-server → Claude

8. Claude renders it for the user.
```

Total round-trip on a healthy bridge: **~50-150ms** for typed tools, **~200-500ms** for `pr_eval_jsx` with a complex script.

---

## Why this stack?

**Why MCP and not a Premiere-native plugin?**
- Universal client surface — works with Claude Code, Cursor, Cline, Continue, anything MCP-compatible
- No Adobe Creative Cloud account / signing certificate required (CEP "PlayerDebugMode" trick is enough for development and personal use)
- Single skill format means non-coders can write rules

**Why ExtendScript and not UXP?**
- ExtendScript still has full access to the project model, sequence model, and AME export queue
- UXP for Premiere is incomplete as of Premiere 2026 — many critical APIs (sequence creation from preset, marker management, AME export) only exist in ExtendScript
- ExtendScript is being deprecated by Adobe long-term but works today and will keep working in Premiere 2026

**Why a separate WebSocket server instead of CEP-direct?**
- Decouples Claude from Premiere's runtime — bridge can survive Premiere restarts without losing the MCP connection
- Allows future remote access (run Claude on one machine, Premiere on another)

---

## Known design constraints

- **Single Premiere instance per machine** — port 9876 is hardcoded. If you run multiple Premiere instances, only the panel that connects first wins. Override via `PREMIERE_BRIDGE_PORT` env var if needed.
- **CEP panel must be open** — if you close the Claude Bridge panel, the bridge dies. Make it part of your default workspace.
- **Long ExtendScript calls block other calls** — host.jsx is single-threaded. A `pr_export_ame` call returns immediately because AME is launched asynchronously, but a `pr_eval_jsx` doing 500-clip iteration will hold the lock.

---

## Extending the bridge

### Add a new MCP tool

Two edits, no rebuild:

**1. JSX function in `cep-extension/jsx/host.jsx`** (must return a string, wrap in `__safe`):
```javascript
function getMyThing() {
  return __safe(function () {
    var seq = app.project.activeSequence;
    return JSON.stringify({thing: seq.name});
  });
}
```

**2. `server.tool()` block in `mcp-server/server.js`:**
```javascript
server.tool(
  "pr_get_my_thing",
  "Description of what this tool does, shown to Claude.",
  {},
  async () => {
    try { return ok(parseResult(await callPanel("getMyThing", []))); }
    catch (e) { return err(e.message); }
  }
);
```

Reload via the panel's "Reconnect" button (no Premiere restart needed if you only changed `host.jsx`).

### Add a new skill

Drop a folder under `skills/`:
```
skills/my-skill/
├── SKILL.md       # YAML frontmatter + markdown body, Claude reads this
├── README.md      # for humans
└── tools/         # optional Python helpers
```

Claude auto-discovers `SKILL.md` files at session start. The frontmatter `description` field is what triggers the skill — write a good one.
