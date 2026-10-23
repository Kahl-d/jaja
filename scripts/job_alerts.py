#!/usr/bin/env python3
"""Job-alert emails from the classified Gmail harvest -> job records (research/job-platforms-2026.md).

Input: state/harvest/*.classified.jsonl (gmail_pull.py harvest + mail_classify.py classify, or the
cloud connector's rows in the same shape); only rows whose classification.label is job_alert are
parsed. The sender decides the platform through targets/platforms.yaml `alert_senders` (an address
or a domain; platform-scout owns that list). LinkedIn, Glassdoor, ZipRecruiter, Welcome to the Jungle
(and Otta), HiringCafe and YC Work at a Startup have their own job-link rules; every other mapped
platform uses the generic rules (known job-board pages plus employer ATS links).

A job card is the text next to one job link. When the harvest row carries `links` (gmail_pull.py keeps
them for job-alert senders, read from the HTML part), those links are preferred: each job link's `card`
is the text of the element the email itself lays around that one job, so an HTML-only alert or a body cut
at the harvest cap still yields every card; a job link with no card goes to review. A row without card
links is read from its body: cards are read before their link (or after it when an
alert puts links first); when the title itself is the link (ZipRecruiter), the text after it up to its
button ("View Details", "1-Click Apply") is the card. A card becomes a record only when, after dropping
badges, ratings, pay, equity, dates, benefits, company stage and size, blurbs and footer lines, exactly one
line names a role (the title) and exactly one line is neither a role nor a place (the company); combined
lines ("Company · City", "Title @ Company", "Title - Company") are split only when the whole lines do not
already give that, and a platform's fixed card order (LAYOUT: company first on Glassdoor and Jobright,
title first elsewhere) settles a title without a role word or a company named like a place. Anything else
stays unparsed and goes to the review list with the card text: never guessed. A job-shaped link with no
card text (a logo, an app badge, a footer link behind an opaque ZipRecruiter token) is counted as a bare
link, not a card. When the stored body was cut at the harvest cap, its last card is never read (review).
Links are only read, never fetched. Indeed links every job through a cts.indeed.com redirect whose path carries the
target as gzip, URL-safe base64 JSON ({"u": target}); that payload is decoded in place (capped, never fetched): a target
with a job key (jk) is that job, a sponsored target without one is keyed on the card's title, company and place (an
Indeed job search URL), and Indeed's one-job mail ("Title (City, ST) @ Company", no card text) is read from its subject.
Google Jobs alert mail ("Job Alerts from Google", platform google-jobs-search) has its own reader (parse_google_alert):
its plain text has no job links, so each card is read by its fixed shape (logo initial; title, wrapped over lines, and
company; place; "via <board>"; "Time icon <date> Work icon <type>"), the search query comes from the subject, the date
is read against the mail's day in the owner's time zone, and a card's URL is the per-card Google link when the harvest kept one (tracking
dropped), else a Google Jobs search for the card's own words. Google links are stored for the owner's browser and never
fetched: Google's terms bar automated reads of its search pages (web_snapshot.py refuses google.com).

Layouts were fitted to a real backlog pull; the synthetic fixtures in tests/fixtures/alerts/layout_rows.yaml
reproduce each one.

Records go through fetch_ats.make_record and the source_url upsert (merge_into): source
<platform>-alert (email-alert for generic platforms), platform <id>, the canonical job URL (tracking
parameters dropped), status discovered, jd_hash null and tag `jd-missing` (fit_gates holds such a
job at review until the JD is read and recorded with jobs_manual.py). A job already known from
another source gets the platform added to its `seen_on`; a new tracking link for the same card on
the same platform re-sights the existing record. Injection text in a card is dropped and the record
tagged `injection-suspect`.

Ledger state/job-alerts.jsonl (append-only): one row per message handled (parsed, partial, review,
unmapped, stale, unlabeled, ignored: a sender in targets/platforms.yaml `ignored_senders`, notice: the board's own
account, alert set-up, profile or welcome mail with no job card), with `read_from` (links or body); a message in the ledger is not parsed again
unless --reparse, except the two open states: an `unlabeled` one is parsed once mail-triage relabels it
job_alert, and an `unmapped` one waits (listed for review once, not ledgered again) and is parsed on the
first run after platform-scout adds its sender to targets/platforms.yaml `alert_senders`. Review list:
state/review/job-alerts-<date>.md. Alerts older than --max-age-days (14) are ledgered as stale, so
a Gmail backlog does not flood jobs/ with dead postings; that includes an unmapped alert that is past the
window when its sender is mapped, or that ages past it while still unmapped.

CLI:  python job_alerts.py [--root R] [--file F.classified.jsonl ...] [--today YYYY-MM-DD]
                           [--max-age-days 14] [--reparse] [--dry-run] [--json]
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dedup  # noqa: E402
import fit_gates  # noqa: E402
import js_comms_util as cu  # noqa: E402
from fetch_ats import (TitleFilter, default_title_filter, iso_utc, jd_text_from_body, load_jobs,  # noqa: E402
                       load_title_filter, make_record, merge_records, norm_employment, parse_comp_text, parse_ts,
                       remote_mode, save_job)
from js_common import (JS_ROOT, append_jsonl, cli_date, dept_day, fm, normalize_company, read_jsonl,  # noqa: E402
                       short_hash, slugify, wrap_untrusted)

MAX_AGE_DAYS = 14
CARD_LINES = 8
GENERIC_SOURCE = "email-alert"
UNLABELED_CHECK = ("newsletter", "other", "review")
OPEN_STATUSES = ("unlabeled", "unmapped")         # ledger states that do not close a message (see the docstring)


def run_now(day: date | None) -> datetime:
    """The run clock: the wall clock for today (or no --today), else noon UTC on that day."""
    if day is None or day == date.today():
        return datetime.now(timezone.utc)
    return datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)


# =============================================================================== job links
@dataclass(frozen=True)
class JobLink:
    canonical: str
    key: str
    vendor: str | None          # set when the link is the employer's own ATS page
    kind: str                   # which rule matched


Builder = Callable[[re.Match, str], "tuple[str, str] | None"]


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.I)


def _bare(url: str) -> str:
    """https://host/path without query or fragment (host lower-cased, trailing slash dropped)."""
    p = urlsplit(url)
    return urlunsplit(("https", p.netloc.lower(), p.path.rstrip("/") or "/", "", ""))


def _query(url: str) -> dict[str, str]:
    return dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))


def _zip(m: re.Match, url: str) -> tuple[str, str]:
    base = "https://www.ziprecruiter.com" + (urlsplit(url).path.rstrip("/") or "/")
    jid = _query(url).get("jid")
    canonical = base + (f"?jid={jid}" if jid else "")
    return canonical, (jid or short_hash(canonical, n=8))


def _gh_embed(m: re.Match, url: str) -> tuple[str, str] | None:
    q = _query(url)
    if q.get("for") and str(q.get("token", "")).isdigit():
        return f"https://job-boards.greenhouse.io/{q['for']}/jobs/{q['token']}", q["token"]
    return None


def _gh_jid(m: re.Match, url: str) -> tuple[str, str]:
    return f"{_bare(url)}?gh_jid={m.group(1)}", m.group(1)


def _workday(m: re.Match, url: str) -> tuple[str, str]:
    canonical = _bare(url)
    req = re.search(r"_([A-Za-z]*-?\d{4,}[\w-]*)$", canonical)
    return canonical, (req.group(1) if req else short_hash(canonical, n=8))


# Indeed's redirect: https://cts.indeed.com/v3/<payload>/<sig>,
# the payload a gzip, URL-safe base64 JSON object {"u": target, "m": click metadata}. Decoded here, never fetched.
CTS_PAYLOAD_CAP = 8_000                  # base64 characters read from one redirect
CTS_JSON_CAP = 64_000                    # bytes inflated at most (a larger payload is not a job link)
INDEED_JOB_PATH = _rx(r"^/(?:viewjob|rc/clk|pagead/clk|m/viewjob|m/basecamp/viewjob|applystart)(?:/|$)")
INDEED_JK = re.compile(r"[0-9a-f]{16}")


def cts_target(blob: str) -> str | None:
    """The target URL inside a cts.indeed.com payload, else None (not base64, not gzip, too large, not {"u": url})."""
    if not blob or len(blob) > CTS_PAYLOAD_CAP:
        return None
    try:
        inflate = zlib.decompressobj(wbits=31)                     # gzip only
        raw = inflate.decompress(base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4)), CTS_JSON_CAP)
        if inflate.unconsumed_tail:
            return None
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, zlib.error):                              # binascii.Error, JSON and UTF-8 errors are ValueErrors
        return None
    target = data.get("u") if isinstance(data, dict) else None
    return target if isinstance(target, str) and target.startswith(("https://", "http://")) else None


def _indeed_cts(m: re.Match, url: str) -> tuple[str, str] | None:
    """A redirect to an Indeed job page: (viewjob URL, jk) when the target names the job key; (a placeholder unique to the
    target, "") when it hides it (a sponsored pagead click), resolved from the card by indeed_search_link; else None."""
    target = cts_target(m.group(1))
    if not target:
        return None
    parts = urlsplit(target)
    host = parts.netloc.lower()
    if not (host == "indeed.com" or host.endswith(".indeed.com")) or not INDEED_JOB_PATH.match(parts.path or ""):
        return None
    jk = _query(target).get("jk", "").lower()
    if INDEED_JK.fullmatch(jk):
        return f"https://www.indeed.com/viewjob?jk={jk}", jk
    return f"https://www.indeed.com{parts.path.rstrip('/')}?cts={short_hash(target, n=12)}", ""


LinkRule = tuple[str, "str | None", re.Pattern, Builder]

LINKEDIN: list[LinkRule] = [
    ("linkedin", None, _rx(r"https?://(?:[\w-]+\.)?linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#\s]*?-)?(\d{6,})"),
     lambda m, u: (f"https://www.linkedin.com/jobs/view/{m.group(1)}/", m.group(1))),
    ("linkedin", None, _rx(r"https?://(?:[\w-]+\.)?linkedin\.com/(?:comm/)?jobs/\S*?[?&]currentJobId=(\d{6,})"),
     lambda m, u: (f"https://www.linkedin.com/jobs/view/{m.group(1)}/", m.group(1))),
]
GLASSDOOR: list[LinkRule] = [
    ("glassdoor", None, _rx(r"https?://(?:[\w-]+\.)?glassdoor\.[a-z.]+/\S*?[?&](?:jl|jobListingId)=(\d{5,})"),
     lambda m, u: (f"https://www.glassdoor.com/job-listing/j?jl={m.group(1)}", m.group(1))),
]
ZIPRECRUITER: list[LinkRule] = [
    ("ziprecruiter", None, _rx(r"https?://(?:[\w-]+\.)?ziprecruiter\.com/(?:c/[^\s?#]+/Job/[^\s?#]+|jobs?/[^\s?#]+"
                               r"|k/[^\s?#]+|km/[^\s?#]+|ekm/[^\s?#]+)"), _zip),
]
WTTJ: list[LinkRule] = [
    ("wttj", None, _rx(r"https?://(?:[\w-]+\.)?welcometothejungle\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?companies/"
                       r"([\w-]+)/jobs/([\w%.-]+)"),
     lambda m, u: (f"https://www.welcometothejungle.com/en/companies/{m.group(1)}/jobs/{m.group(2)}",
                   short_hash(f"{m.group(1)}/{m.group(2)}", n=8))),
    ("otta", None, _rx(r"https?://app\.otta\.com/jobs/([\w-]+)"),
     lambda m, u: (f"https://app.otta.com/jobs/{m.group(1)}", m.group(1))),
]
HIRINGCAFE: list[LinkRule] = [
    ("hiringcafe", None, _rx(r"https?://(?:www\.)?(?:hiring\.cafe|hiringcafe\.com)/viewjob/([\w-]+)"),
     lambda m, u: (f"https://hiringcafe.com/viewjob/{m.group(1)}", m.group(1))),
]
YC: list[LinkRule] = [
    ("yc", None, _rx(r"https?://(?:www\.)?workatastartup\.com/jobs/(\d+)"),
     lambda m, u: (f"https://www.workatastartup.com/jobs/{m.group(1)}", m.group(1))),
    ("yc", None, _rx(r"https?://(?:www\.)?ycombinator\.com/companies/([\w-]+)/jobs/([\w-]+)"),
     lambda m, u: (f"https://www.ycombinator.com/companies/{m.group(1)}/jobs/{m.group(2)}",
                   re.match(r"[A-Za-z0-9]+", m.group(2)).group(0))),
]
ATS: list[LinkRule] = [
    ("greenhouse", "greenhouse", _rx(r"https?://((?:job-)?boards(?:\.eu)?\.greenhouse\.io)/(?!embed/)([\w-]+)/jobs/(\d+)"),
     lambda m, u: (f"https://{m.group(1).lower()}/{m.group(2)}/jobs/{m.group(3)}", m.group(3))),
    ("greenhouse", "greenhouse", _rx(r"https?://(?:job-)?boards\.greenhouse\.io/embed/job_app\?\S+"), _gh_embed),
    ("greenhouse", "greenhouse", _rx(r"https?://[^\s?#]+\?\S*?\bgh_jid=(\d+)"), _gh_jid),
    ("lever", "lever", _rx(r"https?://jobs\.(?:eu\.)?lever\.co/([\w.-]+)/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                           r"[0-9a-f]{4}-[0-9a-f]{12})"),
     lambda m, u: (f"https://jobs.lever.co/{m.group(1)}/{m.group(2).lower()}", m.group(2)[:8].lower())),
    ("ashby", "ashby", _rx(r"https?://jobs\.ashbyhq\.com/([^/\s?#]+)/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                           r"[0-9a-f]{4}-[0-9a-f]{12})"),
     lambda m, u: (f"https://jobs.ashbyhq.com/{m.group(1)}/{m.group(2).lower()}", m.group(2)[:8].lower())),
    ("workday", "workday", _rx(r"https?://[\w-]+\.wd\d+\.myworkdayjobs\.com/[^\s?#]*?/job/[^\s?#]+"), _workday),
    ("smartrecruiters", "smartrecruiters", _rx(r"https?://(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)/(\d+)"),
     lambda m, u: (f"https://jobs.smartrecruiters.com/{m.group(1)}/{m.group(2)}", m.group(2))),
    ("icims", "icims", _rx(r"https?://[\w-]+\.icims\.com/jobs/(\d+)"), lambda m, u: (_bare(m.group(0)), m.group(1))),
    ("workable", "other", _rx(r"https?://apply\.workable\.com/([\w-]+)/j/([0-9A-Fa-f]{6,})"),
     lambda m, u: (f"https://apply.workable.com/{m.group(1)}/j/{m.group(2)}", m.group(2))),
]
BOARDS: list[LinkRule] = [
    ("indeed-redirect", None, _rx(r"https?://cts\.indeed\.com/v\d+/([A-Za-z0-9_-]+)"), _indeed_cts),
    ("indeed", None, _rx(r"https?://(?:[\w-]+\.)?indeed\.com/(?:viewjob|rc/clk|pagead/clk|m/viewjob|m/basecamp/viewjob"
                         r"|applystart)\?\S*?\bjk=([0-9a-f]{16})"),
     lambda m, u: (f"https://www.indeed.com/viewjob?jk={m.group(1)}", m.group(1))),
    ("builtin", None, _rx(r"https?://(?:www\.)?builtin[a-z]*\.com/job/[\w-]+/(\d+)"),
     lambda m, u: (_bare(m.group(0)), m.group(1))),
    ("wellfound", None, _rx(r"https?://(?:www\.)?(?:wellfound\.com|angel\.co)/(?:company/[\w-]+/)?jobs/(\d+)(?:-[\w-]+)?"),
     lambda m, u: (_bare(m.group(0)), m.group(1))),
    ("wellfound", None, _rx(r"https?://(?:www\.)?(?:wellfound\.com|angel\.co)/jobs/?\?(?:\S*?&)?job_listing_slug=((\d+)[\w-]*)"),
     lambda m, u: (f"https://wellfound.com/jobs/{m.group(1)}", m.group(2))),
    ("dice", None, _rx(r"https?://(?:www\.)?dice\.com/job-detail/([0-9a-f-]{36})"),
     lambda m, u: (_bare(m.group(0)), m.group(1)[:8])),
    ("jobright", None, _rx(r"https?://(?:www\.)?jobright\.ai/jobs/info/([0-9a-f]{12,})"),
     lambda m, u: (_bare(m.group(0)), m.group(1)[:12])),
    ("trueup", None, _rx(r"https?://(?:www\.)?trueup\.io/job/([\w-]+)"), lambda m, u: (_bare(m.group(0)), m.group(1))),
    ("levels-fyi", None, _rx(r"https?://(?:www\.)?levels\.fyi/jobs\S*?[?&]jobId=(\d+)"),
     lambda m, u: (f"https://www.levels.fyi/jobs?jobId={m.group(1)}", m.group(1))),
    ("himalayas", None, _rx(r"https?://(?:www\.)?himalayas\.app/companies/[\w-]+/jobs/[\w-]*?(\d{6,})"),
     lambda m, u: (_bare(m.group(0)), m.group(1))),
    ("remotive", None, _rx(r"https?://(?:www\.)?remotive\.com/remote-jobs/[\w-]+/[\w-]*?(\d{4,})"),
     lambda m, u: (_bare(m.group(0)), m.group(1))),
    ("remoteok", None, _rx(r"https?://(?:www\.)?remoteok\.(?:com|io)/remote-jobs/[\w-]*?(\d{4,})"),
     lambda m, u: (_bare(m.group(0)), m.group(1))),
    ("simplify", None, _rx(r"https?://(?:www\.)?simplify\.jobs/p/([\w-]+)"), lambda m, u: (_bare(m.group(0)), m.group(1))),
    ("handshake", None, _rx(r"https?://(?:[\w-]+\.)?joinhandshake\.com/(?:stu/)?jobs/(\d+)"),
     lambda m, u: (_bare(m.group(0)), m.group(1))),
    ("vc-portfolio", None, _rx(r"https?://[\w.-]+/companies/[\w-]+/jobs/(\d+)(?:-[\w-]+)?"),
     lambda m, u: (_bare(m.group(0)), m.group(1))),
]

# Big-company career sites and employer talent communities whose alert mail links to
# their own job pages (the senders are confirmed on the first alert, then mapped in targets/platforms.yaml).
CAREERS: list[LinkRule] = [
    ("google-careers", None, _rx(r"https?://(?:www\.)?google\.com/about/careers/applications/jobs/results/((\d{6,})[\w-]*)"),
     lambda m, u: (f"https://www.google.com/about/careers/applications/jobs/results/{m.group(1)}", m.group(2))),
    ("meta-careers", None, _rx(r"https?://(?:www\.)?metacareers\.com/(?:v2/)?jobs/(\d{6,})"),
     lambda m, u: (f"https://www.metacareers.com/jobs/{m.group(1)}/", m.group(1))),
    ("amazon-jobs", None, _rx(r"https?://(?:www\.)?amazon\.jobs/[a-z]{2}(?:-[a-z]{2})?/jobs/(\d{5,})(?:/[\w-]+)?"),
     lambda m, u: (_bare(m.group(0)), m.group(1))),
    ("apple-jobs", None, _rx(r"https?://jobs\.apple\.com/[a-z]{2}-[a-z]{2}/details/(\d{6,}(?:-\d+)?)(?:/[\w-]+)?"),
     lambda m, u: (_bare(m.group(0)), m.group(1))),
    ("eightfold", None, _rx(r"https?://([\w-]+)\.eightfold\.ai/careers/job/(\d{6,})"),
     lambda m, u: (f"https://{m.group(1).lower()}.eightfold.ai/careers/job/{m.group(2)}", m.group(2))),
    ("eightfold", None, _rx(r"https?://([\w-]+)\.eightfold\.ai/careers\S*?[?&]pid=(\d{6,})"),
     lambda m, u: (f"https://{m.group(1).lower()}.eightfold.ai/careers/job/{m.group(2)}", m.group(2))),
]

# Google Jobs alert mail. A per-card link, when the harvest keeps one, is a Google search page
# naming the job by `htidocid` (in the query or the fragment), possibly behind a google.com/url redirect. Only q, ibp and
# htidocid are kept; every other parameter (ved, usg, ei, sa, utm_*) is tracking. Read from the mail, never fetched.
GOOGLE_JOBS = "google-jobs-search"
GOOGLE_HOST = r"https?://(?:www\.)?google\.com"
GOOGLE_DOCID = re.compile(r"[?&#]htidocid=([^&#\s]+)")


def _google_job(m: re.Match, url: str) -> tuple[str, str] | None:
    docid = unquote(GOOGLE_DOCID.search(url).group(1)).strip()
    if not re.fullmatch(r"[\w.=+/-]{4,200}", docid):
        return None
    fragment = urlsplit(url).fragment
    q = _query(url).get("q") or dict(parse_qsl(fragment)).get("q") or ""
    params = ([("q", q.strip())] if q.strip() else []) + [("ibp", "htl;jobs"), ("htidocid", docid)]
    return "https://www.google.com/search?" + urlencode(params), docid


def _google_redirect(m: re.Match, url: str) -> tuple[str, str] | None:
    raw = re.search(r"(?:^|&)(?:q|url)=([^&]+)", urlsplit(url).query)
    inner = unquote(raw.group(1)) if raw else ""             # unquote, not unquote_plus: the inner URL keeps its "+"
    hit = GOOGLE_JOBS_RULES[0][2].match(inner) if inner else None
    return _google_job(hit, inner) if hit else None


GOOGLE_JOBS_RULES: list[LinkRule] = [
    ("google-jobs", None, _rx(GOOGLE_HOST + r"/search\?\S*?[?&#]htidocid=[^&#\s]+"), _google_job),
    ("google-jobs", None, _rx(GOOGLE_HOST + r"/url\?\S+"), _google_redirect),
]


@dataclass(frozen=True)
class Dedicated:
    short: str
    source: str
    rules: tuple


DEDICATED: dict[str, Dedicated] = {
    "linkedin": Dedicated("li", "linkedin-alert", tuple(LINKEDIN)),
    "glassdoor": Dedicated("gd", "glassdoor-alert", tuple(GLASSDOOR)),
    "ziprecruiter": Dedicated("zr", "ziprecruiter-alert", tuple(ZIPRECRUITER)),
    "wttj": Dedicated("wttj", "wttj-alert", tuple(WTTJ + ATS)),
    "hiringcafe": Dedicated("hc", "hiringcafe-alert", tuple(HIRINGCAFE + ATS)),
    "yc": Dedicated("yc", "yc-alert", tuple(YC + ATS)),
}
GENERIC_RULES = tuple(LINKEDIN + GLASSDOOR + ZIPRECRUITER + WTTJ + HIRINGCAFE + YC + BOARDS + CAREERS + ATS)
GENERIC_SHORT = {"builtin": "bi", "wellfound": "wf", "indeed": "in", "dice": "dc", "jobright": "jr", "trueup": "tu",
                 "levels-fyi": "lv", "himalayas": "him", "remotive": "rv", "remoteok": "rok", "simplify": "sp",
                 "vc-portfolio": "vc", "handshake": "hs", "adzuna": "adz", "company-ats": "ats",
                 "google-careers": "gc", "meta-careers": "meta", "amazon-jobs": "amz", "apple-jobs": "apl",
                 "talent-communities": "tc", "job-newsletters": "nl", "mygreenhouse": "mgh",
                 "workday-candidate-home": "wdc", "hackajob": "hj", "google-jobs-search": "gj"}
ALERT_SOURCES = tuple(d.source for d in DEDICATED.values()) + (GENERIC_SOURCE,)


# Opaque click-tracking links. Two boards wrap every job link in a tracker whose target
# cannot be read without fetching it: Indeed's alert mail (engage.indeed.com/f/a/<id>~~/<id>~/<encrypted payload>; the
# payload is not base64 of the URL) and Welcome to the Jungle (SendGrid click tracking,
# u<N>.ct.sendgrid.net/ls/click?upn=..., an encrypted token). Links are
# never fetched. These rules are used only when the plain-text body is read (split_cards, opaque=True), never for the
# HTML card links or the unlabeled check: an opaque link is a job only when the text around it reads as exactly one job
# card (title and company), and then the record is keyed on a search for that card's own words, like Indeed's sponsored
# cts targets; an opaque link whose text is not a card (a button, a profile or welcome note, a footer) is a bare link.
def _opaque(prefix: str) -> Builder:
    def build(m: re.Match, url: str) -> tuple[str, str]:
        return f"{prefix}?opaque={short_hash(url, n=12)}", ""
    return build


OPAQUE: dict[str, list[LinkRule]] = {
    "indeed": [("indeed-opaque", None, _rx(r"https?://engage\.indeed\.com/f/a/\S+"), _opaque("https://www.indeed.com/jobs"))],
    "wttj": [("wttj-opaque", None, _rx(r"https?://u\d+\.ct\.sendgrid\.net/ls/click\?\S+"),
              _opaque("https://www.welcometothejungle.com/en/jobs"))],
}
OPAQUE_KINDS = frozenset(rule[0] for rules in OPAQUE.values() for rule in rules)
# Welcome to the Jungle's plain text prints a link's own address without a scheme next to the tracked one
# ("( www.welcometothejungle.com/en/me/profile )   Review my profile ( https://u17807810.ct.sendgrid.net/... )"); a
# scheme-less job address there is a real, keyed job link.
SCHEMELESS = _rx(r"(?<![\w/.:])((?:www\.)?welcometothejungle\.com/\S+|app\.otta\.com/\S+)")


def with_scheme(line: str) -> str:
    return SCHEMELESS.sub(lambda m: "https://" + m.group(1), line)


def source_for(platform: str) -> str:
    return DEDICATED[platform].source if platform in DEDICATED else GENERIC_SOURCE


def short_for(platform: str) -> str:
    if platform in DEDICATED:
        return DEDICATED[platform].short
    return GENERIC_SHORT.get(platform) or re.sub(r"[^a-z0-9]", "", platform.lower())[:4] or "al"


def rules_for(platform: str | None) -> tuple:
    if platform == GOOGLE_JOBS:
        return tuple(GOOGLE_JOBS_RULES)
    return DEDICATED[platform].rules if platform in DEDICATED else GENERIC_RULES


URL_RE = re.compile(r"https?://[^\s<>\"'\[\]]+")


def _url_token(text: str) -> str:
    return text.rstrip(".,;:!?)>'\"")


def match_job_url(url: str, platform: str | None, opaque: bool = False) -> JobLink | None:
    """A job link for this platform's rules (generic rules for platforms without their own), else None. With opaque,
    the platform's opaque click-tracking rules (OPAQUE) are tried last: such a link has an empty key."""
    url = _url_token(str(url or "").strip())
    rules = rules_for(platform) + (tuple(OPAQUE.get(platform or "", ())) if opaque else ())
    for kind, vendor, rx, build in rules:
        m = rx.match(url)
        if not m:
            continue
        built = build(m, url)
        if built:
            return JobLink(built[0], built[1], vendor, kind)
    return None


# =============================================================================== card text
ROLE = _rx(r"\b(?:engineers?|developers?|scientists?|researchers?|architects?|analysts?|designers?|managers?"
           r"|directors?|interns?|internship|specialists?|consultants?|swe|sde|mts|member of (?:the )?technical staff"
           r"|head of|vice president|vp|(?:engineering|technical|tech|team|ml|ai) lead|programmers?|technologists?"
           r"|fellows?)\b")
HEADER = _rx(r"\b(?:job alerts?|new jobs?|new roles?|jobs? (?:that )?match|matching (?:your|jobs)|match(?:es)? your"
             r"|jobs? for you|picked for you|job picks|recommended (?:for you|jobs)|based on your|your (?:saved )?search"
             r"|saved search(?:es)?|job matches|top jobs|jobs this week|see (?:all|more)|view (?:all|more)|unsubscribe"
             r"|manage (?:your )?(?:job )?alerts?|notification settings|email preferences|privacy policy"
             r"|terms of (?:use|service)|all rights reserved|you are receiving|you're receiving|this email was"
             r"|intended for|learn why|help center|download (?:the )?app|get the app|copyright|open to offers"
             r"|ready to interview|see all jobs|edit (?:job )?alert|jobs in)\b")
BADGE = _rx(r"^(?:view job|view details|see job|see details|apply|apply now|apply on company (?:site|website)|easy apply"
            r"|easily apply|quick apply|1-click apply|one-click apply|apply with resume(?: (?:&|and) profile)?|save|save job"
            r"|learn more|details|promoted|new|hot|featured|urgent(?:ly hiring)?|hiring now|hiring multiple candidates"
            r"|actively (?:recruiting|hiring)|this company is actively hiring|be an early applicant|early applicant"
            r"|top applicant|you'?d be a top applicant|responsive employer|verified|verified employer"
            r"|responses managed off linkedin|your profile matches this job|in your network"
            r"|\d+ (?:school )?alumni(?: work here)?|\d+ connections?(?: work here)?|\d+\+? applicants?"
            r"|over \d+ applicants|reposted|sponsored|remote friendly|estimated pay|be seen first|our take"
            r"|best places? to work|best[- ]led compan(?:y|ies)|top ceo|\d+\+? referrals?|actively hiring\b.+"
            r"|applied on [a-z]{3,9}\.? \d{1,2}(?:, \d{4})?|edit|be|seen first)$")
SIZE = _rx(r"\s*/\s*\d[\d,]*(?:\s*[-–]\s*\d[\d,]*|\+)?\s+employees$")
GREETING = _rx(r"^(?:hi|hello|hey|dear)\b[^.!?]{0,40}[,!:]?$")
STAGE_LINE = _rx(r"^[^·•|]{1,60}\s[·•|]\s*(?:(?:early|growth|late|seed|scale)[- ]stage|public company|private company"
                 r"|non-?profit)$")
BENEFITS = _rx(r"^(?:(?:medical|vision|dental|pto|life|retirement|401\(?k\)?|parental leave|disability|hsa|fsa"
               r"|tuition|commuter benefits|stock options|equity|bonus|paid time off)(?:\s*,\s*|$))+$")
EXPERIENCE = _rx(r"^(?:\d+\+?\s*)?years? of exp(?:erience)?$")
COMPANY_MARK = _rx(r"(?:,?\s(?:inc|llc|ltd|corp|co|gmbh|plc)\.?$)|\b[a-z0-9-]+\.(?:io|ai|com|co|dev|app)$")
SPONSOR_BADGE = _rx(r"\b(?:visa sponsorship|sponsors? visas?|h-?1b)\b")
POSTED = _rx(r"^(?:posted\s+)?(?:(\d+)\+?\s*(minute|min|hour|hr|day|week|wk)s?\s+ago|(\d+)\s*([mhdw])(?:\s+ago)?"
             r"|(just posted|today|new today|yesterday))$")
EMPLOYMENT = _rx(r"^(?:full[- ]?time|part[- ]?time|contract(?:or)?|contract[- ]to[- ]hire|temporary|temp|internship|intern"
                 r"|permanent)$")
SALARY = _rx(r"[$€£]\s?\d|\b\d{2,3}(?:,\d{3})+(?:\.\d+)?\s*(?:-|–|to)\s*\d|\b\d+(?:\.\d+)?\s*k\s*(?:-|–|to)\s*\d+(?:\.\d+)?\s*k\b")
EQUITY = _rx(r"^\d+(?:\.\d+)?%(?:\s*(?:-|–|to)\s*\d+(?:\.\d+)?%)?(?:\s+equity)?$")
RATING = _rx(r"^\(?\d(?:\.\d)?\s*(?:★|☆|stars?|/\s*5)?\)?$")
SEPARATOR = re.compile(r"^[\s\-=_*~·•—–|]{3,}$")
MODE_WORDS = _rx(r"\b(?:remote|hybrid|on-?site|in[- ]office|in[- ]person|anywhere|worldwide|work from home)\b")
CITY_ST = re.compile(r"^[A-Z][A-Za-z .'-]+,\s*([A-Z]{2})(?=$|\s*[,;()|·•/]|\s+[-–]|\s+\d{5})")
_REGION_CODES = {c.upper() for c in fit_gates.US_STATES} | {"AB", "BC", "MB", "NB", "NL", "NS", "ON", "PE", "QC", "SK"}
METRO = _rx(r"^(?:greater\s+)?[A-Z][\w .'-]*\s(?:metroplex|metro(?:politan)? area|metro)$")
SPLIT = re.compile(r"\s+(?:[·•|—–@]|-)\s+")
_POSTED_DAYS = {"minute": 0, "min": 0, "hour": 0, "hr": 0, "m": 0, "h": 0, "day": 1, "d": 1, "week": 7, "wk": 7, "w": 7}


def clean_line(text: str) -> str:
    s = str(text or "").replace(" ", " ").strip()
    s = re.sub(r"^[>*•·\-–—\s]+", "", s)
    s = re.sub(r"\s*[-–·|,]?\s*\(?\d(?:\.\d)?\s*(?:★|☆|stars?)\)?\s*$", "", s, flags=re.I)          # rating
    s = re.sub(r"\s*\((?:[SWFX]\d{2}|(?:Summer|Winter|Fall|Spring) \d{2,4})\)\s*$", "", s)             # YC batch
    s = re.sub(r"\s*[-–·|•(]\s*(?:easy apply|easily apply|quick apply)\)?\s*$", "", s, flags=re.I)
    s = SIZE.sub("", s)                                                                                 # company size
    s = s.strip(" \t:-–—|·•,")
    return re.sub(r"\s{2,}", " ", s)


def is_location(text: str) -> bool:
    if re.fullmatch(r"[A-Z]{2}", text.strip()) and text.strip() in _REGION_CODES:
        return True                               # a state alone ("Acme • OR • On-site", ZipRecruiter)
    city = CITY_ST.search(text)
    if MODE_WORDS.search(text) or (city and city.group(1) in _REGION_CODES) or METRO.search(text):
        return True
    return fit_gates._location_kind(text) not in ("unknown", "mode")


def _snippet(text: str) -> bool:
    if re.search(r"(?:\.\.\.|…)$", text) and len(text) <= 120:
        return False                              # a field cut short by the sender ("Senior Engineer, Platfo...")
    sentence = re.search(r"[a-z]{2}(?<!\binc)(?<!\bltd)(?<!\bcorp)(?<!\bco)(?<!\bsr)(?<!\bjr)[.!]$", text, re.I)
    limit = 140 if ROLE.search(text) and not sentence else 90          # long titles exist; long blurbs are prose
    return (len(text) > limit or (bool(sentence) and len(text.split()) >= 6)
            or (text.endswith("?") and len(text.split()) >= 4))


def _noise(text: str, greeting: bool = True) -> bool:
    """Footer, badge, header and greeting lines. A line with field separators, or a piece of one (greeting=False), is
    never a greeting: the company "Hey Robin" in "Hey Robin · Springfield, CA" is a name, not "Hey <name>"."""
    return ("©" in text or bool(SEPARATOR.match(text)) or bool(HEADER.search(text)) or bool(BADGE.match(text))
            or bool(RATING.match(text)) or bool(EQUITY.match(text)) or (greeting and bool(GREETING.match(text)) and not BULLET.search(text))
            or bool(STAGE_LINE.match(text)) or bool(EXPERIENCE.match(text)) or bool(BENEFITS.match(text)))


def is_title(text: str) -> bool:
    """A role line; a short line ending in a company mark ("Acme Consultants, Inc.", "Programmers.io") is not."""
    return bool(ROLE.search(text)) and not (len(text.split()) <= 4 and COMPANY_MARK.search(text))


def _prose(block: list[str]) -> bool:
    """A wrapped paragraph of prose (a blurb such as Wellfound's "Our Take"), not card fields."""
    words = sum(len(s.split()) for s in block)
    return (len(block) >= 2 and words >= 20 and max(len(s) for s in block) >= 60
            and any(re.match(r"[a-z]", s) for s in block[1:]))


def _join_wrapped(lines: list[str]) -> list[str]:
    """A "pay | place | experience | type" row the mail wrapped onto the next line is one line again."""
    out: list[str] = []
    for s in lines:
        prev = out[-1] if out else ""
        if s and prev and " | " in prev and (s[:1].islower() or prev.count("(") > prev.count(")")):
            out[-1] = f"{prev} {s}"
        else:
            out.append(s)
    return out


def _drop_prose(raw_lines: Iterable[str]) -> list[str]:
    lines = _join_wrapped([str(x or "").strip() for x in raw_lines])
    out: list[str] = []
    block: list[str] = []
    after_take = False                            # Wellfound: the paragraph after "Our Take" is always a blurb
    for s in lines + [""]:
        if s:
            block.append(s)
            continue
        if block:
            out.extend([] if (after_take or _prose(block)) else block)
            after_take = len(block) == 1 and bool(re.fullmatch(r"our take:?", block[0], re.I))
        block = []
        out.append("")
    return out[:-1]


def _role_piece(line: str) -> bool:
    """A piece of this separated line names a role. "Internship" names the job type, not a role: Wellfound's
    "San Francisco | years of exp | Internship" is a meta line, not a second title."""
    return any(ROLE.search(p) and not EMPLOYMENT.match(p) for p in (clean_line(x) for x in SPLIT.split(line)))


NAME_JOINERS = {"and", "of", "the", "for", "de", "la", "&", "a", "an", "in", "on"}


def _unsnip(line: str) -> str | None:
    """A separated line too long for a field that is still card fields, else None: a "Company · Place" line whose
    company name is long (LinkedIn's "URBN (Urban Outfitters, ...) · Philadelphia, PA (Hybrid)") stays whole; a title
    the poster padded with " | " tags ("Founding AI Engineer (Python) | LLM Agents | San Francisco | Up to $300K")
    keeps its first piece."""
    pieces = [p for p in (clean_line(x) for x in SPLIT.split(line)) if p]
    head = pieces[0] if pieces else ""
    if len(pieces) < 2 or _snippet(head) or _noise(head) or re.search(r"[.!?]\s", head):
        return None                               # one field, a sentence, or prose: still a blurb
    lower = [w for w in head.split() if w[:1].islower() and w not in NAME_JOINERS]
    if all(is_location(p) for p in pieces[1:]) and not ROLE.search(head) and len(head.split()) <= 14 and len(lower) <= 1:
        return line                               # a name: capitalised words ("URBN (Urban Outfitters, ...)")
    if " | " in line and is_title(head):
        return head
    return None


def _meta_piece(piece: str, meta: "CardMeta") -> bool:
    """True when one piece of a separated line is card metadata (recorded in meta) or noise."""
    if _noise(piece, greeting=False):
        return True
    m = POSTED.match(piece)
    if m:
        _set_posted(m, meta)
        return True
    if EMPLOYMENT.match(piece):
        meta.employment = norm_employment(piece)
        return True
    return bool(re.fullmatch(r"co-?founder", piece, re.I))          # Wellfound's job type for founding roles


def _set_posted(m: re.Match, meta: "CardMeta") -> None:
    if m.group(5):
        meta.posted_days = 1 if m.group(5).lower() == "yesterday" else 0
    else:
        n, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        meta.posted_days = int(n) * _POSTED_DAYS[unit.lower()]


def _dedupe(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(i for i in items if i))


@dataclass
class CardMeta:
    comp: tuple = (None, None, None)
    employment: str | None = None
    posted_days: int | None = None
    injection: bool = False
    sponsorship_badge: bool = False
    locations: list[str] = field(default_factory=list)
    company: str | None = None                    # named by a "Company / 11-50 Employees" line (Wellfound)


def card_lines(raw_lines: Iterable[str]) -> tuple[list[str], CardMeta]:
    """Meaningful lines of a card (title, company, place candidates) plus what the rest said."""
    meta = CardMeta()
    kept: list[str] = []
    for raw in _drop_prose(raw_lines):
        s = clean_line(raw)
        if not s:
            continue
        if SIZE.search(raw):
            name = clean_line(SIZE.sub("", raw))
            if name and not cu.strip_injections(name)[1]:
                meta.company = meta.company or name
                continue
        if cu.strip_injections(s)[1]:
            meta.injection = True
            continue
        if _noise(s):
            continue
        sep = SPLIT.search(s)
        if sep and not _role_piece(s):              # "18 hours ago · Be an early applicant", "SF | 5 years of exp"
            pieces = [p for p in (clean_line(x) for x in SPLIT.split(s)) if p]
            rest = [p for p in pieces if not _meta_piece(p, meta)]
            if not rest:
                continue
            if len(rest) < len(pieces):
                s = sep.group(0).join(rest)
        if SPONSOR_BADGE.search(s) and len(s.split()) <= 6 and not ROLE.search(s):
            meta.sponsorship_badge = True
            continue
        m = POSTED.match(s)
        if m:
            _set_posted(m, meta)
            continue
        if EMPLOYMENT.match(s):
            meta.employment = norm_employment(s)
            continue
        if SALARY.search(s) and not (ROLE.search(s) and not re.match(r"[$€£\d]", s)):   # a title may name its pay
            comp = parse_comp_text(re.sub(r"\s*/\s*yr\b(?=\s*[-–])", "", s, flags=re.I))   # "$150K/yr - $210K/yr"
            if (comp[0] or comp[1]) and not (meta.comp[0] or meta.comp[1]):
                meta.comp = comp
            for part in SPLIT.split(s):
                p = clean_line(part)
                if p and not SALARY.search(p) and not EQUITY.match(p) and not _noise(p) and is_location(p):
                    meta.locations.append(p)
            continue
        if _snippet(s):
            s = _unsnip(s)
            if not s:
                continue
        kept.append(s)
    return _dedupe(kept)[-CARD_LINES:], meta


def _classify(lines: list[str]) -> tuple[list[str], list[str], list[str]]:
    titles, companies, places = [], [], []
    for s in lines:
        (titles if is_title(s) else places if is_location(s) else companies).append(s)
    return titles, companies, places


def _split(line: str) -> list[str]:
    out: list[str] = []
    for part in SPLIT.split(line):
        at = re.match(r"^(.*\S)\s+at\s+(\S.*)$", part)
        if at and ROLE.search(at.group(1)):
            out.extend([at.group(1), at.group(2)])
        else:
            out.append(part)
    return [p for p in (clean_line(x) for x in out) if p and not _noise(p, greeting=False) and not SALARY.search(p)
            and not EMPLOYMENT.match(p) and not POSTED.match(p)]


# Card order per platform, from real alert mail: used only when the line kinds alone do not
# settle the card (a title without a role word, a company named like a place). The fields must sit in that
# order with nothing but places after them, else the card goes to review.
LAYOUT = {"glassdoor": "company-first", "jobright": "company-first", "wellfound": "title-first",
          "linkedin": "title-first", "ziprecruiter": "title-first"}


BULLET = re.compile(r"\s+[·•|]\s+")             # the field separators alone: "Acme Staffing - Direct Hire • CA"


def _by_layout(lines: list[str], layout: str | None) -> tuple[str, str, list[str]] | None:
    if layout not in ("company-first", "title-first") or len(lines) < 2:
        return None
    first, second, rest = lines[0], lines[1], lines[2:]
    if not all(is_location(x) for x in rest):
        return None
    company, title = (first, second) if layout == "company-first" else (second, first)
    if layout == "title-first":
        pieces = _split(company)                  # "Example Corp US • Los Angeles, CA • Hybrid": the name comes first
        bullets = [p for p in (clean_line(x) for x in BULLET.split(company)) if p and not _noise(p, greeting=False)]
        if len(pieces) > 1 and all(is_location(x) for x in pieces[1:]):
            company, rest = pieces[0], pieces[1:] + rest
        elif len(bullets) > 1 and not is_location(bullets[0]) and all(is_location(x) for x in bullets[1:]):
            company, rest = bullets[0], bullets[1:] + rest    # "Acme Defense - Acme Technology Inc. · Atlanta, GA"
        elif is_location(company):
            return None
    if (is_location(title) and not is_title(title)) or is_title(company):
        return None
    return title, company, rest


def fields_from(lines: list[str], layout: str | None = None) -> tuple[str | None, str | None, list[str], str | None]:
    """(title, company, places, why-not). Exactly one role line and one company line, or nothing. Tried in
    order: whole lines; combined lines split ("Company · City", keeping role lines whole, then any line with a
    place in it); a "Company · Place" line naming the company of the one other line; every line split
    ("Title - Company", "Title @ Company"); the platform's card order (LAYOUT)."""
    titles, companies, places = _classify(lines)
    if len(titles) == 1 and len(companies) == 1:
        return titles[0], companies[0], places, None

    def split_where(pred: Callable[[str, list[str]], bool]) -> tuple[list[str], list[str]]:
        parts: list[str] = []
        anchors: list[str] = []
        for s in lines:
            pieces = _split(s)
            if not pred(s, pieces) or (s in places and all(is_location(p) for p in pieces)):
                parts.append(s)                  # a place line stays whole ("San Francisco, CA · Onsite")
                continue
            parts.extend(pieces)
            if len(pieces) > 1 and not is_location(pieces[0]) and all(is_location(p) for p in pieces[1:]):
                anchors.append(pieces[0])        # "Company · Place"
        return _dedupe(parts), anchors

    tries = [lambda s, ps: not is_title(s),
             lambda s, ps: len(ps) > 1 and any(is_location(p) for p in ps)]
    for pred in tries:
        parts, anchors = split_where(pred)
        t2, c2, p2 = _classify(parts)
        if len(t2) == 1 and len(c2) == 1:
            return t2[0], c2[0], p2, None
        if len(anchors) == 1:
            others = [x for x in parts if x != anchors[0] and not is_location(x)]
            if len(others) == 1 and (is_title(others[0]) or not is_title(anchors[0])):
                return others[0], anchors[0], [x for x in parts if x not in others and x != anchors[0]], None
    parts, _ = split_where(lambda s, ps: True)
    t2, c2, p2 = _classify(parts)
    if len(t2) == 1 and len(c2) == 1:
        return t2[0], c2[0], p2, None
    ordered = _by_layout(lines, layout)
    if ordered:
        return ordered[0], ordered[1], ordered[2], None
    return None, None, [], f"{len(titles)} title lines, {len(companies)} company lines"


# =============================================================================== platforms and cards
def load_platforms_file(path: Path) -> dict[str, dict[str, Any]]:
    path = Path(path)
    if not path.is_file():
        return {}
    data = fm.parse_yaml(path.read_text(encoding="utf-8"))
    entries = data.get("platforms") if isinstance(data, dict) else None
    return {str(p["id"]): p for p in entries or [] if isinstance(p, dict) and p.get("id")}


def load_ignored(root: Path) -> dict[str, str]:
    """targets/platforms.yaml `ignored_senders:` (address, domain or registrable domain -> why): senders we decided
    never to map (a re-aggregator, noise). Their mail is ledgered `ignored` once and never waits for a mapping."""
    path = platforms_path(root)
    if not path.is_file():
        return {}
    data = fm.parse_yaml(path.read_text(encoding="utf-8"))
    rows = data.get("ignored_senders") if isinstance(data, dict) else None
    return {str(r["sender"]).strip().lower(): str(r.get("why") or "do not map")
            for r in rows or [] if isinstance(r, dict) and r.get("sender")}


def ignored_reason(row: dict[str, Any], ignored: dict[str, str]) -> str | None:
    email = sender_of(row)
    domain = cu.email_domain(email)
    for key in (email, domain, cu.registrable_domain(domain)):
        if key and key in ignored:
            return ignored[key]
    return None


def platforms_path(root: Path) -> Path:
    return Path(root) / "targets" / "platforms.yaml"


def load_platforms(root: Path) -> dict[str, dict[str, Any]]:
    return load_platforms_file(platforms_path(root))


def sender_index(platforms: dict[str, dict[str, Any]]) -> dict[str, str]:
    index: dict[str, str] = {}
    for pid, entry in platforms.items():
        for sender in entry.get("alert_senders") or []:
            index[str(sender).strip().lower()] = pid
    return index


def sender_of(row: dict[str, Any]) -> str:
    return str(row.get("from_email") or cu.parse_address(row.get("from"))[1] or "").strip().lower()


def platform_for(row: dict[str, Any], platforms: dict[str, dict[str, Any]]) -> str | None:
    index = sender_index(platforms)
    email = sender_of(row)
    domain = cu.email_domain(email)
    for key in (email, domain, cu.registrable_domain(domain)):
        if key and key in index:
            return index[key]
    return None


@dataclass
class Card:
    lines: list[str]
    link: JobLink
    after: bool = False          # the card text follows its link
    anchored: bool = False       # opened by a link whose own text is the title (ZipRecruiter: title link, card, button)


def split_cards(body: str, platform: str | None) -> list[Card]:
    items: list[tuple[str, Any, str]] = []
    for raw in str(body or "").splitlines():
        line = with_scheme(raw.strip())
        links = [lk for lk in (match_job_url(u, platform, opaque=True) for u in URL_RE.findall(line)) if lk]
        segments = [x for x in (re.sub(r"^[<>\s]+|[<>\s]+$", "", seg) for seg in URL_RE.split(line)) if x]
        # "Label ( url )": the plain text of a tracked link (Welcome to the Jungle); the brackets belong to the link
        segments = [x for x in (re.sub(r"\s*\($", "", re.sub(r"^\)\s*", "", seg)) for seg in segments) if x]
        text = "\n".join(segments)                 # "Title <url>New": the text on each side of a link is its own line
        if links:
            items.append(("a", links[0], text))
        else:
            items.append(("t", None, text))            # blank lines kept: they separate paragraphs

    def meaningful(lines: list[str]) -> bool:
        return bool(card_lines(lines)[0])

    cards: list[Card] = []
    pending: list[str] = []
    for kind, link, text in items:
        if kind == "t":
            pending.extend(text.splitlines() or [""])
            continue
        lines = pending + text.splitlines()
        pending = []
        button = not (text and meaningful(text.splitlines()[:1]))   # "View job:", "View Details", "1-Click Apply", bare
        if cards and (cards[-1].link.canonical == link.canonical or (button and cards[-1].anchored)):
            cards[-1].lines.extend(lines)                # the same job linked twice, or the button closing a card
            cards[-1].anchored = cards[-1].anchored and not button
            continue
        cards.append(Card(lines, link, anchored=not button))
    trailing_titles = _classify(card_lines(pending)[0])[0]
    if cards and not any(c.anchored for c in cards) and not meaningful(cards[0].lines) and trailing_titles:
        cards = []                                        # links first, card text after each link
        current: Card | None = None
        for kind, link, text in items:
            if kind == "a":
                if current and current.link.canonical == link.canonical and not meaningful(current.lines):
                    continue
                if current:
                    cards.append(current)
                current = Card(text.splitlines(), link, after=True)
            elif current is not None:
                current.lines.extend(text.splitlines() or [""])
        if current:
            cards.append(current)
    return cards


def link_cards(row: dict[str, Any], platform: str | None
               ) -> tuple[list[Card], list[tuple[list[str], str]], int] | None:
    """Cards from the row's `links` (gmail_pull.alert_html): consecutive links to one job are one job, and its
    card is the first `card` among them. Returns (cards, jobs without a card, bare links) or None when no job
    link has a card (then the body is read, as for mail harvested before links were kept). A job link with no
    card whose anchor text is empty or a button or footer label (a logo, an app badge, "Unsubscribe" behind an
    opaque ZipRecruiter token) is a bare link, counted and not reviewed."""
    links = row.get("links")
    if not isinstance(links, list):
        return None
    jobs: list[tuple[JobLink, list[dict[str, Any]]]] = []
    for item in links:
        if not isinstance(item, dict):
            continue
        link = match_job_url(str(item.get("href") or ""), platform)
        if link is None:
            continue
        if jobs and jobs[-1][0].canonical == link.canonical:
            jobs[-1][1].append(item)
        else:
            jobs.append((link, [item]))
    if not any(str(i.get("card") or "").strip() for _, items in jobs for i in items):
        return None
    cards: list[Card] = []
    missing: list[tuple[list[str], str]] = []
    bare = 0
    for link, items in jobs:
        card = next((str(i["card"]) for i in items if str(i.get("card") or "").strip()), None)
        if card is None:
            texts = [t for t in dict.fromkeys(str(i.get("text") or "").strip() for i in items) if t]
            if not card_lines(texts)[0]:
                bare += 1
                continue
            missing.append((texts + [link.canonical], "no card around the link in the HTML (a layout to learn)"))
        else:
            cards.append(Card([ln for ln in card.splitlines() if ln.strip()], link))
    return cards, missing, bare


# A link the mail's footer or app badge wraps, behind the same opaque tracking token as a job (ZipRecruiter's "Pat | Your
# Career Advisor | Download the free iOS or Android app" and its "ZipRecruiter, Inc. © All Rights Reserved" address block):
# text with a footer mark and no role line is not a job card. It is counted as a bare link, like a logo.
FOOTER_MARK = _rx(r"©|\ball rights reserved\b|\bdownload the (?:free )?(?:[\w-]+ ){0,4}apps?\b|\byour career advisor\b")


def footer_card(lines: list[str]) -> bool:
    return any(FOOTER_MARK.search(str(x or "")) for x in lines) and not _classify(card_lines(lines)[0])[0]


# A board's own notice from its alert sender, with no job in it: account housekeeping (password reset or change, email
# verification), an alert's set-up confirmation (Indeed's "Your job alert for <query> is now active"), a profile or welcome
# note (Welcome to the Jungle's "Your profile has been enhanced!", "Welcome! You're in"; LinkedIn's first-time job
# seeker note "<name>, looking for a new job?"). Ledgered `notice` (closed, never
# on the review list) only when the mail has no job card at all; a notice subject on mail with job cards is parsed as usual.
NOTICE_SUBJECT = _rx(r"\bpassword\b|\bverify your (?:email|account)\b|\bconfirm your (?:email|account)\b"
                     r"|\bverification code\b|\bsecurity alert\b|\bnew sign-in\b|\btwo-factor\b"
                     r"|\bjob alert\b.{0,300}\bis (?:now )?active\b|\balert (?:has been |is )?(?:created|activated|set up)\b"
                     r"|\bprofile has been (?:enhanced|updated|completed)\b|^\W*welcome\b.{0,30}\byou(?:'|’)?re in\b"
                     r"|\blooking for a new job\?")


# Indeed's one-job mail (match.indeed.com): the subject is the card, "Full-Stack Developer (City, ST) @ Company"; the body
# is one sentence and its links are buttons and the title, so no card text sits next to the job link.
ONE_JOB_SUBJECT = re.compile(r"^\s*(?P<title>[^()@]+?)\s*\((?P<place>[^()@]+)\)\s*@\s*(?P<company>[^@]+?)\s*$")


def one_job_card(row: dict[str, Any], platform: str | None) -> Card | None:
    """The card of an Indeed one-job mail read from its subject: only when the subject has that form and the mail links
    exactly one job; else None (the mail goes to review as before)."""
    if platform != "indeed":
        return None
    m = ONE_JOB_SUBJECT.match(str(row.get("subject") or ""))
    if not m:
        return None
    urls = URL_RE.findall(str(row.get("body") or "")) + [
        str(i.get("href") or "") for i in row.get("links") or [] if isinstance(i, dict)]
    jobs = {lk.canonical: lk for lk in (match_job_url(u, platform) for u in urls) if lk}
    if len(jobs) != 1:
        return None
    return Card([m["title"], m["company"], m["place"]], next(iter(jobs.values())))


def wttj_search_link(link: JobLink, fields: dict[str, Any]) -> JobLink:
    """A Welcome to the Jungle job behind an opaque tracking link: the WTTJ search for its title and company."""
    canonical = "https://www.welcometothejungle.com/en/jobs?" + urlencode([("query", f"{fields['title']} {fields['company']}")])
    return JobLink(canonical, short_hash(canonical, n=8), link.vendor, link.kind)


def keyed_link(link: JobLink, fields: dict[str, Any], platform: str | None) -> JobLink:
    """The link a record is keyed on: the job link itself, or for a link that hides its job (Indeed's sponsored cts
    target, an opaque tracker) the platform's search for the card's own words."""
    if link.key:
        return link
    return wttj_search_link(link, fields) if platform == "wttj" else indeed_search_link(link, fields)


def indeed_search_link(link: JobLink, fields: dict[str, Any]) -> JobLink:
    """A job whose redirect hides its key: the Indeed search for its title, company and place (what the mail says), so
    the same job from another mail, with other tracking tokens, is the same record."""
    place = str(fields.get("location") or "").split(" | ")[0].strip()
    query = [("q", f"{fields['title']} {fields['company']}")] + ([("l", place)] if place else [])
    canonical = "https://www.indeed.com/jobs?" + urlencode(query)
    return JobLink(canonical, short_hash(canonical, n=8), link.vendor, link.kind)


def _paragraphs(lines: list[str]) -> list[list[str]]:
    blocks, cur = [], []
    for s in lines:
        if s.strip():
            cur.append(s)
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return blocks


def parse_card(card: Card, platform: str | None = None) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    """(fields, why-not, card text). Tries the whole card, then the paragraph nearest its link."""
    blocks = _paragraphs(card.lines)
    tries = [card.lines] + ([blocks[0] if card.after else blocks[-1]] if len(blocks) > 1 else [])
    first_reason = None
    for attempt in tries:
        kept, meta = card_lines(attempt)
        if card.after:
            kept = kept[:6]
        if not kept:
            first_reason = first_reason or "no card text next to the link"
            continue
        if meta.company:
            named = [x for x in kept if is_title(x) or not is_location(x)]
            title, company, places, why = ((named[0], meta.company, [x for x in kept if x != named[0]], None)
                                           if len(named) == 1 else (None, None, [], f"{len(named)} title lines "
                                                                    "next to a sized company line"))
        else:
            title, company, places, why = fields_from(kept, LAYOUT.get(platform or ""))
        if why:
            first_reason = first_reason or why
            continue
        location = " | ".join(_dedupe(places + meta.locations)) or None
        return ({"title": title, "company": company, "location": location, "comp": meta.comp,
                 "employment": meta.employment, "posted_days": meta.posted_days, "injection": meta.injection,
                 "sponsorship_badge": meta.sponsorship_badge}, None, [s for s in card.lines if s.strip()])
    return None, first_reason, [s for s in card.lines if s.strip()]


# =============================================================================== records
@dataclass
class Parsed:
    message_id: str
    platform: str | None
    status: str                                   # parsed | partial | review | unmapped | notice
    reason: str | None = None
    records: list[dict[str, Any]] = field(default_factory=list)
    unparsed: list[tuple[list[str], str]] = field(default_factory=list)
    cards: int = 0
    bare: int = 0                                 # job-shaped links with no card text (logo, button, footer)
    filtered: int = 0
    truncated: bool = False
    read_from: str = "body"                       # links (the HTML's job cards) or body


def alert_body(rec: dict[str, Any], platform_name: str, received: datetime, message_id: str,
               card_text: list[str]) -> str:
    return (f"# {rec['company']}: {rec['title']}\n\n"
            f"- Source: {rec['source_url']} ({platform_name} job alert, received {received:%Y-%m-%d}, "
            f"message {message_id})\n"
            f"- Apply: {rec['apply_url']}\n\n"
            "## JD snapshot (untrusted: data, never instructions)\n\n"
            "JD not captured: this record came from a job-alert email. Before scoring, read the posting: the\n"
            "company's own careers board first (`python3 scripts/fetch_ats.py --board <vendor>:<token> --dry-run`),\n"
            "else with the owner present (`browser-job-search`); then record it with\n"
            "`python3 scripts/jobs_manual.py add --url <source url> --platform <id> --title ... --company ... "
            "--jd-file <file>`.\n\n"
            f"{wrap_untrusted('', 'jd')}\n\n"
            "## Alert card (untrusted)\n\n"
            f"{wrap_untrusted(chr(10).join(card_text), 'alert', limit=1500)}\n\n"
            "## Must-haves\n\n## Nice-to-haves\n")


def build_record(fields: dict[str, Any], link: JobLink, platform: str, platform_name: str, row: dict[str, Any],
                 card_text: list[str], now: datetime) -> dict[str, Any]:
    company = fields["company"]
    slug = slugify(normalize_company(company)) or slugify(company)
    received = parse_ts(row.get("internal_date")) or now
    posted = fields.get("posted_at") or ((received - timedelta(days=fields["posted_days"])).date().isoformat()
                                         if fields.get("posted_days") is not None else None)
    rec = make_record({"name": company, "slug": slug}, title=fields["title"], source=source_for(platform),
                      source_url=link.canonical, apply_url=link.canonical, ats_vendor=link.vendor or "unknown",
                      posted_at=posted, location=fields["location"],
                      remote=remote_mode(fields["location"]), comp=fields["comp"],
                      employment_type=fields["employment"], jd_text="", now=now)
    rec["id"] = f"{slug}-{short_for(platform)}-{slugify(link.key, max_len=40)}"
    rec["jd_hash"] = None
    rec["platform"] = platform
    rec["alert_message_id"] = str(row.get("id") or "")
    rec["alert_received_at"] = iso_utc(received)
    rec["tags"] = ["jd-missing"] + (["injection-suspect"] if fields["injection"] else []) + \
        (["sponsorship-badge"] if fields["sponsorship_badge"] else [])
    rec["_jd_text"] = ""
    rec["_body"] = alert_body(rec, platform_name, received, rec["alert_message_id"], card_text)
    return rec


def parse_alert(row: dict[str, Any], platforms: dict[str, dict[str, Any]], now: datetime,
                title_filter: TitleFilter | None = None) -> Parsed:
    mid = str(row.get("id") or row.get("message_id") or "")
    platform = platform_for(row, platforms)
    out = Parsed(mid, platform, "parsed", truncated=bool(row.get("body_truncated")))
    if platform is None:
        domain = cu.email_domain(sender_of(row)) or "unknown sender"
        out.status, out.reason = "unmapped", (f"sender {domain} is not in targets/platforms.yaml alert_senders: once "
                                              "platform-scout maps it, the next run parses it (if it is not older "
                                              "than the stale window by then)")
        return out
    tf = title_filter or default_title_filter()
    name = str(platforms.get(platform, {}).get("name") or platform)
    if platform == GOOGLE_JOBS:
        return parse_google_alert(row, out, name, now, tf)
    from_links = link_cards(row, platform)
    if from_links is not None:
        cards, missing, bare = from_links
        out.read_from, out.truncated = "links", bool(row.get("links_truncated"))
    else:
        cards, missing, bare = split_cards(str(row.get("body") or ""), platform), [], 0
    real = [c for c in cards if card_lines(c.lines)[0] and not footer_card(c.lines)]
    if not real and not missing:
        single = one_job_card(row, platform)
        if single is not None:
            cards, real = [single], [single]
    out.bare = bare + len(cards) - len(real)
    cards = []
    for c in real:                                 # an opaque link is a job only when its text reads as one card
        if c.link.kind in OPAQUE_KINDS and parse_card(c, platform)[0] is None:
            out.bare += 1
            continue
        cards.append(c)
    if out.read_from == "body" and row.get("body_truncated") and cards:
        cut = cards.pop()                         # the body ends inside it: never read a cut card
        missing = missing + [([s for s in cut.lines if s.strip()],
                              "the last card before the harvest cap cut the body (body_truncated)")]
    out.cards = len(cards) + len(missing)
    out.unparsed.extend(missing)
    if not cards and not missing and NOTICE_SUBJECT.search(str(row.get("subject") or "")):
        out.status = "notice"
        out.reason = (f"a {name} notice, not an alert (account, alert set-up, profile or welcome mail): no job cards, "
                      "none expected")
        return out
    if not cards and not missing:
        out.status = "review"
        out.reason = (f"no job cards: {out.bare} job links without card text (logo, buttons, footer)" if out.bare else
                      "no job links in the stored body or links (a newsletter or notice from an alert sender, an "
                      "HTML-only alert harvested before gmail_pull.py kept its links, or links cut by the harvest cap)")
        return out
    for card in cards:
        fields, why, text = parse_card(card, platform)
        if fields is None:
            out.unparsed.append((text, why or "unreadable card"))
            continue
        if not tf.matches(fields["title"]):
            out.filtered += 1
            continue
        link = keyed_link(card.link, fields, platform)
        if any(r["source_url"] == link.canonical for r in out.records):
            continue                               # the same job linked twice (title and button behind trackers)
        out.records.append(build_record(fields, link, platform, name, row, text, now))
    if out.unparsed:
        out.status = "partial" if (out.records or out.filtered) else "review"
        out.reason = f"{len(out.unparsed)} of {out.cards} cards left unparsed"
    return out


# =============================================================================== Google Jobs alert mail
# The mail ("Job Alerts from Google" from Google's notify-noreply sender): subject
# '10 new jobs for "<query>" - Oct 5'; the plain text is the query and the place ("Near San Francisco, CA"), then per job,
# one paragraph each: a logo initial ("D", or "Logo"), the title (wrapped over lines when long) with the company on its
# last line, the place, "via <board>" (the board Google found it on), "Time icon Sep 30 Work icon Full-time"; then the
# footer. No job link in the plain text. A card is anchored on its "via" line and read only in that shape (review else).
GOOGLE_SUBJECT = re.compile(r"^\s*(?:\d+\+?\s+)?new jobs?\s+for\s+[\"“](?P<query>[^\"”]{1,200})[\"”]", re.I)
GOOGLE_VIA = re.compile(r"^via\s+(?P<board>\S.{0,79})$", re.I)
GOOGLE_ICON = re.compile(r"\b([A-Za-z]+) icon\b", re.I)
GOOGLE_LOGO = re.compile(r"^(?:logo|\S)$", re.I)
MONTH_DAY = re.compile(r"^(?P<mon>[a-z]{3})[a-z]*\.?\s+(?P<day>\d{1,2})(?:,?\s+(?P<year>\d{4}))?$", re.I)
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"), 1)}


@dataclass
class GoogleCard:
    title: str
    company: str
    place: str | None
    board: str | None
    icons: dict[str, str]                         # "time" -> "Sep 30", "work" -> "Full-time" (any "<Name> icon <value>")
    text: list[str]


def google_posted(text: str | None, mail_day: date) -> str | None:
    """A card's date as YYYY-MM-DD, read against the mail's day in the owner's time zone: "Sep 30" (the year that keeps it on or before
    the mail day), "Sep 30, 2026", or relative ("2 days ago", "Just posted", "Yesterday"); None when unreadable."""
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if not s:
        return None
    m = POSTED.match(s)
    if m:
        meta = CardMeta()
        _set_posted(m, meta)
        return (mail_day - timedelta(days=meta.posted_days or 0)).isoformat()
    m = MONTH_DAY.match(s)
    if not m or m["mon"].lower() not in MONTHS:
        return None
    try:
        day = date(int(m["year"] or mail_day.year), MONTHS[m["mon"].lower()], int(m["day"]))
        if not m["year"] and day > mail_day + timedelta(days=2):
            day = day.replace(year=day.year - 1)          # "Dec 30" in a January mail is last year's
    except ValueError:
        return None
    return min(day, mail_day).isoformat()


def _icons(line: str) -> dict[str, str] | None:
    """{"time": "Sep 30", "work": "Full-time"} from "Time icon Sep 30 Work icon Full-time"; None for any other line."""
    parts = GOOGLE_ICON.split(line)
    if len(parts) < 3 or parts[0].strip():
        return None
    return {name.lower(): value.strip() for name, value in zip(parts[1::2], parts[2::2])}


def google_cards(body: str) -> tuple[list[GoogleCard], list[tuple[list[str], str]]]:
    """(cards, unreadable cards with why) from a Google Jobs alert's plain text."""
    paras = _paragraphs([ln.strip() for ln in str(body or "").splitlines()])
    cards: list[GoogleCard] = []
    bad: list[tuple[list[str], str]] = []
    start = 0                                     # paragraphs before this belong to the header or an earlier card
    for i, para in enumerate(paras):
        via = GOOGLE_VIA.match(para[0]) if len(para) == 1 else None
        if not via:
            continue
        j, place = i - 1, None
        if j - 1 >= start and len(paras[j]) == 1 and len(paras[j - 1]) >= 2:
            place, j = paras[j][0], j - 1         # the place sits between the title block and "via"
        block = list(paras[j]) if j >= start else []
        if len(block) >= 3 and GOOGLE_LOGO.match(block[0]):
            block = block[1:]                     # a logo initial with no blank line after it
        lo = j - 1 if j - 1 >= start and len(paras[j - 1]) == 1 and GOOGLE_LOGO.match(paras[j - 1][0]) else max(j, start)
        icons = _icons(paras[i + 1][0]) if i + 1 < len(paras) and len(paras[i + 1]) == 1 else None
        end = i + 1 if icons is not None else i
        text = [ln for p in paras[lo:end + 1] for ln in p]
        start = end + 1
        if len(block) < 2:
            bad.append((text, "a Google Jobs card without its title and company lines (a layout to learn)"))
            continue
        title = re.sub(r"\s+", " ", " ".join(block[:-1])).strip()
        company = block[-1].strip()
        board = via["board"].strip()
        if any(cu.strip_injections(x)[1] for x in (title, company, place or "", board)):
            bad.append((text, "injection text in a Google Jobs card"))
            continue
        if len(title) > 200 or len(company) > 120 or _icons(company) is not None or GOOGLE_VIA.match(company):
            bad.append((text, "a Google Jobs card whose title or company line is not one (a layout to learn)"))
            continue
        cards.append(GoogleCard(title, company, place, board, icons or {}, text))
    return cards, bad


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def google_links(row: dict[str, Any]) -> list[tuple[JobLink, str]]:
    """Per-card Google job links the harvest kept (gmail_pull.py `links`), each with the words of its text and card."""
    out: list[tuple[JobLink, str]] = []
    for item in row.get("links") or []:
        if not isinstance(item, dict):
            continue
        link = match_job_url(str(item.get("href") or ""), GOOGLE_JOBS)
        if link is not None and all(link.canonical != x.canonical for x, _ in out):
            out.append((link, _words(f"{item.get('text') or ''} {item.get('card') or ''}")))
    return out


def google_link_for(card: GoogleCard, links: list[tuple[JobLink, str]], used: set[str]) -> JobLink | None:
    """The kept link whose text names this card: title and company first, else the title alone when only one does."""
    title, company = _words(card.title), _words(card.company)
    free = [(lk, words) for lk, words in links if lk.canonical not in used and title and f" {title} " in f" {words} "]
    both = [lk for lk, words in free if company and f" {company} " in f" {words} "]
    pick = both[0] if both else (free[0][0] if len(free) == 1 else None)
    if pick is not None:
        used.add(pick.canonical)
    return pick


def google_search_link(card: GoogleCard) -> JobLink:
    """A card with no link: the Google Jobs search for its own words (for the owner's browser, never fetched), so the same
    job in another Google alert is the same record."""
    q = " ".join(x for x in (card.title, card.company, card.place) if x)
    canonical = "https://www.google.com/search?" + urlencode([("q", q), ("ibp", "htl;jobs")])
    return JobLink(canonical, short_hash(canonical, n=8), None, "google-jobs-search")


def parse_google_alert(row: dict[str, Any], out: Parsed, name: str, now: datetime, tf: TitleFilter) -> Parsed:
    received = parse_ts(row.get("internal_date")) or now
    mail_day = dept_day(received)
    subject = GOOGLE_SUBJECT.match(str(row.get("subject") or ""))
    query = subject["query"].strip() if subject else None
    cards, bad = google_cards(str(row.get("body") or ""))
    if row.get("body_truncated") and cards:
        cut = cards.pop()                         # the body ends inside it: never read a cut card
        bad.append((cut.text, "the last card before the harvest cap cut the body (body_truncated)"))
    out.cards = len(cards) + len(bad)
    out.unparsed.extend(bad)
    if not cards and not bad:
        out.status = "review"
        out.reason = ("no Google Jobs cards (no 'via <board>' line): a notice from the alert sender, or a new layout")
        return out
    links, used = google_links(row), set()
    for card in cards:
        link = google_link_for(card, links, used)
        if not tf.matches(card.title):
            out.filtered += 1
            continue
        comp = next((parse_comp_text(v) for v in card.icons.values() if SALARY.search(v)), (None, None, None))
        fields = {"title": card.title, "company": card.company, "location": card.place, "comp": comp,
                  "employment": norm_employment(card.icons.get("work")), "posted_days": None,
                  "posted_at": google_posted(card.icons.get("time"), mail_day), "injection": False,
                  "sponsorship_badge": False}
        rec = build_record(fields, link or google_search_link(card), GOOGLE_JOBS, name, row, card.text, now)
        rec["alert_board"] = card.board
        rec["alert_query"] = query
        via = (f"- Via: {card.board} (Google Jobs alert" + (f' for "{query}"' if query else "") + "). Google links are "
               "for the owner's browser; no agent fetches them (Google's terms).\n")
        rec["_body"] = rec["_body"].replace("\n\n## JD snapshot", "\n" + via + "\n## JD snapshot", 1)
        out.records.append(rec)
    if out.unparsed:
        out.status = "partial" if (out.records or out.filtered) else "review"
        out.reason = f"{len(out.unparsed)} of {out.cards} cards left unparsed"
    return out


# =============================================================================== a run over the harvest
@dataclass
class AlertRun:
    files: list[str] = field(default_factory=list)
    alerts: int = 0
    skipped: int = 0
    stale: int = 0
    unmapped: int = 0
    waiting: int = 0                              # unmapped on an earlier run, sender still unmapped
    ignored: int = 0                              # a do-not-map sender (platforms.yaml ignored_senders)
    unlabeled: int = 0
    disabled: int = 0
    truncated: int = 0
    notices: int = 0                              # a board's notice from its alert sender, no job cards (NOTICE_SUBJECT)
    records: list[dict[str, Any]] = field(default_factory=list)
    ledger_rows: list[dict[str, Any]] = field(default_factory=list)
    review: list[dict[str, Any]] = field(default_factory=list)
    platforms: dict[str, dict[str, int]] = field(default_factory=dict)

    def counts(self) -> dict[str, Any]:
        return {"files": len(self.files), "alerts": self.alerts, "already_processed": self.skipped,
                "jobs": len(self.records), "stale": self.stale, "unmapped": self.unmapped,
                "waiting_for_mapping": self.waiting, "ignored": self.ignored, "unlabeled": self.unlabeled,
                "disabled": self.disabled, "notices": self.notices,
                "truncated": self.truncated, "review": len(self.review), "platforms": self.platforms}


def harvest_files(root: Path) -> list[Path]:
    folder = Path(root) / "state" / "harvest"
    return sorted(folder.glob("*.classified.jsonl")) if folder.is_dir() else []


def ledger_path(root: Path) -> Path:
    return Path(root) / "state" / "job-alerts.jsonl"


def review_path(root: Path, day: date) -> Path:
    return Path(root) / "state" / "review" / f"job-alerts-{day:%Y-%m-%d}.md"


def _review_item(row: dict[str, Any], platform: str | None, status: str, reason: str | None, file: str,
                 unparsed: list[tuple[list[str], str]] | None = None) -> dict[str, Any]:
    return {"message_id": str(row.get("id") or ""), "from": str(row.get("from") or sender_of(row))[:120],
            "subject": str(row.get("subject") or "")[:120], "platform": platform, "status": status,
            "reason": reason, "file": file, "unparsed": unparsed or []}


def collect(root: Path, now: datetime, files: list[Path] | None = None,
            platforms: dict[str, dict[str, Any]] | None = None, title_filter: TitleFilter | None = None,
            enabled_sources: Iterable[str] | None = None, reparse: bool = False,
            max_age_days: int = MAX_AGE_DAYS, ignored: dict[str, str] | None = None) -> AlertRun:
    root = Path(root)
    platforms = load_platforms(root) if platforms is None else platforms
    ignored = load_ignored(root) if ignored is None else ignored
    files = harvest_files(root) if files is None else [Path(f) for f in files]
    enabled = set(enabled_sources) if enabled_sources is not None else None
    ledger = [] if reparse else read_jsonl(ledger_path(root))
    done = {r.get("message_id") for r in ledger if r.get("status") not in OPEN_STATUSES}
    reported = {r.get("message_id") for r in ledger if r.get("status") == "unlabeled"}
    waiting = {r.get("message_id") for r in ledger if r.get("status") == "unmapped"} - done
    run = AlertRun(files=[str(f) for f in files])
    seen: set[str] = set()
    stamp = iso_utc(now)

    def ledger_row(mid: str, platform: str | None, status: str, file: str, **extra: Any) -> None:
        run.ledger_rows.append({"at": stamp, "message_id": mid, "platform": platform, "status": status,
                                "file": file, **extra})

    for path in files:
        for row in read_jsonl(path):
            mid = str(row.get("id") or row.get("message_id") or "")
            if not mid or mid in seen:
                continue
            seen.add(mid)
            label = (row.get("classification") or {}).get("label")
            if label != "job_alert":
                if label in UNLABELED_CHECK and mid not in done and mid not in reported:
                    platform = platform_for(row, platforms)
                    urls = URL_RE.findall(str(row.get("body") or "")) + [
                        str(i.get("href") or "") for i in row.get("links") or [] if isinstance(i, dict)]
                    if platform and any(match_job_url(u, platform) for u in urls):
                        run.unlabeled += 1
                        reason = (f"labeled {label} but carries {name_of(platforms, platform)} job links: mail-triage "
                                  "relabels it job_alert with mail_classify.py resolve; the next run parses it")
                        run.review.append(_review_item(row, platform, "unlabeled", reason, path.name))
                        ledger_row(mid, platform, "unlabeled", path.name, label=label)
                continue
            if mid in done:
                run.skipped += 1
                continue
            why = ignored_reason(row, ignored)
            if why is not None:
                run.ignored += 1
                ledger_row(mid, None, "ignored", path.name, reason=f"do not map: {why}")
                continue
            platform = platform_for(row, platforms)
            received = parse_ts(row.get("internal_date"))
            too_old = bool(max_age_days and received is not None and (now - received).days > max_age_days)
            still_unmapped = platform is None and mid in waiting
            if still_unmapped and not too_old:
                run.waiting += 1                  # listed for review on its first run; parsed once mapped
                continue
            if platform and enabled is not None and source_for(platform) not in enabled:
                run.disabled += 1
                continue
            run.alerts += 1
            stats = run.platforms.setdefault(platform, {"alerts": 0, "cards": 0, "jobs": 0, "filtered": 0,
                                                         "unparsed": 0, "bare": 0}) if platform else None
            if stats is not None:
                stats["alerts"] += 1
            if too_old:
                run.stale += 1
                ledger_row(mid, platform, "stale", path.name, reason=f"received {received:%Y-%m-%d}, over "
                           f"{max_age_days} days before the run" + ("; its sender is still unmapped"
                                                                     if still_unmapped else ""))
                continue
            parsed = parse_alert(row, platforms, now, title_filter)
            run.truncated += int(parsed.truncated)
            if parsed.status == "unmapped":
                run.unmapped += 1
            run.notices += int(parsed.status == "notice")
            if stats is not None:
                stats["cards"] += parsed.cards
                stats["jobs"] += len(parsed.records)
                stats["filtered"] += parsed.filtered
                stats["unparsed"] += len(parsed.unparsed)
                stats["bare"] += parsed.bare
            run.records.extend(parsed.records)
            ledger_row(mid, platform, parsed.status, path.name, cards=parsed.cards,
                       jobs=[r["id"] for r in parsed.records], filtered=parsed.filtered,
                       unparsed=len(parsed.unparsed), bare=parsed.bare, reason=parsed.reason, truncated=parsed.truncated,
                       read_from=parsed.read_from)
            if parsed.status in ("partial", "review", "unmapped"):
                run.review.append(_review_item(row, platform, parsed.status, parsed.reason, path.name, parsed.unparsed))
    return run


def record_jd_text(rec: dict[str, Any]) -> str:
    """The JD text of a loaded record ('' when none was captured)."""
    return str(rec.get("_jd_text") or jd_text_from_body(str(rec.get("_body") or ""))).strip()


def name_of(platforms: dict[str, dict[str, Any]], platform: str) -> str:
    return str(platforms.get(platform, {}).get("name") or platform)


def merge_into(existing: dict[str, dict[str, Any]], records: Iterable[dict[str, Any]],
               now: datetime) -> dict[str, list[str]]:
    """Upsert alert records into `existing` (id -> record, in place). Same URL and same source: re-sighting
    (merge_records). Same URL from another source: that record keeps every field and gains the platform in
    `seen_on` ("known"). A new tracking link for a card already recorded from the same platform (same company,
    title and location): re-sighting of that record. A Google Jobs card (a re-aggregator: the posting lives on the board
    its "via" line names) whose company, title and location match a record from any other platform is that record: the
    record gains google-jobs-search in `seen_on` ("known")."""
    by_url = {r.get("source_url"): r for r in existing.values() if r.get("source_url")}
    twins: dict[tuple[str, str], dict[str, Any]] = {}
    by_ctl: dict[str, dict[str, Any]] = {}

    def remember(r: dict[str, Any]) -> None:
        by_url[r.get("source_url")] = r
        ctl = dedup.keys(r)["ctl"]
        if r.get("platform") and str(r.get("source") or "").endswith("alert"):
            twins.setdefault((str(r["platform"]), ctl), r)
        if r.get("platform") != GOOGLE_JOBS:
            by_ctl.setdefault(ctl, r)

    for r in list(existing.values()):
        remember(r)
    out: dict[str, list[str]] = {"new": [], "updated": [], "known": []}
    for rec in records:
        cur = by_url.get(rec.get("source_url"))
        if cur is None and rec.get("platform") == GOOGLE_JOBS:
            other = by_ctl.get(dedup.keys(rec)["ctl"])
            if other is not None:
                seen_on = list(other.get("seen_on") or [])
                if GOOGLE_JOBS not in seen_on:
                    other["seen_on"] = seen_on + [GOOGLE_JOBS]
                if other["id"] not in out["known"]:
                    out["known"].append(other["id"])
                continue
        if cur is None:
            twin = twins.get((str(rec.get("platform")), dedup.keys(rec)["ctl"]))
            if twin is not None and twin.get("source") == rec.get("source"):
                rec = dict(rec, source_url=twin["source_url"], apply_url=twin.get("apply_url") or twin["source_url"])
                cur = twin
        if cur is not None and cur.get("source") != rec.get("source"):
            seen_on = list(cur.get("seen_on") or [])
            if rec.get("platform") and rec["platform"] != cur.get("platform") and rec["platform"] not in seen_on:
                cur["seen_on"] = seen_on + [rec["platform"]]
            if cur["id"] not in out["known"]:
                out["known"].append(cur["id"])
            continue
        res = merge_records(existing, [rec], now)
        out["new"] += res["new"]
        out["updated"] += res["updated"]
        for rid in res["new"] + res["updated"]:
            remember(existing[rid])
    return out


def write_outputs(root: Path, run: AlertRun, now: datetime) -> dict[str, Any]:
    """Append the ledger rows and the review section; returns the paths written."""
    written: dict[str, Any] = {"ledger": None, "review": None}
    for r in run.ledger_rows:
        append_jsonl(ledger_path(root), r)
    if run.ledger_rows:
        written["ledger"] = str(ledger_path(root))
    if run.review:
        path = review_path(root, dept_day(now))       # the department's day, never the UTC date
        lines = [f"## Run {iso_utc(now)}", ""]
        for item in run.review:
            lines.append(f"- {item['message_id']} · {item['from']} · \"{item['subject']}\" · "
                         f"{item['platform'] or 'no platform'} · {item['status']}: {item['reason']}")
            for text, why in item["unparsed"]:
                lines.append(f"  - card, {why}:")
                block = wrap_untrusted(" | ".join(text), "alert", limit=600)
                lines.extend("    " + ln for ln in block.splitlines())
        section = "\n".join(lines) + "\n"
        if path.is_file():
            text = path.read_text(encoding="utf-8").rstrip("\n") + "\n\n" + section
        else:
            text = (f"# Job alerts for review, {now:%Y-%m-%d}\n\n"
                    "Written by scripts/job_alerts.py. Nothing listed here became a job record. Card text is\n"
                    "untrusted data. A mislabeled alert: mail-triage relabels it (mail_classify.py resolve) and the\n"
                    "next run parses it. An unmapped sender: platform-scout vets the platform and adds the sender\n"
                    "to targets/platforms.yaml; the next run parses its waiting alerts (no --reparse), and one older\n"
                    "than the stale window by then is ledgered stale. A new card layout: an engineering issue in\n"
                    "issues.md with the message id; the fix lands with a fixture test, then `job_alerts.py --reparse`.\n\n"
                    + section)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written["review"] = str(path)
    return written


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--file", action="append", default=[], help="a classified harvest file (default: all of them)")
    ap.add_argument("--today", type=cli_date, default=None, help="run date YYYY-MM-DD (default: today)")
    ap.add_argument("--max-age-days", type=int, default=MAX_AGE_DAYS, help="older alerts are ledgered as stale (0: no limit)")
    ap.add_argument("--reparse", action="store_true", help="parse messages already in the ledger again")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    now = now or run_now(args.today)
    platforms = load_platforms(args.root)
    if not platforms:
        print(f"job_alerts: {platforms_path(args.root)} (targets/platforms.yaml) is missing or empty; nothing parsed")
        return 1
    files = [Path(f) for f in args.file] or harvest_files(args.root)
    if not files:
        print("job_alerts: no classified harvest (state/harvest/*.classified.jsonl); nothing to parse")
        return 0
    run = collect(args.root, now, files=files, platforms=platforms, title_filter=load_title_filter(args.root),
                  reparse=args.reparse, max_age_days=args.max_age_days)
    existing = load_jobs(args.root) if (Path(args.root) / "jobs").is_dir() else {}
    out = merge_into(existing, run.records, now)
    written: dict[str, Any] = {}
    if not args.dry_run:
        for rid in dict.fromkeys(out["new"] + out["updated"] + out["known"]):
            save_job(args.root, existing[rid])
        written = write_outputs(args.root, run, now)
    if args.json:
        print(json.dumps({**run.counts(), "new": out["new"], "updated": out["updated"], "known": out["known"],
                          "written": written, "dry_run": args.dry_run}, indent=2, default=str))
        return 0
    cards = sum(p["cards"] for p in run.platforms.values())
    filtered = sum(p["filtered"] for p in run.platforms.values())
    unparsed = sum(p["unparsed"] for p in run.platforms.values())
    print(f"job_alerts: {len(files)} files, {run.alerts} alerts to parse ({run.skipped} already processed); "
          f"{len(run.records)} jobs from {cards} cards ({filtered} off-title, {unparsed} unparsed); "
          f"{run.stale} stale, {run.unmapped} unmapped, {run.unlabeled} unlabeled, {run.truncated} truncated"
          + (f"; {run.waiting} waiting for a sender mapping (platform-scout)" if run.waiting else ""))
    for pid, st in sorted(run.platforms.items()):
        print(f"  {pid}: {st['alerts']} alerts, {st['cards']} cards, {st['jobs']} jobs, {st['filtered']} off-title, "
              f"{st['unparsed']} unparsed, {st.get('bare', 0)} bare links")
    if written.get("review"):
        print(f"review: {written['review']} ({len(run.review)} items)")
    elif run.review:
        print(f"review: {len(run.review)} items (not written)")
    mode = "dry-run (nothing written)" if args.dry_run else "written"
    print(f"{mode}: {len(out['new'])} new, {len(out['updated'])} re-sighted, {len(out['known'])} already known")
    return 0


if __name__ == "__main__":
    sys.exit(main())
