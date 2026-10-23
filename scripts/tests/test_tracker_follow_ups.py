"""tracker.py follow-up schedule: one follow-up 5 business days after the last company activity: find a person at the company.
Our own follow-ups are not activity, so each offset counts from the same last activity."""
from __future__ import annotations

from datetime import datetime, timezone

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import tracker as tr  # noqa: E402

APPLIED = "2026-09-25T17:00:00Z"            # a Friday


def ev(at, type_):
    return tr.Event(at, type_, "manual", 1.0, "e")


def nxt(events, now="2026-09-28T12:00:00Z"):
    when = datetime.fromisoformat(now.replace("Z", "+00:00"))
    return tr.next_action_for(tr.project(events, when), when)


def test_one_follow_up_a_person_found_at_five_business_days():
    assert tr.FOLLOW_UP_OFFSETS == (5,)                       # charter §6: find a person, once


def test_first_follow_up_is_five_business_days_after_applying():
    assert nxt([ev(APPLIED, "applied")]) == (tr.FOLLOW_UP, "2026-10-02")
    assert "find a person" in tr.FOLLOW_UP


def test_after_the_person_step_no_further_follow_up():
    one = [ev(APPLIED, "applied"), ev("2026-10-02T16:00:00Z", "follow_up")]
    assert nxt(one, "2026-10-03T12:00:00Z") == (tr.FOLLOW_UPS_DONE, None)
    assert tr.FOLLOW_UPS_DONE in tr.TRACKER_ACTIONS


def test_old_three_nudge_wording_is_still_the_tracker_s_to_rewrite():
    assert "follow up if no reply (messenger)" in tr.TRACKER_ACTIONS
    assert "no further follow-up (three sent, never a fourth)" in tr.TRACKER_ACTIONS


def test_a_reply_restarts_the_schedule():
    events = [ev(APPLIED, "applied"), ev("2026-10-02T16:00:00Z", "follow_up"),
              ev("2026-10-05T16:00:00Z", "email_in")]                               # Monday: their reply
    assert nxt(events, "2026-10-06T12:00:00Z") == (tr.FOLLOW_UP, "2026-10-12")      # +5 from the reply


def test_project_names_the_schedule(root, capsys):
    folder = root / "applications" / "t1"
    folder.mkdir(parents=True)
    from js_common import fm
    (folder / "application.md").write_text(fm.dump({"id": "t1", "company": "A", "title": "t", "stage": "applied"},
                                                    "# a\n\n## Events\n" + tr.format_event(APPLIED, "applied", "manual",
                                                                                          1.0, "e") + "\n"))
    assert tr.main(["--root", str(root), "project", "--today", "2026-10-05"]) == 0
    out = capsys.readouterr().out
    assert "follow-ups due (+5 business days without inbound: find a person, once): t1 (applied, due 2026-10-02)" in out
