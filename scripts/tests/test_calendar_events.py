"""calendar_events.py: read your calendar, invite guests, change and cancel what we created.

Every Google call goes to FakeGCal; nothing touches the network."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from comms_helpers import offline  # noqa: F401  (autouse: no network; puts scripts/ on sys.path)
import calendar_events as ce
import js_comms_util as cu

TODAY = date(2026, 9, 29)
NOW = datetime(2026, 9, 29, 16, 0, tzinfo=timezone.utc)            # 09:00 PDT
SAM = "sam.guest@example.test"


class FakeHttpError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.resp = type("R", (), {"status": status})()


class _Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeGCal:
    """events() list/get/insert/patch/delete over an in-memory primary calendar."""

    def __init__(self, events=(), page=50):
        self.store = {e["id"]: dict(e) for e in events}
        self.calls: list[tuple[str, dict]] = []
        self.page = page

    def events(self):
        return self

    def list(self, **kw):
        self.calls.append(("list", kw))
        lo, hi = ce.parse_dt(kw["timeMin"]), ce.parse_dt(kw["timeMax"])
        rows = [e for e in self.store.values() if ce.event_span(e)[1] > lo and ce.event_span(e)[0] < hi]
        rows.sort(key=lambda e: ce.event_span(e)[0])
        start = int(kw.get("pageToken") or 0)
        chunk = rows[start:start + self.page]
        out = {"items": chunk}
        if start + self.page < len(rows):
            out["nextPageToken"] = str(start + self.page)
        return _Call(lambda: out)

    def get(self, calendarId, eventId):
        self.calls.append(("get", {"calendarId": calendarId, "eventId": eventId}))
        if eventId not in self.store:
            return _Call(lambda: (_ for _ in ()).throw(FakeHttpError(404)))
        return _Call(lambda: dict(self.store[eventId]))

    def insert(self, calendarId, body, sendUpdates, conferenceDataVersion=0):
        self.calls.append(("insert", {"calendarId": calendarId, "body": body, "sendUpdates": sendUpdates,
                                      "conferenceDataVersion": conferenceDataVersion}))

        def run():
            if body["id"] in self.store:
                raise FakeHttpError(409)
            ev = dict(body, etag='"1"', status="confirmed", htmlLink=f"https://calendar.google.com/e/{body['id']}")
            if conferenceDataVersion and body.get("conferenceData"):
                ev["hangoutLink"] = "https://meet.google.com/abc-defg-hij"
            self.store[body["id"]] = ev
            return dict(ev)
        return _Call(run)

    def patch(self, calendarId, eventId, body, sendUpdates, conferenceDataVersion=0):
        self.calls.append(("patch", {"eventId": eventId, "body": body, "sendUpdates": sendUpdates}))

        def run():
            ev = self.store[eventId]
            ev.update(body)
            ev["etag"] = f'"{int(ev["etag"].strip(chr(34))) + 1}"'
            return dict(ev)
        return _Call(run)

    def delete(self, calendarId, eventId, sendUpdates):
        self.calls.append(("delete", {"eventId": eventId, "sendUpdates": sendUpdates}))
        return _Call(lambda: self.store.pop(eventId) and None)

    def writes(self):
        return [c for c in self.calls if c[0] in ("insert", "patch", "delete")]


def _ev(eid, start, end, summary="Busy", mine=False, **kw):
    ev = {"id": eid, "summary": summary, "etag": '"1"', "status": "confirmed",
          "start": {"dateTime": start, "timeZone": "America/Los_Angeles"},
          "end": {"dateTime": end, "timeZone": "America/Los_Angeles"}}
    if mine:
        ev["extendedProperties"] = {"private": {"job_agency": "job-search"}}
    ev.update(kw)
    return ev


@pytest.fixture
def roots(tmp_path):
    js = tmp_path / "os" / "domains" / "career" / "projects" / "job-search"
    js.mkdir(parents=True)
    return cu.Roots(js=js, os=tmp_path / "os")


def _sam(**kw):
    args = dict(title="Alex / Sam: intro chat", start="2026-09-30T10:30", end="2026-09-30T11:00",
                tz="America/Los_Angeles", guests=[SAM], meet=True, prospect="sam-guest")
    args.update(kw)
    return ce.build_invite(**args)


def _approve(path):
    cu.set_frontmatter(path, status="approved", decided_at="2026-09-29", decided_via="chat")


# --------------------------------------------------------------------------- read
def test_agenda_lists_events_in_order_across_pages_and_finds_conflicts_and_free_time():
    cal = FakeGCal([
        _ev("a", "2026-09-30T09:00:00-07:00", "2026-09-30T10:00:00-07:00", "Standup"),
        _ev("b", "2026-09-30T09:30:00-07:00", "2026-09-30T10:15:00-07:00", "Dentist"),
        _ev("c", "2026-09-30T14:00:00-07:00", "2026-09-30T15:00:00-07:00", "Acme Studio: Tomas"),
        _ev("d", "2026-09-30T12:00:00-07:00", "2026-09-30T13:00:00-07:00", "Lunch (free)", transparency="transparent"),
        _ev("e", "2026-09-30T16:00:00-07:00", "2026-09-30T17:00:00-07:00", "Declined",
            attendees=[{"email": "me@example.test", "self": True, "responseStatus": "declined"}]),
        _ev("f", "2026-09-30T11:00:00-07:00", "2026-09-30T11:30:00-07:00", "Gone", status="cancelled"),
    ], page=2)
    ag = ce.agenda(cal, date(2026, 9, 30), date(2026, 9, 30), tz="America/Los_Angeles", day_start="09:00",
                   day_end="18:00", min_free=30)
    assert [e["id"] for e in ag["events"]] == ["a", "b", "d", "c", "e"]           # cancelled dropped; pages joined
    assert sum(1 for c in cal.calls if c[0] == "list") == 3
    assert ag["conflicts"] == [["a", "b"]]
    assert ag["free"] == [["2026-09-30T10:15:00-07:00", "2026-09-30T14:00:00-07:00"],
                          ["2026-09-30T15:00:00-07:00", "2026-09-30T18:00:00-07:00"]]
    busy = {e["id"]: e["busy"] for e in ag["events"]}
    assert busy == {"a": True, "b": True, "c": True, "d": False, "e": False}
    lst = next(c[1] for c in cal.calls if c[0] == "list")
    assert lst["calendarId"] == "primary" and lst["singleEvents"] is True and lst["orderBy"] == "startTime"


def test_agenda_marks_the_events_the_agency_created_and_an_all_day_event_blocks_the_day():
    cal = FakeGCal([_ev("m", "2026-10-01T10:00:00-07:00", "2026-10-01T10:30:00-07:00", "HOLD: x", mine=True,
                        location="Acme Studio office, San Francisco"),
                    {"id": "h", "summary": "Offsite", "status": "confirmed", "etag": '"1"',
                     "start": {"date": "2026-10-02"}, "end": {"date": "2026-10-03"}}])
    ag = ce.agenda(cal, date(2026, 10, 1), date(2026, 10, 2), tz="America/Los_Angeles")
    mine = {e["id"]: e["mine"] for e in ag["events"]}
    assert ag["events"][0]["location"] == "Acme Studio office, San Francisco" and ag["events"][1]["location"] is None
    assert "at Acme Studio office, San Francisco" in ce._text_agenda(ag)
    assert mine == {"m": True, "h": False}
    assert not any(f[0].startswith("2026-10-02") for f in ag["free"])            # the all-day event is busy


def test_check_reports_the_busy_events_that_overlap_a_slot():
    cal = FakeGCal([_ev("a", "2026-09-30T10:00:00-07:00", "2026-09-30T10:45:00-07:00", "Standup"),
                    _ev("b", "2026-09-30T11:00:00-07:00", "2026-09-30T11:30:00-07:00", "Next")])
    hits = ce.conflicts_for(cal, "2026-09-30T10:30:00-07:00", "2026-09-30T11:00:00-07:00")
    assert [h["id"] for h in hits] == ["a"]                                        # touching at 11:00 is not a clash


# --------------------------------------------------------------------------- invite
def test_invite_names_the_timezone_guests_and_a_meet_link_and_keeps_record_links_private():
    ev = _sam(description="Quick intro after the conference.")
    assert ev["summary"] == "Alex / Sam: intro chat"
    assert ev["start"] == {"dateTime": "2026-09-30T10:30:00-07:00", "timeZone": "America/Los_Angeles"}
    assert ev["end"] == {"dateTime": "2026-09-30T11:00:00-07:00", "timeZone": "America/Los_Angeles"}
    assert ev["attendees"] == [{"email": SAM}]
    req = ev["conferenceData"]["createRequest"]
    assert req["conferenceSolutionKey"] == {"type": "hangoutsMeet"} and req["requestId"]
    assert ev["description"] == "Quick intro after the conference."                           # guests see only this text
    assert "job-search" not in json.dumps({k: v for k, v in ev.items() if k != "extendedProperties"})
    assert ev["extendedProperties"]["private"] == {"jaja": "job-search", "kind": "invite",
                                                   "prospect": "sam-guest"}
    assert ev["id"] == _sam(description="Quick intro after the conference.")["id"]          # stable: a retry cannot duplicate
    assert set(ev["id"]) <= set("abcdefghijklmnopqrstuv0123456789") and len(ev["id"]) >= 5


@pytest.mark.parametrize("bad, match", [
    (dict(guests=[]), "guest"), (dict(guests=["not-an-email"]), "guest"), (dict(tz="Mars/Olympus"), "time zone"),
    (dict(end="2026-09-30T10:00"), "after"), (dict(title="  "), "title"),
])
def test_invite_refuses_a_bad_event(bad, match):
    with pytest.raises(ce.CalendarError, match=match):
        _sam(**bad)


def test_invite_request_files_an_approval_hashing_the_event_guests_and_send_updates(roots):
    aid, path = ce.request(roots, ce.insert_action(_sam()), today=TODAY, conflicts=[])
    appr = cu.parse_approval(path)
    assert aid == "A-2026-0001" and appr.hash_ok and appr.meta["status"] == "pending"
    action = ce.approved_action(appr)
    assert action["action"] == "insert" and action["sendUpdates"] == "all" and action["calendarId"] == "primary"
    assert action["event"]["attendees"] == [{"email": SAM}]
    assert SAM in appr.meta["target"]
    assert "emails an invitation" in path.read_text() and "No conflicts" in path.read_text()


def test_execute_refuses_an_unapproved_or_unticked_approval(roots):
    _, path = ce.request(roots, ce.insert_action(_sam()), today=TODAY)
    cal = FakeGCal()
    with pytest.raises(ce.CalendarError, match="not approved"):
        ce.execute(cal, path, today=TODAY, now=NOW)
    _approve(path)
    item = cu.parse_approval(path).items[0]["id"]
    path.write_text(path.read_text().replace(f"- [x] {item}", f"- [ ] {item}"))
    with pytest.raises(ce.CalendarError):
        ce.execute(cal, path, today=TODAY, now=NOW)
    assert cal.writes() == []


def test_execute_inserts_exactly_the_approved_invite_with_meet_and_emails_the_guests_once(roots):
    ev = _sam()
    _, path = ce.request(roots, ce.insert_action(ev), today=TODAY)
    _approve(path)
    cal = FakeGCal()
    out = ce.execute(cal, path, today=TODAY, now=NOW)
    [(kind, call)] = cal.writes()
    assert kind == "insert" and call["body"] == ev and call["sendUpdates"] == "all"
    assert call["conferenceDataVersion"] == 1 and call["calendarId"] == "primary"
    assert out["event_id"] == ev["id"] and out["meet"] == "https://meet.google.com/abc-defg-hij"
    assert f"calendar insert {ev['id']}" in path.read_text()
    again = ce.execute(cal, path, today=TODAY, now=NOW)                            # the receipt: no second insert
    assert again["existing"] is True and len(cal.writes()) == 1


def test_execute_treats_an_event_that_already_exists_as_done_not_a_second_invite(roots):
    ev = _sam()
    _, path = ce.request(roots, ce.insert_action(ev), today=TODAY)
    _approve(path)
    cal = FakeGCal([dict(ev, etag='"1"', hangoutLink="https://meet.google.com/abc-defg-hij")])  # receipt lost
    out = ce.execute(cal, path, today=TODAY, now=NOW)
    assert out["existing"] is True and out["event_id"] == ev["id"] and len(cal.store) == 1


def test_execute_refuses_an_invite_whose_start_has_passed_and_reports_new_conflicts(roots):
    _, path = ce.request(roots, ce.insert_action(_sam()), today=TODAY)
    _approve(path)
    late = datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc)                       # 11:00 PDT, after the start
    with pytest.raises(ce.CalendarError, match="passed"):
        ce.execute(FakeGCal(), path, today=TODAY, now=late)
    cal = FakeGCal([_ev("x", "2026-09-30T10:00:00-07:00", "2026-09-30T10:45:00-07:00", "Standup")])
    out = ce.execute(cal, path, today=TODAY, now=NOW)
    assert [c["id"] for c in out["conflicts"]] == ["x"] and out["event_id"]


def test_a_hold_approval_is_not_executed_here(roots):
    import calendar_hold as chd
    _, path = chd.request_approval(roots, chd.build_payload(start="2026-10-02T10:00", manual="phone booking"),
                                   today=TODAY)
    _approve(path)
    with pytest.raises(ce.CalendarError, match="calendar_hold.py create"):
        ce.execute(FakeGCal(), path, today=TODAY, now=NOW)


# --------------------------------------------------------------------------- change and cancel
def _invited(start="2026-09-30T10:30:00-07:00", end="2026-09-30T11:00:00-07:00"):
    return _ev("jsinvite1", start, end, "Alex / Sam: intro chat", mine=True, attendees=[{"email": SAM}])


def test_change_of_an_event_with_guests_needs_an_approval_and_keeps_the_duration(roots):
    cal = FakeGCal([_invited()])
    action = ce.change_action(cal, "jsinvite1", start="2026-09-30T11:00", tz="America/Los_Angeles")
    assert action["action"] == "patch" and action["sendUpdates"] == "all" and action["etag"] == '"1"'
    assert action["patch"]["start"]["dateTime"] == "2026-09-30T11:00:00-07:00"
    assert action["patch"]["end"]["dateTime"] == "2026-09-30T11:30:00-07:00"
    with pytest.raises(ce.CalendarError, match="approval"):
        ce.apply_direct(cal, action)
    _, path = ce.request(roots, action, today=TODAY)
    _approve(path)
    ce.execute(cal, path, today=TODAY, now=NOW)
    [(kind, call)] = cal.writes()
    assert kind == "patch" and call["sendUpdates"] == "all" and call["body"] == action["patch"]
    assert cal.store["jsinvite1"]["start"]["dateTime"] == "2026-09-30T11:00:00-07:00"


def test_guest_changes_rewrite_the_whole_guest_list():
    cal = FakeGCal([_invited()])
    action = ce.change_action(cal, "jsinvite1", add_guests=["Ana@Example.test"], remove_guests=[SAM])
    assert action["patch"] == {"attendees": [{"email": "ana@example.test"}]}


def test_change_is_refused_when_the_event_moved_on_since_the_approval(roots):
    cal = FakeGCal([_invited()])
    _, path = ce.request(roots, ce.change_action(cal, "jsinvite1", title="Intro chat"), today=TODAY)
    _approve(path)
    cal.store["jsinvite1"]["etag"] = '"7"'                                          # edited in Google since
    with pytest.raises(ce.CalendarError, match="changed since"):
        ce.execute(cal, path, today=TODAY, now=NOW)
    assert cal.writes() == []


def test_only_events_the_agency_created_can_be_changed_or_cancelled():
    cal = FakeGCal([_ev("theirs", "2026-09-30T09:00:00-07:00", "2026-09-30T10:00:00-07:00", "Team sync")])
    for build in (lambda: ce.change_action(cal, "theirs", title="x"), lambda: ce.cancel_action(cal, "theirs")):
        with pytest.raises(ce.CalendarError, match="not created by this agency"):
            build()


def test_a_private_event_without_guests_changes_and_cancels_directly_without_emailing_anyone():
    cal = FakeGCal([_ev("hold1", "2026-10-01T10:00:00-07:00", "2026-10-01T10:45:00-07:00", "HOLD: x", mine=True)])
    action = ce.change_action(cal, "hold1", start="2026-10-01T13:00", tz="America/Los_Angeles")
    assert action["sendUpdates"] == "none"
    ce.apply_direct(cal, action)
    assert cal.store["hold1"]["end"]["dateTime"] == "2026-10-01T13:45:00-07:00"
    ce.apply_direct(cal, ce.cancel_action(cal, "hold1"))
    assert "hold1" not in cal.store and [c[1]["sendUpdates"] for c in cal.writes()] == ["none", "none"]


def test_adding_a_guest_to_a_private_hold_is_gated():
    cal = FakeGCal([_ev("hold1", "2026-10-01T10:00:00-07:00", "2026-10-01T10:45:00-07:00", "HOLD: x", mine=True)])
    action = ce.change_action(cal, "hold1", add_guests=[SAM])
    assert action["sendUpdates"] == "all"
    with pytest.raises(ce.CalendarError, match="approval"):
        ce.apply_direct(cal, action)


def test_cancel_of_an_invite_emails_the_guests_only_after_approval(roots):
    cal = FakeGCal([_invited()])
    action = ce.cancel_action(cal, "jsinvite1")
    assert action == {"action": "delete", "calendarId": "primary", "eventId": "jsinvite1", "etag": '"1"',
                      "sendUpdates": "all", "summary": "Alex / Sam: intro chat",
                      "guests": [SAM]}
    _, path = ce.request(roots, action, today=TODAY)
    _approve(path)
    ce.execute(cal, path, today=TODAY, now=NOW)
    assert "jsinvite1" not in cal.store and cal.writes()[0][1]["sendUpdates"] == "all"


# --------------------------------------------------------------------------- CLI
def test_cli_invite_prints_the_action_and_writes_nothing_without_request(roots, capsys):
    rc = ce.main(["invite", "--root", str(roots.js), "--os-root", str(roots.os), "--today", "2026-09-29",
                  "--title", "Alex / Sam: intro chat", "--start", "2026-09-30T10:30", "--end", "2026-09-30T11:00",
                  "--tz", "America/Los_Angeles", "--guest", SAM, "--meet", "--no-check"])
    assert rc == 0
    action = json.loads(capsys.readouterr().out)
    assert action["action"] == "insert" and action["event"]["attendees"] == [{"email": SAM}]
    assert not roots.approvals.exists() or not list(roots.approvals.glob("A-*.md"))


def test_cli_invite_request_files_the_approval(roots, capsys):
    rc = ce.main(["invite", "--root", str(roots.js), "--os-root", str(roots.os), "--today", "2026-09-29",
                  "--title", "Alex / Sam: intro chat", "--start", "2026-09-30T10:30", "--duration", "30",
                  "--guest", SAM, "--meet", "--no-check", "--request"])
    assert rc == 0
    path = next(roots.approvals.glob("A-*.md"))
    assert ce.approved_action(cu.parse_approval(path))["event"]["end"]["dateTime"] == "2026-09-30T11:00:00-07:00"
    assert "not checked" in path.read_text()


def test_cli_execute_checks_the_approval_before_loading_any_token(roots, capsys, monkeypatch):
    _, path = ce.request(roots, ce.insert_action(_sam()), today=TODAY)
    monkeypatch.setattr(ce, "calendar_service", lambda: pytest.fail("token loaded for an unapproved approval"))
    assert ce.main(["execute", "--approval", str(path), "--root", str(roots.js), "--os-root", str(roots.os),
                    "--today", "2026-09-29"]) == 2
    assert "not approved" in capsys.readouterr().out


def test_an_event_that_only_touches_the_slot_is_not_a_clash_even_if_the_api_returns_it():
    class Inclusive(FakeGCal):
        def list(self, **kw):
            rows = sorted(self.store.values(), key=lambda e: ce.event_span(e)[0])
            return _Call(lambda: {"items": rows})
    cal = Inclusive([_ev("before", "2026-09-30T10:00:00-07:00", "2026-09-30T10:30:00-07:00"),
                     _ev("after", "2026-09-30T11:00:00-07:00", "2026-09-30T11:30:00-07:00")])
    assert ce.conflicts_for(cal, "2026-09-30T10:30:00-07:00", "2026-09-30T11:00:00-07:00") == []


def test_an_invite_whose_fixed_id_was_cancelled_before_is_refused_not_reported_done(roots):
    ev = _sam()
    _, path = ce.request(roots, ce.insert_action(ev), today=TODAY)
    _approve(path)
    cal = FakeGCal([dict(ev, etag='"3"', status="cancelled")])                     # Google keeps deleted ids
    with pytest.raises(ce.CalendarError, match="cancelled"):
        ce.execute(cal, path, today=TODAY, now=NOW)
    assert "calendar insert" not in path.read_text()


# --------------------------------------------------------------------------- RSVP and the preview
def _theirs():
    return _ev("tomas1", "2026-09-30T14:00:00-07:00", "2026-09-30T15:00:00-07:00", "Alex x Tomas | In person",
               organizer={"email": "tomas@example.test"},
               attendees=[{"email": "tomas@example.test", "organizer": True, "responseStatus": "accepted"},
                          {"email": "alex@example.test", "self": True, "responseStatus": "needsAction"}])


def test_rsvp_answers_an_invite_someone_else_sent_only_through_an_approval(roots):
    cal = FakeGCal([_theirs()])
    action = ce.rsvp_action(cal, "tomas1", "accepted", comment="See you there")
    assert action["action"] == "rsvp" and action["response"] == "accepted" and action["organizer"] == "tomas@example.test"
    me = next(a for a in action["patch"]["attendees"] if a.get("self"))
    assert me["responseStatus"] == "accepted" and me["comment"] == "See you there"
    other = next(a for a in action["patch"]["attendees"] if not a.get("self"))
    assert other == {"email": "tomas@example.test", "organizer": True, "responseStatus": "accepted"}
    with pytest.raises(ce.CalendarError, match="approval"):
        ce.apply_direct(cal, action)
    _, path = ce.request(roots, action, today=TODAY)
    assert "tells the organizer" in path.read_text()
    _approve(path)
    ce.execute(cal, path, today=TODAY, now=NOW)
    [(kind, call)] = cal.writes()
    assert kind == "patch" and call["eventId"] == "tomas1" and call["body"] == action["patch"]
    assert next(a for a in cal.store["tomas1"]["attendees"] if a.get("self"))["responseStatus"] == "accepted"


def test_rsvp_is_refused_when_you_are_not_a_guest_or_is_the_organizer_or_the_answer_is_unknown():
    cal = FakeGCal([_ev("solo", "2026-09-30T09:00:00-07:00", "2026-09-30T10:00:00-07:00", "Focus"), _invited(),
                    _theirs()])
    with pytest.raises(ce.CalendarError, match="not a guest"):
        ce.rsvp_action(cal, "solo", "declined")
    mine = cal.store["jsinvite1"]
    mine["attendees"] = mine["attendees"] + [{"email": "alex@example.test", "self": True, "organizer": True}]
    with pytest.raises(ce.CalendarError, match="organize"):
        ce.rsvp_action(cal, "jsinvite1", "declined")
    with pytest.raises(ce.CalendarError, match="accepted, declined or tentative"):
        ce.rsvp_action(cal, "tomas1", "maybe")


def test_rsvp_is_refused_when_the_invite_changed_since_the_approval(roots):
    cal = FakeGCal([_theirs()])
    _, path = ce.request(roots, ce.rsvp_action(cal, "tomas1", "declined"), today=TODAY)
    _approve(path)
    cal.store["tomas1"]["etag"] = '"9"'
    with pytest.raises(ce.CalendarError, match="changed since"):
        ce.execute(cal, path, today=TODAY, now=NOW)


def test_show_lists_everything_you_approve_for_an_invite():
    ev = _sam(description="Quick intro after the conference.", location="Google Meet")
    out = ce.render(ce.insert_action(ev), guest_tz="America/New_York")
    for part in ("INVITE", "Alex / Sam: intro chat", "Wed 2026-09-30", "10:30 to 11:00 America/Los_Angeles",
                 "13:30 to 14:00 America/New_York", "Guests: sam.guest@example.test", "Video: Google Meet link",
                 "Location: Google Meet", "Description (guests read this): Quick intro after the conference.",
                 "Reminders: popup 30 min", "Visibility: default", "Guests are emailed: yes (sendUpdates=all)"):
        assert part in out, part


def test_show_names_the_change_the_cancel_and_the_answer():
    cal = FakeGCal([_invited(), _theirs()])
    change = ce.render(ce.change_action(cal, "jsinvite1", start="2026-09-30T11:00", tz="America/Los_Angeles"))
    assert "CHANGE" in change and "10:30 to 11:00" in change and "11:00 to 11:30" in change
    assert "Guests are emailed: yes" in change
    cancel = ce.render(ce.cancel_action(cal, "jsinvite1"))
    assert "CANCEL" in cancel and "sam.guest@example.test" in cancel
    rsvp = ce.render(ce.rsvp_action(cal, "tomas1", "declined"))
    assert "REPLY: declined" in rsvp and "tomas@example.test" in rsvp


def test_cli_show_prints_the_preview_from_the_approval_itself(roots, capsys):
    _, path = ce.request(roots, ce.insert_action(_sam()), today=TODAY)
    assert ce.main(["show", "--approval", str(path), "--root", str(roots.js), "--os-root", str(roots.os)]) == 0
    out = capsys.readouterr().out
    assert "INVITE" in out and "sam.guest@example.test" in out
