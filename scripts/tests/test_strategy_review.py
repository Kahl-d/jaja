"""Tests for scripts/strategy_review.py: what worked, by window and bucket, and rule flags with their
numbers. Read-only; built on tracker.py, agency_status.py and the job loader."""
from __future__ import annotations

import json
import textwrap
from datetime import date
from pathlib import Path

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW

import fetch_ats as fa  # noqa: E402
import strategy_review as sr  # noqa: E402

TODAY = date(2026, 10, 1)


def w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


def app(root, slug, company, title, fit, events, prospects=(), via="null"):
    rec = fa.make_record({"name": company, "slug": slug.split("-")[0]}, title=title, source="greenhouse",
                         source_url=f"https://example.com/{slug}", apply_url=None, ats_vendor="greenhouse",
                         posted_at=None, location="SF", remote="onsite", comp=(None, None, None),
                         employment_type="full-time", jd_text="x", now=NOW)
    rec.update({"id": f"job-{slug}", "fit_score": fit})
    fa.save_job(root, rec)
    lines = "\n".join(f"- {at} · {typ} · manual · conf=1.00 · fixture" for at, typ in events)
    path = root / "applications" / slug / "application.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntype: application\nid: {slug}\njob: \"[[job-{slug}]]\"\ncompany: {company}\n"
                    f"title: \"{title}\"\napplied_via: {via}\nprospects: {json.dumps(list(prospects))}\n---\n"
                    f"# {company}\n\n## Events\n{lines}\n", encoding="utf-8")


@pytest.fixture
def js(tmp_path):
    w(tmp_path / "targets" / "companies.yaml", """
        companies:
          - {name: Orbital Labs, slug: orbital, tier: dream, ats_vendor: greenhouse, segment: ai-lab}
          - {name: Globex, slug: globex, tier: strong, ats_vendor: unknown, segment: big-tech}
          - {name: Initech, slug: initech, tier: good, ats_vendor: unknown, segment: big-tech}
          - {name: Acme Startup, slug: acme, tier: good, ats_vendor: unknown, segment: startup}
        """)
    app(tmp_path, "orbital-x", "Orbital Labs", "Applied AI Engineer", 4.5,
        [("2026-09-30T18:00:00Z", "applied")])
    app(tmp_path, "globex-y", "Globex", "Software Engineer, AI", 4.2,
        [("2026-09-10T17:00:00Z", "applied"), ("2026-09-12T17:00:00Z", "email_in"),
         ("2026-09-15T17:00:00Z", "screen"), ("2026-09-20T17:00:00Z", "rejected")])
    app(tmp_path, "acme-z", "Acme Startup", "Founding Engineer", 4.1,
        [("2026-09-01T17:00:00Z", "applied")], prospects=["lee-ref"])
    for n in range(15):
        app(tmp_path, f"initech-{n}", "Initech", "ML Engineer", 3.6, [(f"2026-09-{20 + n % 9:02d}T17:00:00Z", "applied")])
    w(tmp_path / "prospects" / "lee-ref.md", """
        ---
        type: prospect
        id: lee-ref
        name: Lee Ref
        company: Acme Startup
        role: engineer
        tags: [referrer]
        ---
        """)
    w(tmp_path / "state" / "apply-batches" / "2026-09-20-1.md", """
        ---
        type: apply-batch
        tier: mass
        slugs: [globex-y, initech-0, initech-1, initech-2, initech-3, initech-4, initech-5, initech-6, initech-7, initech-8, initech-9, initech-10,
                initech-11, initech-12, initech-13, initech-14]
        ---
        """)
    w(tmp_path / "state" / "apply-batches" / "2026-09-29-1.md", """
        ---
        type: apply-batch
        tier: dream
        slugs: [orbital-x]
        ---
        """)
    return tmp_path


def test_windows_count_applications_and_outcomes_by_applied_date(js):
    r = sr.review(js, TODAY)
    y, d7, d30 = r["windows"]["yesterday"], r["windows"]["7d"], r["windows"]["30d"]
    assert y["totals"]["applications"] == 1
    assert d30["totals"] == {"applications": 18, "replies": 1, "screens": 1, "interviews": 0, "rejections": 1,
                             "silent_10bd": 1}
    assert d7["totals"]["applications"] == sum(1 for n in range(15) if 20 + n % 9 >= 24) + 1
    assert d30["time_to_first_reply_days"] == {"median": 2.0, "measured": 1}
    assert d30["fit_4_plus_share"] == pytest.approx(3 / 18, abs=0.001)


def test_buckets_by_tier_segment_focus_channel_and_fit_band(js):
    b = sr.review(js, TODAY)["windows"]["30d"]["by"]
    assert b["tier"]["mass"]["applications"] == 16 and b["tier"]["dream"]["applications"] == 1
    assert b["tier"]["focused (inferred)"]["applications"] == 1               # acme-z: no batch sheet, fit 4.1
    assert b["segment"]["big-tech"]["applications"] == 16 and b["segment"]["ai-lab"]["applications"] == 1
    assert b["segment"]["startup"]["applications"] == 1                       # companies.yaml segment wins
    assert b["channel"]["referral"]["applications"] == 1 and b["channel"]["cold apply"]["applications"] == 17
    assert b["fit_band"]["3.5-4.0"] == {"applications": 15, "replies": 0, "screens": 0, "interviews": 0,
                                        "rejections": 0, "silent_10bd": 0}
    assert "unknown" in b["focus"]                                             # no lens files in the fixture


def test_flags_carry_the_number_behind_them(js):
    flags = {f["rule"]: f for f in sr.review(js, TODAY)["flags"]}
    assert "15 tries, 0 replies" in flags["no-replies"]["detail"] and "fit_band 3.5-4.0" in flags["no-replies"]["detail"]
    assert flags["dream-share"]["value"] == pytest.approx(1 / 18, abs=0.001)
    assert flags["big-company-share"]["value"] == pytest.approx(16 / 18, abs=0.001)
    assert flags["median-fit"]["value"] == 3.6
    assert "no-interview-by-day-21" not in flags                               # no clock.yaml: nothing to judge
    assert sr.review(js, TODAY)["clock"] == {"state": "absent"}


def test_the_day_21_flag_reads_the_clock(js):
    w(js / "clock.yaml", """
        employment_end: 2026-09-05
        employment_end_confirmed: true
        clock_days: 90
        basis: "fixture"
        new_employment_start: null
        visa_allowance_days: null
        visa_allowance_source: null
        phases:
          - {weeks: [1, 13], name: all}
        """)
    flags = {f["rule"]: f for f in sr.review(js, TODAY)["flags"]}
    assert flags["no-interview-by-day-21"]["value"] == 26
    (js / "clock.yaml").write_text("employment_end: 2026-09-05\n")                    # unreadable: said, not hidden
    assert sr.review(js, TODAY)["clock"]["error"].startswith("clock.yaml:")


def test_the_review_file_and_json_and_no_record_written(js, capsys):
    before = {p: p.read_bytes() for p in js.rglob("*") if p.is_file()}
    assert sr.main(["--root", str(js), "--today", "2026-10-01"]) == 0
    text = (js / "state" / "recruiter" / "review-2026-10-01.md").read_text()
    assert text.startswith("# Strategy review 2026-10-01") and "## Flags" in text and "## Last 30 days" in text
    changed = {p for p in js.rglob("*") if p.is_file() and before.get(p) != p.read_bytes()}
    assert changed == {js / "state" / "recruiter" / "review-2026-10-01.md",       # and the weight proposal
                       js / "state" / "recruiter" / "proposals-2026-10-01.md"}
    assert not (js / "targets" / "hunt.yaml").exists()                         # a proposal, never applied
    capsys.readouterr()
    assert sr.main(["--root", str(js), "--today", "2026-10-01", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)                                # --json prints only the JSON
    assert data["windows"]["30d"]["totals"]["applications"] == 18 and data["date"] == "2026-10-01"


def test_tier_inference_follows_orchs_rule_and_is_marked(js):
    import strategy_review as sr2
    assert sr2.infer_tier(4.6, False) == "dream (inferred)" and sr2.infer_tier(3.0, True) == "dream (inferred)"
    assert sr2.infer_tier(4.2, False) == "focused (inferred)" and sr2.infer_tier(3.9, False) == "mass (inferred)"
    assert sr2.infer_tier(None, False) == "unknown"


def test_a_company_off_the_watchlist_takes_its_segment_from_recorded_evidence(js):
    (js / "companies").mkdir(exist_ok=True)
    (js / "companies" / "tinyco.md").write_text("---\ntype: company\nslug: tinyco\nname: TinyCo\nstage: seed\n---\n")
    app(js, "tinyco-q", "TinyCo", "AI Engineer", 4.0, [("2026-09-25T17:00:00Z", "applied")])
    b = sr.review(js, TODAY)["windows"]["30d"]["by"]["segment"]
    assert b["startup"]["applications"] == 2                                   # acme (companies.yaml) + tinyco (dossier)


def test_the_review_leads_with_interviews_and_only_proposes(monkeypatch, tmp_path):
    """Interviews booked against target, measured channel rates, and proposals that are never applied."""
    import interviews as iv
    monkeypatch.setattr(iv, "rates", lambda root, now: {"warm": 0.25, "cold": 0.03, "warm_k": 2, "warm_n": 2,
                                                         "cold_k": 0, "cold_n": 24})
    monkeypatch.setattr(iv, "booked", lambda root, day: {"this_week": 2, "last_week": 0, "week_of": "2026-09-28",
                                                         "items": []})
    monkeypatch.setattr(iv, "target", lambda root: None)
    from datetime import date
    v = sr.interviews_view(tmp_path, date(2026, 10, 1), {"totals": {"applications": 26}})
    assert v["warm_vs_cold"] is None and len(v["proposals"]) == 2            # n=2 drives no multiplier
    assert not any("worth about" in p for p in v["proposals"]) and "person 2 of 2, cold 0 of 24" in v["small_n"]
