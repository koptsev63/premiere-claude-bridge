"""Clip value — the "meat" model: what is the strong material, ranked.

Honest boundary first: no algorithm has dramaturgical taste. This is a
deterministic *proxy* for "how much meat is in this clip", plus an explicit
human/LLM override (`meat_tag`) for the real call — Claude watches the top
candidates through `/watch` and tags the money moment by timecode; a tagged
clip is protected and ranked top regardless of the metrics. The proxy just
makes sure nothing strong gets *lost* before a person looks.

Composite per clip (all from the analysis report.json):
  value = 0.45 * audio_peak_norm   (a loud peak = applause/shout/strike =
                                    emotional anchor — Murch rule 1)
        + 0.40 * motion_norm        (action energy — story/rhythm)
        + 0.15 * meat_bonus         (1.0 if human/LLM-tagged, else 0)
        - shake_penalty             (only when shaky AND low-motion: jitter
                                     with nothing happening is just bad;
                                     shaky + high-motion is deliberate energy
                                     and is NOT penalized)

`rank_clips()` -> sorted [(clip, score, why)]. `ValuePool` exposes
`protected()` (must never be dropped) and `anchors(n)` (the very top, for
the emotional-center beats PIT/PAYOFF).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

W_AUDIO = 0.45
W_MOTION = 0.40
W_MEAT = 0.15
PROTECT_QUANTILE = 0.30  # top 30% (or any meat-tagged) are protected


def _norm(values: list[float]) -> dict[int, float]:
    """Min-max to [0,1]; all-equal -> 0.5 (no signal, stay neutral)."""
    if not values:
        return {}
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return {i: 0.5 for i in range(len(values))}
    return {i: (v - lo) / (hi - lo) for i, v in enumerate(values)}


@dataclass
class ValueScore:
    clip: str
    score: float
    motion: float
    audio_db: float
    shake: float | None
    meat: bool
    meat_time: float | None
    why: list[str] = field(default_factory=list)


def _clip_name(rec: dict[str, Any]) -> str:
    n = rec.get("clip") or rec.get("name") or rec.get("file") or ""
    return Path(str(n)).name


def rank_clips(
    report: list[dict[str, Any]],
    meat_tags: dict[str, Any] | None = None,
) -> list[ValueScore]:
    """Rank report clips by composite value, descending.

    `meat_tags`: {clip_basename: True | {"time": float}} — the human/LLM
    override. A tagged clip gets the meat bonus and is force-floored to
    the top band so it cannot rank low.
    """
    meat_tags = meat_tags or {}
    if not report:
        return []

    motions = [float(r.get("motion_score") or 0.0) for r in report]
    # audio: louder (less negative dB) is better; missing -> very low
    audios = [float(r.get("audio_peak_db") if r.get("audio_peak_db")
                     is not None else -90.0) for r in report]
    mN = _norm(motions)
    aN = _norm(audios)

    out: list[ValueScore] = []
    for i, r in enumerate(report):
        name = _clip_name(r)
        tag = meat_tags.get(name)
        is_meat = bool(tag)
        meat_time = (tag.get("time") if isinstance(tag, dict) else None)

        m, a = mN.get(i, 0.0), aN.get(i, 0.0)
        score = W_AUDIO * a + W_MOTION * m + (W_MEAT if is_meat else 0.0)

        why = [f"audio={a:.2f}", f"motion={m:.2f}"]
        sh = r.get("shake_score")
        if sh is not None and sh > 0:
            # penalty only if jittery AND not much happening
            if m < 0.35:
                pen = min(0.25, (float(sh) / 100.0) * (0.35 - m) / 0.35)
                score -= pen
                if pen > 0.01:
                    why.append(f"-shake {pen:.2f} (jitter, low motion)")
            else:
                why.append("shake kept (motion = deliberate energy)")
        if is_meat:
            why.append("MEAT (human/LLM tagged)")

        out.append(ValueScore(
            clip=name, score=round(score, 4), motion=m, audio_db=audios[i],
            shake=(float(sh) if sh is not None else None),
            meat=is_meat, meat_time=meat_time, why=why,
        ))

    # meat-tagged clips are floored above every untagged clip
    if any(v.meat for v in out):
        top_untagged = max(
            (v.score for v in out if not v.meat), default=0.0
        )
        for v in out:
            if v.meat and v.score <= top_untagged:
                v.score = round(top_untagged + 0.01, 4)

    out.sort(key=lambda v: v.score, reverse=True)
    return out


class ValuePool:
    """Ranked clips with protected / anchor tiers for the cut builder."""

    def __init__(
        self,
        report: list[dict[str, Any]],
        meat_tags: dict[str, Any] | None = None,
    ) -> None:
        self.ranked = rank_clips(report, meat_tags)

    def by_clip(self) -> dict[str, ValueScore]:
        return {v.clip: v for v in self.ranked}

    def protected(self) -> list[ValueScore]:
        """Must never be dropped: any meat-tagged + the top quantile."""
        if not self.ranked:
            return []
        n = max(1, round(len(self.ranked) * PROTECT_QUANTILE))
        top = set(v.clip for v in self.ranked[:n])
        return [v for v in self.ranked
                if v.meat or v.clip in top]

    def anchors(self, n: int = 2) -> list[ValueScore]:
        """The very strongest — reserve for the emotional-center beats."""
        return self.ranked[:max(0, n)]

    def order(self) -> list[str]:
        """Clip names, strongest first (use-the-best-first)."""
        return [v.clip for v in self.ranked]
