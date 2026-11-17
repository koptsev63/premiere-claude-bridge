---
name: addons-installer
description: Installing skills from the bridge-addons package with a mandatory, detailed explanation BEFORE installation. Use when asked to install, connect, enable or update the dailies-prep and nle-handoff skills, or simply "what is in this package".
---

# Installing skills: explain first, then offer

The order is strict. Nothing gets installed until the person has read and understood what they are installing.

## Steps

1. **Show the list.** `python -m core.addons_install list` - which skills the package contains.
2. **Explain each skill in detail, in plain words.** For each skill run `python -m core.addons_install describe <name>` - the command prints the full description and records that it was shown. Then retell it in your own words, without jargon, and be sure to cover these points:
   - what the skill does and why the person needs it (with an easy example);
   - how it works, step by step;
   - when it turns on (which phrases);
   - what it reads and what it writes, and whether it changes anything in other people's files or in Resolve;
   - what the machine needs (programs, packages, templates);
   - what it cannot do and where it can go wrong;
   - how to remove it.
   Do not shrink this to one line and do not paste the description whole: retell it so the person can answer "yes" or "no" knowingly.
3. **Only after that, offer to install.** Ask separately for each skill (install / do not install) and where it should go (default `~/.claude/skills/`).
4. **Install only what was agreed.** `python -m core.addons_install install <name> --yes [--dest PATH]`. The command refuses on its own if that skill's description was not shown.
5. **Report.** What was copied and where, which Python packages still need to be installed (the command does not install them), and how to remove it: `python -m core.addons_install uninstall <name>`.

## What not to do
- Do not run `install` before `describe`.
- Do not install Python packages or change other people's settings without separate consent.
- Do not say "installed and working": the skill is installed, but whether it works in Resolve, Premiere and Avid is confirmed only by the first run.
