"""Tests for dead-air detection logic (pure, no ffmpeg).

Run:  python -m core.tests.test_silence
"""

from __future__ import annotations

import sys

from core.silence import _parse_silencedetect, invert_to_speech

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


SAMPLE = """
[silencedetect @ 0x1] silence_start: 2.0
[silencedetect @ 0x1] silence_end: 4.0 | silence_duration: 2.0
[silencedetect @ 0x1] silence_start: 7.5
[silencedetect @ 0x1] silence_end: 8.2 | silence_duration: 0.7
[silencedetect @ 0x1] silence_start: 11.0
"""


def main() -> int:
    sil = _parse_silencedetect(SAMPLE)
    check("parse count", len(sil) == 3, str(sil))
    check("parse pair", sil[0] == (2.0, 4.0), str(sil[0]))
    check("parse open trailing end", sil[2] == (11.0, None), str(sil[2]))

    # window 0..12, no pad, no min -> exact complement of silence
    sp = invert_to_speech(sil, start=0.0, end=12.0, pad_s=0.0, min_keep_s=0.0)
    check("speech = silence complement",
          sp == [(0.0, 2.0), (4.0, 7.5), (8.2, 11.0)], str(sp))
    check("trailing silence dropped", all(e <= 11.0 for _, e in sp), str(sp))

    # min_keep drops a tiny speech island between two silences
    sp2 = invert_to_speech([(1.0, 2.0), (2.1, 9.0)],
                           start=0.0, end=10.0, pad_s=0.0, min_keep_s=0.5)
    check("tiny island dropped",
          (0.0, 1.0) in sp2 and (9.0, 10.0) in sp2
          and all(e - s >= 0.5 for s, e in sp2), str(sp2))

    # padding bridges a short silence (two regions merge into one)
    sp3 = invert_to_speech([(2.0, 2.4)],
                           start=0.0, end=5.0, pad_s=0.3, min_keep_s=0.0)
    check("padding merges across short gap", sp3 == [(0.0, 5.0)], str(sp3))

    # in/out window honored (only middle slice considered)
    sp4 = invert_to_speech([(0.5, 1.5)],
                           start=1.0, end=4.0, pad_s=0.0, min_keep_s=0.0)
    check("respects window bounds", sp4 == [(1.5, 4.0)], str(sp4))

    check("empty window -> nothing",
          invert_to_speech([], start=5.0, end=5.0) == [], "")
    check("no silence -> whole window",
          invert_to_speech([], start=0.0, end=6.0, pad_s=0.0, min_keep_s=0.0)
          == [(0.0, 6.0)], "")

    print(f"\n{_p} passed, {_f} failed")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
