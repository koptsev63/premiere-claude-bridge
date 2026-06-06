"""Tests for core.asr_verify - Whisper transcript sanity checker.

Each detector gets at least one positive (should flag) and one negative
(should not flag) case. apply_corrections is tested for immutability and
for both correction key types (positional and text-based).

Run: python -m pytest core/tests/test_asr_verify.py -q
 or: python -m core.tests.test_asr_verify
"""

from __future__ import annotations

import copy
import sys

from core.asr_verify import (
    Suspect,
    apply_corrections,
    flag_suspects,
    report,
    report_json,
    suspect_context,
    _detect_low_confidence,
    _detect_repetition_loop,
    _detect_suspicious_token,
    _detect_impossible_timing,
    _detect_charset_mix,
)

_p = _f = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _seg(text: str, start: float, end: float,
         words: list[dict] | None = None) -> dict:
    """Build a minimal Whisper segment dict."""
    s: dict = {"start": start, "end": end, "text": text}
    if words is not None:
        s["words"] = words
    return s


def _word(text: str, start: float, end: float,
          probability: float | None = None) -> dict:
    """Build a word entry (uses 'word' key to match Whisper standard)."""
    w: dict = {"word": text, "start": start, "end": end}
    if probability is not None:
        w["probability"] = probability
    return w


# ---------------------------------------------------------------------------
# Detector 1 - low confidence
# ---------------------------------------------------------------------------

# Segment with one flagged word (prob 0.3) and one clean word (prob 0.9).
_TR_LOW_CONF = {"segments": [_seg("скот держит", 0.0, 2.0, words=[
    _word("скот", 0.0, 0.5, probability=0.3),   # BELOW threshold - should flag
    _word("держит", 0.5, 2.0, probability=0.9), # above threshold - clean
])]}

# Transcript with no probability data at all - should produce nothing.
_TR_NO_PROB = {"segments": [_seg("нормально", 0.0, 1.0, words=[
    _word("нормально", 0.0, 1.0),  # no 'probability' key
])]}


def test_low_confidence() -> None:
    print("detector 1 - low_confidence")
    suspects = _detect_low_confidence(
        _TR_LOW_CONF["segments"], min_conf=0.55
    )
    check("flags low-prob word", len(suspects) == 1, str(suspects))
    check("correct kind", suspects[0].kind == "low_confidence")
    check("correct text", suspects[0].text == "скот", suspects[0].text)
    check("correct seg_index", suspects[0].seg_index == 0)
    check("correct word_index", suspects[0].word_index == 0)
    check("reason mentions probability", "probability" in suspects[0].reason)

    # Negative: clean word is not flagged.
    suspects2 = _detect_low_confidence(
        _TR_LOW_CONF["segments"], min_conf=0.55
    )
    check("clean word not flagged",
          all(s.text != "держит" for s in suspects2), str(suspects2))

    # Negative: no probability data -> nothing flagged.
    suspects3 = _detect_low_confidence(_TR_NO_PROB["segments"], min_conf=0.55)
    check("no prob data -> nothing flagged", suspects3 == [], str(suspects3))


# ---------------------------------------------------------------------------
# Detector 2 - repetition loop
# ---------------------------------------------------------------------------

# Five consecutive repeats of "да" -> should flag.
_REPEAT_WORDS = [_word(w, i * 0.2, (i + 1) * 0.2)
                 for i, w in enumerate(["да", "да", "да", "да", "да", "ладно"])]
_TR_REPEAT = {"segments": [_seg("да да да да да ладно", 0.0, 1.2,
                                words=_REPEAT_WORDS)]}

# Only 3 repeats (below default max_repeat=4) -> should NOT flag at default.
_REPEAT_SHORT = [_word(w, i * 0.2, (i + 1) * 0.2)
                 for i, w in enumerate(["нет", "нет", "нет", "хорошо"])]
_TR_REPEAT_SHORT = {"segments": [_seg("нет нет нет хорошо", 0.0, 0.8,
                                      words=_REPEAT_SHORT)]}


def test_repetition_loop() -> None:
    print("detector 2 - repetition_loop")
    suspects = _detect_repetition_loop(_TR_REPEAT["segments"], max_repeat=4)
    check("flags 5x repeat", len(suspects) >= 1, str(suspects))
    check("correct kind", suspects[0].kind == "repetition_loop", suspects[0].kind)
    check("reason mentions count", "5x" in suspects[0].reason
          or "5" in suspects[0].reason, suspects[0].reason)

    # Negative: 3 repeats with threshold 4 -> nothing flagged.
    suspects2 = _detect_repetition_loop(_TR_REPEAT_SHORT["segments"], max_repeat=4)
    check("3 repeats below threshold -> not flagged",
          suspects2 == [], str(suspects2))

    # Sensitivity: lowering max_repeat to 3 catches the shorter run.
    suspects3 = _detect_repetition_loop(_TR_REPEAT_SHORT["segments"], max_repeat=3)
    check("3 repeats caught when threshold=3",
          len(suspects3) >= 1, str(suspects3))


# ---------------------------------------------------------------------------
# Detector 3 - suspicious token
# ---------------------------------------------------------------------------

# "Ско" - 3 Cyrillic chars, not in the RU stoplist -> should flag.
_TR_TRUNC = {"segments": [_seg("Ско держит", 0.0, 2.0, words=[
    _word("Ско", 0.0, 0.4),       # suspicious - truncation of 'скот'
    _word("держит", 0.4, 2.0),    # long enough, not suspicious
])]}

# "не" - 2 Cyrillic chars but IS in the stoplist -> should NOT flag.
_TR_LEGIT_SHORT = {"segments": [_seg("не знаю", 0.0, 1.0, words=[
    _word("не", 0.0, 0.3),
    _word("знаю", 0.3, 1.0),
])]}


def test_suspicious_token() -> None:
    print("detector 3 - suspicious_token")
    suspects = _detect_suspicious_token(_TR_TRUNC["segments"])
    check("flags 'Ско' truncation", len(suspects) == 1, str(suspects))
    check("correct kind", suspects[0].kind == "suspicious_token")
    check("text is 'Ско'", suspects[0].text == "Ско", suspects[0].text)
    check("reason mentions truncation", "truncation" in suspects[0].reason)

    # Negative: "не" is in the stoplist.
    suspects2 = _detect_suspicious_token(_TR_LEGIT_SHORT["segments"])
    check("stoplist word 'не' not flagged",
          all(s.text != "не" for s in suspects2), str(suspects2))

    # Negative: "держит" is long enough.
    check("long word 'держит' not flagged",
          all(s.text != "держит" for s in suspects2), str(suspects2))


# ---------------------------------------------------------------------------
# Detector 4 - impossible timing
# ---------------------------------------------------------------------------

# Segment with end <= start.
_TR_SEG_BAD = {"segments": [_seg("проблема", 5.0, 3.0)]}  # end < start

# Word with end <= start.
_TR_WORD_BAD = {"segments": [_seg("хорошо стоп", 0.0, 5.0, words=[
    _word("хорошо", 0.0, 1.5),
    _word("стоп", 3.0, 2.0),   # end < start - bad
])]}

# Word with absurdly long duration (> max_word_s=3.0).
_TR_WORD_LONG = {"segments": [_seg("длинное слово", 0.0, 10.0, words=[
    _word("длинное", 0.0, 5.5),  # 5.5s - way too long
    _word("слово", 5.5, 6.0),
])]}

# Overlapping consecutive words.
_TR_OVERLAP = {"segments": [_seg("перекрытие тест", 0.0, 4.0, words=[
    _word("перекрытие", 0.0, 2.5),
    _word("тест", 2.0, 4.0),    # starts at 2.0 < prev_end 2.5 -> overlap
])]}

# Clean transcript - no timing issues.
_TR_GOOD_TIMING = {"segments": [_seg("нормально всё", 0.0, 2.0, words=[
    _word("нормально", 0.0, 1.0),
    _word("всё", 1.0, 2.0),
])]}


def test_impossible_timing() -> None:
    print("detector 4 - impossible_timing")
    # Segment-level bad timing.
    s1 = _detect_impossible_timing(_TR_SEG_BAD["segments"], max_word_s=3.0)
    check("segment end<start flagged", len(s1) >= 1, str(s1))
    check("seg flag at word_index -1",
          any(s.word_index == -1 for s in s1), str(s1))

    # Word-level bad timing.
    s2 = _detect_impossible_timing(_TR_WORD_BAD["segments"], max_word_s=3.0)
    check("word end<start flagged",
          any(s.kind == "impossible_timing" and s.text == "стоп" for s in s2),
          str(s2))

    # Absurdly long word.
    s3 = _detect_impossible_timing(_TR_WORD_LONG["segments"], max_word_s=3.0)
    check("too-long word flagged",
          any(s.text == "длинное" for s in s3), str(s3))

    # Overlapping words.
    s4 = _detect_impossible_timing(_TR_OVERLAP["segments"], max_word_s=3.0)
    check("overlapping words flagged",
          any("overlaps" in s.reason for s in s4), str(s4))

    # Negative: clean timing -> nothing flagged.
    s5 = _detect_impossible_timing(_TR_GOOD_TIMING["segments"], max_word_s=3.0)
    check("clean timing -> nothing flagged", s5 == [], str(s5))


# ---------------------------------------------------------------------------
# Detector 5 - charset mix
# ---------------------------------------------------------------------------

# "GoodМorning" - Latin G-o-o-d + Cyrillic М + Latin o-r-n-i-n-g.
_TR_MIX = {"segments": [_seg("GoodМorning всем", 0.0, 2.0, words=[
    _word("GoodМorning", 0.0, 1.0),  # mixed Cyrillic + Latin -> flag
    _word("всем", 1.0, 2.0),          # pure Cyrillic -> clean
])]}

# Pure Cyrillic and pure Latin - neither should flag.
_TR_CLEAN_CHARSET = {"segments": [_seg("hello world", 0.0, 1.0, words=[
    _word("hello", 0.0, 0.5),
    _word("world", 0.5, 1.0),
])]}


def test_charset_mix() -> None:
    print("detector 5 - charset_mix")
    suspects = _detect_charset_mix(_TR_MIX["segments"])
    check("flags mixed-charset token", len(suspects) == 1, str(suspects))
    check("correct kind", suspects[0].kind == "charset_mix")
    check("text is 'GoodМorning'",
          suspects[0].text == "GoodМorning", suspects[0].text)
    check("reason mentions Cyrillic and Latin", "Cyrillic" in suspects[0].reason
          and "Latin" in suspects[0].reason)

    # Negative: pure Cyrillic word not flagged.
    suspects2 = _detect_charset_mix(_TR_MIX["segments"])
    check("pure-Cyrillic word not flagged",
          all(s.text != "всем" for s in suspects2), str(suspects2))

    # Negative: pure Latin tokens -> nothing flagged.
    suspects3 = _detect_charset_mix(_TR_CLEAN_CHARSET["segments"])
    check("pure-Latin transcript -> nothing flagged",
          suspects3 == [], str(suspects3))


# ---------------------------------------------------------------------------
# flag_suspects integration
# ---------------------------------------------------------------------------

# A composite transcript that triggers ALL five detectors.
_TR_ALL = {
    "segments": [
        # seg 0: low-confidence word + suspicious truncation
        _seg("Ско держит", 0.0, 2.0, words=[
            _word("Ско", 0.0, 0.4, probability=0.30),  # low conf + truncation
            _word("держит", 0.4, 2.0, probability=0.92),
        ]),
        # seg 1: repetition loop (5x "да")
        _seg("да да да да да ладно", 2.5, 5.0, words=[
            *[_word("да", 2.5 + i * 0.2, 2.5 + (i + 1) * 0.2) for i in range(5)],
            _word("ладно", 3.5, 5.0),
        ]),
        # seg 2: impossible timing (end < start for the segment)
        _seg("плохо", 8.0, 7.0),  # end < start
        # seg 3: charset mix
        _seg("GoodМorning всем", 9.0, 11.0, words=[
            _word("GoodМorning", 9.0, 10.0),
            _word("всем", 10.0, 11.0),
        ]),
    ]
}


def test_flag_suspects_integration() -> None:
    print("flag_suspects - integration (all detectors)")
    suspects = flag_suspects(_TR_ALL, min_conf=0.55, max_repeat=4, max_word_s=3.0)
    kinds = {s.kind for s in suspects}
    check("low_confidence fired", "low_confidence" in kinds, str(kinds))
    check("repetition_loop fired", "repetition_loop" in kinds, str(kinds))
    check("suspicious_token fired", "suspicious_token" in kinds, str(kinds))
    check("impossible_timing fired", "impossible_timing" in kinds, str(kinds))
    check("charset_mix fired", "charset_mix" in kinds, str(kinds))
    # Sorted by start time.
    starts = [s.start for s in suspects]
    check("output sorted by start", starts == sorted(starts), str(starts))


# ---------------------------------------------------------------------------
# apply_corrections - immutability + both key types
# ---------------------------------------------------------------------------

_TR_FOR_CORR = {
    "segments": [
        _seg("Ско держит всё", 0.0, 3.0, words=[
            _word("Ско", 0.0, 0.4),
            _word("держит", 0.4, 1.5),
            _word("всё", 1.5, 3.0),
        ]),
        _seg("GoodМorning привет", 3.5, 5.0, words=[
            _word("GoodМorning", 3.5, 4.5),
            _word("привет", 4.5, 5.0),
        ]),
    ]
}


def test_apply_corrections_immutability() -> None:
    print("apply_corrections - immutability")
    original = copy.deepcopy(_TR_FOR_CORR)
    _ = apply_corrections(_TR_FOR_CORR, {"Ско": "скот"})
    check("original not mutated",
          _TR_FOR_CORR == original, str(_TR_FOR_CORR["segments"][0]["words"][0]))


def test_apply_corrections_text_key() -> None:
    print("apply_corrections - text key correction")
    fixed = apply_corrections(_TR_FOR_CORR, {"Ско": "скот"})
    first_word = (fixed["segments"][0]["words"][0].get("word")
                  or fixed["segments"][0]["words"][0].get("text"))
    check("word replaced", first_word == "скот", first_word)
    # Segment text must be re-derived.
    seg_text = fixed["segments"][0]["text"]
    check("segment text re-derived", "скот" in seg_text, seg_text)
    check("other words untouched", "держит" in seg_text, seg_text)


def test_apply_corrections_positional_key() -> None:
    print("apply_corrections - positional (seg, word) key correction")
    fixed = apply_corrections(_TR_FOR_CORR, {(1, 0): "Доброе утро"})
    first_word = (fixed["segments"][1]["words"][0].get("word")
                  or fixed["segments"][1]["words"][0].get("text"))
    check("positional word replaced",
          first_word == "Доброе утро", first_word)
    seg_text = fixed["segments"][1]["text"]
    check("segment text updated", "Доброе утро" in seg_text, seg_text)


def test_apply_corrections_multi() -> None:
    print("apply_corrections - multiple corrections at once")
    fixed = apply_corrections(_TR_FOR_CORR, {
        "Ско": "скот",
        (1, 0): "Доброе утро",
    })
    w0 = (fixed["segments"][0]["words"][0].get("word")
          or fixed["segments"][0]["words"][0].get("text"))
    w1 = (fixed["segments"][1]["words"][0].get("word")
          or fixed["segments"][1]["words"][0].get("text"))
    check("both corrections applied",
          w0 == "скот" and w1 == "Доброе утро", f"w0={w0} w1={w1}")


# ---------------------------------------------------------------------------
# report() and report_json()
# ---------------------------------------------------------------------------

def test_report() -> None:
    print("report - human-readable output")
    txt = report(_TR_FOR_CORR)  # TR_FOR_CORR has 'Ско' (suspicious token)
    check("report is a string", isinstance(txt, str))
    check("contains kind label",
          "suspicious_token" in txt or "asr_verify" in txt, txt[:80])

    clean = {"segments": [_seg("нормально хорошо", 0.0, 2.0, words=[
        _word("нормально", 0.0, 1.0),
        _word("хорошо", 1.0, 2.0),
    ])]}
    clean_txt = report(clean)
    check("no suspects -> 'no suspects' message",
          "no suspects" in clean_txt.lower(), clean_txt)


def test_report_json() -> None:
    print("report_json - machine-readable output")
    data = report_json(_TR_FOR_CORR)
    check("returns list", isinstance(data, list))
    if data:
        keys = set(data[0].keys())
        for k in ("kind", "text", "start", "end", "seg_index",
                  "word_index", "reason"):
            check(f"json has key '{k}'", k in keys)


# ---------------------------------------------------------------------------
# suspect_context()
# ---------------------------------------------------------------------------

def test_suspect_context() -> None:
    print("suspect_context - LLM handoff helper")
    suspects = flag_suspects(_TR_TRUNC)
    check("at least one suspect for context test",
          len(suspects) >= 1, str(suspects))
    if suspects:
        ctx = suspect_context(_TR_TRUNC, suspects[0], window=3)
        check("context is a string", isinstance(ctx, str))
        check("context contains suspect text marked with []",
              "[Ско]" in ctx, ctx)
        check("context contains segment header", "Segment" in ctx)
        check("context contains timecode", "00:00:" in ctx)


# ---------------------------------------------------------------------------
# CLI interface
# ---------------------------------------------------------------------------

def test_cli_exit_code() -> None:
    print("CLI - exits 1 when suspects found, 0 when clean")
    import json
    import os
    import tempfile

    # Write a suspect transcript to a temp file.
    with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(_TR_TRUNC, fh, ensure_ascii=False)
        suspect_path = fh.name

    with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump({"segments": [_seg("чисто", 0.0, 1.0, words=[
            _word("чисто", 0.0, 1.0)
        ])]}, fh, ensure_ascii=False)
        clean_path = fh.name

    try:
        from core.asr_verify import _main
        rc_suspect = _main([suspect_path])
        check("exits 1 for suspect transcript", rc_suspect == 1, str(rc_suspect))
        rc_clean = _main([clean_path])
        check("exits 0 for clean transcript", rc_clean == 0, str(rc_clean))
    finally:
        os.unlink(suspect_path)
        os.unlink(clean_path)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_edge_empty() -> None:
    print("edge cases - empty and malformed transcripts")
    check("empty dict -> no suspects", flag_suspects({}) == [])
    check("empty segments -> no suspects",
          flag_suspects({"segments": []}) == [])
    check("segment without words -> no word-level flags",
          all(s.word_index == -1 or s.kind == "impossible_timing"
              for s in flag_suspects({"segments": [
                  _seg("ok", 5.0, 3.0)]})))  # only timing flag
    check("segment with None words -> graceful",
          flag_suspects({"segments": [{"start": 0, "end": 1,
                                        "text": "ok", "words": None}]}) == [])


# ---------------------------------------------------------------------------
# Main runner (also pytest-discoverable via function names starting test_)
# ---------------------------------------------------------------------------

def main() -> int:
    for fn in (
        test_low_confidence,
        test_repetition_loop,
        test_suspicious_token,
        test_impossible_timing,
        test_charset_mix,
        test_flag_suspects_integration,
        test_apply_corrections_immutability,
        test_apply_corrections_text_key,
        test_apply_corrections_positional_key,
        test_apply_corrections_multi,
        test_report,
        test_report_json,
        test_suspect_context,
        test_cli_exit_code,
        test_edge_empty,
    ):
        fn()
    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
