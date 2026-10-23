"""outbox.py kind `official`: mail to the DSO, an attorney or an employer's HR (legal-visa's drafts).

Not a job prospect: no prospect record is needed, created or changed, and no touch is logged; the draft
still goes through the same approval file, decision and mark-sent path as every other email."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import comms_helpers  # noqa: F401  (puts scripts/ on sys.path)
from comms_helpers import offline  # noqa: F401  (autouse: no network)
import js_common as jc
import js_comms_util as cu
import outbox as ob

TODAY = date(2026, 9, 25)
BODY = ("Dear DoIE team,\n\nMy employment with Acme Corp ends on 2026-10-02 while my STEM OPT extension "
        "is still pending. Could you tell me what I need to do?\n\nThank you,\nAlex Example")
SUBJECT = "STEM OPT: employment ending 2026-10-02 with the extension pending"


@pytest.fixture
def roots(tmp_path):
    js = tmp_path / "os" / "domains" / "career" / "projects" / "job-search"
    (js / "prospects").mkdir(parents=True)
    return cu.Roots(js=js, os=tmp_path / "os")


def _official(roots, **kw):
    args = dict(prospect=None, channel="email", kind="official", to="dso@example.test", subject=SUBJECT, body=BODY,
                today=TODAY)
    args.update(kw)
    return ob.create_draft(roots, **args)


def test_an_official_email_needs_no_prospect_and_creates_none(roots):
    meta, body = jc.read_record(_official(roots))
    assert (meta["kind"], meta["prospect"], meta["to"], meta["subject"], meta["status"]) == (
        "official", None, "dso@example.test", SUBJECT, "drafted")
    assert meta["thread_id"] is None and body.startswith("Dear DoIE team")
    assert list(roots.prospects.glob("*.md")) == []


@pytest.mark.parametrize("kw,needle", [
    ({"prospect": "maya-chen"}, "never links a job prospect"),
    ({"to": None}, "--to"), ({"subject": None}, "--subject"),
    ({"channel": "linkedin-message"}, "by email")])
def test_official_mail_is_refused_without_its_parts(roots, kw, needle):
    with pytest.raises(ob.OutboxError, match=needle):
        _official(roots, **kw)


def test_other_email_without_a_prospect_is_still_refused(roots):
    with pytest.raises(ob.OutboxError, match="needs --prospect"):
        ob.create_draft(roots, prospect=None, channel="email", kind="reply", to="x@y.test", subject="Hi",
                        body="Hi there", today=TODAY)


def test_official_mail_keeps_the_voice_rules(roots):
    d = _official(roots, body=BODY.replace("pending.", "pending — what now?"))
    with pytest.raises(ob.OutboxError, match="em-dash"):
        ob.request_approval(roots, [d.stem], today=TODAY)


def test_official_approval_is_requested_by_legal_visa_with_its_own_why(roots, capsys):
    d = _official(roots)
    assert ob.main(["batch", "--ids", d.stem, "--ticket", "T-2026-0001", "--root", str(roots.js),
                    "--os-root", str(roots.os), "--today", "2026-09-25"]) == 0
    appr = cu.parse_approval(Path(json.loads(capsys.readouterr().out)[0]["path"]))
    assert (appr.meta["action"], appr.meta["requested_by"], appr.meta["ticket"]) == (
        "send-email", "role:legal-visa", "T-2026-0001")
    assert appr.meta["items"][0]["prospect"] is None and appr.meta["items"][0]["target"] == "dso@example.test"
    why = cu.section_text(appr.body, "Why this is being sent")
    assert "legal-visa" in why and "not a job prospect" in why and "backlog" not in why
    assert "dso@example.test" in cu.section_text(appr.body, "Exact content")


def test_official_mail_is_decided_and_recorded_sent_without_a_prospect_or_touch(roots):
    d = _official(roots)
    [(aid, path)] = ob.request_approval(roots, [d.stem], requested_by="role:legal-visa", today=TODAY)
    cu.set_frontmatter(path, status="approved", decided_at="2026-09-25", decided_via="desktop")
    report = ob.apply_decisions(roots, path, today=TODAY)
    assert report["approved"] == [d.stem]
    res = ob.mark_sent(roots, d.stem, today=TODAY)
    assert (res["status"], res["touch_logged"]) == ("sent", False)
    assert not (roots.js / "touches").exists() and list(roots.prospects.glob("*.md")) == []
    assert "recorded as sent" in path.read_text()


def test_cli_draft_official(roots, tmp_path, capsys):
    body = tmp_path / "dso.txt"
    body.write_text(BODY)
    assert ob.main(["draft", "--channel", "email", "--kind", "official", "--to", "dso@example.test", "--subject", SUBJECT,
                    "--body-file", str(body), "--root", str(roots.js), "--os-root", str(roots.os),
                    "--today", "2026-09-25"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["id"] == "O-2026-0001" and jc.read_record(roots.outbox / "O-2026-0001.md")[0]["kind"] == "official"
