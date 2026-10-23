"""outbox.py comment ceiling: comments_on must not count outbox items only, or the owner's own sent comments logged in
touches/ would not count and the 10-a-day ceiling could be passed. It counts both: outbox
comment items (pending or approved created that day, or sent that day) and touches with a comment channel that day,
never the same comment twice."""
from __future__ import annotations

from datetime import date

import comms_helpers as ch  # noqa: F401  (puts scripts/ on sys.path)
from comms_helpers import offline  # noqa: F401  (autouse: no network)
import js_common as jc
import outbox as ob
from test_outbox import COMMENTS, _create, _stage_comments, roots  # noqa: F401

DAY = date(2026, 9, 25)


def _touch(roots, n, day="2026-09-25", channel="linkedin-comment", key=None):
    jc.log_touch({"at": f"{day}T16:{n:02d}:00Z", "prospect": f"poster-{n}", "channel": channel, "direction": "out",
                  "kind": "comment", "status": "sent", "key": key or f"li-comment-{n}",
                  "summary": f"comment {n} on a post"}, root=roots.js)


def test_comments_logged_in_touches_count_toward_the_day(roots):
    for n in range(4):
        _touch(roots, n)
    _touch(roots, 9, day="2026-09-24")                         # another day
    _touch(roots, 8, channel="linkedin-message")               # not a comment
    assert ob.comments_on(roots, DAY) == 4


def test_the_ceiling_counts_touches_and_outbox_together(roots, capsys):
    for n in range(5):
        _touch(roots, n)                                       # five comments the owner already posted themselves
    _stage_comments(roots, "L-2026-09-25-9", COMMENTS)         # six more drafted
    assert ob.main(["create", "--batch", "L-2026-09-25-9", "--root", str(roots.js), "--os-root", str(roots.os),
                    "--today", "2026-09-25"]) == 2
    assert "10 comments a day" in capsys.readouterr().out
    assert ob.comment_ceiling_problem(roots, DAY, 5) is None   # 5 + 5 = 10 is allowed


def test_a_touch_that_records_an_outbox_comment_is_not_counted_twice(roots, capsys):
    _stage_comments(roots, "L-2026-09-25-10", COMMENTS[:2])
    _create(roots, capsys, "L-2026-09-25-10")
    ids = [m["id"] for m in ob.list_drafts(roots) if m["channel"] == "linkedin-comment"]
    assert ob.comments_on(roots, DAY) == 2
    _touch(roots, 1, key=ids[0])                               # a touch keyed by the outbox id
    assert ob.comments_on(roots, DAY) == 2
