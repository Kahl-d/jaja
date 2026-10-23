"""hn_hiring.py: latest "Ask HN: Who is hiring?" thread -> candidate job records ."""
from __future__ import annotations

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW, FakeHttp, hn_routes, load_fixture

import hn_hiring as hn  # noqa: E402

BASE = "https://hacker-news.firebaseio.com/v0"


def _records(**kw):
    records, info = hn.collect(FakeHttp(hn_routes()), NOW, **kw)
    return {r["id"]: r for r in records}, info


def test_find_latest_thread_picks_newest_who_is_hiring():
    story = hn.find_latest_thread(FakeHttp(hn_routes()))
    assert story["id"] == 2001
    assert story["title"] == "Ask HN: Who is hiring? (September 2026)"


def test_find_latest_thread_skips_who_wants_to_be_hired():
    routes = hn_routes()
    user = load_fixture("hn_user_whoishiring.json")
    user["submitted"] = [2000, 1001]        # newest is the "wants to be hired" thread
    routes[("GET", f"{BASE}/user/whoishiring.json")] = user
    assert hn.find_latest_thread(FakeHttp(routes))["id"] == 1001


def test_thread_comments_skip_deleted_items():
    http = FakeHttp(hn_routes())
    story = hn.find_latest_thread(http)
    comments = hn.fetch_thread_comments(http, story)
    assert len(comments) == 11 and 2005 not in {c["id"] for c in comments}


def test_parse_carewell_header_fields_and_visa_sponsor_flag():
    rec = hn.parse_comment(load_fixture("hn_item_2003.json"), NOW, thread_id=2001)
    assert rec["id"] == "carewell-hn-2003"
    assert rec["company"] == "Carewell" and rec["title"] == "Machine Learning Engineer"
    assert rec["source"] == "hn"
    assert rec["source_url"] == "https://news.ycombinator.com/item?id=2003"
    assert rec["apply_url"] == "https://carewell.recruitee.com/o/machine-learning-engineer"
    assert rec["posted_at"] == "2026-09-01T15:25:03Z"
    assert rec["location"] == "Amsterdam, Netherlands | ONSITE (hybrid)"
    assert rec["remote"] == "hybrid"
    assert rec["employment_type"] == "full-time"
    assert (rec["comp_min"], rec["comp_max"]) == (None, None)       # monthly euro pay is not annual
    assert rec["sponsorship"] == "yes"
    assert "sponsorship" in rec["sponsorship_evidence"]
    assert rec["hn_thread"] == 2001
    assert "<untrusted_jd>" in rec["_body"]


def test_parse_agencyflow_remote_us_comp_and_citizenship_gate():
    rec = hn.parse_comment(load_fixture("hn_item_2012.json"), NOW)
    assert rec["title"] == "Forward Deployed Engineer (All Levels)"
    assert rec["location"] == "REMOTE (US)" and rec["remote"] == "remote"
    assert (rec["comp_min"], rec["comp_max"], rec["comp_currency"]) == (160000, 220000, "USD")
    assert rec["apply_url"] == "https://careers.agencyflow.example.test/jobs/forward-deployed-engineer"
    assert rec["sponsorship"] == "no"
    assert "U.S. citizenship" in rec["sponsorship_evidence"]


def test_parse_title_from_body_role_line_with_its_own_link():
    rec = hn.parse_comment(load_fixture("hn_item_2009.json"), NOW)      # Fissionworks
    assert rec["company"] == "Fissionworks"
    assert rec["title"] == "Software Engineer (Applied AI/ML)"
    assert rec["apply_url"] == "https://job-boards.greenhouse.io/fissionworks/jobs/100303"
    assert rec["ats_vendor"] == "greenhouse"
    assert rec["remote"] == "remote"
    assert rec["sponsorship"] is None


def test_parse_bullet_role_and_citizens_only_header():
    rec = hn.parse_comment(load_fixture("hn_item_2008.json"), NOW)
    assert rec["company"] == "Globex Gov" and rec["title"] == "AI Engineer"
    assert rec["apply_url"] == "https://www.globex-gov.example.test"
    assert rec["sponsorship"] == "no"


def test_parse_company_strips_domain_and_urls():
    acme = hn.parse_comment(load_fixture("hn_item_2011.json"), NOW)
    assert acme["company"] == "Acme Underwriting Company"
    assert acme["apply_url"] == "https://jobs.ashbyhq.com/acme/role-0001"
    assert acme["ats_vendor"] == "ashby"
    assert (acme["comp_min"], acme["comp_max"]) == (200000, 400000)
    fieldworks = hn.parse_comment(load_fixture("hn_item_2006.json"), NOW)
    assert fieldworks["company"] == "FieldWorks"
    assert fieldworks["title"] == "Senior SWE, AI Automation Engr, Senior PM"


def test_candidate_post_in_hiring_thread_is_not_a_job():
    assert hn.parse_comment(load_fixture("hn_item_2013.json"), NOW) is None


def test_collect_keeps_ai_ml_relevant_posts_only():
    recs, info = _records()
    assert {r["company_slug"] for r in recs.values()} == {
        "carewell", "brightpath-foundation", "fieldworks", "globex-gov", "agencyflow",
        "acme-underwriting", "fissionworks"}   # the normalized slug
    assert info["thread_id"] == 2001 and info["comments"] == 11 and info["relevant"] == 7


def test_collect_all_includes_non_ai_posts():
    recs, info = _records(relevant_only=False)
    assert len(recs) == 10
    assert "contoso" in {r["company_slug"] for r in recs.values()}


def test_cli_dry_run_writes_nothing(root, capsys):
    rc = hn.main(["--root", str(root), "--dry-run"], http=FakeHttp(hn_routes()), now=NOW)
    assert rc == 0
    assert not (root / "jobs").exists()
    assert not (root / "state").exists()
    out = capsys.readouterr().out
    assert "Ask HN: Who is hiring? (September 2026)" in out and "7 relevant" in out


def test_cli_writes_jobs_and_monthly_state(root):
    rc = hn.main(["--root", str(root)], http=FakeHttp(hn_routes()), now=NOW)
    assert rc == 0
    assert len(list((root / "jobs").glob("*.md"))) == 7
    state = hn.load_state(root)
    assert state == {"last_thread_id": 2001, "last_run_month": "2026-09", "last_run": "2026-09-24T13:00:00Z"}


def test_the_company_slug_is_the_normalized_one_every_other_scout_writes():
    # HN alone used to slugify the raw name, so "... Company" kept a suffix the others strip
    acme = hn.parse_comment(load_fixture("hn_item_2011.json"), NOW)
    assert acme["company_slug"] == "acme-underwriting"
    assert acme["id"] == "acme-underwriting-hn-2011"


# ----------------------------------------------------------------------------- jobstories: YC job posts
JOBSTORIES = "https://hacker-news.firebaseio.com/v0/jobstories.json"


def _js_http():
    return FakeHttp({**hn_routes(), ("GET", JOBSTORIES): load_fixture("hn_jobstories.json")["ids"]})


def test_jobstories_turn_yc_job_posts_into_job_records():
    records, info = hn.collect_jobstories(_js_http(), NOW)
    assert info == {"stories": 2, "matched": 1, "errors": 0}
    rec = records[0]
    assert rec["company"] == "Acme AI" and rec["title"] == "Founding AI Engineer"
    assert rec["source"] == "hn" and rec["id"] == "acme-ai-hnjob-3002"
    assert rec["source_url"] == "https://news.ycombinator.com/item?id=3002"
    assert rec["yc_batch"] == "YC W26" and "We sponsor visas." in rec["_jd_text"]
    assert rec["posted_at"]                                          # the item's unix time


def test_jobstories_keep_every_story_with_relevant_only_off_and_tolerate_a_bad_item():
    http = _js_http()
    http.routes[("GET", "https://hacker-news.firebaseio.com/v0/item/3002.json")] = RuntimeError("boom")
    records, info = hn.collect_jobstories(http, NOW, relevant_only=False)
    assert info["errors"] == 1 and info["stories"] == 2 and len(records) == 1
    assert records[0]["title"] == "GTM Founder's Associate" and records[0]["company"] == "Beacon Metrics"
    assert records[0]["source_url"] == "https://news.ycombinator.com/item?id=3001"
    assert records[0]["apply_url"].startswith("https://www.ycombinator.com/companies/beacon-metrics/jobs/")


def test_the_monthly_thread_is_re_read_while_it_fills(tmp_path):
    """The thread fills over its first days, so one read a month misses posts."""
    from datetime import datetime, timezone
    first = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)
    assert hn.due_this_month(tmp_path, first)                              # never read: due
    hn.save_state(tmp_path, 4001, first)
    assert not hn.due_this_month(tmp_path, datetime(2026, 10, 1, 21, 0, tzinfo=timezone.utc))   # 3 h later
    assert hn.due_this_month(tmp_path, datetime(2026, 10, 2, 0, 30, tzinfo=timezone.utc))       # 6.5 h later
    hn.save_state(tmp_path, 4001, datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc))
    assert not hn.due_this_month(tmp_path, datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc))   # after day 10: weekly
    assert hn.due_this_month(tmp_path, datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc))
    assert hn.due_this_month(tmp_path, datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc))        # a new month
