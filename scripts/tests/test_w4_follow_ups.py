"""Follow-up cadence (charter section on follow-ups): a person gets a human nudge 4 business days after our message and a
second at 9, then nothing; an application with no reply after 5 business days gets one "find a person at the
company and reach them" follow-up. Do-not-contact holds and the replied/inbound rules still apply."""
from __future__ import annotations

from datetime import date

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; no-network guard)

import follow_ups as fu  # noqa: E402
from test_follow_ups import _app, _prospect, _t, _touches  # noqa: E402

MON = date(2026, 9, 28)


def _sam(root, **kw):
    meta = dict(company="Globex", status="replied", last_outbound_at="2026-09-21T17:00:00Z",   # a Monday
                last_inbound_at="2026-09-10T00:00:00Z", outbound_count=1, follow_ups_sent=0)
    meta.update(kw)
    _prospect(root, "sam-rivera", **meta)


def test_the_first_nudge_is_due_four_business_days_after_the_message(tmp_path):
    root = tmp_path / "js"
    _sam(root)
    assert fu.collect(root, date(2026, 9, 24))["due"] == []                     # Thu: 3 business days
    [row] = fu.collect(root, date(2026, 9, 25))["due"]                          # Fri: 4
    assert (row["who"], row["due"], row["nudge"]) == ("sam-rivera", "2026-09-25", 1)


def test_the_second_nudge_is_due_at_nine_business_days_then_it_stops(tmp_path):
    root = tmp_path / "js"
    _sam(root, last_outbound_at="2026-09-25T17:00:00Z", follow_ups_sent=1)    # nudge 1 sent on day 4 (Fri)
    assert fu.collect(root, date(2026, 10, 1))["due"] == []                     # Thu: day 8
    [row] = fu.collect(root, date(2026, 10, 2))["due"]                          # Fri: day 9 of the first message
    assert (row["due"], row["nudge"]) == ("2026-10-02", 2)
    _sam(root, last_outbound_at="2026-10-02T17:00:00Z", follow_ups_sent=2)
    res = fu.collect(root, date(2026, 10, 30))
    assert res["due"] == [] and [r["who"] for r in res["exhausted"]] == ["sam-rivera"]


def test_a_record_with_a_lower_cap_keeps_it(tmp_path):
    root = tmp_path / "js"
    _sam(root, follow_ups_sent=1, max_follow_ups=1, last_outbound_at="2026-09-21T17:00:00Z")
    assert [r["who"] for r in fu.collect(root, MON)["exhausted"]] == ["sam-rivera"]


def test_a_reply_or_do_not_contact_still_exempts(tmp_path):
    root = tmp_path / "js"
    _sam(root, last_inbound_at="2026-09-22T00:00:00Z")                        # they answered after our message
    assert fu.collect(root, MON)["due"] == []
    _sam(root, do_not_contact_reason="asked not to be contacted")
    res = fu.collect(root, MON)
    assert res["due"] == [] and res["no_answer_owed"].get("on hold (do not contact)") == 1


def test_a_silent_application_asks_to_find_a_person_once(tmp_path):
    root = tmp_path / "js"
    _app(root, "acme-ai-202609", "Acme", [("2026-09-15T17:00:00Z", "applied")])          # +5 bd: 2026-09-22
    [row] = fu.collect(root, MON)["due"]
    assert row["kind"] == "application" and row["due"] == "2026-09-22" and "find a person at the company" in row["next"]
    _app(root, "acme-ai-202609", "Acme", [("2026-09-15T17:00:00Z", "applied"), ("2026-09-23T17:00:00Z", "follow_up")])
    res = fu.collect(root, date(2026, 10, 30))
    assert res["due"] == [] and [r["who"] for r in res["exhausted"]] == ["acme-ai-202609"]


def test_a_portal_thread_follows_the_person_cadence(tmp_path):
    root = tmp_path / "js"
    _touches(root, [_t("2026-09-21T17:00:00Z", "ari", "out", "w1", "wellfound"),
                    _t("2026-09-25T17:00:00Z", "ari", "out", "w1", "wellfound", kind="follow_up"),
                    _t("2026-09-25T17:00:00Z", "bo", "out", "w2", "wellfound"),
                    _t("2026-09-28T17:00:00Z", "bo", "out", "w2", "wellfound", kind="follow_up"),
                    _t("2026-09-29T17:00:00Z", "bo", "out", "w2", "wellfound", kind="follow_up")])
    res = fu.collect(root, date(2026, 10, 2))
    assert [(r["who"], r["due"], r["nudge"]) for r in res["due"]] == [("ari", "2026-10-02", 2)]
    assert [r["who"] for r in res["exhausted"]] == ["bo"]                       # two nudges, then stop
