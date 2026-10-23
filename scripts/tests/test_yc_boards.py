"""Tests for scripts/yc_boards.py: YC companies from the yc-oss hiring mirror, their public
Ashby, Greenhouse or Lever board found from real URLs on their own site first and guessed slugs last, written to
targets/companies.yaml as tier good, unverified. Offline: every request goes through FakeHttp."""
from __future__ import annotations

import json
import os
from datetime import date

import yaml

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import ASHBY, GH, LEVER, FakeHttp

import fetch_ats as fa  # noqa: E402
import yc_boards as yb  # noqa: E402

TODAY = date(2026, 10, 1)
MISS = fa.FetchError("HTTP 404")


def yc(name, slug, website, batch="Summer 2025", locations="San Francisco, CA, USA", text="AI agents for finance",
       **kw):
    rec = {"name": name, "slug": slug, "website": website, "batch": batch, "all_locations": locations,
           "regions": "['United States of America', 'America / Canada']", "one_liner": text,
           "long_description": "", "tags": "['Artificial Intelligence']", "isHiring": "True", "status": "Active",
           "url": f"https://www.ycombinator.com/companies/{slug}"}
    rec.update(kw)
    return rec


def misses(*urls):
    return {("GET", u): MISS for u in urls}


def guesses(*slugs):
    out = {}
    for s in slugs:
        out.update(misses(ASHBY.format(s), GH.format(s), LEVER.format(s)))
    return out


def site(website, pages=None, robots=(404, "")):
    routes = {("GET", website.rstrip("/") + "/robots.txt"): robots}
    for path in ("", "/careers", "/jobs"):
        routes[("GET", website.rstrip("/") + path)] = (pages or {}).get(path, (404, ""))
    return routes


# ---------------------------------------------------------------- candidates
def test_candidates_keep_recent_us_ai_hiring_companies_not_already_watched():
    mirror = [
        yc("Ledgerly", "ledgerly", "https://ledgerly.ai"),
        yc("Old Co", "old-co", "https://old.co", batch="Winter 2019"),
        yc("Euro AI", "euro-ai", "https://euro.ai", locations="Berlin, Germany", regions="['Europe']"),
        yc("Plumbly", "plumbly", "https://plumbly.example.test", text="Payroll for plumbers", tags="['Fintech']"),
        yc("Zeta", "zeta", "https://zeta.ai"),                                    # already in companies.yaml
        yc("Gone", "gone", "https://gone.ai", status="Inactive"),
    ]
    known = [{"name": "Zeta", "slug": "zeta", "ats_vendor": "ashby", "board_token": "zeta"}]
    assert [c["slug"] for c in yb.candidates(mirror, known)] == ["ledgerly"]


# ---------------------------------------------------------------- real URLs
def test_board_links_reads_ashby_greenhouse_and_lever_urls_from_a_page():
    html = """<a href="https://jobs.ashbyhq.com/Ledgerly/abc-123">Apply</a>
              <iframe src="https://job-boards.greenhouse.io/embed/job_board?for=ledgerlyinc"></iframe>
              <a href="https://boards.greenhouse.io/ledgerly2/jobs/4455">x</a>
              <a href='https://jobs.lever.co/ledgerly-lever/'>Lever</a>
              <a href="https://jobs.ashbyhq.com/api/non-user-graphql">no</a>"""
    assert yb.board_links(html) == [("ashby", "Ledgerly"), ("greenhouse", "ledgerlyinc"), ("greenhouse", "ledgerly2"),
                                    ("lever", "ledgerly-lever")]


def test_discover_takes_the_board_linked_from_the_company_careers_page_first():
    c = yc("Ledgerly", "ledgerly", "https://ledgerly.ai")
    http = FakeHttp({**site("https://ledgerly.ai", {"/careers": '<a href="https://jobs.ashbyhq.com/Ledgerly">Jobs</a>'}),
                     ("GET", ASHBY.format("Ledgerly")): {"jobs": [{"title": "AI Engineer"}]}})
    found = yb.discover(c, http)
    assert found == {"vendor": "ashby", "token": "Ledgerly", "how": "website", "jobs": 1,
                     "page": "https://ledgerly.ai/careers", "confirmed": True}
    assert not any(u == ASHBY.format("ledgerly") for _, u, _ in http.calls)      # no guess once a real URL answers


def test_discover_honours_robots_and_falls_back_to_guessed_slugs_last():
    c = yc("Ledger Ly", "ledger-ly", "https://ledgerly.ai")
    robots = (200, "User-agent: *\nDisallow: /\n")
    http = FakeHttp({("GET", "https://ledgerly.ai/robots.txt"): robots,
                     **guesses("ledger-ly"),
                     ("GET", ASHBY.format("ledgerly")): MISS,
                     ("GET", GH.format("ledgerly")): {"jobs": [{"title": "ML Engineer", "content": "Apply at ledgerly.ai"},
                                                               {"title": "FDE"}]}})
    found = yb.discover(c, http)
    assert found["how"] == "guess" and (found["vendor"], found["token"], found["jobs"]) == ("greenhouse", "ledgerly", 2)
    assert found["confirmed"] is True                       # the company's own domain appears on the board
    assert not any(u.startswith("https://ledgerly.ai/careers") for _, u, _ in http.calls)


def test_a_guessed_board_with_no_jobs_is_not_taken_and_nothing_found_is_none():
    c = yc("Quiet", "quiet", "https://quiet.ai")
    http = FakeHttp({**site("https://quiet.ai"), ("GET", ASHBY.format("quiet")): {"jobs": []},
                     **misses(GH.format("quiet"), LEVER.format("quiet"))})
    assert yb.discover(c, http) is None


def test_discover_never_requests_ycombinator_com():
    c = yc("Quiet", "quiet", "https://quiet.ai")
    http = FakeHttp({**site("https://quiet.ai"), **guesses("quiet")})
    yb.discover(c, http)
    assert not any("ycombinator.com" in u for _, u, _ in http.calls)


# ---------------------------------------------------------------- write
def _root(tmp_path, companies_text):
    (tmp_path / "targets").mkdir()
    (tmp_path / "targets" / "companies.yaml").write_text(companies_text)
    (tmp_path / "state").mkdir()
    return tmp_path


HEAD = """# targets/companies.yaml - watchlist
companies:
  # verified 2026-10-01
  - name: Zeta
    slug: zeta
    tier: dream
    ats_vendor: ashby
    board_token: "zeta"
"""


def test_run_writes_found_boards_as_tier_good_unverified_and_keeps_existing_text(tmp_path):
    root = _root(tmp_path, HEAD)
    mirror = [yc("Ledgerly", "ledgerly", "https://ledgerly.ai", text="AI agents for \"finance\" teams"),
              yc("Quiet", "quiet", "https://quiet.ai")]
    http = FakeHttp({**site("https://ledgerly.ai", {"": '<a href="https://jobs.lever.co/ledgerly">Careers</a>'}),
                     ("GET", LEVER.format("ledgerly")): [{"text": "AI Engineer"}],
                     **site("https://quiet.ai"), **guesses("quiet")})
    summary = yb.run(root, http, TODAY, mirror=mirror, write=True)
    assert summary == {"candidates": 2, "probed": 2, "found": 1, "no_board": 1, "cached": 0, "to_confirm": 0,
                       "written": 1}
    text = (root / "targets" / "companies.yaml").read_text()
    assert text.startswith(HEAD)
    assert "  # unverified: found by scripts/yc_boards.py 2026-10-01 (website" in text
    entries = {c["slug"]: c for c in yaml.safe_load(text)["companies"]}
    led = entries["ledgerly"]
    assert (led["tier"], led["ats_vendor"], led["board_token"], led["verified"]) == ("good", "lever", "ledgerly", False)
    assert led["sponsorship_stance"] == "unknown" and led["h1b_approvals_recent"] is None
    assert led["careers_url"] == "https://jobs.lever.co/ledgerly"
    assert "YC Summer 2025" in led["notes"] and "a person confirms it" in led["notes"]
    assert [c for c in fa.load_companies(root) if c["slug"] == "ledgerly"][0]["ats_vendor"] in fa.SUPPORTED_VENDORS


def test_run_is_a_dry_run_unless_write_and_caches_no_board_companies(tmp_path):
    root = _root(tmp_path, HEAD)
    mirror = [yc("Quiet", "quiet", "https://quiet.ai")]
    http = FakeHttp({**site("https://quiet.ai"), **guesses("quiet")})
    s1 = yb.run(root, http, TODAY, mirror=mirror, write=False)
    assert s1["no_board"] == 1 and s1["written"] == 0
    assert (root / "targets" / "companies.yaml").read_text() == HEAD
    assert not (root / "state" / "yc_boards.json").exists()                 # a dry run writes nothing at all
    yb.run(root, http, TODAY, mirror=mirror, write=True)
    cache = json.loads((root / "state" / "yc_boards.json").read_text())
    assert cache["no_board"]["quiet"] == "2026-10-01"
    calls = len(http.calls)
    s3 = yb.run(root, http, TODAY, mirror=mirror, write=True)
    assert s3["cached"] == 1 and s3["probed"] == 0 and len(http.calls) == calls   # re-probed only after NO_BOARD_DAYS


def test_run_skips_a_board_token_already_watched_under_another_slug(tmp_path):
    root = _root(tmp_path, HEAD)
    mirror = [yc("Zeta Studio", "zeta-studio", "https://zetastudio.ai")]
    http = FakeHttp({**site("https://zetastudio.ai", {"/careers": '<a href="https://jobs.ashbyhq.com/zeta">Jobs</a>'}),
                     ("GET", ASHBY.format("zeta")): {"jobs": [{"title": "FDE"}]}})
    s = yb.run(root, http, TODAY, mirror=mirror, write=True)
    assert s["found"] == 1 and s["written"] == 0
    assert (root / "targets" / "companies.yaml").read_text() == HEAD


def test_a_guessed_board_without_the_company_domain_waits_for_a_person_and_is_not_written(tmp_path):
    root = _root(tmp_path, HEAD)
    mirror = [yc("Newton", "newton", "https://joinnewton.com")]
    http = FakeHttp({**site("https://joinnewton.com"),
                     ("GET", ASHBY.format("newton")): MISS,
                     ("GET", GH.format("newton")): {"jobs": [{"title": "Engineer", "content": "Newton Inc, Boston"}]}})
    s = yb.run(root, http, TODAY, mirror=mirror, write=True)
    assert (s["found"], s["to_confirm"], s["written"]) == (0, 1, 0)
    assert (root / "targets" / "companies.yaml").read_text() == HEAD
    cache = json.loads((root / "state" / "yc_boards.json").read_text())
    assert cache["to_confirm"]["newton"] == {"vendor": "greenhouse", "token": "newton", "jobs": 1, "seen": "2026-10-01"}
    assert "newton" not in cache["no_board"]


def test_a_watchlist_changed_by_another_writer_during_the_run_is_re_read_and_its_new_tokens_respected(tmp_path):
    root = _root(tmp_path, HEAD)
    path = root / "targets" / "companies.yaml"
    scout = HEAD + """  # verified 2026-10-01 (HUNT)
  - name: Ledgerly Inc
    slug: ledgerly-inc
    tier: dream
    ats_vendor: lever
    board_token: "ledgerly"
"""

    def scout_writes(_):
        path.write_text(scout)                              # the Scout appends while we probe
        os.utime(path, (1, 1))
        return [{"text": "AI Engineer"}]

    mirror = [yc("Ledgerly", "ledgerly", "https://ledgerly.ai"), yc("Orrin", "orrin", "https://orrin.ai")]
    http = FakeHttp({**site("https://ledgerly.ai", {"": '<a href="https://jobs.lever.co/ledgerly">Careers</a>'}),
                     ("GET", LEVER.format("ledgerly")): scout_writes,
                     **site("https://orrin.ai", {"/careers": '<a href="https://jobs.ashbyhq.com/orrin">Jobs</a>'}),
                     ("GET", ASHBY.format("orrin")): {"jobs": [{"title": "FDE"}]}})
    s = yb.run(root, http, TODAY, mirror=mirror, write=True)
    text = path.read_text()
    assert text.startswith(scout) and s["written"] == 1     # the Scout's entry kept, its token not duplicated
    assert [c["slug"] for c in yaml.safe_load(text)["companies"]] == ["zeta", "ledgerly-inc", "orrin"]
