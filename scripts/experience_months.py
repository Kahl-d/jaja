#!/usr/bin/env python3
"""experience_months.py: the one months-of-experience count every fit report cites (charter section 13 [5]).

The count is apply_pack.py's own: its role list (load_roles: profile/facts.yaml employment, the confirmed clock.yaml
end, full months only: apply_pack.full_months, never a partial month, never rounded up) is
what the apply pack's "Years of professional experience" row answers. Three numbers, one per wording a posting uses:
  industry          industry roles (kind experience; not a university, institute, teaching or internship title), the
                    month union: "full-time industry"
  excl_internships  industry less the internship months a role's `basis:` names ("internship Jan-May 2021"): "exclusive
                    of internships" counts the other roles as engineering, so this is also the engineering count the
                    years gate reads (fit_gates.ENGINEER_MONTHS)
  engineer_titled   industry roles with an engineer, scientist or developer title: "as a software or ML engineer"
A fit report pastes the `months:` line this prints into row C; `check` proves a fit.md carries it with today's numbers.

Usage: python3 scripts/experience_months.py [--today YYYY-MM-DD] [--json]
       python3 scripts/experience_months.py check applications/<slug>/fit.md
Exit 0 ok; 1 a fit.md without the current line; 2 usage error. Reads only; no network.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apply_pack as ap  # noqa: E402  (the existing count: load_ctx, load_roles, full_months, span_words)

from agency_paths import DATA_ROOT as JS_ROOT  # noqa: E402
ENGINEER = re.compile(r"\b(engineer|scientist|developer)\b", re.I)
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"), 1)}
INTERNSHIP = re.compile(r"internship\s+([A-Za-z]{3})[a-z]*\s*(?:-|to)\s*([A-Za-z]{3})[a-z]*\s+(\d{4})", re.I)
LINE = re.compile(r"^.*months: industry (\d+) . exclusive of internships (\d+) . engineer titles (\d+) "
                  r"\(experience_months\.py (\d{4}-\d{2}-\d{2})\).*$", re.M)


def internship_months(basis: Any) -> set[tuple[int, int]]:
    """The months a `basis:` line names as an internship ('semester-long internship Jan-May 2021 ...')."""
    m = INTERNSHIP.search(str(basis or ""))
    if not m or m.group(1).lower() not in MONTHS or m.group(2).lower() not in MONTHS:
        return set()
    y = m.group(3)
    start, end = f"{y}-{MONTHS[m.group(1).lower()]:02d}", f"{y}-{MONTHS[m.group(2).lower()]:02d}"
    return ap.months_between(start, end, date.today())


def counts(root: Path = JS_ROOT, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    ctx = ap.load_ctx(root, today)
    basis = {str(e.get("id") or ""): e.get("basis") for e in ctx.facts.get("employment") or [] if isinstance(e, dict)}
    industry = [r for r in ap.load_roles(ctx) if r.industry and r.title]
    union: set[tuple[int, int]] = set().union(*(r.months for r in industry)) if industry else set()
    intern: set[tuple[int, int]] = set().union(*(internship_months(basis.get(r.id)) & r.months for r in industry)) \
        if industry else set()
    eng = [r for r in industry if ENGINEER.search(r.title or "")]
    eng_union: set[tuple[int, int]] = set().union(*(r.months for r in eng)) if eng else set()
    roles = [{"org": r.org, "title": r.title, "start": r.start, "end": r.end, "months": len(r.months),
              "internship_months": len(internship_months(basis.get(r.id)) & r.months),
              "engineer_title": r in eng} for r in industry]
    return {"date": today.isoformat(), "industry": len(union), "excl_internships": len(union - intern),
            "engineer_titled": len(eng_union), "roles": roles}


def line(c: dict[str, Any]) -> str:
    return (f"months: industry {c['industry']} · exclusive of internships {c['excl_internships']} · engineer titles "
            f"{c['engineer_titled']} (experience_months.py {c['date']})")


def render(c: dict[str, Any]) -> str:
    out = [line(c)]
    for r in c["roles"]:
        tail = f", {r['internship_months']} of them internship" if r["internship_months"] else ""
        tag = "; engineer title" if r["engineer_title"] else ""
        out.append(f"  {r['org']}, {r['title']}: {r['start']} to {r['end']} = {r['months']} "
                   f"({ap.span_words(r['months'])}{tail}{tag})")
    return "\n".join(out)


def check(fit: Path, root: Path = JS_ROOT) -> list[str]:
    """Findings for one fit.md: the `months:` line is there and its numbers are the count on its own date."""
    if not fit.is_file():
        return [f"{fit}: no such file"]
    m = LINE.search(fit.read_text(encoding="utf-8"))
    if not m:
        return [f"{fit}: no `months:` line from experience_months.py in row C (paste the line it prints)"]
    try:
        on = date.fromisoformat(m.group(4))
    except ValueError:
        return [f"{fit}: the months line's date {m.group(4)} is not a date"]
    c = counts(root, on)
    got = tuple(int(x) for x in m.group(1, 2, 3))
    want = (c["industry"], c["excl_internships"], c["engineer_titled"])
    return [] if got == want else [f"{fit}: months line says {got}, experience_months.py {on} counts {want}"]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["check"]:
        p = argparse.ArgumentParser(prog="experience_months.py check")
        p.add_argument("fit", type=Path)
        p.add_argument("--root", type=Path, default=JS_ROOT)
        a = p.parse_args(argv[1:])
        findings = check(a.fit, a.root)
        print("\n".join(findings) if findings else f"{a.fit}: months line OK")
        return 1 if findings else 0
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--root", type=Path, default=JS_ROOT)
    p.add_argument("--today", default=None, help="YYYY-MM-DD (default: today)")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    try:
        today = date.fromisoformat(a.today) if a.today else None
        c = counts(a.root, today)
    except (ValueError, ap.Refusal) as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(c, indent=2) if a.json else render(c))
    return 0


if __name__ == "__main__":
    sys.exit(main())
