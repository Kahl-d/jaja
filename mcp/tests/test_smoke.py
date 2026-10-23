"""Boot the MCP server in-process, list its tools and call a few through the real protocol."""
from __future__ import annotations

import asyncio
import json

from mcp import Client

from job_search_mcp.server import READ_TOOLS, WRITE_TOOLS, mcp

EXPECTED = {
    "jobs_search", "job_get", "prospects_list", "prospect_get", "applications_list", "application_get",
    "application_hub", "pursuits_list", "pursuit_get",
    "touches_recent", "dashboard_metrics", "profile_facts", "evidence_lookup", "resume_variants_list",
    "portals_list", "portal_get", "job_sightings", "reader_model_get", "focus_profile_get", "network_for",
    "prospect_set", "tracker_event", "outbox_create", "approval_create",
}
FORBIDDEN_WORDS = ("send", "post", "submit", "approve", "delete", "token", "password", "secret")


def run(coro):
    return asyncio.run(coro)


async def _session(fn):
    async with Client(mcp) as client:
        return await fn(client)


def test_lists_every_tool_with_a_usable_docstring(tree):
    tools = run(_session(lambda c: c.list_tools())).tools
    names = {t.name for t in tools}
    assert names == EXPECTED
    assert set(READ_TOOLS) | set(WRITE_TOOLS) == EXPECTED
    for t in tools:
        assert t.description and len(t.description) > 80, t.name
        assert not any(w in t.name for w in FORBIDDEN_WORDS), t.name
        hints = t.annotations
        assert hints is not None
        assert hints.read_only_hint is (t.name in READ_TOOLS), t.name
        if t.name in WRITE_TOOLS:
            assert hints.destructive_hint is False and hints.open_world_hint is False


def test_no_tool_takes_a_credential_parameter(tree):
    tools = run(_session(lambda c: c.list_tools())).tools
    for t in tools:
        for param in (t.input_schema.get("properties") or {}):
            assert not any(w in param.lower() for w in ("token", "password", "secret", "api_key", "credential"))


def test_call_read_and_write_tools_over_the_protocol(tree):
    async def go(client):
        listed = await client.call_tool("prospects_list", {"priority": "P0"})
        bad = await client.call_tool("job_get", {"job_id": "../../secrets"})
        made = await client.call_tool("prospect_set", {"prospect_id": "bob-agency", "fields": {"priority": "P2"}})
        return listed, bad, made

    listed, bad, made = run(_session(go))
    body = json.loads(listed.content[0].text)
    assert [r["id"] for r in body["items"]] == ["jane-doe"]
    assert len(listed.content[0].text.encode()) <= 8000
    assert bad.is_error is True and "invalid job_id" in bad.content[0].text
    assert json.loads(made.content[0].text)["changed"] == ["priority"]
