"""follow_ups.py never lists a prospect on do-not-contact (a held recruiter would otherwise still show as
'answer owed', because portal messages are found by mail thread, not by prospect)."""
from __future__ import annotations

import json
from datetime import date

from disc_harness import *  # noqa: F401,F403

import follow_ups as fu  # noqa: E402
import js_common as jc  # noqa: E402
import prospects as pr  # noqa: E402

TODAY = date(2026, 9, 28)


def _prospect(root, pid, name, emails, dnc=None):
    m = pr.new_meta(pid, name, source="gmail-backlog", today=TODAY)
    m.update(emails=emails, do_not_contact_reason=dnc)
    jc.write_record(root / "prospects" / f"{pid}.md", m, "# x\n\n## Timeline\n")


def _m(mid, frm, at, thread):
    return {"id": mid, "from": frm, "from_email": frm.split("<")[-1].rstrip(">"), "subject": "Hello",
            "internal_date": at, "thread_id": thread, "direction": "in", "portal": None,
            "classification": {"label": "recruiter_outreach", "confidence": 0.9, "identity": {"company": "Acme"}}}


def _setup(root):
    _prospect(root, "stella-example", "Stella Example", ["stella@acme.test"],
              "on hold until the owner says")
    _prospect(root, "ada-example", "Ada Example", [], "on hold until the owner says")
    _prospect(root, "jane-doe", "Jane Doe", ["jane@acme.test"])
    rows = [_m("a", "Stella Example <stella@acme.test>", "2026-09-26T17:00:00Z", "t1"),
            _m("b", "Ada Example <inmail-hit-reply@linkedin.com>", "2026-09-26T17:00:00Z", "t2"),
            _m("c", "Cal Example <inmail-hit-reply@linkedin.com>", "2026-09-26T17:00:00Z", "t3"),
            _m("d", "Jane Doe <jane@acme.test>", "2026-09-26T17:00:00Z", "t4")]
    path = root / "state" / "harvest" / "gmail-2026-09-27.classified.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_held_prospects_are_left_out_by_email_and_by_relay_name(tmp_path):
    root = tmp_path / "js"
    root.mkdir()
    _setup(root)
    res = fu.collect(root, TODAY)
    threads = {r["thread"] for r in res["due"] if r["kind"] == "portal-message"}
    assert threads == {"t3", "t4"}                     # the held direct sender and the held InMail name are out
    assert res["no_answer_owed"]["on hold (do not contact)"] == 2


def test_lifting_the_hold_brings_them_back(tmp_path):
    root = tmp_path / "js"
    root.mkdir()
    _setup(root)
    for pid in ("stella-example", "ada-example"):
        path = root / "prospects" / f"{pid}.md"
        meta, body = jc.fm.load(path)
        meta["do_not_contact_reason"] = None
        jc.write_record(path, meta, body)
    threads = {r["thread"] for r in fu.collect(root, TODAY)["due"] if r["kind"] == "portal-message"}
    assert threads == {"t1", "t2", "t3", "t4"}
