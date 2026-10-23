"""Board speed: fetch_boards reads boards of different hosts at once, never two boards of one host
at once, keeps the many-request vendors off the last threads, starts nothing after the run deadline and returns its
reads in the input order; Http's per-host interval holds across threads; the pipeline reads every board with workers,
orders the fetch by tier and writes the per-vendor seconds into the receipt. No network: fake readers and sessions."""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW, FakeHttp, ats_routes, hn_routes

import fetch_ats as fa  # noqa: E402
import pipeline as pl  # noqa: E402


def board(slug, vendor="ashby", token=None, tier=None):
    c = {"name": slug, "slug": slug, "ats_vendor": vendor, "board_token": token or slug}
    if tier:
        c["tier"] = tier
    return c


def done(slug, vendor="ashby", cut=False):
    return fa.BoardResult(slug, vendor, complete=not cut, cut=cut)


# ------------------------------------------------------------------------------ hosts
def test_each_vendor_maps_to_the_host_its_requests_go_to():
    assert fa.board_host(board("modelco")) == "api.ashbyhq.com"
    assert fa.board_host(board("globex", "greenhouse")) == "boards-api.greenhouse.io"
    assert fa.board_host(board("chipco", "workday", "chipco.wd5.myworkdayjobs.com/chipco/CHIPCOExternalCareerSite")) \
        == "chipco.wd5.myworkdayjobs.com"
    assert fa.board_host(board("alpha", "oracle", "careersearch.example.test|alpha")) \
        == fa.board_host(board("beta", "oracle", "careersearch.example.test|BETA")) == "careersearch.example.test"
    assert fa.board_host(board("hooli", "sitemap", "jobs.hooli.example.test/sitemap/sitemap-jobs-en-us.xml")) == "jobs.hooli.example.test"
    assert fa.board_host(board("initech", "jibe", "careers.initech.example.test")) == "careers.initech.example.test"
    assert fa.board_host(board("x", "pinpoint", "acme")) == "acme.pinpointhq.com"
    assert fa.board_host(board("t", "teamtailor", "acme")) == "acme.teamtailor.com"
    assert fa.board_host(board("fabrikam", "other")) == "other:fabrikam"
    assert fa.board_host(board("bad", "workday", "not a board")) == "workday:bad"


# ------------------------------------------------------------------------------ the scheduler
def test_boards_on_different_hosts_are_read_at_once():
    started = {s: threading.Event() for s in ("a", "b")}
    waited = {}

    def fetch(c, *_a, **_k):
        me, other = c["slug"], "b" if c["slug"] == "a" else "a"
        started[me].set()
        waited[me] = started[other].wait(timeout=3)          # one board at a time would time out here
        return done(me, c["ats_vendor"])
    reads = fa.fetch_boards([board("a", "ashby"), board("b", "greenhouse")], None, NOW, None, workers=4, fetch=fetch)
    assert waited == {"a": True, "b": True} and [r.result.company_slug for r in reads] == ["a", "b"]


def test_never_two_boards_of_one_host_at_once_and_the_reads_keep_the_input_order():
    lock, inflight, peak = threading.Lock(), {}, {"host": 0, "all": 0}
    companies = [board(f"ash{i}") for i in range(6)] + [board(f"gh{i}", "greenhouse") for i in range(4)] \
        + [board(f"wd{i}", "workday", f"t{i}.wd1.myworkdayjobs.com/t{i}/site") for i in range(3)]

    def fetch(c, *_a, **_k):
        host = fa.board_host(c)
        with lock:
            inflight[host] = inflight.get(host, 0) + 1
            peak["host"] = max(peak["host"], inflight[host])
            peak["all"] = max(peak["all"], sum(inflight.values()))
        time.sleep(0.03 if c["slug"] != "ash0" else 0.1)    # the first board finishes last
        with lock:
            inflight[host] -= 1
        return done(c["slug"], c["ats_vendor"])
    reads = fa.fetch_boards(companies, None, NOW, None, workers=6, fetch=fetch)
    assert peak["host"] == 1 and peak["all"] >= 2
    assert [r.result.company_slug for r in reads] == [c["slug"] for c in companies]


def test_the_many_request_boards_leave_threads_for_the_one_request_boards():
    release, lock = threading.Event(), threading.Lock()
    heavy_now, peak, order = [0], [0], []
    companies = [board(f"wd{i}", "workday", f"t{i}.wd1.myworkdayjobs.com/t{i}/site") for i in range(6)] + [board("modelco")]

    def fetch(c, *_a, **_k):
        if c["ats_vendor"] == "workday":
            with lock:
                heavy_now[0] += 1
                peak[0] = max(peak[0], heavy_now[0])
            release.wait(timeout=3)
            with lock:
                heavy_now[0] -= 1
        else:
            order.append(c["slug"])
            release.set()                                      # the API board ran while the heavy ones were busy
        return done(c["slug"], c["ats_vendor"])
    fa.fetch_boards(companies, None, NOW, None, workers=4, fetch=fetch)
    assert order == ["modelco"] and peak[0] == 4 - fa.HEAVY_RESERVE


def test_no_board_starts_once_the_run_deadline_has_passed():
    t = {"now": 0.0}

    def fetch(c, *_a, **_k):
        t["now"] = 100.0                                       # the deadline passes inside the first board
        return done(c["slug"])
    companies = [board(s) for s in ("a", "b", "c")]           # one host: the others wait for "a"
    reads = fa.fetch_boards(companies, None, NOW, None, workers=4, clock=lambda: t["now"], deadline=50.0, fetch=fetch)
    assert [r.result is not None for r in reads] == [True, False, False]


def test_with_workers_every_board_fits_a_deadline_one_at_a_time_misses():
    # scaled clock: 1 real ms = 0.1 s; four many-request boards of 150 ms (15 s each) and ten API boards of 5 ms
    companies = [board(f"wd{i}", "workday", f"t{i}.wd1.myworkdayjobs.com/t{i}/site") for i in range(4)] \
        + [board(f"ash{i}") for i in range(10)]

    def fetch(c, *_a, **_k):
        time.sleep(0.15 if c["ats_vendor"] == "workday" else 0.005)
        return done(c["slug"], c["ats_vendor"])

    def run(workers):
        t0 = time.monotonic()
        clock = lambda: (time.monotonic() - t0) * 100       # noqa: E731
        reads = fa.fetch_boards(companies, None, NOW, None, workers=workers, clock=clock, deadline=36.0, fetch=fetch)
        return [r.company["slug"] for r in reads if r.result is None]
    assert run(fa.FETCH_WORKERS) == []
    assert run(1)                                              # the old way defers boards


def test_a_bug_in_a_reader_is_raised_not_swallowed():
    def fetch(c, *_a, **_k):
        raise RuntimeError("bug")
    try:
        fa.fetch_boards([board("a"), board("b", "greenhouse")], None, NOW, None, workers=3, fetch=fetch)
    except RuntimeError as exc:
        assert str(exc) == "bug"
    else:
        raise AssertionError("the reader's bug was swallowed")


def test_a_board_deadline_holds_for_its_own_thread_only():
    fa.set_deadline(lambda: 10.0, None)
    seen = {}

    def other():
        fa.set_deadline(lambda: 10.0, 5.0, board=True)
        seen["other"] = fa.past_deadline()
        fa.clear_board_deadline()
        seen["cleared"] = fa.past_deadline()
    try:
        th = threading.Thread(target=other)
        th.start()
        th.join()
        assert seen == {"other": True, "cleared": False} and not fa.past_deadline()
    finally:
        fa.set_deadline(None, None)


# ------------------------------------------------------------------------------ Http across threads
class _Resp:
    status_code, headers = 200, {}

    def json(self):
        return {}


class _Session:
    def __init__(self):
        self.headers, self.sent, self.lock = {}, [], threading.Lock()

    def request(self, method, url, **kw):
        with self.lock:
            self.sent.append(time.monotonic())
        return _Resp()


def test_the_per_host_interval_holds_when_threads_share_one_http():
    session = _Session()
    http = fa.Http(session=session, min_interval=0.2, retries=0)
    threads = [threading.Thread(target=http.get_json, args=("https://api.ashbyhq.com/x",)) for _ in range(3)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    gaps = [b - a for a, b in zip(sorted(session.sent), sorted(session.sent)[1:])]
    assert len(session.sent) == 3 and min(gaps) >= 0.19


def test_a_board_thread_gets_its_own_session_with_the_same_headers():
    http = fa.Http(min_interval=0)
    got = {}
    th = threading.Thread(target=lambda: got.setdefault("s", http._session()))
    th.start()
    th.join()
    assert got["s"] is not http.session and got["s"].headers["User-Agent"] == fa.USER_AGENT
    assert http._session() is http.session                    # the main thread keeps the one it had


# ------------------------------------------------------------------------------ the pipeline
def _http():
    return FakeHttp({**ats_routes(), **hn_routes()})


def test_the_pipeline_reads_the_same_with_workers_as_one_at_a_time(root_with_targets, os_root):
    one = pl.run_pipeline(root_with_targets, os_root, NOW, http=_http(), stages=["fetch"], dry_run=True, env={},
                          workday_page_size=3, budget_min=20, workers=1)
    many = pl.run_pipeline(root_with_targets, os_root, NOW, http=_http(), stages=["fetch"], dry_run=True, env={},
                           workday_page_size=3, budget_min=20, workers=fa.FETCH_WORKERS)
    a, b = one.receipt["stages"]["fetch"], many.receipt["stages"]["fetch"]
    strip = ("vendor_s", "workers")
    assert {k: v for k, v in a.items() if k not in strip} == {k: v for k, v in b.items() if k not in strip}
    assert sorted(one.workset) == sorted(many.workset) and many.receipt["deferred_boards"] == []
    assert b["workers"] == fa.FETCH_WORKERS and set(b["vendor_s"]) == set(b["by_vendor"])
    assert all({"s", "max_s", "slowest"} <= set(row) for row in b["vendor_s"].values())
    assert many.receipt["notes"] == one.receipt["notes"]                 # notes in the board order, not finish order


def test_the_routine_note_names_the_seconds_by_vendor(root_with_targets, os_root):
    res = pl.run_pipeline(root_with_targets, os_root, NOW, http=_http(), stages=["fetch"], dry_run=True, env={},
                          workday_page_size=3, workers=4)
    assert re.search(r"fetch \d+ s with 4 workers; board seconds by vendor: \w+ \d+ \(max [\w-]+ \d+\)",
                     res.receipt["routine"]["note"])


def test_the_fetch_order_puts_dream_first_then_strong_good_and_fallback():
    polled = [board("f", tier="fallback"), board("g", tier="good"), board("n"), board("s", tier="strong"),
              board("d", tier="dream"), board("dm", "sitemap", "x.example.com", tier="dream")]
    assert [c["slug"] for c in pl.fetch_order(polled, {})] == ["d", "dm", "s", "g", "n", "f"]


def test_a_board_waiting_a_day_still_goes_before_the_tiers():
    now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    polled = [board("d", tier="dream"), board("f", tier="fallback")]
    cursor = {"last_read": {"f": "2026-10-04T06:00:00Z", "d": "2026-10-05T09:00:00Z"}}
    assert [c["slug"] for c in pl.fetch_order(polled, cursor, now)] == ["f", "d"]


def test_the_cli_reads_boards_at_once_by_default(root_with_targets, os_root, monkeypatch):
    seen = {}
    real = pl.run_pipeline
    monkeypatch.setattr(pl, "run_pipeline", lambda *a, **k: (seen.update(k), real(*a, **k))[1])
    monkeypatch.setattr(pl, "_default_http", _http)
    pl.main(["--root", str(root_with_targets), "--os-root", str(os_root), "--workday-page-size", "3",
             "--stages", "fetch", "--dry-run"], now=NOW, env={})
    assert seen["workers"] == fa.FETCH_WORKERS == 8
