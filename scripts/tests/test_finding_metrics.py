"""finding_metrics.py: how complete and how early our role finding is. Coverage of the in-band roles we learn
about outside our boards (alert mail, platform cards, hiring posts) against targets/companies.yaml, the employers that
would close the gap, the lead time of a polled board over alert mail, and the hours from first sighting to applied."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import FakeHttp

import fetch_ats as fa  # noqa: E402
import finding_metrics as fm  # noqa: E402

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
TF = fa.TitleFilter(include=[re.compile(r"\b(ai|ml|machine learning)\b.*\bengineer\b")], exclude=[])
NOT_FOUND = fa.FetchError("404", status=404)
DAY = date(2026, 10, 5)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def job(root, company, slug, title, source, url, seen, *, platform=None, received=None, location="San Francisco, CA",
        gate="pass", reasons=(), posted=None, dup=None, jid=None):
    rec = fa.make_record({"name": company, "slug": slug}, title=title, source=source, source_url=url, apply_url=url,
                         ats_vendor="unknown", posted_at=posted, location=location, remote="onsite",
                         comp=(None, None, None), employment_type="full-time", jd_text="", now=seen)
    rec.update(first_seen=iso(seen), last_seen=iso(seen), gate=gate, gate_reasons=list(reasons))
    if jid:
        rec["id"] = jid
    if platform:
        rec["platform"] = platform
    if received:
        rec["alert_received_at"] = iso(received)
    if dup:
        rec["duplicate_of"] = dup
    fa.save_job(root, rec)
    return rec["id"]


def world(root):
    (root / "targets").mkdir(parents=True)
    (root / "targets" / "companies.yaml").write_text(
        "companies:\n"
        "  - {name: Acme, slug: acme, tier: good, ats_vendor: greenhouse, board_token: acme}\n"
        "  - {name: Globex, slug: globex, tier: good, ats_vendor: workday}\n", encoding="utf-8")
    gh = "https://job-boards.greenhouse.io/acme/jobs/"
    # Acme's board: its first sweep at T0, then a role first seen 2 days later
    job(root, "Acme", "acme", "Data Analyst", "greenhouse", gh + "1", T0, jid="acme-1")
    job(root, "Acme", "acme", "AI Engineer", "greenhouse", gh + "2", T0 + timedelta(days=2), jid="acme-2",
        posted="2026-09-30")
    job(root, "Acme", "acme", "ML Engineer, Search", "greenhouse", gh + "3", T0 + timedelta(minutes=10), jid="acme-3")
    # alerts for the board roles: 30 h after the board (title match), and one folded onto acme-3 by dedup, 5 h before
    job(root, "Acme", "acme", "AI Engineer", "linkedin-alert", "https://www.linkedin.com/jobs/view/11/",
        T0 + timedelta(days=4), platform="linkedin", received=T0 + timedelta(days=2, hours=30), jid="acme-li-11")
    job(root, "Acme Inc", "acme-inc", "ML Engineer - Search", "ziprecruiter-alert", "https://www.ziprecruiter.com/k/12",
        T0, platform="ziprecruiter", received=T0 - timedelta(hours=5), dup="acme-3", jid="acme-zr-12")
    # an Acme role no board has shown
    job(root, "Acme", "acme", "Applied AI Engineer", "linkedin-alert", "https://www.linkedin.com/jobs/view/13/",
        T0, platform="linkedin", received=T0, jid="acme-li-13")
    # Initech: not a target; a LinkedIn and a ZipRecruiter card for one role, a second role, an Ashby link in a URL
    job(root, "Initech", "initech", "AI Engineer", "linkedin-alert", "https://www.linkedin.com/jobs/view/21/", T0,
        platform="linkedin", received=T0, jid="initech-li-21")
    job(root, "Initech", "initech", "AI Engineer", "ziprecruiter-alert", "https://www.ziprecruiter.com/k/22",
        T0 + timedelta(hours=3), platform="ziprecruiter", received=T0 + timedelta(hours=3), jid="initech-zr-22")
    job(root, "Initech", "initech", "ML Engineer", "manual", "https://jobs.ashbyhq.com/initech/abc", T0, platform="linkedin",
        jid="initech-man-23")
    # Solo: not a target, one role
    job(root, "Solo", "solo", "AI Engineer", "glassdoor-alert", "https://www.glassdoor.com/j?jl=31", T0,
        platform="glassdoor", received=T0, jid="solo-gd-31")
    # out of band: a staffing agency, an off-title role, a location fail; and a name extending a target
    job(root, "Example Staffing Solutions", "example-staffing-solutions", "AI Engineer", "linkedin-alert", "https://www.linkedin.com/jobs/view/41/",
        T0, platform="linkedin", received=T0, jid="rh-li-41")
    job(root, "Initech", "initech", "Account Executive", "linkedin-alert", "https://www.linkedin.com/jobs/view/42/", T0,
        platform="linkedin", received=T0, jid="initech-li-42")
    job(root, "Faraway", "faraway", "AI Engineer", "linkedin-alert", "https://www.linkedin.com/jobs/view/43/", T0,
        platform="linkedin", received=T0, gate="fail", reasons=["location: outside the US"], jid="far-li-43")
    job(root, "Globex AI", "globex-ai", "ML Engineer", "linkedin-alert", "https://www.linkedin.com/jobs/view/44/", T0,
        platform="linkedin", received=T0, jid="gxai-li-44")
    posts = root / "state" / "posts"
    posts.mkdir(parents=True)
    rows = [{"key": "x:1", "hiring": True, "in_band": True, "company": "Solo", "company_slug": "solo",
             "role_title": "Founding AI Engineer", "date": "2026-10-02"},
            {"key": "x:2", "hiring": True, "in_band": True, "company": None, "company_slug": None,
             "role_title": "AI Engineer", "date": "2026-10-02"},
            {"key": "x:3", "hiring": False, "in_band": False, "company": "Sidecar", "role_title": "AI Engineer",
             "not_hiring": "commentary"},
            {"key": "x:1", "made": True, "job": None}]
    (posts / "records.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return root


def application(root, slug, job_id, applied_at, event_at=None):
    folder = root / "applications" / slug
    folder.mkdir(parents=True)
    events = f"- {event_at} · applied · manual · conf=1.00 · confirmation\n" if event_at else ""
    folder.joinpath("application.md").write_text(
        f"---\ntype: application\nid: {slug}\njob: '[[{job_id}]]'\nstage: applied\napplied_at: '{applied_at}'\n---\n"
        f"# x\n\n## Events\n{events}", encoding="utf-8")


def test_coverage_counts_roles_not_cards_and_filters_out_of_band(root):
    r = fm.report(world(root), DAY, title_filter=TF)
    c = r["coverage"]
    # roles: acme AI Engineer, acme ML Engineer Search (folded on the board title), acme Applied AI Engineer,
    # initech AI Engineer (two cards), initech ML Engineer, solo AI Engineer, solo Founding AI Engineer (post), globex-ai
    assert c["roles"] == 8
    assert (c["at_targets"], c["at_targets_on_board"], c["at_targets_not_on_board"]) == (3, 2, 1)
    assert c["not_on_board_roles"] == [{"employer": "acme", "title": "Applied AI Engineer"}]
    assert (c["name_extends_target"], c["name_extends_list"]) == (1, [("globex-ai", "globex")])
    assert (c["not_on_watchlist"], c["employers_not_on_watchlist"], c["gap_share"]) == (4, 2, 0.5)
    assert c["posts_without_employer"] == 1
    assert c["excluded"] == {"employer gate": 1, "title filter": 1, "gate location": 1,
                             "post: not an in-band hiring post": 1}
    assert c["by_channel"]["post"] == {"roles": 1, "not_on_watchlist": 1}


def test_missing_employers_are_ranked_with_a_board_from_a_job_url(root):
    r = fm.report(world(root), DAY, title_filter=TF)
    rows = r["missing_employers"]
    assert [m["employer"] for m in rows] == ["initech", "solo"]
    initech = rows[0]
    assert initech["in_band_roles"] == 2 and initech["channels"] == {"alert": 2, "platform": 1}
    assert initech["board"] == {"vendor": "ashby", "token": "initech", "found_by": "job URL"}
    assert rows[1]["board"] is None and rows[1]["channels"] == {"alert": 1, "post": 1}
    assert r["coverage"]["top_close"] == {"employers": 2, "roles": 4, "share_of_gap": 1.0, "with_board": 1}
    assert r["requests"] == 0


def test_lead_time_pairs_board_and_alert_and_marks_the_first_sweep(root):
    lt = fm.report(world(root), DAY, title_filter=TF)["lead_time"]
    rows = {x["alert"]: x for x in lt["rows"]}
    assert set(rows) == {"acme-li-11", "acme-zr-12"}
    later = rows["acme-li-11"]
    assert (later["board"], later["lead_hours"], later["first_sweep"], later["matched_by"]) == \
        ("acme-2", 30.0, False, "employer and title")
    assert later["posted_to_alert_days"] == 4.8                    # posted 09-30 00:00Z, alert 10-04 18:00Z
    folded = rows["acme-zr-12"]
    assert (folded["board"], folded["lead_hours"], folded["first_sweep"], folded["matched_by"]) == \
        ("acme-3", -5.2, True, "dedup")
    assert (lt["pairs"], lt["board_first"], lt["alert_first"]) == (2, 1, 1)
    assert lt["after_first_sweep"]["n"] == 1 and lt["after_first_sweep"]["median"] == 30.0
    assert lt["distribution"]["board 1 to 3 days first"] == 1 and lt["distribution"]["alert up to 1 day first"] == 1


def test_freshness_runs_from_the_earliest_sighting_of_the_duplicate_group(root):
    world(root)
    application(root, "acme-ml", "acme-3", "2026-10-02", "2026-10-02T00:00:00Z")         # group sighted T0 - 5 h
    application(root, "initech-ai", "initech-li-21", "2026-10-01", None)                      # day only: noon Pacific
    application(root, "solo-ai", "solo-gd-31", "2026-09-30", "2026-09-30T12:00:00Z")      # applied before the record
    application(root, "gone", "no-such-job", "2026-10-01", "2026-10-01T12:00:00Z")
    fr = fm.report(root, DAY, title_filter=TF)["freshness"]
    rows = {x["application"]: x for x in fr["rows"]}
    assert rows["acme-ml"]["hours_from_first_sighting"] == 17.0
    assert rows["initech-ai"]["precision"] == "day" and rows["initech-ai"]["hours_from_first_sighting"] == 7.0
    assert rows["solo-ai"]["hours_from_first_sighting"] == -24.0
    assert (fr["applied"], fr["measured"], fr["record_after_apply"], fr["no_job_record"]) == (4, 2, 1, ["gone"])
    assert fr["within_72h"] == 2 and fr["from_first_sighting"]["median"] == 12.0
    assert fr["distribution"]["within 24 h"] == 2
    assert rows["acme-ml"]["posted_to_first_seen_hours"] is None                 # acme-3 carries no posted date
    assert fr["posted_to_first_seen"]["n"] == 0


def test_probes_spend_at_most_the_budget_and_never_without_one(root):
    world(root)
    routes = {("GET", fm_url("greenhouse", "solo")): NOT_FOUND,
              ("GET", fm_url("lever", "solo")): [{"text": "AI Engineer"}, {"text": "Designer"}]}
    http = FakeHttp(routes)
    r = fm.report(root, DAY, probe_budget=2, http=http, title_filter=TF)
    assert len(http.calls) == 2 and r["requests"] == 2
    solo = next(m for m in r["missing_employers"] if m["employer"] == "solo")
    assert solo["board"] == {"vendor": "lever", "token": "solo", "roles": 2, "in_band_roles": 1,
                             "found_by": "name probe: verify it is this company"}
    http0 = FakeHttp({})
    assert fm.report(root, DAY, http=http0, title_filter=TF)["requests"] == 0 and http0.calls == []


def test_a_short_budget_stops_mid_company_without_a_false_verdict(root):
    world(root)
    http = FakeHttp({("GET", fm_url("greenhouse", "solo")): NOT_FOUND})
    r = fm.report(root, DAY, probe_budget=1, http=http, title_filter=TF)
    solo = next(m for m in r["missing_employers"] if m["employer"] == "solo")
    assert len(http.calls) == 1 and solo["board"] is None          # not "no board": the probes were not finished


def fm_url(vendor, token):
    import discover_companies as dc
    return dict(dc.VENDORS)[vendor].format(token=token)


def test_write_saves_the_report_and_the_same_numbers_as_json(root, capsys):
    world(root)
    assert fm.main(["--root", str(root), "--today", "2026-10-05", "--write"]) == 0
    md = (root / "state" / "reports" / "finding-2026-10-05.md").read_text(encoding="utf-8")
    data = json.loads((root / "state" / "reports" / "finding-2026-10-05.json").read_text(encoding="utf-8"))
    assert data["date"] == "2026-10-05" and data["coverage"]["roles"] >= 1
    assert f"**{data['coverage']['roles']}**" in md and "## Lead time" in md and "## Freshness at action" in md
    assert chr(0x2014) not in md                                 # no em-dash in the report
    assert fm.main(["--root", str(root), "--today", "2026-10-05", "--json"]) == 0
    printed = capsys.readouterr().out
    assert json.loads(printed[printed.index("{"):])["coverage"] == data["coverage"]


def test_usage_errors_exit_2(root):
    assert fm.main(["--root", str(root), "--top", "0"]) == 2
    assert fm.main(["--root", str(root), "--probe-budget", "-1"]) == 2
