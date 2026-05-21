"""Tests for subtitle generation (SRT + styled ASS). No ffmpeg.

Run:  python -m core.tests.test_subtitles
"""

from __future__ import annotations

import sys

from core.subtitles import (
    ASS_FORCE_STYLE,
    Chunk,
    build,
    chunk_words,
    segment_chunks,
    to_ass,
    to_srt,
    _fmt_srt,
)

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


# Whisper-shaped transcript: segments + word-level timestamps
TRANSCRIPT = {
    "segments": [
        {"start": 0.0, "end": 2.0, "text": "I won this round.",
         "words": [
             {"word": "I", "start": 0.0, "end": 0.3},
             {"word": "won", "start": 0.3, "end": 0.8},
             {"word": "this", "start": 0.9, "end": 1.2},
             {"word": "round.", "start": 1.2, "end": 2.0},
         ]},
        {"start": 2.5, "end": 4.0, "text": "It was hard work",
         "words": [
             {"word": "It", "start": 2.5, "end": 2.7},
             {"word": "was", "start": 2.7, "end": 3.0},
             {"word": "hard", "start": 3.0, "end": 3.4},
             {"word": "work", "start": 3.4, "end": 4.0},
         ]},
    ]
}


def test_srt_timecode() -> None:
    print("subtitles — SRT timecode formatting")
    check("3661.5s -> 01:01:01,500", _fmt_srt(3661.5) == "01:01:01,500",
          _fmt_srt(3661.5))
    check("0 -> 00:00:00,000", _fmt_srt(0) == "00:00:00,000")


def test_segment_srt() -> None:
    print("subtitles — segment-level SRT (the deliverable)")
    srt = to_srt(segment_chunks(TRANSCRIPT))
    check("starts with index 1", srt.startswith("1\n"))
    check("has arrow timecode line", "00:00:00,000 --> 00:00:02,000" in srt,
          srt[:60])
    check("carries segment text", "I won this round." in srt)
    check("second cue present", "\n2\n" in srt and "It was hard work" in srt)


def test_karaoke_chunks() -> None:
    print("subtitles — 2-word UPPERCASE karaoke chunks, break on punctuation")
    words = [{"text": w["word"], "start": w["start"], "end": w["end"]}
             for seg in TRANSCRIPT["segments"] for w in seg["words"]]
    chunks = chunk_words(words, max_words=2)
    texts = [c.text for c in chunks]
    check("uppercase", all(t == t.upper() for t in texts), str(texts))
    # "I won" (2), then "this round" breaks on punctuation after 'round.'
    check("first chunk is 2 words", texts[0] == "I WON", str(texts))
    check("punctuation stripped + breaks chunk",
          "THIS ROUND" in texts, str(texts))
    check("times from first/last word",
          chunks[0].start == 0.0 and chunks[0].end == 0.8,
          f"{chunks[0].start}/{chunks[0].end}")


def test_ass_style() -> None:
    print("subtitles — ASS carries the proven safe-zone style")
    ass = to_ass([Chunk("HELLO", 0.0, 1.0)])
    check("has Script Info + Events", "[Script Info]" in ass
          and "[Events]" in ass)
    check("Helvetica bold", "Helvetica" in ass)
    check("MarginV 90 safe-zone in style line", "90,1" in ass, "")
    check("Dialogue line for the chunk", "Dialogue: 0," in ass
          and "HELLO" in ass)
    check("force_style constant exposed",
          "MarginV=90" in ASS_FORCE_STYLE and "Alignment=2" in ASS_FORCE_STYLE)


def test_build_fallback() -> None:
    print("subtitles — build() uses words for karaoke, else segments")
    kara = build(TRANSCRIPT, karaoke=True)
    check("karaoke -> many short chunks", len(kara) > 2, str(len(kara)))
    seg = build(TRANSCRIPT, karaoke=False)
    check("non-karaoke -> 2 segment chunks", len(seg) == 2, str(len(seg)))
    no_words = {"segments": [{"start": 0, "end": 3, "text": "no word times"}]}
    fb = build(no_words, karaoke=True)
    check("no word timestamps -> falls back to segment",
          len(fb) == 1 and fb[0].text == "no word times")


def test_degenerate() -> None:
    print("subtitles — degenerate input")
    check("empty transcript -> empty srt", to_srt(segment_chunks({})) == "")
    check("empty -> empty chunks", build({}, karaoke=True) == [])


def main() -> int:
    for fn in (
        test_srt_timecode,
        test_segment_srt,
        test_karaoke_chunks,
        test_ass_style,
        test_build_fallback,
        test_degenerate,
    ):
        fn()
    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
