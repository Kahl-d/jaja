"""Send preflight: the transport and render rules a send must pass. Fixtures are the typical failures:
connector-rewritten signature links, a 70-column hard-wrapped plain-text mail, 'Re: Re:' subjects."""
from __future__ import annotations

import json

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; no-network guard)

import send_preflight as sp  # noqa: E402

CLEAN = """Hi Sam,

It has been far too long. I never thanked you properly for putting my resume in last year.

I'm looking for my next role again, and I would value your help. My resume is attached.

Alex"""


def codes(issues, level="BLOCK"):
    return sorted({i.split()[1].rstrip(":") for i in issues if i.startswith(level)})


def test_clean_message_passes(tmp_path):
    pdf = tmp_path / "AlexExample_Resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    out = sp.check_message("email", CLEAN, subject="Re: Example Role", to=["sam@example.test"], name="Sam Taylor",
                           attachments=[pdf], lint=False)
    assert codes(out) == []


def test_connector_rewritten_link_blocks():
    body = CLEAN.replace("Alex", "Alex\nhttps://www.google.com/url?q=http://linkedin.com/in/alex-example&source=gmail")
    assert "P1" in codes(sp.check_message("email", body, subject="Hi", to=["a@example.test"], attachments=[], lint=False))


def test_any_url_through_connector_blocks_but_not_via_gmail_send():
    body = "Hi Casey,\n\nMy profile is linkedin.com/in/alex-example.\n\nAlex"
    assert "P2" in codes(sp.check_message("email", body, subject="Hi", to=["a@example.test"], lint=False))
    assert "P2" not in codes(sp.check_message("email", body, transport="gmail_send", subject="Hi", to=["a@example.test"], lint=False))


def test_hard_wrapped_plain_text_blocks():
    wrapped = ("Hi Sam,\n\nIt has been far too long. I never thanked you properly for putting my\n"
               "resume in for the Example role in 2025. It didn't work out, but I\n"
               "still appreciated it and wanted to say so before anything else here.\n\nAlex")
    assert sp.hard_wrapped(wrapped)
    assert "P4" in codes(sp.check_message("email", wrapped, transport="gmail-ui-plain", subject="Hi", to=["a@example.test"], lint=False))
    assert not sp.hard_wrapped(CLEAN)


def test_subject_recipient_greeting_and_dashes():
    out = sp.check_message("email", "Hi Robin,\n\nSorry — I missed it.\n\nAlex", subject="Re: Re: Hi",
                           to=["alex@example.test"], name="Sam Taylor", lint=False)
    assert {"P3", "P5", "P6"} <= set(codes(out))


def test_attachment_rules(tmp_path):
    text = "Hi Sam,\n\nMy resume is attached.\n\nAlex"
    assert "P7" in codes(sp.check_message("email", text, subject="Hi", to=["a@example.test"], lint=False))
    bad = tmp_path / "AlexExample_Resume.pdf"
    bad.write_bytes(b"not a pdf")
    assert "P7" in codes(sp.check_message("email", text, subject="Hi", to=["a@example.test"], attachments=[bad], lint=False))


def test_markdown_emoji_signoff_and_signature_url():
    out = sp.check_message("email", "Hi A,\n\n**Bold** note \U0001F600\n\nThanks", subject="Hi", to=["a@example.test"], lint=False)
    assert {"P3", "P9"} <= set(codes(out))


def test_readback_catches_rewrite_and_text_drift(tmp_path):
    sent = {"plaintextBody": CLEAN + "\nhttps://www.google.com/url?q=x&source=gmail", "htmlBody": "",
            "toRecipients": ["sam@example.test"], "attachments": [], "threadId": "T1"}
    out = sp.verify_sent(sent, CLEAN, to=["sam@example.test"], thread_id="T1")
    assert {"P20", "P24"} <= set(codes(out))
    good = {"plaintextBody": CLEAN.replace("\n", "\n"), "htmlBody": "", "toRecipients": ["sam@example.test"],
            "attachments": [], "threadId": "T1"}
    assert codes(sp.verify_sent(good, CLEAN, to=["sam@example.test"], thread_id="T1")) == []
    wrong = dict(good, toRecipients=["x@example.test"], threadId="T2")
    assert {"P21", "P23"} <= set(codes(sp.verify_sent(wrong, CLEAN, to=["sam@example.test"], thread_id="T1")))


def test_cli_exit_codes(tmp_path):
    f = tmp_path / "body.txt"
    f.write_text("Hi A,\n\nSee https://www.google.com/url?q=x for it.\n\nAlex")
    assert sp.main(["check", "--body-file", str(f), "--to", "a@example.test", "--subject", "Hi"]) == 1
    g = tmp_path / "ok.txt"
    g.write_text("Hi A,\n\nThanks for the note.\n\nAlex")
    assert sp.main(["check", "--body-file", str(g), "--to", "a@example.test", "--subject", "Hi"]) == 0


GOOD_HTML = ('<p>Hi Sam,</p><p>It has been far too long.</p><p>Alex Example<br>AI Engineer<br>'
             '<a href="https://www.linkedin.com/in/alex-example">linkedin.com/in/alex-example</a></p>')


def test_html_api_accepts_clean_signature_links_but_connector_draft_does_not():
    text = "Hi Sam,\n\nIt has been far too long.\n\nAlex Example\nAI Engineer\nhttps://www.linkedin.com/in/alex-example"
    out = sp.check_message("email", text, transport="html-api", html=GOOD_HTML, subject="Hello", to=["a@example.test"], lint=False)
    assert codes(out) == []
    out = sp.check_message("email", text, transport="html-draft", html=GOOD_HTML, subject="Hello", to=["a@example.test"], lint=False)
    assert "P2" in codes(out)


def test_html_draft_without_links_passes():
    html = "<p>Hi Jordan sir,</p><p>Thanks.</p><p>Alex Example<br>AI Engineer</p>"
    out = sp.check_message("email", "Hi Jordan sir,\n\nThanks.\n\nAlex Example\nAI Engineer", transport="html-draft",
                           html=html, subject="Hello", to=["a@example.test"], name="Jordan Lee", lint=False)
    assert codes(out) == []


def test_html_draft_blocks_br_inside_paragraph_and_rewritten_links():
    bad = '<p>Hi Sam,<br>wrapped line</p><p>See <a href="https://www.google.com/url?q=x">x</a></p>'
    out = sp.check_message("email", "Hi Sam,\n\nAlex", transport="html-draft", html=bad, subject="Hello", to=["a@example.test"], lint=False)
    assert {"P1", "P12"} <= set(codes(out))


def test_greeting_accepts_title_and_last_name():
    ok = sp.check_message("email", "Hi Dr. Rivera,\n\nThanks.\n\nAlex", subject="Hello", to=["a@example.test"], name="Pat Rivera", lint=False)
    assert "P6" not in codes(ok)
    bad = sp.check_message("email", "Hi Robin,\n\nThanks.\n\nAlex", subject="Hello", to=["a@example.test"], name="Pat Rivera", lint=False)
    assert "P6" in codes(bad)
