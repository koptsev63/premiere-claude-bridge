"""Tests for the Avid bin builder (masters with columns, sync clips, group clips).

Runs on synthetic headers; needs `pyavb` (skipped without it). The bin is built, written,
read back and checked for naming, user columns, sync arithmetic and group wiring.

Run:  python -m core.tests.test_avb_build
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from core.avb_build import bin_name_for, slate_parts, sync_names, tcf

_p = _f = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _p, _f
    if ok:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


def umid(kind: str, n: int) -> str:
    return "0x060A2B34010101050101" + kind + "13000000" + f"{n:034X}"


def audio_hdr(path_i: int, take: str, scene: str, tn: str, frames: int, tc: str, chans):
    tracks = []
    for i, label in enumerate(chans):
        t = {"kind": "audio", "own": i == 0, "file_umid": umid("0F", 100 * path_i + i), "track_name": f"A{i + 1}"}
        if i == 0:
            t.update(reel_umid=umid("0E", path_i), reel_name="260901", tc=tc, name=None, sr=48000, bits=24,
                     samples=frames * 2000)
        tracks.append(t)
    meta = {"comment_Scene": scene, "comment_Take": tn, "comment_TapeID": f"{scene}-{tn}.WAV",
            **{f"comment_TRK{i + 1}": c for i, c in enumerate(chans)}, "comment_Circled": "no"}
    return {"material_umid": umid("0A", path_i), "material_name": take, "meta": meta, "tracks": tracks}


def cam_hdr(i: int, name: str, frames: int, tc: str):
    return {"material_umid": umid("0B", i), "material_name": name, "meta": {"comment_Comment": "Created by Resolve"},
            "tracks": [{"kind": "video", "own": True, "file_umid": umid("0C", i), "track_name": "v1",
                        "reel_umid": umid("0D", i), "reel_name": name, "tc": tc, "name": name, "frames": frames,
                        "w": 1920, "h": 1080, "codec": "dnxhd", "rate": "24/1"},
                       {"kind": "audio", "own": False, "file_umid": umid("0C", 10 + i), "track_name": "A1"}]}


def test_names() -> None:
    check("slate parts", slate_parts("1-18-19-4") == ("1", ["18", "19"], "4"))
    takes = [{"scene": "1-18-19-4", "take": "003", "name": "1-18-19-4/003"}, {"scene": "1-20-1", "take": "007", "name": "1-20-1/007"}]
    check("bin name joins the scenes", bin_name_for(takes) == "01-18.19.20")
    st = sync_names(takes, "01-18.19.20")
    check("sync stem", st["1-18-19-4/003"] == "01-18.19.20-04/03", st["1-18-19-4/003"])
    clash = [{"scene": "1-18-4", "take": "001", "name": "1-18-4/001"}, {"scene": "1-18-19-4", "take": "001", "name": "1-18-19-4/001"}]
    s2 = sync_names(clash, "01-18.19")
    check("a name clash falls back to the full scene key", s2["1-18-4/001"] != s2["1-18-19-4/001"])


def test_build() -> None:
    try:
        import avb  # noqa: F401
    except ImportError:
        print("  skip build tests: pyavb not installed")
        return
    from core.avb_build import build_bin, fingerprint

    t0 = tcf("14:00:00:00")
    hdr = {
        "/V/s/A1.mxf": audio_hdr(1, "1-18-19-4/003", "1-18-19-4", "003", 480, "14:00:00:00", ["Boom1", "Viking"]),
        "/V/s/A2.mxf": audio_hdr(1, "1-18-19-4/003", "1-18-19-4", "003", 480, "14:00:00:00", ["Boom1", "Viking"]),
        "/V/Disk/c/A_1.mxf": cam_hdr(1, "A_1", 300, "14:00:05:00"),
        "/V/Disk/c/B_1.mxf": cam_hdr(2, "B_1", 300, "13:59:55:00"),
    }
    takes = [{"name": "1-18-19-4/003", "scene": "1-18-19-4", "take": "003", "tc_frames": t0, "len_frames": 480,
              "sound": [{"file": "/V/s/A1.mxf"}],
              "cams": [{"file": "/V/Disk/c/A_1.mxf", "clip": "A_1", "cam": "A", "f0": t0 + 120, "f1": t0 + 420,
                        "offset_frames": 2, "trusted": True},
                       {"file": "/V/Disk/c/B_1.mxf", "clip": "B_1", "cam": "B", "f0": t0 - 240, "f1": t0 + 60,
                        "offset_frames": 0, "trusted": False, "sharp": 3.0}]}]
    with tempfile.TemporaryDirectory() as d:
        out = str(Path(d) / "o.avb")
        r = build_bin(takes, hdr, out)
        check("2 syncs, 1 group, 3 masters", (r["sync"], r["groups"], r["masters"]) == (2, 1, 3), str(r))
        import avb
        c = avb.open(out).content
        byname = {m.name: m for m in c.mobs if m.name}
        snd = byname["1-18-19-4/003"]
        user = snd.attributes["_USER"]
        check("sound master carries the Avid columns", user["Scene"] == "1-18-19-4" and user["TRK2"] == "Viking"
              and user["TapeID"] == "1-18-19-4-003.WAV", str(dict(user)))
        sa = byname["01-18.19-04/03a.sync.01"]
        sb = byname["01-18.19-04/03b.sync.01"]
        # camera A starts 120 f after the sound, offset 2: sound frame 118, cut from the camera head
        a_snd = [t for t in sa.tracks if t.media_kind == "sound"][0].component
        check("sync A: sound starts at f0 - offset - tc", a_snd.start_time == 118, str(a_snd.start_time))
        check("sync A is cut by the shorter of the two", sa.length == min(300, 480 - 118), str(sa.length))
        # camera B starts 240 f before the sound: picture trimmed by 240, sound from 0
        b_pic = [t for t in sb.tracks if t.media_kind == "picture"][0].component
        b_snd = [t for t in sb.tracks if t.media_kind == "sound"][0].component
        check("sync B: head trimmed instead of a negative offset", b_pic.start_time == 240 and b_snd.start_time == 0,
              f"{b_pic.start_time} {b_snd.start_time}")
        check("untrusted offset is said in the clip", "inconclusive" in sb.attributes["_USER"]["Comments"])
        grp = byname["01-18.19-04/03.Grp.01"]
        sels = [t.component for t in grp.tracks]
        check("group: a picture selector with a pick per camera, sound selectors per channel + camera",
              len(sels) == 3 and len(sels[0].tracks) == 2 and len(sels[1].tracks) == 3, str([len(s.tracks) for s in sels]))
        check("usage codes", (grp.usage_code, sa.usage_code, snd.usage_code) == (4, 2, 0))
        fp = fingerprint(out)
        check("hidden group composition exists", fp["hidden"]["n"] == 1)


def main() -> int:
    test_names()
    test_build()
    print(f"\navb_build: {_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
