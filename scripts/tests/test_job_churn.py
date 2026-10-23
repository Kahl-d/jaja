"""SETUP-FINAL A5 (R-SF-3): a board run rewrote about 6,340 job files every time (last_seen to the second,
freshness_hours recounted, hunt_scored_at restamped) though nothing in them changed. A job file is now written only
when something a reader uses changes; the ranking inputs (posted_at, hunt_score) are untouched."""
from __future__ import annotations

from datetime import date, timedelta

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW

import fetch_ats as fa
import hunt_score as hs


def _rec(jid="acme-1", posted="2026-09-20T10:00:00Z", now=NOW):
    rec = fa.make_record({"name": "Acme", "slug": "acme"}, title="AI Engineer", source="ashby",
                         source_url=f"https://jobs.ashbyhq.com/acme/{jid}", apply_url=None, ats_vendor="ashby",
                         posted_at=posted, location="San Francisco", remote="onsite", comp=(None, None, None),
                         employment_type=None, jd_text="We build agents. " * 30, now=now)
    rec["id"] = jid
    return rec


def test_a_resighting_the_same_day_writes_nothing(tmp_path):
    fa.upsert_jobs([_rec()], tmp_path, NOW)
    path = tmp_path / "jobs" / "acme-1.md"
    before, mtime = path.read_text(encoding="utf-8"), path.stat().st_mtime_ns
    fa.upsert_jobs([_rec()], tmp_path, NOW + timedelta(hours=3))
    assert path.read_text(encoding="utf-8") == before
    assert path.stat().st_mtime_ns == mtime


def test_a_resighting_on_a_new_day_moves_last_seen_and_nothing_else(tmp_path):
    fa.upsert_jobs([_rec()], tmp_path, NOW)
    first = fa.load_jobs(tmp_path)["acme-1"]
    fa.upsert_jobs([_rec()], tmp_path, NOW + timedelta(days=1))
    later = fa.load_jobs(tmp_path)["acme-1"]
    assert later["last_seen"] == fa.iso_utc(NOW + timedelta(days=1))
    assert later["freshness_hours"] == first["freshness_hours"]             # posting to first sight, kept
    assert later["posted_at"] == first["posted_at"] and later["first_seen"] == first["first_seen"]


def test_freshness_is_recounted_from_first_sight_when_the_posting_date_changes(tmp_path):
    fa.upsert_jobs([_rec(posted="2026-09-20T13:00:00Z")], tmp_path, NOW)                 # first seen 09-24 13:00
    fa.upsert_jobs([_rec(posted="2026-09-23T13:00:00Z")], tmp_path, NOW + timedelta(days=2))
    assert fa.load_jobs(tmp_path)["acme-1"]["freshness_hours"] == 24


def test_save_job_leaves_an_identical_record_alone_and_writes_a_changed_one(tmp_path):
    fa.upsert_jobs([_rec()], tmp_path, NOW)
    path = tmp_path / "jobs" / "acme-1.md"
    rec = fa.load_jobs(tmp_path)["acme-1"]
    mtime = path.stat().st_mtime_ns
    fa.save_job(tmp_path, rec)
    assert path.stat().st_mtime_ns == mtime
    rec["tags"] = ["new-tag"]
    fa.save_job(tmp_path, rec)
    assert "new-tag" in path.read_text(encoding="utf-8")


def test_an_unchanged_hunt_score_keeps_the_day_it_was_set(monkeypatch):
    rec = {"id": "x", "status": "discovered", "hunt_score": 80, "hunt_reasons": ["r"], "hunt_scored_at": date(2026, 10, 1)}
    monkeypatch.setattr(hs, "score", lambda r, ctx: {"hunt_score": 80, "hunt_reasons": ["r"], "components": {}})
    hs.apply_scores([rec], {"today": date(2026, 10, 3)})
    assert rec["hunt_scored_at"] == date(2026, 10, 1)
    monkeypatch.setattr(hs, "score", lambda r, ctx: {"hunt_score": 85, "hunt_reasons": ["r"], "components": {}})
    hs.apply_scores([rec], {"today": date(2026, 10, 3)})
    assert rec["hunt_scored_at"] == date(2026, 10, 3) and rec["hunt_score"] == 85


def test_a_real_resighting_built_by_a_later_run_writes_nothing_the_same_day(tmp_path):
    """Review CS2 #1: the fetcher builds each record with that run's clock, so its freshness_hours is newer; the
    re-sighting must not copy it (the first test re-sighted a record built at the same instant and could not see it)."""
    fa.upsert_jobs([_rec(now=NOW)], tmp_path, NOW)
    path = tmp_path / "jobs" / "acme-1.md"
    before, mtime = path.read_text(encoding="utf-8"), path.stat().st_mtime_ns
    later = NOW + timedelta(hours=3)
    fa.upsert_jobs([_rec(now=later)], tmp_path, later)
    assert path.read_text(encoding="utf-8") == before and path.stat().st_mtime_ns == mtime
