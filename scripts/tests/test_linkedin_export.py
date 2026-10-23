"""linkedin_export.py — the official "Download your data" CSVs (no scraping, no network)."""
from __future__ import annotations

import json
import zipfile

import pytest

import comms_helpers as ch
from comms_helpers import offline  # noqa: F401  (autouse: no network)
import js_comms_util as cu
import linkedin_export as le

LI = ch.FIXTURES / "linkedin_export"
ME_URL = "https://www.linkedin.com/in/alex-example-test"


@pytest.fixture(scope="module")
def result():
    return le.build(le.open_export(LI))


def _person(result, name):
    return next(p for p in result["people"] if p["name"] == name)


# --------------------------------------------------------------------------- raw CSV parsing
def test_connections_preamble_is_skipped_and_the_header_is_found():
    rows = le.parse_connections((LI / "Connections.csv").read_text(encoding="utf-8"))
    assert len(rows) == 7
    assert list(rows[0]) == ["First Name", "Last Name", "URL", "Email Address", "Company", "Position", "Connected On"]
    assert rows[0]["First Name"] == "Derek" and rows[1]["Email Address"] == "leila@brightloop.example.test"


@pytest.mark.parametrize("preamble", ["", "Notes:\n\n\"a note\"\n\n\n", "﻿Notes:\n\"multi\nline note\"\n\n"])
def test_connections_parse_with_any_preamble(preamble):
    body = "First Name,Last Name,URL,Email Address,Company,Position,Connected On\nA,B,https://www.linkedin.com/in/ab,,Co,Eng,01 Jan 2026\n"
    rows = le.parse_connections(preamble + body)
    assert len(rows) == 1 and rows[0]["Company"] == "Co"


def test_messages_current_12_column_export_is_parsed():
    rows = le.parse_messages((LI / "messages.csv").read_text(encoding="utf-8"))
    assert len(rows) == 13
    first = rows[0]
    assert first["conversation_id"] == "2-QUFBRGVyZWtPc2Vp" and first["from_name"] == "Derek Osei"
    assert first["from_url"] == "https://www.linkedin.com/in/derek-osei-4b1a2c"
    assert first["at"] == "2026-06-03T21:22:10Z" and first["folder"] == "INBOX" and first["is_draft"] is False
    assert rows[1]["is_draft"] is True


def test_legacy_10_column_export_still_parses():
    rows = le.parse_messages((ch.FIXTURES / "linkedin_export_legacy" / "messages.csv").read_text(encoding="utf-8"))
    assert len(rows) == 1 and rows[0]["from_name"] == "Kai Moreno" and rows[0]["is_draft"] is False


def test_invitations_with_bom_and_the_old_five_column_format():
    rows = le.parse_invitations((LI / "Invitations.csv").read_text(encoding="utf-8-sig"))
    assert rows[0]["from_name"] == "Olivia Grant" and rows[0]["direction"] == "INCOMING"
    assert rows[0]["at"] == "2026-08-12T09:14:00Z"
    assert rows[0]["inviter_url"] == "https://www.linkedin.com/in/olivia-grant-talent"
    old = le.parse_invitations('From,To,Sent At,Message,Direction\nAna,Alex Example,"4/2/21, 8:45 AM","Hi!",INCOMING\n')
    assert old[0]["message"] == "Hi!" and old[0]["inviter_url"] == "" and old[0]["at"] == "2021-04-02T08:45:00Z"


# --------------------------------------------------------------------------- normalized records
def test_me_is_identified_from_the_profile_and_my_own_messages(result):
    assert result["me"]["name"] == "Alex Example" and result["me"]["url"] == ME_URL
    assert all(p["name"] != "Alex Example" for p in result["people"])


def test_never_answered_and_awaiting_reply_flags(result):
    derek = _person(result, "Derek Osei")
    assert derek["inbound_count"] == 1 and derek["outbound_count"] == 0     # the unsent draft does not count
    assert derek["never_answered"] and derek["awaiting_reply"]
    leila = _person(result, "Leila Haddad")
    assert (leila["inbound_count"], leila["outbound_count"]) == (2, 1)
    assert not leila["never_answered"] and leila["awaiting_reply"]          # she wrote again after my reply
    omar = _person(result, "Omar Siddiqui")
    assert not omar["never_answered"] and not omar["awaiting_reply"]
    priyanka = _person(result, "Priyanka Das")
    assert priyanka["inbound_count"] == 0 and priyanka["outbound_count"] == 1 and not priyanka["never_answered"]


def test_group_intro_yields_each_sender_and_is_marked_group(result):
    conv = next(c for c in result["conversations"] if c["conversation_id"] == "2-QUFBU2FuamF5SW50cm8=")
    assert conv["group"] and conv["never_answered"]
    assert {"Sanjay Rao", "Marta Lopez"} <= {p["name"] for p in result["people"]}
    marta = next(t for t in result["touches"] if t["person_name"] == "Marta Lopez")
    assert marta["kind"] == "intro"                      # her first message, not a follow-up


def test_company_pages_and_drafts_are_skipped(result):
    assert "Acme Cloud" not in {p["name"] for p in result["people"]}
    assert result["stats"]["company_page_messages"] == 1 and result["stats"]["drafts_skipped"] == 1


def test_html_message_content_becomes_plain_text(result):
    touch = next(t for t in result["touches"] if t["person_name"] == "Victor Hale")
    assert "<p>" not in touch["text"] and '"top-5" AI lab' in touch["text"]


def test_invitations_with_a_message_become_connect_touches(result):
    olivia = next(t for t in result["touches"] if t["person_name"] == "Olivia Grant")
    assert (olivia["channel"], olivia["direction"], olivia["kind"]) == ("linkedin-connect", "in", "intro")
    assert "recruiter at Meridian Health AI" in olivia["text"]
    ben = next(t for t in result["touches"] if t["person_name"] == "Ben Carter")
    assert ben["direction"] == "out"
    assert "Jane Wu" not in {p["name"] for p in result["people"]}             # no message: not a touch
    assert result["stats"]["invitations_without_message"] == 2


def test_connections_enrich_people_with_company_position_and_email(result):
    derek = _person(result, "Derek Osei")
    assert (derek["company"], derek["position"], derek["connected_on"]) == ("Northwind Robotics", "Technical Sourcer", "2026-06-10")
    assert _person(result, "Leila Haddad")["email"] == "leila@brightloop.example.test"
    assert _person(result, "Rebecca Stone")["company"] is None               # not connected: unknown, not guessed


def test_touches_have_stable_keys_and_kinds(result):
    again = le.build(le.open_export(LI))
    assert [t["key"] for t in result["touches"]] == [t["key"] for t in again["touches"]]
    leila = [t for t in result["touches"] if t["person_name"] == "Leila Haddad"]
    assert [(t["direction"], t["kind"]) for t in leila] == [("in", "intro"), ("out", "reply"), ("in", "reply")]
    assert all(t["thread_id"] == "2-QUFBTGVpbGFIYWRkYWQ=" for t in leila)


def test_zip_and_folder_exports_give_the_same_people(tmp_path, result):
    archive = tmp_path / "Basic_LinkedIn_Data.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for p in LI.iterdir():
            zf.write(p, f"Basic_LinkedIn_Data_2026/{p.name}")
    zipped = le.build(le.open_export(archive))
    assert [p["key"] for p in zipped["people"]] == [p["key"] for p in result["people"]]


def test_cli_writes_typed_jsonl_with_connections_for_the_prospector(tmp_path, capsys):
    root = tmp_path / "job-search"
    assert le.main(["parse", str(LI), "--root", str(root), "--today", "2026-09-24"]) == 0
    out = root / "state" / "harvest" / "linkedin-2026-09-24.jsonl"
    rows = [json.loads(x) for x in out.read_text().splitlines()]
    kinds = [r["type"] for r in rows]
    assert kinds[0] == "meta" and rows[0]["me"]["url"] == ME_URL and str(out) in capsys.readouterr().out
    assert kinds.count("connection") == 7 and kinds.count("person") == len(le.build(le.open_export(LI))["people"])
    touch = next(r for r in rows if r["type"] == "touch" and r["person_name"] == "Derek Osei")
    assert {"id", "from", "subject", "body", "direction", "internal_date"} <= set(touch)
    assert touch["id"] == touch["key"] and touch["body"].startswith("Hi Alex, I'm reaching out")
    assert le.main(["parse", str(LI), "--root", str(root), "--today", "2026-09-25", "--no-connections"]) == 0
    lean = (root / "state" / "harvest" / "linkedin-2026-09-25.jsonl").read_text()
    assert '"type": "connection"' not in lean


def test_profile_urls_are_normalized_for_merging(result):
    assert all(p["linkedin_url"] in ("", cu.normalize_linkedin_url(p["linkedin_url"])) for p in result["people"])


# --------------------------------------------------------------------------- --today stamps (2026-09-25)
from comms_helpers import clock_2030  # noqa: E402,F401


def test_generated_at_comes_from_today(tmp_path, capsys, clock_2030):
    out = tmp_path / "linkedin.json"
    assert le.main(["parse", str(LI), "--root", str(tmp_path), "--today", "2026-09-24", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["generated_at"] == "2026-09-24T12:00:00+00:00"
