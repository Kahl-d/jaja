"""The learning loop is wired where it runs: the evening routine runs the wrap and no longer feeds the retired
Scout lessons (HUNT.md via hunt_lessons.py), the weekly routine runs finding_metrics, the routines read
targets/goals.yaml, the Recruiter's card reads the wrap by the name the script writes, and the debrief and
track-application skills write their lesson through learn.py's stable CLI."""
from __future__ import annotations

import re
import sys
from pathlib import Path

from layout import JS  # noqa: E402
sys.path.insert(0, str(JS / "scripts"))
import js_common as jc  # noqa: E402


def text(rel: str) -> str:
    return (JS / rel).read_text(encoding="utf-8")


def purpose(rel: str) -> str:
    return jc.fm.parse_yaml(text(rel))["purpose"]


def test_the_evening_routine_runs_the_wrap_and_retires_the_hunt_lesson_feed():
    spec = jc.fm.parse_yaml(text("routines/js-evening-review.yaml"))
    assert "python3 scripts/evening_wrap.py --write" in spec["purpose"]
    assert "state/recruiter/wrap-<date>.md" in spec["purpose"]
    assert "Lessons come from state/learn and LESSONS.md" in spec["purpose"]
    assert "state/recruiter/wrap-<date>.md" in spec["outputs"]
    assert "targets/goals.yaml" in spec["inputs"]


def test_the_weekly_routine_runs_finding_metrics():
    spec = jc.fm.parse_yaml(text("routines/js-weekly-metrics.yaml"))
    assert "python3 scripts/finding_metrics.py --write" in spec["purpose"]
    assert "state/reports/finding-<date>.md" in spec["outputs"]
    assert "finding_metrics.py --write" in text("skills/job-dashboard/SKILL.md")


def test_no_routine_reads_the_retired_target_yaml():
    for name in ("js-morning-brief", "js-evening-review", "js-weekly-metrics"):
        assert "state/recruiter/target.yaml" not in text(f"routines/{name}.yaml"), name


def test_the_recruiter_reads_the_wrap_by_the_name_the_script_writes():
    import evening_wrap as ew
    assert ew.wrap_path(JS, __import__("datetime").date(2026, 10, 6)).name == "wrap-2026-10-06.md"
    card = text("lanes/recruiter/AGENT.md")
    assert "state/recruiter/wrap-<date>.md" in card
    assert "scripts/evening_wrap.py --write" in text("skills/daily-job-plan/SKILL.md")


def test_debrief_and_rejections_write_a_lesson_through_learn_py():
    add = re.compile(r"python3 scripts/learn\.py add --role [a-z-]+ --observation \"[^\"]+\"\s+--evidence")
    assert add.search(text("skills/debrief/SKILL.md"))
    track = text("skills/track-application/SKILL.md")
    assert add.search(track) and "Each `rejected` event also writes a LESSONS candidate" in track
