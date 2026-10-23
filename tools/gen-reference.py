#!/usr/bin/env python3
"""Regenerate the agent, lane, skill and routine tables in docs/REFERENCE.md from the repo itself.

    python3 tools/gen-reference.py            # rewrite the generated block
    python3 tools/gen-reference.py --check    # exit 1 when docs/REFERENCE.md is out of date

The block sits between `<!-- BEGIN GENERATED TABLES ... -->` and `<!-- END GENERATED TABLES -->`; everything else in the
file is hand-written. Sources: agents/*.md (purpose, tier_max), lanes/*/LANE.yaml, skills/*/SKILL.md (description),
routines/*.yaml (schedule, enabled, purpose). Names always match the files.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "REFERENCE.md"
BEGIN, END = "<!-- BEGIN GENERATED TABLES", "<!-- END GENERATED TABLES -->"


def front(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---", text, re.S)
    try:
        return yaml.safe_load(m.group(1)) if m else {}
    except yaml.YAMLError:
        return {}


def sentence(text: object, limit: int = 150) -> str:
    """The first sentence of a description, one line, cut at a word boundary when longer than limit."""
    s = " ".join(str(text or "").split())
    m = re.match(r"(.+?[.;])(\s|$)", s)
    if m and len(m.group(1)) >= 25:
        s = m.group(1)
    s = s.rstrip(".;")
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip(",;:(") + " ..."
    return s


def cell(text: object) -> str:
    return str(text).replace("|", "\\|")


def table(head: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(cell(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def generate() -> str:
    agents = [(p.stem, front(p)) for p in sorted((ROOT / "agents").glob("*.md"))]
    skills = [(p.parent.name, front(p)) for p in sorted((ROOT / "skills").glob("*/SKILL.md"))]
    lanes = [(p.parent.name, yaml.safe_load(p.read_text(encoding="utf-8"))) for p in sorted((ROOT / "lanes").glob("*/LANE.yaml"))]
    routines = [(p.stem, yaml.safe_load(p.read_text(encoding="utf-8"))) for p in sorted((ROOT / "routines").glob("*.yaml"))]
    disabled = all(not (d or {}).get("enabled") for _, d in routines)
    parts = [f"### Agents ({len(agents)})", "",
             "Role cards live in `agents/`; Claude Code loads them from the plugin and `scripts/kernel/sync.py` keeps their native "
             "fields in step.", "",
             table(["Agent", "What it does", "Max tier"],
                   [[f"`{n}`", sentence(m.get("purpose") or m.get("description")), m.get("tier_max", "")] for n, m in agents]),
             "", f"### Lanes ({len(lanes)})", "",
             "A lane is a long-lived session with a charter, a roster and a scope. Launch one from your data folder: "
             "`claude --agent lane-<type> --settings <repo>/.claude/lane-settings/<type>.json` (with the plugin installed the agent "
             "is `jaja:lane-<type>`).", "",
             table(["Lane", "Title", "Agents it uses"],
                   [[f"`{n}`", (d or {}).get("title", ""), ", ".join((d or {}).get("roster") or [])] for n, d in lanes]),
             "", f"### Skills ({len(skills)})", "",
             "Invoked as `/jaja:<skill>`.", "",
             table(["Skill", "Use it when"],
                   [[f"`{n}`", sentence(re.sub(r"^Use (only )?(when |in |after |for |on |at |each |every )?", lambda mm: mm.group(0), str(m.get("description") or "")), 170)]
                    for n, m in skills]),
             "", f"### Routines ({len(routines)}{', all disabled' if disabled else ''})", "",
             table(["Routine", "Schedule (cron)", "What it does"],
                   [[f"`{n}`", f"`{(d or {}).get('schedule', '')}`", sentence((d or {}).get("purpose") or (d or {}).get("description"), 120)]
                    for n, d in routines]), ""]
    return "\n".join(parts)


def main() -> int:
    text = DOC.read_text(encoding="utf-8")
    a = text.index(BEGIN)
    a_end = text.index("\n", a) + 1
    b = text.index(END)
    new = text[:a_end] + "\n" + generate() + "\n" + text[b:]
    if "--check" in sys.argv:
        if new != text:
            print("docs/REFERENCE.md is out of date: run python3 tools/gen-reference.py")
            return 1
        print("docs/REFERENCE.md is current")
        return 0
    DOC.write_text(new, encoding="utf-8")
    print("docs/REFERENCE.md updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
