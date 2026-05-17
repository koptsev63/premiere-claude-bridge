# Progress benchmark — same footage, measured every iteration

One fixed input (the 108 Grave Stakes `.MTS` clips), re-cut over time. Each
version is scored by the **deterministic** Murch checks in
`core.review_loop.analyze_cutlist` so "is this edit getting better?" has an
objective answer instead of a vibe.

What the score measures (objective only — taste/emotion stays human, see
`skills/film-editing/SKILL.md` §I):

- `ratio` — longest / shortest shot. §VII wants **2–4×** (`ratio_ok`).
- `monotony_runs` — runs of 4+ same-length shots (§X). Want none.
- `beat_pacing_flags` — shots whose duration violates the §VII beat table
  for their labelled beat type (HOOK 4–6 s, PIT 6–8 s, PAYOFF 6–10 s, …).
- `is_clean` — all of the above pass.

| Version | Cuts | Total | Ratio | ratio_ok | Monotony | Beat flags | is_clean |
|---|---|---|---|---|---|---|---|
| v3 (data-driven, prior) | 12 | 61 s | 2.33 | yes | none | **1** (`pit-followthrough` underpaced) | **no** |
| v4 (analyzer-iterated)  | 9  | ~35 s | 3.50 | yes | none | **0** | **yes** |

v4's first draft was *also* flagged (ratio 1.75, monotone, 6 beat flags) —
the analyzer rejected it, the cut was revised against that critique, and the
second draft passed. That loop is the point: the machine does the
arithmetic, a person still decides if it *feels* right.

## Reproduce / re-score

```bash
# score any cutlist
python - <<'PY'
from core.cutlist import Cutlist
from core.review_loop import analyze_cutlist
a = analyze_cutlist(Cutlist.load("examples/grave-stakes-teaser/benchmark/v4_cutlist.json"))
print("clean" if a.is_clean() else a.beat_pacing_flags, "ratio", a.ratio)
PY
```

## Rebuild v4 end to end (the full new pipeline)

```bash
# 1. conform bare clip names -> real media on this machine
python -m core.conform examples/grave-stakes-teaser/benchmark/v4_cutlist.json \
    --roots "/path/to/Grave stakes/Videos" --out /tmp/v4_conformed.json
# 2. picture: ffmpeg rough from the cutlist (core.review_loop.build_rough_cut_plan)
# 3. music:   Suno (instrumental, Balkan-brass teaser score) -> loop+loudnorm bed
# 4. live:    core.adapters.resolve.ResolveAdapter().apply_cutlist(...)  (Resolve Studio)
```

Rendered MP4s are **not** committed (large, source footage is the author's).
v4 lives at `~/Desktop/Grave stakes/GRAVE_STAKES_teaser_v4.mp4` and as the
`Grave_Stakes_Teaser_v4` timeline in Resolve.

## Files

- `v4_cutlist.json` — portable (bare clip names; `core.conform` resolves them)
- `v4_murch_score.json` / `v3_murch_score.json` — the objective scores above

Next iteration (v5) should beat v4 on *feel* with the same or better score,
informed by the full 108-clip motion/audio analysis.
