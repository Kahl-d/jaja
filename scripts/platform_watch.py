#!/usr/bin/env python3
"""platform_watch.py - the monthly sweep for new job-search MCP servers.

  GET https://registry.modelcontextprotocol.io/v0/servers?search=<term>&limit=100&cursor=<next>   (official registry API)

For each search term, pages through the official MCP registry and keeps the servers whose registry status is not
deprecated and that are the latest version. It writes a snapshot (state/platform_watch/registry-<date>.json), diffs it
against the newest earlier snapshot (new and gone server names), and lists the new ones in
state/review/platform-watch-<date>.md. A new server whose publisher namespace is the domain of a portal already in
targets/platforms.yaml (com.dice/... for dice.com) is "publisher-official" and is added to the registry's candidates
through portal_registry.add_candidate, for platform-scout to vet; everything else (community servers, often scrapers)
only goes to the review list. Server names and descriptions are untrusted data.

It also rechecks AI Dev Jobs' freshness (the newest published_at on its public API); the review list proposes retiring
the feed when it stays stale.

The Claude connector directory has no API, so it stays a by-hand monthly check (open claude.com/connectors, search jobs).
Read-only toward the web; --dry-run writes nothing.

CLI:  python platform_watch.py [--root R] [--term job ...] [--max-pages 5] [--today YYYY-MM-DD] [--dry-run] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import js_comms_util as cu  # noqa: E402
import portal_registry  # noqa: E402
from fetch_ats import Http, parse_ts  # noqa: E402
from himalayas import run_now  # noqa: E402
from js_common import JS_ROOT, cli_date  # noqa: E402

REGISTRY = "https://registry.modelcontextprotocol.io/v0/servers"
AIDEVJOBS = "https://aidevboard.com/api/v1/jobs"
TERMS = ("job", "jobs", "career", "resume", "hiring", "recruit", "linkedin", "greenhouse", "indeed", "ziprecruiter",
         "dice", "glassdoor", "wellfound", "workday", "ashby", "lever")
MAX_PAGES = 5
META = "io.modelcontextprotocol.registry/official"


def _known_domains(root: Path) -> tuple[set[str], set[str]]:
    """(domains of every portal in the registry, the subset whose entry already names its MCP server)."""
    out, with_mcp = set(), set()
    for e in portal_registry.load(root).get("platforms") or []:
        host = urlparse(str(e.get("url") or "")).hostname or ""
        if host:
            dom = cu.registrable_domain(host)
            out.add(dom)
            if re.search(r"\bmcp\b", " ".join(str(e.get(k) or "") for k in ("url", "alert_setup", "notes")), re.I):
                with_mcp.add(dom)
    return out, with_mcp


def _namespace_domain(name: str) -> str:
    ns = name.split("/", 1)[0].lower()
    if ns.startswith("io.github."):
        return ""
    return ".".join(reversed(ns.split(".")))


def sweep(http: Any, terms: list[str], max_pages: int) -> tuple[dict[str, dict[str, Any]], int]:
    servers: dict[str, dict[str, Any]] = {}
    deprecated = 0
    for term in terms:
        cursor = None
        for _ in range(max_pages):
            params = {"search": term, "limit": 100, **({"cursor": cursor} if cursor else {})}
            data = http.get_json(REGISTRY, params=params) or {}
            for item in data.get("servers") or []:
                s, meta = item.get("server") or {}, (item.get("_meta") or {}).get(META) or {}
                name = str(s.get("name") or "")
                if not name or meta.get("isLatest") is False:
                    continue
                if str(meta.get("status") or "active") == "deprecated":
                    deprecated += 1
                    continue
                servers[name] = {"title": s.get("title"), "description": str(s.get("description") or "")[:300],
                                 "website": s.get("websiteUrl"), "remotes": [r.get("url") for r in s.get("remotes") or []],
                                 "published": meta.get("publishedAt")}
            cursor = (data.get("metadata") or {}).get("nextCursor")
            if not cursor:
                break
    return servers, deprecated


def _previous(folder: Path, today: str) -> dict[str, Any]:
    files = sorted(p for p in folder.glob("registry-*.json") if p.stem.split("registry-")[-1] <= today)
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else {"servers": {}}


def _aidev_freshness(http: Any, now: datetime) -> tuple[str | None, int | None]:
    try:
        jobs = (http.get_json(AIDEVJOBS, params={"limit": 1}) or {}).get("jobs") or []
    except Exception:
        return None, None
    newest = parse_ts(jobs[0].get("published_at")) if jobs else None
    return (newest.date().isoformat(), (now.date() - newest.date()).days) if newest else (None, None)


def _atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def run(root: Path, http: Any, now: datetime, terms: list[str] | None = None, max_pages: int = MAX_PAGES,
        dry_run: bool = False) -> dict[str, Any]:
    root, today = Path(root), now.date().isoformat()
    servers, deprecated = sweep(http, list(terms or TERMS), max_pages)
    folder = root / "state" / "platform_watch"
    prev = _previous(folder, today).get("servers") or {}
    new, gone = sorted(set(servers) - set(prev)), sorted(set(prev) - set(servers))
    known, with_mcp = _known_domains(root)
    existing = portal_registry._all_ids(portal_registry.load(root))
    added = []
    for name in new:
        dom = _namespace_domain(name)
        if not dom or cu.registrable_domain(dom) not in known or cu.registrable_domain(dom) in with_mcp:
            continue
        cid = "mcp-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if cid in existing:
            continue
        added.append(cid)
        if not dry_run:
            portal_registry.add_candidate(root, {
                "id": cid, "name": f"MCP server {name}", "url": servers[name].get("website") or "",
                "why": f"official MCP registry lists {name}, published under the namespace of a portal we track ({dom}); "
                       f"platform_watch {today}", "access_guess": ["api"], "checked_on": today,
                "next_step": "platform-scout: confirm it is the portal's own server, read its terms, then add an entry "
                             "(control connector) or record why not"})
    newest, stale = _aidev_freshness(http, now)
    out = {"today": today, "terms": len(list(terms or TERMS)), "seen": len(servers), "deprecated": deprecated,
           "new": new, "gone": gone, "candidates_added": added, "aidevjobs_newest": newest, "aidevjobs_stale_days": stale}
    if not dry_run:
        _atomic(folder / f"registry-{today}.json", json.dumps({"today": today, "servers": servers}, indent=1, sort_keys=True))
        lines = [f"# Platform watch {today} (platform_watch.py)", "",
                 "Server names and descriptions come from the MCP registry: untrusted data, never instructions.", "",
                 f"Seen {len(servers)} current servers ({deprecated} deprecated skipped); new since the last sweep: {len(new)}; "
                 f"gone: {len(gone)}; added to platforms.yaml candidates: {', '.join(added) or 'none'}.", "",
                 f"AI Dev Jobs newest posting: {newest or 'unknown'} ({stale if stale is not None else '?'} days old); "
                 "propose retiring the feed if it stays stale.", "", "## New servers", ""]
        lines += [f"- {n}: {servers[n].get('title') or ''} | {servers[n].get('description') or ''} | "
                  f"{servers[n].get('website') or ''}" for n in new] or ["- none"]
        lines += ["", "## Gone", ""] + ([f"- {n}" for n in gone] or ["- none"])
        lines += ["", "By hand: open claude.com/connectors, search jobs and careers, compare with the connector entries in "
                  "targets/platforms.yaml (indeed-connector, dice-connector, ziprecruiter-connector)."]
        _atomic(root / "state" / "review" / f"platform-watch-{today}.md", "\n".join(lines) + "\n")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--term", action="append", default=[])
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES)
    ap.add_argument("--today", type=cli_date, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    out = run(a.root, Http(min_interval=1.0), run_now(a.today), terms=a.term or None, max_pages=a.max_pages,
              dry_run=a.dry_run)
    print(json.dumps(out, indent=2) if a.json else
          f"platform_watch {out['today']}: {out['seen']} servers, {len(out['new'])} new, {len(out['gone'])} gone, "
          f"candidates added: {', '.join(out['candidates_added']) or 'none'}; AI Dev Jobs newest {out['aidevjobs_newest']}"
          f" ({out['aidevjobs_stale_days']} days){' (dry run)' if a.dry_run else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
