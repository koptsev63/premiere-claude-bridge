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

Both caption and effect tools refuse when the active sequence belongs to another open project: with two projects open Premiere can report the active project as one and `activeSequence` as the other's sequence (21.10.2026). Open the target sequence first.

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

## Sequences without a dialog (0.6)

Shared by the MCP tools and `pk.js` through `mcp-server/lib/ops.js`; the
ExtendScript lives in `mcp-server/lib/seq.js` and ships from the server, so
no panel reinstall. Field facts behind them (Premiere 26.0.2, Russian UI,
24.10.2026):

- `createNewSequence(name, presetId)` with an id that is not an installed
  preset opens "Создать эпизод" and blocks the script thread. The bridge
  never calls it.
- In/out points snap to the source frame grid (10 s at 29.97 → 9.977 s).
- Motion keyframe times (`addKey`) are seconds from the clip's start on the
  timeline. Hold interpolation is `setInterpolationTypeAtKey(t, 4, 1)`; 5 is
  Bezier and still slides between shots.
- A marker's duration is set as `marker.end = seconds` (a Time object is
  "Illegal Parameter type").
- `app.project.deleteSequence(seq)`, `projectItem.moveBin(bin)`,
  `markers.deleteMarker(m)`, `seq.setInPoint(sec)` all work; the work-area
  setters do not take effect while the work area is disabled.

### `pr_ping`
Version, project name and path, number of sequences, the active sequence's settings. No side effects.

### `pr_dialog_check`
Three signals: the process is running (`pgrep`), the panel's Chromium debug
port (8088) answers, a trivial script answers within 3 s. Port alive and
script silent = `blocked: true` (a modal window or a long operation). The
window list comes from Quartz (`CGWindowListCopyWindowInfo`), because
Premiere's windows are not visible to Accessibility; a dialog is a new titled
window compared with the baseline remembered on the last healthy check
(`~/.claude/state/premiere-windows-baseline.json`). `message` is Russian and
names the button for known dialogs ("Создать эпизод" → «Отмена», "Несоответствие
медиаданных" → «Отмена»). Looks, never clicks. Every 0.6 tool runs it on a
timeout and adds `hint`.

### `pr_probe_media`
`{paths, start?, end?}` → per file: codec, pixel format, size, fps, duration,
`ok`, `reason`, `fixCmd` (ffmpeg: libx264 CRF 16, keyframe every second, AAC
320k, `-ss/-to` for the range), `copyPath`, `copyBytesEst`, `freeBytes`,
`diskOk` (2 GB minimum), `copyDiskOk` (5 GB reserve after the copy). Refused
codecs: AV1, VP9, VP8, Theora, FFV1, H.264/HEVC 4:4:4, HEVC 12-bit.

### `pr_import_media`
`{paths, bin?, start?, end?, convert?, reuseExisting?}`. Gate first; an
unreadable file is not imported unless `convert: true`, in which case the
bridge runs the ffmpeg copy (skipped if the copy already exists) and imports
that. One `importFiles` per file with the UI suppressed. The footage frame
rate is read back after the import: ~0 means Premiere decoded no video, and
the entry carries `error` with the fix command.

### `pr_make_sequence`
`{name, file | item, in?, out?, width?, height?, fps?, bin?, exists?, convert?}`.
`file` imports (gated, reused by path) first; `item` is a nodeId, absolute
path or name. `exists`: `error` (default), `replace` (delete and rebuild),
`skip`. Returns `sequenceID`, settings read back, `video`/`audio` tracks with
clips, `check: {v1Clips, a1Clips, sourceFps, sourceVideoDecoded}`; an empty
V1 is an error that names the cause.

### `pr_set_motion`
`{seq?, track?, clip?, scale?, posX?, posY?, rotation?, anchorX?, anchorY?, units?}`.
Properties found by localized name (`Движение`/`Motion`, `Масштаб`/`Scale`,
`Положение`/`Position`, `Поворот`/`Rotation`, `Опорная точка`/`Anchor Point`).
Position in pixels of the sequence frame by default (`units: "norm"` for
0..1). Returns the values read back.

### `pr_set_motion_keyframes`
`{seq?, track?, clip?, keys: [{t, scale?, posX?, posY?, rotation?}], units?, hold?, clear?}`.
Old keyframes of the touched properties are removed first (`clear: false`
keeps them). `hold: true` = a cut between shots. Returns the keyframes read
back in pixels.

### `pr_add_markers` / `pr_list_markers`
`{seq?, markers: [{t, name?, comment?, duration?}], clear?}`, 400 per call to
Premiere; the count before and after is checked. `pr_list_markers` returns
`t, end, name, comment, type`.

### `pr_open_sequences` / `pr_activate_sequence`
`openSequence(sequenceID)` for each name; the last (or `activate`) stays
active; `missing` lists names that do not exist. `pr_activate_sequence`
confirms `app.project.activeSequence` is the one asked for.

### `pr_get_timeline` / `pr_get_inout`
For any sequence by name or id. `pr_get_timeline`: settings, tracks, clips
with `start/end/inPoint/outPoint` (seconds) and ticks, media path, sequence
in/out, work area, `clipsRange`, `renderRange` (in/out if set, else the clip
span), markers on request. `pr_get_inout` is the short form for a render
after a manual trim.

### `pr_delete_sequence` / `pr_move_to_bin` / `pr_list_bin` / `pr_save_project`
Delete by name or id (confirmed gone); move a clip or a sequence into a bin
path `A/B` (created if missing); list a bin with `videoDecoded` per clip;
`app.project.save()`.

### `pr_build_sequences`
One spec, many sequences. Per sequence: import (gated) → create → Motion →
keyframes → markers; then open the finished ones as tabs, optionally save.
Stops at the first failure unless `continueOnError`; every finished step is
reported. The spec is the same JSON `pk.js build --spec` takes:

```json
{ "bin": "ПОДКАСТ шортсы", "width": 1080, "height": 1920, "fps": 29.97, "exists": "replace",
  "sequences": [ { "name": "ПОДКАСТ 01 - ... (34 с)", "file": "/abs/01-src.mp4", "in": 10, "out": 43.653,
                   "motion": { "scale": 177.778, "x": 753, "y": 960 },
                   "motionKeys": [ { "t": 0, "posX": 753 }, { "t": 8, "posX": 664 } ], "hold": true,
                   "markers": [ { "t": 0, "name": "КРОП", "comment": "..." } ] } ] }
```

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
node mcp-server/pk.js                # usage: every typed command (0.6)
node mcp-server/pk.js status         # health + dialog check
node mcp-server/pk.js probe /abs/talk.mp4 --in 1396 --out 1450
node mcp-server/pk.js make-seq --name "ПОДКАСТ 01" --file /abs/01-src.mp4 --in 10 --out 43.6 --w 1080 --h 1920 --fps 29.97 --bin "ПОДКАСТ шортсы" --exists replace
node mcp-server/pk.js motion --seq "ПОДКАСТ 01" --scale 177.778 --x 753 --y 960
node mcp-server/pk.js motion-keys --seq "ПОДКАСТ 01" --keys '[{"t":0,"posX":753},{"t":8,"posX":664}]' --hold
node mcp-server/pk.js markers --seq "ПОДКАСТ 01" --markers @markers.json --clear
node mcp-server/pk.js inout --seq "ПОДКАСТ 01"   # clip borders after a manual trim
node mcp-server/pk.js open "ПОДКАСТ 01" "ПОДКАСТ 02" --activate "ПОДКАСТ 01"
node mcp-server/pk.js build --spec spec.json
```

The typed commands print JSON and exit 1 on a refusal; on a timeout they add
`diagnosis` (the dialog check). `~/.claude/tools/premiere-kit/pk.js` on
Vladimir's machine is a thin shim that runs this file.

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
