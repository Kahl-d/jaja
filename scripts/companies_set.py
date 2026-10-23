#!/usr/bin/env python3
"""companies_set.py - the one setter for the watchlist fields scripts discover (targets/companies.yaml).

Scripts that find a company's facts never write companies.yaml themselves: they append a proposal to
state/proposals/companies.jsonl and this applies it, one field at a time, after a person or the owning lane reads it.

    python3 scripts/companies_set.py set <slug> <field> <value> --source WHO [--dry-run]
    python3 scripts/companies_set.py unset <slug> <field> --source WHO [--dry-run]
    python3 scripts/companies_set.py proposals
    python3 scripts/companies_set.py apply --source WHO [--write]

`set` takes only FIELDS, each validated (github_org: a GitHub login; ats_vendor: VENDORS; board_token: one token, no
spaces; careers_url and feeds: http(s); oracle_location and oracle_exclude_location: a regex on fetch_oracle's location).
`feeds` is a list: each `set` appends one URL once. `unset` removes a field and its continuation lines. It edits the entry's lines in place (every other line, comment and order kept), re-reads
once if another process touched the file meanwhile, checks the entry count is unchanged, and prints the field's before
and after; the same value is "no change". `proposals` lists the proposals whose value is not in companies.yaml yet,
with the command that applies each; `apply` sets every pending proposal of one source (a dry run unless --write).
Nothing outward; no tier, segment or name is set here (their owners do that).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from js_common import JS_ROOT  # noqa: E402

FIELDS = ("github_org", "ats_vendor", "board_token", "careers_url", "feeds", "oracle_location", "oracle_exclude_location")
LIST_FIELDS = ("feeds",)                 # appended one value at a time; written as one flow-style line
# fetch_ats's vendors, the vendors ats_detect.py names, and `manual`
VENDORS = ("greenhouse", "lever", "ashby", "workday", "jibe", "sitemap", "amazon", "workable", "smartrecruiters",
           "rippling", "gem", "pinpoint", "teamtailor", "recruitee", "icims", "taleo", "oracle", "successfactors",
           "eightfold", "phenom", "avature", "manual", "other", "unknown")
PROPOSALS = Path("state") / "proposals" / "companies.jsonl"


class Refused(ValueError):
    """A field or value the setter will not write."""


def validate(field: str, value: str) -> str:
    value = str(value).strip()
    if field not in FIELDS:
        raise Refused(f"field {field!r} is not set here (only {', '.join(FIELDS)})")
    def regex_ok(v: str) -> bool:
        try:
            re.compile(v)
            return 0 < len(v) <= 200
        except re.error:
            return False
    ok = {"github_org": re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", value),
          "ats_vendor": value in VENDORS,
          "board_token": re.fullmatch(r"\S{1,300}", value),
          "careers_url": re.fullmatch(r"https?://\S+", value),
          "feeds": re.fullmatch(r"https?://\S+", value),
          "oracle_location": regex_ok(value), "oracle_exclude_location": regex_ok(value)}[field]
    if not ok:
        hint = f" (one of {', '.join(VENDORS)})" if field == "ats_vendor" else ""
        raise Refused(f"{field} {value!r} is not a valid value{hint}")
    return value


def _continuation(entry: list[str], at: int) -> int:
    """The index after a field line and its indented continuation (a nested list or mapping under it)."""
    end = at + 1
    while end < len(entry) and (entry[end].startswith("      ") or entry[end].startswith("    - ")):
        end += 1
    return end


def set_in_text(text: str, slug: str, field: str, value: Any, remove: bool = False) -> tuple[str, Any]:
    """(new text, the old value or None): `field: value` in the company entry whose slug is `slug`, replacing its line
    or inserted after the entry's last line among slug, ats_vendor, board_token, careers_url."""
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        if not re.match(r"^  - name:", lines[i]):
            i += 1
            continue
        j = i + 1
        while j < len(lines) and not re.match(r"^  - |^\S|^  #", lines[j]):
            j += 1
        entry = lines[i:j]
        slug_line = next((b for b in entry if re.match(r"^    slug:", b)), "")
        if (slug_line.split(":", 1)[1].strip().strip("\"'") if slug_line else "") == slug:
            flow = isinstance(value, list)
            dumped = yaml.safe_dump(value, default_flow_style=flow or None, width=10000).strip().removesuffix("\n...")
            line = f"    {field}: {dumped}\n"
            at = next((k for k, b in enumerate(entry) if re.match(rf"^    {field}:", b)), None)
            if at is not None:
                end = _continuation(entry, at)
                old = (yaml.safe_load("".join(entry[at:end])) or {}).get(field)
                entry[at:end] = [] if remove else [line]
            elif remove:
                old = None
            else:
                old = None
                anchors = [k for k, b in enumerate(entry) if re.match(r"^    (slug|ats_vendor|board_token|careers_url):", b)]
                entry.insert((anchors[-1] if anchors else 0) + 1, line)
            return "".join(lines[:i] + entry + lines[j:]), old
        i = j
    raise Refused(f"no company with slug {slug!r} in targets/companies.yaml")


def _current(text: str, slug: str) -> dict[str, Any]:
    return next((c for c in (yaml.safe_load(text) or {}).get("companies") or [] if str(c.get("slug")) == slug), {})


def set_field(root: Path, slug: str, field: str, value: str | None, dry_run: bool = False,
              remove: bool = False) -> tuple[Any, Any]:
    """(old value, new value): validated, written atomically unless dry_run. A list field gains `value` (once);
    remove=True deletes the field."""
    if remove:
        if field not in FIELDS:
            raise Refused(f"field {field!r} is not set here (only {', '.join(FIELDS)})")
    else:
        value = validate(field, str(value))
    path = Path(root) / "targets" / "companies.yaml"
    for _ in range(2):
        mtime = path.stat().st_mtime_ns
        old_text = path.read_text(encoding="utf-8")
        new_value: Any = value
        if field in LIST_FIELDS and not remove:
            have = list(_current(old_text, slug).get(field) or [])
            new_value = have + ([value] if value not in have else [])
        new_text, old = set_in_text(old_text, slug, field, new_value, remove=remove)
        value = new_value
        count = lambda t: len((yaml.safe_load(t) or {}).get("companies") or [])  # noqa: E731
        if count(new_text) != count(old_text):
            raise Refused(f"{path}: the entry count changed; nothing written")
        if dry_run or new_text == old_text:
            return old, value
        if path.stat().st_mtime_ns != mtime:                 # another process meanwhile: re-read once
            continue
        tmp = path.with_name("." + path.name + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, path)
        return old, value
    raise Refused(f"{path} kept changing under another process; nothing written, run again")


def pending(root: Path) -> list[dict[str, Any]]:
    path = Path(root) / PROPOSALS
    if not path.is_file():
        return []
    current = {str(c.get("slug")): c for c in (yaml.safe_load((Path(root) / "targets" / "companies.yaml")
                                                              .read_text(encoding="utf-8")) or {}).get("companies") or []}
    out, seen = [], set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        key = (row.get("slug"), row.get("field"), row.get("value"))
        have = (current.get(str(row.get("slug"))) or {}).get(row.get("field"))
        applied = row.get("value") in (have or []) if row.get("field") in LIST_FIELDS else str(have) == str(row.get("value"))
        if key in seen or applied:
            continue
        seen.add(key)
        out.append(row)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set", help="set one watchlist field")
    s.add_argument("slug")
    s.add_argument("field", choices=FIELDS)
    s.add_argument("value")
    s.add_argument("--source", required=True, help="who found it (a script, a lane, the owner)")
    s.add_argument("--root", type=Path, default=JS_ROOT)
    s.add_argument("--dry-run", action="store_true")
    u = sub.add_parser("unset", help="remove one watchlist field")
    u.add_argument("slug")
    u.add_argument("field", choices=FIELDS)
    u.add_argument("--source", required=True)
    u.add_argument("--root", type=Path, default=JS_ROOT)
    u.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("proposals", help="list proposals not applied yet")
    p.add_argument("--root", type=Path, default=JS_ROOT)
    ap_ = sub.add_parser("apply", help="set every pending proposal of one source (dry run unless --write)")
    ap_.add_argument("--source", required=True)
    ap_.add_argument("--root", type=Path, default=JS_ROOT)
    ap_.add_argument("--write", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    if a.cmd == "apply":
        rows = [r for r in pending(a.root) if r.get("source") == a.source]
        done = 0
        for r in rows:
            try:
                old, value = set_field(a.root, str(r["slug"]), str(r["field"]), str(r["value"]), dry_run=not a.write)
            except Refused as exc:
                print(f"  refused {r.get('slug')} {r.get('field')}: {exc}")
                continue
            done += 1
            print(f"  {r['slug']} {r['field']}: {old} -> {value}")
        print(f"{'' if a.write else 'dry-run (pass --write to save): '}{done} of {len(rows)} proposal(s) from "
              f"{a.source} {'set' if a.write else 'would be set'}")
        return 0
    if a.cmd == "proposals":
        rows = pending(a.root)
        for r in rows:
            print(f"{r.get('date')} {r.get('slug')} {r.get('field')} {r.get('value')} (from {r.get('source')}): "
                  f"{r.get('apply')}")
        print(f"{len(rows)} proposal(s) not applied")
        return 0
    if a.cmd == "unset":
        try:
            old, _ = set_field(a.root, a.slug, a.field, None, dry_run=a.dry_run, remove=True)
        except Refused as exc:
            print(f"refused: {exc}")
            return 2
        print(f"no change: {a.slug} has no {a.field}" if old is None else
              f"{'dry-run: ' if a.dry_run else ''}unset {a.slug} (source {a.source})\n  {a.field}: {old} -> (removed)")
        return 0
    try:
        old, value = set_field(a.root, a.slug, a.field, a.value, dry_run=a.dry_run)
    except Refused as exc:
        print(f"refused: {exc}")
        return 2
    if old == value or str(old) == str(value):
        print(f"no change: {a.slug} {a.field} is already {value}")
    else:
        print(f"{'dry-run: ' if a.dry_run else ''}set {a.slug} (source {a.source})\n  {a.field}: {old} -> {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
