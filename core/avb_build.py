"""Build an Avid bin (.avb) of a shoot day: masters with their columns, sync clips, group clips.

Shaped after a real editors' bin (Media Composer 25.12):

    sound master   "1-36-5/001"           one master per take, a track per channel, user
                                          columns Scene/Take/TapeID/Shoot Date/TRKn/...
    camera master  "B_0070C005_..._h1CMP" picture + the camera's own audio
    sync clip      "01-36.37.38.39-05/01a.sync.01"
                                          camera picture + all sound-master tracks, the
                                          sound shifted by the sync offset (usage 2)
    group clip     "01-36.37.38.39-05/01.Grp.01"
                                          selectors over the cameras (usage 4) + the hidden
                                          sub-composition they point at (usage 5)

Everything links by the package UMIDs read from the OP-Atom MXF headers, so Avid
relinks to the same media. Needs `pyavb` (and PyAV only to read headers from disk).

Inputs, all plain data:

* `hdr`   - path -> header, as returned by `read_header` (UMIDs, reel, timecode, lengths,
            the sound take's `meta` = Avid user columns, which Avid itself writes into MXF).
* `takes` - list of take dicts: ``name, scene, take, tc_frames, len_frames, sound=[{file}],
            cams=[{file, cam, f0, f1, offset_frames, trusted}]`` (the sync pass output).
* `cid`   - path -> DNxHD compression id (`read_cid`), default 1253.

Not covered: bin view/columns layout, `_ORG_BIN` bin references, clip colours of cameras.
A bin written here has been read back and compared to the editors' bin shape-by-shape
(`fingerprint`), but never opened in Avid.
"""

from __future__ import annotations

import glob
import os
import re
import time
from collections import defaultdict

FPS = 24
LONG = FPS * 60 * 60 * 24                     # a day of tape
DNX_FRAME = {1253: 188416}
SOUND_COLOR = (16896, 32768, 13824)           # as the editors' sound masters
MIN_SYNC = FPS // 2                           # shorter overlaps are not worth a clip
# the "nothing here" UMID Avid writes on a tape's own track (the example bin's, not all zeros)
NULL_UMID = "urn:smpte:umid:060a2b34.01010101.01010f00.13000000.060e2b34.7f7f2a80.00000000.00000000"


# ------------------------------------------------------------------ headers

def read_header(path: str) -> dict:
    """UMIDs, reel, timecode, lengths and user columns of one MXF (needs PyAV)."""
    import av
    c = av.open(path)
    md = dict(c.metadata)
    tracks = []
    for s in c.streams:
        m = dict(s.metadata)
        e = {"kind": s.type if s.type != "data" else m.get("data_type"), "own": s.type != "data",
             "file_umid": m.get("file_package_umid"), "track_name": m.get("track_name")}
        if e["own"]:
            e.update(reel_umid=m.get("reel_umid"), reel_name=m.get("reel_name"), tc=m.get("timecode"),
                     name=m.get("file_package_name"))
            if s.type == "video":
                e.update(frames=s.frames or int(s.duration * s.time_base * FPS + 0.5), w=s.codec_context.width,
                         h=s.codec_context.height, codec=s.codec_context.name, rate=str(s.average_rate or s.base_rate))
            else:
                e.update(sr=s.codec_context.sample_rate,
                         bits={"pcm_s24le": 24, "pcm_s32le": 32, "pcm_s16le": 16}.get(s.codec_context.name),
                         samples=int(s.duration * s.time_base * s.codec_context.sample_rate + 0.5) if s.duration else None)
        tracks.append(e)
    c.close()
    return {"material_umid": md.get("material_package_umid"), "material_name": md.get("material_package_name"),
            "meta": {k: v for k, v in md.items() if k.startswith("comment_")}, "tracks": tracks}


def collect_headers(files, hdr: dict | None = None) -> dict:
    """Headers for `files` plus the companions Avid needs: a camera's _A0n audio files and
    the other channels of a sound take (same recorder file id)."""
    out = hdr if hdr is not None else {}
    for p in sorted(set(files)):
        if p in out:
            continue
        side = sorted(glob.glob(p[:-4] + "_A0*.mxf"))
        if side:                                   # a camera clip with its _A01/_A02 audio files
            group = [p] + side
        else:
            out[p] = read_header(p)
            group = sorted(q for q in glob.glob(os.path.dirname(p) + "/*.mxf")
                           if q.split(".")[1] == os.path.basename(p).split(".")[1])
        for q in group:
            if q not in out:
                out[q] = read_header(q)
    return out


def read_cid(path: str) -> int:
    """DNxHD compression id from the first packet (CID 1253 = DNxHD 36 proxy)."""
    import av
    c = av.open(path)
    pk = next(c.demux(video=0))
    cid = int.from_bytes(bytes(pk)[0x28:0x2C], "big")
    c.close()
    return cid


def tcf(tc: str) -> int:
    h, m, s, f = map(int, tc.replace(";", ":").split(":"))
    return ((h * 60 + m) * 60 + s) * FPS + f


# ------------------------------------------------------------------- naming

def slate_parts(scene: str):
    """'1-18-19-4' -> ('1', ['18', '19'], '4'); '1-20-1' -> ('1', ['20'], '1')."""
    p = scene.lstrip("+").split("-")
    return p[0], p[1:-1], p[-1]


def bin_name_for(takes) -> str:
    eps, scenes = set(), []
    for t in takes:
        ep, sc, _ = slate_parts(t["scene"])
        eps.add(ep)
        scenes += [s for s in sc if s not in scenes]
    ep = sorted(eps)[0].zfill(2)
    return f"{ep}-{'.'.join(sorted(scenes, key=int))}"


def sync_names(takes, bin_name):
    """take name -> 'bin-SS/TT' stem; scene key is added when two takes would collide."""
    stems, seen = {}, defaultdict(list)
    for t in takes:
        _, _, shot = slate_parts(t["scene"])
        stem = f"{bin_name}-{int(shot):02d}/{int(t['take']):02d}"
        seen[stem].append(t["name"])
        stems[t["name"]] = stem
    for stem, names in seen.items():
        if len(names) > 1:
            for n in names:
                t = next(x for x in takes if x["name"] == n)
                stems[n] = f"{bin_name}-{t['scene']}/{int(t['take']):02d}"
    return stems


# --------------------------------------------------------------------- bin

def build_bin(takes, hdr: dict, out: str, cid: dict | None = None, bin_name: str | None = None,
              now: float | None = None) -> dict:
    import avb
    from avb.mobid import MobID

    cid = cid or {}
    now = int(now if now is not None else time.time())
    f = avb.open()
    C = f.create
    report: list[str] = []

    def fill(o, **kw):
        for k, v in kw.items():
            setattr(o, k, v)
        for p in type(o).propertydefs:
            if hasattr(o, p.name):
                continue
            if p.type.startswith(("int", "uint")):
                setattr(o, p.name, 0)
            elif p.type == "bool":
                setattr(o, p.name, False)
        return o

    def comp(cls, kind, rate, **kw):
        o = getattr(C, cls)()
        o.media_kind = kind
        o.edit_rate = rate
        for k, v in kw.items():
            setattr(o, k, v)
        return o

    def track(index, component):
        t = C.Track()
        t.index = index
        t.component = component
        return t

    def mob(kind, name, umid):
        m = C.Composition(mob_type=kind)
        m.name = name
        m.mob_id = MobID(umid) if umid else MobID.new()
        return m

    def sclip(kind, length, mob_id, track_id, start):
        o = comp("SourceClip", kind, FPS, length=length, track_id=track_id, start_time=start)
        o.mob_id = mob_id if mob_id is not None else MobID(NULL_UMID)
        return o

    def seq(kind, parts):
        s = comp("Sequence", kind, FPS)
        s.components = [comp("Filler", kind, FPS, length=0)] + parts + [comp("Filler", kind, FPS, length=0)]
        return s

    def attrs(m):
        if m.attributes is None:
            m.attributes = C.Attributes()
        return m.attributes

    def user_attrs(m, **cols):
        a = C.Attributes()
        for k, v in cols.items():
            a[k] = v
        attrs(m)["_USER"] = a

    # ---- tape / file / master mobs from the headers
    tapes: dict[str, object] = {}
    masters: dict[str, dict] = {}              # material umid -> info

    def material(path: str) -> dict:
        h = hdr[path]
        mat = h["material_umid"]
        if mat in masters:
            return masters[mat]
        own = next(t for t in h["tracks"] if t["own"])
        tc0 = tcf(own["tc"])
        vids = [t for t in h["tracks"] if t["kind"] == "video"]
        auds = [t for t in h["tracks"] if t["kind"] == "audio"]
        tape = tapes.get(own["reel_umid"])
        if tape is None:
            tape = mob("SourceMob", own["reel_name"], own["reel_umid"])
            tape.descriptor = fill(C.TapeDescriptor())
            tape.tracks = [track(1, comp("Timecode", "timecode", FPS, length=LONG, fps=FPS, start=0))]
            for i, _ in enumerate(vids, 1):
                tape.tracks.append(track(i, sclip("picture", LONG, None, 0, 0)))
            for i, _ in enumerate(auds, 1):
                tape.tracks.append(track(i, sclip("sound", LONG, None, 0, 0)))
            f.content.add_mob(tape).user_placed = False
            tapes[own["reel_umid"]] = tape
        frames = None
        for hh in hdr.values():
            if hh["material_umid"] == mat:
                for t in hh["tracks"]:
                    if t["own"]:
                        frames = frames or (t.get("frames") or round(t["samples"] / 48000 * FPS))
        sr = next((t["sr"] for hh in hdr.values() if hh["material_umid"] == mat for t in hh["tracks"] if t["own"] and t["kind"] == "audio"), 48000)
        bits = next((t["bits"] for hh in hdr.values() if hh["material_umid"] == mat for t in hh["tracks"] if t["own"] and t["kind"] == "audio"), 24)
        samples = next((t["samples"] for hh in hdr.values() if hh["material_umid"] == mat for t in hh["tracks"] if t["own"] and t["kind"] == "audio"), None)
        master = mob("MasterMob", h["material_name"], mat)
        master.tracks = []
        chan = {}
        for i, t in enumerate(vids, 1):
            fm = mob("SourceMob", h["material_name"], t["file_umid"])
            k = cid.get(path) or next((v for p, v in cid.items() if hdr[p]["material_umid"] == mat), 1253)
            fm.descriptor = fill(C.CDCIDescriptor(), edit_rate=FPS, length=frames, resolution_id=k,
                                 frame_sample_size=DNX_FRAME.get(k, 0), horizontal_subsampling=2, vertical_subsampling=1,
                                 component_width=8, color_sitting=0, black_ref_level=16, white_ref_level=235, color_range=225,
                                 frame_index_offset=0, image_alignment_factor=1)
            fm.tracks = [track(i, sclip("picture", frames, MobID(own["reel_umid"]), i, tc0))]
            f.content.add_mob(fm).user_placed = False
            master.tracks.append(track(i, seq("picture", [sclip("picture", frames, MobID(t["file_umid"]), i, 0)])))
        for i, t in enumerate(auds, 1):
            fm = mob("SourceMob", h["material_name"], t["file_umid"])
            ds = fill(C.PCMADescriptor(), edit_rate=sr, length=frames * sr // FPS, channels=1, quantization_bits=bits,
                      sample_rate=sr, locked=True, block_align=bits // 8, average_bps=sr * bits // 8)
            for k in ("ebu_timestamp", "timecode_framerate"):
                if k in ds.property_data:
                    del ds.property_data[k]
            fm.descriptor = ds
            fm.tracks = [track(i, sclip("sound", frames, MobID(own["reel_umid"]), i, tc0))]
            f.content.add_mob(fm).user_placed = False
            master.tracks.append(track(i, seq("sound", [sclip("sound", frames, MobID(t["file_umid"]), i, 0)])))
            chan[t["track_name"]] = i
        master.length = frames
        meta = {k[len("comment_"):]: v for k, v in h.get("meta", {}).items()}
        is_sound_take = "Scene" in meta
        if is_sound_take:
            master.usage_code = 0
            user_attrs(master, **meta)
            attrs(master)["_GEN"] = now
            attrs(master)["_VERSION"] = 2
            attrs(master)["_SAVED_AAF_AUDIO_RATE_NUM"] = sr
            attrs(master)["_SAVED_AAF_AUDIO_RATE_DEN"] = 1
            attrs(master)["_SAVED_AAF_AUDIO_LENGTH"] = int(samples or frames * sr // FPS)
            for key, val in zip(("_COLOR_R", "_COLOR_G", "_COLOR_B"), SOUND_COLOR):
                attrs(master)[key] = val
            attrs(master)["_SPANNEDUSINGFILLER"] = 1
        else:
            master.usage_code = 7
            attrs(master)["_GEN"] = 0
            user_attrs(master, **({"Comment": meta["Comment"]} if "Comment" in meta else {}))
        f.content.add_mob(master)
        masters[mat] = {"mob": master, "chan": chan, "frames": frames, "tc0": tc0, "n_sound": len(auds), "tracks": len(master.tracks)}
        return masters[mat]

    takes = list(takes)
    # ---- which takes can be built from the headers we have
    usable = []
    for t in takes:
        files = [s["file"] for s in t["sound"]]
        if not files or any(p not in hdr for p in files):
            report.append(f"{t['name']}: skipped, no MXF header for its sound files (is the drive mounted?)")
            continue
        cams = []
        for c in t["cams"]:
            if c["file"] not in hdr:
                report.append(f"{t['name']}: camera {c['clip']} skipped, no MXF header")
            else:
                cams.append(c)
        usable.append((t, cams))
    if not usable:
        raise ValueError("no take could be built: " + "; ".join(report[:3]))
    name = bin_name or bin_name_for([t for t, _ in usable])
    stems = sync_names([t for t, _ in usable], name)

    n_groups = n_sync = 0
    pos = 0
    for t, cams in usable:
        sm = material(t["sound"][0]["file"])
        N = sm["n_sound"]
        L_s = sm["frames"]
        tc_s = t["tc_frames"]
        syncs = []                              # (cam dict, sync mob, x0, L)
        for ci, c in enumerate(sorted(cams, key=lambda c: c["cam"])):
            cm = material(c["file"])
            cam_len = cm["frames"]
            s0 = max(0, tc_s - (c["f0"] - c["offset_frames"]))
            x0 = s0 + c["f0"] - c["offset_frames"] - tc_s
            L = min(cam_len - s0, L_s - x0)
            if L < MIN_SYNC:
                report.append(f"{t['name']}: camera {c['clip']} overlaps the sound by {L} frames, no sync clip")
                continue
            letter = c["cam"].lower()
            s = mob("CompositionMob", None, None)
            s.name = f"{stems[t['name']]}{letter}.sync.01"
            s.usage_code = 2
            s.length = L
            s.tracks = [track(1, sclip("picture", L, cm["mob"].mob_id, 1, s0))]
            for i in range(1, N + 1):
                s.tracks.append(track(i, sclip("sound", L, sm["mob"].mob_id, i, x0)))
            drive = (re.match(r"/Volumes/([^/]+)/", c["file"]) or [None, ""])[1]
            cols = {"Color": "", "Drive": drive, "Plug-in": "", "Tapeid": "", "Audio sr": "48000", "Comments": "",
                    "Comment": "Created by Resolve"}
            if not c.get("trusted", True):
                cols["Comments"] = "sync: waveform check inconclusive"
                report.append(f"{s.name}: offset {c['offset_frames']:+d} f not confirmed by the waveform (sharp {c.get('sharp')})")
            user_attrs(s, **cols)
            attrs(s)["_START"] = 0
            attrs(s)["_END"] = L
            attrs(s)["_USER_POS"] = -2
            attrs(s)["_COLOR_NONE"] = 1
            f.content.add_mob(s)
            syncs.append((c, s, x0, L))
            n_sync += 1
        if not syncs:
            continue
        # hidden group sub-composition (usage 5): sound master, then the cameras
        hid = mob("CompositionMob", None, None)
        hid.usage_code = 5
        hid.length = L_s
        grp = mob("CompositionMob", None, None)
        grp.name = f"{stems[t['name']]}.Grp.01"
        grp.usage_code = 4
        grp.length = L_s
        htracks = []                            # (kind, component)
        htracks.append(("picture", seq("picture", [comp("Filler", "picture", FPS, length=syncs[0][2])] +
                                       [sclip("picture", syncs[0][3], syncs[0][1].mob_id, 1, 0)] +
                                       [comp("Filler", "picture", FPS, length=L_s - syncs[0][2] - syncs[0][3])])))
        for i in range(1, N + 1):
            htracks.append(("sound", seq("sound", [sclip("sound", L_s, sm["mob"].mob_id, i, 0)])))
        for k, (c, s, x0, L) in enumerate(syncs[1:], 2):
            htracks.append(("picture", seq("picture", [comp("Filler", "picture", FPS, length=x0),
                                                       sclip("picture", L, s.mob_id, 1, 0),
                                                       comp("Filler", "picture", FPS, length=L_s - x0 - L)])))
        for c, s, x0, L in syncs:
            for i in range(1, N + 1):
                htracks.append(("sound", seq("sound", [comp("Filler", "sound", FPS, length=x0),
                                                       sclip("sound", L, s.mob_id, i, 0),
                                                       comp("Filler", "sound", FPS, length=L_s - x0 - L)])))
        cnt = defaultdict(int)
        hid.tracks = []
        for kind, cmp_ in htracks:
            cnt[kind] += 1
            hid.tracks.append(track(cnt[kind], cmp_))
        ref = C.MobRef()
        ref.mob_id = grp.mob_id
        ref.position = 0
        attrs(hid)["_MATCH"] = ref
        user_attrs(hid)
        f.content.add_mob(hid).user_placed = False
        # group clip: selectors
        def selector(kind, alts):
            sel = comp("Selector", kind, FPS, length=L_s, selected=0, is_ganged=True, mc_mode=0)
            sel.tracks = []
            for tr_idx in alts:
                tr = C.Track()
                tr.component = sclip(kind, L_s, hid.mob_id, tr_idx, 0)
                tr.lock_number = 0
                sel.tracks.append(tr)
            return sel
        grp.tracks = [track(1, selector("picture", list(range(1, len(syncs) + 1))))]
        for i in range(1, N + 1):
            grp.tracks.append(track(i, selector("sound", [i + k * N for k in range(len(syncs) + 1)])))
        user_attrs(grp)
        attrs(grp)["_EDIT_MODTIME"] = now
        pos += 1
        attrs(grp)["_USER_POS"] = pos
        f.content.add_mob(grp)
        n_groups += 1

    f.write(out)
    return {"bin": name, "takes": len(usable), "skipped": len(takes) - len(usable), "sync": n_sync,
            "groups": n_groups, "masters": len(masters), "mobs": len(list(f.content.mobs)), "report": report}


def fingerprint(path: str) -> dict:
    """Shape of a bin: per mob category the attribute keys, track layout and component classes."""
    import avb
    f = avb.open(path)
    c = f.content
    out = defaultdict(lambda: {"n": 0, "attrs": set(), "usage": set(), "comps": set(), "user": set()})
    for m in c.mobs:
        n = m.name or ""
        if m.mob_type_id == 2:
            cat = "master:sound" if "Scene" in (m.attributes.get("_USER") or {}) else "master:camera"
        elif m.mob_type_id == 1:
            cat = "grp" if ".Grp." in n else "sync" if ".sync." in n else "hidden" if not n else "sequence"
        else:
            continue
        d = out[cat]
        d["n"] += 1
        d["usage"].add(m.usage_code)
        d["attrs"] |= set(m.attributes.keys())
        d["user"] |= set((m.attributes.get("_USER") or {}).keys())
        for t in m.tracks:
            d["comps"].add(f"{t.media_kind}:{type(t.component).__name__}")
    return {k: {kk: (sorted(map(str, vv)) if isinstance(vv, set) else vv) for kk, vv in v.items()} for k, v in out.items()}


if __name__ == "__main__":
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description="Avid bin from takes.json: masters, sync clips, group clips")
    ap.add_argument("takes", help="takes.json from the sync pass")
    ap.add_argument("out", help="output .avb")
    ap.add_argument("--hdr", help="header cache (json); read from the drive for files missing in it, then saved")
    ap.add_argument("--bin-name")
    ap.add_argument("--project", help="also write the project settings (<name>.avp + Settings.xml) next to the bin")
    ap.add_argument("--avp-template", help="a real project's .avp (settings are taken from it)")
    ap.add_argument("--settings-template", help="that project's Settings.xml")
    ap.add_argument("--preset", help="project format preset, see core.avp_build.PRESETS")
    a = ap.parse_args()
    takes = json.load(open(a.takes, encoding="utf-8"))
    hdr = json.load(open(a.hdr, encoding="utf-8")) if a.hdr and os.path.exists(a.hdr) else {}
    want = [s["file"] for t in takes for s in t["sound"]] + [c["file"] for t in takes for c in t["cams"]]
    todo = [p for p in want if p not in hdr and os.path.exists(p)]
    if todo:
        print(f"reading {len(todo)} MXF headers from disk", file=sys.stderr)
        collect_headers(todo, hdr)
    if a.hdr:
        json.dump(hdr, open(a.hdr, "w", encoding="utf-8"), ensure_ascii=False)
    cid = {p: read_cid(p) for p, h in hdr.items() if any(t["own"] and t["kind"] == "video" for t in h["tracks"]) and os.path.exists(p)}
    r = build_bin(takes, hdr, a.out, cid=cid, bin_name=a.bin_name)
    print(json.dumps({k: v for k, v in r.items() if k != "report"}, ensure_ascii=False))
    print("\n".join(r["report"]))
    if a.project:
        if not a.avp_template:
            sys.exit("--project needs --avp-template")
        from core.avp_build import build_project
        pr = build_project(a.avp_template, os.path.dirname(os.path.abspath(a.out)), a.project, a.settings_template, a.preset)
        print(json.dumps(pr, ensure_ascii=False))
