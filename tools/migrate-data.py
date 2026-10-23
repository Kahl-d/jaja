#!/usr/bin/env python3
"""Migrate an existing data folder to this repo's neutral data vocabulary.

Earlier versions of the agency wrote the owner's first name into a few data values (awaiting-<name>, <name>-statement,
needs_<name>, <name>_decision, `by: <name>` ...). The code now uses `owner`. Run this once on your data folder:

    python3 tools/migrate-data.py --data /path/to/data --old-token yourfirstname            # dry run: counts only
    python3 tools/migrate-data.py --data /path/to/data --old-token yourfirstname --apply    # rewrite in place

Only the exact vocabulary tokens are rewritten, never free text. Write a backup first (copy the folder).
Text files only (.md .yaml .yml .json .jsonl .csv .txt .base); files over 5 MB and binary files are skipped.
Idempotent: a second run changes nothing.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

TEXT_EXT = {".md", ".yaml", ".yml", ".json", ".jsonl", ".csv", ".txt", ".base"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "scripts", "mcp", "dashboard"}


def rules(old: str):
    o = re.escape(old)
    cap = re.escape(old[:1].upper() + old[1:])
    pairs = [
        (rf"awaiting-{o}\b", "awaiting-owner"), (rf"\b{o}-statement\b", "owner-statement"),
        (rf"needs_{o}\b", "needs_owner"), (rf"\b{o}_decision\b", "owner_decision"), (rf"\b{o}_once\b", "owner_once"),
        (rf"{cap}-confirmed", "owner-confirmed"), (rf"waits_{o}\b", "waits_owner"),
        (rf"\bby: {o}\b", "by: owner"), (rf"\bby: {cap}\b", "by: owner"), (rf'"by": "{o}"', '"by": "owner"'),
        (rf"\bwaits_on: {o}\b", "waits_on: owner"), (rf'"waits_on": "{o}"', '"waits_on": "owner"'),
        (rf"\bwaiting_on: {o}\b", "waiting_on: owner"), (rf'"waiting_on": "{o}"', '"waiting_on": "owner"'),
        (rf"\bdecision: {o}\b", "decision: owner"), (rf'"decision": "{o}"', '"decision": "owner"'),
        (rf"\bsegment_source: {o}\b", "segment_source: owner"), (rf"\bfirst_draft: {o}\b", "first_draft: owner"),
        (rf"\bowner: {o}\b", "owner: owner"), (rf"\b{o}-clicks-", "owner-clicks-"), (rf"\b{o}-setup-list", "owner-setup-list"),
        (rf"\b{o}_s_", "owner_s_"),
    ]
    return [(re.compile(a), b) for a, b in pairs]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--old-token", required=True, help="the lowercase first name that earlier versions wrote into data values")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    root = Path(a.data).expanduser()
    rs = rules(a.old_token)
    files = changed = hits = 0
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix not in TEXT_EXT or any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        try:
            if p.stat().st_size > 5_000_000:
                continue
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        files += 1
        new, n = text, 0
        for rx, rep in rs:
            new, k = rx.subn(rep, new)
            n += k
        if n:
            changed += 1
            hits += n
            if a.apply:
                p.write_text(new, encoding="utf-8")
    renamed = 0
    old = a.old_token
    for p in sorted(root.rglob(f"{old}-*")):                      # file names that carry the old token, e.g. <old>-setup-list.md
        if p.is_file() and not any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            target = p.with_name("owner-" + p.name[len(old) + 1:])
            if not target.exists():
                renamed += 1
                if a.apply:
                    p.rename(target)
    print(f"{'rewrote' if a.apply else 'would rewrite'} {hits} tokens in {changed} of {files} text files under {root}; "
          f"{'renamed' if a.apply else 'would rename'} {renamed} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
