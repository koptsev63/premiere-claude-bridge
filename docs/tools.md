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
    "path": "/Users/.../Grave_Stakes_Teaser.prproj"
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
- `outPath` — absolute path including filename and extension (e.g. `/Users/me/Desktop/teaser.mp4`)
- `eprPath` — absolute path to a `.epr` Adobe Media Encoder preset

**Returns:** `{ok: true, jobID, outPath, eprPath}` — the job is queued; AME launches if not already running.

**Stock preset locations on macOS:**
```
/Applications/Adobe Premiere Pro 2026/Adobe Premiere Pro 2026.app/Contents/MediaIO/systempresets/
  4E49434B_48323634/YouTube 1080p HD.epr     ← H.264 1080p, good default
  4A454646_48455643/HEVC 4K.epr              ← H.265 4K
  3F3F3F3F_574D5620/HD 1080p 25.epr          ← Pro Res / DNxHR
```

---

## Escape hatch

### `pr_eval_jsx`
Run arbitrary ExtendScript code inside Premiere. Use when no specific tool fits.

**Args:** `{code: string}` — must explicitly `return` a value (string, number, or object). Objects are auto-stringified via the JSON polyfill in `host.jsx`.

**Returns:** the returned value (parsed if it's valid JSON, raw string otherwise).

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
- The wrapper around your code wraps it in `(function(){ ... })()` — without explicit `return`, you get `undefined`
- Error handling: wrap in `__safe(function(){...})` for friendly error JSON

---

## Cost model

Each tool call is one round-trip: stdio JSON-RPC → WebSocket → `evalScript()` → reverse. On a healthy bridge, **~50-150ms per typed tool** (status, get_project_info, etc.) and **~200-500ms for `pr_eval_jsx`** with a non-trivial script.

The expensive tools are the ones that iterate large structures:
- `pr_get_project_info` on a project with 1000+ clips: ~2-5s
- `pr_list_timeline` on a 5-hour edit: ~3-10s

For batch operations, prefer `pr_eval_jsx` with one big script over many small typed calls — saves the round-trip cost N times.
