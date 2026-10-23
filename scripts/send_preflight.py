#!/usr/bin/env python3
"""Send preflight and read-back: nothing goes out until the text and the transport pass.

    python3 scripts/send_preflight.py check  --channel email --transport connector|gmail-ui|gmail_send \
        --to a@example.test [--to ...] --subject "..." --body-file F [--attach P ...] [--name "Mike"] [--kind reply|intro|official]
    python3 scripts/send_preflight.py verify --sent-json F --body-file F [--to a@example.test ...] [--attach P ...] [--thread-id T]
    python3 scripts/send_preflight.py check  --channel linkedin --body-file F      (message text only)

`check` runs before the send and exits 1 on any BLOCK; `verify` runs on what the mailbox shows afterwards (the JSON of
a Gmail `get_message`) and exits 1 when what landed differs from what was approved. Both print one line per issue.

Why it exists: the Gmail connector rewrites every URL in HTML bodies into google.com/url?q=... links, Gmail
plain-text mode hard-wraps a mail at about 70 columns, and a send otherwise goes out without anyone looking at the
rendered result. The voice rules stay in outbox.lint_draft; this file adds the transport and render rules and calls
lint_draft so one command covers both. Personal values (own addresses, sign-off names, resume file pattern,
signature URLs, plain-text style) come from config: identity.emails, policy.send.own_addresses, style.signoffs,
identity.resume_file_pattern, identity.signature_urls, style.send.plain_text, style.no_em_dash, style.no_en_dash.

Codes: P1 rewritten or tracked link, P2 URL through the connector, P3 dashes, markdown, emoji, P4 hard-wrapped lines,
P5 subject, P6 recipients and greeting, P7 attachments, P8 voice and placeholders (outbox.lint_draft), P9 sign-off,
P10 invisible characters, P11 length (WARN), P20-P25 read-back.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import agency_config as cfg


def _strs(path: str) -> list[str]:
    return [str(x).strip() for x in (cfg.get(path, []) or []) if str(x).strip()]


# Addresses that are the owner's own: a send to one of them is a mistake.
OWN_ADDRESSES = tuple(sorted({a.lower() for a in _strs("identity.emails") + _strs("policy.send.own_addresses")}))
EMAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
REWRITTEN = re.compile(r"google\.com/url\?|safelinks\.protection\.outlook|urldefense|mandrillapp\.com/track|"
                       r"[?&](?:utm_[a-z]+|gclid|fbclid|mc_eid|mkt_tok)=", re.I)
URL = re.compile(r"https?://|\bwww\.|\b[a-z0-9-]+\.(?:com|io|ai|net|org|dev|app)/", re.I)
MARKDOWN = re.compile(r"\*\*[^*\n]+\*\*|__[^_\n]+__|^#{1,6}\s|`[^`\n]+`|\[[^\]\n]+\]\(https?://[^)]+\)|^\s*[*-]\s+\S", re.M)
# The dash characters the owner's style forbids (style.no_em_dash, style.no_en_dash); none by default.
DASHES = tuple(ch for ch, key in (("\u2014", "style.no_em_dash"), ("\u2013", "style.no_en_dash")) if cfg.get(key, False))
PLAIN_TEXT = bool(cfg.get("style.send.plain_text", False))   # plain sentences only: no markdown, emoji, styled HTML
INVISIBLE = re.compile("[​‌‍⁠﻿  ]")
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐✅❌]")
_SIGNOFF_NAMES = _strs("style.signoffs") or _strs("identity.signature_names")
SIGNOFF = (re.compile(r"(?:^|\n)\s*(?:" + "|".join(re.escape(n) for n in sorted(_SIGNOFF_NAMES, key=len, reverse=True))
                      + r")\s*(?:\n|$)", re.I) if _SIGNOFF_NAMES else None)
ATTACH_WORD = re.compile(r"\battach(?:ed|ment)\b|\bresume is (?:attached|below)\b", re.I)
_PDF_PATTERN = str(cfg.get("identity.resume_file_pattern", "") or "")
PDF_NAME = re.compile(_PDF_PATTERN) if _PDF_PATTERN else None
SIGNATURE_URLS = tuple(_strs("identity.signature_urls"))    # links that belong in the owner's signature block
ANCHOR = re.compile(r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
MAX_ATTACH = 10 * 1024 * 1024
EMAIL_WORD_WARN = int(cfg.get("policy.send.email_word_warn", 250) or 250)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def hard_wrapped(text: str) -> bool:
    """True when a plain-text body breaks sentences mid-line at a near-constant width (Gmail plain-text mode)."""
    lines = text.split("\n")
    run = 0
    for cur, nxt in zip(lines, lines[1:]):
        c, n = cur.rstrip(), nxt.strip()
        if 48 <= len(c) <= 82 and n and not c.endswith((".", "!", "?", ":", ",")) and n[:1].islower() \
                and not c.lstrip().startswith(">"):
            run += 1
            if run >= 2:
                return True
        else:
            run = 0
    return False


def html_issues(html: str) -> list[str]:
    """The HTML mail shape: one <p> per paragraph, no inner <br> outside the signature, clean typed links, and (with
    style.send.plain_text) no styling or images."""
    out: list[str] = []
    if PLAIN_TEXT and re.search(r"<img\b|style=|<font\b|color=", html, re.I):
        out.append("BLOCK P12: images, colours, sizes or inline styles in the HTML (plain default font only)")
    sig_names = [n for n in _strs("identity.signature_names") if " " in n] or _strs("identity.signature_names")
    for para in re.findall(r"<p>(.*?)</p>", html, re.I | re.S):
        if re.search(r"<br\s*/?>", para, re.I) and not any(u in para for u in SIGNATURE_URLS) \
                and not any(n in para for n in sig_names):
            out.append("BLOCK P12: a <br> inside a paragraph (one <p> per paragraph, no inner line breaks)")
            break
    if not re.search(r"<p>|<div", html, re.I):
        out.append("BLOCK P12: the HTML has no <p> paragraphs")
    for href, shown in ANCHOR.findall(html):
        if REWRITTEN.search(href) or REWRITTEN.search(shown):
            out.append(f"BLOCK P1: link '{href[:60]}' is rewritten or tracked")
        elif not href.startswith(("https://", "mailto:")):
            out.append(f"BLOCK P12: link '{href[:60]}' is not https or mailto")
        elif href.startswith("http") and "linkedin.com/in/" in href and href not in SIGNATURE_URLS:
            out.append(f"WARN P12: LinkedIn link {href} is not the signature URL; check it is the intended profile")
    return out


def check_message(channel: str, body: str, *, transport: str = "connector", subject: str | None = None,
                  to: Iterable[str] = (), expected_to: Iterable[str] | None = None, name: str | None = None,
                  attachments: Iterable[Path | str] = (), kind: str | None = None, html: str | None = None,
                  lint: bool = True) -> list[str]:
    """Issues before a send. A line starting 'BLOCK' stops the send; 'WARN' is shown and does not stop it."""
    out: list[str] = []
    text = body or ""
    if not text.strip():
        return ["BLOCK P8: empty message"]
    both = text + "\n" + (html or "")
    if REWRITTEN.search(both):
        out.append("BLOCK P1: a rewritten or tracked link (google.com/url, safelinks, utm_...); use the clean URL or none")
    email = channel == "email"
    if email and transport in ("html-draft", "html-api"):
        out += html_issues(html or "")
        if transport == "html-draft":      # the connector rewrites even typed <a href> links in drafts
            out += [f"BLOCK P2: https link '{h[:50]}' in a connector draft (the connector rewrites it to google.com/url); "
                    f"drop it, or send through gmail_send.py (transport html-api)"
                    for h, _ in ANCHOR.findall(html or "") if h.startswith("http")]
        if not html:
            out.append("BLOCK P12: html-draft transport needs the HTML body")
        stray = [m for m in re.findall(r"https?://\S+|www\.\S+", text) if not any(m.startswith(u) for u in SIGNATURE_URLS)]
        if stray:
            out.append(f"WARN P2: URL in the plain-text part ({stray[0][:50]}); the HTML link must be the same clean URL")
    elif email and transport == "connector" and URL.search(text):
        out.append("BLOCK P2: a URL in a mail sent through the Gmail connector (it rewrites links); remove it, or send "
                   "with gmail_send.py or type it in Gmail")
    if any(d in text for d in DASHES):
        out.append("BLOCK P3: an em or en dash in the text (a style rule)")
    if PLAIN_TEXT and MARKDOWN.search(text):
        out.append("BLOCK P3: markdown in a message (**bold**, #, backticks, [x](url), '- ' bullets); write plain sentences")
    if PLAIN_TEXT and EMOJI.search(text) and kind != "scheduling":
        out.append("BLOCK P3: an emoji in the text")
    if INVISIBLE.search(text):
        out.append("BLOCK P10: invisible or non-breaking characters in the text (they render as odd spacing)")
    if email and transport in ("gmail-ui-plain", "gmail_send", "html-draft", "html-api", "connector") and hard_wrapped(text):
        out.append("BLOCK P4: lines are hard-wrapped near 70 columns; send one line per paragraph or in rich-text mode")
    elif email and transport == "gmail-ui" and hard_wrapped(text):
        out.append("WARN P4: the body looks hard-wrapped; check it renders full width")
    if email:
        s = (subject or "").strip()
        if not s:
            out.append("BLOCK P5: no subject")
        elif re.match(r"(?:re:\s*){2,}", s, re.I):
            out.append("BLOCK P5: the subject repeats 'Re:' (Re: Re:); keep one")
        elif len(s) > 100 or (s.isupper() and len(s) > 8):
            out.append("BLOCK P5: the subject is too long or all caps")
        recips = [a.strip().lower() for a in to]
        if not recips:
            out.append("BLOCK P6: no recipient")
        for a in recips:
            if not EMAIL.match(a):
                out.append(f"BLOCK P6: '{a}' is not a valid address")
            elif a in OWN_ADDRESSES:
                out.append(f"BLOCK P6: '{a}' is your own address")
        if expected_to is not None and sorted(set(recips)) != sorted({e.lower() for e in expected_to}):
            out.append(f"BLOCK P6: recipients {sorted(set(recips))} differ from the approved {sorted(expected_to)}")
        if name:
            m = re.match(r"\s*(?:hi|hey|hello|dear)\s+([A-Za-z.'\- ]{1,40}?)\s*[,!]", text, re.I)
            if m and not any(t.lower() in m.group(1).lower() for t in name.replace(".", " ").split() if len(t) > 1):
                out.append(f"BLOCK P6: the greeting names '{m.group(1)}' but the recipient is {name}")
        files = [Path(p) for p in attachments]
        for f in files:
            if not f.is_file() or f.stat().st_size == 0:
                out.append(f"BLOCK P7: attachment {f} is missing or empty")
            elif f.stat().st_size > MAX_ATTACH:
                out.append(f"BLOCK P7: attachment {f.name} is over 10 MB")
            elif f.suffix.lower() == ".pdf" and f.read_bytes()[:5] != b"%PDF-":
                out.append(f"BLOCK P7: {f.name} is not a PDF")
            elif f.suffix.lower() == ".pdf" and PDF_NAME and not PDF_NAME.match(f.name):
                out.append(f"WARN P7: attachment name '{f.name}' does not match identity.resume_file_pattern")
        if ATTACH_WORD.search(text) and not files:
            out.append("BLOCK P7: the text says something is attached but no file is")
        if files and not ATTACH_WORD.search(text):
            out.append("WARN P7: a file is attached and the text never mentions it")
        if SIGNOFF and not SIGNOFF.search(text[-320:]):
            out.append("BLOCK P9: the text does not end with a sign-off (" + " or ".join(f"'{n}'" for n in _SIGNOFF_NAMES) + ")")
        tail = text.strip().splitlines()[-4:]
        if transport not in ("html-api", "gmail_send") and any(URL.search(line) for line in tail):
            out.append("BLOCK P9: a URL in the signature lines (the connector rewrites it); drop it")
        if len(text.split()) > EMAIL_WORD_WARN and kind not in ("official",):
            out.append(f"WARN P11: {len(text.split())} words; a short note reads best under {EMAIL_WORD_WARN}")
    if lint:
        try:
            import outbox
            ch = "email" if email else channel
            out += [f"BLOCK P8: {i}" for i in outbox.lint_draft(ch, text, kind=kind)]
        except Exception as exc:                      # the voice gate is advisory if it cannot load; say so
            out.append(f"WARN P8: voice gate not run ({type(exc).__name__}: {exc})")
    return out


def verify_sent(sent: dict[str, Any], approved_body: str, *, to: Iterable[str] | None = None,
                attachments: Iterable[Path | str] = (), thread_id: str | None = None,
                subject: str | None = None) -> list[str]:
    """Issues in what the mailbox shows after the send (the dict of a Gmail get_message)."""
    out: list[str] = []
    body = sent.get("plaintextBody") or ""
    html = sent.get("htmlBody") or ""
    if REWRITTEN.search(body + html):
        out.append("BLOCK P20: the sent message contains a rewritten or tracked link")
    if hard_wrapped(re.split(r"\n\s*On .{5,80} wrote:|\n>", body, maxsplit=1)[0]):
        out.append("BLOCK P4: the sent plain-text part is hard-wrapped near 70 columns")
    if html:
        out += [i.replace("P12", "P26") for i in html_issues(html.split('<div class="gmail_quote', 1)[0]) if "<p>" not in i]
    got = _norm(re.split(r"\n\s*On .{5,80} wrote:|\n>", body, maxsplit=1)[0])
    if _norm(approved_body) and got != _norm(approved_body):
        out.append("BLOCK P24: the sent text differs from the approved text after whitespace is ignored")
    if to is not None and sorted(a.lower() for a in sent.get("toRecipients", [])) != sorted(a.lower() for a in to):
        out.append(f"BLOCK P21: sent to {sent.get('toRecipients')} not {sorted(to)}")
    files = [Path(p) for p in attachments]
    names = sorted(a.get("filename", "") for a in sent.get("attachments", []) if a.get("mimeType") != "image/jpeg")
    if names != sorted(f.name for f in files):
        out.append(f"BLOCK P22: attachments sent {names} differ from approved {sorted(f.name for f in files)}")
    if thread_id and sent.get("threadId") != thread_id:
        out.append(f"BLOCK P23: sent in thread {sent.get('threadId')}, expected {thread_id}")
    if subject and _norm(sent.get("subject")) != _norm(subject):
        out.append("WARN P25: the sent subject differs from the approved subject")
    return out


def _print(issues: list[str]) -> int:
    for i in issues:
        print(i)
    blocks = [i for i in issues if i.startswith("BLOCK")]
    print(f"preflight: {len(blocks)} block(s), {len(issues) - len(blocks)} warning(s)")
    return 1 if blocks else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--channel", default="email")
    c.add_argument("--transport", default="connector", choices=["connector", "gmail-ui", "gmail-ui-plain", "gmail_send", "html-draft", "html-api"])
    c.add_argument("--to", action="append", default=[])
    c.add_argument("--expect-to", action="append")
    c.add_argument("--subject")
    c.add_argument("--body-file", required=True)
    c.add_argument("--html-file")
    c.add_argument("--attach", action="append", default=[])
    c.add_argument("--name")
    c.add_argument("--kind")
    v = sub.add_parser("verify")
    v.add_argument("--sent-json", required=True)
    v.add_argument("--body-file", required=True)
    v.add_argument("--to", action="append")
    v.add_argument("--attach", action="append", default=[])
    v.add_argument("--thread-id")
    v.add_argument("--subject")
    a = ap.parse_args(argv)
    body = Path(a.body_file).read_text(encoding="utf-8")
    if a.cmd == "check":
        return _print(check_message(a.channel, body, transport=a.transport, subject=a.subject, to=a.to,
                                    expected_to=a.expect_to, name=a.name, attachments=a.attach, kind=a.kind,
                                    html=Path(a.html_file).read_text(encoding="utf-8") if a.html_file else None))
    sent = json.loads(Path(a.sent_json).read_text(encoding="utf-8"))
    return _print(verify_sent(sent, body, to=a.to, attachments=a.attach, thread_id=a.thread_id, subject=a.subject))


if __name__ == "__main__":
    sys.exit(main())
