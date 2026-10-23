"""fetch_ats.py: vendor normalization (recorded public API answers with the company names replaced), title filter, upsert."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW, FakeHttp, load_fixture, GH, LEVER, ASHBY, FIXTURES

import fetch_ats as fa  # noqa: E402

LABCO = {"name": "Labco", "slug": "labco", "ats_vendor": "greenhouse", "board_token": "labco"}
ROBOTCO = {"name": "Robotco", "slug": "robotco", "ats_vendor": "lever", "board_token": "robotco"}
SHIELD = {"name": "Droneco ai", "slug": "droneco-ai", "ats_vendor": "lever", "board_token": "dronecoai"}
MODELCO = {"name": "Modelco", "slug": "modelco", "ats_vendor": "ashby", "board_token": "modelco"}
CHIPCO = {"name": "CHIPCO", "slug": "chipco", "ats_vendor": "workday",
          "board_token": "chipco.wd5/CHIPCOExternalCareerSite"}


COMPANIES_YAML = """\
companies:
  - name: Labco
    slug: labco
    tier: dream
    ats_vendor: greenhouse
    board_token: labco
    careers_url: https://www.labco.example/careers
    sponsorship_stance: sponsors
    h1b_approvals_recent: 143
    notes: ""
  - {name: Robotco, slug: robotco, tier: strong, ats_vendor: lever, board_token: robotco}
  - {name: Modelco, slug: modelco, tier: dream, ats_vendor: ashby, board_token: modelco}
  - name: CHIPCO
    slug: chipco
    tier: strong
    ats_vendor: workday
    board_token: chipco.wd5/CHIPCOExternalCareerSite
    careers_url: https://chipco.wd5.myworkdayjobs.com/CHIPCOExternalCareerSite
  - {name: Droneco AI, slug: droneco-ai, tier: fallback, ats_vendor: lever, board_token: dronecoai}
  - {name: Legacy Corp, slug: legacy-corp, tier: fallback, ats_vendor: icims, board_token: legacy}
"""


@pytest.fixture
def root_with_targets(root):
    (root / "targets").mkdir()
    (root / "targets" / "companies.yaml").write_text(COMPANIES_YAML, encoding="utf-8")
    return root


WD_BASE = "https://chipco.wd5.myworkdayjobs.com/wday/cxs/chipco/CHIPCOExternalCareerSite"


def ats_routes():
    """Routes for the five fixture boards (greenhouse, 2x lever, ashby, workday)."""
    p0 = load_fixture("workday_chipco_jobs_p0.json")
    p1 = load_fixture("workday_chipco_jobs_p1.json")
    routes = {
        ("GET", GH.format("labco")): load_fixture("greenhouse_labco.json"),
        ("GET", LEVER.format("robotco")): load_fixture("lever_robotco.json"),
        ("GET", LEVER.format("dronecoai")): load_fixture("lever_dronecoai.json"),
        ("GET", ASHBY.format("modelco")): load_fixture("ashby_modelco.json"),
        ("POST", WD_BASE + "/jobs"): lambda payload: p0 if payload["offset"] == 0 else p1,
    }
    for page in (p0, p1):
        for post in page["jobPostings"]:
            path = FIXTURES / f"workday_chipco_detail_{post['bulletFields'][0]}.json"
            if path.exists():
                routes[("GET", WD_BASE + post["externalPath"])] = json.loads(path.read_text())
    return routes


def h(text: str, n: int) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]


def by_url(records):
    return {r["source_url"]: r for r in records}


# ----------------------------------------------------------------------------- greenhouse
def test_greenhouse_normalizes_to_job_schema():
    res = fa.fetch_greenhouse(LABCO, FakeHttp(ats_routes()), NOW, title_filter=None)
    rec = by_url(res.records)["https://job-boards.greenhouse.io/labco/jobs/100101"]
    expected = {
        "id": "labco-480101",
        "company": "Labco",
        "company_slug": "labco",
        "title": "Applied AI, Solutions Research Engineer",
        "req_id": "480101",
        "source": "greenhouse",
        "ats_vendor": "greenhouse",
        "apply_url": "https://job-boards.greenhouse.io/labco/jobs/100101",
        "posted_at": "2026-09-04T15:26:23Z",
        "first_seen": "2026-09-24T13:00:00Z",
        "last_seen": "2026-09-24T13:00:00Z",
        "location": "San Francisco, CA | New York City, NY",
        "remote": "onsite",
        "comp_min": 300000,
        "comp_max": 400000,
        "comp_currency": "USD",
        "seniority_est": "unspecified",
        "freshness_hours": 477,
        "status": "discovered",
    }
    for key, value in expected.items():
        assert rec[key] == value, key
    assert len(rec["jd_hash"]) == 40


def test_greenhouse_body_is_unescaped_plain_text_wrapped_untrusted_and_not_truncated():
    res = fa.fetch_greenhouse(LABCO, FakeHttp(ats_routes()), NOW, title_filter=None)
    body = by_url(res.records)["https://job-boards.greenhouse.io/labco/jobs/100101"]["_body"]
    assert "<untrusted_jd>" in body and "</untrusted_jd>" in body
    assert "<p>" not in body and "&lt;" not in body and "&nbsp;" not in body
    # the visa line sits at the very end of the JD, after the pay block; a clipped body would lose it
    assert "We do sponsor visas!" in body


def test_greenhouse_non_usd_pay_range_and_remote_metadata():
    res = fa.fetch_greenhouse(LABCO, FakeHttp(ats_routes()), NOW, title_filter=None)
    recs = by_url(res.records)
    london = recs["https://job-boards.greenhouse.io/labco/jobs/100102"]
    assert (london["comp_min"], london["comp_max"], london["comp_currency"]) == (225000, 255000, "GBP")
    assert london["location"] == "London, UK"
    universes = recs["https://job-boards.greenhouse.io/labco/jobs/100103"]
    assert universes["remote"] == "remote"
    assert universes["id"] == "labco-pip-2001"
    assert universes["posted_at"] == "2026-01-09T16:41:32Z"


def test_greenhouse_board_filters_titles_and_disambiguates_shared_req_ids():
    res = fa.fetch_greenhouse(LABCO, FakeHttp(ats_routes()), NOW, title_filter=fa.default_title_filter())
    base = "https://job-boards.greenhouse.io/labco/jobs/"
    assert {r["id"] for r in res.records} == {
        "labco-480101",
        "labco-480102",
        "labco-pip-2001",
        "labco-pip-2002",
        f"labco-7001-{h(base + '100104', 6)}",
        f"labco-7001-{h(base + '100105', 6)}",
    }
    assert len(res.all_urls) == 8          # the unfiltered board, used for closure detection
    assert res.complete is True


@pytest.mark.parametrize("title,keep", [
    ("Applied AI, Solutions Research Engineer", True),
    ("Research Engineer, Simulations", True),
    ("Software Engineer, LLM Networking", True),
    ("Forward Deployed Engineer", True),
    ("Senior Machine Learning Engineer, Perception - Robot Navigation", True),
    ("Member of Technical Staff (full-stack + AI evals)", True),
    ("Senior SWE, AI Automation Engr, Senior PM", True),
    ("Software Engineer, Agent Infrastructure", True),
    ("Senior Staff Engineer, ML Ops (R1141)", True),
    ("Account Executive, Digital Natives", False),
    ("Technical Program Manager, Cloud Serving", False),
    ("Pre-Sales Program Lead, Forward Deployed Engineering", False),
    ("Director, Perception", False),
    ("Robot System Test Engineer", False),
    ("Senior Developer Relations Manager, Robotics Platforms", False),
    ("Staff Software Engineer, Payments", False),
])
def test_default_title_filter(title, keep):
    assert fa.default_title_filter().matches(title) is keep


def test_title_filter_from_roles_md_replaces_defaults(root):
    (root / "targets").mkdir()
    (root / "targets" / "roles.md").write_text(
        "# Target roles\n\nSome prose.\n\n## Title patterns\n- Data Infrastructure\n- `\\bprivacy\\b`\n\n"
        "## Exclude titles\n- Technical Program Manager\n\n## Notes\n- not a pattern\n",
        encoding="utf-8")
    tf = fa.load_title_filter(root)
    assert tf.matches("Software Engineer, Data Infrastructure") is True
    assert tf.matches("Research Engineer, Privacy") is True
    assert tf.matches("Machine Learning Engineer, Pricing AI/ML") is False
    assert tf.matches("Technical Program Manager, Data Infrastructure") is False


ROLES_MD_REAL_FORMAT = r"""---
title: Target role definition (read by fit-scorer)
---

# Target roles

## 1. Included titles

| Family | Title patterns to accept | Lens |
|---|---|---|
| Agent engineer | `software engineer, agent*`, `agent platform`, `ai agent * engineer` | agentic-systems |
| Forward deployed | `forward deployed (software\|ai)? engineer`, `fde` | forward-deployed |
| Member of Technical Staff | `member of technical staff`, `mts` (read the team suffix) | by suffix |
| Founding engineer (AI) | `founding (ai\|ml\|software\|full stack) engineer` at a funded AI startup | agentic-systems |

## 2. Excluded titles (skip without scoring)

- Management: `manager`, `director`, `head of`, `vp`, `lead` when it means people management (read the JD).
- Level above band: `staff`, `principal`, `senior staff`.
- Internships, new-grad programs, fellowships, residencies (they do not sponsor visas for fellows).

## 3. Seniority band

- `staff` here is prose, not a pattern section.
"""


@pytest.mark.parametrize("title,keep", [
    ("Software Engineer, Agent Infrastructure", True),
    ("AI Agent Platform Engineer", True),
    ("Forward Deployed Engineer", True),
    ("Forward Deployed AI Engineer", True),
    ("Forward Deployed Software Engineer - Bay Area", True),
    ("Member of Technical Staff", True),                 # 'staff' inside the included phrase
    ("Founding AI Engineer", True),
    ("Staff Software Engineer, Agent Platform", False),  # 'staff' outside the included phrase
    ("Engineering Manager, Agent Platform", False),
    ("Account Executive, Digital Natives", False),
    ("Machine Learning Engineer", False),                # roles.md replaces the defaults
])
def test_title_filter_reads_the_real_roles_md_format(root, title, keep):
    (root / "targets").mkdir()
    (root / "targets" / "roles.md").write_text(ROLES_MD_REAL_FORMAT, encoding="utf-8")
    assert fa.load_title_filter(root).matches(title) is keep


def test_title_filter_defaults_when_roles_md_absent(root):
    assert fa.load_title_filter(root).matches("Forward Deployed Engineer") is True


# ----------------------------------------------------------------------------- lever
def test_lever_normalization():
    post = load_fixture("lever_robotco.json")[0]
    res = fa.fetch_lever(ROBOTCO, FakeHttp(ats_routes()), NOW, title_filter=None)
    rec = by_url(res.records)[post["hostedUrl"]]
    assert rec["id"] == f"robotco-{h(post['hostedUrl'], 8)}"
    assert rec["title"] == "Machine Learning Engineer - Motion Forecasting for Delivery Robots"
    assert rec["req_id"] is None
    assert rec["apply_url"] == post["applyUrl"]
    assert rec["source"] == "lever" and rec["ats_vendor"] == "lever"
    assert rec["posted_at"] == "2026-07-23T04:48:58Z"
    assert rec["location"] == "Foster City, CA"
    assert rec["remote"] == "hybrid"
    assert (rec["comp_min"], rec["comp_max"], rec["comp_currency"]) == (199000, 283000, "USD")
    assert rec["employment_type"] == "full-time"
    assert post["lists"][0]["text"].strip() in rec["_body"]


def test_lever_contract_student_worker_is_contract_intern():
    res = fa.fetch_lever(ROBOTCO, FakeHttp(ats_routes()), NOW, title_filter=fa.default_title_filter())
    titles = {r["title"]: r for r in res.records}
    assert set(titles) == {
        "Machine Learning Engineer - Motion Forecasting for Delivery Robots",
        "Senior / Staff Machine Learning Engineer - Motion Forecasting for Delivery Robots",
        "Contract Student Worker - Machine Learning Engineer - Data Curation & VLM (Full-time)",
        "Lead AI Engineer – Enterprise Data & AI Platforms",
    }
    student = titles["Contract Student Worker - Machine Learning Engineer - Data Curation & VLM (Full-time)"]
    assert student["employment_type"] == "contract"
    assert student["seniority_est"] == "intern"


def test_lever_req_id_from_title_and_shared_req_ids_get_distinct_ids():
    posts = load_fixture("lever_dronecoai.json")
    res = fa.fetch_lever(SHIELD, FakeHttp(ats_routes()), NOW, title_filter=None)
    recs = by_url(res.records)
    mlops = [p for p in posts if p["text"].startswith("Senior Staff Engineer, ML Ops")]
    ids = {recs[p["hostedUrl"]]["id"] for p in mlops}
    assert ids == {f"droneco-ai-r1141-{h(p['hostedUrl'], 6)}" for p in mlops}
    assert all(recs[p["hostedUrl"]]["req_id"] == "R1141" for p in mlops)
    systems = recs[posts[0]["hostedUrl"]]
    assert systems["id"] == "droneco-ai-r1103"
    assert "San Mateo, California" in systems["location"]


# ----------------------------------------------------------------------------- ashby
def test_ashby_normalization():
    res = fa.fetch_ashby(MODELCO, FakeHttp(ats_routes()), NOW, title_filter=None)
    recs = {r["title"]: r for r in res.records}
    mle = recs["Machine Learning Engineer, Pricing AI/ML"]
    url = "https://jobs.ashbyhq.com/modelco/job-2001"
    assert mle["source_url"] == url
    assert mle["apply_url"] == url + "/application"
    assert mle["id"] == f"modelco-{h(url, 8)}"
    assert (mle["comp_min"], mle["comp_max"], mle["comp_currency"]) == (381000, 555000, "USD")
    assert mle["posted_at"] == "2026-09-21T19:33:24Z"
    assert mle["freshness_hours"] == 65
    assert mle["remote"] == "unknown"
    assert mle["employment_type"] == "full-time"
    assert recs["Forward Deployed Software Engineer - Bay Area"]["remote"] == "hybrid"
    privacy = recs["Research Engineer, Data Protection"]
    assert (privacy["comp_min"], privacy["comp_max"]) == (None, 500000)   # a $0 floor is not data
    infra = recs["Software Engineer, Data Infrastructure"]
    assert infra["location"] == "San Francisco | New York City | Seattle | Mountain View"


# ----------------------------------------------------------------------------- workday
def test_workday_paginates_and_calls_details_only_for_title_matches():
    http = FakeHttp(ats_routes())
    res = fa.fetch_workday(CHIPCO, http, NOW, title_filter=fa.default_title_filter(), page_size=3)
    posts = [c for c in http.calls if c[0] == "POST"]
    assert [p[2]["offset"] for p in posts] == [0, 3]
    assert all(p[2]["limit"] == 3 for p in posts)
    details = [c[1] for c in http.calls if c[0] == "GET"]
    assert len(details) == 4 and not any("JR1003" in d for d in details)
    assert len(res.all_urls) == 5
    rec = {r["req_id"]: r for r in res.records}["JR1004"]
    assert rec["id"] == "chipco-jr1004"
    assert rec["title"] == "Senior Machine Learning Engineer, Perception - Robot Navigation"
    assert rec["location"] == "US, CA, Santa Clara | US, CA, Remote"
    assert rec["remote"] == "remote"
    assert rec["posted_at"] == "2026-09-21"
    assert rec["freshness_hours"] == 85
    assert rec["employment_type"] == "full-time"
    assert rec["source_url"] == ("https://chipco.wd5.myworkdayjobs.com/CHIPCOExternalCareerSite/job/US-CA-Santa-Clara/"
                                 "Senior-Machine-Learning-Engineer--Perception---Robot-Navigation_JR1004")
    assert rec["seniority_est"] == "senior"
    assert "<untrusted_jd>" in rec["_body"]


def test_workday_trusts_total_from_first_page_only():
    """Workday returns total=0 on every page after the first (seen live 2026-09-24)."""
    p0 = load_fixture("workday_chipco_jobs_p0.json")
    p1 = load_fixture("workday_chipco_jobs_p1.json")
    postings = p0["jobPostings"] + p1["jobPostings"]
    pages = {0: dict(p0, jobPostings=postings[0:2], total=5),
             2: dict(p1, jobPostings=postings[2:4], total=0),
             4: dict(p1, jobPostings=postings[4:5], total=0)}
    routes = ats_routes()
    routes[("POST", WD_BASE + "/jobs")] = lambda payload: pages[payload["offset"]]
    res = fa.fetch_workday(CHIPCO, FakeHttp(routes), NOW, title_filter=None, page_size=2)
    assert len(res.all_urls) == 5


def test_workday_respects_max_pages():
    routes = ats_routes()
    http = FakeHttp(routes)
    res = fa.fetch_workday(CHIPCO, http, NOW, title_filter=None, page_size=3, max_pages=1)
    assert len([c for c in http.calls if c[0] == "POST"]) == 1
    assert res.complete is False


@pytest.mark.parametrize("entry", [
    {"board_token": "chipco.wd5/CHIPCOExternalCareerSite"},
    {"board_token": "chipco|wd5|CHIPCOExternalCareerSite"},
    {"careers_url": "https://chipco.wd5.myworkdayjobs.com/CHIPCOExternalCareerSite"},
    {"careers_url": "https://chipco.wd5.myworkdayjobs.com/en-US/CHIPCOExternalCareerSite/"},
])
def test_parse_workday_board(entry):
    assert fa.parse_workday_board(entry) == ("chipco", "wd5", "CHIPCOExternalCareerSite")


def test_workday_host_tenant_site_token_from_companies_yaml():
    entry = {"name": "CHIPCO", "slug": "chipco", "ats_vendor": "workday",
             "board_token": "chipco.wd5.myworkdayjobs.com/chipco/CHIPCOExternalCareerSite"}
    assert fa.workday_endpoint(entry) == ("chipco.wd5.myworkdayjobs.com", "chipco", "CHIPCOExternalCareerSite")
    res = fa.fetch_workday(entry, FakeHttp(ats_routes()), NOW, title_filter=fa.default_title_filter(), page_size=3)
    assert len(res.records) == 4 and not res.errors


def test_workday_on_myworkdaysite_without_a_tenant_subdomain():
    entry = {"board_token": "wd1.myworkdaysite.com/wf/WellsFargoJobs"}       # companies.yaml, verified live
    assert fa.workday_endpoint(entry) == ("wd1.myworkdaysite.com", "wf", "WellsFargoJobs")


def test_workday_closure_matches_postings_by_external_path():
    open_url = "https://wd1.myworkdaysite.com/recruiting/wf/WellsFargoJobs/job/CHARLOTTE-NC/Analyst_R-575436"
    gone_url = "https://wd1.myworkdaysite.com/recruiting/wf/WellsFargoJobs/job/CHARLOTTE-NC/Engineer_R-500001"
    existing = {
        "wf-1": {"id": "wf-1", "company_slug": "wells-fargo", "source": "workday", "status": "discovered",
                 "source_url": open_url},
        "wf-2": {"id": "wf-2", "company_slug": "wells-fargo", "source": "workday", "status": "discovered",
                 "source_url": gone_url},
    }
    res = fa.BoardResult("wells-fargo", "workday", complete=True,
                         all_urls={"/job/CHARLOTTE-NC/Analyst_R-575436"})
    assert fa.close_missing_records(existing, [res], NOW) == ["wf-2"]


def test_board_tokens_are_url_encoded():
    routes = {("GET", "https://api.ashbyhq.com/posting-api/job-board/Healthbot%20AI"):
              load_fixture("ashby_modelco.json")}
    res = fa.fetch_ashby({"name": "Healthbot AI", "slug": "healthbot-ai", "board_token": "Healthbot AI"},
                         FakeHttp(routes), NOW, title_filter=None)
    assert len(res.records) == 6


# ----------------------------------------------------------------------------- helpers
@pytest.mark.parametrize("text,expected", [
    ("Annual Salary: $222,800 — $290,000 USD", (222800, 290000, "USD")),
    ("£225,000 — £255,000 GBP", (225000, 255000, "GBP")),
    ("Statecraft | REMOTE (US) | $160-220k + equity", (160000, 220000, "USD")),
    ("Onsite (San Francisco) | $200K - $400K + equity", (200000, 400000, "USD")),
    ("The base salary range is 148,000 USD - 235,750 USD for Level 3", (148000, 235750, "USD")),
    ("€6,000 - €7,000 per month | Full-time", (None, None, None)),
    ("$45 - $60 per hour", (None, None, None)),
    ("Contract rate $26,500 - $31,500 per month", (None, None, None)),
    ("We raised $55m from investors", (None, None, None)),
    ("no numbers here", (None, None, None)),
])
def test_parse_comp_text(text, expected):
    assert fa.parse_comp_text(text) == expected


@pytest.mark.parametrize("title,level", [
    ("Staff+ Software Engineer, Model Serving Path", "staff"),
    ("Senior / Staff Machine Learning Engineer - Motion Forecasting for Delivery Robots", "staff"),
    ("Senior Machine Learning Engineer, Perception", "senior"),
    ("Sr. ML Engineer", "senior"),
    ("Contract Student Worker - Machine Learning Engineer", "intern"),
    ("Machine Learning Intern", "intern"),
    ("Applied Machine Learning Engineer, Chip Verification - New College Grad 2026", "new-grad"),
    ("Senior Manager, Machine Learning Ops Engineering - Fleet Systems", "manager"),
    ("Tech Lead Manager, Agent Runtime Platform", "manager"),
    ("Lead AI Engineer – Enterprise Data & AI Platforms", "lead"),
    ("Director, Perception", "director"),
    ("Principal Engineer, Inference", "principal"),
    ("Software Engineer II, ML Platform", "mid"),
    ("Junior ML Engineer", "junior"),
    ("Research Engineer, Simulations", "unspecified"),
])
def test_estimate_seniority(title, level):
    assert fa.estimate_seniority(title) == level


# ----------------------------------------------------------------------------- targets
def test_load_companies_tolerates_absence(root):
    assert fa.load_companies(root) == []


def test_load_companies_reads_watchlist(root_with_targets):
    companies = fa.load_companies(root_with_targets)
    assert [c["slug"] for c in companies] == ["labco", "robotco", "modelco", "chipco", "droneco-ai", "legacy-corp"]


def test_fetch_company_skips_unsupported_vendor():
    res = fa.fetch_company({"name": "Legacy Corp", "slug": "legacy-corp", "ats_vendor": "icims",
                            "board_token": "legacy"}, FakeHttp({}), NOW, title_filter=None)
    assert res.records == [] and res.complete is False
    assert any("unsupported" in n for n in res.notes)


def test_fetch_company_skips_unverified_entries_without_a_board_token():
    http = FakeHttp({})
    res = fa.fetch_company({"name": "Grammarly", "slug": "grammarly", "ats_vendor": "greenhouse",
                            "board_token": None}, http, NOW, title_filter=None)
    assert res.records == [] and res.errors == [] and http.calls == []
    assert any("no board_token" in n for n in res.notes)


def test_fetch_company_skips_an_unrecognised_workday_board():
    http = FakeHttp({})
    res = fa.fetch_company({"name": "X", "slug": "x", "ats_vendor": "workday",
                            "board_token": "https://careers.example.com/jobs"}, http, NOW, title_filter=None)
    assert res.errors == [] and http.calls == [] and any("not recognised" in n for n in res.notes)


# ----------------------------------------------------------------------------- upsert
def _gh_records(now=NOW, title_filter=None):
    return fa.fetch_greenhouse(LABCO, FakeHttp(ats_routes()), now, title_filter=title_filter).records


def test_upsert_writes_once_and_updates_last_seen_on_resight(root):
    first = fa.upsert_jobs(_gh_records(), root, NOW)
    files = sorted((root / "jobs").glob("*.md"))
    assert len(first["new"]) == 8 and len(files) == 8
    later = NOW + timedelta(days=1)
    second = fa.upsert_jobs(_gh_records(now=later), root, later)
    assert second["new"] == [] and len(second["updated"]) == 8
    assert sorted((root / "jobs").glob("*.md")) == files
    job = fa.load_jobs(root)["labco-480101"]
    assert job["first_seen"] == "2026-09-24T13:00:00Z"
    assert job["last_seen"] == "2026-09-25T13:00:00Z"


def test_upsert_preserves_downstream_fields_and_notes(root):
    fa.upsert_jobs(_gh_records(), root, NOW)
    path = root / "jobs" / "labco-480101.md"
    text = path.read_text(encoding="utf-8")
    text = text.replace("status: discovered", "status: fit_checked")
    text = text.replace("fit_verdict: null", "fit_verdict: apply")
    text += "\nScorer note: strong agentic overlap.\n"
    path.write_text(text, encoding="utf-8")
    fa.upsert_jobs(_gh_records(), root, NOW + timedelta(hours=5))
    job = fa.load_jobs(root)["labco-480101"]
    assert job["status"] == "fit_checked" and job["fit_verdict"] == "apply"
    assert "Scorer note: strong agentic overlap." in job["_body"]


def test_upsert_records_jd_change(root):
    fa.upsert_jobs(_gh_records(), root, NOW)
    before = fa.load_jobs(root)["labco-480101"]
    records = _gh_records()
    for r in records:
        if r["id"] == "labco-480101":
            r["_jd_text"] = r["_jd_text"] + "\nNew requirement: Rust."
            r["jd_hash"] = hashlib.sha1(b"changed").hexdigest()
    later = NOW + timedelta(days=2)
    out = fa.upsert_jobs(records, root, later)
    after = fa.load_jobs(root)["labco-480101"]
    assert "labco-480101" in out["jd_changed"]
    assert after["jd_hash"] != before["jd_hash"]
    assert after["jd_changed_at"] == "2026-09-26T13:00:00Z"
    assert "New requirement: Rust." in after["_body"]


def test_upsert_new_posting_reusing_an_existing_id_gets_a_suffix(root):
    fa.upsert_jobs(_gh_records(), root, NOW)
    rec = dict(_gh_records()[0])
    rec["source_url"] = "https://job-boards.greenhouse.io/labco/jobs/100999"
    rec["id"] = "labco-480101"
    out = fa.upsert_jobs([rec], root, NOW)
    assert out["new"] == [f"labco-480101-{h(rec['source_url'], 6)}"]
    assert fa.load_jobs(root)["labco-480101"]["source_url"].endswith("/100101")


def test_close_missing_marks_vanished_postings_closed(root):
    fa.upsert_jobs(_gh_records(), root, NOW)
    path = root / "jobs" / "labco-pip-2002.md"
    path.write_text(path.read_text(encoding="utf-8").replace("status: discovered", "status: applied"),
                    encoding="utf-8")
    res = fa.fetch_greenhouse(LABCO, FakeHttp(ats_routes()), NOW, title_filter=None)
    res.all_urls = {u for u in res.all_urls
                    if not u.endswith(("/100101", "/100106"))}
    closed = fa.close_missing(root, [res], NOW + timedelta(days=1))
    jobs = fa.load_jobs(root)
    assert closed == ["labco-480101"]
    assert jobs["labco-480101"]["status"] == "closed"
    assert jobs["labco-pip-2002"]["status"] == "applied"      # never touch live applications


# ----------------------------------------------------------------------------- CLI
def test_cli_dry_run_writes_nothing(root_with_targets, capsys):
    rc = fa.main(["--root", str(root_with_targets), "--dry-run"], http=FakeHttp(ats_routes()), now=NOW)
    assert rc == 0
    assert not (root_with_targets / "jobs").exists() or not any((root_with_targets / "jobs").iterdir())
    out = capsys.readouterr().out
    assert "labco" in out and "dry-run" in out
    assert "legacy-corp" in out and "unsupported" in out


def test_cli_board_flag_needs_no_watchlist(root, capsys):
    rc = fa.main(["--root", str(root), "--board", "greenhouse:labco:Labco", "--dry-run"],
                 http=FakeHttp(ats_routes()), now=NOW)
    assert rc == 0
    assert "labco: 8 on board, 6 match" in capsys.readouterr().out


def test_a_targeted_dry_run_for_one_named_company_writes_nothing(root_with_targets, capsys):
    """A targeted search: the owner names a company while discovery is paused."""
    rc = fa.main(["--root", str(root_with_targets), "--company", "labco", "--dry-run"],
                 http=FakeHttp(ats_routes()), now=NOW)
    assert rc == 0
    assert not (root_with_targets / "jobs").exists() or not any((root_with_targets / "jobs").iterdir())
    out = capsys.readouterr().out
    assert "labco" in out and "legacy-corp" not in out



def test_tier_flag_sweeps_only_that_tier(root_with_targets, capsys):
    """The fresh dream sweep is one short command (`--tier dream`)."""
    rc = fa.main(["--root", str(root_with_targets), "--tier", "dream", "--dry-run"],
                 http=FakeHttp(ats_routes()), now=NOW)
    assert rc == 0
    out = capsys.readouterr().out
    assert "labco" in out and "modelco" in out
    assert "robotco" not in out and "legacy-corp" not in out and "droneco-ai" not in out

# ----------------------------------------------------------------------------- jibe (a Jibe career site)
JIBE = "https://careers.instrumentco.com/api/jobs"
JD_HTML = ("<STRONG>Overview</STRONG><br><p>We have an opportunity for AI R&amp;D Engineer for SDLC Acceleration: build "
           "LLM-assisted features and RAG/agent workflows.</p><STRONG>Qualifications</STRONG><p>Python.</p><p>California "
           "pay range: MIN $129,450 - MAX $215,740 Colorado pay range: MIN $119,850 - MAX $199,750</p>")


def _jibe_job(req, title, loc="Santa Rosa, California", posted="2026-09-02T17:00:00+0000"):
    return {"data": {"slug": req, "req_id": req, "title": title, "description": JD_HTML, "full_location": loc,
                     "city": loc.split(",")[0], "country": "United States", "tags1": ["Regular"],
                     "apply_url": f"https://careers-instrumentco.icims.com/jobs/{req}/login", "ats_code": "icims",
                     "posted_date": posted}}


INSTRUMENTCO = {"name": "Instrumentco", "slug": "instrumentco", "ats_vendor": "jibe", "board_token": "careers.instrumentco.com"}


def _jibe_routes(pages):
    total = sum(len(p) for p in pages)
    return {("GET", JIBE): lambda params: {"jobs": pages[int(params["page"]) - 1] if int(params["page"]) <= len(pages)
                                                    else [], "totalCount": total, "count": total}}


def test_jibe_pages_the_whole_board_with_the_crawl_delay_and_keeps_title_matches(monkeypatch):
    slept = []
    monkeypatch.setattr(fa, "_jibe_sleep", slept.append)
    monkeypatch.setattr(fa, "JIBE_PAGE", 2)
    pages = [[_jibe_job("54056", "AI R&D Engineer (SDLC Acceleration)"), _jibe_job("53763", "Senior Application Engineer")],
             [_jibe_job("54723", "Senior Program Manager, NPI Solutions")]]
    http = FakeHttp(_jibe_routes(pages))
    res = fa.fetch_company(INSTRUMENTCO, http, NOW, fa.default_title_filter())
    assert [c[2]["page"] for c in http.calls] == [1, 2]
    assert http.calls[0][2]["country"] == "United States" and http.calls[0][2]["limit"] == 2
    assert slept == [fa.JIBE_DELAY_S]                                    # robots.txt crawl-delay 5, between pages
    assert res.complete and len(res.all_urls) == 3 and not res.errors
    [rec] = res.records
    assert rec["id"] == "instrumentco-54056" and rec["req_id"] == "54056" and rec["source"] == "jibe"
    assert rec["source_url"] == "https://careers.instrumentco.com/jobs/54056"      # the URL the manual record already has
    assert rec["apply_url"] == "https://careers-instrumentco.icims.com/jobs/54056/login" and rec["ats_vendor"] == "icims"
    assert (rec["comp_min"], rec["comp_max"], rec["comp_currency"]) == (129450, 215740, "USD")
    assert rec["location"] == "Santa Rosa, California" and rec["posted_at"].startswith("2026-09-02")
    assert "RAG/agent workflows" in rec["_jd_text"] and "<p>" not in rec["_jd_text"]


def test_jibe_re_sights_the_hand_recorded_instrumentco_job_instead_of_duplicating_it(root, monkeypatch):
    monkeypatch.setattr(fa, "_jibe_sleep", lambda s: None)
    manual = fa.make_record({"name": "Instrumentco", "slug": "instrumentco"}, title="AI R&D Engineer (SDLC Acceleration)",
                            source="manual", source_url="https://careers.instrumentco.com/jobs/54056",
                            apply_url="https://careers.instrumentco.com/jobs/54056", ats_vendor="unknown", posted_at=None,
                            location=None, remote="unknown", comp=(None, None, None), employment_type=None,
                            jd_text="", now=NOW - timedelta(days=1))
    manual["id"] = "instrumentco-man-3be5aea3"
    fa.save_job(root, manual)
    http = FakeHttp(_jibe_routes([[_jibe_job("54056", "AI R&D Engineer (SDLC Acceleration)")]]))
    res = fa.fetch_company(INSTRUMENTCO, http, NOW, fa.default_title_filter())
    fa.upsert_jobs(res.records, root, NOW)
    jobs = fa.load_jobs(root)
    assert list(jobs) == ["instrumentco-man-3be5aea3"] and jobs["instrumentco-man-3be5aea3"]["source"] == "jibe"


def test_jibe_is_a_supported_vendor_and_a_missing_host_is_skipped():
    assert "jibe" in fa.SUPPORTED_VENDORS and "jibe" in fa.FETCHERS
    res = fa.fetch_company(dict(INSTRUMENTCO, board_token=None), FakeHttp({}), NOW, None)
    assert res.records == [] and "no board_token" in res.notes[0]


# ----------------------------------------------------------------------------- sitemap (Avature/SuccessFactors-style career sites)
SYN = "https://careers.chipdesignco.com"
SYN_MAP = f"{SYN}/sitemap.xml"
CHIPDESIGNCO = {"name": "Chipdesignco", "slug": "chipdesignco", "ats_vendor": "sitemap", "board_token": "careers.chipdesignco.com/sitemap.xml"}
SYN_AI = f"{SYN}/job/sunnyvale/ai-engineer-simulation/44408/101000000001"
SYN_RD = f"{SYN}/job/santa-rosa/ai-r-and-d-engineer/44408/101000000002"
SYN_SALES = f"{SYN}/job/austin/sales-account-manager/44408/101000000003"
SYN_ROBOTS = "User-agent: *\nDisallow:/search-jobs/\n"


def _urlset(locs, ns="http://www.sitemaps.org/schemas/sitemap/0.9", bom=True):
    body = "".join(f"<url><loc>{u}</loc><lastmod>2026-09-29T03:59:58.7Z</lastmod></url>" for u in locs)
    return ("﻿" if bom else "") + f'<?xml version="1.0" encoding="utf-8"?><urlset xmlns="{ns}">{body}</urlset>'


def _ld_page(title, ident="17236", posted="2026-9-10", desc="<h3>About</h3><p>Build LLM agents for simulation.</p>",
             salary=None):
    posting = {"@context": "http://schema.org", "@type": "JobPosting", "title": title, "identifier": ident,
               "datePosted": posted, "description": desc,
               "jobLocation": [{"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": "Sunnyvale",
                                                              "addressRegion": "California",
                                                              "addressCountry": "United States"}}]}
    if salary:
        posting["baseSalary"] = {"@type": "MonetaryAmount", "currency": "USD",
                                 "value": {"@type": "QuantitativeValue", "minValue": salary[0], "maxValue": salary[1],
                                           "unitText": "YEAR"}}
    import json as _json
    return (f"<html><head><title>{title} at Chipdesignco</title><script type=\"application/ld+json\">"
            f"{_json.dumps(posting)}</script></head><body><h1>{title}</h1></body></html>")


def _syn_routes(locs, pages, robots=SYN_ROBOTS):
    routes = {("GET", f"{SYN}/robots.txt"): robots, ("GET", SYN_MAP): _urlset(locs)}
    routes.update({("GET", u): p for u, p in pages.items()})
    return routes


def _gets(http):
    return [c[1] for c in http.calls]


def test_sitemap_reads_robots_then_the_sitemap_and_fetches_only_title_matched_job_pages_politely(monkeypatch):
    slept = []
    monkeypatch.setattr(fa, "_sitemap_sleep", slept.append)
    locs = [SYN, f"{SYN}/category/engineering-jobs/44408/8675488/1", SYN_AI, SYN_SALES,
            "https://elsewhere.example.com/job/x/ai-engineer/1/2"]
    http = FakeHttp(_syn_routes(locs, {SYN_AI: _ld_page("AI Engineer, Simulation", salary=(150000, 210000))}))
    res = fa.fetch_company(CHIPDESIGNCO, http, NOW, fa.default_title_filter())
    assert _gets(http) == [f"{SYN}/robots.txt", SYN_MAP, SYN_AI]          # robots first; the sales page is never fetched
    assert slept == [fa.SITEMAP_DELAY_S] * 2                                 # a pause before every request after the first
    assert res.all_urls == {SYN_AI, SYN_SALES} and res.complete and not res.errors   # same host, /job/ pages only
    [rec] = res.records
    assert rec["id"] == "chipdesignco-17236" and rec["req_id"] == "17236" and rec["source"] == "sitemap"
    assert rec["source_url"] == SYN_AI and rec["apply_url"] == SYN_AI and rec["title"] == "AI Engineer, Simulation"
    assert rec["posted_at"] == "2026-09-10"                                  # '2026-9-10' as the page states it
    assert rec["location"] == "Sunnyvale, California, United States"
    assert (rec["comp_min"], rec["comp_max"], rec["comp_currency"]) == (150000, 210000, "USD")
    assert "Build LLM agents for simulation." in rec["_jd_text"] and "<p>" not in rec["_jd_text"]


def test_sitemap_slug_screen_reads_and_as_ampersand_so_r_and_d_titles_are_not_lost(monkeypatch):
    monkeypatch.setattr(fa, "_sitemap_sleep", lambda s: None)
    tf = fa.TitleFilter([fa.title_pattern("ai r&d engineer")], [])
    http = FakeHttp(_syn_routes([SYN_RD], {SYN_RD: _ld_page("AI R&D Engineer", ident="54056")}))
    res = fa.fetch_company(CHIPDESIGNCO, http, NOW, tf)
    assert SYN_RD in _gets(http) and [r["title"] for r in res.records] == ["AI R&D Engineer"]


def test_sitemap_page_whose_real_title_fails_the_filter_is_not_recorded(monkeypatch):
    monkeypatch.setattr(fa, "_sitemap_sleep", lambda s: None)
    tf = fa.TitleFilter([fa.title_pattern("ai engineer")], [])
    http = FakeHttp(_syn_routes([SYN_AI], {SYN_AI: _ld_page("Account Director, Simulation Sales")}))
    res = fa.fetch_company(CHIPDESIGNCO, http, NOW, tf)                     # the URL words passed; the real title does not
    assert SYN_AI in _gets(http) and res.records == []


MUR = "https://jobs.partsco.com"
MUR_ML = f"{MUR}/Partsco/job/San-Diego-Senior-Machine-Learning-Engineer-CA-92101/1423425300/"
MUR_PAGE = """<html><body><div class="jobDisplayShell" itemscope="itemscope" itemtype="http://schema.org/JobPosting">
<span itemprop="jobLocation" itemscope itemtype="http://schema.org/Place"><span itemprop="address" itemscope
itemtype="http://schema.org/PostalAddress"><socialco itemprop="addressLocality" content="San Diego"><socialco
itemprop="addressRegion" content="CA"><socialco itemprop="postalCode" content="92101"><socialco itemprop="addressCountry"
content="US"></span></span><socialco itemprop="datePosted" content="Thu Sep 24 07:00:00 UTC 2026"><socialco
itemprop="hiringOrganization" content="Partsco America">
<h1><span itemprop="title" class="t">Senior Machine Learning Engineer     </span></h1>
<span itemprop="description" class="d"><span class="jobdescription"><p><span style="x">Sensor fusion with
<b>PyTorch</b>.</span></p><p>Salary range: $150,000 - $190,000 per year.</p></span><p>We sponsor visas.</p></span>
<p>Footer, outside the description.</p></div></body></html>"""


def test_sitemap_reads_microdata_pages_and_the_google_sitemap_namespace(monkeypatch):
    monkeypatch.setattr(fa, "_sitemap_sleep", lambda s: None)
    partsco = {"name": "Partsco (pSemi)", "slug": "partsco", "ats_vendor": "sitemap", "board_token": "jobs.partsco.com"}
    other = f"{MUR}/pSemi/job/San-Diego-Quality-Technician-CA-92101/1398784600/"
    http = FakeHttp({("GET", f"{MUR}/robots.txt"): "User-agent: *\nDisallow: /applybutton/\n",
                     ("GET", f"{MUR}/sitemap.xml"): _urlset([MUR_ML, other], ns="http://www.google.com/schemas/sitemap/0.9",
                                                            bom=False),
                     ("GET", MUR_ML): MUR_PAGE})
    res = fa.fetch_company(partsco, http, NOW, fa.default_title_filter())
    assert res.complete and res.all_urls == {MUR_ML, other}
    [rec] = res.records
    assert rec["title"] == "Senior Machine Learning Engineer" and rec["id"] == "partsco-1423425300"
    assert rec["posted_at"] == "2026-09-24" and rec["location"] == "San Diego, CA, US"
    assert "Sensor fusion with" in rec["_jd_text"] and "PyTorch" in rec["_jd_text"]    # the nested spans survive
    assert "We sponsor visas." in rec["_jd_text"]                            # past the inner </span>
    assert "Footer" not in rec["_jd_text"]                                   # the description ends at its own </span>
    assert (rec["comp_min"], rec["comp_max"]) == (150000, 190000)            # from the JD text when no baseSalary


def test_sitemap_honours_robots_disallow_crawl_delay_and_missing_robots(monkeypatch):
    slept = []
    monkeypatch.setattr(fa, "_sitemap_sleep", slept.append)
    robots = "User-agent: *\nCrawl-delay: 12\nDisallow: /job/sunnyvale/\n"
    http = FakeHttp(_syn_routes([SYN_AI, SYN_RD], {SYN_RD: _ld_page("AI Engineer")}, robots=robots))
    res = fa.fetch_company(CHIPDESIGNCO, http, NOW, fa.default_title_filter())
    assert SYN_AI not in _gets(http) and SYN_RD in _gets(http)
    assert slept and set(slept) == {12.0}
    assert any("robots.txt disallows" in n for n in res.notes)
    # a robots.txt that answers 404 has no rules (RFC 9309)
    http = FakeHttp(_syn_routes([SYN_AI], {SYN_AI: _ld_page("AI Engineer")}, robots=(404, "")))
    assert len(fa.fetch_company(CHIPDESIGNCO, http, NOW, fa.default_title_filter()).records) == 1


def test_sitemap_refuses_the_board_when_robots_fails_or_disallows_the_sitemap(monkeypatch):
    monkeypatch.setattr(fa, "_sitemap_sleep", lambda s: None)
    http = FakeHttp({("GET", f"{SYN}/robots.txt"): fa.FetchError("GET robots.txt: HTTP 503")})
    res = fa.fetch_company(CHIPDESIGNCO, http, NOW, None)
    assert res.errors and res.records == [] and not res.complete and _gets(http) == [f"{SYN}/robots.txt"]
    http = FakeHttp(_syn_routes([SYN_AI], {}, robots="User-agent: *\nDisallow: /sitemap.xml\n"))
    res = fa.fetch_company(CHIPDESIGNCO, http, NOW, None)
    assert res.errors and "robots.txt disallows" in res.errors[0] and _gets(http) == [f"{SYN}/robots.txt"]


def test_sitemap_with_no_job_pages_is_not_complete_so_nothing_is_closed(monkeypatch):
    monkeypatch.setattr(fa, "_sitemap_sleep", lambda s: None)
    http = FakeHttp(_syn_routes([SYN, f"{SYN}/category/x/1/2/1"], {}))
    res = fa.fetch_company(CHIPDESIGNCO, http, NOW, None)
    assert not res.complete and res.all_urls == set() and "no job pages" in res.notes[0]


def test_sitemap_caps_new_page_fetches_per_run_and_says_how_many_wait(monkeypatch):
    monkeypatch.setattr(fa, "_sitemap_sleep", lambda s: None)
    monkeypatch.setattr(fa, "SITEMAP_MAX_FETCH", 1)
    http = FakeHttp(_syn_routes([SYN_AI, SYN_RD], {SYN_AI: _ld_page("AI Engineer")}))
    res = fa.fetch_company(CHIPDESIGNCO, http, NOW, None)
    assert SYN_RD not in _gets(http) and len(res.records) == 1
    assert any("1 new matching page(s) left for the next run" in n for n in res.notes)


def test_sitemap_page_without_jobposting_data_or_failing_is_noted_not_recorded(monkeypatch):
    monkeypatch.setattr(fa, "_sitemap_sleep", lambda s: None)
    http = FakeHttp(_syn_routes([SYN_AI, SYN_RD], {SYN_AI: "<html><title>AI Engineer</title></html>",
                                                   SYN_RD: (404, "")}))
    res = fa.fetch_company(CHIPDESIGNCO, http, NOW, None)
    assert res.records == [] and not res.errors
    assert any("no JobPosting data" in n for n in res.notes) and any("HTTP 404" in n for n in res.notes)


def test_sitemap_index_reads_its_same_host_child_sitemaps(monkeypatch):
    monkeypatch.setattr(fa, "_sitemap_sleep", lambda s: None)
    index = ('<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
             f"<sitemap><loc>{SYN}/sitemap-jobs.xml</loc></sitemap>"
             "<sitemap><loc>https://cdn.example.net/sitemap-other.xml</loc></sitemap></sitemapindex>")
    http = FakeHttp({("GET", f"{SYN}/robots.txt"): SYN_ROBOTS, ("GET", SYN_MAP): index,
                     ("GET", f"{SYN}/sitemap-jobs.xml"): _urlset([SYN_AI]), ("GET", SYN_AI): _ld_page("AI Engineer")})
    res = fa.fetch_company(CHIPDESIGNCO, http, NOW, None)
    assert "https://cdn.example.net/sitemap-other.xml" not in _gets(http)
    assert res.all_urls == {SYN_AI} and len(res.records) == 1
    assert not res.complete                                                  # a child was skipped: nothing is closed


def test_the_run_deadline_stops_between_the_child_sitemaps_of_an_index(monkeypatch):
    # The deadline must also be checked before each of the up to 20 child sitemap reads, not only before job pages; a clock past the deadline after the first child reads 1 child, and nothing is closed
    monkeypatch.setattr(fa, "_sitemap_sleep", lambda s: None)
    kids = [f"{SYN}/sitemap-jobs-{i}.xml" for i in range(3)]
    index = ('<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
             + "".join(f"<sitemap><loc>{k}</loc></sitemap>" for k in kids) + "</sitemapindex>")
    t = {"now": 0.0}

    def child(_arg):
        t["now"] = 100.0                                    # past the deadline once the first child is read
        return _urlset([SYN_AI])
    http = FakeHttp({("GET", f"{SYN}/robots.txt"): SYN_ROBOTS, ("GET", SYN_MAP): index, ("GET", kids[0]): child,
                     ("GET", kids[1]): child, ("GET", kids[2]): child})
    fa.set_deadline(lambda: t["now"], 50.0)
    try:
        res = fa.fetch_company(CHIPDESIGNCO, http, NOW, None)
    finally:
        fa.set_deadline(None, None)
    assert [u for u in _gets(http) if "sitemap-jobs-" in u] == [kids[0]]
    assert not res.complete and any("deadline" in n for n in res.notes)


def test_pages_left_by_the_time_limit_are_named_as_such_not_as_the_cap(monkeypatch):
    monkeypatch.setattr(fa, "_sitemap_sleep", lambda s: None)
    t = {"now": 0.0}

    def page(_arg):
        t["now"] = 100.0
        return _ld_page("AI Engineer")
    http = FakeHttp(_syn_routes([SYN_AI, SYN_RD], {SYN_AI: page, SYN_RD: page}))
    fa.set_deadline(lambda: t["now"], 50.0)
    try:
        res = fa.fetch_company(CHIPDESIGNCO, http, NOW, fa.default_title_filter())
    finally:
        fa.set_deadline(None, None)
    assert len(res.records) == 1 and any("1 new matching page(s) left" in n and "time limit after 1 page(s)" in n
                                         for n in res.notes)


def test_sitemap_re_sights_known_postings_without_fetching_their_pages(root, monkeypatch):
    monkeypatch.setattr(fa, "_sitemap_sleep", lambda s: None)
    manual = fa.make_record({"name": "Chipdesignco", "slug": "chipdesignco"}, title="AI Engineer, Simulation", source="manual",
                            source_url=SYN_AI, apply_url=SYN_AI, ats_vendor="unknown", posted_at=None, location=None,
                            remote="unknown", comp=(None, None, None), employment_type=None, jd_text="JD by hand",
                            now=NOW - timedelta(days=3))
    manual["id"] = "chipdesignco-man-1"
    fa.save_job(root, manual)
    http = FakeHttp(_syn_routes([SYN_AI], {}))
    rc = fa.main(["--root", str(root), "--board", "sitemap:careers.chipdesignco.com/sitemap.xml:Chipdesignco"], http=http,
                 now=NOW)
    assert rc == 0 and SYN_AI not in _gets(http)
    job = fa.load_jobs(root)["chipdesignco-man-1"]
    assert job["last_seen"] == "2026-09-24T13:00:00Z" and job["source"] == "manual"   # re-sighted, nothing overwritten
    assert "JD by hand" in job["_jd_text"] and list(fa.load_jobs(root)) == ["chipdesignco-man-1"]


def test_sitemap_posting_gone_from_the_sitemap_is_closed(root):
    gone = fa.make_record(CHIPDESIGNCO, title="AI Engineer", source="sitemap", source_url=SYN_RD, apply_url=SYN_RD,
                          ats_vendor="unknown", posted_at=None, location=None, remote="unknown",
                          comp=(None, None, None), employment_type=None, jd_text="x", now=NOW)
    fa.save_job(root, gone)
    res = fa.BoardResult("chipdesignco", "sitemap", all_urls={SYN_AI}, complete=True)
    assert fa.close_missing(root, [res], NOW) == [gone["id"]]


def test_sitemap_is_a_supported_vendor_and_tokens_resolve():
    assert "sitemap" in fa.SUPPORTED_VENDORS and "sitemap" in fa.FETCHERS
    assert fa.sitemap_url("careers.chipdesignco.com/sitemap.xml") == SYN_MAP
    assert fa.sitemap_url("jobs.partsco.com") == f"{MUR}/sitemap.xml"
    assert fa.sitemap_url("https://jobs.partsco.com/sitemap_index.xml") == f"{MUR}/sitemap_index.xml"
    assert fa.board_from_spec("sitemap:careers.chipdesignco.com/sitemap.xml:Chipdesignco")["board_token"] == \
        "careers.chipdesignco.com/sitemap.xml"


class _Resp:
    def __init__(self, status, body=b"", ctype="text/html"):
        self.status_code, self.content, self.headers = status, body, {"Content-Type": ctype}


class _Session:
    def __init__(self, resp):
        self.resp, self.headers, self.seen = resp, {}, []

    def request(self, method, url, timeout=None, **kw):
        self.seen.append(kw)
        return self.resp


def test_http_get_text_returns_a_4xx_and_decodes_utf8_when_no_charset_is_named():
    http = fa.Http(session=_Session(_Resp(404)), retries=0, min_interval=0)
    assert http.get_text("https://x.example.com/robots.txt") == (404, "")
    s = _Session(_Resp(200, "Engineer – AI".encode("utf-8")))
    http = fa.Http(session=s, retries=0, min_interval=0)
    assert http.get_text("https://x.example.com/job/1", headers={"Accept": "text/html"}) == (200, "Engineer – AI")
    assert s.seen[0]["headers"] == {"Accept": "text/html"}
    with pytest.raises(fa.FetchError):
        fa.Http(session=_Session(_Resp(503)), retries=0, min_interval=0).get_text("https://x.example.com/robots.txt")


def test_a_re_sighting_whose_job_record_is_gone_creates_nothing():
    existing = {}
    out = fa.merge_records(existing, [{"id": None, "source_url": SYN_AI, "_resight": True}], NOW)
    assert existing == {} and out == {"new": [], "updated": [], "jd_changed": []}


# ----------------------------------------------------------------------------- dream boards
def test_sitemap_urls_keep_their_query_string():
    assert fa.sitemap_url("apply.careers.softco.com/careers/sitemap_index.xml?domain=softco.com") == \
        "https://apply.careers.softco.com/careers/sitemap_index.xml?domain=softco.com"


SOCIALCO = {"name": "Socialco", "slug": "socialco", "ats_vendor": "sitemap", "board_token": "www.socialcareers.com/jobsearch/sitemap.xml",
        "job_url_pattern": "/profile/job_details/", "url_titles": False}
MH = "https://www.socialcareers.com"


def _meta_routes(pages):
    locs = list(pages)
    routes = {("GET", f"{MH}/robots.txt"): "User-agent: *\nDisallow: /login\n",
              ("GET", f"{MH}/jobsearch/sitemap.xml"): _urlset(locs)}
    routes.update({("GET", u): p for u, p in pages.items()})
    return routes


def test_a_sitemap_without_titles_in_its_urls_fetches_pages_and_keeps_title_matches(monkeypatch):
    monkeypatch.setattr(fa, "_sitemap_sleep", lambda s: None)
    ml, sales = f"{MH}/profile/job_details/100200300400500/", f"{MH}/profile/job_details/111111111111111/"
    ident = {"@type": "PropertyValue", "name": "Socialco", "value": "100200300400500"}
    pages = {ml: _ld_page("Software Engineer, Machine Learning", ident=ident),
             sales: _ld_page("Sales Account Director", ident="x")}
    http = FakeHttp(_meta_routes(pages))
    res = fa.fetch_company(SOCIALCO, http, NOW, fa.default_title_filter())
    assert ml in _gets(http) and sales in _gets(http)                  # no URL words to screen: both pages read
    [rec] = res.records
    assert rec["title"] == "Software Engineer, Machine Learning" and rec["req_id"] == "100200300400500"


def test_the_fetched_page_memory_stops_refetching_pages_that_did_not_match(tmp_path, monkeypatch):
    monkeypatch.setattr(fa, "_sitemap_sleep", lambda s: None)
    sales = f"{MH}/profile/job_details/111111111111111/"
    cache = tmp_path / "state" / "sitemap" / "socialco.json"
    http = FakeHttp(_meta_routes({sales: _ld_page("Sales Account Director")}))
    fa.fetch_company(SOCIALCO, http, NOW, fa.default_title_filter(), cache_path=cache)
    assert sales in _gets(http) and json.loads(cache.read_text())[sales]["title"] == "Sales Account Director"
    again = FakeHttp(_meta_routes({sales: _ld_page("Sales Account Director")}))
    fa.fetch_company(SOCIALCO, again, NOW, fa.default_title_filter(), cache_path=cache)
    assert sales not in _gets(again)


def test_a_job_id_comes_from_the_leading_number_of_a_url_segment():
    assert fa.url_job_id("https://explore.jobs.streamco.net/careers/job/100200300401-analytics-engineer-5?domain=streamco.com") \
        == "100200300401"
    assert fa.url_job_id("https://jobs.fruitco.com/en-us/details/100125-0310/hardware") == "100125"
    assert fa.url_job_id("https://x.com/job/abc") is None


APPLE_PAGE = ('<html><script>window.__staticRouterHydrationData = JSON.parse("' +
              json.dumps(json.dumps({"loaderData": {"jobDetails": {"jobsData": {
                  "jobNumber": "100124-0301", "postingTitle": "Machine Learning Engineer - Wellness Models",
                  "postDateInGMT": "2026-09-20T19:23:14.407+00:00",
                  "description": "<p>Build ML for health.</p>", "minimumQualifications": "Python, PyTorch",
                  "preferredQualifications": "LLM agents", "homeOffice": False,
                  "locations": [{"name": "Cupertino", "stateProvince": "California", "countryName": "United States"}]}}}}))[1:-1]
              + '");</script></html>')


def test_apple_job_pages_are_read_from_their_embedded_job_data():
    post = fa.job_posting(APPLE_PAGE)
    assert post["title"] == "Machine Learning Engineer - Wellness Models" and post["posted"] == "2026-09-20T19:23:14Z"
    assert post["identifier"] == "100124-0301" and post["locations"] == ["Cupertino, California, United States"]
    assert "Build ML for health." in post["description"] and "LLM agents" in post["description"]


AMZ = "https://www.amazon.jobs/en/search.json"
AMAZON = {"name": "Shopco (incl. cloud)", "slug": "amazon", "ats_vendor": "amazon", "board_token": "www.amazon.jobs",
          "search_queries": [{"q": "machine learning engineer", "loc": "San Francisco, California"}]}


def _amz_job(i, title, posted="September 17, 2026"):
    return {"id_icims": str(i), "title": title, "posted_date": posted, "job_path": f"/en/jobs/{i}/slug-{i}",
            "normalized_location": "Sunnyvale, California, USA", "description": "<p>Agents at scale.</p>",
            "basic_qualifications": "3+ years", "preferred_qualifications": "LLMs", "job_schedule_type": "full-time",
            "country_code": "USA"}


def test_amazon_search_json_pages_with_a_pause_and_keeps_title_matches(monkeypatch):
    slept = []
    monkeypatch.setattr(fa, "_amazon_sleep", slept.append)
    monkeypatch.setattr(fa, "AMAZON_PAGE", 2)
    abroad = dict(_amz_job(4, "Machine Learning Engineer, Music"), country_code="IND")
    pages = [[_amz_job(1, "Machine Learning Engineer, AGI"), _amz_job(2, "Sales Manager")],
             [_amz_job(3, "Applied Scientist"), abroad]]
    http = FakeHttp({("GET", AMZ): lambda p: {"hits": 4, "jobs": pages[int(p["offset"]) // 2]
                                                if int(p["offset"]) // 2 < len(pages) else []}})
    res = fa.fetch_company(AMAZON, http, NOW, fa.default_title_filter())
    assert [c[2]["offset"] for c in http.calls] == [0, 2] and http.calls[0][2]["base_query"] == "machine learning engineer"
    assert http.calls[0][2]["loc_query"] == "San Francisco, California" and slept == [fa.AMAZON_DELAY_S]
    recs = {r["req_id"]: r for r in res.records}
    assert set(recs) == {"1", "3"} and not res.complete                 # a search is never the whole board; US only
    r = recs["1"]
    assert r["source"] == "amazon" and r["source_url"] == "https://www.amazon.jobs/en/jobs/1/slug-1"
    assert r["posted_at"] == "2026-09-17" and r["location"] == "Sunnyvale, California, USA"
    assert "Agents at scale." in r["_jd_text"] and "LLMs" in r["_jd_text"]
    assert "amazon" in fa.SUPPORTED_VENDORS and "amazon" in fa.FETCHERS


def test_vendor_kwargs_carry_known_urls_and_the_page_memory_for_sitemaps(tmp_path):
    kw = fa._vendor_kw("sitemap", 20, {"u"}, cache_dir=tmp_path, slug="socialco")
    assert kw == {"known_urls": {"u"}, "cache_path": tmp_path / "socialco.json"}
    assert fa._vendor_kw("sitemap", 20, set(), cache_dir=None, slug="socialco") == {"known_urls": set(), "cache_path": None}
    assert fa._vendor_kw("greenhouse", 20, {"u"}, cache_dir=tmp_path, slug="x") == {}


def test_robots_rules_follow_rfc_9309_longest_match_not_file_order():
    msft = ("User-agent: *\nDisallow: /\nAllow: /$\nAllow: /careers\nAllow: /api/apply\n"
            "User-agent: AhrefsBot\nDisallow: /careers\n")
    r = fa.Robots(msft)
    assert r.allowed("https://apply.careers.softco.com/careers/job/100200300402-x?domain=softco.com")
    assert r.allowed("https://apply.careers.softco.com/")
    assert not r.allowed("https://apply.careers.softco.com/candidate/profile")
    r2 = fa.Robots("User-agent: *\nDisallow: /jobs/*/apply$\nAllow: /jobs/\nCrawl-delay: 7\n")
    assert r2.allowed("https://x.com/jobs/1/apply?x=1") and not r2.allowed("https://x.com/jobs/1/apply")
    assert r2.delay == 7.0
    assert fa.Robots("").allowed("https://x.com/anything") and fa.Robots(None).allowed("https://x.com/a")
    ours = fa.Robots("User-agent: jaja\nDisallow: /private\nUser-agent: *\nDisallow: /\n")
    assert ours.allowed("https://x.com/careers") and not ours.allowed("https://x.com/private/1")


def test_the_http_session_never_asks_for_zstd():
    # amazon.jobs answered zstd, which urllib3 advertised but could not decode
    session = _Session(_Resp(200))
    fa.Http(session=session, retries=0, min_interval=0)
    assert session.headers["Accept-Encoding"] == "gzip, deflate"


# ----------------------------------------------------------------------------- workable (R2 G4)
HUBCO = {"name": "Hubco", "slug": "hubco", "ats_vendor": "workable", "board_token": "hubco"}
WORKABLE_URL = "https://apply.workable.com/api/v1/widget/accounts/hubco"


def _workable(fixture="workable_hubco.json"):
    return FakeHttp({("GET", WORKABLE_URL): load_fixture(fixture)})


def test_workable_normalizes_to_job_schema():
    res = fa.fetch_workable(HUBCO, _workable(), NOW, title_filter=None)
    assert res.complete and len(res.records) == 2
    rec = by_url(res.records)["https://apply.workable.com/j/HUB0002B"]
    expected = {
        "id": "hubco-hub0002b",
        "company": "Hubco",
        "company_slug": "hubco",
        "title": "Low-Level Senior Software Engineer, Blob Storage - US Remote",
        "req_id": "HUB0002B",
        "source": "workable",
        "ats_vendor": "workable",
        "apply_url": "https://apply.workable.com/j/HUB0002B/apply",
        "posted_at": "2026-07-30",
        "location": "New York, New York, United States",
        "remote": "remote",
        "employment_type": "full-time",
        "comp_min": 180000,
        "comp_max": 240000,
        "comp_currency": "USD",
        "status": "discovered",
    }
    for key, value in expected.items():
        assert rec[key] == value, key
    assert "we sponsor visas case by case" in rec["_jd_text"] and "<p>" not in rec["_jd_text"]


def test_workable_calls_the_widget_endpoint_with_details_and_filters_titles():
    http = _workable()
    tf = fa.TitleFilter([fa.title_pattern("us remote")], [])
    res = fa.fetch_workable(HUBCO, http, NOW, title_filter=tf)
    assert [r["req_id"] for r in res.records] == ["HUB0002B"]
    assert len(res.all_urls) == 2                                 # the whole board, unfiltered
    assert http.calls[0][1] == WORKABLE_URL and http.calls[0][2] == {"details": "true"}


def test_workable_is_a_registered_vendor():
    assert fa.FETCHERS["workable"] is fa.fetch_workable
    res = fa.fetch_company(HUBCO, _workable(), NOW, title_filter=None)
    assert not res.errors and len(res.records) == 2


# ----------------------------------------------------------------------------- smartrecruiters (R-portals §3)
SRCO = {"name": "Srco", "slug": "srco", "ats_vendor": "smartrecruiters", "board_token": "srco"}
SR_LIST = "https://api.smartrecruiters.com/v1/companies/srco/postings"
SR_DETAIL = SR_LIST + "/100002"


def _sr(list_fixture=None):
    return FakeHttp({("GET", SR_LIST): list_fixture or load_fixture("smartrecruiters_postings.json"),
                     ("GET", SR_DETAIL): load_fixture("smartrecruiters_posting_detail.json")})


def test_smartrecruiters_lists_the_board_and_reads_detail_only_for_matched_titles():
    http = _sr()
    tf = fa.TitleFilter([fa.title_pattern("ai engineer")], [])
    res = fa.fetch_smartrecruiters(SRCO, http, NOW, title_filter=tf)
    assert res.complete and len(res.all_urls) == 3 and [r["req_id"] for r in res.records] == ["REF2101Q"]
    assert [c[1] for c in http.calls] == [SR_LIST, SR_DETAIL]               # one detail call, for the match only
    assert http.calls[0][2] == {"limit": 100, "offset": 0}
    rec = res.records[0]
    expected = {"id": "srco-ref2101q", "title": "AI Engineer, Agents", "source": "smartrecruiters",
                "ats_vendor": "smartrecruiters", "source_url": "https://jobs.smartrecruiters.com/srco/100002",
                "apply_url": "https://jobs.smartrecruiters.com/srco/100002-ai-engineer-agents?oga=true",
                "posted_at": "2026-09-28T16:00:00Z", "location": "San Francisco, CA, United States", "remote": "hybrid",
                "employment_type": "full-time", "comp_min": 180000, "comp_max": 220000}
    for k, v in expected.items():
        assert rec[k] == v, k
    assert "We sponsor visas." in rec["_jd_text"] and "<p>" not in rec["_jd_text"]


def test_smartrecruiters_pages_through_a_large_board():
    page = load_fixture("smartrecruiters_postings.json")
    def serve(params):
        off = params["offset"]
        return {**page, "totalFound": 150, "offset": off, "content": page["content"] if off == 0 else page["content"][:1]}
    http = FakeHttp({("GET", SR_LIST): serve})
    res = fa.fetch_smartrecruiters(SRCO, http, NOW, title_filter=fa.TitleFilter([fa.title_pattern("zzz")], []))
    assert [c[2]["offset"] for c in http.calls] == [0, 100]
    assert not res.complete                                   # 4 seen of 150 reported: the board is not whole


def test_smartrecruiters_is_a_registered_and_supported_vendor():
    assert fa.FETCHERS["smartrecruiters"] is fa.fetch_smartrecruiters
    assert "smartrecruiters" in fa.SUPPORTED_VENDORS and "workable" in fa.SUPPORTED_VENDORS
    res = fa.fetch_company(SRCO, _sr(), NOW, title_filter=fa.TitleFilter([fa.title_pattern("ai engineer")], []))
    assert not res.errors and len(res.records) == 1


# ----------------------------------------------------------------------------- rippling (R-portals §3)
RPCO = {"name": "Ripco", "slug": "ripco", "ats_vendor": "rippling", "board_token": "ripco"}
RP_LIST = "https://api.rippling.com/platform/api/ats/v1/board/ripco/jobs"
RP_DETAIL = RP_LIST + "/job-5002"


def _rp():
    return FakeHttp({("GET", RP_LIST): load_fixture("rippling_jobs.json")["jobs"],     # the API answers a bare list
                     ("GET", RP_DETAIL): load_fixture("rippling_job_detail.json")})


def test_rippling_lists_the_board_and_reads_detail_only_for_matched_titles():
    http = _rp()
    res = fa.fetch_rippling(RPCO, http, NOW, title_filter=fa.TitleFilter([fa.title_pattern("ai engineer")], []))
    assert res.complete and len(res.all_urls) == 3 and len(res.records) == 1
    assert [c[1] for c in http.calls] == [RP_LIST, RP_DETAIL]
    rec = res.records[0]
    expected = {"title": "Applied AI Engineer, Agents", "source": "rippling", "ats_vendor": "rippling",
                "source_url": "https://ats.rippling.com/ripco/jobs/job-5002",
                "posted_at": "2026-09-29T16:00:00Z", "location": "San Francisco, CA | Remote (United States)",
                "remote": "remote", "employment_type": "full-time", "comp_min": 170000, "comp_max": 210000}
    for k, v in expected.items():
        assert rec[k] == v, k
    assert "Build LLM agents" in rec["_jd_text"] and "<div>" not in rec["_jd_text"]


def test_rippling_is_a_registered_and_supported_vendor():
    assert fa.FETCHERS["rippling"] is fa.fetch_rippling and "rippling" in fa.SUPPORTED_VENDORS
    res = fa.fetch_company(RPCO, _rp(), NOW, title_filter=fa.TitleFilter([fa.title_pattern("ai engineer")], []))
    assert not res.errors and len(res.records) == 1


# ----------------------------------------------------------------------------- gem (R-portals §3)
GEMCO = {"name": "Gemco", "slug": "gemco", "ats_vendor": "gem", "board_token": "gemco"}
GEM_URL = "https://api.gem.com/job_board/v0/gemco/job_posts/"


def _gem():
    return FakeHttp({("GET", GEM_URL): load_fixture("gem_job_posts.json")["posts"]})


def test_gem_normalizes_the_board_in_one_call():
    http = _gem()
    res = fa.fetch_gem(GEMCO, http, NOW, title_filter=fa.TitleFilter([fa.title_pattern("ai engineer")], []))
    assert res.complete and len(res.all_urls) == 2 and len(http.calls) == 1
    rec = res.records[0]
    expected = {"id": "gemco-212", "title": "AI Engineer, Evals", "source": "gem", "ats_vendor": "gem",
                "source_url": "https://jobs.gem.com/gemco/100202", "posted_at": "2026-09-29T17:00:00Z",
                "location": "Remote, United States", "remote": "remote", "employment_type": "full-time",
                "comp_min": 160000, "comp_max": 200000}
    for k, v in expected.items():
        assert rec[k] == v, k
    hybrid = fa.fetch_gem(GEMCO, _gem(), NOW, title_filter=None)
    assert {r["remote"] for r in hybrid.records} == {"remote", "hybrid"}


def test_gem_is_a_registered_and_supported_vendor():
    assert fa.FETCHERS["gem"] is fa.fetch_gem and "gem" in fa.SUPPORTED_VENDORS
    assert not fa.fetch_company(GEMCO, _gem(), NOW, title_filter=None).errors


# ----------------------------------------------------------------------------- pinpoint (R-portals §3)
PPCO = {"name": "Talentco", "slug": "talentco", "ats_vendor": "pinpoint", "board_token": "talentco"}
PP_URL = "https://talentco.pinpointhq.com/postings.json"


def _pp():
    return FakeHttp({("GET", PP_URL): load_fixture("pinpoint_postings.json")})


def test_pinpoint_normalizes_the_board_with_structured_pay():
    res = fa.fetch_pinpoint(PPCO, _pp(), NOW, title_filter=fa.TitleFilter([fa.title_pattern("ai engineer")], []))
    assert res.complete and len(res.all_urls) == 2 and len(res.records) == 1
    rec = res.records[0]
    expected = {"id": "talentco-pin-0450", "title": "Senior AI Engineer, Agents", "source": "pinpoint",
                "ats_vendor": "pinpoint", "source_url": "https://talentco.pinpointhq.com/en/postings/ai-eng-0002",
                "posted_at": None, "location": "San Francisco, California", "remote": "hybrid",
                "employment_type": "full-time", "comp_min": 175000, "comp_max": 215000, "comp_currency": "USD"}
    for k, v in expected.items():
        assert rec[k] == v, k
    assert "Build LLM agents" in rec["_jd_text"]


def test_pinpoint_hides_pay_the_employer_marks_invisible_and_is_registered():
    res = fa.fetch_pinpoint(PPCO, _pp(), NOW, title_filter=None)
    legal = next(r for r in res.records if "Legal" in r["title"])
    assert legal["comp_min"] is None and legal["remote"] == "remote"
    assert fa.FETCHERS["pinpoint"] is fa.fetch_pinpoint and "pinpoint" in fa.SUPPORTED_VENDORS


# ----------------------------------------------------------------------------- teamtailor RSS (R-portals §3)
TTCO = {"name": "Teamco", "slug": "teamco", "ats_vendor": "teamtailor", "board_token": "career.teamco.example.test"}
TT_URL = "https://career.teamco.example.test/jobs.rss?per_page=100&offset=0"


def _tt():
    return FakeHttp({("GET", TT_URL): (FIXTURES / "teamtailor_jobs.rss").read_text(encoding="utf-8")})


def test_teamtailor_reads_the_public_rss_feed():
    http = _tt()
    res = fa.fetch_teamtailor(TTCO, http, NOW, title_filter=fa.TitleFilter([fa.title_pattern("ai engineer")], []))
    assert res.complete and len(res.all_urls) == 2 and len(res.records) == 1 and len(http.calls) == 1
    rec = res.records[0]
    expected = {"id": "teamco-700002", "title": "Founding AI Engineer", "source": "teamtailor",
                "ats_vendor": "teamtailor", "source_url": "https://career.teamco.example.test/jobs/700002-founding-ai-engineer",
                "posted_at": "2026-09-29T16:00:00Z", "location": "San Francisco, United States", "remote": "remote",
                "comp_min": 165000, "comp_max": 205000}
    for k, v in expected.items():
        assert rec[k] == v, k
    other = fa.fetch_teamtailor(TTCO, _tt(), NOW, title_filter=None)
    assert {r["remote"] for r in other.records} == {"remote", "hybrid"}


def test_teamtailor_token_without_a_dot_is_a_teamtailor_subdomain_and_is_registered():
    assert fa.teamtailor_feed_url("acme", 0) == "https://acme.teamtailor.com/jobs.rss?per_page=100&offset=0"
    assert fa.FETCHERS["teamtailor"] is fa.fetch_teamtailor and "teamtailor" in fa.SUPPORTED_VENDORS


# ----------------------------------------------------------------------------- recruitee (R-portals §3)
RCCO = {"name": "Bankco", "slug": "bankco", "ats_vendor": "recruitee", "board_token": "bankco"}
RC_URL = "https://bankco.recruitee.com/api/offers/"


def _rc():
    return FakeHttp({("GET", RC_URL): load_fixture("recruitee_offers.json")})


def test_recruitee_normalizes_offers_with_salary_and_work_mode():
    res = fa.fetch_recruitee(RCCO, _rc(), NOW, title_filter=fa.TitleFilter([fa.title_pattern("ai engineer")], []))
    assert res.complete and len(res.all_urls) == 2 and len(res.records) == 1
    rec = res.records[0]
    expected = {"id": "bankco-100502", "title": "AI Engineer, LLM Agents", "source": "recruitee", "ats_vendor": "recruitee",
                "source_url": "https://careers.bankco.example.test/o/ai-engineer-llm-agents",
                "apply_url": "https://careers.bankco.example.test/o/ai-engineer-llm-agents/c/new", "posted_at": "2026-09-30T15:00:00Z",
                "location": "San Francisco, California, United States", "remote": "remote", "employment_type": "full-time",
                "comp_min": 170000, "comp_max": 210000, "comp_currency": "USD"}
    for k, v in expected.items():
        assert rec[k] == v, k
    ios = next(r for r in fa.fetch_recruitee(RCCO, _rc(), NOW, title_filter=None).records if "iOS" in r["title"])
    assert ios["remote"] == "hybrid" and ios["comp_min"] is None


def test_recruitee_is_a_registered_and_supported_vendor():
    assert fa.FETCHERS["recruitee"] is fa.fetch_recruitee and "recruitee" in fa.SUPPORTED_VENDORS


# ----------------------------------------------------------------------------- scout lessons
def _ashby_board(titles, published):
    return {"jobs": [{"id": f"0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a{i:02d}", "title": t, "isListed": True,
                      "jobUrl": f"https://jobs.ashbyhq.com/acme/0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a{i:02d}",
                      "publishedAt": p, "location": "San Francisco", "workplaceType": "OnSite",
                      "employmentType": "FullTime", "descriptionPlain": "Build agents."}
                     for i, (t, p) in enumerate(zip(titles, published))]}


def test_l_e1019a7b_an_ashby_board_with_one_publishedAt_for_every_role_is_date_unknown():
    same = ["2026-07-08T12:00:00.000+00:00"] * 3
    http = FakeHttp({("GET", "https://api.ashbyhq.com/posting-api/job-board/acme"):
                     _ashby_board(["AI Engineer", "ML Engineer", "Applied AI Engineer"], same)})
    res = fa.fetch_ashby({"name": "Acme", "slug": "acme", "board_token": "acme"}, http, NOW, title_filter=None)
    assert all(r["posted_at"] is None and r["posted_unknown"].startswith("board-wide republish 2026-07-08")
               for r in res.records)
    assert any("board-wide republish" in n for n in res.notes)
    varied = FakeHttp({("GET", "https://api.ashbyhq.com/posting-api/job-board/acme"):
                       _ashby_board(["AI Engineer", "ML Engineer", "Applied AI Engineer"],
                                    ["2026-09-01T00:00:00Z", "2026-09-20T00:00:00Z", "2026-09-30T00:00:00Z"])})
    res = fa.fetch_ashby({"name": "Acme", "slug": "acme", "board_token": "acme"}, varied, NOW, title_filter=None)
    assert all(r["posted_at"] and "posted_unknown" not in r for r in res.records)


def test_plain_product_engineer_counts_at_listed_ai_first_companies_only():
    # On the list and AI-first, a plain Product Engineer seat is a software engineer seat (policy.title_filter.
    # ai_first_include). AI-first = any listed company whose segment is not big-tech or enterprise and whose tier is
    # not fallback.
    # "Design engineer" stays out. An unlisted company (HN, alert mail) reads the plain filter: no plain Product Engineer.
    board = _ashby_board(["Product Engineer, New Products", "Design Engineer", "AI Engineer"], ["2026-09-30T00:00:00Z"] * 2
                         + ["2026-09-29T00:00:00Z"])
    url = "https://api.ashbyhq.com/posting-api/job-board/whisperco"
    tf = fa.default_title_filter()
    both, ai_only = {"Product Engineer, New Products", "AI Engineer"}, {"AI Engineer"}
    for tier, segment, expected in (("dream", None, both), ("strong", None, both), ("good", None, both),
                                    ("good", "startup", both), ("dream", "ai-lab", both), (None, None, both),
                                    ("dream", "big-tech", ai_only), ("good", "enterprise", ai_only),
                                    ("fallback", None, ai_only)):
        co = {"name": "Whisperco", "slug": "whisperco", "ats_vendor": "ashby", "board_token": "whisperco", "tier": tier,
              "segment": segment}
        res = fa.fetch_company(co, FakeHttp({("GET", url): board}), NOW, tf)
        assert {r["title"] for r in res.records} == expected, (tier, segment)
    assert not tf.matches("Product Engineer") and fa.ai_first({"tier": "good"}) and not fa.ai_first({"segment": "big-tech"})


def test_l_bfdf6ae0_sitemap_pages_are_read_newest_lastmod_first(monkeypatch):
    monkeypatch.setattr(fa, "_sitemap_sleep", lambda s: None)
    monkeypatch.setattr(fa, "SITEMAP_MAX_FETCH", 1)
    old, new = SYN_AI, SYN_AI.replace("ai-engineer-simulation", "ai-engineer-agents").replace("101000000001", "101000000009")
    body = (f"<url><loc>{old}</loc><lastmod>2026-08-01T00:00:00Z</lastmod></url>"
            f"<url><loc>{new}</loc><lastmod>2026-09-30T00:00:00Z</lastmod></url>")
    routes = {("GET", f"{SYN}/robots.txt"): SYN_ROBOTS,
              ("GET", SYN_MAP): f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>',
              ("GET", new): _ld_page("AI Engineer, Agents", ident="9")}
    http = FakeHttp(routes)
    res = fa.fetch_company(CHIPDESIGNCO, http, NOW, fa.default_title_filter())
    assert _gets(http) == [f"{SYN}/robots.txt", SYN_MAP, new]                # the newest page uses the one fetch
    assert [r["title"] for r in res.records] == ["AI Engineer, Agents"]
    assert fa.parse_sitemap_entries(routes[("GET", SYN_MAP)]) == [(old, "2026-08-01T00:00:00Z"), (new, "2026-09-30T00:00:00Z")]


def test_l_8032a608_board_tokens_keep_their_exact_case():
    http = FakeHttp({("GET", "https://api.ashbyhq.com/posting-api/job-board/Primeco"):
                     _ashby_board(["Member of Technical Staff - Prime Agent"], ["2026-09-17T00:00:00Z"])})
    co = {"name": "Primeco", "slug": "prime-intellect", "ats_vendor": "Ashby", "board_token": "Primeco"}
    res = fa.fetch_company(co, http, NOW, title_filter=None)
    assert not res.errors and http.calls[0][1].endswith("/Primeco")


# The company-researcher needs each title, whether it matches, and the count that match;
# --dry-run printed counts only. --list prints every posting with its match flag and URL; what is written is unchanged.
def test_list_prints_every_title_its_match_and_url(root, capsys):
    from disc_harness import load_fixture
    jobs = load_fixture("greenhouse_labco.json")["jobs"]
    tf = fa.load_title_filter(root)
    want = sum(1 for j in jobs if tf.matches(j["title"]))
    rc = fa.main(["--root", str(root), "--board", "greenhouse:labco:Labco", "--dry-run", "--list"],
                 http=FakeHttp(ats_routes()), now=NOW)
    out = capsys.readouterr().out
    assert rc == 0
    rows = [l for l in out.splitlines() if l.startswith("  [")]
    assert len(rows) == len(jobs) and sum(1 for r in rows if r.startswith("  [match]")) == want
    assert all(" · http" in r for r in rows)
    assert f"labco: {len(jobs)} on board, {want} match" in out
    assert "dry-run (nothing written)" in out and not (root / "jobs").exists()


def test_list_writes_only_the_matching_records_on_a_real_run(root, capsys):
    from disc_harness import load_fixture
    jobs = load_fixture("greenhouse_labco.json")["jobs"]
    want = sum(1 for j in jobs if fa.load_title_filter(root).matches(j["title"]))
    fa.main(["--root", str(root), "--board", "greenhouse:labco:Labco", "--list"], http=FakeHttp(ats_routes()),
            now=NOW)
    assert len(list((root / "jobs").glob("*.md"))) == want


# Every stored JD from this kind of site once lacked about half the posting (the team summary, the responsibilities,
# the level, the team, the pay). Fixture: a jobDetails API answer; its page carries the same object in the router
# hydration data.
def _apple_page(job):
    return ('<html><script>window.__staticRouterHydrationData = JSON.parse("' +
            json.dumps(json.dumps({"loaderData": {"jobDetails": {"jobsData": job}}}))[1:-1] + '");</script></html>')


def test_an_apple_page_keeps_the_whole_posting():
    from disc_harness import load_fixture
    job = load_fixture("fruitco_jobdetails_100123-0201.json")["res"]
    post = fa.job_posting(_apple_page(job))
    text = fa.html_to_text(post["description"])
    order = ["Summary", "Description", "Responsibilities", "Minimum Qualifications", "Preferred Qualifications"]
    at = [text.find(h) for h in order]
    assert all(i >= 0 for i in at) and at == sorted(at)
    for field in ("jobSummary", "description", "responsibilities", "minimumQualifications", "preferredQualifications"):
        first = fa.html_to_text(job[field]).split("\n")[0].strip()[:60]
        assert first in text, field
    assert "Level: Machine Learning Band 3 to Machine Learning Band 4" in text and "Team: Product Software" in text
    assert "$141,000 to $262,000" in text and "$149,500 to $276,000" in text and "Cupertino" in text
    assert post["salary"] == (141000, 276000, "USD")
    assert "EEO" not in text and "Accessibility" not in text          # footers other than pay are boilerplate


def test_an_apple_page_without_the_new_fields_still_reads():
    post = fa.job_posting(APPLE_PAGE)
    assert "Build ML for health." in post["description"] and post["salary"] == (None, None, None)


# Some sites' JSON-LD carries `responsibilities` and `qualifications` beside a short description, and the reader once
# kept the description only. Every schema.org JobPosting text field is now kept.
def _ld_json_page(ld):
    return f'<html><script type="application/ld+json">{json.dumps(ld)}</script></html>'


def test_json_ld_responsibilities_and_qualifications_are_kept():
    post = fa.job_posting(_ld_json_page({
        "@type": "JobPosting", "title": "Research Engineer", "datePosted": "2026-09-30",
        "description": "Socialco is seeking Research Engineers to join the Privacy Evals team.",
        "responsibilities": ["Build privacy evaluations for LLMs", "Ship eval pipelines"],
        "qualifications": "Currently has a PhD or 3+ years in ML. Python.",
        "skills": "PyTorch", "jobBenefits": "Bonus and equity"}))
    text = fa.html_to_text(post["description"])
    for piece in ("Privacy Evals team", "Responsibilities", "Build privacy evaluations for LLMs", "Ship eval pipelines",
                  "Qualifications", "PhD or 3+ years", "Skills", "PyTorch", "Benefits", "Bonus and equity"):
        assert piece in text, piece


def test_json_ld_fields_already_in_the_description_are_not_repeated():
    desc = "<p>About.</p><h3>Responsibilities</h3><ul><li>Build evals</li></ul>"
    post = fa.job_posting(_ld_json_page({"@type": "JobPosting", "title": "X", "description": desc,
                                    "responsibilities": "Build evals"}))
    assert fa.html_to_text(post["description"]).count("Build evals") == 1


def test_meta_items_joined_by_nbsp_become_a_list():
    post = fa.job_posting(_ld_json_page({"@type": "JobPosting", "title": "X", "description": "About the team.",
                                         "responsibilities": "Build privacy evals for LLMs&nbsp;Ship the eval pipelines"
                                                             "&nbsp;Drive large efforts across teams"}))
    text = fa.html_to_text(post["description"])
    assert "- Build privacy evals for LLMs" in text and "- Ship the eval pipelines" in text


# A Workday board larger than the page cap (large employers list thousands of rows) once listed only its first 400 rows, so the AI roles past row 400 were never seen. Over the cap, the board is
# read through role keyword searches (searchText), unioned by path; never complete (a search is not the whole board).
def _wd_post(i, title):
    return {"title": title, "externalPath": f"/job/US/{title.replace(' ', '-')}_JR{i:05d}", "postedOn": "Posted Today",
            "bulletFields": [f"JR{i:05d}"], "locationsText": "US, CA, Santa Clara"}


def _big_board(total=900):
    generic = [_wd_post(i, f"Sales Associate {i}") for i in range(6)]
    hits = {q: [_wd_post(100 + n, t)] for n, (q, t) in enumerate(
        [("machine learning", "Machine Learning Engineer"), ("artificial intelligence", "AI Software Engineer"),
         ("applied scientist", "Applied Scientist")])}
    seen = []

    def jobs(payload):
        seen.append((payload["searchText"], payload["offset"]))
        if not payload["searchText"]:
            return {"total": total if payload["offset"] == 0 else 0, "jobPostings": generic[payload["offset"]:payload["offset"] + 2]}
        rows = hits.get(payload["searchText"], [])
        return {"total": len(rows) if payload["offset"] == 0 else 0, "jobPostings": rows if payload["offset"] == 0 else []}
    routes = {("POST", WD_BASE + "/jobs"): jobs}
    for rows in hits.values():
        for p in rows:
            routes[("GET", WD_BASE + p["externalPath"])] = {"jobPostingInfo": {"title": p["title"], "jobDescription": "<p>ML</p>"}}
    return FakeHttp(routes), seen


def test_a_board_over_the_cap_is_read_by_role_searches(root):
    http, seen = _big_board()
    res = fa.fetch_workday(CHIPCO, http, NOW, title_filter=fa.load_title_filter(root), page_size=2, max_pages=3)
    titles = sorted(r["title"] for r in res.records)
    assert titles == ["AI Software Engineer", "Applied Scientist", "Machine Learning Engineer"]
    assert res.complete is False and {q for q, _ in seen if q} >= {"machine learning", "artificial intelligence"}
    assert [o for q, o in seen if not q] == [0]                     # the unfiltered list stops after its first page
    assert any("over the page cap" in n and "role searches" in n for n in res.notes)


def test_a_board_within_the_cap_is_read_whole_as_before(root):
    http, seen = _big_board(total=6)
    res = fa.fetch_workday(CHIPCO, http, NOW, title_filter=fa.load_title_filter(root), page_size=2, max_pages=3)
    assert all(not q for q, _ in seen) and res.complete is True


def test_a_company_search_text_wins_over_the_role_searches(root):
    http, seen = _big_board()
    fa.fetch_workday(dict(CHIPCO, search_text="machine learning"), http, NOW, title_filter=fa.load_title_filter(root),
                     page_size=2, max_pages=3)
    assert {q for q, _ in seen} == {"machine learning"}


# Sitemaps: large sites leave pages for the next run. A page that failed or had no JobPosting was retried every run
# and took a slot from the leftovers; it now waits SITEMAP_FAILED_RETRY_DAYS. And the run's cap is sized so the leftovers are read across the day's runs.
def test_a_page_without_a_job_posting_waits_before_it_is_tried_again(monkeypatch, tmp_path):
    monkeypatch.setattr(fa, "_sitemap_sleep", lambda s: None)
    cache = tmp_path / "chipdesignco.json"
    routes = _syn_routes([SYN_AI], {SYN_AI: "<html>no posting here</html>"})
    http = FakeHttp(routes)
    fa.fetch_company(CHIPDESIGNCO, http, NOW, fa.default_title_filter(), cache_path=cache)
    assert SYN_AI in _gets(http) and json.loads(cache.read_text())[SYN_AI]["failed"]
    http2 = FakeHttp(routes)
    fa.fetch_company(CHIPDESIGNCO, http2, NOW + timedelta(days=1), fa.default_title_filter(), cache_path=cache)
    assert SYN_AI not in _gets(http2)                                      # inside the wait: not fetched
    http3 = FakeHttp(routes)
    fa.fetch_company(CHIPDESIGNCO, http3, NOW + timedelta(days=fa.SITEMAP_FAILED_RETRY_DAYS), fa.default_title_filter(),
                     cache_path=cache)
    assert SYN_AI in _gets(http3)                                          # after it: tried again


@pytest.mark.parametrize("hour, pending, cap", [
    (7, 30, 40),          # few left: the base cap
    (7, 600, 100),        # 6 runs left today: 600 / 6
    (22, 600, 150),       # the last run: as many as the ceiling allows
    (23, 90, 90),         # after the last run: this run is the only one left
])
def test_the_run_cap_spreads_the_leftovers_over_the_day(hour, pending, cap):
    from zoneinfo import ZoneInfo
    now = datetime(2026, 10, 1, hour, 5, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert fa.sitemap_run_cap(pending, now) == cap


def test_a_known_workday_posting_is_re_sighted_without_its_detail_fetch(root):
    http, _ = _big_board()
    host = "https://chipco.wd5.myworkdayjobs.com/CHIPCOExternalCareerSite"
    known = {host + "/job/US/Machine-Learning-Engineer_JR00100"}
    res = fa.fetch_workday(CHIPCO, http, NOW, title_filter=fa.load_title_filter(root), page_size=2, max_pages=3,
                           known_urls=known)
    gets = [u for m, u, _ in http.calls if m == "GET"]
    assert not any(u.endswith("Machine-Learning-Engineer_JR00100") for u in gets)       # no detail fetch
    assert {"id": None, "source_url": next(iter(known)), "_resight": True} in res.records
    assert any(u.endswith("Applied-Scientist_JR00102") for u in gets)                   # a new one still is read


# Oracle Recruiting Cloud's public REST API (the one its candidate site calls), found by ats_detect. Fixtures: a search
# and one requisition (orbitco.fa.us2.oraclecloud.com, site jobsearch).
ORC = "https://orbitco.fa.us2.oraclecloud.com/hcmRestApi/resources/latest"
ORACLE = {"name": "Orbitco", "slug": "orbitco", "ats_vendor": "oracle", "board_token": "orbitco.fa.us2.oraclecloud.com|jobsearch"}


def _orc_http(known_detail=True):
    search = load_fixture("oracle_orc_search_jobsearch.json")
    detail = load_fixture("oracle_orc_detail_100302.json")
    seen = []

    def route(url, params):
        seen.append(params)
        return search if "machine learning" in params["finder"] else {"items": [{"TotalJobsCount": 0, "requisitionList": []}]}
    r = {("GET", f"{ORC}/recruitingCEJobRequisitions"): lambda p: route(None, p)}
    for rid in ("100301", "100302", "100303"):
        r[("GET", f"{ORC}/recruitingCEJobRequisitionDetails")] = lambda p: detail if '"100302"' in p["finder"] else {
            "items": [dict(detail["items"][0], Id=p["finder"].split('"')[1], Title="Other role")]}
    return FakeHttp(r), seen


def test_oracle_searches_by_role_and_records_the_full_posting(root):
    http, seen = _orc_http()
    tf = fa.TitleFilter([fa.title_pattern("ai"), fa.title_pattern("software engineer"), fa.title_pattern("architect")], [])
    res = fa.fetch_company(ORACLE, http, NOW, tf)
    assert not res.errors and res.complete is False
    assert any("siteNumber=jobsearch" in p["finder"] and 'keyword="machine learning"' in p["finder"] for p in seen)
    rec = next(r for r in res.records if r.get("req_id") == "100302")
    assert rec["title"] == "AI & Emerging Technology Strategist – Orbit Cloud Applications" and rec["source"] == "oracle"
    assert rec["source_url"] == "https://orbitco.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/jobsearch/job/100302"
    assert rec["posted_at"].startswith("2026-09-30") and rec["location"] == "United States"
    jd = fa.jd_text_from_body(rec["_body"]) if rec.get("_body") else rec["_jd_text"]
    assert "Responsibilities" in jd and "Qualifications" in jd


def test_oracle_re_sights_known_postings_without_the_detail_call(root):
    http, _ = _orc_http()
    url = "https://orbitco.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/jobsearch/job/100302"
    res = fa.fetch_oracle(ORACLE, http, NOW, None, known_urls={url})
    assert {"id": None, "source_url": url, "_resight": True} in res.records
    assert not any('"100302"' in str(arg) for m, u, arg in http.calls if u.endswith("Details"))


def test_an_oracle_token_without_a_site_is_an_error_not_a_guess():
    res = fa.fetch_company(dict(ORACLE, board_token="orbitco.fa.us2.oraclecloud.com"), FakeHttp({}), NOW, None)
    assert res.errors and "site" in res.errors[0]


def test_oracle_is_a_supported_vendor_and_in_the_job_schema():
    import yaml as _y
    from js_common import OS_ROOT
    assert "oracle" in fa.SUPPORTED_VENDORS and "oracle" in fa.FETCHERS
    enums = _y.safe_load((__import__("agency_paths").SCHEMAS_DIR / "job.yaml").read_text())["enums"]
    assert "oracle" in enums["source"] and "oracle" in enums["ats_vendor"]


# Eightfold's PCSX API (robots.txt allows /api/pcsx; the older /api/apply/v2 answers 403). Fixtures: a search for
# "machine learning" and one position.
QC = "https://careers.wirelessco.example.test/api/pcsx"
WIRELESSCO = {"name": "Wirelessco", "slug": "wirelessco", "ats_vendor": "eightfold", "board_token": "careers.wirelessco.example.test|wirelessco.example.test"}


def _ef_http():
    search = load_fixture("eightfold_pcsx_search_wirelessco.json")
    detail = load_fixture("eightfold_pcsx_detail_wirelessco.json")
    seen = []

    def s(p):
        seen.append(p)
        return search if p["query"] == "machine learning" and p["start"] == 0 else {"data": {"positions": [], "count": 0}}
    return FakeHttp({("GET", f"{QC}/search"): s, ("GET", f"{QC}/position_details"): lambda p: detail}), seen


def test_eightfold_searches_by_role_and_reads_the_position(root):
    http, seen = _ef_http()
    first = load_fixture("eightfold_pcsx_search_wirelessco.json")["data"]["positions"][0]
    res = fa.fetch_company(WIRELESSCO, http, NOW, None)
    assert not res.errors and res.complete is False
    assert all(p["domain"] == "wirelessco.example.test" for p in seen) and any(p["query"] == "machine learning" for p in seen)
    rec = next(r for r in res.records if r["req_id"] == first["displayJobId"])
    assert rec["source"] == "eightfold" and rec["title"] == first["name"]
    assert rec["source_url"].startswith("https://careers.wirelessco.example.test/careers/job/")
    assert rec["posted_at"] and "Job Overview" in rec["_jd_text"]


def test_eightfold_re_sights_known_positions(root):
    http, _ = _ef_http()
    first = load_fixture("eightfold_pcsx_search_wirelessco.json")["data"]["positions"][0]
    url = "https://careers.wirelessco.example.test" + first["positionUrl"]
    res = fa.fetch_eightfold(WIRELESSCO, http, NOW, None, known_urls={url})
    assert {"id": None, "source_url": url, "_resight": True} in res.records


def test_eightfold_is_supported_and_in_the_job_schema():
    import yaml as _y
    from js_common import OS_ROOT
    enums = _y.safe_load((__import__("agency_paths").SCHEMAS_DIR / "job.yaml").read_text())["enums"]
    assert "eightfold" in fa.SUPPORTED_VENDORS and "eightfold" in enums["source"] and "eightfold" in enums["ats_vendor"]


# A custom domain can ignore siteNumber (two sites and a made-up site return the same requisitions). One company's
# postings are the "Examplecity" ones of that feed: a location filter splits the two companies.
SU = "https://careersearch.examplestate.edu/hcmRestApi/resources/latest"


def _su_http():
    reqs = [{"Id": "1", "Title": "Machine Learning Engineer", "PostedDate": "2026-09-30", "PrimaryLocation": "Examplestate, CA, United States"},
            {"Id": "2", "Title": "AI Software Engineer", "PostedDate": "2026-09-30", "PrimaryLocation": "Examplecity, CA, United States"}]
    calls = []

    def search(p):
        calls.append(p["finder"])
        if 'keyword="machine learning"' in p["finder"] or "siteNumber=ZZ_NO_SUCH_SITE" in p["finder"]:
            return {"items": [{"TotalJobsCount": 2, "requisitionList": reqs}]}
        return {"items": [{"TotalJobsCount": 0, "requisitionList": []}]}
    detail = lambda p: {"items": [{"Title": "x", "ExternalDescriptionStr": "<p>ML</p>",
                                    "PrimaryLocation": "Examplecity, CA, United States" if '"2"' in p["finder"] else "Examplestate, CA"}]}
    return FakeHttp({("GET", f"{SU}/recruitingCEJobRequisitions"): search,
                     ("GET", f"{SU}/recruitingCEJobRequisitionDetails"): detail}), calls


def test_a_location_filter_keeps_one_company_out_of_a_shared_feed():
    http, _ = _su_http()
    labsite = {"name": "LABSITE", "slug": "labsite", "ats_vendor": "oracle", "board_token": "careersearch.examplestate.edu|LABSITE",
            "oracle_location": "Examplecity"}
    examplestate = dict(labsite, name="Examplestate", slug="examplestate", board_token="careersearch.examplestate.edu|examplestate",
                    oracle_location=None, oracle_exclude_location="Examplecity")
    a = fa.fetch_oracle(labsite, http, NOW, None)
    b = fa.fetch_oracle(examplestate, _su_http()[0], NOW, None)
    assert [r["req_id"] for r in a.records] == ["2"] and [r["req_id"] for r in b.records] == ["1"]


def test_a_custom_domain_that_ignores_the_site_number_is_noted():
    http, calls = _su_http()
    res = fa.fetch_oracle({"name": "LABSITE", "slug": "labsite", "board_token": "careersearch.examplestate.edu|LABSITE"}, http, NOW, None)
    assert any("ignores the site number" in n for n in res.notes)
    assert any("siteNumber=ZZ_NO_SUCH_SITE" in c for c in calls)


def test_an_oraclecloud_host_is_not_checked_for_the_site_number():
    http, seen = _orc_http()
    fa.fetch_oracle(ORACLE, http, NOW, None)
    assert not any("ZZ_NO_SUCH_SITE" in p["finder"] for p in seen)


# The long readers stop between pages at the run's fetch deadline (set by pipeline.py).
def test_workday_role_searches_stop_at_the_run_deadline(root):
    http, seen = _big_board()
    fa.set_deadline(lambda: 100.0, 50.0)                     # already past
    try:
        res = fa.fetch_workday(CHIPCO, http, NOW, title_filter=fa.load_title_filter(root), page_size=2, max_pages=3)
    finally:
        fa.set_deadline(None, None)
    assert not any(q for q, _ in seen) and any("run deadline" in n for n in res.notes)


def test_sitemap_pages_wait_for_the_next_run_past_the_deadline(monkeypatch):
    monkeypatch.setattr(fa, "_sitemap_sleep", lambda s: None)
    http = FakeHttp(_syn_routes([SYN_AI], {SYN_AI: _ld_page("AI Engineer, Simulation")}))
    fa.set_deadline(lambda: 100.0, 50.0)
    try:
        res = fa.fetch_company(CHIPDESIGNCO, http, NOW, fa.default_title_filter())
    finally:
        fa.set_deadline(None, None)
    assert SYN_AI not in _gets(http) and any("left for the next run" in n for n in res.notes)


def test_no_deadline_by_default():
    assert fa.past_deadline() is False


def test_k224_q10_the_real_roles_md_keeps_plain_product_engineer_out_of_the_global_filter():
    # a backticked `product engineer` in roles.md section 1 would let it through for unlisted companies too
    from pathlib import Path
    tf = fa.load_title_filter(__import__("layout").JS)
    assert not tf.matches("Product Engineer") and tf.matches("AI Product Engineer")


# No long reader once checked the deadline inside its loops, so a fetch ran far past its deadline (a workday detail
# loop after its searches were cut). Every page and detail loop now checks it; a cut board keeps what it read, is marked `cut`, never closes anything and
# says what is left for the next run.
ML = "Machine Learning Engineer"


class _Synthetic:
    """A board of any vendor with more pages than a deadline allows: every list page full, every detail empty."""
    def __init__(self):
        self.calls, self.n = [], 0

    def _ids(self, k):
        self.n += k
        return range(self.n - k, self.n)

    def _serve(self, method, url, arg):
        self.calls.append((method, url))
        if url.endswith("/jobs") and method == "POST":                                  # workday listing
            return {"total": 200, "jobPostings": [{"title": ML, "externalPath": f"/job/x_R{i:05d}",
                                                    "bulletFields": [f"R{i:05d}"]} for i in self._ids(20)]}
        if "myworkdayjobs.com" in url:
            return {"jobPostingInfo": {"title": ML}}
        if "smartrecruiters" in url:
            if url.endswith("/postings"):
                return {"totalFound": 1000, "content": [{"id": str(i), "name": ML} for i in self._ids(fa.SR_PAGE)]}
            return {}
        if "rippling" in url:
            if url.endswith("/jobs"):
                return [{"uuid": str(i), "name": ML, "url": f"https://ats.rippling.com/acme/jobs/{i}"} for i in range(50)]
            return {}
        if "/api/jobs" in url:                                                              # jibe
            return {"totalCount": 5000, "jobs": [{"data": {"title": ML, "slug": str(i), "req_id": str(i)}}
                                                  for i in self._ids(fa.JIBE_PAGE)]}
        if "search.json" in url:                                                            # amazon
            return {"hits": 5000, "jobs": [{"job_path": f"/jobs/{i}", "title": ML, "country_code": "USA"}
                                           for i in self._ids(fa.AMAZON_PAGE)]}
        if "recruitingCEJobRequisitions" in url:                                            # oracle
            return {"items": [{"TotalJobsCount": 5000,
                               "requisitionList": [{"Id": str(i), "Title": ML} for i in self._ids(fa.ORACLE_PAGE)]}]}
        if "recruitingCEJobRequisitionDetails" in url:
            return {"items": [{}]}
        if url.endswith("/api/pcsx/search"):                                                # eightfold
            return {"data": {"count": 5000, "positions": [{"id": str(i), "name": ML} for i in self._ids(10)]}}
        if "position_details" in url:
            return {"data": {}}
        raise AssertionError(f"unexpected {method} {url}")

    def get_json(self, url, params=None):
        return self._serve("GET", url, params)

    def post_json(self, url, payload):
        return self._serve("POST", url, payload)

    def get_text(self, url, headers=None):
        self.calls.append(("GET", url))
        items = "".join(f"<item><title>{ML}</title><link>https://acme.teamtailor.com/jobs/{i}</link>"
                        f"<guid>{i}</guid></item>" for i in self._ids(fa.TT_PAGE))
        return 200, f"<rss><channel>{items}</channel></rss>"


LONG_READERS = {
    "workday": {"board_token": "acme.wd1.myworkdayjobs.com/acme/External"},
    "smartrecruiters": {"board_token": "Acme"},
    "rippling": {"board_token": "acme"},
    "teamtailor": {"board_token": "acme.teamtailor.com"},
    "jibe": {"board_token": "careers.acme.com"},
    "amazon": {"board_token": "www.amazon.jobs"},
    "oracle": {"board_token": "acme.fa.oraclecloud.com|CX_1"},
    "eightfold": {"board_token": "careers.acme.com|acme.com"},
}


@pytest.mark.parametrize("vendor", sorted(LONG_READERS))
def test_every_long_reader_stops_at_the_deadline_inside_its_loops(vendor, monkeypatch):
    for name in ("_jibe_sleep", "_amazon_sleep"):
        monkeypatch.setattr(fa, name, lambda s: None)
    company = {"name": "Acme", "slug": "acme", "ats_vendor": vendor, **LONG_READERS[vendor]}
    tf = fa.TitleFilter([fa.title_pattern("machine learning engineer")], [])
    free = _Synthetic()
    fa.fetch_company(company, free, NOW, tf)
    assert len(free.calls) > 4, "the synthetic board must need more than 4 requests"
    http = _Synthetic()
    fa.set_deadline(lambda: len(http.calls), 2.5)          # past the deadline once the third request is made
    try:
        res = fa.fetch_company(company, http, NOW, tf)
    finally:
        fa.set_deadline(None, None)
    assert len(http.calls) <= 3 and res.cut and not res.complete and not res.errors
    assert any("left for the next run" in n for n in res.notes)


class _OnePageWorkday(_Synthetic):
    def post_json(self, url, payload):
        return {**super().post_json(url, payload), "total": 20}


def test_a_cut_board_keeps_the_postings_it_read_in_full(monkeypatch):
    company = {"name": "Acme", "slug": "acme", "ats_vendor": "workday", **LONG_READERS["workday"]}
    http = _OnePageWorkday()
    fa.set_deadline(lambda: len(http.calls), 4.5)          # the listing page, then 4 details
    try:
        res = fa.fetch_company(company, http, NOW, fa.TitleFilter([fa.title_pattern("machine learning engineer")], []))
    finally:
        fa.set_deadline(None, None)
    assert res.cut and len(res.records) >= 3 and all(r.get("title") == ML for r in res.records)


def test_the_deadline_raised_by_http_is_a_deferral_not_a_board_error(monkeypatch):
    def boom(*a, **k):
        raise fa.DeadlineReached("GET https://x: the run deadline passed")
    monkeypatch.setitem(fa.FETCHERS, "greenhouse", boom)
    res = fa.fetch_company(LABCO, FakeHttp({}), NOW, None)
    assert res.cut and not res.errors and not res.complete and any("left for the next run" in n for n in res.notes)


def test_a_sitemap_page_cut_by_the_deadline_is_not_cached_as_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(fa, "_sitemap_sleep", lambda s: None)
    routes = _syn_routes([SYN_AI, SYN_RD], {SYN_AI: fa.DeadlineReached(f"GET {SYN_AI}: the run deadline passed"),
                                            SYN_RD: _ld_page("AI R&D Engineer")})
    cache = tmp_path / "chipdesignco.json"
    res = fa.fetch_company(CHIPDESIGNCO, FakeHttp(routes), NOW, fa.default_title_filter(), cache_path=cache)
    assert res.cut and not res.errors and SYN_AI not in json.loads(cache.read_text())
    assert any("left for the next run" in n for n in res.notes)


def test_a_crawl_delay_that_would_end_past_the_deadline_is_not_slept(monkeypatch):
    slept = []
    monkeypatch.setattr(fa, "_sitemap_sleep", slept.append)
    http = FakeHttp(_syn_routes([SYN_AI], {SYN_AI: _ld_page("AI Engineer, Simulation")}))
    fa.set_deadline(lambda: 0.0, fa.SITEMAP_DELAY_S - 1)   # less time left than one polite pause
    try:
        res = fa.fetch_company(CHIPDESIGNCO, http, NOW, fa.default_title_filter())
    finally:
        fa.set_deadline(None, None)
    assert slept == [] and _gets(http) == [f"{SYN}/robots.txt"] and res.cut


def test_a_job_record_is_written_whole_or_not_at_all(tmp_path, monkeypatch):
    rec = {"id": "acme-1", "title": ML, "company": "Acme", "company_slug": "acme", "status": "discovered"}
    path = fa.save_job(tmp_path, dict(rec))
    before = path.read_text()
    real = type(path).write_text

    def half(self, data, *a, **k):                          # a kill halfway through the write
        real(self, data[: len(data) // 2], *a, **k)
        raise OSError("killed")
    monkeypatch.setattr(type(path), "write_text", half)
    with pytest.raises(OSError):
        fa.save_job(tmp_path, dict(rec, title="Senior " + ML))
    assert path.read_text() == before


def test_the_real_roles_md_adds_no_identifier_as_a_title_pattern():
    # A backticked identifier in roles.md must not land in title_include as a pattern
    from pathlib import Path
    cfg = fa.read_roles_config(__import__("layout").JS)
    assert not [p for p in cfg["title_include"] if "_" in p]
