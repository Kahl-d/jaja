#!/usr/bin/env python3
"""Fetch postings from public ATS job boards and normalize them to job records (design §4, §5.1).

Keyless public endpoints:
  greenhouse  GET  https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
  lever       GET  https://api.lever.co/v0/postings/{site}?mode=json
  ashby       GET  https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true
  workable    GET  https://apply.workable.com/api/v1/widget/accounts/{account}?details=true
  smartrecruiters GET https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100&offset=N, then
              /postings/{id} for each title-matched posting (the list carries no description)
  rippling    GET  https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs, then /jobs/{uuid} per title match
  gem         GET  https://api.gem.com/job_board/v0/{vanity_path}/job_posts/ (descriptions included)
  pinpoint    GET  https://{subdomain}.pinpointhq.com/postings.json (descriptions and pay included; no posting date)
  teamtailor  GET  https://{careers host or subdomain.teamtailor.com}/jobs.rss?per_page=100&offset=N (RSS 2.0)
  recruitee   GET  https://{company}.recruitee.com/api/offers/ (descriptions and salary included)
  jibe        GET  https://{host}/api/jobs?country=<country>&limit=100&page=N (Jibe career sites over iCIMS and
              others; the host's robots.txt crawl-delay is honoured between pages); the job page is
              https://{host}/jobs/{slug}; `country` in the entry, default policy.search.country
  sitemap     GET  https://{host}/robots.txt, then the sitemap (board_token 'host[/path.xml]', default /sitemap.xml;
              a sitemap index is followed to its same-host children), for careers sites with no board API
              (for example TalentBrew over Avature, or SuccessFactors career sites). Job pages are the
              same-host URLs whose path matches `job_url_pattern` (default a /job/ or /jobs/ segment); only a new
              page whose URL words pass the title filter is fetched, one at a time with a pause (robots.txt
              crawl-delay, at least 5 s), at most 40 a run, and read from its schema.org JobPosting (JSON-LD or
              microdata, or a page's embedded router data). A posting already in jobs/ is re-sighted
              without fetching its page. Options per entry: `url_titles: false` when job URLs carry no title words
              (some large employers), so every new page is read (the cap still holds); a fetched page's title is remembered in
              state/sitemap/<slug>.json, so a page that did not match is never fetched again
  amazon      GET  https://www.amazon.jobs/en/search.json?base_query=<q>&loc_query=<loc>&result_limit=100&offset=N
              (the public search JSON behind amazon.jobs; robots.txt disallows only /internal); `search_queries:`
              [{q, loc}] in the entry (default: policy.search.amazon_queries) and `countries:` (default
              policy.search.amazon_countries: the search's location is loose); a search is never the whole board, so nothing is closed from it
  workday     POST https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
              body {"appliedFacets":{},"limit":20,"offset":N,"searchText":""}; at most 20 pages;
              `total` is only reported on the first page (later pages say 0). One detail call
              GET .../wday/cxs/{tenant}/{site}{externalPath} per posting whose title matches.

Inputs: targets/companies.yaml (`companies:` list of {name, slug, tier, ats_vendor, board_token,
careers_url, sponsorship_stance, h1b_approvals_recent, notes}; optional `search_text` for Workday);
absent file = empty watchlist. Title filter: targets/roles.md sections whose heading mentions
"title" (include) or "exclude ... title" (exclude), one pattern per bullet (plain phrase, or a
regex in backticks); without them the patterns in policy.title_filter (include, exclude) apply.

Output: jobs/<id>.md (frontmatter per schemas/job.yaml, body = JD snapshot wrapped in
<untrusted_jd>). id = <company_slug>-<req_id> or <company_slug>-<sha1(url)[:8]>; postings that
share a req id get a -<sha1(url)[:6]> suffix. A re-sighted posting (matched by source_url) only
updates fetch-owned fields and last_seen; fields written by later stages are never touched.

Records in memory are dicts of frontmatter fields plus private keys: `_body` (markdown body) and
`_jd_text` (plain JD text used by the gates). Private keys are never written to frontmatter.

CLI:  python fetch_ats.py [--root R] [--company SLUG ...] [--tier TIER ...] [--board vendor:token[:Name] ...]
      (--tier dream|strong|good|fallback, repeatable: only watchlist companies of those tiers; with --company, both
      filters apply: the fresh dream sweep is one short command.)
                          [--all-titles] [--max-pages N] [--dry-run] [--workers N]

"""
from __future__ import annotations

import argparse
import html as _html
import json
import re
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from http.client import HTTPException as _HTTPException
from pathlib import Path
from html.parser import HTMLParser
from typing import Any, Iterable, Mapping
from urllib.parse import quote, unquote, urlparse
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from js_common import (JS_ROOT, fm, html_to_text, iter_records, short_hash, slugify,  # noqa: E402
                       text_hash, wrap_untrusted)
import agency_config as cfg  # noqa: E402

JD_LIMIT = 30_000          # keep the whole JD: visa lines often sit at the very end
USER_AGENT = "jaja/1.0 (personal job search; python-requests)"
DEFAULT_COUNTRY = str(cfg.get("policy.search.country", "United States"))   # Jibe sites: the country searched
SUPPORTED_VENDORS = ("greenhouse", "lever", "ashby", "workday", "jibe", "sitemap", "amazon", "workable", "smartrecruiters", "rippling", "gem", "pinpoint", "teamtailor", "recruitee", "oracle", "eightfold")

JOB_FIELDS = [
    "id", "company", "company_slug", "title", "req_id", "source", "source_url", "apply_url",
    "ats_vendor", "posted_at", "first_seen", "last_seen", "location", "remote", "comp_min",
    "comp_max", "comp_currency", "employment_type", "seniority_est", "lens", "sponsorship",
    "sponsorship_evidence", "h1b_approvals_3y", "median_lca_wage", "wage_level_est", "jd_hash",
    "ghost_score", "freshness_hours", "fit_score", "fit_verdict", "status", "application", "tags",
]
# Fields the fetcher owns; everything else belongs to later stages and survives a re-sighting.
FETCH_OWNED = ("company", "company_slug", "title", "req_id", "source", "source_url", "apply_url",
               "ats_vendor", "posted_at", "location", "remote", "comp_min", "comp_max",
               "comp_currency", "employment_type", "seniority_est", "freshness_hours")
OPEN_STATUSES = ("discovered", "fit_checked", "queued", None, "")


# =============================================================================== HTTP
class FetchError(RuntimeError):
    """A request failed after retries (HTTP error, timeout, bad JSON). `status` is the last HTTP status (None for a
    network failure); `retry_after` the seconds a 429 asked for (None when it gave none)."""

    def __init__(self, msg: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(msg)
        self.status, self.retry_after = status, retry_after


class WallClockLimit(FetchError):
    """One request ran past Http.max_seconds of wall clock (a server trickling bytes): failed at once, never retried."""


class DeadlineReached(FetchError):
    """The run's (or this board's) fetch deadline passed: the request is not sent, and fetch_company turns it into a cut
    board left for the next run, never a board error."""


def _close(resp: Any) -> None:
    getattr(resp, "close", lambda: None)()


def _retry_after(value: Any) -> float | None:
    """Retry-After as seconds: a number, or an HTTP date (RFC 9110 10.2.3). None when absent or unreadable."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        return max(0.0, parsedate_to_datetime(text).timestamp() - time.time())
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


class Http:
    """requests wrapper: timeouts, retry with backoff on 429/5xx, a small per-host delay. Safe to share between the
    board threads of fetch_boards: the per-host delay is kept under a lock, and a thread other than the main one gets a
    session of its own (same headers) when this wrapper made its session (an injected session is shared as given)."""

    def __init__(self, session: Any = None, timeout: float = 30.0, retries: int = 2,
                 backoff: float = 1.5, min_interval: float = 0.25, user_agent: str = USER_AGENT,
                 max_retry_after: float = 0.0, max_seconds: float = 120.0, max_bytes: int = 50_000_000,
                 clock: Any = time.monotonic):
        import requests  # local import keeps the module importable without network deps
        import urllib3
        self._requests = requests
        # a connection dropped inside a streamed body: requests' wrappers and urllib3's own (read1 below raises these)
        self._dropped = (requests.RequestException, urllib3.exceptions.HTTPError, _HTTPException)
        self.session = session or requests.Session()
        # gzip and deflate only: some boards answer zstd when it is offered, and urllib3 could not decode it
        self.session.headers.update({"User-Agent": user_agent, "Accept": "application/json",
                                     "Accept-Encoding": "gzip, deflate"})
        self.timeout, self.retries, self.backoff, self.min_interval = timeout, retries, backoff, min_interval
        # > 0: a 429 waits what its Retry-After asks, capped here (people_traces); 0 keeps the plain backoff (boards)
        self.max_retry_after = max_retry_after
        # a total wall-clock limit per request (timeout is per read, so a server trickling bytes never times out),
        # and a byte cap on the body
        self.max_seconds, self.max_bytes, self._clock = max_seconds, max_bytes, clock
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self._own_session, self._local = session is None, threading.local()

    def _session(self) -> Any:
        if not self._own_session or threading.current_thread() is threading.main_thread():
            return self.session
        mine = getattr(self._local, "session", None)
        if mine is None:
            mine = self._local.session = self._requests.Session()
            mine.headers.update(self.session.headers)
        return mine

    def _throttle(self, url: str) -> None:
        """At most one request per min_interval to a host, whichever thread sends it: each request takes the next free
        slot under the lock and sleeps outside it."""
        host = urlparse(url).netloc
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._last.get(host, float("-inf")) + self.min_interval)
            self._last[host] = slot
        if slot > now:
            time.sleep(slot - now)

    def _request(self, method: str, url: str, keep_4xx: bool = False, **kw: Any) -> Any:
        last: Exception | None = None
        status: int | None = None
        asked: float | None = None
        for attempt in range(self.retries + 1):
            if past_deadline():                    # past the run deadline nothing is sent
                raise DeadlineReached(f"{method} {url}: the run deadline passed (not sent)")
            self._throttle(url)
            asked = None
            guard = "stream" not in kw             # download_to streams to disk itself
            start = self._clock()
            try:                                   # the body is read inside the retry (a drop mid-body retries)
                resp = self._session().request(method, url, timeout=self.timeout, **({"stream": True} | kw if guard else kw))
                status = resp.status_code
                if status in (429, 500, 502, 503, 504):
                    last = FetchError(f"{method} {url}: HTTP {status}", status)
                    if status == 429:
                        asked = _retry_after((resp.headers or {}).get("Retry-After"))
                    _close(resp)
                elif status >= 400 and not keep_4xx:
                    _close(resp)
                    raise FetchError(f"{method} {url}: HTTP {status}", status)
                else:
                    return self._read_body(resp, method, url, start) if guard else resp
            except self._dropped as exc:
                last, status = exc, None
            if attempt < self.retries:
                wait = (min(asked, self.max_retry_after) if asked is not None and self.max_retry_after > 0
                        else self.backoff * (2 ** attempt))
                left = time_left()
                if left is not None and left < wait:   # the wait would end past the deadline: stop now, not after it
                    raise DeadlineReached(f"{method} {url}: the run deadline comes before the retry ({last})")
                time.sleep(wait)
        raise FetchError(f"{method} {url}: {last}", status, asked)

    def _chunks(self, resp: Any) -> Iterable[bytes]:
        """The body as it arrives. urllib3's read1 returns what one socket read gave, so a trickle reaches the clock check
        byte by byte; iter_content(None) would not (for a body with a Content-Length it reads to the end in one call)."""
        read1 = getattr(getattr(resp, "raw", None), "read1", None)
        if not callable(read1):
            yield from resp.iter_content(chunk_size=1 << 10)
            return
        while True:
            data = read1(1 << 16, decode_content=True)
            if not data:
                return
            yield data

    def _read_body(self, resp: Any, method: str, url: str, start: float) -> Any:
        """The streamed body read whole, failing (no retry) once the request has run max_seconds of wall clock or the
        body passes max_bytes."""
        if not hasattr(resp, "iter_content"):
            return resp
        chunks, size = [], 0
        try:
            for chunk in self._chunks(resp):
                chunks.append(chunk)
                size += len(chunk)
                if self.max_bytes and size > self.max_bytes:
                    raise FetchError(f"{method} {url}: body over {self.max_bytes} bytes", getattr(resp, "status_code", None))
                if self.max_seconds and self._clock() - start > self.max_seconds:
                    raise WallClockLimit(f"{method} {url}: over the {self.max_seconds:g} s wall-clock limit "
                                         "(a trickling server)", getattr(resp, "status_code", None))
        except BaseException:
            _close(resp)
            raise
        resp._content, resp._content_consumed = b"".join(chunks), True
        return resp

    def get_json(self, url: str, params: dict | None = None) -> Any:
        resp = self._request("GET", url, params=params)
        try:
            return resp.json()
        except ValueError as exc:
            raise FetchError(f"GET {url}: not JSON ({exc})") from exc

    def get_text(self, url: str, headers: dict | None = None) -> tuple[int, str]:
        """(status, text). A 4xx is returned, not raised (robots.txt: 4xx means no rules); 429, 5xx and network
        failures retry, then raise. UTF-8 unless the response names its charset."""
        resp = self._request("GET", url, keep_4xx=True, headers=headers)
        if resp.status_code >= 400:
            return resp.status_code, ""
        ctype = str(resp.headers.get("Content-Type") or "")
        m = re.search(r"charset=([\w.-]+)", ctype, re.I)
        try:
            return resp.status_code, resp.content.decode(m.group(1) if m else "utf-8", "replace")
        except LookupError:
            return resp.status_code, resp.content.decode("utf-8", "replace")

    def get_page(self, url: str, headers: dict | None = None) -> tuple[int, str, str]:
        """(status, text, final URL after redirects): get_text plus where the page landed (ats_detect.py)."""
        resp = self._request("GET", url, keep_4xx=True, headers=headers)
        final = str(getattr(resp, "url", "") or url)
        if resp.status_code >= 400:
            return resp.status_code, "", final
        ctype = str(resp.headers.get("Content-Type") or "")
        m = re.search(r"charset=([\w.-]+)", ctype, re.I)
        try:
            return resp.status_code, resp.content.decode(m.group(1) if m else "utf-8", "replace"), final
        except LookupError:
            return resp.status_code, resp.content.decode("utf-8", "replace"), final

    def post_json(self, url: str, payload: dict) -> Any:
        resp = self._request("POST", url, json=payload)
        try:
            return resp.json()
        except ValueError as exc:
            raise FetchError(f"POST {url}: not JSON ({exc})") from exc

    def download_to(self, url: str, dest: Path, params: dict | None = None) -> Path:
        """Stream a (large) file to dest atomically."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        resp = self._request("GET", url, params=params, stream=True)
        with tmp.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                if chunk:
                    fh.write(chunk)
        tmp.replace(dest)
        return dest


# =============================================================================== time
def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: Any) -> datetime | None:
    """epoch s/ms, ISO timestamps (Z, offsets, fractions) or YYYY-MM-DD (midnight UTC)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        secs = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(secs, tz=timezone.utc)
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def posted_value(value: Any) -> str | None:
    """Normalize a vendor posting date: date-only stays YYYY-MM-DD, timestamps become UTC Z."""
    if value is None or value == "":
        return None
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        return value.strip()
    dt = parse_ts(value)
    return iso_utc(dt) if dt else None


def freshness_hours(posted: Any, now: datetime) -> int | None:
    dt = parse_ts(posted)
    if dt is None:
        return None
    return max(0, int((now - dt).total_seconds() // 3600))


# =============================================================================== text helpers
def join_locations(parts: Iterable[Any]) -> str | None:
    seen: list[str] = []
    for p in parts:
        s = re.sub(r"\s+", " ", str(p or "")).strip()
        if s and s not in seen:
            seen.append(s)
    return " | ".join(seen) or None


def remote_mode(*signals: Any) -> str:
    """remote | hybrid | onsite | unknown from free-text signals (workplace fields, locations)."""
    text = " ".join(str(s) for s in signals if s).lower()
    if re.search(r"\bremote\b", text) and not re.search(r"\b(?:no|not) remote\b", text):
        return "remote"
    if "hybrid" in text:
        return "hybrid"
    if re.search(r"\bon-?site\b|\bin[- ](?:office|person)\b", text):
        return "onsite"
    return "unknown"


def norm_employment(value: Any) -> str | None:
    t = str(value or "").lower().replace("_", " ")
    if not t.strip():
        return None
    if "intern" in t:
        return "intern"
    if "contract" in t:
        return "contract"
    if "part" in t:
        return "part-time"
    if "temp" in t:
        return "temporary"
    if "full" in t or "regular" in t or "permanent" in t:
        return "full-time"
    return t.strip()


_CURRENCY_SYMBOL = {"$": "USD", "£": "GBP", "€": "EUR"}
_AMOUNT = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*([kK])?"
_COMP_RE = re.compile(
    r"(?P<s1>[$£€])?\s*" + _AMOUNT.replace("(", "(?P<a1>", 1).replace("([kK])", "(?P<k1>[kK])") +
    r"\s*(?P<c1>USD|GBP|EUR|CAD)?\s*(?:-|–|—|to)\s*(?P<s2>[$£€])?\s*" +
    _AMOUNT.replace("(", "(?P<a2>", 1).replace("([kK])", "(?P<k2>[kK])") +
    r"\s*(?P<c2>USD|GBP|EUR|CAD)?")
_INTERVAL_RE = re.compile(r"^\s*(?:[A-Z]{3}\s*)?(?:/|per\s+|an?\s+)?(?:hour|hr|month|mo|week|day)\b|^\s*(?:hourly|monthly|weekly|daily)\b", re.I)


def _amount(num: str, k: str | None) -> float:
    value = float(num.replace(",", ""))
    return value * 1000 if k else value


def parse_comp_text(text: str) -> tuple[int | None, int | None, str | None]:
    """First plausible annual salary range in free text -> (min, max, currency)."""
    for m in _COMP_RE.finditer(str(text or "")):
        sym = m.group("s1") or m.group("s2")
        code = m.group("c1") or m.group("c2")
        if not sym and not code:
            continue
        if _INTERVAL_RE.match(text[m.end(): m.end() + 24]):
            continue
        lo = _amount(m.group("a1"), m.group("k1"))
        hi = _amount(m.group("a2"), m.group("k2"))
        if m.group("k2") and not m.group("k1") and lo < 1000:
            lo *= 1000
        if not (20_000 <= lo <= hi <= 5_000_000):
            continue
        currency = code or _CURRENCY_SYMBOL.get(sym or "", None)
        return int(round(lo)), int(round(hi)), currency
    return None, None, None


def estimate_seniority(title: str) -> str:
    t = re.sub(r"\bmember of (?:the )?technical staff\b", " ", str(title or "").lower())
    if re.search(r"\b(?:intern|internship|student|co-?op)\b", t):
        return "intern"
    if re.search(r"\bnew (?:college )?grad(?:uate)?s?\b|\buniversity grad|\bearly career\b|\bentry[- ]level\b", t):
        return "new-grad"
    if re.search(r"\b(?:vp|vice president|chief|head of)\b", t):
        return "executive"
    if re.search(r"\bdirector\b", t):
        return "director"
    if re.search(r"\bmanager\b|\bmgr\b", t):
        return "manager"
    if re.search(r"\b(?:principal|distinguished)\b", t):
        return "principal"
    if re.search(r"\bstaff\b", t):
        return "staff"
    if re.search(r"\blead\b", t):
        return "lead"
    if re.search(r"\b(?:senior|sr)\b", t):
        return "senior"
    if re.search(r"\b(?:junior|jr)\b|\b(?:engineer|scientist|developer)\s+i\b(?![/\w])", t):
        return "junior"
    if re.search(r"\b(?:ii|iii|mid[- ]level)\b", t):
        return "mid"
    return "unspecified"


_REQ_IN_TITLE = re.compile(r"\((R\d{3,}|JR\d{3,}|REQ[-_ ]?\d{3,})\)\s*$", re.I)


def req_from_title(title: str) -> str | None:
    m = _REQ_IN_TITLE.search(str(title or ""))
    return m.group(1).upper() if m else None


# =============================================================================== roles.md / filters
ROLE_WORDS = r"(?:engineer(?:ing)?|engr|developer|scientist|architect|researcher|swe|mts)"
#: Title patterns (regexes, case-insensitive) used when targets/roles.md names none. The built-in include list matches
#: any engineering-style title; the owner narrows it to their own fields in policy.title_filter.include.
_BUILTIN_INCLUDE = [rf"\b{ROLE_WORDS}\b"]
_BUILTIN_EXCLUDE = [
    r"\b(?:account executive|sales|pre-?sales|recruit(?:er|ing)|talent acquisition|marketing|counsel|"
    r"legal|attorney|paralegal|finance|accountant|payroll|designer|customer success|"
    r"support specialist|program manager|product manager|project manager|business development|"
    r"partnerships? manager|people partner|chief of staff|developer relations|devrel|evangelist|"
    r"communications)\b",
]
DEFAULT_TITLE_INCLUDE = [str(p) for p in (cfg.get("policy.title_filter.include") or _BUILTIN_INCLUDE)]
DEFAULT_TITLE_EXCLUDE = [str(p) for p in (cfg.get("policy.title_filter.exclude") or _BUILTIN_EXCLUDE)]
#: Extra title phrases that count at a listed AI-first company (see ai_first).
AI_FIRST_INCLUDE = [str(p) for p in (cfg.get("policy.title_filter.ai_first_include") or [])]

_ROLES_KEYS = {
    "title_include": ("title_include", "include_titles", "titles", "title_patterns"),
    "title_exclude": ("title_exclude", "exclude_titles"),
    "sponsorship_block": ("sponsorship_block", "block_phrases", "sponsorship_block_phrases"),
    "authorization_block": ("authorization_block", "citizenship_block"),
    "sponsorship_boost": ("sponsorship_boost", "boost_phrases", "sponsorship_boost_phrases"),
}


def _classify_heading(text: str) -> str | None:
    h = text.lower()
    if "title" in h and re.search(r"exclu|avoid|skip|reject|never|\bnot\b|negative", h):
        return "title_exclude"
    if "title" in h:
        return "title_include"
    if "boost" in h or ("sponsor" in h and re.search(r"positive|good|welcome|signal", h)):
        return "sponsorship_boost"
    if "block" in h or ("sponsor" in h and re.search(r"no[- ]sponsor|disqualif|reject|hard gate", h)):
        return "sponsorship_block"
    return None


def _classify_label(text: str) -> str | None:
    """A bold label such as '**Block (no sponsorship).**' right above a fenced list."""
    h = text.lower()
    if "boost" in h:
        return "sponsorship_boost"
    if "block" in h and re.search(r"citizen|clearance|export", h):
        return "authorization_block"       # hard: citizenship, clearance, export control
    if "block" in h:
        return "sponsorship_block"         # a flagged risk, never a skip
    return _classify_heading(text)


def parse_roles_md(text: str) -> dict[str, list[str]]:
    """Pull pattern lists out of targets/roles.md.

    Understands: headings that name the list ("Included titles", "Excluded titles", ...); table
    rows and bullets whose patterns sit in backticks (all of them, not just the first); short plain
    bullets (<= 5 words) as phrases; fenced blocks (one regex per line) classified by the bold
    label or heading right above them ("**Block ...**", "**Boost ...**"); fenced YAML with keys
    title_include / title_exclude / sponsorship_block / sponsorship_boost."""
    out: dict[str, list[str]] = {k: [] for k in _ROLES_KEYS}
    lines = text.splitlines()
    section: str | None = None
    label = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        fence = re.match(r"^\s*```\s*([\w-]*)\s*$", line)
        if fence:
            body: list[str] = []
            i += 1
            while i < len(lines) and not re.match(r"^\s*```\s*$", lines[i]):
                body.append(lines[i])
                i += 1
            i += 1
            if fence.group(1).lower() in ("yaml", "yml"):
                try:
                    data = fm.parse_yaml("\n".join(body))
                except Exception:
                    data = {}
                for key, aliases in _ROLES_KEYS.items():
                    for alias in aliases:
                        values = data.get(alias) if isinstance(data, dict) else None
                        if isinstance(values, list):
                            out[key].extend(str(v) for v in values if v not in (None, ""))
            else:
                kind = _classify_label(label) or section
                if kind:
                    out[kind].extend(s.strip() for s in body if s.strip() and not s.strip().startswith("#"))
            continue
        heading = re.match(r"^\s{0,3}#{1,6}\s+(.*)$", line)
        if heading:
            section, label = _classify_heading(heading.group(1)), heading.group(1)
            i += 1
            continue
        if line.strip():
            label = line.strip()
        if section:
            ticks = re.findall(r"`([^`]+)`", line)
            bullet = re.match(r"^\s*[-*+]\s+(.*\S)\s*$", line)
            if ticks and (bullet or line.lstrip().startswith("|")):
                out[section].extend(tk.strip() for tk in ticks if tk.strip())
            elif bullet and len(bullet.group(1).split()) <= 5:
                out[section].append(bullet.group(1).strip().strip('"').strip("'"))
        i += 1
    return out


def read_roles_config(root: Path) -> dict[str, list[str]]:
    path = Path(root) / "targets" / "roles.md"
    if not path.is_file():
        return {k: [] for k in _ROLES_KEYS}
    return parse_roles_md(path.read_text(encoding="utf-8"))


def phrase_regex(phrase: str) -> re.Pattern:
    """`/re/` or anything regex-looking compiles as a regex; plain text becomes a whole-word phrase."""
    s = str(phrase).strip()
    if len(s) > 2 and s.startswith("/") and s.endswith("/"):
        s = s[1:-1]
        return re.compile(s, re.I)
    if re.search(r"\\[bdswBDSW]|\(\?|\.\*|\[[^\]]+\]|(?<!\\)\|", s):
        try:
            return re.compile(s, re.I)
        except re.error:
            pass
    words = [re.escape(w) for w in s.split()]
    return re.compile(r"(?<![A-Za-z0-9])" + r"\s+".join(words) + r"(?![A-Za-z0-9])", re.I)


def title_norm(title: str) -> str:
    """lowercase, punctuation -> space (roles.md: 'match on the normalized title')."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+#]+", " ", str(title or "").lower())).strip()


def title_pattern(pattern: str) -> re.Pattern:
    """roles.md title pattern -> regex over title_norm(). Glob style: `*` wildcard, `(a\\|b)?`
    groups; regex style when it uses \\b, \\s, (?, .* or [..]."""
    s = str(pattern).strip()
    if re.search(r"\\[bdswBDSW]|\(\?|\.\*|\[[^\]]+\]", s) or (s.startswith("/") and s.endswith("/")):
        return phrase_regex(s)
    s = s.replace("\\|", "|").lower()
    out: list[str] = []
    prev = ""
    for ch in s:
        if ch == "*":
            out.append(r"[a-z0-9+#]*" if prev.isalnum() else r".*")
        elif ch in "()|?":
            out.append(ch)
        elif ch.isalnum() or ch in "+#":
            out.append(re.escape(ch))
        elif out and out[-1] != r"\s*":
            out.append(r"\s*")                       # spaces and punctuation are spaces in title_norm
        prev = ch
    body = "".join(out)
    try:
        return re.compile(r"(?<![a-z0-9])" + body + r"(?![a-z0-9])")
    except re.error:
        return phrase_regex(pattern)


@dataclass
class TitleFilter:
    include: list[re.Pattern]
    exclude: list[re.Pattern]

    def matches(self, title: str) -> bool:
        """Some include pattern matches and no exclude pattern matches OUTSIDE the included span
        ('staff' excludes Staff Engineer but not Member of Technical Staff)."""
        t = title_norm(title)
        spans = [m.span() for p in self.include for m in [p.search(t)] if m]
        if not spans:
            return False
        for p in self.exclude:
            for m in p.finditer(t):
                if not any(a <= m.start() and m.end() <= b for a, b in spans):
                    return False
        return True


def default_title_filter() -> TitleFilter:
    return TitleFilter([re.compile(p, re.I) for p in DEFAULT_TITLE_INCLUDE],
                       [re.compile(p, re.I) for p in DEFAULT_TITLE_EXCLUDE])


def load_title_filter(root: Path) -> TitleFilter:
    cfg = read_roles_config(root)
    base = default_title_filter()
    include = [title_pattern(p) for p in cfg["title_include"]] or base.include
    exclude = [title_pattern(p) for p in cfg["title_exclude"]] or base.exclude
    return TitleFilter(include, exclude)


# =============================================================================== watchlist
def load_companies(root: Path) -> list[dict[str, Any]]:
    """targets/companies.yaml -> list of company dicts (absent or unreadable -> [])."""
    path = Path(root) / "targets" / "companies.yaml"
    if not path.is_file():
        return []
    try:
        data = fm.parse_yaml(path.read_text(encoding="utf-8"))
    except Exception as exc:  # a malformed watchlist must not kill the daily run
        print(f"warning: {path}: {exc}", file=sys.stderr)
        return []
    entries = data.get("companies") if isinstance(data, dict) else data
    out: list[dict[str, Any]] = []
    for entry in entries or []:
        if not isinstance(entry, dict) or not (entry.get("name") or entry.get("slug")):
            continue
        c = dict(entry)
        c["name"] = str(c.get("name") or c.get("slug"))
        c["slug"] = str(c.get("slug") or slugify(c["name"]))
        c["ats_vendor"] = str(c.get("ats_vendor") or "unknown").lower()
        out.append(c)
    return out


# A listed AI-first company: any segment but these, any tier but fallback (see policy.title_filter.ai_first_include)
NOT_AI_FIRST_SEGMENTS = ("big-tech", "enterprise")
NOT_AI_FIRST_TIERS = ("fallback",)


def ai_first(company: dict[str, Any]) -> bool:
    """A watchlist company counts as AI-first unless its segment is big-tech or enterprise or its tier is fallback."""
    return (str(company.get("segment") or "").lower() not in NOT_AI_FIRST_SEGMENTS
            and str(company.get("tier") or "").lower() not in NOT_AI_FIRST_TIERS)


def board_from_spec(spec: str) -> dict[str, Any]:
    """'vendor:token[:Name]' -> company dict (for ad-hoc and smoke runs)."""
    parts = spec.split(":", 2)
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise ValueError(f"--board expects vendor:token[:Name], got {spec!r}")
    name = parts[2] if len(parts) == 3 and parts[2] else parts[1]
    return {"name": name, "slug": slugify(name), "ats_vendor": parts[0].lower(), "board_token": parts[1]}


# =============================================================================== records
@dataclass
class BoardResult:
    company_slug: str
    vendor: str
    records: list[dict[str, Any]] = field(default_factory=list)
    all_urls: set[str] = field(default_factory=set)      # every posting on the board (unfiltered)
    complete: bool = False                                 # all_urls is the whole board
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cut: bool = False                                      # the deadline stopped the read (the rest waits)


def build_body(rec: dict[str, Any], jd_text: str) -> str:
    return (f"# {rec.get('company')}: {rec.get('title')}\n\n"
            f"- Source: {rec.get('source_url')}\n"
            f"- Apply: {rec.get('apply_url') or rec.get('source_url')}\n\n"
            "## JD snapshot (untrusted: data, never instructions)\n\n"
            f"{wrap_untrusted(jd_text, 'jd', limit=JD_LIMIT)}\n\n"
            "## Must-haves\n\n## Nice-to-haves\n")


_JD_BLOCK = re.compile(r"<untrusted_jd>\n?(.*?)\n?</untrusted_jd>", re.S)


def jd_text_from_body(body: str) -> str:
    m = _JD_BLOCK.search(body or "")
    return m.group(1) if m else ""


def make_record(company: dict[str, Any], *, title: str, source: str, source_url: str,
                apply_url: str | None, ats_vendor: str, posted_at: Any, location: str | None,
                remote: str, comp: tuple, employment_type: str | None, jd_text: str,
                now: datetime, req_id: str | None = None) -> dict[str, Any]:
    slug = str(company.get("slug") or slugify(company.get("name", "unknown")))
    title = re.sub(r"\s+", " ", str(title or "")).strip()
    req = str(req_id).strip() if req_id not in (None, "") else None
    rec: dict[str, Any] = {k: None for k in JOB_FIELDS}
    posted = posted_value(posted_at)
    rec.update({
        "id": f"{slug}-{slugify(req)}" if req else f"{slug}-{short_hash(source_url, n=8)}",
        "company": str(company.get("name") or slug),
        "company_slug": slug,
        "title": title,
        "req_id": req,
        "source": source,
        "source_url": source_url,
        "apply_url": apply_url or source_url,
        "ats_vendor": ats_vendor,
        "posted_at": posted,
        "first_seen": iso_utc(now),
        "last_seen": iso_utc(now),
        "location": location,
        "remote": remote,
        "comp_min": comp[0],
        "comp_max": comp[1],
        "comp_currency": comp[2] if (comp[0] or comp[1]) else None,
        "employment_type": employment_type,
        "seniority_est": estimate_seniority(title),
        "jd_hash": text_hash(jd_text),
        "freshness_hours": freshness_hours(posted, now),
        "status": "discovered",
    })
    rec["_jd_text"] = jd_text
    rec["_body"] = build_body(rec, jd_text)
    return rec


def disambiguate_ids(records: list[dict[str, Any]]) -> None:
    """Postings on one board that share a req id get a URL-hash suffix (all of them)."""
    counts = Counter(r["id"] for r in records)
    for r in records:
        if counts[r["id"]] > 1:
            r["id"] = f"{r['id']}-{short_hash(r['source_url'], n=6)}"


# =============================================================================== vendors
def _greenhouse_record(company: dict, job: dict, now: datetime) -> dict[str, Any]:
    content = _html.unescape(job.get("content") or "")          # the board API double-escapes
    jd_text = html_to_text(content)
    ranges = [parse_comp_text(html_to_text(b)) for b in
              re.findall(r'<div class="pay-range">(.*?)</div>', content, flags=re.S)]
    ranges = [r for r in ranges if r[0] or r[1]]
    if ranges:
        cur = ranges[0][2]
        same = [r for r in ranges if r[2] == cur]
        comp = (min(r[0] for r in same), max(r[1] for r in same), cur)
    else:
        comp = parse_comp_text(jd_text)
    location = (job.get("location") or {}).get("name")
    meta = {str(m.get("name")): m.get("value") for m in job.get("metadata") or [] if isinstance(m, dict)}
    workplace = meta.get("Location Type") or meta.get("Workplace Type") or meta.get("Remote")
    employment = meta.get("Employment Type") or meta.get("Employment type")
    url = job.get("absolute_url") or ""
    return make_record(
        company, title=job.get("title") or "", source="greenhouse", source_url=url, apply_url=url,
        ats_vendor="greenhouse", posted_at=job.get("first_published") or job.get("updated_at"),
        location=join_locations([location]), remote=remote_mode(location, workplace if isinstance(workplace, str) else None),
        comp=comp, employment_type=norm_employment(employment if isinstance(employment, str) else None),
        jd_text=jd_text, now=now, req_id=job.get("requisition_id"))


def fetch_greenhouse(company: dict, http: Any, now: datetime, title_filter: TitleFilter | None) -> BoardResult:
    token = quote(str(company.get("board_token") or company["slug"]), safe="")
    data = http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", params={"content": "true"})
    res = BoardResult(company["slug"], "greenhouse", complete=True)
    for job in (data or {}).get("jobs") or []:
        res.all_urls.add(job.get("absolute_url") or "")
        if title_filter is None or title_filter.matches(job.get("title") or ""):
            res.records.append(_greenhouse_record(company, job, now))
    disambiguate_ids(res.records)
    return res


def _lever_record(company: dict, post: dict, now: datetime) -> dict[str, Any]:
    cats = post.get("categories") or {}
    locs = cats.get("allLocations") or ([cats.get("location")] if cats.get("location") else [])
    location = join_locations(locs)
    parts = [post.get("description") or ""]
    for lst in post.get("lists") or []:
        parts.append(f"<h3>{lst.get('text') or ''}</h3><ul>{lst.get('content') or ''}</ul>")
    parts.append(post.get("additional") or "")
    jd_text = html_to_text("\n".join(parts))
    sr = post.get("salaryRange") or {}
    if sr and sr.get("interval") in (None, "per-year-salary") and (sr.get("min") or sr.get("max")):
        comp = (sr.get("min") or None, sr.get("max") or None, sr.get("currency"))
    else:
        comp = parse_comp_text(jd_text)
    wt = post.get("workplaceType")
    title = post.get("text") or ""
    return make_record(
        company, title=title, source="lever", source_url=post.get("hostedUrl") or "",
        apply_url=post.get("applyUrl"), ats_vendor="lever", posted_at=post.get("createdAt"),
        location=location, remote=remote_mode(wt if wt != "unspecified" else None, location),
        comp=comp, employment_type=norm_employment(cats.get("commitment")), jd_text=jd_text, now=now,
        req_id=req_from_title(title))


def fetch_lever(company: dict, http: Any, now: datetime, title_filter: TitleFilter | None) -> BoardResult:
    site = quote(str(company.get("board_token") or company["slug"]), safe="")
    data = http.get_json(f"https://api.lever.co/v0/postings/{site}", params={"mode": "json"})
    res = BoardResult(company["slug"], "lever", complete=isinstance(data, list))
    for post in data if isinstance(data, list) else []:
        res.all_urls.add(post.get("hostedUrl") or "")
        if title_filter is None or title_filter.matches(post.get("text") or ""):
            res.records.append(_lever_record(company, post, now))
    disambiguate_ids(res.records)
    return res


def _ashby_record(company: dict, job: dict, now: datetime) -> dict[str, Any]:
    locs = [job.get("location")] + [s.get("location") for s in job.get("secondaryLocations") or []
                                    if isinstance(s, dict)]
    location = join_locations(locs)
    wt = job.get("workplaceType")
    # workplaceType is authoritative; isRemote is True for Hybrid postings too, so it is ignored
    remote = {"Remote": "remote", "Hybrid": "hybrid", "OnSite": "onsite"}.get(wt or "") or remote_mode(location)
    comp: tuple = (None, None, None)
    for c in ((job.get("compensation") or {}).get("summaryComponents") or []):
        if c.get("compensationType") == "Salary" and str(c.get("interval", "")).upper() in ("1 YEAR", "YEAR", ""):
            comp = (c.get("minValue") or None, c.get("maxValue") or None, c.get("currencyCode"))
            break
    jd_text = html_to_text(job.get("descriptionHtml") or "") or str(job.get("descriptionPlain") or "")
    if not (comp[0] or comp[1]):
        comp = parse_comp_text(jd_text)
    return make_record(
        company, title=job.get("title") or "", source="ashby", source_url=job.get("jobUrl") or "",
        apply_url=job.get("applyUrl"), ats_vendor="ashby", posted_at=job.get("publishedAt"),
        location=location, remote=remote, comp=comp,
        employment_type=norm_employment(job.get("employmentType")), jd_text=jd_text, now=now)


def fetch_ashby(company: dict, http: Any, now: datetime, title_filter: TitleFilter | None) -> BoardResult:
    org = quote(str(company.get("board_token") or company["slug"]), safe="")
    data = http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{org}",
                         params={"includeCompensation": "true"})
    res = BoardResult(company["slug"], "ashby", complete=True)
    listed = [j for j in (data or {}).get("jobs") or [] if j.get("isListed") is not False]
    dates = {str(j.get("publishedAt") or "")[:10] for j in listed}
    republish = len(listed) >= ASHBY_REPUBLISH_MIN and len(dates) == 1 and "" not in dates
    if republish:                      # one publishedAt for every role is a board-wide republish
        res.notes.append(f"{company['slug']}: every role shows publishedAt {next(iter(dates))} (board-wide republish): "
                         "posted dates recorded as unknown")
    for job in listed:
        res.all_urls.add(job.get("jobUrl") or "")
        if title_filter is None or title_filter.matches(job.get("title") or ""):
            rec = _ashby_record(company, job, now)
            if republish:
                rec["posted_at"], rec["freshness_hours"] = None, None
                rec["posted_unknown"] = f"board-wide republish {next(iter(dates))} (every role on the board shares it)"
            res.records.append(rec)
    disambiguate_ids(res.records)
    return res


def _workable_place(loc: dict) -> str:
    return ", ".join(str(loc.get(k)).strip() for k in ("city", "region", "country") if loc.get(k))


def _workable_record(company: dict, job: dict, now: datetime) -> dict[str, Any]:
    locs = [_workable_place(loc) for loc in job.get("locations") or [] if isinstance(loc, dict)]
    location = join_locations(locs or [_workable_place({"city": job.get("city"), "region": job.get("state"),
                                                        "country": job.get("country")})])
    remote = "remote" if job.get("telecommuting") else remote_mode(job.get("title"), location)
    jd_text = html_to_text(job.get("description") or "")
    url = job.get("url") or job.get("shortlink") or ""
    return make_record(
        company, title=job.get("title") or "", source="workable", source_url=url,
        apply_url=job.get("application_url"), ats_vendor="workable",
        posted_at=job.get("published_on") or job.get("created_at"), location=location, remote=remote,
        comp=parse_comp_text(jd_text), employment_type=norm_employment(job.get("employment_type")),
        jd_text=jd_text, now=now, req_id=job.get("shortcode") or job.get("code") or None)


def fetch_workable(company: dict, http: Any, now: datetime, title_filter: TitleFilter | None) -> BoardResult:
    """Workable's public accounts API (R2 G4): www.workable.com/api/accounts/<subdomain> redirects here; no auth,
    every published job with its description when details=true."""
    account = quote(str(company.get("board_token") or company["slug"]), safe="")
    data = http.get_json(f"https://apply.workable.com/api/v1/widget/accounts/{account}", params={"details": "true"})
    jobs = (data or {}).get("jobs") if isinstance(data, dict) else None
    res = BoardResult(company["slug"], "workable", complete=isinstance(jobs, list))
    for job in jobs or []:
        res.all_urls.add(job.get("url") or job.get("shortlink") or "")
        if title_filter is None or title_filter.matches(job.get("title") or ""):
            res.records.append(_workable_record(company, job, now))
    disambiguate_ids(res.records)
    return res


SR_PAGE = 100                   # the Posting API's largest page
SR_MAX_PAGES = 10


def _sr_record(company: dict, url: str, post: dict, now: datetime) -> dict[str, Any]:
    loc = post.get("location") or {}
    location = loc.get("fullLocation") or join_locations([", ".join(str(loc.get(k)) for k in ("city", "region", "country")
                                                                     if loc.get(k))])
    remote = "remote" if loc.get("remote") else ("hybrid" if loc.get("hybrid") else remote_mode(location) if location
                                                 else "unknown")
    if remote == "unknown" and location:
        remote = "onsite"
    sections = ((post.get("jobAd") or {}).get("sections") or {})
    jd_text = html_to_text("\n".join(str((sections.get(k) or {}).get("text") or "") for k in
                                     ("jobDescription", "qualifications", "additionalInformation", "companyDescription")))
    return make_record(
        company, title=str(post.get("name") or "").strip(), source="smartrecruiters", source_url=url,
        apply_url=post.get("applyUrl") or post.get("postingUrl"), ats_vendor="smartrecruiters",
        posted_at=post.get("releasedDate"), location=location, remote=remote, comp=parse_comp_text(jd_text),
        employment_type=norm_employment((post.get("typeOfEmployment") or {}).get("label")), jd_text=jd_text, now=now,
        req_id=post.get("refNumber") or post.get("id"))


def fetch_smartrecruiters(company: dict, http: Any, now: datetime, title_filter: TitleFilter | None,
                          max_pages: int = SR_MAX_PAGES) -> BoardResult:
    """SmartRecruiters Posting API: "the OAuth scope internal_postings_read is only required when the
    destination query parameter is INTERNAL or INTERNAL_OR_PUBLIC" (developers.smartrecruiters.com/reference/postingapi-1),
    so public postings need no key. One list page per 100 postings; a detail GET only for title-matched postings."""
    ident = quote(str(company.get("board_token") or company["slug"]), safe="")
    base = f"https://api.smartrecruiters.com/v1/companies/{ident}/postings"
    res = BoardResult(company["slug"], "smartrecruiters")
    seen, total, offset = [], None, 0
    for _ in range(max_pages):
        if cut_at_deadline(res, "listing"):
            break
        data = http.get_json(base, params={"limit": SR_PAGE, "offset": offset}) or {}
        page = data.get("content") or []
        total = int(data.get("totalFound") or 0)
        seen.extend(page)
        offset += SR_PAGE
        if not page or offset >= total:
            break
    res.complete = not res.cut and total is not None and len(seen) >= total
    for post in seen:
        pid = str(post.get("id") or "")
        url = f"https://jobs.smartrecruiters.com/{ident}/{pid}"
        res.all_urls.add(url)
        if title_filter is not None and not title_filter.matches(str(post.get("name") or "")):
            continue
        if cut_at_deadline(res, "detail reads"):
            break
        try:
            detail = http.get_json(f"{base}/{pid}") or {}
        except DeadlineReached:
            cut_at_deadline(res, "detail reads", force=True)
            break
        except Exception as exc:                   # a missing detail drops one posting, not the board
            res.notes.append(f"{company['slug']}: smartrecruiters detail {pid} failed ({exc})")
            continue
        res.records.append(_sr_record(company, url, {**post, **detail}, now))
    disambiguate_ids(res.records)
    return res


def _rp_record(company: dict, url: str, job: dict, now: datetime) -> dict[str, Any]:
    locs = job.get("workLocations") or [(job.get("workLocation") or {}).get("label")]
    location = join_locations(locs)
    desc = job.get("description") or {}
    jd_text = html_to_text("\n".join(str(desc.get(k) or "") for k in ("role", "company"))
                           if isinstance(desc, dict) else str(desc))
    emp = job.get("employmentType") or {}
    return make_record(
        company, title=str(job.get("name") or "").strip(), source="rippling", source_url=url, apply_url=url,
        ats_vendor="rippling", posted_at=job.get("createdOn"), location=location,
        remote=remote_mode(location, job.get("name")) if location else "unknown", comp=parse_comp_text(jd_text),
        employment_type=norm_employment(emp.get("id") or emp.get("label")), jd_text=jd_text, now=now)


def fetch_rippling(company: dict, http: Any, now: datetime, title_filter: TitleFilter | None) -> BoardResult:
    """Rippling's Job Board API (developer.rippling.com/documentation/job-board-api): the board list and
    each job are public JSON, read keyless. The list has no description or date, so a detail GET runs
    only for title-matched jobs."""
    slug = quote(str(company.get("board_token") or company["slug"]), safe="")
    base = f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs"
    data = http.get_json(base)
    jobs = data if isinstance(data, list) else ((data or {}).get("jobs") if isinstance(data, dict) else None)
    res = BoardResult(company["slug"], "rippling", complete=isinstance(jobs, list))
    for job in jobs or []:
        url = str(job.get("url") or f"https://ats.rippling.com/{slug}/jobs/{job.get('uuid')}")
        res.all_urls.add(url)
        if title_filter is not None and not title_filter.matches(str(job.get("name") or "")):
            continue
        if cut_at_deadline(res, "detail reads"):
            break
        try:
            detail = http.get_json(f"{base}/{job.get('uuid')}") or {}
        except DeadlineReached:
            cut_at_deadline(res, "detail reads", force=True)
            break
        except Exception as exc:                   # a missing detail drops one job, not the board
            res.notes.append(f"{company['slug']}: rippling detail {job.get('uuid')} failed ({exc})")
            continue
        res.records.append(_rp_record(company, url, {**job, **detail}, now))
    disambiguate_ids(res.records)
    return res


def _gem_record(company: dict, post: dict, now: datetime) -> dict[str, Any]:
    loc = post.get("location") or {}
    location = join_locations([loc.get("name") if isinstance(loc, dict) else loc])
    lt = str(post.get("location_type") or "").lower()
    remote = {"remote": "remote", "hybrid": "hybrid", "in_office": "onsite", "onsite": "onsite"}.get(lt) \
        or remote_mode(location)
    jd_text = html_to_text(post.get("content") or "") or str(post.get("content_plain") or "")
    url = str(post.get("absolute_url") or "")
    return make_record(
        company, title=str(post.get("title") or "").strip(), source="gem", source_url=url, apply_url=url,
        ats_vendor="gem", posted_at=post.get("first_published_at") or post.get("created_at"), location=location,
        remote=remote, comp=parse_comp_text(jd_text), employment_type=norm_employment(post.get("employment_type")),
        jd_text=jd_text, now=now, req_id=post.get("requisition_id"))


def fetch_gem(company: dict, http: Any, now: datetime, title_filter: TitleFilter | None) -> BoardResult:
    """Gem's Job Board API (help.gem.com, "The Job Board API"): a public list of a company's posts with
    their descriptions, read keyless. One GET per board."""
    path = quote(str(company.get("board_token") or company["slug"]), safe="")
    data = http.get_json(f"https://api.gem.com/job_board/v0/{path}/job_posts/")
    posts = data if isinstance(data, list) else None
    res = BoardResult(company["slug"], "gem", complete=posts is not None)
    for post in posts or []:
        res.all_urls.add(str(post.get("absolute_url") or ""))
        if title_filter is None or title_filter.matches(str(post.get("title") or "")):
            res.records.append(_gem_record(company, post, now))
    disambiguate_ids(res.records)
    return res


def _pp_record(company: dict, post: dict, now: datetime) -> dict[str, Any]:
    loc = post.get("location") or {}
    parts = [loc.get("city"), loc.get("province")]
    location = ", ".join(dict.fromkeys(str(x).strip() for x in parts if x)) or loc.get("name") or None
    wt = str(post.get("workplace_type") or "").lower()
    remote = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "on_site": "onsite"}.get(wt) \
        or remote_mode(location, post.get("workplace_type_text"))
    jd_text = html_to_text("\n".join(str(post.get(k) or "") for k in
                                     ("description", "key_responsibilities", "skills_knowledge_expertise", "benefits")))
    comp: tuple = (None, None, None)
    if post.get("compensation_visible") and str(post.get("compensation_frequency") or "year").lower() in ("year", "yearly", "annual", "annually"):
        comp = (post.get("compensation_minimum") or None, post.get("compensation_maximum") or None,
                post.get("compensation_currency"))
    if not (comp[0] or comp[1]):
        comp = parse_comp_text(jd_text)
    return make_record(
        company, title=str(post.get("title") or "").strip(), source="pinpoint", source_url=str(post.get("url") or ""),
        apply_url=str(post.get("url") or ""), ats_vendor="pinpoint", posted_at=None, location=location, remote=remote,
        comp=comp, employment_type=norm_employment(post.get("employment_type_text") or post.get("employment_type")),
        jd_text=jd_text, now=now, req_id=(post.get("job") or {}).get("requisition_id"))


def fetch_pinpoint(company: dict, http: Any, now: datetime, title_filter: TitleFilter | None) -> BoardResult:
    """Pinpoint's postings.json (developers.pinpointhq.com/docs/jobs-json-endpoint: "This resource can be fetched
    client side with no CORS issues"), read keyless. One GET per board. The feed has no
    posting date, so freshness falls back to first_seen; pay is used only where the employer marks it visible."""
    sub = quote(str(company.get("board_token") or company["slug"]), safe="")
    data = http.get_json(f"https://{sub}.pinpointhq.com/postings.json")
    posts = (data or {}).get("data") if isinstance(data, dict) else None
    res = BoardResult(company["slug"], "pinpoint", complete=isinstance(posts, list))
    for post in posts or []:
        res.all_urls.add(str(post.get("url") or ""))
        if title_filter is None or title_filter.matches(str(post.get("title") or "")):
            res.records.append(_pp_record(company, post, now))
    disambiguate_ids(res.records)
    return res


TT_PAGE = 100
TT_MAX_PAGES = 10
TT_NS = "{https://teamtailor.com/locations}"


def teamtailor_feed_url(token: str, offset: int) -> str:
    host = str(token).strip().strip("/")
    host = host if "." in host else f"{quote(host, safe='')}.teamtailor.com"
    return f"https://{host}/jobs.rss?per_page={TT_PAGE}&offset={offset}"


def _rfc822(value: str) -> datetime | None:
    """An RSS pubDate ("Tue, 29 Sep 2026 18:00:00 +0200") as an aware datetime, or None."""
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(value) if value else None
    except (TypeError, ValueError, IndexError):
        return None
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _tt_record(company: dict, item: Any, now: datetime) -> dict[str, Any]:
    text = lambda tag: (item.findtext(tag) or "").strip()  # noqa: E731
    places = []
    for loc in item.findall(f"{TT_NS}locations/{TT_NS}location"):
        parts = [(loc.findtext(f"{TT_NS}{k}") or "").strip() for k in ("city", "country")]
        places.append(", ".join(x for x in parts if x) or (loc.findtext(f"{TT_NS}name") or "").strip())
    location = join_locations(places)
    status = text("remoteStatus").lower()
    remote = {"fully": "remote", "remote": "remote", "hybrid": "hybrid", "temporary": "hybrid", "none": "onsite"}.get(
        status) or (remote_mode(location) if location else "unknown")
    link = text("link")
    m = re.search(r"/jobs/(\d+)", link)
    jd_text = html_to_text(text("description"))
    return make_record(
        company, title=text("title"), source="teamtailor", source_url=link, apply_url=link, ats_vendor="teamtailor",
        posted_at=_rfc822(text("pubDate")), location=location, remote=remote, comp=parse_comp_text(jd_text),
        employment_type=None, jd_text=jd_text, now=now, req_id=m.group(1) if m else text("guid") or None)


def fetch_teamtailor(company: dict, http: Any, now: datetime, title_filter: TitleFilter | None,
                     max_pages: int = TT_MAX_PAGES) -> BoardResult:
    """Teamtailor's careers RSS (support.teamtailor.com, RSS feed how-to guide: "Anyone can access the feed since it
    contains only public information"), read keyless. board_token is the careers host
    (career.example.com) or the teamtailor subdomain. The feed is untrusted XML: one declaring a DOCTYPE or an entity
    is refused."""
    token = str(company.get("board_token") or company["slug"])
    res = BoardResult(company["slug"], "teamtailor")
    items: list[Any] = []
    for page in range(max_pages):
        if cut_at_deadline(res, "feed pages"):
            break
        try:
            status, body = http.get_text(teamtailor_feed_url(token, page * TT_PAGE))
        except DeadlineReached:
            cut_at_deadline(res, "feed pages", force=True)
            break
        if status >= 400:
            raise FetchError(f"teamtailor feed {token}: HTTP {status}")
        if re.search(r"<!DOCTYPE|<!ENTITY", body[:4096], re.I):
            raise FetchError(f"teamtailor feed {token}: a DOCTYPE or entity declaration (refused)")
        got = ET.fromstring(body.lstrip("\ufeff \t\r\n")).findall("./channel/item")
        items.extend(got)
        if len(got) < TT_PAGE:
            res.complete = True
            break
    for item in items:
        res.all_urls.add((item.findtext("link") or "").strip())
        if title_filter is None or title_filter.matches((item.findtext("title") or "").strip()):
            res.records.append(_tt_record(company, item, now))
    disambiguate_ids(res.records)
    return res


def _rc_amount(value: Any) -> int | None:
    try:
        return int(float(str(value).replace(",", ""))) if value not in (None, "") else None
    except ValueError:
        return None


def _rc_record(company: dict, offer: dict, now: datetime) -> dict[str, Any]:
    location = offer.get("location") or join_locations([", ".join(str(offer.get(k)) for k in ("city", "state_name",
                                                                                              "country") if offer.get(k))])
    remote = "remote" if offer.get("remote") else ("hybrid" if offer.get("hybrid") else
                                                   "onsite" if offer.get("on_site") else remote_mode(location))
    jd_text = html_to_text("\n".join(str(offer.get(k) or "") for k in ("description", "requirements")))
    sal = offer.get("salary") or {}
    comp: tuple = (None, None, None)
    if isinstance(sal, dict) and str(sal.get("period") or "year").lower() in ("year", "yearly", "annual", "annually"):
        lo, hi = _rc_amount(sal.get("min")), _rc_amount(sal.get("max"))
        if lo or hi:
            comp = (lo, hi, sal.get("currency"))
    if not (comp[0] or comp[1]):
        comp = parse_comp_text(jd_text)
    published = str(offer.get("published_at") or offer.get("created_at") or "")
    published = re.sub(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) UTC$", r"\1T\2Z", published)
    url = str(offer.get("careers_url") or "")
    return make_record(
        company, title=str(offer.get("title") or "").strip(), source="recruitee", source_url=url,
        apply_url=offer.get("careers_apply_url") or url, ats_vendor="recruitee", posted_at=published or None,
        location=location, remote=remote, comp=comp, employment_type=norm_employment(offer.get("employment_type_code")),
        jd_text=jd_text, now=now, req_id=offer.get("id"))


def fetch_recruitee(company: dict, http: Any, now: datetime, title_filter: TitleFilter | None) -> BoardResult:
    """Recruitee's public offers JSON (support.recruitee.com FAQ API: "For JSON data, use:
    https://{yourcompany}.recruitee.com/api/offers"), read keyless. One GET per board."""
    sub = quote(str(company.get("board_token") or company["slug"]), safe="")
    data = http.get_json(f"https://{sub}.recruitee.com/api/offers/")
    offers = (data or {}).get("offers") if isinstance(data, dict) else None
    res = BoardResult(company["slug"], "recruitee", complete=isinstance(offers, list))
    for offer in offers or []:
        res.all_urls.add(str(offer.get("careers_url") or ""))
        if title_filter is None or title_filter.matches(str(offer.get("title") or "")):
            res.records.append(_rc_record(company, offer, now))
    disambiguate_ids(res.records)
    return res


ASHBY_REPUBLISH_MIN = 3          # a board this size or larger with one publishedAt for all roles was republished

JIBE_PAGE = 100                 # the page size the API accepts
JIBE_DELAY_S = 5.0              # the host's robots.txt: crawl-delay 5
JIBE_MAX_PAGES = 20
_JIBE_MINMAX = re.compile(r"\bMIN\s+(\$[\d,.]+[kK]?)\s*[-\u2013]\s*MAX\s+(\$[\d,.]+[kK]?)", re.I)


def _jibe_sleep(seconds: float) -> None:
    time.sleep(seconds)


def _jibe_record(company: dict, host: str, job: dict, now: datetime) -> dict[str, Any]:
    jd_text = html_to_text(job.get("description") or "")
    # Some Jibe sites state pay as "California pay range: MIN $100,000 - MAX $150,000"; the first range is taken
    comp = parse_comp_text(_JIBE_MINMAX.sub(r"\1 - \2", jd_text))
    location = job.get("full_location") or join_locations([job.get("city"), job.get("state")])
    slug = str(job.get("slug") or job.get("req_id") or "")
    return make_record(
        company, title=job.get("title") or "", source="jibe", source_url=f"https://{host}/jobs/{quote(slug)}",
        apply_url=job.get("apply_url"), ats_vendor=str(job.get("ats_code") or "unknown").lower(),
        posted_at=job.get("posted_date"), location=location, remote=remote_mode(location, job.get("title")),
        comp=comp, employment_type=norm_employment((job.get("tags1") or [None])[0]), jd_text=jd_text, now=now,
        req_id=job.get("req_id"))


def fetch_jibe(company: dict, http: Any, now: datetime, title_filter: TitleFilter | None) -> BoardResult:
    host = str(company.get("board_token") or "").strip().strip("/")
    url = f"https://{host}/api/jobs"
    res = BoardResult(company["slug"], "jibe")
    total = None
    for page in range(1, JIBE_MAX_PAGES + 1):
        if cut_at_deadline(res, "pages") or (page > 1 and polite_wait(res, JIBE_DELAY_S, "pages", _jibe_sleep)):
            break
        try:
            data = http.get_json(url, params={"country": company.get("country") or DEFAULT_COUNTRY,
                                              "limit": JIBE_PAGE, "page": page}) or {}
        except DeadlineReached:
            cut_at_deadline(res, "pages", force=True)
            break
        total = data.get("totalCount") if total is None else total
        jobs = [j.get("data") or {} for j in data.get("jobs") or []]
        for job in jobs:
            rec = _jibe_record(company, host, job, now)
            res.all_urls.add(rec["source_url"])
            if title_filter is None or title_filter.matches(rec["title"]):
                res.records.append(rec)
        if not jobs or (isinstance(total, int) and page * JIBE_PAGE >= total):
            break
    res.complete = not res.cut and isinstance(total, int) and len(res.all_urls) >= total
    if not res.complete:
        res.notes.append(f"{company['slug']}: jibe listed {len(res.all_urls)} of {total} postings")
    disambiguate_ids(res.records)
    return res


SITEMAP_DELAY_S = 5.0           # pause between requests to the host; a longer robots.txt crawl-delay wins
SITEMAP_MAX_DELAY_S = 60.0      # a crawl-delay above this: the sitemap is read, no job page is fetched
SITEMAP_MAX_FETCH = 40          # new job pages fetched per company per run at least; the rest wait for the next run
# the leftovers are read across the day's runs (the hours in policy.search.sitemap_run_hours, in the owner's
# time zone), up to a ceiling per run; a page that failed or carried no JobPosting waits this many days before it is tried again
SITEMAP_RUN_HOURS = tuple(int(h) for h in (cfg.get("policy.search.sitemap_run_hours") or (7, 10, 13, 16, 19, 22)))
SITEMAP_MAX_FETCH_RUN = 150     # 12.5 minutes at the 5 s pause
SITEMAP_FAILED_RETRY_DAYS = 2
SITEMAP_MAX_CHILDREN = 20       # child sitemaps read from a sitemap index
SITEMAP_MAX_BYTES = 20_000_000
SITEMAP_JOB_PATH = r"/jobs?/"
HTML_ACCEPT = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


def _sitemap_sleep(seconds: float) -> None:
    time.sleep(seconds)


def sitemap_url(token: Any) -> str:
    """board_token 'host[/path.xml]' or a full URL -> the sitemap URL (https unless stated; /sitemap.xml by default)."""
    t = str(token or "").strip()
    if not re.match(r"https?://", t, re.I):
        t = "https://" + t
    parts = urlparse(t)
    path = parts.path if parts.path not in ("", "/") else "/sitemap.xml"
    return f"{parts.scheme}://{parts.netloc}{path}" + (f"?{parts.query}" if parts.query else "")


class Robots:
    """robots.txt read the RFC 9309 way: the group for our product token if one names it, else every `*` group;
    the LONGEST matching rule decides and Allow wins a tie (Python's RobotFileParser applies rules in file order, so
    `Disallow: /` then `Allow: /careers` would wrongly block careers pages); `*` and a trailing `$` are honoured;
    empty or absent text means no rules."""

    AGENT = "jaja"

    def __init__(self, text: str | None, agent: str | None = None):
        self.agent = (agent or self.AGENT).lower()
        groups: list[tuple[list[str], list[tuple[bool, str]], float | None]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        delay: float | None = None
        in_rules = False
        for raw in (text or "").splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (x.strip() for x in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if in_rules:
                    groups.append((agents, rules, delay))
                    agents, rules, delay, in_rules = [], [], None, False
                agents.append(value.lower())
            elif key in ("allow", "disallow"):
                in_rules = True
                if value:
                    rules.append((key == "allow", value))
            elif key == "crawl-delay":
                in_rules = True
                try:
                    delay = float(value)
                except ValueError:
                    pass
        if agents:
            groups.append((agents, rules, delay))
        mine = [g for g in groups if any(a and a != "*" and self.agent.startswith(a) for a in g[0])]
        chosen = mine or [g for g in groups if "*" in g[0]]
        self.rules = [r for g in chosen for r in g[1]]
        delays = [g[2] for g in chosen if g[2] is not None]
        self.delay = max(delays) if delays else None

    @staticmethod
    def _regex(pattern: str) -> re.Pattern:
        anchored = pattern.endswith("$")
        body = re.escape(pattern[:-1] if anchored else pattern).replace(r"\*", ".*")
        return re.compile("^" + body + ("$" if anchored else ""))

    def allowed(self, url: str) -> bool:
        parts = urlparse(url)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best: tuple[int, bool] | None = None
        for allow, pattern in self.rules:
            if self._regex(pattern).match(target):
                key = (len(pattern), allow)
                if best is None or key > best:
                    best = key
        return True if best is None else best[1]


def parse_sitemap_entries(text: str) -> list[tuple[str, str]]:
    """[(loc, lastmod or ''), ...] in file order, from sitemap XML in any namespace."""
    root = ET.fromstring(text.lstrip("\ufeff \t\r\n"))
    local = lambda tag: str(tag).rsplit("}", 1)[-1]  # noqa: E731
    out = []
    for entry in root:
        loc = mod = ""
        for child in entry:
            if local(child.tag) == "loc":
                loc = (child.text or "").strip()
            elif local(child.tag) == "lastmod":
                mod = (child.text or "").strip()
        if loc:
            out.append((loc, mod))
    return out


def parse_sitemap(text: str) -> tuple[str, list[str]]:
    """('urlset' | 'sitemapindex', [loc, ...]) from sitemap XML in any namespace (Google's old one too)."""
    root = ET.fromstring(text.lstrip("﻿ \t\r\n"))
    local = lambda tag: str(tag).rsplit("}", 1)[-1]  # noqa: E731
    locs = []
    for entry in root:
        for child in entry:
            if local(child.tag) == "loc" and (child.text or "").strip():
                locs.append(child.text.strip())
    return local(root.tag), locs


def slug_title(url: str) -> str:
    """The words of a job URL after its /job/ segment, numeric ids dropped: what the title screen reads."""
    segs = [unquote(s) for s in urlparse(url).path.split("/") if s]
    low = [s.lower() for s in segs]
    for marker in ("job", "jobs"):
        if marker in low:
            segs = segs[low.index(marker) + 1:]
            break
    return " ".join(s for s in segs if not s.isdigit()).replace("_", " ")


def _slug_passes(title_filter: TitleFilter, url: str) -> bool:
    text = slug_title(url)                          # 'r-and-d' in a slug is 'R&D' in the title
    return title_filter.matches(text) or title_filter.matches(re.sub(r"\band\b", "&", text))


class _Microdata(HTMLParser):
    """The first value of each itemprop inside a schema.org JobPosting: a `content` attribute, or the element's
    inner HTML sliced from the source (nested markup survives; the element ends at its own close tag)."""

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track",
            "wbr"}

    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.text = text
        self.line_starts = [0] + [i + 1 for i, ch in enumerate(text) if ch == "\n"]
        self.inside = False
        self.props: dict[str, str] = {}
        self.open: list[Any] | None = None           # [prop, tag, depth, start index]

    def _index(self) -> int:
        line, col = self.getpos()
        return self.line_starts[line - 1] + col

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if "JobPosting" in a.get("itemtype", ""):
            self.inside = True
        if self.open is not None:
            if tag == self.open[1]:
                self.open[2] += 1
            return
        prop = a.get("itemprop")
        if not self.inside or not prop or prop in self.props:
            return
        if "content" in a:
            self.props[prop] = a["content"]
        elif tag not in self.VOID and "itemscope" not in a:
            self.open = [prop, tag, 1, self._index() + len(self.get_starttag_text() or "")]

    def handle_endtag(self, tag: str) -> None:
        if self.open is not None and tag == self.open[1]:
            self.open[2] -= 1
            if self.open[2] == 0:
                self.props[self.open[0]] = self.text[self.open[3]:self._index()]
                self.open = None


def _ld_posting(page: str) -> dict[str, Any] | None:
    for m in re.finditer(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", page, re.S | re.I):
        try:
            stack: list[Any] = [json.loads(m.group(1).strip(), strict=False)]
        except ValueError:
            continue
        while stack:
            item = stack.pop(0)
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                types = item.get("@type")
                if "JobPosting" in (types if isinstance(types, list) else [types]):
                    return item
                if isinstance(item.get("@graph"), list):
                    stack.extend(item["@graph"])
    return None


def _text_of(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("value")
    return re.sub(r"\s+", " ", _html.unescape(str(value or ""))).strip()


def _ident(value: Any) -> str | None:
    """A JobPosting identifier: a PropertyValue's `value` (its `name` is the organization), else the text."""
    if isinstance(value, dict):
        value = value.get("value")
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text or None


def url_job_id(url: str) -> str | None:
    """The first path segment that starts with a number of 6 or more digits (common on large employers' job URLs)."""
    for seg in urlparse(str(url)).path.split("/"):
        m = re.match(r"^(\d{6,})(?:\D|$)", seg)
        if m:
            return m.group(1)
    return None


_HYDRATION = re.compile(r"window\.__staticRouterHydrationData\s*=\s*JSON\.parse\(\"(.*?)\"\);", re.S)


def _hydrated_posting(page: str) -> dict[str, Any] | None:
    """Some employers' job pages carry their posting in router hydration data (jobDetails.jobsData), not JSON-LD."""
    m = _HYDRATION.search(page)
    if not m:
        return None
    try:
        data = json.loads(json.loads(f'"{m.group(1)}"'))
    except ValueError:
        return None
    job = ((data.get("loaderData") or {}).get("jobDetails") or {}).get("jobsData")
    if not isinstance(job, dict) or not job.get("postingTitle"):
        return None
    locations = [", ".join(p for p in (_text_of(loc.get(k)) for k in ("name", "stateProvince", "countryName")) if p)
                 for loc in job.get("locations") or [] if isinstance(loc, dict)]
    desc, salary = _apple_sections(job)
    return {"title": _text_of(job["postingTitle"]), "posted": _sitemap_date(job.get("postDateInGMT")),
            "description": desc, "locations": [loc for loc in locations if loc],
            "identifier": _ident(job.get("jobNumber")), "employment": job.get("employmentType"),
            "salary": salary, "remote": "remote" if job.get("homeOffice") else None}


APPLE_SECTIONS = (("Summary", "jobSummary"), ("Description", "description"), ("Responsibilities", "responsibilities"),
                  ("Minimum Qualifications", "minimumQualifications"),
                  ("Preferred Qualifications", "preferredQualifications"))
_APPLE_PAY = re.compile(r"between \$([\d,]+) and \$([\d,]+)")


def _apple_sections(job: dict[str, Any]) -> tuple[str, tuple]:
    """The whole hydrated posting as HTML (the summary, responsibilities, level, team and pay would otherwise be
    dropped): each section under its heading, then the level, the team and the base pay per location (from the
    "Pay & Benefits" footer; the EEO, accessibility and deadline footers are boilerplate and left out). Returns
    (html, (lowest base, highest base, "USD") or (None, None, None))."""
    def block(name: str, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        body = text if re.search(r"<(?:p|br|ul|li|div)\b", text, re.I) else \
            "<br>".join(_html.escape(line.strip()) for line in text.splitlines() if line.strip())
        return f"<h3>{name}</h3><p>{body}</p>"
    parts = [block(name, job.get(key)) for name, key in APPLE_SECTIONS]
    low, high = job.get("lowJobTitle"), job.get("highJobTitle")
    if low or high:
        level = f"{low} to {high}" if low and high and low != high else f"{low or high}"
        parts.append(f"<p>Level: {_html.escape(level)}</p>")
    teams = list(dict.fromkeys(str(t) for t in job.get("teamNames") or [] if t))
    if teams:
        parts.append(f"<p>Team: {_html.escape(', '.join(teams))}</p>")
    where = {str(loc.get("id")): str(loc.get("name") or "") for loc in job.get("locations") or [] if isinstance(loc, dict)}
    pays, lows, highs = [], [], []
    for foot in job.get("postingFooters") or []:
        for item in ((foot.get("localizations") or {}).get("en_US") or []) if isinstance(foot, dict) else []:
            if str(item.get("name") or "").lower().startswith("pay"):
                m = _APPLE_PAY.search(str(item.get("content") or ""))
                if m:
                    lo, hi = (int(g.replace(",", "")) for g in m.groups())
                    lows.append(lo), highs.append(hi)
                    pays.append(f"{where.get(str(foot.get('postLocationId')), foot.get('postLocationId'))}: "
                                f"${lo:,} to ${hi:,}")
    if pays:
        parts.append(block("Pay", "Base pay range\n" + "\n".join(dict.fromkeys(pays))))
    salary = (min(lows), max(highs), "USD") if lows else (None, None, None)
    return "".join(p for p in parts if p), salary


def _sitemap_date(value: Any) -> str | None:
    """'2026-09-10T..', '2026-9-10' (TalentBrew) or 'Thu Sep 24 07:00:00 UTC 2026' (SuccessFactors)."""
    text = str(value or "").strip()
    if not text:
        return None
    if parse_ts(text):
        return posted_value(text)
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})\b", text)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    try:
        return datetime.strptime(text, "%a %b %d %H:%M:%S %Z %Y").date().isoformat()
    except ValueError:
        return None


# schema.org JobPosting text fields beside `description` (some employers' JSON-LD holds its responsibilities and
# qualifications here, and only a short description would otherwise be kept)
LD_SECTIONS = (("responsibilities", "Responsibilities"), ("qualifications", "Qualifications"), ("skills", "Skills"),
               ("educationRequirements", "Education"), ("experienceRequirements", "Experience"),
               ("incentiveCompensation", "Incentive compensation"), ("jobBenefits", "Benefits"))


def _ld_text(value: Any) -> list[str]:
    if isinstance(value, list):
        return [t for v in value for t in _ld_text(v)]
    if isinstance(value, dict):
        value = value.get("description") or value.get("name") or value.get("credentialCategory") or ""
    text = _html.unescape(str(value or "")).strip()
    parts = [p.strip() for p in text.split("\xa0")]
    if len(parts) > 1 and all(len(p) >= 15 for p in parts):
        return parts                                  # some employers join list items with &nbsp; in one string
    return [text] if text else []


def _ld_sections(ld: dict[str, Any], desc: str) -> str:
    """The JobPosting's other text fields as HTML sections, each only when the description does not already hold it."""
    seen = re.sub(r"\W+", " ", html_to_text(desc).lower())
    out = []
    for key, heading in LD_SECTIONS:
        items = [i for i in _ld_text(ld.get(key)) if re.sub(r"\W+", " ", html_to_text(i).lower()).strip() not in seen]
        if items:
            body = items[0] if len(items) == 1 else "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"
            out.append(f"<h3>{heading}</h3><p>{body}</p>" if len(items) == 1 else f"<h3>{heading}</h3>{body}")
    return "".join(out)


def job_posting(page: str) -> dict[str, Any] | None:
    """The page's schema.org JobPosting as {title, posted, description, locations, identifier, employment, salary,
    remote}; JSON-LD first, then microdata; None when the page carries neither."""
    ld = _ld_posting(page)
    if ld is not None:
        places = ld.get("jobLocation") or []
        locations = []
        for place in places if isinstance(places, list) else [places]:
            addr = place.get("address") if isinstance(place, dict) else None
            if isinstance(addr, dict):
                locations.append(", ".join(p for p in (_text_of(addr.get(k)) for k in
                                                       ("addressLocality", "addressRegion", "addressCountry")) if p))
        desc = str(ld.get("description") or "")
        if "<" not in desc and "&lt;" in desc:
            desc = _html.unescape(desc)
        desc += _ld_sections(ld, desc)
        pay = ld.get("baseSalary") if isinstance(ld.get("baseSalary"), dict) else {}
        val = pay.get("value") if isinstance(pay.get("value"), dict) else {}
        salary: tuple = (None, None, None)
        lo, hi = val.get("minValue", val.get("value")), val.get("maxValue", val.get("value"))
        if str(val.get("unitText") or "YEAR").upper() == "YEAR" and isinstance(lo, (int, float)) and lo > 0:
            salary = (int(lo), int(hi) if isinstance(hi, (int, float)) else None, pay.get("currency") or "USD")
        employment = ld.get("employmentType")
        return {"title": _text_of(ld.get("title")), "posted": _sitemap_date(ld.get("datePosted")), "description": desc,
                "locations": [loc for loc in locations if loc], "identifier": _ident(ld.get("identifier")),
                "employment": employment[0] if isinstance(employment, list) and employment else employment,
                "salary": salary, "remote": ld.get("jobLocationType")}
    hydrated = _hydrated_posting(page)
    if hydrated is not None:
        return hydrated
    md = _Microdata(page)
    md.feed(page)
    props = md.props
    if not md.inside or not props.get("title"):
        return None
    where = ", ".join(p for p in (_text_of(props.get(k)) for k in ("addressLocality", "addressRegion",
                                                                  "addressCountry")) if p)
    return {"title": html_to_text(props["title"]).strip(), "posted": _sitemap_date(props.get("datePosted")),
            "description": props.get("description") or "", "locations": [where] if where else [],
            "identifier": _text_of(props.get("identifier")) or None, "employment": props.get("employmentType"),
            "salary": (None, None, None), "remote": props.get("jobLocationType")}


def _sitemap_record(company: dict, url: str, post: dict[str, Any], now: datetime) -> dict[str, Any]:
    jd_text = html_to_text(post["description"])
    location = join_locations(post["locations"])
    comp = post["salary"] if post["salary"][0] else parse_comp_text(jd_text)
    return make_record(
        company, title=post["title"], source="sitemap", source_url=url, apply_url=url, ats_vendor="unknown",
        posted_at=post["posted"], location=location, remote=remote_mode(post["remote"], location, post["title"]),
        comp=comp, employment_type=norm_employment(post["employment"]), jd_text=jd_text, now=now,
        req_id=post["identifier"] or url_job_id(url))


def sitemap_run_cap(pending: int, now: datetime) -> int:
    """New pages this run may fetch: the pending ones spread over the hunt runs left today (this one included), at
    least SITEMAP_MAX_FETCH, at most SITEMAP_MAX_FETCH_RUN."""
    from zoneinfo import ZoneInfo
    hour = now.astimezone(ZoneInfo(str(cfg.get("identity.timezone", "UTC")))).hour
    runs_left = max(1, sum(1 for h in SITEMAP_RUN_HOURS if h >= hour))
    return min(SITEMAP_MAX_FETCH_RUN, max(SITEMAP_MAX_FETCH, -(-pending // runs_left)))


def _waiting(entry: dict[str, Any], now: datetime) -> bool:
    """A cached page is skipped: forever when it did not match, for SITEMAP_FAILED_RETRY_DAYS when it failed."""
    if "failed" not in entry:
        return True
    try:
        return (now.date() - date.fromisoformat(str(entry.get("day")))).days < SITEMAP_FAILED_RETRY_DAYS
    except ValueError:
        return False


def _load_cache(path: Path | None) -> dict[str, Any]:
    if not path or not Path(path).is_file():
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def fetch_sitemap(company: dict, http: Any, now: datetime, title_filter: TitleFilter | None,
                  known_urls: Iterable[str] = (), cache_path: Path | None = None) -> BoardResult:
    """robots.txt, the sitemap, then one polite fetch per new title-matched job page (module docstring)."""
    slug = company["slug"]
    url = sitemap_url(company.get("board_token"))
    parts = urlparse(url)
    host, origin = parts.hostname, f"{parts.scheme}://{parts.netloc}"
    res = BoardResult(slug, "sitemap")
    sent = [0]
    status, robots = http.get_text(f"{origin}/robots.txt")     # 5xx or no answer raises: RFC 9309 full disallow
    sent[0] += 1
    rules = Robots(None if status >= 400 else robots)
    allowed = rules.allowed
    delay = max([SITEMAP_DELAY_S] + ([rules.delay] if rules.delay else []))

    def get(u: str) -> str:
        if sent[0]:
            left = time_left()
            if left is not None and left < delay:   # the pause would end past the deadline: no sleep, no request
                raise DeadlineReached(f"GET {u}: the run deadline comes before the next {delay:g} s pause")
            _sitemap_sleep(delay)
        sent[0] += 1
        code, text = http.get_text(u, headers=HTML_ACCEPT)
        if code >= 400:
            raise FetchError(f"GET {u}: HTTP {code}")
        if len(text) > SITEMAP_MAX_BYTES:
            raise FetchError(f"GET {u}: larger than {SITEMAP_MAX_BYTES} bytes")
        return text

    if not allowed(url):
        res.errors.append(f"{slug} (sitemap): robots.txt disallows {url}")
        return res
    top = get(url)
    kind, locs = parse_sitemap(top)
    lastmod = dict(parse_sitemap_entries(top))
    whole = True
    if kind == "sitemapindex":
        children, locs = locs, []
        if len(children) > SITEMAP_MAX_CHILDREN:
            whole = False
            res.notes.append(f"{slug}: sitemap index lists {len(children)} sitemaps; read {SITEMAP_MAX_CHILDREN}")
        for i, child in enumerate(children[:SITEMAP_MAX_CHILDREN]):
            if past_deadline():                 # the run's deadline also cuts between child sitemaps
                whole, res.cut = False, True
                res.notes.append(f"{slug}: run deadline reached; {min(len(children), SITEMAP_MAX_CHILDREN) - i} "
                                 "child sitemap(s) left for the next run")
                break
            if urlparse(child).hostname != host or not allowed(child):
                whole = False
                res.notes.append(f"{slug}: child sitemap skipped (other host or robots.txt): {child}")
                continue
            try:
                child_text = get(child)
                locs += parse_sitemap(child_text)[1]
                lastmod.update(parse_sitemap_entries(child_text))
            except DeadlineReached:
                whole, res.cut = False, True
                res.notes.append(f"{slug}: run deadline reached; {min(len(children), SITEMAP_MAX_CHILDREN) - i} "
                                 "child sitemap(s) left for the next run")
                break
            except (FetchError, ET.ParseError) as exc:
                whole = False
                res.notes.append(f"{slug}: child sitemap failed: {exc}")
    pattern = re.compile(str(company.get("job_url_pattern") or SITEMAP_JOB_PATH))
    jobs = list(dict.fromkeys(u for u in locs if urlparse(u).hostname == host and pattern.search(urlparse(u).path)))
    # the page cap reads the newest lastmod first (stable for equal or missing dates)
    jobs.sort(key=lambda u: lastmod.get(u, ""), reverse=True)
    res.all_urls.update(jobs)
    res.complete = bool(jobs) and whole
    if not jobs:
        res.notes.append(f"{slug}: the sitemap lists no job pages (nothing closed)")
    known = set(known_urls)
    cache = _load_cache(cache_path)
    screen_urls = company.get("url_titles", True) is not False
    fresh: list[dict[str, Any]] = []
    fetched = left = 0
    cut = False                                     # the run's (or this board's) deadline stopped the page reads

    def candidate(u: str) -> bool:
        return (u not in known and not (u in cache and _waiting(cache[u], now))
                and not (title_filter is not None and screen_urls and not _slug_passes(title_filter, u)) and allowed(u))
    cap = sitemap_run_cap(sum(1 for u in jobs if candidate(u)), now)
    for job_url in jobs:
        if job_url in known:                        # re-sighted: last_seen moves, the page is not fetched again
            res.records.append({"id": None, "source_url": job_url, "_resight": True})
            continue
        if job_url in cache and _waiting(cache[job_url], now):   # did not match (never again) or failed (waits)
            continue
        if title_filter is not None and screen_urls and not _slug_passes(title_filter, job_url):
            continue
        if not allowed(job_url):
            res.notes.append(f"{slug}: robots.txt disallows {job_url}")
            continue
        if delay > SITEMAP_MAX_DELAY_S or fetched >= cap or cut or past_deadline():
            left += 1
            cut = cut or (delay <= SITEMAP_MAX_DELAY_S and fetched < cap)
            continue
        fetched += 1
        try:
            post = job_posting(get(job_url))
        except DeadlineReached:                 # not a failed page: it waits for the next run, uncached
            fetched -= 1
            left += 1
            cut = True
            continue
        except FetchError as exc:
            res.notes.append(f"{slug}: page failed: {exc}")
            cache[job_url] = {"failed": str(exc)[:120], "day": iso_utc(now)[:10]}
            continue
        if post is None or not post["title"]:
            res.notes.append(f"{slug}: no JobPosting data on {job_url}")
            cache[job_url] = {"failed": "no JobPosting data", "day": iso_utc(now)[:10]}
            continue
        rec = _sitemap_record(company, job_url, post, now)
        if title_filter is None or title_filter.matches(rec["title"]):
            fresh.append(rec)
        else:
            cache[job_url] = {"title": rec["title"], "day": iso_utc(now)[:10]}
    res.cut = res.cut or cut
    if left:
        why = (f"crawl-delay {delay:g} s" if delay > SITEMAP_MAX_DELAY_S else
               f"time limit after {fetched} page(s)" if cut else f"cap {cap}")
        res.notes.append(f"{slug}: {left} new matching page(s) left for the next run ({why})")
    disambiguate_ids(fresh)
    res.records.extend(fresh)
    if cache_path is not None:
        live = set(jobs)
        kept = {u: v for u, v in cache.items() if u in live}   # postings gone from the sitemap drop out
        Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
        Path(cache_path).write_text(json.dumps(kept, indent=1, sort_keys=True), encoding="utf-8")
    return res


AMAZON_PAGE = 100
AMAZON_DELAY_S = 2.0
AMAZON_MAX_PAGES = 5                  # per query
AMAZON_QUERIES = tuple(dict(q) for q in (cfg.get("policy.search.amazon_queries") or []))   # [{q, loc}] searches
AMAZON_COUNTRIES = [str(c) for c in (cfg.get("policy.search.amazon_countries") or ["USA"])]


def _amazon_sleep(seconds: float) -> None:
    time.sleep(seconds)


def _amazon_date(text: Any) -> str | None:
    try:
        return datetime.strptime(str(text or "").strip(), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def fetch_amazon(company: dict, http: Any, now: datetime, title_filter: TitleFilter | None) -> BoardResult:
    """amazon.jobs search JSON, one query at a time, paged with a pause; never complete (a search, not the board)."""
    host = str(company.get("board_token") or "www.amazon.jobs").strip().strip("/")
    url = f"https://{host}/en/search.json"
    res = BoardResult(company["slug"], "amazon")
    countries = set(company.get("countries") or AMAZON_COUNTRIES)                  # amazon.jobs country codes
    calls = 0
    for query in company.get("search_queries") or AMAZON_QUERIES:
        if res.cut:
            break
        for page in range(AMAZON_MAX_PAGES):
            if cut_at_deadline(res, "searches") or (calls and polite_wait(res, AMAZON_DELAY_S, "searches", _amazon_sleep)):
                break
            calls += 1
            params = {"base_query": query.get("q", ""), "result_limit": AMAZON_PAGE, "offset": page * AMAZON_PAGE}
            if query.get("loc"):
                params["loc_query"] = query["loc"]
            try:
                data = http.get_json(url, params=params) or {}
            except DeadlineReached:
                cut_at_deadline(res, "searches", force=True)
                break
            jobs = data.get("jobs") or []
            for job in jobs:
                path = str(job.get("job_path") or "")
                if not path:
                    continue
                link = f"https://{host}{path}"
                res.all_urls.add(link)
                title = job.get("title") or ""
                if title_filter is not None and not title_filter.matches(title):
                    continue
                if str(job.get("country_code") or "") not in countries:    # the search's location is loose
                    continue
                jd_text = html_to_text("\n\n".join(str(job.get(k) or "") for k in
                                                     ("description", "basic_qualifications", "preferred_qualifications")))
                location = job.get("normalized_location") or job.get("location")
                res.records.append(make_record(
                    company, title=title, source="amazon", source_url=link, apply_url=link, ats_vendor="other",
                    posted_at=_amazon_date(job.get("posted_date")), location=location,
                    remote=remote_mode(location, title), comp=parse_comp_text(jd_text),
                    employment_type=norm_employment(job.get("job_schedule_type")), jd_text=jd_text, now=now,
                    req_id=job.get("id_icims") or job.get("id")))
            hits = int(data.get("hits") or 0)
            if not jobs or (page + 1) * AMAZON_PAGE >= hits:
                break
    res.complete = False
    records = {r["source_url"]: r for r in res.records}          # a job found by two queries counts once
    res.records = list(records.values())
    disambiguate_ids(res.records)
    return res


def workday_endpoint(entry: dict[str, Any]) -> tuple[str, str, str]:
    """(host, tenant, site). Accepts board_token 'host/tenant/site' (targets/companies.yaml),
    'tenant.wdN/site' (or | : separators), or careers_url 'https://tenant.wdN.myworkdayjobs.com/[xx-XX/]site'."""
    for candidate in (entry.get("board_token"), entry.get("careers_url")):
        if not candidate:
            continue
        s = str(candidate).strip().rstrip("/")
        m = re.match(r"^(?:https?://)?((?:[\w-]+\.)?(wd\d+)\.myworkday(?:jobs|site)\.com)/([^/]+)/([^/?#]+)$", s)
        if m and not s.startswith("http"):
            return m.group(1), m.group(3), m.group(4)             # host/tenant/site
        m = re.match(r"^https?://(([\w-]+)\.(wd\d+)\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)", s)
        if m:
            return m.group(1), m.group(2), m.group(4)
        m = re.match(r"^([^./|:\s]+)[./|:](wd\d+)[./|:]([^/|:\s]+)$", s)
        if m:
            return f"{m.group(1)}.{m.group(2)}.myworkdayjobs.com", m.group(1), m.group(3)
    raise ValueError("workday needs board_token 'host/tenant/site' or 'tenant.wdN/site', or careers_url "
                     "'https://tenant.wdN.myworkdayjobs.com/site'")


def parse_workday_board(entry: dict[str, Any]) -> tuple[str, str, str]:
    """(tenant, wdN, site) for the common *.wdN.myworkdayjobs.com hosts."""
    host, tenant, site = workday_endpoint(entry)
    m = re.search(r"\.(wd\d+)\.", host)
    return tenant, (m.group(1) if m else ""), site


def _workday_posted(info: dict, posted_on: str | None, now: datetime) -> str | None:
    if info.get("startDate"):
        return str(info["startDate"])[:10]
    text = str(posted_on or info.get("postedOn") or "").lower()
    if "today" in text:
        return now.date().isoformat()
    if "yesterday" in text:
        return (now - timedelta(days=1)).date().isoformat()
    m = re.search(r"posted (\d+) days? ago", text)
    if m:
        return (now - timedelta(days=int(m.group(1)))).date().isoformat()
    return None      # "30+ Days Ago" is not a date


# The run's fetch deadline, set by pipeline.py around its fetch loop; the long readers check it between pages so
# one giant board cannot eat the run (None: no deadline, as a CLI or test run). board speed: a board read in
# fetch_boards sets its own deadline for its thread only (set_deadline(..., board=True)), over the run's.
_DEADLINE: list[Any] = [None, None]
_BOARD = threading.local()


def set_deadline(clock: Any, deadline: float | None, board: bool = False) -> None:
    """The run's deadline (every thread), or with `board` this thread's own until cleared (clear_board_deadline)."""
    if board:
        _BOARD.pair = (clock, deadline)
    else:
        _DEADLINE[0], _DEADLINE[1] = clock, deadline


def clear_board_deadline() -> None:
    _BOARD.pair = None


def _deadline_pair() -> tuple[Any, Any]:
    pair = getattr(_BOARD, "pair", None)
    return pair if pair is not None else (_DEADLINE[0], _DEADLINE[1])


def past_deadline() -> bool:
    clock, deadline = _deadline_pair()
    return deadline is not None and clock is not None and clock() > deadline


def time_left() -> float | None:
    """Seconds to the deadline (negative once past); None without one."""
    clock, deadline = _deadline_pair()
    return None if deadline is None or clock is None else deadline - clock()


def cut_at_deadline(res: "BoardResult", what: str, force: bool = False) -> bool:
    """The check every page and detail loop makes. Past the deadline (or `force`, a DeadlineReached caught in the
    loop) the board is marked cut: it keeps what it read in full, closes nothing (incomplete) and says what is left."""
    if not (force or past_deadline()):
        return False
    res.cut, res.complete = True, False
    if not any("run deadline" in n for n in res.notes):
        res.notes.append(f"{res.company_slug}: cut at the run deadline ({what}); the rest is left for the next run")
    return True


def polite_wait(res: "BoardResult", delay: float, what: str, sleep: Any) -> bool:
    """Sleep `delay` before the next request unless the deadline comes first: then cut instead (True). A pause is never
    shortened, so a crawl-delay is always honoured."""
    left = time_left()
    if left is not None and left < delay:
        return cut_at_deadline(res, what, force=True)
    sleep(delay)
    return False


# The searchText queries that read a Workday board too large for the page cap (the roles the owner targets, from
# policy.search.role_searches; with none configured, one generic query)
WORKDAY_ROLE_SEARCHES = tuple(str(t) for t in (cfg.get("policy.search.role_searches") or ["engineer"]))


def fetch_workday(company: dict, http: Any, now: datetime, title_filter: TitleFilter | None,
                  page_size: int = 20, max_pages: int = 20, known_urls: Iterable[str] = ()) -> BoardResult:
    hostname, tenant, site = workday_endpoint(company)
    host = f"https://{hostname}"
    base = f"{host}/wday/cxs/{tenant}/{site}"
    search = str(company.get("search_text") or "")
    res = BoardResult(company["slug"], "workday")
    known = frozenset(known_urls)

    def listing(text: str, stop_after_first_if_over: bool = False) -> tuple[list[dict], int | None]:
        rows: list[dict] = []
        total: int | None = None
        offset = pages = 0
        while pages < max_pages:
            if cut_at_deadline(res, "listing"):
                break
            data = http.post_json(base + "/jobs", {"appliedFacets": {}, "limit": page_size,
                                                   "offset": offset, "searchText": text}) or {}
            pages += 1
            batch = data.get("jobPostings") or []
            if total is None:                  # later pages report total=0
                total = int(data.get("total") or 0)
            rows.extend(batch)
            offset += page_size
            if not batch or offset >= total or (stop_after_first_if_over and total > page_size * max_pages):
                break
        return rows, total

    postings, total = listing(search, stop_after_first_if_over=not search and title_filter is not None)
    res.complete = bool(total is not None and len(postings) >= total and not search)
    if not search and title_filter is not None and total is not None and total > page_size * max_pages:
        # over the page cap the whole list cannot be read; the role searches can (a large board can list thousands)
        by_path = {p.get("externalPath"): p for p in postings}
        for text in WORKDAY_ROLE_SEARCHES:
            if cut_at_deadline(res, f"role searches, before {text!r}"):
                break
            rows, _ = listing(text)
            by_path.update({p.get("externalPath"): p for p in rows if p.get("externalPath") not in by_path})
        postings = list(by_path.values())
        res.notes.append(f"{company['slug']}: workday board has {total} postings, over the page cap "
                         f"({page_size * max_pages}): read by {len(WORKDAY_ROLE_SEARCHES)} role searches "
                         f"({len(postings)} postings listed)")
    elif total is not None and len(postings) < total:
        res.notes.append(f"{company['slug']}: workday listed {len(postings)} of {total} (page cap)")
    for post in postings:
        path = post.get("externalPath") or ""
        public = (f"{host}/recruiting/{tenant}/{site}{path}" if "myworkdaysite.com" in hostname
                  else f"{host}/{site}{path}")
        res.all_urls.add(path or public)       # external paths: the public URL differs by host flavour
        if title_filter is not None and not title_filter.matches(post.get("title") or ""):
            continue
        if public in known:                    # re-sighted (as the sitemap reader does): no detail fetch per run
            res.records.append({"id": None, "source_url": public, "_resight": True})
            continue
        if cut_at_deadline(res, "detail reads"):   # a detail loop must not run on past the deadline
            break
        try:
            info = (http.get_json(base + path) or {}).get("jobPostingInfo") or {}
        except DeadlineReached:                # never a list-level record without its JD: it waits for the next run
            cut_at_deadline(res, "detail reads", force=True)
            break
        except Exception as exc:  # keep the list-level record; the detail is a bonus
            res.notes.append(f"{company['slug']}: workday detail failed for {path}: {exc}")
            info = {}
        req = info.get("jobReqId") or ((post.get("bulletFields") or [None])[0])
        if not req:
            m = re.search(r"_([A-Z]*R?\d{4,})$", path)
            req = m.group(1) if m else None
        location = join_locations([info.get("location") or post.get("locationsText")] +
                                  list(info.get("additionalLocations") or []))
        jd_text = html_to_text(info.get("jobDescription") or "")
        url = info.get("externalUrl") or public
        res.records.append(make_record(
            company, title=info.get("title") or post.get("title") or "", source="workday",
            source_url=url, apply_url=url, ats_vendor="workday",
            posted_at=_workday_posted(info, post.get("postedOn"), now), location=location,
            remote=remote_mode(location, info.get("remoteType")), comp=parse_comp_text(jd_text),
            employment_type=norm_employment(info.get("timeType")), jd_text=jd_text, now=now, req_id=req))
    disambiguate_ids(res.records)
    return res


ORACLE_PAGE = 25                       # requisitions per search page (the candidate site's own page size)
ORACLE_MAX_PAGES = 4                   # per role search


def fetch_oracle(company: dict, http: Any, now: datetime, title_filter: TitleFilter | None,
                 known_urls: Iterable[str] = ()) -> BoardResult:
    """Oracle Recruiting Cloud, the public REST API its candidate site calls: board_token
    "<host>|<siteNumber>" (ats_detect.py); optional oracle_location / oracle_exclude_location (regexes on the posting's
    primary location) split companies that share one tenant. The role searches of WORKDAY_ROLE_SEARCHES, newest first, up to
    ORACLE_MAX_PAGES pages each; a known posting is re-sighted without its detail call; a new one is read in full
    (description, responsibilities, qualifications). Never complete: a search is not the whole board."""
    host, _, site = str(company.get("board_token") or "").partition("|")
    if not host or not site:
        raise FetchError(f"oracle board_token {company.get('board_token')!r} needs '<host>|<site>' (the site number "
                         "from the candidate site's URL)")
    api = f"https://{host}/hcmRestApi/resources/latest"
    res = BoardResult(company["slug"], "oracle")
    known = frozenset(known_urls)
    keep_at = re.compile(str(company["oracle_location"]), re.I) if company.get("oracle_location") else None
    drop_at = re.compile(str(company["oracle_exclude_location"]), re.I) if company.get("oracle_exclude_location") else None
    reqs: dict[str, dict[str, Any]] = {}

    def search(site_no: str, text: str, page: int) -> dict[str, Any]:
        finder = (f"findReqs;siteNumber={site_no},limit={ORACLE_PAGE},offset={page * ORACLE_PAGE},"
                  f'keyword="{text}",sortBy=POSTING_DATES_DESC')
        data = http.get_json(f"{api}/recruitingCEJobRequisitions",
                             params={"onlyData": "true", "expand": "requisitionList.secondaryLocations",
                                     "finder": finder}) or {}
        return (data.get("items") or [{}])[0]
    first_ids: set[str] | None = None
    for text in WORKDAY_ROLE_SEARCHES:
        if cut_at_deadline(res, f"searches, before {text!r}"):
            break
        for page in range(ORACLE_MAX_PAGES):
            if cut_at_deadline(res, f"searches, in {text!r}"):
                break
            item = search(site, text, page)
            batch = item.get("requisitionList") or []
            if first_ids is None:
                first_ids = {str(r.get("Id")) for r in batch}
            for r in batch:
                reqs.setdefault(str(r.get("Id")), r)
            if len(batch) < ORACLE_PAGE or (page + 1) * ORACLE_PAGE >= int(item.get("TotalJobsCount") or 0):
                break
    if first_ids and not host.endswith(".oraclecloud.com") and not res.cut:
        # a custom domain may serve its default site whatever siteNumber says
        probe = {str(r.get("Id")) for r in search("ZZ_NO_SUCH_SITE", WORKDAY_ROLE_SEARCHES[0], 0).get("requisitionList") or []}
        if probe == first_ids:
            res.notes.append(f"{company['slug']}: {host} ignores the site number (a made-up site returns the same "
                             "postings): split companies on one tenant with oracle_location / oracle_exclude_location")
    for rid, r in reqs.items():
        where = str(r.get("PrimaryLocation") or "")
        if (keep_at and not keep_at.search(where)) or (drop_at and drop_at.search(where)):
            continue
        url = f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{rid}"
        res.all_urls.add(url)
        if title_filter is not None and not title_filter.matches(str(r.get("Title") or "")):
            continue
        if url in known:
            res.records.append({"id": None, "source_url": url, "_resight": True})
            continue
        if cut_at_deadline(res, "detail reads"):
            break
        try:
            d = ((http.get_json(f"{api}/recruitingCEJobRequisitionDetails",
                                params={"expand": "all", "onlyData": "true",
                                        "finder": f'ById;Id="{rid}",siteNumber={site}'}) or {}).get("items") or [{}])[0]
        except DeadlineReached:
            cut_at_deadline(res, "detail reads", force=True)
            break
        except FetchError as exc:            # keep the list-level record; the detail is a bonus
            res.notes.append(f"{company['slug']}: oracle detail failed for {rid}: {exc}")
            d = {}
        parts = [("", d.get("ExternalDescriptionStr")), ("Responsibilities", d.get("ExternalResponsibilitiesStr")),
                 ("Qualifications", d.get("ExternalQualificationsStr"))]
        html = "".join((f"<h3>{h}</h3>" if h else "") + str(v) for h, v in parts if v) or str(r.get("ShortDescriptionStr") or "")
        jd_text = html_to_text(html)
        location = d.get("PrimaryLocation") or r.get("PrimaryLocation")
        res.records.append(make_record(
            company, title=d.get("Title") or r.get("Title") or "", source="oracle", source_url=url, apply_url=url,
            ats_vendor="oracle", posted_at=_sitemap_date(d.get("ExternalPostedStartDate") or r.get("PostedDate")),
            location=location, remote=remote_mode(location, d.get("WorkplaceType") or r.get("WorkplaceType")),
            comp=parse_comp_text(jd_text), employment_type=norm_employment(d.get("JobSchedule") or d.get("JobType")),
            jd_text=jd_text, now=now, req_id=rid))
    disambiguate_ids(res.records)
    return res


EIGHTFOLD_MAX_PAGES = 4                # per role search (the PCSX page is 10 positions)


def fetch_eightfold(company: dict, http: Any, now: datetime, title_filter: TitleFilter | None,
                    known_urls: Iterable[str] = ()) -> BoardResult:
    """Eightfold's PCSX API, the one its careers site calls (robots.txt allows /api/pcsx): board_token
    "<careers host>|<company domain>" (ats_detect.py). The role searches, a known position re-sighted without its detail
    call, a new one read in full. Never complete: a search is not the whole board."""
    host, _, domain = str(company.get("board_token") or "").partition("|")
    if not host or not domain:
        raise FetchError(f"eightfold board_token {company.get('board_token')!r} needs '<careers host>|<domain>'")
    api = f"https://{host}/api/pcsx"
    res = BoardResult(company["slug"], "eightfold")
    known = frozenset(known_urls)
    positions: dict[str, dict[str, Any]] = {}
    for text in WORKDAY_ROLE_SEARCHES:
        if cut_at_deadline(res, f"searches, before {text!r}"):
            break
        start = 0
        for _ in range(EIGHTFOLD_MAX_PAGES):
            if cut_at_deadline(res, f"searches, in {text!r}"):
                break
            data = (http.get_json(f"{api}/search", params={"domain": domain, "query": text, "location": "",
                                                            "start": start}) or {}).get("data") or {}
            batch = data.get("positions") or []
            for p in batch:
                positions.setdefault(str(p.get("id")), p)
            start += len(batch)
            if not batch or start >= int(data.get("count") or 0):
                break
    for pid, p in positions.items():
        url = f"https://{host}{p.get('positionUrl') or f'/careers/job/{pid}'}"
        res.all_urls.add(url)
        if title_filter is not None and not title_filter.matches(str(p.get("name") or "")):
            continue
        if url in known:
            res.records.append({"id": None, "source_url": url, "_resight": True})
            continue
        if cut_at_deadline(res, "detail reads"):
            break
        try:
            d = (http.get_json(f"{api}/position_details", params={"position_id": pid, "domain": domain, "hl": "en"})
                 or {}).get("data") or {}
        except DeadlineReached:
            cut_at_deadline(res, "detail reads", force=True)
            break
        except FetchError as exc:            # keep the list-level record; the detail is a bonus
            res.notes.append(f"{company['slug']}: eightfold detail failed for {pid}: {exc}")
            d = {}
        jd_text = html_to_text(str(d.get("jobDescription") or ""))
        location = join_locations(list(d.get("locations") or p.get("locations") or []))
        ts = d.get("postedTs") or p.get("postedTs")
        posted = datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat() if str(ts or "").isdigit() else None
        res.records.append(make_record(
            company, title=d.get("name") or p.get("name") or "", source="eightfold", source_url=url, apply_url=url,
            ats_vendor="eightfold", posted_at=posted, location=location,
            remote=remote_mode(location, d.get("workLocationOption") or p.get("workLocationOption")),
            comp=parse_comp_text(jd_text), employment_type=None, jd_text=jd_text, now=now,
            req_id=p.get("displayJobId") or pid))
    disambiguate_ids(res.records)
    return res


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby, "workable": fetch_workable, "smartrecruiters": fetch_smartrecruiters, "rippling": fetch_rippling, "gem": fetch_gem, "pinpoint": fetch_pinpoint, "teamtailor": fetch_teamtailor, "recruitee": fetch_recruitee,
            "workday": fetch_workday, "jibe": fetch_jibe, "sitemap": fetch_sitemap, "amazon": fetch_amazon,
            "oracle": fetch_oracle, "eightfold": fetch_eightfold}


def fetch_company(company: dict, http: Any, now: datetime, title_filter: TitleFilter | None,
                  **kw: Any) -> BoardResult:
    vendor = str(company.get("ats_vendor") or "unknown").lower()
    slug = str(company.get("slug") or slugify(company.get("name", "unknown")))
    company = dict(company, slug=slug)
    fetcher = FETCHERS.get(vendor)
    if isinstance(title_filter, TitleFilter) and ai_first(company):
        # at a listed AI-first company the extra title phrases in policy.title_filter.ai_first_include count too
        # (for example a plain product engineer seat is a software engineer seat; "design engineer" stays out)
        extra = [title_pattern(p) for p in AI_FIRST_INCLUDE]
        title_filter = TitleFilter(list(title_filter.include) + extra, list(title_filter.exclude))
    if fetcher is None:
        return BoardResult(slug, vendor, notes=[f"{slug}: unsupported ats_vendor '{vendor}' (skipped)"])
    if not company.get("board_token") and not (vendor == "workday" and company.get("careers_url")):
        # companies.yaml marks unverified boards with board_token: null; polling a guessed token only 404s
        return BoardResult(slug, vendor, notes=[f"{slug}: no board_token (unverified board, skipped)"])
    if vendor == "workday":
        try:
            workday_endpoint(company)
        except ValueError:
            return BoardResult(slug, vendor, notes=[f"{slug}: workday board not recognised (skipped)"])
    try:
        return fetcher(company, http, now, title_filter, **kw)
    except DeadlineReached:                  # CS83: the backstop; a deferral, never a board error
        res = BoardResult(slug, vendor)
        cut_at_deadline(res, "mid-request", force=True)
        return res
    except Exception as exc:
        return BoardResult(slug, vendor, errors=[f"{slug} ({vendor}): {exc}"])


# =============================================================================== many boards at once
# board speed (setup part 4): a sequential run spent its 12-minute fetch share on a dozen many-request boards
# (Workday role searches, sitemap pages) while the one-request API boards waited, so about half the watchlist was
# deferred each run. fetch_boards reads boards on different hosts at the same time and never two boards of one host at
# once, so each host sees the same pace as before (Http's per-host interval, crawl-delays, backoff all unchanged).
API_HOSTS = {"greenhouse": "boards-api.greenhouse.io", "lever": "api.lever.co", "ashby": "api.ashbyhq.com",
             "workable": "apply.workable.com", "smartrecruiters": "api.smartrecruiters.com",
             "rippling": "api.rippling.com", "gem": "api.gem.com"}
# boards that make many requests (searches, pages, detail reads): they never take the last HEAVY_RESERVE threads
HEAVY_VENDORS = ("workday", "sitemap", "oracle", "eightfold", "jibe", "amazon", "smartrecruiters", "teamtailor")
FETCH_WORKERS = 8
HEAVY_RESERVE = 2
# Board speed: a hundred Ashby or Greenhouse boards share one API host each, so reading one board per host
# one after another is slow. These hosts take a few boards at once; Http's per-host interval still spaces every
# request (min_interval, under its lock), so the request rate to each host is what it was; only the waits overlap.
HOST_SLOTS = {"api.ashbyhq.com": 3, "boards-api.greenhouse.io": 3, "api.lever.co": 2}
# A must-read board (the caller's must_read: dream boards and the one-request API boards) is never deferred: it starts
# even after the run deadline, with this much time of its own.
MUST_READ_GRACE_S = 60.0


def board_host(company: dict[str, Any]) -> str:
    """The host a board's requests go to (the one-board-per-host key); `vendor:slug` when it cannot be told."""
    vendor = str(company.get("ats_vendor") or "unknown").lower()
    token = str(company.get("board_token") or company.get("slug") or "").strip()
    host = ""
    try:
        if vendor in API_HOSTS:
            host = API_HOSTS[vendor]
        elif vendor == "workday":
            host = workday_endpoint(company)[0]
        elif vendor in ("oracle", "eightfold"):
            host = token.partition("|")[0]
        elif vendor == "pinpoint":
            host = f"{token}.pinpointhq.com"
        elif vendor == "recruitee":
            host = f"{token}.recruitee.com"
        elif vendor == "teamtailor":
            host = urlparse(teamtailor_feed_url(token, 0)).netloc
        elif vendor == "sitemap":
            host = urlparse(sitemap_url(token)).netloc
        elif vendor == "amazon":
            host = str(company.get("board_token") or "www.amazon.jobs")
        elif vendor == "jibe":
            host = token
    except ValueError:
        host = ""
    host = host.strip().strip("/").lower()
    return host or f"{vendor}:{company.get('slug')}"


@dataclass
class BoardRead:
    company: dict[str, Any]
    result: BoardResult | None = None      # None: not started before the run deadline (deferred to the next run)
    seconds: float = 0.0
    run_cut: bool = False                  # the run's deadline (not the board's own cap) stopped the read


def fetch_boards(companies: list[dict[str, Any]], http: Any, now: datetime, title_filter: TitleFilter | None,
                 kw: Any = None, *, workers: int = 1, clock: Any = time.monotonic, deadline: float | None = None,
                 own_deadline: Any = None, filter_for: Any = None, fetch: Any = None,
                 must_read: Any = None, host_slots: dict[str, int] | None = None) -> list[BoardRead]:
    """Read the boards, in the given order of priority; one BoardRead per company, in the same order (the output never
    depends on which thread finished first). kw(company): the vendor keywords; own_deadline(company): the board's own
    deadline (default the run's); filter_for(company): its title filter (default title_filter); fetch: the reader
    (default fetch_company, looked up at call time). No board starts once
    `deadline` has passed (it is deferred), except a must_read(company) board: it is never deferred and, started past
    the deadline, reads with MUST_READ_GRACE_S of its own. workers=1 reads one board at a time, as before; more reads
    boards of different hosts at once, one board per host (host_slots, e.g. HOST_SLOTS: a few on a shared API host), the
    many-request vendors on at most workers - HEAVY_RESERVE threads."""
    kw = kw or (lambda c: {})
    own_deadline = own_deadline or (lambda c: deadline)
    filter_for = filter_for or (lambda c: title_filter)
    must_read = must_read or (lambda c: False)
    reads = [BoardRead(c) for c in companies]
    must = [bool(must_read(c)) for c in companies]
    slots = host_slots or {}

    def past() -> bool:
        return deadline is not None and clock() > deadline

    def read(r: BoardRead, must_board: bool = False) -> None:
        t0 = clock()
        own = own_deadline(r.company)
        if must_board and own is not None and clock() >= own:
            own = clock() + MUST_READ_GRACE_S
        set_deadline(clock, own, board=True)
        try:
            r.result = (fetch or fetch_company)(r.company, http, now, filter_for(r.company), **kw(r.company))
        finally:
            clear_board_deadline()
        r.seconds = round(clock() - t0, 1)
        r.run_cut = bool(r.result.cut and deadline is not None and (own is None or own >= deadline or clock() > deadline))

    if workers <= 1:
        for i, r in enumerate(reads):
            if past() and not must[i]:
                continue
            read(r, must[i])
        return reads

    hosts = [board_host(c) for c in companies]
    heavy = [str(c.get("ats_vendor") or "").lower() in HEAVY_VENDORS for c in companies]
    heavy_cap = max(1, workers - HEAVY_RESERVE)
    pending = list(range(len(reads)))
    busy: dict[str, int] = {}                       # host -> boards being read there
    running = [0]                                   # many-request boards being read
    failed: list[BaseException] = []
    cond = threading.Condition()

    def take() -> int | None:
        with cond:
            while pending:
                if failed:
                    pending.clear()
                    cond.notify_all()
                    return None
                if past() and any(not must[i] for i in pending):
                    pending[:] = [i for i in pending if must[i]]     # the rest are deferred; must-read boards stay
                    cond.notify_all()
                    if not pending:
                        return None
                for i in pending:
                    if busy.get(hosts[i], 0) < slots.get(hosts[i], 1) and \
                            not (heavy[i] and running[0] >= heavy_cap):
                        pending.remove(i)
                        busy[hosts[i]] = busy.get(hosts[i], 0) + 1
                        running[0] += heavy[i]
                        return i
                cond.wait()                         # a board in progress frees its host or its slot
            return None

    def worker() -> None:
        while (i := take()) is not None:
            try:
                read(reads[i], must[i])
            except BaseException as exc:            # a bug, not a board error (fetch_company keeps those): re-raised
                with cond:
                    failed.append(exc)
            finally:
                with cond:
                    busy[hosts[i]] -= 1
                    running[0] -= heavy[i]
                    cond.notify_all()

    threads = [threading.Thread(target=worker, name=f"board-{n}", daemon=True) for n in range(min(workers, len(reads)))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if failed:
        raise failed[0]
    return reads


# =============================================================================== persistence
def load_jobs(root: Path) -> dict[str, dict[str, Any]]:
    jobs: dict[str, dict[str, Any]] = {}
    for path, meta, body in iter_records(Path(root) / "jobs"):
        rec = dict(meta)
        rec.setdefault("id", path.stem)
        rec["_body"] = body
        rec["_path"] = path
        rec["_jd_text"] = jd_text_from_body(body)
        jobs[path.stem] = rec
    return jobs


def save_job(root: Path, rec: dict[str, Any]) -> Path:
    """Write a job record whole or not at all: a temporary file beside it, then an atomic rename, so a run killed
    mid-write never leaves half a record."""
    import os
    from graph_links import stamp_path
    path = Path(root) / "jobs" / f"{rec['id']}.md"
    meta = {k: rec.get(k) for k in JOB_FIELDS}
    meta.update({k: v for k, v in rec.items() if k not in meta and not k.startswith("_")})
    stamp_path(path, meta)                   # write_record's type and links stamp, kept
    text = fm.dump(meta, rec.get("_body") or "")
    try:                                     # an unchanged record is not rewritten (no mtime, no churn)
        if path.read_text(encoding="utf-8") == text:
            return path
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def _merge(cur: dict[str, Any], rec: dict[str, Any], now: datetime) -> tuple[dict[str, Any], bool]:
    merged = dict(cur)
    for key in FETCH_OWNED:
        if key == "freshness_hours":                 # counted from this run's clock in make_record;
            continue                                 # a re-sighting keeps the first-sight value (below)
        if rec.get(key) is not None:
            merged[key] = rec[key]
    # A re-sighting must not rewrite every job file on every run (last_seen to the second,
    # freshness_hours recounted, though nothing reads it). last_seen moves when the UTC day changes (its readers,
    # scout_review's liveness and role_hub's "last seen", read the day); freshness_hours keeps its first-sight meaning
    # (posting to first sight) and is recounted only when posted_at itself changes. Freshness for ranking is always
    # computed from posted_at at read time (fresh10, hunt_score), so no rank moves.
    seen = iso_utc(now)
    if str(cur.get("last_seen") or "")[:10] != seen[:10]:
        merged["last_seen"] = seen
    if merged.get("posted_at") != cur.get("posted_at"):
        first = parse_ts(merged.get("first_seen")) or now
        merged["freshness_hours"] = freshness_hours(merged.get("posted_at") or merged.get("first_seen"), first)
    if merged.get("status") == "closed":
        merged["status"] = "discovered"
        merged["reopened_at"] = iso_utc(now)
    changed = bool(rec.get("jd_hash")) and rec.get("jd_hash") != cur.get("jd_hash")
    if changed:
        merged["jd_hash"] = rec["jd_hash"]
        merged["jd_changed_at"] = iso_utc(now)
        new_block = wrap_untrusted(rec.get("_jd_text") or "", "jd", limit=JD_LIMIT)
        body = cur.get("_body") or ""
        merged["_body"] = (_JD_BLOCK.sub(lambda _m: new_block, body, count=1) if _JD_BLOCK.search(body)
                           else build_body(merged, rec.get("_jd_text") or ""))
        merged["_jd_text"] = rec.get("_jd_text") or ""
        if merged["_jd_text"].strip() and "jd-missing" in (merged.get("tags") or []):
            merged["tags"] = [t for t in merged["tags"] if t != "jd-missing"]   # an alert record got its JD
    return merged, changed


def merge_records(existing: dict[str, dict[str, Any]], records: Iterable[dict[str, Any]],
                  now: datetime) -> dict[str, list[str]]:
    """In-memory upsert into `existing` (id -> record): new postings are added, re-sighted ones
    (matched by source_url) refreshed. Never duplicates. Returns ids by outcome."""
    by_url = {r.get("source_url"): r for r in existing.values() if r.get("source_url")}
    out: dict[str, list[str]] = {"new": [], "updated": [], "jd_changed": []}
    for rec in records:
        cur = by_url.get(rec.get("source_url"))
        if cur is None and rec.get("_resight"):
            continue                                # a re-sighting whose record is gone: nothing to refresh
        if cur is None:
            rid = rec["id"]
            if rid in existing:
                rid = f"{rid}-{short_hash(rec['source_url'], n=6)}"
            if rid in existing:
                rid = f"{rec['id']}-{short_hash(rec['source_url'], n=12)}"
            new = dict(rec, id=rid, first_seen=iso_utc(now), last_seen=iso_utc(now))
            existing[rid] = new
            by_url[new["source_url"]] = new
            out["new"].append(rid)
            continue
        merged, changed = _merge(cur, rec, now)
        existing[merged["id"]] = merged
        by_url[merged["source_url"]] = merged
        out["updated"].append(merged["id"])
        if changed:
            out["jd_changed"].append(merged["id"])
    return out


def upsert_jobs(records: Iterable[dict[str, Any]], root: Path, now: datetime,
                dry_run: bool = False) -> dict[str, list[str]]:
    """Write new postings, refresh re-sighted ones (matched by source_url). Never duplicates."""
    existing = load_jobs(root)
    out = merge_records(existing, records, now)
    if not dry_run:
        for rid in out["new"] + out["updated"]:
            save_job(root, existing[rid])
    return out


def close_missing_records(existing: dict[str, dict[str, Any]], results: Iterable[BoardResult],
                          now: datetime, configured: Mapping[str, str] | None = None,
                          flagged: list[str] | None = None) -> list[str]:
    """Postings gone from a fully-listed board -> status closed (open statuses only). With `configured` (company slug
    -> its watchlist ats_vendor), a complete read of a company's CONFIGURED board also retires that company's open
    records from another ATS vendor (the board moved, for example Greenhouse to Ashby): an untouched one is closed, a queued
    one or one an application names is only flagged `board_moved_to` for re-linking. An ad-hoc read of
    another vendor never touches the configured board's records."""
    closed: list[str] = []
    for res in results:
        if not res.complete:
            continue
        for rid, rec in existing.items():
            if rec.get("company_slug") != res.company_slug:
                continue
            source = str(rec.get("source") or "")
            moved = (configured is not None and configured.get(res.company_slug) == res.vendor and source != res.vendor
                     and source in SUPPORTED_VENDORS and rec.get("status") in OPEN_STATUSES)
            if moved:
                if rec.get("status") == "queued" or rec.get("application"):
                    if rec.get("board_moved_to") != res.vendor and flagged is not None:
                        flagged.append(rid)                       # reported once, in the run's receipt
                    rec["board_moved_to"] = res.vendor          # its queue state and application stay; re-link it
                    continue
                rec["status"] = "closed"
                rec["closed_at"] = iso_utc(now)
                rec["closed_reason"] = f"board moved to {res.vendor}"
                closed.append(rid)
                continue
            if source != res.vendor:
                continue
            url = str(rec.get("source_url") or "")
            if rec.get("status") not in OPEN_STATUSES or url in res.all_urls or any(
                    k.startswith("/") and url.endswith(k) for k in res.all_urls):
                continue
            rec["status"] = "closed"
            rec["closed_at"] = iso_utc(now)
            closed.append(rid)
    return sorted(closed)


def close_missing(root: Path, results: Iterable[BoardResult], now: datetime,
                  dry_run: bool = False) -> list[str]:
    jobs = load_jobs(root)
    closed = close_missing_records(jobs, results, now)
    if not dry_run:
        for rid in closed:
            save_job(root, jobs[rid])
    return closed


# =============================================================================== CLI
def _vendor_kw(vendor: str, max_pages: int, known_urls: Iterable[str], cache_dir: Path | None = None,
               slug: str | None = None) -> dict[str, Any]:
    if vendor in ("workday", "oracle", "eightfold"):
        return {"max_pages": max_pages, "known_urls": known_urls} if vendor == "workday" else {"known_urls": known_urls}
    if vendor == "sitemap":
        return {"known_urls": known_urls,
                "cache_path": Path(cache_dir) / f"{slug}.json" if cache_dir is not None and slug else None}
    return {}


def run(companies: list[dict], http: Any, now: datetime, title_filter: TitleFilter | None,
        max_pages: int = 20, known_urls: Iterable[str] = (), cache_dir: Path | None = None) -> list[BoardResult]:
    """known_urls: source URLs already in jobs/ (the sitemap vendor re-sights them without a page fetch);
    cache_dir: where the sitemap vendor remembers pages read that did not match (None: nothing is written)."""
    known = frozenset(known_urls)
    return [fetch_company(c, http, now, title_filter,
                          **_vendor_kw(str(c.get("ats_vendor")).lower(), max_pages, known, cache_dir, c.get("slug")))
            for c in companies]


def main(argv: list[str] | None = None, http: Any = None, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT, help="department root (default: %(default)s)")
    ap.add_argument("--company", action="append", default=[], help="only these watchlist slugs")
    ap.add_argument("--tier", action="append", default=[], help="only watchlist companies of these tiers (dream, strong ...)")
    ap.add_argument("--board", action="append", default=[], help="ad-hoc board vendor:token[:Name]")
    ap.add_argument("--all-titles", action="store_true", help="skip the title filter")
    ap.add_argument("--max-pages", type=int, default=20, help="workday page cap (20 postings/page)")
    ap.add_argument("--dry-run", action="store_true", help="fetch and report; write nothing")
    ap.add_argument("--workers", type=int, default=1,
                    help=f"boards read at once, one per host (pipeline runs use {FETCH_WORKERS}; default %(default)s)")
    ap.add_argument("--list", action="store_true",
                    help="print every posting: [match] or [  -  ], title, URL (sitemap boards: the pages read only)")
    args = ap.parse_args(argv)
    now = now or datetime.now(timezone.utc)
    if args.board:
        companies = [board_from_spec(b) for b in args.board]
    else:
        companies = load_companies(args.root)
        if args.company:
            companies = [c for c in companies if c["slug"] in set(args.company)]
        if args.tier:
            tiers = {t.strip().lower() for t in args.tier}
            companies = [c for c in companies if str(c.get("tier") or "").lower() in tiers]
        if not companies:
            print(f"no companies to fetch (targets/companies.yaml under {args.root} absent or empty)")
            return 0
    title_filter = None if args.all_titles else load_title_filter(args.root)
    if args.list and title_filter is not None:
        # fetch unfiltered and flag each title, so the non-matching postings are seen too; a sitemap board keeps
        # its filter (it decides which pages are fetched, 5 s each), and says so
        listed = [c for c in companies if str(c.get("ats_vendor")).lower() != "sitemap"]
        if len(listed) != len(companies):
            print("note: sitemap boards list only the pages their title filter read")
    known = ({str(r["source_url"]) for r in load_jobs(args.root).values() if r.get("source_url")}
             if any(str(c.get("ats_vendor")).lower() in ("sitemap", "workday", "oracle", "eightfold") for c in companies) else set())
    http = http or Http()
    raw = {str(c.get("slug")) for c in companies if args.list and title_filter is not None
           and str(c.get("ats_vendor")).lower() != "sitemap"}       # fetched unfiltered: the filter is applied here
    known = frozenset(known)
    reads = fetch_boards(companies, http, now, title_filter, workers=args.workers,
                         filter_for=lambda c: None if str(c.get("slug")) in raw else title_filter,
                         kw=lambda c: _vendor_kw(str(c.get("ats_vendor")).lower(), args.max_pages, known,
                                                 None if args.dry_run else args.root / "state" / "sitemap", c.get("slug")))
    results = [r.result for r in reads if r.result is not None]

    def keep(res: BoardResult, r: dict[str, Any]) -> bool:
        return res.company_slug not in raw or title_filter.matches(str(r.get("title") or ""))
    records = [r for res in results for r in res.records if keep(res, r)]
    for res in results:
        line = f"{res.company_slug}: {len(res.all_urls)} on board, {sum(1 for r in res.records if keep(res, r))} match"
        print(line + ("" if not res.notes else "  [" + "; ".join(res.notes) + "]"))
        for err in res.errors:
            print(f"  ERROR {err}")
        if args.list:
            for r in sorted(res.records, key=lambda r: (not keep(res, r), str(r.get("title")))):
                print(f"  [{'match' if keep(res, r) else '  -  '}] {r.get('title')} · {r.get('source_url')}")
    plan = upsert_jobs(records, args.root, now, dry_run=args.dry_run)
    closed = close_missing(args.root, results, now, dry_run=args.dry_run)
    mode = "dry-run (nothing written)" if args.dry_run else "written"
    print(f"{mode}: {len(plan['new'])} new, {len(plan['updated'])} re-sighted, "
          f"{len(plan['jd_changed'])} JD changed, {len(closed)} closed")
    return 1 if results and all(res.errors for res in results) else 0


if __name__ == "__main__":
    sys.exit(main())
