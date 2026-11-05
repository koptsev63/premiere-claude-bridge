"""Install the add-on skills only after their description has been shown.

    python -m core.addons_install list
    python -m core.addons_install describe <skill>          prints ABOUT.md, notes that it was shown
    python -m core.addons_install install <skill> --yes [--dest DIR]
    python -m core.addons_install uninstall <skill> [--dest DIR]

The order is enforced here, not only in the skill text: `install` refuses unless `describe`
printed the CURRENT text of that skill's ABOUT.md (a changed text must be shown again), and
unless `--yes` says the person agreed. It copies the skill folder and nothing else: it does not
install Python packages and does not touch other settings.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[1] / "skills"
STATE = Path.home() / ".claude" / ".addons_described.json"
DEFAULT_DEST = Path.home() / ".claude" / "skills"
OFFERED = ("dailies-prep", "nle-handoff")


def _about(name: str) -> Path:
    p = SKILLS / name / "ABOUT.md"
    if name not in OFFERED or not p.exists():
        raise SystemExit(f"no such skill in this package: {name} (have: {', '.join(OFFERED)})")
    return p


def _digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def describe(name: str) -> str:
    p = _about(name)
    text = p.read_text(encoding="utf-8")
    st = _state()
    st[name] = _digest(p)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1), encoding="utf-8")
    return text


def install(name: str, dest: Path, yes: bool) -> list[str]:
    p = _about(name)
    if _state().get(name) != _digest(p):
        raise SystemExit(f"first show the description: python -m core.addons_install describe {name}")
    if not yes:
        raise SystemExit("not installed: the person has not agreed (--yes)")
    target = dest / name
    if target.exists():
        raise SystemExit(f"{target} already exists: remove it first (uninstall) or choose another --dest")
    shutil.copytree(SKILLS / name, target)
    return sorted(str(x.relative_to(dest)) for x in target.rglob("*") if x.is_file())


def uninstall(name: str, dest: Path) -> str:
    target = dest / name
    if name not in OFFERED:
        raise SystemExit(f"{name} is not one of this package's skills")
    if not target.exists():
        raise SystemExit(f"{target} is not there")
    shutil.rmtree(target)
    return str(target)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="install add-on skills after describing them")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    d = sub.add_parser("describe")
    d.add_argument("skill")
    i = sub.add_parser("install")
    i.add_argument("skill")
    i.add_argument("--yes", action="store_true")
    i.add_argument("--dest", default=str(DEFAULT_DEST))
    u = sub.add_parser("uninstall")
    u.add_argument("skill")
    u.add_argument("--dest", default=str(DEFAULT_DEST))
    a = ap.parse_args()
    if a.cmd == "list":
        for n in OFFERED:
            first = next((l for l in (SKILLS / n / "ABOUT.md").read_text(encoding="utf-8").splitlines() if l.startswith("# ")), n)
            print(first[2:])
    elif a.cmd == "describe":
        print(describe(a.skill))
    elif a.cmd == "install":
        for f in install(a.skill, Path(a.dest).expanduser(), a.yes):
            print("copied", f)
    else:
        print("removed", uninstall(a.skill, Path(a.dest).expanduser()))
