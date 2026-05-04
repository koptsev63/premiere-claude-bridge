---
name: trailer-bridge
description: Genre-specific trailer/teaser editing recipes for Premiere via the Claude Bridge. Encodes pacing, structure, and emotional beat patterns for action, drama, comedy, horror, documentary, thriller, and romance trailers. Each recipe is a parameterized cutlist generator that takes selects + length and outputs a Premiere-ready sequence with markers. Pair with `mcp__premiere__*` tools and the base `film-editing` skill. PAID PACK ($19) — see MONETIZATION.md.
---

# trailer-bridge — paid skill pack

> **Status:** scaffold — first 2 recipes shipping with v0.2.

This pack extends the free `film-editing` skill with **genre-specific trailer recipes**. Each recipe knows the pacing, structural beats, and emotional arc that audiences expect from a particular genre, and emits a parameterized cutlist Claude can drop straight into Premiere.

## Why a paid pack

The free skill encodes universal principles (Murch's Rule of Six). This pack encodes **specific working knowledge** that takes years to acquire:
- Pacing math per genre (action averages 1.8s/cut, drama 5.2s/cut)
- Standard 60/90/120-sec trailer structures
- Color grade presets (LUTs) that match festival expectations
- Genre-specific opening-hook patterns

## Recipes

| Recipe | Use for | Example structure |
|---|---|---|
| `action-trailer.md` | Genre features, sports, conflict-driven docs | Hook → Stakes → Build → 1st climax → False resolution → 2nd climax → Title |
| `drama-trailer.md` | Character pieces, art-house | Quote → Conflict → Reveal → Reaction → Title |
| `comedy-trailer.md` | Comedies, satire | Setup joke → Punchline → Bigger setup → Bigger punchline → Title |
| `horror-trailer.md` | Horror, thriller-with-genre-elements | Calm → Disturbance → False jump → Reveal → Title sting |
| `documentary-teaser.md` | Doc shorts, festival pieces | Question → Voices → World → Stakes → Open question → Title |
| `thriller-trailer.md` | Thrillers, neo-noir | Hook → Riddle → Tension build → Cliffhanger → Title |
| `romance-trailer.md` | Romance, coming-of-age | Meet → Connect → Conflict → Choice → Title |

## Tools

- `tools/trailer_assembly.py` — input: folder of selects + recipe name + target length → output: ready Premiere sequence cutlist (JSON) + JSX commands to apply.
- `tools/beat_detector.py` — analyzes audio for "trailer beats" (drops, swells, stings) — auto-aligns cuts to musical beats.

## Color presets (LUTs)

Three classic festival/streamer trailer looks:
- `teal_orange.cube` — modern blockbuster
- `desaturated_high_contrast.cube` — A24-style indie
- `golden_warm.cube` — character drama / romance

## How a user uses it

```
You:    "Use trailer-bridge for a 75-sec action trailer from these 24 selects.
        Apply teal_orange LUT. Skip the false-resolution beat — too short."

Claude: ✓ Loaded action-trailer recipe (Hook→Stakes→Build→Climax→Title, 75s mode)
        ✓ Mapped selects to beats by motion + audio peak rankings
        ✓ Built sequence Trailer_v1_action_75s in your project
        ✓ Applied teal_orange.cube LUT to all clips on V1
        ✓ Placed 8 markers at structural beats
        Open in Premiere.
```

## Install

```bash
# After purchase, you receive a download link to:
trailer-bridge-v0.2.zip

# Extract into your bridge:
unzip trailer-bridge-v0.2.zip -d ~/Dev/premiere-claude-bridge/skills/

# Skill auto-registers on next Claude session.
```

## Refund policy

30-day no-questions-back via Gumroad.

## Roadmap (pack v0.3+)

- Multicam-aware recipes (interview-style trailers)
- Vertical/9:16 export presets for Reels/TikTok promo
- Music-sync mode: import MP3 → recipe auto-aligns cuts to beat detection
- AME export presets per festival (Sundance, Berlinale, Cannes deliverables)

---

**This is a SCAFFOLD.** Full recipes shipping with v0.2 release (week 2 of monetization roadmap — see MONETIZATION.md).
