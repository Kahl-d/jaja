#!/usr/bin/env python3
"""Build state/linkedin/LINKEDIN.md from the approved texts (state/linkedin/approved/*.txt) and the skills map.

    python3 scripts/build_linkedin_md.py            (from the job-search data root)

LINKEDIN.md is the one source for the owner's LinkedIn. Edit the approved/*.txt files or the `policy.linkedin_doc`
config, never the generated sections of LINKEDIN.md by hand; the Changelog section is carried over from the existing
file (append-only).

The document is assembled from `policy.linkedin_doc.sections`, an ordered list of items:

  {lines: [...]}      static Markdown lines (the narrative: vision, settings, intro card, notes). Tokens replaced:
                      {RUN} today's date, {TODAY} the config `decision_date`, {ABOUT_LEN} characters in about.txt,
                      {N_SKILLS} skills in the skills map, {N_ADD} skills beyond `skills_keep`, {N_FREE} slots left
                      of 100, {SKILLS_KEEP} and {SKILLS_REMOVE} the config counts.
  {block: "x.txt"}   the approved text approved/x.txt in a fenced block
  {headlines: true}  the table rows of `policy.linkedin_doc.headlines` ([headline, where, outcome])
  {experience: true} one "###" entry per `policy.linkedin_doc.experience` ({title, file, meta, before, evidence,
                     status}) with its approved text
  {skills_table: true}  the skills map (state/linkedin/tools/skills_map.py, SKILLS = [(name, evidence, families)])
  {changelog: true}  the existing Changelog section carried over, else `changelog_default`
"""
from __future__ import annotations

import datetime as _dt
import re
import sys
from pathlib import Path

import agency_config as cfg
from agency_paths import DATA_ROOT as JS  # noqa: E402

LI = JS / "state/linkedin"
A = LI / "approved"
sys.path.insert(0, str(LI / "tools"))
try:
    from skills_map import SKILLS  # noqa: E402
except ImportError:                       # no skills map yet: the table is empty
    SKILLS = []

CHANGELOG_RE = re.compile(r"\n## [^\n]*Changelog[^\n]*\n.*?(?=\n## |\Z)", re.S)


def txt(name: str) -> str:
    return (A / name).read_text().strip()


def block(t: str) -> str:
    return "```text\n" + t.strip() + "\n```"


def approved_block(name: str) -> str:
    """The approved text in a fenced block, or a plain note when that file has not been written yet."""
    if not (A / name).is_file():
        return f"(no approved text yet: state/linkedin/approved/{name})"
    return block(txt(name))


def _doc() -> dict:
    return cfg.get("policy.linkedin_doc", {}) or {}


def main() -> None:
    doc = _doc()
    run = _dt.date.today().isoformat()
    old = (LI / "LINKEDIN.md").read_text() if (LI / "LINKEDIN.md").exists() else ""
    m = CHANGELOG_RE.search(old)
    changelog = m.group(0).split("\n", 2)[2] if m else ""
    about_len = len(txt("about.txt")) if (A / "about.txt").exists() else 0
    keep = int(doc.get("skills_keep") or 0)
    tokens = {"{RUN}": run, "{TODAY}": str(doc.get("decision_date") or run), "{ABOUT_LEN}": str(about_len),
              "{N_SKILLS}": str(len(SKILLS)), "{N_ADD}": str(max(len(SKILLS) - keep, 0)),
              "{N_FREE}": str(100 - len(SKILLS)), "{SKILLS_KEEP}": str(keep),
              "{SKILLS_REMOVE}": str(int(doc.get("skills_remove") or 0))}

    def fill(line: str) -> str:
        for k, v in tokens.items():
            line = line.replace(k, v)
        return line

    out: list[str] = []
    for item in doc.get("sections") or [{"lines": ["# LinkedIn: the one source", ""]}]:
        if "lines" in item:
            out += [fill(str(x)) for x in item["lines"]]
        elif "block" in item:
            out.append(approved_block(str(item["block"])))
        elif item.get("headlines"):
            out += [f"| {h} | {w} | {o} |" for h, w, o in doc.get("headlines") or []]
        elif item.get("experience"):
            for e in doc.get("experience") or []:
                out += [f"### {e['title']}", "",
                        f"{e.get('meta', '')}. Before: {e.get('before', '')}. Status: **{e.get('status', 'proposed')}**. "
                        f"Evidence: {e.get('evidence', '')}.", "", approved_block(str(e["file"])), ""]
        elif item.get("skills_table"):
            out += [f"| {i} | {n} | {e} | {f} |" for i, (n, e, f) in enumerate(SKILLS, 1)]
        elif item.get("changelog"):
            out += [changelog.strip() if changelog.strip() else "\n".join(str(x) for x in doc.get("changelog_default") or [
                "| date | section | change | source |", "|---|---|---|---|"])]
    (LI / "LINKEDIN.md").write_text("\n".join(out))
    print("LINKEDIN.md", sum(1 for _ in out), "lines")


if __name__ == "__main__":
    main()
