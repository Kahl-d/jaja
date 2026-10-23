"""dashboard/cockpit.py: the cockpit shows what decides the next hour, conservatively."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from js_common import write_record  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard"))
import cockpit  # noqa: E402

NOW = datetime(2026, 10, 4, 17, 0, tzinfo=timezone.utc)          # Sun 10:00 PT
RATES = {"warm": 0.25, "cold": 0.03, "warm_k": 2, "warm_n": 2, "cold_k": 0, "cold_n": 25}


def _snap():
    return {"generated_at": "2026-10-04T16:00:00Z", "clock": {"line": "Job clock: day 2 of 90"},
            "overview": {"funnel": [{"step": "applied", "count": 27}], "response_by_via": {}},
            "follow_ups": [
                {"kind": "portal-message", "who": "joel@recruit.example.com", "title": "Re: Founding Engineer at Initech",
                 "due": "2026-10-02", "days_overdue": 2},
                {"kind": "portal-message", "who": "copilot@platform.example.com", "name": "Alice from HireBoard", "title": "Alice flagged your profile",
                 "due": "2026-09-07", "days_overdue": 27},
                {"kind": "portal-message", "who": "copilot@platform.example.com", "name": "Alice from HireBoard", "title": "Alice flagged your profile",
                 "due": "2026-09-09", "days_overdue": 25},
                {"kind": "application", "company": "Acme", "title": "AI Engineer", "stage": "applied", "due": "2026-10-04",
                 "days_overdue": 0}],
            "crm": {"kpis": [{"key": "interviews", "value": 1, "target": 2, "last_week": 0, "items": []}],
                    "touches": [{"label": "sent", "day": "2026-09-30"}, {"label": "sent", "day": "2026-09-20"},
                                {"label": "received", "day": "2026-10-01"}],
                    "band48": {"calendar_read": True, "calendar_other": 1, "rounds_undated": [{"company": "R", "stage": "screen"}],
                               "rounds": [{"when": "2026-10-05 11:45", "day": "2026-10-05", "company": "Northwind",
                                           "slug": "northwind-202609", "pack": True, "title": None},
                                          {"when": "2026-10-09 11:45", "day": "2026-10-09", "company": "Later",
                                           "slug": "later-202609", "pack": False, "title": None}],
                               "approval_groups": [{"label": "LinkedIn", "ages": [{"items": [
                                   {"id": "A-1", "expires": "2026-10-09"}, {"id": "A-2", "expires": "2026-10-09"},
                                   {"id": "A-3", "expires": "2026-11-01"}]}]}]},
                    "pipeline": [{"slug": "a", "company": "Acme", "stage": "applied", "days_in_stage": 3},
                                 {"slug": "t", "company": "Northwind", "stage": "screen", "days_in_stage": 5},
                                 {"slug": "z", "company": "Zed", "stage": "fit_checked", "days_in_stage": 1}]}}


def _notes(tmp_path):
    js = tmp_path / "js"
    for d in ("prospects", "outbox"):
        (js / d).mkdir(parents=True)
    write_record(js / "prospects" / "pat.md", {"id": "pat", "name": "Pat Lee", "company": "Globex"}, "# p\n")
    for oid, status, kind, appr in (("O-1", "approved", "intro", "A-7"), ("O-2", "approved", "intro", "A-7"),
                                    ("O-3", "approved", "reply", "A-9"), ("O-4", "sent", "intro", "A-6"),
                                    ("O-5", "pending", "intro", "A-1")):
        write_record(js / "outbox" / f"{oid}.md", {"id": oid, "prospect": "pat", "channel": "linkedin-connect",
                                                  "kind": kind, "status": status, "approval": appr,
                                                  "created": "2026-10-01"}, "x\n")
    return js


MAIL = [{"kind": "portal-message", "who": "joel@recruit.example.com", "title": "Re: Founding Engineer at Initech",
         "due": "2026-10-02", "days_overdue": 2, "thread": "t1"},
        {"kind": "portal-message", "who": "copilot@platform.example.com", "name": "Alice from HireBoard", "title": "Alice flagged your profile",
         "due": "2026-09-07", "days_overdue": 27, "thread": "t2"},
        {"kind": "portal-message", "who": "copilot@platform.example.com", "name": "Alice from HireBoard", "title": "Alice flagged your profile",
         "due": "2026-09-09", "days_overdue": 25, "thread": "t3"}]


def _approvals(tmp_path):
    folder = tmp_path / "work" / "approvals"
    folder.mkdir(parents=True)
    for aid, status, created in (("A-1", "pending", "2026-10-02"), ("A-2", "pending", "2026-10-02"),
                                 ("A-7", "approved", "2026-09-30"), ("A-6", "approved", "2026-09-30"),
                                 ("A-5", "approved", "2026-09-30"), ("A-9", "approved", "2026-09-01")):
        write_record(folder / f"{aid}.md", {"id": aid, "status": status, "created": created}, "x\n")


def _build(tmp_path, nu=None, connectors=(), snap=None):
    js = _notes(tmp_path)
    _approvals(tmp_path)
    import follow_ups
    import interviews
    real, real_fu = interviews.rates, follow_ups.collect
    interviews.rates = lambda root, now: RATES
    follow_ups.collect = lambda root, today: {"due": MAIL}
    try:
        return cockpit.build(js, tmp_path, NOW, snap or _snap(), nu=nu or {"rows": [], "applying": "paused", "counts": {}},
                             connectors=list(connectors) if connectors is not None else None)
    finally:
        interviews.rates, follow_ups.collect = real, real_fu


def test_the_wilson_range_is_honest_about_small_samples():
    assert cockpit.wilson(2, 2) == (0.342, 1.0)
    assert cockpit.wilson(0, 25) == (0.0, 0.133)
    assert cockpit.wilson(0, 0) is None
    assert cockpit.channel(2, 2)["enough"] is False and cockpit.channel(0, 25)["enough"] is True


def test_a_round_within_72_hours_leads_then_a_fresh_reply_then_notes(tmp_path):
    c = _build(tmp_path)
    kinds = [a["kind"] for a in c["next"]]
    assert kinds[0] == "prep" and "Northwind" in c["next"][0]["what"]
    assert kinds[1] == "reply" and "Initech" in c["next"][1]["what"]
    assert not any("Later" in a["what"] for a in c["next"] + c["more"] if a["kind"] == "prep")   # 10-09 is past 72 h


def test_two_copies_of_one_pitch_are_one_action_and_a_stale_thread_cools(tmp_path):
    c = _build(tmp_path)
    replies = [a for a in c["next"] + c["more"] if a["kind"] == "reply"]
    assert len(replies) == 2
    fresh, stale = sorted(replies, key=lambda a: -a["chance"])
    assert fresh["chance"] > stale["chance"]


def test_only_unsent_first_contact_notes_become_send_actions_grouped_by_company(tmp_path):
    c = _build(tmp_path)
    acts = c["next"] + c["more"]
    sends = [a for a in acts if a["kind"] == "send"]
    assert len(sends) == 1 and "2 approved notes" in sends[0]["what"] and "O-3" not in sends[0]["why"]
    assert [a["kind"] for a in acts].count("decide") == 1


def test_a_people_first_company_gets_one_action_and_no_duplicate_note(tmp_path):
    rows = [{"state": "PEOPLE FIRST", "company": "Globex", "title": t, "why": "send O-1 first", "expected": 0.25,
             "per_hour": 0.9, "slug": f"g{i}", "job": f"g{i}"} for i, t in enumerate(("A", "B"))]
    c = _build(tmp_path, nu={"rows": rows, "applying": "paused", "counts": {}})
    acts = c["next"] + c["more"]
    assert [a["kind"] for a in acts].count("people first") == 1
    assert not [a for a in acts if a["kind"] in ("send", "decide")]          # Globex's notes live in its people-first row


def test_week_works_coming_pipeline_and_volume(tmp_path):
    c = _build(tmp_path)
    assert c["week"]["warm_sent"] == 1 and c["week"]["since"] == "2026-09-28"
    assert c["week"]["waiting"] == {"approved_notes": 2, "pending_notes": 1, "replies_owed": 2}   # deduplicated (F3)
    assert c["works"]["applied"] == 27 and c["works"]["warm"]["low"] == 0.342
    assert sorted((e["when"], e["what"]) for e in c["coming"]["expiring"]) == [
        ("2026-10-07", "1 approval approved, not sent yet (A-7)"),          # A-6 all sent, A-5 no outbox record
        ("2026-10-09", "2 approvals waiting on your decision (A-1 to A-2)")]          # approved ones expire too (F1)
    assert c["coming"]["hidden"] == 1 and c["coming"]["undated"][0]["company"] == "R"
    assert [d["company"] for d in c["pipeline"]] == ["Northwind", "Acme"]          # rounds first; pre-applied never


def test_the_machine_line_reads_the_receipts(tmp_path):
    runs = tmp_path / "logs" / "runs"
    (runs / "js-hunt-boards").mkdir(parents=True)
    (runs / "watchdog").mkdir(parents=True)
    (runs / "js-hunt-boards" / "2026-10-04.md").write_text(
        "## Run 2026-10-04T07:00:00-07:00 · started\n- x\n\n## Run 2026-10-04T07:20:00-07:00 · ok\n- y\n")
    (runs / "watchdog" / "2026-10-04.md").write_text("## Run x · ok\n- state OK · 19 enabled\n")
    m = cockpit.machine(tmp_path, [{"name": "x-credits", "state": "red"}], NOW.date())
    assert m["hunt"] == {"at": "2026-10-04T07:20:00-07:00", "status": "ok"}
    assert m["watchdog"] == "OK · 19 enabled" and m["red"] == ["x-credits"]



def test_a_reply_opens_its_thread_and_every_file_link_is_relative_to_the_vault(tmp_path):
    rows = [{"state": "READY", "company": "Acme", "title": "AI", "why": "w", "expected": 0.03, "per_hour": 0.06,
             "slug": "acme-202610", "job": "acme-1"}]
    c = _build(tmp_path, nu={"rows": rows, "applying": "paused", "counts": {}})
    acts = c["next"] + c["more"]
    initech = next(a for a in acts if "Initech" in a["what"])
    assert initech["link"] == "https://mail.google.com/mail/u/0/#all/t1" and initech["open"] is None
    assert next(a for a in acts if a["kind"] == "apply")["open"] == \
        "agency-data/applications/acme-202610/application.md"
    assert next(a for a in acts if a["kind"] == "decide")["open"].startswith("work/approvals/")
    assert c["pipeline"][0]["open"].startswith("agency-data/applications/")


def test_a_fresh_reply_outranks_a_note_and_a_note_outranks_a_cold_follow_up(tmp_path):
    acts = [a for a in (lambda c: c["next"] + c["more"])(_build(tmp_path)) if a["kind"] != "prep"]
    kinds = [a["kind"] for a in acts]
    assert kinds.index("reply") < kinds.index("send") < kinds.index("follow up")


def test_a_block_that_fails_is_named_and_the_rest_still_builds(tmp_path, monkeypatch):
    monkeypatch.setattr(cockpit, "pipeline", lambda snap: 1 / 0)
    c = _build(tmp_path, connectors=None)
    assert c["pipeline"] == [] and c["next"]
    assert any("pipeline could not be read" in e for e in c["errors"])
    assert any("connectors could not be read" in e for e in c["errors"])


def test_one_approved_note_ranks_below_a_fresh_reply():
    """Spec section 3: a note is one step toward a referral (x0.2); a fresh reply to a person who wrote ranks above it.
    At x0.5 a single note would outrank the reply (1.25 per hour against 1.0)."""
    snap = {"follow_ups": []}
    note = [{"id": "O-1", "status": "approved", "channel": "linkedin-connect", "to": "Pat", "company": "Globex",
             "created": "2026-10-01", "path": "outbox/O-1.md"}]
    reply = [cockpit._act("reply", "Founding role", "x, waiting 1 days", RATES["warm"], cockpit.HOURS["reply"], None, "2026-10-03")]
    acts = cockpit.actions(snap, None, note, RATES, NOW, reply)
    assert [a["kind"] for a in acts] == ["reply", "send"]


def test_tokens_due_within_seven_days_are_one_line_and_unread_connectors_are_named(tmp_path):
    conns = [{"name": "gmail-read-token", "state": "green", "summary": "read-only token good until 2026-10-09"},
             {"name": "gmail-send-token", "state": "green", "summary": "gmail.send token good until 2026-10-09"},
             {"name": "x-credits", "state": "red", "summary": "402"}]
    c = _build(tmp_path, connectors=conns)
    assert ("2026-10-09", "gmail-read, gmail-send tokens (re-authorize; CONNECTORS.md)") in \
        [(e["when"], e["what"]) for e in c["coming"]["expiring"]]
    assert c["coming"]["connectors_read"] is True and c["machine"]["red"] == ["x-credits"]
    c2 = _build(tmp_path / "b", connectors=None)
    assert c2["coming"]["connectors_read"] is False and c2["machine"]["connectors_read"] is False


def test_equal_chances_go_to_the_older_item_first():
    old = cockpit._act("reply", "Older", "w", 0.25, 0.25, None, "2026-09-30")
    new = cockpit._act("reply", "Newer", "w", 0.25, 0.25, None, "2026-10-03")
    acts = cockpit.actions({"follow_ups": []}, None, [], RATES, NOW, [new, old])
    assert [a["what"] for a in acts] == ["Older", "Newer"]
