#!/usr/bin/env python3
"""mass_prep.py - make scored mass-tier JOBS ready for the mass apply batch: an application folder and a
coverage matrix per job, so `apply_batch.py --tier mass` can judge the base resume against it.

For each job id (jobs/<id>.md):
  1. skip it when its gate is `fail` (scripts/fit_gates.py), or when it is already an application (reported "exists");
  2. `tracker.new_application` (the same code as `tracker.py new --job <id>`): applications/<slug>/ with jd.md, fit.md,
     application.md and outcome.md; slug <company_slug>-<title-slug>-<yyyymm>; stage `discovered`, never `applied`;
  3. `jd_matrix.py build --jd applications/<slug>/jd.md --title "<title>" --out research/targets/<slug>.matrix.yaml`.
It never applies, submits or sends: a submit is the owner's act, logged later with `tracker.py event`.

Sources of job ids: the arguments, or --from-next-up: hub/NEXT-UP.md table rows whose last cell (tier) is `mass` and that
carry `jobs/<id>.md`, plus bullets `- <job-id> ...` under a "## Mass queue" heading. Prose (company names without ids)
is never guessed into ids: with no machine-readable mass rows it says so and makes nothing.

Output (stdout): progress lines, then "made:" and the made slugs one per line (the batch reads them), then
"visa checks needed:" and the company slugs of those applications that have no legal/checks/<company>.md, one per
line (the routine runs visa-check for them before apply_batch). parse_output() reads both lists back. Skips and problems
go to stderr. Exit 0, or 1 when a job id given as an argument is unknown or a step failed.
--dry-run writes nothing: it prints "would make <slug>" per job and the visa list for those, and "made:" stays empty.

CLI:  python3 scripts/mass_prep.py [--root JS] [--today D] [--max N] [--dry-run] (--from-next-up | <job-id> ...)
"""
from __future__ import annotations

import argparse
import contextlib
import io
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jd_matrix  # noqa: E402
from company_index import CompanyIndex  # noqa: E402
import tracker as tr  # noqa: E402
from fetch_ats import load_jobs  # noqa: E402
from js_common import JS_ROOT, cli_date  # noqa: E402

JOB_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
JOB_LINK_RE = re.compile(r"jobs/([a-z0-9][a-z0-9-]*)\.md")
DEFAULT_MAX = 15


def next_up_ids(root: Path) -> list[str]:
    path = root / "hub" / "NEXT-UP.md"
    if not path.is_file():
        return []
    ids: list[str] = []
    section = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section = line[3:].strip().lower()
            continue
        if line.startswith("|"):
            cells = [c.strip().lower() for c in line.strip().strip("|").split("|")]
            if cells and cells[-1] == "mass":
                ids += JOB_LINK_RE.findall(line)
        elif section == "mass queue" and line.startswith("- "):
            token = line[2:].split()[0] if line[2:].split() else ""
            if JOB_ID_RE.match(token) and (root / "jobs" / f"{token}.md").is_file():
                ids.append(token)
            else:
                print(f"skip NEXT-UP line (not a job id in jobs/): {line[:80]}", file=sys.stderr)
    seen: set[str] = set()
    return [i for i in ids if not (i in seen or seen.add(i))]


def existing_application(root: Path, job_id: str) -> str | None:
    for slug, meta, _ in tr.iter_applications(root) or []:
        if tr._link_target(meta.get("job")) == job_id:
            return slug
    return None


def build_matrix(root: Path, slug: str, title: str, today: date) -> tuple[int, str]:
    argv = ["build", "--jd", str(root / "applications" / slug / "jd.md"), "--title", title,
            "--out", str(root / "research" / "targets" / f"{slug}.matrix.yaml"), "--date", today.isoformat()]
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        try:
            code = jd_matrix.main(argv)
        except SystemExit as exc:
            code = int(exc.code or 2)
        except Exception as exc:       # a malformed JD must not stop the batch
            code, _ = 1, err.write(f"{type(exc).__name__}: {exc}")
    return code, err.getvalue().strip()


def parse_output(text: str) -> dict[str, list[str]]:
    """{"made": [...], "visa_checks_needed": [...]} from mass_prep's stdout."""
    out: dict[str, list[str]] = {"made": [], "visa_checks_needed": []}
    key = None
    for line in text.splitlines():
        if line == "made:":
            key = "made"
        elif line == "visa checks needed:":
            key = "visa_checks_needed"
        elif key and line.strip():
            out[key].append(line.strip())
    return out


def company_slug(root: Path, job: dict[str, Any], slug: str | None) -> str:
    if slug:
        try:
            meta = tr.load_application(root, slug)[1]
            if meta.get("company_slug"):
                return str(meta["company_slug"])
        except (OSError, ValueError):
            pass
    return str(CompanyIndex.load(root).resolve(job.get("company_slug") or job.get("company"))[0] or "")


def prepare(root: Path, job_ids: list[str], now: datetime, max_n: int,
            dry_run: bool = False) -> tuple[list[str], int, list[str]]:
    jobs = load_jobs(root) if (root / "jobs").is_dir() else {}
    made: list[str] = []
    companies: list[str] = []
    planned = 0
    problems = 0
    for job_id in job_ids:
        if len(made) + planned >= max_n:
            print(f"stop: --max {max_n} reached", file=sys.stderr)
            break
        job = jobs.get(job_id)
        if job is None:
            print(f"unknown job {job_id}: no jobs/{job_id}.md", file=sys.stderr)
            problems += 1
            continue
        if str(job.get("gate") or "").lower() == "fail":
            print(f"skip {job_id}: gate fail ({'; '.join(job.get('gate_reasons') or []) or 'see the job file'})", file=sys.stderr)
            continue
        have = existing_application(root, job_id)
        if have:
            print(f"exists {job_id}: applications/{have}/ (not re-made)", file=sys.stderr)
            continue
        if dry_run:
            slug, _ = tr.new_application(root, job_id, now, dry_run=True)
            print(f"would make {slug} (stage discovered; research/targets/{slug}.matrix.yaml)")
            companies.append(company_slug(root, job, None))
            planned += 1
            continue
        try:
            slug, created = tr.new_application(root, job_id, now)
        except (tr.TrackerError, KeyError) as exc:
            print(f"failed {job_id}: tracker new refused ({exc})", file=sys.stderr)
            problems += 1
            continue
        if not created:
            print(f"exists {job_id}: applications/{slug}/ (not re-made)", file=sys.stderr)
            continue
        code, err = build_matrix(root, slug, str(job.get("title") or ""), tr.dept_day(now))
        if code != 0:
            print(f"failed {slug}: jd_matrix build exit {code} {err[:200]} (the application stays at discovered)",
                  file=sys.stderr)
            problems += 1
            continue
        print(f"prepared {slug} (stage discovered; research/targets/{slug}.matrix.yaml)")
        made.append(slug)
        companies.append(company_slug(root, job, slug))
    seen: set[str] = set()
    need = [c for c in companies if c and not (root / "legal" / "checks" / f"{c}.md").is_file()
            and not (c in seen or seen.add(c))]
    return made, problems, need


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--root", type=Path, default=JS_ROOT)
    p.add_argument("--today", type=cli_date, default=None)
    p.add_argument("--max", type=int, default=DEFAULT_MAX)
    p.add_argument("--from-next-up", action="store_true", help="read the mass rows of hub/NEXT-UP.md")
    p.add_argument("--dry-run", action="store_true", help="print what it would make; write nothing")
    p.add_argument("job_ids", nargs="*")
    args = p.parse_args(argv)
    now = tr.run_now(args.today, now)
    ids = list(args.job_ids)
    if args.from_next_up:
        found = next_up_ids(args.root)
        if not found:
            print("no machine-readable mass rows in hub/NEXT-UP.md: a row needs tier `mass` and `jobs/<id>.md`, or a "
                  "\"## Mass queue\" bullet `- <job-id>`; nothing made", file=sys.stderr)
        ids += found
    made, problems, need = prepare(args.root, ids, now, max(args.max, 0), args.dry_run) if ids else ([], 0, [])
    print("nothing was applied or submitted (folders at stage discovered only)"
          + (" (dry-run: nothing written)" if args.dry_run else ""))
    print("made:")
    for slug in made:
        print(slug)
    print("visa checks needed:")
    for company in need:
        print(company)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
