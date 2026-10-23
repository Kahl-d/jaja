"""Sent and received touches (touches/*.jsonl) join the updates feed and the People and
Companies timelines. Drafts never show;
a touch the prospect file already logs at the same minute is not shown twice."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from layout import JS  # noqa: E402
for p in (str(JS / "scripts"), str(JS / "dashboard")):
    if p not in sys.path:
        sys.path.insert(0, p)

import crm  # noqa: E402

NOW = datetime(2026, 10, 2, 6, 0, tzinfo=timezone.utc)
PEOPLE = {"jess-hollis": {"id": "jess-hollis", "name": "Jess Hollis", "company": "Bluefin Health", "company_slug": "bluefin-health"},
          "pat": {"id": "pat", "name": "Pat Quinn Roe", "company": "Orion", "company_slug": "orion"}}


def tree(tmp_path):
    js = tmp_path / "js"
    (js / "touches").mkdir(parents=True)
    rows = [{"at": "2026-10-02T04:58:57Z", "direction": "out", "status": "sent", "channel": "email", "prospect": "jess-hollis",
             "subject": "Re: Voice AI engineer"},
            {"at": "2026-10-01T16:00:00Z", "direction": "in", "status": "received", "channel": "email", "prospect": "pat",
             "subject": "Re: Orion teams"},
            {"at": "2026-10-01T17:00:00Z", "direction": "out", "status": "draft", "channel": "email", "prospect": "pat"},
            {"at": "2026-09-20T17:00:00Z", "direction": "out", "status": "sent", "channel": "email", "prospect": "pat", "subject": "old"}]
    (js / "touches" / "2026-10.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    (js / "prospects").mkdir()
    (js / "prospects" / "pat.md").write_text("---\nid: pat\nname: Pat\n---\n# Pat\n\n## Timeline\n"
                                               "- 2026-10-01T16:00:00Z · in · email · logged by mail-triage\n")
    (js / "prospects" / "jess-hollis.md").write_text("---\nid: jess-hollis\nname: Jess Hollis\n---\n# Jess\n\n## Timeline\n")
    return js


def test_sent_and_received_touches_of_the_week_feed_the_updates(tmp_path):
    t = crm.touches(tree(tmp_path), NOW, PEOPLE)
    week = [x for x in t if x["day"] >= "2026-09-25"]
    assert [(x["label"], x["name"], x["at_pt"]) for x in week] == [("sent", "Jess Hollis", "10-01 21:58"), ("received", "Pat Quinn Roe", "10-01 09:00")]
    assert week[0]["company"] == "Bluefin Health" and week[0]["title"] == "email: Re: Voice AI engineer"
    assert not [x for x in t if x.get("status") == "draft"]


def test_a_person_timeline_takes_the_touches_without_doubling_a_logged_one(tmp_path):
    js = tree(tmp_path)
    tl = crm.timelines(js, crm.touches(js, NOW, PEOPLE))
    assert [x["when"] for x in tl["jess-hollis"]] == ["10-01 21:58"] and tl["jess-hollis"][0]["text"].startswith("sent · email")
    assert [x["when"] for x in tl["pat"]] == ["10-01 09:00", "09-20 10:00"]          # 16:00Z logged once, not twice


def test_a_company_account_lists_its_people_touches(tmp_path):
    js = tree(tmp_path)
    acc = {a["slug"]: a for a in crm.companies([], [], [], list(PEOPLE.values()), crm.touches(js, NOW, PEOPLE))}
    assert [x["label"] for x in acc["orion"]["activity"]] == ["received", "sent"]
    assert acc["bluefin-health"]["activity"][0]["name"] == "Jess Hollis"
