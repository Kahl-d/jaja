"""Today's learning is read by tomorrow's morning cards (the charter's evening and morning steps): the evening wrap
(scripts/evening_wrap.py --write) writes state/recruiter/wrap-<date>.md with the day's numbers against the goals, what
worked, new LESSONS candidates, experiments and the items for the owner to decide, keeping the Recruiter's `## Decide`;
cards.py shows a header before the cards: the clock, goal progress, rounds coming (with any flag), re-checks due,
experiments, learned rows and the last wrap's items. Fixture trees only."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(SCRIPTS))
import interviews as iv  # noqa: E402
import learn  # noqa: E402
import tracker  # noqa: E402


def _load(name: str, alias: str):
    spec = importlib.util.spec_from_file_location(alias, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cards = _load("cards", "js_cards_header_under_test")
import evening_wrap as ew  # noqa: E402

ev = lambda at, t, evidence="x": tracker.format_event(at, t, "manual", 1.0, evidence)   # noqa: E731
MON, TUE = date(2026, 10, 5), date(2026, 10, 6)


def app(root: Path, slug: str, events: list[str], **meta) -> None:
    d = root / "applications" / slug
    d.mkdir(parents=True)
    head = {"type": "application", "id": slug, "company": meta.pop("company", slug.title()), **meta}
    (d / "application.md").write_text("---\n" + "".join(f"{k}: {json.dumps(v)}\n" for k, v in head.items())
                                      + "---\n## Events\n" + "\n".join(events) + "\n", encoding="utf-8")


@pytest.fixture
def js(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.delattr(iv, "upcoming", raising=False)
    monkeypatch.delattr(iv, "held", raising=False)
    (tmp_path / "clock.yaml").write_text(
        "employer: Example Corp\nemployment_end: 2026-10-02\nemployment_end_confirmed: true\nclock_days: 90\n"
        "basis: synthetic test clock\nphases:\n  - {weeks: [1, 13], name: search}\n", encoding="utf-8")
    rec = tmp_path / "state" / "recruiter"
    rec.mkdir(parents=True)
    (rec / "LESSONS.md").write_text(
        "2026-10-02 | Acme rejected | codes YRS | stop cold above the years count | x.md | 2026-10-05\n", encoding="utf-8")
    (rec / "experiments.yaml").write_text(
        "experiments:\n  - {id: X9, hypothesis: person beats cold, count: path, result: null}\n", encoding="utf-8")
    (tmp_path / "targets").mkdir()
    (tmp_path / "targets" / "goals.yaml").write_text(
        "status: proposed\nfloors:\n  interviews_booked: {daily: null, weekly: 2}\n"
        "  applications_mass: {daily: 3, weekly: 15}\n", encoding="utf-8")
    app(tmp_path, "acme-x", [ev("2026-10-01T17:00:00Z", "applied"), ev("2026-10-05T18:00:00Z", "rejected")],
        path="cold", company="Acme")
    app(tmp_path, "brightwell-x", [ev("2026-10-05T16:00:00Z", "applied"),
                                  ev("2026-10-05T20:00:00Z", "screen", "invite round_at=2026-10-09T18:30:00Z")],
        path="person", applied_via="recruiter", company="Brightwell")
    learn.add(tmp_path, "prep-coach", "Brightwell screen: lead with the agent harness story",
              "prep/debriefs/brightwell-screen-2026-10-05.md", day=MON)
    return tmp_path


def _sweep() -> dict:
    groups = {k: [] for k in ("posts", "people", "listings", "alerts", "mail", "scam", "follow_ups", "approvals",
                              "outbox", "applications")}
    groups["listings"] = [{"key": "job:a", "group": "listings", "kind": "listing", "job": "a", "company": "A",
                           "company_slug": "a", "title": "AI Engineer", "location": "Remote US", "gate": "pass",
                           "hunt_score": 60, "source": "greenhouse", "url": "https://example.com/a"}]
    return {"type": "sweep", "date": "2026-10-06", "since": "2026-10-06T07:00:00Z", "groups": groups}


# --------------------------------------------------------------------------- the evening wrap
def test_the_wrap_writes_one_file_with_every_section(js):
    w = ew.build(js, MON)
    path = ew.write(js, w)
    assert path == js / "state" / "recruiter" / "wrap-2026-10-05.md"
    text = path.read_text(encoding="utf-8")
    for head in ("# Evening wrap 2026-10-05", "## Numbers against the goals", "## What went out", "## What came back",
                 "## What worked", "## New LESSONS candidates", "## Experiments", "## Re-checks due",
                 "## For the owner to decide", "## Decide"):
        assert head in text, head
    assert "Job clock: day 3 of 90" in text
    assert "- applications mass: today 0 of 3" in text
    assert "Brightwell: screen (path person, channel recruiter" in text and "Acme: rejected (path cold" in text
    assert "prep-coach: Brightwell screen: lead with the agent harness story" in text
    assert "rejection today, `acme-x`: write its coded LESSONS line" in text
    assert "X9: running (needs 15 a side" in text
    assert "re-check due 2026-10-05 (LESSONS.md line 1)" in text
    assert "confirm the floors in targets/goals.yaml (status proposed)" in text


def test_the_recruiters_decide_section_survives_a_rewrite(js):
    path = ew.write(js, ew.build(js, MON))
    text = path.read_text(encoding="utf-8")
    path.write_text(text.split("\n## Decide\n")[0] + "\n## Decide\n\n- keep recruiters first: 2 of 2 screens (Recruiter)\n",
                    encoding="utf-8")
    ew.write(js, ew.build(js, MON))
    assert path.read_text(encoding="utf-8").endswith("## Decide\n\n- keep recruiters first: 2 of 2 screens (Recruiter)\n")


def test_the_cli_writes_only_with_write(js):
    cmd = [sys.executable, str(SCRIPTS / "evening_wrap.py"), "--root", str(js), "--date", "2026-10-05"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("Wrap 2026-10-05:") and not (js / "state" / "recruiter" / "wrap-2026-10-05.md").exists()
    r = subprocess.run(cmd + ["--write"], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and (js / "state" / "recruiter" / "wrap-2026-10-05.md").is_file()


def test_last_wrap_is_the_newest_before_today(js):
    rec = js / "state" / "recruiter"
    for d in ("2026-10-01", "2026-10-02", "2026-10-06"):
        (rec / f"wrap-{d}.md").write_text(f"## For the owner to decide\n\n- item {d}\n- none\n", encoding="utf-8")
    got = ew.last_wrap(js, date(2026, 10, 5))
    assert got.name == "wrap-2026-10-02.md" and ew.decide_items(got) == ["item 2026-10-02"]


# --------------------------------------------------------------------------- the cards header
def test_the_cards_show_goals_and_learning_before_the_cards(js):
    ew.write(js, ew.build(js, MON))                                     # last evening's wrap
    res = cards.build(js, _sweep())
    md = cards.render(res)
    head = md.index("## Goals and learning")
    assert head < md.index("\ndecisions:") < md.index("## Quick cards")
    block = md[head:md.index("\ndecisions:")]
    assert "Job clock: day 4 of 90" in block
    assert "- Goals (targets/goals.yaml, proposed; floors, no ceiling), week of 2026-10-05:" in block
    assert "  - interviews booked: today 0 · week 1 of 2 (week below by 1)" in block
    assert "Interview rounds in the next 7 days (tracker round_at): Brightwell screen 2026-10-09 11:30 PT" in block
    assert "Re-checks due (LESSONS.md): 1" in block
    assert "X9: running (needs 15 a side; smallest arm 1)" in block
    assert "prep-coach: Brightwell screen: lead with the agent harness story" in block
    assert "Last evening wrap (wrap-2026-10-05.md):" in block
    assert "confirm the floors in targets/goals.yaml (status proposed)" in block
    assert res["header"][0] == "## Goals and learning"


def test_the_header_shows_a_flag_from_interviews_upcoming(js, monkeypatch):
    monkeypatch.setattr(iv, "upcoming", lambda root, today: [
        {"slug": "brightwell-x", "company": "Brightwell", "stage": "screen", "status": "upcoming",
         "round_at": "2026-10-09T18:30:00Z", "date_pt": "2026-10-09", "time_pt": "11:30",
         "flag": "records disagree: 11:30 vs 11:45"}], raising=False)
    block = "\n".join(cards.header(js, "2026-10-06"))
    assert "(interviews.upcoming): Brightwell screen 2026-10-09 11:30 PT (records disagree: 11:30 vs 11:45)" in block


def test_the_header_degrades_and_never_stops_the_cards(root):
    res = cards.build(root, _sweep())                                   # an empty tree: no goals, clock or ledgers
    block = "\n".join(res["header"])
    assert "Job clock: no clock.yaml" in block
    assert "Goals: no targets/goals.yaml (missing is not empty)" in block
    assert "Experiments: 0 (0 ready to decide) (state/recruiter/experiments.yaml missing)" in block
    assert "Last evening wrap: none written yet" in block
    assert len(res["cards"]) == 1


@pytest.fixture
def root(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.delattr(iv, "upcoming", raising=False)
    r = tmp_path / "data"
    r.mkdir()
    return r
