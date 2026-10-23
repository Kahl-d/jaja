"""Write side of the store: records and drafts inside the workspace only; nothing is sent."""
from __future__ import annotations

import pytest

from job_search_mcp import store as st


@pytest.fixture
def s(tree):
    return st.Store.from_env()


def load(path):
    import js_common as jc
    return jc.read_record(path)


# ----------------------------------------------------------------------------- prospect_set
def test_prospect_set_updates_fields_timeline_and_tags(s, tree):
    out = s.prospect_set("bob-agency", fields={"priority": "P2", "status": "triaged", "warmth": 2},
                         timeline="triage: W1 F2 = P2; agency", add_tags=["sponsor-likely"], remove_tags=["agency"])
    assert out["changed"] == ["priority", "status", "tags", "warmth"]
    meta, body = load(tree["js"] / "prospects" / "bob-agency.md")
    assert meta["priority"] == "P2" and meta["warmth"] == 2 and meta["tags"] == ["sponsor-likely"]
    assert str(meta["updated"]) == "2026-09-25"
    assert "priority" in meta["locked_fields"]          # manual sets lock the field against rescoring
    assert "- 2026-09-25 triage: W1 F2 = P2; agency" in body


@pytest.mark.parametrize("kwargs", [
    {"add_tags": ["reviewed"]},                                        # an ad hoc tag
    {"add_tags": ["referrer", "hot-lead"]},                            # one bad tag refuses the whole call
    {"fields": {"tags": ["needs-review", "warm"]}},                    # fields.tags is checked the same way
    {"add_tags": ["project/no-such-project"]},
])
def test_prospect_set_refuses_tags_outside_the_vocabulary_and_writes_nothing(s, tree, kwargs):
    path = tree["js"] / "prospects" / "bob-agency.md"
    before = path.read_text(encoding="utf-8")
    with pytest.raises(st.ToolError, match="not in schemas/tags.yaml") as exc:
        s.prospect_set("bob-agency", fields={"status": "triaged", **kwargs.get("fields", {})},
                       add_tags=kwargs.get("add_tags"))
    assert "Record tags allowed:" in str(exc.value) and "sponsor-likely" in str(exc.value)
    assert path.read_text(encoding="utf-8") == before


def test_prospect_set_create_with_a_bad_tag_leaves_no_record(s, tree):
    with pytest.raises(st.ToolError, match="not in schemas/tags.yaml"):
        s.prospect_set("new-person", fields={"name": "New Person", "source": "outbound", "status": "triaged"},
                       add_tags=["vip"], create=True)
    assert not (tree["js"] / "prospects" / "new-person.md").exists()


def test_prospect_set_accepts_vocabulary_tags(s, tree):
    s.prospect_set("bob-agency", add_tags=["referrer", "domain/career", "job-search"])
    meta, _ = load(tree["js"] / "prospects" / "bob-agency.md")
    assert {"referrer", "domain/career", "job-search"} <= set(meta["tags"])


def test_prospect_set_validates_schema(s):
    with pytest.raises(st.ToolError, match="unknown field"):
        s.prospect_set("bob-agency", fields={"favourite_color": "red"})
    with pytest.raises(st.ToolError, match="priority"):
        s.prospect_set("bob-agency", fields={"priority": "urgent"})
    with pytest.raises(st.ToolError, match="warmth"):
        s.prospect_set("bob-agency", fields={"warmth": 7})
    with pytest.raises(st.ToolError, match="managed"):
        s.prospect_set("bob-agency", fields={"updated": "2020-01-01"})


def test_prospect_set_refuses_the_graph_fields(s):
    # type: and links: are written only by code (scripts/graph_links.py), never through a tool by hand
    for key, value in (("links", ["[[x]]"]), ("type", "prospect")):
        with pytest.raises(st.ToolError, match="managed"):
            s.prospect_set("bob-agency", fields={key: value})


def test_prospect_set_writes_type_and_the_company_link(s, tree):
    (tree["js"] / "companies").mkdir(exist_ok=True)
    (tree["js"] / "companies" / "acme-ai.md").write_text("---\nslug: acme-ai\nname: Acme AI\n---\n")
    s.prospect_set("dan-hm", create=True, fields={"name": "Dan HM", "source": "outbound", "company": "Acme AI",
                                                  "company_slug": "acme-ai", "role": "hiring-manager"})
    meta, _ = load(tree["js"] / "prospects" / "dan-hm.md")
    assert meta["type"] == "prospect"
    assert meta["links"] == ["[[domains/career/projects/job-search/companies/acme-ai|acme-ai]]"]


def test_prospect_set_never_lifts_do_not_contact(s):
    with pytest.raises(st.ToolError, match="do_not_contact"):
        s.prospect_set("carol-founder", fields={"status": "triaged"})


def test_prospect_set_create_writes_every_field_prospects_py_expects(s, tree):
    """I-047 item 12: a created prospect carries the list and count fields (sources, channels, thread_ids, emails,
    inbound_count, ...) that outbox.py mark-sent and prospects.py read; before, they were missing (KeyError)."""
    from datetime import date
    import prospects
    s.prospect_set("erin-new", create=True, fields={"name": "Erin New", "source": "outbound", "company": "Acme AI"})
    meta, _ = load(tree["js"] / "prospects" / "erin-new.md")
    expected = prospects.new_meta("erin-new", "Erin New", "outbound", date(2026, 9, 28))
    assert set(expected) <= set(meta), sorted(set(expected) - set(meta))
    assert meta["sources"] == ["outbound"] and meta["channels"] == [] and meta["inbound_count"] == 0
    assert meta["company"] == "Acme AI" and meta["status"] == "new"


def test_prospect_set_create(s, tree):
    out = s.prospect_set("dan-hm", create=True, fields={"name": "Dan HM", "source": "outbound",
                                                        "company": "Acme AI", "role": "hiring-manager"})
    assert out["created"] is True
    meta, body = load(tree["js"] / "prospects" / "dan-hm.md")
    assert meta["status"] == "new" and meta["priority"] == "P3" and meta["warmth"] == 0
    assert "## Timeline" in body
    with pytest.raises(st.ToolError, match="exists"):
        s.prospect_set("dan-hm", create=True, fields={"name": "Dan HM", "source": "outbound"})
    with pytest.raises(st.ToolError, match="not found"):
        s.prospect_set("ghost-person", fields={"priority": "P1"})


def test_writes_refuse_credential_like_values(s):
    with pytest.raises(st.ToolError, match="credential"):
        s.prospect_set("bob-agency", timeline="token ya29.a0AfH6SMBxyz1234567890abcdef")
    with pytest.raises(st.ToolError, match="credential"):
        s.outbox_create(prospect="jane-doe", channel="email",
                        body="here is my key " + "sk-" + "ant-api03-" + "abcdefghijklmnopqrstuvwxyz", subject="Re: hi")


# ----------------------------------------------------------------------------- outbox + approval
def test_outbox_create_goes_through_outbox_py(s, tree):
    out = s.outbox_create(prospect="jane-doe", channel="email", subject="Re: Agents",
                          body="Hi Jane, thanks for the note about agents. Could we talk Tuesday?",
                          kind="reply")
    assert out["id"] == "O-2026-0001" and out["via"] == "scripts/outbox.py draft"
    meta, body = load(tree["js"] / "outbox" / f"{out['id']}.md")
    assert meta["status"] == "drafted" and meta["to"] == "jane@acme.example.com" and meta["content_hash"]
    assert "Could we talk Tuesday?" in body
    pmeta, _ = load(tree["js"] / "prospects" / "jane-doe.md")
    assert pmeta["status"] == "drafted"                      # outbox.py moves triaged -> drafted


def test_outbox_create_reports_lint_and_refuses_do_not_contact(s):
    out = s.outbox_create(prospect="jane-doe", channel="email", subject="Re: x",
                          body="Hi Jane \u2014 great to hear from you.")
    assert any("em-dash" in c for c in out["checks"])
    with pytest.raises(st.ToolError, match="do_not_contact"):
        s.outbox_create(prospect="carol-founder", channel="linkedin-message", body="hi")
    with pytest.raises(st.ToolError, match="channel"):
        s.outbox_create(prospect="jane-doe", channel="sms", body="hi")


def test_approval_create_from_outbox_items_uses_outbox_batch(s, tree):
    a = s.outbox_create(prospect="jane-doe", channel="email", subject="Re: A", body="Thanks Jane, Tuesday works.")
    b = s.outbox_create(prospect="bob-agency", channel="linkedin-message", body="Thanks Bob, happy to talk.")
    out = s.approval_create(outbox_ids=[a["id"], b["id"]], requested_by="messenger", why="backlog replies")
    assert out["via"] == "scripts/outbox.py batch" and out["status"] == "pending"
    assert [x["approval"] for x in out["approvals"]] == ["A-2026-0001", "A-2026-0002"]   # one per action
    import js_comms_util as cu
    appr = cu.parse_approval(tree["os"] / "work" / "approvals" / "A-2026-0001.md")
    assert appr.hash_ok and appr.meta["status"] == "pending"
    meta, _ = load(tree["js"] / "outbox" / f"{a['id']}.md")
    assert meta["approval"] in ("A-2026-0001", "A-2026-0002")
    with pytest.raises(st.ToolError, match="already in approval"):
        s.approval_create(outbox_ids=[a["id"]], requested_by="messenger", why="again")


def test_approval_create_refuses_lint_failures_and_bad_action(s):
    a = s.outbox_create(prospect="jane-doe", channel="email", subject="Re: A", body="Hi Jane \u2014 hello.")
    with pytest.raises(st.ToolError, match="em-dash"):
        s.approval_create(outbox_ids=[a["id"]], requested_by="messenger", why="x")
    with pytest.raises(st.ToolError, match="action"):
        s.approval_create(action="wire-money", items=[{"id": "x", "channel": "ats-form", "target": "t",
                                                       "body": "b"}], requested_by="job-lead", why="x", effect="y")
    with pytest.raises(st.ToolError, match="effect"):
        s.approval_create(action="submit-form", items=[{"id": "x", "channel": "ats-form", "target": "t",
                                                        "body": "b"}], requested_by="job-lead", why="x")


def test_approval_create_free_items(s, tree):
    out = s.approval_create(action="submit-form", requested_by="job-lead", why="apply batch", effect="Alex submits",
                            items=[{"id": "acme-ai-applied-ai-engineer-202609", "channel": "ats-form",
                                    "target": "https://boards.greenhouse.io/acme/jobs/123",
                                    "body": "Resume variant hash abc; cover letter attached."}])
    assert out["status"] == "pending" and out["via"] == "js_comms_util.write_approval"
    aid = out["approvals"][0]["approval"]
    import js_comms_util as cu
    appr = cu.parse_approval(tree["os"] / "work" / "approvals" / f"{aid}.md")
    assert appr.hash_ok and appr.meta["action"] == "submit-form" and appr.meta["requested_by"] == "role:job-lead"


# ----------------------------------------------------------------------------- tracker_event
def test_tracker_event_goes_through_tracker_py(s, tree):
    out = s.tracker_event("acme-ai-applied-ai-engineer-202609", "applied", source="manual",
                          evidence="Alex said he submitted on 2026-09-25", at="2026-09-25T17:00:00+00:00",
                          resume="none", reason="fixture: no PDF in this test")   # post-mortem change 3
    assert out["stage"] == "applied"
    assert out["via"] == "scripts/tracker.py"
    meta, body = load(tree["js"] / "applications" / "acme-ai-applied-ai-engineer-202609" / "application.md")
    assert meta["stage"] == "applied"
    assert "applied · manual" in body


def test_tracker_event_surfaces_the_missing_submission_time_warning(s, tree):
    out = s.tracker_event("acme-ai-applied-ai-engineer-202609", "applied", source="manual",
                          evidence="Alex said he submitted", resume="none", reason="fixture: no PDF in this test")
    assert out["stage"] == "applied"
    assert any("without --at" in w for w in out["warnings"])
    _meta, body = load(tree["js"] / "applications" / "acme-ai-applied-ai-engineer-202609" / "application.md")
    assert "time: clock (no --at; submission time not stated); Alex said he submitted" in body


def test_tracker_event_requires_evidence_and_known_application(s):
    with pytest.raises(st.ToolError, match="evidence"):
        s.tracker_event("acme-ai-applied-ai-engineer-202609", "applied")
    with pytest.raises(st.ToolError, match="not found"):
        s.tracker_event("nope-202609", "applied", evidence="x")


# ----------------------------------------------------------------------------- delegation to the scripts
def test_prospect_set_routes_status_priority_tags_through_prospects_py(s, tree):
    out = s.prospect_set("bob-agency", fields={"status": "triaged", "priority": "P1", "fit_score": 3.5},
                         add_tags=["df-adjacent"])
    assert out["via"].startswith("scripts/prospects.py set")
    meta, body = load(tree["js"] / "prospects" / "bob-agency.md")
    assert meta["status"] == "triaged" and meta["priority"] == "P1" and meta["fit_score"] == 3.5
    assert meta.get("priority_locked") is True            # prospects.py semantics: a manual priority is locked
    assert "status new → triaged" in body


def test_prospect_set_do_not_contact_goes_through_dnc(s, tree):
    s.prospect_set("bob-agency", fields={"status": "do_not_contact", "do_not_contact_reason": "asked to stop 2026-09-25"})
    meta, _ = load(tree["js"] / "prospects" / "bob-agency.md")
    assert meta["status"] == "do_not_contact" and "asked to stop" in meta["do_not_contact_reason"]


def test_writes_fall_back_to_records_when_scripts_are_absent(s, tree, tmp_path, monkeypatch):
    empty = tmp_path / "no-scripts"
    empty.mkdir()
    monkeypatch.setattr(st, "SCRIPTS", empty)
    out = s.prospect_set("bob-agency", fields={"status": "triaged"})
    assert "not present" in out["via"]
    meta, _ = load(tree["js"] / "prospects" / "bob-agency.md")
    assert meta["status"] == "triaged"
    with pytest.raises(st.ToolError, match="tracker.py is missing"):
        s.tracker_event("acme-ai-applied-ai-engineer-202609", "applied", evidence="x")
    with pytest.raises(st.ToolError, match="outbox.py is missing"):
        s.outbox_create(prospect="jane-doe", channel="email", subject="Re: x", body="hi")
    m = s.dashboard_metrics()
    assert m["prospects"]["by_status"]["triaged"] == 2 and "unavailable" in m["sources"]["applications"]


def test_tracker_event_applied_names_the_page_that_went(s, tree):
    """Rejection post-mortem 2026-10-02, change 3: an applied event through the MCP tool needs resume (a PDF path under
    resumes/, its sha256, none with a reason, or same); tracker.py refuses it otherwise and nothing is written."""
    import pytest as _pytest
    path = tree["js"] / "applications" / "acme-ai-applied-ai-engineer-202609" / "application.md"
    before = path.read_text()
    with _pytest.raises(st.ToolError, match="resume"):
        s.tracker_event("acme-ai-applied-ai-engineer-202609", "applied", evidence="Alex said he submitted",
                        at="2026-09-25T17:00:00+00:00")
    assert path.read_text() == before
    with _pytest.raises(st.ToolError, match="resume is for applied events"):
        s.tracker_event("acme-ai-applied-ai-engineer-202609", "screen", evidence="invite m-1", resume="none")
    s.tracker_event("acme-ai-applied-ai-engineer-202609", "applied", evidence="Alex said he submitted",
                    at="2026-09-25T17:00:00+00:00", resume="none", reason="a referral form with no upload")
    meta, _ = load(path)
    assert meta["resume_none_reason"] == "a referral form with no upload"
