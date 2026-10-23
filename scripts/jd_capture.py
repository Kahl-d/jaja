#!/usr/bin/env python3
"""jd_capture.py - capture the JD of every open job at or above the hunt cut, only through channels whose terms allow
it.

Most open jobs came from alert mail (LinkedIn, ZipRecruiter, Glassdoor): a title and a link to a platform page we may
not fetch. This finds the same role where reading is allowed, cheapest first, and stops at the first JD found:
  application the JD an application folder already holds (applications/<slug>/jd.md; its application.md names the
              job), taken whatever the job's score: the owner picked that job
  local       another record in jobs/ is the same role (company slug and normalized title) and holds its JD
  board       the company's board in targets/companies.yaml (Greenhouse, Lever, Ashby, Workday, Jibe, sitemap and the
              other public APIs fetch_ats.py polls), read once per company per run, matched by normalized title
  ats-url     the job's own apply or source URL is on a Greenhouse, Lever or Ashby board (a company not on the
              watchlist): that board's public API
  ats-guess   a company not on the watchlist: its slug tried once on Greenhouse (accepted only when the board's own
              name is the company's), then Ashby and Lever (accepted only on an exact normalized title)
  page        the job's own page on a company site: robots.txt first (RFC 9309, crawl-delay honoured), one GET, the
              page's schema.org JobPosting. Job platforms and LinkedIn, Glassdoor and Indeed are never fetched
              (web_snapshot.check_url: targets/platforms.yaml plus the hard floor)
  firecrawl   only with --firecrawl: scripts/web_snapshot.py (its robots check, budget and refusals) on a company
              page with no JobPosting, and for a company with no public board but a careers search page (config
              `policy.jd_capture.careers_search`) a search by the exact title, then the matching job page (two credits)
Whatever is left goes to the named-read list: at most READS_PER_DAY a day, highest hunt score first, for the owner's
own browser (LinkedIn job pages through `browser_research.py job-view`). After a read, `record` files the text; it
refuses a hand read past the daily cap.

    python3 scripts/jd_capture.py run [--cut 72] [--firecrawl] [--dry-run] [--json] [--today YYYY-MM-DD]
    python3 scripts/jd_capture.py record --job ID --jd-file FILE|-

`run` writes the JDs into jobs/<id>.md (inside <untrusted_jd>, with a line naming where each came from), the receipt
state/jd/capture-<date>.json and the list state/jd/read-list-<date>.md (and .json). Nothing is applied to or sent.
Re-gate and re-score afterwards: python3 scripts/pipeline.py --stages gates,queue,triage
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as cfg  # noqa: E402
import fetch_ats as fa  # noqa: E402
import pending_triage as pt  # noqa: E402
import web_snapshot as ws  # noqa: E402
from fetch_ats import iso_utc  # noqa: E402
from js_common import (JS_ROOT, append_jsonl, cli_date, dept_day, html_to_text, normalize_company,  # noqa: E402
                       normalize_title, read_jsonl, text_hash, wrap_untrusted)

CUT = 72.0                     # the daily hunt cut (hunt_score.py)
READS_PER_DAY = int(cfg.get("policy.jd_capture.reads_per_day", 20))   # named reads a day, in the owner's session
BUDGET_S = 12 * 60             # one capture run; the rest are deferred to the next run
MIN_JD = 200                   # characters: shorter text is a teaser, not a JD
PAGE_DELAY_S = 2.0
GUESS_VENDORS = ("greenhouse", "ashby", "lever")
MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
NO_CHANNEL = "no public board or page"
CLOSED = "likely closed"
TITLED = ("greenhouse", "lever", "ashby", "workday")    # boards that list every title (a sitemap lists URLs)
ATS_HOSTS = {"boards.greenhouse.io": "greenhouse", "job-boards.greenhouse.io": "greenhouse",
             "jobs.lever.co": "lever", "jobs.ashbyhq.com": "ashby"}
STATE = Path("state") / "jd"
READS_LOG = STATE / "reads.jsonl"
Snap = Callable[[str, Path], "tuple[int, str]"]
_JD_SECTION = fa._JD_BLOCK                   # the <untrusted_jd> section of a job record body


class Refused(ValueError):
    pass


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def careers_search(slug: str) -> tuple[str, re.Pattern[str]] | None:
    """(search URL template with {q}, job-page pattern) for a company whose careers site has a search page but no
    public board; from config `policy.jd_capture.careers_search`: {<company slug>: {search, job_pattern}}."""
    entry = (cfg.get("policy.jd_capture.careers_search", {}) or {}).get(slug)
    if isinstance(entry, dict) and entry.get("search") and entry.get("job_pattern"):
        return str(entry["search"]), re.compile(str(entry["job_pattern"]))
    return None


def jd_of(rec: dict[str, Any]) -> str:
    return str(rec.get("_jd_text") or fa.jd_text_from_body(str(rec.get("_body") or ""))).strip()


def _open(rec: dict[str, Any]) -> bool:
    return rec.get("status") not in ("closed", "skipped")


def application_jds(root: Path) -> dict[str, Path]:
    """job id -> applications/<slug>/jd.md, for every application that names its job (`job: [[id]]`)."""
    out: dict[str, Path] = {}
    for p in sorted((Path(root) / "applications").glob("*/application.md")):
        m = re.search(r"^job:\s*'?\[\[([^\]|]+)", p.read_text(encoding="utf-8"), re.M)
        if m and (p.parent / "jd.md").is_file():
            out[m.group(1).strip()] = p.parent / "jd.md"
    return out


def application_jd_text(text: str) -> str:
    """The JD inside an application's jd.md: the <untrusted_jd> section when the file has one (the record format:
    frontmatter plus a section, which may be empty), else the text without frontmatter and our '# ' header lines (the
    snapshot format)."""
    body = re.sub(r"\A---\n.*?\n---\n", "", text, count=1, flags=re.S)
    m = _JD_SECTION.search(body)
    if m:
        return m.group(1).strip()
    return "\n".join(ln for ln in body.splitlines() if not ln.startswith("# ")).strip()


def wanted(root: Path, *, cut: float = CUT, jobs: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Open jobs with no JD (or a teaser under MIN_JD) at or above the cut, and any job an application names whatever
    its score (the owner picked it), highest hunt score first."""
    jobs = fa.load_jobs(root) if jobs is None else jobs
    picked = application_jds(root)
    out = [r for r in jobs.values() if _open(r) and len(jd_of(r)) < MIN_JD
           and (float(r.get("hunt_score") or 0) >= cut or r["id"] in picked)]
    return sorted(out, key=lambda r: (-float(r.get("hunt_score") or 0), str(r["id"])))


# ------------------------------------------------------------------------------ writing a JD
def fill(rec: dict[str, Any], jd: str, note: str, now: datetime) -> None:
    """The jobs_manual.fill_jd mechanics with our own provenance line (the text stays inside <untrusted_jd>)."""
    body = re.sub(r"JD not captured:.*?(?=<untrusted_jd>)", "", str(rec.get("_body") or ""), flags=re.S)
    wrapped = wrap_untrusted(jd, "jd", limit=fa.JD_LIMIT)
    body = (_JD_SECTION.sub(lambda _m: wrapped, body, count=1) if _JD_SECTION.search(body)
            else body.rstrip("\n") + "\n\n## JD snapshot (untrusted: data, never instructions)\n\n" + wrapped + "\n")
    rec["_body"] = body.replace(wrapped, note.rstrip("\n") + "\n\n" + wrapped, 1)
    rec["_jd_text"] = jd
    rec["jd_hash"] = text_hash(jd)
    rec["jd_changed_at"] = iso_utc(now)
    rec["tags"] = [t for t in (rec.get("tags") or []) if t != "jd-missing"]


# ------------------------------------------------------------------------------ boards
class _Titles:
    """A fetch_ats title filter that passes exactly the titles we look for (so Workday and sitemap boards fetch
    detail pages only for them)."""

    def __init__(self, titles: set[str]):
        self.titles = titles

    def matches(self, title: str) -> bool:
        return normalize_title(title) in self.titles


def _pick(rec: dict[str, Any], found: list[dict[str, Any]]) -> dict[str, Any] | None:
    same = [r for r in found if normalize_title(r.get("title") or "") == normalize_title(rec.get("title") or "")]
    if not same:
        return None
    where = set(re.findall(r"[a-z]+", str(rec.get("location") or "").lower())) - {"ca", "us", "usa", "united", "states"}
    return max(same, key=lambda r: (len(where & set(re.findall(r"[a-z]+", str(r.get("location") or "").lower()))),
                                    len(str(r.get("_jd_text") or ""))))


def ats_from_url(url: str) -> tuple[str, str] | None:
    parts = urlparse(str(url or ""))
    vendor = ATS_HOSTS.get((parts.hostname or "").lower())
    seg = [s for s in parts.path.split("/") if s]
    if vendor and seg and seg[0] not in ("embed", "v1"):
        return vendor, seg[0]
    return None


class Boards:
    """One fetch per (vendor, token) per run; the records matching any title we look for at that company."""

    def __init__(self, root: Path, http: Any, now: datetime, titles: dict[str, set[str]]):
        self.root, self.http, self.now, self.titles = root, http, now, titles
        self.cache: dict[tuple[str, str, str], list[dict[str, Any]] | str] = {}
        self.full: dict[tuple[str, str, str], int | None] = {}      # roles on a board read in full; None when partial
        self.named: dict[str, str | None] = {}

    @staticmethod
    def _key(company: dict[str, Any], title: str | None) -> tuple[str, str, str]:
        vendor = str(company.get("ats_vendor") or "").lower()
        token = str(company.get("board_token") or company.get("careers_url") or "")
        return vendor, token, (str(title or "") if vendor == "workday" else "")

    def read(self, company: dict[str, Any], key: str, title: str | None = None) -> list[dict[str, Any]] | str:
        """A Workday board is searched by the job's own title (its listing pages run to thousands); every other
        board is read once."""
        k = self._key(company, title)
        vendor = k[0]
        if k not in self.cache:
            kw = fa._vendor_kw(vendor, 1 if vendor == "workday" else 3, (), None, company.get("slug"))
            if vendor == "workday" and title:
                company = dict(company, search_text=title)
            res = fa.fetch_company(company, self.http, self.now, _Titles(self.titles.get(key, set())), **kw)
            failed = res.errors or (res.notes and not res.records and not res.all_urls)
            self.cache[k] = "; ".join(res.errors or res.notes) if failed else res.records
            self.full[k] = len(res.all_urls) if res.complete and not res.errors and vendor in TITLED else None
        return self.cache[k]

    def read_in_full(self, company: dict[str, Any], title: str | None = None) -> int | None:
        return self.full.get(self._key(company, title))

    def greenhouse_name(self, token: str) -> str | None:
        if token not in self.named:
            try:
                data = self.http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{quote(token, safe='')}")
                self.named[token] = str((data or {}).get("name") or "") or None
            except AssertionError:
                raise
            except Exception:
                self.named[token] = None
        return self.named[token]


def _guess_tokens(rec: dict[str, Any]) -> list[str]:
    slug = str(rec.get("company_slug") or "")
    name = normalize_company(rec.get("company") or "")
    out = []
    for t in (slug, slug.replace("-", ""), name.replace(" ", ""), name.replace(" ", "-")):
        if t and t not in out:
            out.append(t)
    return out[:2]


# ------------------------------------------------------------------------------ the run
def _default_snap(root: Path) -> Snap:
    def snap(url: str, out: Path) -> tuple[int, str]:
        proc = subprocess.run([sys.executable, str(Path(__file__).with_name("web_snapshot.py")), "scrape", url,
                               "--out", str(out), "--root", str(root)], capture_output=True, text=True)
        if proc.returncode not in (0, 4) or not out.is_file():
            return proc.returncode, (proc.stderr or proc.stdout).strip()[-300:]
        m = re.search(r"<untrusted_web>\n?(.*?)\n?</untrusted_web>", out.read_text(encoding="utf-8"), re.S)
        return proc.returncode, (m.group(1) if m else "")
    return snap


def capture(root: Path, *, today: date, http: Any = None, cut: float = CUT, snap: Snap | None = None,
            dry_run: bool = False, now: datetime | None = None, budget_s: float | None = None,
            clock: Any = time.monotonic) -> dict[str, Any]:
    """With budget_s, finds are tried best first until the budget is spent (requests in flight stop at
    the same deadline, fetch_ats.set_deadline); the rest are `deferred` to the next run, and the run still writes its
    true counts. Without it every find is tried."""
    root = Path(root)
    deadline = clock() + budget_s if budget_s else None
    fa.set_deadline(clock, deadline)
    try:
        return _capture(root, today=today, http=http, cut=cut, snap=snap, dry_run=dry_run, now=now,
                        deadline=deadline, clock=clock)
    finally:
        fa.set_deadline(clock, None)


def _capture(root: Path, *, today: date, http: Any, cut: float, snap: Snap | None, dry_run: bool,
             now: datetime | None, deadline: float | None, clock: Any) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    http = http if http is not None else fa.Http()
    jobs = fa.load_jobs(root)
    todo = wanted(root, cut=cut, jobs=jobs)
    watch = {str(c.get("slug")): c for c in fa.load_companies(root)}
    watch_names = {normalize_company(c.get("name") or ""): c for c in watch.values()}
    titles: dict[str, set[str]] = {}
    for rec in todo:
        titles.setdefault(str(rec.get("company_slug")), set()).add(normalize_title(rec.get("title") or ""))
    boards = Boards(root, http, now, titles)
    twins: dict[tuple[str, str], dict[str, Any]] = {}
    for r in jobs.values():
        if len(jd_of(r)) >= MIN_JD:
            twins.setdefault((str(r.get("company_slug")), normalize_title(r.get("title") or "")), r)
    picked = application_jds(root)
    guessed: dict[str, tuple[str, dict[str, Any]] | None] = {}
    robots: dict[str, Any] = {}
    last_page: dict[str, float] = {}
    captured: list[dict[str, Any]] = []
    left: list[dict[str, Any]] = []
    deferred: list[str] = []
    counts = {k: 0 for k in ("application", "local", "board", "ats-url", "ats-guess", "page", "firecrawl")}

    def take(rec: dict[str, Any], channel: str, jd: str, where: str) -> None:
        how = {"local": "copied from", "application": "copied from",
               "firecrawl": "captured through Firecrawl from"}.get(channel, "captured from")
        fill(rec, jd, f"JD {how} {where} on {today.isoformat()} ({channel}; scripts/jd_capture.py).", now)
        if not dry_run:
            fa.save_job(root, rec)
        counts[channel] += 1
        captured.append({"id": rec["id"], "channel": channel, "from": where, "hunt_score": rec.get("hunt_score"),
                         "company": rec.get("company"), "title": rec.get("title")})

    def from_board(rec: dict[str, Any], company: dict[str, Any], key: str) -> tuple[str, str] | str:
        found = boards.read(company, key, rec.get("title"))
        name = f"{company.get('slug')} {company.get('ats_vendor')}"
        if isinstance(found, str):
            return f"the {name} board did not answer ({found[:80]})"
        hit = _pick(rec, found)
        if hit is None:
            n = boards.read_in_full(company, rec.get("title"))
            return f"not on the {name} board" + (f", read in full ({n} roles): likely closed" if n is not None else "")
        jd = str(hit.get("_jd_text") or "").strip()
        if len(jd) < MIN_JD:
            return "the board's description is too short"
        return jd, str(hit.get("source_url") or name)

    for rec in todo:
        if deferred or (deadline is not None and clock() > deadline):     # best first: the rest wait for the next run
            deferred.append(rec["id"])
            continue
        slug = str(rec.get("company_slug"))
        why: list[str] = []
        if rec["id"] in picked:
            text = application_jd_text(picked[rec["id"]].read_text(encoding="utf-8"))
            if len(text) >= MIN_JD:
                take(rec, "application", text, str(picked[rec["id"]].relative_to(root)))
                continue
        twin = twins.get((slug, normalize_title(rec.get("title") or "")))
        if twin is not None and twin["id"] != rec["id"]:
            take(rec, "local", jd_of(twin), f"jobs/{twin['id']}.md")
            continue
        company = watch.get(slug) or watch_names.get(normalize_company(rec.get("company") or ""))
        if company and (company.get("board_token") or (str(company.get("ats_vendor")).lower() == "workday"
                                                       and company.get("careers_url"))):
            got = from_board(rec, dict(company, name=company.get("name") or rec.get("company")), slug)
            if isinstance(got, tuple):
                take(rec, "board", *got)
                continue
            why.append(got)
        urls = [u for u in dict.fromkeys([rec.get("apply_url"), rec.get("source_url")]) if u]
        ats = next((a for a in map(ats_from_url, urls) if a), None)
        if ats and not (company and company.get("board_token") == ats[1]):
            got = from_board(rec, {"name": rec.get("company"), "slug": slug, "ats_vendor": ats[0],
                                   "board_token": ats[1]}, slug)
            if isinstance(got, tuple):
                take(rec, "ats-url", *got)
                continue
            why.append(got)
        if company is None and not ats:
            if slug not in guessed:
                guessed[slug] = None
                for token in _guess_tokens(rec):
                    for vendor in GUESS_VENDORS:
                        if vendor == "greenhouse":
                            name = boards.greenhouse_name(token)
                            if not name or normalize_company(name) != normalize_company(rec.get("company") or ""):
                                continue
                        cand = {"name": rec.get("company"), "slug": slug, "ats_vendor": vendor, "board_token": token}
                        found = boards.read(cand, slug)
                        if isinstance(found, list):
                            guessed[slug] = (vendor, cand)
                            break
                    if guessed[slug]:
                        break
            if guessed[slug]:
                got = from_board(rec, guessed[slug][1], slug)
                if isinstance(got, tuple):
                    take(rec, "ats-guess", *got)
                    continue
        page_urls = []
        for u in urls:
            if ats_from_url(u):
                continue
            try:
                ws.check_url(u, root)
            except ws.Refused:
                continue
            page_urls.append(u)
        done = False
        for u in page_urls:
            parts = urlparse(u)
            host = f"{parts.scheme}://{parts.netloc}"
            if host not in robots:
                robots[host] = pt._robots(http, host)
            rules = robots[host]
            if isinstance(rules, str):
                why.append(rules)
                continue
            if not rules.allowed(u):
                why.append(f"{parts.netloc}/robots.txt disallows this path")
                continue
            if host in last_page:
                _sleep(max(PAGE_DELAY_S, rules.delay or 0))
            last_page[host] = time.monotonic()
            try:
                status, page = http.get_text(u, headers=fa.HTML_ACCEPT)
            except AssertionError:
                raise
            except Exception as exc:
                why.append(f"{parts.netloc}: no answer ({str(exc)[:60]})")
                continue
            if status != 200:
                why.append(f"{parts.netloc}: HTTP {status}")
                continue
            post = fa.job_posting(page) or {}
            jd = html_to_text(str(post.get("description") or "")).strip()
            t_rec, t_page = normalize_title(rec.get("title") or ""), normalize_title(post.get("title") or "")
            if len(jd) >= MIN_JD and t_page and (t_rec in t_page or t_page in t_rec):
                take(rec, "page", jd, u)
                done = True
                break
            why.append(f"{parts.netloc}: no JobPosting for this title on the page")
        if done:
            continue
        if snap is not None:
            got = _firecrawl(root, rec, snap, page_urls, slug, today)
            if isinstance(got, tuple):
                take(rec, "firecrawl", *got)
                continue
            if got:
                why.append(got)
        if deadline is not None and clock() > deadline:     # cut mid-find: the next run tries it, not the read list
            deferred.append(rec["id"])
            continue
        reason = why[0] if why else NO_CHANNEL + ("" if snap is not None else "; Firecrawl not asked (--firecrawl)")
        left.append({"id": rec["id"], "hunt_score": rec.get("hunt_score"), "company": rec.get("company"),
                     "title": rec.get("title"), "url": rec.get("source_url") or rec.get("apply_url"), "why": reason})

    closed = [r for r in left if r["why"].endswith(CLOSED)]
    left = [r for r in left if not r["why"].endswith(CLOSED)]
    read_list, later = left[:READS_PER_DAY], left[READS_PER_DAY:]
    out = {"date": today.isoformat(), "cut": cut, "dry_run": dry_run, "firecrawl": snap is not None,
           "counts": {"wanted": len(todo), "captured": len(captured), **counts, "likely_closed": len(closed),
                      "read_list": len(read_list), "read_later": len(later), "deferred": len(deferred)},
           "captured": captured, "likely_closed": closed, "read_list": read_list,
           "read_later": [r["id"] for r in later], "deferred": deferred}
    if not dry_run:
        d = root / STATE
        d.mkdir(parents=True, exist_ok=True)
        (d / f"capture-{today.isoformat()}.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        (d / f"read-list-{today.isoformat()}.json").write_text(json.dumps(read_list, indent=2) + "\n",
                                                               encoding="utf-8")
        (d / f"read-list-{today.isoformat()}.md").write_text(render_list(out), encoding="utf-8")
    return out


def _firecrawl(root: Path, rec: dict[str, Any], snap: Snap, page_urls: list[str], slug: str,
               today: date) -> tuple[str, str] | str | None:
    d = root / STATE / "snapshots"
    stem = f"{rec['id']}-{today.isoformat()}"
    target = page_urls[0] if page_urls else None
    searcher = careers_search(slug)
    if target is None and searcher:
        search_url, job_pattern = searcher
        search = search_url.format(q=quote(f'"{rec.get("title")}"'))
        saved = d / f"{stem}-search.md"
        if saved.is_file():                               # today's search is already paid for: read it again
            m = re.search(r"<untrusted_web>\n?(.*?)\n?</untrusted_web>", saved.read_text(encoding="utf-8"), re.S)
            code, text = 0, (m.group(1) if m else "")
        else:
            code, text = snap(search, saved)
        if code not in (0, 4):
            return f"Firecrawl search failed ({text[:80]})"
        want = normalize_title(rec.get("title") or "")
        links = [(re.sub(r"^\s*learn more about\s+", "", t, flags=re.I), u.split("?", 1)[0])
                 for t, u in MD_LINK.findall(text)]           # a search page may write "Learn more about <title>" and add ?q=
        target = next((u for t, u in links if job_pattern.match(u) and normalize_title(t) == want), None)
        if target is None:
            return "no careers-search result with this exact title"
    if target is None:
        return None
    code, text = snap(target, d / f"{stem}.md")
    if code not in (0, 4) or len(text.strip()) < MIN_JD:
        return f"Firecrawl read nothing usable ({text.strip()[:80]})"
    return text.strip(), target


def render_list(out: dict[str, Any]) -> str:
    def cell(v: Any) -> str:
        return pt._cell(v, 70)
    lines = [f"# Named reads for {out['date']}: JDs no allowed channel could reach",
             "",
             f"Written by scripts/jd_capture.py. {len(out['read_list'])} postings, highest hunt score first (at most "
             f"{READS_PER_DAY} a day); {out['counts']['read_later']} wait for a later day. Read each in the owner's "
             "own browser (a LinkedIn job page: `python3 scripts/browser_research.py job-view --job ID --url URL` "
             "first), copy the whole description into a file, then run the record command. Reading only: never "
             "Apply, Save, Follow or Message from these pages.",
             "",
             "| score | company | title | why no JD | open | then |",
             "|---|---|---|---|---|---|"]
    for r in out["read_list"]:
        lines.append(f"| {r.get('hunt_score')} | {cell(r.get('company'))} | {cell(r.get('title'))} | {cell(r['why'])} | "
                     f"{r.get('url')} | `python3 scripts/jd_capture.py record --job {r['id']} --jd-file <file>` |")
    if out.get("likely_closed"):
        lines += ["", f"## Likely closed ({len(out['likely_closed'])}): not read, not counted",
                  "", "The company's own board was read in full today and the title is not on it. job-scout confirms "
                  "and closes the record (a board scan closes board-sourced records by itself; alert records it cannot "
                  "see).", "", "| score | company | title | why |", "|---|---|---|---|"]
        lines += [f"| {r.get('hunt_score')} | {cell(r.get('company'))} | {cell(r.get('title'))} | {cell(r['why'])} |"
                  for r in out["likely_closed"]]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------ a hand read
def reads_today(root: Path, now: datetime) -> int:
    day = dept_day(now).isoformat()
    return sum(1 for row in read_jsonl(Path(root) / READS_LOG) if row.get("day") == day)


def record(root: Path, *, job_id: str, jd_file: Path | str, now: datetime | None = None) -> str:
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    jobs = fa.load_jobs(root)
    rec = jobs.get(job_id)
    if rec is None:
        raise Refused(f"no job {job_id} in jobs/")
    if len(jd_of(rec)) >= MIN_JD:
        raise Refused(f"{job_id} already has its JD; nothing changed")
    if reads_today(root, now) >= READS_PER_DAY:
        raise Refused(f"{READS_PER_DAY} hand reads already filed today ({dept_day(now)}); the rest wait")
    text = sys.stdin.read() if str(jd_file) == "-" else Path(jd_file).read_text(encoding="utf-8")
    jd = (html_to_text(text) if re.search(r"<(?:p|div|li|ul|br|h\d)\b", text, re.I) else text).strip()
    if len(jd) < MIN_JD:
        raise Refused(f"the text is {len(jd)} characters; copy the whole description (at least {MIN_JD})")
    where = rec.get("source_url") or rec.get("apply_url")
    fill(rec, jd, f"JD recorded {dept_day(now)} by hand in the owner's browser from {where} (scripts/jd_capture.py "
                  "record).", now)
    fa.save_job(root, rec)
    append_jsonl(root / READS_LOG, {"at": iso_utc(now), "day": dept_day(now).isoformat(), "job": job_id,
                                    "url": where, "chars": len(jd)})
    return (f"JD added to {job_id} ({reads_today(root, now)} of {READS_PER_DAY} hand reads today); re-gate: "
            "python3 scripts/pipeline.py --stages gates,queue,triage")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--cut", type=float, default=CUT)
    r.add_argument("--firecrawl", action="store_true", help="also use web_snapshot.py (credits, its budget)")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--json", action="store_true")
    r.add_argument("--today", type=cli_date, default=None)
    r.add_argument("--budget-min", type=float, default=BUDGET_S / 60,
                   help="stop starting new finds after this many minutes (0: no budget); the rest wait for the next run")
    c = sub.add_parser("record")
    c.add_argument("--job", required=True)
    c.add_argument("--jd-file", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "record":
        try:
            print(record(args.root, job_id=args.job, jd_file=args.jd_file))
        except Refused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        return 0
    today = args.today or dept_day(datetime.now(timezone.utc))
    out = capture(args.root, today=today, cut=args.cut, dry_run=args.dry_run,
                  snap=_default_snap(args.root) if args.firecrawl else None,
                  budget_s=args.budget_min * 60 if args.budget_min else None)
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        c = out["counts"]
        print(f"{'dry run: ' if args.dry_run else ''}{c['wanted']} open jobs at or above {args.cut:g} had no JD; "
              f"captured {c['captured']} (application {c['application']}, local {c['local']}, board {c['board']}, ats-url {c['ats-url']}, "
              f"ats-guess {c['ats-guess']}, page {c['page']}, firecrawl {c['firecrawl']}); named reads "
              f"{c['read_list']}, later {c['read_later']}, deferred to the next run {c['deferred']}")
        if not args.dry_run:
            print(f"list: state/jd/read-list-{today.isoformat()}.md; next: python3 scripts/pipeline.py "
                  "--stages gates,queue,triage")
    return 0


if __name__ == "__main__":
    sys.exit(main())
