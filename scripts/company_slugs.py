#!/usr/bin/env python3
"""company_slugs.py - keep every stored company_slug on the one canonical slug (company_index.py).

  migrate [--dry-run | --apply]   rewrite `company_slug:` in jobs/, jobs-pending/, applications/*/application.md and
                      prospects/ (and a prospect's `company_slug_source:`) to the canonical slug. Dry run by
                      default: prints every change and writes nothing. Only those lines change; every other
                      byte stays, and ids and folder names never move (an id is an identity, not a join key).
  check               exit 1 when a record drifts from the canonical slug, or when a watchlist entry
                      (targets/companies.yaml) names a dossier's company under another slug: the scouts
                      stamp the watchlist slug, the joins use the dossier's, and the records split again.
                      The fix for a conflict is to give the watchlist entry the dossier's slug.

The canonical slug of a record: its stored slug resolved against the index; when that only derives, its
`company` name resolved (the name may reach a dossier or watchlist entry the old slug does not); when both
only derive, the name's derived slug (the one jobs_manual.py and prospects.py now write).

    python3 scripts/company_slugs.py migrate --dry-run       # the diff (also the default)
    python3 scripts/company_slugs.py migrate --apply
    python3 scripts/company_slugs.py check
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))
from company_index import CompanyIndex, derive  # noqa: E402
from js_common import JS_ROOT, fm  # noqa: E402

FRONT = re.compile(r"\A(---\n.*?\n)(---\n)", re.S)


def record_files(root: Path) -> Iterator[Path]:
    for folder in ("jobs", "jobs-pending", "prospects"):
        yield from sorted((root / folder).glob("*.md")) if (root / folder).is_dir() else ()
    yield from sorted((root / "applications").glob("*/application.md")) if (root / "applications").is_dir() else ()


def canonical_for(idx: CompanyIndex, fields: dict[str, Any]) -> tuple[str | None, str | None]:
    stored, name = fields.get("company_slug"), fields.get("company")
    stored = str(stored) if stored else None
    name = str(name) if name else None
    if stored:
        slug, source = idx.resolve(stored)
        if source != "derived":
            return slug, source
    if name:
        slug, source = idx.resolve(name)
        return slug, source
    return (derive(stored), "derived") if stored else (None, None)


def plan(root: Path) -> list[tuple[Path, str, str, str]]:
    idx = CompanyIndex.load(root)
    out = []
    for path in record_files(root):
        fields, _ = fm.load(path)
        if not fields.get("company_slug"):
            continue
        new, source = canonical_for(idx, fields)
        if new and new != str(fields["company_slug"]):
            out.append((path, str(fields["company_slug"]), new, source or "derived"))
    return out


def rewrite(path: Path, new: str, source: str) -> None:
    text = path.read_text(encoding="utf-8")
    m = FRONT.match(text)
    if not m:
        raise ValueError(f"{path}: no frontmatter")
    head = re.sub(r"^company_slug:.*$", f"company_slug: {new}", m.group(1), count=1, flags=re.M)
    head = re.sub(r"^company_slug_source:.*$", f"company_slug_source: {source}", head, count=1, flags=re.M)
    path.write_text(head + m.group(2) + text[m.end():], encoding="utf-8")


def conflicts(root: Path) -> list[str]:
    idx = CompanyIndex.load(root)
    dossiers = CompanyIndex(idx_entries_as_companies(idx.groups[0]))
    out = []
    for e in idx.groups[1]:
        for value in [e["slug"], *e["names"]]:
            slug, source = dossiers.resolve(value)
            if source == "dossier" and slug != e["slug"]:
                out.append(f"conflict: watchlist slug {e['slug']} names the dossier companies/{slug}.md (slug {slug}); "
                           f"give the watchlist entry slug {slug}")
                break
    return out


def idx_entries_as_companies(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"slug": e["slug"], "name": e["names"][0] if e["names"] else e["slug"], "aliases": e["names"][1:],
             "domains": sorted(e["domains"])} for e in entries]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("migrate", help="rewrite stored company_slug fields (dry run unless --apply)")
    m.add_argument("--root", type=Path, default=JS_ROOT)
    mode = m.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="the default: print the diff, write nothing")
    mode.add_argument("--apply", action="store_true")
    c = sub.add_parser("check", help="exit 1 on drift or a dossier/watchlist slug conflict")
    c.add_argument("--root", type=Path, default=JS_ROOT)
    args = ap.parse_args(argv)
    root = Path(args.root)
    changes = plan(root)
    if args.cmd == "migrate":
        for path, old, new, source in changes:
            print(f"{path.relative_to(root).as_posix()}: company_slug {old} -> {new} ({source})")
            if args.apply:
                rewrite(path, new, source)
        print(f"{len(changes)} records changed" if args.apply
              else f"dry run: {len(changes)} records would change; nothing written")
        return 0
    problems = conflicts(root)
    if changes:
        problems.insert(0, f"drift: {len(changes)} records off the canonical slug "
                           "(python3 scripts/company_slugs.py migrate shows them)")
    for line in problems:
        print(line)
    if not problems:
        print(f"ok: every stored company_slug is canonical; no dossier/watchlist conflict "
              f"({len(list(record_files(root)))} records read)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
