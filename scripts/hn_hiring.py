#!/usr/bin/env python3
"""Hacker News "Ask HN: Who is hiring?" -> candidate job records (source: hn).

Official HN Firebase API (keyless):
  GET https://hacker-news.firebaseio.com/v0/user/whoishiring.json   -> submitted story ids (newest first)
  GET https://hacker-news.firebaseio.com/v0/item/{id}.json          -> story (title, kids) / comment
  GET https://hacker-news.firebaseio.com/v0/jobstories.json          -> up to 200 job-story ids (YC companies' posts),
      read daily by collect_jobstories (the HN API's terms allow it)
Official HN Algolia search API (keyless; 10,000 requests/hour per IP, 1,000 hits max per query):
  GET https://hn.algolia.com/api/v1/search_by_date?query=Q&tags=story&numericFilters=created_at_i>T&hitsPerPage=N
      -> "hiring" stories posted BETWEEN the monthly threads. A fixed tuple of
      queries, one request each per run; only titles that announce hiring are kept (is_hiring_title), the
      whoishiring threads are dropped (collected above) and hits are deduped by objectID.
The newest submission titled "Ask HN: Who is hiring?" is the thread; its top-level comments are
the postings. Each comment header ("Company | Role | Location | ...") is split on "|" and the
fields classified (url, location, pay, employment type, visa, role). When the header names no
AI/ML role, a short body line such as "- Software Engineer (Applied AI/ML): <link>" supplies the
title and its own link. Sponsorship flags come from the whole comment: citizenship, clearance or "no
sponsorship" -> sponsorship "no"; "visa sponsorship", "we sponsor" -> "yes".

Only posts whose title matches the department title filter (fetch_ats.load_title_filter) are
kept unless --all. Records follow the job schema with id <company_slug>-hn-<comment id>; the
comment text is the JD snapshot, wrapped as untrusted. The thread is read monthly: state/hn_state.json
records the last thread and month processed. The Algolia collector writes ids <company_slug>-hn-<objectID>
with the story text as the untrusted JD snapshot, and keeps "algolia_since" (the newest created_at_i seen)
in the same state file; the next run asks only for newer posts, a first run looks back ALGOLIA_LOOKBACK_DAYS.

CLI:  python hn_hiring.py [--root R] [--all] [--limit N] [--dry-run]
      python hn_hiring.py --algolia [--root R] [--all] [--limit N] [--dry-run]   (only the Algolia collector;
      --limit caps the records kept)
"""
from __future__ import annotations

import argparse
import html as _html
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_ats import (Http, TitleFilter, iso_utc, load_title_filter, make_record,  # noqa: E402
                       norm_employment, parse_comp_text, remote_mode, upsert_jobs)
from js_common import JS_ROOT, html_to_text, normalize_company, slugify  # noqa: E402

HN_API = "https://hacker-news.firebaseio.com/v0"
THREAD_RE = re.compile(r"^Ask HN: Who is hiring\?", re.I)

_URL_FIELD = re.compile(r"^(?:https?://\S+|www\.\S+|[\w-]+(?:\.[\w-]+)*\.(?:com|ai|io|co|org|net|dev|app|us|pro|ch|so|xyz|tech)(?:/\S*)?)$", re.I)
_LOCATION_WORDS = re.compile(
    r"\b(?:remote|onsite|on-site|hybrid|in[- ]person|in[- ]office|worldwide|anywhere|usa|u\.s\.a?\.?|"
    r"uk|eu|emea|apac|europe|canada|germany|netherlands|london|nyc|new york|san francisco|sf|"
    r"bay area|seattle|boston|austin|berlin|amsterdam|paris|toronto)\b", re.I)
_CITY_REGION = re.compile(r"\b[A-Z][\w .'-]+,\s*(?:[A-Z]{2}\b|[A-Z][a-z]+)")
_EMPLOYMENT = re.compile(r"\b(?:full[- ]?time|part[- ]?time|contract(?:or)?|intern(?:ship)?|freelance)\b", re.I)
_PAY = re.compile(r"[$€£]\s?\d|\b\d+\s?[kK]\b")
_VISA_FIELD = re.compile(r"visa|sponsor|citizen|green card|clearance", re.I)
_ROLE_HINT = re.compile(r"\b(?:engineers?|engr|developers?|scientists?|swe|architects?|researchers?|"
                        r"designers?|managers?|leads?|roles|positions|staff|analysts?|pm)\b", re.I)
_VISA_NO = re.compile(
    r"\b(?:no|not|unable to|cannot|can't|won't|will not|do not|don't|does not|doesn't)\s+"
    r"(?:offer\s+|provide\s+|support\s+)?(?:visa\s+)?sponsor\w*"
    r"|\bU\.?S\.? citizen(?:s|ship)?\b|\bsecurity clearance\b|\bgreen card holders?\b", re.I)
_VISA_YES = re.compile(r"\bvisa sponsor(?:ship|s)?\b|\bsponsor(?:s|ing)? visas?\b|\bwill sponsor\b"
                       r"|\bh-?1b (?:sponsorship|transfers?) (?:available|welcome|ok)\b", re.I)
_ATS_HINT = re.compile(r"greenhouse\.io|lever\.co|ashbyhq\.com|ashby_jid|myworkdayjobs\.com|recruitee\.com|"
                       r"workable\.com|smartrecruiters\.com|jobvite\.com|bamboohr\.com|rippling\.com|"
                       r"/jobs?\b|/careers?\b|/apply\b", re.I)


# ------------------------------------------------------------------------------ fetching
def find_latest_thread(http: Any, scan: int = 6) -> dict[str, Any]:
    user = http.get_json(f"{HN_API}/user/whoishiring.json") or {}
    for item_id in (user.get("submitted") or [])[:scan]:
        item = http.get_json(f"{HN_API}/item/{item_id}.json") or {}
        if item.get("type") == "story" and THREAD_RE.match(item.get("title") or ""):
            return item
    raise LookupError("no 'Ask HN: Who is hiring?' thread among whoishiring's recent submissions")


def fetch_thread_comments(http: Any, story: dict[str, Any], limit: int | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for kid in (story.get("kids") or [])[: limit or None]:
        item = http.get_json(f"{HN_API}/item/{kid}.json")
        if not item or item.get("deleted") or item.get("dead") or not item.get("text"):
            continue
        out.append(item)
    return out


# ------------------------------------------------------------------------------ parsing
def _anchors_to_urls(text: str) -> str:
    """<a href="X">shown...</a> -> X (HN truncates the visible text; the href is complete)."""
    return re.sub(r'<a\s[^>]*href="([^"]+)"[^>]*>.*?</a>', lambda m: _html.unescape(m.group(1)), text, flags=re.S)


def _plain_lines(raw_html: str) -> list[str]:
    text = _anchors_to_urls(raw_html or "").replace("<p>", "\n")
    text = _html.unescape(re.sub(r"<[^>]+>", "", text))
    return [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]


def _urls(text: str) -> list[str]:
    return [u.rstrip(".,);") for u in re.findall(r"https?://[^\s<>\"')]+", text)]


def _clean_company(field: str) -> str:
    s = re.sub(r"https?://\S+", " ", field)
    s = re.sub(r"\(\s*(?:www\.)?[\w.-]+\.[a-z]{2,}[^)]*\)", " ", s)        # "(example.com)", "( https://x )"
    s = re.sub(r"\(\s*\)", " ", s).replace("*", " ")
    return re.sub(r"\s+", " ", s).strip(" -–|")


def _classify(field: str) -> str:
    if _URL_FIELD.match(field):
        return "url"
    if _VISA_FIELD.search(field):
        return "visa"
    if _PAY.search(field):
        return "pay"
    if _EMPLOYMENT.search(field) and len(field) < 60 and not _ROLE_HINT.search(_EMPLOYMENT.sub(" ", field)):
        return "employment"
    has_location_word = bool(_LOCATION_WORDS.search(field))
    if _ROLE_HINT.search(field) and not has_location_word:
        return "role"                     # "Senior SWE, AI Automation Engr" is not "City, ST"
    if has_location_word or _CITY_REGION.search(field):
        return "location"
    return "role"


def _role_line_title(line: str) -> tuple[str, str | None]:
    """'- Software Engineer (Applied AI/ML): https://x' -> ('Software Engineer (Applied AI/ML)', url)."""
    urls = _urls(line)
    s = re.sub(r"https?://\S+", " ", line)
    s = re.sub(r"^[\s\-*•·–]+", "", s)
    s = re.split(r"\s+[—–]\s+|:\s|\s-\s", s, maxsplit=1)[0]
    return s.strip(" :-–—"), (urls[0] if urls else None)


def _visa(lines: list[str]) -> tuple[str | None, str | None]:
    for ln in lines:
        if _VISA_NO.search(ln):
            return "no", ln[:300]
    for ln in lines:
        if _VISA_YES.search(ln):
            return "yes", ln[:300]
    return None, None


def _vendor(url: str | None) -> str:
    if not url:
        return "unknown"
    for needle, vendor in (("greenhouse.io", "greenhouse"), ("lever.co", "lever"), ("ashbyhq.com", "ashby"),
                           ("ashby_jid", "ashby"), ("myworkdayjobs.com", "workday")):
        if needle in url:
            return vendor
    return "other"


def parse_comment(item: dict[str, Any], now: datetime, title_filter: TitleFilter | None = None,
                  thread_id: int | None = None) -> dict[str, Any] | None:
    """One top-level comment -> job record, or None when it is not a job post."""
    raw = item.get("text") or ""
    lines = _plain_lines(raw)
    header = lines[0] if lines else ""
    if not header or re.match(r"^(?:location|remote|willing to relocate)\s*:", header, re.I):
        return None                                   # a candidate's post, not a job
    fields = [f.strip() for f in header.split("|") if f.strip()]
    company = _clean_company(fields[0]) if fields else ""
    if not company:
        return None
    kinds = [(f, _classify(f)) for f in fields[1:]]
    tf = title_filter or _default_filter()
    title, title_url, relevant = None, None, False
    for f, kind in kinds:
        if kind == "role" and tf.matches(f):
            title, relevant = f, True
            break
    if title is None:
        for ln in lines[1:]:
            if 0 < len(ln) <= 160:
                cand, url = _role_line_title(ln)
                if cand and len(cand) <= 90 and tf.matches(cand):
                    title, title_url, relevant = cand, url, True
                    break
    if title is None:
        roles = [f for f, kind in kinds if kind == "role" and _ROLE_HINT.search(f)]
        title = roles[0] if roles else "Multiple roles"
    location_fields = [f for f, kind in kinds if kind == "location"]
    all_urls = _urls("\n".join(lines))
    apply_url = title_url or next((u for u in all_urls if _ATS_HINT.search(u)), None) or (all_urls[0] if all_urls else None)
    visa, evidence = _visa(lines)
    employment = next((norm_employment(_EMPLOYMENT.search(f).group(0)) for f, kind in kinds if kind == "employment"), None)
    pay = next((parse_comp_text(f) for f, kind in kinds if kind == "pay"), (None, None, None))
    jd_text = html_to_text(_anchors_to_urls(raw))
    slug = slugify(normalize_company(company)) or slugify(company)       # the scouts' one derived slug
    rec = make_record(
        {"name": company, "slug": slug}, title=title, source="hn",
        source_url=f"https://news.ycombinator.com/item?id={item['id']}", apply_url=apply_url,
        ats_vendor=_vendor(apply_url), posted_at=item.get("time"),
        location=" | ".join(location_fields) or None,
        remote=remote_mode(*location_fields), comp=pay, employment_type=employment,
        jd_text=jd_text, now=now)
    rec["id"] = f"{slug}-hn-{item['id']}"
    rec["sponsorship"] = visa
    rec["sponsorship_evidence"] = evidence
    rec["hn_thread"] = thread_id if thread_id is not None else item.get("parent")
    rec["_relevant"] = relevant
    return rec


_DEFAULT_FILTER: TitleFilter | None = None


def _default_filter() -> TitleFilter:
    global _DEFAULT_FILTER
    if _DEFAULT_FILTER is None:
        from fetch_ats import default_title_filter
        _DEFAULT_FILTER = default_title_filter()
    return _DEFAULT_FILTER


def collect(http: Any, now: datetime, title_filter: TitleFilter | None = None, relevant_only: bool = True,
            limit: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    story = find_latest_thread(http)
    comments = fetch_thread_comments(http, story, limit=limit)
    parsed = [parse_comment(c, now, title_filter, thread_id=story["id"]) for c in comments]
    parsed = [p for p in parsed if p is not None]
    relevant = [p for p in parsed if p.get("_relevant")]
    records = relevant if relevant_only else parsed
    for r in records:
        r.pop("_relevant", None)
    info = {"thread_id": story["id"], "title": story.get("title"), "comments": len(comments),
            "jobs": len(parsed), "relevant": len(relevant)}
    return records, info


# ------------------------------------------------------------------------------ jobstories (YC job posts)
JOB_TITLE_RE = re.compile(r"^(?P<company>.+?)\s*(?:\((?P<batch>YC [^)]+)\))?\s+(?:is|are)\s+hiring(?:\s+(?:an?|for|the))?\s+"
                          r"(?P<role>.+?)\s*$", re.I)
JOBSTORIES_LIMIT = 60


def parse_job_story(item: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    """A job record from one HN job story ("Acme (YC W26) Is Hiring a Founding AI Engineer"), or None."""
    title = _html.unescape(str(item.get("title") or "")).strip()
    m = JOB_TITLE_RE.match(title)
    company, role = (m.group("company").strip(), m.group("role").strip()) if m else (title, title)
    if not company or not role:
        return None
    slug = slugify(normalize_company(company)) or slugify(company)
    hn_url = f"https://news.ycombinator.com/item?id={item['id']}"
    jd_text = html_to_text(_anchors_to_urls(str(item.get("text") or ""))) or title
    apply_url = str(item.get("url") or "") or hn_url
    rec = make_record({"name": company, "slug": slug}, title=role, source="hn", source_url=hn_url, apply_url=apply_url,
                      ats_vendor=_vendor(apply_url), posted_at=item.get("time"), location=None,
                      remote=remote_mode(jd_text), comp=parse_comp_text(jd_text), employment_type=None, jd_text=jd_text,
                      now=now)
    rec["id"] = f"{slug}-hnjob-{item['id']}"
    rec["yc_batch"] = m.group("batch") if m and m.group("batch") else None
    return rec


def collect_jobstories(http: Any, now: datetime, title_filter: TitleFilter | None = None, relevant_only: bool = True,
                       limit: int = JOBSTORIES_LIMIT) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """YC companies' job posts from the HN API, newest first; one item GET each, a bad item is skipped and counted."""
    ids = (http.get_json(f"{HN_API}/jobstories.json") or [])[:limit]
    tf = title_filter or _default_filter()
    records, errors = [], 0
    for item_id in ids:
        try:
            item = http.get_json(f"{HN_API}/item/{item_id}.json") or {}
        except Exception:
            errors += 1
            continue
        if item.get("type") != "job" or item.get("dead") or item.get("deleted"):
            continue
        rec = parse_job_story(item, now)
        if rec and (not relevant_only or tf.matches(rec["title"])):
            records.append(rec)
    return records, {"stories": len(ids), "matched": len(records), "errors": errors}


# ------------------------------------------------------------------------------ Algolia: hiring posts between threads
ALGOLIA_API = "https://hn.algolia.com/api/v1/search_by_date"
ALGOLIA_QUERIES = ("hiring", "we're hiring", "is hiring")
ALGOLIA_HITS = 1000             # the API's cap per query; one request per query per run, no paging
ALGOLIA_LOOKBACK_DAYS = 31      # a first run (no "algolia_since" yet) looks back one month

_ROLE_WORD = r"(?:engineers?|developers?|founding|cto|scientists?|researchers?|ml|ai|designers?|head of|lead)"
HIRING_TITLE_RE = re.compile(
    r"^\s*[\[(]hiring[\])]|^\s*hiring\s*:"                            # "[Hiring] ...", "Hiring: ..."
    # "Hiring a Founding CTO" (a role word within three words), not "Hiring Smart"
    r"|^\s*hiring\s+(?:an?\s+|our\s+(?:first\s+)?)?(?:[\w-]+\s+){0,3}" + _ROLE_WORD + r"\b"
    r"|^\s*we(?:'|\u2019)?re hiring\b|^\s*we are hiring\b"           # "We're hiring: ..."
    r"|^(?!(?:why|how|what|when|where|who|is|are|does|do|should|can)\b)[^?]+?\s(?:is|are)\s+(?:now\s+)?hiring\b",
    re.I)
_NOT_ANNOUNCEMENT = re.compile(r"^\s*(?:ask|tell|show|launch) hn\b|\?|\bwho is hiring\b", re.I)   # questions,
                                                                     # discussions, posts about the monthly thread
_AT_CLAUSE = re.compile(r"^(?P<role>.+?)\s+at\s+(?P<company>[^()\[\],|]+?)\s*(?:[(\[,|]|$)", re.I)


def is_hiring_title(title: str) -> bool:
    """True for a title that announces hiring ("Acme is hiring ...", "[Hiring] ..."), not a talk about hiring."""
    t = _html.unescape(title or "")
    return bool(HIRING_TITLE_RE.search(t)) and not _NOT_ANNOUNCEMENT.search(t) and not THREAD_RE.match(t)


def _algolia_company_role(title: str, url: str | None) -> tuple[str, str] | None:
    """'Acme (YC W26) is hiring a Founding AI Engineer' -> ('Acme', 'Founding AI Engineer'); '[Hiring] Senior AI
    Engineer at Northwind (Remote)' -> ('Northwind', 'Senior AI Engineer'); else the url's host names the company."""
    m = JOB_TITLE_RE.match(title)
    if m:
        return m.group("company").strip(), m.group("role").strip()
    rest = re.split(r"\bhiring\b", title, maxsplit=1, flags=re.I)[-1]
    rest = re.sub(r"^[\s\]):|\-\u2013]+(?:(?:an?|for|the)\s+)?", "", rest).strip()
    at = _AT_CLAUSE.match(rest)
    if at:
        return at.group("company").strip(), at.group("role").strip()
    host = (urlparse(url).hostname or "") if url else ""
    host = re.sub(r"^(?:www|jobs|careers)\.", "", host)
    if not host:
        return None
    return host.split(".")[0], (rest or "Multiple roles")


def parse_algolia_hit(hit: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    """One Algolia story hit -> job record, or None when the title names no company."""
    title = _html.unescape(str(hit.get("title") or "")).strip()
    url = str(hit.get("url") or "") or None
    pair = _algolia_company_role(title, url)
    if not pair or not pair[0] or not pair[1]:
        return None
    company, role = pair
    slug = slugify(normalize_company(company)) or slugify(company)
    oid = str(hit["objectID"])
    hn_url = f"https://news.ycombinator.com/item?id={oid}"
    jd_text = html_to_text(_anchors_to_urls(str(hit.get("story_text") or ""))) or title
    apply_url = url or hn_url
    rec = make_record({"name": company, "slug": slug}, title=role, source="hn", source_url=hn_url, apply_url=apply_url,
                      ats_vendor=_vendor(apply_url), posted_at=hit.get("created_at_i"), location=None,
                      remote=remote_mode(jd_text), comp=parse_comp_text(jd_text), employment_type=None, jd_text=jd_text,
                      now=now)
    rec["id"] = f"{slug}-hn-{oid}"
    return rec


def collect_algolia(http: Any, now: datetime, since: int, title_filter: TitleFilter | None = None,
                    relevant_only: bool = True, limit: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Hiring stories newer than `since` (unix seconds): one search_by_date GET per query, deduped by objectID."""
    tf = title_filter or _default_filter()
    seen: dict[str, dict[str, Any]] = {}
    raw = 0
    for query in ALGOLIA_QUERIES:
        params = {"query": query, "tags": "story", "numericFilters": f"created_at_i>{since}",
                  "hitsPerPage": ALGOLIA_HITS}
        hits = (http.get_json(ALGOLIA_API, params=params) or {}).get("hits") or []
        raw += len(hits)
        for hit in hits:
            if hit.get("objectID") is not None:
                seen.setdefault(str(hit["objectID"]), hit)
    newest = max([since] + [int(h.get("created_at_i") or 0) for h in seen.values()])
    hiring = [h for h in seen.values()
              if str(h.get("author") or "") != "whoishiring" and is_hiring_title(str(h.get("title") or ""))]
    records = []
    for hit in hiring:
        rec = parse_algolia_hit(hit, now)
        if rec and (not relevant_only or tf.matches(rec["title"])):
            records.append(rec)
    records = records[:limit] if limit else records
    return records, {"hits": raw, "unique": len(seen), "hiring": len(hiring), "matched": len(records),
                     "newest": newest}


# ------------------------------------------------------------------------------ state
def state_path(root: Path) -> Path:
    return Path(root) / "state" / "hn_state.json"


def load_state(root: Path) -> dict[str, Any]:
    p = state_path(root)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _update_state(root: Path, fields: dict[str, Any]) -> None:
    """Merge fields into the state file, so the monthly and the Algolia collectors keep each other's keys."""
    p = state_path(root)
    state = {**load_state(root), **fields}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def save_state(root: Path, thread_id: int, now: datetime) -> None:
    _update_state(root, {"last_thread_id": thread_id, "last_run_month": now.strftime("%Y-%m"),
                         "last_run": iso_utc(now)})


def algolia_since(root: Path, now: datetime) -> int:
    """The created_at_i floor for the next Algolia run: the newest seen, else ALGOLIA_LOOKBACK_DAYS back."""
    saved = load_state(root).get("algolia_since")
    if isinstance(saved, int) and saved > 0:
        return saved
    return int((now - timedelta(days=ALGOLIA_LOOKBACK_DAYS)).timestamp())


def save_algolia_state(root: Path, newest: int, now: datetime) -> None:
    _update_state(root, {"algolia_since": newest, "algolia_last_run": iso_utc(now)})


FILL_DAYS = 10          # the thread fills over its first days: re-read it while the month is young
FILL_EVERY_H = 6        # ... at most every 6 hours then
LATE_EVERY_D = 7        # ... and weekly after that (late posts still come in)


def due_this_month(root: Path, now: datetime) -> bool:
    """True when the monthly thread should be (re)read now. The first read of a month always runs; then, from the
    scout's lesson that the thread "fills over the day", again every 6 h through day 10 and
    weekly after, so posts added after the first read are not missed. Re-reads only re-sight known posts."""
    state = load_state(root)
    if state.get("last_run_month") != now.strftime("%Y-%m"):
        return True
    try:
        last = datetime.fromisoformat(str(state.get("last_run")).replace("Z", "+00:00"))
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age = now - last
    if now.day <= FILL_DAYS:
        return age >= timedelta(hours=FILL_EVERY_H)
    return age >= timedelta(days=LATE_EVERY_D)


# ------------------------------------------------------------------------------ CLI
def main(argv: list[str] | None = None, http: Any = None, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--all", action="store_true", help="keep non-AI/ML posts too")
    ap.add_argument("--limit", type=int, default=None, help="only the first N top-level comments")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--algolia", action="store_true",
                    help="only the Algolia collector: hiring stories posted between the monthly threads")
    args = ap.parse_args(argv)
    now = now or datetime.now(timezone.utc)
    http = http or Http(min_interval=0.05)
    if args.algolia:
        since = algolia_since(args.root, now)
        records, info = collect_algolia(http, now, since, load_title_filter(args.root), relevant_only=not args.all,
                                        limit=args.limit)
        print(f"HN Algolia since {since}: {info['hits']} hits, {info['unique']} unique, "
              f"{info['hiring']} hiring posts, {info['matched']} kept")
        plan = upsert_jobs(records, args.root, now, dry_run=args.dry_run)
        if not args.dry_run:
            save_algolia_state(args.root, info["newest"], now)
        mode = "dry-run (nothing written)" if args.dry_run else "written"
        print(f"{mode}: {len(plan['new'])} new, {len(plan['updated'])} re-sighted")
        return 0
    records, info = collect(http, now, load_title_filter(args.root), relevant_only=not args.all, limit=args.limit)
    print(f"{info['title']} (id {info['thread_id']}): {info['comments']} comments, "
          f"{info['jobs']} job posts, {info['relevant']} relevant")
    plan = upsert_jobs(records, args.root, now, dry_run=args.dry_run)
    if not args.dry_run:
        save_state(args.root, info["thread_id"], now)
    mode = "dry-run (nothing written)" if args.dry_run else "written"
    print(f"{mode}: {len(plan['new'])} new, {len(plan['updated'])} re-sighted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
