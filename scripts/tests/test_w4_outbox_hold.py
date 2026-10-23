"""Held drafts are never sent: the outbox's first-touch hold. mark_sent checked only the status and the content hash,
yet a batch of approved first touches can fail first_touch_issues. Now mark_sent (and `outbox.py can-send`, and
gmail_send.execute) refuse a draft that is held (its `hold:` field, or a row of a state/outbox-*hold*.md file) or a
first touch that fails first_touch_issues (M17-M19). No network: comms_helpers blocks it; FakeGmail sends nothing."""
# ruff: noqa: F811  (the outbox suite's `roots` fixture is imported and used as each test's argument)
from __future__ import annotations

from datetime import date

import pytest

import comms_helpers  # noqa: F401
from comms_helpers import offline  # noqa: F401  (autouse: no network)
import js_comms_util as cu
import outbox as ob
from test_outbox import TODAY, _approved, roots  # noqa: F401  (the fixture and helpers of the outbox suite)


def _hold_file(roots, ids, released=()):
    path = roots.js / "state" / "outbox-first-touch-hold-2030-01-05.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [f"| {i} | approved | email | sam-rivera | lane-research | M19 |" for i in ids]
    rows += [f"| {i} | approved | email | sam-rivera | released 2030-01-06 | M19 |" for i in released]
    path.write_text("# First-touch HOLD\n\n| draft | status | channel | person | owner | why |\n|---|---|---|---|---|---|\n"
                    + "\n".join(rows) + "\n")


def test_a_draft_listed_on_a_hold_file_cannot_be_marked_sent(roots):
    d, _ = _approved(roots)
    _hold_file(roots, [d.stem])
    with pytest.raises(ob.OutboxError, match="on hold: listed in state/outbox-first-touch-hold-2030-01-05.md"):
        ob.mark_sent(roots, d.stem, at="2026-09-25T17:00:00Z", today=date(2026, 9, 25))
    assert cu.read_record(d)[0]["status"] == "approved"               # nothing recorded


def test_a_released_row_no_longer_holds_the_draft(roots):
    d, _ = _approved(roots)
    _hold_file(roots, ["O-2026-9999"], released=[d.stem])
    assert ob.mark_sent(roots, d.stem, at="2026-09-25T17:00:00Z", today=date(2026, 9, 25))["status"] == "sent"


def test_a_hold_field_on_the_draft_holds_it(roots):
    d, _ = _approved(roots)
    cu.set_frontmatter(d, hold="rewrite as a conversation")
    with pytest.raises(ob.OutboxError, match="on hold: rewrite"):
        ob.mark_sent(roots, d.stem, today=TODAY)


def test_an_approved_first_touch_that_fails_the_first_touch_check_is_refused(roots):
    d, _ = _approved(roots)                                   # the body says "reaching out" (M19 on a first touch)
    cu.set_frontmatter(d, kind="intro")                       # kind is not in the content hash: still "approved"
    with pytest.raises(ob.OutboxError, match="M19"):
        ob.mark_sent(roots, d.stem, today=TODAY)


def test_send_problems_is_empty_for_a_clean_reply(roots):
    d, _ = _approved(roots)
    meta, body = cu.read_record(d)
    assert ob.send_problems(roots, meta, body) == []


def test_can_send_cli_exits_1_with_the_reasons(roots, capsys):
    d, _ = _approved(roots)
    _hold_file(roots, [d.stem])
    code = ob.main(["can-send", "--id", d.stem, "--root", str(roots.js), "--os-root", str(roots.os)])
    out = capsys.readouterr().out
    assert code == 1 and '"can_send": false' in out and "on hold" in out


def test_holds_reads_every_hold_file_row(roots):
    _hold_file(roots, ["O-2026-0196", "O-2026-0197"])
    assert ob.holds(roots.js) == {"O-2026-0196": "outbox-first-touch-hold-2030-01-05.md",
                                  "O-2026-0197": "outbox-first-touch-hold-2030-01-05.md"}


def test_gmail_send_execute_refuses_a_held_item(tmp_path):
    import gmail_send as gs
    from test_gmail_send import FakeGmail, _approve, _reply
    js = tmp_path / "os" / gs.JS_PREFIX
    js.mkdir(parents=True)
    r = cu.Roots(js=js, os=tmp_path / "os")
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF-1.7\n" + b"x" * 100 + b"\n%%EOF\n")
    aid, path = gs.request(r, _reply(pdf, prospect="sam-rivera"), today=date(2026, 9, 29))
    _approve(path)
    _hold_file(r, [aid])                                      # a send is held by its approval id
    svc = FakeGmail()
    with pytest.raises(gs.MailError, match="on hold"):
        gs.execute(svc, path, today=date(2026, 9, 29))
    assert svc.calls == []                                     # nothing sent
