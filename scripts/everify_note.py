#!/usr/bin/env python3
"""Record the owner's manual E-Verify Employer Search lookup in legal/checks/<company>.md, in one command.

The public E-Verify Employer Search (https://www.e-verify.gov/about-e-verify/e-verify-data/e-verify-employer-search)
is an interactive Tableau view with no queryable export, so no script reads it and
nothing here scrapes it. The owner looks the employer up in a browser; this writes what was found where
legal-visa's check and the apply gate (`scripts/apply_pack.py` visa gate) read it:

  frontmatter  e_verify         enrolled -> "yes"; not-found -> unknown; unknown -> unknown
               e_verify_source  "E-Verify Employer Search, looked up by the owner <date>: <status> (<source>)"
               (the other keys: company, legal_entity, stance, cap_exempt, remote_ok_for_i983, employment_type,
               verdict, checked_at are left as they are; see roles/legal-visa.md "Output contract")
  body         one appended line under "## E-Verify lookups" (append-only: earlier lookups stay)

A not-found result is not "no": a company that is not listed may be enrolled under another name, or not
enrolled, so e_verify stays unknown and the source says so; only legal-visa (or the employer in writing)
turns a lookup into `no`. The verdict is never changed here: when the E-Verify value changes, the script
says legal-visa must re-run `visa-check company <slug>`. With no check file yet it writes a stub with
`verdict: pending` (the apply gate stays closed until legal-visa runs the check). Information only, not
legal advice.

CLI:  python everify_note.py [--root R] record <company or slug> --status enrolled|not-found|unknown
                             --source "<how the owner checked>" [--date YYYY-MM-DD] [--dry-run]
      python everify_note.py [--root R] show <company or slug>
Exit: 0 recorded (or shown); 1 bad input or an unreadable check file.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from js_common import JS_ROOT, append_section_line, cli_date, fm, slugify  # noqa: E402

SEARCH_URL = "https://www.e-verify.gov/about-e-verify/e-verify-data/e-verify-employer-search"
STATUSES = {"enrolled": "yes", "not-found": "unknown", "unknown": "unknown"}
FIRST_LINE = ("Information only, not legal advice; questions for an attorney or the DSO are in "
              "`legal/questions.md`.")
SECTION = "E-Verify lookups"
KEY_ORDER = ["company", "legal_entity", "e_verify", "e_verify_source", "stance", "cap_exempt", "remote_ok_for_i983",
             "employment_type", "verdict", "checked_at"]
NOT_FOUND_NOTE = ("not listed may mean enrolled under another name, or not enrolled; ask the employer for the "
                  "enrolled legal name and E-Verify Company ID")


def check_path(root: Path, company: str) -> Path:
    return Path(root) / "legal" / "checks" / f"{slugify(company)}.md"


def _q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)          # a YAML double-quoted scalar


def source_text(status: str, source: str, day: date) -> str:
    text = f"E-Verify Employer Search ({SEARCH_URL}), looked up by the owner {day.isoformat()}: {status} ({source})"
    return text + (f"; {NOT_FOUND_NOTE}" if status == "not-found" else "")


def _value(status: str) -> str:
    return _q("yes") if STATUSES[status] == "yes" else "unknown"   # quoted: a bare yes would parse as true


def set_keys(text: str, updates: dict[str, str]) -> str:
    """Replace (or insert, before `stance:` or at the end) single-line frontmatter keys, keeping every other line."""
    if not text.startswith("---\n"):
        raise ValueError("no frontmatter at line 1")
    end = text.find("\n---", 4)
    if end < 0:
        raise ValueError("frontmatter not closed")
    head, rest = text[4:end].split("\n"), text[end:]
    out, done = [], set()
    i = 0
    while i < len(head):
        line = head[i]
        m = re.match(r"^([A-Za-z_][\w-]*):", line)
        if m and m.group(1) in updates:
            key = m.group(1)
            out.append(f"{key}: {updates[key]}")
            done.add(key)
            i += 1
            while i < len(head) and head[i].startswith((" ", "\t")):   # drop a folded continuation of the old value
                i += 1
            continue
        out.append(line)
        i += 1
    missing = [k for k in updates if k not in done]
    if missing:
        at = next((n for n, line in enumerate(out) if line.startswith("stance:")), len(out))
        out[at:at] = [f"{k}: {updates[k]}" for k in missing]
    return "---\n" + "\n".join(out) + rest


def stub(company: str, slug: str) -> str:
    fields = {"company": _q(company), "legal_entity": _q("Unknown - to confirm"), "e_verify": "unknown",
              "e_verify_source": _q("not looked up yet"), "stance": "unknown", "cap_exempt": "unknown",
              "remote_ok_for_i983": "unknown", "employment_type": "unknown", "verdict": "pending", "checked_at": "null"}
    lines = ["---"] + [f"{k}: {fields[k]}" for k in KEY_ORDER] + ["---", FIRST_LINE, "",
             f"# {company}: employer check (pending)", "",
             f"Created by `scripts/everify_note.py` to hold the owner's E-Verify lookup. legal-visa's company check "
             f"(`visa-check company {slug}`) has not run yet, so there is no verdict and the apply gate stays closed.", ""]
    return "\n".join(lines)


def record(root: Path, company: str, status: str, source: str, day: date, today: date | None = None,
           dry_run: bool = False) -> dict[str, object]:
    if status not in STATUSES:
        raise ValueError(f"--status must be one of {', '.join(STATUSES)}")
    source = re.sub(r"\s+", " ", source or "").strip()
    if not source:
        raise ValueError("--source must say how the owner checked (for example the name searched and what the page showed)")
    today = today or date.today()
    if day > today:
        raise ValueError(f"--date {day.isoformat()} is after today ({today.isoformat()})")
    path = check_path(root, company)
    slug = path.stem
    created = not path.is_file()
    text = stub(company, slug) if created else path.read_text(encoding="utf-8")
    before = _head_of(text)
    old = before.get("e_verify")
    old = "yes" if old is True else "no" if old is False else str(old or "unknown")
    new = STATUSES[status]
    text = set_keys(text, {"e_verify": _value(status), "e_verify_source": _q(source_text(status, source, day))})
    head, body = _split(text)
    line = (f"- {day.isoformat()}: {status} (e_verify {new}; was {old}). Source: {source}. "
            f"Recorded with scripts/everify_note.py.")
    body = append_section_line(body, SECTION, line)
    text = head + body
    after = _head_of(text)                                # must still parse, with the value we meant
    got = after.get("e_verify")
    got = "yes" if got is True else "no" if got is False else str(got)
    if got != new:
        raise ValueError(f"{path}: e_verify reads back as {got!r}, expected {new!r}; nothing written")
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    verdict = str(after.get("verdict") or "missing")
    return {"path": path, "slug": slug, "created": created, "old": old, "new": new, "verdict": verdict,
            "recheck": (old != new and verdict not in ("pending", "missing")), "line": line}


def _split(text: str) -> tuple[str, str]:
    end = text.find("\n---", 4)
    nl = text.find("\n", end + 1)
    cut = len(text) if nl < 0 else nl + 1
    return text[:cut], text[cut:]


def _head_of(text: str) -> dict:
    head_text, _ = _split(text)
    inner = head_text.strip()[3:]
    inner = inner[: inner.rfind("---")]
    data = fm.parse_yaml(inner)
    return data if isinstance(data, dict) else {}


def main(argv: list[str] | None = None, today: date | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record", help="write the owner's E-Verify lookup into legal/checks/<company>.md")
    rec.add_argument("company", help="company name or slug (the file is legal/checks/<slug>.md)")
    rec.add_argument("--status", required=True, choices=sorted(STATUSES))
    rec.add_argument("--source", required=True, help="how the owner checked: the name searched and what the page showed")
    rec.add_argument("--date", type=cli_date, default=None, help="the day of the lookup (default today)")
    rec.add_argument("--dry-run", action="store_true", help="show the line; write nothing")
    sh = sub.add_parser("show", help="print the E-Verify keys of legal/checks/<company>.md")
    sh.add_argument("company")
    args = ap.parse_args(argv)
    today = today or date.today()
    if args.cmd == "show":
        path = check_path(args.root, args.company)
        if not path.is_file():
            print(f"everify: {path.relative_to(args.root)} missing (legal-visa has not checked this company)")
            return 1
        head = _head_of(path.read_text(encoding="utf-8"))
        for k in ("company", "e_verify", "e_verify_source", "verdict", "checked_at"):
            print(f"{k}: {head.get(k)}")
        return 0
    try:
        res = record(args.root, args.company, args.status, args.source, args.date or today, today, args.dry_run)
    except (ValueError, fm.FrontmatterError) as exc:
        print(f"everify: {exc}", file=sys.stderr)
        return 1
    rel = Path(res["path"]).relative_to(args.root)
    verb = "would write" if args.dry_run else ("created" if res["created"] else "updated")
    print(f"everify: {verb} {rel}: e_verify {res['old']} -> {res['new']}")
    print(f"  {res['line']}")
    if res["created"]:
        print(f"  verdict pending: legal-visa runs visa-check company {res['slug']} before any apply pack")
    elif res["recheck"]:
        print(f"  legal-visa: re-run visa-check company {res['slug']}: the verdict ({res['verdict']}) predates this "
              f"E-Verify finding")
    return 0


if __name__ == "__main__":
    sys.exit(main())
