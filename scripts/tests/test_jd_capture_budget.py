"""jd_capture runs inside a budget. A capture that runs on without a limit can be killed with nothing reported; the finds
are walked best first, so past the budget the rest wait for the next run and the run still ends with its true counts."""
from __future__ import annotations

import json
from datetime import date

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import FakeHttp

import fetch_ats as fa
import jd_capture as jc_
from test_jd_capture import LONG, _job, world  # noqa: F401  (the same world fixture)

TODAY = date(2026, 10, 3)


class Ticks:
    """A clock that moves 100 s every time it is read."""
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 100.0
        return self.t


def _three_with_twins(root):
    for jid, score in (("a", 90), ("b", 80), ("c", 75)):
        title = f"Engineer {jid.upper()}"
        _job(root, jid, title=title, score=score)
        _job(root, f"{jid}-board", title=title, source="ashby", url=f"https://jobs.ashbyhq.com/northwind/{jid}", jd=LONG,
             score=10)


def test_past_its_budget_capture_leaves_the_rest_for_the_next_run_best_first(world):
    _three_with_twins(world)
    out = jc_.capture(world, today=TODAY, http=FakeHttp({}), budget_s=150, clock=Ticks())
    assert [c["id"] for c in out["captured"]] == ["a"]
    assert out["deferred"] == ["b", "c"]
    assert out["counts"]["deferred"] == 2 and out["counts"]["captured"] == 1
    saved = json.loads((world / jc_.STATE / f"capture-{TODAY.isoformat()}.json").read_text(encoding="utf-8"))
    assert saved["deferred"] == ["b", "c"]


def test_without_a_budget_every_find_is_tried_and_no_deadline_is_left_behind(world):
    _three_with_twins(world)
    out = jc_.capture(world, today=TODAY, http=FakeHttp({}))
    assert sorted(c["id"] for c in out["captured"]) == ["a", "b", "c"] and out["deferred"] == []
    assert fa.time_left() is None


def test_the_budget_is_cleared_after_the_run(world):
    _three_with_twins(world)
    jc_.capture(world, today=TODAY, http=FakeHttp({}), budget_s=150, clock=Ticks())
    assert fa.time_left() is None


def test_the_cli_default_budget_is_twelve_minutes():
    assert jc_.BUDGET_S == 12 * 60


def test_a_find_cut_mid_way_is_deferred_not_sent_to_the_read_list(world):
    """A find in flight when the deadline passed must be deferred, not sent to the daily read list."""
    class All404:
        def __getattr__(self, name):
            def answer(url, *a, **k):
                raise fa.FetchError(f"GET {url}: HTTP 404", 404)
            return answer

    _job(world, "z", title="Engineer Z", score=90, company="Nobody Inc", slug="nobody")
    out = jc_.capture(world, today=TODAY, http=All404(), budget_s=150, clock=Ticks())
    assert out["deferred"] == ["z"] and out["read_list"] == []
