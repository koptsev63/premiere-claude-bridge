---
name: nle-handoff
description: Hand off an edit from DaVinci Resolve to Premiere Pro and Avid Media Composer - build a Premiere project (.prproj) from the open Resolve project, build an Avid bin (.avb) with masters, sync and group clips plus a separate project settings file (.avp), and check AAF and XML files, filling in tape names. Use for editor handoff exports, an Avid bin, a Premiere project, an AAF that will not open, or checking an XML. Triggers - nle handoff, prproj, avb, AAF, XML check, hand off to editor.
---

# Edit handoff: Premiere, Avid, AAF/XML

None of the output files was opened in its target application when this was written: only structure, references and a comparison against the source were checked. Tell the recipient this.

## Premiere project

```bash
PRPROJ_OUT=out.prproj PRPROJ_TEMPLATE=your_project.prproj [PRPROJ_TIMELINES="Edit A|Edit B"] \
  python -m core.adapters.resolve_run exec core/adapters/resolve_prproj_job.py
```

- Resolve is opened read-only. The template is any real `.prproj` saved by the OLDEST Premiere in which the result must open (an old Premiere will not open a new project, a new one will open an old project). Project settings are taken from the template; its content is discarded.
- Carried over: bins following the media pool folders, media (video, audio, video+audio, mono/stereo, freeze frames), sequences with tracks, clips (position, in/out, speed), Motion (position, scale, rotation), the link between video and audio, hidden video tracks and muted audio tracks, clip volume (read from the Resolve XML, written as Volume Level `10^((dB-15)/20)`, clipped above +15 dB).
- NOT carried over, and listed in the report: Resolve grade and effects (the clip gets a color label), volume keyframes, fades, crossfades, transitions, markers, opacity, flip, crop, blend modes, compound clips, titles without a file, individual disabled clips. The clip label color from Resolve is not carried over.
- Resolve reports track status (hidden/muted) only for the open timeline. For the others all tracks stay enabled, with a warning in the report.
- Format description: `docs/prproj-format.md`.

## Avid bin and project settings

```bash
python -m core.avb_build takes.json day.avb --hdr headers.json \
  --project "Project name" --avp-template their_project.avp --settings-template their_project_Settings.xml --preset hd-1080p24
```

- Needed: `pip install pyavb`, PyAV, `takes.json` from `dailies-prep`, and the disk with the MXF files (headers are read from it and cached in `--hdr`). Takes whose headers are missing are skipped and listed in the report.
- In the bin: a sound master per take with columns from the MXF header (Scene, Take, TapeID, TRKn...), camera masters, `...a.sync.01` (camera picture and all sound tracks with an offset; if the camera started before the sound, the extra picture head is trimmed), `...Grp.01` (selectors per camera and a hidden composition).
- The bin is the project itself, `.avp` holds its settings, and they are separate files. `.avp` is written by in-place edits with no byte shifts: a new GUID, a timestamp and the integer format parameters. The list of bins inside `.avp` stays as in the template (Avid will show them as missing). One format preset is known, `hd-1080p24`; other frame rates and sizes need a sample of such a project.
- Not done: bin view (columns, sorting, fonts), `_ORG_BIN`.
- An `.avp` file is inspected with `core.avp_build.read_attrs`.

## Checking AAF and XML

```bash
python -m core.handoff_check aaf file.aaf
python -m core.handoff_check name-tapes file.aaf fixed.aaf --hdr headers.json
python -m core.handoff_check xml file.xml
```

- AAF: object count, frame rates, descriptors, tapes without a name, dangling references, media on disk. Resolve writes all tapes with an empty name; `name-tapes` fills in the reel name from the MXF header (or the file name). That empty tape names are exactly what keeps the AAF from opening in a particular Avid is NOT confirmed: this is a check and a best-effort fix, not a guarantee. If it still does not open afterwards, you need the Avid error text and the Avid project frame rate (the AAF here is entirely at 24 fps).
- XML: clips, disabled clips (Resolve skips them on import), clips with position `-1` at transitions, file paths, duplicate ids.
