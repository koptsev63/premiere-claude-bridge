# Bridge add-ons: day preparation and edit handoff

A set on top of `premiere-claude-bridge`. It replaces nothing in the bridge: it adds new files.

## What is inside

| what | where | why |
|---|---|---|
| Audio sync | `core/dailies_sync.py` | takes by timecode and MXF headers, fitting audio to camera by correlation, reliability report |
| Sound restore | `core/adapters/resolve_restore_sound.py` | gives every clip on a Resolve timeline the sound of its take |
| Staged frame check | `core/framecheck.py` | numbers, overview, edges, frames around cuts (seamless edits only) |
| Premiere project from Resolve | `core/prproj_build.py`, `core/adapters/resolve_spec.py`, `core/adapters/resolve_prproj_job.py` | `.prproj` from an open Resolve project |
| Avid bin | `core/avb_build.py` | masters with columns, sync clips, group clips |
| Avid project settings | `core/avp_build.py` | a separate `.avp` and `Settings.xml` |
| AAF and XML check | `core/handoff_check.py` | report and filling in tape names |
| Two skills for Claude | `skills/dailies-prep/`, `skills/nle-handoff/` | order of work and limits; each has an `ABOUT.md` with a plain-words description |
| Skill installer | `skills/addons-installer/`, `core/addons_install.py` | a detailed explanation first, then an offer to install |
| Premiere format description | `docs/prproj-format.md` | how `.prproj` is built |
| Tests | `core/tests/test_prproj_build.py`, `test_avb_build.py`, `test_avp_build.py` | run one at a time: `python -m core.tests.test_avb_build` |

## What the machine needs

- The bridge is already installed, Python 3.13 and Resolve Studio (external scripts: Local).
- Packages: `numpy`, `av` (PyAV), `Pillow` for sync and frames; `pyaaf2` for AAF; `pyavb` for Avid bins.
- Your own templates. Other people's projects are deliberately NOT included. You need: a real `.prproj` (saved by the oldest Premiere the result must open in), a real Avid project (`.avp` and its `Settings.xml`).

## Installation

1. Unpack the archive into the root of the bridge repository: the `core`, `docs`, `skills` folders merge with the existing ones.
2. Install the skills through the installer, not by copying: ask Claude "install the skills from the package". It will first explain each skill in detail and in plain words (what it does, when it triggers, what it reads and writes, what it needs, what it cannot do, how to remove it) and only then offer to install. By hand it is the same: `python -m core.addons_install list`, then `describe <skill>`, then `install <skill> --yes`. The `install` command refuses to work until the description has been shown and until `--yes` is given. The installer copies only the skill folder (by default into `~/.claude/skills/`) and does not install Python packages.
3. Install check (quick): `python -m core.tests.test_avb_build` and `python -m core.tests.test_avp_build`. Tests that need a template are skipped without `AVP_TEMPLATE` / `PRPROJ_TEMPLATE`.

## Order of work

1. `dailies-prep`: `scan` -> `sync` -> check the result (`trusted`, `fixed_by_roll`).
2. Sound into the timeline: `resolve_restore_sound.py` (camera option: `keep` / `mute` / `omit`).
3. Frame check in stages; dense sheets around cuts only if the edit is seamless.
4. `nle-handoff`: Premiere project, Avid bin and settings, AAF/XML check before sending.

## Honest limits

- Neither the Premiere project, nor the bin, nor the `.avp` has been opened in the target program: structure, references and comparison with Resolve were checked. The first run on the editor's side is the check; send the error text if there is one.
- Premiere does not carry over: Resolve grade and effects (the clip is flagged and goes into the report), volume keys and fades, transitions, markers, opacity, compound clips, titles. The full list is in the builder's report.
- `.avp`: the list of bins inside stays as in the template. One format preset is known (`hd-1080p24`).
- AAF: tape names are filled in, but what exactly this fixes when opening in Avid is not confirmed.
- Sound restore for Resolve was written on a working project and has not been rerun in Resolve since being moved to the general form.
