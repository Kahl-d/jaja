"""dashboard/crm.py (the CRM, interviews first). Fixture trees and a stub calendar only: no network, no real records."""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from layout import JS  # noqa: E402
for p in (str(JS / "scripts"), str(JS / "dashboard")):
    if p not in sys.path:
        sys.path.insert(0, p)

import crm  # noqa: E402

NOW = datetime(2026, 10, 2, 4, 30, tzinfo=timezone.utc)          # 2026-10-01 21:30 local


def w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def app(js: Path, slug: str, company: str, events: list[tuple[str, str]], job: str = "", via: str = "ats") -> None:
    lines = "\n".join(f"- {at} · {typ} · manual · conf=1.00 · fixture" for at, typ in events)
    w(js / "applications" / slug / "application.md",
      f"---\ntype: application\nid: {slug}\njob: \"[[{job}]]\"\ncompany: {company}\ntitle: AI Engineer\n"
      f"applied_via: {via}\nstage: discovered\n---\n# {company}\n\n## Events\n{lines}\n")


def job(js: Path, jid: str, years_line: str) -> None:
    w(js / "jobs" / f"{jid}.md", f"---\ntype: job\nid: {jid}\ntitle: AI Engineer\ncompany: X\n---\n"
                                  f"## Job description (snapshot)\n\n<untrusted_jd>\n- {years_line}\n</untrusted_jd>\n")


@pytest.fixture
def tree(tmp_path):
    os_root, js = tmp_path / "os", tmp_path / "js"
    app(js, "zenith-fde", "Zenith Labs", [("2026-09-25T17:00:00Z", "applied"), ("2026-09-29T18:00:00Z", "screen")],
        job="rq-1", via="recruiter")
    app(js, "orion-rd", "Orion", [("2026-09-28T17:00:00Z", "applied")], job="ks-1")
    app(js, "acme-ai", "Acme", [("2026-09-28T17:00:00Z", "applied"), ("2026-09-30T17:00:00Z", "tailored")], job="ac-1")
    app(js, "beta-ai", "Beta", [("2026-10-02T01:00:00Z", "applied")], job="be-1")
    job(js, "rq-1", "2+ years of experience in Python")
    job(js, "ks-1", "4+ years of experience in ML")
    job(js, "ac-1", "6+ years of experience in software")
    job(js, "be-1", "no years stated here")
    w(js / "prep" / "packs" / "zenith-fde" / "pack.md", "x")
    w(js / "hub" / "lanes" / "FOCUSED-ORION.md", "---\nscope: FOCUSED-ORION\nclaims: [orion-rd]\n---\n")
    w(js / "state" / "recruiter" / "stopped.yaml", "stopped:\n  - application: beta-ai\n    by: orchestrator\n")
    w(os_root / "work" / "approvals" / "A-1.md", "---\nid: A-1\nstatus: pending\ncreated: 2026-09-30\nexpires: 2026-10-07\n---\n")
    w(os_root / "work" / "approvals" / "A-2.md", "---\nid: A-2\nstatus: pending\ncreated: 2026-09-20\nexpires: 2026-09-27\n---\n")
    w(os_root / "work" / "approvals" / "A-3.md", "---\nid: A-3\nstatus: approved\ncreated: 2026-09-30\n---\n")
    return os_root, js


def snapshot(js: Path) -> dict:
    stage = {"zenith-fde": "screen", "orion-rd": "applied", "acme-ai": "applied", "beta-ai": "applied"}
    people = {"orion-rd": [{"id": "pat-quinn-roe", "name": "Pat Quinn Roe", "via": "prospect"}]}
    jobs = {"zenith-fde": "rq-1", "orion-rd": "ks-1", "acme-ai": "ac-1", "beta-ai": "be-1"}
    company = {"zenith-fde": "Zenith Labs", "orion-rd": "Orion", "acme-ai": "Acme", "beta-ai": "Beta"}
    apps = [{"slug": s, "company": company[s], "company_slug": company[s].lower(), "title": "AI Engineer", "stage": st, "job": jobs[s], "people": people.get(s, []),
             "applied_via": "recruiter" if s == "zenith-fde" else "ats", "next_action": "follow up", "next_action_at": "2026-10-01",
             "fit": 4.2, "tier": "focused", "segment": "startup", "link": None} for s, st in stage.items()]
    return {"applications": apps, "clock": {"line": "Job clock: day -1 of 90", "clock_start": "2026-10-03"},
            "overview": {"funnel": [{"step": "applied", "count": 4}, {"step": "screen", "count": 1}],
                         "weeks": {"rows": [{"metric": "applied", "this": 4, "delta": 4}]},
                         "apply_latency_hours": {"median": 48, "measured": 4, "within_72h": 3}},
            "follow_ups": [{"company": "Orion", "next": "answer owed (email_in): reply", "due": "2026-10-01"},
                           {"company": "", "who": "venu@spam.example.test", "next": "answer owed (recruiter_outreach)", "due": "2026-09-01"}]}


CAL = [{"start": "2026-10-01T14:00:00-07:00", "end": "2026-10-01T14:30:00-07:00", "title": "Old call with Sam"},
       {"start": "2026-10-02T10:00:00-07:00", "end": "2026-10-02T10:30:00-07:00", "title": "Zenith Labs <> Alex Example"},
       {"start": "2026-10-02T11:30:00-07:00", "end": "2026-10-02T12:00:00-07:00", "title": "Pat / Alex: Chat"}]


def build(tree, calendar=lambda a, b: CAL, target=None):
    os_root, js = tree
    if target:
        w(js / "state" / "recruiter" / "target.yaml", f"weekly_interviews: {target}\n")
    pat = [{"id": "pat-quinn-roe", "name": "Pat Quinn Roe", "warmth": 2, "company_slug": "orion"}]
    return crm.build(js, os_root, NOW, snapshot(js), prospects=pat, calendar=calendar)


def test_1_kpi_one_is_interviews_against_the_target_and_its_drawer_names_them(tree):
    k = build(tree)["kpis"]
    assert k[0]["key"] == "interviews" and k[1]["key"] == "applied" and k[0]["note"] == "target not set"
    assert k[0]["value"] == 1 and k[0]["items"][0]["company"] == "Zenith Labs" and k[0]["items"][0]["via"] == "recruiter"
    assert build(tree, target=3)["kpis"][0]["note"] == "target 3"
    assert "1 of 1 (small sample)" in k[2]["note"]                      # every rate carries its n
    assert k[2]["key"] == "interview_rate" and k[2]["label"] == "Interviews per application, warm vs cold"


def test_2_calendar_rounds_carry_pt_times_and_a_failed_read_is_missing_not_zero(tree):
    b = build(tree)["band48"]
    assert b["calendar_read"] is True
    cal = [r for r in b["rounds"] if r["source"] == "calendar"]
    assert [(r["when"], r["company"]) for r in cal] == [("2026-10-02 10:00", "Zenith Labs"), ("2026-10-02 11:30", "Orion")]
    assert cal[0]["pack"] is True and cal[1]["pack"] is False             # Pat matched to his deal by name
    assert not [r for r in b["rounds"] if "Sam" in str(r["title"])]   # already over
    miss = build(tree, calendar=lambda a, b: None)["band48"]
    assert miss["calendar_read"] is False
    # a round with no round_at on record is a data gap, never a coming round
    assert miss["rounds"] == [] and [r["stage"] for r in miss["rounds_undated"]] == ["screen"]


def test_3_times_are_pacific():
    assert crm.pt("2026-10-02T01:00:00Z") == "10-01 18:00" and crm.pt("2026-10-02") == "10-02"


def test_3b_updates_show_pt_times(tree):
    u = build(tree)["updates"]
    assert u[0]["company"] == "Beta" and u[0]["at_pt"] == "10-01 18:00"


def test_4_approvals_count_only_pending_and_unexpired(tree):
    b = build(tree)["band48"]
    assert b["approvals"] == 1 and [a["id"] for a in b["approvals_expired"]] == ["A-2"]


def test_5_replies_count_only_pipeline_contacts(tree):
    b = build(tree)["band48"]
    assert b["replies_waiting"] == 1 and len(b["replies_other"]) == 1


def test_6_no_dream_share_line_until_ruled(tree):
    assert [f["key"] for f in build(tree)["findings"]] == ["warm-vs-cold", "speed"]
    assert all(f["enough"] is False for f in build(tree)["findings"])     # n under 10: too few to say
    warm = next(f for f in build(tree)["findings"] if f["key"] == "warm-vs-cold")
    assert "reached an interview" in warm["text"] and "replied" not in warm["text"]


def test_7_days_in_stage_come_from_the_entering_event(tree):
    deals = {d["slug"]: d for d in build(tree)["pipeline"]}
    assert deals["acme-ai"]["days_in_stage"] == 3 and deals["acme-ai"]["since"] == "2026-09-28"   # the later 'tailored' is bookkeeping
    assert deals["beta-ai"]["days_in_stage"] == 0 and deals["zenith-fde"]["since"] == "2026-09-29"


def test_8_years_with_stretch_and_out(tree):
    deals = {d["slug"]: d for d in build(tree)["pipeline"]}
    assert (deals["zenith-fde"]["years"], deals["zenith-fde"]["years_flag"]) == (2, None)
    assert (deals["orion-rd"]["years"], deals["orion-rd"]["years_flag"]) == (4, "STRETCH")
    assert (deals["acme-ai"]["years"], deals["acme-ai"]["years_flag"]) == (6, "OUT")
    assert deals["beta-ai"]["years"] is None


def test_8b_only_a_warm_person_makes_a_stretch_k243(tree):
    # a person inside means a warm path by fresh10's test (warmth 1+ or a referrer, never do_not_contact); a cold
    # or blocked prospect attached to the deal is not one
    os_root, js = tree
    snap = snapshot(js)
    for a in snap["applications"]:
        if a["slug"] == "acme-ai":
            a["people"] = [{"id": "cold-one", "name": "Cold One", "via": "application"}, "blocked-one"]
        if a["slug"] == "orion-rd":
            a["people"] = ["pat-quinn-roe"]
    prospects = [{"id": "cold-one", "name": "Cold One", "warmth": 0},
                 {"id": "blocked-one", "name": "Blocked One", "warmth": 3, "status": "do_not_contact"},
                 {"id": "pat-quinn-roe", "name": "Pat Quinn Roe", "warmth": 2, "company_slug": "orion"}]
    deals = {d["slug"]: d for d in crm.build(js, os_root, NOW, snap, prospects=prospects, calendar=lambda a, b: CAL)["pipeline"]}
    assert deals["acme-ai"]["years_flag"] == "OUT"
    assert deals["orion-rd"]["years_flag"] == "STRETCH"
    assert crm.deal_warmth(["r"], {"r": {"tags": ["referrer"], "company_slug": "acme"}}, {"company_slug": "acme"}) == "warm"


def test_9_the_mode_line_is_in_the_header_once(tree):
    c = build(tree)
    assert c["header"]["mode"] == "Applications: focused lanes only"
    assert not [k for k in c["kpis"] if k["key"] == "days_left"]          # days left lives in the header line


def test_10_lane_warm_names_and_stops_on_the_cards(tree):
    deals = {d["slug"]: d for d in build(tree)["pipeline"]}
    assert deals["orion-rd"]["lane"] == "FOCUSED-ORION" and deals["orion-rd"]["warm"] == ["Pat Quinn Roe"]
    assert deals["beta-ai"]["stopped"] is True and deals["acme-ai"]["stopped"] is False


def test_tasks_are_rounds_with_a_deal_then_approvals_and_follow_ups(tree):
    t = [x["what"] for x in build(tree)["tasks"]]
    assert t[0] == "Prep: Zenith Labs 2026-10-02 10:00 (prep pack)" and t[1] == "Prep: Orion 2026-10-02 11:30 (no prep pack)"
    assert "Decide 1 approvals waiting" in t and not [x for x in t if "Sam" in x]


def test_a_fixture_tree_never_reads_the_live_calendar(tree):
    os_root, js = tree
    assert crm.build(js, os_root, NOW, snapshot(js))["band48"]["calendar_read"] is False


def test_r_s4_a_company_off_the_watchlist_takes_its_recorded_evidence(tmp_path):
    import dash_api
    js = tmp_path / "js"
    w(js / "targets" / "companies.yaml", "companies:\n  - {name: Known, slug: known, tier: good}\n")
    w(js / "jobs" / "newco-1.md", "---\ntype: job\nid: newco-1\ncompany: Newco\ncompany_slug: newco\ntitle: AI Engineer\n---\n"
                                  "## Job description (snapshot)\n\n<untrusted_jd>\nWe are a seed-stage team.\n</untrusted_jd>\n")
    seg, src = dash_api.Companies(js, [], {}).segment("newco", "Newco")
    assert seg == "startup" and src.startswith("jobs/newco-1.md")
    assert dash_api.Companies(js, [], {}).segment("ghost", "Ghost Co") == ("unknown", "none")    # never guessed


P8C = {p["id"]: p for p in [
    {"id": "keana", "name": "Keana", "warmth": 2, "company_slug": "corvid"},           # a warm outsider
    {"id": "hm", "name": "Hiring Manager", "warmth": 0, "company_slug": "brightwell", "status": "triaged"},
    {"id": "dnc", "name": "Blocked", "warmth": 3, "company_slug": "brightwell", "status": "do_not_contact"},
    {"id": "saurav", "name": "Saurav", "warmth": 0, "company_slug": "brightwell", "status": "triaged"},
    {"id": "replier", "name": "Replier", "warmth": 0, "company_slug": "brightwell", "status": "replied"},
    {"id": "chris", "name": "Chris", "warmth": 3, "company": "Oldco AI"},           # no slug: company text rules
    {"id": "friend", "name": "Friend", "warmth": 2, "company": "Brightwell, Inc."},
    {"id": "nobody", "name": "Nobody", "warmth": 2}]}                                    # no company on record
DEAL = {"company_slug": "brightwell", "company": "Brightwell", "stage": "applied"}


def _via(pid, pursuit):
    return {"id": pid, "name": P8C[pid]["name"], "via": f"pursuit {pursuit}"}


def test_8c_an_outside_introducer_counts_once_the_intro_is_made_k243():
    # a warm outsider counts only once the introduction to someone at the company is
    # made: a named insider (on the same pursuit), an insider's reply, or the company's reply (screen or later).
    # Before that the deal is cold and says "intro pending". Missing data is never inside (S-29 review 1 to 4).
    w = lambda entries, deal=DEAL: crm.deal_warmth(entries, P8C, deal)
    assert w([]) is None and w(["hm"]) is None and w(["dnc"]) is None
    assert w(["friend"]) == "warm"                                          # warm, at the company by its name
    assert w(["keana"]) == "intro pending"
    assert w(["keana", "hm"]) == "intro pending"                            # a cold research target is no intro
    assert w(["keana", "dnc"]) == "intro pending"
    assert w([_via("keana", "p1"), _via("dnc", "p1")]) == "intro pending"   # never through do_not_contact
    assert w([_via("keana", "p1"), _via("saurav", "p1")]) == "warm"         # the Fenwick shape: a named insider
    assert w([_via("keana", "p1"), _via("saurav", "p2")]) == "intro pending"
    assert w(["keana", "replier"]) == "warm"                                # the insider replied
    for stage in sorted(crm.ROUND_STAGES | {"offer"}):                       # the company replied (final too)
        assert w(["keana"], {**DEAL, "stage": stage}) == "warm", stage
    assert w(["chris"]) == "intro pending" and w(["nobody"]) == "intro pending"
    assert w(["keana"], {"company_slug": None, "company": None}) == "intro pending"
    assert w(["friend"], {"company_slug": None, "company": None}) == "intro pending"
    assert crm._warm_names(["keana", "dnc", "hm"], P8C, "intro pending") == ["Keana (intro pending)", "Blocked", "Hiring Manager"]
    assert crm._warm_names(["keana"], P8C, "warm") == ["Keana"]


def test_8d_the_deal_card_carries_intro_pending(tree):
    os_root, js = tree
    snap = snapshot(js)
    for a in snap["applications"]:
        a["company_slug"] = a["company"].lower()
        if a["slug"] == "acme-ai":
            a["people"] = ["keana"]
    deals = {d["slug"]: d for d in crm.build(js, os_root, NOW, snap, prospects=list(P8C.values()),
                                              calendar=lambda a, b: CAL)["pipeline"]}
    assert deals["acme-ai"]["years_flag"] == "OUT" and deals["acme-ai"]["intro"] == "intro pending"
    assert deals["acme-ai"]["warm"] == ["Keana (intro pending)"]
