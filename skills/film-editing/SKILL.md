---
name: film-editing
description: Editing principles for Premiere Pro work via the Claude Bridge. Encodes Walter Murch's "In the Blink of an Eye" — the Rule of Six, the blink theory, misdirection, eye trace, dreaming in pairs — as concrete decision rules Claude must apply when cutting a sequence. Trigger this skill whenever editing video — whether building a teaser, a trailer, a documentary cut, a reel, a short, or a feature assembly — and when reviewing or critiquing an existing edit. Pair with `mcp__premiere__*` tools.
---

# Film Editing — Murch's Operating System

Encodes the canonical principles of film editing as a hierarchy Claude follows when making cut decisions inside Premiere via the bridge. Source: Walter Murch, *In the Blink of an Eye: A Perspective on Film Editing* (1995, expanded 2001) — the most-cited text in editing pedagogy. Murch edited *Apocalypse Now*, *The Conversation*, *The English Patient* (Oscars for both editing and sound).

## When to invoke

- Building any sequence on a Premiere timeline (teaser, trailer, scene, reel)
- Choosing in/out points or clip durations
- Deciding cut order
- Reviewing an existing cut and proposing changes
- Anytime Vladimir says: смонтируй, перемонтируй, нарежь, склей, тизер, трейлер, монтаж, сцена, ритм, темп, edit, recut, cut, pacing

Always paired with the `mcp__premiere__*` tools (pr_eval_jsx, pr_get_active_sequence, pr_list_timeline, pr_set_playhead, pr_add_marker, pr_export_ame).

---

## I. The Rule of Six — the priority stack

Murch's hierarchy for evaluating any cut. **Read top-down: an upper rule outranks every rule below it.** Allocations are Murch's own (lecture, Sydney 1988):

| # | Criterion | Weight | What it means in practice |
|---|---|---|---|
| 1 | **Emotion** | 51% | Does the cut feel right for the audience's emotional state at that beat? "If you're true to the emotion of the moment, the audience will forgive technical problems." |
| 2 | **Story** | 23% | Does the cut advance the story? Is information delivered when the audience needs it? |
| 3 | **Rhythm** | 10% | Does the cut land at the right musical/breathing moment? Editing is "visual music." |
| 4 | **Eye trace** | 7% | Where is the viewer's eye at the cut point — and where does the new shot place the next focus? |
| 5 | **2D plane (planarity)** | 5% | Compositional continuity within the frame: does the new shot's composition flow from the old? |
| 6 | **3D space (continuity)** | 4% | The 180° rule, screen direction, spatial geometry. |

**Operational consequence — when criteria conflict, sacrifice from the bottom:**
- Sacrifice 3D continuity to preserve eye trace.
- Sacrifice eye trace to preserve rhythm.
- Sacrifice rhythm to preserve story.
- Sacrifice story to preserve emotion.
- **Never sacrifice emotion.**

Murch's exact line: "An ideal cut satisfies all six criteria at once. If you can't have all six, get the top four. If you can't have the top four, get the top three. The top three is most important — preferably the top two, and most of all the top one."

---

## II. The Blink Theory

Murch's central insight: humans blink at moments of mental punctuation — when one thought completes and another begins. Cuts work because the audience already does this internally; a good cut aligns with where the audience would naturally blink.

**Rules:**
1. **Cut at the end of a thought, not in the middle of one.** Every shot should end at a moment of completion (the end of a gesture, the end of a sentence, the end of a beat).
2. **The cut is the visual equivalent of the audience's blink** — make it land where the audience is mentally ready to receive a new image.
3. **Watch the actor's eyes** — performers blink at the same moments the audience does. If your subject blinks at frame X, X is a candidate cut point.

---

## III. The Idea Cut vs the Continuity Cut

Murch distinguishes two paradigms:
- **Continuity editing** — the invisible cut, hides the join (Hollywood classical).
- **Idea editing** — uses the discontinuity itself as a meaning-maker (Eisenstein, Resnais, Godard).

**For documentaries, teasers, and montage sequences (like Grave Stakes):** lean toward idea cuts. The juxtaposition between two shots produces a third meaning that exists in neither shot alone. Example: the *НОВОСИБИРСКИЙ КРЕМАТОРИЙ* T-shirt + a guy intensely digging a grave = the absurdist joke.

---

## IV. Misdirection

Borrowed from stagecraft: distract the viewer's attention away from the cut so the cut is invisible. Tools:
- A sudden movement on one side of the frame masks a cut on the other side.
- A character's quick gesture or look can hide a transition.
- An on-screen sound spike (door slam, gunshot, hand clap) at the cut point.

Use when continuity matters and you must hide the seam. Skip in idea-cut sequences where the seam IS the point.

---

## V. Eye Trace — concrete protocol

Before each cut, ask: **where is the viewer's eye in the outgoing frame?** Then ensure the incoming shot's primary subject lands at or near that same screen position.

**Practical protocol when working in Premiere via the bridge:**
1. `pr_set_playhead` to the last frame of the outgoing clip.
2. Identify the focal point (face, action, contrast peak).
3. `pr_set_playhead` to the first frame of the incoming clip.
4. If the new focal point lands near the old position → cut works for eye trace.
5. If not — either re-frame (motion effect / Position kf), or reorder the cut, or accept the eye-trace cost (allowed if the upper four rules win).

---

## VI. Dreaming in Pairs

A cut is never about one shot — always about two. Always evaluate the **A→B relationship**, not A or B in isolation.
- Two cuts that work individually may not work together (rhythm collision).
- Two cuts that look weak alone may sing as a pair.

When selecting clips for a sequence: **storyboard the pairs, not the singles.**

---

## VII. Pacing rules of thumb (Murch + standard practice)

These are starting points, not absolutes. Override per Rule of Six.

| Beat type | Typical duration | Why |
|---|---|---|
| **Establishing / breath shot** | 5–8 sec | Audience needs time to orient |
| **Hook / opening reveal** | 4–6 sec | Long enough to register, short enough to provoke |
| **Comedy beat** | 4–5 sec | Joke needs time to read; cut on the audience's mental "punchline received" |
| **Rhythmic action montage** | 2–4 sec each | Cumulative energy; no single shot dominates |
| **Cinematic / emotional center** | 6–8 sec | The shot the trailer is built around — let it land |
| **Interview / talking head** | 5–7 sec minimum | Below 5s the speech feels truncated |
| **Title card** | 3–5 sec | Long enough to read aloud + 1 sec |
| **End title** | 6–10 sec | Closure beat |
| **Climax / payoff reveal** | 6–10 sec | The "moneyshot" — overrides all rhythm rules |

**Ratio rule**: the longest shot in a teaser should be 2–4× the shortest. More than that and the sequence breaks rhythm; less and it feels monotone.

---

## VIII. Process — the editor's workflow Murch advocates

1. **First viewing — DON'T touch tools.** Just watch all the rushes. Make notes by hand.
2. **Mark "selects"** — the moments that are alive, the gold.
3. **First assembly is always too long.** The teaser will be 2–3× its target length on first pass.
4. **Cut by feel, then justify.** If a cut feels wrong but you can't say why — trust the feel, find the why later.
5. **Watch a cut, walk away, watch again.** Distance reveals what's hidden when too close.
6. **Show to one person you trust before locking.** Their gut reaction is data.

---

## IX. The Decisive Moment

Borrowed from Cartier-Bresson: the single frame in any take where everything aligns — gesture, expression, light, composition. **For every clip going into a sequence, identify its decisive moment** and ensure the cut either lands ON or LEADS TO that frame.

When using `pi.setInPoint(ss, 4)` and `pi.setOutPoint(ss + dur, 4)` via the bridge, the **out-point** of each clip should ideally fall at or just past the decisive moment — never before it.

---

## X. Concrete operational checklist for Premiere via the bridge

Before placing a clip on the timeline:
- [ ] Have I identified the decisive moment? (If not — `pr_set_playhead` and scrub via `pr_eval_jsx` to find it.)
- [ ] Does this clip serve emotion FIRST?
- [ ] Does it advance story?
- [ ] Is its duration appropriate to its beat type (table above)?
- [ ] Does its first frame eye-trace from the previous shot's last frame?
- [ ] Does its last frame complete a thought?
- [ ] If breaking 3D continuity — am I doing it deliberately?

After laying down a sequence:
- [ ] Pull `pr_list_timeline` and audit the duration distribution. Is the longest shot 2–4× the shortest?
- [ ] Are the 3 most important emotional beats the 3 longest shots?
- [ ] Is there variety — are no 4 consecutive shots the same length?
- [ ] Mark suspect cuts with `pr_add_marker` for human review.

---

## XI. Russian terminology mapping

When Vladimir uses Russian terms, map to Murch concepts:

| Russian | Murch |
|---|---|
| склейка | cut |
| монтажная фраза | montage sequence |
| ритм / темп | rhythm |
| эмоциональный центр | emotional center / decisive moment |
| внутрикадровый монтаж | intra-shot continuity |
| 8-ка / правило восьмёрки | 180° rule (3D continuity) |
| перебивка | cutaway / reaction shot |
| монтажный аттракцион | idea cut / Eisensteinian montage |
| стык по движению | motion-matched cut (eye-trace tool) |
| на эмоции | "cut on the emotion" |
| дать вздохнуть | "let it breathe" (extend a shot) |
| дожать | tighten, trim further |

---

## XII. Bridge tool reference (one-liners)

| Tool | Use during editing |
|---|---|
| `mcp__premiere__pr_status` | Sanity check before any session |
| `mcp__premiere__pr_get_project_info` | Inspect bins, find clips by name/nodeId |
| `mcp__premiere__pr_get_active_sequence` | Get sequence specs (fps, dimensions, tracks) |
| `mcp__premiere__pr_list_timeline` | Audit current cuts (durations, in/out, order) |
| `mcp__premiere__pr_get_playhead` / `pr_set_playhead` | Scrub to specific timecode |
| `mcp__premiere__pr_add_marker` | Mark decisive moments, problem cuts, notes |
| `mcp__premiere__pr_export_ame` | Send to Adobe Media Encoder for output |
| `mcp__premiere__pr_eval_jsx` | Escape hatch for any custom ExtendScript (set in/out, insertClip, motion params, color) |

---

## XIII. Analysis pipeline — making the LLM "see" motion

**Hard truth:** by default the LLM sees stop-frames, not motion. Murch's Rule of Six leans on perceiving how a gesture begins and completes — impossible from one frame. The pipeline below partially closes that gap.

### Tool: `tools/analyze_clips.py`

```bash
python3 skills/film-editing/tools/analyze_clips.py \
  /path/to/raw_footage \
  /path/to/output_log [--whisper]
```

Per clip, it produces:

| Output | What it captures | LLM use |
|---|---|---|
| `_thumb.jpg` | mid-clip frame, 480w | identify content (faces, objects, location) |
| `_strip.jpg` | **6 evenly-spaced frames hstacked** | approximate motion: see how subject moves across the clip |
| `motion_score` | scene-change frequency | distinguish action clips from static b-roll |
| `audio_peak_db` + `audio_peak_time_sec` | loudness peak + when | find applause, gasps, dialogue moments — these are emotional anchors |
| `audio_mean_db` | average loudness | identify speech-bearing vs ambient-only clips |
| `speech_text` (with `--whisper`) | transcript first 300 chars | exact dialogue + timing |

Output bundle: `report.json` (machine-readable) + `report.html` (human contact sheet).

### Mandatory editorial protocol with this data

Before designing a cutlist:

1. **Run the pipeline** on the source folder. ~12 min for 100 clips at 1080p.
2. **Open `report.html`** in a browser. Sort by motion + audio peak.
3. **Read each clip's `_strip.jpg`** — six frames give you a rough "how the action unfolds" view. A static b-roll clip is six identical frames; a true action clip has motion across the strip.
4. **Mark candidate IN/OUT points using `audio_peak_time_sec`** — the peak is usually where the emotional anchor lives. Set the OUT to land on or just past the peak.
5. **Cross-reference rankings against your gut picks** — if a clip you assumed was weak ranks high (or vice versa), look at its strip again. Often the in-point is wrong.

### Lessons from Grave Stakes case study

- **Don't trust a single thumbnail.** The clip `00172.MTS` looked like a static "wooden frame on grass" from one mid-frame. The strip revealed: first half static, **second half a tight digger close-up at ss≈15+**. Almost dropped a strong clip.
- **Audio peak ≠ visual peak.** For documentary footage, audio peaks (a clap, a shout, a tool strike) often mark the moment that ought to be in the cut. Use `audio_peak_time_sec` as a candidate IN-anchor.
- **Whisper `tiny` model fails on noisy field audio in non-English languages.** For real dialogue extraction use `--model medium --language XX`. Plan for ~15 sec/min of audio per Whisper pass.

### Roadmap for the analysis pipeline

| Capability | Status | How |
|---|---|---|
| Motion-score | ✅ shipped | ffmpeg scene detect |
| Audio peaks | ✅ shipped | ffmpeg astats + ametadata parse |
| 6-frame motion strip | ✅ shipped | ffmpeg hstack |
| HTML contact sheet | ✅ shipped | inline jinja-style template |
| Speech-to-text | ⚠️ needs medium-model + language hint | Whisper |
| OCR (signage, T-shirts) | 🚧 planned | tesseract on motion-strip frames |
| Face count + sentiment | 🚧 planned | mediapipe / opencv |
| Optical flow direction | 🚧 planned | ffmpeg mestimate filter for match-cut suggestion |
| Auto silent-trim | 🚧 planned | ffmpeg silencedetect → trim deadwood per clip |
| Multicam sync | 🚧 planned | audio waveform xcorr |

---

## XIV. Real video perception via the bundled `/watch` skill

The honest limitation called out in §XIII — "the LLM sees stop-frames, not motion" — is **partially closed** by vendoring [bradautomates/claude-video](https://github.com/bradautomates/claude-video) into `skills/watch/`. See `skills/watch/ATTRIBUTION.md` for the full credit.

`/watch` does what our 6-frame motion strip can't:
- Pulls **30–100 frames** per clip (auto-scaled to duration), not 6
- Pulls a **timestamped transcript** — native captions if available, Whisper API (Groq or OpenAI) as fallback
- Lets Claude `Read` every frame as a multimodal image and align to the transcript

### When to use `/watch` from inside an editing session

| Editing task | Without `/watch` | With `/watch` |
|---|---|---|
| Verify a candidate cut | one mid-frame thumbnail | `--start ss --end ss+dur` → ~30 frames + transcript over the cut window |
| Find the decisive moment in a clip | guess from one frame + `audio_peak_time_sec` | scan ~60 frames, hear what's said, pick precisely |
| Read interview content | broken `tiny` Whisper (see §XIII) | `--whisper groq` runs `whisper-large-v3` (handles Hungarian, Russian, etc.) |
| Study a reference trailer | n/a | `/watch <youtube-url> "what's the structure?"` |

### Concrete recipes

**Find decisive moment in a clip before placing it:**
```bash
python3 skills/watch/scripts/watch.py "/path/to/clip.MTS" --start 28 --end 35
```
Then in the editing thread: read the frames, pick the in/out, call `pi.setInPoint(...)` via the bridge.

**Get a real transcript of the interview clip used in Grave Stakes (Hungarian):**
```bash
# Set GROQ_API_KEY in ~/.config/watch/.env first
python3 skills/watch/scripts/watch.py "/Users/.../Videos/00195.MTS" --no-frames-mode-not-supported
# Or just normal call — script returns transcript regardless of frames
python3 skills/watch/scripts/watch.py "/Users/.../Videos/00195.MTS" --whisper groq
```
The transcript appears in the markdown report. Use the timestamped lines to pick which 6-second slice of the interview lands on the timeline.

**Reference-driven cutdown:**
```bash
# Vladimir wants the new teaser to feel like the Sundance trailer for Anora
python3 skills/watch/scripts/watch.py "https://youtu.be/<anora-trailer>" "describe the cut structure beat by beat with timestamps"
```
Claude will return a beat sheet you can encode as a recipe in `skills/trailer-bridge/`.

### Setup (one-time)

```bash
# macOS — auto-installs ffmpeg + yt-dlp via brew
python3 skills/watch/scripts/setup.py

# Set Whisper API key (Groq preferred — cheaper/faster, handles non-English well)
echo 'GROQ_API_KEY=...' > ~/.config/watch/.env
chmod 600 ~/.config/watch/.env
```

After that the editing skill can call `/watch` on any clip in the project bin.

### Cost discipline

- Each `/watch` call burns ~30–100 image tokens. On a 108-clip raw folder, watching all of them = ~5 000 frames = significant context pressure.
- **Don't watch everything.** Use `analyze_clips.py` first (motion + audio + horizon) to rank, then `/watch` only the top 10–15 candidates.
- For long clips, always use `--start`/`--end` once you know roughly where the action is.

---

## XV. Reference

- Walter Murch, *In the Blink of an Eye: A Perspective on Film Editing*, 2nd ed., Silman-James Press, 2001 (Russian: «В мгновение ока», аудиокнига Кирилла Никитенко 2024)
- Murch's Sydney lecture (1988) — origin of the Rule of Six
- StudioBinder, "Walter Murch's The Rule of Six" — modern annotation with examples (*Godfather*, *Eternal Sunshine*, *Bonnie and Clyde*, *Fight Club*, *Mad Max: Fury Road*, *The Shining*, *Inception*)

---

**Last updated:** 2026-05-04. Maintained as part of the `premiere-claude-bridge` open-source release.
