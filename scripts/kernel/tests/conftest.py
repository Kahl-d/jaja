"""Make scripts/kernel, scripts/ and hooks/ importable from the tests without packaging, and give every test a
`repo` fixture: a tmp tree laid out like the real repo (copied schemas and templates, empty agents/skills/lanes/
routines), used as code, data and runtime root at once (the single-tree form of scripts/kernel/roots.py)."""
import shutil
import sys
from pathlib import Path

import pytest

KERNEL = Path(__file__).resolve().parents[1]
SCRIPTS = KERNEL.parent
CODE = SCRIPTS.parent
HOOKS = CODE / "hooks"
for p in (KERNEL, SCRIPTS, HOOKS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

ROLE_CARD = """---
name: researcher
title: Researcher
department: agency
purpose: Find and verify facts before anyone reasons.
tools_allow: [Read, Grep, WebSearch]
tier_max: T0
model: sonnet
status: active
---
# Researcher

Search first. Cite every claim.
"""

SKILL = """---
name: brief
description: Use when the owner opens a session and wants the morning brief.
---
# Brief

1. Run `python3 scripts/dashboard.py --status`.
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """tmp/repo: the real schemas/ and templates/, one role card, one skill, nothing else."""
    root = tmp_path / "repo"
    root.mkdir()
    shutil.copytree(CODE / "schemas", root / "schemas")
    shutil.copytree(CODE / "templates", root / "templates", ignore=shutil.ignore_patterns("data", "orchestration"))
    (root / "agents").mkdir()
    (root / "agents" / "researcher.md").write_text(ROLE_CARD, encoding="utf-8")
    (root / "skills" / "brief").mkdir(parents=True)
    (root / "skills" / "brief" / "SKILL.md").write_text(SKILL, encoding="utf-8")
    for d in ("routines", "lanes", "work/approvals", "work/inbox", "work/tasks", "logs/runs"):
        (root / d).mkdir(parents=True)
    return root
