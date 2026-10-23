#!/usr/bin/env python3
"""Create a data folder for the jaja plugin.

    python3 tools/init-data.py PATH [--demo] [--set-config] [--vault-root DIR] [--quiet]

PATH becomes the folder that holds YOUR records: profile/, resumes/, applications/, prospects/, jobs/, state/,
hub/, targets/, views/, config/ and the runtime/ folder (approvals, run receipts, secret pointers). The code stays
in the repo; this folder is never part of it.

  --demo         fill PATH with the synthetic demo data (examples/demo-data: "Sam Rivera", Acme Corp, Globex,
                 Initech) instead of a blank skeleton.
  --set-config   write `data_root: PATH` into ~/.config/jaja/config.yaml (JAJA_CONFIG overrides the
                 file) so every script finds the folder without an environment variable. Other keys in that
                 file are kept. Without this flag the config file is not touched.
  --vault-root   the Obsidian vault root the views/*.base files are written for (default: PATH itself).

Never overwrites: a file that already exists is skipped and reported. Safe to run again.
Exit 0 on success, 2 on a usage error.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = CODE_ROOT / "templates" / "data"
RESUME_TEMPLATES = CODE_ROOT / "templates" / "resume"
DEFAULTS_D = CODE_ROOT / "config" / "defaults.d"
DEMO = CODE_ROOT / "examples" / "demo-data"

DIRS = ["profile/corpus", "profile/answers", "resumes/bases", "resumes/variants", "applications", "prospects", "jobs",
        "jobs-pending", "companies", "state", "graph", "touches", "outbox", "pursuits", "legal", "hub/lanes",
        "hub/briefs", "targets", "views", "config", "runtime/work/approvals", "runtime/logs/runs",
        "runtime/vault"]


def config_file() -> Path:
    env = os.environ.get("JAJA_CONFIG") or os.environ.get("JOB_AGENCY_CONFIG")
    if env:
        return Path(env).expanduser()
    new, old = Path.home() / ".config" / "jaja", Path.home() / ".config" / "job-agency"
    return (old if (old.is_dir() and not new.exists()) else new) / "config.yaml"


class Writer:
    def __init__(self, root: Path, quiet: bool = False):
        self.root, self.quiet = root, quiet
        self.created: list[str] = []
        self.skipped: list[str] = []

    def file(self, rel: str, text: str | bytes) -> None:
        path = self.root / rel
        if path.exists():
            self.skipped.append(rel)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
        self.created.append(rel)

    def dirs(self, names: list[str]) -> None:
        for name in names:
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def report(self) -> None:
        if self.quiet:
            return
        print(f"created {len(self.created)} file(s), skipped {len(self.skipped)} existing")
        for rel in self.skipped:
            print(f"  skipped (exists): {rel}")


# ----------------------------------------------------------------------------- config guide
def _defaults_by_section() -> dict[str, dict]:
    """Every key the code reads, merged from config/defaults.d/*.yaml, split into identity / style / policy."""
    import yaml
    merged: dict = {}

    def merge(base: dict, extra: dict) -> None:
        for k, v in extra.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                merge(base[k], v)
            else:
                base[k] = v

    for f in sorted(DEFAULTS_D.glob("*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict):
            merge(merged, data)
    return {name: merged.get(name) or {} for name in ("identity", "style", "policy")}


def config_guide(section: str, values: dict) -> str:
    import yaml
    intro = {
        "identity": "who you are on paper: name, emails, signature, resume file name, profile URLs, time zone",
        "style": "how you write: banned characters, words and phrases, greeting and sign-off, message rules",
        "policy": "your search rules: years gates, location rules, outreach limits, apply rules, pacing",
    }[section]
    head = (f"# {section}.yaml: {intro}.\n"
            "# Every key the scripts read is listed below with its neutral default, commented out.\n"
            "# To set one, remove the leading '# ' from the key and its parents and change the value.\n"
            "# Values here win over the defaults in the repo's config/defaults.d/. Never put a secret in this file.\n#\n")
    body = yaml.safe_dump({section: values}, sort_keys=False, allow_unicode=True, default_flow_style=False, width=110)
    return head + "".join(f"# {ln}\n" if ln.strip() else "#\n" for ln in body.splitlines())


# ----------------------------------------------------------------------------- skeleton content
def _today() -> str:
    return dt.date.today().isoformat()


def skeleton_files() -> dict[str, str]:
    today = _today()
    files: dict[str, str] = {}
    files["README.md"] = f"""# Your jaja data folder

Created {today} by `tools/init-data.py`. The code lives in the jaja repo; **this folder is yours** and is never
part of the repo. Keep it private (a private git repo, or a synced folder you trust).

| Folder | What goes there |
|---|---|
| `profile/` | `facts.yaml` and `evidence.yaml` (the truth sheet and where each fact comes from), `PROFILE.md`, `VOICE.md`, `skills-taxonomy.yaml`, `corpus/`, `answers/` |
| `resumes/` | `bases/<name>/resume.yaml` (your base resume) and `variants/` (one per role) |
| `applications/` | one folder per application with `application.md` and an append-only event log (use `scripts/tracker.py`) |
| `prospects/` | one file per person you may contact (recruiters, founders, referrers) |
| `jobs/` | one file per posting found (written by the scout scripts) |
| `companies/` | a researched dossier per company |
| `outbox/` | drafts waiting for your approval; nothing is ever sent from here |
| `targets/` | what you look for: `roles.md`, `fit-gates.yaml`, `platforms.yaml`, `mass.yaml` |
| `views/` | Obsidian Bases (`*.base`) over the folders above; open this folder as a vault |
| `hub/` | the orchestration hub: `programme.json`, `rulings.yaml`, `lanes/`, `briefs/` |
| `config/` | `identity.yaml`, `style.yaml`, `policy.yaml`: your values (every key listed, commented out) |
| `legal/` | work-authorization timers (`timers.md`) |
| `runtime/` | approvals (`work/approvals/`), run receipts (`logs/runs/`), secret *pointers* (`vault/`; never values) |

## First steps

1. Fill `config/identity.yaml` (name, emails, time zone) and `config/style.yaml`.
2. Fill `profile/facts.yaml`, then add one evidence id per fact in `profile/evidence.yaml`.
3. Write `resumes/bases/general/resume.yaml` (a fully fictional example is in the repo's `examples/demo-data/`).
4. Fill `targets/roles.md` and `targets/fit-gates.yaml` with what you want and what you refuse.
5. Run `python3 scripts/doctor.py` from the repo to check everything.

Tokens for Gmail, Calendar and the rest live in `~/.config/jaja/`, outside this folder (docs/SETUP-CONNECTORS.md).
"""
    files[".gitignore"] = ".cache/\n.DS_Store\nruntime/vault/*\n!runtime/vault/README.md\n"
    files["profile/facts.yaml"] = f"""# facts.yaml - the truth sheet: canonical identity, education, employment, projects, skills.
# Every value cites evidence ids defined in evidence.yaml. You rule; agents propose.
schema_version: 1
updated: {today}
identity:
  full_name: ""
  name_on_resumes: {{canonical: "", status: needs-confirmation}}
  emails: {{}}
  location: {{value: "", status: needs-confirmation}}
  links: {{}}
education: []
employment: []
projects_index: []
skills: {{}}
rules: []
conflicts: []
"""
    files["profile/evidence.yaml"] = f"""# evidence.yaml - evidence id -> where a fact comes from.
# Ids look like ev-<topic>-001. Each entry needs source, locator, captured_at and note.
# Sources that hold IDs, credentials or compensation are cited by locator only, never copied.
schema_version: 1
updated: '{today}'
count: 0
evidence: {{}}
"""
    files["profile/PROFILE.md"] = ("# Your master profile\n\nThe finalized profile every resume, message and LinkedIn edit is drawn from. "
                                   "Write it from facts.yaml, with an evidence id after each claim.\n")
    files["profile/VOICE.md"] = """# VOICE.md

How you write. Fill it in from your own approved messages, then keep it short.

- Greeting and sign-off:
- Sentence length and tone:
- Words you use, words you never use:
- A sample of a message you were happy with (paste it, do not paraphrase):
"""
    files["profile/skills-taxonomy.yaml"] = f"""# skills-taxonomy.yaml - skills with level, last use and evidence ids.
# Levels: expert, strong, working, familiar. A skill with no evidence stays off resumes.
schema_version: 1
updated: {today}
groups: {{}}
"""
    files["profile/corpus/README.md"] = ("# corpus/\n\nOne folder per experience, project, education, award or publication, each with a README.md "
                                         "whose frontmatter carries `id, kind, org, title, dates, location, status, discretion, lenses, evidence`.\n")
    files["profile/answers/README.md"] = ("# answers/\n\nReusable answers to application questions (why this field, a project you are proud of, "
                                          "work authorization). One short file per topic, in your own words.\n")
    files["hub/programme.json"] = ('{\n "programme": "Job Search",\n "prefix": "JS",\n "ticket": "",\n "shipping_branch": "main",\n'
                                   ' "base": "origin/main",\n "author": "",\n "created": "%s",\n "hub": "hub",\n "lanes": []\n}\n' % today)
    files["hub/registry.json"] = '{\n "programme": "Job Search",\n "lanes": {},\n "updated_utc": "%sT00:00:00Z"\n}\n' % today
    files["hub/rulings.yaml"] = "rulings: []\n"
    files["hub/events.jsonl"] = ""
    for keep in ("hub/lanes", "hub/briefs", "state", "graph", "touches", "outbox", "pursuits", "jobs", "jobs-pending",
                 "companies", "applications", "prospects", "resumes/bases", "resumes/variants", "runtime/work/approvals",
                 "runtime/logs/runs"):
        files[f"{keep}/.gitkeep"] = ""
    files["runtime/vault/README.md"] = ("# vault/\n\nSecret *pointers* only: where a document or credential lives (a password manager item, "
                                        "a file path), never the value. This folder's contents are git-ignored.\n")
    files["legal/timers.md"] = f"""---
title: Work-authorization timers
type: legal-timers
owner: role:legal-visa
created: {today}
updated: {today}
---
Fill in only what applies to you (the visa-check skill reads this file): authorization type, end date, reporting
deadlines, unemployment-day ledger. Leave it as is if you need none of it. This is not legal advice.
"""
    return files


def template_files(vault_rel: str) -> dict[str, bytes]:
    """targets/ and views/ from templates/data; {{DATA_REL}}/ becomes the data folder's path inside the vault."""
    out: dict[str, bytes] = {}
    prefix = (vault_rel.strip("/") + "/") if vault_rel and vault_rel != "." else ""
    for src in sorted(TEMPLATES.rglob("*")):
        if src.is_file():
            data = src.read_bytes().replace(b"{{DATA_REL}}/", prefix.encode())
            data = re.sub(rb'waiting_on == "[a-z]+"', b'waiting_on == "owner"', data)   # identity.owner_token default
            out[str(src.relative_to(TEMPLATES))] = data.replace(b"{{DATA_REL}}", vault_rel.strip("/").encode() or b".")
    return out


def resume_template_files() -> dict[str, bytes]:
    """templates/resume -> resumes/templates (the house design, line widths, rulings, LaTeX fallback); the two
    general-base designs go next to the base. render_resume.py falls back to the repo copy when a file is missing."""
    out: dict[str, bytes] = {}
    for src in sorted(RESUME_TEMPLATES.rglob("*")):
        if src.is_file() and src.name != ".DS_Store":
            rel = src.relative_to(RESUME_TEMPLATES)
            if src.name in ("template-classic.yaml", "template-inline.yaml"):
                out[f"resumes/bases/general/{src.name}"] = src.read_bytes()
            else:
                out[f"resumes/templates/{rel}"] = src.read_bytes()
    return out


# ----------------------------------------------------------------------------- set-config
def set_config(data: Path) -> Path:
    """Write `data_root:` into the config file, keeping every other line."""
    cfg = config_file()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    lines = cfg.read_text(encoding="utf-8").splitlines() if cfg.is_file() else []
    new = f"data_root: {data}"
    for i, ln in enumerate(lines):
        if ln.split("#", 1)[0].startswith("data_root:"):
            lines[i] = new
            break
    else:
        lines.append(new)
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        cfg.chmod(0o600)
    except OSError:
        pass
    return cfg


# ----------------------------------------------------------------------------- main
def init_skeleton(root: Path, w: Writer, vault_rel: str) -> None:
    w.dirs(DIRS)
    for rel, text in skeleton_files().items():
        w.file(rel, text)
    for rel, data in template_files(vault_rel).items():
        w.file(rel, data)
    for rel, data in resume_template_files().items():
        w.file(rel, data)
    for section, values in _defaults_by_section().items():
        w.file(f"config/{section}.yaml", config_guide(section, values))


def init_demo(root: Path, w: Writer) -> None:
    if not DEMO.is_dir():
        sys.exit(f"init-data: demo data missing at {DEMO}")
    w.dirs(DIRS)
    for src in sorted(DEMO.rglob("*")):
        rel = src.relative_to(DEMO)
        if "__pycache__" in rel.parts or src.name == ".DS_Store":
            continue
        if src.is_file():
            w.file(str(rel), src.read_bytes())
    for rel, data in resume_template_files().items():
        w.file(rel, data)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("path", help="the data folder to create")
    ap.add_argument("--demo", action="store_true", help="fill it with the synthetic demo data")
    ap.add_argument("--set-config", action="store_true", help="write data_root into ~/.config/jaja/config.yaml")
    ap.add_argument("--vault-root", help="Obsidian vault root for views/*.base (default: the data folder)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    root = Path(a.path).expanduser().resolve()
    try:
        root.relative_to(CODE_ROOT)
        inside = True
    except ValueError:
        inside = False
    if inside and root != DEMO and DEMO not in root.parents:
        print("init-data: refusing to create a data folder inside the repo (your data must stay out of it)", file=sys.stderr)
        return 2
    if root.exists() and not root.is_dir():
        print(f"init-data: {root} exists and is not a folder", file=sys.stderr)
        return 2
    vault = Path(a.vault_root).expanduser().resolve() if a.vault_root else root
    try:
        vault_rel = str(root.relative_to(vault)) if root != vault else ""
    except ValueError:
        print("init-data: --vault-root must be the data folder or one of its parents", file=sys.stderr)
        return 2
    root.mkdir(parents=True, exist_ok=True)
    w = Writer(root, a.quiet)
    if a.demo:
        init_demo(root, w)
    else:
        init_skeleton(root, w, vault_rel)
    w.report()
    if a.set_config:
        cfg = set_config(root)
        if not a.quiet:
            print(f"config: data_root set to {root} in {cfg}")
    if not a.quiet:
        print(f"data folder ready: {root}" + ("  (demo data)" if a.demo else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
