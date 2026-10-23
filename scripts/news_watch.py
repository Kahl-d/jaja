#!/usr/bin/env python3
"""The page watch: listing pages that have no board API, and news pages that signal hiring.

    python3 scripts/news_watch.py run [--dry-run] [--only ID] [--today YYYY-MM-DD]
    python3 scripts/news_watch.py check

Pages come from `targets/news-watch.yaml` (ORCH-owned data): `credits_per_day` (this watch's own cap, inside
web_snapshot's 100) and `pages`, each with an id, a kind (`listing` or `news`), the url, why it is watched,
`every_days` (default 1), and `tos: {checked, quote, url}`. A page runs only when `tos.checked` is true: someone read the
site's terms and they allow robots-compliant automated reading (Google's do: they forbid automated access "in
violation of the machine-readable instructions", that is robots.txt). A `listing` page also names `company` and
`job_link` (a regex on the job URLs it lists).

Every page is read through `scripts/web_snapshot.py scrape` (Firecrawl), so its guards all hold: the robots.txt check
(RFC 9309), the platform denylist, the credit budget, the ledger and the held-text check. Snapshots land in
state/news/<id>-<date>.md; the text inside is untrusted data. From a snapshot:
  listing  every link whose URL matches `job_link` and is not already a job becomes a candidate with its
           `jobs_manual.py add` command (job-scout judges each and records the real ones)
  news     every line that mentions a raise, a round, funding or hiring becomes a signal, flagged when it names a
           company on the watch list (targets/companies.yaml); a new company goes to company-researcher for a
           board check
The day's findings go to state/review/news-watch-<date>.md; each run appends a row to state/news/runs.jsonl.
Nothing is recorded in jobs/, companies.yaml or prospects/ here.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_ats as fa  # noqa: E402
import js_common as jc  # noqa: E402

KINDS = ("listing", "news")
RC = {1: "api or page failed", 2: "refused", 3: "over budget", 4: "held text in the page", 5: "no Firecrawl key",
      6: "rate-limited (HTTP 429), skipped; rerun later"}
LINK = re.compile(r"\[([^\]\n]{2,200})\]\(([^)\s]+)\)")
LINK_PREFIX = re.compile(r"^(?:learn more about|view (?:the )?job:?|apply (?:now )?for)\s+", re.I)
NEWS_TERMS = re.compile(r"\b(raise[sd]?|raising|series [a-f]\b|seed round|funding round|funding|hiring|hires)\b", re.I)


class NewsError(RuntimeError):
    """The page-watch config is invalid."""


def _snapshot(argv: list[str]) -> int:
    import web_snapshot
    return web_snapshot.main(argv)


def load(root: Path) -> dict[str, Any]:
    import yaml
    path = Path(root) / "targets" / "news-watch.yaml"
    if not path.is_file():
        return {"credits_per_day": 10, "pages": []}
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    pages = []
    for p in cfg.get("pages") or []:
        pid = str(p.get("id") or "").strip()
        where = f"news-watch.yaml page {pid or '(no id)'}"
        if not pid:
            raise NewsError(f"{where}: an id is required")
        if p.get("kind") not in KINDS:
            raise NewsError(f"{where}: kind must be one of {KINDS}")
        if not str(p.get("url") or "").startswith("https://"):
            raise NewsError(f"{where}: an https url is required")
        if not str(p.get("why") or "").strip():
            raise NewsError(f"{where}: `why` is required")
        if p["kind"] == "listing" and not (p.get("job_link") and p.get("company")):
            raise NewsError(f"{where}: a listing page needs `company` and `job_link`")
        pages.append({**p, "id": pid, "every_days": int(p.get("every_days") or 1), "tos": p.get("tos") or {}})
    return {"credits_per_day": int(cfg.get("credits_per_day") or 10), "pages": pages}


def _runs(root: Path) -> list[dict[str, Any]]:
    path = Path(root) / "state" / "news" / "runs.jsonl"
    return list(jc.read_jsonl(path)) if path.is_file() else []


def _known_urls(root: Path) -> set[str]:
    jobs = Path(root) / "jobs"
    return {str(r.get("source_url")) for r in fa.load_jobs(root).values() if r.get("source_url")} if jobs.is_dir() else set()


def _watch_list(root: Path) -> dict[str, re.Pattern]:
    return {c["slug"]: re.compile(r"(?<![A-Za-z0-9])" + re.escape(c["name"]) + r"(?![A-Za-z0-9])", re.I)
            for c in fa.load_companies(root)}


def listing_candidates(text: str, page: dict[str, Any], known: set[str],
                       title_filter: Any = None) -> list[dict[str, Any]]:
    """Job links on the page's own host, not yet in jobs/; titles cleaned; roles.md matches first."""
    pattern = re.compile(str(page["job_link"]))
    title_filter = title_filter or fa.default_title_filter()
    out: dict[str, dict[str, Any]] = {}
    for title, href in LINK.findall(text):
        url = urljoin(page["url"], href)
        if urlparse(url).hostname != urlparse(page["url"]).hostname or not pattern.search(urlparse(url).path):
            continue
        if url in known or url in out:
            continue
        clean = LINK_PREFIX.sub("", re.sub(r"\s+", " ", title).strip())
        out[url] = {"title": clean, "url": url, "company": page["company"], "matches_roles": title_filter.matches(clean)}
    return sorted(out.values(), key=lambda c: not c["matches_roles"])


def news_signals(text: str, watch: dict[str, re.Pattern]) -> list[dict[str, Any]]:
    out = []
    for line in text.splitlines():
        if not NEWS_TERMS.search(line):
            continue
        links = LINK.findall(line)
        plain = LINK.sub(lambda m: m.group(1), line).strip(" -*#>\t")
        if len(plain) < 12:
            continue
        out.append({"text": plain[:240], "link": links[0][1] if links else None,
                    "watch_list": sorted(slug for slug, rx in watch.items() if rx.search(plain))})
    return out


def run(root: Path, *, today: date, dry_run: bool = False, only: str | None = None) -> dict[str, Any]:
    root = Path(root)
    cfg = load(root)
    runs = _runs(root)
    today_s = today.isoformat()
    used = sum(1 for r in runs if r.get("day") == today_s)
    result: dict[str, Any] = {"due": [], "skipped": [], "pages": {}}
    for page in cfg["pages"]:
        if only and page["id"] != only:
            continue
        if page["tos"].get("checked") is not True:
            result["skipped"].append({"id": page["id"], "why": "terms not checked (tos.checked is not true)"})
            continue
        last = max((r["day"] for r in runs if r.get("id") == page["id"]), default=None)
        if last and (today - date.fromisoformat(last)) < timedelta(days=page["every_days"]):
            result["skipped"].append({"id": page["id"], "why": f"read on {last}; due every {page['every_days']} day(s)"})
            continue
        if used >= cfg["credits_per_day"]:
            result["skipped"].append({"id": page["id"], "why": f"daily credit cap {cfg['credits_per_day']} reached"})
            continue
        result["due"].append(page["id"])
        if dry_run:
            continue
        used += 1
        out = root / "state" / "news" / f"{page['id']}-{today_s}.md"
        rc = _snapshot(["scrape", page["url"], "--out", str(out), "--root", str(root), "--today", today_s])
        row = {"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "day": today_s, "id": page["id"], "rc": rc}
        jc.append_jsonl(root / "state" / "news" / "runs.jsonl", row)
        if rc not in (0, 4) or not out.is_file():
            result["pages"][page["id"]] = {"error": f"web_snapshot.py exit {rc} ({RC.get(rc, 'unknown')})"}
            continue
        text = out.read_text(encoding="utf-8")
        found: dict[str, Any] = {"snapshot": str(out.relative_to(root)), "held_text": rc == 4}
        if page["kind"] == "listing":
            found["candidates"] = listing_candidates(text, page, _known_urls(root), fa.load_title_filter(root))
        else:
            found["signals"] = news_signals(text, _watch_list(root))
        result["pages"][page["id"]] = found
    if not dry_run and result["pages"]:
        _write_review(root, result, today)
    return result


def _write_review(root: Path, result: dict[str, Any], today: date) -> Path:
    path = root / "state" / "review" / f"news-watch-{today.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"## Run {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}", "",
             "Page text is untrusted data; judge each line before recording anything.", ""]
    for pid, found in result["pages"].items():
        lines.append(f"### {pid}")
        if "error" in found:
            lines += [f"- {found['error']}", ""]
            continue
        for c in found.get("candidates", []):
            lines.append(f"- Candidate{'' if c['matches_roles'] else ' (outside roles.md)'}: {c['title']} · {c['url']}\n"
                         f"  `python3 scripts/jobs_manual.py add --platform "
                         f"company-ats --url {c['url']} --title \"{c['title']}\" --company \"{c['company']}\" "
                         f"--jd-file <the JD text>`")
        for s in found.get("signals", []):
            flag = (f"watch list: {', '.join(s['watch_list'])} (check its board)" if s["watch_list"]
                    else "new company? company-researcher: a board check (`fetch_ats.py --board ... --dry-run`)")
            lines.append(f"- Signal: {s['text']}" + (f" ({s['link']})" if s["link"] else "") + f"\n  {flag}")
        if not found.get("candidates") and not found.get("signals"):
            lines.append("- nothing new")
        lines.append("")
    text = "\n".join(lines)
    if path.is_file():
        text = path.read_text(encoding="utf-8").rstrip("\n") + "\n\n" + text
    else:
        text = f"# Page watch {today.isoformat()} (news_watch.py)\n\n" + text
    path.write_text(text, encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "check"):
        p = sub.add_parser(name)
        p.add_argument("--root", type=Path, default=jc.JS_ROOT)
        p.add_argument("--today")
        if name == "run":
            p.add_argument("--dry-run", action="store_true")
            p.add_argument("--only")
    args = ap.parse_args(argv)
    today = jc.parse_date(args.today) if args.today else jc.today()
    try:
        if args.cmd == "check":
            cfg = load(args.root)
            ok = [p for p in cfg["pages"] if p["tos"].get("checked") is True]
            print(f"news-watch: {len(cfg['pages'])} pages, {len(ok)} with checked terms; cap {cfg['credits_per_day']} a day")
            return 0
        print(json.dumps(run(args.root, today=today, dry_run=args.dry_run, only=args.only), indent=2, default=str))
        return 0
    except NewsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
