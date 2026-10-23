"""discover_companies.py: companies that posted in jobs/ but are not in
targets/companies.yaml get a probe of the public Greenhouse, Lever and Ashby board APIs (the token from a job URL
first, else the slug and its unhyphenated form) and a count of AI-title roles; the candidates go to
state/reports/new-companies-<date>.md for the Recruiter. Nothing is ever added to companies.yaml."""
from __future__ import annotations

import re
from datetime import date

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW, FakeHttp

import discover_companies as dc  # noqa: E402
import fetch_ats as fa  # noqa: E402

GH, LV, AB = dc.GREENHOUSE, dc.LEVER, dc.ASHBY
TF = fa.TitleFilter(include=[re.compile(r"\b(ai|ml|machine learning)\b")], exclude=[])
NOT_FOUND = fa.FetchError("404", status=404)


def _job(root, company, slug, url):
    rec = fa.make_record({"name": company, "slug": slug}, title="AI Engineer", source="manual", source_url=url,
                         apply_url=None, ats_vendor="unknown", posted_at=None, location="SF", remote="onsite",
                         comp=(None, None, None), employment_type="full-time", jd_text="x", now=NOW)
    fa.save_job(root, rec)


def _world(root):
    (root / "targets").mkdir(parents=True)
    (root / "targets" / "companies.yaml").write_text("companies:\n  - {name: Known, slug: known, tier: good}\n")
    _job(root, "Acme", "acme", "https://jobs.ashbyhq.com/acme/d9bcb6a2")
    _job(root, "Mystery Co", "mystery-co", "https://www.linkedin.com/jobs/view/1/")
    _job(root, "Nowhere", "nowhere", "https://www.linkedin.com/jobs/view/2/")
    _job(root, "Known", "known", "https://jobs.ashbyhq.com/known/1")
    return root


def _routes():
    r = {("GET", AB.format(token="acme")): {"jobs": [{"title": "AI Engineer"}, {"title": "Account Executive"},
                                                      {"title": "ML Platform Engineer", "isListed": True}]},
         ("GET", GH.format(token="mysteryco")): {"jobs": [{"title": "Machine Learning Engineer"}]}}
    for slug in ("mystery-co", "nowhere"):
        for url in (GH, LV, AB):
            r.setdefault(("GET", url.format(token=slug)), NOT_FOUND)
    for url in (LV, AB):
        r[("GET", url.format(token="mysteryco"))] = NOT_FOUND
    return r


def test_the_job_url_token_comes_first_and_roles_are_counted(root):
    http = FakeHttp(_routes())
    rows = {r["slug"]: r for r in dc.discover(_world(root), ["acme"], http, TF)}
    n = rows["acme"]
    assert (n["vendor"], n["token"], n["roles"], n["ai_roles"], n["found_by"]) == ("ashby", "acme", 3, 2, "job URL")
    assert len(http.calls) == 1                                            # no guessing when a URL names the board


def test_name_probes_try_the_slug_then_its_unhyphenated_form(root):
    rows = {r["slug"]: r for r in dc.discover(_world(root), ["mystery-co", "nowhere"], FakeHttp(_routes()), TF)}
    m = rows["mystery-co"]
    assert (m["vendor"], m["token"], m["ai_roles"], m["found_by"]) == ("greenhouse", "mysteryco", 1, "name probe")
    assert rows["nowhere"]["vendor"] is None and "no public board" in rows["nowhere"]["note"]


def test_a_company_already_on_the_watchlist_is_skipped(root):
    assert dc.discover(_world(root), ["known"], FakeHttp({}), TF) == []


def test_the_report_lists_candidates_and_never_touches_companies_yaml(root, capsys):
    _world(root)
    before = (root / "targets" / "companies.yaml").read_bytes()
    argv = ["--root", str(root), "--today", "2026-10-01", "--slugs", "acme,mystery-co,nowhere"]
    assert dc.main(argv + ["--dry-run"], http=FakeHttp(_routes()), title_filter=TF) == 0
    assert not (root / "state" / "reports").exists()
    assert dc.main(argv, http=FakeHttp(_routes()), title_filter=TF) == 0
    text = (root / "state" / "reports" / "new-companies-2026-10-01.md").read_text()
    assert "| acme | Acme |" in text and "ashby `acme`" in text and "never added automatically" in text
    assert "name probe: verify it is this company" in text                 # a shared name can be another company
    assert text.index("| acme |") < text.index("| mystery-co |") < text.index("| nowhere |")   # most AI roles first
    assert (root / "targets" / "companies.yaml").read_bytes() == before and "\u2014" not in text
