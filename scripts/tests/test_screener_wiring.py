"""The AI screener is a required reader of every page (agency charter).

Three readers must pass: a human, an ATS parser and an AI agent ranking the page as a recruiter's Claude would. The
ai-screener card exists at the judge row of the model policy, the page-review skill launches it beside the persona and
the company recruiter and runs screener_check.py, and the resume lane's roster, procedure and pipeline require it.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

from layout import JS  # noqa: E402
CHECK = r"python3 (?:-B )?scripts/screener_check\.py resumes/variants/<slug>/"


pytestmark = pytest.mark.real_records     # reads the data folder's role cards, skills and lane cards


def read(rel: str) -> str:
    return (JS / rel).read_text(encoding="utf-8")


def front(rel: str) -> dict:
    return yaml.safe_load(read(rel).split("---")[1])


def test_the_card_is_a_findings_only_judge_that_writes_screener_md():
    card = front("roles/ai-screener.md")
    assert card["name"] == "ai-screener"
    assert (card["model"], card["effort"]) == ("opus", "high")
    assert "Edit" in card["disallowed_tools"] and "Agent" in card["disallowed_tools"]
    assert card["tier_max"] == "T0"
    assert any("screener.md" in o for o in card["outputs"])
    text = read("roles/ai-screener.md")
    for need in ("AGENCY-CHARTER.md", "4 to 6", "synthetic", "1 to 10", "PASS", "REVISE",
                 "Top reasons", "Requirements without evidence", "Copied or stuffed lines", "page-1.png"):
        assert need in text, f"the card never says {need}"
    assert re.search(r"PASS\*\* when the page ranks 1st or 2nd[^.]*scores 8 or more", text)
    assert re.search(CHECK, text)


def test_page_review_launches_the_screener_and_gates_its_file():
    text = read("skills/page-review/SKILL.md")
    for reader in ("hiring-manager-persona", "technical-recruiter-reviewer", "ai-screener"):
        assert f"`{reader}`" in text, f"page-review never launches {reader}"
    assert "screener.md" in text and re.search(CHECK, text)
    assert re.search(r"only with the recruiter's APPROVE and the screener's PASS", text)
    desc = front("skills/page-review/SKILL.md")["description"]
    assert "screener" in desc.lower() and len(desc) <= 300


def test_the_resume_lane_requires_the_screener():
    assert "ai-screener" in yaml.safe_load(read("lanes/resume/LANE.yaml"))["roster"]
    agent = read("lanes/resume/AGENT.md")
    assert "screener.md" in agent and re.search(CHECK, agent) and "AGENCY-CHARTER.md section 8" in agent
    assert "screener" in read("skills/resume-for/SKILL.md")


def test_the_model_policy_row_matches_the_card():
    text = read("roles/references/model-policy.md")
    assert re.search(r"^\s+ai-screener: judge$", text, re.M)


def test_the_gate_version_line_names_the_screener_check():
    sys.path.insert(0, str(JS / "scripts"))
    import gate_versions as gv
    assert "screener_check" in gv.GATES and (JS / "scripts" / "screener_check.py").is_file()
