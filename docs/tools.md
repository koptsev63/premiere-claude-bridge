# Tool reference

Every tool the MCP server exposes to Claude. Each is a thin wrapper over an ExtendScript function in [`cep-extension/jsx/host.jsx`](../cep-extension/jsx/host.jsx).

> Result format: tools that hit the panel return JSON-stringified objects, parsed by `parseResult()` in `mcp-server/server.js` before being returned to Claude. Errors propagate as MCP `isError: true` results.

---

## Health & introspection

### `pr_status`
Bridge health check. Returns whether the CEP panel is connected and basic Premiere info.

**Args:** none
**Returns:**
```json
{
  "connected": true,
  "app": "Premiere Pro",
  "version": "26.0.2",
  "build": "2",
  "project": {
    "name": "Grave_Stakes_Teaser.prproj",
    "path": "/path/to/Grave_Stakes_Teaser.prproj"
  },
  "activeSequence": {"name": "Teaser_v3_DataDriven", "id": "..."}
}
```

If the panel is disconnected: `{ "connected": false, "hint": "Open panel in Premiere: Window > Extensions > Claude Bridge" }`.

### `pr_get_project_info`
Full bin tree of the open project.

**Returns:** `{name, path, itemCount, items: [{name, type, treePath, nodeId, path}, ...]}` — `items` is flat (recursive walk into bins). `nodeId` is what you reference in subsequent calls.

### `pr_get_active_sequence`
Specs of the active sequence.

**Returns:** `{name, id, durationSeconds, durationTicks, fps, width, height, videoTrackCount, audioTrackCount}`

### `pr_list_timeline`
Every clip on every track of the active sequence.

**Returns:** `{sequenceName, video: [{kind, index, name, id, muted, clipCount, clips: [...]}], audio: [...]}` where each clip has `{name, inPointSec, outPointSec, startSec, endSec, durationSec, type, mediaType, nodeId}`.

### `pr_get_selected`
Currently selected clips on the timeline.

**Returns:** array of clip objects (same shape as `pr_list_timeline` clips).

---

## Playhead control

### `pr_get_playhead`
Current playback position (CTI).

**Returns:** `{seconds, ticks}`

### `pr_set_playhead`
Move the playhead.

**Args:** `{seconds: number}`
**Returns:** `{ok: true, seconds}`

---

## Annotation

### `pr_add_marker`
Place a marker on the active sequence at given time.

**Args:** `{seconds: number, name?: string, comment?: string}`
**Returns:** `{ok: true, seconds, name}`

Markers are typed as comments (visible as text bubbles in the timeline).

---

## Export

### `pr_export_ame`
Queue active sequence export to Adobe Media Encoder. Non-blocking.

**Args:** `{outPath: string, eprPath: string}`
- `outPath` — absolute path including filename and extension (e.g. `/path/to/teaser.mp4`)
- `eprPath` — absolute path to a `.epr` Adobe Media Encoder preset

**Returns:** `{ok: true, jobID, outPath, eprPath}` — the job is queued; AME launches if not already running.

**Use a Match Source preset for a sequence.** Ingest and proxy presets (`Settings/IngestPresets/...`, anything named Proxy) carry their own frame size: on 20.10.2026 `exportAsMediaDirect` with `IngestPresets/Proxy/02_H.264 MP4.epr` turned a 1080x1920 vertical sequence into 1280x720. `pr_export_ame` refuses such presets unless `allowProxyPreset: true`. The same applies to `exportAsMediaDirect` in your own scripts.

**Stock preset locations on macOS:**
```
/Applications/Adobe Premiere Pro 2026/Adobe Premiere Pro 2026.app/Contents/MediaIO/systempresets/
  4E49434B_48323634/00 - Match Source - High bitrate.epr   ← H.264 at the sequence's own size, good default
  4E49434B_48323634/YouTube 1080p HD.epr     ← H.264 1080p (fixed 16:9 size)
  4A454646_48455643/HEVC 4K.epr              ← H.265 4K
  3F3F3F3F_574D5620/HD 1080p 25.epr          ← Pro Res / DNxHR
```

---

## Captions

### `pr_import_captions`
Import caption files and put each on a new caption track of the active sequence.

**Args:** `{paths: string[], bin?, createTracks? = true, startSeconds? = 0, format? = "subtitle" | "608" | "708" | "teletext" | "open_ebu", reuseExisting? = true, perFileTimeoutSec? = 60}`

**Returns:** `{imported, done: [{path, nodeId, name, reused, track}]}`; on a failure, an error with what was done so far.

Why a tool: `app.project.importFiles([several .srt], true, bin, false)` (suppressUI) hung Premiere 26 on "Import files" for good (20.10.2026). One file per call with the UI allowed went through, then `seq.createCaptionTrack(item, 0, Sequence.CAPTION_FORMAT_SUBTITLE)` worked. The tool does exactly that: paths are checked on disk first, each file is its own panel call with its own timeout, and on a timeout it stops and says which file, without touching the rest. A file already in the project is reused, not imported twice.

---

## Effects

### `pr_list_video_effects`
Effect names exactly as this Premiere shows them. In a Russian UI most are Russian (`Черное и белое`), some newer ones stay English (`Gaussian Blur`, `Vignette`).

**Args:** `{filter?: string}` (matches the local name or its known English name)
**Returns:** `{count, effects: [{name, en?}]}`

### `pr_add_video_effect`
Add a video effect and prove it landed.

**Args:** `{effect: string, target? = "clip" | "track" | "selected", trackIndex? = 0, clipIndex? = 0}` (indexes as in `pr_list_timeline`; gaps do not count)

**Returns:** `{requested, resolved, how, results: [{track, clip, name, before, after, added, component}]}`. An error if any clip's component count did not grow.

Why a tool: `qe.project.getVideoEffectByName("Black & White")` in a Russian UI returned an object and `addVideoEffect` silently added nothing; `"Черное и белое"` worked (20.10.2026). The name is resolved against `qe.project.getVideoEffectList()` of the running Premiere: exact name, then case/ё-insensitive, then an English-to-local map checked against the real 26.0.2 list, then a loose match that is used only when it is unique. Otherwise the tool refuses and lists candidates. The QE item is found by start ticks, not by index (QE counts gaps as items).

---

## Escape hatch

### `pr_eval_jsx`
Run arbitrary ExtendScript code inside Premiere. Use when no specific tool fits.

**Args:** `{code: string}` - ES3 ExtendScript. Two styles, both return a value:
- end with an expression: `var s = app.project.activeSequence; s.name`
- use `return` at top level: `var s = app.project.activeSequence; return s.name;`

Code with a top-level `return` (outside any function) runs as a function body, as it always did. Any other code runs through `eval`, which gives back the last expression (before 0.5.0 that case returned an empty string). Objects are auto-stringified via the JSON polyfill in `host.jsx`.

**Returns:** the value (parsed if it's valid JSON, raw string otherwise). A statement with no value (`var x = 1;`) returns `""`.

**Example — create a sequence and place clips:**
```javascript
return (function() {
  app.enableQE();
  var preset = "/Applications/Adobe Premiere Pro 2026/Adobe Premiere Pro 2026.app/Contents/Settings/SequencePresets/Legacy/AVCHD/1080p/AVCHD 1080p25.sqpreset";
  qe.project.newSequence("MySeq", preset);
  var s = app.project.activeSequence;
  return s.name + " " + s.frameSizeHorizontal + "x" + s.frameSizeVertical;
})();
```

**Common ExtendScript recipes:**

```javascript
// Find a project item by name
var bin = app.project.rootItem.findItemsMatchingMediaPath("00118.MTS", true)[0];

// Set in/out and insert on V1 at given seconds
bin.setInPoint(2, 4);   // 4 = MEDIA_TYPE_VIDEO_AUDIO
bin.setOutPoint(8, 4);
app.project.activeSequence.videoTracks[0].insertClip(bin, 0);

// Save project
app.project.save();

// Save as
app.project.saveAs("/path/to/new.prproj");
```

**Common gotchas:**
- ExtendScript is ES3 — no `let`, no arrow functions, no `Object.assign`
- `JSON.stringify` works because we ship a polyfill in `host.jsx`
- Without a top-level `return` the value of the last expression comes back; with one, only what you `return` comes back
- Error handling: wrap in `__safe(function(){...})` for friendly error JSON

---

## Without the MCP server: `pk.js`

Since 0.5.0 the session that calls a tool takes port 9876 over, so `pr_*`
tools work from any session. If an older bridge (before 0.5.0) still holds
the port, the tools answer with an error naming its pid; restart that
session, or meanwhile use `pk.js`. [`mcp-server/pk.js`](../mcp-server/pk.js) runs
ExtendScript through the panel's Chromium debug port instead.

```bash
node mcp-server/pk.js info
node mcp-server/pk.js eval '(function(){ return JSON.stringify({n: app.project.rootItem.children.numItems}); })()'
node mcp-server/pk.js file skills/film-editing/tools/verify_timeline.jsx
node mcp-server/pk.js targets        # what the debug port exposes
```

| Variable | Default | Meaning |
|---|---|---|
| `PK_PORT` | from `cep-extension/.debug` (8088) | debug port |
| `PK_HOST` | `127.0.0.1` | debug host |
| `PK_TARGET` | `claude-bridge` | substring of the panel URL to attach to |
| `PK_TIMEOUT` | `120000` | ms to wait for ExtendScript |

Differences from `pr_eval_jsx`: your code is passed to `evalScript` as is, so
write an expression (wrap statements in `(function(){ ... })()` yourself); the
`JSON` polyfill and `__safe` from `host.jsx` are available because it is the
same ExtendScript engine. A script error comes back as the literal string
`EvalScript error.` and exit code 1 - catch inside the script if you need the
message.

---

## Cost model

Each tool call is one round-trip: stdio JSON-RPC → WebSocket → `evalScript()` → reverse. On a healthy bridge, **~50-150ms per typed tool** (status, get_project_info, etc.) and **~200-500ms for `pr_eval_jsx`** with a non-trivial script.

The expensive tools are the ones that iterate large structures:
- `pr_get_project_info` on a project with 1000+ clips: ~2-5s
- `pr_list_timeline` on a 5-hour edit: ~3-10s

For batch operations, prefer `pr_eval_jsx` with one big script over many small typed calls — saves the round-trip cost N times.
