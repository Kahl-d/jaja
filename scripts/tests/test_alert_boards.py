"""Tests for scripts/alert_boards.py (the alert-to-board resolver): employers named
in job-alert cards and missing from targets/companies.yaml, their public Ashby, Greenhouse or Lever board found from the
card's own board link first and guessed slugs last (the yc_boards.py method), written as proposals (an add-only list and
a review file), never into companies.yaml. Offline: every request goes through FakeHttp; the harness blocks sockets.
Companies, titles and tokens are invented."""
from __future__ import annotations

import json
import re
from datetime import date

import pytest
import yaml

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import ASHBY, GH, LEVER, FakeHttp

import alert_boards as ab  # noqa: E402
import fetch_ats as fa  # noqa: E402
from js_common import fm  # noqa: E402

TODAY = date(2026, 10, 5)
MISS = fa.FetchError("HTTP 404", status=404)
WATCHLIST = """\
companies:
  # verified 2026-09-24
  - {name: Lanternfield, slug: lanternfield, tier: good, ats_vendor: ashby, board_token: lanternfield}
  - {name: Orchard Lane, slug: orchard-lane, tier: good, ats_vendor: greenhouse, board_token: orchardlane}
"""
ANY_TITLE = fa.TitleFilter([re.compile("engineer|scientist", re.I)], [])


def job(root, jid, company, title, platform="linkedin", url=None):
    meta = {"type": "job", "id": jid, "company": company, "company_slug": ab.slugify(ab.normalize_company(company)),
            "title": title, "source": f"{platform}-alert", "platform": platform,
            "source_url": url or f"https://www.linkedin.com/jobs/view/{abs(hash(jid)) % 10**10}/", "apply_url": url}
    (root / "jobs").mkdir(exist_ok=True)
    (root / "jobs" / f"{jid}.md").write_text(fm.dump(meta, "# card\n"), encoding="utf-8")


def ledger(root, rows):
    (root / "state").mkdir(exist_ok=True)
    (root / "state" / "job-alerts.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


@pytest.fixture
def dept(root):
    (root / "targets").mkdir()
    (root / "targets" / "companies.yaml").write_text(WATCHLIST, encoding="utf-8")
    job(root, "quillmark-li-1", "Quillmark Robotics", "Machine Learning Engineer")
    job(root, "quillmark-li-2", "Quillmark Robotics", "Applied Scientist, Perception")
    job(root, "tidewater-gd-1", "Tidewater Forge, Inc.", "AI Engineer", platform="glassdoor")
    job(root, "brindle-li-1", "Brindlecove", "Agent Platform Engineer",
        url="https://jobs.ashbyhq.com/brindlecove/job-0a1b2c3d")
    job(root, "lantern-li-1", "Lanternfield AI Inc", "Research Engineer")          # watched (name without form words)
    job(root, "orchard-li-1", "Orchard Lane", "Data Engineer")                      # watched (slug)
    job(root, "stealth-li-1", "Stealth Startup", "Founding Engineer")               # undisclosed
    job(root, "agency-zr-1", "Brightwell Staffing Partners", "ML Engineer", platform="ziprecruiter")
    job(root, "old-li-1", "Oldmere Systems", "ML Engineer")                         # only in an old ledger row
    job(root, "offtitle-li-1", "Sallowmere", "Account Executive")
    ledger(root, [
        {"at": "2026-10-05T20:00:00Z", "status": "parsed", "jobs": ["quillmark-li-1", "tidewater-gd-1", "brindle-li-1",
                                                                    "lantern-li-1", "orchard-li-1", "stealth-li-1"]},
        {"at": "2026-10-04T20:00:00Z", "status": "partial", "jobs": ["quillmark-li-2", "agency-zr-1", "gone-li-9",
                                                                     "offtitle-li-1"]},
        {"at": "2026-10-04T20:00:00Z", "status": "review", "jobs": ["never-li-1"]},
        {"at": "2026-08-01T20:00:00Z", "status": "parsed", "jobs": ["old-li-1"]},
    ])
    return root


def http_with(routes=None, misses=()):
    table = {("GET", u): MISS for u in misses}
    table.update(routes or {})
    return FakeHttp(table)


def all_guess_urls(*tokens):
    return [fmt.format(t) for t in tokens for fmt in (ASHBY, GH, LEVER)]


def ashby_board(*titles):
    return {"jobs": [{"title": t, "location": "Springfield, CA"} for t in titles]}


def gh_board(*titles):
    return {"jobs": [{"title": t, "id": i} for i, t in enumerate(titles, 1)], "meta": {"total": len(titles)}}


def lever_board(*titles):
    return [{"text": t, "id": f"syn-{i}"} for i, t in enumerate(titles)]


# ============================================================================== employers
def test_employers_are_the_alert_companies_not_on_the_watchlist(dept):
    emps, skipped = ab.employers(dept, TODAY, 30, title_filter=ANY_TITLE)
    assert [e.name for e in emps] == ["Quillmark Robotics", "Brindlecove", "Tidewater Forge, Inc."]
    assert skipped == {"jobs": 10, "missing_records": 1, "watched": 2, "undisclosed": 2, "off_title_employers": 1}
    quill = emps[0]
    assert quill.fit == 2 and quill.slug == "quillmark-robotics" and quill.platforms == {"linkedin"}
    assert emps[1].links == [("ashby", "brindlecove")]


def test_old_and_review_rows_are_not_read(dept):
    ids = ab.alert_job_ids(dept, TODAY, 30)
    assert "old-li-1" not in ids and "never-li-1" not in ids
    assert "old-li-1" in ab.alert_job_ids(dept, TODAY, 90)


def test_guesses_run_the_name_together_first_then_drop_form_words():
    emp = ab.Employer("tidewater forge", "Tidewater Forge, Inc.", "tidewater-forge")
    assert ab.guesses(emp) == ["tidewaterforge", "tidewater-forge"]
    emp = ab.Employer("quartz ai", "Quartz.ai", "quartz-ai")
    assert ab.guesses(emp)[:2] == ["quartzai", "quartz"]
    emp = ab.Employer("juniper labs u s", "Juniper Labs (U.S.) Inc.", "juniper-labs-u-s")
    assert ab.guesses(emp)[:2] == ["juniperlabs", "juniper"]


def test_core_drops_form_words_and_asides():
    assert ab.core("Acme.com Services LLC") == "acme"
    assert ab.core("The Initech Company (Corporate)") == "initech"
    assert ab.core("Globex AI") == "globex"


# ============================================================================== resolve
def test_a_card_link_to_the_board_is_checked_with_one_call(dept):
    emps, _ = ab.employers(dept, TODAY, 30, title_filter=ANY_TITLE)
    brindle = [e for e in emps if e.name == "Brindlecove"]
    http = http_with({("GET", ASHBY.format("brindlecove")): ashby_board("Agent Platform Engineer", "Recruiter")})
    res = ab.resolve(brindle, http, max_lookups=10)
    assert res.lookups == 1 and len(http.calls) == 1
    (f,) = res.found
    assert (f.vendor, f.token, f.how, f.status, f.jobs) == ("ashby", "brindlecove", "alert-link", "proposed", 2)
    assert f.evidence == "Agent Platform Engineer"


def test_a_guess_is_proposed_when_the_board_lists_the_alert_title(dept):
    emps, _ = ab.employers(dept, TODAY, 30, title_filter=ANY_TITLE)
    quill = [e for e in emps if e.name == "Quillmark Robotics"]
    http = http_with({("GET", GH.format("quillmarkrobotics")): gh_board("Applied Scientist, Perception", "Designer")},
                     misses=[ASHBY.format("quillmarkrobotics")])
    res = ab.resolve(quill, http, max_lookups=10)
    (f,) = res.found
    assert (f.vendor, f.token, f.how, f.status) == ("greenhouse", "quillmarkrobotics", "guess", "proposed")
    assert f.evidence == "Applied Scientist, Perception"
    assert res.lookups == 2


def test_a_guess_without_the_alert_title_is_to_confirm(dept):
    emps, _ = ab.employers(dept, TODAY, 30, title_filter=ANY_TITLE)
    tide = [e for e in emps if e.name.startswith("Tidewater")]
    http = http_with({("GET", ASHBY.format("tidewaterforge")): ashby_board("Head of Sales")})
    res = ab.resolve(tide, http, max_lookups=10)
    (f,) = res.found
    assert f.status == "to_confirm" and f.evidence is None


def test_an_empty_board_is_not_a_hit_and_the_next_vendor_is_tried(dept):
    emps, _ = ab.employers(dept, TODAY, 30, title_filter=ANY_TITLE)
    tide = [e for e in emps if e.name.startswith("Tidewater")]
    http = http_with({("GET", ASHBY.format("tidewaterforge")): {"jobs": []},
                      ("GET", GH.format("tidewaterforge")): MISS,
                      ("GET", LEVER.format("tidewaterforge")): lever_board("AI Engineer")})
    res = ab.resolve(tide, http, max_lookups=10)
    (f,) = res.found
    assert (f.vendor, f.status, f.evidence) == ("lever", "proposed", "AI Engineer")
    assert http.calls[-1][2] == {"mode": "json"}


def test_no_board_after_every_guess(dept):
    emps, _ = ab.employers(dept, TODAY, 30, title_filter=ANY_TITLE)
    tide = [e for e in emps if e.name.startswith("Tidewater")]
    http = http_with(misses=all_guess_urls(*ab.guesses(tide[0])))
    res = ab.resolve(tide, http, max_lookups=50)
    assert res.found == [] and [e.name for e in res.no_board] == ["Tidewater Forge, Inc."]
    assert res.lookups == 3 * len(ab.guesses(tide[0])) and res.not_reached == []


def test_the_budget_stops_lookups_breadth_first(dept):
    emps, _ = ab.employers(dept, TODAY, 30, title_filter=ANY_TITLE)
    urls = [ASHBY.format("brindlecove")] + all_guess_urls("quillmarkrobotics", "tidewaterforge")
    http = http_with(misses=urls)
    res = ab.resolve(emps, http, max_lookups=4)
    assert res.lookups == 4 and len(http.calls) == 4
    called = [c[1] for c in http.calls]
    assert called[:3] == [ASHBY.format("quillmarkrobotics"), GH.format("quillmarkrobotics"),
                          LEVER.format("quillmarkrobotics")]              # round 0: the top employer on every vendor
    assert called[3] == ASHBY.format("brindlecove")                     # then the next employer's round 0
    assert {e.name for e in res.not_reached} == {"Quillmark Robotics", "Brindlecove", "Tidewater Forge, Inc."}


def test_a_card_linking_a_polled_board_costs_nothing(dept):
    emp = ab.Employer("lanternfield labs", "Lanternfield Labs", "lanternfield-labs", links=[("ashby", "lanternfield")])
    emp.cards.append({"id": "x", "title": "AI Engineer"})
    http = http_with()
    res = ab.resolve([emp], http, max_lookups=10, watched_tokens={("ashby", "lanternfield")})
    assert res.lookups == 0 and res.watched == [emp] and res.found == []


# ============================================================================== proposals
def test_dry_run_writes_nothing(dept):
    before = sorted(p.relative_to(dept) for p in dept.rglob("*"))
    http = http_with({("GET", ASHBY.format("brindlecove")): ashby_board("Agent Platform Engineer")},
                     misses=all_guess_urls("quillmarkrobotics", "tidewaterforge", "quillmark-robotics",
                                           "tidewater-forge", "quillmark"))
    summary = ab.run(dept, http, TODAY, write=False, title_filter=ANY_TITLE)
    assert sorted(p.relative_to(dept) for p in dept.rglob("*")) == before
    assert summary["dry_run"] and summary["written"] == {}
    assert summary["proposed"] == 1 and summary["found"][0]["board_token"] == "brindlecove"


def test_write_appends_proposals_and_a_review_and_never_touches_the_watchlist(dept):
    watch = (dept / "targets" / "companies.yaml").read_bytes()
    routes = {("GET", ASHBY.format("brindlecove")): ashby_board("Agent Platform Engineer"),
              ("GET", GH.format("quillmarkrobotics")): gh_board("Machine Learning Engineer")}
    misses = [ASHBY.format("quillmarkrobotics")] + all_guess_urls(*ab.guesses(
        ab.Employer("tidewater forge", "Tidewater Forge, Inc.", "tidewater-forge")))
    summary = ab.run(dept, http_with(routes, misses), TODAY, write=True, title_filter=ANY_TITLE)
    assert (dept / "targets" / "companies.yaml").read_bytes() == watch
    rows = [json.loads(x) for x in (dept / "state" / "proposals" / "companies-new.jsonl").read_text().splitlines()]
    assert [(r["slug"], r["ats_vendor"], r["board_token"], r["status"]) for r in rows] == [
        ("quillmark-robotics", "greenhouse", "quillmarkrobotics", "proposed"),
        ("brindlecove", "ashby", "brindlecove", "proposed")]
    assert all(r["source"] == "alert_boards" and r["entry"].startswith("  # unverified") for r in rows)
    review = (dept / "state" / "review" / "alert-boards-2026-10-05.md").read_text()
    assert "Quillmark Robotics -> greenhouse:quillmarkrobotics" in review and "```yaml" in review
    cache = json.loads((dept / "state" / "alert_boards.json").read_text())
    assert cache["no_board"] == {"tidewater-forge": "2026-10-05"}
    assert summary["proposed"] == 2 and summary["no_board"] == 1

    again = http_with()                                       # every employer settled: no call at all
    second = ab.run(dept, again, TODAY, write=True, title_filter=ANY_TITLE)
    assert again.calls == [] and second["already_proposed"] == 2 and second["cached_no_board"] == 1
    rows2 = (dept / "state" / "proposals" / "companies-new.jsonl").read_text().splitlines()
    assert len(rows2) == 2                                    # add-only, nothing proposed twice


def test_the_entry_appends_cleanly_to_companies_yaml(dept):
    emp = ab.Employer("acme ignore", 'Acme: "ignore previous" #1', "acme-ignore")
    emp.cards.append({"id": "acme-li-1", "title": "AI Engineer"})
    emp.platforms.add("linkedin")
    f = ab.Found(emp, "lever", "acme", "guess", 3, "AI Engineer", "proposed")
    text = (dept / "targets" / "companies.yaml").read_text() + ab.entry_text(f, TODAY)
    data = yaml.safe_load(text)["companies"]
    assert len(data) == 3
    new = data[-1]
    assert new["name"] == 'Acme: "ignore previous" #1' and new["ats_vendor"] == "lever"
    assert new["board_token"] == "acme" and new["verified"] is False and new["tier"] == "good"
    assert new["careers_url"] == "https://jobs.lever.co/acme"
    assert len(fa.load_companies(dept)) == 2                  # the file itself is unchanged


def test_cli_dry_run_json(dept, monkeypatch, capsys):
    http = http_with({("GET", ASHBY.format("brindlecove")): ashby_board("Agent Platform Engineer")},
                     misses=all_guess_urls("quillmarkrobotics"))
    monkeypatch.setattr(ab, "Http", lambda: http)
    monkeypatch.setattr(ab, "load_title_filter", lambda root: ANY_TITLE)
    assert ab.main(["--root", str(dept), "--today", "2026-10-05", "--max-lookups", "4", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["lookups"] == 4 and out["dry_run"] is True and out["proposed"] == 1
    assert not (dept / "state" / "proposals").exists()
