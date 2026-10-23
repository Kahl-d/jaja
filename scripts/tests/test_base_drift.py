"""base_drift.py: a variant cut from an older base is caught before review (F71)."""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import base_drift as bd  # noqa: E402

OLD = "Built the evaluation bench where an LLM judge scored each run on an RF rubric"
NEW = "Built the evaluation bench where an LLM judge scored each run on an RF rubric of 8 gates"
KEEP = "Owned the desktop app end to end, from UI and UX to production releases"


def page(path: Path, bullets: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"cv": {"sections": {"Experience": [{"company": "DF", "highlights": bullets}]}}}),
                    encoding="utf-8")


def tree(tmp_path: Path, variant_bullets: list[str]) -> Path:
    page(tmp_path / "resumes/bases/archive/general-iter73/resume.yaml", [OLD, KEEP])
    page(tmp_path / "resumes/bases/general/resume.yaml", [NEW, KEEP])
    (tmp_path / "resumes/bases/general/README.md").write_text("Iteration 74, promoted", encoding="utf-8")
    page(tmp_path / "resumes/variants/v/resume.yaml", variant_bullets)
    return tmp_path / "resumes/variants/v"


def test_a_line_from_the_old_base_is_stale(tmp_path):
    res = bd.drift(tree(tmp_path, [OLD, KEEP]), tmp_path)
    assert res["current_base"] == "74" and [s["from_base"] for s in res["stale"]] == ["73"]


def test_a_variant_on_the_current_base_is_clean(tmp_path):
    v = tree(tmp_path, [NEW, KEEP, "A new line written for this role with its own evidence ids"])
    assert bd.drift(v, tmp_path)["stale"] == [] and bd.main([str(v), "--root", str(tmp_path)]) == 0
