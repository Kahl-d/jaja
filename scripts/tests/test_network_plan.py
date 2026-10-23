"""network_plan.py: the day's network plan. At most 10 connection suggestions,
each with who and why (no note; a live thread continues instead); at most 3 posts worth a comment, each
with a drafted comment giving the owner's view on the post's subject from real work; one post idea a week. Drafts go to
state/drafts/ and through outbox lint; nothing is filed or sent, no record is changed."""
from __future__ import annotations

from datetime import date

import yaml

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; the root fixture; no-network guard)

import network_plan as npl  # noqa: E402

TODAY = date(2026, 10, 2)          # a Friday; ISO week 40
NEXT_UP = """# NEXT-UP

| # | role | fit | why here | chance | posted | state | page | tier |
|---|---|---|---|---|---|---|---|---|
| 1 | Quillon · Agent Evaluation Engineer | 4.7 | evals | sponsors | 2026-09-28 | READY | v | dream |
| 2 | Brightwave · AI Engineer | 4.2 | agents | unknown | 2026-09-25 | PACKAGE | - | focused |
| 3 | Nimbus Labs · Applied AI Engineer | 4.0 | agents | unknown | 2026-09-20 | JOB | - | mass |
| 4 | Orbit AI · Agent Engineer | 4.0 | agents | unknown | 2026-09-19 | JOB | - | mass |
| 5 | Zephyr Labs · AI Engineer | 4.0 | agents | unknown | 2026-09-18 | JOB | - | mass |
"""
ANSWERS = """---
family: {family}
status: draft for the owner
---
# Form answers

## A project you are proud of (200 words max)
I made agent quality measurable on real user prompts with an evaluation bench. [ev:ev-acme-023]

## Why AI and agents (120 words max)
Real evals come from real use, and each round's prompt changes are what make them read real. [ev:ev-acme-028]
"""
XWATCH = """# X watch 2026-10-01: hiring candidates (2)

## @evalnerd · 2026-10-01T15:00:00Z · https://x.com/evalnerd/status/1
- Why: hiring post; replies 3, who may reply: everyone

We're hiring engineers to build LLM evals and judge pipelines for agent benchmarks.

- Job: `python3 scripts/jobs_manual.py add ...`

## @chefbot · 2026-10-01T16:00:00Z · https://x.com/chefbot/status/2
- Why: hiring post; replies 1, who may reply: everyone

Our bakery is hiring a pastry chef.

"""


def prospect(root, slug, **meta):
    base = {"type": "prospect", "id": slug, "name": slug.replace("-", " ").title(), "status": "new", "priority": "P2",
            "company": "", "company_slug": "", "linkedin_url": f"https://www.linkedin.com/in/{slug}", "warmth": 0}
    base.update(meta)
    p = root / "prospects" / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\n" + yaml.safe_dump(base, sort_keys=False) + "---\n# " + base["name"] + "\n\n## Timeline\n"
                 + meta.pop("_timeline", "- 2026-09-30 created by prospector\n"))
    return p


def world(root, extra_prospects=0):
    (root / "hub").mkdir(parents=True)
    (root / "hub" / "NEXT-UP.md").write_text(NEXT_UP)
    (root / "profile" / "answers").mkdir(parents=True)
    for fam in ("evaluation", "agent-harness"):
        (root / "profile" / "answers" / f"{fam}.md").write_text(ANSWERS.format(family=fam))
    prospect(root, "ana-quill", company="Quillon", company_slug="quillon", relation="no prior contact; target of Quillon Agent Evaluation Engineer", priority="P0")
    prospect(root, "ben-bright", company="Brightwave", company_slug="brightwave")
    prospect(root, "cara-ref", company="Nimbus Labs", company_slug="nimbus-labs", tags=["referrer"])
    prospect(root, "dan-done", company="Quillon", company_slug="quillon", relation="1st-degree LinkedIn connection")      # connected
    prospect(root, "eve-dnc", company="Quillon", company_slug="quillon", status="do_not_contact")                        # never
    prospect(root, "fay-random", company="Example Bakery", company_slug="example-bakery")                                      # no reason
    prospect(root, "gus-thread", company="Brightwave", company_slug="brightwave", thread_ids=["t1"], inbound_count=1,
             _timeline="- 2026-09-29 in: \"Thanks Alex, let's talk next week about the agents role.\"\n")
    prospect(root, "hal-drafted", company="Quillon", company_slug="quillon", status="drafted")                           # pending
    prospect(root, "ivy-nourl", company="Quillon", company_slug="quillon", linkedin_url=None)                           # no channel
    for i in range(extra_prospects):
        prospect(root, f"zed-{i:02d}", company="Quillon", company_slug="quillon")
    (root / "state" / "review").mkdir(parents=True)
    (root / "state" / "review" / "x-watch-2026-10-01.md").write_text(XWATCH)
    (root / "targets").mkdir()
    (root / "targets" / "people-watch.yaml").write_text(yaml.safe_dump({"people": [
        {"name": "Jo Poster", "linkedin_url": "https://www.linkedin.com/in/jo-poster", "company": "Brightwave",
         "role": "Head of AI", "why": "posts Brightwave's agent hiring", "status": "watch"},
        {"name": "Xi Handle", "x_handle": "xihandle", "company": "orbit-ai", "role": "founder",
         "why": "posts Orbit AI's hiring", "status": "watch"},
        {"name": "Old Follow", "x_handle": "oldf", "company": "quillon", "why": "posts hiring", "status": "followed"}]}))
    return root


def test_suggestions_have_reasons_and_skip_the_wrong_people(root):
    plan = npl.build(world(root), TODAY)
    who = [s["slug"] for s in plan["connect"]]
    assert who[0] == "ana-quill"                                     # P0, NEXT-UP #1, on the role's path
    assert {"ben-bright", "cara-ref", "jo-poster", "xi-handle"} <= set(who) and "old-follow" not in who
    xi = next(s for s in plan["connect"] if s["slug"] == "xi-handle")
    assert xi["channel"] == "x-follow" and xi["note"] == "" and xi["company"] == "Orbit AI"
    assert not {"dan-done", "eve-dnc", "fay-random", "hal-drafted", "ivy-nourl"} & set(who)
    assert all(s["why"] for s in plan["connect"])
    assert next(s for s in plan["connect"] if s["slug"] == "cara-ref")["why"][0].startswith("a referrer path")


def test_at_most_ten_suggestions(root):
    plan = npl.build(world(root, extra_prospects=15), TODAY)
    assert len(plan["connect"]) == 10


def test_notes_are_plain_short_and_never_open_with_the_closure_or_on_prem(root):
    plan = npl.build(world(root), TODAY)
    for s in [x for x in plan["connect"] if x["channel"] == "linkedin-connect"]:
        assert len(s["note"]) <= 200, s["note"]
        low = s["note"].lower()
        assert "closing" not in low and "on-prem" not in low and "\u2014" not in s["note"]
        assert s["lint"] == [], (s["slug"], s["lint"])


def test_a_live_thread_gets_a_continuation_not_a_cold_note(root):
    plan = npl.build(world(root), TODAY)
    gus = next(t for t in plan["threads"] if t["slug"] == "gus-thread")
    assert "let's talk next week about the agents role" in gus["continue_from"]
    assert "gus-thread" not in [s["slug"] for s in plan["connect"]]


def test_comments_answer_the_subject_and_skip_unrelated_posts(root):
    plan = npl.build(world(root), TODAY)
    assert [c["url"] for c in plan["comments"]] == ["https://x.com/evalnerd/status/1"]
    c = plan["comments"][0]
    assert c["family"] == "evaluation" and "Real evals come from real use" in c["draft"]
    assert len(c["draft"]) <= 280 and c["lint"] == [] and "ev-acme-028" in c["evidence_ids"]


def test_one_post_idea_a_week(root, capsys):
    world(root)
    assert npl.main(["--root", str(root), "--today", TODAY.isoformat()]) == 0
    first = (root / "state" / "network" / "2026-10-02.md").read_text()
    assert "## Post idea" in first and "Post idea: " in first
    nxt = npl.build(root, date(2026, 10, 3))                         # same ISO week: no second idea
    assert nxt["post_idea"] is None
    assert npl.build(root, date(2026, 10, 5))["post_idea"] is not None   # the next week


def test_writes_plan_and_drafts_lints_them_and_changes_no_record(root, capsys):
    world(root)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert npl.main(["--root", str(root), "--today", TODAY.isoformat()]) == 0
    out = capsys.readouterr().out
    drafts = sorted((root / "state" / "drafts" / "N-2026-10-02").glob("*.md"))
    xdrafts = sorted((root / "state" / "drafts" / "N-2026-10-02-x").glob("*.md"))
    assert not any(d.name.startswith("connect-") for d in drafts)                 # connections carry no note
    assert [d.name for d in xdrafts] == ["comment-1.md"]                           # the X reply, filed through x_api
    plan = (root / "state" / "network" / "2026-10-02.md").read_text()
    assert "outbox lint: {}" in plan and "nothing is filed or sent" in plan and "\u2014" not in plan
    assert "connect with no note; messenger opens after the person accepts" in plan
    assert "people_traces.py --company nimbus" not in plan and "x_api.py follow --handle xihandle --request" in plan
    assert "people_traces.py --company zephyr-labs --team agents" in plan     # nobody known there: a lead
    changed = {p for p in root.rglob("*") if p.is_file() and before.get(p) != p.read_bytes()}
    assert all("state/network" in str(p) or "state/drafts" in str(p) for p in changed)
    assert not (root / "outbox").exists() and "wrote state/network/2026-10-02.md" in out


def test_dry_run_writes_nothing(root, capsys):
    world(root)
    before = {p for p in root.rglob("*") if p.is_file()}
    assert npl.main(["--root", str(root), "--today", TODAY.isoformat(), "--dry-run"]) == 0
    assert {p for p in root.rglob("*") if p.is_file()} == before
    assert "Connection suggestions" in capsys.readouterr().out


# The evening review's one network line, read only: today's plan suggestions still
# waiting, connection notes and X follows counted apart.
def test_evening_line_counts_todays_suggestions_still_waiting(root, capsys):
    world(root)
    npl.main(["--root", str(root), "--today", TODAY.isoformat()])
    capsys.readouterr()
    ev = npl.evening(root, TODAY)
    plan = npl.build(root, TODAY)
    notes = sum(1 for c in plan["connect"] if c.get("channel") != "x-follow")
    assert ev["connect_waiting"] == ev["connect_total"] == notes and ev["follows_waiting"] == ev["follows_total"] == 1
    assert ev["line"] == (f"network (state/network/{TODAY.isoformat()}.md): connection suggestions waiting {notes} of "
                          f"{notes} (the owner sends each); X follows waiting 1 of 1 (each needs an approval and X credits)")


def test_evening_line_drops_people_acted_on_since_the_plan(root, capsys):
    world(root)
    npl.main(["--root", str(root), "--today", TODAY.isoformat()])
    capsys.readouterr()
    prospect(root, "ana-quill", company="Quillon", company_slug="quillon", relation="connection accepted 2026-10-02")
    w = yaml.safe_load((root / "targets" / "people-watch.yaml").read_text())
    for r in w["people"]:
        if r["name"] == "Xi Handle":
            r["status"] = "followed"
    (root / "targets" / "people-watch.yaml").write_text(yaml.safe_dump(w))
    ev = npl.evening(root, TODAY)
    assert ev["connect_waiting"] == ev["connect_total"] - 1 and ev["follows_waiting"] == 0


def test_evening_line_says_when_no_plan_ran_today(root):
    world(root)
    ev = npl.evening(root, TODAY)
    assert ev["line"] == f"network: no plan for {TODAY.isoformat()} (network_plan.py has not run today; not zero, missing)"


def test_evening_cli_prints_the_line_and_writes_nothing(root, capsys):
    world(root)
    npl.main(["--root", str(root), "--today", TODAY.isoformat()])
    capsys.readouterr()
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert npl.main(["--root", str(root), "--today", TODAY.isoformat(), "--evening"]) == 0
    assert capsys.readouterr().out.startswith("network (state/network/")
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
