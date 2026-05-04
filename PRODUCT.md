# premiere-claude-bridge — Product Brief

> **TL;DR.** Open-source bridge that lets Claude (or any LLM) control Adobe Premiere Pro on the desktop — import, sequence build, marker placement, AME export, anything ExtendScript can do — through natural language. Adobe's official Creative Cloud connector explicitly does NOT do this; this fills the gap.

---

## What it is

```
Claude Code  ──stdio──▶  MCP Server (Node)  ──WebSocket──▶  CEP Panel  ──evalScript──▶  Premiere host.jsx
```

Three components:
1. **MCP server** (`mcp-server/server.js`) — Node, exposes `mcp__premiere__*` tools to Claude
2. **CEP panel** (`cep-extension/`) — Adobe extension that runs ExtendScript on demand
3. **Skill pack** (`skills/film-editing/`) — Walter Murch's editing principles encoded as decision rules + analysis tools (motion-aware logging, audio peaks, contact sheets)

Adobe's stance: their Creative Cloud connector contains 4 cloud video tools (`video_create_quick_cut`, `media_summarize`, `video_resize`, `media_enhance_speech`) — none of them touch desktop Premiere. They explicitly recommend "use Adobe Premiere or Rush" for trim-by-timestamp and format conversion. This bridge IS that.

## Who needs it

| Audience | Use case | Pain right now |
|---|---|---|
| **Solo doc/indie editors** | Auto-log 4 hours of footage in 5 min, get HTML contact sheet, build rough assemblies via prompt | Spend 4-8 hrs logging by hand per project |
| **Editor-directors** (Vladimir's segment) | Use Claude as on-set assistant editor — "import these 80 clips, mark every applause moment" | No agentic workflow on desktop Premiere |
| **Production assistants** | Stop being a slate-clicker. Run logging passes, dailies, AME batches | Dropbox + spreadsheets + manual setup |
| **Film schools** | Teach Murch's principles WITH a working example — not just a book | Theory without practice tool |
| **Content teams** | Auto-generate variants of one teaser for different platforms | Manual recut every time |

## Non-trivial monetization options

Listed by ascending creativity. Open-source-core stays free in all of them — paid tiers are skill packs, services, or partnerships.

### Standard
1. **OSS core, donations** — Patreon/GitHub Sponsors. Probably $0–$500/mo.
2. **Pro tier** — paid analysis tools (Whisper transcription, face detection, advanced LUT auto-color). $9/mo or $79 lifetime.
3. **Skill packs** — genre-specific recipe bundles ($19 each):
   - `trailer-bridge` — feature trailer cutdown templates
   - `reel-bridge` — vertical/social formatting workflows
   - `podcast-cut-bridge` — auto-remove ums, dead air, multicam sync
   - `interview-bridge` — multicam doc interviews

### Less obvious
4. **Course / training** — "How I edit a teaser with Claude + Premiere in 10 minutes." 90-min video course on Gumroad. $29–$49. The bridge is the lead magnet; you're selling the *workflow*. Vladimir's strength: he can record himself doing real client work.
5. **Templates marketplace** — extends Vladimir's existing Notion-template business model into Premiere territory. Build a small storefront, accept submissions from other editors.
6. **Custom integration consulting** — production companies ($800-$3500 setup) for customized skill packs tied to their pipelines (Avid Bin Lock workflows, multi-editor handoffs).

### Non-trivial
7. **"AI Editor's Assistant" subscription** — managed service. You/team handle a client's footage organization, daily contact sheets, batch processing, raw-cut assemblies. SaaS-with-human-in-loop. $99–$299/mo per editor seat. Higher LTV than pure SaaS.
8. **Festival / school partnership** — bundle bridge with a workshop. Vladimir already has TFL/CineLink connections. Speaking + bundle. €500–€2000 per workshop, plus participants buy skill packs.
9. **Adobe partnership / acquisition long-shot** — Adobe has been vocal about "AI in Premiere" but their CC tools can't actually control desktop Premiere due to corporate constraints. A community-built bridge proving the demand → either Adobe licenses, partners, or acquires. Low odds, asymmetric upside. Free to attempt: open-source first, build community, then pitch.
10. **Closed beta + Discord/TG community** — gate early access via Patreon ($5/mo for beta), build a community of beta users who become evangelists. Open-source after 6 months when revenue plateau. Builds brand + network effect.
11. **"Editor in a Pocket" mobile companion** — phone app: shoot footage, push to home Mac+Premiere, see auto-assembly on phone. Different revenue model: mobile + cloud sync. €4.99 one-off + €2/mo cloud.
12. **White-label for agencies** — let video production agencies rebrand it as their internal tool. License $500–$2000/yr per agency.
13. **Live editing on Twitch** — Vladimir live-edits real client work using the bridge while subscribers watch. Twitch subs + clients pay for the work + visibility for the bridge. Hyper-niche but unique positioning.

## Recommended path for Vladimir specifically

Stack three:
- **Open-source core** (reputation, GitHub stars, developer goodwill) — $0
- **Paid skill packs** ($19 each, Vladimir's existing Notion-templates muscle memory) — target $500–$2000/mo within 6 months
- **Course/training on Gumroad** ($39, recorded once) — passive, $200–$1000/mo
- **Plus**: open Adobe partnership pitch (long shot, free attempt)

**Year 1 realistic:** $500–$5000/mo combined.
**Year 2 if Adobe pitch lands or 1 enterprise client:** order of magnitude higher.

## Roadmap (post-launch)

| When | What |
|---|---|
| Day 0 (publish) | OSS release v0.1, README with install steps, demo GIF, TG post |
| Week 1 | Beta-test with 20 TG subscribers, gather feedback |
| Week 2-4 | Iterate, fix install pain, add one skill pack as paid PoC |
| Month 2 | Course recording session |
| Month 3 | Launch course + Skills marketplace |
| Month 6 | Adobe pitch deck + outreach |

## License decision

**MIT** — maximum adoption, no friction for production companies to use it commercially. Skill packs and course can be separately licensed (commercial).

Alternative: **AGPL** for the bridge core — forces SaaS reseller forks to open-source. Considered if afraid of someone else building a paid service on top. Probably too restrictive for adoption.

→ **Recommended: MIT.**

## Repo layout (for GitHub)

```
premiere-claude-bridge/
├── README.md                      ← install + demo + screenshots
├── LICENSE                        ← MIT
├── PRODUCT.md                     ← this file
├── CONTRIBUTING.md                ← how to add skills, recipes
├── mcp-server/
│   ├── server.js                  ← patched, multi-instance safe
│   ├── package.json
│   └── README.md
├── cep-extension/
│   ├── CSXS/manifest.xml
│   ├── index.html
│   ├── js/main.js
│   ├── jsx/host.jsx               ← with JSON polyfill
│   └── README.md
├── skills/
│   └── film-editing/
│       ├── SKILL.md               ← Murch operating system
│       ├── tools/
│       │   └── analyze_clips.py   ← motion/audio/contact sheet
│       └── recipes/
│           ├── teaser-from-rushes.md
│           ├── interview-cleanup.md
│           └── multicam-sync.md
├── examples/
│   ├── grave-stakes-teaser/
│   │   ├── before.mp4
│   │   ├── after.mp4
│   │   ├── cutlist.json
│   │   └── README.md
│   └── ...
└── docs/
    ├── install.md
    ├── architecture.md
    └── troubleshooting.md
```

## Demo content (lead magnet)

The Grave Stakes teaser process IS the demo:
- 108 raw .MTS clips
- Auto-logged via `analyze_clips.py` → HTML contact sheet
- Vladimir picks selects through Claude prompts
- Claude builds 3 cutlist variants
- Final cut exported

Record a 4-min screencast of this → embed in README → pin to TG channel.
