"""Tests for the editor rules from a live cut (Denis, 16.11.2026).

Run:  python -m core.tests.test_taste

Synthetic data only: drum clicks (noise bursts) plus a sine pad for strings,
synthetic shot lists and shot metadata, tiny RGB frames. One test reads
frames with ffmpeg and is skipped without it.

  rule 1  percussive accents, free spans  - core.beats, core.rhythm_gate
  rule 2  strong beats, bar-end fill       - core.beats, core.rhythm_gate
  rule 3  a fast series as a phrase        - core.taste, core.variants
  rule 4  neighbours differ in hue         - core.taste
  rule 5  two-hit titles on a strong beat  - core.overlays
"""

from __future__ import annotations

import math
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from core.beats import (
    analyze_samples,
    beat_strengths,
    free_spans,
    percussive_accents,
    snap_cutlist_to_beats,
    strong_beats,
)
from core.cutlist import Cut, Cutlist, Marker
from core.overlays import Overlay, align_to_strong_beat, from_markers
from core.rhythm_gate import Shot, accent_placement, check, fast_series, series_on_weak_beats
from core.taste import (
    ShotMeta,
    check_series,
    clip_hue,
    dominant_hue,
    hue_distance,
    hue_flags,
    links_to,
    phrase_order,
    repeats,
)
from core.variants import ClipMeta, build_variant

try:
    import numpy as np
    HAS_NP = True
except ImportError:  # pragma: no cover
    HAS_NP = False

_p = _f = _s = 0
SR = 22050


def check_(name: str, cond: bool, detail: str = "") -> None:
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}  {detail}")


def skip(name: str, why: str) -> None:
    global _s
    _s += 1
    print(f"  skip  {name}: {why}")


# --------------------------------------------------------------------------- #
# Synthetic music: clicks for drums, a sine pad for strings
# --------------------------------------------------------------------------- #


def _click(seed: int):
    n = int(0.03 * SR)
    rng = np.random.default_rng(seed)
    return rng.standard_normal(n) * np.exp(-np.arange(n) / (0.006 * SR))


def synth(dur: float, clicks: list[float], pad: list[tuple[float, float]]):
    """Clicks at the given times (drums) + sine pad notes [(start_s, Hz)] with
    an 80 ms fade-in (strings): each note sustains to the end."""
    n = int(dur * SR)
    x = np.zeros(n)
    t = np.arange(n) / SR
    for k, c in enumerate(clicks):
        i = int(round(c * SR))
        b = _click(k + 1)
        x[i:i + len(b)] += 0.8 * b[:max(0, n - i)]
    for start, hz in pad:
        env = np.clip((t - start) / 0.08, 0.0, 1.0)
        x += 0.3 * env * np.sin(2 * np.pi * hz * t)
    return x.astype(np.float32)


def grid(a: float, b: float, step: float = 0.5) -> list[float]:
    out, t = [], a
    while t < b - 1e-9:
        out.append(round(t, 3))
        t += step
    return out


def shots_at(starts: list[float], end: float) -> list[Shot]:
    b = [*starts, end]
    return [Shot(round(x, 3), round(y - x, 3)) for x, y in zip(b, b[1:])]


# drums 0.5-4 s and 8-10 s at 120 bpm; strings all the way, a new note at 5 s
DRUMS = grid(0.5, 4.0) + grid(8.0, 10.0)
PAD = [(0.0, 220.0), (5.0, 330.0)]


# --------------------------------------------------------------------------- #
# Rule 1 - accents come from percussion only; strings-only spans are free
# --------------------------------------------------------------------------- #


def test_rule1_percussive_accents() -> None:
    print("rule 1 - percussive accents (HPSS) ignore the strings")
    if not HAS_NP:
        return skip("rule 1 audio", "numpy missing")
    x = synth(10.0, DRUMS, PAD)
    acc = percussive_accents(x, SR)
    times = [t for t, _ in acc]
    matched = all(any(abs(t - c) <= 0.03 for c in DRUMS) for t in times)
    check_("every accent is a drum click (within 30 ms)", matched, str(times))
    check_("every drum click is found", len(times) == len(DRUMS), f"{len(times)} vs {len(DRUMS)}")
    check_("no accent in the strings-only passage (pad entry at 5 s ignored)",
           not any(4.2 < t < 7.9 for t in times), str(times))
    res = analyze_samples(x, SR)
    check_("full-mix onsets DO fire on the strings (why rule 1 exists)",
           any(4.5 < t < 7.5 for t in res["onsets"]))
    check_("tempo from the drums: 120 bpm", abs(res["bpm"] - 120.0) <= 1.0, str(res["bpm"]))
    fs = res["free_spans"]
    check_("strings-only passage exposed as a free span",
           any(s0 <= 4.1 and s1 >= 7.9 for s0, s1 in fs), str(fs))
    check_("the drum sections are not free",
           not any(s0 < 2.0 < s1 for s0, s1 in fs) and not any(s0 < 9.0 < s1 for s0, s1 in fs), str(fs))


def test_rule1_median_fallback() -> None:
    print("rule 1 - HPSS median filter: numpy fallback equals scipy")
    if not HAS_NP:
        return skip("median fallback", "numpy missing")
    import builtins

    from core.beats import _median_along
    S = np.random.default_rng(3).random((80, 50)).astype(np.float32)
    fast = [_median_along(S, 17, ax) for ax in (0, 1)]
    real = builtins.__import__

    def no_scipy(name, *a, **k):
        if name.startswith("scipy"):
            raise ImportError(name)
        return real(name, *a, **k)

    builtins.__import__ = no_scipy
    try:
        slow = [_median_along(S, 17, ax) for ax in (0, 1)]
    finally:
        builtins.__import__ = real
    check_("same result along both axes",
           all(float(np.abs(x - y).max()) == 0.0 for x, y in zip(fast, slow)))


def test_rule1_free_spans_pure() -> None:
    print("rule 1 - free_spans on an accent list")
    fs = free_spans([0.5, 1.0, 1.5, 6.0, 6.5], 8.0, min_gap_s=1.5)
    check_("gap between accents is free, edges too", fs == [(1.5, 6.0), (6.5, 8.0)], str(fs))
    check_("no accents at all: the whole track is free", free_spans([], 5.0) == [(0.0, 5.0)])


def test_rule1_gate_placement() -> None:
    print("rule 1 - rhythm gate: on / off / free / pulse")
    acc = [(t, 1.0) for t in DRUMS]
    free = free_spans(acc, 10.0, 1.5)
    fps = 25.0
    # cuts: on a hit, off between hits (drums play), off-beat in the strings
    # (free), a 0.1 s shot inside the strings (pulse), 2 frames before the
    # drums come back at 8.0 (pulse)
    shots = shots_at([0.0, 1.0, 2.27, 5.3, 6.0, 6.1, 7.92], 10.0)
    st = {round(p.t, 2): p.status for p in accent_placement(shots, acc, free, 0.5, fps)}
    check_("cut on a drum hit is on", st[1.0] == "on", str(st))
    check_("off-beat cut where the drums play is off", st[2.27] == "off", str(st))
    check_("off-beat cut in the strings passage is free", st[5.3] == "free", str(st))
    check_("shot shorter than half a beat breaks the pulse", st[6.0] == "pulse" and st[6.1] == "pulse", str(st))
    check_("cut 2 frames before a strong hit breaks the pulse", st[7.92] == "pulse", str(st))
    late = {round(p.t, 2): p.status for p in accent_placement(
        shots_at([0.0, 1.0 + 2 / fps, 2.0 - 0.5 / fps], 3.0), acc, free, 0.5, fps)}
    check_("2 frames late and half a frame early still count as on",
           set(late.values()) == {"on"}, str(late))

    music = {"bpm": 120.0, "accents": acc, "free_spans": free, "beats": [], "beat_strength": []}
    ok_shots = shots_at([0.0, 1.0, 2.0, 5.3, 8.0], 10.0)
    rep = check(ok_shots, "promo", fps, music=music)
    check_("a cut free in the strings is not penalised", not rep.fails, str(rep.fails))
    check_("gate exposes the free spans", rep.info.get("free_spans") == free)
    bad = check(shots, "teaser", fps, music=music)
    check_("off and pulse cuts fail a gated genre",
           any("between percussive accents" in f for f in bad.fails)
           and sum("breaks the pulse" in f for f in bad.fails) == 3, str(bad.fails))


def test_rule1_end_to_end_audio() -> None:
    print("rule 1 - gate on an analysed synthetic track")
    if not HAS_NP:
        return skip("rule 1 end to end", "numpy missing")
    music = analyze_samples(synth(10.0, DRUMS, PAD), SR)
    rep = check(shots_at([0.0, 0.5, 3.0, 5.3, 6.7, 8.5], 10.0), "promo", 25.0, music=music)
    check_("drum cuts on accents, strings cuts free: no fails", not rep.fails, str(rep.fails))
    check_("two cuts counted free", rep.info.get("cuts_free") == 2, str(rep.info))


# --------------------------------------------------------------------------- #
# Rule 2 - strong beats (1 and 3) in a fast series; weak beats and fills skipped
# --------------------------------------------------------------------------- #


def test_rule2_beat_strengths() -> None:
    print("rule 2 - strong / weak / fill per beat")
    beats = grid(0.0, 8.0)
    plain = beat_strengths(beats, [(b, 1.0) for b in beats], 0.5)
    check_("bars of 4: 1 and 3 strong, 2 and 4 weak",
           plain[:8] == ["strong", "weak", "strong", "weak"] * 2, str(plain[:8]))
    fill_acc = [*beats, 1.62, 1.74, 1.86]                # 4 hits in beat 4 of bar 1
    lab = beat_strengths(beats, fill_acc, 0.5)
    check_("dense hits in the last beat of a bar = fill", lab[3] == "fill" and lab[7] == "weak", str(lab[:8]))
    hat = sorted(set(round(b + k * 0.125, 3) for b in beats for k in range(4)))
    check_("a steady 16th hi-hat on every beat is not a fill",
           "fill" not in beat_strengths(beats, hat, 0.5))
    check_("strong_beats keeps 1 and 3", strong_beats(beats, plain)[:4] == [0.0, 1.0, 2.0, 3.0])


def test_rule2_fill_in_audio() -> None:
    print("rule 2 - a drum fill found in synthetic audio")
    if not HAS_NP:
        return skip("rule 2 audio", "numpy missing")
    clicks = grid(0.5, 6.5)
    clicks += [2.125, 2.25, 2.375]                       # fill on beat 4 of bar 1
    res = analyze_samples(synth(7.0, sorted(clicks), [(0.0, 220.0)]), SR)
    lab, beats = res["beat_strength"], res["beats"]
    i = min(range(len(beats)), key=lambda k: abs(beats[k] - 2.0))
    check_("beat at 2.0 s labelled fill", lab[i] == "fill", f"{list(zip(beats, lab))[:8]}")
    check_("one label per beat", len(lab) == len(beats))


def test_rule2_series_on_strong_beats() -> None:
    print("rule 2 - fast series cuts on 1 and 3")
    beats = grid(0.0, 10.0)
    lab = beat_strengths(beats, [], 0.5)
    every_beat = shots_at([0.0, 0.5, 1.0, 1.5, 2.0], 6.0)
    check_("a run of one-beat shots is a fast series", fast_series(every_beat, 0.5) == [(0, 4)],
           str(fast_series(every_beat, 0.5)))
    weak = series_on_weak_beats(every_beat, beats, lab, 25.0)
    check_("cuts on weak beats in a series are flagged", [t for t, _ in weak] == [0.5, 1.5], str(weak))
    on_strong = shots_at([0.0, 1.0, 2.0, 3.0, 4.0], 8.0)
    check_("cuts on strong beats pass", series_on_weak_beats(on_strong, beats, lab, 25.0) == [])
    slow = shots_at([0.0, 2.5, 5.0], 8.0)
    check_("long shots are not a series", fast_series(slow, 0.5) == [])
    music = {"bpm": 120.0, "beats": beats, "beat_strength": lab, "accents": [], "free_spans": []}
    rep = check(every_beat, "teaser", 25.0, music=music)
    check_("gate fails a series cut on weak beats", any("weak beat" in f for f in rep.fails), str(rep.fails))
    cl = Cutlist("s", 25, cuts=[Cut("a", 2.0, 3.0, 1.45)])
    any_beat = snap_cutlist_to_beats(cl, beats).cuts[0]
    strong = snap_cutlist_to_beats(cl, beats, lab).cuts[0]
    check_("snap without strengths takes the nearest beat (weak 1.5)", any_beat.offset == 1.5, str(any_beat))
    check_("snap with strengths takes the nearest strong beat (1.0)",
           strong.offset == 1.0 and math.isclose(strong.in_, 1.55, abs_tol=1e-6), str(strong))


# --------------------------------------------------------------------------- #
# Rule 3 - a fast series is a phrase
# --------------------------------------------------------------------------- #


def _series() -> tuple[list[ShotMeta], ShotMeta]:
    s = [
        ShotMeta("crowd", subject="crowd", motion=2.0, hue=30.0),
        ShotMeta("singer", subject="singer", motion=3.0, hue=300.0),
        ShotMeta("jump", subject="dancer jump", motion=9.0, hue=100.0),
        ShotMeta("stage", subject="stage", motion=4.0, hue=200.0),
    ]
    return s, ShotMeta("next", subject="singer", hue=290.0)


def test_rule3_phrase_order() -> None:
    print("rule 3 - impact first, last links to the next shot, no repeats")
    s, after = _series()
    probs = check_series(s, after)
    check_("given order: opens off the impact shot", any("not on the impact shot jump" in p for p in probs), str(probs))
    check_("given order: ends on a shot that does not lead on",
           any("does not lead into next" in p for p in probs), str(probs))
    o = phrase_order(s, after)
    ids = [x.id for x in o]
    check_("impact (largest motion) opens", ids[0] == "jump", str(ids))
    check_("same subject as the next shot closes", ids[-1] == "singer", str(ids))
    check_("ordered series is clean", check_series(o, after) == [], str(check_series(o, after)))
    tagged = [ShotMeta("a", tags=("street",), motion=9.0), ShotMeta("b", tags=("impact", "car"))]
    check_("a hand-tagged impact beats motion", phrase_order(tagged)[0].id == "b")
    rep = [ShotMeta("a", subject="crowd"), ShotMeta("b", subject="stage"), ShotMeta("c", subject="Crowd")]
    check_("repeated content is reported", repeats(rep) == [(0, 2)]
           and any("repeat the same content" in p for p in check_series(rep)), str(repeats(rep)))
    hue_only = [ShotMeta("x", hue=0.0), ShotMeta("y", hue=180.0), ShotMeta("z", hue=95.0)]
    check_("no tags: the last shot links by dominant colour",
           phrase_order(hue_only, ShotMeta("n", hue=100.0))[-1].id == "z")
    bare = [ShotMeta("p"), ShotMeta("q"), ShotMeta("r")]
    check_("no metadata at all: order unchanged", [x.id for x in phrase_order(bare, ShotMeta("n"))] == ["p", "q", "r"])
    check_("links_to is None when there is nothing to compare", links_to(ShotMeta("p"), ShotMeta("n")) is None)


def test_rule3_variants_action_run() -> None:
    print("rule 3 - variants order the ACTION run as a phrase")
    metas = [ClipMeta(f"{c}.MTS", 60.0, 20.0, 10.0 - i) for i, c in enumerate("ABCDEFGH")]
    plain = [c.clip for c in build_variant("drive", metas).cuts]
    tagged = [replace(m) for m in metas]
    # value order puts the ACTION run on clips C/D/E (shorter beats); tag them
    run = [plain[i] for i in (2, 3, 4)]
    pit = plain[5]
    meta_by = {m.clip: m for m in tagged}
    meta_by[run[0]].tags, meta_by[run[0]].hue = ("street",), 40.0
    meta_by[run[1]].tags, meta_by[run[1]].hue = ("car",), 200.0
    meta_by[run[2]].tags, meta_by[run[2]].hue = ("impact", "crash"), 120.0
    meta_by[pit].tags = ("street",)
    notes: list[str] = []
    cl = build_variant("drive", tagged, notes_out=notes)
    got = [c.clip for c in cl.cuts]
    check_("untagged clips keep the old order", plain == [c.clip for c in build_variant("drive", metas).cuts])
    check_("impact clip opens the action run", got[2] == run[2], f"{got} run={run}")
    check_("run ends on the clip that shares a tag with the PIT", got[4] == run[0], f"{got}")
    check_("no phrase notes on a clean run", not [n for n in notes if n.startswith("action run")], str(notes))


# --------------------------------------------------------------------------- #
# Rule 4 - neighbours differ in dominant colour (hue), not brightness
# --------------------------------------------------------------------------- #


def _frame(rgb, grey_share: float = 0.0):
    f = np.zeros((18, 32, 3), dtype=np.uint8)
    f[:] = rgb
    rows = int(round(18 * grey_share))
    if rows:
        f[:rows] = (128, 128, 128)
    return f


def test_rule4_dominant_hue() -> None:
    print("rule 4 - dominant hue, circular distance, neighbour flags")
    check_("hue distance is circular", hue_distance(350, 10) == 20 and hue_distance(0, 180) == 180)
    if not HAS_NP:
        return skip("rule 4 frames", "numpy missing")
    red = dominant_hue([_frame((220, 20, 20)), _frame((200, 30, 25))])
    check_("red frames -> hue near 0", red is not None and hue_distance(red, 0) < 8, str(red))
    blue = dominant_hue(_frame((20, 40, 230), grey_share=0.6))
    check_("blue on grey background -> hue near 235 (grey ignored)",
           blue is not None and hue_distance(blue, 235) < 10, str(blue))
    check_("grey frame is achromatic (None)", dominant_hue(_frame((90, 90, 90))) is None)
    dark_red = dominant_hue(_frame((90, 10, 10)))
    green = dominant_hue(_frame((20, 210, 30)))
    flags = hue_flags([red, dark_red, green])
    check_("bright red next to dark red = same colour (brightness is not enough)", 0 in flags, str(flags))
    check_("red next to green of the same brightness = different", 1 not in flags, str(flags))
    check_("an unknown hue is not judged", hue_flags([10.0, None, 12.0]) == [])
    s = [ShotMeta("a", hue=red), ShotMeta("b", hue=dark_red)]
    check_("check_series reports shared dominant colour",
           any("share a dominant colour" in p for p in check_series(s)), str(check_series(s)))


def test_rule4_clip_hue_ffmpeg() -> None:
    print("rule 4 - clip_hue reads a few frames with ffmpeg")
    if not HAS_NP or not shutil.which("ffmpeg"):
        return skip("clip_hue", "numpy or ffmpeg missing")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "green.mp4"
        r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                            "color=c=0x20C030:s=64x36:d=1:r=25", "-pix_fmt", "yuv420p", str(p)],
                           capture_output=True)
        if r.returncode != 0:
            return skip("clip_hue", "ffmpeg could not make the test clip")
        h = clip_hue(str(p), 0.0, 0.9)
        check_("green clip -> hue near 128", h is not None and hue_distance(h, 128) < 12, str(h))


# --------------------------------------------------------------------------- #
# Rule 5 - a two-hit title lands its second hit on a strong beat
# --------------------------------------------------------------------------- #


def test_rule5_two_hit_title() -> None:
    print("rule 5 - second title hit on the nearest strong beat")
    strong = [0.0, 1.0, 2.0, 3.0]
    ov = Overlay("BIG NEWS", 1.0, 3.0, "title", hits=[0.3, 0.8])
    a = align_to_strong_beat(ov, strong)
    check_("hit 2 lands on a strong beat", math.isclose(a.start + 0.8, 2.0, abs_tol=1e-6), f"{a.start}")
    check_("duration kept", math.isclose(a.end - a.start, 2.0, abs_tol=1e-6), f"{a.start}-{a.end}")
    check_("no hits: unchanged", align_to_strong_beat(Overlay("T", 1.1, 3.0), strong) == Overlay("T", 1.1, 3.0))
    one = Overlay("T", 1.1, 3.0, hits=[0.4])
    check_("one hit: unchanged", align_to_strong_beat(one, strong) == one)
    early = align_to_strong_beat(Overlay("T", 0.0, 2.0, hits=[0.0, 1.5]), [1.0, 3.0])
    check_("never before 0: takes the next strong beat", early.start == 1.5 and early.end == 3.5, str(early))
    beats = grid(0.5, 8.5)
    sb = strong_beats(beats, beat_strengths(beats, [], 0.5))
    cl = Cutlist("s", 25, markers=[Marker("HOOK", 0.9), Marker("PAYOFF", 4.2)])
    ovs = from_markers(cl, hold=2.0, kind="title", hits=[0.2, 0.6], strong_beats=sb)
    check_("from_markers puts every second hit on a strong beat",
           all(any(math.isclose(o.start + 0.6, b, abs_tol=1e-6) for b in sb) for o in ovs),
           str([(o.start, o.end) for o in ovs]))
    plain = from_markers(cl, hold=2.0)
    check_("from_markers without hits behaves as before", [o.start for o in plain] == [0.9, 4.2])


def main() -> int:
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        fn()
    print(f"\ntaste: {_p} passed, {_f} failed, {_s} skipped")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
