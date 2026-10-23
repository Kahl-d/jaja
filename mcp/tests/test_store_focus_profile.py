"""focus_profile_get (R-190): one focus profile's titles and its section 7 (best foot), by family or job title."""
from __future__ import annotations

import json
import textwrap

import pytest

from job_search_mcp import store as st

TOOL_USE = """
---
family: tool-use
titles_covered: ["Software Engineer, Tool Use*", "Tool Use Engineer", "Integrations Engineer (agents)"]
status: draft
---

Everything comes from our real work; only the presentation changes.

## 1. What this title means to its reader
x
## 2. What must be seen first
x
## 3. Real work
x
## 4. Proof
x
## 5. Traps
x
## 6. Example first bullet
x
## 7. Best foot forward
- Headline: AI engineer who builds the typed tools agents call.
- Vision: tools that refuse bad calls before they run.
- Page order: Example 1 first.
"""
EVAL = """
---
family: evaluation
titles_covered: ["Software Engineer, Evaluations*", "Evals Engineer"]
status: draft
---

## 1. What this title means to its reader
x
"""


@pytest.fixture
def s(tree):
    store = st.Store.from_env()
    d = store.js / "profile" / "lenses"
    d.mkdir(parents=True, exist_ok=True)
    (d / "tool-use.md").write_text(textwrap.dedent(TOOL_USE).lstrip(), encoding="utf-8")
    (d / "evaluation.md").write_text(textwrap.dedent(EVAL).lstrip(), encoding="utf-8")
    (d / "README.md").write_text("# readme\n", encoding="utf-8")
    return store


def test_by_family_returns_titles_and_the_best_foot(s):
    out = s.focus_profile_get("tool-use")
    assert out["family"] == "tool-use" and out["matched_by"] == "family"
    assert "Tool Use Engineer" in out["titles"] and "typed tools agents call" in out["best_foot"]
    assert out["best_foot_written"] is True and len(json.dumps(out)) < 8000


def test_by_title_picks_the_best_profile_and_says_when_section_7_is_missing(s):
    out = s.focus_profile_get("Software Engineer, Tool Use and Integrations")
    assert out["family"] == "tool-use" and out["matched_by"].startswith("title")
    ev = s.focus_profile_get("Evals Engineer")
    assert ev["family"] == "evaluation" and ev["best_foot_written"] is False and "not written" in ev["note"]


def test_no_match_is_a_clear_error(s):
    with pytest.raises(st.ToolError):
        s.focus_profile_get("Pastry Chef")
