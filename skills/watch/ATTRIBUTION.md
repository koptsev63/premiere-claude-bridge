# Attribution

This skill is **vendored** from the upstream open-source project:

- **Project:** [bradautomates/claude-video](https://github.com/bradautomates/claude-video)
- **Author:** Bradley Bonanno
- **License:** MIT (see `LICENSE`)
- **Version vendored:** 0.1.3 (per `.claude-plugin/plugin.json`)

## Why it's here

The `premiere-claude-bridge` project encodes editing principles (Walter Murch's Rule of Six, motion analysis, horizon detection) but the LLM driving Premiere has only ever had access to **stop-frames** — six-frame strips per clip, nothing more. That limits how well it can reason about motion, dialogue, decisive moments, micro-expressions, and pacing.

`/watch` closes that gap. It downloads (or accepts a local path), extracts an auto-scaled batch of frames (~30–100 depending on duration), pulls a timestamped transcript (native captions first, Whisper fallback), and hands all of it to Claude as multimodal input. For our use case — analyzing 60+ minutes of raw footage before designing a teaser cutlist — it transforms what Claude can actually perceive about the material.

## How we use it inside `film-editing`

When the editing skill needs to:

1. **Verify a cut feels right** — run `/watch <clip> --start <ss> --end <ss+dur>` to see the actual motion across the cut window
2. **Find a decisive moment** — run `/watch <clip>` and let Claude scan ~60 frames + the transcript to nominate the in/out
3. **Read interview content** — run `/watch <interview-clip>` and use the transcript (Groq Whisper handles Hungarian/Russian/etc. far better than the `tiny` model we shipped in v0.1)
4. **Analyze a reference** — feed a YouTube link of a film/trailer Vladimir wants to study, get back the structure he can apply

See `skills/film-editing/SKILL.md` Section XV for the integrated workflow.

## What we did NOT modify

This is a clean copy. No code edits, no SKILL.md edits. If you want to upgrade to a newer upstream version, delete this directory and re-vendor:

```bash
rm -rf skills/watch
git clone --depth 1 https://github.com/bradautomates/claude-video.git skills/watch
rm -rf skills/watch/.git
# Add this ATTRIBUTION.md back.
```

## License compatibility

The bridge core is MIT. The `watch` skill is MIT. Same license, no friction. Bradley Bonanno's copyright notice is preserved in `LICENSE` — please retain it in any redistributions.

## Optional: install as a separate Claude Code plugin instead

If you don't want it bundled with the bridge, you can install upstream directly and skip this directory:

```bash
/plugin marketplace add bradautomates/claude-video
/plugin install watch@claude-video
```

Either path works. We bundle it because the bridge's value proposition depends on Claude having real video perception, not because we wrote it.
