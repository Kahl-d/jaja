"""ats_detect.py: recognise the board vendor of each target the scouts cannot read yet, from fixed
signatures in its careers page (read once, robots-first, redirects followed), and propose ats_vendor and board_token
for companies_set.py. Dry run by default; no page is read twice; never a login, a platform or a stealth mode."""
from __future__ import annotations

import json

import pytest
import yaml

from disc_harness import *  # noqa: F401,F403
from disc_harness import FakeHttp

import ats_detect as ad  # noqa: E402


class PageHttp(FakeHttp):
    """FakeHttp plus get_page: a route value is the page text, or (status, text, final_url)."""

    def get_page(self, url, headers=None):
        value = self._serve("GET", url, headers)
        return value if isinstance(value, tuple) else (200, value, url)


@pytest.mark.parametrize("final, page, vendor, token", [
    ("https://acme.example/careers", '<a href="https://job-boards.greenhouse.io/acmeai/jobs/1">x</a>', "greenhouse", "acmeai"),
    ("https://acme.example/careers", '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>', "greenhouse", "acme"),
    ("https://jobs.lever.co/acme", "", "lever", "acme"),
    ("https://acme.example/careers", '<iframe src="https://jobs.ashbyhq.com/acme-labs"></iframe>', "ashby", "acme-labs"),
    ("https://exampleco.wd1.myworkdayjobs.com/en-US/ExampleCo", "", "workday", "exampleco.wd1/ExampleCo"),
    ("https://jobs.smartrecruiters.com/Acme1", "", "smartrecruiters", "Acme1"),
    ("https://careers-chipco.icims.com/jobs/search", "", "icims", "careers-chipco.icims.com"),
    ("https://lab.taleo.net/careersection/2/jobsearch.ftl", "", "taleo", "lab.taleo.net"),
    ("https://orbitco.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/jobsearch/requisitions", "", "oracle",
     "orbitco.fa.us2.oraclecloud.com|jobsearch"),
    ("https://career5.successfactors.eu/careers?company=EngCoP", "", "successfactors", "career5.successfactors.eu|EngCoP"),
    ("https://acme.eightfold.ai/careers", "", "eightfold", "acme.eightfold.ai|acme.com"),
    ("https://careers.acme.com/us/en", '<link href="https://cdn.phenompeople.com/CareerConnectResources/x.css">', "phenom",
     "careers.acme.com"),
])
def test_signatures(final, page, vendor, token):
    assert ad.detect(final, page)[:2] == (vendor, token)


def test_the_final_url_wins_over_links_on_the_page():
    page = '<a href="https://jobs.lever.co/other">old</a>'
    assert ad.detect("https://acme.wd5.myworkdayjobs.com/External", page)[:2] == ("workday", "acme.wd5/External")


def test_a_page_with_no_signature_but_job_postings_is_a_sitemap_candidate():
    page = '<script type="application/ld+json">{"@type":"JobPosting","title":"AI Engineer"}</script>'
    assert ad.detect("https://acme.example/careers", page)[0] == "schema-org"
    assert ad.detect("https://acme.example/careers", "<html>Join us</html>")[0] is None


CO = {"name": "ExampleCo", "slug": "exampleco", "ats_vendor": "unknown", "careers_url": "https://www.exampleco.com/careers/"}


def _http(robots="User-agent: *\nAllow: /\n", page=None):
    page = page if page is not None else (200, "<html>careers</html>", "https://exampleco.wd1.myworkdayjobs.com/en-US/ExampleCo")
    return PageHttp({("GET", "https://www.exampleco.com/robots.txt"): robots, ("GET", CO["careers_url"]): page})


def test_a_target_is_read_once_robots_first_and_proposed():
    http = _http()
    row = ad.check(http, CO, js_root=None)
    assert [u for _, u, _ in http.calls] == ["https://www.exampleco.com/robots.txt", CO["careers_url"]]
    assert (row["state"], row["vendor"], row["token"]) == ("detected", "workday", "exampleco.wd1/ExampleCo")
    assert row["readable"] is True                                # fetch_ats has a workday reader


def test_robots_disallow_reads_nothing_more():
    http = _http(robots="User-agent: *\nDisallow: /careers/\n")
    row = ad.check(http, CO, js_root=None)
    assert row["state"] == "robots" and CO["careers_url"] not in [u for _, u, _ in http.calls]


def test_a_vendor_without_a_reader_is_flagged_as_needing_an_adapter():
    http = _http(page=(200, "", "https://careers-chipco.icims.com/jobs"))
    row = ad.check(http, dict(CO, slug="amd"), js_root=None)
    assert (row["vendor"], row["readable"]) == ("icims", False)


def test_a_careers_page_on_a_platform_whose_terms_forbid_automation_is_manual():
    co = dict(CO, slug="linkedin", careers_url="https://www.linkedin.com/company/linkedin/jobs/")
    http = PageHttp({})
    row = ad.check(http, co, js_root=None)
    assert (row["state"], row["vendor"]) == ("terms", "manual") and http.calls == []


def test_targets_are_the_unread_ones():
    rows = [{"slug": "a", "ats_vendor": "other", "careers_url": "https://a.example"},
            {"slug": "b", "ats_vendor": "greenhouse", "board_token": "b"},
            {"slug": "c", "ats_vendor": "greenhouse", "careers_url": "https://c.example"},     # no token
            {"slug": "d", "ats_vendor": "taleo", "careers_url": "https://d.example"},
            {"slug": "e", "ats_vendor": "workday", "careers_url": "https://e.wd1.myworkdayjobs.com/E"}]
    assert [r["slug"] for r in ad.targets(rows)] == ["a", "c", "d"]


def test_propose_appends_rows_for_companies_set_and_dry_run_writes_nothing(root, monkeypatch, capsys):
    (root / "targets").mkdir()
    (root / "targets" / "companies.yaml").write_text(yaml.safe_dump({"companies": [CO]}))
    monkeypatch.setattr(ad, "_http", lambda: _http())
    assert ad.main(["--root", str(root)]) == 0
    assert "exampleco" in capsys.readouterr().out and not (root / "state").exists()
    monkeypatch.setattr(ad, "_http", lambda: _http())
    assert ad.main(["--root", str(root), "--propose"]) == 0
    rows = [json.loads(x) for x in (root / "state" / "proposals" / "companies.jsonl").read_text().splitlines()]
    assert {(r["field"], r["value"]) for r in rows} == {("ats_vendor", "workday"), ("board_token", "exampleco.wd1/ExampleCo")}
    assert all(r["source"] == "ats_detect" and "companies_set.py set exampleco" in r["apply"] for r in rows)


# More shapes the first signatures missed.
@pytest.mark.parametrize("final, page, vendor, token", [
    ("https://www.bigbank.example/careers", '<a href="https://bigbank.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001">',
     "oracle", "bigbank.fa.oraclecloud.com|CX_1001"),
    ("https://hr.examplestate.edu/careers", '<a href="https://iazuqy.fa.ocs.oraclecloud.com/x">jobs</a>', "oracle",
     "iazuqy.fa.ocs.oraclecloud.com"),
    ("https://careersearch.examplestate.edu/hcmUI/CandidateExperience/en/sites/examplestate/", "", "oracle",
     "careersearch.examplestate.edu|examplestate"),
    ("https://careers.chipco.com/careers-home/jobs", '<script src="https://app.jibecdn.com/x.js"></script>'
     '<a href="https://careers-chipco.icims.com">', "jibe", "careers.chipco.com"),
    ("https://cloudco.example/careers/", '<a href="https://ats.rippling.com/cloudco/jobs">', "rippling", "cloudco"),
    ("https://apply.advisorco.com/en_US/careers", '<script>var x = "avature";</script>', "avature", "apply.advisorco.com"),
    ("https://careers.wirelessco.example.test/careers", '<script src="https://app.eightfold.ai/a.js"></script>'
     '<a href="https://wirelessco.eightfold.ai/careers">', "eightfold", "careers.wirelessco.example.test|example.test"),
])
def test_more_signatures(final, page, vendor, token):
    assert ad.detect(final, page)[:2] == (vendor, token)


def test_a_talentbrew_site_is_proposed_as_a_sitemap_from_robots():
    http = PageHttp({("GET", "https://jobs.exampleco.com/robots.txt"): "User-agent: *\nAllow: /\nSitemap: https://jobs.exampleco.com/sitemap.xml\n",
                     ("GET", "https://jobs.exampleco.com/"): (200, '<link href="https://tbcdn.talentbrew.com/x.css">',
                                                           "https://jobs.exampleco.com/")})
    row = ad.check(http, {"slug": "exampleco", "ats_vendor": "unknown", "careers_url": "https://jobs.exampleco.com/"}, js_root=None)
    assert (row["vendor"], row["token"], row["readable"]) == ("sitemap", "jobs.exampleco.com/sitemap.xml", True)


def test_eightfold_prefers_the_company_named_host_over_service_hosts():
    page = '<script src="https://vs-errors.eightfold.ai/e.js"></script><a href="https://wirelessco.eightfold.ai/careers">'
    assert ad.detect("https://careers.wirelessco.example.test/careers", page)[:2] == ("eightfold", "careers.wirelessco.example.test|example.test")
    assert ad.detect("https://careers.acme.com/c", '<script src="https://vs-errors.eightfold.ai/e.js">')[:2] == \
        ("eightfold", "careers.acme.com|acme.com")
    # the API is the careers host with the company's domain (an example: careers.wirelessco.example.test, domain=example.test)
    assert ad.detect("https://jobs.acme.com/x", '<a href="https://acme.eightfold.ai/careers">')[:2] == \
        ("eightfold", "jobs.acme.com|acme.com")


def test_an_oracle_page_on_its_own_domain_wins_over_hosts_named_in_the_page():
    page = '<a href="https://exuf-dev8.fa.ocs.oraclecloud.com/x">'
    assert ad.detect("https://careersearch.examplestate.edu/hcmUI/CandidateExperience/en/sites/LABSITE", page)[:2] == \
        ("oracle", "careersearch.examplestate.edu|LABSITE")


def test_oracle_short_site_paths_count_once_an_oracle_host_is_seen():
    page = '<a href="https://orbitco.fa.us2.oraclecloud.com/x">'
    assert ad.detect("https://careers.bigco.example/en/sites/jobsearch", page)[:2] == \
        ("oracle", "orbitco.fa.us2.oraclecloud.com|jobsearch")


# A company site still linked job-boards.greenhouse.io/writeco, a board that is gone (404), and the
# token was proposed unchecked. A public-board token is now checked with one API call first.
import fetch_ats as fa  # noqa: E402

WRITECO = {"slug": "writeco", "ats_vendor": "greenhouse", "careers_url": "https://writeco.example/company/careers"}


def _gh(api_value):
    return PageHttp({("GET", "https://writeco.example/robots.txt"): "User-agent: *\nAllow: /\n",
                     ("GET", WRITECO["careers_url"]): (200, '<a href="https://job-boards.greenhouse.io/writeco">',
                                                         WRITECO["careers_url"]),
                     ("GET", "https://boards-api.greenhouse.io/v1/boards/writeco/jobs"): api_value})


def test_a_board_token_that_404s_is_a_stale_link_and_not_proposed():
    row = ad.check(_gh(fa.FetchError("GET ...: HTTP 404", status=404)), WRITECO, js_root=None)
    assert (row["state"], row["vendor"], row["readable"]) == ("stale link", "greenhouse", False)
    assert ad.proposals(row, "2026-10-01") == []


def test_a_board_token_that_answers_is_proposed():
    row = ad.check(_gh({"jobs": []}), WRITECO, js_root=None)
    assert row["state"] == "detected" and ad.proposals(row, "2026-10-01")


def test_an_ashby_embed_without_its_org_in_the_page_is_named_with_no_token():
    page = '<div class="page"><div id="ashby_embed"></div></div>'
    assert ad.detect("https://writeco.example/company/careers/jobs", page)[:2] == ("ashby", None)
