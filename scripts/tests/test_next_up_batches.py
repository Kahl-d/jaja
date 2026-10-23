"""NEXT-UP recalculates importance on every run: age decay, a warmth boost for people and due items, a STALE
bucket FOCUS never claims first, hunt findings (queue leads, hunt prospects, hunt jobs) ranked with their age, and the
dashboard's "FOCUS next" block from the same function."""
from __future__ import annotations

from datetime import datetime, timezone

from disc_harness import *  # noqa: F401,F403

import agency_status  # noqa: E402
import day_queue  # noqa: E402
import fetch_ats as fa  # noqa: E402
import next_up as nu  # noqa: E402
import pytest  # noqa: E402
from js_common import write_record  # noqa: E402
from test_next_up import JD, NOW, _app, _note, _rows, ship, world  # noqa: E402,F401  (ship: the autouse apply_pack stub)


def job(root, jid, company, slug, posted="2026-10-02T17:00:00Z", last_seen=None, first_seen=None, **over):
    rec = fa.make_record({"name": company, "slug": slug}, title="AI Engineer", source="ashby",
                         source_url=f"https://jobs.ashbyhq.com/{slug}/{jid}", apply_url=None, ats_vendor="ashby",
                         posted_at=posted, location="San Francisco, CA", remote="onsite", comp=(None, None, None),
                         employment_type=None, jd_text=JD, now=NOW)
    rec.update({"id": jid, "status": "discovered", "fit_score": 4.3, "gate": "pass", "sponsorship": "yes"})
    if last_seen:
        rec["last_seen"] = last_seen
    if first_seen:
        rec["first_seen"] = first_seen
    rec.update(over)
    fa.save_job(root, rec)


def role(root, name, **kw):
    job(root, f"{name}-1", name.title(), name, **kw)
    _app(root, f"{name}-ai-202610", name.title(), f"{name}-1")


def queue(root, day, items):
    out = root / "state" / "recruiter"
    for kind, title, status in items:
        it = day_queue.add(out, day, NOW, kind, title, "E2")
        if status != "open":
            day_queue.mutate(out, day, NOW, lambda its, i=it: next(x for x in its if x["id"] == i["id"]).update(status=status))


def test_age_decay_is_full_then_linear_then_floored():
    assert nu.age_decay(None) == 1.0 and nu.age_decay(3) == 1.0 and nu.age_decay(nu.FULL_WEIGHT_DAYS) == 1.0
    assert nu.age_decay(14) == pytest.approx(0.65, abs=1e-3)
    assert nu.age_decay(nu.DECAY_END_DAYS) == nu.DECAY_FLOOR == nu.age_decay(200)
    assert nu.age_decay(10) > nu.age_decay(15) > nu.age_decay(20)


def test_an_older_posting_ranks_below_a_fresh_one_and_shows_its_age(tmp_path):
    root = world(tmp_path)
    role(root, "fresh", posted="2026-10-03T17:00:00Z")
    role(root, "aged", posted="2026-09-18T17:00:00Z")                        # 16 days
    rows = _rows(nu.build(root, NOW))
    assert rows["Aged"]["decay"] < 0.6 < rows["Fresh"]["decay"] == 1.0
    assert rows["Aged"]["per_hour"] < rows["Fresh"]["per_hour"]
    assert round(rows["Aged"]["age_days"]) == 16 and rows["Aged"]["age_basis"] == "posted"
    assert "(16 d)" in nu.render(nu.build(root, NOW), "2026-10-04")


def test_a_role_older_than_30_days_goes_to_stale_not_the_list(tmp_path):
    root = world(tmp_path)
    role(root, "ancient", posted="2026-08-20T17:00:00Z")
    res = nu.build(root, NOW)
    assert "Ancient" not in _rows(res) and [r["company"] for r in res["stale"]] == ["Ancient"]
    assert "older than 30 d" in res["stale"][0]["stale"] and res["counts"]["STALE"] == 1
    text = nu.render(res, "2026-10-04")
    assert "## STALE" in text and "S1 | Ancient" in text
    assert nu.main(["--root", str(root), "--now", NOW.isoformat(), "--write"]) == 0
    assert "STALE" not in (agency_status.queue(root) or {})                  # the status counts read only the list


def test_a_posting_the_scans_no_longer_see_is_stale_even_when_young(tmp_path):
    root = world(tmp_path)
    role(root, "gone", last_seen="2026-09-20T00:00:00Z")                      # 14 days since the scans saw it
    role(root, "live", last_seen="2026-10-04T10:00:00Z")
    res = nu.build(root, NOW)
    assert [r["company"] for r in res["stale"]] == ["Gone"] and "Live" in _rows(res)
    assert "no longer live" in res["stale"][0]["stale"]


def test_a_person_live_on_the_role_keeps_it_off_the_stale_list_until_90_days(tmp_path):
    root = world(tmp_path)
    job(root, "old-1", "Oldnote", "oldnote", posted="2026-08-20T17:00:00Z")      # 45 days, a note waits
    _app(root, "oldnote-ai-202610", "Oldnote", "old-1")
    _note(root, "O-7", "p-old", "oldnote")
    job(root, "dead-1", "Deadnote", "deadnote", posted="2026-06-01T17:00:00Z")   # 125 days, a note waits
    _app(root, "deadnote-ai-202610", "Deadnote", "dead-1")
    _note(root, "O-8", "p-dead", "deadnote")
    res = nu.build(root, NOW)
    assert "Oldnote" in _rows(res) and [r["company"] for r in res["stale"]] == ["Deadnote"]
    assert "even with a person" in res["stale"][0]["stale"]


def test_a_hiring_post_a_reply_and_a_due_touch_raise_the_rate(tmp_path):
    root = world(tmp_path)
    role(root, "plain")
    role(root, "referred")
    job(root, "ref-job", "Refco", "refco", source="prospect")
    _app(root, "refco-ai-202610", "Refco", "ref-job")
    role(root, "replied")
    write_record(root / "prospects" / "pat-r.md", {"id": "pat-r", "name": "Pat", "company_slug": "replied", "status": "active",
                                                   "last_inbound_at": "2026-10-03T09:00:00Z"}, "# p\n")
    role(root, "dueco")
    write_record(root / "prospects" / "pat-d.md", {"id": "pat-d", "name": "Dee", "company_slug": "dueco", "status": "active",
                                                   "next_touch_at": "2026-10-04"}, "# p\n")
    rows = _rows(nu.build(root, NOW))
    plain = rows["Plain"]["per_hour"]
    assert rows["Refco"]["boost"] == nu.PERSON_BOOST and rows["Refco"]["per_hour"] == pytest.approx(plain * nu.PERSON_BOOST, rel=0.02)
    assert rows["Replied"]["boost"] == nu.REPLY_BOOST and "reply from pat-r" in rows["Replied"]["why"]
    assert rows["Dueco"]["boost"] == nu.DUE_BOOST and rows["Plain"]["boost"] == 1.0
    assert rows["Replied"]["expected"] == rows["Plain"]["expected"]           # the chance column stays honest, unboosted


def test_a_prospect_who_was_answered_last_is_not_a_reply(tmp_path):
    root = world(tmp_path)
    role(root, "quiet")
    write_record(root / "prospects" / "pat-q.md", {"id": "pat-q", "name": "Q", "company_slug": "quiet", "status": "active",
                                                   "last_inbound_at": "2026-10-03T09:00:00Z",
                                                   "last_outbound_at": "2026-10-03T12:00:00Z"}, "# p\n")
    assert _rows(nu.build(root, NOW))["Quiet"]["boost"] == 1.0


def test_the_trackers_own_next_action_date_is_not_a_deadline(tmp_path):
    root = world(tmp_path)
    role(root, "auto")
    path = root / "applications" / "auto-ai-202610" / "application.md"
    path.write_text(path.read_text().replace("stage: fit_checked", "stage: fit_checked\nnext_action: tailor the resume (resume-tailor)\n"
                                             "next_action_at: '2026-10-01'"))
    role(root, "hand")
    path = root / "applications" / "hand-ai-202610" / "application.md"
    path.write_text(path.read_text().replace("stage: fit_checked", "stage: fit_checked\nnext_action: call the recruiter back\n"
                                             "next_action_at: '2026-10-03'"))
    rows = _rows(nu.build(root, NOW))
    assert rows["Auto"]["boost"] == 1.0 and rows["Hand"]["boost"] == nu.DUE_BOOST


def test_an_applied_role_is_never_a_row_or_stale(tmp_path):
    root = world(tmp_path)
    job(root, "done-1", "Done", "done", posted="2026-06-01T17:00:00Z")
    _app(root, "done-ai-202610", "Done", "done-1", stage="applied")
    res = nu.build(root, NOW)
    assert "Done" not in _rows(res) and all(r["company"] != "Done" for r in res["stale"])


def test_a_hunt_job_with_no_folder_is_ranked_with_its_age(tmp_path):
    root = world(tmp_path)
    job(root, "hunted-1", "Hunted", "hunted", source="hunt", posted="2026-10-01T17:00:00Z")
    job(root, "hunted-2", "Huntgone", "huntgone", source="hunt", status="skipped")
    job(root, "hunted-3", "Huntold", "huntold", source="hunt", posted="2026-07-01T17:00:00Z")
    res = nu.build(root, NOW)
    row = _rows(res)["Hunted"]
    assert row["state"] in ("JOB", "SCORE") and round(row["age_days"]) == 3 and "Huntgone" not in _rows(res)
    assert [r["company"] for r in res["stale"]] == ["Huntold"]


def test_queue_leads_are_ranked_with_age_and_done_blocked_held_leave(tmp_path):
    root = world(tmp_path)
    queue(root, "2026-10-01", [("focused", "F1 · Ada Lovelace via Bob", "open"), ("posts", "Old post lead", "open"),
                               ("research", "not a lead kind", "open")])
    queue(root, "2026-10-04", [("posts", "Cy Young / CTO / Acme AI", "open"), ("hunt", "Hunt find three", "claimed"),
                               ("focused", "Finished one", "done"), ("focused", "Skipped one", "blocked"),
                               ("focused", "HELD: somebody", "open")])
    res = nu.build(root, NOW)
    leads = {r["title"]: r for r in res["rows"] if r["state"] == "LEAD"}
    assert set(leads) == {"F1 / Ada Lovelace via Bob", "Old post lead", "Cy Young / CTO / Acme AI", "Hunt find three"}
    assert not res["stale"]
    assert int(leads["F1 / Ada Lovelace via Bob"]["age_days"]) == 3 and leads["Cy Young / CTO / Acme AI"]["age_days"] < 1
    assert res["counts"]["LEAD"] == len(leads)
    assert leads["F1 / Ada Lovelace via Bob"]["per_hour"] > leads["Cy Young / CTO / Acme AI"]["per_hour"] * 0.99  # focused prior 0.8 > posts 0.4
    text = nu.render(res, "2026-10-04")
    assert "LEAD `Q-2026-10-04" in text and "3 d (added)" in text


def test_a_carried_lead_is_one_row_with_its_first_day_and_a_stale_lead_is_parked(tmp_path):
    root = world(tmp_path)
    queue(root, "2026-09-20", [("posts", "Same lead carried", "open")])
    queue(root, "2026-10-04", [("posts", "Same lead carried", "open"), ("posts", "Lead carried since August", "open"),
                               ("posts", "Claimed since August", "claimed")])
    out = root / "state" / "recruiter"
    for title in ("Lead carried since August", "Claimed since August"):
        day_queue.mutate(out, "2026-10-04", NOW, lambda its, t=title: next(x for x in its if x["title"] == t).update(
            carried_from="Q-2026-08-01-03"))
    res = nu.build(root, datetime(2026, 10, 4, 17, tzinfo=timezone.utc))
    live = {r["title"]: r for r in res["rows"] if r["state"] == "LEAD"}
    assert int(live["Same lead carried"]["age_days"]) == 14 and list(live).count("Same lead carried") == 1
    assert "Claimed since August" in live                              # a lane is working it: not parked
    assert [r["title"] for r in res["stale"]] == ["Lead carried since August"]


def test_a_hunt_prospect_is_a_lead_with_its_age(tmp_path):
    root = world(tmp_path)
    write_record(root / "prospects" / "ned-hunt.md", {"id": "ned-hunt", "name": "Ned Hunt", "company": "Huntco", "source": "hunt",
                                                      "status": "new", "created": "2026-10-01"}, "# p\n")
    write_record(root / "prospects" / "not-hunt.md", {"id": "not-hunt", "name": "No", "company": "Other", "source": "outbound",
                                                      "status": "new"}, "# p\n")
    row = [r for r in nu.build(root, NOW)["rows"] if r["state"] == "LEAD"]
    assert [r["key"] for r in row] == ["prospect:ned-hunt"] and int(row[0]["age_days"]) == 3 and "Ned Hunt" in row[0]["title"]


def test_focus_next_is_the_top_n_without_blocked_or_stale(tmp_path):
    root = world(tmp_path)
    role(root, "ancient", posted="2026-08-01T17:00:00Z")
    res = nu.build(root, NOW)
    top = nu.focus_next(res, 3)
    assert [x["rank"] for x in top] == [1, 2, 3] and all(x["state"] != "BLOCKED" for x in top)
    assert not any("Ancient" in x["role"] or "Delta" in x["role"] for x in nu.focus_next(res, 50))
    assert set(top[0]) >= {"rank", "role", "why", "age", "state", "per_hour"}
    assert nu.focus_lines(top)[0].startswith(" 1. [")


def test_the_focus_flag_prints_the_block_and_write_still_writes(tmp_path, capsys):
    root = world(tmp_path)
    assert nu.main(["--root", str(root), "--now", NOW.isoformat(), "--write", "--focus", "2"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("FOCUS next (top 2 of ") and "age " in out and (root / "hub" / "NEXT-UP.md").is_file()


def test_the_dashboard_prints_the_focus_block_from_the_same_function(tmp_path):
    import dashboard
    root = world(tmp_path)
    (root / "state").mkdir(exist_ok=True)
    os_root = tmp_path / "os"
    os_root.mkdir()
    data = dashboard.build(root, os_root, NOW, focus=True)
    text = dashboard.render(data)
    assert "## FOCUS next" in text and "1. **PEOPLE FIRST** Zeta" in text and "age " in text
    assert "next_up.py --write --focus" in text
    assert "focus_next" not in dashboard.build(root, os_root, NOW)             # the fast path (API, tests) stays fast
    assert "FOCUS next" not in dashboard.render(dashboard.build(root, os_root, NOW))


def test_the_dashboard_says_why_the_block_is_missing(tmp_path, monkeypatch):
    import dashboard
    import next_up
    root = world(tmp_path)
    monkeypatch.setattr(next_up, "focus_for", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    text = dashboard.render({**dashboard.build(root, tmp_path, NOW), "focus_next": dashboard.focus_next(root, NOW)})
    assert "Not computed: RuntimeError: boom" in text
