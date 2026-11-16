"""Rhythm gate - Murch's machine-checkable rhythm rules as a delivery gate.

Run on a locked cut before it is shown or delivered:

    python3 -m core.rhythm_gate SOURCE --genre trailer [--fps 25] [--brief DIR] [--music TRACK] [--json]

SOURCE is one of:
  * a bridge cutlist .json ("cuts": [...]) - exact shot lengths plus beat labels;
  * a Premiere timeline dump .json (the answer of `pr_list_timeline`, "video": [...]);
  * a rendered media file - shot lengths come from ffmpeg scene detection
    (a dissolve can merge two shots, a flash can add one: prefer the timeline).

What blocks (exit 1) for --genre trailer / teaser, validated 2026-10-26 on three
approved cuts (Grave Stakes teaser v4, Grave Stakes teaser V7, DUALITY trailer)
against a machine-made monotone draft:
  * spread    - p90/p10 shot length (max/min under 8 shots) below 2.0:
                the cut is monotone. Approved cuts: 2.85, 4.81, 6.48.
  * monotony  - 4+ consecutive shots of the same length (within 2 frames).
                Approved cuts: none.
  * beats     - a labelled beat outside its duration band (cutlist only;
                the band table lives in core.review_loop).

What only warns: flash shots under 6 frames, a final shot under 2 s (no room
for a button or a title card). What is only reported: longest/shortest ratio
(Murch's 2-4x upper bound failed validation: DUALITY 15.7x, V7 49.7x) and the
first-quarter vs last-quarter average shot length (all three approved cuts
slow down at the end, so "must accelerate" is not a rule here).

Other genres get the same report with exit 0: no threshold there has been
validated against his material yet.

With --music TRACK (or `check(..., music=core.beats.detect_beats(...))`) two
editor rules from a live cut (Denis, 16.11.2026) are checked as well:
  * accents   - every cut lands on a PERCUSSIVE accent (drums only, from
                `core.beats.strong_accents`: the drum peaks, not every
                percussive tick). A cut inside a free span
                (no percussive accent nearby, e.g. strings only) is free and
                not penalised, unless it breaks the pulse: a neighbouring shot
                shorter than half a beat, or the cut 1-3 frames before a strong
                percussive hit it misses ("pulse"). `accent_placement()`.
  * strong    - in a fast series (3+ shots in a row, each at most 2 beats) a
                cut on the grid lands on a strong beat (1 or 3 of the bar),
                not on a weak beat or the bar-end fill. `series_on_weak_beats()`.
Off-accent, pulse and weak-beat cuts fail for the gated genres and are notes
elsewhere; `info["free_spans"]` lists the free spans.
"""

from __future__ import annotations

import argparse
import bisect
import json
import statistics
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from core.cutlist import Cutlist
from core.review_loop import analyze_cutlist

GATED_GENRES = {"trailer", "teaser"}

# Genre -> digest file in the genre brief folder (the film-editing skill keeps them).
GENRE_BRIEF = {
    "trailer": "trailer.md",
    "teaser": "trailer.md",
    "documentary": "documentary.md",
    "interview": "interview.md",
    "shorts": "interview.md",
    "fiction": "fiction.md",
    "promo": "promo.md",
    "music": "promo.md",
}

MIN_SPREAD = 2.0          # p90/p10 below this = monotone
MONOTONY_RUN = 4          # this many same-length shots in a row
SAME_LEN_FRAMES = 2       # "same length" tolerance
FLASH_FRAMES = 6          # shorter than this = flash
MIN_FINAL_SEC = 2.0       # final shot shorter than this = no button / card

# Editor rules from a live cut (Denis, 16.11.2026)
ON_EARLY_FRAMES = 1.0     # a cut less than this early on an accent is on it
ON_LATE_FRAMES = 2.0      # ... and up to this late (core.hits: two frames)
PULSE_EARLY_FRAMES = (1.0, 3.0)  # this many frames before a strong hit = miss
SERIES_MIN_SHOTS = 3      # this many short shots in a row = fast series
SERIES_MAX_BEATS = 2.0    # a series shot is at most this many beats long


@dataclass
class Shot:
    start: float
    duration: float
    label: str = ""


@dataclass
class GateReport:
    source: str
    genre: str
    shot_source: str                     # cutlist | premiere | detected
    shots: int
    total_sec: float
    fails: list[str] = field(default_factory=list)
    warns: list[str] = field(default_factory=list)
    info: dict = field(default_factory=dict)
    brief: str | None = None

    @property
    def gated(self) -> bool:
        return self.genre in GATED_GENRES

    @property
    def passed(self) -> bool:
        return not (self.gated and self.fails)


# --------------------------------------------------------------------------- #
# Shot extraction
# --------------------------------------------------------------------------- #


def shots_from_cutlist(cl: Cutlist) -> list[Shot]:
    return [Shot(c.offset, c.duration, c.label or "") for c in sorted(cl.cuts, key=lambda c: c.offset)]


def shots_from_premiere(dump: dict) -> list[Shot]:
    """Cut points of every unmuted video track, merged into one shot list."""
    points: set[float] = set()
    lo, hi = None, None
    for track in dump.get("video", []):
        if track.get("muted"):
            continue
        for c in track.get("clips", []):
            s, e = float(c["startSec"]), float(c["endSec"])
            if e - s <= 0:
                continue
            points.update((round(s, 3), round(e, 3)))
            lo = s if lo is None else min(lo, s)
            hi = e if hi is None else max(hi, e)
    if lo is None:
        return []
    pts = sorted(p for p in points if lo <= p <= hi)
    return [Shot(a, round(b - a, 3)) for a, b in zip(pts, pts[1:]) if b - a > 1e-3]


def media_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(out.strip())


def shots_from_media(path: str, threshold: float = 0.3) -> list[Shot]:
    from core.highlights import scene_cuts

    dur = media_duration(path)
    cuts = [c for c in scene_cuts(path, threshold=threshold) if 0.05 < c < dur - 0.05]
    return shots_from_bounds([0.0, *cuts, dur])


def shots_from_bounds(bounds: list[float]) -> list[Shot]:
    b = sorted(bounds)
    return [Shot(round(x, 3), round(y - x, 3)) for x, y in zip(b, b[1:]) if y - x > 1e-3]


def load_shots(source: str) -> tuple[list[Shot], str, Cutlist | None]:
    p = Path(source)
    if p.suffix.lower() == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        if "cuts" in data:
            cl = Cutlist.from_dict(data)
            return shots_from_cutlist(cl), "cutlist", cl
        if "error" in data and "video" not in data:
            raise ValueError(f"{source}: Premiere answered with an error: {data['error']}")
        if "video" in data:
            return shots_from_premiere(data), "premiere", None
        raise ValueError(f"{source}: neither a cutlist (cuts) nor a Premiere timeline dump (video)")
    return shots_from_media(str(p)), "detected", None


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #


@dataclass
class CutPlacement:
    t: float              # cut time on the timeline, s
    status: str           # on | free | off | pulse
    accent: float | None  # nearest percussive accent, s
    delta: float | None   # t - accent, s (negative = early)
    why: str = ""


def _accents(accents) -> list[tuple[float, float]]:
    out = []
    for a in accents:
        if isinstance(a, (tuple, list)):
            out.append((float(a[0]), float(a[1]) if len(a) > 1 else 1.0))
        else:
            out.append((float(a), 1.0))
    return sorted(out)


def accent_placement(shots: list[Shot], accents, free_spans, period: float,
                     fps: float) -> list[CutPlacement]:
    """Where every cut sits against the percussive accents.

    accents    : [(t, strength 0..1)] or [t]: the STRONG percussive peaks
                 (`core.beats.strong_accents`, the "accents" of detect_beats);
                 every one of them is a strong hit for the pulse rule
    free_spans : [(start, end)] with no percussive accent (`core.beats.free_spans`)
    period     : beat length, s (60 / bpm); 0 disables the half-beat test

    status
      on    - within ON_EARLY_FRAMES before .. ON_LATE_FRAMES after an accent
      pulse - breaks the pulse: 1-3 frames before a strong hit it misses, or a
              free cut next to a shot shorter than half a beat
      free  - inside a free span (no drums to land on): not penalised
      off   - between percussive accents where there are drums
    The first shot's start is not a cut and is skipped.
    """
    fr = 1.0 / fps
    acc = _accents(accents)
    ts = [a for a, _ in acc]
    out: list[CutPlacement] = []
    for i in range(1, len(shots)):
        t = shots[i].start
        k = bisect.bisect_left(ts, t)
        near = [j for j in (k - 1, k) if 0 <= j < len(ts)]
        j = min(near, key=lambda j: abs(t - ts[j])) if near else None
        a = ts[j] if j is not None else None
        d = round(t - a, 4) if a is not None else None
        if d is not None and -ON_EARLY_FRAMES * fr < d <= ON_LATE_FRAMES * fr + 1e-6:
            out.append(CutPlacement(t, "on", a, d))
            continue
        # the next strong hit after the cut: missed by 1-3 frames?
        lo, hi = PULSE_EARLY_FRAMES
        nxt = next(((ta, st) for ta, st in acc[k:] if ta > t), None)
        if nxt and lo * fr - 1e-6 <= nxt[0] - t <= hi * fr + 1e-6:
            out.append(CutPlacement(t, "pulse", nxt[0], round(t - nxt[0], 4),
                                    f"{(nxt[0] - t) * fps:.1f} frames before a strong hit"))
            continue
        if any(s0 <= t <= s1 for s0, s1 in free_spans):
            short = [n for n in (i - 1, i) if period > 0 and shots[n].duration < period / 2]
            if short:
                out.append(CutPlacement(t, "pulse", a, d,
                                        f"shot {short[0] + 1} is under half a beat"))
            else:
                out.append(CutPlacement(t, "free", a, d))
            continue
        out.append(CutPlacement(t, "off", a, d))
    return out


def fast_series(shots: list[Shot], period: float) -> list[tuple[int, int]]:
    """(first index, count) of runs of SERIES_MIN_SHOTS+ shots each at most
    SERIES_MAX_BEATS beats long."""
    if period <= 0:
        return []
    lim = SERIES_MAX_BEATS * period + 1e-3
    runs, i = [], 0
    while i < len(shots):
        j = i
        while j < len(shots) and shots[j].duration <= lim:
            j += 1
        if j - i >= SERIES_MIN_SHOTS:
            runs.append((i, j - i))
        i = max(j, i + 1)
    return runs


def series_on_weak_beats(shots: list[Shot], beats: list[float],
                         strengths: list[str], fps: float) -> list[tuple[float, str]]:
    """Cuts of a fast series that land on a weak beat or a bar-end fill.

    Only cuts that sit on the grid (within two frames of a beat) are judged;
    a cut off the grid is the accent check's business. Returns [(t, label)].
    """
    if not beats or len(beats) != len(strengths):
        return []
    period = (beats[-1] - beats[0]) / (len(beats) - 1) if len(beats) > 1 else 0.0
    tol = ON_LATE_FRAMES / fps
    bad = []
    for first, n in fast_series(shots, period):
        cuts = [shots[k].start for k in range(first, first + n)]
        end = shots[first + n - 1]
        cuts.append(round(end.start + end.duration, 3))
        for t in cuts:
            if t <= 1e-6:
                continue
            k = bisect.bisect_left(beats, t)
            near = [j for j in (k - 1, k) if 0 <= j < len(beats)]
            j = min(near, key=lambda j: abs(t - beats[j]))
            if abs(t - beats[j]) <= tol and strengths[j] != "strong":
                bad.append((t, strengths[j]))
    return bad


def _pct(sorted_vals: list[float], q: float) -> float:
    return sorted_vals[int(round(q * (len(sorted_vals) - 1)))]


def spread(durs: list[float]) -> float:
    s = sorted(durs)
    if len(s) >= 8:
        lo, hi = _pct(s, 0.1), _pct(s, 0.9)
    else:
        lo, hi = s[0], s[-1]
    return round(hi / lo, 2) if lo > 0 else 0.0


def monotony_runs(durs: list[float], tol: float) -> list[tuple[int, int, float]]:
    """(first index, run length, length) for runs of MONOTONY_RUN+ same-length shots."""
    runs = []
    i = 0
    while i < len(durs):
        j = i + 1
        while j < len(durs) and abs(durs[j] - durs[i]) <= tol:
            j += 1
        if j - i >= MONOTONY_RUN:
            runs.append((i, j - i, durs[i]))
        i = j
    return runs


def check(shots: list[Shot], genre: str, fps: float, *, source: str = "",
          shot_source: str = "", cutlist: Cutlist | None = None,
          brief_dir: str | None = None, music: dict | None = None) -> GateReport:
    durs = [s.duration for s in shots]
    rep = GateReport(source=source, genre=genre, shot_source=shot_source,
                     shots=len(durs), total_sec=round(sum(durs), 2))
    if brief_dir and genre in GENRE_BRIEF:
        rep.brief = str(Path(brief_dir).expanduser() / GENRE_BRIEF[genre])
    if len(durs) < 3:
        rep.warns.append(f"only {len(durs)} shot(s): rhythm checks need 3+")
        return rep

    frame = 1.0 / fps
    sp = spread(durs)
    rep.info["spread_p90_p10"] = sp
    if sp < MIN_SPREAD:
        rep.fails.append(
            f"monotone cut: long shots are only {sp}x the short ones (want >= {MIN_SPREAD}x). "
            "Give the emotional beats room and tighten the connective shots."
        )

    for first, n, length in monotony_runs(durs, SAME_LEN_FRAMES * frame + 1e-6):
        rep.fails.append(
            f"shots {first + 1}-{first + n} are all ~{length:.2f}s: {n} same-length shots in a row "
            f"(start {shots[first].start:.2f}s). Vary them."
        )

    if cutlist is not None:
        for flag in analyze_cutlist(cutlist).beat_pacing_flags:
            rep.fails.append(f"beat pacing: {flag}")

    if music:
        _check_music(rep, shots, music, fps)

    flashes = [i for i, d in enumerate(durs) if d < FLASH_FRAMES * frame]
    if flashes:
        where = ", ".join(f"{shots[i].start:.2f}s" for i in flashes[:8])
        rep.warns.append(
            f"{len(flashes)} flash shot(s) under {FLASH_FRAMES} frames at {where}: "
            "intended, or a stray frame left on a cut?"
        )
    if genre in GATED_GENRES and durs[-1] < MIN_FINAL_SEC:
        rep.warns.append(
            f"final shot is {durs[-1]:.2f}s: no room for a button or a title card (want >= {MIN_FINAL_SEC}s)"
        )

    s = sorted(durs)
    rep.info.update({
        "asl": round(statistics.mean(durs), 2),
        "median": round(statistics.median(durs), 2),
        "shortest": s[0],
        "longest": s[-1],
        "longest_to_shortest": round(s[-1] / s[0], 1) if s[0] else None,
    })
    body = durs[:-1] if len(durs) >= 5 else durs
    q = max(1, len(body) // 4)
    rep.info["asl_first_quarter"] = round(statistics.mean(body[:q]), 2)
    rep.info["asl_last_quarter_before_final"] = round(statistics.mean(body[-q:]), 2)
    if shot_source == "detected":
        rep.info["note"] = "shot lengths from scene detection: a dissolve merges shots, a flash adds one"
    return rep


def _check_music(rep: GateReport, shots: list[Shot], music: dict, fps: float) -> None:
    """Accent and strong-beat rules against a `core.beats` analysis dict."""
    bpm = float(music.get("bpm") or 0.0)
    period = 60.0 / bpm if bpm > 0 else 0.0
    accents = music.get("accents") or []
    free = [tuple(x) for x in music.get("free_spans") or []]
    rep.info["free_spans"] = free
    if accents or free:
        places = accent_placement(shots, accents, free, period, fps)
        by = {k: [p for p in places if p.status == k] for k in ("on", "free", "off", "pulse")}
        rep.info["cuts_on_accent"] = len(by["on"])
        rep.info["cuts_free"] = len(by["free"])
        if by["off"]:
            where = ", ".join(
                f"{p.t:.2f}s ({p.delta:+.2f} from {p.accent:.2f})" if p.accent is not None
                else f"{p.t:.2f}s" for p in by["off"][:8])
            rep.fails.append(
                f"{len(by['off'])} cut(s) between percussive accents where the drums play: {where}. "
                "Move them onto a drum hit.")
        for p in by["pulse"]:
            rep.fails.append(f"cut at {p.t:.2f}s breaks the pulse: {p.why}")
    beats, strengths = music.get("beats") or [], music.get("beat_strength") or []
    weak = series_on_weak_beats(shots, beats, strengths, fps)
    if weak:
        where = ", ".join(f"{t:.2f}s ({lab})" for t, lab in weak[:8])
        rep.fails.append(
            f"fast series cut on {len(weak)} weak beat(s) or bar-end fill: {where}. "
            "Cut a series on beats 1 and 3.")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def brief_checklist(path: str) -> list[str]:
    """The digest's machine-check section, printed so the brief travels with the gate."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out, on = [], False
    for ln in lines:
        if ln.startswith("#"):
            low = ln.lower()
            if on:
                break
            on = "машин" in low and "провер" in low
            continue
        if on and ln.strip():
            out.append(ln)
    return out


def format_report(rep: GateReport) -> str:
    lines = [f"rhythm gate - {rep.genre} - {rep.shots} shots, {rep.total_sec}s ({rep.shot_source})"]
    for f in rep.fails:
        lines.append(("FAIL  " if rep.gated else "note  ") + f)
    for w in rep.warns:
        lines.append("warn  " + w)
    lines.append("info  " + ", ".join(f"{k}={v}" for k, v in rep.info.items()))
    if rep.brief:
        lines.append(f"brief {rep.brief}")
        check_lines = brief_checklist(rep.brief)
        if check_lines:
            lines.append("      genre checklist from the brief (look, not gate):")
            lines.extend("      " + c for c in check_lines[:14])
    if not rep.gated:
        lines.append(f"verdict: report only ({rep.genre} is not gated yet)")
    else:
        lines.append("verdict: " + ("PASS" if rep.passed else "FAIL - fix the cut, re-run the same gate"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m core.rhythm_gate", description=__doc__.split("\n\n")[0])
    ap.add_argument("source", help="cutlist .json, Premiere timeline dump .json, or a media file")
    ap.add_argument("--genre", required=True, choices=sorted(GENRE_BRIEF))
    ap.add_argument("--fps", type=float, default=25.0)
    ap.add_argument("--brief", help="folder with the genre digests (trailer.md, documentary.md, ...)")
    ap.add_argument("--music", help="music track: check cuts against percussive accents and strong beats")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        shots, kind, cl = load_shots(a.source)
    except (ValueError, OSError, subprocess.CalledProcessError) as e:
        print(f"rhythm gate: cannot read shots: {e}", file=sys.stderr)
        return 2
    music = None
    if a.music:
        from core.beats import detect_beats
        music = detect_beats(a.music)
    rep = check(shots, a.genre, a.fps, source=a.source, shot_source=kind, cutlist=cl,
                brief_dir=a.brief, music=music)
    if a.json:
        print(json.dumps({**asdict(rep), "passed": rep.passed, "gated": rep.gated}, ensure_ascii=False, indent=1))
    else:
        print(format_report(rep))
    return 0 if rep.passed else 1


if __name__ == "__main__":
    sys.exit(main())
