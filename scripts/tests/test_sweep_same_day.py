"""A second sweep the same day adds to the day's sweep file instead of replacing it (A3 audit 2026-10-06: a second
--write cut the morning listing from 276 KB to 55 KB, and the cards built from it lost the morning items)."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone

from disc_harness import *  # noqa: F401,F403
from disc_harness import SCRIPTS

spec = importlib.util.spec_from_file_location("js_sweep_same_day", SCRIPTS / "recruiter_sweep.py")
sweep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sweep)


def res(root, os_root, now: str, since: str, listings: list[str], approvals: list[str]) -> dict:
    """A real build of an empty tree, with the listings and approvals put in by hand."""
    when = datetime.fromisoformat(now.replace("Z", "+00:00"))
    r = sweep.build(root, os_root, when, since, root / "state" / "recruiter")
    r["groups"]["listings"] = [{"key": f"job:{k}", "group": "listings", "kind": "listing", "title": k, "job": k,
                                "source": "greenhouse", "company": "Acme"} for k in listings]
    r["groups"]["approvals"] = [{"key": f"approval:{a}", "group": "due", "kind": "approval", "id": a}
                                for a in approvals]
    r["counts"] = {k: (None if v is None else len(v)) for k, v in r["groups"].items()}
    return r


def test_a_second_sweep_keeps_the_morning_items_and_adds_the_new(root, os_root):
    out = root / "state" / "recruiter"
    out.mkdir(parents=True, exist_ok=True)
    t1 = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)
    sweep.write(res(root, os_root, "2026-10-06T15:00:00Z", "2026-10-05T15:00:00Z", ["a", "b"], ["A-1", "A-2"]), out, t1)
    t2 = datetime(2026, 10, 6, 21, 0, tzinfo=timezone.utc)
    sweep.write(res(root, os_root, "2026-10-06T21:00:00Z", "2026-10-06T15:00:00Z", ["b", "c"], ["A-2"]), out, t2)
    day = json.loads((out / "sweep-2026-10-06.json").read_text())
    assert [r["key"] for r in day["groups"]["listings"]] == ["job:a", "job:b", "job:c"]
    assert [r["id"] for r in day["groups"]["approvals"]] == ["A-2"]          # due items are state: the latest wins
    assert day["since"].startswith("2026-10-05T15:00:00") and len(day["sweeps"]) == 2
    assert day["counts"]["listings"] == 3


def test_a_new_day_starts_a_new_file(root, os_root):
    out = root / "state" / "recruiter"
    out.mkdir(parents=True, exist_ok=True)
    earlier = res(root, os_root, "2026-10-05T15:00:00Z", "2026-10-04T15:00:00Z", ["old"], [])
    earlier["date"] = "2026-10-05"
    (out / "sweep-2026-10-06.json").write_text(json.dumps(earlier))       # a stray file of another day: not merged
    sweep.write(res(root, os_root, "2026-10-06T15:00:00Z", "2026-10-05T15:00:00Z", ["a"], []), out,
                datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc))
    day = json.loads((out / "sweep-2026-10-06.json").read_text())
    assert [r["key"] for r in day["groups"]["listings"]] == ["job:a"] and "sweeps" not in day
