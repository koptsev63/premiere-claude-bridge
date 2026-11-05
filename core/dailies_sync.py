"""Sound-to-picture sync of a shoot day: group recorder files into takes, find the cameras by
timecode, then check and correct every link by cross-correlating the camera's scratch audio
with the boom.

    scan_mxf(dirs)  ->  rows        what the MXF headers say (scene, take, channel names, timecode)
    build_takes(rows) -> takes      per take: sound files in channel order, cameras with an offset

`takes` is the JSON the other tools read (`core.avb_build`, `core.adapters.resolve_restore_sound`,
`core.framecheck`). Offsets are in frames: ``offset_frames`` > 0 means the camera picture is late
against its own timecode. Camera frame ``s`` shows the sound-timecode moment
``s + f0 - offset_frames``; the sound-file frame is that minus the take's ``tc_frames``.

Why the correlation is PHAT-weighted: camera scratch tracks and the boom have different
microphones and EQ; whitening the spectrum keeps a sharp peak where plain correlation smears.
`sharp` is the peak against the spread of the rest of the curve; a link below ``trust`` is
marked ``trusted: False``. Weak links with a big jump (``sharp < roll_fix`` and a shift of more
than 2 frames) take the median offset of the same camera roll (``fixed_by_roll``), since one
camera does not drift between its clips of a day.

Needs numpy and PyAV. Recorder files are expected as Avid OP-Atom MXF (one mono file per
channel, scene/take/TRKn in the header comments); a camera's audio is read from
``<clip>_A01.mxf`` next to it, or from the clip itself when there is no such file.
"""

from __future__ import annotations

import collections
import glob
import json
import os
import statistics
import sys
from pathlib import Path

FPS = 24
SR = 8000                       # analysis rate
MIN_OVERLAP_S = 2               # a camera must share this much time with the take
LAG_S = 2                       # search window, seconds either way


def tcf(tc: str, fps: int = FPS) -> int:
    h, m, s, f = map(int, tc.replace(";", ":").split(":"))
    return ((h * 60 + m) * 60 + s) * fps + f


# ---------------------------------------------------------------- headers

def scan_mxf(dirs, progress=None) -> list[dict]:
    """One row per MXF under `dirs`: kind, material name, scene, take, channel names, timecode."""
    import av
    rows = []
    for d in dirs:
        for p in sorted(glob.glob(os.path.join(d, "**", "*.mxf"), recursive=True)):
            try:
                c = av.open(p)
            except Exception as e:                      # unreadable file: keep a trace, go on
                rows.append({"file": p, "err": repr(e)})
                continue
            m = dict(c.metadata)
            st = c.streams[0]
            rows.append({"file": p, "kind": st.type, "name": m.get("material_package_name"),
                         "scene": m.get("comment_Scene"), "take": m.get("comment_Take"),
                         "tracks": [m[k].strip() for k in sorted(m, key=lambda k: (len(k), k)) if k.startswith("comment_TRK")],
                         "track_name": st.metadata.get("track_name"), "tc": st.metadata.get("timecode"),
                         "dur": float(c.duration / 1e6) if c.duration else None,
                         "circled": m.get("comment_Circled"), "rate": getattr(st, "rate", None)})
            c.close()
            if progress and len(rows) % 100 == 0:
                progress(f"{len(rows)} files")
    return rows


# ------------------------------------------------------------------ audio

def load_audio(path: str, sr: int = SR):
    import av
    import numpy as np
    c = av.open(path)
    rs = av.AudioResampler(format="flt", layout="mono", rate=sr)
    out = []
    for fr in c.decode(audio=0):
        for r in rs.resample(fr):
            out.append(r.to_ndarray().reshape(-1))
    for r in rs.resample(None):
        out.append(r.to_ndarray().reshape(-1))
    c.close()
    return np.concatenate(out) if out else np.zeros(0, np.float32)


def xcorr_offset(ref, sig, max_lag: int):
    """Lag (samples) such that ``sig[n + lag] ~ ref[n]``, and how sharp the peak is."""
    import numpy as np
    ref = ref - ref.mean()
    sig = sig - sig.mean()
    n = 1 << int(np.ceil(np.log2(len(ref) + len(sig))))
    X = np.fft.rfft(sig, n) * np.conj(np.fft.rfft(ref, n))
    X /= np.abs(X) + 1e-9                               # PHAT weighting
    cc = np.fft.irfft(X, n)
    cc = np.concatenate([cc[-max_lag:], cc[:max_lag + 1]])
    k = int(np.argmax(cc))
    others = np.delete(cc, range(max(0, k - 40), min(len(cc), k + 41)))
    return k - max_lag, float(cc[k] / (np.std(others) + 1e-9))


def camera_audio_path(video: str) -> str:
    side = video[:-4] + "_A01.mxf" if video.endswith(".mxf") else video
    return side if os.path.exists(side) else video


# ------------------------------------------------------------------ takes

def _scene_match(scene: str, wanted) -> bool:
    if not wanted:
        return True
    p = scene.lstrip("+").split("-")
    ep, scenes = p[0], p[1:-1]
    return any(w in (f"{ep}-{s}" for s in scenes) for w in wanted)


def group_sound(rows, scenes=None) -> dict:
    """Material name -> its channel rows, in channel order."""
    snd = collections.defaultdict(list)
    for r in rows:
        if r.get("kind") == "audio" and r.get("scene") and r.get("name") and _scene_match(r["scene"], scenes):
            snd[r["name"]].append(r)
    for rs in snd.values():
        rs.sort(key=lambda r: int(r["track_name"].lstrip("A")))
    return snd


def cameras(rows) -> list[dict]:
    out = []
    for r in rows:
        if r.get("kind") == "video" and r.get("tc") and r.get("dur"):
            v = dict(r)
            v["f0"] = tcf(r["tc"])
            v["f1"] = v["f0"] + round(r["dur"] * FPS)
            out.append(v)
    return out


def build_takes(rows, scenes=None, trust: float = 8.0, roll_fix: float | None = 10.0,
                progress=print, correlate: bool = True) -> list[dict]:
    """Takes with their cameras and offsets (see the module doc). `correlate=False` links by
    timecode only (offset 0, nothing trusted): fast, for a first look."""
    snd = group_sound(rows, scenes)
    vids = cameras(rows)
    takes = []
    for name, rs in sorted(snd.items(), key=lambda kv: tcf(kv[1][0]["tc"])):
        t0 = tcf(rs[0]["tc"])
        t1 = t0 + round(rs[0]["dur"] * FPS)
        labels = rs[0]["tracks"]
        take = {"name": name, "scene": rs[0]["scene"], "take": rs[0]["take"], "tc_frames": t0, "len_frames": t1 - t0,
                "sound": [{"file": r["file"], "track": r["track_name"],
                           "label": labels[i] if i < len(labels) else r["track_name"]} for i, r in enumerate(rs)],
                "cams": []}
        boom = load_audio(rs[0]["file"]) if correlate else None
        for v in vids:
            o0, o1 = max(t0, v["f0"]), min(t1, v["f1"])
            if o1 - o0 < FPS * MIN_OVERLAP_S:
                continue
            cam = {"file": v["file"], "clip": os.path.basename(v["file"]), "cam": os.path.basename(v["file"])[:1],
                   "f0": v["f0"], "f1": v["f1"], "overlap": round((o1 - o0) / (t1 - t0), 2)}
            if correlate:
                ca = load_audio(camera_audio_path(v["file"]))
                a = boom[int((o0 - t0) / FPS * SR): int((o1 - t0) / FPS * SR)]
                b = ca[int((o0 - v["f0"]) / FPS * SR): int((o1 - v["f0"]) / FPS * SR)]
                m = min(len(a), len(b))
                lag, sharp = xcorr_offset(a[:m], b[:m], LAG_S * SR)
                cam.update(xcorr_lag_ms=round(lag / SR * 1000, 1), offset_frames=round(lag / SR * FPS),
                           sharp=round(sharp, 1), trusted=sharp >= trust)
            else:
                cam.update(xcorr_lag_ms=0.0, offset_frames=0, sharp=0.0, trusted=False)
            take["cams"].append(cam)
        takes.append(take)
        progress(f"{name:14s} {len(rs)}ch  " + "  ".join(
            f"{c['cam']}:{c['clip'][2:10]} ov={c['overlap']} {c['offset_frames']:+d}f q={c['sharp']}" for c in take["cams"]))
    if correlate and roll_fix is not None:
        fix_by_roll(takes, roll_fix)
    return takes


def fix_by_roll(takes, strong: float = 10.0, roll_chars: int = 6) -> list[str]:
    """Weak links with a big jump take the median offset of their camera roll. Returns a log."""
    roll = collections.defaultdict(list)
    for t in takes:
        for c in t["cams"]:
            if c["sharp"] >= strong:
                roll[c["clip"][:roll_chars]].append(c["offset_frames"])
    log = []
    for t in takes:
        for c in t["cams"]:
            if c["sharp"] < strong and abs(c["offset_frames"]) > 2 and roll[c["clip"][:roll_chars]]:
                new = round(statistics.median(roll[c["clip"][:roll_chars]]))
                log.append(f"{t['name']} {c['clip'][:10]} {c['offset_frames']:+d}f q={c['sharp']} -> {new:+d}f (by roll)")
                c.update(offset_frames=new, trusted=False, fixed_by_roll=True)
    return log


def unmatched_cameras(rows, takes) -> list[str]:
    """Camera clips that share no time with any sound take (shot without the recorder, MOS)."""
    used = {c["file"] for t in takes for c in t["cams"]}
    return [v["file"] for v in cameras(rows) if v["file"] not in used]


def residual_ms(take: dict, cam: dict, start: int, length: int, x0: int) -> tuple[float, float]:
    """Sample-level lag (ms) and sharpness between the boom and the camera's own audio over the
    placed range (`start` camera frame, `length` frames, sound frame `x0`): what is left after the
    frame-rounded offset. A few ms is normal; it is why camera audio played together with the
    recorder sounds like a flanger."""
    boom = load_audio(take["sound"][0]["file"])
    camera = load_audio(camera_audio_path(cam["file"]))
    a = boom[max(0, int(x0 / FPS * SR)): int((x0 + length) / FPS * SR)]
    v = camera[int((start + max(0, -x0)) / FPS * SR): int((start + length) / FPS * SR)]
    m = min(len(a), len(v))
    if m < SR:
        raise ValueError("less than a second to measure")
    lag, q = xcorr_offset(a[:m], v[:m], SR // 2)
    return lag / SR * 1000, q


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="sound-to-picture sync of a shoot day")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s1 = sub.add_parser("scan", help="read MXF headers into meta.json")
    s1.add_argument("dirs", nargs="+")
    s1.add_argument("--out", required=True)
    s2 = sub.add_parser("sync", help="meta.json -> takes.json")
    s2.add_argument("meta")
    s2.add_argument("--out", required=True)
    s2.add_argument("--scenes", help="comma list like 1-18,1-19 (episode-scene); default all")
    s2.add_argument("--no-correlate", action="store_true", help="timecode only")
    a = ap.parse_args()
    if a.cmd == "scan":
        rows = scan_mxf(a.dirs, progress=lambda s: print(s, file=sys.stderr))
        Path(a.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        print(len(rows), "files")
    else:
        rows = json.loads(Path(a.meta).read_text(encoding="utf-8"))
        takes = build_takes(rows, scenes=a.scenes.split(",") if a.scenes else None, correlate=not a.no_correlate)
        Path(a.out).write_text(json.dumps(takes, ensure_ascii=False, indent=1), encoding="utf-8")
        miss = unmatched_cameras(rows, takes)
        print(f"{len(takes)} takes; cameras without sound: {len(miss)}")
