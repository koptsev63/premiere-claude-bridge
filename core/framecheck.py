"""Stepped picture check of a cut, made of contact sheets and a numeric pass.

The steps go from cheap to dear and each is run only where it can pay:

0. `motion_profile`   numbers, nothing looked at: where the picture changes (movement, someone
                      enters, a light jump, black). Says where it is worth looking.
1. `overview_sheets`  every camera of every take: 8 frames spread over the take, one row per
                      clip (about one frame per 15 s on a 2-minute take). Catches the wrong
                      scene and junk in frame.
2. `cut_sheets`       around a cut: 4 frames in the last second before it and 4 in the first
                      second after (a step of a third of a second, 3 fps). Is the position,
                      gaze and prop the same on both sides?
   `edge_sheet`       in, in + 1 s, out - 0.5 s of a shot: no slate or crew at the edges.
3. Inside a shot only where step 0 found movement: a frame every half second (2 fps).
4. Continuity log (written by hand/Claude from the sheets): per shot who stands where at the
   in and at the out; compared across cuts and against the story.
5. A single full-size frame for a disputed place.

Only for SEAMLESS editing. When the cut is fragmented (montage, jump cuts, cutaways, short
pieces) the sheets around cuts say nothing, so `plan_cuts(..., seamless=False)` returns nothing
and only steps 0, 1 and the edges are worth running. Even when seamless, only cuts that are meant
to match are checked: two cameras of the same take, or one action continuing; a cutaway from
another take is a deliberate break and is skipped.

EDL rows (JSON): ``[sound_take, from_s, to_s, picture_take, camera, picture_from_s|null, note]``;
``picture_from_s`` null means the picture is the take's own, in sync with the sound.
Frames come from `takes.json` (`core.dailies_sync`): camera frame
``tc_frames + s*fps - f0 + offset_frames``. Needs PyAV and Pillow; step 0 needs numpy.
"""

from __future__ import annotations

import json
import os
import sys

FPS = 24
BEFORE = (1.0, 0.67, 0.33, 0.05)        # seconds before the cut
AFTER = (0.0, 0.33, 0.67, 1.0)          # seconds after it
TW, TH = 240, 100


# ---------------------------------------------------------------- planning

def _pic_pos(row, fallback_key="from"):
    """(picture seconds at the start, at the end) in the take's own time."""
    a0, a1, v0 = row[1], row[2], row[5]
    start = a0 if v0 is None else v0
    return start, start + (a1 - a0)


def plan_cuts(edl, seamless: bool) -> list[int]:
    """Indexes i of the cuts between row i and row i+1 worth the dense sheet.

    Nothing unless `seamless`. A cut matches when both sides are the take's own picture in sync
    (no inserted shot from another take) and it is the same take on both sides."""
    if not seamless:
        return []
    out = []
    for i in range(len(edl) - 1):
        a, b = edl[i], edl[i + 1]
        if a[5] is None and b[5] is None and a[3] == b[3]:
            out.append(i)
    return out


def steps_for(tiers_run: bool = True) -> list[str]:
    return ["motion_profile", "overview_sheets", "edge_sheet"] + (["cut_sheets", "continuity log"] if tiers_run else [])


# ----------------------------------------------------------------- frames

def _camera(takes, take, cam):
    t = takes[take]
    c = next((c for c in t["cams"] if c["cam"] == cam and c["overlap"] >= 0.1), None)
    if c is None:
        raise KeyError(f"{take}: no camera {cam}")
    return t, c


def grab(takes, take, cam, sec, size=(TW, TH), fps=FPS):
    """The frame of `cam` at `sec` seconds of the take's sound timeline, resized and cropped."""
    import av
    from PIL import Image
    t, c = _camera(takes, take, cam)
    f = t["tc_frames"] + round(sec * fps) - c["f0"] + c["offset_frames"]
    if not 0 <= f < c["f1"] - c["f0"]:
        return Image.new("RGB", size, "gray")
    cont = av.open(c["file"])
    st = cont.streams.video[0]
    cont.seek(int(f / fps / st.time_base), stream=st)
    img = None
    for fr in cont.decode(st):
        if fr.pts * st.time_base * fps >= f - 1:
            img = fr.to_image()
            break
    cont.close()
    if img is None:
        return Image.new("RGB", size, "gray")
    w, h = size
    img = img.resize((w, int(w * img.height / img.width)))
    top = max(0, (img.height - h) // 2)
    return img.crop((0, top, w, top + h))


def _sheet(rows, path, per_sheet):
    from PIL import Image
    paths = []
    for k in range(0, len(rows), per_sheet):
        part = rows[k:k + per_sheet]
        sh = Image.new("RGB", (part[0].width, sum(r.height for r in part)))
        y = 0
        for r in part:
            sh.paste(r, (0, y))
            y += r.height
        p = path.format(n=k // per_sheet + 1)
        sh.save(p, quality=75)
        paths.append(p)
    return paths


def overview_sheets(takes_list, out_dir, n=8, per_sheet=10, min_overlap=0.1):
    """Step 1. One row per camera clip, `n` frames across the part of the take it covers."""
    from PIL import Image, ImageDraw
    os.makedirs(out_dir, exist_ok=True)
    takes = {t["name"]: t for t in takes_list}
    rows = []
    for t in takes_list:
        for c in t["cams"]:
            if c["overlap"] < min_overlap:
                continue
            lo = max(t["tc_frames"], c["f0"] - c["offset_frames"])
            hi = min(t["tc_frames"] + t["len_frames"], c["f1"] - c["offset_frames"])
            secs = [((lo + (hi - lo) * (k + 0.5) / n) - t["tc_frames"]) / FPS for k in range(n)]
            row = Image.new("RGB", (TW * n, TH + 16))
            for i, s in enumerate(secs):
                row.paste(grab(takes, t["name"], c["cam"], s), (TW * i, 16))
            ImageDraw.Draw(row).text((4, 2), f"{t['name']} cam {c['cam']} {c['clip'][:10]}  s=" + ", ".join(f"{s:.0f}" for s in secs), fill="white")
            rows.append(row)
    return _sheet(rows, os.path.join(out_dir, "ov{n:02d}.jpg"), per_sheet)


def cut_sheets(takes_list, edl, out_dir, seamless, per_sheet=6):
    """Step 2. Frames around the cuts `plan_cuts` selects (none unless `seamless`)."""
    from PIL import Image, ImageDraw
    os.makedirs(out_dir, exist_ok=True)
    cuts = plan_cuts(edl, seamless)
    if not cuts:
        return []
    takes = {t["name"]: t for t in takes_list}
    rows = []
    for i in cuts:
        a, b = edl[i], edl[i + 1]
        pa = _pic_pos(a)[1]
        pb = _pic_pos(b)[0]
        ims = [grab(takes, a[3], a[4], pa - d) for d in BEFORE] + [grab(takes, b[3], b[4], pb + d) for d in AFTER]
        row = Image.new("RGB", (TW * 8 + 8, TH + 16))
        for k, im in enumerate(ims):
            row.paste(im, (TW * k + (8 if k >= 4 else 0), 16))
        ImageDraw.Draw(row).text((4, 2), f"cut {i + 1}->{i + 2}: {a[3]} {a[4]}  |  {b[3]} {b[4]}", fill="white")
        rows.append(row)
    return _sheet(rows, os.path.join(out_dir, "cuts{n}.jpg"), per_sheet)


def edge_sheet(takes_list, pieces, out_path, tw=300, th=125):
    """Edges of chosen shots: `pieces` = [(take, in_s, out_s)]; every camera of the take."""
    from PIL import Image, ImageDraw
    takes = {t["name"]: t for t in takes_list}
    rows = []
    for name, s_in, s_out in pieces:
        for c in takes[name]["cams"]:
            if c["overlap"] < 0.1:
                continue
            row = Image.new("RGB", (tw * 3, th + 16))
            for i, s in enumerate((s_in, s_in + 1.0, s_out - 0.5)):
                row.paste(grab(takes, name, c["cam"], s, (tw, th)), (tw * i, 16))
            ImageDraw.Draw(row).text((4, 2), f"{name} {c['cam']}  in {s_in}  in+1  out {s_out}", fill="white")
            rows.append(row)
    if not rows:
        return None
    sh = Image.new("RGB", (tw * 3, sum(r.height for r in rows)))
    y = 0
    for r in rows:
        sh.paste(r, (0, y))
        y += r.height
    sh.save(out_path, quality=75)
    return out_path


# --------------------------------------------------------- step 0, numbers

def motion_profile(path, step=0.5, size=(64, 36), jump=25.0, black=12.0, motion=8.0):
    """Mean brightness and frame-to-frame change every `step` seconds of a clip.

    Returns ``[(second, brightness, change)]`` and a list of flags
    (``black``, ``light jump``, ``movement``) with seconds."""
    import av
    import numpy as np
    c = av.open(path)
    st = c.streams.video[0]
    rate = float(st.average_rate or FPS)
    every = max(1, round(step * rate))
    prev, prof, flags = None, [], []
    for i, fr in enumerate(c.decode(st)):
        if i % every:
            continue
        g = np.asarray(fr.to_image().convert("L").resize(size), dtype=np.float32)
        b = float(g.mean())
        d = float(np.abs(g - prev).mean()) if prev is not None else 0.0
        sec = round(i / rate, 2)
        prof.append((sec, round(b, 1), round(d, 1)))
        if b < black:
            flags.append(("black", sec))
        if prev is not None and abs(b - float(prev.mean())) > jump:
            flags.append(("light jump", sec))
        if d > motion:
            flags.append(("movement", sec))
        prev = g
    c.close()
    return prof, flags


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="stepped picture check")
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("overview")
    o.add_argument("takes")
    o.add_argument("out")
    c = sub.add_parser("cuts")
    c.add_argument("takes")
    c.add_argument("edl", help="EDL json, see the module doc")
    c.add_argument("out")
    c.add_argument("--seamless", action="store_true", help="without it no sheet is made")
    m = sub.add_parser("motion")
    m.add_argument("clip")
    a = ap.parse_args()
    if a.cmd == "overview":
        print(overview_sheets(json.load(open(a.takes, encoding="utf-8")), a.out))
    elif a.cmd == "cuts":
        res = cut_sheets(json.load(open(a.takes, encoding="utf-8")), json.load(open(a.edl, encoding="utf-8")), a.out, a.seamless)
        print(res or "no cuts to check" + ("" if a.seamless else " (not a seamless cut: use --seamless if it is)"))
    else:
        prof, flags = motion_profile(a.clip)
        print(len(prof), "samples; flags:", flags[:40])
