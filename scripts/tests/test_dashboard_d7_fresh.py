"""Freshness: "fresh" and "posted this week" on the dashboard use posted_at, never first_seen.
A board's first scan stamps first_seen on every open role it finds, so first_seen is a baseline, not a posting date.
A record with no posted_at is never fresh and is counted as "no posting date", not guessed. Fixture tree only."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from layout import JS  # noqa: E402
for p in (str(JS / "scripts"), str(JS / "dashboard")):
    if p not in sys.path:
        sys.path.insert(0, p)

import dash_api as da  # noqa: E402
from js_common import fm  # noqa: E402

NOW = datetime(2026, 10, 5, 19, 0, tzinfo=timezone.utc)      # 12:00 PDT, department day 2026-10-05


def _job(js: Path, jid: str, **meta) -> None:
    base = {"id": jid, "company": "Acme", "company_slug": "acme", "title": "AI Engineer", "status": "fit_checked"}
    base.update(meta)
    path = js / "jobs" / f"{jid}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fm.dump(base, "# job\n"), encoding="utf-8")


def _tree(tmp_path):
    os_root = tmp_path / "workspace"
    js = os_root / "domains" / "career" / "projects" / "job-search"
    # posted two days ago, first seen today: fresh
    _job(js, "new-role", posted_at="2026-10-03T17:00:00Z", first_seen="2026-10-05T08:00:00Z")
    # posted three weeks ago, first seen today by a board's first scan: NOT fresh
    _job(js, "baseline-role", posted_at="2026-09-14", first_seen="2026-10-05T08:00:00Z")
    # no posting date, first seen today: not fresh, counted as no posting date
    _job(js, "undated-role", first_seen="2026-10-05T08:00:00Z")
    # posted last week, first seen this week
    _job(js, "last-week-role", posted_at="2026-09-25", first_seen="2026-10-04T08:00:00Z")
    return os_root, js


def test_fresh_is_posted_within_72_hours_never_first_seen(tmp_path):
    os_root, js = _tree(tmp_path)
    s = da.build(js, os_root, NOW)
    rows = {r["id"]: r for r in s["_jobs"]}
    assert rows["new-role"]["fresh"] is True
    assert rows["baseline-role"]["fresh"] is False          # first seen today, posted 2026-09-14
    assert rows["undated-role"]["fresh"] is False           # missing is not fresh
    assert s["jobs_summary"]["fresh_72h"] == 1
    assert s["jobs_summary"]["no_posted_date"] == 1


def test_posted_this_week_counts_posting_dates(tmp_path):
    os_root, js = _tree(tmp_path)
    wk = {r["metric"]: r for r in da.build(js, os_root, NOW)["overview"]["weeks"]["rows"]}
    assert (wk["discovered"]["this"], wk["discovered"]["last"]) == (1, 1)   # new-role this week, last-week-role before


def test_recent_sort_is_by_posting_date(tmp_path):
    os_root, js = _tree(tmp_path)
    out = da.query_jobs(da.build(js, os_root, NOW)["_jobs"], {"sort": "recent"})
    assert [r["id"] for r in out["rows"]] == ["new-role", "last-week-role", "baseline-role", "undated-role"]
