"""tracker.py dates are the department's day (js_common.DEPT_TZ), never the UTC date.
An application submitted in the evening local time has a UTC instant that is already the next day; projecting it with
`.date()` on the UTC timestamp gave the wrong applied_at. The event lines keep the
true UTC instant; only the YYYY-MM-DD fields written from them change."""
from __future__ import annotations

from datetime import date, datetime, timezone

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import tracker as tr  # noqa: E402
from js_common import fm  # noqa: E402
from test_tracker_package_only import _job  # noqa: E402

EVENING = datetime(2026, 10, 2, 3, 0, tzinfo=timezone.utc)      # 2026-10-01 20:00 PDT
APPLIED = "2026-10-02T02:30:00Z"                                  # 2026-10-01 19:30 PDT, a Thursday


def _applied(root):
    slug, _ = tr.new_application(root, _job(root), EVENING)
    tr.append_event(root, slug, "applied", at=APPLIED, now=EVENING, evidence="confirmation mail")
    return slug


def _props(root, slug):
    return fm.load(root / "applications" / slug / "application.md")[0]


def test_applied_at_is_the_local_day_of_an_evening_submit(root):
    slug = _applied(root)
    props = _props(root, slug)
    assert str(props["applied_at"]) == "2026-10-01"
    assert APPLIED in (root / "applications" / slug / "application.md").read_text()   # the event keeps its instant


def test_the_first_follow_up_counts_from_the_local_day(root):
    props = _props(root, _applied(root))
    assert str(props["next_action_at"]) == tr.add_business_days(date(2026, 10, 1), tr.FOLLOW_UP_OFFSETS[0]).isoformat()


def test_an_evening_reply_is_inbound_on_the_local_day(root):
    slug = _applied(root)
    tr.append_event(root, slug, sorted(tr.INBOUND)[0], at="2026-10-06T04:10:00Z", now=EVENING.replace(day=6, hour=5))
    assert str(_props(root, slug)["last_inbound_at"]) == "2026-10-05"


def test_a_date_only_stamp_keeps_its_own_day(root):
    slug, _ = tr.new_application(root, _job(root), EVENING)
    tr.append_event(root, slug, "applied", at="2026-09-28", now=EVENING, evidence="the owner's word, day only")
    assert str(_props(root, slug)["applied_at"]) == "2026-09-28"


def test_a_stated_17_00_pdt_submit_stamped_midnight_utc_is_still_the_local_day(root):
    # an evening submit: "2026-10-02T00:00:00Z ... time: stated" is 17:00 PDT on 2026-10-01
    slug, _ = tr.new_application(root, _job(root), EVENING)
    tr.append_event(root, slug, "applied", at="2026-10-02T00:00:00Z", now=EVENING, evidence="time: stated")
    assert str(_props(root, slug)["applied_at"]) == "2026-10-01"
