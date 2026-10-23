"""jd_capture.py: terms-safe JD capture for open jobs at or above the hunt cut ."""
from __future__ import annotations

import json
from datetime import date

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import ASHBY, GH, LEVER, NOW, FakeHttp

import fetch_ats as fa
import jd_capture as jc_
import js_common as jc

TODAY = date(2026, 9, 30)
WD_BASE = "https://nimbus.wd5.myworkdayjobs.com/wday/cxs/nimbus/NimbusExternalSite"
LONG = "We build LLM agents and evaluation harnesses in Python. " * 8          # a real JD is longer than 200 chars


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(jc_, "_sleep", lambda s: None)
    monkeypatch.setattr(fa, "_sitemap_sleep", lambda s: None)


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "js"
    for d in ("jobs", "targets", "state"):
        (root / d).mkdir(parents=True)
    (root / "targets" / "companies.yaml").write_text(
        "companies:\n"
        "  - {name: Northwind, slug: northwind, tier: dream, ats_vendor: ashby, board_token: northwind}\n"
        "  - {name: Veridian Corp, slug: veridian, tier: dream, ats_vendor: custom, board_token: null}\n")
    (root / "targets" / "platforms.yaml").write_text(
        "platforms:\n  - {id: ziprecruiter, name: ZipRecruiter, url: 'https://www.ziprecruiter.com'}\n")
    return root


def _job(root, jid, *, company="Northwind", slug="northwind", title="Software Engineer, Data Infrastructure",
         url=None, apply=None, score=80, source="linkedin-alert", jd="", status="discovered", location=None):
    rec = fa.make_record({"name": company, "slug": slug}, title=title, source=source,
                         source_url=url or f"https://www.linkedin.com/jobs/view/{jid}", apply_url=apply,
                         ats_vendor="unknown", posted_at=None, location=location, remote="unknown",
                         comp=(None, None, None), employment_type=None, jd_text=jd, now=NOW)
    rec.update({"id": jid, "hunt_score": score, "status": status, "tags": [] if jd else ["jd-missing"]})
    if not jd:
        rec["jd_hash"] = None
    fa.save_job(root, rec)
    return rec


def _load(root, jid):
    rec, body = jc.fm.load(root / "jobs" / f"{jid}.md")
    return rec, body


def _ashby(jobs):
    return {"jobs": [{"title": t, "jobUrl": u, "applyUrl": u, "descriptionPlain": d, "location": "San Francisco",
                      "isListed": True} for t, u, d in jobs]}


def test_only_open_jobs_without_a_jd_at_or_above_the_cut_are_wanted(world):
    _job(world, "a", score=80)
    _job(world, "b", score=90)
    _job(world, "low", score=60)
    _job(world, "has-jd", jd=LONG)
    _job(world, "closed", status="closed")
    _job(world, "skipped", status="skipped")
    assert [r["id"] for r in jc_.wanted(world, cut=72)] == ["b", "a"]                 # highest score first


def test_a_jd_already_held_by_a_twin_record_is_copied_without_the_network(world):
    _job(world, "alert", title="Software Engineer - Data Infrastructure")
    _job(world, "board", title="Software Engineer, Data Infrastructure", source="ashby",
         url="https://jobs.ashbyhq.com/northwind/1", jd=LONG)
    out = jc_.capture(world, today=TODAY, http=FakeHttp({}))
    assert [(c["id"], c["channel"]) for c in out["captured"]] == [("alert", "local")]
    rec, body = _load(world, "alert")
    assert "LLM agents" in fa.jd_text_from_body(body) and "jd-missing" not in (rec.get("tags") or [])
    assert rec["jd_hash"] and "jobs/board.md" in body                        # the note names where it came from


def test_the_company_board_is_read_once_and_matched_by_normalized_title(world):
    _job(world, "x1", title="Research Engineer / Research Scientist -Planning Systems, Proactivity")
    _job(world, "x2", title="Software Engineer, AI Safety")
    http = FakeHttp({("GET", ASHBY.format("northwind")): _ashby([
        ("Research Engineer / Research Scientist - Planning Systems, Proactivity", "https://jobs.ashbyhq.com/northwind/9", LONG),
        ("Software Engineer, AI Safety", "https://jobs.ashbyhq.com/northwind/8", LONG)])})
    out = jc_.capture(world, today=TODAY, http=http)
    assert sorted(c["id"] for c in out["captured"]) == ["x1", "x2"]
    assert {c["channel"] for c in out["captured"]} == {"board"}
    assert len([c for c in http.calls if c[1] == ASHBY.format("northwind")]) == 1
    _, body = _load(world, "x1")
    assert "https://jobs.ashbyhq.com/northwind/9" in body and "<untrusted_jd>" in body


def test_a_title_missing_from_a_board_read_in_full_is_likely_closed_not_a_read(world):
    _job(world, "gone", title="Staff Wizard")
    http = FakeHttp({("GET", ASHBY.format("northwind")): _ashby([("Other", "https://jobs.ashbyhq.com/northwind/1", LONG)])})
    out = jc_.capture(world, today=TODAY, http=http)
    assert out["captured"] == [] and out["read_list"] == []
    assert [(r["id"], r["why"]) for r in out["likely_closed"]] == [
        ("gone", "not on the northwind ashby board, read in full (1 roles): likely closed")]
    assert out["counts"]["likely_closed"] == 1


def test_a_title_missing_from_a_sitemap_board_is_still_a_read(world):
    """A sitemap lists URLs, not titles (its slugs are matched loosely), so absence there proves nothing."""
    (world / "targets" / "companies.yaml").write_text(
        "companies:\n  - {name: Orchard, slug: orchard, tier: dream, ats_vendor: sitemap, board_token: jobs.orchard.example.test/s.xml, "
        "job_url_pattern: /details/}\n")
    _job(world, "a1", company="Orchard", slug="orchard", title="ML Engineer")
    http = FakeHttp({("GET", "https://jobs.orchard.example.test/robots.txt"): (404, ""),
                     ("GET", "https://jobs.orchard.example.test/s.xml"): ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>'
                                                                 'https://jobs.orchard.example.test/en-us/details/200612345/other-role'
                                                                 '</loc></url></urlset>')})
    out = jc_.capture(world, today=TODAY, http=http)
    assert [r["id"] for r in out["read_list"]] == ["a1"] and out["likely_closed"] == []


def test_an_ats_apply_url_leads_to_that_board_even_for_a_company_not_on_the_watchlist(world):
    _job(world, "lv", company="Acme", slug="acme", title="AI Engineer", url="https://acme.example/careers",
         apply="https://jobs.lever.co/acmeco/abc-123")
    http = FakeHttp({("GET", LEVER.format("acmeco")): [
        {"text": "AI Engineer", "hostedUrl": "https://jobs.lever.co/acmeco/abc-123", "description": LONG,
         "categories": {}}]})
    out = jc_.capture(world, today=TODAY, http=http)
    assert [(c["id"], c["channel"]) for c in out["captured"]] == [("lv", "ats-url")]


def test_a_guessed_greenhouse_board_counts_only_when_its_name_matches_the_company(world):
    _job(world, "g1", company="Sim", slug="sim", title="Software Engineer, Product")
    _job(world, "g2", company="Strawpile", slug="strawpile", title="Software Engineer")
    board = {"jobs": [{"title": "Software Engineer, Product", "absolute_url": "https://job-boards.greenhouse.io/sim/jobs/1",
                       "content": LONG}]}
    other = {"jobs": [{"title": "Software Engineer", "absolute_url": "https://job-boards.greenhouse.io/strawpile/jobs/2",
                       "content": LONG}]}
    routes = {("GET", "https://boards-api.greenhouse.io/v1/boards/sim"): {"name": "Sim"},
              ("GET", GH.format("sim")): board,
              ("GET", "https://boards-api.greenhouse.io/v1/boards/strawpile"): {"name": "Strawpile Oceanics Ltd"}}
    for slug in ("strawpile",):
        routes[("GET", ASHBY.format(slug))] = fa.FetchError("HTTP 404")
        routes[("GET", LEVER.format(slug))] = fa.FetchError("HTTP 404")
    routes[("GET", GH.format("strawpile"))] = other
    out = jc_.capture(world, today=TODAY, http=FakeHttp(routes))
    assert [(c["id"], c["channel"]) for c in out["captured"]] == [("g1", "ats-guess")]
    assert [r["id"] for r in out["read_list"]] == ["g2"]


def test_a_company_page_is_read_through_robots_and_its_jobposting(world):
    _job(world, "p1", company="Quill", slug="quill", title="Software Engineer", url="https://quill.example.test/careers/se",
         source="manual")
    _job(world, "p2", company="Quill", slug="quill", title="ML Engineer", url="https://quill.example.test/private/ml",
         source="manual")
    page = ('<script type="application/ld+json">' + json.dumps(
        {"@type": "JobPosting", "title": "Software Engineer", "description": f"<p>{LONG}</p>"}) + "</script>")
    routes = {("GET", "https://quill.example.test/robots.txt"): "User-agent: *\nDisallow: /private\nCrawl-delay: 3",
              ("GET", "https://quill.example.test/careers/se"): page}
    for vendor in (ASHBY, LEVER):
        routes[("GET", vendor.format("quill"))] = fa.FetchError("HTTP 404")
    routes[("GET", "https://boards-api.greenhouse.io/v1/boards/quill")] = fa.FetchError("HTTP 404")
    http = FakeHttp(routes)
    out = jc_.capture(world, today=TODAY, http=http)
    assert [(c["id"], c["channel"]) for c in out["captured"]] == [("p1", "page")]
    assert ("GET", "https://quill.example.test/private/ml", jc_.fa.HTML_ACCEPT) not in http.calls   # disallowed: never fetched
    assert [(r["id"], r["why"]) for r in out["read_list"]] == [("p2", "quill.example.test/robots.txt disallows this path")]


def test_platform_pages_are_never_fetched_and_the_list_keeps_twenty_a_day(world):
    for i in range(25):
        _job(world, f"z{i:02d}", company="Zed", slug="zed", title=f"AI Engineer {i}", score=72 + (i % 5),
             url=f"https://www.ziprecruiter.com/c/Zed/Job/{i}")
    routes = {("GET", "https://boards-api.greenhouse.io/v1/boards/zed"): fa.FetchError("HTTP 404"),
              ("GET", ASHBY.format("zed")): fa.FetchError("HTTP 404"),
              ("GET", LEVER.format("zed")): fa.FetchError("HTTP 404")}
    http = FakeHttp(routes)
    out = jc_.capture(world, today=TODAY, http=http)
    assert not [c for c in http.calls if "ziprecruiter" in c[1]]
    assert len(out["read_list"]) == jc_.READS_PER_DAY == 20
    assert out["counts"]["read_later"] == 5
    scores = [r["hunt_score"] for r in out["read_list"]]
    assert scores == sorted(scores, reverse=True)


def test_board_guesses_are_tried_once_per_company(world):
    _job(world, "z1", company="Zed", slug="zed", title="AI Engineer A")
    _job(world, "z2", company="Zed", slug="zed", title="AI Engineer B")
    routes = {("GET", "https://boards-api.greenhouse.io/v1/boards/zed"): fa.FetchError("HTTP 404"),
              ("GET", ASHBY.format("zed")): fa.FetchError("HTTP 404"),
              ("GET", LEVER.format("zed")): fa.FetchError("HTTP 404")}
    http = FakeHttp(routes)
    jc_.capture(world, today=TODAY, http=http)
    assert len(http.calls) == 3


def test_dry_run_writes_nothing(world):
    _job(world, "x1", title="Software Engineer, AI Safety")
    before = (world / "jobs" / "x1.md").read_bytes()
    http = FakeHttp({("GET", ASHBY.format("northwind")): _ashby([("Software Engineer, AI Safety", "u", LONG)])})
    out = jc_.capture(world, today=TODAY, http=http, dry_run=True)
    assert [c["id"] for c in out["captured"]] == ["x1"]
    assert (world / "jobs" / "x1.md").read_bytes() == before
    assert not (world / "state" / "jd").exists()


def test_a_short_description_is_not_taken_for_a_jd(world):
    _job(world, "x1", title="Software Engineer, AI Safety")
    http = FakeHttp({("GET", ASHBY.format("northwind")): _ashby([("Software Engineer, AI Safety", "u", "Apply now.")])})
    out = jc_.capture(world, today=TODAY, http=http)
    assert out["captured"] == [] and out["read_list"][0]["why"] == "the board's description is too short"


def test_the_firecrawl_path_is_used_only_when_asked_and_reads_the_snapshot(world):
    _job(world, "g1", company="Veridian Corp", slug="veridian", title="Software Engineer III, AI/ML, Video")
    calls = []

    def snap(url, out):
        calls.append(url)
        if "results?q=" in url:
            return 0, ("[Software Engineer III, AI/ML, Search](https://careers.veridian.example.test/"
                       "jobs/results/99-software-engineer-iii-ai-ml-search)\n"
                       "[Software Engineer III, AI/ML, Video](https://careers.veridian.example.test/"
                       "jobs/results/123-software-engineer-iii-ai-ml-video)\n[Other](https://careers.veridian.example.test/x)")
        return 0, "Minimum qualifications: " + LONG

    out = jc_.capture(world, today=TODAY, http=FakeHttp({}))
    assert out["captured"] == [] and calls == []
    assert out["read_list"][0]["why"] == "no public board or page; Firecrawl not asked (--firecrawl)"
    out = jc_.capture(world, today=TODAY, http=FakeHttp({}), snap=snap)
    assert [(c["id"], c["channel"]) for c in out["captured"]] == [("g1", "firecrawl")]
    assert len(calls) == 2 and calls[1].endswith("123-software-engineer-iii-ai-ml-video")


def test_the_run_leaves_a_receipt_and_a_read_list_file(world):
    _job(world, "gone", company="Zed", slug="zed", title="Staff Wizard")
    http = FakeHttp({("GET", "https://boards-api.greenhouse.io/v1/boards/zed"): fa.FetchError("HTTP 404"),
                     ("GET", ASHBY.format("zed")): fa.FetchError("HTTP 404"),
                     ("GET", LEVER.format("zed")): fa.FetchError("HTTP 404")})
    jc_.capture(world, today=TODAY, http=http)
    receipt = json.loads((world / "state" / "jd" / "capture-2026-09-30.json").read_text())
    assert receipt["counts"]["read_list"] == 1
    md = (world / "state" / "jd" / "read-list-2026-09-30.md").read_text()
    assert "https://www.linkedin.com/jobs/view/gone" in md and "jd_capture.py record --job gone" in md
    assert "\u2014" not in md


def test_a_hand_read_is_recorded_and_the_daily_cap_holds(world, tmp_path):
    _job(world, "h1", title="Staff Wizard")
    jd = tmp_path / "jd.txt"
    jd.write_text(LONG)
    msg = jc_.record(world, job_id="h1", jd_file=jd, now=NOW)
    assert msg.startswith("JD added to h1")
    rec, body = _load(world, "h1")
    assert "LLM agents" in fa.jd_text_from_body(body) and "by hand in the owner's browser" in body
    with pytest.raises(jc_.Refused):
        jc_.record(world, job_id="h1", jd_file=jd, now=NOW)                        # it has a JD now
    for i in range(jc_.READS_PER_DAY - 1):
        _job(world, f"k{i}", title=f"Role {i}")
        jc_.record(world, job_id=f"k{i}", jd_file=jd, now=NOW)
    _job(world, "over", title="Role over")
    with pytest.raises(jc_.Refused, match="20 hand reads"):
        jc_.record(world, job_id="over", jd_file=jd, now=NOW)


def test_a_twin_at_another_company_is_not_the_same_role(world):
    _job(world, "alert", title="Software Engineer")
    _job(world, "other-co", company="Zed", slug="zed", title="Software Engineer", source="ashby",
         url="https://jobs.ashbyhq.com/zed/1", jd=LONG)
    http = FakeHttp({("GET", ASHBY.format("northwind")): _ashby([])})
    out = jc_.capture(world, today=TODAY, http=http)
    assert out["captured"] == []


def test_a_page_whose_posting_is_another_role_is_not_taken(world):
    _job(world, "p1", company="Quill", slug="quill", title="Software Engineer", url="https://quill.example.test/careers/se",
         source="manual")
    page = ('<script type="application/ld+json">' + json.dumps(
        {"@type": "JobPosting", "title": "Head of Sales", "description": f"<p>{LONG}</p>"}) + "</script>")
    routes = {("GET", "https://quill.example.test/robots.txt"): (404, ""), ("GET", "https://quill.example.test/careers/se"): page,
              ("GET", "https://boards-api.greenhouse.io/v1/boards/quill"): fa.FetchError("HTTP 404"),
              ("GET", ASHBY.format("quill")): fa.FetchError("HTTP 404"),
              ("GET", LEVER.format("quill")): fa.FetchError("HTTP 404")}
    out = jc_.capture(world, today=TODAY, http=FakeHttp(routes))
    assert out["captured"] == []
    assert out["read_list"][0]["why"] == "quill.example.test: no JobPosting for this title on the page"


def test_pages_on_one_host_wait_out_its_crawl_delay(world, monkeypatch):
    waits = []
    monkeypatch.setattr(jc_, "_sleep", waits.append)
    page = lambda t: ('<script type="application/ld+json">' + json.dumps(
        {"@type": "JobPosting", "title": t, "description": f"<p>{LONG}</p>"}) + "</script>")
    _job(world, "p1", company="Quill", slug="quill", title="Software Engineer", url="https://quill.example.test/c/1", source="manual")
    _job(world, "p2", company="Quill", slug="quill", title="ML Engineer", url="https://quill.example.test/c/2", source="manual")
    routes = {("GET", "https://quill.example.test/robots.txt"): "User-agent: *\nCrawl-delay: 7",
              ("GET", "https://quill.example.test/c/1"): page("Software Engineer"), ("GET", "https://quill.example.test/c/2"): page("ML Engineer"),
              ("GET", "https://boards-api.greenhouse.io/v1/boards/quill"): fa.FetchError("HTTP 404"),
              ("GET", ASHBY.format("quill")): fa.FetchError("HTTP 404"),
              ("GET", LEVER.format("quill")): fa.FetchError("HTTP 404")}
    out = jc_.capture(world, today=TODAY, http=FakeHttp(routes))
    assert len(out["captured"]) == 2 and waits == [7.0]


def test_a_workday_board_cut_off_by_the_page_cap_proves_nothing(world):
    (world / "targets" / "companies.yaml").write_text(
        "companies:\n  - {name: Nimbus, slug: nimbus, tier: dream, ats_vendor: workday, "
        "board_token: 'nimbus.wd5/NimbusExternalSite'}\n")
    _job(world, "n1", company="Nimbus", slug="nimbus", title="AI Engineer, Agents")
    page = {"total": 500, "jobPostings": [{"title": f"Role {i}", "externalPath": f"/job/x/Role-{i}_JR{i:06d}"}
                                          for i in range(20)]}
    http = FakeHttp({("POST", WD_BASE + "/jobs"): lambda payload: page})
    out = jc_.capture(world, today=TODAY, http=http)
    assert [r["id"] for r in out["read_list"]] == ["n1"] and out["likely_closed"] == []
    assert out["read_list"][0]["why"] == "not on the nimbus workday board"


def test_a_workday_board_is_searched_by_the_jobs_own_title(world):
    (world / "targets" / "companies.yaml").write_text(
        "companies:\n  - {name: Nimbus, slug: nimbus, tier: dream, ats_vendor: workday, "
        "board_token: 'nimbus.wd5/NimbusExternalSite'}\n")
    titles = {"AI Engineer, Agents": "/job/US-CA-Santa-Clara/AI-Engineer--Agents_JR123456",
              "ML Engineer, Evals": "/job/US-CA-Santa-Clara/ML-Engineer--Evals_JR123457"}
    _job(world, "n1", company="Nimbus", slug="nimbus", title="AI Engineer, Agents", score=80)
    _job(world, "n2", company="Nimbus", slug="nimbus", title="ML Engineer, Evals", score=79)

    def jobs(payload):
        t = payload.get("searchText")
        return {"total": 1 if t in titles else 0,
                "jobPostings": [{"title": t, "externalPath": titles[t]}] if t in titles else []}
    routes = {("POST", WD_BASE + "/jobs"): jobs}
    for t, path in titles.items():
        routes[("GET", WD_BASE + path)] = {"jobPostingInfo": {"title": t, "jobDescription": f"<p>{LONG}</p>"}}
    out = jc_.capture(world, today=TODAY, http=FakeHttp(routes))
    assert [(c["id"], c["channel"]) for c in out["captured"]] == [("n1", "board"), ("n2", "board")]


def test_a_job_an_application_names_takes_that_applications_jd_whatever_its_score(world):
    _job(world, "tf", company="Rivet", slug="rivet", title="Founding Engineer", score=42,
         url="https://jobs.rivet.example.test/founding-engineer/69121", source="manual")
    app = world / "applications" / "rivet-founding-engineer-202609"
    app.mkdir(parents=True)
    (app / "application.md").write_text("---\ntype: application\njob: '[[tf]]'\n---\n")
    (app / "jd.md").write_text("# Snapshot, read only, 2026-09-29: https://jobs.rivet.example.test/x\n# Quoting note: none\n\n" + LONG)
    out = jc_.capture(world, today=TODAY, http=FakeHttp({}))
    assert [(c["id"], c["channel"]) for c in out["captured"]] == [("tf", "application")]
    _, body = _load(world, "tf")
    jd = fa.jd_text_from_body(body)
    assert jd.startswith("We build LLM agents") and "Snapshot, read only" not in jd
    assert "applications/rivet-founding-engineer-202609/jd.md" in body


@pytest.mark.parametrize("inner, taken", [("", False), (LONG, True)])
def test_an_application_jd_in_the_record_format_gives_only_its_jd_section(world, inner, taken):
    """An application's jd.md in the record format is frontmatter plus an <untrusted_jd> section, which may be empty:
    the frontmatter is never the JD."""
    _job(world, "k", company="Quill", slug="quill", title="Software Engineer", score=40, source="manual",
         url="https://quill.example.test/careers/software-engineer")
    app = world / "applications" / "quill-software-engineer-202609"
    app.mkdir(parents=True)
    (app / "application.md").write_text("---\ntype: application\njob: '[[k]]'\n---\n")
    (app / "jd.md").write_text("---\njob: '[[k]]'\ncompany: Quill\ntitle: Software Engineer\nsource_url: "
                               "https://quill.example.test/careers/software-engineer\nsnapshot_at: '2026-09-29T08:30:07Z'\n---\n"
                               f"# JD snapshot: Software Engineer at Quill\n\n<untrusted_jd>\n{inner}\n</untrusted_jd>\n")
    routes = {("GET", "https://quill.example.test/robots.txt"): (404, ""),
              ("GET", "https://quill.example.test/careers/software-engineer"): (200, "<html>no posting</html>"),
              ("GET", "https://boards-api.greenhouse.io/v1/boards/quill"): fa.FetchError("HTTP 404"),
              ("GET", ASHBY.format("quill")): fa.FetchError("HTTP 404"),
              ("GET", LEVER.format("quill")): fa.FetchError("HTTP 404")}
    out = jc_.capture(world, today=TODAY, http=FakeHttp(routes))
    _, body = _load(world, "k")
    if taken:
        assert [c["channel"] for c in out["captured"]] == ["application"]
        assert fa.jd_text_from_body(body).strip() == LONG.strip()
    else:
        assert out["captured"] == [] and fa.jd_text_from_body(body).strip() == ""


def test_application_jd_text_drops_frontmatter_and_our_header_lines():
    fm_plain = "---\njob: '[[k]]'\ncompany: Quill\n---\n# Snapshot, read only\nThe role: build agents."
    assert jc_.application_jd_text(fm_plain) == "The role: build agents."
    assert jc_.application_jd_text("---\na: 1\n---\n<untrusted_jd>\nX\n</untrusted_jd>\n") == "X"


def test_careers_search_results_in_their_real_form_are_matched_and_a_saved_search_is_reused(world):
    """A careers search page may write 'Learn more about <title>' links with a ?q= query; those still match."""
    _job(world, "g4", company="Veridian Corp", slug="veridian", title="Forward Deployed Engineer IV, Applied AI, Veridian Cloud")
    url = ("https://careers.veridian.example.test/jobs/results/125981803457454790-forward-deployed-engineer-"
           "iv-applied-ai-veridian-cloud?q=%22Forward%20Deployed%20Engineer%22")
    snaps = world / "state" / "jd" / "snapshots"
    snaps.mkdir(parents=True)
    (snaps / "g4-2026-09-30-search.md").write_text(
        "# Snapshot\n\n<untrusted_web>\n[Learn more about Forward Deployed Engineer IV, Applied AI, Veridian Cloud]"
        f"({url})\n</untrusted_web>\n")
    calls = []

    def snap(u, out):
        calls.append(u)
        return 0, "Minimum qualifications: " + LONG

    out = jc_.capture(world, today=TODAY, http=FakeHttp({}), snap=snap)
    assert [(c["id"], c["channel"]) for c in out["captured"]] == [("g4", "firecrawl")]
    assert calls == [url.split("?")[0]]                              # the saved search is read, not bought again
