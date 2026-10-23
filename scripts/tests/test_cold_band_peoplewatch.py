"""The cold-application band in the hunt gate and the people watch.

A role asking up to 3 years goes COLD (the candidate is a few months short; experienced framing); 4+ goes only
through a person. fit_gates wrote "no cold application" for a 3-year ask; its note now agrees with
fresh10.years_rule, and both read one band (fit_gates.cold_years).

The people watch: a recruiter is not a hiring poster. The row stays (the file is add-only) with status `dropped` and a
dated note, so the X people query no longer names the person and the watch's worst case stays at or under its cap.
Synthetic files in tmp_path; the real files are only read for structure. No call is made."""
from __future__ import annotations

from datetime import date

import pytest
import yaml

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import fit_gates as fg  # noqa: E402
import fresh10  # noqa: E402
import js_common as jc  # noqa: E402
import people_watch as pw  # noqa: E402
import x_api as xa  # noqa: E402


def _job(title):
    return {"id": "acme-1", "company": "Acme", "company_slug": "acme", "title": title, "location": "San Francisco, CA",
            "remote": "onsite", "employment_type": "full-time", "sponsorship": None, "sponsorship_evidence": None,
            "status": "discovered"}


def _text(title, jd):
    out = fg.evaluate(_job(title), jd, config=fg.default_config())
    return out, " ".join(str(r) for r in out["reasons"] + out.get("notes", []))


@pytest.mark.parametrize("title", ["AI Engineer", "Software Engineer, Early Career"])
def test_a_three_year_ask_is_a_cold_application_in_the_gate_note(title):
    out, text = _text(title, "3+ years of experience with Python.")
    assert out["gate"] == "pass", out["reasons"]
    assert "no cold application" not in text and "STRETCH" not in text, text
    assert f"about 7 months over your ~{fg.ENGINEER_MONTHS} engineer months" in text and "cold application" in text, text


@pytest.mark.parametrize("years", [4, 5, 7])
def test_four_to_seven_years_stay_a_stretch_only_through_a_person(years):
    out, text = _text("AI Engineer", f"{years}+ years of experience with Python.")
    assert out["gate"] == "pass" and "no cold application; STRETCH only through a person" in text, text


def test_the_gate_and_fresh10_read_one_cold_band():
    assert fg.COLD_MAX_YEARS == 3 and fresh10.COLD_MAX_YEARS == fg.COLD_MAX_YEARS
    for years in range(1, 8):                                          # 8+ is the gate's fail (test_fit_gates.py)
        _, text = _text("AI Engineer", f"{years}+ years of experience with Python.")
        cold_note = "no cold application" not in text
        assert cold_note == (fresh10.years_rule(years) is None) == fg.cold_years(years), (years, text)
    assert fg.evaluate(_job("AI Engineer"), "8+ years of experience.", config=fg.default_config())["gate"] == "fail"
    assert fresh10.years_rule(8) == fresh10.years_rule(8, warm=True) == "OUT"


def _watch(tmp_path):
    (tmp_path / "targets").mkdir()
    (tmp_path / "targets" / "people-watch.yaml").write_text(yaml.safe_dump({"people": [
        {"name": "Priya Nair", "x_handle": "priyanair", "linkedin_url": "https://www.linkedin.com/in/priya-nair",
         "status": "dropped", "why": "posts about hiring", "note": "2030-01-05: a recruiter, not a hiring poster"},
        {"name": "Sam Rivera", "x_handle": "samrivera", "linkedin_url": "https://www.linkedin.com/in/sam-rivera",
         "status": "watch", "why": "founder who posts roles"}]}), encoding="utf-8")
    (tmp_path / "x-watch.yaml").write_text(yaml.safe_dump({"max_results": 10, "queries": []}), encoding="utf-8")
    return tmp_path


def test_a_dropped_person_is_off_the_watch_with_a_dated_reason_and_the_row_kept(tmp_path):
    root = _watch(tmp_path)
    rows = [p for p in pw.load(root) if p["name"] == "Priya Nair"]
    assert len(rows) == 1                                               # add-only: the row stays
    assert rows[0]["status"] == "dropped" and rows[0]["x_handle"] == "priyanair"
    assert str(rows[0].get("note") or "").startswith("2030-01-05") and "recruiter" in rows[0]["note"]
    assert pw.x_handles(root) == ["samrivera"]
    plan = pw.linkedin_plan(root, today=date(2030, 1, 5), limit=pw.A_DAILY_VIEWS)
    assert [p["name"] for p in plan] == ["Sam Rivera"]


def test_the_people_query_leaves_a_dropped_person_out_within_the_run_cap(tmp_path):
    root = _watch(tmp_path)
    plan = xa.watch_plan(root, root / "x-watch.yaml")
    runs = [q for q, _ in plan["runs"]]
    assert all("priyanair" not in q.lower() for q in runs), runs
    assert plan["runs"][0][1] == "people on the watch list" and "from:samrivera" in runs[0]
    assert plan["worst_usd"] <= 0.45 + 1e-9


@pytest.mark.real_records
def test_every_dropped_row_in_the_real_watch_file_keeps_a_dated_reason_and_is_off_the_queries():
    people = pw.load(jc.JS_ROOT)
    dropped = [p for p in people if p["status"] == "dropped"]
    handles = pw.x_handles(jc.JS_ROOT)
    for p in dropped:
        assert str(p.get("note") or "")[:4].isdigit(), p["name"]
        assert p["x_handle"] not in handles and p["name"] not in [q["name"] for q in pw.linkedin_plan(
            jc.JS_ROOT, today=date(2030, 1, 5), limit=pw.A_DAILY_VIEWS)]
