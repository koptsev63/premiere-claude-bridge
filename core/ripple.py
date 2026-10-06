"""Ripple plan compiler — variants of an EXISTING hand-built sequence.

Field origin (Grave Stakes teaser, 06.10.2026). The director's editor had
cut a 377 s teaser by hand, to the bars of a song. Every version we built
from scratch was rejected ("you drift too far"). What was accepted was a
*declension* of his cut: delete a weak scene here, ripple-insert a block of
AI shots there, keep everything else frame-exact. Six such versions were
built in one evening with this compiler. On the way we paid for every rule
below with a broken export or a wrong frame; they are tests now.

The plan is written in ORIGINAL seconds of the base sequence, in the order
a human thinks:

    ops = [D(224.24, 241.56),            # ripple-delete the rake scene
           I(224.24, 'card1', 0, 5.28),  # then insert, list order = screen order
           I(224.24, 'fight', 0, 8.76),
           A(224.24, 'music'),            # audio-only, no shift, starts with the block
           I(369.28, 'card8', 0, 3.52)]

`compile_plan()` turns it into what the ExtendScript builder
(`core/jsx/ripple_ops.jsx`) must execute, and into a time map that remaps
an SRT of the base onto the variant. Rules encoded, each with the defect
that taught it:

1. ALL deletions first (native QE `extract`, descending), THEN inserts in
   post-deletion coordinates. An inserted clip that later passes through
   `extract` can leave the sequence un-exportable ("low-level exception",
   06.10.2026: clip E1, two hours of bisecting).
2. Inserts at the same anchor: the builder ripples right, so the LAST
   listed must be inserted FIRST for list order to equal screen order.
3. Audio-only placements (`A`) live in FINAL coordinates: deletions before
   the anchor subtract, inserts strictly before the anchor add, inserts AT
   the anchor do not (the music starts with the first card). The first
   build placed the finale room tone 67 s early.
4. A subtitle cue crossing a cut point ends at that point («Давай, Лаци!»
   hung over the stretcher insert); a cue that STARTS inside a deletion but
   ends after it starts at the deletion end (the interview question was
   dropped whole because its first 0.24 s were in the deleted crowd shot).
5. Everything is frame-quantised (`fr`) before it reaches Premiere; a
   10-frame still and a 10.04-s source do not share a grid.
6. Expected length = base − deletions + insertions; the builder asserts the
   built sequence against it (Premiere's `sequence.end` does not shrink
   after deletions, so the export end is the max clip end, not `end`).

Pure Python, no NLE. `core/jsx/ripple_ops.jsx` is the executor; the JSON it
reads is exactly `compile_plan()['variants']`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

Cue = tuple[float, float, str]


def fr(x: float, fps: int = 25) -> float:
    """Quantise seconds to the frame grid (rounded to 1/fps, 2 decimals at 25)."""
    return round(round(x * fps) / fps, 4)


# ---------------------------------------------------------------- op constructors
def D(a: float, b: float, note: str = "") -> dict[str, Any]:
    """Ripple-delete [a, b) of the base (original seconds). All tracks."""
    if b <= a:
        raise ValueError(f"D: b must be > a ({a}, {b})")
    return dict(op="del", a=a, b=b, note=note)


def I(t: float, code: str, i: float, o: float, note: str = "", vt: int = 3) -> dict[str, Any]:
    """Ripple-insert source `code` [i, o) at base time t on video track index vt (0-based).
    Several inserts at the same t appear on screen in list order."""
    if o <= i:
        raise ValueError(f"I: out must be > in ({code}: {i}, {o})")
    return dict(op="ins", t=t, code=code, i=i, o=o, note=note, vt=vt)


def A(t: float, code: str, track: int = 7, note: str = "") -> dict[str, Any]:
    """Audio-only overwrite of source `code` at base time t (no ripple), on audio track index `track`."""
    return dict(op="aud", t=t, code=code, track=track, note=note)


def R(a: float, b: float, track: int, note: str = "") -> dict[str, Any]:
    """Remove audio items lying entirely inside [a, b] on audio track index `track` (original coords,
    executed BEFORE deletions). Note: an item longer than the window is not touched — razor first or
    use a post-build fix (06.10.2026: a 12-s song clip survived a 2.24-s window)."""
    return dict(op="rmaud", a=a, b=b, track=track, note=note)


# ---------------------------------------------------------------- compiler
@dataclass
class CompiledVariant:
    key: str
    name: str
    ops: list[dict[str, Any]]            # builder order: rmaud, dels (desc), inserts (desc, same-t reversed), aud
    expected: float                      # expected sequence length, seconds
    notes: list[str] = field(default_factory=list)
    srt_body: str | None = None
    cues: int = 0

    def as_json(self) -> dict[str, Any]:
        d = dict(key=self.key, name=self.name, ops=self.ops, expected=round(self.expected, 2), notes=self.notes, cues=self.cues)
        return d


def _resolve(ops: list[dict[str, Any]], sources: dict[str, str], dur_of: Callable[[str], float] | None, fps: int) -> tuple[list[dict], list[str]]:
    out, notes = [], []
    for op0 in ops:
        op = dict(op0)
        if op["op"] in ("ins", "aud"):
            p = sources.get(op["code"])
            if p is None:
                notes.append(f"SKIP {op['code']}: no source"); continue
            if op["op"] == "ins":
                if dur_of is not None:
                    d = dur_of(p)
                    if d and op["o"] > d + 1e-3:
                        notes.append(f"CLAMP {op['code']} out {op['o']}->{d:.2f}"); op["o"] = fr(d, fps)
                op["i"], op["o"] = fr(op["i"], fps), fr(op["o"], fps)
            op["item"], op["path"], op["t"] = os.path.basename(p), p, fr(op["t"], fps)
        else:
            op["a"], op["b"] = fr(op["a"], fps), fr(op["b"], fps)
        out.append(op)
    return out, notes


def time_map(ops: list[dict[str, Any]]) -> Callable[[float], float | None]:
    """Original second -> variant second for VIDEO content. None if inside a deletion.
    An insert at t pushes content at x >= t (the inserted block lands before it)."""
    def remap(x: float) -> float | None:
        y = x
        for op in ops:
            if op["op"] == "del":
                if op["a"] <= x < op["b"]:
                    return None
                if x >= op["b"]:
                    y -= op["b"] - op["a"]
            elif op["op"] == "ins":
                if x >= op["t"] - 1e-6:
                    y += op["o"] - op["i"]
        return y
    return remap


def final_time(ops: list[dict[str, Any]], x: float) -> float:
    """Original second -> FINAL second for an audio placement anchored at x: inserts AT x do not shift it."""
    y = x - sum(o["b"] - o["a"] for o in ops if o["op"] == "del" and x >= o["b"])
    return y + sum(o["o"] - o["i"] for o in ops if o["op"] == "ins" and x > o["t"] + 1e-6)


def remap_cues(cues: Iterable[Cue], ops: list[dict[str, Any]], gap: float = 0.04) -> list[Cue]:
    """Remap base subtitle cues onto the variant (rules 4 of the module docstring)."""
    remap = time_map(ops)
    cuts = sorted([o["t"] for o in ops if o["op"] == "ins"] + [o["a"] for o in ops if o["op"] == "del"])
    out: list[Cue] = []
    for a, b, txt in cues:
        for o in ops:
            if o["op"] == "del" and o["a"] <= a < o["b"] < b:
                a = o["b"]
        for c in cuts:
            if a < c < b:
                b = c - gap
        na, nb = remap(a), remap(b - 0.001)
        if na is None or nb is None or nb <= na:
            continue
        out.append((na, nb + 0.001, txt))
    return out


def compile_variant(key: str, name: str, ops: list[dict[str, Any]], sources: dict[str, str], base_len: float,
                    dur_of: Callable[[str], float] | None = None, base_cues: Iterable[Cue] | None = None, fps: int = 25) -> CompiledVariant:
    rops, notes = _resolve(ops, sources, dur_of, fps)
    dels = sorted([o for o in rops if o["op"] == "del"], key=lambda o: -o["a"])

    def after_dels(x: float) -> float:
        return x - sum(o["b"] - o["a"] for o in dels if x >= o["b"])

    ins = [dict(o, t=fr(after_dels(o["t"]), fps), t0=o["t"]) for o in rops if o["op"] == "ins"]
    order = sorted(range(len(ins)), key=lambda k: (-ins[k]["t"], -k))     # same t: last listed first
    aud = [dict(o, t=fr(final_time(rops, o["t"]), fps), t0=o["t"]) for o in rops if o["op"] == "aud"]
    seq = [o for o in rops if o["op"] == "rmaud"] + dels + [ins[k] for k in order] + aud
    expected = base_len - sum(o["b"] - o["a"] for o in dels) + sum(o["o"] - o["i"] for o in rops if o["op"] == "ins")
    cv = CompiledVariant(key=key, name=name, ops=seq, expected=expected, notes=notes)
    if base_cues is not None:
        cues = remap_cues(list(base_cues), rops)
        cv.srt_body = srt_dump(cues); cv.cues = len(cues)
    return cv


def compile_plan(base_id: str, bin_name: str, variants: dict[str, dict[str, Any]], sources: dict[str, str], base_len: float,
                 dur_of: Callable[[str], float] | None = None, base_cues: Iterable[Cue] | None = None,
                 titles: dict[str, str] | None = None, srt_dir: str | None = None, rebuild: bool = True, fps: int = 25) -> dict[str, Any]:
    """variants: {key: {name, ops}}. Returns the JSON the jsx builder reads; writes SRT files into srt_dir when given."""
    out: dict[str, Any] = dict(base_id=base_id, bin=bin_name, titles=titles or {}, variants=[])
    cues = list(base_cues) if base_cues is not None else None
    for key, v in variants.items():
        cv = compile_variant(key, v["name"], v["ops"], sources, base_len, dur_of, cues, fps)
        d = cv.as_json(); d["rebuild"] = rebuild
        if cv.srt_body is not None and srt_dir:
            os.makedirs(srt_dir, exist_ok=True)
            h = hashlib.md5(cv.srt_body.encode()).hexdigest()[:6]
            d["srt"] = os.path.join(srt_dir, f"{key}_{h}.srt")
            with open(d["srt"], "w", encoding="utf-8") as f:
                f.write(cv.srt_body)
        out["variants"].append(d)
    return out


# ---------------------------------------------------------------- srt
_TC = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")


def _tc(s: str) -> float:
    m = _TC.match(s.strip())
    if not m:
        raise ValueError(f"bad timecode {s!r}")
    return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4]) / 1000


def srt_load(text: str) -> list[Cue]:
    cues: list[Cue] = []
    for blk in text.strip().replace("\r", "").split("\n\n"):
        ls = blk.strip().split("\n")
        if len(ls) < 3 or "-->" not in ls[1]:
            continue
        a, b = ls[1].split("-->")
        cues.append((_tc(a), _tc(b), "\n".join(ls[2:])))
    return cues


def srt_fmt(x: float) -> str:
    ms = int(round(x * 1000))
    return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


def srt_dump(cues: Iterable[Cue]) -> str:
    return "\n\n".join(f"{n + 1}\n{srt_fmt(a)} --> {srt_fmt(b)}\n{t}" for n, (a, b, t) in enumerate(cues)) + "\n"


def dump_json(plan: dict[str, Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)
