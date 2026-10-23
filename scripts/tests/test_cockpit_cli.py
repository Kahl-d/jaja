"""`python3 dashboard/cockpit.py --top N`: the morning plan opens with the cockpit's top actions."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard"))
import cockpit  # noqa: E402
import dash_api  # noqa: E402


def test_the_cli_prints_the_top_actions_in_order_and_says_what_it_could_not_read(tmp_path, monkeypatch, capsys):
    acts = [cockpit._act("reply", f"Role {i}", "w", 0.25, 0.25, None, "2026-10-0%d" % i) for i in range(1, 8)]
    monkeypatch.setattr(dash_api, "load_jobs", lambda root: {})
    monkeypatch.setattr(dash_api, "build", lambda *a, **k: {})
    monkeypatch.setattr(cockpit, "read_connectors", lambda js, osr: None)
    monkeypatch.setattr(cockpit, "build", lambda *a, **k: {"next": acts[:5], "more": acts[5:], "errors": ["the mail could not be read"]})
    assert cockpit.main(["--root", str(tmp_path), "--top", "3"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[1].startswith("1. reply: Role 1 (25%, 15 min)") and out[3].startswith("3. reply: Role 3")
    assert len([ln for ln in out if ln[:2] in ("1.", "2.", "3.", "4.")]) == 3
    assert out[-1] == "not read: the mail could not be read"


def test_an_amber_token_due_soon_is_still_in_the_expiring_list():
    """CS12 F1: an amber token reads "re-auth due <date> PT" (no "until"); the cockpit dropped it from Next 7 days."""
    conns = [{"name": "gmail-send-token", "state": "amber", "summary": "re-auth due 2026-10-08 21:17 PT (Testing mode)"},
             {"name": "calendar-token", "state": "green", "summary": "calendar.events token good until 2026-10-08 21:17 PT"}]
    now = datetime(2026, 10, 7, 9, 0, tzinfo=cockpit.PT)
    out = cockpit.coming({}, conns, now, [])
    assert ("2026-10-08", "calendar, gmail-send tokens (re-authorize; CONNECTORS.md)") in \
        [(e["when"], e["what"]) for e in out["expiring"]]
