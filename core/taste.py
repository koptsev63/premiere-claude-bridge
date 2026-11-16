"""core.taste - editor rules from a live cut that have no other home.

Field origin (16.11.2026): Denis, a working editor, cut a 63 s promo by hand
and said what he did. Three of his rules live where the engine already
decides (core.beats, core.rhythm_gate, core.overlays). The two below had no
home: the engine never built a fast series as a phrase and never compared
neighbouring shots by colour. They are standalone here, cheap, pure where
possible; `core.variants` calls `phrase_order` on its ACTION run.

Series as a phrase
  A fast series (a montage of short shots) is read as one phrase:
    * it opens on an IMPACT shot - tagged "impact", else the largest motion
      or brightness jump;
    * its last shot LINKS to the shot after the series - same subject, a
      shared tag, or a close dominant colour;
    * no two shots of the series repeat content (same subject, or the same
      tag set).
  `phrase_order(series, after)` orders a series that way, `check_series()`
  lists what breaks the rules. Metadata is whatever the caller has
  (`ShotMeta`); missing fields are skipped, never guessed. There is no
  tagging model here on purpose: `tags` / `subject` are the hook for one
  (hand tags, a vision pass, a bin's labels).

Neighbours differ in dominant colour, not brightness
  Two shots side by side should differ in dominant HUE. Brightness
  alternation is not the rule (the engine had no brightness-alternation rule
  to replace; `core.framecheck` only flags light jumps inside one clip).
  `dominant_hue(frames)` is the peak of a saturation-weighted hue histogram
  over a few small frames, `hue_distance()` is circular, `hue_flags()` lists
  neighbours closer than MIN_HUE_DEG. `clip_hue()` reads the frames with
  ffmpeg (three 32x18 frames per shot: cheap).

numpy is needed for `dominant_hue` / `clip_hue` only.
"""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass, field
from typing import Iterable, Sequence

MIN_HUE_DEG = 30.0       # neighbours closer than this in dominant hue = same colour
LINK_HUE_DEG = 30.0      # series end and the next shot this close = colour link
HUE_BINS = 36            # 10 degrees per bin
MIN_SAT = 0.20           # below this saturation a pixel has no usable hue
MIN_VAL = 0.15           # below this value (dark) likewise
MIN_CHROMA_SHARE = 0.05  # fewer chromatic pixels than this = achromatic shot
IMPACT_TAG = "impact"
_MARKER_TAGS = {IMPACT_TAG}


# --------------------------------------------------------------------------- #
# Dominant colour
# --------------------------------------------------------------------------- #


def hue_distance(a: float, b: float) -> float:
    """Circular distance between two hues in degrees, 0..180."""
    d = abs(float(a) - float(b)) % 360.0
    return min(d, 360.0 - d)


def dominant_hue(frames, *, bins: int = HUE_BINS, min_sat: float = MIN_SAT,
                 min_val: float = MIN_VAL) -> float | None:
    """Dominant hue in degrees of one or more RGB frames, or None if achromatic.

    frames : an (H, W, 3) array or a list of them, uint8 0..255 or float 0..1.
    Pixels too grey or too dark carry no hue and are left out; the rest vote
    into a hue histogram weighted by saturation. The peak bin (smoothed with
    its two neighbours, circularly) wins and the answer is the circular mean
    of the hues in those three bins.
    """
    import numpy as np

    arrs = frames if isinstance(frames, (list, tuple)) else [frames]
    px = []
    for f in arrs:
        a = np.asarray(f, dtype=np.float32)
        if a.size == 0:
            continue
        if a.max() > 1.0:
            a = a / 255.0
        px.append(a.reshape(-1, 3))
    if not px:
        return None
    p = np.concatenate(px)
    mx, mn = p.max(axis=1), p.min(axis=1)
    delta = mx - mn
    sat = np.where(mx > 0, delta / np.maximum(mx, 1e-9), 0.0)
    keep = (sat >= min_sat) & (mx >= min_val) & (delta > 1e-6)
    if keep.sum() < MIN_CHROMA_SHARE * len(p):
        return None
    p, mx, delta, sat = p[keep], mx[keep], delta[keep], sat[keep]
    r, g, b = p[:, 0], p[:, 1], p[:, 2]
    h = np.where(mx == r, ((g - b) / delta) % 6.0,
                 np.where(mx == g, (b - r) / delta + 2.0, (r - g) / delta + 4.0)) * 60.0
    h = h % 360.0
    width = 360.0 / bins
    idx = np.minimum((h // width).astype(int), bins - 1)
    hist = np.bincount(idx, weights=sat, minlength=bins)
    smooth = hist + np.roll(hist, 1) + np.roll(hist, -1)
    peak = int(np.argmax(smooth))
    sel = np.isin(idx, [(peak - 1) % bins, peak, (peak + 1) % bins])
    rad = np.deg2rad(h[sel])
    w = sat[sel]
    ang = math.degrees(math.atan2(float((np.sin(rad) * w).sum()),
                                  float((np.cos(rad) * w).sum())))
    return round(ang % 360.0, 1)


def hue_flags(hues: Sequence[float | None], min_deg: float = MIN_HUE_DEG) -> list[int]:
    """Indexes i where shot i and shot i+1 share a dominant colour.

    A None hue (achromatic or unknown) is not judged.
    """
    out = []
    for i in range(len(hues) - 1):
        a, b = hues[i], hues[i + 1]
        if a is not None and b is not None and hue_distance(a, b) < min_deg:
            out.append(i)
    return out


def clip_hue(path: str, t_in: float, t_out: float, n: int = 3,
             size: tuple[int, int] = (32, 18)) -> float | None:
    """Dominant hue of a shot: `n` tiny frames spread over [t_in, t_out] via ffmpeg."""
    import numpy as np

    w, h = size
    frames = []
    span = max(0.0, t_out - t_in)
    for k in range(n):
        t = t_in + span * (k + 0.5) / n
        raw = subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(path),
             "-frames:v", "1", "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, check=True).stdout
        if len(raw) >= w * h * 3:
            frames.append(np.frombuffer(raw[:w * h * 3], dtype=np.uint8).reshape(h, w, 3))
    return dominant_hue(frames) if frames else None


# --------------------------------------------------------------------------- #
# Series as a phrase
# --------------------------------------------------------------------------- #


@dataclass
class ShotMeta:
    """What the series rules can use about a shot. Every field but id is optional.

    tags / subject  : content labels - the hook for a tagging pass; "impact"
                      in tags marks an impact shot by hand
    motion          : mean frame-to-frame change (`core.framecheck.motion_profile`)
    brightness_jump : largest brightness jump inside the shot, same scale
    hue             : dominant hue, degrees (`dominant_hue` / `clip_hue`)
    """
    id: str
    tags: tuple[str, ...] = ()
    subject: str = ""
    motion: float | None = None
    brightness_jump: float | None = None
    hue: float | None = None
    extra: dict = field(default_factory=dict)


def _content(s: ShotMeta) -> tuple[str, frozenset]:
    return (s.subject.strip().lower(),
            frozenset(t.lower() for t in s.tags if t.lower() not in _MARKER_TAGS))


def same_content(a: ShotMeta, b: ShotMeta) -> bool:
    """Same subject, or the same (non-empty) tag set."""
    sa, ta = _content(a)
    sb, tb = _content(b)
    return bool((sa and sa == sb) or (ta and ta == tb))


def impact_score(s: ShotMeta) -> float | None:
    """inf for a hand-tagged impact shot, else the larger of motion and
    brightness jump; None when the shot carries neither."""
    if any(t.lower() == IMPACT_TAG for t in s.tags):
        return math.inf
    vals = [v for v in (s.motion, s.brightness_jump) if v is not None]
    return max(vals) if vals else None


def links_to(a: ShotMeta, b: ShotMeta, hue_deg: float = LINK_HUE_DEG) -> bool | None:
    """Does shot a lead into shot b? Same subject or a shared tag, or a close
    dominant colour. None when there is nothing to compare."""
    sa, ta = _content(a)
    sb, tb = _content(b)
    if (sa and sa == sb) or (ta & tb):
        return True
    if a.hue is not None and b.hue is not None:
        return hue_distance(a.hue, b.hue) <= hue_deg
    if (sa or ta) and (sb or tb):
        return False
    return None


def _link_rank(s: ShotMeta, after: ShotMeta) -> tuple[int, float]:
    sa, ta = _content(s)
    sb, tb = _content(after)
    if (sa and sa == sb) or (ta & tb):
        return (0, 0.0)
    if s.hue is not None and after.hue is not None:
        return (1, hue_distance(s.hue, after.hue))
    return (2, 0.0)


def repeats(series: Sequence[ShotMeta]) -> list[tuple[int, int]]:
    """Pairs (i, j), i < j, of series shots with the same content."""
    return [(i, j) for i in range(len(series)) for j in range(i + 1, len(series))
            if same_content(series[i], series[j])]


def phrase_order(series: Sequence[ShotMeta], after: ShotMeta | None = None) -> list[ShotMeta]:
    """Order a fast series as a phrase.

    1. first: the impact shot (highest `impact_score`, earliest on a tie; the
       given first shot stays first when nobody carries impact data);
    2. last: the shot that best links to `after` (shared subject or tag first,
       then the closest dominant hue); the given last shot when there is no
       `after` or nothing to compare;
    3. middle: greedy, each next shot never the same content as the previous
       one and the most different from it in dominant hue; given order when
       there is nothing to compare.
    Repeated content is not dropped here (the caller decides what to cut);
    `check_series` reports it.
    """
    rest = list(series)
    if len(rest) < 2:
        return rest
    scored = [(impact_score(s), i) for i, s in enumerate(rest)]
    if any(sc is not None for sc, _ in scored):
        best = max(scored, key=lambda x: (x[0] if x[0] is not None else -math.inf, -x[1]))[1]
    else:
        best = 0
    first = rest.pop(best)

    last = None
    if after is not None:
        ranks = [(_link_rank(s, after), i) for i, s in enumerate(rest)]
        top = min(ranks)
        if top[0][0] < 2:
            last = rest.pop(top[1])
    if last is None:
        last = rest.pop()

    middle: list[ShotMeta] = []
    prev = first
    while rest:
        comparable = any(s.hue is not None for s in rest) or any(same_content(s, prev) for s in rest)
        if comparable:
            def key(s: ShotMeta, prev: ShotMeta = prev) -> tuple[int, float]:
                clash = 1 if same_content(s, prev) else 0
                d = (hue_distance(s.hue, prev.hue)
                     if s.hue is not None and prev.hue is not None else 0.0)
                return (-clash, d)
            nxt = max(rest, key=key)
        else:
            nxt = rest[0]
        rest.remove(nxt)
        middle.append(nxt)
        prev = nxt
    return [first, *middle, last]


def check_series(series: Sequence[ShotMeta], after: ShotMeta | None = None,
                 min_hue_deg: float = MIN_HUE_DEG) -> list[str]:
    """What breaks the phrase and colour rules, in words (empty = clean)."""
    out: list[str] = []
    if not series:
        return out
    scores = [impact_score(s) for s in series]
    known = [sc for sc in scores if sc is not None]
    if known:
        top = max(known)
        if scores[0] is None or scores[0] < top:
            who = series[scores.index(top)].id
            out.append(f"series opens on {series[0].id}, not on the impact shot {who}")
    if after is not None and links_to(series[-1], after) is False:
        out.append(f"series ends on {series[-1].id}, which does not lead into {after.id} "
                   "(no shared subject or tag, dominant colour too far)")
    for i, j in repeats(series):
        out.append(f"{series[i].id} and {series[j].id} repeat the same content")
    for i in hue_flags([s.hue for s in series], min_hue_deg):
        out.append(f"{series[i].id} and {series[i + 1].id} share a dominant colour "
                   f"({hue_distance(series[i].hue, series[i + 1].hue):.0f} deg apart, "
                   f"want >= {min_hue_deg:.0f})")
    return out


def has_phrase_data(shots: Iterable[ShotMeta]) -> bool:
    """True when any shot carries something the phrase rules can use."""
    return any(s.tags or s.subject or s.motion is not None or s.brightness_jump is not None
               or s.hue is not None for s in shots)
