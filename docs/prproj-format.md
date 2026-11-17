# `.prproj` structure (analysis of a real editing project, Premiere 14.0.1, Project Version 38)

The file is gzip with XML. Objects lie in a flat list at the top level (`<Type ObjectID="N" ...>` or
`ObjectUID="uuid"`), references between them are `ObjectRef` (N) and `ObjectURef` (uuid). Time is in ticks,
254,016,000,000 per second. One frame at 25 fps = 10,160,640,000 ticks, one 48 kHz sample = 5,292,000 ticks per sample tick
(`AudioStream/FrameRate`).

Rule for the builder: make the template from a file of an **old** version. An old Premiere
will not open a new project version, while a new one opens an old one and upgrades it itself.

## Project tree

    Project
    └─ RootProjectItem  (Items: BinProjectItem | ClipProjectItem by ObjectURef)
       ├─ BinProjectItem  (ProjectItemContainer → Items)  - bins, nested to any depth
       └─ ClipProjectItem → MasterClip

Order inside bins is set by `project.icon.view.grid.order`; the label color is `Column.PropertyText.Label`
(`BE.Prefs.LabelColors.N`).

## Media (master clip), 18 objects per file with video and audio

    ClipProjectItem ─ MasterClip ─┬ ClipLoggingInfo      (name, MediaIn/OutPoint = file TC, MediaFrameRate)
                                  ├ AudioComponentChain  (master clip volume)
                                  ├ Clips: VideoClip, AudioClip   (Clip.Source → *MediaSource, Markers)
                                  ├ AudioClipChannelGroups → ClipChannelGroupVectorSerializer
                                  │     → ClipChannelVectorSerializer → ClipChannelSerializer × number of channels
                                  └ Markers
    VideoMediaSource / AudioMediaSource → Media (shared)
    AudioClip.SecondaryContents → SecondaryContent × channel (ChannelIndex → AudioMediaSource)
    Media: FilePath (absolute), RelativePath, ActualMediaFilePath, Title, FileKey, Start (start TC, ticks),
           ConformedAudioRate; streams VideoStream (FrameRate, FrameRect, Duration, CodecType) and
           AudioStream (FrameRate=5292000, Duration, AudioChannelLayout, PeakFilePath)

Synthetic clips (Black Video, Adjustment Layer, Title): the same MasterClip, but Media has no file:
`FilePath` = a number, `<Infinite>true`, its own `ImplementationID`; a title has `IsStill` and `CodecType`
`1416197228`, with `ImporterPrefs` inside.

## Sequence

    Sequence ─ TrackGroups ─┬ VideoTrackGroup (FrameRect, FrameRate, Tracks→VideoClipTrack)
                            └ AudioTrackGroup (Tracks→AudioClipTrack, FrameRate=5292000)
              + AudioMixTrack / AudioTrackInlet / AudioFader / AudioMeter / pan processors for each
                audio track; PersistentGroupContainer (video↔audio Links)
    ClipTrack: ClipItems→TrackItems (in order, left to right), TransitionItems

Every sequence in the project is also a clip: its own ClipProjectItem → MasterClip → Video/AudioSequenceSource
(`Sequence ObjectURef`). There are no nested sequences inside tracks in this project.

## Clip on a track

- `VideoClipTrackItem` / `AudioClipTrackItem`: `Start`/`End` on the timeline (ticks), `SubClip`,
  `ComponentOwner → Components` (effect chain).
- `SubClip` → `Clip` (`VideoClip`/`AudioClip` with `InPoint`/`OutPoint` in the source and `PlaybackSpeed`, if not 1)
  and `MasterClip`.
- Speed: `OutPoint − InPoint = timeline length × PlaybackSpeed` (video in ticks, audio the same).
- Video and audio of one source are linked by a `Link` object (`TrackItemGroup`).
- An audio clip carries a `SecondaryContent` for each channel and an `AudioChannelLayout`.
- The clip color label is `asl.clip.label.color` + `asl.clip.label.name` in `Clip`.

## Effects found in the project

| MatchName | what |
|---|---|
| AE.ADBE Motion | Position/Scale/Rotation/Anchor; non-default in 139 clips (proxy ≠ sequence size, Scale 179, etc.) |
| Volume / Channel Volume (audio) | Level, Bypass; keyframes in 97 parameters |
| Lowpass | audio filter |
| AE.ADBE Opacity | Opacity, Blend Mode |
| AE.ADBE SubspaceStabilizer | Warp Stabilizer, ~30 parameters |
| AE.ADBE AEMask | mask with `ArbVideoComponentParam` |
| Constant Gain | audio transition (`AudioTransitionTrackItem`) |

A parameter = `VideoComponentParam`/`AudioComponentParam`/`PointComponentParam`; the value lives in
`StartKeyframe` (`time,value,...`), animation is a list of keys, `IsTimeVarying`.
There are no video transitions, Lumetri or nested sequences in the project.

## Markers

Every clip has a `Markers` object; the markers themselves are `Marker` with JSON in `DVAMarker`
(`mStartTime.ticks`, `mType`: Comment, etc.).

## What this means for a builder from Resolve

The minimum for a working sequence: a master clip for each file, a `Sequence` with tracks, for each clip a
`TrackItem` + `SubClip` + `VideoClip`/`AudioClip` + a chain (empty is allowed), a `Link` for pairs, in bins
a `ClipProjectItem` for master clips and for sequences. Then by priority: speed, volume and keys,
Motion (Scale/Position), labels, colors, audio transitions.

## Builder: `core/prproj_build.py`

Resolve -> `core/adapters/resolve_spec.py` (read only) -> spec -> `core.prproj_build.build()`.

    PRPROJ_OUT=out.prproj PRPROJ_TEMPLATE=template.prproj [PRPROJ_TIMELINES="Edit A|Edit B"] \
      python -m core.adapters.resolve_run exec core/adapters/resolve_prproj_job.py

Objects are cloned from the template project with new ids; the template supplies the project settings, its content
is discarded. The template must be saved by the oldest Premiere the result has to open in.
What is carried over: bins by media pool folders, media (video, audio, video+audio, mono/stereo, stills),
sequences with tracks, clips (position, in/out, speed), Motion (position, scale, rotation),
video-audio link, hidden video tracks and muted audio tracks. Color and effects are not created: a clip
gets a color label, and the reason goes into the report.

Checked on a live project (a timeline of 638 video and 1121 audio clips, 359 files): the reference graph
is closed, ids are unique, positions, in/out and speed of all 1759 clips matched the spec. The file
was not opened in Premiere (it is not on this Mac).

Not carried over, and goes into the report: volume automation (keys), fades and crossfades, transitions, markers (timeline and clip),
opacity, flip, crop, blend mode, compound clips, titles and fills without a file,
single disabled clips. The clip label color from Resolve is also not carried over yet.

### Clip volume

The Resolve API does not give volume (`GetProperty()` on an audio clip is empty), so the level is read from the FCP7 XML
that Resolve exports for the same timeline in the same run. There `Audio Levels` is a linear
multiplier (1.122 = +1 dB). Clips next to transitions have `start/end = -1`, so the XML and the API
are matched by the order of names on the track, and the result is accepted only if the explicit starts in the XML
matched the API in at least 90 % of cases (883 of 883).

In Premiere the level lives in the `Volume` component (`Internal Volume Stereo/Mono`), parameter `Level`:
`value = 10^((dB - 15) / 20)`. That is 0.1778 for 0 dB and 1.0 for +15 dB, the top of the scale;
confirmed by two projects (default Channel Volume in one, default mono Volume in the other).
Above +15 dB the value is clipped, and a line goes into the report. There are no volume keyframes yet: the format
is not in any sample. On the same timeline 987 levels were recorded, all matched the XML.
