"""pipeline.py: the daily js-scout harness over recorded fixtures (dry run and real run), stage
subsets, error isolation, the run receipt, and the sources.yaml registry."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from datetime import timedelta

import pytest
import yaml

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import COMPANIES_YAML, FIXTURES, LEVER, NOW, FakeHttp, ats_routes, hn_routes, load_fixture

import fetch_ats as fa  # noqa: E402
import pipeline as pl  # noqa: E402
from js_common import JS_ROOT, fm, read_jsonl  # noqa: E402


def _first_slug(vendor: str) -> str:
    """The first board of a vendor in the shared fixture company list, so no fixture company is named here."""
    return next(c["slug"] for c in yaml.safe_load(COMPANIES_YAML)["companies"] if c["ats_vendor"] == vendor)


LEVER_SLUG = _first_slug("lever")
GH_SLUG = _first_slug("greenhouse")


@pytest.fixture(autouse=True)
def _deferrable_boards(monkeypatch):
    """The deferral tests here use API boards as stand-ins for boards that can wait. The pipeline reads dream and
    one-request API boards every run (pipeline.must_read; its own tests: test_board_must_read.py), so these tests
    turn it off to keep testing the cursor that still governs the many-request boards."""
    monkeypatch.setattr(pl, "must_read", lambda c: False)

IDS = ["greenhouse", "lever", "ashby", "workday", "hn", "adzuna", "himalayas", "aidevjobs", "linkedin-alert", "glassdoor-alert",
       "ziprecruiter-alert", "wttj-alert", "hiringcafe-alert", "yc-alert", "email-alert", "funding"]
FIELDS = {"id", "kind", "enabled", "cadence", "yield_30d", "last_run", "probation", "notes"}
HIMALAYAS = "https://himalayas.app/jobs/api/search"
NO_HIMALAYAS_JOBS = {"offset": 0, "limit": 20, "totalCount": 0, "jobs": []}
ALERTS = FIXTURES.parent / "alerts"


AIDEVJOBS = "https://aidevboard.com/api/v1/jobs"
NO_AIDEVJOBS = {"page": 1, "per_page": 50, "total": 0, "has_next": False, "jobs": []}


def http(*drop, himalayas=None):
    routes = {**ats_routes(), **hn_routes(), ("GET", HIMALAYAS): himalayas or NO_HIMALAYAS_JOBS,
              ("GET", AIDEVJOBS): NO_AIDEVJOBS}
    for key in drop:
        routes.pop(key)
    return FakeHttp(routes)


def himalayas_pages(params):
    return load_fixture("himalayas_search_us.json" if params.get("page", 1) == 1 else "himalayas_search_us_p2.json")


def with_alerts(root):
    shutil.copy(ALERTS / "platforms.yaml", root / "targets" / "platforms.yaml")
    folder = root / "state" / "harvest"
    folder.mkdir(parents=True)
    with (folder / "gmail-2026-09-24.classified.jsonl").open("w", encoding="utf-8") as fh:
        for row in yaml.safe_load((ALERTS / "alert_rows.yaml").read_text(encoding="utf-8")):
            row["classification"] = {"label": row.pop("label"), "confidence": 0.95, "level": "L0"}
            fh.write(json.dumps(row) + "\n")
    return root


def go(root, os_root, **kw):
    kw.setdefault("env", {})
    kw.setdefault("workday_page_size", 3)
    return pl.run_pipeline(root, os_root, kw.pop("now", NOW), **kw)


def test_committed_sources_registry_is_complete():
    data = fm.parse_yaml((JS_ROOT / "sources.yaml").read_text(encoding="utf-8"))
    assert [s["id"] for s in data["sources"]] == IDS
    for s in data["sources"]:
        assert FIELDS <= set(s), s["id"]


def test_dry_run_over_fixtures_computes_every_stage_and_writes_nothing(root_with_targets, os_root):
    root = root_with_targets
    res = go(root, os_root, http=http(), dry_run=True)
    st = res.receipt["stages"]
    assert st["fetch"]["new"] == 20 and st["fetch"]["boards"] == 6
    assert st["hn"]["new"] == 7
    assert st["adzuna"]["skipped"].startswith("ADZUNA_APP_ID")
    assert st["dedup"]["records"] == 27
    assert st["gates"]["evaluated"] == 27
    assert st["gates"]["pass"] + st["gates"]["review"] + st["gates"]["fail"] == 27
    assert st["queue"]["queued"] == st["gates"]["pass"] + st["gates"]["review"]
    ws = res.workset
    assert ws[f"{GH_SLUG}-480101"]["status"] == "fit_checked" and ws[f"{GH_SLUG}-480101"]["fit_verdict"] == "unscored"
    assert ws["agencyflow-hn-2012"]["gate"] == "fail" and ws["agencyflow-hn-2012"]["status"] == "skipped"
    assert ws[f"{GH_SLUG}-480101"]["sponsorship"] == "yes"            # JD says "We do sponsor visas!"
    assert not (root / "jobs").exists() and not (root / "state").exists()
    assert st["himalayas"]["new"] == 0 and "no classified harvest" in st["alerts"]["skipped"]
    assert not (root / "DASHBOARD.md").exists() and not (os_root / "logs").exists()
    assert res.receipt["status"] == "ok" and res.receipt["receipt_path"] is None


def test_real_run_writes_records_ledger_dashboard_and_receipt(root_with_targets, os_root):
    root = root_with_targets
    res = go(root, os_root, http=http())
    assert res.receipt["status"] == "ok"
    assert len(list((root / "jobs").glob("*.md"))) == 27
    assert len(read_jsonl(root / "state" / "seen.jsonl")) == 27
    assert (root / "DASHBOARD.md").read_text().startswith("<!-- GENERATED")
    receipt = os_root / "logs" / "runs" / "js-scout" / "2026-09-24.md"
    props, body = fm.load(receipt)
    assert props["routine"] == "js-scout" and "| fetch |" in body and "| gates |" in body
    jobs = fa.load_jobs(root)
    assert jobs[f"{GH_SLUG}-480102"]["status"] == "skipped" and jobs[f"{GH_SLUG}-480102"]["fit_verdict"] == "skip"
    assert jobs[f"{GH_SLUG}-480101"]["ghost_score"] == 0.0
    # same day again: nothing new, receipt gets a second run section (append-only)
    again = go(root, os_root, http=http())
    assert again.receipt["stages"]["fetch"]["new"] == 0 and again.receipt["stages"]["fetch"]["updated"] == 20
    assert again.receipt["stages"]["hn"]["skipped"] == "already ran this month (2026-09)"
    assert fm.load(receipt)[1].count("## Run ") == 2
    assert len(read_jsonl(root / "state" / "seen.jsonl")) == 27


def test_next_day_run_resights_without_duplicates(root_with_targets, os_root):
    root = root_with_targets
    go(root, os_root, http=http())
    later = go(root, os_root, http=http(), now=NOW + timedelta(days=1))
    assert later.receipt["stages"]["fetch"]["new"] == 0
    assert len(list((root / "jobs").glob("*.md"))) == 27
    assert fa.load_jobs(root)[f"{GH_SLUG}-480101"]["last_seen"] == "2026-09-25T13:00:00Z"
    assert (os_root / "logs" / "runs" / "js-scout" / "2026-09-25.md").is_file()


def test_rerun_respects_later_decisions(root_with_targets, os_root):
    root = root_with_targets
    go(root, os_root, http=http())
    jobs = fa.load_jobs(root)
    scored = jobs[f"{GH_SLUG}-480101"]
    scored["status"], scored["fit_verdict"], scored["fit_score"] = "queued", "apply", 4.4      # fit-scorer's call
    fa.save_job(root, scored)
    overridden = jobs[f"{GH_SLUG}-480102"]                                                    # London, gate fail
    overridden["status"], overridden["fit_verdict"] = "fit_checked", "maybe"                 # the owner: look anyway
    fa.save_job(root, overridden)
    go(root, os_root, http=http(), now=NOW + timedelta(days=1))
    jobs = fa.load_jobs(root)
    assert (jobs[f"{GH_SLUG}-480101"]["status"], jobs[f"{GH_SLUG}-480101"]["fit_verdict"]) == ("queued", "apply")
    assert (jobs[f"{GH_SLUG}-480102"]["status"], jobs[f"{GH_SLUG}-480102"]["fit_verdict"]) == ("fit_checked", "maybe")


def test_stage_subset_runs_only_what_was_asked(root_with_targets, os_root):
    res = go(root_with_targets, os_root, http=FakeHttp({}), stages=["dashboard"])
    st = res.receipt["stages"]
    assert st["dashboard"]["written"] is True
    assert st["fetch"] == {"skipped": "not selected"}
    assert (root_with_targets / "DASHBOARD.md").is_file()


def test_a_failing_board_is_recorded_and_the_run_continues(root_with_targets, os_root):
    res = go(root_with_targets, os_root, http=http(("GET", LEVER.format(LEVER_SLUG))))
    assert res.receipt["status"] == "partial"
    assert any(LEVER_SLUG in e for e in res.receipt["errors"])
    assert res.receipt["stages"]["fetch"]["new"] == 16
    assert LEVER_SLUG in (os_root / "logs" / "runs" / "js-scout" / "2026-09-24.md").read_text()


def test_registry_last_run_and_yield_update_in_place(root_with_targets, os_root):
    root = root_with_targets
    shutil.copy(JS_ROOT / "sources.yaml", root / "sources.yaml")
    before = {s["id"]: s for s in fm.parse_yaml((root / "sources.yaml").read_text())["sources"]}
    go(root, os_root, http=http())
    text = (root / "sources.yaml").read_text()
    assert text.startswith("# Discovery source registry")          # comments survive
    reg = {s["id"]: s for s in fm.parse_yaml(text)["sources"]}
    assert reg["greenhouse"]["last_run"] == "2026-09-24T13:00:00Z"
    assert reg["greenhouse"]["yield_30d"] == 3        # SF research eng, remote research eng, FDE; London + 2 staff fail
    # a skipped stage leaves last_run as it was (the live registry carries real run dates)
    assert reg["adzuna"]["last_run"] == before["adzuna"]["last_run"]                  # skipped without keys
    assert reg["linkedin-alert"]["last_run"] == before["linkedin-alert"]["last_run"]  # no harvest: alerts skipped
    assert reg["himalayas"]["last_run"] == "2026-09-24T13:00:00Z" and reg["himalayas"]["yield_30d"] == 0


def test_a_disabled_source_is_not_polled(root_with_targets, os_root):
    root = root_with_targets
    text = (JS_ROOT / "sources.yaml").read_text().replace(
        "  - id: lever\n    kind: ats-json\n    enabled: true", "  - id: lever\n    kind: ats-json\n    enabled: false")
    (root / "sources.yaml").write_text(text)
    h = http()
    res = go(root, os_root, http=h, dry_run=True)
    assert not any(c[1].startswith("https://api.lever.co") for c in h.calls)
    assert res.receipt["stages"]["fetch"]["new"] == 14
    assert any("lever disabled" in n for n in res.receipt["notes"])


def test_cli_json_dry_run(root_with_targets, os_root, capsys, monkeypatch):
    monkeypatch.setattr(pl, "_default_http", lambda: http())
    rc = pl.main(["--root", str(root_with_targets), "--os-root", str(os_root), "--dry-run", "--json",
                  "--workday-page-size", "3"], now=NOW, env={})
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["stages"]["fetch"]["new"] == 20 and out["dry_run"] is True


# ----------------------------------------------------------------------------- himalayas and alerts stages
def test_himalayas_stage_adds_records_and_marks_the_registry(root_with_targets, os_root):
    root = root_with_targets
    shutil.copy(JS_ROOT / "sources.yaml", root / "sources.yaml")
    res = go(root, os_root, http=http(himalayas=himalayas_pages))
    assert res.receipt["status"] == "ok"
    st = res.receipt["stages"]["himalayas"]
    assert st["new"] == 3 and st["kept"] == 3 and st["requests"] == 14
    jobs = fa.load_jobs(root)
    assert jobs["brightline-robotics-him-5100000001"]["status"] == "fit_checked"
    reg = {s["id"]: s for s in fm.parse_yaml((root / "sources.yaml").read_text())["sources"]}
    assert reg["himalayas"]["last_run"] == "2026-09-24T13:00:00Z" and reg["himalayas"]["yield_30d"] == 3


def test_himalayas_without_a_network_is_a_clean_skip(root_with_targets, os_root):
    offline = http(himalayas=ConnectionError("Failed to establish a new connection"))
    res = go(root_with_targets, os_root, http=offline, dry_run=True)
    assert res.receipt["stages"]["himalayas"]["skipped"].startswith("no network")
    assert res.receipt["status"] == "ok" and res.receipt["stages"]["fetch"]["new"] == 20


def test_alerts_stage_parses_the_harvest_and_ledgers_only_on_a_real_run(root_with_targets, os_root):
    root = with_alerts(root_with_targets)
    dry = go(root, os_root, http=http(), dry_run=True)
    st = dry.receipt["stages"]["alerts"]
    assert (st["alerts"], st["new"], st["updated"], st["review"], st["filtered"]) == (12, 15, 1, 4, 1)
    assert not (root / "state" / "job-alerts.jsonl").exists() and not (root / "jobs").exists()
    real = go(root, os_root, http=http())
    assert real.receipt["status"] == "ok"
    q = fa.load_jobs(root)["quillstack-li-4012345678"]
    assert q["status"] == "fit_checked" and q["gate"] == "review" and q["platform"] == "linkedin"
    assert any(r.startswith("jd: ") for r in q["gate_reasons"])
    assert len(read_jsonl(root / "state" / "job-alerts.jsonl")) == 13
    assert (root / "state" / "review" / "job-alerts-2026-09-24.md").is_file()
    assert any("job alerts: 4 for review" in n for n in real.receipt["notes"])
    again = go(root, os_root, http=http(), now=NOW + timedelta(hours=1))
    assert again.receipt["stages"]["alerts"]["alerts"] == 0 and again.receipt["stages"]["alerts"]["new"] == 0


def test_a_disabled_alert_source_is_left_in_the_harvest(root_with_targets, os_root):
    root = with_alerts(root_with_targets)
    text = (JS_ROOT / "sources.yaml").read_text().replace(
        "  - id: linkedin-alert\n    kind: email-alert\n    enabled: true",
        "  - id: linkedin-alert\n    kind: email-alert\n    enabled: false")
    (root / "sources.yaml").write_text(text)
    st = go(root, os_root, http=http(), dry_run=True).receipt["stages"]["alerts"]
    assert (st["disabled"], st["alerts"], st["new"]) == (5, 7, 12)


# ----------------------------------------------------------------------------- query terms per source (sources.yaml)
ADZUNA = "https://api.adzuna.com/v1/api/jobs/us/search/1"
ADZUNA_ENV = {"ADZUNA_APP_ID": "test-app-id", "ADZUNA_APP_KEY": "test-key-000"}


def _sources_with(root, **queries):
    """The committed sources.yaml with a `queries:` list added to the named sources."""
    lines = (JS_ROOT / "sources.yaml").read_text(encoding="utf-8").splitlines()
    out, current = [], None
    for line in lines:
        out.append(line)
        if line.startswith("  - id: "):
            current = line.split(": ", 1)[1].strip()
            if current in queries:
                out.append(f"    queries: {json.dumps(queries[current])}")
    (root / "sources.yaml").write_text("\n".join(out) + "\n", encoding="utf-8")


def test_himalayas_takes_its_query_terms_from_sources_yaml(root_with_targets, os_root):
    root = root_with_targets
    _sources_with(root, himalayas=["LLM engineer", "agent engineer"])
    fake = http(himalayas=NO_HIMALAYAS_JOBS)
    res = go(root, os_root, http=fake, dry_run=True)
    asked = [params["q"] for method, url, params in fake.calls if url == HIMALAYAS]
    assert asked == ["LLM engineer", "agent engineer"]
    assert res.receipt["stages"]["himalayas"]["queries"] == 2


def test_adzuna_takes_what_and_where_from_sources_yaml(root_with_targets, os_root):
    root = root_with_targets
    _sources_with(root, adzuna=[{"what": "LLM engineer", "where": "San Francisco"}, "agent engineer"])
    fake = http()
    fake.routes[("GET", ADZUNA)] = {"results": []}
    go(root, os_root, http=fake, dry_run=True, env=ADZUNA_ENV)
    asked = [(params["what"], params.get("where")) for method, url, params in fake.calls if url == ADZUNA]
    assert asked == [("LLM engineer", "San Francisco"), ("agent engineer", None)]


def test_without_queries_the_adapters_keep_their_built_in_lists(root_with_targets, os_root):
    import himalayas
    root = root_with_targets
    shutil.copy(JS_ROOT / "sources.yaml", root / "sources.yaml")
    fake = http(himalayas=NO_HIMALAYAS_JOBS)
    go(root, os_root, http=fake, dry_run=True)
    asked = [params["q"] for method, url, params in fake.calls if url == HIMALAYAS]
    assert asked == list(himalayas.DEFAULT_QUERIES)


def test_unreadable_queries_fall_back_to_the_built_in_list_and_say_so(root_with_targets, os_root):
    import himalayas
    root = root_with_targets
    _sources_with(root, himalayas=[{"where": "no what"}], adzuna=[{"where": "no what"}])
    fake = http(himalayas=NO_HIMALAYAS_JOBS)
    res = go(root, os_root, http=fake, dry_run=True)
    asked = [params["q"] for method, url, params in fake.calls if url == HIMALAYAS]
    assert asked == list(himalayas.DEFAULT_QUERIES)
    notes = " ".join(res.receipt["notes"])
    assert "sources.yaml himalayas queries" in notes and "built-in queries used" in notes


# ----------------------------------------------------------------------------- triage stage, local day
def test_the_triage_stage_scores_every_open_gated_job_and_reports_it(root_with_targets, os_root):
    res = go(root_with_targets, os_root, http=http())
    tri = res.receipt["stages"]["triage"]
    jobs = fa.load_jobs(root_with_targets)
    scored = [j for j in jobs.values() if j.get("hunt_score") is not None]
    assert tri["scored"] == len(scored) > 0 and tri["above_cut"] <= tri["scored"]
    assert all(j.get("gate") != "fail" and j.get("status") in ("discovered", "fit_checked", "queued") for j in scored)
    assert tri["top"] and tri["top"][0]["hunt_score"] == max(j["hunt_score"] for j in scored)
    assert "triage" in pl.STAGES and pl.STAGES.index("triage") == pl.STAGES.index("queue") + 1


def test_receipts_and_reviews_are_named_by_the_department_day_not_utc(root_with_targets, os_root):
    late = NOW.replace(hour=6, minute=45) + timedelta(days=1)       # 06:45 UTC = 23:45 the day before in California
    go(root_with_targets, os_root, http=http(), now=late, stages=["fetch", "queue"])
    runs = os_root / "logs" / "runs" / "js-scout"
    assert (runs / "2026-09-24.md").is_file() and not (runs / "2026-09-25.md").exists()


def test_the_fetch_stage_gives_sitemap_boards_the_known_jobs_and_the_page_memory(root_with_targets, os_root, monkeypatch):
    root = root_with_targets
    monkeypatch.setattr(fa, "_sitemap_sleep", lambda s: None)
    (root / "targets" / "companies.yaml").write_text(
        "companies:\n  - {name: Sitemap One, slug: sitemap-one, tier: dream, ats_vendor: sitemap, url_titles: false,"
        " board_token: www.metacareers.com/jobsearch/sitemap.xml, job_url_pattern: /profile/job_details/}\n")
    known = "https://www.metacareers.com/profile/job_details/222222222222222/"
    other = "https://www.metacareers.com/profile/job_details/333333333333333/"
    rec = fa.make_record({"name": "Sitemap One", "slug": "sitemap-one"}, title="ML Engineer", source="manual", source_url=known,
                         apply_url=known, ats_vendor="unknown", posted_at=None, location=None, remote="unknown",
                         comp=(None, None, None), employment_type=None, jd_text="x", now=NOW)
    fa.save_job(root, rec)
    page = ('<script type="application/ld+json">{"@type": "JobPosting", "title": "Sales Director",'
            ' "datePosted": "2026-09-20", "description": "sell"}</script>')
    urlset = ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
              f"<url><loc>{known}</loc></url><url><loc>{other}</loc></url></urlset>")
    fake = FakeHttp({("GET", "https://www.metacareers.com/robots.txt"): "User-agent: *\nDisallow: /login\n",
                     ("GET", "https://www.metacareers.com/jobsearch/sitemap.xml"): urlset, ("GET", other): page})
    go(root, os_root, http=fake, stages=["fetch"])
    fetched = [c[1] for c in fake.calls]
    assert known not in fetched and other in fetched
    assert json.loads((root / "state" / "sitemap" / "sitemap-one.json").read_text())[other]["title"] == "Sales Director"


def test_the_news_stage_runs_the_page_watch_and_reports_it(root_with_targets, os_root, monkeypatch):
    import news_watch as nw
    root = root_with_targets
    (root / "targets" / "news-watch.yaml").write_text(
        "credits_per_day: 5\npages:\n  - {id: g, kind: listing, url: 'https://www.google.com/about/careers/applications/"
        "jobs/results?q=ai', company: google, job_link: '/jobs/results/\\d+', why: test, tos: {checked: true}}\n")

    def snap(argv):
        out = Path(argv[argv.index("--out") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("[AI Engineer](https://www.google.com/about/careers/applications/jobs/results/123456-ai)\n")
        return 0
    monkeypatch.setattr(nw, "_snapshot", snap)
    res = go(root, os_root, http=http(), stages=["news"])
    assert res.receipt["stages"]["news"] == {"due": 1, "read": 1, "candidates": 1, "signals": 0, "skipped": 0,
                                             "errors": 0}
    assert "news" in pl.STAGES and pl.STAGES.index("news") == pl.STAGES.index("funding") + 1


def test_the_fetch_receipt_counts_each_vendor(root_with_targets, os_root):
    """Every vendor's boards, matches and errors are in the receipt, so a new adapter's yield
    is visible from its first run without a sources.yaml entry."""
    res = go(root_with_targets, os_root, http=http(), dry_run=True)
    st = res.receipt["stages"]["fetch"]
    by = st["by_vendor"]
    assert set(by) >= {"greenhouse", "lever", "ashby", "workday"}
    assert sum(v["matched"] for v in by.values()) == st["matched"]
    assert sum(v["boards"] for v in by.values()) == st["polled"]
    assert all(set(v) == {"boards", "matched", "errors"} for v in by.values())


def test_hn_jobstories_run_daily_even_when_the_monthly_thread_already_ran(root_with_targets, os_root):
    routes = http().routes
    routes[("GET", "https://hacker-news.firebaseio.com/v0/jobstories.json")] = load_fixture("hn_jobstories.json")["ids"]
    go(root_with_targets, os_root, http=FakeHttp(routes))                       # the monthly thread runs once
    again = go(root_with_targets, os_root, http=FakeHttp(routes), now=NOW + timedelta(days=1))
    hn = again.receipt["stages"]["hn"]
    assert hn["skipped"] == "already ran this month (2026-09)"
    assert hn["jobstories"]["stories"] == 2 and hn["jobstories"]["matched"] == 1


def test_aidevjobs_runs_only_with_a_sources_yaml_entry(root_with_targets, os_root):
    """AI Dev Jobs joins discovery once sources.yaml lists it."""
    routes = http().routes
    routes[("GET", "https://aidevboard.com/api/v1/jobs")] = load_fixture("aidevjobs_jobs.json")
    res = go(root_with_targets, os_root, http=FakeHttp(routes), dry_run=True, stages=["aidevjobs"])
    assert res.receipt["stages"]["aidevjobs"] == {"skipped": "no aidevjobs entry in sources.yaml"}
    (root_with_targets / "sources.yaml").write_text(
        "sources:\n  - id: aidevjobs\n    kind: api\n    enabled: true\n", encoding="utf-8")
    res = go(root_with_targets, os_root, http=FakeHttp(routes), dry_run=True, stages=["aidevjobs"], now=NOW)
    st = res.receipt["stages"]["aidevjobs"]
    assert st["requests"] >= 1 and "kept" in st and "new" in st


def test_remote_feeds_run_only_for_feeds_sources_yaml_lists(root_with_targets, os_root):
    """Remotive and RemoteOK join discovery feed by feed."""
    routes = http().routes
    routes[("GET", "https://remoteok.com/api")] = load_fixture("remoteok_api.json")["rows"]
    routes[("GET", "https://remotive.com/api/remote-jobs")] = load_fixture("remotive_jobs.json")
    res = go(root_with_targets, os_root, http=FakeHttp(routes), dry_run=True, stages=["remote_feeds"])
    assert res.receipt["stages"]["remote_feeds"] == {"skipped": "neither remotive nor remoteok is in sources.yaml"}
    (root_with_targets / "sources.yaml").write_text(
        "sources:\n  - id: remoteok\n    kind: aggregator-api\n    enabled: true\n", encoding="utf-8")
    fake = FakeHttp(routes)
    res = go(root_with_targets, os_root, http=fake, dry_run=True, stages=["remote_feeds"], now=NOW)
    st = res.receipt["stages"]["remote_feeds"]
    assert set(st["feeds"]) == {"remoteok"} and "new" in st
    assert not any("remotive" in c[1] for c in fake.calls)


# One board erroring is a warning, not a failed run. The routine verdict
# names the board and counts the boards that read fine; a stage error, no board polled or too many board errors fail.
def test_one_failing_board_gives_a_warn_verdict_naming_it_and_the_boards_that_read_fine(root_with_targets, os_root):
    res = go(root_with_targets, os_root, http=http(("GET", LEVER.format(LEVER_SLUG))))
    v = res.receipt["routine"]
    polled = res.receipt["stages"]["fetch"]["polled"]
    assert v["status"] == "warn" and v["sentinel"] == "NO_ACTION"
    assert LEVER_SLUG in v["note"] and f"boards read fine {polled - 1} of {polled}" in v["note"]
    assert res.receipt["board_errors"] and all(LEVER_SLUG in e for e in res.receipt["board_errors"])


def test_a_clean_run_is_ok(root_with_targets, os_root):
    v = go(root_with_targets, os_root, http=http()).receipt["routine"]
    assert v["status"] == "ok" and v["sentinel"] == "NO_ACTION" and "board_errors 0" in v["note"]


def test_a_stage_error_still_fails_the_run(root_with_targets, os_root, monkeypatch):
    monkeypatch.setattr(pl.dashboard, "build", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    v = go(root_with_targets, os_root, http=http()).receipt["routine"]
    assert v["status"] == "failed" and v["sentinel"] == "NEEDS_YOU" and "dashboard: RuntimeError: boom" in v["note"]


def test_more_board_errors_than_the_limit_fail(root_with_targets, os_root, monkeypatch):
    monkeypatch.setattr(pl, "BOARD_ERROR_LIMIT", 0)
    v = go(root_with_targets, os_root, http=http(("GET", LEVER.format(LEVER_SLUG)))).receipt["routine"]
    assert v["status"] == "failed" and v["sentinel"] == "NEEDS_YOU"


def test_no_board_polled_fails():
    v = pl.routine_verdict({"status": "ok", "errors": [], "board_errors": [], "stages": {"fetch": {"polled": 0}}})
    assert v["status"] == "failed" and "no board polled" in v["note"]


def test_the_cli_writes_the_routine_receipt_with_the_verdict(root_with_targets, os_root, monkeypatch):
    monkeypatch.setattr(pl, "_default_http", lambda: http(("GET", LEVER.format(LEVER_SLUG))))
    rc = pl.main(["--root", str(root_with_targets), "--os-root", str(os_root), "--workday-page-size", "3",
                  "--routine-receipt", "js-hunt-boards"], now=NOW, env={})
    assert rc == 0
    files = list((os_root / "logs" / "runs" / "js-hunt-boards").glob("*.md"))
    text = files[0].read_text()
    assert len(files) == 1 and " · warn\n" in text and "- sentinel: NO_ACTION" in text and LEVER_SLUG in text


# A `coverage` stage runs scripts/scout_coverage.py after the run's receipt is written (so this run
# counts), overwrites state/reports/scout-coverage-<date>.md each run, and the routine note carries the coverage line.
def test_the_coverage_stage_writes_the_day_report_and_the_routine_note(root_with_targets, os_root):
    (root_with_targets / "sources.yaml").write_text("sources:\n  - {id: greenhouse, kind: ats, enabled: true}\n")
    res = go(root_with_targets, os_root, http=http(), stages=["fetch", "dashboard", "coverage"])
    cov = res.receipt["stages"]["coverage"]
    report = root_with_targets / "state" / "reports" / "scout-coverage-2026-09-24.md"
    assert report.is_file() and "# Scout coverage · 2026-09-24" in report.read_text()
    assert cov["report"] == "state/reports/scout-coverage-2026-09-24.md" and cov["read"] >= 1
    saved = json.loads((root_with_targets / "state" / "reports" / "scout-coverage-2026-09-24.json").read_text())
    assert saved["date"] == "2026-09-24" and saved["states"]["read"] == cov["read"]   # --write-json: the dashboard reads it
    assert re.search(r"coverage \d+% \(read \d+, capped \d+, no reader \d+\)", res.receipt["routine"]["note"])


def test_coverage_is_computed_but_not_written_on_a_dry_run(root_with_targets, os_root):
    res = go(root_with_targets, os_root, http=http(), stages=["fetch", "coverage"], dry_run=True)
    assert not (root_with_targets / "state" / "reports").exists()
    assert "coverage" in res.receipt["routine"]["note"]


def test_coverage_is_skipped_when_not_selected(root_with_targets, os_root):
    res = go(root_with_targets, os_root, http=http(), stages=["fetch"])
    assert res.receipt["stages"]["coverage"] == {"skipped": "not selected"}
    assert "coverage" not in res.receipt["routine"]["note"]


def test_a_coverage_report_that_fails_never_fails_the_run(root_with_targets, os_root, monkeypatch):
    import scout_coverage

    def broken(*_a, **_k):
        raise FileNotFoundError("simulated: the coverage report could not read its inputs")
    monkeypatch.setattr(scout_coverage, "report", broken)
    res = go(root_with_targets, os_root, http=http(), stages=["fetch", "coverage"])
    assert res.receipt["routine"]["status"] == "ok" and "error" in res.receipt["stages"]["coverage"]
    assert "coverage: not measured (FileNotFoundError)" in res.receipt["routine"]["note"]


# Per-stage seconds in the receipt, so a slow stage is seen without a profiler.
def test_every_run_stage_has_its_seconds_and_skipped_ones_none(root_with_targets, os_root):
    res = go(root_with_targets, os_root, http=http(), stages=["fetch", "dedup", "dashboard"])
    st = res.receipt["stage_s"]
    assert set(st) == {"fetch", "dedup", "dashboard"} and all(isinstance(v, float) and v >= 0 for v in st.values())
    assert "hn" not in st                                   # skipped: no time
    assert sum(st.values()) <= res.receipt["duration_s"] + 1.0


def test_the_receipt_table_and_the_routine_note_carry_the_times(root_with_targets, os_root):
    res = go(root_with_targets, os_root, http=http(), stages=["fetch", "dashboard"])
    text = (os_root / "logs" / "runs" / "js-scout" / "2026-09-24.md").read_text()
    assert "| stage | result | s |" in text and re.search(r"\| fetch \| .* \| \d+\.\d \|", text)
    assert re.search(r"slowest: \w+ \d+ s", res.receipt["routine"]["note"])


def test_the_coverage_stage_is_timed_too(root_with_targets, os_root):
    (root_with_targets / "sources.yaml").write_text("sources:\n  - {id: greenhouse, kind: ats, enabled: true}\n")
    res = go(root_with_targets, os_root, http=http(), stages=["fetch", "coverage"])
    assert "coverage" in res.receipt["stage_s"]


# With --routine-receipt the run writes "started" (run_id, planned stages) before any stage and its verdict
# with the same run_id at the end; an exception still writes a failed verdict. A kill leaves started with no end, which
# the watchdog reports after its grace.
def _routine_text(os_root):
    files = list((os_root / "logs" / "runs" / "js-hunt-boards").glob("*.md"))
    return files[0].read_text() if files else ""


def test_the_started_line_comes_before_the_stages_and_the_end_carries_its_run_id(root_with_targets, os_root, monkeypatch):
    seen = {}
    real = pl.run_pipeline

    def spy(*a, **k):
        seen["text_at_start"] = _routine_text(os_root)
        return real(*a, **k)
    monkeypatch.setattr(pl, "run_pipeline", spy)
    monkeypatch.setattr(pl, "_default_http", lambda: http())
    pl.main(["--root", str(root_with_targets), "--os-root", str(os_root), "--workday-page-size", "3",
             "--stages", "fetch,dashboard", "--routine-receipt", "js-hunt-boards"], now=NOW, env={})
    start = seen["text_at_start"]
    assert "· started" in start and "- stages: fetch,dashboard" in start
    rid = re.search(r"- run_id: (\S+)", start).group(1)
    text = _routine_text(os_root)
    ends = [s for s in re.split(r"(?m)^(?=## Run )", text) if s.startswith("## Run ") and "· started" not in s.splitlines()[0]]
    assert len(ends) == 1 and f"- run_id: {rid}" in ends[0]


def test_an_exception_in_the_run_still_writes_a_failed_end(root_with_targets, os_root, monkeypatch):
    monkeypatch.setattr(pl, "run_pipeline", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    rc = pl.main(["--root", str(root_with_targets), "--os-root", str(os_root), "--routine-receipt", "js-hunt-boards"],
                 now=NOW, env={})
    text = _routine_text(os_root)
    assert rc == 1 and "· failed" in text and "- sentinel: NEEDS_YOU" in text and "RuntimeError: boom" in text
    rid = re.search(r"- run_id: (\S+)", text).group(1)
    assert text.count(f"- run_id: {rid}") == 2


def test_the_watchdog_sees_a_killed_run(root_with_targets, os_root, monkeypatch):
    import importlib.util
    from js_common import _KERNEL
    spec_ = importlib.util.spec_from_file_location("wd_for_test", _KERNEL / "watchdog.py")
    wd = importlib.util.module_from_spec(spec_)
    monkeypatch.setitem(__import__("sys").modules, "wd_for_test", wd)      # its dataclasses look themselves up here
    spec_.loader.exec_module(wd)
    monkeypatch.setattr(pl, "run_pipeline", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        pl.main(["--root", str(root_with_targets), "--os-root", str(os_root), "--routine-receipt", "js-hunt-boards"],
                now=NOW, env={})
    except KeyboardInterrupt:
        pass                                           # a kill writes nothing more: only the started line is there
    runs = wd.open_runs(os_root, "js-hunt-boards", wd.to_local(NOW) + __import__("datetime").timedelta(minutes=50))
    assert runs and runs[0]["overdue"]


# A run budget (CLI default 20 min); fetch stops polling at its share and the boards left lead the next
# run (state/pipeline/fetch-cursor.json); deferral is warn, a board unread for 24 h is failed; deferred boards are never
# closed; the cheap stages still run.
class Clock:
    def __init__(self, step):
        self.t, self.step = 0.0, step

    def __call__(self):
        self.t += self.step
        return self.t


def _board_slugs(root):
    return [c["slug"] for c in pl.load_companies(root)]


def test_fetch_stops_at_its_budget_and_names_the_boards_left(root_with_targets, os_root):
    slugs = _board_slugs(root_with_targets)
    # budget 1 min -> fetch share 36 s; each clock read is 10 s: a few boards are read, the rest deferred
    res = go(root_with_targets, os_root, http=http(), stages=["fetch", "dedup", "dashboard"], budget_min=1,
             clock=Clock(10))
    f = res.receipt["stages"]["fetch"]
    assert 0 < f["deferred"] < len(slugs) and res.receipt["deferred_boards"]
    assert set(res.receipt["deferred_boards"]) <= set(slugs)
    assert "dedup" in res.receipt["stage_s"] and "dashboard" in res.receipt["stage_s"]      # cheap stages still run
    v = res.receipt["routine"]
    assert v["status"] == "warn" and "deferred" in v["note"] and v["sentinel"] == "NO_ACTION"
    cursor = json.loads((root_with_targets / "state" / "pipeline" / "fetch-cursor.json").read_text())
    assert cursor["deferred"] == res.receipt["deferred_boards"]


def test_the_next_run_starts_with_the_deferred_boards_and_all_are_read_in_turn(root_with_targets, os_root):
    slugs = set(_board_slugs(root_with_targets))
    read: set[str] = set()
    for i in range(len(slugs) + 2):
        # The readers check the clock on every page, so a 10 s step cut the workday board in every run; at 2 s it
        # is cut once and finished the next run (the details it read are known by then)
        res = go(root_with_targets, os_root, http=http(), stages=["fetch"], budget_min=1, clock=Clock(2),
                 now=NOW + timedelta(hours=3 * i))
        deferred = set(res.receipt["deferred_boards"])
        read |= slugs - deferred
        if i == 0:
            first_deferred = list(res.receipt["deferred_boards"])
        if i == 1:
            assert first_deferred[0] not in deferred                       # the boards left last time go first
    assert read == slugs


def test_a_deferred_board_is_never_closed(root_with_targets, os_root):
    go(root_with_targets, os_root, http=http(), stages=["fetch"])                     # everything read once
    res = go(root_with_targets, os_root, http=http(), stages=["fetch"], budget_min=1, clock=Clock(10),
             now=NOW + timedelta(hours=3))
    assert res.receipt["stages"]["fetch"]["closed"] == 0


def test_a_board_unread_for_a_day_fails_the_run(root_with_targets, os_root):
    go(root_with_targets, os_root, http=http(), stages=["fetch"])
    cursor = root_with_targets / "state" / "pipeline" / "fetch-cursor.json"
    data = json.loads(cursor.read_text())
    victim = sorted(data["last_read"])[0]
    data["last_read"][victim] = "2026-09-20T00:00:00Z"
    data["deferred"] = [victim]
    cursor.write_text(json.dumps(data))
    res = go(root_with_targets, os_root, http=http(), stages=["fetch"], budget_min=1, clock=Clock(1000),
             now=NOW + timedelta(hours=3))
    assert victim in res.receipt["deferred_boards"]                 # a 1000 s clock step: past the deadline at once
    v = res.receipt["routine"]
    assert v["status"] == "failed" and victim in v["note"] and "24 h" in v["note"] and v["sentinel"] == "NEEDS_YOU"


# A yield record per board (new postings and seconds, the last YIELD_KEEP runs) in the
# fetch cursor; a board that cost SLOW_LANE_MIN_S or more and gave no new posting on its last two runs moves to the slow
# lane: read once in SLOW_LANE_H hours, named in the receipt for the Recruiter, never deferred or closed meanwhile.
def _cursor(root):
    return json.loads((root / "state" / "pipeline" / "fetch-cursor.json").read_text())


def test_each_board_run_leaves_a_yield_record(root_with_targets, os_root):
    go(root_with_targets, os_root, http=http(), stages=["fetch"], budget_min=30, clock=Clock(1))
    first = _cursor(root_with_targets)["yield"][GH_SLUG]
    assert len(first) == 1 and first[0]["new"] > 0 and first[0]["s"] > 0 and first[0]["at"]
    go(root_with_targets, os_root, http=http(), stages=["fetch"], budget_min=30, clock=Clock(1),
       now=NOW + timedelta(hours=3))
    rows = _cursor(root_with_targets)["yield"][GH_SLUG]
    assert len(rows) == 2 and rows[-1]["new"] == 0                     # the same board again: nothing new


def _seed_slow(root, slug, s, hours_ago):
    go(root, root.parent / "workspace", http=http(), stages=["fetch"])
    data = _cursor(root)
    data["yield"][slug] = [{"at": "2026-09-24T07:00:00Z", "new": 0, "s": s}, {"at": "2026-09-24T10:00:00Z", "new": 0, "s": s}]
    data["last_read"][slug] = pl.iso_utc(NOW + timedelta(hours=3) - timedelta(hours=hours_ago))
    (root / "state" / "pipeline" / "fetch-cursor.json").write_text(json.dumps(data))


def test_a_costly_board_with_nothing_new_twice_moves_to_the_slow_lane(root_with_targets, os_root):
    _seed_slow(root_with_targets, LEVER_SLUG, pl.SLOW_LANE_MIN_S * 5, hours_ago=3)
    res = go(root_with_targets, os_root, http=http(), stages=["fetch"], budget_min=30, clock=Clock(1),
             now=NOW + timedelta(hours=3))
    assert res.receipt["slow_lane"] == [LEVER_SLUG] and LEVER_SLUG not in res.receipt["deferred_boards"]
    assert res.receipt["stages"]["fetch"]["slow_lane"] == 1 and res.receipt["stages"]["fetch"]["closed"] == 0
    assert any("slow lane" in n and LEVER_SLUG in n and "Recruiter" in n for n in res.receipt["notes"])
    assert _cursor(root_with_targets)["last_read"][LEVER_SLUG] != pl.iso_utc(NOW + timedelta(hours=3))


def test_a_slow_lane_board_is_read_once_its_day_is_up(root_with_targets, os_root):
    _seed_slow(root_with_targets, LEVER_SLUG, pl.SLOW_LANE_MIN_S * 5, hours_ago=pl.SLOW_LANE_H + 1)
    res = go(root_with_targets, os_root, http=http(), stages=["fetch"], budget_min=30, clock=Clock(1),
             now=NOW + timedelta(hours=3))
    assert res.receipt["slow_lane"] == [] and _cursor(root_with_targets)["last_read"][LEVER_SLUG] == pl.iso_utc(NOW + timedelta(hours=3))


def test_a_cheap_board_with_nothing_new_keeps_every_run(root_with_targets, os_root):
    _seed_slow(root_with_targets, LEVER_SLUG, pl.SLOW_LANE_MIN_S - 1, hours_ago=3)
    res = go(root_with_targets, os_root, http=http(), stages=["fetch"], budget_min=30, clock=Clock(1),
             now=NOW + timedelta(hours=3))
    assert res.receipt["slow_lane"] == []


# A sitemap board went first and its 150-page cap (about 6.5 s a page) is
# longer than the whole fetch share, so one page-crawling board could defer every API board. The page crawlers now go
# last, and each gets at most SITEMAP_BOARD_S seconds a run (its pages wait for the next run, newest first).
def test_page_crawling_boards_are_read_after_the_api_boards():
    polled = [{"slug": "sitemap-one", "ats_vendor": "sitemap"}, {"slug": "a", "ats_vendor": "greenhouse"},
              {"slug": "sitemap-two", "ats_vendor": "sitemap"}, {"slug": "b", "ats_vendor": "ashby"}]
    cursor = {"deferred": ["sitemap-two"], "last_read": {"sitemap-one": "2026-10-01T00:00:00Z", "a": "2026-10-01T05:00:00Z",
                                                   "b": "2026-10-01T01:00:00Z"}}
    assert [c["slug"] for c in pl.fetch_order(polled, cursor)] == ["b", "a", "sitemap-two", "sitemap-one"]


def test_a_page_crawling_board_gets_its_own_time_cap_inside_the_run_deadline():
    clock = lambda: 1000.0  # noqa: E731
    assert pl.board_deadline({"ats_vendor": "sitemap"}, clock, 5000.0) == 1000.0 + pl.SITEMAP_BOARD_S
    assert pl.board_deadline({"ats_vendor": "sitemap"}, clock, 1010.0) == 1010.0          # the run deadline wins
    assert pl.board_deadline({"ats_vendor": "greenhouse"}, clock, 5000.0) == 5000.0
    assert pl.board_deadline({"ats_vendor": "sitemap"}, clock, None) == 1000.0 + pl.SITEMAP_BOARD_S


def test_without_a_budget_every_board_is_read_as_before(root_with_targets, os_root):
    res = go(root_with_targets, os_root, http=http(), stages=["fetch"])
    assert res.receipt["stages"]["fetch"]["deferred"] == 0 and res.receipt["deferred_boards"] == []


def test_the_cli_defaults_to_a_20_minute_budget_and_notes_it_in_the_started_line(root_with_targets, os_root, monkeypatch):
    seen = {}
    real = pl.run_pipeline
    monkeypatch.setattr(pl, "run_pipeline", lambda *a, **k: (seen.update(k), real(*a, **k))[1])
    monkeypatch.setattr(pl, "_default_http", lambda: http())
    pl.main(["--root", str(root_with_targets), "--os-root", str(os_root), "--workday-page-size", "3",
             "--stages", "fetch", "--routine-receipt", "js-hunt-boards"], now=NOW, env={})
    assert seen["budget_min"] == 20 and "- budget_min: 20" in _routine_text(os_root)


# Two runs overlapping would lose the first run's cursor writes and write stale job records; one run at a time
# (a non-blocking lock on state/pipeline/run.lock), and a run that finds it held closes its started line with a warn and exits 0.
def test_a_second_run_while_one_holds_the_lock_is_skipped_and_closes_its_started_line(root_with_targets, os_root, monkeypatch):
    import fcntl
    lock = root_with_targets / "state" / "pipeline" / "run.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    held = open(lock, "a+")
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setattr(pl, "run_pipeline", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    try:
        rc = pl.main(["--root", str(root_with_targets), "--os-root", str(os_root), "--routine-receipt", "js-hunt-boards"],
                     now=NOW, env={})
    finally:
        held.close()
    text = _routine_text(os_root)
    assert rc == 0 and "· started" in text and "· warn" in text and "skipped: previous run in progress" in text
    rid = re.search(r"- run_id: (\S+)", text).group(1)
    assert text.count(f"- run_id: {rid}") == 2


def test_the_lock_is_free_again_once_a_run_ends(root_with_targets, os_root, monkeypatch):
    import fcntl
    monkeypatch.setattr(pl, "_default_http", lambda: http())
    assert pl.main(["--root", str(root_with_targets), "--os-root", str(os_root), "--workday-page-size", "3",
                    "--stages", "fetch"], now=NOW, env={}) == 0
    with open(root_with_targets / "state" / "pipeline" / "run.lock", "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)          # raises if the run left it held


# P1: a board cut at the run deadline or erring is not a read: it neither moves last_read nor leaves a yield record (the
# slow lane reads only clean, whole reads); a cut board leads the next run. "Boards read fine" counts boards read.
def test_a_board_cut_at_the_run_deadline_is_deferred_and_not_counted_as_read(root_with_targets, os_root, monkeypatch):
    real = pl.fetch_company
    clock = Clock(1)

    def cut_lever(c, *a, **k):
        res = real(c, *a, **k)
        if c["slug"] == LEVER_SLUG:
            clock.t += 10_000                                     # the run deadline passed inside the lever board
            res.cut = True
        return res
    monkeypatch.setattr(pl, "fetch_company", cut_lever)
    res = go(root_with_targets, os_root, http=http(), stages=["fetch"], budget_min=30, clock=clock)
    cur = _cursor(root_with_targets)
    f = res.receipt["stages"]["fetch"]
    assert res.receipt["cut_boards"] == [LEVER_SLUG] and LEVER_SLUG not in res.receipt["deferred_boards"]
    assert cur["deferred"][-1] == LEVER_SLUG                          # after the boards never reached (no starvation)
    assert LEVER_SLUG not in cur["last_read"] and LEVER_SLUG not in cur["yield"]
    assert f["cut"] == 1 and f["polled"] + f["deferred"] == len(_board_slugs(root_with_targets))
    assert "cut 1 at the run deadline: " + LEVER_SLUG in res.receipt["routine"]["note"]


# A retry wait or a polite pause that would end past the deadline cuts a board while the
# clock is still before it; for a board whose deadline is the run's, any cut is the run's cut
def test_a_board_cut_just_before_the_run_deadline_is_still_not_a_read(root_with_targets, os_root, monkeypatch):
    real = pl.fetch_company

    def cut_lever(c, *a, **k):
        res = real(c, *a, **k)
        if c["slug"] == LEVER_SLUG:
            res.cut = True                                        # the clock never passes the deadline
        return res
    monkeypatch.setattr(pl, "fetch_company", cut_lever)
    res = go(root_with_targets, os_root, http=http(), stages=["fetch"], budget_min=30, clock=Clock(1))
    cur = _cursor(root_with_targets)
    assert res.receipt["cut_boards"] == [LEVER_SLUG] and LEVER_SLUG not in cur["last_read"] and LEVER_SLUG not in cur["yield"]
    assert "boards read fine 5 of 6" in res.receipt["routine"]["note"]


def test_a_page_crawler_cut_by_its_own_cap_is_a_read(root_with_targets, os_root, monkeypatch):
    monkeypatch.setattr(pl, "PAGE_CRAWLERS", ("lever",))         # the lever board plays a page crawler here
    real = pl.fetch_company

    def capped(c, *a, **k):
        res = real(c, *a, **k)
        if c["slug"] == LEVER_SLUG:
            res.cut = True                                        # its own SITEMAP_BOARD_S cap, well inside the run's
        return res
    monkeypatch.setattr(pl, "fetch_company", capped)
    res = go(root_with_targets, os_root, http=http(), stages=["fetch"], budget_min=30, clock=Clock(1))
    assert res.receipt["cut_boards"] == [] and LEVER_SLUG in _cursor(root_with_targets)["last_read"]


def test_the_lock_is_released_when_the_run_raises(root_with_targets, os_root, monkeypatch):
    import fcntl
    monkeypatch.setattr(pl, "run_pipeline", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert pl.main(["--root", str(root_with_targets), "--os-root", str(os_root), "--stages", "fetch"], now=NOW, env={}) == 1
    with open(root_with_targets / "state" / "pipeline" / "run.lock", "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)


# A deferred board's wait counts from the older of its last read and its first deferral
def test_a_deferred_board_waits_from_its_last_read_not_its_last_deferral():
    polled = [{"slug": "a", "ats_vendor": "greenhouse"}, {"slug": "sitemap-one", "ats_vendor": "sitemap"}]
    cursor = {"deferred": ["sitemap-one"], "deferred_since": {"sitemap-one": "2026-09-24T05:00:00Z"},
              "last_read": {"a": "2026-09-24T06:00:00Z", "sitemap-one": "2026-09-23T08:00:00Z"}}
    now = pl.parse_ts("2026-09-24T07:00:00Z")
    assert [c["slug"] for c in pl.fetch_order(polled, cursor, now)] == ["sitemap-one", "a"]


def test_an_erring_board_moves_neither_last_read_nor_the_yield(root_with_targets, os_root):
    go(root_with_targets, os_root, http=http(("GET", LEVER.format(LEVER_SLUG))), stages=["fetch"], budget_min=30, clock=Clock(1))
    cur = _cursor(root_with_targets)
    assert LEVER_SLUG not in cur["last_read"] and LEVER_SLUG not in cur["yield"] and GH_SLUG in cur["last_read"]


def test_boards_read_fine_counts_only_the_boards_read(root_with_targets, os_root):
    res = go(root_with_targets, os_root, http=http(), stages=["fetch"], budget_min=1, clock=Clock(10))
    f = res.receipt["stages"]["fetch"]
    total = len(_board_slugs(root_with_targets))
    assert f["deferred"] > 0 and f["polled"] + f["deferred"] == total
    assert f"boards read fine {f['polled']} of {f['polled']}" in res.receipt["routine"]["note"]


# P1: the cursor said "read" before the jobs were saved, so a run killed between the two lost those postings for good
def test_the_cursor_is_written_only_after_the_jobs_are_saved(root_with_targets, os_root, monkeypatch):
    def killed(*a, **k):
        raise RuntimeError("killed while saving")
    monkeypatch.setattr(pl, "save_job", killed)
    try:
        go(root_with_targets, os_root, http=http(), stages=["fetch"], budget_min=30, clock=Clock(1))
    except RuntimeError:
        pass
    assert not (root_with_targets / "state" / "pipeline" / "fetch-cursor.json").exists()


# P2: a board deferred or unread for SLOW_LANE_H hours or more goes first, page crawler or not (no starvation)
def test_a_board_waiting_a_day_goes_first_even_a_page_crawler():
    polled = [{"slug": "a", "ats_vendor": "greenhouse"}, {"slug": "sitemap-one", "ats_vendor": "sitemap"},
              {"slug": "b", "ats_vendor": "ashby"}, {"slug": "sitemap-two", "ats_vendor": "sitemap"}]
    cursor = {"deferred": ["sitemap-two"], "deferred_since": {"sitemap-two": "2026-09-23T06:00:00Z"},
              "last_read": {"a": "2026-09-24T05:00:00Z", "b": "2026-09-24T01:00:00Z", "sitemap-one": "2026-09-23T09:00:00Z",
                            "sitemap-two": "2026-09-23T05:00:00Z"}}
    now = pl.parse_ts("2026-09-24T07:00:00Z")
    assert [c["slug"] for c in pl.fetch_order(polled, cursor, now)] == ["sitemap-two", "sitemap-one", "b", "a"]


# P2: the cursor's temporary file had one fixed name, so two writers could swap half-written files
def test_the_cursor_write_does_not_depend_on_a_fixed_temporary_name(root_with_targets, os_root):
    fixed = root_with_targets / "state" / "pipeline" / ".fetch-cursor.json.tmp"
    fixed.mkdir(parents=True)                                     # a fixed-name temp file would fail on this
    go(root_with_targets, os_root, http=http(), stages=["fetch"], budget_min=30, clock=Clock(1))
    assert _cursor(root_with_targets)["last_read"]


# js-hunt-signals (`--stages funding,news`) and js-hunt-boards (a poll) can start together. A plain lock skipped
# whichever came second, so the poll could be lost to a short signals run. The
# holder writes its stages into the lock file: a poll skips only when a poll holds the lock (the same work is
# under way); any other overlap waits for the lock, up to LOCK_WAIT_S, then skips with a warn receipt.
def _hold(root, stages=None):
    import fcntl
    lock = root / "state" / "pipeline" / "run.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock, "a+")
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if stages is not None:
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps({"pid": 1, "stages": stages}))
        fh.flush()
    return fh


def test_a_poll_skips_when_a_poll_holds_the_lock(root_with_targets, os_root, monkeypatch):
    held = _hold(root_with_targets, ["fetch", "dedup"])
    waits = []
    monkeypatch.setattr(pl, "_lock_sleep", waits.append)
    try:
        assert pl.main(["--root", str(root_with_targets), "--os-root", str(os_root), "--stages", "fetch",
                        "--routine-receipt", "js-hunt-boards"], now=NOW, env={}) == 0
    finally:
        held.close()
    assert "skipped: previous run in progress" in _routine_text(os_root) and waits == [1]   # one look again, no wait


def test_a_poll_waits_for_a_signals_run_then_runs(root_with_targets, os_root, monkeypatch, capsys):
    held = _hold(root_with_targets, ["funding", "news"])
    waits = []

    def sleep(s):                                    # the signals run ends while we wait
        waits.append(s)
        held.close()
    monkeypatch.setattr(pl, "_lock_sleep", sleep)
    monkeypatch.setattr(pl, "_default_http", lambda: http())
    assert pl.main(["--root", str(root_with_targets), "--os-root", str(os_root), "--workday-page-size", "3",
                    "--stages", "fetch", "--routine-receipt", "js-hunt-boards"], now=NOW, env={}) == 0
    text = _routine_text(os_root)
    assert waits and "skipped" not in text and "boards polled" in text
    assert "waiting for the lock (held by a run of funding, news)" in capsys.readouterr().err   # a hand run says so
    assert (root_with_targets / "state" / "pipeline" / "run.lock").read_text() == ""   # no stale holder record left


def test_a_wait_that_outlasts_the_limit_skips_with_a_warn(root_with_targets, os_root, monkeypatch):
    held = _hold(root_with_targets, ["fetch"])
    monkeypatch.setattr(pl, "LOCK_WAIT_S", 10)
    monkeypatch.setattr(pl, "_lock_sleep", lambda s: None)
    t = {"now": 0.0}

    def clock():
        t["now"] += 6
        return t["now"]
    monkeypatch.setattr(pl, "_lock_clock", clock)
    try:
        assert pl.main(["--root", str(root_with_targets), "--os-root", str(os_root), "--stages", "funding,news",
                        "--routine-receipt", "js-hunt-signals"], now=NOW, env={}) == 0
    finally:
        held.close()
    text = (os_root / "logs" / "runs" / "js-hunt-signals").glob("*.md").__next__().read_text()
    assert "· warn" in text and re.search(r"skipped: waited 1\d s for the lock \(held by a run of fetch\)", text)


# lock review (non-blocking findings, fixed): a holder that has not written its stages yet (or a stale record) must not
# cost the poll; only an OSError meaning "held" is held; a failed holder write never leaks the lock
def test_a_poll_confirms_before_skipping_so_a_holder_mid_write_does_not_cost_it(root_with_targets, os_root, monkeypatch):
    held = _hold(root_with_targets)                          # wrote nothing yet
    monkeypatch.setattr(pl, "_lock_sleep", lambda s: held.close())   # ...and it was signals, ending at once
    monkeypatch.setattr(pl, "_default_http", lambda: http())
    assert pl.main(["--root", str(root_with_targets), "--os-root", str(os_root), "--workday-page-size", "3",
                    "--stages", "fetch", "--routine-receipt", "js-hunt-boards"], now=NOW, env={}) == 0
    assert "skipped" not in _routine_text(os_root)


def test_a_poll_still_skips_when_the_holder_says_nothing_after_the_confirm(root_with_targets, os_root, monkeypatch):
    held = _hold(root_with_targets)
    monkeypatch.setattr(pl, "_lock_sleep", lambda s: None)
    try:
        assert pl.main(["--root", str(root_with_targets), "--os-root", str(os_root), "--stages", "fetch",
                        "--routine-receipt", "js-hunt-boards"], now=NOW, env={}) == 0
    finally:
        held.close()
    assert "skipped: previous run in progress" in _routine_text(os_root)


def test_a_flock_error_that_is_not_held_is_raised_not_waited_on(root_with_targets, os_root, monkeypatch):
    import errno
    import fcntl
    monkeypatch.setattr(fcntl, "flock", lambda *a: (_ for _ in ()).throw(OSError(errno.ENOLCK, "no locks")))
    monkeypatch.setattr(pl, "_lock_sleep", lambda s: (_ for _ in ()).throw(AssertionError("must not wait")))
    with pytest.raises(OSError):
        pl._take_lock(root_with_targets, ["funding"])


# feed_watch.py and discover_companies.py are pipeline stages, opt-in (only when --stages names them: a bare run reads no new hosts), for js-hunt-signals.
FEED = "https://acme.example/blog/rss.xml"
RSS = ('<?xml version="1.0"?><rss><channel><item><title>We are hiring: Applied AI Engineer</title>'
       '<link>https://acme.example/blog/hiring</link><pubDate>Wed, 23 Sep 2026 10:00:00 GMT</pubDate></item>'
       '</channel></rss>')


def test_the_feeds_stage_reads_the_companies_feeds_and_writes_the_day_report(root_with_targets, os_root, monkeypatch):
    import feed_watch
    path = root_with_targets / "targets" / "companies.yaml"
    path.write_text(path.read_text() + f"  - {{name: Acme, slug: acme, ats_vendor: other, feeds: ['{FEED}']}}\n")
    monkeypatch.setattr(feed_watch, "_http", lambda: FakeHttp({("GET", FEED): RSS}))
    monkeypatch.setattr(feed_watch, "PAUSE", 0)
    res = go(root_with_targets, os_root, http=http(), stages=["feeds"])
    out = res.receipt["stages"]["feeds"]
    report = root_with_targets / "state" / "feeds" / "feed-watch-2026-09-24.md"
    assert out["feeds_read"] == 1 and out["posts"] == 1 and report.is_file() and "Applied AI Engineer" in report.read_text()
    go(root_with_targets, os_root, http=http(), stages=["feeds"], dry_run=True, now=NOW + timedelta(days=1))
    assert not (root_with_targets / "state" / "feeds" / "feed-watch-2026-09-25.md").exists()


def test_the_discover_stage_finds_boards_for_yesterdays_new_companies(root_with_targets, os_root, monkeypatch):
    import scout_coverage
    seen = {}

    def report(day, root, os_root=None, *a, **k):
        seen["day"], seen["os_root"] = day, os_root
        return {"new_companies": [{"slug": "acme-labs"}]}       # posted, not a target (a target is never re-found)
    monkeypatch.setattr(scout_coverage, "report", report)
    board = FakeHttp({("GET", "https://boards-api.greenhouse.io/v1/boards/acme-labs/jobs"):
                      {"jobs": [{"title": "Applied AI Engineer"}, {"title": "Office Manager"}]}})
    res = go(root_with_targets, os_root, http=board, stages=["discover"])
    out = res.receipt["stages"]["discover"]
    assert seen["day"].isoformat() == "2026-09-23"                   # the whole previous day, not this morning's
    assert seen["os_root"] == os_root                                 # this run's receipts, never the real OS's
    assert out["companies"] == 1 and out["boards"] == 1
    report_file = root_with_targets / "state" / "reports" / "new-companies-2026-09-23.md"   # named by the day it covers
    assert report_file.is_file() and "2026-09-23" in report_file.read_text().splitlines()[0]
    assert not (root_with_targets / "state" / "reports" / "new-companies-2026-09-24.md").exists()


def test_a_bare_run_does_not_run_the_opt_in_stages(root_with_targets, os_root, monkeypatch):
    monkeypatch.setattr(pl, "run_pipeline", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop after started")))
    pl.main(["--root", str(root_with_targets), "--os-root", str(os_root), "--routine-receipt", "js-hunt-boards"],
            now=NOW, env={})
    started = re.search(r"- stages: (\S+)", _routine_text(os_root)).group(1).split(",")
    assert "feeds" not in started and "discover" not in started and "fetch" in started   # the started line tells true
    monkeypatch.undo()
    res = go(root_with_targets, os_root, http=http(), dry_run=True)
    assert res.receipt["stages"]["feeds"] == {"skipped": "not selected"}
    assert res.receipt["stages"]["discover"] == {"skipped": "not selected"}
