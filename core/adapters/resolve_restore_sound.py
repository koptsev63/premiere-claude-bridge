"""Runner job: give every picture clip of a Resolve timeline back its own sound.

Copies the timeline (the source is never touched), drops the copy's audio, and lays under each
picture clip the recorder channels of its take, shifted by the sync offset from `takes.json`
(`core.dailies_sync`), one mono track per recorder channel. The camera's own audio is added on two
mono tracks, because a stereo camera clip appended onto a mono track puts channel 1 on that track
and channel 2 on the next. Video and its sounds are linked.

    RESTORE_SRC="Edit v2" RESTORE_TAKES=takes.json [RESTORE_NEW="Edit v2 (sound restored)"] \\
    [RESTORE_CAMERA=keep|mute|omit] [RESTORE_FIRST="Boom1,Boom2"] [RESTORE_PROJECT=NAME] \\
    [RESTORE_OUT=report.json] \\
      python -m core.adapters.resolve_run exec core/adapters/resolve_restore_sound.py

``RESTORE_CAMERA``: ``keep`` (default) camera tracks present and enabled; ``mute`` present but
disabled; ``omit`` not laid at all. Camera audio plays together with the recorder: the two are
a few milliseconds apart (the offset is rounded to a frame), so with both on the sum can sound
like a flanger - choose per project.

Clips with no take (shot without the recorder) keep only their camera audio (or nothing with
``omit``). Parts of a clip the recorder file does not cover are reported, never padded.
Needs the recorder files in the media pool. Same maths as `core.avb_build`: the camera frame
``s`` shows the sound-timecode moment ``s + f0 - offset_frames``; sound-file frame is that
minus the take's ``tc_frames``.
"""
import json
import os

SRC = os.environ["RESTORE_SRC"]
NEW = os.environ.get("RESTORE_NEW") or f"{SRC} (sound restored)"
CAMERA = os.environ.get("RESTORE_CAMERA", "keep")
FIRST = [x for x in os.environ.get("RESTORE_FIRST", "Boom1,Boom2").split(",") if x]
if CAMERA not in ("keep", "mute", "omit"):
    raise SystemExit("RESTORE_CAMERA must be keep, mute or omit")
if os.environ.get("RESTORE_PROJECT") and project.GetName() != os.environ["RESTORE_PROJECT"]:
    raise SystemExit(f"open project is {project.GetName()!r}, not {os.environ['RESTORE_PROJECT']!r}: nothing touched")

tls = {project.GetTimelineByIndex(i).GetName(): project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1)}
if SRC not in tls:
    raise SystemExit(f"no timeline {SRC!r}")
if NEW in tls:
    raise SystemExit(f"{NEW!r} already exists: stopping")
takes = json.load(open(os.environ["RESTORE_TAKES"], encoding="utf-8"))
by_cam = {c["clip"]: (t, c) for t in takes for c in t["cams"]}

items = {}


def walk(folder):
    for c in folder.GetClipList() or []:
        p = (c.GetClipProperty() or {}).get("File Path")
        if p:
            items[p] = c
    for s in folder.GetSubFolderList() or []:
        walk(s)


walk(mp.GetRootFolder())

project.SetCurrentTimeline(tls[SRC])
tl = tls[SRC].DuplicateTimeline(NEW)
project.SetCurrentTimeline(tl)
st = tl.GetStartFrame()
fps = float(tl.GetSetting("timelineFrameRate"))

old = [it for i in range(1, tl.GetTrackCount("audio") + 1) for it in (tl.GetItemListInTrack("audio", i) or [])]
if old:
    tl.DeleteClips(old, False)
while tl.GetTrackCount("audio") > 0:          # the first track cannot be deleted: the empty one is removed below
    if not tl.DeleteTrack("audio", tl.GetTrackCount("audio")):
        break


def labels(take):
    seen, out = {}, []
    for s in take["sound"]:
        seen[s["label"]] = seen.get(s["label"], 0) + 1
        out.append(s["label"] if seen[s["label"]] == 1 else f"{s['label']} {seen[s['label']]}")
    return out


vids = [it for i in range(1, tl.GetTrackCount("video") + 1) for it in (tl.GetItemListInTrack("video", i) or [])]
chans = []
for v in vids:
    tc = by_cam.get(v.GetName())
    if tc:
        for lab in labels(tc[0]):
            if lab not in chans:
                chans.append(lab)
chans.sort(key=lambda c: (FIRST.index(c) if c in FIRST else len(FIRST), c))
base = tl.GetTrackCount("audio")              # 0 once the old tracks are gone; a stubborn first track is skipped over
a_track = {c: base + i + 1 for i, c in enumerate(chans)}
for _ in range(len(chans) + (2 if CAMERA != "omit" else 0)):
    tl.AddTrack("audio", "mono")
for c, i in a_track.items():
    tl.SetTrackName("audio", i, c)
CAM = base + len(chans) + 1
if CAMERA != "omit":
    sfx = " (off)" if CAMERA == "mute" else ""
    tl.SetTrackName("audio", CAM, f"Camera ch.1{sfx}")
    tl.SetTrackName("audio", CAM + 1, f"Camera ch.2{sfx}")

report, rows = [], []
for v in vids:
    mpi = v.GetMediaPoolItem()
    rec, dur, s = v.GetStart(), v.GetDuration(), v.GetLeftOffset()
    infos = []
    if CAMERA != "omit":
        infos.append({"mediaPoolItem": mpi, "startFrame": s, "endFrame": s + dur, "recordFrame": rec, "trackIndex": CAM, "mediaType": 2})
    tc = by_cam.get(v.GetName())
    row = {"clip": v.GetName(), "rec": rec - st, "dur": dur, "lofs": s}
    if tc:
        t, c = tc
        x0 = s + c["f0"] - c["offset_frames"] - t["tc_frames"]
        a0, a1 = max(0, x0), min(t["len_frames"], x0 + dur)
        if a1 > a0:
            for snd, lab in zip(t["sound"], labels(t)):
                it = items.get(snd["file"])
                if it is None:
                    report.append(f"{v.GetName()}: {os.path.basename(snd['file'])} is not in the media pool")
                    continue
                infos.append({"mediaPoolItem": it, "startFrame": a0, "endFrame": a1, "recordFrame": rec + (a0 - x0),
                              "trackIndex": a_track[lab], "mediaType": 2})
            if a0 > x0 or a1 < x0 + dur:
                report.append(f"{v.GetName()} @{(rec - st) / fps:.1f}s: the recorder does not cover the whole clip")
        else:
            report.append(f"{v.GetName()} @{(rec - st) / fps:.1f}s: take {t['name']} but the sound is outside the clip")
        row.update(take=t["name"], off=c["offset_frames"], trusted=c.get("trusted"), fixed=c.get("fixed_by_roll", False), x0=x0)
    else:
        row["take"] = None
    placed = [p for p in (mp.AppendToTimeline(infos) or []) if p] if infos else []
    if len(placed) != len(infos):
        report.append(f"{v.GetName()} @{(rec - st) / fps:.1f}s: placed {len(placed)} of {len(infos)}")
    if placed:
        tl.SetClipsLinked([v] + placed, True)
    row["placed"] = [(p.GetName(), p.GetStart() - st, p.GetLeftOffset(), p.GetDuration(), p.GetTrackTypeAndIndex()) for p in placed]
    rows.append(row)

if CAMERA == "mute":
    tl.SetTrackEnable("audio", CAM, False)
    tl.SetTrackEnable("audio", CAM + 1, False)
out = os.environ.get("RESTORE_OUT")
if out:
    json.dump({"chans": chans, "rows": rows, "report": report}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(NEW, "| clips", len(vids), "| tracks", [(tl.GetTrackName("audio", i), tl.GetTrackSubType("audio", i), tl.GetIsTrackEnabled("audio", i))
                                              for i in range(1, tl.GetTrackCount("audio") + 1)])
print("\n".join(report))
