"""Tests for the smart media library (tag / search / find lines / sequence).

Run:  python -m core.tests.test_library
Hermetic — synthetic report, no video, no NLE.
"""

from __future__ import annotations

import sys

from core.library import MediaLibrary, auto_tags, rough_sentiment
from core.library import Clip

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


REPORT = [
    {"name": "00101.MTS", "duration": 30, "motion_score": 2.5,
     "audio_peak_db": -10, "audio_peak_time_sec": 12,
     "speech_text": "Я выиграл этот раунд, отлично копаю"},
    {"name": "00102.MTS", "duration": 20, "motion_score": 0.1,
     "audio_peak_db": -35, "audio_peak_time_sec": 5,
     "speech_text": ""},
    {"name": "00103.MTS", "duration": 25, "motion_score": 0.5,
     "audio_peak_db": -22, "audio_peak_time_sec": 8,
     "speech_text": "Это тяжело, я устал, плохо"},
    {"name": "interview_hero.MTS", "duration": 40, "motion_score": 0.2,
     "audio_peak_db": -14, "audio_peak_time_sec": 10,
     "speech_text": "The grave digging championship is serious work"},
]


def test_sentiment_and_tags() -> None:
    print("library — rough sentiment + auto tags")
    check("positive sentiment", rough_sentiment("отлично выиграл") == "positive")
    check("negative sentiment", rough_sentiment("плохо тяжело") == "negative")
    check("neutral sentiment", rough_sentiment("просто текст") == "neutral")
    lib = MediaLibrary(REPORT)
    t = lib.by_name()["00101.MTS"].tags
    check("high motion -> action", "action" in t, str(t))
    check("loud peak -> loud", "loud" in t)
    check("has speech -> speech + mood", "speech" in t
          and any(x.startswith("mood:") for x in t))
    silent = lib.by_name()["00102.MTS"].tags
    check("no speech -> silent + static", "silent" in silent
          and "static" in silent, str(silent))


def test_search_and_or_ranking() -> None:
    print("library — search AND/OR + ranking (transcript > tag > name)")
    lib = MediaLibrary(REPORT)
    hits = lib.search("копаю")
    check("transcript hit found", hits and hits[0].clip == "00101.MTS",
          str([h.clip for h in hits]))
    check("transcript match recorded + snippet",
          "transcript" in hits[0].where and hits[0].snippet)
    # AND requires all terms; "выиграл" + "копаю" both in 00101 only
    a = lib.search("выиграл копаю", "and")
    check("AND: only the clip with both", [h.clip for h in a] == ["00101.MTS"],
          str([h.clip for h in a]))
    o = lib.search("устал копаю", "or")
    check("OR: union of matches", {h.clip for h in o}
          == {"00101.MTS", "00103.MTS"}, str([h.clip for h in o]))
    # tag search
    act = lib.search("action")
    check("tag search works", any(h.clip == "00101.MTS" for h in act))
    # name search
    nm = lib.search("interview")
    check("name search works", any(h.clip == "interview_hero.MTS" for h in nm))
    check("empty query -> no hits", lib.search("") == [])
    check("case-insensitive", lib.search("КОПАЮ") and
          lib.search("КОПАЮ")[0].clip == "00101.MTS")


def test_find_lines() -> None:
    print("library — find a character's line by phrase")
    lib = MediaLibrary(REPORT)
    h = lib.find_lines("grave digging championship")
    check("phrase found in the right clip",
          len(h) == 1 and h[0].clip == "interview_hero.MTS", str(h))
    check("snippet returned", bool(h[0].snippet))
    check("absent phrase -> nothing", lib.find_lines("dragons") == [])


def test_to_cutlist_sequence() -> None:
    print("library — search results -> a Cutlist (new sequence)")
    lib = MediaLibrary(REPORT)
    hits = lib.search("копаю устал", "or")  # 00101 + 00103
    cl = lib.to_cutlist(hits, name="Hero_lines", fps=25, window_sec=6)
    check("sequence named", cl.sequence_name == "Hero_lines")
    check("one cut per hit", len(cl.cuts) == len(hits) == 2, str(len(cl.cuts)))
    check("cutlist validates", cl.validate() == [], str(cl.validate()))
    check("offsets contiguous from 0",
          cl.cuts[0].offset == 0.0
          and cl.cuts[1].offset == round(cl.cuts[0].out - cl.cuts[0].in_, 2))
    # window centred on the peak, clamped inside the clip
    c0 = cl.cuts[0]
    src = lib.by_name()[c0.clip]
    check("window inside clip duration",
          c0.in_ >= 0 and c0.out <= src.duration and c0.out > c0.in_,
          f"{c0.in_}/{c0.out} dur {src.duration}")


def test_all_tags_and_degenerate() -> None:
    print("library — tag counts + degenerate inputs")
    lib = MediaLibrary(REPORT)
    tags = lib.all_tags()
    check("tag counts present", tags.get("speech", 0) >= 2, str(tags))
    empty = MediaLibrary([])
    check("empty report -> empty lib", empty.clips == []
          and empty.search("x") == [])


def main() -> int:
    for fn in (
        test_sentiment_and_tags,
        test_search_and_or_ranking,
        test_find_lines,
        test_to_cutlist_sequence,
        test_all_tags_and_degenerate,
    ):
        fn()
    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
