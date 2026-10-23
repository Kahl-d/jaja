"""Charter section 7 on the morning cards and the day queue: no cap on applying (the tier-3 base listings below the card cut
fold into ONE mass batch card), never a second application (a listing already applied is skipped), and one yes is
enough (a yes on a mass card queues kind mass, and --yes-quote records that yes in one approval file)."""
from __future__ import annotations

import json
from pathlib import Path

from disc_harness import *  # noqa: F401,F403
from test_sweep_cards_queue import T0, _listing, _sweep_doc, cards, dq

import frontmatter as kfm  # the kernel's (system/scripts), on sys.path through js_common


def _app(root: Path, slug: str, job: str, stage: str, company: str = "Globex", title: str = "AI Engineer") -> None:
    path = root / "applications" / slug / "application.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(kfm.dump({"id": slug, "job": f"[[{job}]]", "company": company,
                              "company_slug": company.lower(), "title": title, "stage": stage}, "# app\n"))


def _many(n: int) -> list[dict]:
    return [_listing(f"co{i:03d}", 50, "Remote US", company=f"Co{i:03d}", slug=f"co{i:03d}") for i in range(n)]


def test_base_listings_below_the_cut_fold_into_one_mass_batch_card(root):
    doc = _sweep_doc({"listings": _many(8) + [_listing("hot", 90, "Remote US")]})
    res = cards.build(root, doc, max_listings=3)
    batch = [c for c in res["cards"] if c["action"] == cards.MASS_BATCH]
    assert len(batch) == 1
    card = batch[0]
    assert card["mode"] == "quick" and card["effort"] == "E2" and len(card["items"]) == 6   # hot, co000, co001 carded
    assert card["title"] == "[mass batch] 6 base-resume applications on employer sites"
    assert "yes = submit all 6 base-resume applications on employer sites" in card["why"][0]
    assert [i["job"] for i in card["items"]] == [f"co{i:03d}" for i in range(2, 8)]
    quick = [c["n"] for c in res["cards"] if c["mode"] == "quick"]
    assert card["n"] == max(quick) and card["n"] < min(c["n"] for c in res["cards"] if c["mode"] == "deep")
    assert res["counted"]["listings below the cut"] == 0                 # nothing dropped: all folded
    md = cards.render(res)
    assert f"### {card['n']} · [mass batch] 6 base-resume applications" in md
    assert "  - [greenhouse] Co003 · AI Engineer · Remote US · `job:co003`" in md


def test_a_tailored_listing_below_the_cut_is_still_only_counted(root):
    doc = _sweep_doc({"listings": [_listing("a", 60, "Remote US"), _listing("big", 40, "Remote US", h1b=150)]})
    res = cards.build(root, doc, max_listings=1)
    assert res["counted"]["listings below the cut"] == 1
    assert not [c for c in res["cards"] if c["action"] == cards.MASS_BATCH]


def test_no_fold_no_batch_card(root):
    res = cards.build(root, _sweep_doc({"listings": _many(2)}), max_listings=60)
    assert not [c for c in res["cards"] if c["action"] == cards.MASS_BATCH]


def test_a_listing_already_applied_is_never_carded(root):
    _app(root, "globex-ai-engineer-202610", "globex-1", "applied")
    _app(root, "northwind-ml-engineer-202610", "northwind-old", "screen", company="Northwind", title="ML Engineer")
    _app(root, "acme-ai-engineer-202610", "acme-1", "tailored", company="Acme")
    doc = _sweep_doc({"listings": [
        _listing("globex-1", 60, "Remote US", company="Globex", slug="globex"),          # same job id
        {**_listing("northwind-new", 60, "Remote US", company="Northwind", slug="northwind"), "title": "ML Engineer"},  # same pair
        _listing("acme-1", 60, "Remote US", company="Acme", slug="acme")]})           # tailored: not applied yet
    res = cards.build(root, doc)
    keys = [c["item_key"] for c in res["cards"]]
    assert keys == ["job:acme-1"] and res["counted"]["already applied"] == 2


def _files(root: Path, out: Path, answer: str, n: int = 5, cut: int = 2) -> dict:
    res = cards.build(root, _sweep_doc({"listings": _many(n)}), max_listings=cut)
    out.mkdir(parents=True, exist_ok=True)
    (out / "cards-2026-10-06.json").write_text(json.dumps(res), encoding="utf-8")
    (out / "cards-2026-10-06.md").write_text(cards.render(res).replace("decisions:", f"decisions: {answer}"),
                                              encoding="utf-8")
    return res


def test_a_yes_on_the_batch_card_queues_every_listing_as_mass(root):
    out = root / "state" / "recruiter"
    res = _files(root, out, "yes 3")                                     # cards 1-2 base cards, 3 the batch
    assert res["cards"][2]["action"] == cards.MASS_BATCH
    added = dq.build(out, "2026-10-06", T0)["added"]
    items = dq.load(out / "queue-2026-10-06.yaml")["items"]
    assert added == 3 and [i["kind"] for i in items] == ["mass"] * 3
    assert [i["links"]["job"] for i in items] == ["co002", "co003", "co004"]
    assert all(i["approval"] is None and i["card"] == res["cards"][2]["id"] for i in items)
    assert items[0]["title"] == "[mass] [greenhouse] Co002 · AI Engineer · Remote US"
    claimed = dq.claim(out, "2026-10-06", T0, "lane-apply", "mass")
    assert claimed["links"]["job"] == "co002" and claimed["claimed_by"] == "lane-apply"
    assert dq.build(out, "2026-10-06", T0)["added"] == 0                 # idempotent


def test_the_yes_quote_records_one_approval_for_the_mass_items(root, os_root):
    out = root / "state" / "recruiter"
    _files(root, out, "yes 1-3")
    dq.build(out, "2026-10-06", T0, yes_quote="yes 1-3", via="chat", session="s-1", os_root=os_root)
    items = dq.load(out / "queue-2026-10-06.yaml")["items"]
    assert len(items) == 5 and {i["approval"] for i in items} == {"A-2026-0001"}
    meta, body = kfm.load(os_root / "work" / "approvals" / "A-2026-0001.md")
    assert meta["status"] == "approved" and meta["action"] == "submit-form"
    assert meta["decided_quote"] == "yes 1-3" and meta["decided_session"] == "s-1" and meta["decided_via"] == "chat"
    assert meta["approved_items"] == ["co000", "co001", "co002", "co003", "co004"]
    assert "co004 · [greenhouse] Co004 · AI Engineer · Remote US · https://example.com/co004" in body


def test_the_cli_queues_mass_without_a_flag(root):
    out = root / "state" / "recruiter"
    _files(root, out, "yes 1")
    assert dq.main(["--out", str(out), "--date", "2026-10-06", "--now", "2026-10-06T15:00:00Z", "build"]) == 0
    assert [i["kind"] for i in dq.load(out / "queue-2026-10-06.yaml")["items"]] == ["mass"]


def test_interview_and_assessment_mail_cards_queue_as_prep_and_the_prep_lane_claims_them(root):
    out = root / "state" / "recruiter"
    doc = _sweep_doc({"mail": [
        {"key": "mail:i", "kind": "mail", "label": "interview_invite", "from": "Initech", "subject": "Round 2"},
        {"key": "mail:s", "kind": "mail", "label": "interview_scheduling", "from": "Ann Lee", "subject": "Times"},
        {"key": "mail:a", "kind": "mail", "label": "ats_assessment", "from": "HackerRank", "subject": "Test"},
        {"key": "mail:r", "kind": "mail", "label": "recruiter_outreach", "from": "Bo Chen", "subject": "Hi"}]})
    res = cards.build(root, doc)
    out.mkdir(parents=True, exist_ok=True)
    (out / "cards-2026-10-06.json").write_text(json.dumps(res), encoding="utf-8")
    (out / "cards-2026-10-06.md").write_text(cards.render(res).replace("decisions:", "decisions: yes 1-4"),
                                              encoding="utf-8")
    dq.build(out, "2026-10-06", T0)
    kinds = {i["item_key"]: i["kind"] for i in dq.load(out / "queue-2026-10-06.yaml")["items"]}
    assert kinds == {"mail:i": "prep", "mail:s": "prep", "mail:a": "prep"}      # a plain reply is not queued
    assert dq.kind_for({"action": "prep", "mode": "quick"}) == "prep"
    got = dq.main(["--out", str(out), "--date", "2026-10-06", "--now", "2026-10-06T15:00:00Z", "claim",
                   "--lane", "lane-prep", "--kind", "prep"])
    assert got == 0
    claimed = [i for i in dq.load(out / "queue-2026-10-06.yaml")["items"] if i["status"] == "claimed"]
    assert len(claimed) == 1 and claimed[0]["claimed_by"] == "lane-prep" and claimed[0]["kind"] == "prep"


def test_build_reads_every_card_batch_of_the_day_with_its_own_decisions(root):
    out = root / "state" / "recruiter"
    first = _files(root, out, "yes 1", n=2, cut=60)                      # cards 1-2, the owner said yes to 1
    later = cards.build(root, _sweep_doc({"listings": [_listing("late1", 50, "Remote US"),
                                                       _listing("late2", 50, "Remote US")]}))
    for i, c in enumerate(later["cards"]):                               # cards.py numbers a later batch on
        c["n"] = len(first["cards"]) + 1 + i
        c["id"] = f"C-2026-10-06-{c['n']:03d}"
    (out / "cards-2026-10-06-b2.json").write_text(json.dumps(later), encoding="utf-8")
    md = "decisions: yes 4\n" + "".join(f"### {c['n']} · {c['title']}\n- decision:\n\n" for c in later["cards"])
    (out / "cards-2026-10-06-b2.md").write_text(md, encoding="utf-8")
    (out / "cards-2026-10-06-bad.json").write_text("{}", encoding="utf-8")   # not a batch name: ignored
    assert dq.build(out, "2026-10-06", T0)["added"] == 2
    jobs = [i["links"]["job"] for i in dq.load(out / "queue-2026-10-06.yaml")["items"]]
    assert jobs == ["co000", "late2"]                                    # "yes 1" never reaches the b2 file's card 3
