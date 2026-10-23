"""Rounds and counts on the dashboard tell the truth: a personal appointment is never an interview round, a held round
is not listed as coming, and an application is applied once (a second applied line from a resume backfill does not
count). A round carries its own time (tracker.py event --round-at), a moved round is marked (--reschedule), a
calendar event with no deal is private."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import interviews  # noqa: E402
import tracker as tr  # noqa: E402
from js_common import fm  # noqa: E402

sys_path = Path(__file__).resolve().parents[2] / "dashboard"
import sys  # noqa: E402
sys.path.insert(0, str(sys_path))
import crm  # noqa: E402

NOW = datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc)          # Sat 2026-10-03 23:00 PDT


def _app(root, slug, company, lines, stage=None):
    body = "\n".join(tr.format_event(at, t, "manual", 1.0, ev) for at, t, ev in lines)
    path = root / "applications" / slug / "application.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fm.dump({"id": slug, "company": company, "title": f"{company} AI Engineer"},
                            f"# {slug}\n\n## Events\n{body}\n"))
    return {"slug": slug, "company": company, "stage": stage, "people": []}


def test_round_at_and_reschedule_are_written_as_evidence_and_read_back():
    ev = tr.round_evidence("screen", "invite from Sam", "2026-10-09T11:45:00-07:00", True)
    assert ev == "invite from Sam round_at=2026-10-09T18:45:00Z [reschedule]"
    assert tr.round_info(ev) == {"round_at": "2026-10-09T18:45:00Z", "reschedule": True}
    assert tr.round_info("plain evidence") == {"round_at": None, "reschedule": False}


@pytest.mark.parametrize("type_, at", [("applied", "2026-10-09T11:45:00-07:00"), ("screen", "2026-10-09"),
                                       ("screen", "next friday")])
def test_round_flags_are_refused_off_a_round_or_without_a_time(type_, at):
    with pytest.raises(tr.TrackerError):
        tr.round_evidence(type_, "", at, False)


def test_the_cli_writes_the_round_time(tmp_path):
    _app(tmp_path, "acme-ai-202610", "Acme", [("2026-10-01T17:00:00Z", "applied", "e")])
    assert tr.main(["--root", str(tmp_path), "event", "acme-ai-202610", "screen", "--at", "2026-10-02T17:00:00Z",
                    "--round-at", "2026-10-09T11:45:00-07:00", "--evidence", "invite"]) == 0
    body = (tmp_path / "applications" / "acme-ai-202610" / "application.md").read_text()
    assert "round_at=2026-10-09T18:45:00Z" in body


def test_a_reschedule_is_never_a_new_round(tmp_path):
    _app(tmp_path, "acme-ai-202610", "Acme", [
        ("2026-09-29T17:00:00Z", "screen", "invite"),
        ("2026-09-30T17:00:00Z", "screen", "moved round_at=2026-10-02T17:00:00Z [reschedule]"),
        ("2026-10-02T17:00:00Z", "screen", "held")])
    got = interviews.booked(tmp_path, date(2026, 10, 3))
    assert got["this_week"] == 1, got["items"]              # the old count made the held line a second round


def test_a_calendar_event_with_no_deal_is_private_and_never_a_round(tmp_path):
    apps = [_app(tmp_path, "initech-fe-202609", "Initech", [("2026-09-29T17:00:00Z", "screen", "invite")], "screen")]
    cal = lambda a, b: [{"title": "Clinic appointment, Dr. Example", "start": "2026-10-05T15:10:00-07:00",
                         "end": "2026-10-05T15:40:00-07:00"},
                        {"title": "Initech screen", "start": "2026-10-09T11:45:00-07:00", "end": "2026-10-09T12:30:00-07:00"}]
    b = crm.band48(tmp_path, tmp_path, NOW, apps, [], {}, cal)
    assert [r["company"] for r in b["rounds"]] == ["Initech"]
    assert b["calendar_other"] == 1 and "Clinic" not in str(b)


def test_a_tracker_round_shows_at_its_round_time_only_while_it_is_coming(tmp_path):
    no_cal = lambda a, b: []
    coming = _app(tmp_path, "a-202610", "Acme", [("2026-10-01T17:00:00Z", "screen", "round_at=2026-10-06T17:00:00Z")], "screen")
    held = _app(tmp_path, "r-202609", "Globex", [("2026-09-29T17:00:00Z", "screen", "round_at=2026-10-02T17:00:00Z")], "screen")
    undated = _app(tmp_path, "u-202610", "Undated", [("2026-10-01T17:00:00Z", "screen", "invite")], "screen")
    b = crm.band48(tmp_path, tmp_path, NOW, [coming, held, undated], [], {}, no_cal)
    assert [(r["company"], r["when"]) for r in b["rounds"]] == [("Acme", "2026-10-06 10:00")]
    assert [r["company"] for r in b["rounds_undated"]] == ["Undated"]
    assert not b["rounds_48h"]                                # Tue is past today + 2 days (the band's horizon)


def test_an_application_is_applied_once_and_a_round_counted_once():
    body = "## Events\n" + "\n".join(tr.format_event(at, t, "manual", 1.0, ev) for at, t, ev in [
        ("2026-10-01T17:00:00Z", "applied", "e"), ("2026-10-02T17:00:00Z", "applied", "resume backfill"),
        ("2026-10-02T18:00:00Z", "screen", "invite"), ("2026-10-02T19:00:00Z", "screen", "moved [reschedule]"),
        ("2026-10-03T17:00:00Z", "screen", "held"), ("2026-10-03T18:00:00Z", "rejected", "e")])
    kinds = [e.type for e in crm.counted_events(body)]
    assert kinds == ["applied", "screen", "rejected"]


def test_a_time_with_no_zone_is_pacific():
    """11:45 with no zone was stored as UTC and shown 7 hours early."""
    assert tr.round_info(tr.round_evidence("screen", "", "2026-10-09T11:45", False))["round_at"] == "2026-10-09T18:45:00Z"


def test_the_word_reschedule_in_prose_is_not_the_marker():
    """only --reschedule's exact token marks a moved round."""
    assert tr.round_info("they asked to reschedule, then held the screen")["reschedule"] is False


def test_two_rounds_of_one_stage_weeks_apart_are_both_counted():
    """a second interview three weeks later was hidden from the plot and the updates."""
    body = "## Events\n" + "\n".join(tr.format_event(at, t, "manual", 1.0, "e") for at, t in [
        ("2026-09-01T17:00:00Z", "interview"), ("2026-09-30T17:00:00Z", "interview")])
    assert [e.at for e in crm.counted_events(body)] == ["2026-09-01T17:00:00Z", "2026-09-30T17:00:00Z"]


def test_a_company_matches_without_its_generic_tail_and_a_first_name_alone_never_does(tmp_path):
    """'Fabrikam interview' is the Fabrikam AI deal; 'Priya birthday dinner' is private."""
    apps = [_app(tmp_path, "h-202609", "Fabrikam AI", [("2026-09-29T17:00:00Z", "screen", "e")], "screen"),
            _app(tmp_path, "f-202609", "Contoso Motor Company", [("2026-09-29T17:00:00Z", "applied", "e")], "applied")]
    apps[0]["people"] = ["priya-nair"]
    people = {"priya-nair": {"id": "priya-nair", "name": "Priya Nair"}}
    cal = lambda a, b: [{"title": "Fabrikam interview", "start": "2026-10-06T10:00:00-07:00", "end": "2026-10-06T11:00:00-07:00"},
                        {"title": "Priya birthday dinner", "start": "2026-10-06T19:00:00-07:00", "end": "2026-10-06T21:00:00-07:00"},
                        {"title": "Contoso dealership oil change", "start": "2026-10-06T09:00:00-07:00", "end": "2026-10-06T10:00:00-07:00"}]
    b = crm.band48(tmp_path, tmp_path, NOW, apps, [], people, cal)
    assert [(r["company"], r["title"]) for r in b["rounds"]] == [("Fabrikam AI", None)]   # the title never leaves
    assert b["calendar_other"] == 2 and "birthday" not in str(b) and "oil" not in str(b)


def test_the_latest_event_decides_the_round_time(tmp_path):
    """an old round's past round_at hid a new undated round of the same stage."""
    a = _app(tmp_path, "x-202609", "Acme", [("2026-09-01T17:00:00Z", "screen", "round_at=2026-09-03T17:00:00Z"),
                                            ("2026-10-01T17:00:00Z", "screen", "new invite")], "screen")
    b = crm.band48(tmp_path, tmp_path, NOW, [a], [], {}, lambda x, y: [])
    assert [r["company"] for r in b["rounds_undated"]] == ["Acme"]
