"""NLE-neutral review loop — assemble, watch, critique, patch, re-render.

This is the "self-review" step: the assembler builds a rough cut, the
perception layer (`/watch`) lets Claude see it, the editing brain critiques
it against Murch's Rule of Six, a structured patch is applied to the
cutlist, and it re-renders to ANY backend. The loop is identical regardless
of editor because it operates on the `Cutlist`, not on Premiere/Resolve.

Honest boundary: *taste is not in this file.* The qualitative critique is
LLM-driven — Claude reads the `/watch` frames and applies
`skills/film-editing/SKILL.md`. What the harness contributes is everything
deterministic around that judgement:

  * `analyze_cutlist()` — the machine-checkable Murch arithmetic
    (Rule-of-Six §VII ratio rule, monotony, beat-type pacing). Objective
    inputs to the critique, not a replacement for it.
  * `build_rough_cut_plan()` — an NLE-free ffmpeg rough assembly so the
    loop runs with zero NLE installed (the way the Grave Stakes teasers
    were actually built).
  * `watch_plan()` — the exact `/watch` invocations to perceive the cut.
  * `CutlistPatch.apply()` — structured, validated, immutable edits.
  * `ReviewLoop` — iteration history + version diff.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.cutlist import Cutlist

# Beat-type → (min, max) seconds, from SKILL.md §VII (Murch + practice).
# Keyed by substrings that appear in cut labels in real cutlists.
_BEAT_PACING = {
    "hook": (4, 6),
    "reveal": (4, 6),
    "comedy": (4, 5),
    "action": (2, 4),
    "pit": (6, 8),
    "center": (6, 8),
    "stakes": (3, 8),
    "breath": (5, 8),
    "emotion": (6, 8),
    "interview": (5, 7),
    "title": (3, 5),
    "payoff": (6, 10),
    "trophy": (3, 8),
}


# --------------------------------------------------------------------------- #
# Deterministic Murch analysis
# --------------------------------------------------------------------------- #


@dataclass
class CutlistAnalysis:
    shortest: float
    longest: float
    ratio: float                       # longest / shortest
    ratio_ok: bool                     # SKILL §VII: 2x..4x
    monotony_runs: list[tuple[int, float]]   # (start_index, length) runs >=4
    beat_pacing_flags: list[str]
    notes: list[str]

    def is_clean(self) -> bool:
        return (
            self.ratio_ok
            and not self.monotony_runs
            and not self.beat_pacing_flags
        )


def analyze_cutlist(cutlist: Cutlist) -> CutlistAnalysis:
    """Compute the deterministic Murch checks (objective critique inputs)."""
    ordered = sorted(cutlist.cuts, key=lambda c: c.offset)
    durs = [c.duration for c in ordered]
    shortest = min(durs)
    longest = max(durs)
    ratio = round(longest / shortest, 3) if shortest else 0.0
    ratio_ok = 2.0 <= ratio <= 4.0

    # SKILL §X: no 4 consecutive shots the same length
    monotony: list[tuple[int, float]] = []
    run_start = 0
    for i in range(1, len(durs) + 1):
        if i < len(durs) and abs(durs[i] - durs[run_start]) < 1e-6:
            continue
        run_len = i - run_start
        if run_len >= 4:
            monotony.append((run_start, durs[run_start]))
        run_start = i

    # SKILL §VII: beat-type pacing
    flags: list[str] = []
    for idx, c in enumerate(ordered):
        label = (c.label or "").lower()
        for key, (lo, hi) in _BEAT_PACING.items():
            if key in label:
                if not (lo <= c.duration <= hi):
                    flags.append(
                        f"cut[{idx}] '{c.label}' is {c.duration:g}s; "
                        f"'{key}' beats want {lo}-{hi}s (§VII)"
                    )
                break

    notes: list[str] = []
    if not ratio_ok:
        notes.append(
            f"longest/shortest = {ratio} (want 2-4x, §VII). "
            f"{'too monotone' if ratio < 2 else 'rhythm too jagged'}."
        )
    if monotony:
        notes.append(
            f"{len(monotony)} run(s) of 4+ same-length shots (§X)."
        )
    return CutlistAnalysis(
        shortest=shortest,
        longest=longest,
        ratio=ratio,
        ratio_ok=ratio_ok,
        monotony_runs=monotony,
        beat_pacing_flags=flags,
        notes=notes,
    )


# --------------------------------------------------------------------------- #
# Perception plan (/watch)
# --------------------------------------------------------------------------- #


def watch_plan(
    cutlist: Cutlist, media_dir: str | None = None
) -> list[str]:
    """The exact `/watch` invocations to perceive each cut's window.

    Claude runs these and Reads the frames + transcript to critique the
    assembly. Perception is NLE-independent (it reads media files).
    """
    cmds: list[str] = []
    for c in cutlist.cuts:
        path = (
            str(Path(media_dir) / c.clip) if media_dir else c.clip
        )
        cmds.append(
            f'python3 skills/watch/scripts/watch.py "{path}" '
            f"--start {c.in_:g} --end {c.out:g}"
        )
    return cmds


# --------------------------------------------------------------------------- #
# NLE-free rough assembler (ffmpeg)
# --------------------------------------------------------------------------- #


def build_rough_cut_plan(
    cutlist: Cutlist, media_dir: str, out_path: str
) -> dict[str, Any]:
    """Plan an ffmpeg rough cut straight from the cutlist (no NLE).

    Returns a structured plan (segment list + concat/ffmpeg argv) so it is
    unit-testable; `render_rough_cut()` executes it. This is how the Grave
    Stakes teasers were actually built — it makes the review loop runnable
    with zero NLE installed.
    """
    segments = []
    for i, c in enumerate(sorted(cutlist.cuts, key=lambda x: x.offset)):
        src = str(Path(media_dir) / c.clip)
        segments.append(
            {
                "index": i,
                "src": src,
                "ss": c.in_,
                "to": c.out,
                "duration": c.duration,
                "label": c.label,
            }
        )
    seg_args: list[list[str]] = [
        ["-ss", f"{s['ss']:g}", "-to", f"{s['to']:g}", "-i", s["src"]]
        for s in segments
    ]
    n = len(segments)
    concat = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(n))
    filtergraph = f"{concat}concat=n={n}:v=1:a=1[v][a]"
    argv = ["ffmpeg", "-y"]
    for sa in seg_args:
        argv += sa
    argv += [
        "-filter_complex",
        filtergraph,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-crf",
        "18",
        "-c:a",
        "aac",
        out_path,
    ]
    return {
        "segments": segments,
        "argv": argv,
        "out": out_path,
        "fps": cutlist.fps,
    }


def render_rough_cut(
    cutlist: Cutlist, media_dir: str, out_path: str
) -> str:
    import subprocess

    plan = build_rough_cut_plan(cutlist, media_dir, out_path)
    subprocess.run(plan["argv"], check=True, capture_output=True)
    return out_path


# --------------------------------------------------------------------------- #
# Structured, validated, immutable patch
# --------------------------------------------------------------------------- #


@dataclass
class CutlistPatch:
    """A structured edit produced from a critique.

    All ops are by cut index (post offset-sort, as analyze/watch report).
    `apply()` returns a NEW validated Cutlist; the input is never mutated.
    """

    adjust: dict[int, dict[str, float]] = field(default_factory=dict)
    drop: list[int] = field(default_factory=list)
    reorder: list[int] | None = None         # new full ordering of indices
    add_markers: list[dict[str, Any]] = field(default_factory=list)

    def apply(self, cutlist: Cutlist) -> Cutlist:
        cl = copy.deepcopy(cutlist)
        ordered = sorted(cl.cuts, key=lambda c: c.offset)

        for idx, deltas in self.adjust.items():
            c = ordered[idx]
            if "in" in deltas:
                c.in_ = float(deltas["in"])
            if "out" in deltas:
                c.out = float(deltas["out"])
            if "offset" in deltas:
                c.offset = float(deltas["offset"])

        keep = [c for i, c in enumerate(ordered) if i not in set(self.drop)]

        if self.reorder is not None:
            survivors = [
                i for i in self.reorder if i not in set(self.drop)
            ]
            by_index = {i: ordered[i] for i in range(len(ordered))}
            keep = [by_index[i] for i in survivors]
            # re-pack offsets contiguously in the new order
            cursor = 0.0
            for c in keep:
                c.offset = round(cursor, 6)
                cursor = round(cursor + c.duration, 6)

        cl.cuts = keep
        for m in self.add_markers:
            from core.cutlist import Marker

            cl.markers.append(Marker.from_dict(m))

        errs = cl.validate()
        if errs:
            raise ValueError(
                "patch produced an invalid cutlist:\n  - "
                + "\n  - ".join(errs)
            )
        return cl


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #


@dataclass
class ReviewIteration:
    n: int
    cutlist: Cutlist
    analysis: CutlistAnalysis


class ReviewLoop:
    """Holds the version history across review iterations.

    Typical use, driven by Claude:

        loop = ReviewLoop(cutlist)
        loop.analyze()                     # deterministic Murch checks
        # ...Claude runs loop.watch_plan(), reads frames, critiques...
        loop.apply(patch)                  # structured edit -> new version
        adapter.apply_cutlist(loop.current)  # re-render to ANY backend
    """

    def __init__(self, cutlist: Cutlist) -> None:
        self.history: list[ReviewIteration] = []
        self._push(cutlist)

    def _push(self, cl: Cutlist) -> None:
        self.history.append(
            ReviewIteration(
                n=len(self.history),
                cutlist=cl,
                analysis=analyze_cutlist(cl),
            )
        )

    @property
    def current(self) -> Cutlist:
        return self.history[-1].cutlist

    @property
    def analysis(self) -> CutlistAnalysis:
        return self.history[-1].analysis

    def watch_plan(self, media_dir: str | None = None) -> list[str]:
        return watch_plan(self.current, media_dir)

    def apply(self, patch: CutlistPatch) -> Cutlist:
        self._push(patch.apply(self.current))
        return self.current

    def diff(self, a: int = 0, b: int = -1) -> dict[str, Any]:
        """Coarse version diff: cut count, total duration, ratio."""
        ca, cb = self.history[a].cutlist, self.history[b].cutlist

        def total(cl: Cutlist) -> float:
            return round(
                max((c.timeline_end for c in cl.cuts), default=0.0), 3
            )

        return {
            "cuts": (len(ca.cuts), len(cb.cuts)),
            "total_sec": (total(ca), total(cb)),
            "ratio": (
                self.history[a].analysis.ratio,
                self.history[b].analysis.ratio,
            ),
        }
