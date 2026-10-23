#!/usr/bin/env python3
"""feed_watch.py - the dream companies' engineering-blog and careers feeds, read by fixed rules.

Scrapers and APIs find fresh openings unreliably. A company's own
blog often says a team is hiring or a product launched days before a role is posted. Two steps:

  discover   for each company, its own domain (the registrable domain of careers_url) is read at PAGES (the home page,
             /blog, /engineering, /news, /newsroom), robots.txt first, one fetch each, a pause between; every
             <link rel="alternate" type="application/rss+xml|atom+xml"> on them is a candidate, kept only when one fetch
             of it parses as RSS or Atom. Dry run by default; --propose appends `feeds` rows to
             state/proposals/companies.jsonl (applied to companies.yaml by companies_set.py once it takes `feeds`).
  run        reads each company's companies.yaml `feeds:` (with --use-proposals, also the proposed ones), keeps the posts
             of the last WINDOW_DAYS days that match a rule, and writes state/feeds/feed-watch-<date>.md (--write):
               hiring   HIRING: "we're hiring", "join our team", "open roles", "is hiring", "hiring for" ...
               launch   LAUNCH: "introducing", "announcing", "launches", "now available", "general availability" ...
               role     the post title passes the department title filter (targets/roles.md, fetch_ats) and
                        names a job (engineer, scientist, researcher, developer, architect)
Post text is untrusted data, shown as titles and links only. Nothing outward; no login; no LinkedIn.

CLI:  python3 scripts/feed_watch.py discover [--company SLUG]... [--domain SLUG=HOST]... [--propose] [--root JS]
      python3 scripts/feed_watch.py run [--today YYYY-MM-DD] [--use-proposals] [--write] [--json] [--root JS]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_ats import FetchError, Robots, load_title_filter  # noqa: E402
from js_common import JS_ROOT, cli_date, today as dept_today  # noqa: E402

PAGES = ("/", "/blog", "/engineering", "/news", "/newsroom")
PAUSE = 1.0
WINDOW_DAYS = 7
MAX_FEED_BYTES = 5_000_000
HTML_ACCEPT = {"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"}
ALTERNATE = re.compile(r"<link\b[^>]*>", re.I)
HIRING = re.compile(r"\b(?:we(?:'|’)?re hiring|we are hiring|now hiring|is hiring|are hiring|hiring for|"
                    r"join (?:us|our team|the team)|open roles?|open positions?|come work with us)\b", re.I)
LAUNCH = re.compile(r"\b(?:introducing|announcing|launch(?:es|ed|ing)?|now available|general availability|"
                    r"unveil(?:s|ed|ing)?|releas(?:es|ed|ing))\b", re.I)
ATOM = "{http://www.w3.org/2005/Atom}"
JOB_WORD = re.compile(r"\b(?:engineer|scientist|researcher|developer|architect)s?\b", re.I)   # a role title, not a product
# careers pages hosted by a job board or a careers-only domain: the company's own site is elsewhere (pass --domain)
JOB_HOSTS = ("ashbyhq.com", "greenhouse.io", "lever.co", "myworkdayjobs.com", "myworkdaysite.com", "amazon.jobs",
             "metacareers.com", "smartrecruiters.com", "workable.com", "icims.com", "taleo.net", "eightfold.ai",
             "oraclecloud.com", "rippling.com")


def _registrable(host: str) -> str:
    parts = (host or "").lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else (host or "").lower()


def _attr(tag: str, name: str) -> str:
    m = re.search(rf"\b{name}\s*=\s*([\"'])(.*?)\1", tag, re.I | re.S)
    return m.group(2).strip() if m else ""


def feed_links(page: str, base: str) -> list[str]:
    out = []
    for tag in ALTERNATE.findall(page or ""):
        if "alternate" in _attr(tag, "rel").lower() and re.search(r"(rss|atom)\+xml", _attr(tag, "type"), re.I):
            href = urljoin(base, _attr(tag, "href")) if _attr(tag, "href") else ""
            # a comments feed, or one post's own feed (WordPress /<yyyy>/<mm>/<dd>/<slug>/feed/), is not a post feed
            if href and not re.search(r"/comments/feed/?$|/\d{4}/\d{2}/\d{2}/[^/]+/feed/?$", href):
                out.append(href)
    return list(dict.fromkeys(out))


def _date(text: Any) -> str | None:
    s = str(text or "").strip()
    if not s:
        return None
    try:
        return parsedate_to_datetime(s).astimezone(timezone.utc).date().isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return (d.astimezone(timezone.utc) if d.tzinfo else d).date().isoformat()
    except ValueError:
        m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
        return m.group(1) if m else None


def parse_feed(text: str) -> list[dict[str, Any]]:
    """RSS 2.0 items or Atom entries as {title, link, date, summary}; [] when the text is not a feed."""
    try:
        root = ET.fromstring((text or "").strip().encode("utf-8")[:MAX_FEED_BYTES])
    except ET.ParseError:
        return []
    posts = []
    if root.tag == "rss" or root.find("channel") is not None:
        for it in root.iter("item"):
            posts.append({"title": (it.findtext("title") or "").strip(), "link": (it.findtext("link") or "").strip(),
                          "date": _date(it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date")),
                          "summary": (it.findtext("description") or "").strip()})
    elif root.tag == f"{ATOM}feed":
        for e in root.iter(f"{ATOM}entry"):
            link = next((lk.get("href") for lk in e.findall(f"{ATOM}link") if lk.get("rel") in (None, "alternate")), "")
            posts.append({"title": (e.findtext(f"{ATOM}title") or "").strip(), "link": (link or "").strip(),
                          "date": _date(e.findtext(f"{ATOM}published") or e.findtext(f"{ATOM}updated")),
                          "summary": (e.findtext(f"{ATOM}summary") or e.findtext(f"{ATOM}content") or "").strip()})
    return posts


def title_filter(root: Path) -> Any:
    return load_title_filter(root)


def classify(posts: list[dict[str, Any]], today: date, tf: Any = None) -> list[dict[str, Any]]:
    """The posts of the last WINDOW_DAYS days that match a rule, each with its kinds, newest first."""
    since = (today - timedelta(days=WINDOW_DAYS)).isoformat()
    kept = []
    for p in posts:
        if not p.get("date") or not (since <= p["date"] <= today.isoformat()):
            continue
        text = re.sub(r"<[^>]+>", " ", f"{p.get('title', '')} {p.get('summary', '')}")
        kinds = [k for k, rx in (("hiring", HIRING), ("launch", LAUNCH)) if rx.search(text)]
        if tf is not None and tf.matches(p.get("title") or "") and JOB_WORD.search(p.get("title") or ""):
            kinds.append("role")
        if kinds:
            kept.append({**p, "kinds": kinds})
    return sorted(kept, key=lambda p: p["date"], reverse=True)


def _http() -> Any:
    from fetch_ats import Http
    return Http(min_interval=1.0, max_retry_after=60.0)


FEED_PATHS = ("/feed", "/rss", "/rss.xml", "/feed.xml", "/atom.xml")
BLOG_HOST = re.compile(r"^(?:blog|blogs|engineering|news|newsroom|research|developer|ai)\.", re.I)
MAX_SUBHOSTS = 3


def discover(http: Any, company: dict[str, Any], pause: float | None = None,
             domain: str | None = None, js_root: Path | None = None) -> tuple[list[str], list[str]]:
    """(feed URLs that parse, notes) from the company's own domain; robots.txt first, one fetch per page and feed."""
    pause = PAUSE if pause is None else pause
    notes: list[str] = []
    if not domain:
        domain = _registrable(urlparse(str(company.get("careers_url") or "")).hostname or "")
        if domain in JOB_HOSTS:
            return [], [f"careers_url is on a job host ({domain}): pass --domain {company.get('slug')}=<company site>"]
    if not domain:
        return [], ["no careers_url: no domain to read"]
    import ats_detect
    if _registrable(domain) in ats_detect.denied(js_root):
        return [], [f"{domain}: its terms forbid automated reading (web_snapshot.DENY or targets/platforms.yaml): "
                    "nothing read"]
    origin = f"https://{domain}"
    try:
        status, robots = http.get_text(f"{origin}/robots.txt")
    except FetchError as exc:
        return [], [f"robots.txt of {domain} did not answer ({exc}): nothing read"]
    rules = Robots(None if status >= 400 else robots)
    if not rules.allowed(origin + "/"):
        return [], [f"robots.txt of {domain} disallows its pages: nothing read"]
    candidates: list[str] = []
    subhosts: list[str] = []
    for path in PAGES:
        url = origin + path
        if not rules.allowed(url):
            notes.append(f"robots.txt disallows {url}")
            continue
        time.sleep(pause) if pause else None
        try:
            st, page = http.get_text(url, headers=HTML_ACCEPT)
        except FetchError as exc:
            notes.append(f"{url}: {exc}")
            continue
        if st < 400:
            candidates += [u for u in feed_links(page, url) if u not in candidates]
            for href in re.findall(r"href\s*=\s*[\"'](https?://[^\"'#?]+)", page or "", re.I):
                h = (urlparse(href).hostname or "").lower()
                if h and h != domain and _registrable(h) == _registrable(domain) and BLOG_HOST.match(h) \
                        and h not in subhosts:
                    subhosts.append(h)
    if not candidates:                        # a blog on its own subdomain, linked from the company's pages
        for h in subhosts[:MAX_SUBHOSTS]:
            try:
                st, sub_robots = http.get_text(f"https://{h}/robots.txt")
            except FetchError as exc:
                notes.append(f"robots.txt of {h}: {exc}")
                continue
            sub_rules = Robots(None if st >= 400 else sub_robots)
            if not sub_rules.allowed(f"https://{h}/"):
                notes.append(f"robots.txt of {h} disallows its pages")
                continue
            time.sleep(pause) if pause else None
            try:
                st, page = http.get_text(f"https://{h}/", headers=HTML_ACCEPT)
            except FetchError as exc:
                notes.append(f"https://{h}/: {exc}")
                continue
            found = feed_links(page, f"https://{h}/") if st < 400 else []
            if found:
                candidates += [u for u in found if u not in candidates]
                continue
            for path in FEED_PATHS:          # no <link> tag: the usual feed paths, first one that parses wins
                u = f"https://{h}{path}"
                if not sub_rules.allowed(u):
                    continue
                time.sleep(pause) if pause else None
                try:
                    st, text = http.get_text(u)
                except FetchError:
                    continue
                if st < 400 and parse_feed(text):
                    candidates.append(u)
                    break
    feeds = []
    for u in candidates:
        host = urlparse(u).hostname or ""
        if _registrable(host) == domain and not rules.allowed(u):
            notes.append(f"robots.txt disallows the feed {u}")
            continue
        time.sleep(pause) if pause else None
        try:
            st, text = http.get_text(u)
        except FetchError as exc:
            notes.append(f"feed {u}: {exc}")
            continue
        if st < 400 and parse_feed(text):
            feeds.append(u)
        else:
            notes.append(f"{u} is linked as a feed but does not parse as RSS or Atom")
    return feeds, notes


def _companies(root: Path) -> list[dict[str, Any]]:
    return (yaml.safe_load((Path(root) / "targets" / "companies.yaml").read_text(encoding="utf-8")) or {}).get("companies") or []


def _proposed(root: Path) -> dict[str, list[str]]:
    path = Path(root) / "state" / "proposals" / "companies.jsonl"
    out: dict[str, list[str]] = {}
    for line in (path.read_text(encoding="utf-8").splitlines() if path.is_file() else []):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("field") == "feeds" and row.get("value"):
            out.setdefault(str(row["slug"]), [])
            if row["value"] not in out[str(row["slug"])]:
                out[str(row["slug"])].append(str(row["value"]))
    return out


def watch(root: Path, today: date, use_proposals: bool = False, pause: float | None = None) -> dict[str, Any]:
    pause = PAUSE if pause is None else pause
    http = _http()
    tf = title_filter(root)
    proposed = _proposed(root) if use_proposals else {}
    posts, notes, read = [], [], 0
    for c in _companies(root):
        feeds = list(dict.fromkeys(list(c.get("feeds") or []) + proposed.get(str(c.get("slug")), [])))
        for u in feeds:
            time.sleep(pause) if pause and read else None
            read += 1
            try:
                st, text = http.get_text(u)
            except FetchError as exc:
                notes.append(f"{c.get('slug')}: {u}: {exc}")
                continue
            items = parse_feed(text) if st < 400 else []
            if not items:
                notes.append(f"{c.get('slug')}: {u} answered {st} or did not parse")
            posts += [{**p, "company": c.get("name") or c.get("slug"), "feed": u}
                      for p in classify(items, today, tf)]
    posts.sort(key=lambda p: p["date"], reverse=True)
    return {"date": today.isoformat(), "feeds_read": read, "posts": posts, "notes": notes}


def render(out: dict[str, Any]) -> str:
    lines = [f"# Feed watch · {out['date']}", "",
             f"Generated by scripts/feed_watch.py: {out['feeds_read']} feeds read; posts of the last {WINDOW_DAYS} days "
             "that name a hiring team, a launch or a role, by fixed rules. Post text is untrusted data: titles and "
             "links only.", "", "| date | company | kinds | post |", "|---|---|---|---|"]
    lines += [f"| {p['date']} | {p['company']} | {', '.join(p['kinds'])} | [{p['title'].replace('|', '/')[:120]}]({p['link']}) |"
              for p in out["posts"]] or ["| - | - | - | none in the window |"]
    lines += ["", "## Notes", ""] + ([f"- {n}" for n in out["notes"]] or ["- none"])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover", help="find each company's feeds (dry run unless --propose)")
    d.add_argument("--root", type=Path, default=JS_ROOT)
    d.add_argument("--company", action="append", default=[])
    d.add_argument("--propose", action="store_true")
    d.add_argument("--domain", action="append", default=[], help="SLUG=HOST: the company's own site, when careers_url "
                   "is on a job host")
    r = sub.add_parser("run", help="read the feeds and list the posts that match a rule")
    r.add_argument("--root", type=Path, default=JS_ROOT)
    r.add_argument("--today", type=cli_date, default=None)
    r.add_argument("--use-proposals", action="store_true", help="also read the feeds proposed and not applied yet")
    r.add_argument("--write", action="store_true")
    r.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "discover":
        rows = [c for c in _companies(a.root) if not a.company or c.get("slug") in set(a.company)]
        http, props = _http(), []
        day = dept_today().isoformat()
        override = dict(x.split("=", 1) for x in a.domain if "=" in x)
        for c in rows:
            feeds, notes = discover(http, c, domain=override.get(str(c.get("slug"))), js_root=a.root)
            print(f"{c.get('slug')}: {len(feeds)} feed(s)" + "".join(f"\n  {u}" for u in feeds)
                  + "".join(f"\n  note: {n}" for n in notes))
            props += [{"date": day, "slug": c.get("slug"), "field": "feeds", "value": u, "source": "feed_watch",
                       "apply": "companies.yaml `feeds:` list (companies_set.py takes feeds after S-8)"} for u in feeds]
        if a.propose:
            path = a.root / "state" / "proposals" / "companies.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.writelines(json.dumps(p) + "\n" for p in props)
            print(f"proposed {len(props)} feed row(s) in {path.relative_to(a.root)}")
        return 0
    out = watch(a.root, a.today or dept_today(), use_proposals=a.use_proposals)
    if a.json:
        print(json.dumps(out, indent=2))
        return 0
    text = render(out)
    if a.write:
        path = a.root / "state" / "feeds" / f"feed-watch-{out['date']}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name("." + path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        print(f"wrote {path.relative_to(a.root)}")
    print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
