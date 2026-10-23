#!/usr/bin/env python3
"""dash_tags.py: the single writer of state/dashboard-tags.yaml, the dashboard's own tags.

Applications and jobs have no sanctioned tag field (schemas/application.yaml has none; a job's `tags:` are
machine flags from the controlled vocabulary in schemas/tags.yaml). the owner's research tags therefore live in this one state file, which only this script writes. Prospect
tags are different: they go through `scripts/prospects.py set --tag` (the vocabulary applies), never here.

A company's segment is not kept here: the owner's pick is written into targets/companies.yaml by
`scripts/segment_companies.py set <company> <segment> --source owner` (one source). load() still
returns an empty `company_segments` so older readers keep working.

    python3 dashboard/dash_tags.py [--root JS] add <application|job> <id> <tag>
    python3 dashboard/dash_tags.py [--root JS] remove <application|job> <id> <tag>
    python3 dashboard/dash_tags.py [--root JS] show

A tag is lowercase kebab-case, 1 to 40 characters. The id must exist (applications/<id>/ or jobs/<id>.md). Every change
appends one line to state/dashboard-tags.log.jsonl (append-only history). Exit 0 done, 2 refused (nothing written).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from agency_paths import DATA_ROOT as JS_ROOT  # noqa: E402
TAGS_FILE = Path("state") / "dashboard-tags.yaml"
LOG_FILE = Path("state") / "dashboard-tags.log.jsonl"
KINDS = ("application", "job")
SEGMENTS = ("startup", "growth", "big-tech", "ai-lab", "enterprise")   # growth Q11
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,160}$")
HEADER = ("# Written only by dashboard/dash_tags.py (the job agency dashboard). Do not edit by hand.\n"
          "# tags: research tags on applications and jobs (no sanctioned tag field exists on those records).\n")


class Refused(ValueError):
    """The change was refused; nothing was written."""


def load(root: Path) -> dict[str, Any]:
    """The current file as {"tags": {kind: {id: [tag, ...]}}, "company_segments": {}} (absent -> empty; segments now
    live in targets/companies.yaml, so company_segments is always empty)."""
    path = Path(root) / TAGS_FILE
    data: dict[str, Any] = {}
    if path.is_file():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    tags = data.get("tags") or {}
    return {"tags": {k: {str(i): [str(t) for t in (v or [])] for i, v in (tags.get(k) or {}).items()} for k in KINDS},
            "company_segments": {}}


def _save(root: Path, data: dict[str, Any]) -> Path:
    path = Path(root) / TAGS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {"tags": {k: {i: sorted(set(v)) for i, v in sorted(data["tags"].get(k, {}).items()) if v} for k in KINDS}}
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-tags-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(HEADER + yaml.safe_dump(clean, sort_keys=False, allow_unicode=True))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return path


def _log(root: Path, row: dict[str, Any]) -> None:
    path = Path(root) / LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"), **row}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _check_target(root: Path, kind: str, ident: str) -> None:
    if kind not in KINDS:
        raise Refused(f"kind must be one of {', '.join(KINDS)}")
    if not ID_RE.match(ident or ""):
        raise Refused(f"not a record id: {ident!r}")
    exists = (Path(root) / "applications" / ident).is_dir() if kind == "application" \
        else (Path(root) / "jobs" / f"{ident}.md").is_file()
    if not exists:
        where = f"applications/{ident}/" if kind == "application" else f"jobs/{ident}.md"
        raise Refused(f"no record at {where}")


def _check_tag(tag: str) -> str:
    tag = str(tag or "").strip().lower()
    if not TAG_RE.match(tag):
        raise Refused("a tag is lowercase kebab-case, 1 to 40 characters (letters, digits, hyphens)")
    return tag


def add(root: Path, kind: str, ident: str, tag: str) -> list[str]:
    _check_target(root, kind, ident)
    tag = _check_tag(tag)
    data = load(root)
    have = data["tags"][kind].setdefault(ident, [])
    if tag not in have:
        have.append(tag)
        _save(root, data)
        _log(root, {"op": "add", "kind": kind, "id": ident, "tag": tag})
    return sorted(set(have))


def remove(root: Path, kind: str, ident: str, tag: str) -> list[str]:
    if kind not in KINDS:
        raise Refused(f"kind must be one of {', '.join(KINDS)}")
    tag = _check_tag(tag)
    data = load(root)
    have = data["tags"][kind].get(ident) or []
    if tag not in have:
        raise Refused(f"{kind} {ident} has no tag {tag!r}")
    data["tags"][kind][ident] = [t for t in have if t != tag]
    _save(root, data)
    _log(root, {"op": "remove", "kind": kind, "id": ident, "tag": tag})
    return sorted(data["tags"][kind][ident])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("add", "remove"):
        p = sub.add_parser(name)
        p.add_argument("kind", choices=KINDS)
        p.add_argument("id")
        p.add_argument("tag")
    sub.add_parser("show")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "add":
            print(f"{args.kind} {args.id}: tags {', '.join(add(args.root, args.kind, args.id, args.tag))}")
        elif args.cmd == "remove":
            left = remove(args.root, args.kind, args.id, args.tag)
            print(f"{args.kind} {args.id}: tags {', '.join(left) or 'none'}")
        else:
            print(json.dumps(load(args.root), indent=2))
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
