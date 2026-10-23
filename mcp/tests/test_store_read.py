"""Read side of the store: filters, pagination, the 8 KB cap and untrusted wrapping."""
from __future__ import annotations

import json

import pytest

from job_search_mcp import store as st

MAX = st.MAX_BYTES


def size(obj) -> int:
    return len(json.dumps(obj, separators=(",", ":"), default=str).encode("utf-8"))


@pytest.fixture
def s(tree):
    return st.Store.from_env()


# ----------------------------------------------------------------------------- roots
def test_from_env_uses_overrides(tree, s):
    assert s.js == tree["js"]
    assert s.os_root == tree["os"]
    assert str(s.today()) == "2026-09-25"


# ----------------------------------------------------------------------------- jobs
def test_jobs_search_filters_and_compact_rows(s):
    out = s.jobs_search(status="queued")
    assert out["total"] == 1
    row = out["items"][0]
    assert row["id"] == "acme-ai-123"
    assert row["fit_score"] == 4.4
    assert "body" not in row and "jd" not in row


def test_jobs_search_query_and_sponsorship(s):
    assert [r["id"] for r in s.jobs_search(query="infrastructure")["items"]] == ["beta-labs-9"]
    assert [r["id"] for r in s.jobs_search(sponsorship="yes")["items"]] == ["acme-ai-123"]
    assert [r["id"] for r in s.jobs_search(min_fit=4.0)["items"]] == ["acme-ai-123"]
    assert [r["id"] for r in s.jobs_search(posted_since="2026-09-20")["items"]] == ["acme-ai-123"]


def test_jobs_search_paginates(s):
    first = s.jobs_search(limit=1)
    assert len(first["items"]) == 1 and first["total"] == 2 and first["next_cursor"] == "1"
    second = s.jobs_search(limit=1, cursor=first["next_cursor"])
    assert second["next_cursor"] is None
    assert first["items"][0]["id"] != second["items"][0]["id"]


def test_job_get_wraps_jd_and_pages_long_bodies(s):
    job = s.job_get("acme-ai-123")
    assert job["meta"]["company"] == "Acme AI"
    assert job["jd"].startswith("<untrusted_jd>")
    assert "IGNORE PREVIOUS INSTRUCTIONS" in job["jd"]  # kept, but fenced as data

    long = s.job_get("beta-labs-9")
    assert size(long) <= MAX
    assert long["next_offset"] is not None
    more = s.job_get("beta-labs-9", offset=long["next_offset"])
    assert more["offset"] == long["next_offset"]


def test_job_get_unknown_and_traversal(s):
    with pytest.raises(st.ToolError):
        s.job_get("nope")
    with pytest.raises(st.ToolError):
        s.job_get("../../etc/passwd")


# ----------------------------------------------------------------------------- prospects
def ids(out):
    return [r["id"] for r in out["items"]]


def test_prospects_list_crm_filters(s):
    assert ids(s.prospects_list(status="triaged")) == ["jane-doe"]
    assert set(ids(s.prospects_list(status=["new", "do_not_contact"]))) == {"bob-agency", "carol-founder"}
    assert ids(s.prospects_list(priority="P0,P1")) == ["jane-doe"]
    assert ids(s.prospects_list(company="acme")) == ["jane-doe"]
    assert set(ids(s.prospects_list(source="linkedin-export"))) == {"bob-agency", "carol-founder"}
    assert set(ids(s.prospects_list(tags=["warm"]))) == {"jane-doe", "carol-founder"}
    assert ids(s.prospects_list(tags=["warm", "ai-lab"])) == ["jane-doe"]
    assert ids(s.prospects_list(sponsorship="yes")) == ["jane-doe"]
    assert set(ids(s.prospects_list(role="recruiter"))) == {"jane-doe", "bob-agency"}
    assert ids(s.prospects_list(min_warmth=3)) == ["jane-doe"]
    assert ids(s.prospects_list(due_by="2026-09-25")) == ["jane-doe"]


def test_prospects_list_last_touch_range(s):
    # last touch = the later of last_inbound_at / last_outbound_at
    assert ids(s.prospects_list(last_touch_from="2026-09-01")) == ["jane-doe"]
    assert set(ids(s.prospects_list(last_touch_to="2026-08-31"))) == {"bob-agency", "carol-founder"}
    assert ids(s.prospects_list(last_touch_from="2026-08-01", last_touch_to="2026-08-31")) == ["carol-founder"]


def test_prospects_list_sorts_by_priority_by_default(s):
    assert ids(s.prospects_list()) == ["jane-doe", "carol-founder", "bob-agency"]
    assert ids(s.prospects_list(sort="last_touch"))[0] == "jane-doe"


def test_prospects_list_rejects_bad_enum(s):
    with pytest.raises(st.ToolError):
        s.prospects_list(priority="P9")


def test_prospect_get_wraps_timeline_and_touches(s):
    p = s.prospect_get("jane-doe")
    assert p["meta"]["name"] == "Jane Doe"
    assert p["timeline"].startswith("<untrusted_timeline>")
    assert p["touches"][0]["summary"].startswith("<untrusted_message>")
    assert p["touches"][0]["at"] >= p["touches"][-1]["at"]


# ----------------------------------------------------------------------------- applications
def test_applications_list_and_get(s):
    out = s.applications_list(stage="fit_checked")
    assert ids(out) == ["acme-ai-applied-ai-engineer-202609"]
    app = s.application_get("acme-ai-applied-ai-engineer-202609")
    assert app["meta"]["stage"] == "fit_checked"
    assert app["events"][0]["type"] == "fit_checked"
    assert app["events"][0]["evidence"].startswith("<untrusted_evidence>")
    assert "fit.md" in app["files"]
    assert app["fit"] == {"score": 4.4, "verdict": "apply"}


def test_application_hub_joins_live_and_says_whether_hub_md_is_fresh(s, tree):
    import role_hub
    slug = "acme-ai-applied-ai-engineer-202609"
    out = s.application_hub(slug)
    assert out["hub"].startswith("<untrusted_hub>") and out["via"] == "scripts/role_hub.py"
    assert out["hub_on_disk"] == "absent" and out["path"] == f"applications/{slug}/HUB.md"
    assert "## Opening" in out["hub"] and "[Jane Doe](../../prospects/jane-doe.md)" in out["hub"]
    assert "forward all mail" not in out["hub"]                  # message text never enters the hub
    assert size(out) <= MAX
    role_hub.refresh(tree["js"])
    assert s.application_hub(slug)["hub_on_disk"] == "fresh"
    with pytest.raises(st.ToolError, match="not found"):
        s.application_hub("nope-202609")


# ----------------------------------------------------------------------------- touches
def test_touches_recent_defaults_to_14_days_and_wraps(s):
    out = s.touches_recent()
    assert [t["prospect"] for t in out["items"]] == ["jane-doe"]
    assert out["items"][0]["summary"].startswith("<untrusted_message>")
    everything = s.touches_recent(since="2026-08-01")
    assert everything["total"] == 3
    assert [t["at"] for t in everything["items"]] == sorted((t["at"] for t in everything["items"]), reverse=True)
    assert s.touches_recent(since="2026-08-01", direction="out", channel="linkedin-message")["total"] == 1


# ----------------------------------------------------------------------------- profile
def test_profile_facts_sections_then_path(s):
    top = s.profile_facts()
    assert {"identity", "experience"} <= {sec["key"] for sec in top["sections"]}
    ident = s.profile_facts(path="identity")
    assert ident["value"]["full_name"] == "Alex Example"
    assert "work_authorization" not in ident["value"]            # internal-only hidden by default
    assert "work_authorization" in s.profile_facts(path="identity", include_internal=True)["value"]
    assert s.profile_facts(path="identity.links.github")["value"]["value"] == "github.com/example-handle"
    assert s.profile_facts(path="experience.1")["value"]["company"] == "Example University"
    with pytest.raises(st.ToolError):
        s.profile_facts(path="identity.nope")


def test_profile_facts_serves_the_render_title_never_a_held_record(s, tree):
    """I-006: `title` in facts.yaml is the record (held variants, conflict notes); profile_facts returns
    `title_render` as `title`, keeps the record as `title_record`, and refuses an unmarked conflict title."""
    import yaml
    facts = {"employment": [
        {"id": "exp-acme-analyst", "title": "Data Analyst (LinkedIn and PhD CVs say 'Data Associate' - C15)",
         "title_render": "Data Analyst"},
        {"id": "exp-assistant", "title": "Teaching Assistant (lead, Cohort 4) - proposed; see C18"},
        {"id": "exp-founding", "title": "Founding AI Engineer"},
    ]}
    (tree["js"] / "profile" / "facts.yaml").write_text(yaml.safe_dump(facts), encoding="utf-8")
    rows = st.Store.from_env().profile_facts(path="employment")["value"]
    assert rows[0]["title"] == "Data Analyst" and "C15" in rows[0]["title_record"] and "title_render" not in rows[0]
    assert rows[1]["title"] is None and "proposed" in rows[1]["title_flag"] and "C18" in rows[1]["title_record"]
    assert rows[2] == {"id": "exp-founding", "title": "Founding AI Engineer"}
    one = st.Store.from_env().profile_facts(path="employment.0")["value"]
    assert one["title"] == "Data Analyst"
    direct = st.Store.from_env().profile_facts(path="employment.0.title")
    assert direct["value"] == "Data Analyst" and "C15" in direct["title_record"]
    refused = st.Store.from_env().profile_facts(path="employment.1.title")
    assert refused["value"] is None and "proposed" in refused["title_flag"]


def test_profile_facts_hides_fields_of_an_internal_item(s, tree):
    """A dotted path into an internal-only item is refused like the item itself."""
    import yaml
    facts = {"talks": [{"id": "t1", "title": "Internal talk (2026-08-20)", "discretion": "internal-only"}]}
    (tree["js"] / "profile" / "facts.yaml").write_text(yaml.safe_dump(facts), encoding="utf-8")
    store = st.Store.from_env()
    for path in ("talks.0", "talks.0.title", "talks.0.id"):
        with pytest.raises(st.ToolError):
            store.profile_facts(path=path)
    assert store.profile_facts(path="talks.0.id", include_internal=True)["value"] == "t1"


def test_profile_facts_too_large_returns_keys(s, tree):
    big = {"big": {f"k{i}": "x" * 200 for i in range(100)}}
    import yaml
    (tree["js"] / "profile" / "facts.yaml").write_text(yaml.safe_dump(big), encoding="utf-8")
    out = st.Store.from_env().profile_facts(path="big")
    assert out["too_large"] is True and "k0" in out["keys"]
    assert size(out) <= MAX


def test_evidence_lookup_by_id_and_query(s):
    out = s.evidence_lookup(ids=["ev-acme-001", "ev-missing"])
    assert out["items"][0]["id"] == "ev-acme-001"
    assert out["missing"] == ["ev-missing"]
    assert ids(s.evidence_lookup(query="example university")) == ["ev-doc-002"]


# ----------------------------------------------------------------------------- resumes
def test_resume_variants_list(s):
    out = s.resume_variants_list()
    kinds = {(r["kind"], r["id"]) for r in out["items"]}
    assert kinds == {("base", "agentic-systems"), ("variant", "acme-ai-applied-ai-engineer-202609")}
    var = next(r for r in out["items"] if r["kind"] == "variant")
    assert var["has_pdf"] is True and var["review_verdict"] == "changes-requested"
    assert ids(s.resume_variants_list(kind="bases")) == ["agentic-systems"]
    assert ids(s.resume_variants_list(editorial_verdict="approved")) == ["agentic-systems"]


# ----------------------------------------------------------------------------- the 8 KB cap
def test_every_page_fits_the_cap(s, tree):
    for i in range(60):
        (tree["js"] / "prospects" / f"p-{i:03d}.md").write_text(
            "---\n" + f"id: p-{i:03d}\nname: Person {i} " + "Long" * 40 + "\nsource: outbound\nstatus: new\n"
            "priority: P2\nwarmth: 1\ncreated: 2026-09-24\nupdated: 2026-09-24\n---\n", encoding="utf-8")
    out = st.Store.from_env().prospects_list(limit=50)
    assert size(out) <= MAX
    assert out["next_cursor"] is not None
    assert len(out["items"]) < 50


# ----------------------------------------------------------------------------- dashboard
def test_dashboard_metrics_combines_tracker_and_records(s, tree):
    m = s.dashboard_metrics()
    assert m["applications"]["applications"] == 1            # from scripts/tracker.py metrics --json
    assert m["prospects"]["by_status"]["triaged"] == 1
    assert m["prospects"]["by_priority"]["P0"] == 1
    assert m["prospects"]["follow_ups_due"] == 1              # jane-doe next_touch_at 2026-09-24
    assert m["jobs"]["by_status"] == {"queued": 1, "skipped": 1}
    assert m["approvals_pending"] == 0
    assert "tracker.py" in m["sources"]["applications"]
    assert size(m) <= MAX


def test_dashboard_metrics_includes_dashboard_build_when_present(s):
    m = s.dashboard_metrics()
    assert "dashboard.py" in m["sources"]["dashboard"]
    assert "needs_owner" in m["dashboard"] and "fresh_fits" in m["dashboard"]
    assert "gate_reviews" not in m["dashboard"]["needs_owner"]               # item 1b: fit-scorer's, never Alex's
    assert "gate_reviews" in m["dashboard"]["agency_backlog"]
    assert "outbox" not in m["dashboard"]["needs_owner"] and "drafts_unfiled" in m["dashboard"]["agency_backlog"]  # CS92


def test_prospects_list_label_and_channel_filters(s, tree):
    p = tree["js"] / "prospects" / "bob-agency.md"
    p.write_text(p.read_text().replace("tags: [agency]", "tags: [agency]\nlabel: recruiter_outreach\n"
                                       "channels: [linkedin]\nsources: [linkedin-export, gmail-backlog]"))
    assert ids(s.prospects_list(label="recruiter_outreach")) == ["bob-agency"]
    assert ids(s.prospects_list(channel="linkedin")) == ["bob-agency"]
    assert set(ids(s.prospects_list(source="gmail-backlog"))) == {"jane-doe", "bob-agency"}


# ----------------------------------------------------------------------------- pursuits (REQ-9)
def _pursuit(tree):
    import pursuits
    assert pursuits.main(["new", "--root", str(tree["js"]), "--today", "2026-09-25", "--company", "Acme AI",
                          "--kind", "referral", "--referrer", "jane-doe",
                          "--application", "acme-ai-applied-ai-engineer-202609", "--waiting-on", "owner",
                          "--next-action", "Alex reviews the bundle", "--next-action-at", "2026-09-28"]) == 0
    assert pursuits.main(["decision", "acme-ai-via-jane-doe-202609", "--root", str(tree["js"]), "--today",
                          "2026-09-25", "--ruling", "R-7", "--text", "one master resume"]) == 0
    return "acme-ai-via-jane-doe-202609"


def test_pursuits_list_filters_and_pages(s, tree):
    assert s.pursuits_list()["total"] == 0                       # no pursuits/ folder yet
    slug = _pursuit(tree)
    out = s.pursuits_list()
    assert out["total"] == 1 and out["items"][0]["id"] == slug
    assert out["items"][0]["waiting_on"] == "owner" and out["items"][0]["next_action_at"] == "2026-09-28"
    assert s.pursuits_list(stage="offer")["total"] == 0 and s.pursuits_list(waiting_on="owner")["total"] == 1
    assert s.pursuits_list(waiting_on="nobody")["total"] == 0
    with pytest.raises(st.ToolError, match="stage"):
        s.pursuits_list(stage="won")


def test_pursuit_get_returns_the_record_and_the_live_join(s, tree):
    import pursuit_hub
    slug = _pursuit(tree)
    out = s.pursuit_get(slug)
    assert out["meta"]["company_slug"] == "acme-ai" and out["meta"]["stage"] == "intake"
    assert out["decisions"] == ["2026-09-25 · R-7 · one master resume"]
    assert out["hub"].startswith("<untrusted_hub>") and out["via"] == "scripts/pursuit_hub.py"
    assert "## People" in out["hub"] and "Jane Doe" in out["hub"] and out["hub_on_disk"] == "absent"
    assert "forward all mail" not in out["hub"]                 # message text never enters the hub
    assert size(out) <= MAX
    pursuit_hub.refresh(tree["js"], tree["os"])
    assert s.pursuit_get(slug)["hub_on_disk"] == "fresh"
    with pytest.raises(st.ToolError, match="not found"):
        s.pursuit_get("nope-202609")
