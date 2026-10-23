#!/usr/bin/env python3
"""pace.py - the hard daily counter for outward acts on LinkedIn and X (human pace).

    python3 scripts/pace.py check  --platform linkedin|x --action connect|follow|message|comment|read [--over-soft-cap]
                                   [--now ISO] [--root JS]
    python3 scripts/pace.py record --platform linkedin|x --action ACTION|read|warning|captcha [--note TEXT]
                                   [--warning TEXT (with a read)] [--now ISO]

Run `check` right before every act and `record` right after it lands (or right after a warning shows). The counter
applies whatever the owner has approved: a yes for an act never lifts a cap or the pace.

  caps a day   Defaults: LinkedIn: connect 15, message 30, comment 10, follow 20 (a follow is a lighter act than a
               connect, so above it, under the connect and message total); X: follow 10, message 5, comment 5 (the same
               caps as x_api.py CAPS for follow, dm and reply). X has no connect. Override any of them in the config
               (policy.pace.caps, policy.pace.read_caps, policy.pace.gap_seconds).
  reads        `read` is a page-heavy read (a search, a feed, a profile list). A SOFT cap a day per platform (READ_CAPS,
               default LinkedIn 150, X 100): at the cap `check` exits 1 "soft cap reached" so the lane decides; `--over-soft-cap`
               goes on and says so. A read is not an act: no gap, and it does not move the act gap or the act caps.
  pace         at least GAP_S (default 120 s) between two acts on the same platform, whatever the action.
  stop         `record --action warning` or `--action captcha` (any warning, limit notice, login wall, security
               check or CAPTCHA) stops that platform for the rest of the day; the note says what the page showed.
               `record --action read --warning TEXT` records the read and the warning in one call. A stop blocks every
               action on the platform, reads included, and --over-soft-cap never lifts it.
  day          the local day (identity.timezone); the ledger is state/posts/pace-<date>.jsonl, one JSON object a
               line {"at", "platform", "action", "note"}, append-only.

check prints `go: ...` and exits 0, or `wait N s`, `cap reached` or `stopped for the day` and exits 1; an action a
platform does not have exits 2. record always appends (the ledger tells the truth) and prints the new count; past a
cap it says OVER CAP and exits 1. Nothing here acts on a site.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import agency_config as cfg
from agency_paths import DATA_ROOT as JS_ROOT  # noqa: E402
TZ = ZoneInfo(str(cfg.get("identity.timezone", "UTC") or "UTC"))
CAPS = {"linkedin": {"connect": 15, "message": 30, "comment": 10, "follow": 20},
        "x": {"follow": 10, "message": 5, "comment": 5}}
READ_CAPS = {"linkedin": 150, "x": 100}                # soft: the lane decides at the cap
for _p, _acts in (cfg.get("policy.pace.caps", {}) or {}).items():          # config overrides the built-ins
    if isinstance(_acts, dict):
        CAPS.setdefault(str(_p), {}).update({str(a): int(n) for a, n in _acts.items()})
READ_CAPS.update({str(p): int(n) for p, n in (cfg.get("policy.pace.read_caps", {}) or {}).items()})
READ = "read"
STOPS = ("warning", "captcha")
GAP_S = int(cfg.get("policy.pace.gap_seconds", 120) or 120)


class PaceError(ValueError):
    """A platform or action this counter does not know."""


def _known(platform: str, action: str, *, stops: bool = False) -> None:
    if platform not in CAPS:
        raise PaceError(f"unknown platform {platform!r} (linkedin or x)")
    if action not in CAPS[platform] and action != READ and not (stops and action in STOPS):
        raise PaceError(f"{platform} has no {action} (one of {', '.join(CAPS[platform])}, {READ})")


def ledger(root: Path, now: datetime) -> Path:
    return Path(root) / "state" / "posts" / f"pace-{now.astimezone(TZ).date().isoformat()}.jsonl"


def rows(root: Path, now: datetime) -> list[dict[str, Any]]:
    path = ledger(root, now)
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def check(root: Path, platform: str, action: str, now: datetime, over_soft_cap: bool = False) -> tuple[bool, str]:
    _known(platform, action)
    mine = [r for r in rows(root, now) if r.get("platform") == platform]
    stop = next((r for r in mine if r.get("action") in STOPS), None)
    if stop:
        return False, (f"stopped for the day: {platform} {stop['action']} at {stop.get('at')}"
                       + (f" ({stop['note']})" if stop.get("note") else "") + "; it goes on the owner's card")
    done = sum(1 for r in mine if r.get("action") == action)
    if action == READ:                                  # soft cap, no gap
        cap = READ_CAPS[platform]
        if done < cap:
            return True, f"go: {platform} read {done}/{cap} today (soft cap)"
        if over_soft_cap:
            return True, f"go, over the soft cap: {platform} read {done}/{cap} today; stop at any warning"
        return False, (f"soft cap reached: {platform} read {done}/{cap} today; go on only by a decision "
                       f"(--over-soft-cap), or leave the rest for tomorrow")
    cap = CAPS[platform][action]
    if done >= cap:
        return False, f"cap reached: {platform} {action} {done}/{cap} today"
    acts = [datetime.fromisoformat(r["at"]) for r in mine if r.get("action") in CAPS[platform] and r.get("at")]
    if acts:
        since = (now - max(acts)).total_seconds()
        if since < GAP_S:
            return False, f"wait {int(GAP_S - since + 0.999)} s: the last {platform} act was {int(since)} s ago"
    return True, f"go: {platform} {action} {done}/{cap} today"


def record(root: Path, platform: str, action: str, now: datetime, note: str | None = None,
           warning: str | None = None) -> tuple[bool, str]:
    _known(platform, action, stops=True)
    if warning is not None and action != READ:
        raise PaceError("--warning goes with a read (use --action warning or captcha for the others)")
    path = ledger(root, now)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": now.astimezone(timezone.utc).isoformat(), "platform": platform, "action": action,
                             "note": note}) + "\n")
    if warning is not None:                              # the read, then the warning that stops the platform
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": now.astimezone(timezone.utc).isoformat(), "platform": platform,
                                 "action": "warning", "note": warning}) + "\n")
        return True, f"recorded {platform} read and warning: {platform} is stopped for the rest of the day"
    if action in STOPS:
        return True, f"recorded {platform} {action}: {platform} is stopped for the rest of the day"
    done = sum(1 for r in rows(root, now) if r.get("platform") == platform and r.get("action") == action)
    if action == READ:
        cap = READ_CAPS[platform]
        return True, (f"recorded {platform} read {done}/{cap} today" if done <= cap
                      else f"recorded, past the soft cap: {platform} read {done}/{cap} today")
    cap = CAPS[platform][action]
    if done > cap:
        return False, f"recorded, OVER CAP: {platform} {action} {done}/{cap} today"
    return True, f"recorded {platform} {action} {done}/{cap} today"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "record"):
        s = sub.add_parser(name)
        s.add_argument("--platform", required=True)
        s.add_argument("--action", required=True)
        s.add_argument("--now", help="ISO time (default: now)")
        s.add_argument("--root", type=Path, default=JS_ROOT)
        if name == "check":
            s.add_argument("--over-soft-cap", action="store_true", help="read only: go on past the soft cap")
        if name == "record":
            s.add_argument("--note")
            s.add_argument("--warning", help="read only: also record this warning, which stops the platform")
    args = ap.parse_args(argv)
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        if args.cmd == "check":
            ok, why = check(args.root, args.platform, args.action, now, args.over_soft_cap)
        else:
            ok, why = record(args.root, args.platform, args.action, now, args.note, args.warning)
    except PaceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(why)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
