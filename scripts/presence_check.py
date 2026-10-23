#!/usr/bin/env python3
"""presence_check.py - every public surface says the current truth, and nothing that arrives there goes unwatched.

Reads the portal tracker from targets/platforms.yaml
through scripts/portal_registry.py (one source per portal; the old state/presence/portals.yaml only when the
registry carries no presence data) and flags, one line each:

  (a) refresh due       `next_refresh` is a date before today
  (b) old resume        `resume.uploaded` carries a sha256 that is not the current base PDF's
                        (resumes/bases/general/<identity.resume_pdf_name>); also the tracker's own `base_resume.pdf_sha256`
  (c) stale profile     `profile.checked_on` is before the last change to profile/PROFILE.md (`updated:`) or the latest
                        ruling in profile/facts.yaml (`ruled.on`)
  (d) Present after     after the last working day in clock.yaml, state/linkedin/live.md (the live LinkedIn text) still shows
      the last day      the employer as current
  (e) unwatched         an account (exists or exists-inferred) with an inbox (`inbox.in_app`) and no email sender that
                        scripts/mail_classify.py has a rule for
  (f) inbox not swept   `inbox.last_swept` more than 8 days ago

Missing is not empty: a `next_refresh` that is a condition ("after the content pack"), a profile never checked or an inbox
never swept is printed as a `note:`, never as a pass and never as a flag. Exit 0 no flag, 1 a flag, 2 no tracker.
`summary()` gives the dashboard its presence line. Read-only; stdlib plus the department's own modules.

    python3 scripts/presence_check.py [--root R] [--today YYYY-MM-DD] [--json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as acfg  # noqa: E402
import clock as job_clock  # noqa: E402
import js_comms_util as cu  # noqa: E402
import mail_classify as mc  # noqa: E402
import portal_registry  # noqa: E402
from js_common import JS_ROOT, cli_date, dept_day, fm  # noqa: E402

TRACKER = Path("state") / "presence" / "portals.yaml"
BASE_PDF = Path("resumes") / "bases" / "general" / str(acfg.get("identity.resume_pdf_name", "Resume.pdf"))
LIVE_LINKEDIN = Path("state") / "linkedin" / "live.md"
SWEEP_DAYS = 8
ACCOUNT = ("exists", "exists-inferred")
NO_INBOX = ("", "n/a", "none", "unknown")


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10]) if re.match(r"^\d{4}-\d{2}-\d{2}", str(value).strip()) else None
    except ValueError:
        return None


def routed(sender: str) -> bool:
    """True when scripts/mail_classify.py has a rule for this sender's mail."""
    addr = str(sender or "").strip().lower()
    reg = cu.registrable_domain(cu.email_domain(addr))
    return addr in mc.LINKEDIN_RELAYS | mc.LINKEDIN_JOB_ALERTS or reg == "linkedin.com" or reg in (
        mc.JOB_BOARD_DOMAINS | mc.BOARD_MESSAGE_DOMAINS | mc.ATS_DOMAINS | mc.ASSESSMENT_DOMAINS | mc.SCHEDULING_DOMAINS)


def _latest_change(root: Path) -> date | None:
    days = []
    profile = root / "profile" / "PROFILE.md"
    if profile.is_file():
        days.append(_date(fm.load(profile)[0].get("updated")))

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            ruled = node.get("ruled")
            if isinstance(ruled, dict):
                days.append(_date(ruled.get("on", ruled.get(True))))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    facts = root / "profile" / "facts.yaml"
    if facts.is_file():
        walk(yaml.safe_load(facts.read_text(encoding="utf-8")))
    days = [d for d in days if d]
    return max(days) if days else None


def _shows_current(text: str, employer: str) -> bool:
    name = re.compile(r"\b" + r"\s+".join(map(re.escape, employer.split())) + r"\b", re.I)
    lines = text.splitlines()
    return any(name.search(line) and re.search(r"\bpresent\b", " ".join(lines[max(0, i - 1):i + 2]), re.I)
               for i, line in enumerate(lines))


def check(root: Path, today: date) -> tuple[list[str], list[str], dict[str, Any]]:
    """(flags, notes, the tracker); raises FileNotFoundError when there is no tracker."""
    root = Path(root)
    data = portal_registry.tracker(root)          # targets/platforms.yaml; the legacy tracker only as fallback
    flags, notes = [], []
    pdf = root / BASE_PDF
    base = hashlib.sha256(pdf.read_bytes()).hexdigest() if pdf.is_file() else None
    if base is None:
        notes.append(f"{BASE_PDF} not found: the uploaded-resume check (b) did not run")
    elif (data.get("base_resume") or {}).get("pdf_sha256") not in (None, base):
        flags.append(f"the tracker's base_resume.pdf_sha256 is not the current base PDF ({base[:12]}...)")
    latest = _latest_change(root)
    for s in data.get("surfaces") or []:
        sid = s.get("id", "?")
        nxt = s.get("next_refresh")
        if _date(nxt):
            if _date(nxt) < today:
                flags.append(f"{sid}: refresh due (next_refresh {_date(nxt)})")
        elif nxt not in (None, "n/a"):
            notes.append(f"{sid}: next_refresh is a condition, not a date ({nxt})")
        uploaded = (s.get("resume") or {}).get("uploaded")
        if isinstance(uploaded, dict) and base:
            sha = uploaded.get("sha256") or uploaded.get("pdf_sha256")
            if sha and sha != base:
                flags.append(f"{sid}: the resume there is not the current base (uploaded {str(sha)[:12]}...)")
        profile = s.get("profile") or {}
        checked = _date(profile.get("checked_on"))
        if checked and latest and checked < latest:
            flags.append(f"{sid}: profile checked {checked}, before the last change to PROFILE.md or a facts ruling ({latest})")
        elif not checked and (s.get("account") or {}).get("state") in ACCOUNT \
                and profile.get("state") != "none-on-this-portal":
            notes.append(f"{sid}: profile never checked")
        inbox = s.get("inbox") or {}
        has_inbox = str(inbox.get("in_app") or "").strip().lower() not in NO_INBOX
        if (s.get("account") or {}).get("state") in ACCOUNT and has_inbox \
                and not any(routed(x.get("sender")) for x in inbox.get("email_senders") or [] if isinstance(x, dict)):
            flags.append(f"{sid}: unwatched: an account and an inbox, and no sender the classifier routes")
        swept = _date(inbox.get("last_swept") or s.get("last_swept"))
        if swept and swept < today - timedelta(days=SWEEP_DAYS):
            flags.append(f"{sid}: inbox last swept {swept}, more than {SWEEP_DAYS} days ago")
        elif not swept and has_inbox and (s.get("account") or {}).get("state") in ACCOUNT:
            notes.append(f"{sid}: no inbox sweep recorded")
    try:
        cfg = job_clock.load(root)
    except job_clock.ClockError as exc:
        cfg = None
        notes.append(f"clock.yaml unreadable ({exc}): the Present check (d) did not run")
    live = root / LIVE_LINKEDIN
    if cfg and cfg.get("employer") and today > cfg["employment_end"] and live.is_file() \
            and _shows_current(live.read_text(encoding="utf-8"), str(cfg["employer"])):
        notes.append(f"linkedin: {LIVE_LINKEDIN.as_posix()} still shows {cfg['employer']} as current after the last day "
                     f"({cfg['employment_end']}), end it there unless that is deliberate")
    return flags, notes, data


def summary(root: Path, today: date) -> dict[str, Any] | None:
    """The dashboard's presence line: surfaces by profile state, unwatched, flags; None without a tracker."""
    try:
        flags, _, data = check(root, today)
    except FileNotFoundError:
        return None
    surfaces = data.get("surfaces") or []
    state = lambda s: (s.get("profile") or {}).get("state")  # noqa: E731
    return {"surfaces": len(surfaces), "current": sum(state(s) == "current" for s in surfaces),
            "stale": sum(state(s) == "stale" for s in surfaces),
            "contradicts": sum(state(s) == "contradicts" for s in surfaces),
            "unwatched": sum(": unwatched:" in f for f in flags), "flags": len(flags)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--today", type=cli_date, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    today = args.today or dept_day(datetime.now(timezone.utc))
    try:
        flags, notes, _ = check(args.root, today)
    except FileNotFoundError as exc:
        print(f"presence_check: {exc}; the portal tracker is missing")
        return 2
    if args.json:
        print(json.dumps({"today": today.isoformat(), "flags": flags, "notes": notes}, indent=2))
    else:
        for line in flags:
            print(f"presence_check: {line}")
        for line in notes:
            print(f"presence_check: note: {line}")
        print(f"presence_check: {len(flags)} flag{'' if len(flags) == 1 else 's'}")
    return 1 if flags else 0


if __name__ == "__main__":
    sys.exit(main())
