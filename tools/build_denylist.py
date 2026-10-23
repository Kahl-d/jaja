#!/usr/bin/env python3
"""Build the private deny-list for the leak check from YOUR data folder.

The list holds your own identifiers (names, emails, phone, profile URLs, private answers) and the people and
companies in your records. It is written OUTSIDE the repo (default ~/.config/jaja/denylist.txt, mode 600)
and is never committed. Each line is `hard<TAB>term` (a hit fails the leak check), `company<TAB>term` (an employer, school or
company you targeted: a hit fails unless the term is in tools/leak_vendor_allow.txt) or `soft<TAB>term`
(every other posting company: reported for review).

    python3 tools/build_denylist.py --data /path/to/data [--out FILE]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import yaml

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SKIP_MAIL = re.compile(r"(no-?reply|donotreply|@example\.|@users\.noreply\.github\.com)", re.I)
URL = re.compile(r"https?://(?:www\.)?(?:linkedin\.com/in|github\.com|x\.com|twitter\.com)/[A-Za-z0-9._%-]+", re.I)
COMMON_TECH: set[str] = set()      # filled from <deny-list folder>/denylist-ordinary.txt: your companies whose names are ordinary words
COMMON = {"yes", "no", "true", "false", "open", "remote", "hybrid", "onsite", "confirmed", "internal-only", "needs-confirmation", "none", "n/a", "unknown",
          "united states", "san francisco, ca", "san francisco"}
PHONE = re.compile(r"\+?\d[\d\s().-]{8,}\d")


def strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from strings(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from strings(v)


def load_yaml(path: Path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def frontmatter(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    try:
        return yaml.safe_load(text.split("---", 2)[1]) or {}
    except Exception:
        return {}


def _default_denylist() -> str:
    """~/.config/jaja/denylist.txt, else the earlier ~/.config/job-agency/denylist.txt when only that one exists."""
    new = Path.home() / ".config" / "jaja" / "denylist.txt"
    old = Path.home() / ".config" / "job-agency" / "denylist.txt"
    return str(old if (old.is_file() and not new.is_file()) else new)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default=_default_denylist())
    a = ap.parse_args()
    ordinary = Path(a.out).expanduser().with_name("denylist-ordinary.txt")      # your companies whose names are ordinary words
    keep_cs: set[str] = set()
    if ordinary.is_file():
        for ln in ordinary.read_text(encoding="utf-8").splitlines():
            ln = ln.strip().lower()
            if ln and not ln.startswith("#"):
                if ln.startswith("="):                       # '=word': still matched, case-sensitively as written
                    keep_cs.add(ln[1:])
                COMMON_TECH.add(ln.lstrip("="))
    data = Path(a.data).expanduser()
    hard: set[str] = set()
    soft: set[str] = set()

    def add_hard(t: str):
        """Keep identifying strings only: names, emails, URLs, handles, numbers with digits or capitals.
        Plain lowercase words, evidence ids and status words match ordinary prose and are skipped."""
        t = t.strip()
        if len(t) < 4 or len(t) > 80 or "\n" in t:
            return
        if re.fullmatch(r"[a-z][a-z -]*", t) or re.match(r"ev-", t, re.I):
            return
        if t.lower() in COMMON:
            return
        hard.add(t)

    facts = load_yaml(data / "profile" / "facts.yaml") or {}
    ident = facts.get("identity") if isinstance(facts, dict) else None
    for s in strings(ident):
        add_hard(s)
        for m in EMAIL.findall(s) + URL.findall(s):
            add_hard(m)
    for sect in ("education", "employment"):
        for s in strings((facts or {}).get(sect)):
            if 3 <= len(s) <= 60 and re.match(r"^[A-Z][A-Za-z0-9&.' -]+$", s):
                soft.add(s.strip())
    priv = load_yaml(data / "profile" / "private.yaml") or {}
    for s in strings(priv):
        if len(s) >= 4 and not s.isdigit():
            add_hard(s)
    holds = data / "profile" / "private-holds.txt"
    if holds.is_file():
        for line in holds.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                add_hard(line)
    for p in sorted((data / "prospects").glob("*.md")) if (data / "prospects").is_dir() else []:
        fm = frontmatter(p)
        name = str(fm.get("name") or "").strip()
        if len(name.split()) >= 2:
            add_hard(name)
        if fm.get("company"):
            soft.add(str(fm["company"]).strip())
        body = p.read_text(encoding="utf-8", errors="ignore")
        for m in EMAIL.findall(body):
            if not SKIP_MAIL.search(m):
                add_hard(m)
        for m in URL.findall(body):
            add_hard(m)
    company: set[str] = set(soft)             # employers and schools from your facts, plus every company you targeted
    soft = set()
    if (data / "companies").is_dir():
        for p in (data / "companies").glob("*.md"):
            fm = frontmatter(p)
            for k in ("name", "slug"):
                if fm.get(k):
                    company.add(str(fm[k]).replace("-", " ").strip())
            for alias in fm.get("aliases") or []:
                company.add(str(alias).strip())
    for p in sorted((data / "prospects").glob("*.md")) if (data / "prospects").is_dir() else []:
        c = frontmatter(p).get("company")
        if c:
            company.add(str(c).strip())
    for p in sorted((data / "applications").glob("*/application.md")) if (data / "applications").is_dir() else []:
        fm = frontmatter(p)
        for k in ("company", "company_slug"):
            if fm.get(k):
                company.add(str(fm[k]).replace("-", " ").strip())
    for p in sorted((data / "jobs").glob("*.md")) if (data / "jobs").is_dir() else []:     # every posting seen: report only
        c = frontmatter(p).get("company")
        if c:
            soft.add(str(c).strip())
    srcs = load_yaml(data / "targets" / "companies.yaml")
    for s in strings(srcs):
        if re.fullmatch(r"[A-Z][A-Za-z0-9&.' -]{2,40}", s):
            company.add(s.strip())
    lower_hard = {t.lower() for t in hard}
    role_word = re.compile(r"\b(engineer|analyst|researcher|scientist|manager|developer|intern|director|lead|architect|specialist)\b", re.I)
    company = {c for c in company if len(c) >= 4 and c.lower() not in lower_hard and not role_word.search(c)}
    words = set()
    try:
        words = {w.strip().lower() for w in open("/usr/share/dict/words", encoding="utf-8", errors="ignore")}
    except OSError:
        pass
    # a company named like an ordinary word (Meta, Block, Lambda) is matched case-sensitively, as written
    company_cs = {c for c in company if " " not in c and c[0].isupper() and c.lower() in words and (c.lower() not in COMMON_TECH or c.lower() in keep_cs)}
    company -= {c for c in company if " " not in c and (c.lower() in words or c.lower() in COMMON_TECH)}
    soft = {c for c in soft if len(c) >= 6 and c.lower() not in lower_hard and c not in company}
    out = Path(a.out).expanduser()
    extra = out.with_name("denylist-extra.txt")          # your own additions, one per line: `hard<TAB>term` or just the term
    if extra.is_file():
        for ln in extra.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                hard.add(ln.split("\t", 1)[-1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(f"hard\t{t}\n" for t in sorted(hard)) + "".join(f"company\t{t}\n" for t in sorted(company)) + "".join(f"companycs\t{t}\n" for t in sorted(company_cs))
                   + "".join(f"soft\t{t}\n" for t in sorted(soft)), encoding="utf-8")
    os.chmod(out, 0o600)
    print(f"deny-list written: {len(hard)} hard, {len(company)} company, {len(company_cs)} case-sensitive company, {len(soft)} soft -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
