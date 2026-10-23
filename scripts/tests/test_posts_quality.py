"""posts.py quality gate: most posts a search turns up are commentary, news or job-board accounts, and a Paris role asking
for a work permit must not count as in band. These tests use a synthetic run (fixtures/posts/x-2026-10-05.jsonl: 15 invented
accounts and posts) and the ledger records an earlier, looser gate wrote for it (fixtures/posts/records-2026-10-05.jsonl).
A post is hiring only when it names a role or a company and is written in the first person by someone at the company,
from a person's account; a post placed outside the US or asking for a work permit there is out of band; news, job
boards, agencies and bots make no prospect (the deny list plus heuristics). Nothing touches the network."""
from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from comms_helpers import offline  # noqa: F401  (autouse: no network; puts scripts/ on sys.path)
import js_common as jc
import posts

TODAY = date(2026, 10, 5)
FIX = Path(__file__).resolve().parent / "fixtures" / "posts"
ROWS = [json.loads(line) for line in (FIX / "x-2026-10-05.jsonl").read_text(encoding="utf-8").splitlines() if line]
BY_ID = {r["id"]: r for r in ROWS}
# The run's three hiring posts: a founder, a CEO and a founder, each in the first person.
REAL = {"2000000000000008008": "mira_builds", "2000000000000011011": "riya_ng", "2000000000000012012": "elena_p"}
PARIS, INDIA = "2000000000000004004", "2000000000000013013"
OFFENDERS = {"mktnewsdaily", "cryptojobs_feed", "ai_jobs_digest", "remote_jobs_hub", "web3_listings", "talent_scout_eu"}
COMMENTARY, NO_COMPANY = "2000000000000006006", "2000000000000010010"
WATCHED = "dana_intros"


def _post(row):
    return posts.normalize_row(row, "x")


def _deny():
    """The synthetic deny list (fixtures/posts/x-deny.yaml), loaded through a scratch root."""
    import tempfile
    root = Path(tempfile.mkdtemp())
    (root / "targets").mkdir()
    shutil.copy(FIX / "x-deny.yaml", root / "targets" / "x-deny.yaml")
    return posts.load_deny(root)


@pytest.fixture
def js(tmp_path):
    root = tmp_path / "os" / "area" / "group" / "projects" / "job-search"
    for d in ("targets", "prospects", "jobs", "state/posts", "state/x", "outbox", "touches", "profile"):
        (root / d).mkdir(parents=True)
    shutil.copy(FIX / "x-deny.yaml", root / "targets" / "x-deny.yaml")     # a synthetic deny list
    (root / "targets" / "platforms.yaml").write_text("platforms:\n  - {id: x, name: X, alert_senders: []}\n")
    (root / "targets" / "companies.yaml").write_text("companies: []\n")
    (root / "targets" / "people-watch.yaml").write_text(     # Dana is a watched person
        "people:\n  - {name: Dana Intros, x_handle: dana_intros, company: inkwell, role: hiring poster, why: test row, "
        "added: 2026-10-04, source: test, status: watch}\n")
    shutil.copy(FIX / "x-2026-10-05.jsonl", root / "state" / "posts" / "x-2026-10-05.jsonl")
    return root


def test_the_runs_commentary_and_aggregator_posts_are_not_hiring_and_the_three_real_ones_are():
    recs = {r["id"]: posts.structure(_post(r)) for r in ROWS}          # the gate and heuristics alone, no deny list
    hiring = {pid for pid, rec in recs.items() if rec["hiring"]}
    assert hiring == set(REAL), {pid: recs[pid]["not_hiring"] for pid in hiring ^ set(REAL)}
    for pid, rec in recs.items():
        if pid not in REAL:
            assert rec["not_hiring"] and not rec["in_band"], pid
    assert all(recs[pid]["in_band"] and recs[pid]["first_person"] for pid in REAL)
    # commentary from a person (a reply about "a hiring spree"; "Hiring a Founding Engineer at <link>" with no bio tie)
    assert recs[COMMENTARY]["not_hiring"].startswith("commentary")
    assert recs[NO_COMPANY]["not_hiring"].startswith("commentary")


def test_first_person_alone_is_not_hiring_the_post_must_name_a_role_or_a_company():
    # The run's ana_codes row (a person; her bio names no company), its text put in the first person: with no role
    # and no company named it stays not hiring; naming the role makes it a hiring post.
    row = BY_ID[NO_COMPANY]
    vague = posts.structure(_post(dict(row, text="We're hiring for AI. DM me if you ship agents.")))
    assert vague["first_person"] and vague["poster_kind"] == "person"
    assert not vague["hiring"] and vague["not_hiring"] == "names no role and no company" and not vague["in_band"]
    named = posts.structure(_post(dict(row, text="We're hiring a Founding Engineer. DM me if you ship agents.")))
    assert named["hiring"] and named["role_title"] == "Founding Engineer" and named["not_hiring"] is None


def test_the_paris_role_with_a_work_permit_and_the_india_role_are_out_of_band():
    paris = posts.structure(_post(BY_ID[PARIS]))
    assert not paris["in_band"] and paris["where_call"].startswith("out of band") and "work permit" in paris["where_call"]
    # the place alone decides, whoever posts it: the same two posts from a person at a company stay out of band
    person = {"author_handle": "someone", "author_name": "Some One", "author_bio": "Founder @acme"}
    paris_p = posts.structure(_post(dict(BY_ID[PARIS], **person)))
    india_p = posts.structure(_post(dict(BY_ID[INDIA], **person)))
    assert paris_p["hiring"] and not paris_p["in_band"]
    assert india_p["hiring"] and not india_p["in_band"] and "India" in india_p["where_call"]
    us = posts.structure(_post(dict(BY_ID[INDIA], **person, text=BY_ID[INDIA]["text"].replace("India", "San Francisco"))))
    assert us["hiring"] and us["in_band"] and us["where_call"] == "in band"
    assert posts.where_call("Must be based in London. Python, agents.").startswith("out of band")
    assert posts.where_call("SF or London, agents and evals") == "in band"


def test_news_job_boards_and_agencies_are_not_people_by_list_and_by_heuristic():
    deny = _deny()
    assert OFFENDERS <= set(deny["accounts"])
    for r in ROWS:
        a = _post(r)["author"]
        listed, _ = posts.poster_kind(a, r["text"], deny=deny)
        bare, why = posts.poster_kind(a, r["text"])                  # the heuristics alone, no list
        if a["handle"] in OFFENDERS:
            assert listed != "person" and bare != "person", (a["handle"], why)
        if r["id"] in REAL:
            assert listed == "person" and bare == "person", a["handle"]
    dana = _post(next(r for r in ROWS if r["author_handle"] == WATCHED))["author"]
    assert posts.poster_kind(dana, "", watched={"company": "inkwell"}) == ("person", None)
    assert posts.poster_kind({"handle": "busy", "posts_per_day": 400.0}, "")[0] == "bot"


def test_a_dry_ingest_of_the_rows_would_make_only_the_three_posters(js):
    rep = posts.ingest(js, today=TODAY, write=False, os_root=js.parents[3])
    assert (rep["read"], rep["new"], rep["hiring"], rep["in_band"]) == (15, 15, 3, 3)
    made = {w["post"] for w in rep["would_make"]}
    assert made == {BY_ID[pid]["url"] for pid in REAL}
    nots = {n["post"]: n for n in rep["not_hiring"]}
    for r in ROWS:
        if r["author_handle"].lower() in OFFENDERS:
            assert r["url"] in nots and nots[r["url"]]["poster"] != "person"
    assert BY_ID[PARIS]["url"] in nots and not list((js / "prospects").iterdir())


def test_a_write_makes_no_prospect_for_aggregators_and_a_rerun_recreates_nothing(js):
    # The ledger as an earlier, looser run wrote it (13 called in band), with no made rows: the next run retries pending
    # posts, and today's gate must drop the 10 that are not hiring instead of making their prospects.
    shutil.copy(FIX / "records-2026-10-05.jsonl", posts.ledger_path(js))
    rep = posts.ingest(js, today=TODAY, write=True, os_root=js.parents[3])
    assert rep["errors"] == [] and rep["retried"] == 3 and len(rep["retry_dropped"]) == 10
    handles = set()
    for f in (js / "prospects").glob("*.md"):
        meta, _ = jc.read_record(f)
        handles.add(meta.get("x_handle"))
    assert handles == {h.lower() for h in REAL.values()}
    assert not handles & OFFENDERS
    jobs = {jc.read_record(f)[0]["company"] for f in (js / "jobs").glob("*.md")}
    assert jobs == {"quillhq", "northwindio"}                    # no pixelforge or examplecoin job this time
    again = posts.ingest(js, today=TODAY, write=True, os_root=js.parents[3])
    assert again["retried"] == 0 and again["prospects"] == 0 and len(list((js / "prospects").glob("*.md"))) == 3
