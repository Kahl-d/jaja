"""The morning plan said Dana (Globex) was "waiting 1 day" when she first wrote 5 days before (her second copy hid
the first) and Initech "waiting 2 days" when it was 3 in Pacific (the received day was UTC)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import follow_ups as fu  # noqa: E402
from test_follow_ups import _m, _mail  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard"))
import cockpit  # noqa: E402

TODAY = date(2026, 10, 4)


def test_a_message_received_in_the_pacific_evening_is_that_days(tmp_path):
    root = tmp_path / "js"
    root.mkdir()
    _mail(root, [_m("m1", "Sam <sam@staffing.example.test>", "recruiter_outreach", "2026-10-02T00:32:00Z", "t1",
                    subject="Initech Research Engineer Update")])          # 2026-10-01 17:32 Pacific
    row = [r for r in fu.collect(root, TODAY)["due"] if r["kind"] == "portal-message"][0]
    assert row["due"] == "2026-10-01" and row["days_overdue"] == 3


def test_the_wait_counts_from_the_first_unanswered_copy_and_the_follow_up_is_named(tmp_path):
    root = tmp_path / "js"
    root.mkdir()
    pitch = "Building voice agents at Globex"
    _mail(root, [_m("m1", "Dana <hit-reply@linkedin.com>", "recruiter_outreach", "2026-09-30T01:45:00Z", "t1",
                    portal="linkedin", subject=pitch),                           # 2026-09-29 Pacific
                 _m("m2", "Dana <hit-reply@linkedin.com>", "recruiter_outreach", "2026-10-03T01:45:00Z", "t2",
                    portal="linkedin", subject=pitch)])                          # 2026-10-02 Pacific
    acts = cockpit.replies(root, TODAY, 0.25)
    assert len(acts) == 1
    assert acts[0]["why"] == "Dana (LinkedIn InMail), waiting 5 days, followed up 2 days ago"


def test_one_copy_reads_as_before(tmp_path):
    root = tmp_path / "js"
    root.mkdir()
    _mail(root, [_m("m1", "Joel <joel@recruit.example.test>", "recruiter_outreach", "2026-10-02T18:00:00Z", "t1",
                    subject="Founding Engineer opportunity")])
    assert cockpit.replies(root, TODAY, 0.25)[0]["why"] == "joel@recruit.example.test, waiting 2 days"


def test_top_below_one_is_refused(capsys):
    import pytest
    with pytest.raises(SystemExit):
        cockpit.main(["--top", "0"])
    assert "--top must be 1 or more" in capsys.readouterr().err


def test_two_people_behind_one_relay_address_are_two_replies_and_neither_followed_up(tmp_path):
    """Every InMail comes from one relay address; Dana and Max with one generic subject merged into one
    line that said Max "followed up" and dropped Dana's thread."""
    root = tmp_path / "js"
    root.mkdir()
    _mail(root, [_m("m1", "Dana Reyes <inmail-hit-reply@linkedin.com>", "recruiter_outreach", "2026-09-26T18:00:00Z",
                    "t1", portal="linkedin", subject="Opportunity"),
                 _m("m2", "Max Weber <inmail-hit-reply@linkedin.com>", "recruiter_outreach", "2026-10-02T18:00:00Z",
                    "t2", portal="linkedin", subject="Opportunity")])
    acts = sorted(cockpit.replies(root, TODAY, 0.25), key=lambda a: a["why"])
    assert [a["why"] for a in acts] == ["Dana Reyes (LinkedIn InMail), waiting 8 days",
                                        "Max Weber (LinkedIn InMail), waiting 2 days"]
    assert {a["link"].rsplit("/", 1)[1] for a in acts} == {"t1", "t2"}


def test_copies_with_no_display_name_are_never_merged(tmp_path):
    root = tmp_path / "js"
    root.mkdir()
    _mail(root, [_m("m1", "jobs@acme.example.test", "recruiter_outreach", "2026-09-30T18:00:00Z", "t1", subject="Hi"),
                 _m("m2", "jobs@acme.example.test", "recruiter_outreach", "2026-10-02T18:00:00Z", "t2", subject="Hi")])
    assert len(cockpit.replies(root, TODAY, 0.25)) == 2


def test_a_message_from_this_pacific_evening_is_in_todays_list(tmp_path):
    """The 30-day window read the UTC date, so 18:30 PT on 10-03 (01:30Z on 10-04) was left out on 10-03."""
    root = tmp_path / "js"
    root.mkdir()
    _mail(root, [_m("m1", "Joel <joel@recruit.example.test>", "recruiter_outreach", "2026-10-04T01:30:00Z", "t1")])
    rows = [r for r in fu.collect(root, date(2026, 10, 3))["due"] if r["kind"] == "portal-message"]
    assert [(r["due"], r["days_overdue"]) for r in rows] == [("2026-10-03", 0)]
