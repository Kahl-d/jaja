#!/usr/bin/env python3
"""YC startups -> their public Ashby, Greenhouse or Lever board -> targets/companies.yaml.

Most YC startups post only on Work at a Startup, which no script may read (YC's terms bar scraping; its alert mail
is the channel). The rest have a public board that fetch_ats.py can poll once it
is on the watchlist. This script finds those boards:

  1. Companies: the yc-oss hiring mirror (GET https://yc-oss.github.io/api/companies/hiring.json, a community
     snapshot of YC's directory). Verdict (also in targets/platforms.yaml, yc):
     index-only, no license file. It holds facts (company names, slugs, websites); we keep only those plus the board
     token verified on the vendor's own API, and never republish it.
     Kept: active, hiring, batch year >= --since-year, a location matching policy.search.yc_location_pattern, the topic words
     (policy.search.yc_topic_pattern, none = any) in the one-liner, tags or description, and not already on the
     watchlist (slug, name or board token).
  2. Real URLs first: the company's own website (robots.txt honoured): its home page, /careers and /jobs, read for
     links to jobs.ashbyhq.com, (job-)boards.greenhouse.io and jobs.lever.co. ycombinator.com and workatastartup.com
     are never requested.
  3. Guessed slugs last: the YC slug, the slug without dashes and the name's letters, against each vendor's public
     API; a guess counts only when its board lists at least one job, and it is written only when the company's own
     website domain appears in that board's payload (a guessed one-word board can belong to
     another company of the same name). A guess without the domain goes to `to_confirm` in state/yc_boards.json for a person.

Every hit is checked against the vendor's public API, then appended to targets/companies.yaml as tier good,
verified: false, under an "unverified" comment line, so the daily pipeline polls it and a person confirms it is the
right company. The Scout writes the watchlist too: if companies.yaml changed during the run it is re-read (its new
tokens respected) and the append retried once. A company with no board is cached in state/yc_boards.json and not probed again for NO_BOARD_DAYS.
Sequential requests, a small per-host delay. Mirror text is untrusted data.

CLI:  python yc_boards.py [--root R] [--mirror FILE] [--since-year 2024] [--limit N] [--today YYYY-MM-DD] [--write]
      (a dry run unless --write: nothing is written, not even the cache)
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as cfg  # noqa: E402
from fetch_ats import FetchError, Http, Robots, load_companies  # noqa: E402
from js_common import JS_ROOT, cli_date, normalize_company, today  # noqa: E402

MIRROR = "https://yc-oss.github.io/api/companies/hiring.json"
API = {"ashby": "https://api.ashbyhq.com/posting-api/job-board/{}",
       "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{}/jobs",
       "lever": "https://api.lever.co/v0/postings/{}"}
BOARD_URL = {"ashby": "https://jobs.ashbyhq.com/{}", "greenhouse": "https://job-boards.greenhouse.io/{}",
             "lever": "https://jobs.lever.co/{}"}
PAGES = ("", "/careers", "/jobs")
NEVER_HOSTS = ("ycombinator.com", "workatastartup.com")
NO_BOARD_DAYS = 14

# Which mirror companies are kept: the locations and the topic are the owner's choice (config).
LOCATION = re.compile(str(cfg.get("policy.search.yc_location_pattern", r"Remote|United States")), re.I)
_TOPIC = str(cfg.get("policy.search.yc_topic_pattern", "") or "")
TOPIC_TEXT = re.compile(_TOPIC, re.I) if _TOPIC else None       # none: every topic passes
LINK = re.compile(
    r"https?://jobs\.ashbyhq\.com/(?P<ashby>(?!api/)[^/?#\"'\s<>]+)"
    r"|https?://(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?(?:[^\"'\s<>]*&)?for=(?P<ghembed>[\w-]+)"
    r"|(?!embed/)(?P<gh>[\w-]+))"
    r"|https?://jobs\.lever\.co/(?P<lever>[\w.-]+)", re.I)


def _as_list(value: Any) -> list[str]:
    """The mirror stores lists as their Python repr ("['AI', 'B2B']"); accept either form."""
    if isinstance(value, list):
        return [str(v) for v in value]
    try:
        parsed = ast.literal_eval(str(value or "[]"))
    except (ValueError, SyntaxError):
        return [str(value)] if value else []
    return [str(v) for v in parsed] if isinstance(parsed, (list, tuple)) else []


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() == "true"


def _known(companies: list[dict[str, Any]]) -> tuple[set[str], set[tuple[str, str]]]:
    names = {str(c.get("slug") or "").lower() for c in companies} | {normalize_company(str(c.get("name") or ""))
                                                                     for c in companies}
    tokens = {(str(c.get("ats_vendor") or "").lower(), str(c.get("board_token") or "").lower())
              for c in companies if c.get("board_token")}
    return names - {""}, tokens


def candidates(mirror: list[dict[str, Any]], companies: list[dict[str, Any]], since_year: int = 2024) -> list[dict]:
    names, _ = _known(companies)
    out = []
    for c in mirror:
        if not isinstance(c, dict) or not c.get("slug") or not c.get("website"):
            continue
        if str(c.get("status") or "Active") != "Active" or not _truthy(c.get("isHiring", True)):
            continue
        year = re.search(r"(\d{4})", str(c.get("batch") or ""))
        if not year or int(year.group(1)) < since_year:
            continue
        if not LOCATION.search(" ".join([str(c.get("all_locations") or "")] + _as_list(c.get("regions")))):
            continue
        text = " ".join([str(c.get("one_liner") or ""), str(c.get("long_description") or "")] + _as_list(c.get("tags")))
        if TOPIC_TEXT and not TOPIC_TEXT.search(text):
            continue
        if str(c["slug"]).lower() in names or normalize_company(str(c.get("name") or "")) in names:
            continue
        out.append(c)
    return out


def board_links(html: str) -> list[tuple[str, str]]:
    """(vendor, token) for every board URL in a page, in page order, each once."""
    out: list[tuple[str, str]] = []
    for m in LINK.finditer(html or ""):
        if m.group("ashby"):
            pair = ("ashby", unquote(m.group("ashby")))
        elif m.group("ghembed") or m.group("gh"):
            pair = ("greenhouse", m.group("ghembed") or m.group("gh"))
        else:
            pair = ("lever", m.group("lever"))
        if pair not in out:
            out.append(pair)
    return out


def probe(vendor: str, token: str, http: Any) -> tuple[int, str] | None:
    """(number of jobs the vendor's public API lists for this board, the payload as lowercase text), or None when
    there is no such board."""
    url = API[vendor].format(quote(token, safe=""))
    try:
        data = http.get_json(url, params={"mode": "json"} if vendor == "lever" else None)
    except (FetchError, ValueError):
        return None
    if vendor == "lever":
        jobs = data if isinstance(data, list) else None
    else:
        jobs = data.get("jobs") if isinstance(data, dict) else None
    return (len(jobs), json.dumps(data).lower()) if isinstance(jobs, list) else None


def _website(c: dict[str, Any]) -> str | None:
    site = str(c.get("website") or "").strip()
    if site and "://" not in site:
        site = "https://" + site
    host = urlparse(site).netloc.lower()
    if urlparse(site).scheme not in ("http", "https") or not host:
        return None
    if any(host == h or host.endswith("." + h) for h in NEVER_HOSTS):
        return None
    return site.rstrip("/")


def _guesses(c: dict[str, Any]) -> list[str]:
    slug = str(c.get("slug") or "").lower()
    out: list[str] = []
    for s in (slug, slug.replace("-", ""), re.sub(r"[^a-z0-9]", "", str(c.get("name") or "").lower())):
        if s and s not in out:
            out.append(s)
    return out


def discover(c: dict[str, Any], http: Any) -> dict[str, Any] | None:
    site = _website(c)
    tried: set[tuple[str, str]] = set()
    if site:
        try:
            status, text = http.get_text(site + "/robots.txt")
        except FetchError:
            status, text = None, None
        if status is not None:
            robots = Robots(text if status < 400 else "")
            for path in PAGES:
                page = site + path
                if not robots.allowed(page):
                    continue
                try:
                    code, html = http.get_text(page)
                except FetchError:
                    continue
                if code >= 400:
                    continue
                for vendor, token in board_links(html):
                    if (vendor, token.lower()) in tried:
                        continue
                    tried.add((vendor, token.lower()))
                    hit = probe(vendor, token, http)
                    if hit is not None:
                        return {"vendor": vendor, "token": token, "how": "website", "jobs": hit[0], "page": page,
                                "confirmed": True}
    for token in _guesses(c):
        for vendor in ("ashby", "greenhouse", "lever"):
            if (vendor, token) in tried:
                continue
            hit = probe(vendor, token, http)
            if hit and hit[0]:
                domain = urlparse(site or "").netloc.lower().removeprefix("www.")
                return {"vendor": vendor, "token": token, "how": "guess", "jobs": hit[0], "page": None,
                        "confirmed": bool(domain) and domain in hit[1]}
    return None


def entry_text(c: dict[str, Any], found: dict[str, Any], today: date) -> str:
    vendor, token = found["vendor"], found["token"]
    where = f"website {found['page']}" if found["how"] == "website" else f"guessed slug {token}"
    one_liner = re.sub(r"\s+", " ", str(c.get("one_liner") or "")).strip()[:140]
    notes = (f"YC {c.get('batch') or 'batch unknown'}: {one_liner} (yc-oss mirror text, untrusted). Board found by "
             f"scripts/yc_boards.py ({found['how']}) on {today.isoformat()}; a person confirms it "
             "is this company, then the comment line reads verified.")
    lines = [
        f"  # unverified: found by scripts/yc_boards.py {today.isoformat()} ({where}; the {vendor} API listed "
        f"{found['jobs']} jobs)",
        f"  - name: {json.dumps(str(c.get('name') or c['slug']))}",
        f"    slug: {json.dumps(str(c['slug']))}",
        "    tier: good",
        "    verified: false",
        f"    ats_vendor: {vendor}",
        f"    board_token: {json.dumps(token)}",
        f"    careers_url: {json.dumps(BOARD_URL[vendor].format(quote(token, safe='')))}",
        "    sponsorship_stance: unknown",
        "    h1b_approvals_recent: null",
        f"    notes: {json.dumps(notes)}",
    ]
    return "\n".join(lines) + "\n"


def _cache_path(root: Path) -> Path:
    return Path(root) / "state" / "yc_boards.json"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def run(root: Path, http: Any, today: date, mirror: list[dict[str, Any]] | None = None, write: bool = False,
        since_year: int = 2024, limit: int | None = None) -> dict[str, int]:
    root = Path(root)
    companies = load_companies(root)
    if mirror is None:
        mirror = http.get_json(MIRROR)
    cands = candidates(mirror or [], companies, since_year)
    if limit is not None:
        cands = cands[:limit]
    cache_file = _cache_path(root)
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    no_board: dict[str, str] = dict(cache.get("no_board") or {})
    to_confirm: dict[str, dict[str, Any]] = dict(cache.get("to_confirm") or {})
    _, tokens = _known(companies)
    summary = {"candidates": len(cands), "probed": 0, "found": 0, "no_board": 0, "cached": 0, "to_confirm": 0, "written": 0}
    path = root / "targets" / "companies.yaml"
    start_mtime = path.stat().st_mtime_ns if path.is_file() else None
    blocks: list[tuple[tuple[str, str], str]] = []
    for c in cands:
        seen = no_board.get(str(c["slug"]))
        if seen and date.fromisoformat(seen) > today - timedelta(days=NO_BOARD_DAYS):
            summary["cached"] += 1
            continue
        summary["probed"] += 1
        found = discover(c, http)
        if not found:
            summary["no_board"] += 1
            no_board[str(c["slug"])] = today.isoformat()
            continue
        no_board.pop(str(c["slug"]), None)
        if not found["confirmed"]:
            summary["to_confirm"] += 1
            to_confirm[str(c["slug"])] = {"vendor": found["vendor"], "token": found["token"], "jobs": found["jobs"],
                                          "seen": today.isoformat()}
            print(f"to confirm {c['slug']}: {found['vendor']}:{found['token']} (guess; its domain is not on the board)")
            continue
        summary["found"] += 1
        to_confirm.pop(str(c["slug"]), None)
        key = (found["vendor"], found["token"].lower())
        print(f"found {c['slug']}: {found['vendor']}:{found['token']} ({found['how']}, {found['jobs']} jobs)"
              + ("  [already watched]" if key in tokens else ""))
        if key in tokens:
            continue
        tokens.add(key)
        blocks.append((key, entry_text(c, found, today)))
    if write:
        for attempt in range(2):
            if not blocks:
                break
            mtime = path.stat().st_mtime_ns
            old = path.read_text(encoding="utf-8")
            if mtime != start_mtime:                        # another writer since we read it: respect its tokens
                _, fresh = _known(load_companies(root))
                blocks = [(k, b) for k, b in blocks if k not in fresh]
                if not blocks:
                    break
            new = old + ("" if old.endswith("\n") else "\n") + "".join(b for _, b in blocks)
            before = len((yaml.safe_load(old) or {}).get("companies") or [])
            after = len((yaml.safe_load(new) or {}).get("companies") or [])
            if after != before + len(blocks):
                raise RuntimeError(f"{path}: appending {len(blocks)} entries gave {after - before}; nothing written")
            if path.stat().st_mtime_ns != mtime:            # changed while we built the text: re-read once
                start_mtime = None
                continue
            _atomic_write(path, new)
            summary["written"] = len(blocks)
            break
        else:
            raise RuntimeError(f"{path} kept changing under another writer; nothing written, run again")
        _atomic_write(cache_file, json.dumps({"no_board": dict(sorted(no_board.items())),
                                             "to_confirm": dict(sorted(to_confirm.items()))}, indent=1) + "\n")
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--mirror", type=Path, help="a saved copy of the yc-oss hiring.json instead of fetching it")
    ap.add_argument("--since-year", type=int, default=2024)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--today", type=cli_date)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    mirror = json.loads(args.mirror.read_text(encoding="utf-8")) if args.mirror else None
    summary = run(args.root, Http(), args.today or today(), mirror=mirror, write=args.write,
                  since_year=args.since_year, limit=args.limit)
    mode = "written" if args.write else "dry-run (nothing written)"
    print(f"yc_boards {mode}: " + ", ".join(f"{k} {v}" for k, v in summary.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
