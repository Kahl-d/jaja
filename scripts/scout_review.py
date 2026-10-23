#!/usr/bin/env python3
"""Close job-scout's review by script: re-gate ghost keeps that lack their proof, then count passed_gates.

    python3 scripts/scout_review.py close [--today YYYY-MM-DD] [--dry-run] [--root R]
    python3 scripts/scout_review.py live <job id> --evidence "<quoted fetch result>" [--today YYYY-MM-DD]

close   run after the scout review (roles/job-scout.md), in this order:
        1. Ghost keeps. Every job the scout kept that day (status fit_checked, fit_verdict unscored, a line
           dated that day under "## Scout review") is held to the ghost rubric by its ghost_score:
             0.3 to 0.6  needs a liveness fetch that day: last_seen on that day (the daily board fetch or a
                         fresh alert saw it) or live_checked_at on that day (`live`, below);
             over 0.6    needs a warm contact: a prospects/*.md with the job's company_slug.
           A keep without it is re-gated: gate review, a gate_reasons entry "ghost re-gate <day> (gate was
           <g>): ..." and a dated Scout review line; it is not counted as passed. Under 0.3 nothing happens.
        2. passed_gates. For each sources.yaml entry, the jobs first seen that day whose source is the entry's
           id (or, when no entry has that id, its kind): passed = status fit_checked, queued, tailoring or
           applied, minus review gates the scout has not decided (no Scout review line) and open re-gates.
           Written in place as `passed_gates: N` (multi-line entries get the line added after last_run when
           missing; flow entries `{...}` get the key added), for the entries with such jobs or with
           last_run on that day; comments and every other value survive. Printed per source with new and
           skipped counts, and the undecided review gates by id.
live    records a liveness fetch the scout made (the employer's ATS record or apply page answered, quoted in
        --evidence): live_checked_at <day>, a Scout review line "<day> liveness ok: <evidence>", and an open
        ghost re-gate is cleared (the gate it had before comes back; a "note: ghost re-gate cleared" reason is
        added). Never for a failed fetch: a gone posting is `closed` by the scout with the fetch quoted.
Exit 0 ok, 1 unknown job, 2 no sources.yaml (close). Deterministic, no network; job records are written
through fetch_ats.save_job (the kernel frontmatter saver).
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_ats import load_jobs, save_job  # noqa: E402
from js_common import JS_ROOT, append_section_line, cli_date, fm, iter_records, parse_date, today  # noqa: E402

PASSED = ("fit_checked", "queued", "tailoring", "applied")
KEEP_MAX = 0.3            # ghost_score under this: keep (roles/job-scout.md, Ghost signals)
LIVENESS_MAX = 0.6        # 0.3 to 0.6: keep only after a liveness fetch succeeds that day; over: a warm contact
REGATE = "ghost re-gate"
CLEARED = "note: ghost re-gate cleared"


# ------------------------------------------------------------------------------ job helpers
def _section(body: str, heading: str) -> str:
    m = re.search(rf"(?ms)^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", body or "")
    return m.group(1) if m else ""


def reviewed_on(job: dict[str, Any], day: date) -> bool:
    return day.isoformat() in _section(job.get("_body") or "", "Scout review")


def open_regate(job: dict[str, Any]) -> str | None:
    """The last re-gate reason when no clearing note follows it, else None."""
    last = None
    for reason in job.get("gate_reasons") or []:
        text = str(reason)
        if text.startswith(REGATE):
            last = text
        elif text.startswith(CLEARED):
            last = None
    return last


def _ghost(job: dict[str, Any]) -> float:
    try:
        return float(job.get("ghost_score") or 0)
    except (TypeError, ValueError):
        return 0.0


def warm_companies(root: Path) -> set[str]:
    return {str(props.get("company_slug")) for _p, props, _b in iter_records(Path(root) / "prospects")
            if props.get("company_slug")}


def ghost_check(job: dict[str, Any], day: date, warm: set[str]) -> tuple[bool, str]:
    """(ok, why) for a keep with ghost_score 0.3 or more."""
    score = _ghost(job)
    if score <= LIVENESS_MAX:
        seen, checked = parse_date(job.get("last_seen")), parse_date(job.get("live_checked_at"))
        if seen == day:
            return True, f"ghost_score {score:.2f}, live (last_seen {job.get('last_seen')})"
        if checked == day:
            return True, f"ghost_score {score:.2f}, live (live_checked_at {day.isoformat()})"
        return False, (f"ghost_score {score:.2f} is in the liveness band ({KEEP_MAX} to {LIVENESS_MAX}) and no "
                       f"liveness fetch succeeded on {day.isoformat()} (last_seen {job.get('last_seen') or 'none'})")
    if str(job.get("company_slug")) in warm:
        return True, f"ghost_score {score:.2f}, kept on a warm contact at {job.get('company_slug')} (prospects/)"
    return False, (f"ghost_score {score:.2f} is over {LIVENESS_MAX} and no prospect at {job.get('company_slug')} "
                   "(no warm contact)")


def regate(job: dict[str, Any], day: date, why: str) -> None:
    job["gate_reasons"] = list(job.get("gate_reasons") or []) + [
        f"{REGATE} {day.isoformat()} (gate was {job.get('gate') or 'none'}): {why}"]
    job["gate"] = "review"
    job["_body"] = append_section_line(
        job.get("_body") or "", "Scout review",
        f"- {day.isoformat()} re-gated by scout_review.py: {why}; record a liveness fetch with "
        f"`scout_review.py live {job['id']}`, or skip the job")


# ------------------------------------------------------------------------------ sources.yaml
def load_entries(path: Path) -> list[dict[str, Any]]:
    data = fm.parse_yaml(path.read_text(encoding="utf-8")) or {}
    return [e for e in data.get("sources") or [] if isinstance(e, dict) and e.get("id")]


def source_of(entries: list[dict[str, Any]], job_source: Any) -> str | None:
    ids = {str(e["id"]) for e in entries}
    if str(job_source) in ids:
        return str(job_source)
    by_kind = [str(e["id"]) for e in entries if str(e.get("kind")) == str(job_source)]
    return by_kind[0] if len(by_kind) == 1 else None


def write_passed_gates(path: Path, counts: dict[str, int]) -> None:
    """Set `passed_gates: N` inside each named entry, in place (multi-line or flow style)."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        flow = re.match(r"^(\s*-\s*\{)(.*)(\}\s*(?:#[^\n]*)?\n?)$", line)
        fid = re.search(r"(?:^|[{,]\s*)id:\s*['\"]?([\w.-]+)", flow.group(2)) if flow else None
        if flow and fid and fid.group(1) in counts:
            inner = flow.group(2)
            value = counts[fid.group(1)]
            if re.search(r"\bpassed_gates:", inner):
                inner = re.sub(r"\bpassed_gates:\s*[^,}]*", f"passed_gates: {value}", inner)
            else:
                inner = inner.rstrip() + f", passed_gates: {value}"
            out.append(flow.group(1) + inner + flow.group(3))
            i += 1
            continue
        multiline = re.match(r"^(\s*)-\s+id:\s*['\"]?([\w.-]+)", line)
        if not (multiline and multiline.group(2) in counts):
            out.append(line)
            i += 1
            continue
        dash_indent = len(multiline.group(1))
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            ind = len(nxt) - len(nxt.lstrip())
            if nxt.strip() and (ind <= dash_indent):
                break
            j += 1
        chunk = lines[i:j]
        value = counts[multiline.group(2)]
        key_indent = next((re.match(r"^(\s*)\w", ln).group(1) for ln in chunk[1:]
                           if re.match(r"^\s*\w[\w-]*:", ln)), " " * (dash_indent + 2))
        done = False
        for k, ln in enumerate(chunk):
            m = re.match(r"^(\s*)passed_gates:[^\n#]*?(\s+#[^\n]*)?(\n?)$", ln)
            if k and m:
                chunk[k] = f"{m.group(1)}passed_gates: {value}{m.group(2) or ''}{m.group(3)}"
                done = True
        if not done:
            at = max((k for k, ln in enumerate(chunk) if re.match(r"^\s*(last_run|yield_30d):", ln)), default=None)
            if at is None:
                at = max(k for k, ln in enumerate(chunk) if ln.strip())
            if not chunk[at].endswith("\n"):
                chunk[at] += "\n"
            chunk.insert(at + 1, f"{key_indent}passed_gates: {value}\n")
        out.extend(chunk)
        i = j
    path.write_text("".join(out), encoding="utf-8")


# ------------------------------------------------------------------------------ commands
def close(root: Path, day: date, dry_run: bool = False) -> tuple[list[str], int]:
    sources_path = Path(root) / "sources.yaml"
    if not sources_path.is_file():
        return [f"no sources.yaml under {root}: nothing to count against"], 2
    jobs = load_jobs(root)
    warm = warm_companies(root)
    lines: list[str] = []
    for job in jobs.values():
        if job.get("status") != "fit_checked" or str(job.get("fit_verdict") or "unscored") != "unscored":
            continue
        if not reviewed_on(job, day) or _ghost(job) < KEEP_MAX:
            continue
        ok, why = ghost_check(job, day, warm)
        if ok:
            lines.append(f"{job['id']}: {why}")
            continue
        if open_regate(job) and open_regate(job).startswith(f"{REGATE} {day.isoformat()}"):
            lines.append(f"re-gated {job['id']} (already): {why}")
            continue
        regate(job, day, why)
        lines.append(f"re-gated {job['id']}: {why}")
        if not dry_run:
            save_job(root, job)
    entries = load_entries(sources_path)
    per: dict[str, dict[str, int]] = {}
    undecided: list[str] = []
    for job in jobs.values():
        if parse_date(job.get("first_seen")) != day:
            continue
        sid = source_of(entries, job.get("source"))
        if sid is None:
            lines.append(f"{job['id']}: source {job.get('source')} is not in sources.yaml (not counted)")
            continue
        row = per.setdefault(sid, {"new": 0, "passed_gates": 0, "skipped": 0})
        row["new"] += 1
        if job.get("status") == "skipped":
            row["skipped"] += 1
        undecided_gate = job.get("gate") == "review" and not _section(job.get("_body") or "", "Scout review").strip()
        if undecided_gate and job.get("status") in PASSED:
            undecided.append(job["id"])
        elif job.get("status") in PASSED and not open_regate(job):
            row["passed_gates"] += 1
    for e in entries:
        if parse_date(e.get("last_run")) == day:
            per.setdefault(str(e["id"]), {"new": 0, "passed_gates": 0, "skipped": 0})
    for sid, row in per.items():
        lines.append(f"{sid}: new {row['new']}, passed_gates {row['passed_gates']}, skipped {row['skipped']}")
    lines.append("undecided review gates: " + (", ".join(sorted(undecided)) or "none"))
    if not dry_run and per:
        write_passed_gates(sources_path, {sid: row["passed_gates"] for sid, row in per.items()})
    lines.append(f"sources.yaml passed_gates {'would be ' if dry_run else ''}written for "
                 + (", ".join(per) or "no source (no job first seen that day)")
                 + (" (dry-run, nothing written)" if dry_run else ""))
    return lines, 0


def live(root: Path, job_id: str, day: date, evidence: str, dry_run: bool = False) -> tuple[list[str], int]:
    jobs = load_jobs(root)
    if job_id not in jobs:
        return [f"no job {job_id} under {Path(root) / 'jobs'}"], 1
    job = jobs[job_id]
    job["live_checked_at"] = day.isoformat()
    job["_body"] = append_section_line(job.get("_body") or "", "Scout review",
                                       f"- {day.isoformat()} liveness ok: {' '.join(evidence.split())}")
    lines = [f"{job_id}: live_checked_at {day.isoformat()}"]
    reason = open_regate(job)
    if reason:
        m = re.search(r"\(gate was ([\w-]+)\)", reason)
        job["gate"] = m.group(1) if m and m.group(1) != "none" else job.get("gate")
        job["gate_reasons"] = list(job.get("gate_reasons") or []) + [
            f"{CLEARED} {day.isoformat()}: liveness ok"]
        lines.append(f"{job_id}: ghost re-gate cleared, gate {job['gate']}")
    if not dry_run:
        save_job(root, job)
    return lines, 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", type=Path, default=JS_ROOT)
    common.add_argument("--today", type=cli_date, default=None, help="the review day YYYY-MM-DD (default: today)")
    common.add_argument("--dry-run", action="store_true")
    sub.add_parser("close", parents=[common], help="re-gate unproven ghost keeps, then write passed_gates")
    lv = sub.add_parser("live", parents=[common], help="record a liveness fetch that succeeded")
    lv.add_argument("job")
    lv.add_argument("--evidence", required=True, help="the fetch result, quoted (URL and what it answered)")
    args = ap.parse_args(argv)
    day = args.today or today()
    if args.cmd == "close":
        lines, code = close(args.root, day, dry_run=args.dry_run)
    else:
        if not args.evidence.strip():
            ap.error("--evidence must quote the fetch result")
        lines, code = live(args.root, args.job, day, args.evidence, dry_run=args.dry_run)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
