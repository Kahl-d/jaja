"""gmail_send.py: approval-gated Gmail sends and drafts with local attachments.

Every Gmail call goes to FakeGmail; nothing touches the network and nothing is ever sent."""
from __future__ import annotations

import base64
import email
import hashlib
import json
from datetime import date
from email import policy

import pytest

from comms_helpers import offline  # noqa: F401  (autouse: no network; puts scripts/ on sys.path)
import gmail_send as gs
import js_common as jc
import js_comms_util as cu

TODAY = date(2026, 9, 29)
BODY = "Hi Sam,\n\nThanks for the call today. My resume is attached as a PDF.\n\nAlex"
REC = {"id": "18c9a1", "thread_id": "t-777", "message_id": "<abc@mail.recruiter.example.test>",
       "references": "<root@mail.recruiter.example.test>", "subject": "MTS AI role", "from_email": "sam@recruiter.example.test",
       "reply_to": "", "from": "Sam Recruiter <sam@recruiter.example.test>"}


class _Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeGmail:
    """users().messages().send and users().drafts().create; records every call."""

    def __init__(self, fail=None):
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail

    def users(self):
        return self

    def messages(self):
        return _Endpoint(self, "send")

    def drafts(self):
        return _Endpoint(self, "draft")


class _Endpoint:
    def __init__(self, svc, kind):
        self.svc, self.kind = svc, kind

    def _go(self, **kw):
        self.svc.calls.append((self.kind, kw))

        def run():
            if self.svc.fail:
                raise self.svc.fail
            n = len(self.svc.calls)
            if self.kind == "send":
                return {"id": f"m-{n}", "threadId": kw.get("body", {}).get("threadId") or f"t-new-{n}"}
            return {"id": f"r-{n}", "message": {"id": f"m-{n}", "threadId": "t-draft"}}
        return _Call(run)

    def send(self, **kw):
        return self._go(**kw)

    def create(self, **kw):
        return self._go(**kw)


@pytest.fixture
def roots(tmp_path):
    js = tmp_path / "os" / gs.JS_PREFIX
    js.mkdir(parents=True)
    return cu.Roots(js=js, os=tmp_path / "os")


@pytest.fixture
def pdf(tmp_path):
    p = tmp_path / "Downloads" / "AlexExample_Resume.pdf"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"%PDF-1.7\n" + b"x" * 5000 + b"\n%%EOF\n")
    return p


def _approve(path):
    cu.set_frontmatter(path, status="approved", decided_at="2026-09-29", decided_via="chat")


def _decode(raw: str) -> email.message.EmailMessage:
    return email.message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)), policy=policy.default)


def _reply(pdf, **kw):
    args = dict(body=BODY, attachments=[pdf], reply_to=REC)
    args.update(kw)
    return gs.build_action(**args)


# --------------------------------------------------------------------------- build
def test_a_reply_threads_on_the_harvested_message_and_hashes_every_attachment(pdf):
    act = _reply(pdf)
    assert act["action"] == "send" and act["to"] == ["sam@recruiter.example.test"] and act["cc"] == []
    assert act["subject"] == "Re: MTS AI role" and act["thread_id"] == "t-777"
    assert act["in_reply_to"] == "<abc@mail.recruiter.example.test>"
    assert act["references"] == "<root@mail.recruiter.example.test> <abc@mail.recruiter.example.test>"
    [att] = act["attachments"]
    assert att == {"path": str(pdf.resolve()), "name": "AlexExample_Resume.pdf", "mime": "application/pdf",
                   "bytes": pdf.stat().st_size, "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()}


def test_a_reply_goes_to_reply_to_when_the_sender_set_one_and_keeps_an_existing_re(pdf):
    act = _reply(pdf, reply_to=dict(REC, reply_to="Talent Team <talent@recruiter.example.test>", subject="Re: MTS AI role"))
    assert act["to"] == ["talent@recruiter.example.test"] and act["subject"] == "Re: MTS AI role"


def test_a_new_message_needs_recipients_and_a_subject(pdf):
    act = gs.build_action(to=["Ana@Acme.test"], cc=["b@x.test"], subject="Resume", body=BODY, attachments=[pdf],
                          mode="draft")
    assert act["action"] == "draft" and act["to"] == ["ana@acme.test"] and act["thread_id"] is None
    for bad, match in ((dict(to=[]), "recipient"), (dict(to=["nope"]), "not an email"), (dict(subject=" "), "subject"),
                       (dict(body="  "), "empty")):
        args = dict(to=["a@b.test"], subject="Resume", body=BODY, attachments=[])
        args.update(bad)
        with pytest.raises(gs.MailError, match=match):
            gs.build_action(**args)


def test_the_voice_gate_runs_on_the_body(pdf):
    with pytest.raises(gs.MailError, match="em-dash"):
        _reply(pdf, body="Hi Sam \u2014 resume attached.\n\nAlex")


@pytest.mark.parametrize("where", ["missing", "folder", "secret"])
def test_attachments_must_be_real_files_outside_the_credential_folders(tmp_path, monkeypatch, where):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setenv("JAJA_CONFIG_DIR", str(cfg))
    path = {"missing": tmp_path / "gone.pdf", "folder": tmp_path, "secret": cfg / "gmail_token_send.json"}[where]
    if where == "secret":
        path.write_text("{}")
    with pytest.raises(gs.MailError, match="attachment"):
        gs.build_action(to=["a@b.test"], subject="x", body=BODY, attachments=[path])


def test_the_whole_message_must_fit_gmail_s_limit(pdf, monkeypatch):
    monkeypatch.setattr(gs, "MAX_MESSAGE_BYTES", 3000)
    with pytest.raises(gs.MailError, match="too large"):
        _reply(pdf)


def test_request_files_an_approval_whose_exact_content_is_the_action(roots, pdf):
    aid, path = gs.request(roots, _reply(pdf, prospect="sam-recruiter"), today=TODAY)
    appr = cu.parse_approval(path)
    assert aid == "A-2026-0001" and appr.hash_ok and appr.meta["action"] == "send-email"
    assert gs.approved_action(appr)["attachments"][0]["name"] == "AlexExample_Resume.pdf"
    text = path.read_text()
    assert "sam@recruiter.example.test" in appr.meta["target"] and "cannot be recalled" in text
    assert "AlexExample_Resume.pdf" in text and "Re: MTS AI role" in text


def test_show_renders_the_exact_action_for_the_owner(pdf):
    out = gs.render(_reply(pdf))
    for part in ("SEND", "To: sam@recruiter.example.test", "Subject: Re: MTS AI role", "In reply to: thread t-777",
                 "AlexExample_Resume.pdf", "Thanks for the call today.", "cannot be recalled"):
        assert part in out, part


# --------------------------------------------------------------------------- execute
def test_execute_refuses_an_unapproved_approval_before_anything_is_sent(roots, pdf):
    _, path = gs.request(roots, _reply(pdf), today=TODAY)
    svc = FakeGmail()
    with pytest.raises(gs.MailError, match="not approved"):
        gs.execute(svc, path, today=TODAY)
    assert svc.calls == []


def test_execute_sends_exactly_the_approved_message_once_in_its_thread(roots, pdf):
    _, path = gs.request(roots, _reply(pdf, prospect="sam-recruiter"), today=TODAY)
    _approve(path)
    svc = FakeGmail()
    out = gs.execute(svc, path, today=TODAY)
    [(kind, call)] = svc.calls
    assert kind == "send" and call["userId"] == "me" and call["body"]["threadId"] == "t-777"
    msg = _decode(call["body"]["raw"])
    assert msg["To"] == "sam@recruiter.example.test" and msg["Subject"] == "Re: MTS AI role"
    assert msg["In-Reply-To"] == "<abc@mail.recruiter.example.test>" and "<abc@mail.recruiter.example.test>" in msg["References"]
    assert msg["From"] is None and msg["Bcc"] is None                      # Gmail sets From; no hidden recipients
    [part] = [p for p in msg.iter_attachments()]
    assert part.get_filename() == "AlexExample_Resume.pdf" and part.get_content() == pdf.read_bytes()
    assert "Thanks for the call today." in msg.get_body(("plain",)).get_content()
    assert out["message_id"] == "m-1" and out["thread_id"] == "t-777"
    text = path.read_text()
    assert "gmail send started for" in text and "gmail send m-1 done for" in text
    again = gs.execute(svc, path, today=TODAY)
    assert again["existing"] is True and len(svc.calls) == 1
    [touch] = list(jc.iter_touches(roots.js))
    assert (touch["prospect"], touch["direction"], touch["status"], touch["message_id"]) == \
        ("sam-recruiter", "out", "sent", "m-1")


def test_an_attachment_changed_after_approval_is_refused(roots, pdf):
    _, path = gs.request(roots, _reply(pdf), today=TODAY)
    _approve(path)
    pdf.write_bytes(pdf.read_bytes() + b"edited")
    svc = FakeGmail()
    with pytest.raises(gs.MailError, match="changed after"):
        gs.execute(svc, path, today=TODAY)
    assert svc.calls == []


def test_a_send_that_started_without_a_confirmation_is_never_repeated(roots, pdf):
    _, path = gs.request(roots, _reply(pdf), today=TODAY)
    _approve(path)
    with pytest.raises(RuntimeError, match="boom"):
        gs.execute(FakeGmail(fail=RuntimeError("boom")), path, today=TODAY)
    svc = FakeGmail()
    with pytest.raises(gs.MailError, match="Sent folder"):
        gs.execute(svc, path, today=TODAY)
    assert svc.calls == []


def test_draft_mode_creates_a_draft_and_never_sends(roots, pdf):
    _, path = gs.request(roots, _reply(pdf, mode="draft"), today=TODAY)
    _approve(path)
    svc = FakeGmail()
    out = gs.execute(svc, path, today=TODAY)
    [(kind, call)] = svc.calls
    assert kind == "draft" and call["body"]["message"]["threadId"] == "t-777" and out["draft_id"] == "r-1"
    assert "Drafts" in gs.effect_text(_reply(pdf, mode="draft"))


def test_a_large_message_goes_through_the_resumable_upload(roots, pdf, monkeypatch):
    monkeypatch.setattr(gs, "INLINE_LIMIT", 1000)
    made = []
    monkeypatch.setattr(gs, "_media", lambda data: made.append(len(data)) or "MEDIA")
    _, path = gs.request(roots, _reply(pdf), today=TODAY)
    _approve(path)
    svc = FakeGmail()
    gs.execute(svc, path, today=TODAY)
    [(_, call)] = svc.calls
    assert call["media_body"] == "MEDIA" and "raw" not in call["body"] and call["body"]["threadId"] == "t-777"
    assert made and made[0] > 1000


def test_the_token_kind_follows_the_mode():
    assert gs.token_kind({"action": "send"}) == "send" and gs.token_kind({"action": "draft"}) == "compose"


def test_cli_checks_the_approval_before_loading_any_token(roots, pdf, capsys, monkeypatch):
    _, path = gs.request(roots, _reply(pdf), today=TODAY)
    monkeypatch.setattr(gs, "gmail_service", lambda kind: pytest.fail("token loaded for an unapproved approval"))
    assert gs.main(["execute", "--approval", str(path), "--root", str(roots.js), "--os-root", str(roots.os),
                    "--today", "2026-09-29"]) == 2
    assert "not approved" in capsys.readouterr().out


def test_cli_compose_from_a_harvest_prints_and_requests(roots, pdf, tmp_path, capsys):
    harvest = tmp_path / "h.jsonl"
    harvest.write_text(json.dumps(REC) + "\n")
    body = tmp_path / "body.txt"
    body.write_text(BODY)
    base = ["compose", "--root", str(roots.js), "--os-root", str(roots.os), "--today", "2026-09-29",
            "--harvest", str(harvest), "--message-id", "18c9a1", "--body-file", str(body), "--attach", str(pdf)]
    assert gs.main(base) == 0
    assert "Subject: Re: MTS AI role" in capsys.readouterr().out
    assert not roots.approvals.exists() or not list(roots.approvals.glob("A-*.md"))
    assert gs.main(base + ["--request"]) == 0
    assert len(list(roots.approvals.glob("A-*.md"))) == 1


def test_an_approved_file_with_the_item_unticked_sends_nothing(roots, pdf):
    _, path = gs.request(roots, _reply(pdf), today=TODAY)
    _approve(path)
    item = cu.parse_approval(path).items[0]["id"]
    path.write_text(path.read_text().replace(f"- [x] {item}", f"- [ ] {item}"))
    svc = FakeGmail()
    with pytest.raises(gs.MailError, match="unticked"):
        gs.execute(svc, path, today=TODAY)
    assert svc.calls == []
