#!/usr/bin/env python3
"""browser_pace.py: the guard before every browser read of a careers site.

A bulk read of job pages on one careers site can trip its rate limit and get the owner's own browser blocked. The rule: browser reads of
a careers site are paced (at least 60 s apart per host, or the host's own `browser_pacing_s` in
targets/platforms.yaml), only for a seat a lane has claimed, never in bulk, and the first 429 or block page stops all
reads of that host (no retry around it) until the recorded time.

Every session that reads a careers page in the owner's browser runs, per page:
  python3 scripts/browser_pace.py check <url>     exit 0: go; 1: wait N s or blocked until T (printed); 2: usage
  ... read the page once ...
  python3 scripts/browser_pace.py mark <url>      records the read time
and on a 429, "access denied", a CAPTCHA or any block page:
  python3 scripts/browser_pace.py blocked <url> [--hours 24] --note "<what the page said>"
State: state/browser/pace.json (per host: last read, blocked until, note). Shared by every lane on this machine.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from agency_paths import DATA_ROOT as JS_ROOT  # noqa: E402
STATE = JS_ROOT / "state" / "browser" / "pace.json"
DEFAULT_GAP_S = 60


def host_of(url: str) -> str:
    h = (urlparse(url if "://" in url else "https://" + url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def gap_for(host: str, root: Path = JS_ROOT) -> int:
    """targets/platforms.yaml may set `browser_pacing_s` for a platform whose url or domains name this host."""
    p = root / "targets" / "platforms.yaml"
    if not p.is_file():
        return DEFAULT_GAP_S
    try:
        import yaml
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return DEFAULT_GAP_S
    rows = data.get("platforms") if isinstance(data, dict) else data
    for r in rows or []:
        if not isinstance(r, dict) or not r.get("browser_pacing_s"):
            continue
        names = [str(r.get("url") or "")] + [str(d) for d in (r.get("domains") or [])]
        if any(host and host_of(n) and (host == host_of(n) or host.endswith("." + host_of(n))) for n in names):
            return max(DEFAULT_GAP_S, int(r["browser_pacing_s"]))
    return DEFAULT_GAP_S


def check(url: str, now: datetime, path: Path = STATE, root: Path = JS_ROOT) -> tuple[bool, str]:
    h = host_of(url)
    rec = _load(path).get(h) or {}
    until = rec.get("blocked_until")
    if until and datetime.fromisoformat(until) > now:
        return False, f"BLOCKED {h} until {until} ({rec.get('note') or 'block page seen'}): do not read, do not retry"
    last = rec.get("last_read")
    gap = gap_for(h, root)
    if last:
        wait = gap - (now - datetime.fromisoformat(last)).total_seconds()
        if wait > 0:
            return False, f"WAIT {int(wait) + 1} s before reading {h} again (at least {gap} s between reads)"
    return True, f"GO {h} (at least {gap} s between reads)"


def mark(url: str, now: datetime, path: Path = STATE) -> None:
    data = _load(path)
    data.setdefault(host_of(url), {})["last_read"] = now.isoformat(timespec="seconds")
    _save(path, data)


def blocked(url: str, now: datetime, hours: float, note: str, path: Path = STATE) -> str:
    data = _load(path)
    rec = data.setdefault(host_of(url), {})
    rec["blocked_until"] = (now + timedelta(hours=hours)).isoformat(timespec="seconds")
    rec["note"] = note[:200]
    _save(path, data)
    return rec["blocked_until"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "mark", "blocked"):
        s = sub.add_parser(name)
        s.add_argument("url")
        if name == "blocked":
            s.add_argument("--hours", type=float, default=24.0)
            s.add_argument("--note", default="block page seen")
    a = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    if a.cmd == "check":
        ok, msg = check(a.url, now)
        print(msg)
        return 0 if ok else 1
    if a.cmd == "mark":
        mark(a.url, now)
        print(f"marked {host_of(a.url)} at {now.isoformat(timespec='seconds')}")
        return 0
    until = blocked(a.url, now, a.hours, a.note)
    print(f"recorded: {host_of(a.url)} blocked until {until}; every lane's check now refuses it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
