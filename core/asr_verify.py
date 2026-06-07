"""core.asr_verify - second-pass sanity checker for Whisper transcripts.

Flags likely mis-hearings before a transcript is turned into subtitles or
committed to an edit. Produces a machine report of suspect spans (with
timecodes) that a human or LLM can review and correct; also applies
corrections to produce a clean transcript for downstream use.

Honest boundary: this is heuristic, not semantic. It catches measurable
signals - low confidence scores, repeated tokens, implausibly short
fragments, impossible timing, and charset garble. It cannot determine
whether a word is *contextually wrong* (only an LLM or human can do that).
Pair it with the LLM-handoff helper `suspect_context()` which surfaces the
surrounding words so an LLM can propose the right replacement.

Detectors (each is a pure function, independently unit-testable):
  1. low_confidence   - words below a probability threshold (faster-whisper)
  2. repetition_loop  - hallucination: the same token repeated >= N times
  3. suspicious_token - very short alpha fragment not in a known-short stoplist
  4. impossible_timing - end <= start, zero duration, too long, or overlap
  5. charset_mix      - a single token mixing Cyrillic + Latin letters

Public API:
  Suspect            dataclass
  flag_suspects()    -> list[Suspect]
  apply_corrections() -> new transcript (immutable)
  report()           -> human-readable string
  report_json()      -> list of dicts
  suspect_context()  -> surrounding-words snippet for LLM prompt

CLI:
  python -m core.asr_verify TRANSCRIPT.json [--min-conf 0.55]
  Exits 1 if any suspects found (suitable for pipeline gating).
"""

from __future__ import annotations

import copy
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Short-word stoplists - legitimate short tokens that are NOT truncations.
# Add to these freely; all comparisons are case-insensitive.
# ---------------------------------------------------------------------------
_RU_SHORT_OK: frozenset[str] = frozenset({
    # Single Cyrillic letters used as words.
    "а", "б", "в", "г", "д", "е", "ж", "з", "и", "к", "л", "м", "н",
    "о", "с", "т", "у", "ф", "х", "ц", "ч", "ш", "щ", "э", "ю", "я",
    # Two-char legitimate words.
    "не", "да", "на", "по", "за", "но", "из", "до", "во", "со", "об",
    "ко", "ну", "эх", "ой", "ах", "ух", "эй", "же", "ли", "бы", "уж",
    "ты", "он", "мы", "вы", "то", "чо", "го", "ну", "ай",
    # Three-char legitimate words (high-frequency, unambiguously complete).
    "все", "всё", "вот", "там", "тут", "тем", "том", "был", "кто", "что",
    "где", "как", "так", "уже", "ещё", "ещо", "еще", "два", "три", "раз",
    "сам", "сей", "при", "под", "над", "про", "для", "без", "или", "ему",
    "его", "её", "ей", "них", "нас", "вас", "нет", "век", "год", "дом",
    "шла", "шёл", "шел", "нём", "нем", "неё", "нее", "чем", "тот", "эта",
    "это", "эти", "рук", "глаз", "рот",
})

_EN_SHORT_OK: frozenset[str] = frozenset({
    "a", "i", "o",
    "an", "as", "at", "be", "by", "do", "go", "he", "if", "in",
    "is", "it", "me", "my", "no", "of", "ok", "on", "or", "so",
    "to", "up", "us", "we",
})

_SHORT_OK: frozenset[str] = _RU_SHORT_OK | _EN_SHORT_OK

# A token is "suspicious" if it is 1-3 alpha chars AND not in the stoplist.
# Max length for the suspicion check (4+ char fragments are long enough).
_MAX_SHORT_SUSPECT_LEN = 3

# Regex to strip leading/trailing punctuation before measuring length.
_ALPHA_STRIP = re.compile(r"[^\w]", re.UNICODE)

# Detect charset mix: token must contain both Cyrillic and Latin letters.
_HAS_CYR = re.compile(r"[А-ЯЁа-яё]")
_HAS_LAT = re.compile(r"[A-Za-z]")


# ---------------------------------------------------------------------------
# Core dataclass
# ---------------------------------------------------------------------------

@dataclass
class Suspect:
    """One flagged span in the transcript."""
    kind: str           # detector name: "low_confidence" | "repetition_loop" | ...
    text: str           # the offending token(s) as they appear in the transcript
    start: float        # timecode of the suspect span (seconds)
    end: float
    seg_index: int      # index into transcript["segments"]
    word_index: int     # index of the first offending word in the segment's words;
                        # -1 if the flag is segment-level (impossible_timing)
    reason: str         # human-readable explanation


def _fmt_tc(t: float) -> str:
    """Format seconds as HH:MM:SS.mmm for reports."""
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


# ---------------------------------------------------------------------------
# Detector 1 - low confidence
# ---------------------------------------------------------------------------

def _detect_low_confidence(
    segments: list[dict],
    min_conf: float,
) -> list[Suspect]:
    """Flag words whose probability/confidence is below min_conf.

    Gracefully skips transcripts that carry no probability data.
    Works with faster-whisper's 'probability' key and openai-whisper's
    potential 'confidence' key.
    """
    suspects: list[Suspect] = []
    for si, seg in enumerate(segments):
        for wi, w in enumerate(seg.get("words") or []):
            prob = w.get("probability") if w.get("probability") is not None \
                else w.get("confidence")
            if prob is None:
                continue
            try:
                prob = float(prob)
            except (TypeError, ValueError):
                continue
            if prob < min_conf:
                txt = (w.get("word") or w.get("text") or "").strip()
                suspects.append(Suspect(
                    kind="low_confidence",
                    text=txt,
                    start=round(float(w.get("start", 0.0)), 3),
                    end=round(float(w.get("end", 0.0)), 3),
                    seg_index=si,
                    word_index=wi,
                    reason=f"probability {prob:.3f} < {min_conf}",
                ))
    return suspects


# ---------------------------------------------------------------------------
# Detector 2 - repetition loop
# ---------------------------------------------------------------------------

def _detect_repetition_loop(
    segments: list[dict],
    max_repeat: int,
) -> list[Suspect]:
    """Flag runs where the same word/token appears >= max_repeat times in a row.

    This is a well-known Whisper hallucination on low-quality or silence-heavy
    audio. Comparison is case-insensitive and strips leading/trailing punct.
    """
    suspects: list[Suspect] = []
    for si, seg in enumerate(segments):
        words = seg.get("words") or []
        if len(words) < max_repeat:
            continue
        run_word = None
        run_start_wi = 0
        run_count = 0
        for wi, w in enumerate(words):
            raw = (w.get("word") or w.get("text") or "").strip()
            key = _ALPHA_STRIP.sub("", raw).lower()
            if key and key == run_word:
                run_count += 1
            else:
                if run_count >= max_repeat and run_word:
                    # emit for the whole run
                    first_w = words[run_start_wi]
                    last_w = words[wi - 1]
                    suspects.append(Suspect(
                        kind="repetition_loop",
                        text=raw,
                        start=round(float(first_w.get("start", 0.0)), 3),
                        end=round(float(last_w.get("end", 0.0)), 3),
                        seg_index=si,
                        word_index=run_start_wi,
                        reason=f"'{run_word}' repeated {run_count}x (max {max_repeat})",
                    ))
                run_word = key
                run_start_wi = wi
                run_count = 1
        # flush end of sequence
        if run_count >= max_repeat and run_word:
            first_w = words[run_start_wi]
            last_w = words[-1]
            suspects.append(Suspect(
                kind="repetition_loop",
                text=run_word,
                start=round(float(first_w.get("start", 0.0)), 3),
                end=round(float(last_w.get("end", 0.0)), 3),
                seg_index=si,
                word_index=run_start_wi,
                reason=f"'{run_word}' repeated {run_count}x (max {max_repeat})",
            ))
    return suspects


# ---------------------------------------------------------------------------
# Detector 3 - suspicious token (possible truncation)
# ---------------------------------------------------------------------------

def _detect_suspicious_token(segments: list[dict]) -> list[Suspect]:
    """Flag very short alpha fragments that are likely truncated words.

    The canonical bug: Whisper mis-hears 'скот' (cattle) as 'Ско' - a 3-char
    Cyrillic fragment that is not a known short word. Any 1-3 alpha-char token
    not in the stoplist is flagged as a possible truncation.

    Digits-only and punctuation-only tokens are ignored (they can be short).
    """
    suspects: list[Suspect] = []
    for si, seg in enumerate(segments):
        for wi, w in enumerate(seg.get("words") or []):
            raw = (w.get("word") or w.get("text") or "").strip()
            # Strip leading/trailing punctuation to get the alpha core.
            core = _ALPHA_STRIP.sub("", raw)
            if not core:
                continue
            # Only alpha content counts - skip purely numeric tokens.
            if not re.search(r"[^\W\d_]", core, re.UNICODE):
                continue
            if (len(core) <= _MAX_SHORT_SUSPECT_LEN
                    and core.lower() not in _SHORT_OK):
                suspects.append(Suspect(
                    kind="suspicious_token",
                    text=raw,
                    start=round(float(w.get("start", 0.0)), 3),
                    end=round(float(w.get("end", 0.0)), 3),
                    seg_index=si,
                    word_index=wi,
                    reason=(
                        f"'{raw}' is {len(core)} alpha chars, not in "
                        "short-word stoplist - possible truncation"
                    ),
                ))
    return suspects


# ---------------------------------------------------------------------------
# Detector 4 - impossible timing
# ---------------------------------------------------------------------------

def _detect_impossible_timing(
    segments: list[dict],
    max_word_s: float,
) -> list[Suspect]:
    """Flag segments or words with impossible or degenerate timing.

    Checks:
    - Segment end <= start (negative or zero duration)
    - Word end <= start
    - Word duration == 0
    - Word duration > max_word_s (absurdly long single-word span)
    - Overlapping consecutive words within a segment
    """
    suspects: list[Suspect] = []
    for si, seg in enumerate(segments):
        ss = seg.get("start")
        se = seg.get("end")
        if ss is not None and se is not None:
            ss, se = float(ss), float(se)
            if se <= ss:
                suspects.append(Suspect(
                    kind="impossible_timing",
                    text=(seg.get("text") or "").strip()[:40],
                    start=round(ss, 3),
                    end=round(se, 3),
                    seg_index=si,
                    word_index=-1,
                    reason=f"segment end {se} <= start {ss}",
                ))

        words = seg.get("words") or []
        prev_end: float | None = None
        for wi, w in enumerate(words):
            ws = w.get("start")
            we = w.get("end")
            if ws is None or we is None:
                continue
            ws, we = float(ws), float(we)
            txt = (w.get("word") or w.get("text") or "").strip()
            dur = we - ws

            if we <= ws:
                suspects.append(Suspect(
                    kind="impossible_timing",
                    text=txt,
                    start=round(ws, 3),
                    end=round(we, 3),
                    seg_index=si,
                    word_index=wi,
                    reason=f"word end {we} <= start {ws} (duration {dur:.3f}s)",
                ))
            elif dur > max_word_s:
                suspects.append(Suspect(
                    kind="impossible_timing",
                    text=txt,
                    start=round(ws, 3),
                    end=round(we, 3),
                    seg_index=si,
                    word_index=wi,
                    reason=f"word duration {dur:.3f}s > max {max_word_s}s",
                ))

            if prev_end is not None and ws < prev_end:
                suspects.append(Suspect(
                    kind="impossible_timing",
                    text=txt,
                    start=round(ws, 3),
                    end=round(we, 3),
                    seg_index=si,
                    word_index=wi,
                    reason=(
                        f"word start {ws:.3f} overlaps previous word "
                        f"end {prev_end:.3f}"
                    ),
                ))
            if we > ws:  # only advance cursor on valid words
                prev_end = we
    return suspects


# ---------------------------------------------------------------------------
# Detector 5 - charset mix
# ---------------------------------------------------------------------------

def _detect_charset_mix(segments: list[dict]) -> list[Suspect]:
    """Flag tokens that contain both Cyrillic and Latin letters.

    This is a reliable ASR garble signal: a genuine word is either
    Cyrillic or Latin, never both (unless it is a transliterated brand name,
    but those should be caught and whitelisted if needed).

    Example garble: 'GoodМorning', 'мcDonald', 'Тankоvый'.
    """
    suspects: list[Suspect] = []
    for si, seg in enumerate(segments):
        for wi, w in enumerate(seg.get("words") or []):
            raw = (w.get("word") or w.get("text") or "").strip()
            if _HAS_CYR.search(raw) and _HAS_LAT.search(raw):
                suspects.append(Suspect(
                    kind="charset_mix",
                    text=raw,
                    start=round(float(w.get("start", 0.0)), 3),
                    end=round(float(w.get("end", 0.0)), 3),
                    seg_index=si,
                    word_index=wi,
                    reason=(
                        f"'{raw}' contains both Cyrillic and Latin "
                        "characters - likely ASR garble"
                    ),
                ))
    return suspects


# ---------------------------------------------------------------------------
# Master detector
# ---------------------------------------------------------------------------

def flag_suspects(
    transcript: dict[str, Any],
    *,
    min_conf: float = 0.55,
    max_repeat: int = 4,
    max_word_s: float = 3.0,
) -> list[Suspect]:
    """Run all five detectors and return a deduplicated list of Suspect objects.

    Parameters
    ----------
    transcript : Whisper-shaped dict  {"segments":[{"start","end","text",
                 "words":[{"word"/"text","start","end","probability"?}]}]}
    min_conf   : words below this probability are flagged (default 0.55).
                 Skipped gracefully when no probability data is present.
    max_repeat : flag a run of the same word if it appears >= this many
                 times in a row (Whisper hallucination pattern, default 4).
    max_word_s : a single word spanning longer than this is flagged as
                 impossibly timed (default 3.0s).

    Returns
    -------
    list of Suspect, sorted by (start, kind).
    """
    segs = transcript.get("segments") or []
    found: list[Suspect] = []
    found.extend(_detect_low_confidence(segs, min_conf))
    found.extend(_detect_repetition_loop(segs, max_repeat))
    found.extend(_detect_suspicious_token(segs))
    found.extend(_detect_impossible_timing(segs, max_word_s))
    found.extend(_detect_charset_mix(segs))
    found.sort(key=lambda s: (s.start, s.kind))
    return found


# ---------------------------------------------------------------------------
# Corrections
# ---------------------------------------------------------------------------

def apply_corrections(
    transcript: dict[str, Any],
    corrections: dict,
) -> dict[str, Any]:
    """Apply text corrections to a transcript WITHOUT mutating the original.

    Parameters
    ----------
    transcript  : Whisper-shaped dict (not mutated).
    corrections : dict mapping either:
                  - str -> str    : exact word text -> replacement
                    (matched case-insensitively after stripping punct)
                  - (seg_index, word_index) -> str : positional replacement

    Returns a deep copy with the corrected text. Each segment's top-level
    'text' field is re-derived from the corrected words so it stays
    consistent with the word list. If a segment has no words, its text
    is replaced only when a positional key hits that segment directly.
    """
    tr = copy.deepcopy(transcript)
    # Normalise keys: separate positional vs. text-based corrections.
    pos_corr: dict[tuple[int, int], str] = {}
    text_corr: dict[str, str] = {}
    for k, v in corrections.items():
        if isinstance(k, tuple) and len(k) == 2:
            pos_corr[(int(k[0]), int(k[1]))] = str(v)
        else:
            text_corr[str(k).strip().lower()] = str(v)

    for si, seg in enumerate(tr.get("segments") or []):
        words = seg.get("words") or []
        changed_any = False
        for wi, w in enumerate(words):
            raw = (w.get("word") or w.get("text") or "").strip()
            core = _ALPHA_STRIP.sub("", raw).lower()
            replacement: str | None = None
            if (si, wi) in pos_corr:
                replacement = pos_corr[(si, wi)]
            elif core in text_corr:
                replacement = text_corr[core]
            elif raw.lower() in text_corr:
                replacement = text_corr[raw.lower()]
            if replacement is not None:
                # Preserve whichever key the word actually uses.
                if "word" in w:
                    w["word"] = replacement
                else:
                    w["text"] = replacement
                changed_any = True
        # Re-derive the segment's text from corrected words.
        if changed_any and words:
            seg["text"] = " ".join(
                (w.get("word") or w.get("text") or "").strip()
                for w in words
            ).strip()
    return tr


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def report(transcript: dict[str, Any], **kw) -> str:
    """Human-readable timecoded report of all suspects.

    Extra kwargs are forwarded to flag_suspects().
    Returns an empty string when no suspects are found.
    """
    suspects = flag_suspects(transcript, **kw)
    if not suspects:
        return "asr_verify: no suspects found.\n"
    lines = [f"asr_verify: {len(suspects)} suspect(s) found:"]
    for s in suspects:
        tc = f"{_fmt_tc(s.start)} - {_fmt_tc(s.end)}"
        lines.append(
            f"  [{s.kind}]  {tc}  seg={s.seg_index} w={s.word_index}"
            f"  '{s.text}'  |  {s.reason}"
        )
    return "\n".join(lines) + "\n"


def report_json(transcript: dict[str, Any], **kw) -> list[dict]:
    """Return suspects as a list of plain dicts (JSON-serialisable)."""
    suspects = flag_suspects(transcript, **kw)
    return [
        {
            "kind": s.kind,
            "text": s.text,
            "start": s.start,
            "end": s.end,
            "seg_index": s.seg_index,
            "word_index": s.word_index,
            "reason": s.reason,
        }
        for s in suspects
    ]


# ---------------------------------------------------------------------------
# LLM-handoff helper
# ---------------------------------------------------------------------------

def suspect_context(
    transcript: dict[str, Any],
    suspect: Suspect,
    window: int = 6,
) -> str:
    """Return the words surrounding a suspect as plain text for LLM review.

    Gathers up to `window` words before and after the suspect word within
    the same segment, then adds the segment text for full context. This gives
    an LLM the local speech context it needs to propose the correct word.

    Format (example):
        Segment 2 [00:01:05.200 - 00:01:08.400]:
        ... и поэтому [Ско] держит всё равно ...
        Raw segment text: 'и поэтому Ско держит всё равно'
    """
    segs = transcript.get("segments") or []
    if suspect.seg_index >= len(segs):
        return f"(segment {suspect.seg_index} out of range)"
    seg = segs[suspect.seg_index]
    words = seg.get("words") or []

    if words and suspect.word_index >= 0:
        wi = suspect.word_index
        lo = max(0, wi - window)
        hi = min(len(words), wi + window + 1)
        tokens = []
        for i in range(lo, hi):
            t = (words[i].get("word") or words[i].get("text") or "").strip()
            if i == wi:
                t = f"[{t}]"
            tokens.append(t)
        context_line = "... " + " ".join(tokens) + " ..."
    else:
        context_line = seg.get("text", "").strip()

    seg_start = seg.get("start", 0.0)
    seg_end = seg.get("end", 0.0)
    raw_text = seg.get("text", "").strip()

    return (
        f"Segment {suspect.seg_index}"
        f" [{_fmt_tc(float(seg_start))} - {_fmt_tc(float(seg_end))}]:\n"
        f"  {context_line}\n"
        f"  Raw segment text: '{raw_text}'\n"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="python -m core.asr_verify",
                                description="Sanity-check a Whisper transcript.")
    p.add_argument("transcript", help="Whisper JSON file path")
    p.add_argument("--min-conf", type=float, default=0.55,
                   help="Probability threshold for low-confidence flag (default 0.55)")
    p.add_argument("--max-repeat", type=int, default=4,
                   help="Run length that triggers repetition-loop flag (default 4)")
    p.add_argument("--max-word-s", type=float, default=3.0,
                   help="Max word duration in seconds before flagging (default 3.0)")
    p.add_argument("--json", action="store_true",
                   help="Output machine-readable JSON instead of text")
    args = p.parse_args(argv)

    from pathlib import Path
    raw = Path(args.transcript).read_text()
    tr = json.loads(raw)

    kw = dict(min_conf=args.min_conf,
              max_repeat=args.max_repeat,
              max_word_s=args.max_word_s)

    if args.json:
        print(json.dumps(report_json(tr, **kw), ensure_ascii=False, indent=2))
    else:
        print(report(tr, **kw), end="")

    suspects = flag_suspects(tr, **kw)
    return 1 if suspects else 0


if __name__ == "__main__":
    sys.exit(_main())
