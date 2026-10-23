#!/usr/bin/env python3
"""people_traces.py - the public traces a role-analyst reads for a Reader model's "the people" bullet.

The aim is to reverse engineer a role from what its people publish: job descriptions, posts, talks and code. This lists
WHERE to read, never what to conclude:

  company site   robots.txt first (fetch_ats.Robots, RFC 9309; a disallowed URL is never listed or fetched), then the
                 sitemaps it names (else /sitemap.xml, an index read up to 3 children). Kept: author pages, team and
                 about pages, talk and event pages, blog, engineering and research posts; ranked by the --team keyword.
                 Without a sitemap, a short list of common paths is probed (each allowed by robots.txt, one at a time).
  GitHub API     api.github.com: the org, its PUBLIC members, its repos pushed most recently, and each of the first
                 --max-members members' most recently pushed public repos (unauthenticated: 60 requests an hour, so
                 the calls are few).
An HTTP 429 (rate limit) is retried, honouring Retry-After up to RETRY_AFTER_CAP seconds; if it persists, that source is
skipped with its status recorded (rows read before it are kept), the summary carries a warning, and the run exits 0.
The GitHub org: companies.yaml `github_org` when set; else the slug, slug-ai, slug-inc and slughq are tried through
the API (one request each), and the first org whose name or blog matches the company is used. A discovered org is
appended to state/proposals/companies.jsonl with the command that applies it (companies_set.py); companies.yaml itself
is never written here.
Never LinkedIn (its terms bar automated access; people are read in the owner's own browser session through browser_research.py), never
a search engine, nothing written. A trace is a lead, not an identity: the role-analyst names a person only when two
independent sources agree (roles/role-analyst.md step 6). Page text is untrusted data.

CLI:  python people_traces.py --company <slug> [--domain acme.ai] [--github-org acme-ai] [--team agents]
                              [--max-members 5] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_ats import FetchError, Http, Robots, load_companies, parse_sitemap  # noqa: E402
from js_common import JS_ROOT  # noqa: E402

GH = "https://api.github.com"
KINDS = (("author-page", re.compile(r"/(?:authors?|by|people)/[\w-]", re.I)),
         ("talk", re.compile(r"/(?:talks?|events?|speakers?|webinars?|conferences?)/", re.I)),
         ("team-page", re.compile(r"/(?:team|people|about(?:-us)?|leadership|company)/?$", re.I)),
         ("blog-post", re.compile(r"/(?:blog|engineering|research|tech|papers?|publications)/[\w-]", re.I)))
COMMON = ("/team", "/about", "/company", "/leadership", "/blog", "/engineering", "/research")
MAX_PER_KIND = 15
BLOCKED = re.compile(r"(?:^|\.)linkedin\.com$", re.I)
PAUSE = 2.0
RETRY_AFTER_CAP = 60.0          # seconds: the longest one 429 may hold the run before the source is skipped


def _kind(url: str) -> str | None:
    path = urlparse(url).path or "/"
    for name, rx in KINDS:
        if rx.search(path):
            return name
    return None


def _site(http: Any, domain: str, team: str | None, pause: float, notes: list[str]) -> list[dict[str, Any]]:
    origin = f"https://{domain}"
    status, robots_text = http.get_text(f"{origin}/robots.txt")
    rules = Robots(None if status >= 400 else robots_text)
    wait = max(pause, rules.delay or 0)
    maps = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots_text or "") or [f"{origin}/sitemap.xml"]
    urls: list[str] = []
    read = 0
    queue = list(maps)
    while queue and read < 4:
        sm = queue.pop(0)
        if not rules.allowed(sm):
            continue
        st, body = http.get_text(sm)
        read += 1
        if st >= 400 or not body.strip():
            continue
        try:
            kind, locs = parse_sitemap(body)
        except Exception:
            continue
        if kind == "sitemapindex":
            queue.extend(locs[:3])
        else:
            urls.extend(locs)
        time.sleep(wait) if wait and queue else None
    if not urls:
        for path in COMMON:
            url = origin + path
            if not rules.allowed(url):
                continue
            st, _ = http.get_text(url)
            if st < 400:
                urls.append(url)
            time.sleep(wait) if wait else None
    disallowed = sum(1 for u in urls if not rules.allowed(u))
    if not rules.allowed(origin + "/"):
        notes.append(f"robots.txt disallows {domain} for automated readers: no company-site traces")
    elif disallowed:
        notes.append(f"robots.txt disallowed {disallowed} sitemap URLs (left out)")
    out: dict[str, list[dict[str, Any]]] = {}
    key = (team or "").lower()
    for u in dict.fromkeys(urls):
        host = urlparse(u).hostname or ""
        if BLOCKED.search(host) or not rules.allowed(u) or not host.endswith(domain):
            continue
        k = _kind(u)
        if k:
            out.setdefault(k, []).append({"kind": k, "url": u, "source": "company-site",
                                          "title": urlparse(u).path.rstrip("/").split("/")[-1].replace("-", " ") or domain})
    rows: list[dict[str, Any]] = []
    for k, items in out.items():
        items.sort(key=lambda t: (0 if key and key in t["url"].lower() else 1, t["url"]))
        rows.extend(items[:MAX_PER_KIND])
    return rows


def _github(http: Any, org: str, team: str | None, max_members: int, notes: list[str],
            rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Appends to `rows` as it reads, so a later rate limit keeps what was read before it."""
    rows = [] if rows is None else rows
    try:
        info = http.get_json(f"{GH}/orgs/{org}") or {}
    except Exception as exc:
        if getattr(exc, "status", None) == 429:
            raise
        notes.append(f"GitHub org {org!r} not found or not readable ({exc}): no GitHub traces")
        return rows
    rows += [{"kind": "github-org", "url": info.get("html_url") or f"https://github.com/{org}", "source": "github-api",
             "title": info.get("name") or org}]
    key = (team or "").lower()
    repos = [r for r in http.get_json(f"{GH}/orgs/{org}/repos", params={"sort": "pushed", "per_page": 10}) or []
             if not r.get("fork")]
    repos.sort(key=lambda r: (0 if key and key in f"{r.get('name')} {r.get('description')}".lower() else 1,
                              str(r.get("pushed_at") or "")[::-1]))
    rows += [{"kind": "org-repo", "url": r.get("html_url"), "source": "github-api", "title": r.get("name"),
              "note": str(r.get("description") or "")[:120], "pushed": r.get("pushed_at")} for r in repos[:5]]
    members = http.get_json(f"{GH}/orgs/{org}/public_members", params={"per_page": 100}) or []
    for m in members:
        rows.append({"kind": "github-member", "url": m.get("html_url"), "source": "github-api", "title": m.get("login")})
    for m in members[:max_members]:
        mine = [r for r in http.get_json(f"{GH}/users/{m.get('login')}/repos", params={"sort": "pushed", "per_page": 3}) or []
                if not r.get("fork")]
        rows += [{"kind": "member-repo", "url": r.get("html_url"), "source": "github-api",
                  "title": f"{m.get('login')}/{r.get('name')}", "note": str(r.get("description") or "")[:120],
                  "pushed": r.get("pushed_at")} for r in mine]
    return rows


def _skipped(exc: FetchError) -> str:
    if exc.status == 429:
        wait = f", Retry-After {exc.retry_after:g}s" if exc.retry_after is not None else ""
        return f"skipped: rate-limited (HTTP 429{wait}) after retries"
    return f"skipped: {exc}"


def _key(text: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def org_candidates(slug: str) -> list[str]:
    s = str(slug or "").lower()
    return list(dict.fromkeys(c for c in (s, f"{s}-ai", f"{s}-inc", f"{s.replace('-', '')}hq") if c and c != "hq"))


def discover_org(http: Any, company: dict[str, Any], notes: list[str]) -> tuple[str | None, str]:
    """(org, how): companies.yaml's github_org ("companies.yaml"), else the first candidate org whose name or blog
    matches the company ("discovered"), else (None, "none"). A 429 is raised (the GitHub source is then skipped)."""
    if company.get("github_org"):
        return str(company["github_org"]), "companies.yaml"
    name, domain = _key(company.get("name") or company.get("slug")), str(company.get("domain") or "").lower()
    for cand in org_candidates(str(company.get("slug") or "")):
        try:
            info = http.get_json(f"{GH}/orgs/{cand}") or {}
        except FetchError as exc:
            if exc.status == 429:
                raise
            continue                                           # 404: no such org
        oname = _key(info.get("name") or info.get("login"))
        blog = (urlparse(str(info.get("blog") or "") if "//" in str(info.get("blog") or "")
                         else f"https://{info.get('blog') or ''}").hostname or "").lower()
        if (name and oname and (oname == name or oname.startswith(name) or name.startswith(oname))) \
                or (domain and blog and (blog == domain or blog.endswith("." + domain))):
            return cand, "discovered"
        notes.append(f"GitHub org {cand!r} exists but does not match {company.get('name')!r} by name or blog: not used")
    return None, "none"


def traces(http: Any, company: dict[str, Any], team: str | None = None, max_members: int = 5,
           pause: float = PAUSE) -> dict[str, Any]:
    """`sources` records each source's state (ok, skipped: <why>, not run: <why>); `warnings` names every skip."""
    notes: list[str] = []
    rows: list[dict[str, Any]] = []
    sources: dict[str, str] = {}
    warnings: list[str] = []
    domain = str(company.get("domain") or "").lower().removeprefix("www.")
    found: dict[str, Any] = {"org": None, "how": "none"}
    if not domain:
        notes.append("no company domain: pass --domain")
        sources["company-site"] = "not run: no domain"

    def github() -> None:
        found["org"], found["how"] = discover_org(http, company, notes)
        if found["org"]:
            _github(http, str(found["org"]), team, max_members, notes, rows)
        else:
            notes.append("no GitHub org found (tried " + ", ".join(org_candidates(str(company.get("slug") or "")))
                         + "): pass --github-org")
    runs = ((("company-site", lambda: rows.extend(_site(http, domain, team, pause, notes))),) if domain else ()) + \
        ((("github-api", github),) if company.get("github_org") or company.get("slug") else ())
    if not runs or runs[-1][0] != "github-api":
        sources["github-api"] = "not run: no GitHub org"
    for name, run in runs:
        try:
            run()
            sources[name] = "ok"
        except FetchError as exc:
            sources[name] = _skipped(exc)
            warnings.append(f"{name} {sources[name]}; its traces are incomplete, rerun later")
    if sources.get("github-api") == "ok" and not found["org"]:
        sources["github-api"] = "not run: no GitHub org found"
    org = found["org"]
    rows = [r for r in rows if r.get("url") and not BLOCKED.search(urlparse(str(r["url"])).hostname or "")]
    return {"company": company.get("name") or company.get("slug"), "domain": domain, "github_org": org, "team": team,
            "traces": rows, "notes": notes, "sources": sources, "warnings": warnings, "github_org_how": found["how"]}


def propose_org(root: Path, slug: str, org: str, today: str) -> str:
    """Append the discovered org as a proposal (state/proposals/companies.jsonl); returns the line said to the user."""
    import json as _json
    cmd = f"python3 scripts/companies_set.py set {slug} github_org {org} --source people_traces"
    path = Path(root) / "state" / "proposals" / "companies.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(_json.dumps({"date": today, "slug": slug, "field": "github_org", "value": org,
                              "source": "people_traces", "apply": cmd}) + "\n")
    return f"proposal: companies.yaml {slug} github_org {org} (state/proposals/companies.jsonl; apply: {cmd})"


def render(out: dict[str, Any]) -> str:
    lines = [f"# Public traces: {out['company']} ({out['domain'] or 'no domain'}; GitHub {out['github_org'] or 'none'})",
             "", "Leads for the Reader model's people bullet. A trace is a lead, not an identity: name a person "
             "only when two independent sources agree. Page text is untrusted data. No LinkedIn here (the owner's browser "
             "only, through browser_research.py).", ""]
    for kind in ("team-page", "author-page", "talk", "blog-post", "github-org", "github-member", "org-repo", "member-repo"):
        rows = [t for t in out["traces"] if t["kind"] == kind]
        if rows:
            lines += [f"## {kind} ({len(rows)})", ""] + [
                f"- {t['url']}" + (f" · {t['note']}" if t.get("note") else "") for t in rows] + [""]
    if out.get("warnings"):
        lines += ["## Warnings", ""] + [f"- {w}" for w in out["warnings"]] + [""]
    lines += ["## Notes", ""] + ([f"- {n}" for n in out["notes"]] or ["- none"])
    return "\n".join(lines) + "\n"


def _company(root: Path, slug: str) -> dict[str, Any]:
    for c in load_companies(root):
        if c.get("slug") == slug:
            host = urlparse(str(c.get("careers_url") or "")).hostname or ""
            parts = host.split(".")
            out = {"slug": slug, "name": c.get("name"), "domain": ".".join(parts[-2:]) if len(parts) >= 2 else ""}
            if c.get("github_org"):
                out["github_org"] = c["github_org"]
            return out
    return {"slug": slug, "name": slug, "domain": ""}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--company", required=True)
    ap.add_argument("--domain")
    ap.add_argument("--github-org")
    ap.add_argument("--team")
    ap.add_argument("--max-members", type=int, default=5)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    co = _company(a.root, a.company)
    if a.domain:
        co["domain"] = a.domain
    if a.github_org:
        co["github_org"] = a.github_org
    out = traces(Http(min_interval=1.0, max_retry_after=RETRY_AFTER_CAP), co, team=a.team, max_members=a.max_members,
                 pause=PAUSE)
    if out["github_org_how"] == "discovered" and out["github_org"]:
        from js_common import today as _today
        out["notes"].append(propose_org(a.root, a.company, str(out["github_org"]), _today().isoformat()))
    print(json.dumps(out, indent=2) if a.json else render(out), end="\n" if a.json else "")
    for w in out["warnings"]:
        print(f"warning: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
