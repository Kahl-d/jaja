"""posts.py: hiring posts from X and LinkedIn through one parser into structured records, the poster's prospect, a job
ranked above listings, people-watch proposals, the weekly trend and the yield per credit. Synthetic posts only; nothing
touches the network, nothing is sent, followed or drafted."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest
import yaml

from comms_helpers import offline  # noqa: F401  (autouse: no network; puts scripts/ on sys.path)
import fetch_ats as fa
import hunt_score as hs
import js_common as jc
import posts
import prospects

TODAY = date(2026, 10, 5)
APPLY_URL = "https://boards.greenhouse.io/tinyai/jobs/4012345678"
JO_X = {"platform": "x", "id": "1975000000000000001", "url": "https://x.com/founder_jo/status/1975000000000000001",
        "author_handle": "founder_jo", "author_name": "Jo Park", "author_bio": "Co-founder & CTO @tinyai",
        "date": "2026-10-02", "links": [APPLY_URL], "why": "founding seats",
        "text": ("We're hiring a Founding AI Engineer at Tiny AI to build agents and evals in Python. San Francisco, in "
                 f"person. 2+ years of experience. We sponsor visas. DM me or apply: {APPLY_URL}")}
PAT_X = {"platform": "x", "id": "1975000000000000009", "url": "https://x.com/pat_ai/status/1975000000000000009",
         "author_handle": "pat_ai", "author_name": "Pat Kim", "author_bio": "Head of AI at Scribe", "date": "2026-10-03",
         "text": "Looking for an agent engineer to join the Applied Agents team. Evals, RAG, MCP. Remote. Email pat@scribe.example.test"}
NOISE_X = {"platform": "x", "id": "1975000000000000010", "url": "https://x.com/pat_ai/status/1975000000000000010",
           "author_handle": "pat_ai", "author_name": "Pat Kim", "date": "2026-10-03", "text": "Great talk on agents today"}
JO_LI = {"url": "https://www.linkedin.com/posts/jo-park_hiring-activity-7380000000000000001-AbCd", "author_name": "Jo Park",
         "author_url": "https://www.linkedin.com/in/jo-park-tiny/", "author_title": "Co-founder and CTO at Tiny AI",
         "date": "2026-10-02", "text": JO_X["text"]}
SAM_LI = {"url": "https://www.linkedin.com/posts/sam-lee_hiring-activity-7380000000000000002-XyZw", "author_name": "Sam Lee",
          "author_url": "https://www.linkedin.com/in/sam-lee-giant/", "author_title": "Engineering Manager at Giant Co",
          "date": "2026-10-03", "text": ("My team at Giant Co is looking for a Senior ML Engineer with 8+ years of experience in "
                                         "distributed systems and inference. Remote US. Comment below.")}
PLATFORMS = """platforms:
  - id: linkedin
    name: LinkedIn
    alert_senders: []
    yield: {fetched: 0, new: 0, queued: 0, applied: 0, interviews: 0}
    yield_as_of: '2026-10-03'
    notes: "kept"
  - id: x
    name: X (Twitter)   # a comment survives
    alert_senders: []
    yield: {fetched: 0, new: 0, queued: 0, applied: 0, interviews: 0}
    yield_as_of: '2026-10-03'
  - id: indeed
    name: Indeed
    alert_senders: []
    yield: {fetched: 3, new: 3, queued: 0, applied: 0, interviews: 0}
    yield_as_of: '2026-10-03'
"""


@pytest.fixture
def js(tmp_path):
    root = tmp_path / "os" / "area" / "group" / "projects" / "job-search"
    for d in ("targets", "prospects", "jobs", "state/posts", "state/x", "outbox", "touches", "profile"):
        (root / d).mkdir(parents=True)
    (root / "targets" / "platforms.yaml").write_text(PLATFORMS)
    (root / "targets" / "companies.yaml").write_text(
        "companies:\n  - {name: Giant Co, slug: giant-co, tier: good, segment: giant, ats_vendor: greenhouse, board_token: g}\n")
    (root / "targets" / "people-watch.yaml").write_text(
        "people:\n  - {name: Pat Kim, x_handle: pat_ai, company: scribe, role: hiring poster, why: test row, added: "
        "2026-10-01, source: test, status: watch}\n")
    (root / "profile" / "skills-taxonomy.yaml").write_text("skills:\n  - agents\n  - evals\n  - python\n")
    return root


def _write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _files(js):
    _write(js / "state" / "posts" / "x-2026-10-05.jsonl", [JO_X, PAT_X, NOISE_X])
    _write(js / "state" / "posts" / "linkedin-2026-10-05.jsonl", [JO_LI, SAM_LI])


# ----------------------------------------------------------------------------- the parser
def test_a_founder_post_becomes_a_structured_record():
    rec = posts.structure(posts.normalize_row(JO_X, "x"))
    assert (rec["role_title"], rec["company"], rec["seniority"]) == ("Founding AI Engineer", "Tiny AI", "founding")
    assert (rec["years"], rec["years_call"], rec["in_band"], rec["hiring"]) == (2, "in band", True, True)
    assert rec["location"] == ["San Francisco"] and rec["remote"] == "onsite"
    assert rec["sponsorship"] == "yes" and rec["apply_route"] == ["dm", "link"] and rec["apply_link"] == APPLY_URL
    assert {"agents", "evals", "Python"} <= set(rec["skills"]) and rec["family"] == "founding engineer"
    assert rec["stage"] == "founding" and rec["key"] == "x:1975000000000000001"


def test_team_company_from_the_bio_and_an_email_route():
    rec = posts.structure(posts.normalize_row(PAT_X, "x"))
    assert (rec["role_title"], rec["team"], rec["company"]) == ("Agent Engineer", "Applied Agents", "Scribe")
    assert rec["apply_route"] == ["email"] and rec["emails"] == ["pat@scribe.example.test"] and rec["remote"] == "remote"
    assert rec["years"] is None and rec["in_band"]


def test_eight_years_is_a_stretch_through_the_poster():
    rec = posts.structure(posts.normalize_row(SAM_LI, "linkedin"))
    assert rec["hiring"] and rec["years"] == 8 and rec["in_band"]
    assert rec["years_call"].startswith("stretch") and "through a person" in rec["years_call"]
    assert "8 or more is still the fit gate" in rec["years_call"]
    assert rec["apply_route"] == ["reply"] and rec["role_title"] == "Senior ML Engineer"


def test_a_watched_poster_takes_the_company_from_people_watch():
    rec = posts.structure(posts.normalize_row(NOISE_X, "x"), watched={"company": "scribe"})
    assert rec["company"] == "scribe" and not rec["hiring"] and not rec["in_band"]


def test_nothing_is_invented_when_the_post_does_not_say():
    row = dict(PAT_X, text="We're hiring engineers. Come build with us.", author_bio="")
    rec = posts.structure(posts.normalize_row(row, "x"))
    assert rec["company"] is None and rec["location"] is None and rec["remote"] == "unknown"
    assert rec["apply_route"] == ["not stated"] and rec["sponsorship"] == "unknown" and rec["team"] is None


# ----------------------------------------------------------------------------- LinkedIn input (the browser pass)
def test_check_refuses_bad_linkedin_rows_with_their_line(js, capsys):
    path = js / "state" / "posts" / "linkedin-2026-10-05.jsonl"
    _write(path, [JO_LI, dict(JO_LI, url="https://www.linkedin.com/jobs/view/4012345678/"),
                  dict(SAM_LI, author_url="https://www.linkedin.com/search/results/people/?q=x"),
                  dict(SAM_LI, date="Oct 3"), dict(SAM_LI, text="")])
    assert posts.main(["check", "--root", str(js), "--file", str(path)]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["rows"] == 1 and [p.split(":")[0] for p in out["problems"]] == [
        "linkedin-2026-10-05.jsonl line 2", "linkedin-2026-10-05.jsonl line 3", "linkedin-2026-10-05.jsonl line 4",
        "linkedin-2026-10-05.jsonl line 5"]


def test_post_keys_are_the_platform_ids():
    assert posts.post_key("linkedin", JO_LI["url"]) == "linkedin:7380000000000000001"
    assert posts.post_key("linkedin", "https://www.linkedin.com/feed/update/urn:li:activity:7380000000000000003/") == \
        "linkedin:7380000000000000003"
    assert posts.post_key("x", JO_X["url"]) == "x:1975000000000000001"


# ----------------------------------------------------------------------------- ingest
def test_a_dry_run_writes_nothing_and_says_what_it_would_make(js):
    _files(js)
    before = sorted(p.relative_to(js) for p in js.rglob("*"))
    rep = posts.ingest(js, today=TODAY)
    assert sorted(p.relative_to(js) for p in js.rglob("*")) == before
    assert (rep["read"], rep["new"], rep["cross_platform"], rep["hiring"], rep["in_band"]) == (5, 4, 1, 3, 3)
    assert {w["role"] for w in rep["would_make"]} == {"Founding AI Engineer", "Agent Engineer", "Senior ML Engineer"}
    assert rep["out_of_band"] == []                                   # a post asking 8 years is a stretch, not out of band


def test_write_makes_records_people_and_jobs_once(js):
    _files(js)
    rep = posts.ingest(js, today=TODAY, write=True)
    assert (rep["new"], rep["cross_platform"], rep["in_band"], rep["jobs"]) == (4, 1, 3, 3)
    led = posts.load_ledger(js)
    assert set(led["records"]) == {"x:1975000000000000001", "x:1975000000000000009", "x:1975000000000000010",
                                   "linkedin:7380000000000000002"}                # every post read, hiring or not
    assert led["alias"] == {"linkedin:7380000000000000001": "x:1975000000000000001"}    # the same post, both platforms
    jo = led["records"]["x:1975000000000000001"]
    assert jo["urls"] == [JO_X["url"], JO_LI["url"]] and jo["prospect"] and jo["job"]
    meta, body = jc.read_record(js / "prospects" / f"{jo['prospect']}.md")
    assert (meta["name"], meta["x_handle"], meta["company"], meta["hiring_post_url"]) == \
        ("Jo Park", "founder_jo", "Tiny AI", JO_X["url"])
    assert meta["role"] == "founder" and "hiring for" in meta["relation"] and "x" in meta["channels"]
    job = fa.load_jobs(js)[jo["job"]]
    assert job["source_url"] == APPLY_URL and job["platform"] == "x" and job["prospect"] == jo["prospect"]
    assert job["hiring_signal"] == {"url": JO_X["url"], "date": "2026-10-02", "by": "@founder_jo"}
    assert hs.from_post(job) == JO_X["url"]                                       # ranked above listings
    assert "<untrusted_jd>" in job["_body"] and "Founding AI Engineer" in job["title"]
    review = (js / "state" / "review" / "posts-2026-10-05.md").read_text()
    assert "<untrusted_x>" in review and SAM_LI["url"] in review and "through a person" in review
    again = posts.ingest(js, today=TODAY, write=True)                              # latest only: nothing twice
    assert (again["new"], again["duplicates"], again["jobs"], again["retried"]) == (0, 5, 0, 0)
    assert len(list((js / "prospects").glob("*.md"))) == 3 and len(fa.load_jobs(js)) == 3


def test_a_second_post_updates_the_same_person_by_handle(js):
    _write(js / "state" / "posts" / "x-2026-10-05.jsonl", [PAT_X])
    posts.ingest(js, today=TODAY, write=True)
    later = dict(PAT_X, id="1975000000000000020", url="https://x.com/pat_ai/status/1975000000000000020",
                 author_name="Pat K.", text="We're hiring an AI Engineer for evals at Scribe. Remote.")
    _write(js / "state" / "posts" / "x-2026-10-06.jsonl", [later])
    posts.ingest(js, today=date(2026, 10, 6), write=True)
    people = list((js / "prospects").glob("*.md"))
    assert len(people) == 1
    meta, body = jc.read_record(people[0])
    assert meta["hiring_post_url"] == PAT_X["url"] and body.count("hiring post on X") == 2
    assert body.count(" · added · x · ") == 2 and " · email · " not in body          # an X poster is not an email


def test_people_watch_proposals_skip_watched_and_proposed_people(js):
    _files(js)
    posts.ingest(js, today=TODAY, write=True)
    prop = yaml.safe_load((js / "state" / "posts" / "people-watch-proposals-2026-10-05.yaml").read_text())["proposals"]
    assert [p["name"] for p in prop] == ["Jo Park", "Sam Lee"]          # Pat is watched
    assert prop[0]["x_handle"] == "founder_jo" and prop[0]["status"] == "watch" and prop[0]["why"]
    assert "people:" in (js / "targets" / "people-watch.yaml").read_text()           # never written by the script
    _write(js / "state" / "posts" / "x-2026-10-06.jsonl", [dict(JO_X, id="1975000000000000030",
                                                                 url="https://x.com/founder_jo/status/1975000000000000030",
                                                                 text=JO_X["text"].replace("Founding", "Senior Founding"))])
    posts.ingest(js, today=date(2026, 10, 6), write=True)
    assert not (js / "state" / "posts" / "people-watch-proposals-2026-10-06.yaml").exists()


def test_outbound_rows_keep_a_bad_handle_out_and_the_person_in(js):
    roots = prospects.cu.Roots(js=js, os=js.parents[3])
    out = js / "state" / "harvest" / "outbound-posts-2026-10-05.jsonl"
    out.parent.mkdir(parents=True)
    _write(out, [{"name": "Lee Doe", "company": "Acme", "x_handle": "not a handle!", "hiring_post_url": "javascript:x",
                  "found_via": "hiring post", "at": "2026-10-05T12:00:00Z"}])
    prospects.ingest(roots, outbound_files=[out], today=TODAY)
    meta, _ = jc.read_record(next((js / "prospects").glob("*.md")))
    assert meta["name"] == "Lee Doe" and not meta.get("x_handle") and not meta.get("hiring_post_url")


# ----------------------------------------------------------------------------- trend and yield
def test_trend_compares_weeks_and_counts_the_yield_per_credit(js):
    _files(js)
    old = dict(PAT_X, id="1974000000000000001", url="https://x.com/pat_ai/status/1974000000000000001", date="2026-09-25",
               text="We're hiring an ML Engineer for inference at Scribe.")
    _write(js / "state" / "posts" / "x-2026-09-25.jsonl", [old])
    posts.ingest(js, today=TODAY, write=True)
    (js / "state" / "x" / "usage.jsonl").write_text(
        json.dumps({"at": "2026-10-02T15:00:00Z", "day": "2026-10-02", "endpoint": "search", "status": 200, "usd": 0.3,
                    "results": 20}) + "\n" +
        json.dumps({"at": "2026-09-20T15:00:00Z", "day": "2026-09-20", "endpoint": "search", "status": 200, "usd": 0.4,
                    "results": 25}) + "\n")
    jo = posts.load_ledger(js)["records"]["x:1975000000000000001"]["prospect"]
    jc.write_record(js / "outbox" / "O-2026-0900.md", {"id": "O-2026-0900", "prospect": jo, "kind": "intro",
                                                       "created": "2026-10-05", "status": "pending"}, "note\n")
    (js / "touches" / "2026-10.jsonl").write_text(json.dumps({"at": "2026-10-05T18:00:00Z", "prospect": jo,
                                                              "direction": "in", "channel": "x-dm"}) + "\n")
    out = posts.trend(js, today=TODAY, write_yield=True)
    assert (out["this_week"], out["last_week"]) == (3, 1)
    y = out["yield_week"]["x"]
    assert (y["spend_usd"], y["posts_read"], y["hiring_posts"], y["roles_in_band"]) == (0.3, 20, 2, 2)
    assert (y["people_added"], y["notes_drafted"], y["replies"]) == (2, 1, 1)
    assert out["yield_week"]["linkedin"]["hiring_posts"] == 1 and out["yield_week"]["linkedin"]["spend_usd"] == 0
    text = (js / "state" / "review" / "posts-trend-2026-10-05.md").read_text()
    assert "| agents | 2 | 0 | +2 |" in text and "in the skills taxonomy" in text
    assert "| ML and research | 1 | 1 | +0 |" in text and "free (the owner's time)" in text and JO_X["url"] in text
    reg = (js / "targets" / "platforms.yaml").read_text()
    assert out["platforms_written"] == 2 and reg.count("posts_yield:") == 2 and "# a comment survives" in reg
    assert "posts_yield: {spend_usd: 0.7, posts_read: 45, hiring_posts: 3" in reg
    assert 'notes: "kept"' in reg and "fetched: 3, new: 3" in reg
    posts.trend(js, today=TODAY, write_yield=True)                       # rewritten in place, never duplicated
    assert (js / "targets" / "platforms.yaml").read_text().count("posts_yield:") == 2
    data = yaml.safe_load((js / "targets" / "platforms.yaml").read_text())
    assert set(data["platforms"][1]["yield"]) == {"fetched", "new", "queued", "applied", "interviews"}


def test_a_skill_not_in_the_taxonomy_is_never_suggested_as_a_keyword(js):
    rows = [dict(PAT_X, id=f"19750000000000001{i}", url=f"https://x.com/pat_ai/status/19750000000000001{i}",
                 text=f"We're hiring an AI engineer for Rust inference, team {i}.") for i in range(2)]
    _write(js / "state" / "posts" / "x-2026-10-04.jsonl", rows)
    posts.ingest(js, today=TODAY, write=True)
    posts.trend(js, today=TODAY)
    text = (js / "state" / "review" / "posts-trend-2026-10-05.md").read_text()
    assert "Rust rising" in text and "never added without evidence" in text


# ----------------------------------------------------------------------------- the routine's command
def test_the_x_command_reports_an_x_refusal_and_ingests_what_the_watch_found(js, monkeypatch, capsys):
    import x_api
    monkeypatch.setattr(x_api, "Client", lambda *a, **k: object())

    def depleted(client, cfg, today):
        raise x_api.XError("X answered 402 for search: credits depleted")
    monkeypatch.setattr(x_api, "watch", depleted)
    assert posts.main(["x", "--root", str(js), "--today", "2026-10-05", "--write"]) == 2
    assert "credits depleted" in json.loads(capsys.readouterr().out)["error"]

    def found(client, cfg, today):
        _write(js / "state" / "posts" / f"x-{today.isoformat()}.jsonl", [dict(PAT_X, text=(
            "We're hiring engineers for Example Platform: agents, evals, infra."))])
        return {"queries": 3, "candidates": [{}]}
    monkeypatch.setattr(x_api, "watch", found)
    assert posts.main(["x", "--root", str(js), "--today", "2026-10-05", "--write"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert (rep["new"], rep["in_band"], rep["jobs"]) == (1, 1, 1)
    job = next(iter(fa.load_jobs(js).values()))
    assert job["title"] == "Engineer, Example Platform" and job["platform"] == "x"


def test_a_board_job_at_the_posts_apply_link_gets_the_post_not_a_twin(js):
    rec = fa.make_record({"name": "Tiny AI", "slug": "tiny-ai"}, title="Founding AI Engineer", source="greenhouse",
                         source_url=APPLY_URL, apply_url=APPLY_URL, ats_vendor="greenhouse", posted_at="2026-09-20", location=None,
                         remote="onsite", comp=(None, None, None), employment_type=None, jd_text="Agents.",
                         now=datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc))
    rec.update({"id": "tiny-ai-board-1"})
    fa.save_job(js, rec)
    _write(js / "state" / "posts" / "x-2026-10-05.jsonl", [JO_X])
    rep = posts.ingest(js, today=TODAY, write=True)
    jobs = fa.load_jobs(js)
    assert rep["jobs"] == 1 and list(jobs) == ["tiny-ai-board-1"]
    job = jobs["tiny-ai-board-1"]
    assert job["source"] == "greenhouse" and job["hiring_signal"]["url"] == JO_X["url"] and job["prospect"]
    assert hs.from_post(job) == JO_X["url"]


# ----------------------------------------------------------------------------- retries, repeat posters, registry
def _made(js):
    return {k: r for k, r in posts.load_ledger(js)["records"].items() if r.get("made_on")}


def test_retry_a_job_not_made_leaves_no_made_row_and_the_next_run_makes_it_once(js, monkeypatch):
    _write(js / "state" / "posts" / "x-2026-10-05.jsonl", [JO_X])
    real = posts.make_job
    monkeypatch.setattr(posts, "make_job", lambda *a, **k: None)
    rep = posts.ingest(js, today=TODAY, write=True)
    assert rep["jobs"] == 0 and rep["errors"] and not _made(js)
    monkeypatch.setattr(posts, "make_job", real)
    rep = posts.ingest(js, today=TODAY, write=True)
    assert (rep["retried"], rep["jobs"]) == (1, 1) and list(_made(js)) == ["x:1975000000000000001"]
    rep = posts.ingest(js, today=TODAY, write=True)
    assert (rep["retried"], rep["jobs"]) == (0, 0) and len(fa.load_jobs(js)) == 1
    assert len(list((js / "prospects").glob("*.md"))) == 1


def test_retry_a_systemexit_from_jobs_manual_is_caught_reported_and_retried(js, monkeypatch):
    import jobs_manual
    _write(js / "state" / "posts" / "x-2026-10-05.jsonl", [JO_X])
    real = jobs_manual.main

    def boom(argv):
        raise SystemExit(2)
    monkeypatch.setattr(jobs_manual, "main", boom)
    rep = posts.ingest(js, today=TODAY, write=True)
    assert "SystemExit" in rep["errors"][0]["error"] and not _made(js) and not fa.load_jobs(js)
    monkeypatch.setattr(jobs_manual, "main", real)
    rep = posts.ingest(js, today=date(2026, 10, 6), files=[], write=True)     # the next run, no new file: a retry
    assert (rep["retried"], rep["jobs"]) == (1, 1) and len(fa.load_jobs(js)) == 1
    assert len(_made(js)) == 1


def test_poster_a_repeat_linkedin_poster_without_a_profile_url_links_to_the_same_prospect(js):
    first = dict(JO_LI, text="We're hiring a Founding AI Engineer at Tiny AI to build agents. 2+ years.")
    second = {"url": "https://www.linkedin.com/posts/jo-park_ai-activity-7380000000000000077-QwEr", "author_name": "Jo Park",
              "date": "2026-10-04", "text": "We're hiring an Applied AI Engineer at Tiny AI for evals and agents. Remote."}
    _write(js / "state" / "posts" / "linkedin-2026-10-05.jsonl", [first])
    posts.ingest(js, today=TODAY, write=True)
    _write(js / "state" / "posts" / "linkedin-2026-10-06.jsonl", [second])
    posts.ingest(js, today=date(2026, 10, 6), write=True)
    people = list((js / "prospects").glob("*.md"))
    assert len(people) == 1
    jobs = fa.load_jobs(js)
    assert len(jobs) == 2 and {j["prospect"] for j in jobs.values()} == {people[0].stem}


def test_poster_check_warns_when_a_linkedin_row_has_no_profile_url(js, capsys):
    path = js / "state" / "posts" / "linkedin-2026-10-05.jsonl"
    _write(path, [{k: v for k, v in JO_LI.items() if k != "author_url"}])
    assert posts.main(["check", "--root", str(js), "--file", str(path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rows"] == 1 and not out["problems"] and "author_url" in out["warnings"][0]


def test_registry_write_yield_without_a_registry_is_reported_not_raised(js, capsys):
    (js / "targets" / "platforms.yaml").unlink()
    assert posts.main(["trend", "--root", str(js), "--today", "2026-10-05", "--write-yield"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["platforms_written"] == 0 and "platforms.yaml" in out["yield_note"]


def test_a_post_asking_8_years_still_records_the_job_marked_stretch(js):
    rec = posts.structure(posts.normalize_row(SAM_LI, "linkedin"))
    assert rec["in_band"] and rec["years"] == 8 and rec["years_call"].startswith("stretch")
    _write(js / "state" / "posts" / "linkedin-2026-10-05.jsonl", [SAM_LI])
    rep = posts.ingest(js, today=TODAY, write=True)
    assert rep["jobs"] == 1


def test_an_off_role_hiring_post_is_recorded_not_made(js):
    row = dict(PAT_X, id="1975000000000000044", url="https://x.com/pat_ai/status/1975000000000000044",
               text="We're hiring an account executive to sell our AI agents platform.")
    rec = posts.structure(posts.normalize_row(row, "x"))
    assert rec["hiring"] and not rec["in_band"] and rec["role_title"] is None
    _write(js / "state" / "posts" / "x-2026-10-05.jsonl", [row])
    rep = posts.ingest(js, today=TODAY, write=True)
    assert (rep["jobs"], rep["prospects"]) == (0, 0)
    assert "Off-role" in (js / "state" / "review" / "posts-2026-10-05.md").read_text()
