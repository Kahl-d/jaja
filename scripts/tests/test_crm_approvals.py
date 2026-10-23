"""The approvals drawer, read only: pending approvals grouped by kind (the `action:`)
and then by age (today, 1 to 2 days, 3 to 6 days), each with its target, expiry and a link to its file, so the owner can
clear them in a batch; expired ones apart as "expired, not decided". Approvals are signed in their own files."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from layout import JS  # noqa: E402
for p in (str(JS / "scripts"), str(JS / "dashboard")):
    if p not in sys.path:
        sys.path.insert(0, p)

import crm  # noqa: E402


def appr(os_root: Path, aid: str, action: str, created: str, expires: str, status: str = "pending", target: str = "x"):
    p = os_root / "work" / "approvals" / f"{aid}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\nid: {aid}\naction: {action}\nstatus: {status}\ncreated: {created}\nexpires: {expires}\ntarget: \"{target}\"\n---\n")


def test_pending_approvals_group_by_kind_then_age(tmp_path):
    os_root = tmp_path / "os"
    appr(os_root, "A-1", "post-linkedin", "2026-10-01", "2026-10-08", target="comment on Jo's post")
    appr(os_root, "A-2", "post-linkedin", "2026-09-30", "2026-10-07")
    appr(os_root, "A-3", "send-email", "2026-09-27", "2026-10-04", target="reply to Jess Hollis")
    appr(os_root, "A-4", "send-email", "2026-09-20", "2026-09-27")                 # expired
    appr(os_root, "A-5", "send-email", "2026-10-01", "2026-10-08", status="approved")
    a = crm.approvals(os_root, date(2026, 10, 1))
    assert a["pending"] == 3 and [x["id"] for x in a["expired"]] == ["A-4"]
    g = {grp["kind"]: grp for grp in a["groups"]}
    assert list(g) == ["LinkedIn post", "send-email"]                           # the biggest kind first, by what it holds
    assert [(b["age"], [x["id"] for x in b["items"]]) for b in g["LinkedIn post"]["ages"]] == [("today", ["A-1"]), ("1 to 2 days", ["A-2"])]
    assert g["send-email"]["ages"][0]["age"] == "3 to 6 days" and g["send-email"]["ages"][0]["items"][0]["target"] == "reply to Jess Hollis"
    assert g["LinkedIn post"]["ages"][0]["items"][0]["path"].endswith("work/approvals/A-1.md")


def test_linkedin_approvals_are_labelled_by_what_they_hold(tmp_path):
    os_root = tmp_path / "os"
    appr(os_root, "A-1", "post-linkedin", "2026-10-01", "2026-10-08", target="2 item(s) via linkedin-connect, linkedin-message: Jo")
    appr(os_root, "A-2", "post-linkedin", "2026-10-01", "2026-10-08", target="1 item(s) via linkedin-post: a proof post")
    labels = {g["label"]: [x["id"] for b in g["ages"] for x in b["items"]] for g in crm.approvals(os_root, date(2026, 10, 1))["groups"]}
    assert labels == {"LinkedIn: connection notes and messages (action post-linkedin)": ["A-1"], "LinkedIn post": ["A-2"]}
