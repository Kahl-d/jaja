"""scripts/stage_batch.py: the REMOVE list keeps a skill's own parentheses, and the batch items come from config.

The approved line "Python (Programming Language) (duplicate of Python)" must stage as "REMOVE: Python (Programming
Language)": `x.split(" (")[0]` would cut every name at its first parenthesis (also SEO, DBMS, CSS, OOP). Only the trailing
reason goes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agency_config as cfg  # noqa: E402
import stage_batch as sb  # noqa: E402


@pytest.mark.parametrize("item, name", [
    ("Python (Programming Language) (duplicate of Python)", "Python (Programming Language)"),
    ("Search Engine Optimization (SEO) (unrelated)", "Search Engine Optimization (SEO)"),
    ("Database Management System (DBMS) (generic)", "Database Management System (DBMS)"),
    ("Cascading Style Sheets (CSS) (basic web, not the target)", "Cascading Style Sheets (CSS)"),
    ("Object-Oriented Programming (OOP) (generic)", "Object-Oriented Programming (OOP)"),
    ("Computer Science (generic (the degree says it))", "Computer Science"),
    ("Knowledge Graphs (graph wording kept off)", "Knowledge Graphs"),
    ("Render (no evidence)", "Render"),
    ("Git", "Git"),
])
def test_only_the_trailing_reason_is_dropped(item, name):
    assert sb.skill_name(item) == name


def test_the_remove_list_keeps_every_names_parentheses():
    line = ("REMOVE: Python (Programming Language) (duplicate of Python); Search Engine Optimization (SEO) (unrelated); "
            "Database Management System (DBMS) (generic); Cascading Style Sheets (CSS) (basic web); "
            "Object-Oriented Programming (OOP) (generic); Render (no evidence)")
    names = sb.remove_list(line)
    for kept in ("Python (Programming Language)", "Search Engine Optimization (SEO)",
                 "Database Management System (DBMS)", "Cascading Style Sheets (CSS)",
                 "Object-Oriented Programming (OOP)"):
        assert kept in names
    assert "Python" not in names and not any("evidence" in n or "generic" in n for n in names)


@pytest.fixture
def approved(tmp_path, monkeypatch):
    folder = tmp_path / "approved"
    folder.mkdir()
    (folder / "about.txt").write_text("About text for Alex.\n")
    (folder / "job.txt").write_text("Engineer, Acme Corp\n\nBuilt things.\n\n[note]\n")
    monkeypatch.setattr(sb, "A", folder)
    return folder


def test_items_come_from_the_batch_config(approved):
    cfg.override({"policy": {"linkedin_batch": {"live_ref": "LIVE (capture)", "items": [
        {"slug": "01-about", "label": "About", "field": "about", "where": "Profile, About.",
         "old": "{LIVE} lines 1-2", "body_file": "about.txt", "evidence": ["ev-1"], "checks": ["approved"]},
        {"slug": "02-job", "label": "Job", "field": "experience/acme", "where": "Experience.", "old": "none",
         "body_file": "job.txt", "body_mode": "description", "body_prefix": "Title: Engineer\nDescription:\n"},
        {"slug": "03-lang", "label": "Languages", "field": "languages", "body": "English: Full professional proficiency"},
    ]}}})
    try:
        items = sb.build_items()
    finally:
        cfg.reload()
    assert [i[0] for i in items] == ["01-about", "02-job", "03-lang"]
    assert items[0][4] == "LIVE (capture) lines 1-2" and items[0][5] == "About text for Alex." and items[0][6] == ["ev-1"]
    assert items[1][5] == "Title: Engineer\nDescription:\nBuilt things."
    assert items[2][5] == "English: Full professional proficiency"
