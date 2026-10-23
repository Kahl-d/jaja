"""remote_feeds.py: Remotive and RemoteOK public APIs -> job records, with their link-back terms and Remotive's
4-calls-a-day cap."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from disc_harness import *  # noqa: F401,F403
from disc_harness import FakeHttp, load_fixture

import fetch_ats as fa  # noqa: E402
import remote_feeds as rf  # noqa: E402

NOW = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)
TF = fa.TitleFilter([fa.title_pattern("ai engineer"), fa.title_pattern("machine learning engineer")], [])


def _http():
    return FakeHttp({("GET", rf.REMOTEOK): load_fixture("remoteok_api.json")["rows"],
                     ("GET", rf.REMOTIVE): load_fixture("remotive_jobs.json")})


def test_remoteok_keeps_us_or_open_fresh_title_matches_with_attribution(tmp_path):
    records, info = rf.collect(_http(), NOW, tmp_path, feeds=["remoteok"], title_filter=TF)
    assert [r["title"] for r in records] == ["AI Engineer, Agents"]          # Europe-only dropped, sales off-title
    rec = records[0]
    assert rec["source"] == "remoteok" and rec["platform"] == "remoteok"
    assert rec["source_url"] == "https://remoteOK.com/remote-jobs/remote-ai-engineer-agents-acme-ai-2000001"
    assert rec["comp_min"] == 170000 and rec["remote"] == "remote" and "Remote OK" in rec["_body"]
    assert info["remoteok"]["kept"] == 1


def test_remotive_keeps_usa_roles_and_names_remotive(tmp_path):
    records, info = rf.collect(_http(), NOW, tmp_path, feeds=["remotive"], title_filter=TF)
    assert [r["title"] for r in records] == ["Senior AI Engineer"]
    rec = records[0]
    assert rec["source"] == "remotive" and rec["location"] == "USA" and rec["comp_min"] == 180000
    assert "Remotive" in rec["_body"] and rec["source_url"].startswith("https://remotive.com/remote-jobs/")


def test_remotive_is_called_at_most_four_times_a_day(tmp_path):
    for _ in range(4):
        rf.collect(_http(), NOW, tmp_path, feeds=["remotive"], title_filter=TF)
    http = _http()
    records, info = rf.collect(http, NOW, tmp_path, feeds=["remotive"], title_filter=TF)
    assert records == [] and info["remotive"]["skipped"].startswith("daily cap")
    assert not any(c[1] == rf.REMOTIVE for c in http.calls)
    ledger = json.loads((tmp_path / "state" / "remote_feeds.json").read_text())
    assert ledger["remotive"]["2026-10-01"] == 4
