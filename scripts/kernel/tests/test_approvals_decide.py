"""approvals.py decide: the owner's card or batch yes recorded in an approval file with their exact words, the session and
the time (charter sections 6 and 7). One file per batch; the Exact content is never touched, so verify still passes."""
import datetime as dt
from pathlib import Path

import pytest

import approvals
import frontmatter as fm

CLOCK = dt.datetime(2026, 10, 6, 8, 30, 0, tzinfo=dt.timezone(dt.timedelta(hours=-7)))
TODAY = dt.date(2026, 10, 6)

PENDING = """---
id: A-2026-0500
action: send-email
requested_by: role:messenger
target: 'to: two people'
status: pending
content_hash: "{hash}"
decided_at:
decided_via:
created: 2026-10-05
expires: 2026-10-12
---
# send-email → two people

## Decisions
- [x] O-2026-0001 · Reply to Ann · email
- [x] O-2026-0002 · Reply to Bo · email

## Exact content
### 1. Reply to Ann
Hi Ann, thanks.

### 2. Reply to Bo
Hi Bo, thanks.

## Receipts
"""


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(approvals, "_now", lambda: CLOCK)


def pending(root: Path) -> Path:
    exact = "### 1. Reply to Ann\nHi Ann, thanks.\n\n### 2. Reply to Bo\nHi Bo, thanks."
    text = PENDING.format(hash=approvals.content_hash_of_text(exact))
    path = root / "work" / "approvals" / "A-2026-0500.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_yes_on_a_pending_file_records_his_words_and_ticks_exactly_those_items(tmp_path):
    path = pending(tmp_path)
    aid, got, line = approvals.decide(tmp_path, ["O-2026-0001"], "chat", "yes 1, no 2", "yes",
                                      approval="A-2026-0500", session="sess-1", today=TODAY)
    assert (aid, got) == ("A-2026-0500", path)
    meta, body = fm.load(path)
    assert meta["status"] == "approved" and meta["decided_via"] == "chat"
    assert meta["decided_quote"] == "yes 1, no 2" and meta["decided_session"] == "sess-1"
    assert meta["approved_items"] == ["O-2026-0001"] and str(meta["decided_at"]).startswith("2026-10-06T08:30:00")
    assert "- [x] O-2026-0001" in body and "- [ ] O-2026-0002" in body
    assert "owner's words: \"yes 1, no 2\"" in body.split("## Receipts", 1)[1]
    assert all(ok for ok, _ in approvals.gates(meta, body, TODAY))          # the sender's gates pass: hash untouched


def test_a_no_rejects_and_unticks_everything(tmp_path):
    path = pending(tmp_path)
    approvals.decide(tmp_path, ["O-2026-0001", "O-2026-0002"], "chat", "no to both", "no", approval=str(path),
                     today=TODAY)
    meta, body = fm.load(path)
    assert meta["status"] == "rejected" and meta["approved_items"] == []
    assert "- [x]" not in body


def test_refusals_write_nothing(tmp_path):
    path = pending(tmp_path)
    before = path.read_text()
    for kwargs, match in (
            ({"ids": ["O-2026-0009"]}, "no item O-2026-0009"),
            ({"quote": " "}, "--quote is required"),
            ({"via": "email"}, "--via"),
            ({"decision": "maybe"}, "yes or no"),
            ({"ids": []}, "--ids is required")):
        args = {"ids": ["O-2026-0001"], "via": "chat", "quote": "yes", "decision": "yes", **kwargs}
        with pytest.raises(ValueError, match=match):
            approvals.decide(tmp_path, args["ids"], args["via"], args["quote"], args["decision"],
                             approval="A-2026-0500", today=TODAY)
    assert path.read_text() == before
    with pytest.raises(ValueError, match="expired"):
        approvals.decide(tmp_path, ["O-2026-0001"], "chat", "yes", "yes", approval="A-2026-0500",
                         today=dt.date(2026, 10, 20))
    approvals.decide(tmp_path, ["O-2026-0001"], "chat", "yes", "yes", approval="A-2026-0500", today=TODAY)
    with pytest.raises(ValueError, match="not pending"):                     # a decided file is never re-decided
        approvals.decide(tmp_path, ["O-2026-0001"], "chat", "yes", "yes", approval="A-2026-0500", today=TODAY)


def test_a_batch_yes_without_a_file_writes_one_decided_file_listing_the_items(tmp_path):
    pending(tmp_path)                                                         # A-2026-0500 exists: the next is 0501
    lines = ["acme-r1 · Acme · AI Engineer · https://acme.example/jobs/1",
             "bolt-r2 · Bolt · ML Engineer · https://bolt.example/jobs/2"]
    aid, path, _ = approvals.decide(tmp_path, ["acme-r1", "bolt-r2"], "chat", "yes 1-61", "yes", target="mass batch",
                                    lines=lines, session="s9", project="job-search", today=TODAY)
    assert aid == "A-2026-0501" and path.name == "A-2026-0501.md"
    meta, body = fm.load(path)
    assert meta["action"] == "submit-form" and meta["status"] == "approved" and meta["project"] == "job-search"
    assert meta["created"] == "2026-10-06" and meta["expires"] == "2026-10-13"
    assert meta["approved_items"] == ["acme-r1", "bolt-r2"] and meta["decided_quote"] == "yes 1-61"
    assert approvals.exact_content(body) == "\n".join(lines)
    assert all(ok for ok, _ in approvals.gates(meta, body, TODAY, "mass batch"))
    with pytest.raises(ValueError, match="--target"):
        approvals.decide(tmp_path, ["x"], "chat", "yes", "yes", today=TODAY)


def test_the_cli(tmp_path, capsys):
    pending(tmp_path)
    code = approvals.main(["decide", "A-2026-0500", "--ids", "O-2026-0001,O-2026-0002", "--via", "chat",
                           "--quote", "yes both", "--decision", "yes", "--root", str(tmp_path), "--today", "2026-10-06"])
    assert code == 0 and "A-2026-0500" in capsys.readouterr().out
    assert approvals.main(["decide", "A-2026-0500", "--ids", "O-2026-0001", "--via", "chat", "--quote", "yes",
                           "--decision", "yes", "--root", str(tmp_path)]) == 2
