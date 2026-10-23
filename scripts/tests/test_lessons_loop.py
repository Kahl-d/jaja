"""lessons.py and the goals: LESSONS.md parsed with its re-checks,
experiments with n per arm computed from the records and marked ready only at about 15 tries a side (one constant,
js_common.MIN_TRIES, shared with strategy_review's MIN_N) or a clear cause, targets/goals.yaml read before the retired
target.yaml, and today's and this week's numbers against the floors. Fixture trees only."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import interviews as iv  # noqa: E402
import js_common as jc  # noqa: E402
import lessons as ls  # noqa: E402
import tracker  # noqa: E402

DAY = date(2026, 10, 6)                      # a Tuesday; the week starts Monday 2026-10-05
NOW = datetime(2026, 10, 7, 6, 59, tzinfo=timezone.utc)
ev = lambda at, t, evidence="x": tracker.format_event(at, t, "manual", 1.0, evidence)   # noqa: E731

LESSONS = """# The Lead Recruiter's lessons

Append-only. One line per lesson: `date | what happened | evidence (file or number) | change | where | re-check on`.

2026-10-02 | Acme rejected in 21 h | codes YRS (high), COLD | stop cold above the years count | research/x.md | 2026-10-05
2026-10-03 | Beta screen via recruiter | applications/beta/application.md | keep recruiters first | LESSONS.md | 2026-10-20
2026-10-03 | Kappa resume unrecorded | code PDF | applied events require the PDF hash | scripts/tracker.py | on merge
2026-10-05 | path backfill | person 4, cold 38 | the multiplier is withdrawn | review-2026-10-05.md Decide

## Decision thresholds
2. A change is proposed only at 20+ sends per arm.
"""

EXPERIMENTS = """experiments:
  - id: X1
    hypothesis: first touches get replies
    n_so_far: 0
    read_on: "2026-10-20"
    result: null
  - id: X2
    hypothesis: fresh cold applications do better
    n_so_far: {within_72h: 6, later: 36}
    result: null
  - id: X3
    hypothesis: recruiter submits keep converting
    n_so_far: {recruiter: 2, referral: 2}
    result: null
  - id: X9
    hypothesis: the person path beats cold
    count: path
    result: null
  - id: X5
    hypothesis: a named years bar rejects
    clear_cause: "the rejection mail named the years"
    result: null
"""


def job(root: Path, jid: str, posted: str | None) -> None:
    (root / "jobs").mkdir(exist_ok=True)
    meta = {"type": "job", "id": jid, "company": "Acme", "title": "AI Engineer", "posted_at": posted}
    (root / "jobs" / f"{jid}.md").write_text("---\n" + "".join(f"{k}: {json.dumps(v)}\n" for k, v in meta.items())
                                             + "---\n# job\n", encoding="utf-8")


def app(root: Path, slug: str, events: list[str], **meta) -> None:
    d = root / "applications" / slug
    d.mkdir(parents=True)
    head = {"type": "application", "id": slug, "company": meta.pop("company", slug.title()), **meta}
    (d / "application.md").write_text("---\n" + "".join(f"{k}: {json.dumps(v)}\n" for k, v in head.items())
                                      + "---\n## Events\n" + "\n".join(events) + "\n", encoding="utf-8")


@pytest.fixture
def js(tmp_path: Path) -> Path:
    rec = tmp_path / "state" / "recruiter"
    rec.mkdir(parents=True)
    (rec / "LESSONS.md").write_text(LESSONS, encoding="utf-8")
    (rec / "experiments.yaml").write_text(EXPERIMENTS, encoding="utf-8")
    # 15 person-path applications (2 by recruiters, 1 by referral), 16 cold ones (3 applied within 72 h of posting)
    for n in range(15):
        via = "recruiter" if n < 2 else "referral" if n == 2 else "email"
        events = [ev("2026-09-20T17:00:00Z", "applied")] + ([ev("2026-09-25T17:00:00Z", "screen")] if n < 2 else [])
        app(tmp_path, f"warm-{n}", events, path="person", applied_via=via, tier="focused")
    for n in range(16):
        jid = f"job-cold-{n}"
        job(tmp_path, jid, "2026-09-19T17:00:00Z" if n < 3 else "2026-09-01T17:00:00Z")
        app(tmp_path, f"cold-{n}", [ev("2026-09-20T17:00:00Z", "applied")], path="cold", applied_via="ats",
            job=f"[[{jid}]]")
    # today: one mass and one focused application, a booked round with its time on record
    app(tmp_path, "today-mass", [ev("2026-10-06T17:00:00Z", "applied")], path="cold", tier="mass")
    app(tmp_path, "today-focused", [ev("2026-10-06T18:00:00Z", "applied"),
                                    ev("2026-10-06T20:00:00Z", "screen", "invite round_at=2026-10-09T18:30:00Z")],
        path="person", applied_via="recruiter", company="FormCo")
    app(tmp_path, "undated", [ev("2026-10-01T17:00:00Z", "applied"), ev("2026-10-02T17:00:00Z", "screen")],
        path="person", applied_via="recruiter", company="QuantCo")
    touches = [{"at": "2026-10-06T16:00:00Z", "prospect": "pat", "channel": "linkedin", "direction": "out",
                "kind": "intro", "status": "sent"},
               {"at": "2026-10-06T19:00:00Z", "prospect": "pat", "channel": "linkedin", "direction": "in",
                "kind": "reply", "status": "replied"},
               {"at": "2026-10-05T16:00:00Z", "prospect": "lee", "channel": "email", "direction": "out",
                "kind": "intro", "status": "sent"},
               {"at": "2026-10-04T16:00:00Z", "prospect": "sam", "channel": "email", "direction": "in",
                "kind": "reply", "status": "replied"},
               {"at": "2026-10-06T16:00:00Z", "prospect": "sam", "channel": "email", "direction": "out",
                "kind": "reply", "status": "sent"}]
    (tmp_path / "touches").mkdir()
    (tmp_path / "touches" / "2026-10.jsonl").write_text("".join(json.dumps(t) + "\n" for t in touches),
                                                        encoding="utf-8")
    posts = tmp_path / "state" / "posts"
    posts.mkdir(parents=True)
    (posts / "linkedin-2026-10-06.jsonl").write_text(
        "".join(json.dumps({"url": f"https://www.linkedin.com/posts/{i}"}) + "\n" for i in (1, 2, 2)), encoding="utf-8")
    (posts / "x-2026-10-05.jsonl").write_text(json.dumps({"url": "https://x.com/p/9"}) + "\n", encoding="utf-8")
    (tmp_path / "targets").mkdir()
    (tmp_path / "targets" / "goals.yaml").write_text("""status: proposed
floors:
  interviews_booked: {daily: null, weekly: 2, why: "reported daily"}
  applications_mass: {daily: 10, weekly: 50}
  applications_focused: {daily: 1, weekly: 10}
  first_touches: {daily: 1, weekly: 5}
  posts_read_linkedin: {daily: 2, weekly: 10}
  posts_read_x: {daily: 1, weekly: 5}
""", encoding="utf-8")
    return tmp_path


# --------------------------------------------------------------------------- the one constant
def test_the_learning_gate_is_one_constant_shared_with_strategy_review():
    import strategy_review as sr
    assert jc.MIN_TRIES == 15
    assert sr.MIN_N == jc.MIN_TRIES and ls.MIN_TRIES == jc.MIN_TRIES


# --------------------------------------------------------------------------- LESSONS.md
def test_lessons_parse_with_recheck_dates_codes_and_problems(js):
    rows, problems = ls.lessons(js)
    assert [r["date"] for r in rows] == ["2026-10-02", "2026-10-03", "2026-10-03", "2026-10-05"]
    assert rows[0]["codes"] == ["YRS", "COLD"] and rows[0]["clear_cause"]
    assert rows[2]["recheck_on"] is None and rows[2]["codes"] == ["PDF"]          # "on merge" is never due
    assert any("missing: recheck" in p for p in problems)                        # the 5-field line is shown
    assert any("20+ sends per arm" in p and "about 15" in p for p in problems)   # the thresholds conflict
    assert [d["what"] for d in ls.due(rows, DAY)] == ["Acme rejected in 21 h"]
    assert [d["what"] for d in ls.due(rows, date(2026, 10, 20))] == ["Acme rejected in 21 h", "Beta screen via recruiter"]


# --------------------------------------------------------------------------- experiments
def test_experiments_count_n_per_arm_from_the_records(js):
    ex = {e["id"]: e for e in ls.experiments(js, NOW)[0]}
    assert ex["X2"]["counter"] == "apply_freshness" and ex["X2"]["source"] == "records (apply_freshness)"
    assert {a: v["n"] for a, v in ex["X2"]["arms"].items()} == {"within_72h": 3, "later": 13}   # not n_so_far's 6/36
    assert ex["X3"]["counter"] == "person_kind"
    assert {a: v["n"] for a, v in ex["X3"]["arms"].items()} == {"recruiter": 4, "referral": 1, "other": 12}
    assert ex["X3"]["arms"]["recruiter"]["screens"] == 4
    assert ex["X1"]["counter"] == "tagged" and ex["X1"]["source"].startswith("hand count")
    assert ex["X9"]["arms"]["person"]["n"] == 17 and ex["X9"]["arms"]["cold"]["n"] == 17


def test_ready_only_at_fifteen_a_side_or_a_clear_cause(js):
    ex = {e["id"]: e for e in ls.experiments(js, NOW)[0]}
    assert ex["X9"]["ready"] and ex["X9"]["state"] == "ready (15+ a side)"
    assert not ex["X2"]["ready"] and "smallest arm 3" in ex["X2"]["state"]
    assert ex["X5"]["ready"] and ex["X5"]["state"] == "ready (clear cause)"
    assert not ex["X1"]["ready"]


def test_tagged_arms_count_applications_and_touches(js):
    app(js, "tag-a", [ev("2026-10-01T17:00:00Z", "applied")], path="cold", experiment="X7", arm="education-first")
    app(js, "tag-b", [ev("2026-10-01T17:00:00Z", "applied")], path="cold", experiment="X7", arm="experience-first")
    (js / "state" / "recruiter" / "experiments.yaml").write_text(
        "experiments:\n  - {id: X7, hypothesis: base order, result: null}\n", encoding="utf-8")
    ex = ls.experiments(js, NOW)[0][0]
    assert ex["counter"] == "tagged" and {a: v["n"] for a, v in ex["arms"].items()} == \
        {"education-first": 1, "experience-first": 1}


# --------------------------------------------------------------------------- goals
def test_goals_yaml_wins_and_target_yaml_is_only_the_fallback(tmp_path):
    assert jc.load_goals(tmp_path)["source"] is None and jc.weekly_interview_floor(tmp_path) is None
    (tmp_path / "state" / "recruiter").mkdir(parents=True)
    (tmp_path / "state" / "recruiter" / "target.yaml").write_text("weekly_interviews: 2\n", encoding="utf-8")
    assert jc.load_goals(tmp_path)["source"] == "state/recruiter/target.yaml"
    assert jc.weekly_interview_floor(tmp_path) == 2
    (tmp_path / "targets").mkdir()
    (tmp_path / "targets" / "goals.yaml").write_text(
        "floors:\n  interviews_booked: {daily: 1, weekly: 4}\n  first_touches: {daily: 5, weekly: null}\n",
        encoding="utf-8")
    g = jc.load_goals(tmp_path)
    assert g["source"] == "targets/goals.yaml" and jc.weekly_interview_floor(tmp_path) == 4
    assert g["floors"]["first_touches"] == {"daily": 5, "weekly": None, "why": ""}


@pytest.mark.parametrize("text, why", [
    ("floors:\n  interviews_booked: {weekly: 2, ceiling: 5}\n", "no ceiling"),
    ("floors:\n  revenue: {weekly: 2}\n", "unknown metric"),
    ("floors:\n  interviews_booked: {weekly: -1}\n", "whole number"),
    ("status: proposed\n", "no `floors:`"),
])
def test_goals_refuse_a_ceiling_and_bad_floors(tmp_path, text, why):
    (tmp_path / "targets").mkdir()
    (tmp_path / "targets" / "goals.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=why):
        jc.load_goals(tmp_path)
    assert jc.weekly_interview_floor(tmp_path) is None


def test_the_real_goals_file_loads_with_a_floor_for_every_metric():
    g = jc.load_goals(jc.JS_ROOT)
    assert g["source"] == "targets/goals.yaml"
    assert set(g["floors"]) == set(jc.GOAL_METRICS)
    assert g["floors"]["interviews_booked"]["weekly"] >= 1


def test_progress_counts_today_and_the_week_against_the_floors(js, monkeypatch):
    monkeypatch.delattr(iv, "held", raising=False)
    p = ls.progress(js, DAY)
    rows = {r["metric"]: r for r in p["rows"]}
    assert p["goals"] == "targets/goals.yaml" and p["week_of"] == "2026-10-05"
    assert (rows["interviews_booked"]["today"], rows["interviews_booked"]["week"]) == (1, 1)
    assert rows["interviews_booked"]["state"] == "week below by 1"
    assert (rows["applications_mass"]["today"], rows["applications_focused"]["today"]) == (1, 1)
    assert rows["applications_focused"]["state"] == "day met; week below by 9"
    assert (rows["first_touches"]["today"], rows["first_touches"]["week"]) == (1, 2)   # sam wrote first: not ours
    assert (rows["posts_read_linkedin"]["today"], rows["posts_read_linkedin"]["week"]) == (2, 2)   # unique urls
    assert (rows["posts_read_x"]["today"], rows["posts_read_x"]["week"]) == (0, 1)       # per channel (LinkedIn, X, YC)
    assert rows["posts_read_x"]["state"] == "day below by 1; week below by 4"
    assert rows["posts_read_yc"]["state"] == "no floor"
    assert rows["interviews_held"]["today"] is None and "not measured" in rows["interviews_held"]["source"]


def test_progress_uses_a_held_count_when_interviews_py_has_one(js, monkeypatch):
    monkeypatch.setattr(iv, "held", lambda root, day: {"this_week": 1, "items": [{"date": "2026-10-06"}]},
                        raising=False)
    rows = {r["metric"]: r for r in ls.progress(js, DAY)["rows"]}
    assert (rows["interviews_held"]["today"], rows["interviews_held"]["week"]) == (1, 1)


# --------------------------------------------------------------------------- rounds coming
def test_rounds_from_the_recorded_round_time_and_undated_ones_listed(js, monkeypatch):
    monkeypatch.delattr(iv, "upcoming", raising=False)
    r = ls.rounds(js, DAY)
    assert r["source"] == "tracker round_at"
    assert [(x["company"], x["stage"], x["when"]) for x in r["rounds"]] == [("FormCo", "screen", "2026-10-09 11:30 PT")]
    assert sorted(x["company"] for x in r["undated"]) == ["QuantCo", "Warm-0", "Warm-1"]   # screens, no time


def test_rounds_prefer_interviews_upcoming_and_keep_its_flags(js, monkeypatch):
    """BUILD-SCREENER's interviews.upcoming(root, today): soonest first, untimed last, a flag on doubtful rows."""
    rows = [{"slug": "co-final", "company": "Co", "stage": "final", "status": "upcoming",
             "round_at": "2026-10-08T17:00:00Z", "date_pt": "2026-10-08", "time_pt": "10:00",
             "flag": "records disagree: 10:00 vs 10:15"},
            {"slug": "far", "company": "Far", "stage": "screen", "status": "upcoming",
             "round_at": "2026-10-30T17:00:00Z", "date_pt": "2026-10-30", "time_pt": "10:00", "flag": None},
            {"slug": "rq", "company": "QuantCo", "stage": "screen", "status": "upcoming", "round_at": None,
             "date_pt": None, "time_pt": None, "flag": "no round time recorded: tracker.py event --round-at"}]
    monkeypatch.setattr(iv, "upcoming", lambda root, today: rows, raising=False)
    r = ls.rounds(js, DAY)
    assert r["source"] == "interviews.upcoming"
    assert [(x["company"], x["when"], x["flag"]) for x in r["rounds"]] == \
        [("Co", "2026-10-08 10:00 PT", "records disagree: 10:00 vs 10:15")]     # Far is beyond the 7 days
    assert [(x["company"], x["flag"]) for x in r["undated"]] == \
        [("QuantCo", "no round time recorded: tracker.py event --round-at")]


# --------------------------------------------------------------------------- the CLI
def test_cli_is_read_only_and_prints_json(js):
    before = sorted(p.relative_to(js).as_posix() for p in js.rglob("*"))
    r = subprocess.run([sys.executable, str(SCRIPTS / "lessons.py"), "--root", str(js), "--today", "2026-10-06",
                        "--json"], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["min_tries"] == 15 and len(data["due"]) == 1 and len(data["experiments"]) == 5
    text = subprocess.run([sys.executable, str(SCRIPTS / "lessons.py"), "--root", str(js), "--today", "2026-10-06"],
                          capture_output=True, text=True, timeout=120).stdout
    assert "1 re-checks due" in text and "X9: ready (15+ a side)" in text
    assert sorted(p.relative_to(js).as_posix() for p in js.rglob("*")) == before        # nothing written
