"""The follow-up cadence of the agency charter, one rule everywhere: a person gets a nudge 4 business days
after our message and a second 5 after that (day 9), then nothing; a silent application gets a person found at 5
business days, once. prospects.py, follow_ups.py and tracker.py agree, and no card, skill, playbook or routine still
teaches +5/+10/+15 or a third nudge. Tiny fixture trees only."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from comms_helpers import offline  # noqa: F401  (autouse: no network)
import js_common as jc
import js_comms_util as cu
import follow_ups as fu
import prospects as pr
import tracker as tr

from layout import JS  # noqa: E402
TODAY = date(2026, 10, 6)


def _roots(tmp_path):
    js = tmp_path / "os" / "a" / "b" / "c" / "data"
    js.mkdir(parents=True)
    return cu.Roots(js=js, os=tmp_path / "os")


def _seed(roots, rows):
    for row in rows:
        meta = pr.new_meta(row["id"], row["id"].title(), source="gmail-backlog", today=TODAY)
        meta.update(row)
        jc.write_record(roots.prospects / f"{row['id']}.md", meta, "# x\n\n## Timeline\n")


def test_one_cadence_in_every_script():
    assert pr.FOLLOW_UP_GAPS == (4, 5) and pr.MAX_FOLLOW_UPS == 2
    assert fu.PERSON_GAPS == pr.FOLLOW_UP_GAPS and fu.MAX_PERSON_NUDGES == 2
    assert tr.FOLLOW_UP_OFFSETS == (5,) and "find a person" in tr.FOLLOW_UP


def test_second_nudge_is_five_business_days_after_the_first(tmp_path):
    roots = _roots(tmp_path)
    _seed(roots, [{"id": "ana", "status": "replied", "last_outbound_at": "2026-10-01T17:00:00Z",   # Thu: nudge 1 sent
                   "last_inbound_at": "2026-09-20T00:00:00Z", "outbound_count": 2, "follow_ups_sent": 1}])
    assert pr.due(roots, today=date(2026, 10, 7))["due"] == []
    due = pr.due(roots, today=date(2026, 10, 8))["due"]                                         # Thu + 5 business days
    assert [d["id"] for d in due] == ["ana"] and due[0]["nudge"] == 2


def test_an_old_record_with_max_three_stops_after_two(tmp_path):
    roots = _roots(tmp_path)
    _seed(roots, [{"id": "old", "status": "replied", "last_outbound_at": "2026-09-01T17:00:00Z",
                   "last_inbound_at": "2026-08-20T00:00:00Z", "follow_ups_sent": 2, "max_follow_ups": 3}])
    res = pr.due(roots, today=TODAY)
    assert [d["id"] for d in res["exhausted"]] == ["old"] and res["due"] == []
    assert fu.prospects(roots.js, TODAY)["exhausted"][0]["who"] == "old"


def test_an_old_automatic_plus_five_is_not_a_deferral(tmp_path):
    roots = _roots(tmp_path)
    _seed(roots, [{"id": "bo", "status": "replied", "last_outbound_at": "2026-09-25T17:00:00Z",   # Fri
                   "last_inbound_at": "2026-09-20T00:00:00Z", "follow_ups_sent": 0, "next_touch_at": "2026-10-02"}])
    due = pr.due(roots, today=date(2026, 10, 1))["due"]                                         # Thu = +4
    assert [d["id"] for d in due] == ["bo"] and due[0]["due_date"] == "2026-10-01"


def test_a_later_hand_set_day_is_still_a_deferral(tmp_path):
    roots = _roots(tmp_path)
    _seed(roots, [{"id": "cy", "status": "replied", "last_outbound_at": "2026-09-25T17:00:00Z",
                   "last_inbound_at": "2026-09-20T00:00:00Z", "follow_ups_sent": 0, "next_touch_at": "2026-10-09"}])
    assert pr.due(roots, today=date(2026, 10, 8))["due"] == []
    assert [d["id"] for d in pr.due(roots, today=date(2026, 10, 9))["due"]] == ["cy"]


def test_a_sent_follow_up_sets_the_next_touch_on_the_cadence():
    meta = pr.new_meta("dee", "Dee", source="gmail-backlog", today=TODAY)
    meta.update(status="replied", follow_ups_sent=0)
    assert pr.follow_up_gap(0) == 4 and pr.follow_up_gap(1) == 5 and pr.follow_up_gap(7) == 5
    assert pr.follow_up_cap(meta) == 2 and pr.follow_up_cap({"max_follow_ups": 1}) == 1


OLD = re.compile(r"\+5, \+10|\+10 business|\+15 business|never a fourth|at most 3 per prospect|never more than 3")


def test_no_card_skill_playbook_or_routine_teaches_the_old_cadence():
    files = [JS / "skills/follow-up-sweep/SKILL.md", JS / "skills/track-application/SKILL.md",
             JS / "agents/messenger.md", JS / "playbooks/outreach.md", JS / "routines/js-follow-up-sweep.yaml"]
    hits = [f"{p.relative_to(JS)}:{n}" for p in files for n, line in enumerate(p.read_text().splitlines(), 1)
            if OLD.search(line)]
    assert hits == []


def test_the_sweep_skill_reads_the_one_due_list_and_routes_silent_applications():
    skill = (JS / "skills/follow-up-sweep/SKILL.md").read_text()
    assert "python3 scripts/follow_ups.py --json" in skill and "prospector" in skill
    assert "two nudges, then stop" in skill                                     # the cadence ends after nudge 2
    assert "Personal (the owner's)" in skill and "review trial" in skill        # the personal-path routing kept
