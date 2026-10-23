#!/usr/bin/env python3
"""Leak check: nothing personal may enter this repo. Run before every commit (pre-commit and CI).

Checks the tracked and untracked-not-ignored files of the repo for
  1. forbidden paths (your data folders, documents, generated caches),
  2. secret and identifier patterns (emails, home paths, connector UUIDs, phone numbers, tokens),
  3. your private deny-list (tools/build_denylist.py; never committed): `hard` terms fail, `soft` terms report,
  4. gitleaks, when installed.
Matched values are never printed, only the file, line and rule.

    python3 tools/leak_check.py [--root .] [--denylist FILE | --no-denylist] [--soft] [--staged]
Exit 0 = clean, 1 = findings.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

FORBIDDEN_TOP = {"profile", "resumes", "applications", "prospects", "graph", "touches", "outbox", "jobs", "jobs-pending",
                 "state", "research", "companies", "legal", "vault", "brain", "core", "pursuits", "prep", "hub-data"}
ALLOWED_PREFIX = ("data-skeleton/", "examples/demo-data/", "tests/", "scripts/tests/", "mcp/tests/", "scripts/kernel/tests/")
BAD_EXT = {".pdf", ".docx", ".doc", ".xlsx", ".pptx", ".eml", ".mbox", ".sqlite", ".db", ".pem", ".key", ".p12", ".jsonl"}
ALLOWED_BAD = ("examples/demo-data/", "tests/", "scripts/tests/fixtures/", "mcp/tests/", "evals/")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
OK_MAIL_DOMAINS = re.compile(r"(^|\.)(example\.(com|org|net)|users\.noreply\.github\.com|noreply\.github\.com|anthropic\.com|localhost|test|invalid)$", re.I)
HOME = re.compile(r"(/Users/|/home/)(?!you\b|user\b|name\b|<|\$|\{|example\b|username\b|me\b|runner\b)[A-Za-z0-9._-]+")
UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
PHONE = re.compile(r"(?<![\d.])(?:\+?1[ .-]?)?\(?\b[2-9]\d{2}\)?[ .-]\d{3}[ .-]\d{4}\b")
TOKENS = [("token:sk", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")), ("token:ghp", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
          ("token:aws", re.compile(r"\bAKIA[0-9A-Z]{16}\b")), ("token:slack", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
          ("token:google", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")), ("token:privkey", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"))]
RULING = re.compile(r"\bK-\d{1,3}\b")
VENDOR_OK: set[str] = set()
SOFT_OK: set[str] = set()
SKIP_CONTENT = {"uv.lock", "package-lock.json", "leak_check.py", "build_denylist.py", ".gitleaks.toml", "allowlist.txt"}


def files(root: Path, staged: bool) -> list[str]:
    if staged:
        cmd = ["git", "-C", str(root), "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"]
    else:
        cmd = ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    return [f for f in subprocess.run(cmd, capture_output=True, check=True).stdout.decode().split("\0") if f]


def load_allow(root: Path) -> set[tuple[str, str]]:
    p = root / "tools" / "leak_allowlist.txt"
    out = set()
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line and ":" in line:
                path, rule = line.rsplit(":", 1)
                out.add((path.strip(), rule.strip()))
    return out


def _words(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {ln.split("#", 1)[0].strip().lower() for ln in path.read_text(encoding="utf-8").splitlines() if ln.split("#", 1)[0].strip()}


def soft_ok(root: Path, denylist: Path) -> set[str]:
    """Reviewed words of the soft deny-list that are fine in the repo. They are your own review, so they live next to the
    deny-list (denylist-soft-ok.txt), never in the repo."""
    return _words(denylist.with_name("denylist-soft-ok.txt"))


def vendor_ok(root: Path) -> set[str]:
    p = root / "tools" / "leak_vendor_allow.txt"
    if not p.is_file():
        return set()
    return {ln.split("#", 1)[0].strip().lower() for ln in p.read_text(encoding="utf-8").splitlines() if ln.split("#", 1)[0].strip()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--denylist", default=str(Path.home() / ".config" / "job-agency" / "denylist.txt"))
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--no-denylist", action="store_true", help="pattern checks only (CI has no private deny-list); no warning")
    ap.add_argument("--soft", action="store_true", help="also list soft (company-name) hits")
    ap.add_argument("--no-gitleaks", action="store_true")
    ap.add_argument("--only", nargs="+", metavar="PREFIX", help="scan only files whose path starts with one of these")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    allow = load_allow(root)
    global VENDOR_OK, SOFT_OK
    VENDOR_OK = vendor_ok(root)
    SOFT_OK = soft_ok(root, Path(a.denylist).expanduser())
    VENDOR_OK |= _words(Path(a.denylist).expanduser().with_name("denylist-vendor-extra.txt"))
    findings: list[str] = []
    soft_hits: list[str] = []

    def add(path: str, line: int, rule: str):
        if (path, rule) in allow or (path, "*") in allow:
            return
        findings.append(f"{path}:{line}: {rule}")

    hard_terms, soft_terms, company_terms, companycs_terms = [], [], [], []
    dl = Path(a.denylist).expanduser()
    if a.no_denylist:
        dl = root / ".no-denylist-requested"          # never exists: only the pattern checks run
    if dl.is_file():
        for ln in dl.read_text(encoding="utf-8").splitlines():
            kind, _, term = ln.partition("\t")
            if term:
                if kind in ("company", "companycs") and term.lower() in VENDOR_OK:
                    continue
                {"hard": hard_terms, "company": company_terms, "companycs": companycs_terms}.get(kind, soft_terms).append(re.escape(term))

    def combine(terms, substring=False):
        # one alternation per kind, longest first, so a line is scanned once. A long term also matches inside a
        # word or identifier (test_<name>_..., a possessive form); a short one needs word boundaries.
        if not terms:
            return []
        terms = sorted(set(terms), key=len, reverse=True)
        def _long(t):
            raw = t.replace("\\", "")
            return substring and len(raw) >= 6 and any(ch.isalpha() for ch in raw)
        long_t = [t for t in terms if _long(t)]
        short_t = [t for t in terms if not _long(t)]
        out = []
        if long_t:
            out.append(re.compile("(?:" + "|".join(long_t) + ")", re.I))
        if short_t:
            out.append(re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(short_t) + r")(?![A-Za-z0-9])", re.I))
        return out

    hard, soft, company = combine(hard_terms, substring=True), combine(soft_terms), combine(company_terms)
    company_cs = (re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(sorted(set(companycs_terms), key=len, reverse=True)) + r")(?![A-Za-z0-9])")
                  if companycs_terms else None)
    if not dl.is_file() and not a.no_denylist:
        print(f"WARNING: no deny-list at {dl}; run tools/build_denylist.py --data <your data folder>", file=sys.stderr)

    flist = files(root, a.staged)
    if a.only:
        flist = [f for f in flist if f.startswith(tuple(a.only))]
    for rel in flist:
        top = rel.split("/", 1)[0]
        if top in FORBIDDEN_TOP and not rel.startswith(ALLOWED_PREFIX):
            add(rel, 0, "forbidden-path")
            continue
        if "/__pycache__/" in rel or rel.endswith(".pyc"):
            add(rel, 0, "forbidden-path")
            continue
        ext = Path(rel).suffix.lower()
        if ext in BAD_EXT and not rel.startswith(ALLOWED_BAD):
            add(rel, 0, f"forbidden-type:{ext}")
        path = root / rel
        if not path.is_file() or path.name in SKIP_CONTENT or ext in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".pdf", ".docx"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            line = line.replace("\\n", " ").replace("\\t", " ").replace("\\r", " ")      # "\nName" in a string literal
            for m in EMAIL.finditer(line):
                dom = m.group(1).lower()
                if not OK_MAIL_DOMAINS.search(dom) and not any(v in dom for v in VENDOR_OK if len(v) >= 5):
                    add(rel, i, "email")
            if HOME.search(line):
                add(rel, i, "home-path")
            if UUID.search(line):
                add(rel, i, "uuid")
            if PHONE.search(line):
                add(rel, i, "phone")
            for name, rx in TOKENS:
                if rx.search(line):
                    add(rel, i, name)
            for rx in hard:
                if rx.search(line):
                    add(rel, i, "denylist-hard")
            for rx in company:
                if rx.search(line):
                    add(rel, i, "denylist-company")
            if company_cs is not None and company_cs.search(line):
                add(rel, i, "denylist-company")
            if RULING.search(line):
                add(rel, i, "ruling-id")
            for rx in soft:
                for m in rx.finditer(line):
                    if m.group(0).lower() in SOFT_OK:
                        soft_hits.append(f"{rel}:{i}: denylist-soft")      # ordinary word or vendor, reviewed
                    else:
                        add(rel, i, "denylist-soft")                      # a name from your records, unreviewed

    if not a.no_gitleaks and shutil.which("gitleaks"):
        r = subprocess.run(["gitleaks", "detect", "--no-git", "--source", str(root), "--redact", "--no-banner", "--log-level", "error"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            findings.append("gitleaks: findings (run `gitleaks detect --no-git --redact` for detail)")
    elif not a.no_gitleaks:
        print("NOTE: gitleaks not installed; skipped (CI runs it)", file=sys.stderr)

    for f in sorted(set(findings)):
        print("FAIL", f)
    if a.soft:
        for f in sorted(set(soft_hits)):
            print("SOFT", f)
    print(f"leak_check: {len(flist)} files, {len(set(findings))} failing findings, {len(set(soft_hits))} soft hits")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
