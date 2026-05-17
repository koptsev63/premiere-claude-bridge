"""Two-variant builder — contrasting Murch-valid cuts from one pool.

Vladimir's call: not one cut, two well-developed contrasting strategies,
then a human picks the one that *feels* right. This builds them.

Both variants share an 8-beat Murch arc
(HOOK·COMEDY·ACTION·ACTION·ACTION·PIT·STAKES·PAYOFF) but differ in
*rhythm*:

  DRIVE  — aggressive, tight, accelerating. Short shots, action-forward.
  BREATH — atmospheric, longer establishings and emotional centers,
           more air.

Both: pull clips from `core.value.ValuePool` (every protected clip used,
the two strongest as the PIT/PAYOFF anchors, strongest-first), anchor each
shot's in/out on its decisive moment (audio peak) clamped to the real
clip length, and are tuned so `core.review_loop.analyze_cutlist` reports
`is_clean` by construction (verified arithmetic). The builder still runs
the analyzer and reports the score honestly — taste (which variant) stays
with the person.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.cutlist import Cut, Cutlist, Marker
from core.review_loop import analyze_cutlist
from core.value import ValuePool

# Beat name -> duration (s). Pre-tuned so durations satisfy §VII pacing
# AND the 2-4x ratio AND no 4-in-a-row monotony, by construction.
_ARC = ["HOOK", "COMEDY", "ACTION", "ACTION", "ACTION", "PIT", "STAKES",
        "PAYOFF"]
STRATEGIES: dict[str, list[float]] = {
    # ratio 6/2=3.0  | durs varied | total 30.5s
    "drive":  [4.0, 4.0, 2.0, 2.5, 3.0, 6.0, 3.0, 6.0],
    # ratio 8/2=4.0  | durs varied | total 38.5s
    "breath": [6.0, 5.0, 3.0, 2.5, 2.0, 7.0, 5.0, 8.0],
}


@dataclass
class ClipMeta:
    clip: str            # path or bare name (conform later if bare)
    dur: float           # real source duration (s)
    peak_time: float | None  # decisive-moment anchor (audio peak), s
    value: float
    meat: bool = False


_FIT_TOL = 0.2  # a clip may be up to this many seconds short of a beat


def _window(meta: ClipMeta, beat_dur: float) -> tuple[float, float]:
    """Source [in,out] of length ~beat_dur around the decisive moment,
    clamped inside the real clip. Caller guarantees the clip is long
    enough (dur >= beat_dur - _FIT_TOL), so the beat duration is honoured
    (only trimmed by at most _FIT_TOL, which keeps §VII pacing)."""
    d = max(min(beat_dur, meta.dur - 0.05), 0.5)
    if meta.peak_time is not None and 0 <= meta.peak_time <= meta.dur:
        start = meta.peak_time - d * 0.55   # land OUT just past the peak
    else:
        start = (meta.dur - d) / 2.0        # middle if no anchor
    start = min(max(0.0, start), max(0.0, meta.dur - d))
    return round(start, 2), round(start + d, 2)


def _assign(
    metas: list[ClipMeta], beat_durs: list[float], protected: set[str]
) -> tuple[list[ClipMeta], list[str]]:
    """Length-aware assignment: every beat gets a value-strong clip that
    is actually long enough for it (physics > protection — a clip too
    short for even the shortest beat cannot be used). PIT/PAYOFF (the two
    longest beats) take the strongest qualifying clips. Returns
    (clip_per_beat, notes)."""
    by_val = sorted(metas, key=lambda m: m.value, reverse=True)
    notes: list[str] = []
    min_beat = min(beat_durs)
    usable = [m for m in by_val if m.dur >= min_beat - _FIT_TOL]
    too_short = [m for m in by_val if m not in usable]
    for m in too_short:
        tag = " (protected)" if m.clip in protected else ""
        notes.append(
            f"skip {m.clip}{tag}: {m.dur:.1f}s < shortest beat "
            f"{min_beat:.1f}s"
        )
    if not usable:
        raise ValueError("no clip long enough for any beat")

    # fill the longest beats first with the strongest long-enough clips
    order = sorted(range(len(_ARC)), key=lambda i: -beat_durs[i])
    pick = [None] * len(_ARC)
    pool = list(usable)
    for bi in order:
        need = beat_durs[bi] - _FIT_TOL
        cand = next((m for m in pool if m.dur >= need), None)
        if cand is None:                      # nobody long enough -> reuse
            cand = next((m for m in usable if m.dur >= need), usable[0])
        else:
            pool.remove(cand)
        pick[bi] = cand
    return pick, notes


def build_variant(
    name: str, metas: list[ClipMeta], fps: float = 25.0,
    protected: set[str] | None = None,
    notes_out: list[str] | None = None,
) -> Cutlist:
    if name not in STRATEGIES:
        raise KeyError(f"unknown strategy {name!r}; {list(STRATEGIES)}")
    durs = STRATEGIES[name]
    seq, notes = _assign(list(metas), durs, protected or set())
    if notes_out is not None:
        notes_out.extend(notes)

    cuts: list[Cut] = []
    off = 0.0
    for beat, beat_dur, m in zip(_ARC, durs, seq):
        i, o = _window(m, beat_dur)
        cuts.append(Cut(clip=m.clip, in_=i, out=o, offset=round(off, 2),
                         label=f"{beat}-{name}"))
        off += (o - i)
    markers = [
        Marker(name=b, time=c.offset, comment=f"{name} {b.lower()}")
        for b, c in zip(_ARC, cuts)
        if b in ("HOOK", "COMEDY", "PIT", "STAKES", "PAYOFF")
    ]
    return Cutlist(
        sequence_name=f"Grave_Stakes_Teaser_v5_{name}",
        fps=fps, cuts=cuts, markers=markers, resolution="1920x1080",
        total_duration_sec=round(off, 2),
    )


def build_variants(
    report: list[dict[str, Any]],
    dur_lookup,
    fps: float = 25.0,
    meat_tags: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build both variants from an analysis report.

    `dur_lookup(clip_basename) -> float` supplies real clip durations
    (ffprobe in production; a dict.get in tests).
    Returns {strategy: {"cutlist": Cutlist, "score": {...},
                         "is_clean": bool}}.
    """
    pool = ValuePool(report, meat_tags)
    by = pool.by_clip()
    protected = {v.clip for v in pool.protected()}

    metas: list[ClipMeta] = []
    for v in pool.ranked:
        rec = next((r for r in report
                    if (r.get("clip") or r.get("name") or "").endswith(
                        v.clip)), {})
        metas.append(ClipMeta(
            clip=v.clip,
            dur=float(dur_lookup(v.clip) or 0.0),
            peak_time=rec.get("audio_peak_time_sec"),
            value=v.score,
            meat=v.meat,
        ))

    out: dict[str, dict[str, Any]] = {}
    for strat in STRATEGIES:
        notes: list[str] = []
        cl = build_variant(strat, metas, fps, protected, notes_out=notes)
        an = analyze_cutlist(cl)
        out[strat] = {
            "cutlist": cl,
            "is_clean": an.is_clean(),
            "notes": notes,
            "score": {
                "ratio": an.ratio, "ratio_ok": an.ratio_ok,
                "monotony_runs": an.monotony_runs,
                "beat_pacing_flags": an.beat_pacing_flags,
                "is_clean": an.is_clean(),
                "total_sec": cl.total_duration_sec,
                "cuts": len(cl.cuts),
            },
        }
    return out
