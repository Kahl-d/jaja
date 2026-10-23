#!/usr/bin/env python3
"""screener_check.py: validate the AI screener's resumes/variants/<slug>/screener.md.

Checks, per variant folder:
  front matter  parses; verdict is exactly PASS or REVISE; score an integer 1 to 10; pool_size 5 to 7 (the page and
                4 to 6 synthetic competitors); rank an integer 1 to pool_size; resume_sha256 equals sha256 of
                resume.yaml now (the screener read the current page)
  verdict       PASS only with rank 1 or 2 and score 8 or more; those numbers with REVISE are a finding too
  ## Pool       one table row per member (pool_size rows), exactly one `candidate` row, at the front matter's rank
  ## Top reasons             exactly three numbered items
  ## Requirements without evidence, ## Copied or stuffed lines   at least one bullet (`- none` when empty)
  ## Must-fix   at least one bullet on a REVISE
While roles/ai-screener.md does not exist (the role has not been created yet), no screener can run, so the
page is not blocked on it: `--not-run` records the gap as screener.md with `verdict: NOT RUN`, the reason and the
resume's sha256, and the check passes that file (it prints NOT RUN, never PASS). Once the role exists, NOT RUN is a
finding and `--not-run` refuses: the screener runs.
Usage: python3 scripts/screener_check.py resumes/variants/<slug>/ [more folders] [--not-run]
Exit 0 every file clean, 1 findings, 2 usage error (a folder that does not exist; --not-run once the role exists).
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from js_common import fm  # noqa: E402  (the department's front matter module)

VERDICTS = ("PASS", "REVISE")
NOT_RUN = "NOT RUN"
from agency_paths import DATA_ROOT as JS_ROOT  # noqa: E402
ROLE = Path("roles") / "ai-screener.md"
NOT_RUN_REASON = "role not created: roles/ai-screener.md waits for /os:new-role ai-screener"
LISTS = ("Requirements without evidence", "Copied or stuffed lines")
POOL_MIN, POOL_MAX = 5, 7


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _section(body: str, name: str) -> str | None:
    m = re.search(rf"^## {re.escape(name)}[ \t]*\n(.*?)(?=^## |\Z)", body, re.M | re.S)
    return m.group(1) if m else None


def _pool_rows(section: str) -> list[list[str]]:
    rows = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or cells[0].lower() == "rank" or set("".join(cells)) <= set("-: "):
            continue
        rows.append(cells)
    return rows


def role_exists(root: Path = JS_ROOT) -> bool:
    return (root / ROLE).is_file()


def _sha(vdir: Path) -> str | None:
    resume = vdir / "resume.yaml"
    return hashlib.sha256(resume.read_bytes()).hexdigest() if resume.is_file() else None


def not_run_text(vdir: Path, today: date | None = None) -> str:
    """screener.md recording that no screener ran (the role is not created yet)."""
    return (f"---\nverdict: {NOT_RUN}\nreason: \"{NOT_RUN_REASON}\"\nresume_sha256: {_sha(vdir) or 'missing'}\n"
            f"date: {(today or date.today()).isoformat()}\n---\n\n# AI screener: NOT RUN\n\n"
            "No AI screener read this page: the ai-screener role does not exist yet. This is a recorded gap, not a "
            "PASS. Run the screener on this page once the role has been created (/os:new-role ai-screener).\n")


def is_not_run(vdir: Path) -> bool:
    path = vdir / "screener.md"
    if not path.is_file():
        return False
    try:
        rec, _ = fm.load(path)
    except fm.FrontmatterError:
        return False
    return str((rec or {}).get("verdict") or "").strip() == NOT_RUN


def check(vdir: Path, root: Path = JS_ROOT) -> list[str]:
    path = vdir / "screener.md"
    if not path.is_file():
        if not role_exists(root):
            return [f"{path}: screener.md missing; the ai-screener role is not created yet: record the gap with "
                    f"`screener_check.py {vdir} --not-run`"]
        return [f"{path}: screener.md missing (run the ai-screener through /page-review)"]
    try:
        rec, body = fm.load(path)
    except fm.FrontmatterError as exc:
        return [f"{path}: front matter is not valid YAML ({str(exc).splitlines()[0][:160]})"]
    rec = rec or {}
    found: list[str] = []
    verdict = str(rec.get("verdict") or "").strip()
    if verdict == NOT_RUN:
        if role_exists(root):
            return [f"{path}: verdict NOT RUN but {ROLE} exists now: run the ai-screener on this page"]
        digest = _sha(vdir)
        if digest and str(rec.get("resume_sha256") or "").strip().lower() != digest:
            return [f"{path}: NOT RUN was recorded for resume {str(rec.get('resume_sha256') or 'missing')[:12]}, not "
                    f"{digest[:12]}: record it again with --not-run"]
        return []
    if verdict not in VERDICTS:
        found.append(f"verdict is {verdict or 'missing'!r}; exactly PASS or REVISE")
    score, rank, pool = _int(rec.get("score")), _int(rec.get("rank")), _int(rec.get("pool_size"))
    if score is None or not 1 <= score <= 10:
        found.append(f"score is {rec.get('score')!r}; an integer 1 to 10")
    if pool is None or not POOL_MIN <= pool <= POOL_MAX:
        found.append(f"pool_size is {rec.get('pool_size')!r}; {POOL_MIN} to {POOL_MAX} (the page and 4 to 6 synthetic "
                     "competitors)")
    if rank is None or rank < 1 or (pool is not None and rank > pool):
        found.append(f"rank is {rec.get('rank')!r}; an integer 1 to pool_size")
    resume = vdir / "resume.yaml"
    if resume.is_file():
        digest = hashlib.sha256(resume.read_bytes()).hexdigest()
        if str(rec.get("resume_sha256") or "").strip().lower() != digest:
            found.append(f"resume_sha256 is {str(rec.get('resume_sha256') or 'missing')[:12]}, not {digest[:12]}: "
                         "the screener re-reads the current page")
    if score is not None and rank is not None:
        earns = rank <= 2 and score >= 8
        if verdict == "PASS" and not earns:
            found.append(f"PASS needs rank 1 or 2 and score 8 or more (rank {rank}, score {score}): REVISE")
        if verdict == "REVISE" and earns:
            found.append(f"REVISE on rank {rank}, score {score}: those numbers are a PASS; fix the numbers or the verdict")
    pool_text = _section(body, "Pool")
    if pool_text is None:
        found.append("## Pool missing")
    else:
        rows = _pool_rows(pool_text)
        if pool is not None and len(rows) != pool:
            found.append(f"## Pool has {len(rows)} rows, pool_size says {pool}")
        cand = [r for r in rows if len(r) > 1 and r[1].lower() == "candidate"]
        if len(cand) != 1:
            found.append(f"## Pool has {len(cand)} candidate rows; exactly one (who = candidate)")
        elif rank is not None and cand[0][0] != str(rank):
            found.append(f"## Pool ranks the candidate {cand[0][0]!r}, front matter rank is {rank}")
    reasons = _section(body, "Top reasons")
    if reasons is None:
        found.append("## Top reasons missing")
    elif len(re.findall(r"^\s*\d+[.)]\s+\S", reasons, re.M)) != 3:
        found.append("## Top reasons needs exactly three numbered items")
    for name in LISTS:
        text = _section(body, name)
        if text is None:
            found.append(f"## {name} missing")
        elif not re.search(r"^\s*[-*]\s+\S", text, re.M):
            found.append(f"## {name} is empty; list the items or write `- none`")
    if verdict == "REVISE":
        text = _section(body, "Must-fix")
        if text is None or not re.search(r"^\s*[-*\d][.)]?\s*\S", text, re.M):
            found.append("## Must-fix needs at least one item on a REVISE")
    return [f"{path}: {f}" if not f.startswith(str(path)) else f for f in found]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("variants", type=Path, nargs="+")
    ap.add_argument("--not-run", action="store_true",
                    help="record screener.md as NOT RUN (only while roles/ai-screener.md does not exist)")
    ap.add_argument("--root", type=Path, default=JS_ROOT, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    missing = [v for v in a.variants if not v.is_dir()]
    if missing:
        print(f"not a folder: {', '.join(map(str, missing))}", file=sys.stderr)
        return 2
    if a.not_run:
        if role_exists(a.root):
            print(f"refused: {ROLE} exists; run the ai-screener instead of recording NOT RUN", file=sys.stderr)
            return 2
        for v in a.variants:
            path = v / "screener.md"
            if path.is_file() and not is_not_run(v):
                print(f"{v}: kept: screener.md already holds a screener's verdict", file=sys.stderr)
                continue
            path.write_text(not_run_text(v), encoding="utf-8")
            print(f"{v}: screener NOT RUN recorded ({NOT_RUN_REASON})")
    total = 0
    for v in a.variants:
        found = check(v, a.root)
        total += len(found)
        ok = "OK (screener NOT RUN: role not created; a recorded gap, not a PASS)" if is_not_run(v) else "OK"
        print(f"{v}: {ok if not found else f'FINDINGS {len(found)}'}")
        for f in found:
            print(f"  {f}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
