"""fresh10.py: the day's freshest roles worth a page."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fresh10 as f  # noqa: E402

from layout import JS  # noqa: E402
def test_title_rules():
    assert f.on_target("AI Software Engineer, App Intelligence", None)
    assert f.on_target("Forward Deployed Engineer", "forward-deployed")
    assert not f.on_target("Penetration Testing Engineer, MTS", None)
    assert not f.on_target("Product Engineer (Analog Circuit Design)", "product-engineer")


def test_a_role_the_gate_could_not_place_abroad_is_out_and_us_is_in():
    reasons = ["location: cannot place it"]
    assert f.placed_abroad({"gate_reasons": reasons, "location": "Shenzhen, Guangdong,CN, CN"})
    assert f.placed_abroad({"gate_reasons": reasons, "location": "LEEDS 01"})
    assert not f.placed_abroad({"gate_reasons": reasons, "location": "California - Remote"})
    assert not f.placed_abroad({"gate_reasons": [], "location": "London"})        # the gate placed it: not ours to judge


def test_stopped_reads_jobs_and_applications(tmp_path):
    (tmp_path / "state" / "recruiter").mkdir(parents=True)
    (tmp_path / "state" / "recruiter" / "stopped.yaml").write_text(
        "stopped:\n  - application: a-1\n    job: j-1\n  - job: j-2\n", encoding="utf-8")
    assert f.stopped(tmp_path) == {"a-1", "j-1", "j-2"}
    assert f.stopped(tmp_path / "none") == set()


@pytest.mark.real_records
def test_the_real_list_keeps_its_rules():
    now = datetime.now(f.TZ)
    res = f.pick(JS, now)
    rows = res["rows"]
    assert res["counts"]["jobs"] > 1000 and rows                 # the real job records, not an empty folder
    assert len(rows) <= 10
    per = {}
    for r in rows:
        per[r["company"]] = per.get(r["company"], 0) + 1
        posted = datetime.strptime(r["posted"], "%Y-%m-%d %H:%M").replace(tzinfo=f.TZ)
        assert posted >= now - timedelta(hours=73)
        assert r["fit"] is None or r["fit"] >= 4.0
        assert r["years"] is None or r["years"] <= f.COLD_MAX_YEARS or (r["warm"] and r["stretch"])
        assert r["years"] is None or r["years"] < f.fit_gates.REJECT_YEARS
    assert all(n <= 2 for n in per.values())
    assert not {r["id"] for r in rows} & f.stopped(JS)


def test_expected_interviews_order_warm_fresh_and_true_years_first():
    rates = {"warm": 0.25, "cold": 0.03}
    warm_fresh = f.expected(rates, True, 10, False, "yes", 4.5)
    cold_fresh = f.expected(rates, False, 10, False, "yes", 4.5)
    warm_stretch_old = f.expected(rates, True, 70, True, "unknown", None)
    assert warm_fresh > warm_stretch_old > cold_fresh
    assert f.expected(rates, False, 10, False, "no", 4.5) < cold_fresh       # sponsorship is weighed, not a gate


def test_a_post_with_no_named_employer_is_out():
    for name in ("Hiring: Data, Cloud & Backend Engineers", "Stealth AI fintech (via Example Recruitment)",
                 "Acme (unnamed client)"):
        assert f.NO_EMPLOYER.search(name), name
    for name in ("Example Corp", "Acme", "Globex AI", "Initech (incl. Cloud)"):
        assert not f.NO_EMPLOYER.search(name), name


def test_render_links_the_people_file_only_when_a_row_has_no_warm_path():
    from datetime import datetime as dt
    res = f.pick(JS, dt.now(f.TZ))
    text = f.render(res)
    linked = "-fresh10-people.md" in text
    assert linked == any(not r["warm"] for r in res["rows"])


def test_years_rule_cold_band_and_reject_bar():
    # The persona holds about 29 engineer months: up to 3 years asked goes cold; 4+ only through a person (a stretch);
    # 8+ is the fit gate.
    assert [f.years_rule(y) for y in (None, 1, 2, 3, 4, 8)] == [None, None, None, None, "OUT", "OUT"]
    assert [f.years_rule(y, warm=True) for y in (2, 3, 4, 5, 7, 8)] == [None, None, "STRETCH", "STRETCH", "STRETCH", "OUT"]


def test_pick_takes_an_over_count_ask_only_through_a_person(tmp_path, monkeypatch):
    # 4+ years asked is no cold application; with a person inside it is a STRETCH row; 8+ is out even
    # warm. The warm path is looked up before the years decision (the review's mutants a and b).
    import company_index
    import fetch_ats
    import focus_profiles
    import strategy_review
    now = datetime(2026, 10, 2, 12, 0, tzinfo=f.TZ)
    posted = (now - timedelta(hours=5)).isoformat()

    def job(company, ask):
        return {"company": company, "company_slug": company.lower(), "title": "AI Engineer", "posted_at": posted,
                "status": "fit_checked", "gate": "pass", "fit_score": 4.4, "_jd_text": ask, "location": "SF"}
    jobs = {"w-3": job("WarmCo", "4+ years of experience."), "c-3": job("ColdCo", "4+ years of experience."),
            "c-2": job("ColdTwo", "2+ years of experience."), "w-8": job("WarmEight", "8+ years of experience.")}
    monkeypatch.setattr(fetch_ats, "load_jobs", lambda root: jobs)
    monkeypatch.setattr(f, "channel_rates", lambda root, now: {"warm": 0.2, "cold": 0.03, "warm_k": 0, "warm_n": 0,
                                                               "cold_k": 0, "cold_n": 0})
    monkeypatch.setattr(f, "stopped", lambda root: set())
    monkeypatch.setattr(company_index.CompanyIndex, "load", classmethod(lambda cls, root: None))
    monkeypatch.setattr(focus_profiles, "pick", lambda root, title: [])
    monkeypatch.setattr(strategy_review, "warm_paths",
                        lambda root, index, slug: ["pat-inside"] if slug in ("warmco", "warmeight") else [])
    res = f.pick(tmp_path, now)
    rows = {r["id"]: r for r in res["rows"]}
    assert set(rows) == {"w-3", "c-2"}, rows
    assert rows["w-3"]["stretch"] and "only through the person named" in rows["w-3"]["next"]
    assert not rows["c-2"]["stretch"] and res["counts"]["years_out"] == 2       # c-3 cold, w-8 eight or more
