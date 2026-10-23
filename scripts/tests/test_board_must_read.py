"""W5 board speed (2026-10-06): dream and one-request API boards are never deferred (pipeline.must_read, fetch_boards
must_read), and the shared API hosts take a few boards at once (fetch_ats.HOST_SLOTS) with the request pace unchanged."""
from __future__ import annotations

import inspect
import threading
import time
from datetime import datetime, timezone

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; no-network guard)

import fetch_ats as fa  # noqa: E402
import pipeline as pl  # noqa: E402

NOW = datetime(2026, 10, 6, 14, tzinfo=timezone.utc)


def co(slug, vendor="greenhouse", tier="good"):
    return {"slug": slug, "ats_vendor": vendor, "tier": tier, "board_token": slug}


class Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def test_must_read_is_dream_or_a_one_request_api_board():
    assert pl.must_read(co("a", "workday", "dream")) and pl.must_read(co("b", "sitemap", "dream"))
    assert pl.must_read(co("c", "ashby")) and pl.must_read(co("d", "greenhouse", "fallback"))
    assert pl.must_read(co("e", "lever", "strong"))
    assert not pl.must_read(co("f", "workday")) and not pl.must_read(co("g", "sitemap", "strong"))
    assert not pl.must_read(co("h", "smartrecruiters"))           # an API host, but many requests a board


def test_fetch_order_puts_the_must_read_boards_first():
    order = pl.fetch_order([co("w", "workday", "strong"), co("x", "ashby"), co("y", "workday", "dream")], {}, NOW)
    assert [c["slug"] for c in order][:2] == ["y", "x"] and order[-1]["slug"] == "w"


def _fetch(seen):
    def fetch(company, http, now, tf, **kw):
        seen.append((company["slug"], fa.time_left()))
        return fa.BoardResult(company["slug"], company["ats_vendor"])
    return fetch


def test_past_the_deadline_must_read_boards_still_read_with_their_own_grace():
    clock = Clock(100.0)                                  # the run deadline (50) has passed before any board starts
    for workers in (1, 4):
        seen: list = []
        reads = fa.fetch_boards([co("wd", "workday"), co("gh"), co("dr", "workday", "dream")], None, NOW, None,
                                workers=workers, clock=clock, deadline=50.0, fetch=_fetch(seen),
                                must_read=pl.must_read)
        got = {r.company["slug"]: r.result is not None for r in reads}
        assert got == {"wd": False, "gh": True, "dr": True}, workers
        assert all(left == fa.MUST_READ_GRACE_S for _, left in seen)


def test_without_must_read_everything_waits_as_before():
    reads = fa.fetch_boards([co("gh")], None, NOW, None, workers=1, clock=Clock(100.0), deadline=50.0,
                            fetch=_fetch([]))
    assert reads[0].result is None


def _concurrency(host_slots):
    lock, now_on, peak = threading.Lock(), [0], [0]

    def fetch(company, http, now, tf, **kw):
        with lock:
            now_on[0] += 1
            peak[0] = max(peak[0], now_on[0])
        time.sleep(0.05)
        with lock:
            now_on[0] -= 1
        return fa.BoardResult(company["slug"], company["ats_vendor"])
    reads = fa.fetch_boards([co(f"a{i}", "ashby") for i in range(6)], None, NOW, None, workers=6, fetch=fetch,
                            host_slots=host_slots)
    assert [r.company["slug"] for r in reads] == [f"a{i}" for i in range(6)] and all(r.result for r in reads)
    return peak[0]


def test_a_shared_api_host_takes_its_slots_and_no_more():
    assert _concurrency(None) == 1                       # one board per host, as before
    assert _concurrency(fa.HOST_SLOTS) == fa.HOST_SLOTS["api.ashbyhq.com"] == 3


def test_the_pipeline_passes_both():
    src = inspect.getsource(pl)
    assert "must_read=must_read if budget_min else None" in src and "host_slots=fetch_ats.HOST_SLOTS" in src
