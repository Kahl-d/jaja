"""The morning loop's three scripts: recruiter_sweep.py lists what is new since the last sweep, cards.py ranks
it into flashcards with an action and an effort, day_queue.py turns the yes deep cards into a queue lanes claim from.
Tiny fixture trees only; no network, no live data."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from disc_harness import *  # noqa: F401,F403
from disc_harness import SCRIPTS

COMPANIES_YAML = """\
companies:
  - name: Acme Labs
    slug: acme-labs
    tier: dream
    ats_vendor: greenhouse
    board_token: acmelabs
    careers_url: https://acmelabs.example.test/careers
    sponsorship_stance: sponsors
    h1b_approvals_recent: 143
    notes: ""
  - {name: Roadrunner, slug: roadrunner, tier: strong, ats_vendor: lever, board_token: roadrunner}
  - {name: Legacy Corp, slug: legacy-corp, tier: fallback, ats_vendor: icims, board_token: legacy}
"""


def _load(name: str, alias: str):
    """By path: the kernel's scripts/kernel/sweep.py sits ahead on sys.path once js_common is imported."""
    spec = importlib.util.spec_from_file_location(alias, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sweep = _load("recruiter_sweep", "js_sweep_under_test")
cards = _load("cards", "js_cards_under_test")
dq = _load("day_queue", "js_day_queue_under_test")

T0 = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)          # 08:00 Pacific, the first sweep
T1 = datetime(2026, 10, 6, 21, 0, tzinfo=timezone.utc)          # 14:00 Pacific, the second


def job(root: Path, jid: str, first_seen: str, **kw) -> None:
    meta = {"type": "job", "id": jid, "company": kw.get("company", "Acme"), "company_slug": kw.get("slug", "acme"),
            "title": kw.get("title", "AI Engineer"), "source": kw.get("source", "greenhouse"),
            "apply_url": f"https://example.com/{jid}", "first_seen": first_seen, "location": kw.get("location", "Remote US"),
            "gate": kw.get("gate", "pass"), "hunt_score": kw.get("hunt", 60), "status": "fit_checked"}
    lines = ["---"] + [f"{k}: {json.dumps(v)}" for k, v in meta.items()] + ["---", "# job", ""]
    (root / "jobs").mkdir(exist_ok=True)
    (root / "jobs" / f"{jid}.md").write_text("\n".join(lines), encoding="utf-8")


def mail_row(mid: str, at: str, label: str, subject: str = "Hello") -> dict:
    return {"id": mid, "thread_id": mid, "internal_date": at, "direction": "in", "from": f"Ann <ann-{mid}@acme.com>",
            "from_email": f"ann-{mid}@acme.com", "subject": subject, "classification": {"label": label}}


def lead(url: str, seen_at: str, kind: str = "hiring_post", **kw) -> dict:
    row = {"platform": "linkedin", "kind": kind, "url": url, "author": kw.get("author", "Pat Lee"),
           "author_title": "CTO", "author_url": "https://www.linkedin.com/in/pat/", "company": kw.get("company", "Acme"),
           "posted_at": None, "seen_at": seen_at, "text": "We're hiring an AI engineer", "role_hint": "AI Engineer",
           "location_hint": kw.get("location", "San Francisco"), "query": "hiring ai engineer",
           "lead_id": sweep.lead_id_for(url)}
    row.update(kw.get("extra", {}))
    return row


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


@pytest.fixture
def tree(root: Path, os_root: Path) -> tuple[Path, Path, Path]:
    (root / "targets").mkdir()
    (root / "targets" / "companies.yaml").write_text(COMPANIES_YAML, encoding="utf-8")
    job(root, "old-1", "2026-10-05T10:00:00Z")
    job(root, "new-1", "2026-10-06T16:00:00Z", location="San Francisco, CA")
    job(root, "new-fail", "2026-10-06T16:00:00Z", gate="fail", location="London, UK")
    job(root, "alert-1", "2026-10-06T16:30:00Z", source="linkedin-alert")
    write_jsonl(root / "state" / "harvest" / "gmail-2026-10-06.classified.jsonl", [
        mail_row("m-old", "2026-10-05T12:00:00Z", "recruiter_outreach"),
        mail_row("m-new", "2026-10-06T16:00:00Z", "recruiter_outreach", "Role at Acme"),
        mail_row("m-news", "2026-10-06T16:05:00Z", "newsletter"),
        mail_row("m-alert", "2026-10-06T16:06:00Z", "job_alert")])
    write_jsonl(root / "state" / "posts" / "sweep-2026-10-06.jsonl", [
        lead("https://www.linkedin.com/posts/pat_1", "2026-10-06T15:30:00Z"),
        lead("https://www.linkedin.com/posts/old", "2026-10-05T15:30:00Z"),
        lead("https://www.linkedin.com/in/sam", "2026-10-06T15:40:00Z", kind="person", author="Sam Roe")])
    os_root.joinpath("work", "approvals").mkdir(parents=True)
    out = root / "state" / "recruiter"
    return root, os_root, out


# --------------------------------------------------------------------------- sweep
def test_sweep_lists_only_items_since_the_last_sweep(tree):
    root, os_root, out = tree
    first = sweep.build(root, os_root, T0 + (T1 - T0) / 6, "2026-10-06", out)   # since midnight Pacific
    g = first["groups"]
    assert [r["job"] for r in g["listings"]] == ["new-1"]                      # old-1 before since, new-fail left out
    assert first["left_out"]["listings"]["gate fail"] == 1
    assert [r["job"] for r in g["alerts"]] == ["alert-1"]
    assert [r["key"] for r in g["mail"]] == ["mail:m-new"]                     # newsletter and job alert left out
    assert first["left_out"]["mail"] == {"newsletter": 1, "job alert (listed as listings)": 1}
    assert [r["url"] for r in g["posts"]] == ["https://www.linkedin.com/posts/pat_1"]
    assert [r["author"] for r in g["people"]] == ["Sam Roe"]
    sweep.write(first, out, T0 + (T1 - T0) / 6)
    assert (out / "sweep-2026-10-06.md").is_file() and (out / "sweep-2026-10-06.json").is_file()

    job(root, "new-2", "2026-10-06T20:00:00Z")                                  # arrives after the first sweep
    write_jsonl(root / "state" / "posts" / "sweep-2026-10-07.jsonl", [
        lead("https://www.linkedin.com/posts/pat_1", "2026-10-07T15:30:00Z"),  # the same lead read again next day
        lead("https://www.linkedin.com/posts/pat_2", "2026-10-06T20:30:00Z")])
    second = sweep.build(root, os_root, T1, "last", out)
    g = second["groups"]
    assert second["since_why"].startswith("the last sweep")
    assert [r["job"] for r in g["listings"]] == ["new-2"]
    assert g["alerts"] == [] and g["mail"] == [] and g["people"] == []
    assert [r["url"] for r in g["posts"]] == ["https://www.linkedin.com/posts/pat_2"]   # deduped on lead_id across days
    assert second["left_out"]["sweep posts"]["already listed"] == 3                 # pat_1 twice, sam once


def test_sweep_with_no_earlier_sweep_reads_the_last_24_hours(tree):
    root, os_root, out = tree
    res = sweep.build(root, os_root, T1, "last", out)
    assert res["since_why"] == "no earlier sweep: the last 24 hours"
    assert res["since"] == "2026-10-05T21:00:00Z"


def test_sweep_refuses_a_post_row_that_breaks_the_format(tree):
    root, os_root, out = tree
    bad = lead("https://www.linkedin.com/posts/x", "2026-10-06T15:30:00Z", extra={"platform": "forum"})
    no_seen = lead("https://www.linkedin.com/posts/y", "2026-10-06T15:30:00Z", extra={"seen_at": ""})
    write_jsonl(root / "state" / "posts" / "sweep-2026-10-06.jsonl", [bad, no_seen])
    res = sweep.build(root, os_root, T1, "2026-10-06", out)
    assert res["groups"]["posts"] == []
    assert res["refused"] == ["sweep-2026-10-06.jsonl:1: platform 'forum' is not linkedin, x or yc",
                              "sweep-2026-10-06.jsonl:2: missing seen_at"]


def test_sweep_cli_dry_run_writes_nothing(tree, capsys):
    root, os_root, out = tree
    assert sweep.main(["--root", str(root), "--os-root", str(os_root), "--since", "2026-10-06",
                       "--now", "2026-10-06T21:00:00Z"]) == 0
    assert "boards 1 · alert mail 1" in capsys.readouterr().out
    assert not out.exists()


# --------------------------------------------------------------------------- cards
def _listing(key: str, hunt: float, location: str, **kw) -> dict:
    return {"key": f"job:{key}", "group": "listings", "kind": "listing", "job": key, "company": kw.get("company", key),
            "company_slug": kw.get("slug", key), "title": "AI Engineer", "location": location, "gate": kw.get("gate", "pass"),
            "hunt_score": hunt, "source": "greenhouse", "url": f"https://example.com/{key}",
            "h1b_approvals_3y": kw.get("h1b")}


def _sweep_doc(groups: dict) -> dict:
    base = {k: [] for k in ("posts", "people", "listings", "alerts", "mail", "scam", "follow_ups", "approvals", "outbox",
                            "applications")}
    base.update(groups)
    return {"type": "sweep", "date": "2026-10-06", "since": "2026-10-06T07:00:00Z", "groups": base}


def test_cards_put_posts_first_and_break_ties_by_preferred_area(root):
    (root / "targets").mkdir()
    (root / "targets" / "companies.yaml").write_text(COMPANIES_YAML, encoding="utf-8")
    doc = _sweep_doc({
        "listings": [_listing("austin", 60, "Austin, TX"), _listing("la", 60, "Capital Town, EX"),
                     _listing("sf", 60, "Springfield, EX"), _listing("top", 65, "Austin, TX")],
        "posts": [{"key": "lead:abc", "group": "posts", "kind": "hiring_post", "platform": "x", "author": "Pat",
                   "company": "Tiny", "url": "https://x.com/p/1", "location_hint": None}],
        "mail": [{"key": "mail:1", "kind": "mail", "label": "recruiter_outreach", "from": "Ann", "subject": "Hi"}]})
    res = cards.build(root, doc)
    order = [c["item_key"] for c in res["cards"]]
    assert order == ["lead:abc", "mail:1", "job:top", "job:sf", "job:la", "job:austin"]
    assert [c["n"] for c in res["cards"]] == [1, 2, 3, 4, 5, 6]
    assert res["cards"][0]["action"] == "reach-out" and res["cards"][0]["mode"] == "quick"


def test_cards_effort_follows_importance(root):
    (root / "targets").mkdir()
    (root / "targets" / "companies.yaml").write_text(COMPANIES_YAML, encoding="utf-8")
    doc = _sweep_doc({
        "listings": [_listing("base", 55, "Remote US"), _listing("hot", 80, "Remote US"),
                     _listing("bigco", 50, "Remote US", h1b=150), _listing("rev", 50, "Remote US", gate="review"),
                     _listing("acme-1", 50, "Capital Town, EX", company="Acme Labs", slug="acme-labs")],
        "mail": [{"key": "mail:o", "kind": "mail", "label": "offer", "from": "HR", "subject": "Offer"},
                 {"key": "mail:c", "kind": "mail", "label": "ats_confirmation", "from": "ATS", "subject": "Thanks"}]})
    res = cards.build(root, doc)
    by = {c["item_key"]: c for c in res["cards"]}
    assert (by["job:base"]["action"], by["job:base"]["effort"]) == ("quick-apply-base", "E2")
    assert (by["job:hot"]["action"], by["job:hot"]["effort"]) == ("tailored page", "E3")
    assert (by["job:bigco"]["action"], by["job:bigco"]["effort"]) == ("tailored page", "E3")
    assert (by["job:rev"]["action"], by["job:rev"]["effort"]) == ("research", "E2")
    assert (by["job:acme-1"]["action"], by["job:acme-1"]["effort"], by["job:acme-1"]["tier"]) == \
        ("tailored page", "E4", 2)
    assert by["mail:o"]["effort"] == "E5"
    assert "mail:c" not in by and res["counted"]["information only"] == 1
    quick = [c["n"] for c in res["cards"] if c["mode"] == "quick"]
    deep = [c["n"] for c in res["cards"] if c["mode"] == "deep"]
    assert max(quick) < min(deep)                                              # quick cards are numbered first


def test_cards_cap_listings_per_company_and_count_the_rest(root):
    doc = _sweep_doc({"listings": [_listing(f"a{i}", 50 + i, "Remote US", company="Apple", slug="apple")
                                   for i in range(7)]})
    res = cards.build(root, doc, per_company=5)
    assert len(res["cards"]) == 5 and res["counted"]["listings beyond the per-company cap"] == 2


def test_decisions_batch_line_then_card_lines():
    assert cards.parse_answer("yes 1-12, no 4") == {**{n: "yes" for n in range(1, 13)}, 4: "no"}
    md = "decisions: yes 1-3\n\n### 2 · x\n- decision: no\n\n### 3 · y\n- decision:\n"
    assert cards.decisions(md) == {1: "yes", 2: "no", 3: "yes"}


# --------------------------------------------------------------------------- day queue
def _cards_files(root: Path, out: Path, answer: str) -> None:
    doc = _sweep_doc({"listings": [_listing("hot1", 90, "San Francisco, CA"), _listing("hot2", 85, "Remote US"),
                                   _listing("base", 50, "Remote US"), _listing("rev", 50, "Remote US", gate="review")]})
    res = cards.build(root, doc)
    out.mkdir(parents=True, exist_ok=True)
    (out / "cards-2026-10-06.json").write_text(json.dumps(res), encoding="utf-8")
    (out / "cards-2026-10-06.md").write_text(cards.render(res).replace("decisions:", f"decisions: {answer}"),
                                              encoding="utf-8")


def test_queue_build_takes_the_yes_deep_cards(root):
    out = root / "state" / "recruiter"
    _cards_files(root, out, "yes 1-4, no 3")
    res = dq.build(out, "2026-10-06", T0)
    data = dq.load(out / "queue-2026-10-06.yaml")
    assert res["added"] == 3
    assert [(i["kind"], i["links"]["job"], i["rank"], i["status"]) for i in data["items"]] == [
        ("mass", "base", 1, "open"), ("resume", "hot1", 2, "open"), ("research", "rev", 3, "open")]
    # card 1 is the quick base apply: a yes queues it as mass (charter §7, one yes is enough); 3 is no
    assert dq.build(out, "2026-10-06", T0)["added"] == 0                     # idempotent


def _run(out: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPTS / "day_queue.py"), "--out", str(out), "--date", "2026-10-06",
                           *args], capture_output=True, text=True, timeout=60)


def test_queue_claim_is_exclusive_under_two_claimers(root):
    out = root / "state" / "recruiter"
    dq.add(out, "2026-10-06", T0, "research", "only one")
    procs = [subprocess.Popen([sys.executable, str(SCRIPTS / "day_queue.py"), "--out", str(out), "--date", "2026-10-06",
                               "claim", "--lane", lane, "--kind", "research"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) for lane in ("lane-a", "lane-b")]
    results = [(p.wait(timeout=60), p.stdout.read()) for p in procs]
    assert sorted(code for code, _ in results) == [0, 1]
    winner = json.loads(next(o for c, o in results if c == 0))
    item = dq.load(out / "queue-2026-10-06.yaml")["items"][0]
    assert item["status"] == "claimed" and item["claimed_by"] == winner["claimed_by"]

    for t in ("two", "three"):
        dq.add(out, "2026-10-06", T0, "resume", t)
    procs = [subprocess.Popen([sys.executable, str(SCRIPTS / "day_queue.py"), "--out", str(out), "--date", "2026-10-06",
                               "claim", "--lane", lane, "--kind", "resume"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) for lane in ("lane-a", "lane-b")]
    got = [json.loads(p.communicate(timeout=60)[0])["id"] for p in procs]
    assert sorted(got) == ["Q-2026-10-06-02", "Q-2026-10-06-03"]


def test_queue_claim_waits_for_the_lock_and_reports_busy(root):
    out = root / "state" / "recruiter"
    dq.add(out, "2026-10-06", T0, "research", "x")
    with dq.locked(out / "queue-2026-10-06.yaml"):
        r = _run(out, "--lock-timeout", "0.2", "claim", "--lane", "a", "--kind", "research")
    assert r.returncode == 2 and "queue busy" in r.stderr
    assert dq.load(out / "queue-2026-10-06.yaml")["items"][0]["status"] == "open"


def test_queue_done_and_release(root):
    out = root / "state" / "recruiter"
    dq.add(out, "2026-10-06", T0, "research", "a")
    dq.add(out, "2026-10-06", T0, "research", "b")
    first = dq.claim(out, "2026-10-06", T0, "lane-a", "research")
    assert first["id"] == "Q-2026-10-06-01"
    with pytest.raises(dq.QueueError, match="claimed by lane-a"):
        dq.done(out, "2026-10-06", T0, first["id"], "research/x.md", lane="lane-b")
    dq.release(out, "2026-10-06", T0, first["id"])
    again = dq.claim(out, "2026-10-06", T0, "lane-b", "research")
    assert again["id"] == first["id"] and again["claimed_by"] == "lane-b"     # released back to the top
    finished = dq.done(out, "2026-10-06", T1, again["id"], "research/x.md", lane="lane-b")
    assert finished["status"] == "done" and finished["result"] == "research/x.md"
    with pytest.raises(dq.QueueError, match="done"):
        dq.release(out, "2026-10-06", T1, again["id"])
    blocked = dq.release(out, "2026-10-06", T1, "Q-2026-10-06-02", blocked="needs the owner's fact")
    assert blocked["status"] == "blocked"
    assert dq.claim(out, "2026-10-06", T1, "lane-c", "research") is None       # nothing open is left


def test_queue_carries_unfinished_items_to_the_next_day(root):
    out = root / "state" / "recruiter"
    for t in ("done one", "claimed one", "open one"):
        dq.add(out, "2026-10-05", T0, "research", t)
    dq.claim(out, "2026-10-05", T0, "lane-a", "research")
    dq.done(out, "2026-10-05", T0, "Q-2026-10-05-01", "r.md")
    dq.claim(out, "2026-10-05", T0, "lane-a", "research")
    _cards_files(root, out, "")
    res = dq.build(out, "2026-10-06", T1)
    items = dq.load(out / "queue-2026-10-06.yaml")["items"]
    assert res["carried"] == 2
    assert [(i["title"], i["status"], i["carried_from"]) for i in items] == [
        ("claimed one", "open", "queue-2026-10-05.yaml"), ("open one", "open", "queue-2026-10-05.yaml")]
    assert "was claimed by lane-a" in items[0]["note"]
