"""Smart media library — tag, search, and pull selects into a sequence.

Built clean-room (MIT) over our own analysis `report.json`. It answers the
editor's real pains (Denis): "find the bits I mean", "pull a character's
lines", "split them into their own sequence", "stop losing things toward
the end". Inspired by the idea of a tagged/searchable media bin; none of
anyone else's code.

Honest boundaries:
- **No face recognition.** A character is found by what they *say*
  (the Whisper transcript) plus an optional manual tag — never by
  scanning faces.
- Search quality is bounded by transcript quality; noisy field audio
  gives imperfect text. It's a strong selects tool, not magic.
- Sentiment is a *rough* lexical heuristic, labelled as such.

The payoff: `to_cutlist()` turns any search result into a `Cutlist`, so
the existing adapters drop the matches into Premiere/Resolve/FCP as a new
**sequence** — that is the "split into separate timelines" Denis asked for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.cutlist import Cut, Cutlist

# rough sentiment lexicons (EN + RU), deliberately small and honest
_POS = {
    "good", "great", "win", "won", "happy", "love", "best", "yes", "wow",
    "хорошо", "отлично", "победа", "рад", "круто", "да", "супер", "класс",
}
_NEG = {
    "bad", "no", "lose", "lost", "fail", "hard", "tired", "angry", "hate",
    "плохо", "нет", "тяжело", "устал", "проиграл", "злой", "сложно",
}
_WORD = re.compile(r"[\w']+", re.UNICODE)


@dataclass
class Clip:
    name: str
    duration: float
    motion: float
    peak_db: float | None
    peak_time: float | None
    speech: str
    tags: set[str] = field(default_factory=set)

    @classmethod
    def from_rec(cls, r: dict[str, Any]) -> "Clip":
        return cls(
            name=Path(str(r.get("name") or r.get("clip") or "")).name,
            duration=float(r.get("duration") or 0.0),
            motion=float(r.get("motion_score") or 0.0),
            peak_db=(float(r["audio_peak_db"])
                     if r.get("audio_peak_db") is not None else None),
            peak_time=(float(r["audio_peak_time_sec"])
                       if r.get("audio_peak_time_sec") is not None else None),
            speech=str(r.get("speech_text") or ""),
            tags=set(r.get("tags") or []),
        )


def rough_sentiment(text: str) -> str:
    toks = {t.lower() for t in _WORD.findall(text)}
    p, n = len(toks & _POS), len(toks & _NEG)
    if p > n:
        return "positive"
    if n > p:
        return "negative"
    return "neutral"


def auto_tags(c: Clip) -> set[str]:
    """Derived tags from the signals we already compute."""
    t: set[str] = set(c.tags)
    if c.motion >= 1.0:
        t.add("action")
    elif c.motion >= 0.3:
        t.add("movement")
    else:
        t.add("static")
    if c.peak_db is not None and c.peak_db >= -16:
        t.add("loud")
    if c.speech.strip():
        t.add("speech")
        t.add(f"mood:{rough_sentiment(c.speech)}")
    else:
        t.add("silent")
    return t


@dataclass
class Hit:
    clip: str
    score: float
    where: list[str]          # "transcript" | "tag" | "name"
    snippet: str
    peak_time: float | None


class MediaLibrary:
    def __init__(self, report: list[dict[str, Any]]) -> None:
        self.clips: list[Clip] = [Clip.from_rec(r) for r in report]
        for c in self.clips:
            c.tags = auto_tags(c)

    def by_name(self) -> dict[str, Clip]:
        return {c.name: c for c in self.clips}

    def all_tags(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for c in self.clips:
            for tag in c.tags:
                counts[tag] = counts.get(tag, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    # ---- search -------------------------------------------------------- #

    def _snippet(self, text: str, term: str) -> str:
        i = text.lower().find(term)
        if i < 0:
            return ""
        a, b = max(0, i - 25), min(len(text), i + len(term) + 25)
        return ("..." if a else "") + text[a:b].strip() + (
            "..." if b < len(text) else "")

    def search(self, query: str, mode: str = "and") -> list[Hit]:
        """Keyword search over transcript + tags + filename.

        `mode`: 'and' (all terms) or 'or' (any term). Transcript matches
        score highest (that's dialogue), then tags, then name.
        """
        terms = [t.lower() for t in _WORD.findall(query)]
        if not terms:
            return []
        hits: list[Hit] = []
        for c in self.clips:
            speech_l, name_l = c.speech.lower(), c.name.lower()
            where: list[str] = []
            score = 0.0
            snippet = ""
            matched = 0
            for term in terms:
                hit_here = False
                if term in speech_l:
                    score += 3.0
                    where.append("transcript")
                    snippet = snippet or self._snippet(c.speech, term)
                    hit_here = True
                if any(term in tag.lower() for tag in c.tags):
                    score += 1.5
                    where.append("tag")
                    hit_here = True
                if term in name_l:
                    score += 1.0
                    where.append("name")
                    hit_here = True
                if hit_here:
                    matched += 1
            ok = matched == len(terms) if mode == "and" else matched > 0
            if ok and score > 0:
                # tiebreak: livelier clips first
                score += min(c.motion, 3.0) * 0.1
                hits.append(Hit(c.name, round(score, 3),
                                sorted(set(where)), snippet, c.peak_time))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits

    def find_lines(self, phrase: str) -> list[Hit]:
        """Find clips whose transcript contains a phrase (a character's
        line). Phrase is matched as-is (not split into words)."""
        p = phrase.lower().strip()
        if not p:
            return []
        out: list[Hit] = []
        for c in self.clips:
            if p in c.speech.lower():
                out.append(Hit(c.name, 1.0, ["transcript"],
                               self._snippet(c.speech, p), c.peak_time))
        return out

    # ---- the payoff: matches -> a new sequence ------------------------- #

    def to_cutlist(
        self,
        hits: list[Hit],
        name: str,
        fps: float = 25.0,
        window_sec: float = 6.0,
    ) -> Cutlist:
        """Assemble search results into a Cutlist (one cut per hit), so an
        adapter can drop them into the NLE as a dedicated sequence.
        Each cut is a `window_sec` slice around the clip's decisive moment
        (audio peak), clamped to the real duration."""
        by = self.by_name()
        cuts: list[Cut] = []
        off = 0.0
        for h in hits:
            c = by.get(h.clip)
            if c is None:
                continue
            d = max(min(window_sec, c.duration - 0.05), 0.5) \
                if c.duration else window_sec
            if c.peak_time is not None and c.duration and c.peak_time <= c.duration:
                start = min(max(0.0, c.peak_time - d * 0.5),
                            max(0.0, c.duration - d))
            else:
                start = 0.0
            cuts.append(Cut(clip=h.clip, in_=round(start, 2),
                            out=round(start + d, 2), offset=round(off, 2),
                            label=(h.snippet or h.clip)[:40]))
            off += d
        return Cutlist(sequence_name=name, fps=fps, cuts=cuts,
                       resolution="1920x1080", total_duration_sec=round(off, 2))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys

    p = argparse.ArgumentParser(prog="python -m core.library")
    p.add_argument("report", help="analysis report.json")
    sub = p.add_subparsers(dest="cmd", required=True)
    ps = sub.add_parser("search"); ps.add_argument("query")
    ps.add_argument("--or", dest="or_", action="store_true")
    pl = sub.add_parser("lines"); pl.add_argument("phrase")
    sub.add_parser("tags")
    pc = sub.add_parser("sequence")  # search -> cutlist json
    pc.add_argument("query"); pc.add_argument("--out", required=True)
    pc.add_argument("--name", default="Selects")
    args = p.parse_args(argv)

    lib = MediaLibrary(json.loads(Path(args.report).read_text()))

    if args.cmd == "tags":
        for tag, n in lib.all_tags().items():
            print(f"{n:4d}  {tag}")
        return 0
    if args.cmd == "search":
        for h in lib.search(args.query, "or" if args.or_ else "and"):
            print(f"{h.score:5.1f}  {h.clip}  [{','.join(h.where)}]  "
                  f"{h.snippet}")
        return 0
    if args.cmd == "lines":
        for h in lib.find_lines(args.phrase):
            print(f"{h.clip}  @{h.peak_time}  {h.snippet}")
        return 0
    if args.cmd == "sequence":
        cl = lib.to_cutlist(lib.search(args.query), args.name)
        cl.save(args.out)
        print(f"{len(cl.cuts)} clips -> {args.out} "
              f"(sequence '{args.name}', {cl.total_duration_sec}s)")
        return 0
    return 2


if __name__ == "__main__":
    import sys

    sys.exit(_main())
