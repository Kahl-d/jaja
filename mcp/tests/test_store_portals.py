"""Portal tools (R-178, R-183): every portal readable from any session (R-164): portals_list, portal_get, job_sightings."""
from __future__ import annotations

import json
import textwrap

import pytest

from job_search_mcp import store as st

REGISTRY = """
platforms:
  - id: linkedin
    name: LinkedIn Jobs
    url: https://www.linkedin.com/jobs/
    access: [email-alert, browser]
    control: alert-mail
    status: probation
    agent_terms: none
    setup_priority: P0
    alert_senders: [jobalerts-noreply@linkedin.com]
    tos: {quote: "browser plugins and add-ons ... to scrape or copy", url: https://www.linkedin.com/legal/user-agreement, read_on: 2026-10-01}
    alert_setup: |
      linkedin.com/jobs: turn on Job alert for each search; Daily; Email.
    presence: {account: {state: exists}, alerts: {state: arriving}, next_step: refresh the headline}
  - id: company-ats
    name: Company ATS boards
    url: https://boards-api.greenhouse.io
    access: [api]
    control: api
    status: active
    agent_terms: none
    alert_senders: []
    tos: {quote: "publicly available", url: https://docs.greenhouse.io, read_on: 2026-09-25}
    alert_setup: None.
presence_surfaces:
  - {id: github, name: GitHub, kind: code}
"""


def _w(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")


@pytest.fixture
def s(tree):
    store = st.Store.from_env()
    _w(store.js / "targets" / "platforms.yaml", REGISTRY)
    _w(store.js / "state" / "job-alerts.jsonl", '{"at": "2026-09-24T07:00:00Z", "platform": "linkedin"}\n')
    _w(store.js / "jobs" / "acme-ai-123.md", """
        ---
        id: acme-ai-123
        company: Acme AI
        company_slug: acme-ai
        title: Applied AI Engineer
        source: greenhouse
        source_url: https://boards.greenhouse.io/acme/jobs/123
        first_seen: 2026-09-23
        status: queued
        sightings:
        - {portal: linkedin, url: 'https://www.linkedin.com/jobs/view/1', first_seen: '2026-09-22'}
        - {portal: company-ats, url: 'https://boards.greenhouse.io/acme/jobs/123', first_seen: '2026-09-23'}
        similar: [acme-ai-124]
        ---
        # Applied AI Engineer
        """)
    _w(store.js / "jobs" / "acme-ai-124.md", """
        ---
        id: acme-ai-124
        company: Acme AI
        company_slug: acme-ai
        title: Applied AI Engineer, Evals
        source: linkedin-alert
        platform: linkedin
        source_url: https://www.linkedin.com/jobs/view/2
        first_seen: 2026-09-24
        status: discovered
        ---
        """)
    _w(store.js / "jobs" / "acme-ai-125.md", """
        ---
        id: acme-ai-125
        company: Acme AI
        title: Applied AI Engineer
        source: linkedin-alert
        platform: linkedin
        first_seen: 2026-09-22
        status: skipped
        duplicate_of: acme-ai-123
        ---
        """)
    return store


def test_portals_list_reads_every_portal_from_the_registry(s):
    out = s.portals_list()
    assert out["total"] == 2 and [r["id"] for r in out["items"]] == ["linkedin", "company-ats"]
    li = out["items"][0]
    assert (li["control"], li["account"], li["alerts"], li["agent_terms"]) == ("alert-mail", "exists", "arriving", "none")
    assert li["last_alert"] == "2026-09-24T07:00:00Z"
    assert [r["id"] for r in s.portals_list(control="api")["items"]] == ["company-ats"]
    assert s.portals_list(limit=1)["next_cursor"] == "1"


def test_portal_get_gives_the_checklist_terms_jobs_and_follow_ups(s):
    out = s.portal_get("linkedin")
    assert out["portal"]["id"] == "linkedin" and "turn on Job alert" in out["setup"]
    assert out["terms"]["url"] == "https://www.linkedin.com/legal/user-agreement"
    assert out["presence"]["next_step"] == "refresh the headline"
    assert {j["id"] for j in out["jobs"]["items"]} == {"acme-ai-123", "acme-ai-124", "acme-ai-125"}
    assert isinstance(out["follow_ups_due"], list)
    with pytest.raises(st.ToolError):
        s.portal_get("nowhere")


def test_job_sightings_follows_duplicates_to_the_canonical_job(s):
    out = s.job_sightings("acme-ai-125")
    assert out["canonical"] == "acme-ai-123" and out["duplicate_of"] == "acme-ai-123"
    assert [x["portal"] for x in out["sightings"]] == ["linkedin", "company-ats"]
    assert out["similar"] == [{"id": "acme-ai-124", "title": "Applied AI Engineer, Evals", "status": "discovered",
                               "first_seen": "2026-09-24"}]
    solo = s.job_sightings("acme-ai-124")
    assert solo["canonical"] == "acme-ai-124" and solo["sightings"] == [
        {"portal": "linkedin", "url": "https://www.linkedin.com/jobs/view/2", "first_seen": "2026-09-24"}]
    assert len(json.dumps(out)) < 8000
