"""The follow-up list must not bury the real recruiter inbound under mail that owes no reply: calendar invitations
and reminders (some of them personal appointments), a scheduling tool's booking confirmations, a LinkedIn connection notice, an ATS auto-confirmation from a no-reply address, talent
community digests and a recruiting firm's blast. Each class now owes no answer and is counted, never silently dropped;
a person who wrote about a role still owes one."""
from __future__ import annotations

from datetime import date

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import follow_ups as fu  # noqa: E402
from test_follow_ups import _m, _mail  # noqa: E402

TODAY = date(2026, 10, 3)
AT = "2026-10-01T17:00:00Z"


def _cal(row):
    row["has_calendar"] = True          # a real invitation carries its calendar part
    return row


def _rows(root):
    res = fu.collect(root, TODAY)
    return [r["thread"] for r in res["due"] if r["kind"] == "portal-message"], res


def test_mail_that_owes_no_reply_is_left_out_and_counted_by_reason(tmp_path):
    root = tmp_path / "js"
    root.mkdir()
    _mail(root, [
        _cal(_m("a", "Agent <agent@example.test>", "interview_scheduling", AT, "t-tour",
           subject="Invitation from an unknown sender: Tour at Example Apartments @ Sun Sep 13, 2026 3:30pm")),
        _cal(_m("b", "Tim <tim@example.test>", "interview_scheduling", AT, "t-inv",
           subject="Updated invitation: Alex x Tim | In person @ Sat Oct 3, 2026 11am")),
        _cal(_m("b2", "Tim <tim@example.test>", "interview_scheduling", AT, "t-inv2",
           subject="Updated invitation from an unknown sender: 15' chat (tim) between Example Co and Alex")),
        _m("c", "calendar-notification@google.com", "interview_scheduling", AT, "t-rem",
           subject="Reminder: Intro Call - Kim (Alex Example) @ Wed Sep 30, 2026 3:30pm"),
        _m("d", "hello@cal.com", "interview_scheduling", AT, "t-cal",
           subject="15' chat (tim) between Example Co and Alex M Example"),
        _m("e", "invitations@linkedin.com", "recruiter_outreach", AT, "t-li", portal="linkedin",
           subject="You have an invitation"),
        _m("f", "recruiting-no-reply@example.test", "recruiter_outreach", AT, "t-conf",
           subject="Thank you for your application to Example Co, Alex!"),
        _m("g", "user-1@acme.mail.clinchtalent.com", "recruiter_outreach", AT, "t-dig", portal="talent-communities",
           subject="Multimodal Perception: Here are some new roles at Acme Robotics"),
        _m("h", "jobs@alerts.staffing.example.test", "recruiter_outreach", AT, "t-blast",
           subject="Technical Consultant (Permanent/Direct Hire) opportunities need to be filled"),
        # real ones: a person wrote about a role
        _m("i", "Ada <inmail-hit-reply@linkedin.com>", "recruiter_outreach", AT, "t-inmail", portal="linkedin",
           subject="Building voice agents at Acme Labs"),
        _m("j", "Stella <stella@acme.test>", "recruiter_outreach", AT, "t-person",
           subject="Founding AI Engineer | SF / NYC"),
        _m("k", "no-reply@greenhouse-mail.io", "interview_scheduling", AT, "t-ats", subject="Schedule your interview"),
    ])
    threads, res = _rows(root)
    assert sorted(threads) == ["t-ats", "t-inmail", "t-person"]
    left = res["no_answer_owed"]
    assert left == {"calendar invitation or reminder": 4, "booking confirmation": 1, "LinkedIn connection notice": 1,
                    "no-reply sender": 1, "digest or blast": 2}


def test_the_render_names_what_was_left_out(tmp_path, capsys):
    root = tmp_path / "js"
    root.mkdir()
    _mail(root, [_m("e", "invitations@linkedin.com", "recruiter_outreach", AT, "t-li", portal="linkedin",
                    subject="You have an invitation")])
    fu.main(["--root", str(root), "--today", TODAY.isoformat()])
    out = capsys.readouterr().out
    assert "Owe no reply (left out): 1 (LinkedIn connection notice 1)" in out


def test_a_person_is_never_left_out_by_a_subject_alone(tmp_path):
    """A subject is never enough; each rule needs the sign of a machine."""
    root = tmp_path / "js"
    root.mkdir()
    ack = _m("n2", "no-reply@ashbyhq.com", "recruiter_outreach", "2026-10-02T17:00:00Z", "t-thread", subject="We got it")
    _mail(root, [
        _m("r1", "Sam <sam@acme.test>", "recruiter_outreach", AT, "t-reminder", subject="Reminder: are you still interested?"),
        _m("r2", "Lee <lee@example.test>", "referral_intro", AT, "t-note", subject="Saw new roles at Acme, want an intro?"),
        _m("r3", "Ana <ana@alerts.test>", "founder_outreach", AT, "t-founder", subject="Founding engineer at Alerts"),
        _m("r4", "Dana <messages-noreply@linkedin.com>", "recruiter_outreach", AT, "t-relay", portal="linkedin",
           subject="Dana sent you a message"),
        _m("n1", "Kim <kim@acme.test>", "recruiter_outreach", AT, "t-thread", subject="Quick chat?"),
        ack,
    ])
    threads, res = _rows(root)
    assert sorted(threads) == ["t-founder", "t-note", "t-relay", "t-reminder", "t-thread"]
    assert res["no_answer_owed"] == {}


def test_a_person_relayed_by_a_portal_is_never_bulk(tmp_path):
    """Review R2-1 and R2-2: an InMail or a Wellfound founder with a digest-like subject, and a founder at a domain named
    alerts, still owe a reply."""
    root = tmp_path / "js"
    root.mkdir()
    inmail = _m("p1", "Ada <inmail-hit-reply@linkedin.com>", "recruiter_outreach", AT, "t-in", portal="linkedin",
                subject="New roles at Acme Labs: building voice agents")
    inmail["list_unsubscribe"] = "<https://www.linkedin.com/unsub>"
    _mail(root, [inmail,
                 _m("p2", "Ari <ari@wellfound.us>", "founder_outreach", AT, "t-wf", portal="wellfound",
                    subject="Re: jobs for you at Acme"),
                 _m("p3", "Ceo <ceo@alerts.invalid>", "founder_outreach", AT, "t-uk", subject="Founding engineer")])
    threads, res = _rows(root)
    assert sorted(threads) == ["t-in", "t-uk", "t-wf"] and res["no_answer_owed"] == {}
