"""One human queue (agency charter): the morning sweep and cards read every place the owner's work waits, not only
approvals and the outbox: HOME needs-you rows for job-search, routine runs whose receipt says NEEDS_YOU, and the open
setup list. Tiny fixture trees only; no live data."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

from disc_harness import *  # noqa: F401,F403
from disc_harness import SCRIPTS


def _load(name: str, alias: str):
    spec = importlib.util.spec_from_file_location(alias, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sweep = _load("recruiter_sweep", "js_sweep_one_queue")
cards = _load("cards", "js_cards_one_queue")

NOW = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)           # 08:00 Pacific

RECEIPT = """---
routine: js-hunt-review
date: 2026-10-06
---
# js-hunt-review receipts 2026-10-06

## Run 2026-10-05T23:50:00-07:00 · ok
- sentinel: NEEDS_YOU
- before the window: not listed

## Run 2026-10-06T07:50:31-07:00 · ok
- sentinel: NEEDS_YOU
- exit 0; new finds 39; review state/review/fresh-roles-2026-10-06.md

## Run 2026-10-06T07:55:00-07:00 · ok
- sentinel: NO_ACTION
- nothing new
"""

SETUP = """---
title: Owner setup list
---
# Do these first

1. A1 Boardco join
2. ~~B1 Jobsite~~ done 2026-10-06
3. A2 Acceleratorco signup

| # | where | your step | unblocks |
|---|---|---|---|
| A1 | boardco | join | four networks |
| A2 | acceleratorco | sign in | accelerator |
| ~~B1~~ | jobsite | delete | clean resumes ~~done~~ |
| D7 (optional) | talentsite | references | 100% |

# Already done (no action)

| Z9 | old | row | never counted |
"""


def seed(root: Path, os_root: Path) -> None:
    (os_root / "HOME.json").write_text(json.dumps({"needs_you": [
        {"id": "A-2026-0001", "kind": "approval", "project": "job-search", "title": "an approval file"},
        {"id": "job-search", "kind": "feed", "project": "job-search", "title": "Mail review: 3 message(s) need your call",
         "why": "a label needs your decision", "path": "projects/job-search/DASHBOARD.md"},
        {"id": "home", "kind": "feed", "project": "apartment-search", "title": "Tour on Friday", "why": "house"}]}),
        encoding="utf-8")
    runs = os_root / "logs" / "runs" / "js-hunt-review"
    runs.mkdir(parents=True)
    (runs / "2026-10-06.md").write_text(RECEIPT, encoding="utf-8")
    (root / "state" / "recruiter").mkdir(parents=True, exist_ok=True)
    (root / "state" / "recruiter" / "owner-setup-list.md").write_text(SETUP, encoding="utf-8")


def test_sweep_lists_every_place_the_owners_work_waits(root, os_root):
    seed(root, os_root)
    res = sweep.build(root, os_root, NOW, "2026-10-06", root / "state" / "recruiter")
    g = res["groups"]
    assert [r["title"] for r in g["needs_you"]] == ["Mail review: 3 message(s) need your call"]   # approvals and
    flags = g["routine_flags"]                                                                    # other projects out
    assert [(r["routine"], r["run"]) for r in flags] == [("js-hunt-review", "2026-10-06T07:50:31-07:00")]
    assert "new finds 39" in flags[0]["detail"]
    assert g["setup"][0]["open"] == ["A1", "A2", "D7"] and g["setup"][0]["first"] == ["A1", "A2"]
    md = sweep.render(res)
    assert "### HOME needs you, job-search (1)" in md and f"### Routine runs that need {sweep.OWNER_LABEL} (1)" in md
    assert "3 open: A1, A2, D7" in md


def test_needs_you_key_survives_a_changing_count(root, os_root):
    seed(root, os_root)
    a = sweep.needs_you(os_root)[0]["key"]
    home = json.loads((os_root / "HOME.json").read_text())
    home["needs_you"][1]["title"] = "Mail review: 4 message(s) need your call"
    (os_root / "HOME.json").write_text(json.dumps(home), encoding="utf-8")
    assert sweep.needs_you(os_root)[0]["key"] == a


def test_missing_sources_say_no_data_never_zero(root, os_root):
    res = sweep.build(root, os_root, NOW, "2026-10-06", root / "state" / "recruiter")
    assert res["counts"]["needs_you"] is None and res["groups"]["setup"] is None
    assert f"Also waiting on {sweep.OWNER_LABEL}: HOME needs-you no data yet" in "\n".join(sweep.summary(res))


def test_cards_put_the_whole_human_queue_in_tier_one(root, os_root):
    seed(root, os_root)
    res = sweep.build(root, os_root, NOW, "2026-10-06", root / "state" / "recruiter")
    out = cards.build(root, res)
    mine = {c["kind"]: c for c in out["cards"] if c["kind"] in ("needs_you", "routine_flag", "setup")}
    assert set(mine) == {"needs_you", "routine_flag", "setup"}
    assert all(c["tier"] == 1 and c["action"] == "decide" and c["effort"] == "E1" for c in mine.values())
    assert mine["setup"]["title"].startswith("[setup list] 3 open: A1, A2, D7")
    assert "first A1, A2" in mine["setup"]["why"][0]
