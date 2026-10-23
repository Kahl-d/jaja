"""reader_model_get (R-189): a package's section 6 Reader model, parsed, readable from any session (R-164)."""
from __future__ import annotations

import json
import textwrap

import pytest

from job_search_mcp import store as st

PACKAGE = """
# Acme AI · Applied AI Engineer

## 1 Title and lens
AI engineer.

## 6 Reader model
- **Reader:** the Applied AI team lead reads first; the CTO decides.
- **Wants**
W1 | "we need engineers who ship evals, not demos" | https://acme.ai/blog/evals, 2026-08-12
W2 | "agents that recover from tool failures" | https://acme.ai/talks/agents, 2026-07-01
W3 | "own a feature from prototype to production" | https://acme.ai/careers/123, 2026-09-20
- **Fears:** demo-ware; no tests.
- **Proof they trust:** shipped product, evals with numbers.
- **Their words**
evals | evaluation bench | ev-ps-029
tool failures | fail-closed permission callback | ev-ps-007
- **Answer table**
W1 | Built an evaluation bench on real user prompts with an LLM judge | ev-acme-023
W2 | Built a harness whose permission callback fails closed | ev-ps-007

## 8 Questions for Alex (at most 5)
- W3: which feature did you take from prototype to production alone?

## Sources
- https://acme.ai/blog/evals
check: wants 3
"""


@pytest.fixture
def s(tree):
    store = st.Store.from_env()
    p = store.js / "research" / "targets" / "acme-ai-applied-ai-engineer-202610.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(PACKAGE).lstrip(), encoding="utf-8")
    return store


def test_reader_model_get_parses_wants_answers_and_gaps(s):
    out = s.reader_model_get("acme-ai-applied-ai-engineer-202610")
    assert out["reader"].startswith("the Applied AI team lead")
    assert [w["id"] for w in out["wants"]] == ["W1", "W2", "W3"]
    assert "ship evals, not demos" in out["wants"][0]["words"] and "<untrusted_" in out["wants"][0]["words"]
    assert out["wants"][0]["source"] == "https://acme.ai/blog/evals, 2026-08-12"
    assert [a["id"] for a in out["answers"]] == ["W1", "W2"] and out["answers"][1]["ev"] == "ev-ps-007"
    assert out["gaps"] == [{"id": "W3", "asked": True}]
    assert out["their_words"][0] == {"their": "evals", "ours": "evaluation bench", "ev": "ev-ps-029"}
    assert out["fears"] == "demo-ware; no tests." and out["proof"].startswith("shipped product")
    assert out["check"]["errors"] == [] and len(json.dumps(out)) < 8000


def test_reader_model_get_reports_a_missing_package_or_section(s):
    with pytest.raises(st.ToolError):
        s.reader_model_get("nope-202610")
    p = s.js / "research" / "targets" / "bare-202610.md"
    p.write_text("# Bare\n\n## 1 Title and lens\nx\n", encoding="utf-8")
    out = s.reader_model_get("bare-202610")
    assert out["wants"] == [] and out["check"]["errors"]
