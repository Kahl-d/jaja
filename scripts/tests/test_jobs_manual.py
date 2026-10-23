"""jobs_manual.py add: a posting the owner (or job-scout with the owner present) read in the browser becomes a
job record through the same make_record/upsert path; never a duplicate; a JD added later fills an
alert record in place."""
from __future__ import annotations

import shutil

import pytest
import yaml

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import FIXTURES, NOW

import fetch_ats as fa  # noqa: E402
import fit_gates as fg  # noqa: E402
import job_alerts as ja  # noqa: E402
import jobs_manual as jm  # noqa: E402
from js_common import short_hash  # noqa: E402

ALERTS = FIXTURES.parent / "alerts"
LI = "https://www.linkedin.com/jobs/view/4012345678/?refId=abc&trk=public_jobs_topcard-title"
JD = ("About the role\nYou will build agent harnesses and evals in Python.\n"
      "Visa sponsorship: We do sponsor visas!\nSan Francisco, CA (Hybrid).\n")


@pytest.fixture
def js(root):
    (root / "targets").mkdir()
    shutil.copy(ALERTS / "platforms.yaml", root / "targets" / "platforms.yaml")
    return root


def add(js, *extra, url=LI, platform="linkedin", title="Applied AI Engineer", company="Quillstack"):
    argv = ["add", "--root", str(js), "--url", url, "--platform", platform, "--title", title, "--company", company,
            "--today", "2026-09-24", *extra]
    return jm.main(argv)


def test_add_creates_a_manual_record_held_until_a_jd_exists(js, capsys):
    assert add(js, "--location", "San Francisco, CA (Hybrid)") == 0
    rec = fa.load_jobs(js)["quillstack-li-4012345678"]
    assert rec["source"] == "manual" and rec["platform"] == "linkedin" and rec["status"] == "discovered"
    assert rec["source_url"] == "https://www.linkedin.com/jobs/view/4012345678/"
    assert (rec["location"], rec["remote"]) == ("San Francisco, CA (Hybrid)", "hybrid")
    assert rec["first_seen"] == "2026-09-24T12:00:00Z"
    assert rec["tags"] == ["jd-missing"] and rec["jd_hash"] is None
    assert "by hand from LinkedIn" in rec["_body"]
    assert fg.evaluate(rec)["gate"] == "review"
    assert "added quillstack-li-4012345678" in capsys.readouterr().out


def test_add_with_a_jd_file_records_the_jd(js, tmp_path):
    jd = tmp_path / "jd.txt"
    jd.write_text(JD, encoding="utf-8")
    assert add(js, "--location", "San Francisco, CA", "--jd-file", str(jd), "--comp", "$180K - $220K",
               "--employment", "full-time", "--posted", "2026-09-22") == 0
    rec = fa.load_jobs(js)["quillstack-li-4012345678"]
    assert "We do sponsor visas!" in fa.jd_text_from_body(rec["_body"])
    assert rec["jd_hash"] and "jd-missing" not in (rec.get("tags") or [])
    assert (rec["comp_min"], rec["comp_max"], rec["employment_type"], rec["posted_at"]) == \
        (180000, 220000, "full-time", "2026-09-22")
    assert fg.evaluate(rec)["sponsorship_signal"] == "boost"


def test_the_same_job_twice_is_never_duplicated(js, capsys):
    add(js)
    capsys.readouterr()
    other_tracking = "https://www.linkedin.com/jobs/collections/recommended/?currentJobId=4012345678&origin=JYMBII"
    assert add(js, url=other_tracking) == 0
    assert "exists: quillstack-li-4012345678" in capsys.readouterr().out
    assert len(list((js / "jobs").glob("*.md"))) == 1


def test_a_jd_fills_an_alert_record_in_place(js, tmp_path, capsys):
    rows = yaml.safe_load((ALERTS / "alert_rows.yaml").read_text(encoding="utf-8"))
    row = dict(next(r for r in rows if r["id"] == "li-01"))
    row["classification"] = {"label": row.pop("label")}
    for rec in ja.parse_alert(row, ja.load_platforms(js), NOW).records:
        fa.save_job(js, rec)
    jd = tmp_path / "jd.txt"
    jd.write_text(JD, encoding="utf-8")
    assert add(js, "--jd-file", str(jd)) == 0
    assert "JD added to quillstack-li-4012345678" in capsys.readouterr().out
    rec = fa.load_jobs(js)["quillstack-li-4012345678"]
    assert rec["source"] == "linkedin-alert" and rec["alert_message_id"] == "li-01"
    assert "jd-missing" not in rec["tags"] and rec["jd_hash"] and rec["jd_changed_at"] == "2026-09-24T12:00:00Z"
    assert "We do sponsor visas!" in fa.jd_text_from_body(rec["_body"])
    assert "JD not captured" not in rec["_body"] and "<untrusted_alert>" in rec["_body"]
    assert fg.evaluate(rec)["gate"] == "pass"
    assert len(list((js / "jobs").glob("*.md"))) == 2


def test_an_unknown_platform_is_refused(js, capsys):
    assert add(js, platform="monster") == 2
    out = capsys.readouterr().out
    assert "monster" in out and "linkedin" in out and not (js / "jobs").exists()


@pytest.mark.parametrize("url", ["linkedin.com/jobs/view/4012345678", "ftp://example.com/job/1", "not a url"])
def test_a_bad_url_is_refused(js, url, capsys):
    assert add(js, url=url) == 2
    assert not (js / "jobs").exists()


def test_a_missing_platform_registry_is_refused(root, capsys):
    assert add(root) == 2
    assert "targets/platforms.yaml" in capsys.readouterr().out


def test_other_urls_lose_tracking_parameters(js):
    url = "https://careers.acme.ai/jobs/ml-engineer?utm_source=linkedin&utm_medium=job&ref=abc&team=ml"
    assert add(js, url=url, platform="company-ats", title="ML Engineer", company="Acme AI") == 0
    canonical = "https://careers.acme.ai/jobs/ml-engineer?team=ml"
    rec = fa.load_jobs(js)[f"acme-ai-man-{short_hash(canonical, n=8)}"]
    assert rec["source_url"] == canonical and rec["platform"] == "company-ats"


def test_dry_run_writes_nothing(js, capsys):
    assert add(js, "--dry-run") == 0
    assert not (js / "jobs").exists()
    assert "dry-run" in capsys.readouterr().out


def test_an_off_filter_title_is_recorded_with_a_warning(js, capsys):
    assert add(js, title="Data Analyst", company="Quillstack") == 0
    assert "warning: title 'Data Analyst' is outside the department title filter" in capsys.readouterr().out


def test_a_company_with_a_dossier_takes_the_dossier_slug(js):
    # jobs said acme-corp while companies/acme.md says acme, so no join found them
    (js / "companies").mkdir()
    (js / "companies" / "acme.md").write_text('---\ntype: company\nslug: acme\nname: "Acme Corp"\n---\n')
    assert add(js, company="Acme Corp") == 0
    rec = fa.load_jobs(js)["acme-li-4012345678"]
    assert (rec["company"], rec["company_slug"]) == ("Acme Corp", "acme")


# ----------------------------------------------------------------------------- where it came from
def _prospect(js, slug="pat-recruiter"):
    (js / "prospects").mkdir(exist_ok=True)
    (js / "prospects" / f"{slug}.md").write_text(f"---\nid: {slug}\nname: Pat Recruiter\n---\n# Pat\n", encoding="utf-8")


def test_a_job_from_a_person_records_the_prospect_the_message_and_source_prospect(js, capsys):
    _prospect(js)
    assert add(js, "--prospect", "pat-recruiter", "--message-id", "1a0e36ad95b2e916") == 0
    rec = fa.load_jobs(js)["quillstack-li-4012345678"]
    assert (rec["source"], rec["prospect"], rec["source_message_id"]) == ("prospect", "pat-recruiter",
                                                                         "1a0e36ad95b2e916")
    assert "source prospect" in capsys.readouterr().out


def test_an_alert_mail_capture_takes_its_source_and_message_without_a_person(js):
    assert add(js, "--source", "email-alert", "--message-id", "19c341858df3d5b1") == 0
    rec = fa.load_jobs(js)["quillstack-li-4012345678"]
    assert (rec["source"], rec.get("prospect"), rec["source_message_id"]) == ("email-alert", None, "19c341858df3d5b1")


def test_an_unknown_prospect_is_refused(js, capsys):
    assert add(js, "--prospect", "nobody-here") == 2
    assert "nobody-here" in capsys.readouterr().out and not (js / "jobs").exists()


def test_a_source_outside_the_hand_capture_set_is_refused(js):
    with pytest.raises(SystemExit):
        add(js, "--source", "greenhouse")


def test_a_rerun_fills_the_links_of_an_existing_job_once_and_never_overwrites(js, capsys):
    _prospect(js)
    _prospect(js, "someone-else")
    assert add(js) == 0                                                        # records captured by hand: source manual
    capsys.readouterr()
    assert add(js, "--prospect", "pat-recruiter", "--message-id", "1a0e36ad95b2e916") == 0
    rec = fa.load_jobs(js)["quillstack-li-4012345678"]
    assert (rec["source"], rec["prospect"], rec["source_message_id"]) == ("prospect", "pat-recruiter",
                                                                         "1a0e36ad95b2e916")
    assert "linked quillstack-li-4012345678" in capsys.readouterr().out
    assert add(js, "--prospect", "pat-recruiter", "--message-id", "1a0e36ad95b2e916") == 0
    assert "nothing written" in capsys.readouterr().out
    assert add(js, "--prospect", "someone-else") == 2                          # a different value: refused
    assert "already" in capsys.readouterr().out
    assert fa.load_jobs(js)["quillstack-li-4012345678"]["prospect"] == "pat-recruiter"


def test_the_hand_capture_sources_and_new_fields_are_in_the_job_schema():
    schema = yaml.safe_load((jm.OS_SCHEMA).read_text(encoding="utf-8"))
    assert set(jm.HAND_SOURCES) <= set(schema["enums"]["source"])
    fields = [str(f).split("#")[0].strip() for f in schema["fields"]]
    assert "prospect" in fields and "source_message_id" in fields


# Connector side: a find from the Indeed, ZipRecruiter or Dice connector in Claude is recorded with its own source.
CONNECTOR_ENTRY = """
  - id: {pid}
    name: {pid}
    url: https://example.com/mcp
    access: [api]
    control: connector
    adapter: null
    alert_senders: []
    status: probation
"""


@pytest.mark.parametrize("pid", ["indeed-connector", "ziprecruiter-connector", "dice-connector"])
def test_a_connector_find_records_the_connector_as_source_and_platform(js, pid):
    reg = js / "targets" / "platforms.yaml"
    reg.write_text(reg.read_text() + CONNECTOR_ENTRY.format(pid=pid))
    url = "https://careers.quillstack.ai/jobs/applied-ai-engineer?utm_source=indeed"
    jd = js / "jd.txt"
    jd.write_text(JD)
    assert add(js, "--source", pid, "--jd-file", str(jd), url=url, platform=pid) == 0
    [rec] = [r for r in fa.load_jobs(js).values() if r.get("company") == "Quillstack"]
    assert (rec["source"], rec["platform"]) == (pid, pid)
    assert "<untrusted_jd>" in rec["_body"]


def test_connector_sources_are_in_the_job_schema():
    from js_common import OS_ROOT
    text = (__import__("agency_paths").SCHEMAS_DIR / "job.yaml").read_text()
    for pid in ("indeed-connector", "ziprecruiter-connector", "dice-connector"):
        assert pid in jm.HAND_SOURCES and pid in text


# ----------------------------------------------------------------------------- set
# fit-scorer had to call fetch_ats.save_job from a Python snippet: `set` writes the scored fields, whitelisted and
# validated against schemas/job.yaml, and prints the before and after.
def _one(js):
    add(js)
    return "quillstack-li-4012345678"


def test_set_writes_the_scored_fields_and_prints_the_diff(js, capsys):
    rid = _one(js)
    capsys.readouterr()
    assert jm.main(["set", rid, "--root", str(js), "--fit-score", "4.3", "--fit-verdict", "apply",
                    "--lens", "agentic-systems", "--status", "fit_checked"]) == 0
    out = capsys.readouterr().out
    rec = fa.load_jobs(js)[rid]
    assert (rec["fit_score"], rec["fit_verdict"], rec["lens"], rec["status"]) == (4.3, "apply", "agentic-systems", "fit_checked")
    assert "fit_score: None -> 4.3" in out and "status: discovered -> fit_checked" in out
    assert rec["source_url"] == "https://www.linkedin.com/jobs/view/4012345678/" and "by hand from LinkedIn" in rec["_body"]


@pytest.mark.parametrize("extra, why", [
    (["--fit-score", "5.5"], "fit_score"), (["--fit-score", "0.9"], "fit_score"), (["--fit-score", "high"], "fit_score"),
    (["--fit-verdict", "yes"], "fit_verdict"), (["--lens", "poetry"], "lens"), (["--status", "hired"], "status")])
def test_set_refuses_values_outside_the_schema(js, capsys, extra, why):
    rid = _one(js)
    before = (js / "jobs" / f"{rid}.md").read_bytes()
    capsys.readouterr()
    assert jm.main(["set", rid, "--root", str(js), *extra]) == 2
    assert why in capsys.readouterr().out and (js / "jobs" / f"{rid}.md").read_bytes() == before


def test_set_refuses_applied_since_the_tracker_owns_it(js, capsys):
    rid = _one(js)
    assert jm.main(["set", rid, "--root", str(js), "--status", "applied"]) == 2
    assert "tracker.py" in capsys.readouterr().out


def test_set_refuses_an_unknown_job_and_an_empty_call(js, capsys):
    rid = _one(js)
    assert jm.main(["set", "nope-123", "--root", str(js), "--fit-score", "4"]) == 2
    assert jm.main(["set", rid, "--root", str(js)]) == 2


def test_set_dry_run_and_no_change_write_nothing(js, capsys):
    rid = _one(js)
    path = js / "jobs" / f"{rid}.md"
    before = path.read_bytes()
    capsys.readouterr()
    assert jm.main(["set", rid, "--root", str(js), "--fit-score", "4", "--dry-run"]) == 0
    assert "dry-run" in capsys.readouterr().out and path.read_bytes() == before
    assert jm.main(["set", rid, "--root", str(js), "--status", "discovered"]) == 0
    assert "no change" in capsys.readouterr().out and path.read_bytes() == before


# ----------------------------------------------------------------------------- jd
# A job held at review for "jd not captured" gets its remedy: fetch the posting (robots-first), record the JD, take
# posted_at from JSON-LD datePosted, drop jd-missing, re-gate. The shape: a YC page, datePosted 2026-03-11.
from disc_harness import FakeHttp  # noqa: E402

YC = "https://www.ycombinator.com/companies/exampledata/jobs/AbCdEf1-founding-ml-engineer"
YC_PAGE = """<html><head><title>Founding ML Engineer at Exampledata</title>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting","title":"Founding ML Engineer",
"datePosted":"2026-03-11","description":"&lt;p&gt;Build ML models in Python for B2B data. We sponsor visas.&lt;/p&gt;&lt;p&gt;San Francisco, CA.&lt;/p&gt;",
"hiringOrganization":{"@type":"Organization","name":"Exampledata"}}</script></head><body>nav</body></html>"""


def _yc_job(js, title="Founding ML Engineer", company="Exampledata"):
    assert add(js, url=YC, platform="yc", title=title, company=company) == 0
    return next(iter(fa.load_jobs(js)))


def _http(robots="User-agent: *\nAllow: /\n", page=YC_PAGE):
    return FakeHttp({("GET", "https://www.ycombinator.com/robots.txt"): robots, ("GET", YC): page})


def test_jd_records_the_posting_its_date_and_regates(js, capsys, monkeypatch):
    rid = _yc_job(js)
    assert fa.load_jobs(js)[rid]["posted_at"] is None and fa.load_jobs(js)[rid]["tags"] == ["jd-missing"]
    monkeypatch.setattr(jm, "_http", lambda: _http())
    capsys.readouterr()
    assert jm.main(["jd", rid, "--root", str(js), "--today", "2026-10-01"]) == 0
    out = capsys.readouterr().out
    rec = fa.load_jobs(js)[rid]
    assert rec["posted_at"] == "2026-03-11" and "jd-missing" not in (rec.get("tags") or []) and rec["jd_hash"]
    assert "Build ML models in Python" in fa.jd_text_from_body(rec["_body"]) and "<p>" not in fa.jd_text_from_body(rec["_body"])
    assert "JD not captured" not in rec["_body"] and "fetched from" in rec["_body"]
    assert rec["gate"] != "review" or not any("not captured" in r for r in rec["gate_reasons"])
    assert "posted_at: None -> 2026-03-11" in out and "gate:" in out


def test_jd_honours_robots_and_writes_nothing_when_disallowed(js, capsys, monkeypatch):
    rid = _yc_job(js)
    before = (js / "jobs" / f"{rid}.md").read_bytes()
    monkeypatch.setattr(jm, "_http", lambda: _http(robots="User-agent: *\nDisallow: /companies/\n"))
    assert jm.main(["jd", rid, "--root", str(js)]) == 2
    assert "robots.txt" in capsys.readouterr().out and (js / "jobs" / f"{rid}.md").read_bytes() == before


def test_jd_refuses_a_page_without_a_job_posting(js, capsys, monkeypatch):
    rid = _yc_job(js)
    monkeypatch.setattr(jm, "_http", lambda: _http(page="<html><body>Sign in</body></html>"))
    assert jm.main(["jd", rid, "--root", str(js)]) == 2
    assert "--jd-file" in capsys.readouterr().out


def test_jd_keeps_an_earlier_posted_at_and_takes_a_url(js, capsys, monkeypatch):
    rid = _yc_job(js)
    rec = fa.load_jobs(js)[rid]
    rec["posted_at"] = "2026-02-01"
    fa.save_job(js, rec)
    other = "https://www.ycombinator.com/companies/exampledata/jobs/other"
    monkeypatch.setattr(jm, "_http", lambda: FakeHttp({("GET", "https://www.ycombinator.com/robots.txt"): "",
                                                       ("GET", other): YC_PAGE}))
    assert jm.main(["jd", rid, "--root", str(js), "--url", other]) == 0
    assert fa.load_jobs(js)[rid]["posted_at"] == "2026-02-01"


def test_jd_dry_run_writes_nothing(js, capsys, monkeypatch):
    rid = _yc_job(js)
    before = (js / "jobs" / f"{rid}.md").read_bytes()
    monkeypatch.setattr(jm, "_http", lambda: _http())
    assert jm.main(["jd", rid, "--root", str(js), "--dry-run"]) == 0
    assert (js / "jobs" / f"{rid}.md").read_bytes() == before and "dry-run" in capsys.readouterr().out


def test_jd_asks_for_html_since_yc_answers_404_to_json(js, capsys, monkeypatch):
    rid = _yc_job(js)
    http = _http()
    monkeypatch.setattr(jm, "_http", lambda: http)
    jm.main(["jd", rid, "--root", str(js), "--dry-run"])
    page = [arg for m, u, arg in http.calls if u == YC]
    assert page and "text/html" in page[0]["Accept"]       # Accept application/json gave HTTP 404


# ----------------------------------------------------------------------------- YC dates
# jobs/exampledata-yc-abcdef1.md had posted_at null, so freshness read first_seen (2 days) for a posting 204 days old.
# `add --fetch` captures datePosted (and the JD) at capture; `backfill-dates` fixes the records already there.
WAAS = "https://www.workatastartup.com/jobs/93490"


def test_add_fetch_captures_the_date_and_jd_at_capture(js, capsys, monkeypatch):
    monkeypatch.setattr(jm, "_http", lambda: _http())
    assert add(js, "--fetch", url=YC, platform="yc", title="Founding ML Engineer", company="Exampledata") == 0
    rec = next(iter(fa.load_jobs(js).values()))
    assert rec["posted_at"] == "2026-03-11" and "jd-missing" not in (rec.get("tags") or []) and rec["jd_hash"]


def test_add_fetch_that_fails_still_records_the_job_held(js, capsys, monkeypatch):
    monkeypatch.setattr(jm, "_http", lambda: _http(page="<html>Sign in</html>"))
    assert add(js, "--fetch", url=YC, platform="yc", title="Founding ML Engineer", company="Exampledata") == 0
    rec = next(iter(fa.load_jobs(js).values()))
    assert rec["posted_at"] is None and rec["tags"] == ["jd-missing"]
    assert "fetch refused" in capsys.readouterr().out


def _two_yc(js):
    a = _yc_job(js)
    assert add(js, url=WAAS, platform="yc", title="AI Engineer", company="Waasco") == 0
    b = next(k for k in fa.load_jobs(js) if k != a)
    return a, b


def _yc_http():
    return FakeHttp({("GET", "https://www.ycombinator.com/robots.txt"): "", ("GET", YC): YC_PAGE,
                     ("GET", "https://www.workatastartup.com/robots.txt"): "User-Agent: *\nDisallow:\n",
                     ("GET", WAAS): "<html><body><div id=root></div></body></html>"})


def test_backfill_is_a_dry_run_by_default_and_counts(js, capsys, monkeypatch):
    a, b = _two_yc(js)
    before = {p: p.read_bytes() for p in (js / "jobs").glob("*.md")}
    monkeypatch.setattr(jm, "_http", _yc_http)
    capsys.readouterr()
    assert jm.main(["backfill-dates", "--root", str(js), "--today", "2026-10-01"]) == 0
    out = capsys.readouterr().out
    assert {p: p.read_bytes() for p in (js / "jobs").glob("*.md")} == before
    assert "dry-run" in out and "candidates 2; dated 1; jd recorded 1; refused 1" in out and b in out


def test_backfill_write_dates_the_yc_record_and_leaves_the_refused_one(js, capsys, monkeypatch):
    a, b = _two_yc(js)
    monkeypatch.setattr(jm, "_http", _yc_http)
    assert jm.main(["backfill-dates", "--root", str(js), "--write"]) == 0
    jobs = fa.load_jobs(js)
    assert jobs[a]["posted_at"] == "2026-03-11" and jobs[b]["posted_at"] is None


def test_backfill_dates_a_record_that_has_its_jd_without_touching_the_jd(js, capsys, monkeypatch):
    rid = _yc_job(js)
    rec = fa.load_jobs(js)[rid]
    jm.fill_jd(rec, "Our own captured JD text.", "YC", jm.ja.run_now(None))
    fa.save_job(js, rec)
    monkeypatch.setattr(jm, "_http", lambda: _http())
    assert jm.main(["backfill-dates", "--root", str(js), "--write"]) == 0
    rec = fa.load_jobs(js)[rid]
    assert rec["posted_at"] == "2026-03-11" and "Our own captured JD text." in fa.jd_text_from_body(rec["_body"])


def test_notes_carry_the_department_day_not_the_utc_date(js):
    # at 20:22 Pacific a note said "JD recorded <next day>" (the UTC date)
    from datetime import datetime, timezone
    late = datetime(2026, 10, 2, 3, 22, tzinfo=timezone.utc)                 # 2026-10-01 20:22 in Los Angeles
    rec = {"_body": "", "tags": ["jd-missing"]}
    jm.fill_jd(rec, "a JD", "YC", late, how="fetched from x")
    assert "JD recorded 2026-10-01 fetched from x" in rec["_body"]
    assert jm.captured_line("YC", late).startswith("- Captured: 2026-10-01,")


# ----------------------------------------------------------------------------- recapture
# Stored JDs lacked half the posting; `recapture --company acme` re-reads each open job's page through the same
# reader and replaces a JD that changed. A dry run by default; it lists the applications whose jd.md came from a job
# that would change (those files are the fit-scorer's and are never touched).
POSTING_URL = "https://jobs.fruitco.example.test/en-us/details/100123-0201/ml-engineer"


def _recapture_world(js):
    from disc_harness import load_fixture
    job = load_fixture("fruitco_jobdetails_100123-0201.json")["res"]
    page = ('<html><script>window.__staticRouterHydrationData = JSON.parse("' +
            __import__("json").dumps(__import__("json").dumps({"loaderData": {"jobDetails": {"jobsData": job}}}))[1:-1]
            + '");</script></html>')
    rec = fa.make_record({"name": "Acme", "slug": "acme"}, title=job["postingTitle"], source="sitemap",
                         source_url=POSTING_URL, apply_url=POSTING_URL, ats_vendor="unknown", posted_at="2026-09-28",
                         location="Cupertino", remote="unknown", comp=(None, None, None), employment_type=None,
                         jd_text="Old half JD: description and qualifications only.", now=NOW)
    rec["id"], rec["status"] = "acme-100123-0201", "fit_checked"
    fa.save_job(js, rec)
    closed = dict(rec, id="acme-closed-1", status="closed", source_url=POSTING_URL + "-x", apply_url=POSTING_URL + "-x")
    fa.save_job(js, closed)
    app = js / "applications" / "acme-ml-engineer-202610"
    app.mkdir(parents=True)
    (app / "application.md").write_text("---\ntype: application\njob: '[[acme-100123-0201]]'\n---\n")
    (app / "jd.md").write_text("old copy\n")
    return FakeHttp({("GET", "https://jobs.fruitco.example.test/robots.txt"): (404, ""), ("GET", POSTING_URL): page})


def test_recapture_dry_run_counts_and_lists_applications_and_writes_nothing(js, capsys, monkeypatch):
    http = _recapture_world(js)
    monkeypatch.setattr(jm, "_http", lambda **kw: http)
    before = {p: p.read_bytes() for p in js.rglob("*.md")}
    assert jm.main(["recapture", "--company", "acme", "--root", str(js), "--delay", "0"]) == 0
    out = capsys.readouterr().out
    assert {p: p.read_bytes() for p in js.rglob("*.md")} == before
    assert "dry-run" in out and "open jobs 1; changed 1; unchanged 0; refused 0" in out
    assert "applications/acme-ml-engineer-202610/jd.md" in out


def test_recapture_write_replaces_the_jd_and_never_touches_the_application(js, capsys, monkeypatch):
    http = _recapture_world(js)
    monkeypatch.setattr(jm, "_http", lambda **kw: http)
    assert jm.main(["recapture", "--company", "acme", "--root", str(js), "--delay", "0", "--write"]) == 0
    rec = fa.load_jobs(js)["acme-100123-0201"]
    text = fa.jd_text_from_body(rec["_body"])
    assert "Responsibilities" in text and "$141,000 to $262,000" in text and "Old half JD" not in text
    assert (js / "applications" / "acme-ml-engineer-202610" / "jd.md").read_text() == "old copy\n"
    assert jm.main(["recapture", "--company", "acme", "--root", str(js), "--delay", "0"]) == 0
    assert "changed 0; unchanged 1" in capsys.readouterr().out
