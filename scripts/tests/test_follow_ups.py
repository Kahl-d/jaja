"""follow_ups.py: the one daily follow-up list the Recruiter runs (design §8): application follow-ups from tracker.py
(+5, +10, +15 business days after the last company activity, never a fourth) and prospect nudges from prospects.py due,
in one list by due date, plus the exhausted ones and the referral windows. It drafts nothing and sends nothing."""
from __future__ import annotations

import json
from datetime import date

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import follow_ups as fu  # noqa: E402
import js_common as jc  # noqa: E402
import prospects as pr  # noqa: E402
import tracker as tr  # noqa: E402
from js_common import fm  # noqa: E402

TODAY = date(2026, 9, 28)          # a Monday


def _app(root, slug, company, events):
    lines = "\n".join(tr.format_event(at, t, "manual", 1.0, "e") for at, t in events)
    path = root / "applications" / slug / "application.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fm.dump({"id": slug, "company": company, "title": f"{company} AI Engineer", "prospects": ["dana-recruiter"]},
                            f"# {slug}\n\n## Events\n{lines}\n"))


def _prospect(root, pid, **meta):
    m = pr.new_meta(pid, pid.replace("-", " ").title(), source="gmail-backlog", today=TODAY)
    m.update(meta)
    jc.write_record(root / "prospects" / f"{pid}.md", m, "# x\n\n## Timeline\n")


def populate(root):
    _app(root, "acme-ai-202609", "Acme", [("2026-09-15T17:00:00Z", "applied")])                 # +5 bd: 2026-09-22
    _app(root, "beta-ai-202609", "Beta", [("2026-09-25T17:00:00Z", "applied")])                 # +5 bd: 2026-10-02
    _app(root, "zeta-ai-202609", "Zeta", [("2026-08-20T17:00:00Z", "applied"), ("2026-08-27T17:00:00Z", "follow_up"),
                                             ("2026-09-03T17:00:00Z", "follow_up"), ("2026-09-10T17:00:00Z", "follow_up")])
    _app(root, "delta-ai-202609", "Delta", [("2026-09-01T17:00:00Z", "applied"), ("2026-09-10T17:00:00Z", "rejected")])
    _prospect(root, "maya-chen", company="Helio", status="replied", last_outbound_at="2026-09-18T17:00:00Z",
              last_inbound_at="2026-09-10T00:00:00Z", outbound_count=1, follow_ups_sent=0)          # +4 bd: 2026-09-24


def test_one_list_by_due_date_from_both_sources(tmp_path):
    root = tmp_path / "js"
    populate(root)
    res = fu.collect(root, TODAY)
    assert [(r["kind"], r["who"]) for r in res["due"]] == [("application", "acme-ai-202609"), ("prospect", "maya-chen")]
    acme = res["due"][0]
    assert acme["due"] == "2026-09-22" and acme["nudge"] is None and acme["days_overdue"] == 6   # find a person
    assert acme["company"] == "Acme" and acme["contacts"] == ["dana-recruiter"] and "find a person" in acme["next"]
    assert res["due"][1]["due"] == "2026-09-24" and res["due"][1]["channel"]


def test_exhausted_and_terminal_applications(tmp_path):
    root = tmp_path / "js"
    populate(root)
    res = fu.collect(root, TODAY)
    assert [r["who"] for r in res["exhausted"]] == ["zeta-ai-202609"]           # three sent, never a fourth
    assert all(r["who"] not in ("delta-ai-202609", "beta-ai-202609") for r in res["due"] + res["exhausted"])


def test_cli_prints_the_list_and_writes_nothing_by_default(tmp_path, capsys):
    root = tmp_path / "js"
    populate(root)
    assert fu.main(["--root", str(root), "--today", TODAY.isoformat()]) == 0
    out = capsys.readouterr().out
    assert "Follow-ups due 2026-09-28: 2" in out and "acme-ai-202609" in out and "maya-chen" in out
    assert "No follow-ups left" in out and "zeta-ai-202609" in out
    assert "drafts nothing" in out
    assert not (root / "state" / "follow-ups").exists()


def test_write_saves_one_dated_file_atomically(tmp_path):
    root = tmp_path / "js"
    populate(root)
    assert fu.main(["--root", str(root), "--today", TODAY.isoformat(), "--write"]) == 0
    path = root / "state" / "follow-ups" / "2026-09-28.md"
    meta, body = fm.load(path)
    assert meta["counts"] == {"due": 2, "exhausted": 1, "windows": 0}
    assert "acme-ai-202609" in body and "\u2014" not in body
    assert not list((root / "state" / "follow-ups").glob(".tmp-*"))


def test_json_output(tmp_path, capsys):
    root = tmp_path / "js"
    populate(root)
    fu.main(["--root", str(root), "--today", TODAY.isoformat(), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert len(data["due"]) == 2 and data["date"] == "2026-09-28"


def test_empty_department_says_nothing_due(tmp_path, capsys):
    root = tmp_path / "js"
    root.mkdir()
    assert fu.main(["--root", str(root), "--today", TODAY.isoformat()]) == 0
    assert "Follow-ups due 2026-09-28: 0" in capsys.readouterr().out


# ----------------------------------------------------------------------------- portal threads, grouped by portal
def _touches(root, rows):
    path = root / "touches" / "2026-09.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _t(at, prospect, direction, thread, portal, kind="message", status=None):
    return {"at": at, "prospect": prospect, "channel": "email", "direction": direction, "kind": kind,
            "status": status or ("sent" if direction == "out" else "received"), "thread_id": thread, "portal": portal}


def test_a_silent_portal_thread_is_due_after_four_business_days(tmp_path):   # four business days
    root = tmp_path / "js"
    root.mkdir()
    _touches(root, [_t("2026-09-17T17:00:00Z", "ari-acme", "in", "w1", "wellfound"),
                    _t("2026-09-18T17:00:00Z", "ari-acme", "out", "w1", "wellfound"),            # Fri; +4 bd = 09-24
                    _t("2026-09-24T17:00:00Z", "bo-beta", "out", "w2", "wellfound"),             # +4 bd = 09-30: not yet
                    _t("2026-09-10T17:00:00Z", "cy-zeta", "out", "l1", "linkedin"),
                    _t("2026-09-12T17:00:00Z", "cy-zeta", "in", "l1", "linkedin")])             # they replied
    res = fu.collect(root, TODAY)
    rows = [r for r in res["due"] if r["kind"] == "portal-thread"]
    assert [(r["who"], r["portal"], r["thread"], r["due"], r["nudge"]) for r in rows] == [
        ("ari-acme", "wellfound", "w1", "2026-09-24", 1)]


def test_three_follow_ups_in_a_thread_and_it_is_exhausted(tmp_path):
    root = tmp_path / "js"
    root.mkdir()
    _touches(root, [_t("2026-08-20T17:00:00Z", "dee", "out", "x1", "indeed"),
                    _t("2026-08-28T17:00:00Z", "dee", "out", "x1", "indeed", kind="follow_up"),
                    _t("2026-09-05T17:00:00Z", "dee", "out", "x1", "indeed", kind="follow_up"),
                    _t("2026-09-12T17:00:00Z", "dee", "out", "x1", "indeed", kind="follow_up")])
    res = fu.collect(root, TODAY)
    assert not [r for r in res["due"] if r["kind"] == "portal-thread"]
    assert [(r["kind"], r["who"], r["portal"]) for r in res["exhausted"]] == [("portal-thread", "dee", "indeed")]


def test_a_prospect_nudge_takes_its_portal_and_is_not_listed_twice(tmp_path):
    root = tmp_path / "js"
    populate(root)
    _touches(root, [_t("2026-09-18T17:00:00Z", "maya-chen", "out", "h1", "wellfound")])
    res = fu.collect(root, TODAY)
    maya = [r for r in res["due"] if r["who"] == "maya-chen"]
    assert len(maya) == 1 and maya[0]["kind"] == "prospect" and maya[0]["portal"] == "wellfound"


def test_the_list_is_grouped_by_portal(tmp_path, capsys):
    root = tmp_path / "js"
    populate(root)
    _touches(root, [_t("2026-09-18T17:00:00Z", "ari-acme", "out", "w1", "wellfound")])
    fu.main(["--root", str(root), "--today", TODAY.isoformat()])
    out = capsys.readouterr().out
    assert "By portal: wellfound 1, no portal 2" in out
    assert out.index("[wellfound]") < out.index("[no portal]")


# ----------------------------------------------------------------------------- portal messages that need an answer
def _mail(root, rows, name="gmail-2026-09-27.classified.jsonl"):
    path = root / "state" / "harvest" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _m(mid, frm, label, at, thread, portal=None, direction="in", subject="Hello"):
    return {"id": mid, "from": frm, "from_email": frm.split("<")[-1].rstrip(">"), "subject": subject,
            "internal_date": at, "thread_id": thread, "direction": direction, "portal": portal,
            "classification": {"label": label, "confidence": 0.9, "identity": {"company": "Acme"}}}


def test_portal_messages_needing_an_answer_are_due_with_their_portal(tmp_path):
    root = tmp_path / "js"
    root.mkdir()
    _mail(root, [
        _m("m1", "Ari <ari@wellfound.us>", "founder_outreach", "2026-09-25T17:00:00Z", "t1", portal="wellfound"),
        _m("m2", "no-reply@greenhouse-mail.io", "interview_scheduling", "2026-09-26T17:00:00Z", "t2"),
        _m("m3", "Jane <jane@acme.test>", "recruiter_outreach", "2026-09-26T18:00:00Z", "t3"),
        _m("m4", "Dana <messages-noreply@linkedin.com>", "recruiter_outreach", "2026-09-20T17:00:00Z", "t4", portal="linkedin"),
        _m("m4r", "Alex <alex@example.test>", "other", "2026-09-21T17:00:00Z", "t4", direction="out"),   # we answered
        _m("m5", "jobs@indeed.com", "job_alert", "2026-09-26T17:00:00Z", "t5", portal="indeed"),
        _m("m6", "no-reply@greenhouse-mail.io", "ats_confirmation", "2026-09-26T17:00:00Z", "t6"),
        _m("m7", "Old <old@acme.test>", "recruiter_outreach", "2026-07-20T17:00:00Z", "t7"),                        # over 30 days
        _m("m8", "Prize <win@prize.test>", "suspected_scam", "2026-09-26T17:00:00Z", "t8"),
    ])
    res = fu.collect(root, TODAY)
    rows = {r["thread"]: r for r in res["due"] if r["kind"] == "portal-message"}
    assert set(rows) == {"t1", "t2", "t3"}
    assert rows["t1"]["portal"] == "wellfound" and rows["t1"]["due"] == "2026-09-25"
    assert rows["t2"]["portal"] == "unknown sender: confirm on first alert"
    assert rows["t3"]["portal"] == "email"


def test_an_out_touch_in_the_thread_answers_it(tmp_path):
    root = tmp_path / "js"
    root.mkdir()
    _mail(root, [_m("m1", "Ari <ari@wellfound.us>", "founder_outreach", "2026-09-25T17:00:00Z", "t1", portal="wellfound")])
    _touches(root, [_t("2026-09-26T10:00:00Z", "ari", "out", "t1", "wellfound")])
    res = fu.collect(root, TODAY)
    assert not [r for r in res["due"] if r["kind"] == "portal-message"]


def test_the_same_message_in_two_harvest_files_is_listed_once(tmp_path):
    root = tmp_path / "js"
    root.mkdir()
    row = _m("m1", "Ari <ari@wellfound.us>", "founder_outreach", "2026-09-25T17:00:00Z", "t1", portal="wellfound")
    _mail(root, [row], "gmail-2026-09-26.classified.jsonl")
    _mail(root, [row], "gmail-2026-09-28.classified.jsonl")
    assert len([r for r in fu.collect(root, TODAY)["due"] if r["kind"] == "portal-message"]) == 1


def test_the_render_says_answer_owed_and_drafts_only(tmp_path, capsys):
    root = tmp_path / "js"
    root.mkdir()
    _mail(root, [_m("m2", "no-reply@greenhouse-mail.io", "interview_scheduling", "2026-09-26T17:00:00Z", "t2",
                    subject="Schedule your interview")])
    fu.main(["--root", str(root), "--today", TODAY.isoformat()])
    out = capsys.readouterr().out
    assert "[unknown sender: confirm on first alert]" in out and "answer owed" in out
    assert "Schedule your interview" in out and "drafts nothing" in out


def test_bulk_mail_and_calendar_notices_owe_no_answer(tmp_path):
    root = tmp_path / "js"
    root.mkdir()
    blast = _m("b1", "jobs@alerts.jobot.com", "recruiter_outreach", "2026-09-26T17:00:00Z", "tb")
    blast["list_unsubscribe"] = "<mailto:unsubscribe@jobot.com>"
    _mail(root, [blast,
                 _m("c1", "x@acme.test", "interview_scheduling", "2026-09-26T17:00:00Z", "tc1", subject="Canceled event: Chat @ Mon"),
                 _m("c2", "y@acme.test", "interview_scheduling", "2026-09-26T17:00:00Z", "tc2", subject="Appointment booked: Intro Call"),
                 _m("c3", "z@acme.test", "interview_scheduling", "2026-09-26T17:00:00Z", "tc3", subject="Accepted: Chat @ Mon"),
                 _m("ok", "r@acme.test", "interview_invite", "2026-09-26T17:00:00Z", "tok", subject="Interview with Acme")])
    rows = [r["thread"] for r in fu.collect(root, TODAY)["due"] if r["kind"] == "portal-message"]
    assert rows == ["tok"]


def test_a_portal_relay_with_list_unsubscribe_still_owes_an_answer(tmp_path):
    """LinkedIn InMail carries List-Unsubscribe (as a real InMail does): only direct bulk mail is skipped."""
    root = tmp_path / "js"
    root.mkdir()
    inmail = _m("i1", "Dana <inmail-hit-reply@linkedin.com>", "recruiter_outreach", "2026-09-26T17:00:00Z", "ti")
    inmail["list_unsubscribe"] = "<https://www.linkedin.com/unsub>"
    plat = root / "targets" / "platforms.yaml"
    plat.parent.mkdir(parents=True)
    plat.write_text("platforms:\n  - id: linkedin\n    alert_senders: [jobalerts-noreply@linkedin.com]\n")
    _mail(root, [inmail])
    rows = [r for r in fu.collect(root, TODAY)["due"] if r["kind"] == "portal-message"]
    assert [(r["thread"], r["portal"]) for r in rows] == [("ti", "linkedin")]
