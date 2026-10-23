#!/usr/bin/env python3
"""rollcall.py - the the orchestrator roll call from files, and GO as an event, instead of check-in messages.

Why (docs/superpowers/specs/2026-10-07-token-efficiency-redesign.md, section 4.4): every lane start sent the same
check-in line to the orchestrator, RECRUITER and its manager, and RECRUITER answered each with a GO message. Each cross-session
send re-sends the sender's whole context and the Desktop app throttles them (10+ minute stalls on 2026-10-06). The
facts were already on disk: lane_register.py writes every lane's address, session id and tip into hub/registry.json at
SessionStart, and each lane appends an `## Identity <time>` section to its hub file. The gate the owner set stays
("don't start working until all lanes are in place", 2026-10-06): a lane holds until a GO event for it is in
hub/events.jsonl, which its start hook prints and `hub_events.py since` shows.

Usage:
  python3 scripts/rollcall.py                         # who is registered, who wrote an identity, who has a GO
  python3 scripts/rollcall.py --go APPLY FOCUSED --text "first task: the 9 queued mass rows"   # RECRUITER sends GO
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

from agency_paths import DATA_ROOT as JS  # noqa: E402
HUB = JS / "hub"
from agency_paths import KERNEL_DIR  # noqa: E402
sys.path.insert(0, str(KERNEL_DIR))
import hub_events  # noqa: E402


def expected_lanes(prog: dict) -> list[str]:
    """The live tree's lanes: the orchestrator rows when there are any (2026-10-07), else the the orchestrator rows."""
    nine = [str(r.get("scope")) for r in prog.get("lanes") or [] if str(r.get("note") or "").startswith("AGENCY-9")
            and str(r.get("scope")) != "PREP"]
    if nine:
        return nine
    out = []
    for r in prog.get("lanes") or []:
        note, wave, status = str(r.get("note") or ""), str(r.get("wave") or ""), str(r.get("status") or "").lower()
        if note.startswith("AGENCY-8") and "on demand" not in (wave + note).lower() and not status.startswith("retired"):
            out.append(str(r.get("scope")))
    return out


def newest_identity(lane: str) -> str:
    p = HUB / "lanes" / f"{lane}.md"
    if not p.is_file():
        return ""
    hits = re.findall(r"(?m)^## Identity\b[ \t]*(.*?)[ \t]*$", p.read_text(errors="replace"))
    return (hits[-1] or "(undated)") if hits else ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--go", nargs="*", help="lanes to send GO to (an event in hub/events.jsonl)")
    ap.add_argument("--text", default="GO")
    ap.add_argument("--by", default="RECRUITER")
    a = ap.parse_args(argv)
    if a.go:
        e = hub_events.append(HUB, a.go, f"GO: {a.text}", by=a.by)
        hub_events.render(HUB)
        print(hub_events.line(e))
        return 0
    prog = json.loads((HUB / "programme.json").read_text())
    reg = (json.loads((HUB / "registry.json").read_text()).get("lanes") or {}) if (HUB / "registry.json").is_file() else {}
    events = hub_events.load(HUB)
    print(f"{'lane':16} {'registered (utc)':22} {'identity':24} GO")
    for lane in expected_lanes(prog):
        row = reg.get(lane) or {}
        when = row.get("registered_utc", "")
        go = [e for e in events if lane.upper() in e["scope"] and e["text"].startswith("GO")]
        if when:
            t = dt.datetime.fromisoformat(when.replace("Z", "+00:00"))
            go = [e for e in go if e["_t"] >= t]
        print(f"{lane:16} {when or '-':22} {newest_identity(lane)[:24] or '-':24} {'yes' if go else '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
