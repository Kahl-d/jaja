"""prospects.py: the referral fields: req_ids, referral_window_ends,
hiring_post_url, last_reply_at; `due` honours a referral window's end."""
from __future__ import annotations

import json
from datetime import date

import pytest

import comms_helpers as ch  # noqa: F401  (puts scripts/ on sys.path)
from comms_helpers import offline  # noqa: F401  (autouse: no network)
import js_common as jc
import js_comms_util as cu
import prospects as pr

TODAY = date(2026, 10, 1)


@pytest.fixture
def roots(tmp_path):
    js = tmp_path / "os" / "domains" / "career" / "projects" / "job-search"
    js.mkdir(parents=True)
    return cu.Roots(js=js, os=tmp_path / "os")


def _seed(roots, rows):
    for row in rows:
        meta = pr.new_meta(row["id"], row["id"].replace("-", " ").title(), source="gmail-backlog", today=TODAY)
        meta.update(row)
        jc.write_record(roots.prospects / f"{row['id']}.md", meta, "# x\n\n## Timeline\n")


def _load(roots, slug):
    return jc.read_record(roots.prospects / f"{slug}.md")[0]


def test_the_four_fields_are_optional_and_new_records_do_not_carry_them():
    meta = pr.new_meta("x", "X", "outbound", TODAY)
    assert not {"req_ids", "referral_window_ends", "hiring_post_url", "last_reply_at"} & set(meta)


@pytest.mark.parametrize("pair,key,value", [
    ("req_ids=R-123, 68128", "req_ids", ["R-123", "68128"]),
    ("referral_window_ends=2026-10-31", "referral_window_ends", date(2026, 10, 31)),
    ("hiring_post_url=https://x.com/founder/status/123", "hiring_post_url", "https://x.com/founder/status/123"),
    ("last_reply_at=2026-09-30T17:00:00Z", "last_reply_at", "2026-09-30T17:00:00Z"),
])
def test_set_accepts_and_types_each_field(pair, key, value):
    fields, _, _, _ = pr.parse_assignments([pair])
    assert fields == {key: value}


@pytest.mark.parametrize("pair", ["referral_window_ends=soon", "hiring_post_url=not a url",
                                  "last_reply_at=yesterday"])
def test_set_rejects_bad_values(pair):
    with pytest.raises(ValueError):
        pr.parse_assignments([pair])


def test_req_ids_is_a_list_field_that_takes_additions(roots):
    _seed(roots, [{"id": "parisa", "req_ids": ["R-1"]}])
    _, adds, _, _ = pr.parse_assignments(["req_ids+=R-2"])
    pr.set_fields(roots, "parisa", list_add=adds, today=TODAY)
    assert _load(roots, "parisa")["req_ids"] == ["R-1", "R-2"]


def test_an_inbound_after_an_outbound_stamps_last_reply_at(roots):
    _seed(roots, [{"id": "maya-chen", "name": "Maya Chen", "emails": ["maya@helio.test"], "status": "replied",
                   "last_outbound_at": "2026-09-20T17:00:00Z", "outbound_count": 1}])
    pr.ingest_events(roots, [{"key": "gm-reply", "at": "2026-09-22T17:00:00Z", "direction": "in", "channel": "email",
                              "source": "gmail-backlog", "label": "recruiter_outreach", "confidence": 0.9,
                              "person": {"name": "Maya Chen", "emails": ["maya@helio.test"], "linkedin_url": "",
                                         "company": "Helio", "title": "Recruiter"},
                              "subject": "Re: role", "summary": "Thanks Alex", "text": "Thanks Alex",
                              "thread_id": "t1", "message_id": "<r@x>", "references": "", "reply_to": "", "tags": []}],
                     today=TODAY)
    assert str(_load(roots, "maya-chen")["last_reply_at"]).startswith("2026-09-22")


def test_due_lists_referral_windows_closing_soon(roots):
    _seed(roots, [
        {"id": "closing", "status": "in_conversation", "referral_window_ends": "2026-10-04", "req_ids": ["G-1"]},
        {"id": "far", "status": "in_conversation", "referral_window_ends": "2026-10-30"},
        {"id": "lapsed", "status": "in_conversation", "referral_window_ends": "2026-09-29"},
    ])
    result = pr.due(roots, today=TODAY)
    rows = {r["id"]: r for r in result["windows"]}
    assert set(rows) == {"closing", "lapsed"}
    assert rows["closing"]["days_left"] == 3 and rows["closing"]["req_ids"] == ["G-1"]
    assert rows["lapsed"]["days_left"] == -2
    assert result["due"] == [] and result["exhausted"] == []


def test_a_follow_up_is_never_due_after_the_window_closes(roots):
    _seed(roots, [{"id": "ref", "status": "replied", "last_outbound_at": "2026-09-29T17:00:00Z",
                   "last_inbound_at": "2026-09-20T00:00:00Z", "outbound_count": 1, "referral_window_ends": "2026-10-02"}])
    due = pr.due(roots, today=date(2026, 10, 2))["due"]           # five business days would be 2026-10-06
    assert [d["id"] for d in due] == ["ref"] and due[0]["due_date"] == "2026-10-02"
    assert due[0]["referral_window_ends"] == "2026-10-02"
    assert pr.due(roots, today=TODAY)["due"] == []


def test_the_due_cli_prints_the_windows(roots, capsys):
    _seed(roots, [{"id": "closing", "status": "in_conversation", "referral_window_ends": "2026-10-04"}])
    assert pr.main(["due", "--root", str(roots.js), "--today", TODAY.isoformat()]) == 0
    assert "closing" in capsys.readouterr().out
    assert pr.main(["due", "--root", str(roots.js), "--today", TODAY.isoformat(), "--format", "json"]) == 0
    assert [r["id"] for r in json.loads(capsys.readouterr().out)["windows"]] == ["closing"]
