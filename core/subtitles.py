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
    import sys

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
