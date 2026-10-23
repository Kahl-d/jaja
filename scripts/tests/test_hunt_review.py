"""hunt_review.py: the daily fresh-roles review, routing each find to an action."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW

import fetch_ats as fa
import hunt_review as hr

TODAY = date(2026, 9, 29)
RUN = datetime(2026, 9, 29, 14, 30, tzinfo=timezone.utc)          # 07:30 PDT


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "js"
    for d in ("jobs", "prospects", "targets", "hub", "state"):
        (root / d).mkdir(parents=True)
    (root / "targets" / "companies.yaml").write_text(
        "companies:\n  - {name: Tiny AI, slug: tiny-ai, tier: strong, segment: startup, ats_vendor: ashby,"
        " board_token: t}\n  - {name: Giant Co, slug: giant-co, tier: dream, segment: giant, ats_vendor: greenhouse,"
        " board_token: g}\n")
    # One queue, hub/NEXT-UP.md; its rows name "Company · Title", some an id
    (root / "hub" / "NEXT-UP.md").write_text(
        "| # | role | fit | why here | chance | posted | state | page | tier |\n|---|---|---|---|---|---|---|---|---|\n"
        "| 2 | Tiny AI · Agent Engineer | 4.5 | agents | sponsors | 2026-09-28 | READY | 09 | focused |\n"
        "| 3 | Giant Co · ML Engineer | 4.1 | ml | ? | 2026-09-28 | JOB (queued-by-id) | none | mass |\n")
    return root


def _job(root, jid, *, score=80, company="tiny-ai", title="AI Engineer", jd="We build agents.", status="fit_checked",
         gate="pass", first_seen=RUN - timedelta(hours=3), posted=RUN - timedelta(hours=5), fit=None, **extra):
    rec = fa.make_record({"name": company.replace("-", " ").title(), "slug": company}, title=title, source="ashby",
                         source_url=f"https://jobs.example.com/{jid}", apply_url=None, ats_vendor="ashby",
                         posted_at=posted.strftime("%Y-%m-%dT%H:%M:%SZ") if posted else None, location="SF",
                         remote="onsite", comp=(None, None, None), employment_type=None, jd_text=jd, now=NOW)
    rec.update({"id": jid, "status": status, "gate": gate, "hunt_score": score, "hunt_reasons": ["title: matches"],
                "first_seen": first_seen.strftime("%Y-%m-%dT%H:%M:%SZ"), "fit_score": fit, **extra})
    fa.save_job(root, rec)
    return rec


def _route(review, jid):
    return next(r for r in review["rows"] if r["id"] == jid)


def test_each_find_gets_its_action(world):
    _job(world, "top-jd", score=85)
    _job(world, "top-nojd", score=84, jd="")
    _job(world, "fit4", score=78, fit=4.3)
    _job(world, "queued-already", score=90, fit=4.5, title="Agent Engineer")
    _job(world, "queued-by-id", score=88, fit=4.1, company="giant-co", title="Research Scientist")
    _job(world, "maybe", score=74, fit=3.7)
    _job(world, "low", score=40)
    _job(world, "via-post", score=80, platform="x", prospect="sam-alba")
    (world / "prospects" / "ana.md").write_text("---\nid: ana\nname: Ana\ncompany_slug: giant-co\nstatus: replied\n---\n")
    _job(world, "warm", score=76, company="giant-co")
    review = hr.build(world, today=TODAY, now=RUN)
    assert _route(review, "top-jd")["action"] == "full report"
    assert _route(review, "top-nojd")["action"] == "capture the JD"
    assert _route(review, "fit4")["action"] == "apply: propose a NEXT-UP row"
    assert _route(review, "queued-already")["action"] == "in NEXT-UP"
    assert _route(review, "queued-by-id")["action"] == "in NEXT-UP"
    assert _route(review, "maybe")["action"] == "maybe list"
    assert "outreach draft" in _route(review, "via-post")["also"]
    assert "referral path" in _route(review, "warm")["also"]
    assert "low" not in {r["id"] for r in review["rows"]} and review["counts"]["below_cut"] == 1


def test_the_window_is_since_the_last_review_and_old_finds_are_left_out(world):
    _job(world, "new", first_seen=RUN - timedelta(hours=2))
    _job(world, "old", first_seen=RUN - timedelta(days=3))
    (world / "state" / "hunt").mkdir(parents=True)
    (world / "state" / "hunt" / "last-review.json").write_text(json.dumps({"at": "2026-09-28T14:30:00Z"}))
    review = hr.build(world, today=TODAY, now=RUN)
    assert [r["id"] for r in review["rows"]] == ["new"] and review["since"] == "2026-09-28T14:30:00Z"


def test_rows_are_ranked_by_score_and_closed_or_applied_jobs_never_appear(world):
    _job(world, "a", score=75)
    _job(world, "b", score=88)
    _job(world, "shut", score=99, status="closed")
    _job(world, "done", score=99, status="applied")
    _job(world, "failed", score=99, gate="fail", status="skipped")
    assert [r["id"] for r in hr.build(world, today=TODAY, now=RUN)["rows"]] == ["b", "a"]    # by score, not by name


def test_freshness_is_measured_from_posted_at_to_first_seen(world):
    _job(world, "quick", posted=RUN - timedelta(hours=3), first_seen=RUN - timedelta(hours=2))
    _job(world, "slow", posted=RUN - timedelta(hours=10), first_seen=RUN - timedelta(hours=1))
    _job(world, "undated", posted=None)
    _job(world, "skewed", posted=RUN - timedelta(hours=1), first_seen=RUN - timedelta(hours=2))   # seen before posted
    f = hr.build(world, today=TODAY, now=RUN)["freshness"]
    assert f["measured"] == 2 and f["median_hours"] == 5.0 and f["within_target"] == 1 and f["target_hours"] == 3


def test_freshness_counts_only_postings_published_in_the_window_and_names_the_backlog(world):
    """A first board scan after a long gap sees months-old postings and the median freshness reads in the thousands of hours. A posting older than the window could never have been seen fresh: it is backlog, counted apart."""
    _job(world, "quick", posted=RUN - timedelta(hours=3), first_seen=RUN - timedelta(hours=2))
    _job(world, "old", posted=RUN - timedelta(days=40), first_seen=RUN - timedelta(hours=1))
    f = hr.build(world, today=TODAY, now=RUN)["freshness"]
    assert f["measured"] == 1 and f["median_hours"] == 1.0 and f["backlog"] == 1


def test_coverage_counts_open_jobs_with_a_triage_score(world):
    _job(world, "scored")
    rec = _job(world, "unscored")
    rec.pop("hunt_score")
    rec["hunt_score"] = None
    fa.save_job(world, rec)
    c = hr.build(world, today=TODAY, now=RUN)["coverage"]
    assert c == {"open": 2, "scored": 1, "share": 0.5}


def test_write_puts_the_review_file_and_moves_the_window(world):
    _job(world, "top", score=85, title="AI Engineer, Agents")
    path = hr.write(world, hr.build(world, today=TODAY, now=RUN), today=TODAY, now=RUN)
    text = path.read_text()
    assert path.name == "fresh-roles-2026-09-29.md" and "AI Engineer, Agents" in text and "full report" in text
    assert "score-fit" in text                                          # the command for the action is printed
    assert json.loads((world / "state" / "hunt" / "last-review.json").read_text())["at"] == "2026-09-29T14:30:00Z"


def test_cli_dry_run_prints_and_writes_nothing(world, capsys):
    _job(world, "top", score=85)
    assert hr.main(["--root", str(world), "--today", "2026-09-29", "--dry-run"]) == 0
    assert "top" in capsys.readouterr().out
    assert not (world / "state" / "review").exists() and not (world / "state" / "hunt").exists()


def test_the_cli_window_comes_from_today_or_now_never_the_wall_clock(world, capsys):
    """--today moves the date and the window follows it, not the real clock."""
    _job(world, "top", score=85)
    assert hr.main(["--root", str(world), "--today", "2026-09-29", "--dry-run", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["now"] == "2026-09-30T06:59:59Z" and out["since"] == "2026-09-29T06:59:59Z"   # the end of that PT day
    assert hr.main(["--root", str(world), "--now", "2026-09-29T14:30:00Z", "--dry-run", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["now"] == "2026-09-29T14:30:00Z" and out["date"] == "2026-09-29" and [r["id"] for r in out["rows"]] == ["top"]


def test_the_review_carries_todays_linkedin_sweep_from_the_people_watch(world):
    (world / "targets" / "people-watch.yaml").write_text(
        "people:\n  - {name: Ana Ruiz, linkedin_url: 'https://www.linkedin.com/in/ana-ruiz', company: tiny-ai,"
        " why: founder posts roles, added: 2026-09-29, source: prospects, status: watch}\n")
    review = hr.build(world, today=TODAY, now=RUN)
    assert [p["name"] for p in review["linkedin_plan"]] == ["Ana Ruiz"]
    text = hr.render(review)
    assert "LinkedIn sweep" in text and "browser_research.py view --person \"Ana Ruiz\"" in text


def test_the_review_works_without_a_people_watch(world):
    assert hr.build(world, today=TODAY, now=RUN)["linkedin_plan"] == []


# --------------------------------------------------------------------------- --capture (the scheduled review runs it)
def test_capture_takes_the_jds_then_rescores_then_reviews(world, monkeypatch, capsys):
    calls = []
    import jd_capture
    monkeypatch.setattr(jd_capture, "capture",
                        lambda root, **kw: calls.append(("capture", kw.get("snap"))) or {"counts": {"captured": 2}})
    monkeypatch.setattr(hr.hs, "run", lambda root, **kw: calls.append(("rescore",)) or [])
    _job(world, "top", score=85)
    assert hr.main(["--root", str(world), "--now", "2026-09-29T14:30:00Z", "--capture", "--dry-run"]) == 0
    assert calls == [("capture", None), ("rescore",)]                  # never Firecrawl in a routine
    assert "top" in capsys.readouterr().out


def test_a_capture_failure_never_blocks_the_review(world, monkeypatch, capsys):
    import jd_capture

    def boom(root, **kw):
        raise RuntimeError("network down")
    monkeypatch.setattr(jd_capture, "capture", boom)
    _job(world, "top", score=85)
    assert hr.main(["--root", str(world), "--now", "2026-09-29T14:30:00Z", "--capture", "--dry-run"]) == 0
    out = capsys.readouterr()
    assert "top" in out.out and "JD capture failed (RuntimeError: network down)" in out.err


# The morning brief builds Fresh 10 before the first poll; the review rebuilds it afterwards (--fresh10), so the
# day's Fresh 10 sees the day's first polls
def test_fresh10_is_rebuilt_after_the_review(world, capsys):
    _job(world, "top-jd", score=85)
    assert hr.main(["--root", str(world), "--today", "2026-09-29", "--now", "2026-09-29T14:30:00Z", "--fresh10"]) == 0
    review = world / "state" / "review" / "fresh-roles-2026-09-29.md"
    fresh = world / "state" / "daily" / "2026-09-29-fresh10.md"
    assert review.is_file() and fresh.is_file() and fresh.stat().st_mtime >= review.stat().st_mtime


def test_a_dry_run_rebuilds_nothing(world):
    assert hr.main(["--root", str(world), "--today", "2026-09-29", "--now", "2026-09-29T14:30:00Z", "--fresh10",
                    "--dry-run"]) == 0
    assert not (world / "state" / "daily").exists()


# The rebuild once ranked Fresh 10 as of 23:59 PT (fresh10 --today means the end of the
# day), so a role posted 70 h before the 07:30 run fell out of the 72 h window; it ranks at the review's instant now
def test_the_fresh10_rebuild_ranks_at_the_review_instant_not_the_end_of_the_day(world, capsys):
    _job(world, "seventy-h", score=85, title="Applied AI Engineer", posted=RUN - timedelta(hours=70),
         first_seen=RUN - timedelta(hours=3))
    assert hr.main(["--root", str(world), "--today", "2026-09-29", "--now", "2026-09-29T14:30:00Z", "--fresh10"]) == 0
    fresh = (world / "state" / "daily" / "2026-09-29-fresh10.md").read_text()
    assert "Applied AI Engineer" in fresh


# finding 3: the people file comes from 06:45; rows that enter at 07:30 with no people entry are named
def test_rows_new_since_the_people_report_are_named(world, capsys):
    _job(world, "seventy-h", score=85, title="Applied AI Engineer", posted=RUN - timedelta(hours=10))
    (world / "state" / "daily").mkdir(parents=True)
    (world / "state" / "daily" / "2026-09-29-fresh10-people.md").write_text("---\ntype: report\n---\n| other-job | x |\n")
    assert hr.main(["--root", str(world), "--today", "2026-09-29", "--now", "2026-09-29T14:30:00Z", "--fresh10"]) == 0
    err = capsys.readouterr().err
    assert "no people entry" in err and "seventy-h" in err


# finding 2: an id matches only as a whole token, and a "Company · Title" row counts only when one job carries it
def test_an_id_inside_a_longer_id_is_not_queued_and_a_shared_title_is_flagged(world):
    (world / "hub" / "NEXT-UP.md").write_text(
        "| # | role | fit | why here | chance | posted | state | page | tier |\n|---|---|---|---|---|---|---|---|---|\n"
        "| 1 | Tiny AI · Agent Engineer | 4.5 | x | x | x | JOB (tiny-12345) | none | mass |\n")
    _job(world, "tiny-1234", score=90, fit=4.3, title="ML Engineer")
    _job(world, "tiny-ae-1", score=90, fit=4.4, title="Agent Engineer")
    _job(world, "tiny-ae-2", score=89, fit=4.4, title="Agent Engineer")
    review = hr.build(world, today=TODAY, now=RUN)
    assert _route(review, "tiny-1234")["action"] == "apply: propose a NEXT-UP row"
    for jid in ("tiny-ae-1", "tiny-ae-2"):
        row = _route(review, jid)
        assert row["action"] == "apply: propose a NEXT-UP row" and "2 postings share" in row["command"]


def test_the_shared_title_check_rides_on_every_action_not_only_apply(world):
    (world / "hub" / "NEXT-UP.md").write_text("| 1 | Tiny AI · Agent Engineer | 4.5 | x | x | x | READY | 09 | focused |\n")
    _job(world, "tiny-ae-1", score=90, fit=4.4, title="Agent Engineer")
    _job(world, "tiny-ae-new", score=88, title="Agent Engineer")                 # unscored: a full report
    row = _route(hr.build(world, today=TODAY, now=RUN), "tiny-ae-new")
    assert row["action"] == "full report" and "2 postings share" in row["command"]


def test_the_people_note_matches_whole_ids(world, capsys):
    _job(world, "acme-1", score=85, title="Applied AI Engineer", posted=RUN - timedelta(hours=10))
    (world / "state" / "daily").mkdir(parents=True)
    (world / "state" / "daily" / "2026-09-29-fresh10-people.md").write_text("| acme-12 | x |\n")
    hr.main(["--root", str(world), "--today", "2026-09-29", "--now", "2026-09-29T14:30:00Z", "--fresh10"])
    assert "acme-1" in capsys.readouterr().err.split("no people entry", 1)[-1]
