"""network_for (R-195): the people in Alex's network at a company or of a role kind, from scripts/network_map.py."""
from __future__ import annotations

import json

import pytest

from job_search_mcp import store as st


@pytest.fixture
def s(tree):
    return st.Store.from_env()


def test_by_company_lists_the_people_there_with_kind_and_warmth_and_no_email(s):
    out = s.network_for("Acme AI")
    assert out["matched_by"] == "company" and out["count"] == 1
    [jane] = out["people"]
    assert (jane["name"], jane["kind"], jane["warmth"]) == ("Jane Doe", "recruiter", "warm")
    text = json.dumps(out)
    assert "jane@acme.example.com" not in text and len(text) < 8000
    assert "LinkedIn export" in out["sources"]["linkedin"] or "no parsed LinkedIn export" in out["sources"]["linkedin"]


def test_by_role_kind_and_an_empty_result_is_a_count_not_an_error(s):
    out = s.network_for("recruiter")
    assert out["matched_by"] == "role kind" and {p["name"] for p in out["people"]} == {"Jane Doe", "Bob Agency"}
    none = s.network_for("Nonexistent Co")
    assert none["count"] == 0 and "nobody" in none["note"]


def test_a_blank_query_is_a_clear_error(s):
    with pytest.raises(st.ToolError):
        s.network_for("  ")
