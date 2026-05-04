# Grave Stakes Teaser — Case Study

The first end-to-end use of premiere-claude-bridge on real footage.

## The brief

- Director-editor: Vladimir Koptsev
- Project: *Grave Stakes* — short black-comedy/sports doc about a grave-digging championship
- Material: 108 raw .MTS clips, 4.4 GB, 60 minutes total
- Goal: 60-90 sec teaser for festival pitches

## What the bridge did

1. **Imported all 108 clips** into bin `01_Source_MTS` (one prompt)
2. **Created 1080p25 sequence** via QE preset
3. **Built three teaser variants** — each via cutlist prompt, applied through `pr_eval_jsx`
4. **Generated HTML contact sheet** of all 108 clips with motion + audio metadata
5. **Identified clips Vladimir missed** in his manual selects — `00173.MTS` (AMBULANCE — added a "real stakes" beat) and `00185.MTS` (#2 by motion, replaced redundant wide)

## Three sequences, same source

| Sequence | Logic | Result |
|---|---|---|
| `Teaser_v1_PR` | Original ffmpeg-script cutlist, no editorial pass | 13 cuts, gaps for title cards |
| `Teaser_v2_Murch` | Rule of Six applied: comedy +1s, action montage tightened, golden +1s | 11 cuts, 61 sec, no gaps, 6 markers |
| `Teaser_v3_DataDriven` | Selected via `analyze_clips.py` rankings: motion + audio peak | 12 cuts, 61 sec, AMBULANCE beat added, deeper-pit follow-through |

## The data that drove v3

- `report.json` — full per-clip metadata
- `report.html` — visual contact sheet
- Top 5 by motion score: `00173`, `00185`, `00172`, `00215`, `00213`
- All my v2 selects ranked among 108 by data — `00118` (field) was #107, but kept as intentional establishing-breath

## Files

- `cutlist_v3.json` — 12 clips with in/out/offset
- `screenshots/` — Premiere timeline + contact sheet preview
- `contact_sheet_preview.jpg` — first 48 of 108 thumbnails

## Reproduce

```bash
# 1. Run analysis
python3 ../../skills/film-editing/tools/analyze_clips.py \
  /path/to/your/footage \
  /path/to/output_log

# 2. Open report.html in browser, mark selects

# 3. Build sequence via Claude prompt:
#    "Use Teaser_v3_DataDriven sequence in Grave_Stakes_Teaser.prproj.
#     Insert these 12 clips in order with these in/out points: ..."
```

