"""motif_check.py: the editor's repeated-phrasing scan as a script (F66)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import motif_check as mc  # noqa: E402


def test_repeats_openers_and_runs():
    cv = {"sections": {"Experience": [{"highlights": ["Built the LLM judge", "Built an LLM judge again",
                                                      "Built a third LLM judge"]}]}}
    r = mc.report(cv)
    assert "3x llm judge" in r["MOTIFS"] and "3x built" in r["OPENERS"] and r["RUNS OF 3"] == "Built"


def test_cli_on_the_base(capsys):
    from layout import JS
    assert mc.main([str(JS / "resumes" / "bases" / "general" / "resume.yaml")]) == 0
    assert "MOTIFS:" in capsys.readouterr().out
