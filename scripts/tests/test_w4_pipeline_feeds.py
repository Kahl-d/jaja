"""W4 fixes 1 and 2: js-hunt-boards reads the feeds once a department day (--daily, from sources.yaml last_run), the
fetch order puts the API boards the last run deferred before the tiers (every board read at least every second
run), and a lock skip with --json prints JSON (an empty stdout reads as a crash). No network: stubs only."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import FakeHttp

import fit_gates  # noqa: E402,F401
import pipeline as pl  # noqa: E402

NOW = datetime(2026, 10, 6, 14, 10, tzinfo=timezone.utc)          # 07:10 PDT, the first scheduled run of the day
ROUTINE = Path(pl.__file__).resolve().parents[1] / "routines" / "js-hunt-boards.yaml"


def _root(tmp_path, last_run):
    root = tmp_path / "js"
    root.mkdir()
    (root / "sources.yaml").write_text("sources:\n  - id: himalayas\n    kind: aggregator-api\n    enabled: true\n"
                                       f"    last_run: '{last_run}'\n")
    return root


def _stub(monkeypatch):
    calls = []

    def collect(http, now, **kw):
        calls.append(now)
        return [], {"queries": 1, "requests": 1, "results": 0, "kept": 0, "notes": []}
    monkeypatch.setattr(pl.himalayas, "collect", collect)
    return calls


def test_a_daily_feed_runs_on_the_first_run_of_the_day(tmp_path, monkeypatch):
    calls = _stub(monkeypatch)
    root = _root(tmp_path, "2026-10-05T14:10:00Z")                            # yesterday (Pacific)
    res = pl.run_pipeline(root, tmp_path, now=NOW, http=FakeHttp(), stages=["himalayas"], dry_run=True,
                          daily=["himalayas"])
    assert len(calls) == 1 and "skipped" not in res.receipt["stages"]["himalayas"]


def test_a_daily_feed_is_skipped_once_it_ran_today(tmp_path, monkeypatch):
    calls = _stub(monkeypatch)
    root = _root(tmp_path, "2026-10-06T14:12:00Z")                            # this morning
    later = NOW.replace(hour=17)
    res = pl.run_pipeline(root, tmp_path, now=later, http=FakeHttp(), stages=["himalayas"], dry_run=True,
                          daily=["himalayas"])
    assert calls == [] and "already ran today" in res.receipt["stages"]["himalayas"]["skipped"]


def test_without_daily_the_feed_runs_every_time(tmp_path, monkeypatch):
    calls = _stub(monkeypatch)
    root = _root(tmp_path, "2026-10-06T14:12:00Z")
    pl.run_pipeline(root, tmp_path, now=NOW.replace(hour=17), http=FakeHttp(), stages=["himalayas"], dry_run=True)
    assert len(calls) == 1


def test_the_hunt_routine_names_the_feeds_once_a_day():
    action = re.search(r'^action: "script:\S+ (.*?)"', ROUTINE.read_text(), re.M).group(1)
    stages = re.search(r"--stages (\S+)", action).group(1).split(",")
    daily = re.search(r"--daily (\S+)", action).group(1).split(",")
    assert {"fetch", "hn", "himalayas", "aidevjobs", "adzuna"} <= set(stages)
    assert set(daily) == set(pl.DAILY_SOURCES) and set(daily) <= set(stages)
    assert not set(stages) - set(pl.STAGES)


def test_daily_takes_feed_stages_only(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        pl.main(["--root", str(tmp_path), "--stages", "fetch", "--daily", "fetch", "--dry-run"])


# ------------------------------------------------------------------------------ fix 2: the fetch order
def _b(slug, tier=None, vendor="ashby"):
    c = {"slug": slug, "ats_vendor": vendor, "board_token": slug}
    if tier:
        c["tier"] = tier
    return c


def test_the_api_boards_deferred_last_run_lead_whatever_their_tier():
    polled = [_b("d1", "dream"), _b("d2", "dream"), _b("f1", "fallback"), _b("g1", "good"),
              _b("crawl", "fallback", "sitemap")]
    cursor = {"deferred": ["g1", "f1", "crawl"]}
    order = [c["slug"] for c in pl.fetch_order(polled, cursor, NOW)]
    assert order[:2] == ["g1", "f1"]                       # the boards left last run first, in their order
    assert order.index("crawl") > order.index("d2")        # a page crawler is not moved up (S-10: crawlers last)


def test_with_a_fetch_share_for_half_the_boards_every_board_is_read_within_two_runs():
    boards = [_b(f"dream{i}", "dream") for i in range(4)] + [_b(f"fb{i}", "fallback") for i in range(6)]
    cursor: dict = {}
    seen: list[set] = []
    for _ in range(2):
        order = pl.fetch_order(boards, cursor, NOW)
        read, left = order[:5], order[5:]                  # the share reads five boards a run
        seen.append({c["slug"] for c in read})
        cursor = {"deferred": [c["slug"] for c in left]}
    assert seen[0] | seen[1] == {c["slug"] for c in boards}


def test_a_lock_skip_with_json_prints_json(tmp_path, capsys):
    import argparse
    args = argparse.Namespace(json=True, routine_receipt=None, root=tmp_path, os_root=tmp_path)
    assert pl._skipped(args, ["fetch"], NOW, "skipped: previous run in progress") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "skipped" and "previous run" in data["skipped"]
