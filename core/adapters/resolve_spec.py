"""Read the open DaVinci Resolve project into the spec that `core.prproj_build` turns into a `.prproj`.

Read-only: nothing in Resolve is changed. Run it through the runner, which
supplies `resolve`, `project` and `mp`::

    PRPROJ_OUT=out.prproj PRPROJ_TEMPLATE=template.prproj [PRPROJ_TIMELINES="Edit A|Edit B"] \\
      python -m core.adapters.resolve_run exec core/adapters/resolve_prproj_job.py

Rules (memory `prproj-builder-rules`): cuts, source in/out, speed and the
geometry (position, scale, rotation) are carried. Grade, Fusion and OFX are
not recreated; a clip that had one is flagged with a colour label and the
reason goes to the report. What the Resolve API cannot report (single-node
grades, OFX, transitions, audio level and fades, retime curves) is named in
the report as "not detectable", never skipped silently.

Time bases, verified on a live project: `GetStart/GetEnd` are absolute
timeline frames (subtract `GetStartFrame()`); `GetSourceStartFrame()` is
relative to the file start, whereas `GetSourceStartTime()` includes the
clip's start timecode, so the file-relative frame / clip fps is used.
"""

from __future__ import annotations

import difflib
import math
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict

SUPPORTED = {"Video", "Video + Audio", "Audio", "Still"}
STILL_FRAMES = 1


def _tc_seconds(tc: str | None, fps: float) -> float:
    if not tc:
        return 0.0
    m = re.match(r"(\d+):(\d+):(\d+)[:;](\d+)", tc)
    if not m:
        return 0.0
    h, mi, s, f = (int(x) for x in m.groups())
    return h * 3600 + mi * 60 + s + f / fps


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _walk(folder, path, out):
    for c in folder.GetClipList() or []:
        out[c.GetUniqueId()] = (c, path)
    for s in folder.GetSubFolderList() or []:
        _walk(s, f"{path}/{s.GetName()}" if path else s.GetName(), out)


class Report:
    def __init__(self):
        self.cats: dict[str, list[str]] = defaultdict(list)

    def add(self, cat: str, where: str):
        self.cats[cat].append(where)

    def lines(self) -> list[str]:
        out = []
        for cat, wh in self.cats.items():
            ex = "; ".join(wh[:3])
            out.append(f"{cat}: {len(wh)}" + (f" (e.g. {ex})" if ex else ""))
        return out


def read_audio_levels(xml_path: str, spec_tl: dict) -> dict:
    """Clip gain per audio item from the FCP7 XML Resolve exports (the API has no audio level).

    Resolve writes the gain as the `Audio Levels` filter, linear (1.122 = +1 dB). Clips next
    to a transition carry start/end -1, so items are paired by NAME ORDER per track
    (difflib), and the pairing is trusted only when the explicit XML starts agree with the
    spec starts (same snapshot of the timeline). Returns stats; sets `gain_db` on items.
    """
    seq = ET.parse(xml_path).getroot().find("sequence")
    out = {"paired": 0, "gain": 0, "clamped": 0, "keyframed": 0, "explicit": 0, "agree": 0, "applied": False}
    plan = []
    for ti, trk in enumerate(seq.findall("media/audio/track")):
        if ti >= len(spec_tl["audio"]):
            break
        xs, sp = trk.findall("clipitem"), spec_tl["audio"][ti]["items"]
        sm = difflib.SequenceMatcher(None, [c.findtext("name") for c in xs], [i["name"] for i in sp], autojunk=False)
        for blk in sm.get_matching_blocks():
            for k in range(blk.size):
                c, it = xs[blk.a + k], sp[blk.b + k]
                out["paired"] += 1
                st = int(c.findtext("start") or -1)
                if st >= 0:
                    out["explicit"] += 1
                    out["agree"] += st == it["start"]
                gain = None
                for f in c.findall("filter"):
                    e = f.find("effect")
                    if e is not None and e.findtext("effectid") == "audiolevels":
                        prm = e.find("parameter")
                        if prm.findall("keyframe"):
                            out["keyframed"] += 1
                        else:
                            lin = float(prm.findtext("value"))
                            gain = 20 * math.log10(lin) if lin > 0 else -100.0
                plan.append((it, gain))
    if out["explicit"] and out["agree"] / out["explicit"] >= 0.9:
        out["applied"] = True
        for it, gain in plan:
            if gain is not None and abs(gain) > 0.05:
                if gain > 15.0:  # Premiere's Volume Level tops out at +15 dB
                    gain = 15.0
                    out["clamped"] += 1
                it["gain_db"] = round(gain, 3)
                out["gain"] += 1
    return out


def extract(project, mp, only: list[str] | None = None, log=print, resolve=None) -> dict:
    rep = Report()
    pool: dict[str, tuple] = {}
    _walk(mp.GetRootFolder(), "", pool)
    media: dict[str, dict] = {}

    def media_for(mpi, tl_fps):
        uid = mpi.GetUniqueId()
        if uid in media or uid in media.get("_bad", {}):
            return media.get(uid)
        p = lambda k: mpi.GetClipProperty(k)
        typ = p("Type")
        if typ not in SUPPORTED:
            media.setdefault("_bad", {})[uid] = typ
            return None
        fps = _f(p("FPS"), tl_fps) or tl_fps
        res = (p("Resolution") or "").split("x")
        has_v = typ in ("Video", "Video + Audio", "Still")
        has_a = typ in ("Video + Audio", "Audio") and int(_f(p("Audio Ch"))) > 0
        frames = int(_f(p("Frames"))) if p("Frames") else 0
        dur_s = _tc_seconds(p("Duration"), fps)
        m = {"path": p("File Path"), "name": p("Clip Name"), "bin": pool.get(uid, (None, ""))[1],
             "video": None, "audio": None, "tc_ticks": round(_tc_seconds(p("Start TC"), fps) * 254_016_000_000),
             "still": typ == "Still"}
        if has_v and len(res) == 2:
            m["video"] = {"w": int(res[0]), "h": int(res[1]), "fps": fps, "frames": frames or max(1, round(dur_s * fps))}
        if has_a:
            rate = int(_f(p("Sample Rate"), 48000)) or 48000
            m["audio"] = {"channels": int(_f(p("Audio Ch"))), "rate": rate, "samples": round((dur_s + 1 / fps) * rate)}
        if not m["path"] or (not m["video"] and not m["audio"]):
            media.setdefault("_bad", {})[uid] = typ
            return None
        m["fps"] = fps
        media[uid] = m
        return m

    names = [project.GetTimelineByIndex(i).GetName() for i in range(1, project.GetTimelineCount() + 1)]
    chosen = [n for n in names if not only or n in only]
    timelines = []
    for tname in chosen:
        tl = next(project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1)
                  if project.GetTimelineByIndex(i).GetName() == tname)
        fps = _f(tl.GetSetting("timelineFrameRate"), 24.0)
        tw, th = int(tl.GetSetting("timelineResolutionWidth")), int(tl.GetSetting("timelineResolutionHeight"))
        t0 = tl.GetStartFrame()
        spec_tl = {"name": tname, "fps": fps, "w": tw, "h": th, "bin": "Sequences", "video": [], "audio": [], "hidden_video": [], "hidden_audio": []}
        cur = project.GetCurrentTimeline()
        is_cur = bool(cur and cur.GetUniqueId() == tl.GetUniqueId())
        if not is_cur:
            rep.add("track enable/mute state is readable only for the open timeline: other timelines keep all tracks on", tname)

        def track_on(kind_, ti_):
            return tl.GetIsTrackEnabled(kind_, ti_) if is_cur else True

        by_key: dict = {}
        group = [0]

        def item_spec(it, kind):
            mpi = it.GetMediaPoolItem()
            where = f"{tname}/{it.GetName()}"
            if mpi is None:
                rep.add("clip without media (generator, title, adjustment) skipped", where)
                return None
            m = media_for(mpi, fps)
            if m is None:
                rep.add("clip type not supported (compound, nested timeline, subtitle) skipped", where)
                return None
            if not it.GetClipEnabled():
                rep.add("single disabled clips skipped", where)
                return None
            start, end = it.GetStart() - t0, it.GetEnd() - t0
            cfps = m["fps"]
            s_frame = it.GetSourceStartFrame()
            span = it.GetSourceEndTime() - it.GetSourceStartTime()
            tl_span = (end - start) / fps
            speed = span / tl_span if tl_span > 0 else 1.0
            if abs(speed - 1.0) < 0.01:
                speed = 1.0
            d = {"media": mpi.GetUniqueId(), "start": start, "end": end, "src_in": 0.0 if m["still"] else s_frame / cfps,
                 "speed": round(speed, 6), "name": m["name"], "link": None}
            flags = []
            if kind == "v":
                gp = lambda k: it.GetProperty(k)
                pan, tilt, zx, zy, rot = _f(gp("Pan")), _f(gp("Tilt")), _f(gp("ZoomX"), 1.0), _f(gp("ZoomY"), 1.0), _f(gp("RotationAngle"))
                sw, sh = m["video"]["w"], m["video"]["h"]
                mode = gp("Scaling")
                fit = min(tw / sw, th / sh) if mode in (0, 2, None) else (max(tw / sw, th / sh) if mode == 1 else 1.0)
                if mode not in (0, 1, 2, None):
                    rep.add("input scaling mode other than fit/crop not converted", where)
                scale = zx * fit * 100.0
                if abs(zx - zy) > 1e-3:
                    rep.add("non-uniform zoom (ZoomX != ZoomY) carried as ZoomX", where)
                if abs(scale - 100.0) > 0.01 or abs(pan) > 0.01 or abs(tilt) > 0.01 or abs(rot) > 0.01:
                    d["tf"] = {"x": round(0.5 + pan / tw, 6), "y": round(0.5 - tilt / th, 6), "scale": round(scale, 4), "rot": round(rot, 4)}
                if _f(gp("Opacity"), 100.0) < 99.99:
                    flags.append("opacity")
                if gp("FlipX") or gp("FlipY"):
                    flags.append("flip")
                if any(_f(gp(k)) for k in ("CropLeft", "CropRight", "CropTop", "CropBottom")):
                    flags.append("crop")
                if gp("CompositeMode") not in (0, None):
                    flags.append("blend mode")
                if (it.GetNumNodes() or 1) > 1:
                    flags.append("grade")
                if it.GetFusionCompCount():
                    flags.append("fusion")
            if flags:
                d["label"] = "flag"
                for f in flags:
                    rep.add(f"{f} not transferred (clip flagged)", where)
            if kind == "v":
                d["_a"] = (mpi.GetUniqueId(), start, end, d["src_in"])
            return d

        for ti in range(1, tl.GetTrackCount("video") + 1):
            items = [d for d in (item_spec(it, "v") for it in (tl.GetItemListInTrack("video", ti) or [])) if d]
            all_items = tl.GetItemListInTrack("video", ti) or []
            if not track_on("video", ti) or (all_items and not any(i.GetClipEnabled() for i in all_items)):
                spec_tl["hidden_video"].append(ti - 1)
                if all_items:
                    rep.add("video tracks hidden", f"{tname}/V{ti}")
            spec_tl["video"].append(items)
            for d in items:
                by_key.setdefault(d.pop("_a"), []).append(d)
        for ti in range(1, tl.GetTrackCount("audio") + 1):
            sub = tl.GetTrackSubType("audio", ti)
            kind = "mono" if sub == "mono" else "stereo"
            if sub not in ("mono", "stereo"):
                rep.add(f"audio track type {sub} carried as stereo", f"{tname}/A{ti}")
            items = []
            for it in tl.GetItemListInTrack("audio", ti) or []:
                d = item_spec(it, "a")
                if not d:
                    continue
                if media[d["media"]]["audio"] is None:
                    rep.add("audio clip without audio stream skipped", f"{tname}/{it.GetName()}")
                    continue
                items.append(d)
                peers = by_key.get((d["media"], d["start"], d["end"], d["src_in"]))
                if peers and peers[0]["link"] is None:
                    group[0] += 1
                    peers[0]["link"] = group[0]
                if peers:
                    d["link"] = peers[0]["link"]
            if not track_on("audio", ti):
                spec_tl["hidden_audio"].append(ti - 1)
                rep.add("audio tracks muted", f"{tname}/A{ti}")
            spec_tl["audio"].append({"kind": kind, "items": items})
        # drop empty trailing tracks
        while len(spec_tl["video"]) > 1 and not spec_tl["video"][-1]:
            spec_tl["video"].pop()
        while len(spec_tl["audio"]) > 1 and not spec_tl["audio"][-1]["items"]:
            spec_tl["audio"].pop()
        if resolve is not None and any(tr["items"] for tr in spec_tl["audio"]):
            with tempfile.TemporaryDirectory() as d:
                xp = os.path.join(d, "t.xml")
                if tl.Export(xp, resolve.EXPORT_FCP_7_XML):
                    st = read_audio_levels(xp, spec_tl)
                    log(f"  levels: {st['gain']} clips with gain, pairing agreement {st['agree']}/{st['explicit']}")
                    if not st["applied"]:
                        rep.add("audio levels NOT read: XML and API snapshots disagree", tname)
                    if st["clamped"]:
                        rep.add("clip gain above +15 dB cut to +15 dB (Premiere limit)", f"{tname}: {st['clamped']} clips")
                    if st["keyframed"]:
                        rep.add("audio level automation (keyframes) not transferred", f"{tname}: {st['keyframed']} clips")
                else:
                    rep.add("audio levels NOT read: XML export failed", tname)
        else:
            rep.add("audio levels not read", tname)
        timelines.append(spec_tl)
        log(f"{tname}: V{len(spec_tl['video'])} A{len(spec_tl['audio'])} items "
            f"{sum(len(t) for t in spec_tl['video'])}+{sum(len(t['items']) for t in spec_tl['audio'])}")
    rep.add("not detectable through the Resolve API (check by eye): single-node grades, OFX, transitions, audio fades and crossfades, retime curves, timeline and clip markers", "")
    used = {d["media"] for t in timelines for tr in t["video"] for d in tr} | {d["media"] for t in timelines for tr in t["audio"] for d in tr["items"]}
    return {"project": project.GetName(), "media": {k: v for k, v in media.items() if k in used},
            "timelines": timelines, "report": rep.lines()}
