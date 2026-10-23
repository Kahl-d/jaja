import hashlib
from pathlib import Path

import approvals

REPO = Path(__file__).resolve().parents[3]

APPROVAL = """---
id: A-2026-0901
action: send-email
requested_by: writer
ticket: T-2026-0401
target: coordinator@example.org
draft_ref: inline
diff: inline below
risk: low
status: approved
content_hash: "{hash}"
decided_at: 2026-10-04
decided_via: desktop
created: 2026-10-02
expires: 2026-10-09
---

# send-email → coordinator@example.org

## Exact content
{content}
## Why this is being sent
Ticket T-2026-0401.

## Receipts
"""

CONTENT = "\nHi Sam,\n\nI can take the 2026-10-10 shift.\n\n```\n## not a heading inside a fence\n```\n\nSam\n\n"
CANON = "Hi Sam,\n\nI can take the 2026-10-10 shift.\n\n```\n## not a heading inside a fence\n```\n\nSam"


def write(tmp_path: Path, content: str = CONTENT, digest: str | None = None, **fields: str) -> Path:
    text = APPROVAL.format(hash=digest or approvals.content_hash_of_text(CONTENT), content=content)
    for key, value in fields.items():
        text = "\n".join(f"{key}: {value}" if line.startswith(f"{key}:") else line for line in text.split("\n"))
    p = tmp_path / "A-2026-0901.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_hash_is_sha256_of_the_stripped_exact_content_section(tmp_path: Path):
    p = write(tmp_path)
    want = "sha256:" + hashlib.sha256(CANON.encode("utf-8")).hexdigest()
    assert approvals.exact_content(approvals.load(p)[1]) == CANON
    assert approvals.content_hash(p) == want


def test_whitespace_noise_does_not_change_the_hash_but_words_do():
    base = approvals.content_hash_of_text(CONTENT)
    assert approvals.content_hash_of_text(CONTENT.replace("\n", "\r\n")) == base
    assert approvals.content_hash_of_text(CONTENT.replace("shift.", "shift.   ")) == base
    assert approvals.content_hash_of_text("\n\n" + CONTENT + "\n\n") == base
    assert approvals.content_hash_of_text(CONTENT.replace("2026-10-10", "2026-10-11")) != base


def test_matches_the_template_example_in_the_repo(tmp_path: Path):
    # templates/approval.md is the contract: an approval built from it with its Exact content hashes as the rule says.
    text = (REPO / "templates" / "approval.md").read_text(encoding="utf-8")
    p = tmp_path / "A-2026-0001.md"
    p.write_text(text, encoding="utf-8")
    exact = approvals.exact_content(approvals.load(p)[1])
    assert exact is not None and approvals.content_hash(p) == approvals.content_hash_of_text(exact)


def test_verify_passes_an_approved_unexpired_matching_file(tmp_path: Path, capsys):
    p = write(tmp_path)
    assert approvals.main(["verify", str(p), "--today", "2026-10-09", "--target", "coordinator@example.org"]) == 0
    out = capsys.readouterr().out
    assert "OK status approved" in out and "OK expires 2026-10-09" in out and "RESULT: GO" in out


def test_verify_accepts_a_bare_hex_digest(tmp_path: Path):
    bare = approvals.content_hash_of_text(CONTENT).split(":", 1)[1]
    assert approvals.main(["verify", str(write(tmp_path, digest=bare)), "--today", "2026-10-05"]) == 0


def test_verify_refuses_each_failed_gate(tmp_path: Path, capsys):
    cases = [
        ({"status": "pending"}, "status"),
        ({"decided_at": ""}, "decided_at"),
        ({}, "expired"),                                            # today after expires
        ({"expires": "", "created": "2026-09-24"}, "expired"),     # no expires: created + 7 days
    ]
    for fields, word in cases:
        today = "2026-10-10" if word == "expired" and not fields else "2026-10-05"
        p = write(tmp_path, **fields)
        assert approvals.main(["verify", str(p), "--today", today]) == 1, fields
        assert word in capsys.readouterr().out
    p = write(tmp_path, content=CONTENT.replace("Sam\n", "Sam (edited)\n"))
    assert approvals.main(["verify", str(p), "--today", "2026-10-05"]) == 1
    assert "content_hash" in capsys.readouterr().out
    p = write(tmp_path)
    assert approvals.main(["verify", str(p), "--today", "2026-10-05", "--target", "someone@example.org"]) == 1
    assert "target" in capsys.readouterr().out


def test_missing_exact_content_section_is_an_error(tmp_path: Path, capsys):
    p = tmp_path / "A-2026-0902.md"
    p.write_text("---\nid: A-2026-0902\nstatus: approved\n---\n# no section\n", encoding="utf-8")
    assert approvals.main(["hash", str(p)]) == 2
    assert approvals.main(["verify", str(p), "--today", "2026-10-05"]) == 2


# --------------------------------------------------------------------------- #
# receipt and refuse: the sender's two writes, done by the script (sender card 0.1.2)
# --------------------------------------------------------------------------- #
import datetime as dt  # noqa: E402
import json  # noqa: E402

import pytest  # noqa: E402

import watchdog  # noqa: E402

CLOCK = dt.datetime(2026, 10, 5, 9, 7, 3).astimezone()
LOCAL_STAMP = CLOCK.isoformat(timespec="seconds")


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(approvals, "_now", lambda: CLOCK)
    return CLOCK


def sender_log(root: Path, day: str = "2026-10-05") -> Path:
    return root / "logs" / "runs" / "sender" / f"{day}.md"


def frontmatter_block(p: Path) -> str:
    return p.read_text(encoding="utf-8").split("\n---\n", 1)[0]


def test_sent_receipt_creates_the_receipts_heading_writes_the_log_and_leaves_the_hash(tmp_path, clock, capsys):
    p = write(tmp_path).rename(tmp_path / "A-2026-0901.md")
    text = p.read_text(encoding="utf-8").replace("\n## Receipts\n", "\n")   # no heading yet
    p.write_text(text, encoding="utf-8")
    before_meta, before_hash = frontmatter_block(p), approvals.content_hash(p)
    rc = approvals.main(["receipt", str(p), "--result", "sent", "--message-id", "stub-5614a1e82eb0",
                         "--root", str(tmp_path), "--today", "2026-10-05", "--no-channel-log"])
    assert rc == 0
    body = p.read_text(encoding="utf-8")
    assert body.count("## Receipts") == 1
    receipts = body.split("## Receipts", 1)[1]
    assert "stub-5614a1e82eb0" in receipts and "coordinator@example.org" in receipts
    assert LOCAL_STAMP in receipts and "time source: clock" in receipts
    # set nothing else: frontmatter and the hash are untouched, and verify still says GO
    assert frontmatter_block(p) == before_meta and approvals.content_hash(p) == before_hash
    capsys.readouterr()
    assert approvals.main(["verify", str(p), "--today", "2026-10-05", "--target", "coordinator@example.org"]) == 0
    log = sender_log(tmp_path).read_text(encoding="utf-8")
    assert f"## Run {LOCAL_STAMP} · ok" in log
    assert "sent A-2026-0901" in log and "stub-5614a1e82eb0" in log and "coordinator@example.org" in log
    stamp, path, how = watchdog.latest_receipt(tmp_path, "sender")
    assert (stamp, how) == (CLOCK.replace(tzinfo=None), "heading") and path == sender_log(tmp_path)


def test_receipt_goes_inside_an_existing_receipts_section_and_appends(tmp_path, clock):
    p = write(tmp_path)
    p.write_text(p.read_text(encoding="utf-8") + "- earlier line\n\n## Notes\nkeep me last\n", encoding="utf-8")
    for mid in ("stub-aaaaaaaaaaaa", "stub-bbbbbbbbbbbb"):
        assert approvals.main(["receipt", str(p), "--result", "sent", "--message-id", mid, "--root", str(tmp_path),
                               "--no-channel-log"]) == 0
    body = p.read_text(encoding="utf-8")
    receipts, notes = body.split("## Receipts", 1)[1].split("## Notes", 1)
    assert receipts.index("earlier line") < receipts.index("stub-aaaaaaaaaaaa") < receipts.index("stub-bbbbbbbbbbbb")
    assert notes.strip() == "keep me last"
    assert approvals.content_hash(p) == approvals.content_hash_of_text(CONTENT)


def test_receipt_after_a_trailing_exact_content_section_keeps_the_hash(tmp_path, clock):
    p = tmp_path / "A-2026-0903.md"
    digest = approvals.content_hash_of_text("Hello\n\nSam")
    p.write_text(f'---\nid: A-2026-0903\naction: send-email\ntarget: a@example.org\nstatus: approved\n'
                 f'content_hash: "{digest}"\n---\n# x\n\n## Exact content\nHello\n\nSam\n', encoding="utf-8")
    assert approvals.main(["receipt", str(p), "--result", "sent", "--message-id", "m-1", "--root", str(tmp_path),
                           "--no-channel-log"]) == 0
    assert approvals.content_hash(p) == digest
    last = p.read_text(encoding="utf-8").rstrip().splitlines()[-1]
    assert "message id m-1" in last and "time source: clock" in last


def test_the_time_comes_from_the_channel_log_line_and_the_day_from_today(tmp_path, clock):
    p = write(tmp_path)
    channel = tmp_path / "sent.jsonl"
    rows = [{"approval": "A-2026-0900", "message_id": "stub-000000000000", "at": "2026-09-25T15:30:00"},
            {"approval": "A-2026-0901", "message_id": "stub-5614a1e82eb0", "at": "2026-09-25T15:31:42"}]
    channel.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert approvals.main(["receipt", str(p), "--result", "sent", "--message-id", "stub-5614a1e82eb0",
                           "--channel-log", str(channel), "--today", "2026-10-05", "--root", str(tmp_path)]) == 0
    sent_at = dt.datetime(2026, 9, 25, 15, 31, 42).astimezone().isoformat(timespec="seconds")
    receipts = p.read_text(encoding="utf-8").split("## Receipts", 1)[1]
    assert sent_at in receipts and LOCAL_STAMP not in receipts and "line 2" in receipts
    log = sender_log(tmp_path).read_text(encoding="utf-8")        # the run day names the file
    assert f"## Run {sent_at} · ok" in log and LOCAL_STAMP not in log


def test_a_channel_log_without_the_message_id_writes_nothing(tmp_path, clock, capsys):
    p = write(tmp_path)
    before = p.read_text(encoding="utf-8")
    channel = tmp_path / "sent.jsonl"
    channel.write_text(json.dumps({"message_id": "stub-other", "at": "2026-09-25T15:31:42"}) + "\n", encoding="utf-8")
    assert approvals.main(["receipt", str(p), "--result", "sent", "--message-id", "stub-5614a1e82eb0",
                           "--channel-log", str(channel), "--root", str(tmp_path)]) == 2
    assert "stub-5614a1e82eb0" in capsys.readouterr().out
    assert p.read_text(encoding="utf-8") == before and not sender_log(tmp_path).exists()


def test_the_caller_cannot_supply_a_time(tmp_path, clock):
    p = write(tmp_path)
    for flag in ("--now", "--at", "--time"):
        with pytest.raises(SystemExit):
            approvals.main(["receipt", str(p), "--result", "sent", "--message-id", "m", flag, "2026-10-05T14:00:00"])
    before = p.read_text(encoding="utf-8")
    assert approvals.main(["receipt", str(p), "--result", "sent", "--root", str(tmp_path)]) == 2   # needs its message id
    assert p.read_text(encoding="utf-8") == before and not sender_log(tmp_path).exists()


def test_refuse_logs_the_verify_fail_lines_and_notes_the_approval(tmp_path, clock, capsys):
    p = write(tmp_path, status="pending", decided_at="")
    before_meta, before_hash = frontmatter_block(p), approvals.content_hash(p)
    assert approvals.main(["refuse", str(p), "--today", "2026-10-05", "--root", str(tmp_path)]) == 0
    log = sender_log(tmp_path).read_text(encoding="utf-8")
    assert f"## Run {LOCAL_STAMP} · warn" in log and "sentinel: NEEDS_YOU" in log
    refusals = [ln for ln in log.splitlines() if ln.startswith("- refused")]
    assert len(refusals) == 1
    assert "A-2026-0901" in refusals[0] and "FAIL status pending is not approved" in refusals[0]
    assert "FAIL decided_at is empty" in refusals[0] and "nothing was sent" in refusals[0]
    receipts = p.read_text(encoding="utf-8").split("## Receipts", 1)[1]
    assert "refused" in receipts and "FAIL status pending is not approved" in receipts
    assert frontmatter_block(p) == before_meta and approvals.content_hash(p) == before_hash


def test_five_refusals_in_a_day_append_one_line_each(tmp_path, clock):
    cases = [
        (write(tmp_path, status="pending").rename(tmp_path / "A-2026-0811.md"), [], "FAIL status pending is not approved"),
        (write(tmp_path, expires="2026-10-01").rename(tmp_path / "A-2026-0812.md"), [], "expired (today 2026-10-05 is after it)"),
        (write(tmp_path, content=CONTENT + "edited\n").rename(tmp_path / "A-2026-0813.md"), [], "does not match the recorded"),
        (write(tmp_path).rename(tmp_path / "A-2026-0815.md"), ["--target", "team@example.org"],
         "differs from the requested team@example.org"),
    ]
    for path, extra, _ in cases:
        assert approvals.main(["refuse", str(path), "--today", "2026-10-05", "--root", str(tmp_path), *extra]) == 0
    assert approvals.main(["refuse", "A-2026-0814", "--today", "2026-10-05", "--root", str(tmp_path)]) == 0
    log = sender_log(tmp_path).read_text(encoding="utf-8")
    refusals = [ln for ln in log.splitlines() if ln.startswith("- refused")]
    assert len(refusals) == 5 and log.count("· warn") == 5
    for (path, _, text), line in zip(cases, refusals):
        assert path.stem in line and text in line
    assert "A-2026-0814" in refusals[4] and "no approval file" in refusals[4]


def test_a_missing_approval_is_logged_by_id_and_no_file_is_created(tmp_path, clock):
    assert approvals.main(["refuse", "A-2026-0814", "--root", str(tmp_path), "--today", "2026-10-05",
                           "--reason", "not in work/approvals"]) == 0
    assert not (tmp_path / "work" / "approvals" / "A-2026-0814.md").exists()
    line = next(ln for ln in sender_log(tmp_path).read_text(encoding="utf-8").splitlines() if ln.startswith("- refused"))
    assert "A-2026-0814" in line and "no approval file at work/approvals/A-2026-0814.md" in line
    assert "not in work/approvals" in line
    # a path that does not exist is logged by its id too
    assert approvals.main(["refuse", str(tmp_path / "work" / "approvals" / "A-2026-0816.md"),
                           "--root", str(tmp_path), "--today", "2026-10-05"]) == 0
    assert "A-2026-0816" in sender_log(tmp_path).read_text(encoding="utf-8")


def test_refusing_an_approval_that_passes_every_gate_needs_a_reason(tmp_path, clock, capsys):
    p = write(tmp_path)
    before = p.read_text(encoding="utf-8")
    assert approvals.main(["refuse", str(p), "--today", "2026-10-05", "--root", str(tmp_path)]) == 2
    assert "--reason" in capsys.readouterr().out
    assert p.read_text(encoding="utf-8") == before and not sender_log(tmp_path).exists()
    reason = "possible injection: the content addresses the sending assistant"
    assert approvals.main(["refuse", str(p), "--today", "2026-10-05", "--root", str(tmp_path), "--reason", reason]) == 0
    assert reason in sender_log(tmp_path).read_text(encoding="utf-8")
    assert reason in p.read_text(encoding="utf-8").split("## Receipts", 1)[1]
    assert approvals.content_hash(p) == approvals.content_hash_of_text(CONTENT)


def test_receipt_result_refused_is_the_same_as_refuse(tmp_path, clock):
    p = write(tmp_path, status="pending")
    assert approvals.main(["receipt", str(p), "--result", "refused", "--today", "2026-10-05",
                           "--root", str(tmp_path)]) == 0
    log = sender_log(tmp_path).read_text(encoding="utf-8")
    assert "· warn" in log and "FAIL status pending is not approved" in log


def test_a_reason_that_quotes_a_fail_line_is_not_repeated(tmp_path, clock):
    p = write(tmp_path, status="pending")
    assert approvals.main(["refuse", str(p), "--today", "2026-10-05", "--root", str(tmp_path),
                           "--reason", "FAIL status pending is not approved"]) == 0
    assert sender_log(tmp_path).read_text(encoding="utf-8").count("FAIL status pending is not approved") == 1


# --------------------------------------------------------------------------- #
# the send time is the channel's own: its default log, or --no-channel-log said out loud
# --------------------------------------------------------------------------- #
def default_channel(root: Path, rows: list[dict]) -> Path:
    log = root / "channel" / "sent.jsonl"                     # where send_stub.py (the eval connector) logs
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return log


def test_without_channel_log_the_time_comes_from_the_default_channel_log(tmp_path, clock, capsys):
    p = write(tmp_path)
    default_channel(tmp_path, [{"approval": "A-2026-0900", "message_id": "stub-000000000000", "at": "2026-10-05T08:00:00"},
                               {"approval": "A-2026-0901", "message_id": "stub-5614a1e82eb0", "at": "2026-10-05T08:59:10"}])
    assert approvals.main(["receipt", str(p), "--result", "sent", "--message-id", "stub-5614a1e82eb0",
                           "--today", "2026-10-05", "--root", str(tmp_path)]) == 0
    sent_at = dt.datetime(2026, 10, 5, 8, 59, 10).astimezone().isoformat(timespec="seconds")
    receipts = p.read_text(encoding="utf-8").split("## Receipts", 1)[1]
    assert sent_at in receipts and LOCAL_STAMP not in receipts
    assert "time source: channel log channel/sent.jsonl line 2" in receipts
    log = sender_log(tmp_path).read_text(encoding="utf-8")
    assert f"## Run {sent_at} · ok" in log and LOCAL_STAMP not in log


def test_no_default_channel_log_refuses_and_writes_nothing(tmp_path, clock, capsys):
    p = write(tmp_path)
    before = p.read_text(encoding="utf-8")
    assert approvals.main(["receipt", str(p), "--result", "sent", "--message-id", "stub-5614a1e82eb0",
                           "--today", "2026-10-05", "--root", str(tmp_path)]) == 2
    out = capsys.readouterr().out
    assert "channel/sent.jsonl" in out and "--no-channel-log" in out
    assert p.read_text(encoding="utf-8") == before and not sender_log(tmp_path).exists()


def test_a_default_channel_log_without_the_message_id_refuses_and_writes_nothing(tmp_path, clock, capsys):
    p = write(tmp_path)
    before = p.read_text(encoding="utf-8")
    default_channel(tmp_path, [{"message_id": "stub-other", "at": "2026-10-05T08:59:10"}])
    assert approvals.main(["receipt", str(p), "--result", "sent", "--message-id", "stub-5614a1e82eb0",
                           "--today", "2026-10-05", "--root", str(tmp_path)]) == 2
    assert "stub-5614a1e82eb0" in capsys.readouterr().out
    assert p.read_text(encoding="utf-8") == before and not sender_log(tmp_path).exists()


def test_no_channel_log_records_the_clock_as_the_time_source(tmp_path, clock):
    p = write(tmp_path)
    default_channel(tmp_path, [{"message_id": "stub-5614a1e82eb0", "at": "2026-10-05T08:59:10"}])
    assert approvals.main(["receipt", str(p), "--result", "sent", "--message-id", "stub-5614a1e82eb0",
                           "--today", "2026-10-05", "--root", str(tmp_path), "--no-channel-log"]) == 0
    last = p.read_text(encoding="utf-8").rstrip().splitlines()[-1]
    assert LOCAL_STAMP in last and "time source: clock" in last and "channel log" not in last
    assert "time source: clock" in sender_log(tmp_path).read_text(encoding="utf-8")


def test_channel_log_and_no_channel_log_together_is_a_usage_error(tmp_path, clock):
    p = write(tmp_path)
    log = default_channel(tmp_path, [{"message_id": "m", "at": "2026-10-05T08:59:10"}])
    with pytest.raises(SystemExit):
        approvals.main(["receipt", str(p), "--result", "sent", "--message-id", "m", "--root", str(tmp_path),
                        "--channel-log", str(log), "--no-channel-log"])
    assert not sender_log(tmp_path).exists()
