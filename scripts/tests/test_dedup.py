"""dedup.py: keys, cross-source duplicates, repost detection, append-only ledger, ghost score."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW, FakeHttp, ats_routes

import dedup  # noqa: E402
import fetch_ats as fa  # noqa: E402


def rec(id, *, company="Labco", title="Research Engineer", location="San Francisco, CA",
        source="greenhouse", req_id=None, jd="a" * 40, first_seen="2026-09-24T13:00:00Z",
        posted_at=None, status="discovered", slug=None, **extra):
    r = {"id": id, "company": company, "company_slug": slug or company.lower().replace(" ", "-"),
         "title": title, "location": location, "source": source, "req_id": req_id, "jd_hash": jd,
         "first_seen": first_seen, "posted_at": posted_at, "status": status}
    r.update(extra)
    return r


def row(id, at, *, ct=None, jd=None, event="first_seen", slug="labco"):
    return {"at": at, "event": event, "id": id, "company_slug": slug, "ct": ct, "ctl": None, "req": None, "jd": jd}


# ----------------------------------------------------------------------------- keys
def test_keys_ignore_formatting_noise():
    a = dedup.keys(rec("a", company="Labco, PBC", title="Sr. Research Engineer (Remote)",
                       location="San Francisco, CA"))
    b = dedup.keys(rec("b", company="Labco", title="Senior Research Engineer",
                       location="San Francisco, California"))
    assert a["ct"] == b["ct"] and a["ctl"] == b["ctl"]
    c = dedup.keys(rec("c", title="Senior Research Engineer", location="New York City, NY"))
    assert c["ct"] == b["ct"] and c["ctl"] != b["ctl"]


@pytest.mark.parametrize("raw,norm", [
    ("San Francisco, CA | New York City, NY", "new york|san francisco"),
    ("US, CA, Santa Clara | US, CA, Remote", "remote:us ca|santa clara"),
    ("Onsite (San Francisco)", "san francisco"),
    ("Remote (US) or Santa Clara or Brooklyn", "brooklyn|remote:us|santa clara"),
    ("United Kingdom (Remote)", "remote:united kingdom"),
    ("Remote", "remote"),
    ("NYC; SF", "new york|san francisco"),
    (None, ""),
])
def test_normalize_location(raw, norm):
    assert dedup.normalize_location(raw) == norm


def test_remote_postings_for_different_regions_are_not_duplicates():
    """A US-remote posting must not fold into its UK-remote twin with the same JD."""
    us = rec("contoso-cd34507d", company="Contoso", source="ashby", title="Forward Deployed Engineer",
             location="New York, NY (Remote) | San Francisco, CA (Remote) | United States (Remote)")
    uk = rec("contoso-5307c7fb", company="Contoso", source="ashby", title="Forward Deployed Engineer",
             location="United Kingdom (Remote)")
    dedup.apply_dedup([us, uk], [], NOW)
    assert "duplicate_of" not in us and "duplicate_of" not in uk


def test_req_key_is_company_scoped():
    assert dedup.keys(rec("a", req_id="JR1004", slug="northwind"))["req"] == "northwind:jr1004"
    assert dedup.keys(rec("b"))["req"] is None


# ----------------------------------------------------------------------------- duplicates
def test_aggregator_copy_is_marked_duplicate_of_the_ats_posting():
    gh = rec("labco-480101", title="Applied AI, Solutions Research Engineer", req_id="480101")
    hn = rec("labco-hn-1", title="Applied AI Solutions Research Engineer", source="hn", jd="b" * 40)
    out = dedup.apply_dedup([hn, gh], [], NOW)
    assert hn["duplicate_of"] == "labco-480101" and hn["status"] == "skipped"
    assert "duplicate_of" not in gh and gh["status"] == "discovered"
    assert out["summary"]["duplicates"] == 1


def test_same_req_on_one_board_with_different_titles_is_not_a_duplicate():
    records = fa.fetch_greenhouse({"name": "Labco", "slug": "labco", "board_token": "labco"},
                                  FakeHttp(ats_routes()), NOW, title_filter=None).records
    dedup.apply_dedup(records, [], NOW)
    assert not any(r.get("duplicate_of") for r in records)


def test_same_req_from_another_source_is_a_duplicate():
    wd = rec("northwind-jr1004", company="Northwind", slug="northwind", source="workday", req_id="JR1004",
             title="Senior Machine Learning Engineer, Perception - Robot Navigation",
             location="US, CA, Santa Clara")
    alert = rec("northwind-li-9", company="Northwind", slug="northwind", source="linkedin-alert", req_id="jr1004",
                title="Sr ML Engineer - Perception", location="Santa Clara, California", jd="c" * 40)
    dedup.apply_dedup([alert, wd], [], NOW)
    assert alert["duplicate_of"] == "northwind-jr1004"


def test_same_board_same_title_location_needs_identical_jd_to_be_a_duplicate():
    a = rec("x-1", jd="1" * 40)
    b = rec("x-2", jd="2" * 40)
    dedup.apply_dedup([a, b], [], NOW)
    assert "duplicate_of" not in a and "duplicate_of" not in b
    c = rec("x-3", jd="1" * 40)
    dedup.apply_dedup([a, b, c], [], NOW)
    assert c["duplicate_of"] == "x-1"


def test_an_applied_posting_wins_canonical_even_from_an_aggregator():
    adz = rec("labco-adz-5", source="adzuna", status="applied", first_seen="2026-09-20T00:00:00Z")
    gh = rec("labco-9", source="greenhouse", first_seen="2026-09-24T13:00:00Z", jd="d" * 40)
    dedup.apply_dedup([gh, adz], [], NOW)
    assert gh["duplicate_of"] == "labco-adz-5" and gh["status"] == "skipped"


def test_a_duplicate_already_in_progress_keeps_its_status():
    gh = rec("labco-9", source="greenhouse", status="applied")
    adz = rec("labco-adz-5", source="adzuna", status="tailoring", jd="d" * 40)
    dedup.apply_dedup([adz, gh], [], NOW)
    assert adz["duplicate_of"] == "labco-9" and adz["status"] == "tailoring"


# ----------------------------------------------------------------------------- reposts
def test_same_jd_under_a_new_id_from_an_earlier_day_is_a_repost():
    k = dedup.keys(rec("new", jd="f" * 40))
    ledger = [row("old", "2026-08-01T10:00:00Z", ct="zzz", jd="f" * 40)]
    r = rec("new", jd="f" * 40)
    dedup.apply_dedup([r], ledger, NOW)
    assert r["reposted"] is True and r["repost_count"] == 1 and r["repost_of"] == "old"
    assert k["jd"] == "f" * 40


def test_same_day_clones_are_not_reposts():
    ledger = [row("sibling", "2026-09-24T09:00:00Z", jd="f" * 40)]
    r = rec("new", jd="f" * 40)
    dedup.apply_dedup([r], ledger, NOW)
    assert r["reposted"] is False and r["repost_count"] == 0


def test_company_and_title_posted_three_times_is_a_repost():
    ct = dedup.keys(rec("c"))["ct"]
    ledger = [row("a", "2026-06-01T00:00:00Z", ct=ct, jd="1" * 40),
              row("b", "2026-07-15T00:00:00Z", ct=ct, jd="2" * 40)]
    r = rec("c", jd="3" * 40)
    dedup.apply_dedup([r], ledger, NOW)
    assert r["reposted"] is True and r["repost_count"] == 2
    one_earlier = rec("d", jd="4" * 40)
    dedup.apply_dedup([one_earlier], ledger[:1], NOW)
    assert one_earlier["reposted"] is False and one_earlier["repost_count"] == 1


# ----------------------------------------------------------------------------- ghost score
@pytest.mark.parametrize("kw,repost_count,score", [
    ({"posted_at": "2026-09-20T00:00:00Z", "req_id": "1"}, 0, 0.0),
    ({"posted_at": "2026-07-16T13:00:00Z", "req_id": None}, 3, 1.0),          # 70 days, no req, 3 reposts
    ({"posted_at": "2026-08-05T13:00:00Z", "req_id": "1"}, 0, 0.17),          # 50 days: 0.5 * 5/15
    ({"posted_at": "2026-07-26T13:00:00Z", "req_id": "1"}, 0, 0.5),           # 60 days unchanged
    ({"posted_at": "2026-06-26T13:00:00Z", "req_id": "1",                      # 90 days, JD edited 10 d ago
      "jd_changed_at": "2026-09-14T13:00:00Z"}, 3, 0.35),
    ({"posted_at": None, "first_seen": "2026-08-05T13:00:00Z", "req_id": "1"}, 0, 0.17),
])
def test_ghost_score_formula(kw, repost_count, score):
    got, reasons = dedup.ghost_score(rec("g", **kw), repost_count, NOW)
    assert got == score
    assert isinstance(reasons, list)


# ----------------------------------------------------------------------------- ledger + CLI
def _write_jobs(root, now=NOW):
    records = fa.fetch_greenhouse({"name": "Labco", "slug": "labco", "board_token": "labco"},
                                  FakeHttp(ats_routes()), now, title_filter=None).records
    fa.upsert_jobs(records, root, now)
    return records


def test_ledger_is_append_only_and_idempotent(root):
    _write_jobs(root)
    assert dedup.main(["--root", str(root)], now=NOW) == 0
    ledger = root / "state" / "seen.jsonl"
    first = ledger.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in first.splitlines()]
    assert len(rows) == 8 and {r["event"] for r in rows} == {"first_seen"}
    assert dedup.main(["--root", str(root)], now=NOW + timedelta(days=1)) == 0
    assert ledger.read_text(encoding="utf-8") == first                  # nothing new -> nothing appended
    job = fa.load_jobs(root)["labco-480101"]
    job["jd_hash"] = "e" * 40
    fa.save_job(root, job)
    assert dedup.main(["--root", str(root)], now=NOW + timedelta(days=2)) == 0
    after = ledger.read_text(encoding="utf-8")
    assert after.startswith(first)
    new_rows = [json.loads(line) for line in after[len(first):].splitlines()]
    assert [(r["event"], r["id"], r["jd"]) for r in new_rows] == [("jd_changed", "labco-480101", "e" * 40)]


def test_cli_writes_scores_to_job_records(root):
    _write_jobs(root)
    dedup.main(["--root", str(root)], now=NOW)
    job = fa.load_jobs(root)["labco-pip-2001"]           # posted 2026-01-09, no JD change seen
    assert job["ghost_score"] == 0.5 and job["repost_count"] == 0 and job["reposted"] is False
    assert any("unchanged" in r for r in job["ghost_reasons"])


def test_cli_dry_run_writes_nothing(root, capsys):
    _write_jobs(root)
    before = {p.name: p.read_text() for p in (root / "jobs").glob("*.md")}
    assert dedup.main(["--root", str(root), "--dry-run"], now=NOW) == 0
    assert not (root / "state").exists()
    assert {p.name: p.read_text() for p in (root / "jobs").glob("*.md")} == before
    assert "dry-run" in capsys.readouterr().out


@pytest.mark.parametrize("source", ["jibe", "sitemap", "amazon"])
def test_a_company_careers_site_copy_outranks_an_alert_copy(source):
    # Acme (jibe) and Globex or Initech (sitemap) are the company's own pages, like a Greenhouse board
    site = {"id": "a", "status": "discovered", "source": source, "first_seen": "2026-09-29T00:00:00Z"}
    alert = {"id": "b", "status": "discovered", "source": "email-alert", "first_seen": "2026-09-28T00:00:00Z"}
    assert dedup._canonical_order(site) < dedup._canonical_order(alert)


# ----------------------------------------------------------------------------- the cross-portal view
def test_the_canonical_job_carries_every_portal_it_was_seen_on():
    board = rec("a", source="greenhouse", source_url="https://boards.greenhouse.io/labco/jobs/1",
                first_seen="2026-09-25T10:00:00Z", status="queued")
    li = rec("b", source="linkedin-alert", platform="linkedin", source_url="https://www.linkedin.com/jobs/view/9",
             first_seen="2026-09-24T08:00:00Z")
    zr = rec("c", source="ziprecruiter-alert", source_url="https://www.ziprecruiter.com/k/x",
             first_seen="2026-09-26T08:00:00Z")
    dedup.apply_dedup([board, li, zr], [], NOW)
    assert li["duplicate_of"] == "a" and zr["duplicate_of"] == "a"
    assert board["sightings"] == [
        {"portal": "linkedin", "url": "https://www.linkedin.com/jobs/view/9", "first_seen": "2026-09-24T08:00:00Z"},
        {"portal": "company-ats", "url": "https://boards.greenhouse.io/labco/jobs/1", "first_seen": "2026-09-25T10:00:00Z"},
        {"portal": "ziprecruiter", "url": "https://www.ziprecruiter.com/k/x", "first_seen": "2026-09-26T08:00:00Z"}]
    assert board["status"] == "queued"                                     # the stage is never rewritten
    assert "sightings" not in li and "sightings" not in zr


def test_a_job_seen_once_carries_no_sightings_list():
    solo = rec("s", sightings=[{"portal": "x", "url": "u", "first_seen": "t"}])
    dedup.apply_dedup([solo], [], NOW)
    assert "sightings" not in solo


def test_similar_roles_are_same_company_same_title_family_within_30_days():
    a = rec("a", title="Applied AI Engineer, Agents", location="San Francisco, CA", first_seen="2026-09-01T00:00:00Z")
    b = rec("b", title="Applied AI Engineer - Evals", location="New York, NY", jd="b" * 40, first_seen="2026-09-20T00:00:00Z")
    c = rec("c", title="Applied AI Engineer (Health)", location="Seattle, WA", jd="c" * 40, first_seen="2026-11-15T00:00:00Z")
    d = rec("d", title="Research Engineer", location="Boston, MA", jd="d" * 40, first_seen="2026-09-05T00:00:00Z")
    e = rec("e", company="Modelco", title="Applied AI Engineer", jd="e" * 40, first_seen="2026-09-02T00:00:00Z")
    dedup.apply_dedup([a, b, c, d, e], [], NOW)
    assert a["similar"] == ["b"] and b["similar"] == ["a"]
    assert "similar" not in c and "similar" not in d and "similar" not in e
    assert all(r["status"] == "discovered" for r in (a, b, c, d, e))


def test_portal_of_maps_sources_to_registry_ids():
    assert dedup.portal_of({"source": "ashby"}) == "company-ats"
    assert dedup.portal_of({"source": "glassdoor-alert"}) == "glassdoor"
    assert dedup.portal_of({"source": "hn"}) == "hacker-news"
    assert dedup.portal_of({"source": "linkedin-alert", "platform": "linkedin"}) == "linkedin"
    assert dedup.portal_of({"source": "manual"}) == "manual"
    assert "sightings" in dedup.TRACKED and "similar" in dedup.TRACKED


# ----------------------------------------------------------------------------- alert copies and ATS records
def test_a_linkedin_alert_copy_folds_into_the_ats_record_and_keeps_the_ats_date():
    ats = rec("fabrikam-606c993e", company="Fabrikam", title="AI Engineer, Search", location="San Francisco",
              source="ashby", posted_at="2026-08-27T00:00:00Z", first_seen="2026-09-01T00:00:00Z")
    li = rec("fabrikam-li-4465486094", company="Fabrikam", title="AI Engineer, Search",
             location="San Francisco, CA (On-site)", source="linkedin-alert", platform="linkedin", jd=None,
             posted_at=None, first_seen="2026-09-30T08:00:00Z")
    dedup.apply_dedup([ats, li], [], NOW)
    assert li["duplicate_of"] == "fabrikam-606c993e" and li["status"] == "skipped"
    assert ats["posted_at"] == "2026-08-27T00:00:00Z" and "duplicate_of" not in ats
    assert [s["portal"] for s in ats["sightings"]] == ["company-ats", "linkedin"]


def test_an_alert_record_further_along_takes_the_ats_date_as_canonical():
    ats = rec("acme-1", title="ML Engineer", location="Remote", source="greenhouse", posted_at="2026-06-03T00:00:00Z")
    li = rec("acme-li-9", title="ML Engineer", location="New York, NY", source="linkedin-alert", jd=None,
             posted_at=None, status="queued")
    dedup.apply_dedup([ats, li], [], NOW)
    assert ats["duplicate_of"] == "acme-li-9" and li["posted_at"] == "2026-06-03T00:00:00Z"


def test_two_ats_seats_with_one_title_are_never_merged_through_an_alert():
    a = rec("acme-1", title="ML Engineer", location="Remote", source="greenhouse", req_id="1")
    b = rec("acme-2", title="ML Engineer", location="Seattle, WA", source="greenhouse", req_id="2", jd="b" * 40)
    li = rec("acme-li-9", title="ML Engineer", location="Boston, MA", source="linkedin-alert", jd=None)
    dedup.apply_dedup([a, b, li], [], NOW)
    assert not any(r.get("duplicate_of") for r in (a, b, li))
