"""core.silence — kill dead air. Detect silence (ffmpeg `silencedetect`) and
keep only the speech-dense regions, so a rambling talking take becomes tight.

Honest boundary: this is loudness-gated, not semantic. It removes *pauses*
(gaps below a dB floor), not weak content — a long boring-but-loud sentence
survives. Pair it with the meaning pass (read the transcript, pick what
matters); this just stops dead air from bloating the cut. Tune `noise_db` to
the room: quiet field audio needs a lower floor (e.g. -35), a noisy room a
higher one.

Pure, unit-tested core (no ffmpeg):
  _parse_silencedetect(text)  — ffmpeg stderr -> [(start, end|None)]
  invert_to_speech(sils, ...) — silence intervals -> padded speech intervals
ffmpeg-backed:
  detect_silence(path, ...)   — run silencedetect on a file
  speech_segments(path, ...)  — speech intervals within an in/out window
  tighten_cutlist(cl, ...)    — replace every Cut with its speech sub-cuts,
                                repacked back-to-back; returns (Cutlist, stats)

CLI:  python -m core.silence CLIP [IN_SEC] [OUT_SEC] [--noise -30] [--min 0.5]
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import replace

from core.cutlist import Cut, Cutlist

DEF_NOISE_DB = -30.0      # dB floor below which audio counts as silence
DEF_MIN_SILENCE = 0.5     # a pause must last this long to be cut (seconds)
DEF_PAD = 0.10            # keep this much around speech so onsets aren't clipped
DEF_MIN_KEEP = 0.40      # drop speech islands shorter than this (seconds)


def _parse_silencedetect(text: str) -> list[tuple[float, float | None]]:
    """ffmpeg silencedetect stderr -> [(silence_start, silence_end_or_None)].
    A trailing None means silence ran to end-of-file."""
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", text)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*(-?[\d.]+)", text)]
    out: list[tuple[float, float | None]] = []
    for i, s in enumerate(starts):
        out.append((s, ends[i] if i < len(ends) else None))
    return out


def invert_to_speech(
    silences: list[tuple[float, float | None]],
    *,
    start: float,
    end: float,
    pad_s: float = DEF_PAD,
    min_keep_s: float = DEF_MIN_KEEP,
) -> list[tuple[float, float]]:
    """Complement of silence within [start, end] -> speech intervals, padded
    outward by pad_s (so word onsets/offsets aren't clipped), overlaps merged,
    islands shorter than min_keep_s dropped."""
    if end <= start:
        return []
    sil: list[tuple[float, float]] = []
    for s, e in silences:
        e2 = end if e is None else e
        s2, e2 = max(s, start), min(e2, end)
        if e2 > s2:
            sil.append((s2, e2))
    sil.sort()

    speech: list[list[float]] = []
    cur = start
    for s, e in sil:
        if s > cur:
            speech.append([cur, s])
        cur = max(cur, e)
    if cur < end:
        speech.append([cur, end])

    padded = [[max(start, s - pad_s), min(end, e + pad_s)] for s, e in speech]
    merged: list[list[float]] = []
    for seg in padded:
        if merged and seg[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], seg[1])
        else:
            merged.append(seg)
    return [(round(s, 3), round(e, 3)) for s, e in merged
            if e - s >= min_keep_s]


def _probe_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True)
    try:
        return float((r.stdout or "0").strip())
    except ValueError:
        return 0.0


def detect_silence(
    path: str,
    *,
    noise_db: float = DEF_NOISE_DB,
    min_silence_s: float = DEF_MIN_SILENCE,
) -> list[tuple[float, float | None]]:
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-i", str(path), "-af",
         f"silencedetect=noise={noise_db}dB:d={min_silence_s}",
         "-f", "null", "-"],
        capture_output=True, text=True)
    return _parse_silencedetect(r.stderr or "")


def speech_segments(
    path: str,
    *,
    in_s: float = 0.0,
    out_s: float | None = None,
    noise_db: float = DEF_NOISE_DB,
    min_silence_s: float = DEF_MIN_SILENCE,
    pad_s: float = DEF_PAD,
    min_keep_s: float = DEF_MIN_KEEP,
) -> list[tuple[float, float]]:
    """Speech intervals within [in_s, out_s] of a media file (dead air removed)."""
    end = out_s if out_s is not None else _probe_duration(path)
    sils = detect_silence(path, noise_db=noise_db, min_silence_s=min_silence_s)
    return invert_to_speech(sils, start=in_s, end=end,
                            pad_s=pad_s, min_keep_s=min_keep_s)


def tighten_cutlist(cutlist: Cutlist, **kw) -> tuple[Cutlist, dict]:
    """Replace every Cut with its speech sub-cuts (dead air removed), repacked
    contiguously from the first cut's offset. `cut.clip` must be a resolvable
    file path (run conform first). Returns (new Cutlist, stats). If a clip has
    no detectable speech, the original cut is kept untouched."""
    cuts = sorted(cutlist.cuts, key=lambda c: c.offset)
    new: list[Cut] = []
    off = cuts[0].offset if cuts else 0.0
    orig = kept = 0.0
    pauses = 0
    for c in cuts:
        orig += c.duration
        segs = speech_segments(c.clip, in_s=c.in_, out_s=c.out, **kw)
        if not segs:
            segs = [(c.in_, c.out)]
        pauses += max(0, len(segs) - 1)
        for s, e in segs:
            new.append(Cut(clip=c.clip, in_=s, out=e,
                           offset=round(off, 3), label=c.label))
            off = round(off + (e - s), 3)
            kept += (e - s)
    stats = {
        "orig_sec": round(orig, 2),
        "kept_sec": round(kept, 2),
        "removed_sec": round(orig - kept, 2),
        "pauses_removed": pauses,
        "n_cuts_in": len(cuts),
        "n_cuts_out": len(new),
    }
    return replace(cutlist, cuts=new, total_duration_sec=round(off, 3)), stats


def _main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python -m core.silence CLIP [IN] [OUT] "
              "[--noise -30] [--min 0.5]")
        return 1
    path = argv[1]
    pos = [a for a in argv[2:] if not a.startswith("--")]
    in_s = float(pos[0]) if len(pos) > 0 else 0.0
    out_s = float(pos[1]) if len(pos) > 1 else None
    noise = float(argv[argv.index("--noise") + 1]) if "--noise" in argv \
        else DEF_NOISE_DB
    mins = float(argv[argv.index("--min") + 1]) if "--min" in argv \
        else DEF_MIN_SILENCE
    end = out_s if out_s is not None else _probe_duration(path)
    segs = speech_segments(path, in_s=in_s, out_s=out_s,
                           noise_db=noise, min_silence_s=mins)
    win = end - in_s
    kept = sum(e - s for s, e in segs)
    print(f"clip:   {path}")
    print(f"window: {in_s:.2f}-{end:.2f}s ({win:.1f}s)  noise={noise}dB "
          f"min_silence={mins}s")
    if win > 0:
        print(f"speech: {len(segs)} segments, kept {kept:.1f}s, "
              f"removed {win - kept:.1f}s ({100 * (win - kept) / win:.0f}% dead air)")
    for s, e in segs:
        print(f"  {s:8.2f} -> {e:8.2f}   ({e - s:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
