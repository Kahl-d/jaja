"""aidevjobs.py: AI Dev Jobs (aidevboard.com) public API -> job records."""
from __future__ import annotations

from datetime import datetime, timezone

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import FakeHttp, load_fixture

import aidevjobs as adj  # noqa: E402
import fetch_ats as fa  # noqa: E402

NOW = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)
URL = "https://aidevboard.com/api/v1/jobs"


def _http(calls=None):
    page = load_fixture("aidevjobs_jobs.json")
    def serve(params):
        if calls is not None:
            calls.append(params)
        return page
    return FakeHttp({("GET", URL): serve})


def test_full_time_recent_title_matched_jobs_become_records_with_attribution():
    calls = []
    records, info = adj.collect(_http(calls), NOW, queries=["llm,agents"], title_filter=fa.TitleFilter(
        [fa.title_pattern("ai engineer"), fa.title_pattern("ml engineer"), fa.title_pattern("llm engineer")], []))
    assert calls[0] == {"tags": "llm,agents", "type": "full-time", "limit": 50, "page": 1}
    assert [r["title"] for r in records] == ["AI Engineer, Agents"]              # contract dropped (type), old dropped
    rec = records[0]
    expected = {"source": "aidevjobs", "company": "Acme AI", "company_slug": "acme-ai",
                "source_url": "https://aidevboard.com/job/job-0002",
                "apply_url": "https://jobs.ashbyhq.com/acme/role-job-0002",
                "posted_at": "2026-09-30T10:00:00Z", "location": "Remote (US)", "remote": "remote",
                "employment_type": "full-time", "comp_min": 170000, "comp_max": 210000, "comp_currency": "USD"}
    for k, v in expected.items():
        assert rec[k] == v, k
    assert "AI Dev Jobs" in rec["_body"]                                         # the source is named (link back)
    assert info["queries"] == 1 and info["kept"] == 1


def test_pages_stop_when_the_api_says_no_next_page():
    calls = []
    adj.collect(_http(calls), NOW, queries=["llm", "evals"], max_pages=3)
    assert [c["page"] for c in calls] == [1, 1]
