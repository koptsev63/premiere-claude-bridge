---
name: dailies-prep
description: Preparing a shooting day's material for editing in DaVinci Resolve through the bridge - syncing the sound recorder's audio to the camera, returning each clip its sound, stepwise frame checking. Use when asked to sync sound, assemble takes by slate and timecode, restore sound in the timeline, check the picture of an edit, or when takes.json, MXF recorder, or dailies come up.
---

# Day prep: sync, sound, frame checking

Three tools in one chain. The sync result (`takes.json`) is read by the others.

Requirements: Python 3.13 with `numpy`, `av` (PyAV), `Pillow`; for Resolve work - Resolve Studio with external scripting set to Local and the `core.adapters.resolve_run` runner.

## 1. Sound sync `core/dailies_sync.py`

```bash
python -m core.dailies_sync scan /path/to/sound /path/to/cameras --out meta.json
python -m core.dailies_sync sync meta.json --out takes.json [--scenes 1-18,1-19]
```

- Recorder: MXF OP-Atom, one file per channel; scene, take and channel names (`TRK1`...) come from the header comments. Camera: the clip plus `<name>_A01.mxf` next to it (or sound inside the clip).
- Takes are assembled by timecode, cameras are matched by overlap (at least 2 s). Then the offset is refined by correlating the camera's scratch track with the boom (PHAT-weighted, ±2 s window).
- Offset in frames: `offset_frames` above zero means the camera picture lags behind its own timecode. Camera frame `s` shows the moment `s + f0 - offset_frames` on the sound timecode.
- `sharp` - sharpness of the peak. Below 8: `trusted: false`. A weak link (below 10) with a jump of more than 2 frames is replaced by the median of the same camera roll and gets `fixed_by_roll: true`.
- Cameras that fell into no take are footage shot without the recorder: `unmatched_cameras()`.

How to read the result: first how many links are `trusted`, then all `fixed_by_roll` and `trusted: false` ones - check those by eye and by ear, do not trust them. The remainder after rounding to a frame (milliseconds) is computed by `residual_ms()`.

## 2. Restoring sound in the timeline `core/adapters/resolve_restore_sound.py`

```bash
RESTORE_SRC="Edit" RESTORE_TAKES=takes.json RESTORE_CAMERA=keep \
  python -m core.adapters.resolve_run exec core/adapters/resolve_restore_sound.py
```

- Copies the timeline (leaves the original alone), strips the copy's sound and under each clip lays the recorder channels of its take, shifted: one mono track per channel (Boom1, Boom2 above the rest), the clip and the sounds linked.
- Camera sound: `RESTORE_CAMERA=keep` (default, tracks enabled), `mute` (present, disabled), `omit` (not laid). The camera and the recorder drift apart by milliseconds, so together they can sound like a flanger: decide per project.
- Clips without a take keep only the camera sound. If the recorder ended before the clip, the report has a line about it and the sound is not extended.
- `RESTORE_PROJECT=NAME` - a guard against running in the wrong project.
- The recorder files must be in the media pool.

## 3. Stepwise frame checking `core/framecheck.py`

Go from cheap to expensive. Detailed frames around cuts are only for seamless editing: for choppy editing (montage cutting, jump cuts, cutaways) they tell you nothing.

| step | what | how much |
|---|---|---|
| 0 | numbers without viewing: where the picture changes (`motion_profile`) | 0.5 s step |
| 1 | overview of each camera of each take (`overview_sheets`) | 8 frames per take |
| 2 | edges of the shot (`edge_sheet`) | in, in+1 s, out-0.5 s |
| 3 | around the cut (`cut_sheets`, seamless only) | one second before and one second after, 4 frames each, step of a third of a second |
| 4 | inside the shot - only where step 0 found movement | one frame every half second |
| 5 | continuity log: in each shot who is where at the start and at the end; compare the cuts and the story | per shot |
| 6 | one full-size frame for a disputed spot | as needed |

```bash
python -m core.framecheck overview takes.json out/
python -m core.framecheck cuts takes.json edl.json out/ --seamless   # no sheets without --seamless
```

- First ask, or read from the brief, whether the edit is seamless. The `--seamless` switch is off by default.
- Even in a seamless edit, dense sheets are built only at cuts that must match: two cameras of the same take or continuing action. Inserts from another take are skipped, because the break there is intended.
- EDL rows: `[sound_take, from_s, to_s, picture_take, camera, picture_from_s or null, note]`.
- When reporting on a check, name which steps were run. Real misses came not from sparse frames but from a take not being looked at at all, or from the positions of people not being compared between shots.
