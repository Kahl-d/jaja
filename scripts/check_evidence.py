#!/usr/bin/env python3
"""check_evidence.py - validate evidence references across profile/.

Every evidence id cited anywhere in profile/ (facts.yaml, skills-taxonomy.yaml, corpus/**/*.md,
projects-index.md, VOICE.md, CORPUS-STATUS.md, README.md, LESSONS.md) must exist in evidence.yaml.
Ids are tokens like `ev-acme-003` or `ev-resume-2026-05-26-011`, cited bare, in frontmatter
`evidence:` lists, in parentheses, or as `[ev: ev-...]`. Also checks that every corpus README
has the required frontmatter keys. In facts.yaml it also checks the render titles (every titled entry that can
render has a `title_render` free of held text from PROFILE-HOLDS.md and of conflict markers; see
scripts/profile_titles.py) and the corpus links (every `corpus:` path exists under corpus/<dir>/). The records the
corpus-keeper writes (agents/corpus-keeper.md) are checked too: a facts.yaml conflict is awaiting the owner or
`ruled`, and a ruled one (or a ruled rule line) carries `ruled: {by: <owner tag>, "on": YYYY-MM-DD, basis, ruling}`
(quote "on": bare `on` is a YAML 1.1 boolean); a `condition_met:` block (the owner met a ruling's condition) is
checked the same way with the owner's `words`, and each `partial_rulings:` entry with its `ruling` and `item`; every
evidence.yaml entry has source, locator, captured_at and note, an optional `type` from EVIDENCE_TYPES, and an
owner-statement cites an existing intake note under profile/_inbox/intake/; `count` equals the number of entries.
Lens names in a corpus README are checked against policy.lenses when that list is set. Exit code = number of problems (0 = clean).

Usage: python3 check_evidence.py [--unused]
"""
from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

import yaml

import agency_config as agency_cfg
from agency_paths import DATA_ROOT, SCRIPTS_DIR  # noqa: E402
HERE = DATA_ROOT / "profile"                       # the profile records live in the data folder
sys.path.insert(0, str(SCRIPTS_DIR))
import profile_titles as pt  # noqa: E402
ID_RE = re.compile(r"\bev-[a-z]+(?:-[a-z0-9]+)*-\d{3}\b")
REQUIRED = ["id", "kind", "org", "title", "dates", "location", "status", "discretion", "lenses", "evidence"]
KINDS = {"experience", "project", "education", "award", "publication", "teaching"}
STATUS = {"confirmed", "needs-confirmation"}
DISCRETION = {"public", "resume-ok", "internal-only"}
LENSES = {str(x) for x in (agency_cfg.get("policy.lenses", []) or [])}     # empty: lens names are not checked
# Data-file vocabulary values shared with the owner's records (renamed centrally with a data migration).
OWNER_TAG = "owner"                    # `ruled.by` value: only the owner rules
AWAITING = "awaiting-owner"            # a conflict status
STATEMENT_TYPE = "owner-statement"     # an evidence type: the owner's own words in an intake note
CONFLICT_STATUS = {AWAITING, "ruled"}
EVIDENCE_TYPES = {"file", "commit", "url", "document", STATEMENT_TYPE}
EVIDENCE_REQUIRED = ("source", "locator", "captured_at", "note")
INTAKE_DIR = "profile/_inbox/intake/"          # the owner's dated words (add-experience intake notes)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def frontmatter(text: str) -> dict:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    try:
        return yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as exc:
        return {"_error": str(exc).splitlines()[0]}


def check_facts(facts: dict, held: list[str], profile_dir: Path = HERE) -> list[str]:
    """facts.yaml render titles and corpus links; one line per problem."""
    problems: list[str] = []
    for path, entry in pt.iter_titled(facts):
        if "title_render" in entry:
            for why in pt.title_problems(entry["title_render"], held):
                problems.append(f"facts.yaml {path}.title_render: {why}")
        elif str(entry.get("discretion") or "") != "internal-only":
            problems.append(f"facts.yaml {path}: has a `title` but no `title_render` (renderers refuse the record)")
    for path, rel in pt.iter_corpus_links(facts):
        parts = Path(rel).parts
        target = profile_dir / rel
        if len(parts) < 2 or parts[0] != "corpus" or not (profile_dir / parts[0] / parts[1]).is_dir() \
                or not target.exists():
            problems.append(f"facts.yaml {path}: {rel} does not resolve to an existing corpus directory and file")
    return problems


def _is_date(value) -> bool:
    return isinstance(value, dt.date) or bool(DATE_RE.match(str(value or "")))


def _ruled_problems(where: str, block, need_ruling: bool, key: str = "ruled",
                    need: tuple[str, ...] = ()) -> list[str]:
    """Problems with one `ruled:`-shaped block (`key` names it: ruled, condition_met, partial_rulings[i]):
    only the owner rules, on a date, with a basis (and the ruling text; `need` adds fields such as words, item)."""
    if not isinstance(block, dict):
        return [f"{where}: `{key}:` must be a mapping {{by: {OWNER_TAG}, \"on\": YYYY-MM-DD, basis, ...}}"]
    out: list[str] = []
    if any(isinstance(k, bool) for k in block):
        out.append(f'{where}: `{key}:` has a boolean key: quote "on" (bare on is true in YAML 1.1)')
    if str(block.get("by") or "").strip().lower() != OWNER_TAG:
        out.append(f"{where}: {key}.by is {block.get('by')!r}: only the owner rules (the keeper records)")
    if "on" in block and not _is_date(block.get("on")):
        out.append(f"{where}: {key}.on {block.get('on')!r} is not a date (YYYY-MM-DD)")
    elif "on" not in block and not any(isinstance(k, bool) for k in block):
        out.append(f"{where}: {key}.on is missing")
    if not str(block.get("basis") or "").strip():
        out.append(f"{where}: {key}.basis is empty (where the owner ruled: session, chat, intake note)")
    if need_ruling and not str(block.get("ruling") or "").strip():
        out.append(f"{where}: {key}.ruling is empty (the owner's ruling, in words)")
    for field in need:
        if not str(block.get(field) or "").strip():
            out.append(f"{where}: {key}.{field} is empty")
    return out


def condition_met_problems(where: str, block) -> list[str]:
    """A `condition_met:` block: the owner met a ruling's condition (its `ruled.note`) in their own words."""
    return _ruled_problems(where, block, need_ruling=False, key="condition_met", need=("words",))


def check_conflicts(facts: dict) -> list[str]:
    """facts.yaml `conflicts:` (and ruled `rules:` lines): statuses, ruling blocks, unique ids."""
    problems: list[str] = []
    seen: set[str] = set()
    for i, c in enumerate(facts.get("conflicts") or []):
        if not isinstance(c, dict):
            problems.append(f"facts.yaml conflicts[{i}]: not a mapping")
            continue
        cid = str(c.get("id") or "").strip()
        where = f"facts.yaml conflicts[{i}] ({cid or '?'})"
        if not cid:
            problems.append(f"facts.yaml conflicts[{i}]: missing id")
        elif cid in seen:
            problems.append(f"facts.yaml conflicts: duplicate id {cid}")
        seen.add(cid)
        status = str(c.get("status") or "")
        if status not in CONFLICT_STATUS:
            problems.append(f"{where}: status {status!r} is not one of {sorted(CONFLICT_STATUS)}")
        elif status == "ruled":
            if "ruled" not in c:
                problems.append(f'{where}: status ruled needs a `ruled:` block {{by: {OWNER_TAG}, "on": YYYY-MM-DD, basis, ruling}}')
            else:
                problems += _ruled_problems(where, c["ruled"], need_ruling=True)
        elif "ruled" in c:
            problems.append(f"{where}: status {AWAITING} but carries a `ruled:` block (set status: ruled, or remove it)")
        if "condition_met" in c:
            if status != "ruled":
                problems.append(f"{where}: carries `condition_met:` but is not ruled (a condition belongs to a ruling)")
            problems += condition_met_problems(where, c["condition_met"])
        if "partial_rulings" in c:
            partial = c["partial_rulings"]
            if not isinstance(partial, list):
                problems.append(f"{where}: `partial_rulings:` must be a list of ruled blocks")
            else:
                for j, block in enumerate(partial):
                    problems += _ruled_problems(f"{where} partial_rulings[{j}]", block, need_ruling=True,
                                                key="partial_rulings", need=("item",))
    for i, r in enumerate(facts.get("rules") or []):
        if isinstance(r, dict) and "ruled" in r:
            problems += _ruled_problems(f"facts.yaml rules[{i}]", r["ruled"], need_ruling=False)
    return problems


def check_registry(data: dict, js_root: Path = DATA_ROOT) -> list[str]:
    """evidence.yaml entries: required fields, type and discretion enums, owner-statement sources, count."""
    problems: list[str] = []
    data = data or {}
    entries = data.get("evidence") or {}
    if data.get("count") != len(entries):
        problems.append(f"evidence.yaml: count is {data.get('count')} but {len(entries)} entries are registered")
    for eid, e in entries.items():
        if not isinstance(e, dict):
            problems.append(f"evidence.yaml {eid}: not a mapping")
            continue
        for k in EVIDENCE_REQUIRED:
            if not str(e.get(k) or "").strip():
                problems.append(f"evidence.yaml {eid}: {k} is empty")
        if e.get("captured_at") and not _is_date(e.get("captured_at")):
            problems.append(f"evidence.yaml {eid}: captured_at {e.get('captured_at')!r} is not a date (YYYY-MM-DD)")
        if "discretion" in e and e.get("discretion") not in DISCRETION:
            problems.append(f"evidence.yaml {eid}: discretion {e.get('discretion')!r} is not one of {sorted(DISCRETION)}")
        etype = e.get("type")
        if etype is not None and etype not in EVIDENCE_TYPES:
            problems.append(f"evidence.yaml {eid}: type {etype!r} is not one of {sorted(EVIDENCE_TYPES)}")
        if etype == STATEMENT_TYPE:
            src = str(e.get("source") or "").strip()
            if not src.startswith(INTAKE_DIR):
                problems.append(f"evidence.yaml {eid}: a {STATEMENT_TYPE} cites an intake note under {INTAKE_DIR} (got {src!r})")
            elif not (js_root / src).is_file():
                problems.append(f"evidence.yaml {eid}: intake note {src} does not exist")
    return problems


def main() -> int:
    registry = yaml.safe_load((HERE / "evidence.yaml").read_text())["evidence"]
    problems: list[str] = []
    cited: set[str] = set()
    files = [p for p in HERE.rglob("*") if p.is_file() and p.suffix in {".md", ".yaml"}
             and "_inbox" not in p.parts and p.name != "evidence.yaml"]
    for path in sorted(files):
        text = path.read_text(encoding="utf-8")
        ids = set(ID_RE.findall(text))
        cited |= ids
        for i in sorted(ids - set(registry)):
            problems.append(f"{path.relative_to(HERE)}: unknown evidence id {i}")
        if path.parent.parent.name == "corpus":
            if path.suffix != ".md":
                continue
            meta = frontmatter(text)
            if "_error" in meta:
                problems.append(f"{path.relative_to(HERE)}: frontmatter does not parse ({meta['_error']})")
                continue
            for k in REQUIRED:
                if k not in meta:
                    problems.append(f"{path.relative_to(HERE)}: frontmatter missing '{k}'")
            if meta.get("kind") not in KINDS:
                problems.append(f"{path.relative_to(HERE)}: kind '{meta.get('kind')}' not in {sorted(KINDS)}")
            if meta.get("status") not in STATUS:
                problems.append(f"{path.relative_to(HERE)}: status '{meta.get('status')}' invalid")
            if meta.get("discretion") not in DISCRETION:
                problems.append(f"{path.relative_to(HERE)}: discretion '{meta.get('discretion')}' invalid")
            for lens in meta.get("lenses") or []:
                if LENSES and lens not in LENSES:
                    problems.append(f"{path.relative_to(HERE)}: unknown lens '{lens}'")
            if agency_cfg.get("style.no_em_dash", False) and "\u2014" in text:
                problems.append(f"{path.relative_to(HERE)}: contains an em-dash")
    facts = yaml.safe_load((HERE / "facts.yaml").read_text(encoding="utf-8")) or {}
    problems += check_facts(facts, pt.load_held(HERE / "PROFILE-HOLDS.md"))
    problems += check_conflicts(facts)
    problems += check_registry(yaml.safe_load((HERE / "evidence.yaml").read_text(encoding="utf-8")) or {})
    for p in problems:
        print(p)
    if "--unused" in sys.argv:
        for i in sorted(set(registry) - cited):
            print(f"unused: {i}")
    print(f"check_evidence: {len(registry)} registry ids, {len(cited)} cited, {len(problems)} problem(s)")
    return len(problems)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
