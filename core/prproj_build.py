"""Build a Premiere `.prproj` from a plain description of a Resolve project.

    Resolve project --(core.adapters.resolve_spec)--> spec dict --(build)--> .prproj

Nothing is invented: every object is a copy of an object found in a real
Premiere project (the *template*) with fresh ids and edited fields. The
template supplies the project-level settings; its content (bins, clips,
sequences) is dropped. Use a template saved by the OLDEST Premiere that must
open the result: an old project opens in a new Premiere, not the other way
round. Structure notes: `docs/prproj-format.md`.

What is carried over: bins (from the media pool folders), media (video,
audio, video+audio, mono/stereo), sequences with video/audio tracks, clips
(position, source in/out, speed), the Motion transform (position, scale,
rotation), clip colour label, video<->audio links, hidden tracks.
What is NOT carried over, on purpose (see memory `prproj-builder-rules`):
grade, Resolve effects, Fusion. Such clips get a colour label and the
reason in the report. Also not carried: audio level, fades, timeline and
clip markers, disabled single clips, transitions - they all go into the
report, never silently dropped.

Spec (all times in frames of the timeline unless noted)::

    {"project": "name",
     "media": {id: {"path", "name", "bin": "Folder/Sub",
                    "video": {"w","h","fps","frames"} | None,
                    "audio": {"channels", "rate", "samples"} | None,
                    "tc_ticks": 0}},
     "timelines": [{"name", "fps", "w", "h", "bin",
        "video": [[item, ...], ...],                 # V1, V2, ...
        "audio": [{"kind": "mono"|"stereo", "items": [item, ...]}, ...],
        "hidden_video": [track index, ...]}],
     "report": [str, ...]}
    item = {"media": id, "start": f, "end": f, "src_in": seconds,
            "speed": 1.0, "name", "label": None|"flag",
            "tf": {"x": 0.5, "y": 0.5, "scale": 100, "rot": 0} | None,
            "link": None|group id, "ch": 0}          # ch = channel of a multi-channel file

Only reads the template and writes the output. Premiere itself is not
needed, but it is also not run: the result is checked structurally
(`verify`), never opened.
"""

from __future__ import annotations

import gzip
import json
import re
import sys
import uuid
from pathlib import Path

TPS = 254_016_000_000
AUDIO_TICKS = TPS // 48000  # 5 292 000
MIXER_MEDIA = {"audio": "80b8e3d5-6dca-4195-aefb-cb5f407ab009",
               "video": "228cda18-3625-4d2d-951e-348879e4ed93"}

_OBJ = re.compile(r'^\t<(\w[\w.]*) Object(U?ID)="([^"]+)"[^>]*>.*?^\t</\1>\n', re.M | re.S)
_REF = re.compile(r'Object(?:U)?Ref="([^"]+)"')
STOP_TYPES = {"Sequence", "SubClip", "VideoSequenceSource", "AudioSequenceSource",
              "VideoClipTrackItem", "AudioClipTrackItem", "Link", "VideoClipTrack",
              "AudioClipTrack", "PersistentGroup", "MasterClip", "Media",
              "VideoMediaSource", "AudioMediaSource", "Markers"}


def frame_ticks(fps: float) -> int:
    for nominal, den in ((24, 1001), (30, 1001), (60, 1001), (48, 1001)):
        if abs(fps - nominal * 1000 / den) < 0.02:
            return TPS * den // (nominal * 1000)
    return round(TPS / fps)


def esc(x: str) -> str:
    return x.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def tag_get(t: str, tag: str):
    m = re.search(rf"<{re.escape(tag)}>([^<]*)</{re.escape(tag)}>", t)
    return m.group(1) if m else None


def tag_set(t: str, tag: str, val, count: int = 1) -> str:
    out, n = re.subn(rf"<{re.escape(tag)}>[^<]*</{re.escape(tag)}>",
                     lambda m: f"<{tag}>{val}</{tag}>", t, count=count)
    if not n:
        raise KeyError(f"<{tag}> not found in {t[:90]!r}")
    return out


class Template:
    """A real project: objects indexed by id, with closure and fresh-id cloning."""

    def __init__(self, path: str | Path):
        raw = Path(path).read_bytes()
        s = (gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw).decode("utf-8")
        self.objs: dict[str, tuple[str, str]] = {}
        self.order: list[str] = []
        for m in _OBJ.finditer(s):
            self.objs[m.group(3)] = (m.group(1), m.group(0))
            self.order.append(m.group(3))
        self.head = s[: s.index(self.objs[self.order[0]][1])]
        nums = [int(k) for k in self.objs if k.isdigit()]
        self.next_id = max(nums) + 1
        self.version = re.search(r'<Project ObjectID="1"[^>]*Version="(\d+)"', s).group(1)
        self.build = (re.findall(r"MZ\.BuildVersion\.Modified>([^<]+)<", s) or ["?"])[0]
        self.labels = dict(re.findall(
            r"<asl\.clip\.label\.color>(\d+)</asl\.clip\.label\.color>\s*<asl\.clip\.label\.name>([^<]+)<", s))
        self.labels = {name: int(col) for col, name in self.labels.items()}

    def type_of(self, k):
        return self.objs[k][0]

    def text(self, k):
        return self.objs[k][1]

    def of_type(self, t):
        return [k for k in self.order if self.objs[k][0] == t]

    def closure(self, root: str, stop=frozenset(), stop_types=frozenset()) -> list[str]:
        seen, st = [], [root]
        while st:
            k = st.pop(0)
            if k in seen or k not in self.objs:
                continue
            if k != root and (k in stop or self.objs[k][0] in stop_types):
                continue
            seen.append(k)
            st.extend(_REF.findall(self.objs[k][1]))
        return seen

    def clone(self, keys, extra: dict | None = None) -> tuple[dict, dict]:
        """Copies of `keys` with fresh ids. Refs to ids in `extra` are redirected."""
        mp = dict(extra or {})
        for k in keys:
            if k in mp:
                continue
            if "-" in k:
                mp[k] = str(uuid.uuid4())
            else:
                mp[k] = str(self.next_id)
                self.next_id += 1
        out = {}
        for k in keys:
            t = self.objs[k][1]
            t = re.sub(r'(Object(?:U?ID|U?Ref))="([^"]+)"',
                       lambda m: f'{m.group(1)}="{mp.get(m.group(2), m.group(2))}"', t)
            t = re.sub(r"<ClipID>[^<]+</ClipID>", lambda m: f"<ClipID>{uuid.uuid4()}</ClipID>", t)
            t = re.sub(r"<ID>[0-9a-f]{8}-[0-9a-f-]{27}</ID>", lambda m: f"<ID>{uuid.uuid4()}</ID>", t)
            out[mp[k]] = t
        return out, mp

    def fresh(self) -> str:
        k = str(self.next_id)
        self.next_id += 1
        return k


# ----------------------------------------------------------------- templates

def _first(tpl: Template, pred):
    for k in tpl.of_type("ClipProjectItem"):
        mcs = _REF.findall(tpl.text(k))
        mc = next((m for m in mcs if m in tpl.objs and tpl.type_of(m) == "MasterClip"), None)
        if not mc:
            continue
        cl = tpl.closure(mc, stop_types={"Sequence", "SubClip", "VideoSequenceSource", "AudioSequenceSource"})
        types = {tpl.type_of(x) for x in cl}
        media = [x for x in cl if tpl.type_of(x) == "Media"]
        if media and pred(tpl, cl, types):
            return k, mc, cl
    raise LookupError("template has no suitable clip")


def _stereo(tpl, cl):
    return any("[{\"channellabel\":100},{\"channellabel\":101}]" in tpl.text(x) for x in cl if tpl.type_of(x) == "AudioStream")


def _real_media(tpl, cl):
    return any(tag_get(tpl.text(x), "RelativePath") and tag_get(tpl.text(x), "Start") is not None
               and "<ModificationState" in tpl.text(x) for x in cl if tpl.type_of(x) == "Media")


class Kit:
    """Prototype subgraphs found in the template by structure, not by id."""

    def __init__(self, tpl: Template):
        self.tpl = tpl
        self.av = _first(tpl, lambda t, cl, ty: {"VideoStream", "AudioStream"} <= ty and _stereo(t, cl) and _real_media(t, cl))
        try:
            self.a = _first(tpl, lambda t, cl, ty: "AudioStream" in ty and "VideoStream" not in ty and _stereo(t, cl) and _real_media(t, cl))
        except LookupError:
            self.a = None
        # sequence prototype: most audio tracks, has items
        best = None
        for sk in tpl.of_type("Sequence"):
            tg = [r for r in _REF.findall(tpl.text(sk)) if r in tpl.objs]
            n_a = next((len(re.findall(r"<Track Index", tpl.text(r))) for r in tg if tpl.type_of(r) == "AudioTrackGroup"), 0)
            if best is None or n_a > best[1]:
                best = (sk, n_a)
        self.seq = best[0]
        self._find_units()

    def _find_units(self):
        t = self.tpl
        s = t.text(self.seq)
        refs = [r for r in _REF.findall(s) if r in t.objs]
        self.vg = next(r for r in refs if t.type_of(r) == "VideoTrackGroup")
        self.ag = next(r for r in refs if t.type_of(r) == "AudioTrackGroup")
        self.vtrack = _REF.findall(t.text(self.vg))[0]
        self.atrack = _REF.findall(t.text(self.ag))[0]
        mix = re.search(r'<MasterTrack ObjectRef="(\d+)"', t.text(self.ag)).group(1)
        self.mix = mix
        self.inlet = re.search(r'<Inlet ObjectRef="(\d+)"', t.text(mix)).group(1)
        # a plain video item (no components) and one with Motion; same for audio
        self.v_plain = self.v_motion = self.a_plain = None
        for k in t.of_type("VideoClipTrackItem"):
            comp = re.search(r"<Components ObjectRef=\"(\d+)\"", t.text(k)).group(1)
            has = "<Component Index" in t.text(comp)
            if has and "AE.ADBE Motion" in "".join(t.text(c) for c in _REF.findall(t.text(comp)) if c in t.objs):
                self.v_motion = self.v_motion or k
            elif not has:
                self.v_plain = self.v_plain or k
        for k in t.of_type("AudioClipTrackItem"):
            comp = re.search(r"<Components ObjectRef=\"(\d+)\"", t.text(k)).group(1)
            if "<Component Index" not in t.text(comp) and 'ChannelType>1<' in t.text(comp):
                self.a_plain = k
                break
        if not (self.v_plain and self.v_motion and self.a_plain):
            raise LookupError("template lacks a plain video/audio item or a Motion clip")
        self.volume = next((k for k in t.of_type("AudioFilterComponent")
                            if "Internal Volume Stereo" in t.text(k) and "<Param Index=\"1\"" in t.text(k)), None)


# ------------------------------------------------------------------- media

def _mono(t: str) -> str:
    """Patch stereo audio object text to mono (verified against a real mono master clip)."""
    t = t.replace('[{"channellabel":100},{"channellabel":101}]', '[{"channellabel":0}]')
    t = re.sub(r"<ChannelType>1</ChannelType>", "<ChannelType>0</ChannelType>", t)
    return t


def _ticks_audio(samples: int, rate: int) -> int:
    return round(samples * TPS / rate)


def make_media(kit: Kit, m: dict, order_index: int, bin_ref: list):
    """Instantiate one master clip. Returns (objects, info) with info the keys the items need."""
    tpl = kit.tpl
    has_v, has_a = bool(m.get("video")), bool(m.get("audio"))
    proto = kit.av
    cpi, mc, cl = proto
    objs, mp = tpl.clone(cl + [cpi])
    byt = {}
    for k in cl:
        byt.setdefault(tpl.type_of(k), []).append(mp[k])
    name = esc(m["name"])
    path = esc(m["path"])
    nch = int(m["audio"]["channels"]) if has_a else 0
    mono = nch == 1
    vsrc = (byt.get("VideoMediaSource") or [None])[0]
    asrc = (byt.get("AudioMediaSource") or [None])[0]
    markers = (byt.get("Markers") or [None])[0]
    mcn = mp[mc]
    media_key = byt["Media"][0]
    if has_v:
        vfps = m["video"]["fps"]
        vdur = 10_973_491_200_000_000 if m.get("still") else round(m["video"]["frames"] * frame_ticks(vfps))
    if has_a:
        adur = _ticks_audio(m["audio"]["samples"], m["audio"].get("rate", 48000))
    dur = vdur if has_v else adur

    t = objs[mp[cpi]]
    t = tag_set(t, "Name", name)
    t = tag_set(t, "project.icon.view.grid.order", order_index)
    t = re.sub(r"\t\t\t\t\t<Column\.PropertyText\.Label>[^<]*</Column\.PropertyText\.Label>\n", "", t)
    objs[mp[cpi]] = t

    t = objs[mcn]
    t = tag_set(t, "Name", name)
    if has_v and not has_a:  # prune audio from a video+audio prototype
        t = re.sub(r"\t\t<AudioComponentChains Version=\"1\">.*?</AudioComponentChains>\n", "", t, flags=re.S)
        t = re.sub(r"\t\t<DefMappingID>[^<]*</DefMappingID>\n", "", t)
        vclip = byt["VideoClip"][0]
        t = re.sub(r"<Clips Version=\"1\">.*?</Clips>",
                   f'<Clips Version="1">\n\t\t\t<Clip Index="0" ObjectRef="{vclip}"/>\n\t\t</Clips>', t, flags=re.S)
        grp = tpl.fresh()
        t = re.sub(r'<AudioClipChannelGroups ObjectRef="\d+"/>', f'<AudioClipChannelGroups ObjectRef="{grp}"/>', t)
        objs[grp] = ('\t<ClipChannelGroupVectorSerializer ObjectID="%s" ClassID="a3127a8c-95d4-456e-a7f5-171b3f922426" Version="1">\n'
                     '\t</ClipChannelGroupVectorSerializer>\n' % grp)
        for ty in ("AudioClip", "AudioMediaSource", "AudioStream", "AudioComponentChain", "SecondaryContent",
                   "ClipChannelVectorSerializer", "ClipChannelSerializer", "ClipChannelGroupVectorSerializer"):
            for k in byt.get(ty, []):
                if k != grp:
                    objs.pop(k, None)
        asrc = None
    if has_a and not has_v:  # prune video from a video+audio prototype
        aclip = byt["AudioClip"][0]
        t = re.sub(r"<Clips Version=\"1\">.*?</Clips>",
                   f'<Clips Version="1">\n\t\t\t<Clip Index="0" ObjectRef="{aclip}"/>\n\t\t</Clips>', t, flags=re.S)
        for ty in ("VideoClip", "VideoMediaSource", "VideoStream"):
            for k in byt.get(ty, []):
                objs.pop(k, None)
        vsrc = None
    objs[mcn] = t

    # logging info
    li = byt["ClipLoggingInfo"][0]
    t = objs[li]
    t = tag_set(t, "ClipName", name)
    tc = int(m.get("tc_ticks", 0))
    t = tag_set(t, "MediaInPoint", tc)
    t = tag_set(t, "MediaOutPoint", tc + dur)
    if has_v:
        t = tag_set(t, "MediaFrameRate", frame_ticks(m["video"]["fps"]))
    objs[li] = t
    # media + streams
    t = objs[media_key]
    t = re.sub(r"\t\t<ModificationState.*?</ModificationState>\n", "", t, flags=re.S)
    t = re.sub(r"\t\t<ModificationState[^>]*/>\n", "", t)
    t = tag_set(t, "FilePath", path)
    t = tag_set(t, "ActualMediaFilePath", path)
    t = tag_set(t, "Title", name)
    t = tag_set(t, "FileKey", str(uuid.uuid4()))
    t = tag_set(t, "RelativePath", esc(m.get("rel") or "../" + m["path"].lstrip("/")))
    t = tag_set(t, "Start", tc)
    t = tag_set(t, "ContentAndMetadataState", str(uuid.uuid4()))
    if not has_v:
        t = re.sub(r'\t\t<VideoStream ObjectRef="\d+"/>\n', "", t)
    if not has_a:
        t = re.sub(r'\t\t<AudioStream ObjectRef="\d+"/>\n', "", t)
    objs[media_key] = t
    for k in (vsrc, asrc):
        if k and k in objs:
            objs[k] = tag_set(objs[k], "OriginalDuration", dur)
    if has_v:
        vs = byt["VideoStream"][0]
        t = objs[vs]
        w, h = m["video"]["w"], m["video"]["h"]
        t = tag_set(t, "FrameRate", frame_ticks(m["video"]["fps"]))
        t = tag_set(t, "FrameRect", f"0,0,{w},{h}")
        t = tag_set(t, "Duration", vdur)
        if m.get("still") and "<IsStill>" not in t:
            t = re.sub(r"(<VideoStream [^>]*>\n)", r"\1\t\t<IsStill>true</IsStill>\n", t, count=1)
        objs[vs] = t
    if has_a:
        ast = byt["AudioStream"][0]
        t = objs[ast]
        t = re.sub(r"\t\t<(ConformedAudioPath|PeakFilePath)>.*?</\1>\n", "", t)
        t = tag_set(t, "Duration", adur)
        objs[ast] = t
        if mono:
            for k in list(objs):
                if tpl.objs.get(k) is None and k in objs:
                    pass
            for ty in ("AudioStream", "AudioClip", "AudioComponentChain", "ClipChannelVectorSerializer"):
                for k in byt.get(ty, []):
                    if k in objs:
                        objs[k] = _mono(objs[k])
            # one channel only
            for k in byt.get("ClipChannelVectorSerializer", []):
                refs = re.findall(r'<ClipChannelItem Index="(\d+)" ObjectRef="(\d+)"/>', objs[k])
                objs[k] = re.sub(r'\t\t\t<ClipChannelItem Index="1" ObjectRef="\d+"/>\n', "", objs[k])
                for _, r in refs[1:]:
                    objs.pop(r, None)
            for k in byt.get("AudioClip", []):
                sc = re.findall(r'<SecondaryContentItem Index="(\d+)" ObjectRef="(\d+)"/>', objs[k])
                objs[k] = re.sub(r'\t\t\t<SecondaryContentItem Index="1" ObjectRef="\d+"/>\n', "", objs[k])
                for _, r in sc[1:]:
                    objs.pop(r, None)
        elif nch > 2:
            m.setdefault("_warn", []).append(f"{nch} channels: Premiere master clip built as stereo (channels 1-2)")
    return objs, dict(cpi=mp[cpi], mc=mcn, vsrc=vsrc, asrc=asrc, markers=markers, has_v=has_v, has_a=has_a,
                      mono=mono, dur=dur, w=(m["video"]["w"] if has_v else 0), h=(m["video"]["h"] if has_v else 0),
                      name=name, nch=nch)


# ---------------------------------------------------------------- sequences

def _unit(kit, root, stop_types=frozenset()):
    tpl = kit.tpl
    cl = tpl.closure(root, stop_types=STOP_TYPES - {"VideoClipTrack", "AudioClipTrack"} | set(stop_types))
    return cl


def make_item(kit: Kit, kind: str, it: dict, info: dict, fps_t: int, seq_wh, track_mono: bool):
    """One track item: returns (objects, top_key, link_key)."""
    tpl = kit.tpl
    start, end = it["start"] * fps_t, it["end"] * fps_t
    speed = float(it.get("speed", 1.0))
    src_in = round(it["src_in"] * TPS)
    src_out = src_in + round((end - start) * speed)
    use_motion = kind == "v" and it.get("tf") is not None
    proto = kit.v_motion if use_motion else (kit.v_plain if kind == "v" else kit.a_plain)
    cl = tpl.closure(proto, stop_types={"MasterClip", "Media", "VideoMediaSource", "AudioMediaSource", "Markers"})
    extra = {}
    for k in cl:
        for r in _REF.findall(tpl.text(k)):
            if r in tpl.objs and tpl.type_of(r) in ("MasterClip", "VideoMediaSource", "AudioMediaSource", "Markers", "Media"):
                ty = tpl.type_of(r)
                tgt = {"MasterClip": info["mc"], "VideoMediaSource": info["vsrc"],
                       "AudioMediaSource": info["asrc"], "Markers": info["markers"]}.get(ty)
                extra[r] = tgt
    extra = {k: v for k, v in extra.items() if v}
    objs, mp = tpl.clone(cl, extra)
    byt = {}
    for k in cl:
        byt.setdefault(tpl.type_of(k), []).append(mp[k])
    top = mp[proto]
    t = objs[top]
    t = tag_set(t, "Start", start)
    t = tag_set(t, "End", end)
    if kind == "v":
        w, h = info["w"], info["h"]
        t = tag_set(t, "FrameRect", f"0,0,{w},{h}")
    objs[top] = t
    sub = byt["SubClip"][0]
    objs[sub] = tag_set(objs[sub], "Name", info["name"])
    clip = (byt.get("VideoClip") or byt.get("AudioClip"))[0]
    c = objs[clip]
    c = tag_set(c, "InPoint", src_in) if "<InPoint>" in c else c.replace("</ClipID>", f"</ClipID>\n\t\t\t<InPoint>{src_in}</InPoint>", 1)
    c = tag_set(c, "OutPoint", src_out) if "<OutPoint>" in c else c.replace("</InPoint>", f"</InPoint>\n\t\t\t<OutPoint>{src_out}</OutPoint>", 1)
    c = re.sub(r"\t\t\t<PlaybackSpeed>[^<]*</PlaybackSpeed>\n", "", c)
    if abs(speed - 1.0) > 1e-4:
        c = c.replace("</ClipID>", f"</ClipID>\n\t\t\t<PlaybackSpeed>{speed!r}</PlaybackSpeed>", 1)
    if it.get("label"):
        col = kit.tpl.labels
        name = kit.flag_label if hasattr(kit, "flag_label") else None
        if name:
            c = re.sub(r"<asl\.clip\.label\.color>\d+</asl\.clip\.label\.color>", f"<asl.clip.label.color>{col[name]}</asl.clip.label.color>", c)
            c = re.sub(r"<asl\.clip\.label\.name>[^<]+</asl\.clip\.label\.name>", f"<asl.clip.label.name>{name}</asl.clip.label.name>", c)
    objs[clip] = c
    if kind == "a":
        if track_mono:
            for ty in ("AudioClip", "AudioComponentChain"):
                for k in byt.get(ty, []):
                    objs[k] = _mono(objs[k])
            for k in byt.get("AudioClip", []):
                sc = re.findall(r'<SecondaryContentItem Index="(\d+)" ObjectRef="(\d+)"/>', objs[k])
                objs[k] = re.sub(r'\t\t\t<SecondaryContentItem Index="1" ObjectRef="\d+"/>\n', "", objs[k])
                for _, r in sc[1:]:
                    objs.pop(r, None)
        for k in byt.get("SecondaryContent", []):
            if k in objs and info.get("asrc"):
                objs[k] = re.sub(r'<Content ObjectRef="\d+"/>', f'<Content ObjectRef="{info["asrc"]}"/>', objs[k])
    gain = it.get("gain_db")
    if kind == "a" and gain is not None and abs(gain) > 0.005 and kit.volume:
        _add_volume(kit, objs, byt["AudioComponentChain"][0], gain, track_mono)
    if use_motion:
        tf = it["tf"]
        params = re.findall(r'<Param Index="(\d+)" ObjectRef="(\d+)"/>', objs[byt["VideoFilterComponent"][0]])
        vals = {0: f"{tf.get('x', 0.5)!r}:{tf.get('y', 0.5)!r}", 1: f"{tf.get('scale', 100.0)!r}",
                2: "100.", 3: "true", 4: f"{tf.get('rot', 0.0)!r}", 5: "0.5:0.5", 6: "0."}
        for idx, key in params:
            idx = int(idx)
            if idx in vals:
                objs[key] = re.sub(r"(<StartKeyframe>-?\d+,)[^,]*(,)", lambda m: m.group(1) + vals[idx] + m.group(2), objs[key], count=1)
    return objs, top


def level_value(db: float) -> float:
    """Premiere's stored Level: 1.0 is +15 dB, 0 dB is 0.1778 (verified: Channel Volume default in
    one template, the mono Volume default in another). Below -100 dB it is silence."""
    if db <= -100:
        return 0.0
    return min(1.0, 10 ** ((db - 15.0) / 20.0))


def _add_volume(kit: Kit, objs: dict, chain_key: str, db: float, mono: bool) -> None:
    """Give an audio clip a Volume component with a constant Level (no automation)."""
    tpl = kit.tpl
    cl = tpl.closure(kit.volume)
    new, mp = tpl.clone(cl)
    top = mp[kit.volume]
    for k, v in list(new.items()):
        if mono:
            v = _mono(v).replace("Internal Volume Stereo", "Internal Volume Mono")
        if v.lstrip().startswith("<AudioComponentParam") and tag_get(v, "UnitsString") == "dB":
            val = repr(level_value(db))
            v = tag_set(v, "StartKeyframe", f"-91445760000000000,{val},0,0,0,0,0,0")
            v = tag_set(v, "CurrentValue", val)
        new[k] = v
    objs.update(new)
    ch = objs[chain_key]
    block = f'<Components Version="1">\n\t\t\t\t<Component Index="0" ObjectRef="{top}"/>\n\t\t\t</Components>\n\t\t'
    ch2 = re.sub(r"(</Node>\s*)(</ComponentChain>)", lambda m: m.group(1) + block + m.group(2), ch, count=1)
    assert ch2 != ch, "audio chain shape not as expected"
    objs[chain_key] = ch2


def build(spec: dict, template: str | Path, out: str | Path) -> dict:
    tpl = Template(template)
    kit = Kit(tpl)
    kit.flag_label = next((n for n in ("BE.Prefs.LabelColors.7", "BE.Prefs.LabelColors.5", "BE.Prefs.LabelColors.3") if n in tpl.labels), None)
    report = list(spec.get("report", []))
    new: dict[str, str] = {}
    items_root: list[str] = []
    bins: dict[str, str] = {}      # path -> bin uid
    bin_children: dict[str, list[str]] = {}

    def bin_uid(path: str) -> str | None:
        if not path:
            return None
        if path in bins:
            return bins[path]
        parent = bin_uid("/".join(path.split("/")[:-1]))
        u = str(uuid.uuid4())
        bins[path] = u
        bin_children[u] = []
        (bin_children[parent] if parent else items_root).append(u)
        return u

    # media
    infos, order_n = {}, {}
    media_ids = {mid for tl in spec["timelines"] for tr in tl["video"] + [a["items"] for a in tl["audio"]] for it in tr for mid in [it["media"]]}
    for mid in sorted(media_ids):
        m = spec["media"][mid]
        folder = m.get("bin") or ""
        parent = bin_uid(folder)
        n = order_n.setdefault(parent, 0)
        order_n[parent] += 1
        objs, info = make_media(kit, m, n, [])
        new.update(objs)
        infos[mid] = info
        (bin_children[parent] if parent else items_root).append(info["cpi"])
        for w in m.get("_warn", []):
            report.append(f"{m['name']}: {w}")

    # sequences
    seq_ids = []
    for si, tl in enumerate(spec["timelines"], 1):
        ft = frame_ticks(tl["fps"])
        nv, na = max(1, len(tl["video"])), max(1, len(tl["audio"]))
        cl = tpl.closure(kit.seq, stop_types=STOP_TYPES - {"VideoClipTrack", "AudioClipTrack"} | {"VideoClipTrack", "AudioClipTrack"})
        # keep group objects + mixer + inlet; tracks are rebuilt below
        cl = [k for k in cl if tpl.type_of(k) not in ("VideoClipTrack", "AudioClipTrack", "Link", "PersistentGroup")]
        objs, mp = tpl.clone(cl)
        seq, vg, ag, inlet = mp[kit.seq], mp[kit.vg], mp[kit.ag], mp[kit.inlet]
        # tracks
        vt_keys, at_keys = [], []
        v_unit = tpl.closure(kit.vtrack, stop_types=STOP_TYPES - {"VideoClipTrack"})
        a_unit = tpl.closure(kit.atrack, stop_types=STOP_TYPES - {"AudioClipTrack"})
        a_unit = [k for k in a_unit if tpl.type_of(k) != "AudioMixTrack"]
        track_tops = {"v": [], "a": []}
        items_by_track = {"v": [], "a": []}
        links: dict = {}
        vtrack_items = tl["video"] or [[]]
        atrack_items = tl["audio"] or [{"kind": "stereo", "items": []}]
        for ti, items in enumerate(vtrack_items):
            o, m_ = tpl.clone(v_unit)
            top = m_[kit.vtrack]
            t = o[top]
            t = _set_track_indexes(t, ti, ti + 1)
            t = tag_set(t, "MZ.TrackTargeted", 1 if ti == 0 else 0)
            if ti in tl.get("hidden_video", []):
                t = tag_set(t, "IsMuted", "true")
            refs = []
            for it in items:
                io, itop = make_item(kit, "v", it, infos[it["media"]], ft, (tl["w"], tl["h"]), False)
                o.update(io)
                refs.append(itop)
                if it.get("link") is not None:
                    links.setdefault(it["link"], []).append(itop)
            t = _set_items(t, refs)
            o[top] = t
            new.update(o)
            vt_keys.append(top)
        for ti, tr in enumerate(atrack_items):
            mono = tr.get("kind") == "mono"
            o, m_ = tpl.clone(a_unit)
            top = m_[kit.atrack]
            t = o[top]
            t = _set_track_indexes(t, ti, ti + 2)
            t = tag_set(t, "MZ.TrackTargeted", 1 if ti == 0 else 0)
            if ti in tl.get("hidden_audio", []):
                t = tag_set(t, "IsMuted", "true")
            if mono:
                t = _mono(t)
                for k, v in list(o.items()):
                    if k != top and v.lstrip().startswith(("<AudioComponentChain", "<StereoToStereoPanProcessor", "<DefaultPanProcessor")):
                        o[k] = _mono(v)
            refs = []
            for it in tr["items"]:
                io, itop = make_item(kit, "a", it, infos[it["media"]], ft, (tl["w"], tl["h"]), mono)
                o.update(io)
                refs.append(itop)
                if it.get("link") is not None:
                    links.setdefault(it["link"], []).append(itop)
            t = _set_items(t, refs)
            o[top] = t
            new.update(o)
            at_keys.append(top)
        # groups
        t = objs[vg]
        t = _set_tracks(t, vt_keys)
        t = tag_set(t, "NextTrackID", len(vt_keys) + 1)
        t = tag_set(t, "FrameRect", f"0,0,{tl['w']},{tl['h']}")
        t = tag_set(t, "FrameRate", ft, count=1)
        objs[vg] = t
        t = objs[ag]
        t = _set_tracks(t, at_keys)
        t = tag_set(t, "NextTrackID", len(at_keys) + 2)
        objs[ag] = t
        objs[inlet] = re.sub(r"<Sources Version=\"1\">.*?</Sources>",
                             "<Sources Version=\"1\">\n" + "".join(f'\t\t\t<Source Index="{i}" ObjectURef="{k}"/>\n' for i, k in enumerate(at_keys)) + "\t\t</Sources>",
                             objs[inlet], flags=re.S)
        # links
        lrefs = []
        for g_, tis in links.items():
            lid = tpl.fresh()
            objs[lid] = (f'\t<Link ObjectID="{lid}" ClassID="149d4ea5-a7d4-4b34-9bb7-16d783904bf2" Version="1">\n\t\t<TrackItemGroup Version="1">\n'
                         '\t\t\t<TrackItems Version="1">\n' + "".join(f'\t\t\t\t<TrackItem Index="{j}" ObjectRef="{x}"/>\n' for j, x in enumerate(tis))
                         + "\t\t\t</TrackItems>\n\t\t</TrackItemGroup>\n\t</Link>\n")
            lrefs.append(lid)
        t = objs[seq]
        t = re.sub(r"\t\t\t<LinkContainer Version=\"1\">.*?</LinkContainer>\n",
                   "\t\t\t<LinkContainer Version=\"1\">\n\t\t\t\t<Links Version=\"1\">\n" + "".join(
                       f'\t\t\t\t\t<Link Index="{j}" ObjectRef="{r}"/>\n' for j, r in enumerate(lrefs)) + "\t\t\t\t</Links>\n\t\t\t</LinkContainer>\n", t, flags=re.S)
        t = re.sub(r"\t\t\t<Groups Version=\"1\">.*?</Groups>\n", "", t, flags=re.S)
        t = tag_set(t, "Name", esc(tl["name"]))
        t = tag_set(t, "ID", si)
        end_t = max([it["end"] for tr in vtrack_items for it in tr] + [it["end"] for tr in atrack_items for it in tr["items"]] + [1]) * ft
        t = tag_set(t, "MZ.WorkOutPoint", end_t)
        t = tag_set(t, "MZ.EditLine", 0)
        t = tag_set(t, "MZ.InPoint", -101606400000000000)
        t = tag_set(t, "MZ.OutPoint", -101606400000000000)
        t = tag_set(t, "MZ.WorkInPoint", 0)
        t = tag_set(t, "Monitor.ProgramZoomOut", end_t)
        t = tag_set(t, "MZ.Sequence.PreviewFrameSizeWidth", tl["w"])
        t = tag_set(t, "MZ.Sequence.PreviewFrameSizeHeight", tl["h"])
        t = re.sub(r"(<HSL\.TimelinePatchingAndTargeting\.VideoPatches[^>]*>)\[.*?\](</)",
                   lambda m: m.group(1) + json.dumps([{"mNumber": 0 if i == 0 else -1, "mState": 2 if i == 0 else 0} for i in range(len(vt_keys))], separators=(",", ":")) + m.group(2), t)
        t = re.sub(r"(<HSL\.TimelinePatchingAndTargeting\.AudioPatches[^>]*>)\[.*?\](</)",
                   lambda m: m.group(1) + json.dumps([{"mNumber": 0 if i == 0 else -1, "mState": 2 if i == 0 else 0} for i in range(len(at_keys))], separators=(",", ":")) + m.group(2), t)
        objs[seq] = t
        new.update(objs)
        # project item for the sequence
        sc, smp = _seq_item(kit, seq, tl, end_t, si, len(items_root))
        new.update(sc)
        parent = bin_uid(tl.get("bin", "Sequences"))
        (bin_children[parent] if parent else items_root).append(smp)
        seq_ids.append(seq)

    # bins
    for path, u in bins.items():
        name = path.split("/")[-1]
        kids = bin_children[u]
        new[u] = (f'\t<BinProjectItem ObjectUID="{u}" ClassID="dbfd6653-24da-480e-a35e-ba45e9504e4b" Version="1">\n\t\t<ProjectItem Version="1">\n'
                  f'\t\t\t<Node Version="1">\n\t\t\t\t<Properties Version="1">\n\t\t\t\t\t<project.icon.view.grid.order>0</project.icon.view.grid.order>\n'
                  f'\t\t\t\t</Properties>\n\t\t\t</Node>\n\t\t\t<Name>{esc(name)}</Name>\n\t\t</ProjectItem>\n'
                  '\t\t<ProjectItemContainer Version="1">\n\t\t\t<Items Version="1">\n'
                  + "".join(f'\t\t\t\t<Item Index="{j}" ObjectURef="{x}"/>\n' for j, x in enumerate(kids))
                  + '\t\t\t</Items>\n\t\t</ProjectItemContainer>\n\t</BinProjectItem>\n')

    # project + root; drop template content
    content = {"ClipProjectItem", "MasterClip", "Media", "VideoStream", "AudioStream", "VideoMediaSource", "AudioMediaSource",
               "VideoClip", "AudioClip", "Markers", "Marker", "SecondaryContent", "ClipChannelSerializer", "ClipChannelVectorSerializer",
               "ClipChannelGroupVectorSerializer", "ClipLoggingInfo", "AudioComponentChain", "Sequence", "VideoTrackGroup", "AudioTrackGroup",
               "VideoClipTrack", "AudioClipTrack", "AudioMixTrack", "AudioTrackInlet", "SubClip", "VideoClipTrackItem", "AudioClipTrackItem",
               "AudioTransitionTrackItem", "Link", "PersistentGroup", "VideoSequenceSource", "AudioSequenceSource", "VideoComponentChain",
               "VideoFilterComponent", "AudioFilterComponent", "VideoComponentParam", "AudioComponentParam", "PointComponentParam",
               "ArbVideoComponentParam", "AudioFader", "AudioMeter", "StereoToStereoPanProcessor", "S51ToStereoPanProcessor",
               "DefaultPanProcessor", "AudioPreviewItem", "BinProjectItem", "DataTrackGroup"}
    root_key = tpl.of_type("RootProjectItem")[0]
    keep = [k for k in tpl.order if tpl.type_of(k) not in content and k != root_key and k != "1"]
    root = re.sub(r"<Items Version=\"1\">.*?</Items>",
                  "<Items Version=\"1\">\n" + "".join(f'\t\t\t\t<Item Index="{j}" ObjectURef="{x}"/>\n' for j, x in enumerate(items_root)) + "\t\t\t</Items>",
                  tpl.text(root_key), flags=re.S)
    proj = tpl.text("1")
    for tag in ("MZ.PrefixKey.OpenSequenceGuidList.1",):
        if f"<{tag}>" in proj:
            proj = tag_set(proj, tag, seq_ids[0] if seq_ids else "")
    if "<MZ.Project.GUID>" in proj:
        proj = tag_set(proj, "MZ.Project.GUID", str(uuid.uuid4()))
    if "<NextSequenceID>" in proj:
        proj = tag_set(proj, "NextSequenceID", len(spec["timelines"]) + 1)
    body = proj + "".join(tpl.text(k) for k in keep) + root + "".join(new.values())
    # order: Project first (as in template); the rest after
    xml = tpl.head + body + "</PremiereData>\n"
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wb") as fh:
        fh.write(xml.encode("utf-8"))
    res = verify(xml)
    res.update(report=report, template_version=tpl.version, template_build=tpl.build,
               sequences=len(spec["timelines"]), media=len(infos))
    return res


def _set_track_indexes(t: str, index: int, track_id: int) -> str:
    t = re.sub(r"<Index>\d+</Index>", f"<Index>{index}</Index>", t)
    t = re.sub(r"(<IsLocked>.*?)", r"\1", t)
    t = re.sub(r"(<MZ\.SourceTrackNumber>)\d+(</MZ\.SourceTrackNumber>)", rf"\g<1>{index}\g<2>", t)
    t = re.sub(r"(</Index>\n\t\t\t\t<ID>)\d+(</ID>)", rf"\g<1>{track_id}\g<2>", t, count=1)
    return t


def _set_items(t: str, refs: list[str]) -> str:
    block = ('<TrackItems Version="1">\n' + "".join(f'\t\t\t\t\t<TrackItem Index="{j}" ObjectRef="{x}"/>\n' for j, x in enumerate(refs)) + "\t\t\t\t</TrackItems>")
    return re.sub(r"<TrackItems Version=\"1\">.*?</TrackItems>", lambda m: block, t, count=1, flags=re.S)


def _set_tracks(t: str, keys: list[str]) -> str:
    block = ('<Tracks Version="1">\n' + "".join(f'\t\t\t\t<Track Index="{i}" ObjectURef="{k}"/>\n' for i, k in enumerate(keys)) + "\t\t\t</Tracks>")
    return re.sub(r"<Tracks Version=\"1\">.*?</Tracks>", lambda m: block, t, count=1, flags=re.S)


def _seq_item(kit: Kit, seq_key: str, tl: dict, dur: int, n: int, order: int):
    """ClipProjectItem -> MasterClip -> Video/AudioSequenceSource for a new sequence."""
    tpl = kit.tpl
    tseq = kit.seq
    cpi = next(k for k in tpl.of_type("ClipProjectItem")
               if any(tpl.type_of(r) == "MasterClip" and any(seq_ref(tpl, x) == tseq for x in tpl.closure(r, stop_types={"Sequence"}) if tpl.type_of(x) in ("VideoSequenceSource", "AudioSequenceSource"))
                      for r in _REF.findall(tpl.text(k)) if r in tpl.objs))
    mc = next(r for r in _REF.findall(tpl.text(cpi)) if r in tpl.objs and tpl.type_of(r) == "MasterClip")
    cl = tpl.closure(mc, stop_types={"Sequence"})
    objs, mp = tpl.clone(cl + [cpi], {tseq: seq_key})
    nm = esc(tl["name"])
    objs[mp[cpi]] = tag_set(tag_set(objs[mp[cpi]], "Name", nm), "project.icon.view.grid.order", order)
    objs[mp[mc]] = tag_set(objs[mp[mc]], "Name", nm)
    for k in cl:
        if tpl.type_of(k) in ("VideoSequenceSource", "AudioSequenceSource"):
            objs[mp[k]] = tag_set(objs[mp[k]], "OriginalDuration", dur)
    return objs, mp[cpi]


def seq_ref(tpl, k):
    m = re.search(r'<Sequence ObjectURef="([^"]+)"', tpl.text(k))
    return m.group(1) if m else None


def verify(text: str) -> dict:
    """Structural checks only: unique top-level ids, no dangling refs, counts.

    Objects nested inside `Project` (view states, columns) have their own id scope, so
    they count for reference lookups but not for the duplicate check.
    """
    top = re.findall(r'^\t<\w[\w.]* Object(?:U?ID)="([^"]+)"', text, re.M)
    anyid = set(re.findall(r'<\w[\w.]* Object(?:U?ID)="([^"]+)"', text))
    dup = sorted({k for k in top if top.count(k) > 1})[:10]
    refs = set(re.findall(r'Object(?:U)?Ref="([^"]+)"', text))
    dangling = sorted(refs - anyid)[:10]
    from xml.dom import minidom
    minidom.parseString(text.encode("utf-8"))
    return {"objects": len(top), "duplicate_ids": dup, "dangling_refs": dangling,
            "video_items": text.count("<VideoClipTrackItem "), "audio_items": text.count("<AudioClipTrackItem ")}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("spec")
    ap.add_argument("template")
    ap.add_argument("out")
    a = ap.parse_args()
    r = build(json.load(open(a.spec, encoding="utf-8")), a.template, a.out)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    sys.exit(1 if r["duplicate_ids"] or r["dangling_refs"] else 0)
