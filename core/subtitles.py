"""Subtitles — transcript to SRT (deliverable) and styled ASS (burn-in).

Two outputs from one transcript:
- **SRT** (segment-level, standard) — the format real deliveries need
  (e.g. Denis's DUALITY `SUB`/`SRT` outputs). Imports as a subtitle
  track in Resolve/Premiere, or ships as a sidecar.
- **ASS** (karaoke-style, short UPPERCASE chunks) — for burning a punchy
  social caption directly onto the picture.

We already produce transcripts via Whisper (`skills/watch`). This turns
them into captions and burns them with ffmpeg.

Attribution: the ASS burn approach — 2-word UPPERCASE chunks broken on
punctuation, and the proven force_style (Helvetica 18 Bold, Alignment 2,
MarginV 90 as a platform safe-zone rule, not taste) — is adapted from
[browser-use/video-use](https://github.com/browser-use/video-use) (MIT).
Reimplemented here in our own code; MIT permits this with credit.

Honest: nice 2-word karaoke needs WORD-level timestamps (run Whisper with
word timestamps). Without them we fall back to segment-level chunks, which
is exactly right for a standard SRT deliverable anyway.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Proven at 1920x1080 and 1080x1920. MarginV is a safe-zone rule, not taste.
ASS_FORCE_STYLE = (
    "FontName=Helvetica,FontSize=18,Bold=1,"
    "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
    "BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=90"
)
_PUNCT_END = re.compile(r"[.,!?;:…]$")
_SENT_END = re.compile(r"[.!?…]+$")


@dataclass
class Chunk:
    text: str
    start: float
    end: float


def _fmt_srt(t: float) -> str:
    if t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        s += 1
        ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_ass(t: float) -> str:
    if t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _words(transcript: dict[str, Any]) -> list[dict]:
    """Flatten word-level timestamps if present, else []."""
    out: list[dict] = []
    for seg in transcript.get("segments", []):
        for w in seg.get("words", []) or []:
            txt = (w.get("word") or w.get("text") or "").strip()
            if txt and w.get("start") is not None and w.get("end") is not None:
                out.append({"text": txt, "start": float(w["start"]),
                            "end": float(w["end"])})
    return out


def chunk_words(words: list[dict], max_words: int = 2,
                upper: bool = True) -> list[Chunk]:
    """Group words into <= max_words chunks, breaking on punctuation."""
    chunks: list[Chunk] = []
    cur: list[dict] = []
    for w in words:
        cur.append(w)
        ends_punct = bool(_PUNCT_END.search(w["text"]))
        if len(cur) >= max_words or ends_punct:
            text = " ".join(x["text"] for x in cur).strip()
            text = _PUNCT_END.sub("", text)
            if upper:
                text = text.upper()
            chunks.append(Chunk(text, cur[0]["start"], cur[-1]["end"]))
            cur = []
    if cur:
        text = " ".join(x["text"] for x in cur).strip()
        if upper:
            text = text.upper()
        chunks.append(Chunk(text, cur[0]["start"], cur[-1]["end"]))
    return chunks


def readable_chunks(words: list[dict], max_words: int = 8,
                    max_gap: float = 0.8) -> list[Chunk]:
    """Group words into readable subtitle lines (punctuation kept). Breaks on
    a sentence end, on max_words, or on a time gap > max_gap (so dead-air-
    tightened cuts read naturally). Built from WORDS, so a sentence split
    across speech sub-cuts is never duplicated — each word lands once."""
    def _mk(ws: list[dict]) -> Chunk:
        return Chunk(" ".join(x["text"] for x in ws).strip(),
                     ws[0]["start"], ws[-1]["end"])

    chunks: list[Chunk] = []
    cur: list[dict] = []
    for w in words:
        if cur and (w["start"] - cur[-1]["end"] > max_gap):
            chunks.append(_mk(cur))
            cur = []
        cur.append(w)
        if len(cur) >= max_words or _SENT_END.search(w["text"]):
            chunks.append(_mk(cur))
            cur = []
    if cur:
        chunks.append(_mk(cur))
    return chunks


def segment_chunks(transcript: dict[str, Any]) -> list[Chunk]:
    """Segment-level chunks (standard SRT). Fallback when no word times."""
    out: list[Chunk] = []
    for seg in transcript.get("segments", []):
        txt = (seg.get("text") or "").strip()
        if txt and seg.get("start") is not None and seg.get("end") is not None:
            out.append(Chunk(txt, float(seg["start"]), float(seg["end"])))
    return out


def to_srt(chunks: list[Chunk]) -> str:
    lines: list[str] = []
    for i, c in enumerate(chunks, 1):
        lines.append(str(i))
        lines.append(f"{_fmt_srt(c.start)} --> {_fmt_srt(c.end)}")
        lines.append(c.text)
        lines.append("")
    return "\n".join(lines)


def to_ass(chunks: list[Chunk], style: str = ASS_FORCE_STYLE) -> str:
    head = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 384\nPlayResY: 288\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
        "OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,"
        "ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,"
        "MarginL,MarginR,MarginV,Encoding\n"
        "Style: Default,Helvetica,18,&H00FFFFFF,&H000000FF,&H00000000,"
        "&H00000000,1,0,0,0,100,100,0,0,1,2,0,2,10,10,90,1\n\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,"
        "Effect,Text\n"
    )
    rows = [
        f"Dialogue: 0,{_fmt_ass(c.start)},{_fmt_ass(c.end)},Default,,0,0,0,,"
        f"{c.text}"
        for c in chunks
    ]
    return head + "\n".join(rows) + "\n"


def build(transcript: dict[str, Any], karaoke: bool = False) -> list[Chunk]:
    """Word-level 2-word chunks if karaoke and words exist, else segments."""
    if karaoke:
        words = _words(transcript)
        if words:
            return chunk_words(words)
    return segment_chunks(transcript)


def write_srt(transcript: dict[str, Any], path: str | Path) -> str:
    Path(path).write_text(to_srt(segment_chunks(transcript)))
    return str(path)


def write_ass(transcript: dict[str, Any], path: str | Path,
              karaoke: bool = True) -> str:
    Path(path).write_text(to_ass(build(transcript, karaoke=karaoke)))
    return str(path)


_SRT_TIME = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")


def parse_srt(text: str) -> dict[str, Any]:
    """An existing .srt -> Whisper-shaped transcript ({"segments": [...]}),
    so a sidecar/delivery SRT can be reused as a source transcript."""
    segs: list[dict] = []
    blocks = re.split(r"\n\s*\n", text.strip())
    for blk in blocks:
        m = _SRT_TIME.search(blk)
        if not m:
            continue
        a = (int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4]) / 1000)
        b = (int(m[5]) * 3600 + int(m[6]) * 60 + int(m[7]) + int(m[8]) / 1000)
        body = blk[m.end():].strip().replace("\n", " ")
        if body:
            segs.append({"start": round(a, 3), "end": round(b, 3),
                         "text": body})
    return {"segments": segs}


def timeline_transcript(cutlist: Any,
                        transcripts: dict[str, dict]) -> dict[str, Any]:
    """Map per-clip transcripts onto the assembled timeline.

    `cutlist` is anything with `.cuts` (each cut: `.clip`, `.in_`, `.out`,
    `.offset`). `transcripts` maps a clip key -> a Whisper-shaped transcript;
    keys are matched against the cut's clip path, basename and stem. Every
    segment/word inside a cut's [in_, out] window is clipped and shifted to its
    timeline position (t_timeline = t_source - in_ + offset). Returns one merged
    transcript in TIMELINE time — feed straight into build()/to_srt()/to_ass().
    Works on a tightened (dead-air-removed) cutlist: each sub-cut maps its own
    window, so subtitles follow the cut automatically.
    """
    def _lookup(clip: str) -> dict | None:
        for k in (clip, Path(clip).name, Path(clip).stem):
            if k in transcripts:
                return transcripts[k]
        return None

    segs: list[dict] = []
    for c in sorted(getattr(cutlist, "cuts", []), key=lambda x: x.offset):
        tr = _lookup(c.clip)
        if not tr:
            continue
        lo, hi, shift = c.in_, c.out, c.offset - c.in_
        for seg in tr.get("segments", []):
            ss, se = seg.get("start"), seg.get("end")
            if ss is None or se is None:
                continue
            ss, se = float(ss), float(se)
            if se <= lo or ss >= hi:           # segment outside this cut
                continue
            txt = (seg.get("text") or "").strip()
            if not txt:
                continue
            words = []
            for w in seg.get("words") or []:
                ws, we = w.get("start"), w.get("end")
                if ws is None or we is None:
                    continue
                ws, we = float(ws), float(we)
                if we <= lo or ws >= hi:
                    continue
                wtxt = (w.get("word") or w.get("text") or "").strip()
                if wtxt:
                    words.append({"word": wtxt,
                                  "start": round(max(lo, ws) + shift, 3),
                                  "end": round(min(hi, we) + shift, 3)})
            segs.append({"start": round(max(lo, ss) + shift, 3),
                         "end": round(min(hi, se) + shift, 3),
                         "text": txt, "words": words})
    segs.sort(key=lambda s: s["start"])
    return {"segments": segs}


def write_timeline_srt(cutlist: Any, transcripts: dict[str, dict],
                       path: str | Path) -> str:
    """Auto-subtitles for the assembled cut, SRT. Built from word timestamps
    when present (so dead-air-tightened cuts never duplicate a split sentence);
    falls back to segment-level when there are no word times."""
    tt = timeline_transcript(cutlist, transcripts)
    words = _words(tt)
    chunks = readable_chunks(words) if words else segment_chunks(tt)
    Path(path).write_text(to_srt(chunks))
    return str(path)


def write_timeline_ass(cutlist: Any, transcripts: dict[str, dict],
                       path: str | Path, karaoke: bool = True) -> str:
    """Auto-subtitles for the assembled cut, styled ASS (karaoke needs words)."""
    Path(path).write_text(
        to_ass(build(timeline_transcript(cutlist, transcripts),
                     karaoke=karaoke)))
    return str(path)


def burn(video: str, subs: str, out: str) -> str:
    """Burn a subtitle file (.ass or .srt) onto a video via ffmpeg.
    ASS carries its own style; SRT gets force_style applied."""
    p = Path(subs)
    if p.suffix.lower() == ".ass":
        vf = f"subtitles={p.as_posix()}"
    else:
        vf = f"subtitles={p.as_posix()}:force_style='{ASS_FORCE_STYLE}'"
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", video, "-vf", vf,
         "-c:a", "copy", out],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError("subtitle burn failed:\n" + r.stderr[-1500:])
    return out


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    p = argparse.ArgumentParser(prog="python -m core.subtitles")
    p.add_argument("transcript", help="Whisper JSON (segments[, words])")
    p.add_argument("--srt", help="write standard SRT here")
    p.add_argument("--ass", help="write styled karaoke ASS here")
    p.add_argument("--burn", nargs=2, metavar=("VIDEO", "OUT"),
                   help="burn the ASS (or SRT) onto VIDEO -> OUT")
    args = p.parse_args(argv)

    tr = json.loads(Path(args.transcript).read_text())
    made = None
    if args.srt:
        print("wrote", write_srt(tr, args.srt))
        made = args.srt
    if args.ass:
        print("wrote", write_ass(tr, args.ass))
        made = args.ass
    if args.burn:
        if not made:
            print("need --srt or --ass to burn"); return 2
        print("burned", burn(args.burn[0], made, args.burn[1]))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_main())
