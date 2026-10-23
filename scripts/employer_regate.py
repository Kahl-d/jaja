#!/usr/bin/env python3
"""employer_regate.py: a dry run of the real-employer gate over the applications we already have.
Report only: it changes no record.

For each applications/*/application.md it reads the company and the JD (applications/<slug>/jd.md, else the job's
body) and runs fit_gates.gate_employer, the same check the gates run on new jobs: a staffing agency, recruiting
firm or talent marketplace by name, or a JD hiring for an unnamed client, fails; an IT-services consultancy goes to
review. Each row says what follows from it, for the Recruiter to decide:
  hold       fail, not applied yet: do not apply here; work the lead at the named, verified end employer
  note       fail or review, already applied (or closed): nothing to undo; the lesson goes to the hunt
  review     review, not applied yet: read the seat before applying
Usage: python3 scripts/employer_regate.py [--root JS] [--json]
Exit 0 every application passes, 1 rows listed, 2 usage error. No network.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fit_gates  # noqa: E402
from fetch_ats import jd_text_from_body  # noqa: E402
from js_common import fm  # noqa: E402

from agency_paths import DATA_ROOT as JS_ROOT  # noqa: E402
PRE_APPLY = ("discovered", "fit_checked", "tailored", "on_hold")


def _load(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        return {}, ""
    try:
        props, body = fm.load(path)
    except fm.FrontmatterError:
        return {}, ""
    return (props if isinstance(props, dict) else {}), body


def _job_id(value: Any) -> str:
    text = str(value or "").strip()
    return text[2:].split("|")[0].split("]")[0].strip() if text.startswith("[[") else text


def regate(root: Path, no_jd: list[str] | None = None) -> list[dict[str, Any]]:
    """Rows for the applications the gate does not pass; `no_jd` collects those with no JD text (name check only)."""
    rows = []
    for path in sorted((root / "applications").glob("*/application.md")):
        app, _ = _load(path)
        slug = str(app.get("id") or path.parent.name)
        job_id = _job_id(app.get("job"))
        job, job_body = _load(root / "jobs" / f"{job_id}.md") if job_id else ({}, "")
        jd_path = path.parent / "jd.md"
        jd = jd_text_from_body(_load(jd_path)[1]) if jd_path.is_file() else jd_text_from_body(job_body)
        if len(jd.strip()) < 200 and no_jd is not None:
            no_jd.append(slug)
        record = {"company": app.get("company") or job.get("company") or ""}
        findings = fit_gates.gate_employer(record, jd)
        if not findings:
            continue
        verdict = "fail" if any(lv == "fail" for lv, _, _ in findings) else "review"
        stage = str(app.get("stage") or "")
        call = ("hold" if verdict == "fail" else "review") if stage in PRE_APPLY else "note"
        rows.append({"application": slug, "company": str(record["company"]), "stage": stage, "verdict": verdict,
                     "reasons": [r for _, r, _ in findings], "evidence": [e for _, _, e in findings if e],
                     "call": call})
    return rows


def render(rows: list[dict[str, Any]], total: int, no_jd: list[str] | None = None) -> str:
    out = [f"employer_regate (dry run, no record changed): {total} application(s), {len(rows)} listed "
           f"({sum(r['verdict'] == 'fail' for r in rows)} fail, {sum(r['verdict'] == 'review' for r in rows)} review)",
           "", "| application | company | stage | verdict | call | reason |", "|---|---|---|---|---|---|"]
    for r in rows:
        reason = "; ".join(r["reasons"]).replace("|", "/")
        out.append(f"| {r['application']} | {r['company']} | {r['stage'] or '-'} | {r['verdict']} | {r['call']} | "
                   f"{reason} |")
    if no_jd:
        out += ["", f"no JD text (the company name was checked, the JD words could not be): {', '.join(no_jd)}"]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not (args.root / "applications").is_dir():
        print(f"usage error: no applications/ under {args.root}", file=sys.stderr)
        return 2
    no_jd: list[str] = []
    rows = regate(args.root, no_jd)
    total = len(list((args.root / "applications").glob("*/application.md")))
    print(json.dumps({"rows": rows, "no_jd": no_jd}, indent=2) if args.json else render(rows, total, no_jd))
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
