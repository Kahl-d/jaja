#!/usr/bin/env python3
"""ats_detect.py - recognise the board vendor of each target the scouts cannot read yet.

Scouts cover the surface every day through scrapers and APIs, nothing random. The
coverage report (scout_coverage.py) names the targets with no reader: ats_vendor other, unknown, icims or taleo, or a
supported vendor with no board token. For each, this reads its careers_url ONCE, robots.txt first (fetch_ats.Robots,
RFC 9309), redirects followed, and matches fixed signatures in the final URL first, then in the page's links:

  greenhouse  (job-)boards.greenhouse.io/<token>, boards-api.greenhouse.io/v1/boards/<token>, embed ?for=<token>
  lever, ashby, workable, rippling, smartrecruiters   their public board URL with its token
  workday     <tenant>.wd<N>.myworkdayjobs.com/<site>  (token tenant.wdN/site, the form companies.yaml uses)
  successfactors career<N>.successfactors.(com|eu)...company=<id>  (token host|company)
  jibe        jibecdn.com on the page (iCIMS's Jibe; token: the careers host, as fetch_ats reads it)
  oracle      Oracle Recruiting Cloud: the CandidateExperience app on the company's own domain (token host|site), else
              a <name>.fa[.<region>].oraclecloud.com host named on the page (token host|site, or host)
  eightfold   an Eightfold page (PCSX): token "<careers host>|<company domain>", the API fetch_eightfold reads
  sitemap     a TalentBrew (Radancy) site: the sitemap robots.txt names (token host/sitemap.xml)
  avature, phenom, icims (<name>.icims.com), taleo (<name>.taleo.net)   named; no reader yet
A Greenhouse, Lever or Ashby token is checked with one call to that vendor's public board API before it is proposed: a
404 is reported as "stale link" (D1: the page linked a board that is gone). An `ashby_embed` with no org in the static
page is named ashby with no token.
A page with none of these but schema.org JobPosting data is a `schema-org` candidate (the sitemap reader can read it);
a page with neither is reported as no signature. A careers page on a platform whose terms forbid automated reading
(web_snapshot.DENY and targets/platforms.yaml's job platforms) is never fetched and is proposed `manual`.

Dry run by default: a table of what was found. --propose appends ats_vendor and board_token rows to
state/proposals/companies.jsonl, each with the companies_set.py command that applies it; companies.yaml itself is never
written here. No login, no CAPTCHA, no stealth, one page per target, a pause between requests.

CLI:  python3 scripts/ats_detect.py [--company SLUG]... [--propose] [--json] [--root JS]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_ats import SUPPORTED_VENDORS, FetchError, Robots  # noqa: E402
from js_common import JS_ROOT  # noqa: E402

UNREAD = ("other", "unknown", "icims", "taleo")
PAUSE = 2.0
HTML_ACCEPT = {"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"}
TOKEN = r"([A-Za-z0-9][A-Za-z0-9_.%-]*)"
# (vendor, pattern, token builder): vendors with a public JSON reader first, the order a text is searched in
SIGNATURES: tuple[tuple[str, re.Pattern, Any], ...] = (
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=" + TOKEN, re.I), lambda m: m.group(1)),
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/" + TOKEN, re.I), lambda m: m.group(1)),
    ("greenhouse", re.compile(r"(?<![\w.])(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?!embed\b)" + TOKEN, re.I),
     lambda m: m.group(1)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/" + TOKEN, re.I), lambda m: m.group(1)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/" + TOKEN, re.I), lambda m: m.group(1)),
    ("workday", re.compile(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)"),
     lambda m: f"{m.group(1)}.{m.group(2)}/{m.group(3)}"),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/" + TOKEN, re.I), lambda m: m.group(1)),
    ("workable", re.compile(r"apply\.workable\.com/" + TOKEN, re.I), lambda m: m.group(1)),
    ("rippling", re.compile(r"ats\.rippling\.com/" + TOKEN, re.I), lambda m: m.group(1)),
    ("successfactors", re.compile(r"(career\d*\.successfactors\.(?:com|eu))[^\"'\s]*?company=([A-Za-z0-9_-]+)", re.I),
     lambda m: f"{m.group(1).lower()}|{m.group(2)}"),
)
ORACLE_HOST = re.compile(r"([a-z0-9-]+\.fa(?:\.[a-z0-9-]+)?\.oraclecloud\.com)", re.I)
ORACLE_SITE = re.compile(r"/hcmUI/CandidateExperience/[a-z]{2}(?:-[A-Z]{2})?/sites/([A-Za-z0-9_-]+)", re.I)
ORACLE_SHORT_SITE = re.compile(r"^https?://[^/]+/[a-z]{2}(?:-[A-Z]{2})?/sites/([A-Za-z0-9_-]+)", re.I)
EIGHTFOLD = re.compile(r"([a-z0-9-]+)\.eightfold\.ai", re.I)
JIBE = re.compile(r"jibecdn\.com|jibeapply\.com", re.I)
TALENTBREW = re.compile(r"tbcdn\.talentbrew\.com|radancy", re.I)
AVATURE = re.compile(r"avature", re.I)
PHENOM = re.compile(r"cdn\.phenompeople\.com|phenompeople", re.I)
HOSTED = (("icims", re.compile(r"([a-z0-9-]+\.icims\.com)", re.I)), ("taleo", re.compile(r"([a-z0-9-]+\.taleo\.net)", re.I)))
JOBPOSTING = re.compile(r"application/ld\+json[^>]*>[^<]*\"JobPosting\"", re.I | re.S)
GENERIC = {"embed", "js", "jobs", "job", "careers", "v1", "boards", "en", "en-us"}
VERIFY = {"greenhouse": "https://boards-api.greenhouse.io/v1/boards/{}/jobs",
          "lever": "https://api.lever.co/v0/postings/{}?mode=json",
          "ashby": "https://api.ashbyhq.com/posting-api/job-board/{}"}


def detect(final_url: str, page: str) -> tuple[str | None, str | None, str]:
    """(vendor, token, where), searched in this order: the vendors with a public JSON reader (final URL, then page
    links), Jibe, Oracle Recruiting Cloud, Eightfold, TalentBrew (a sitemap), Avature, Phenom, iCIMS, Taleo; then
    ("schema-org", None, ...) when the page carries JobPosting data; (None, None, "") when nothing matches."""
    page = page or ""
    host = (urlparse(final_url).hostname or "").lower()
    for where, text in (("final URL", final_url), ("page link", page)):
        for vendor, rx, token in SIGNATURES:
            for m in rx.finditer(text):
                tok = token(m)
                if tok and tok.lower() not in GENERIC:
                    return vendor, tok.rstrip("."), where
    both = final_url + " " + page
    if re.search(r"id\s*=\s*[\"']ashby_embed", page, re.I):
        return "ashby", None, "page (ashby embed; its org is not in the static page)"
    if JIBE.search(page):
        return "jibe", host, "page (jibecdn)"                     # iCIMS's Jibe: fetch_ats reads it by host
    own = ORACLE_SITE.search(final_url)
    if own:                                   # the CX app on the company's own domain: that host serves the API too
        return "oracle", f"{host}|{own.group(1)}", "final URL"
    fa_host = ORACLE_HOST.search(both)
    if fa_host:
        site = ORACLE_SITE.search(page) or ORACLE_SHORT_SITE.search(final_url)
        api = fa_host.group(1).lower()
        return "oracle", f"{api}|{site.group(1)}" if site else api, "page link"
    if EIGHTFOLD.search(both):                # token "<api host>|<company domain>" (fetch_ats.fetch_eightfold, PCSX)
        if host.endswith(".eightfold.ai"):    # the tenant itself: its name is the company's
            return "eightfold", f"{host}|{host.split('.')[0]}.com", "final URL"
        return "eightfold", f"{host}|{_registrable(host)}", "page link"
    if TALENTBREW.search(page):
        return "sitemap", None, "page (TalentBrew)"                # the token is the sitemap robots.txt names
    if AVATURE.search(page):
        return "avature", host, "page (avature)"
    if PHENOM.search(page):
        return "phenom", host, "page link"
    for vendor, rx in HOSTED:
        m = rx.search(both)
        if m:
            return vendor, m.group(1).lower(), "page link"
    if JOBPOSTING.search(page):
        return "schema-org", None, "page JSON-LD"
    return None, None, ""


def _registrable(host: str) -> str:
    parts = host.lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()


def denied(js_root: Path | None) -> set[str]:
    """Domains never fetched: web_snapshot's hard floor plus the job platforms in targets/platforms.yaml."""
    import web_snapshot
    out = set(web_snapshot.DENY)
    if js_root is not None:
        try:
            out |= set(web_snapshot.denied_domains(Path(js_root)))
        except Exception:                      # a missing registry leaves the hard floor
            pass
    return out


def check(http: Any, company: dict[str, Any], js_root: Path | None) -> dict[str, Any]:
    url = str(company.get("careers_url") or "")
    row = {"slug": company.get("slug"), "was": company.get("ats_vendor"), "url": url, "state": "", "vendor": None,
           "token": None, "where": "", "readable": False, "note": ""}
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return dict(row, state="no url", note="no careers_url in companies.yaml")
    if _registrable(host) in denied(js_root):
        return dict(row, state="terms", vendor="manual", note=f"{_registrable(host)}: its terms forbid automated "
                                                              "reading; read in the owner's own browser")
    origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    try:
        status, robots = http.get_text(f"{origin}/robots.txt")
        if not Robots(None if status >= 400 else robots).allowed(url):
            return dict(row, state="robots", note=f"robots.txt disallows {url}")
        status, page, final = http.get_page(url, headers=HTML_ACCEPT)
    except FetchError as exc:
        return dict(row, state="error", note=str(exc)[:160])
    if status >= 400:
        return dict(row, state="error", note=f"HTTP {status}")
    vendor, token, where = detect(final, page)
    if vendor == "sitemap":                   # TalentBrew: the sitemap robots.txt names on this host, else /sitemap.xml
        named = [u for u in re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots or "")
                 if (urlparse(u).hostname or "").lower() == (urlparse(final).hostname or "").lower()]
        sm = named[0] if named else f"https://{urlparse(final).hostname}/sitemap.xml"
        token = sm.split("://", 1)[-1]
    if vendor is None:
        return dict(row, state="no signature", note=f"landed on {final}")
    api = VERIFY.get(vendor)
    if api and token:                         # D1: a public board is checked before it is proposed
        try:
            http.get_json(api.format(token))
        except FetchError as exc:
            if exc.status == 404:
                return dict(row, state="stale link", vendor=vendor, token=token, where=where,
                            note=f"the page links {vendor} board {token!r}, which answers 404 (gone or renamed)")
            raise
    readable = (vendor in SUPPORTED_VENDORS and bool(token)) or vendor == "schema-org"
    return dict(row, state="detected", vendor=vendor, token=token, where=where, readable=readable,
                note=f"landed on {final}" if final != url else "")


def targets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The targets with no reader: an unread vendor, or a supported vendor without a board token (a Workday target
    with a careers_url is read from it)."""
    out = []
    for r in rows:
        vendor = str(r.get("ats_vendor") or "unknown").lower()
        if vendor in UNREAD or (not r.get("board_token") and not (vendor == "workday" and r.get("careers_url"))):
            out.append(r)
    return out


def proposals(row: dict[str, Any], today: str) -> list[dict[str, Any]]:
    fields = []
    if row["vendor"] == "manual":
        fields = [("ats_vendor", "manual")]
    elif row["state"] == "detected" and row["vendor"] == "schema-org":
        fields = []                           # the sitemap reader needs a sitemap URL: said in the table, not guessed
    elif row["state"] == "detected":
        fields = [("ats_vendor", row["vendor"])] + ([("board_token", row["token"])] if row["token"] else [])
    return [{"date": today, "slug": row["slug"], "field": f, "value": v, "source": "ats_detect",
             "evidence": f"{row['where'] or row['state']}: {row['note'] or row['url']}"[:200],
             "apply": f"python3 scripts/companies_set.py set {row['slug']} {f} {v} --source ats_detect"}
            for f, v in fields]


def _http() -> Any:
    from fetch_ats import Http
    return Http(min_interval=1.0, max_retry_after=60.0)


def render(rows: list[dict[str, Any]]) -> str:
    lines = ["| target | was | state | vendor | token | reader | note |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['slug']} | {r['was']} | {r['state']} | {r['vendor'] or '-'} | {r['token'] or '-'} | "
                     f"{'yes' if r['readable'] else 'no'} | {r['note'][:90]} |")
    found = sum(1 for r in rows if r["state"] == "detected")
    readable = sum(1 for r in rows if r["readable"])
    lines.append(f"\n{len(rows)} targets; vendor found {found}; readable now {readable}; "
                 f"manual {sum(1 for r in rows if r['vendor'] == 'manual')}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--company", action="append", default=[], help="only these slugs (default: every unread target)")
    ap.add_argument("--propose", action="store_true", help="append the proposals (default: dry run)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rows = (yaml.safe_load((a.root / "targets" / "companies.yaml").read_text(encoding="utf-8")) or {}).get("companies") or []
    todo = [r for r in rows if r.get("slug") in set(a.company)] if a.company else targets(rows)
    http = _http()
    out = []
    for i, co in enumerate(todo):
        if i:
            time.sleep(PAUSE) if PAUSE else None
        out.append(check(http, co, a.root))
    print(json.dumps(out, indent=2) if a.json else render(out), end="")
    if a.propose:
        from js_common import today
        props = [p for r in out for p in proposals(r, today().isoformat())]
        path = a.root / "state" / "proposals" / "companies.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.writelines(json.dumps(p) + "\n" for p in props)
        print(f"proposed {len(props)} row(s) in {path.relative_to(a.root)}; apply with companies_set.py "
              "(`proposals` lists them)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
