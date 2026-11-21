"""Defects a viewer sees - freeze, repeated shot, stutter, squeeze, cut-off audio.

The owner of the edits rejected videos that passed every other measurement in
this toolkit. His recurring complaints that a machine can catch:

1. Freeze: a live shot stops moving. Usually a timeline segment longer than
   its source clip, so the NLE holds (clones) the last frame.
2. The same shot used twice.
3. Stutter: 24 fps (or slower) footage conformed into a 30 / 60 fps timeline,
   or generations with duplicated frames. Motion "hiccups".
4. Squeezed picture: non-square pixels (SAR not 1:1).
5. Audio that ends abruptly at the end of the file (no fade, a click).

Method:

- One ffmpeg decode runs freezedetect and the scene filter of
  `core.highlights.scene_cuts()` (same filter, same threshold) on full frames
  and pipes every frame at `GRAY_WIDTH` px gray to numpy; above
  `GRAY_CACHE_MAX_MB` OpenCV seeks per shot instead.
- Cuts: given, or the scene filter hits; hits closer than
  `FLASH_MERGE_S` are one event (a flash), the first hit is kept. Shots are
  the spans between cuts.
- freeze: ffmpeg `freezedetect` (noise `FREEZE_NOISE`, min `FREEZE_MIN_S`).
  The middle frame of a frozen span is tested for a card (title on black, logo
  on a flat colour); a frozen card is a "note", frozen live picture is a FAIL.
  A frozen span that runs into a cut or the end of the file (its end within
  `CLONE_TAIL_S`) is the cloned-last-frame case and says so.
- repeat: a 64 bit dHash of the middle frame of every shot; two shots with a
  Hamming distance <= `REPEAT_MAX_DIST` are WARN. Cards and shots shorter
  than `REPEAT_MIN_SHOT_S` are skipped. Adjacent shots count (jump cut on the
  same frame).
- stutter: up to `STUTTER_FRAMES` consecutive frames from the middle of each
  shot longer than `STUTTER_MIN_SHOT_S`, at `STUTTER_WIDTH` px, gray. A pair
  whose mean absolute difference is below `DUP_DIFF` is a duplicate, counted
  only when the shot moves (its `LIVE_PCT` percentile pair difference above
  `LIVE_DIFF`; a percentile, not the median, because at 50-70 % duplicates
  the median pair IS a duplicate). A share of duplicates inside
  `STUTTER_SHARE` = WARN; when at least `STUTTER_FILE_SHARE` of the measured
  shots stutter, one FAIL for the file.
- geometry: ffprobe sample_aspect_ratio not 1:1 = FAIL; avg_frame_rate vs
  r_frame_rate off by more than `VFR_TOL` = WARN.
- audio end: RMS of the last `AUDIO_TAIL_S` above `AUDIO_TAIL_DBFS` = WARN.

Honest boundaries - the numbers are engineering defaults, checked on synthetic
clips and a handful of real edits, not calibrated on a corpus:

- freezedetect compares whole frames. A live shot with only a tiny moving part
  (a blinking light, a slow drift under the noise floor) reads as frozen; an
  intentional still (a photo held without a move) is a FAIL by design, the
  owner rejects those too. Heavy grain or noise hides a freeze.
- The card test is a tone histogram, not a text detector: a live shot of a
  flat wall or sky can pass for a card, and a title over moving footage is
  live picture.
- dHash on one middle frame: two different takes of the same static setup look
  the same (true for the viewer as well); the same clip re-graded or re-framed
  far enough is not caught.
- Stutter needs motion. A shot that barely moves cannot show duplicates; a
  shot that is half still and half moving can show false ones. Frame blending
  (optical-flow retime) does not duplicate frames and is not caught here.
- Scene detection misses dissolves and soft cuts; a missed cut makes two shots
  one, which can hide a repeat but never invents a freeze.
- Audio end measures level only; a deliberate hard out on a beat is the
  editor's call.

```bash
python3 -m core.defects VIDEO.mp4
python3 -m core.defects VIDEO.mp4 --cuts 1.2,3.4 --json
```
Exit code: 1 if any FAIL, 0 otherwise, 2 if the file cannot be read.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

import numpy as np

# --------------------------------------------------------------------------- #
# Constants (every threshold the verdict depends on)
# --------------------------------------------------------------------------- #

STANDARD_ASPECTS = (16 / 9, 4 / 3, 9 / 16, 1.0, 4 / 5, 3 / 4)   # display aspects an anamorphic file may target
ANAMORPHIC_TOL = 0.02         # relative distance to a standard aspect that still counts as it
FLASH_MERGE_S = 0.15          # s: scene hits closer than this (under 4 frames) are one event (a flash); same as core.highlights.BURST_GAP
EDGE_GUARD_S = 0.05           # s: cuts closer than this to start / end are dropped

FREEZE_NOISE = 0.003          # freezedetect n: frame difference below this = no change
FREEZE_MIN_S = 0.4            # freezedetect d: a still span at least this long = freeze
CLONE_TAIL_S = 0.15           # s: a freeze ending this close to a cut / EOF = cloned last frame

CARD_DARK_MEDIAN = 12.0       # median gray below this = title on black
CARD_FLAT_TOL = 6             # gray levels around the most common level ...
CARD_FLAT_SHARE = 0.60        # ... holding at least this share of pixels = flat-colour card
CARD_WIDTH = 160              # px, analysis width for the card test

HASH_SIZE = 8                 # dHash 8x8 = 64 bit (gray, resize to 9x8)
REPEAT_MAX_DIST = 6           # Hamming distance <= this = the same shot
REPEAT_MIN_SHOT_S = 0.3       # shots shorter than this are not hashed

STUTTER_MIN_SHOT_S = 0.6      # shots shorter than this are not measured
STUTTER_FRAMES = 24           # consecutive frames read from the middle of a shot
STUTTER_WIDTH = 160           # px, analysis width
DUP_DIFF = 0.15               # mean abs gray difference below this = duplicated frame (spec 0.3;
                              # real duplicates measured 0.00-0.11, a slow real shot 0.27-0.31)
LIVE_DIFF = 1.0               # the shot moves when its LIVE_PCT pair difference is above this
LIVE_PCT = 90.0               # percentile of pair differences used for "the shot moves"
STUTTER_SHARE = (0.10, 0.70)  # duplicate share inside this range = stutter
STUTTER_FILE_SHARE = 1 / 3    # this share of measured shots stutter = one FAIL for the file

VFR_TOL = 0.01                # avg vs r_frame_rate relative difference above this = VFR

AUDIO_TAIL_S = 0.05           # s measured at the end of the audio
AUDIO_TAIL_DBFS = -45.0       # RMS above this in the tail = cut off
AUDIO_RATE = 48000            # Hz, decode rate for the tail

GRAY_WIDTH = 160              # px: the one decode pass also pipes every frame at this width, gray
GRAY_CACHE_MAX_MB = 300       # above this the frames are not cached; OpenCV seeks per shot instead

FAIL, WARN, NOTE = "fail", "warn", "note"


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #


@dataclass
class Finding:
    kind: str                 # freeze, repeat, stutter, geometry, audio
    level: str                # fail, warn, note
    start: float
    end: float
    message: str


@dataclass
class Report:
    path: str
    width: int = 0
    height: int = 0
    sar: str = "1:1"
    dar: float = 0.0
    fps: float = 0.0
    duration: float = 0.0
    has_audio: bool = False
    cuts: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    elapsed_s: float = 0.0

    @property
    def fails(self) -> list:
        return [f for f in self.findings if f.level == FAIL]

    @property
    def warns(self) -> list:
        return [f for f in self.findings if f.level == WARN]

    @property
    def passed(self) -> bool:
        return not self.fails


# --------------------------------------------------------------------------- #
# Probe
# --------------------------------------------------------------------------- #


def _rate(s: str) -> float:
    try:
        n, d = s.split("/")
        return float(n) / float(d) if float(d) else 0.0
    except (ValueError, AttributeError):
        return 0.0


def probe(path: str) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {r.stderr.strip()[:200]}")
    return json.loads(r.stdout or "{}")


# --------------------------------------------------------------------------- #
# Frame helpers (numpy / OpenCV)
# --------------------------------------------------------------------------- #


def _gray_small(frame: np.ndarray, width: int) -> np.ndarray:
    import cv2
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    h, w = g.shape
    if w != width:
        g = cv2.resize(g, (width, max(1, round(h * width / w))), interpolation=cv2.INTER_AREA)
    return g


def is_card(gray: np.ndarray) -> bool:
    """A frame whose background is one flat tone: title on black or logo on a flat colour."""
    if float(np.median(gray)) < CARD_DARK_MEDIAN:
        return True
    hist = np.bincount(gray.ravel(), minlength=256)
    mode = int(hist.argmax())
    lo, hi = max(0, mode - CARD_FLAT_TOL), min(255, mode + CARD_FLAT_TOL)
    return hist[lo:hi + 1].sum() / gray.size >= CARD_FLAT_SHARE


def dhash(gray: np.ndarray) -> int:
    import cv2
    small = cv2.resize(gray, (HASH_SIZE + 1, HASH_SIZE), interpolation=cv2.INTER_AREA).astype(np.int16)
    bits = (small[:, 1:] > small[:, :-1]).ravel()
    return int(sum(1 << i for i, b in enumerate(bits) if b))


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def pair_diffs(frames: list) -> np.ndarray:
    return np.array([float(np.mean(np.abs(frames[i + 1].astype(np.int16) - frames[i].astype(np.int16))))
                     for i in range(len(frames) - 1)])


def stutter_share(diffs: np.ndarray) -> Optional[float]:
    """Duplicate share of a moving shot, None when the shot does not move."""
    if len(diffs) < 2 or float(np.percentile(diffs, LIVE_PCT)) <= LIVE_DIFF:
        return None
    return float(np.mean(diffs < DUP_DIFF))


class _Reader:
    def __init__(self, path: str):
        import cv2
        self.cap = cv2.VideoCapture(path)
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 25.0

    def frames(self, t: float, n: int, width: int) -> list:
        import cv2
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(round(t * self.fps))))
        out = []
        for _ in range(n):
            ok, fr = self.cap.read()
            if not ok:
                break
            out.append(_gray_small(fr, width))
        return out

    def close(self) -> None:
        self.cap.release()


class _CacheReader:
    """Frames already decoded by ffmpeg at GRAY_WIDTH, indexed by time (constant frame rate)."""

    def __init__(self, frames: np.ndarray, fps: float):
        self.arr, self.fps = frames, fps or 25.0

    def frames(self, t: float, n: int, width: int) -> list:
        i = max(0, int(round(t * self.fps)))
        out = list(self.arr[i:i + n])
        return [f if f.shape[1] == width else _gray_small(f, width) for f in out]

    def close(self) -> None:
        self.arr = None


# --------------------------------------------------------------------------- #
# Cuts
# --------------------------------------------------------------------------- #


def merge_cuts(cuts: list, duration: float) -> list:
    out: list = []
    for c in sorted(cuts):
        if c < EDGE_GUARD_S or (duration and c > duration - EDGE_GUARD_S):
            continue
        if out and c - out[-1] < FLASH_MERGE_S:
            continue
        out.append(round(c, 3))
    return out


def shots_from_cuts(cuts: list, duration: float) -> list:
    b = [0.0] + list(cuts) + [duration]
    return [(b[i], b[i + 1]) for i in range(len(b) - 1) if b[i + 1] > b[i]]


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #


def _pass_cmd(path: str, scene: bool, gray_h: int) -> list:
    """One decode: freezedetect (+ the scene filter of core.highlights) on full frames, small gray to stdout."""
    from core.highlights import DEF_SCENE_THRESH
    meas = f"freezedetect=n={FREEZE_NOISE}:d={FREEZE_MIN_S}"
    if scene:
        meas += f",select='gt(scene,{DEF_SCENE_THRESH})',showinfo"
    if not gray_h:
        return ["ffmpeg", "-nostdin", "-hide_banner", "-i", path, "-map", "0:v:0",
                "-vf", meas, "-an", "-f", "null", "-"]
    graph = f"[0:v:0]split[m][g];[m]{meas},nullsink;[g]scale={GRAY_WIDTH}:{gray_h}:flags=area,format=gray[o]"
    return ["ffmpeg", "-nostdin", "-hide_banner", "-i", path, "-filter_complex", graph,
            "-map", "[o]", "-an", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]


def parse_scene(stderr: str) -> list:
    """Same parse as core.highlights.scene_cuts (showinfo pts_time of selected frames)."""
    return sorted({round(float(m), 3) for m in re.findall(r"pts_time:([0-9.]+)", stderr)})


def parse_freezes(stderr: str, duration: float) -> list:
    starts = [float(x) for x in re.findall(r"freeze_start:\s*([0-9.]+)", stderr)]
    ends = [float(x) for x in re.findall(r"freeze_end:\s*([0-9.]+)", stderr)]
    return [(s, ends[i] if i < len(ends) else duration) for i, s in enumerate(starts)]


def check_freezes(spans: list, cuts: list, duration: float, reader: _Reader) -> list:
    out = []
    bounds = list(cuts) + [duration]
    for s, e in spans:
        fr = reader.frames((s + e) / 2, 1, CARD_WIDTH)
        card = bool(fr) and is_card(fr[0])
        span = f"{s:.2f}-{e:.2f} s ({e - s:.2f} s)"
        if card:
            out.append(Finding("freeze", NOTE, s, e, f"still card {span}: title / logo hold, fine"))
            continue
        clone = any(abs(b - e) <= CLONE_TAIL_S for b in bounds)
        msg = f"picture freezes {span} on live picture"
        if clone:
            msg += (", runs into the cut: segment is longer than its source clip? "
                    "trim it or use a longer source")
        else:
            msg += ": replace with moving footage or add a move"
        out.append(Finding("freeze", FAIL, s, e, msg))
    return out


def check_shots(shots: list, reader: _Reader) -> list:
    """Read each shot once: middle-frame hash for repeat, consecutive frames for stutter."""
    hashes = []          # (start, end, hash)
    stutters = []        # (start, end, dups, pairs)
    measured = 0
    for s, e in shots:
        d = e - s
        if d < REPEAT_MIN_SHOT_S:
            continue
        mid = (s + e) / 2
        if d > STUTTER_MIN_SHOT_S:
            n = min(STUTTER_FRAMES, max(2, int(d * reader.fps) - 2))
            frs = reader.frames(mid - n / 2 / reader.fps, n, STUTTER_WIDTH)
        else:
            frs = reader.frames(mid, 1, STUTTER_WIDTH)
        if not frs:
            continue
        m = frs[len(frs) // 2]
        if not is_card(m):
            hashes.append((s, e, dhash(m)))
        if d > STUTTER_MIN_SHOT_S and len(frs) >= 3:
            diffs = pair_diffs(frs)
            share = stutter_share(diffs)
            if share is not None:
                measured += 1
                if STUTTER_SHARE[0] <= share <= STUTTER_SHARE[1]:
                    stutters.append((s, e, int(np.sum(diffs < DUP_DIFF)), len(diffs)))
    out = []
    for i in range(len(hashes)):
        for j in range(i + 1, len(hashes)):
            dist = hamming(hashes[i][2], hashes[j][2])
            if dist <= REPEAT_MAX_DIST:
                a, b = hashes[i], hashes[j]
                out.append(Finding("repeat", WARN, a[0], b[1],
                                   f"shot at {a[0]:.2f} s looks the same as shot at {b[0]:.2f} s "
                                   f"(hash distance {dist}/64): replace one of them"))
    for s, e, dups, pairs in stutters:
        out.append(Finding("stutter", WARN, s, e,
                           f"{dups} of {pairs} frames repeat in shot {s:.2f}-{e:.2f} s: source frame rate "
                           f"below the timeline's; conform the clip or interpolate frames"))
    if measured and len(stutters) / measured >= STUTTER_FILE_SHARE:
        out.append(Finding("stutter", FAIL, 0.0, shots[-1][1] if shots else 0.0,
                           f"{len(stutters)} of {measured} moving shots stutter: the whole edit judders; "
                           f"render at the source frame rate or retime the sources"))
    return out


def check_geometry(vs: dict, rep: Report) -> list:
    out = []
    sar = vs.get("sample_aspect_ratio") or "N/A"
    rep.sar = sar
    if sar not in ("1:1", "N/A", "0:1"):
        # Anamorphic storage (HDV 1440x1080 at SAR 4:3 = 16:9 on screen) is a real delivery format:
        # it plays right wherever the SAR is honoured. A display aspect that is no standard frame
        # means the SAR itself is wrong and the picture is squeezed everywhere.
        dar = rep.dar or 0.0
        standard = any(abs(dar - a) / a < ANAMORPHIC_TOL for a in STANDARD_ASPECTS)
        if standard:
            out.append(Finding("geometry", WARN, 0.0, rep.duration,
                               f"anamorphic pixels (SAR {sar}, shown as {dar:.2f}:1): fine where the player honours "
                               f"it, squeezed on platforms that ignore SAR; for web render square pixels "
                               f"(scale to the display size, then setsar=1)"))
        else:
            out.append(Finding("geometry", FAIL, 0.0, rep.duration,
                               f"pixels are not square (SAR {sar}) and the display aspect {dar:.2f}:1 is no standard "
                               f"frame: the picture is squeezed; render with square pixels "
                               f"(scale to the display size, then setsar=1)"))
    avg, rf = _rate(vs.get("avg_frame_rate", "")), _rate(vs.get("r_frame_rate", ""))
    if avg and rf and abs(avg - rf) / rf > VFR_TOL:
        out.append(Finding("geometry", WARN, 0.0, rep.duration,
                           f"variable frame rate (avg {avg:.3f} vs nominal {rf:.3f} fps): "
                           f"render at a constant frame rate"))
    return out


def audio_tail_dbfs(path: str) -> Optional[float]:
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-sseof", str(-max(0.5, 4 * AUDIO_TAIL_S)), "-i", path,
         "-map", "0:a:0", "-ac", "1", "-ar", str(AUDIO_RATE), "-f", "s16le", "-"],
        capture_output=True,
    )
    pcm = np.frombuffer(r.stdout, dtype=np.int16)
    n = int(AUDIO_TAIL_S * AUDIO_RATE)
    if len(pcm) < n:
        return None
    tail = pcm[-n:].astype(np.float64) / 32768.0
    rms = math.sqrt(float(np.mean(tail ** 2)))
    return 20 * math.log10(rms) if rms > 0 else -120.0


def check_audio(path: str, rep: Report) -> list:
    if not rep.has_audio:
        return [Finding("audio", NOTE, 0.0, 0.0, "no audio stream")]
    db = audio_tail_dbfs(path)
    if db is None:
        return [Finding("audio", NOTE, 0.0, 0.0, "audio tail could not be decoded")]
    if db > AUDIO_TAIL_DBFS:
        return [Finding("audio", WARN, max(0.0, rep.duration - AUDIO_TAIL_S), rep.duration,
                        f"audio is cut off at the end ({db:.1f} dBFS in the last "
                        f"{int(AUDIO_TAIL_S * 1000)} ms): add a fade")]
    return []


# --------------------------------------------------------------------------- #
# Entry
# --------------------------------------------------------------------------- #


def check(path: str, cuts: Optional[list] = None) -> Report:
    t0 = time.monotonic()
    info = probe(path)
    streams = info.get("streams", [])
    vs = next((s for s in streams if s.get("codec_type") == "video"), None)
    if vs is None:
        raise RuntimeError("no video stream")
    rep = Report(path=path)
    rep.width, rep.height = int(vs.get("width", 0)), int(vs.get("height", 0))
    rep.fps = round(_rate(vs.get("avg_frame_rate", "")) or _rate(vs.get("r_frame_rate", "")), 3)
    rep.duration = float(vs.get("duration") or info.get("format", {}).get("duration") or 0.0)
    rep.has_audio = any(s.get("codec_type") == "audio" for s in streams)

    rep.sar = vs.get("sample_aspect_ratio") or "N/A"
    sar = rep.sar if rep.sar not in ("N/A", "0:1") else "1:1"
    sn, sd = (int(x) for x in sar.split(":"))
    rep.dar = round(rep.width * sn / sd / rep.height, 4) if rep.height and sd else 0.0
    findings = check_geometry(vs, rep)

    # One ffmpeg decode does freezedetect, scene detection and a small gray copy of every frame.
    gray_h = 2 * max(1, round(rep.height * GRAY_WIDTH / max(1, rep.width) / 2))
    n_frames = rep.duration * (rep.fps or 25.0)
    cache = n_frames * GRAY_WIDTH * gray_h / 1e6 <= GRAY_CACHE_MAX_MB
    r = subprocess.run(_pass_cmd(path, cuts is None, gray_h if cache else 0), capture_output=True)
    err = r.stderr.decode("utf-8", "replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {err.strip()[-200:]}")
    if cuts is None:
        cuts = parse_scene(err)
    rep.cuts = merge_cuts(cuts, rep.duration)
    if cache:
        buf = np.frombuffer(r.stdout, dtype=np.uint8)
        k = len(buf) // (GRAY_WIDTH * gray_h)
        reader = _CacheReader(buf[:k * GRAY_WIDTH * gray_h].reshape(k, gray_h, GRAY_WIDTH), rep.fps)
    else:
        reader = _Reader(path)
    try:
        findings += check_freezes(parse_freezes(err, rep.duration), rep.cuts, rep.duration, reader)
        findings += check_shots(shots_from_cuts(rep.cuts, rep.duration), reader)
    finally:
        reader.close()
    findings += check_audio(path, rep)
    order = {FAIL: 0, WARN: 1, NOTE: 2}
    rep.findings = sorted(findings, key=lambda f: (order[f.level], f.start))
    rep.elapsed_s = round(time.monotonic() - t0, 2)
    return rep


def format_report(rep: Report) -> str:
    lines = [
        f"defects: {rep.path}",
        f"  {rep.width}x{rep.height}  SAR {rep.sar}  DAR {rep.dar:.3f}  {rep.fps:g} fps  "
        f"{rep.duration:.2f} s  audio {'yes' if rep.has_audio else 'no'}  "
        f"{len(rep.cuts)} cuts  ({rep.elapsed_s:.1f} s)",
    ]
    if not rep.findings:
        lines.append("  no findings")
    for f in rep.findings:
        lines.append(f"  {f.level.upper():4}  {f.kind:8} {f.start:7.2f}-{f.end:<7.2f} {f.message}")
    lines.append(f"  verdict: {'FAIL' if rep.fails else 'PASS'} "
                 f"({len(rep.fails)} fail, {len(rep.warns)} warn)")
    return "\n".join(lines)


def to_json(rep: Report) -> str:
    d = asdict(rep)
    d["passed"] = rep.passed
    return json.dumps(d, ensure_ascii=False, indent=2)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="core.defects", description="defects a viewer sees")
    ap.add_argument("video")
    ap.add_argument("--cuts", help="comma-separated cut times in seconds (default: scene detect)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    # --cuts "" is an explicit empty list (the caller already looked and found a single shot)
    cuts = [float(x) for x in a.cuts.split(",") if x.strip()] if a.cuts is not None else None
    try:
        rep = check(a.video, cuts)
    except (RuntimeError, OSError, ValueError) as e:
        print(f"defects: cannot read {a.video}: {e}", file=sys.stderr)
        return 2
    print(to_json(rep) if a.json else format_report(rep))
    return 1 if rep.fails else 0


if __name__ == "__main__":
    sys.exit(main())
