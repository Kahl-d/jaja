"""answers_check.py: a default run (no files named) reports every focus family in profile/lenses/ that has no bank file
as missing. Before, the default run printed "0 missing" while founding-engineer,
inference-and-serving, post-training and 3d-and-spatial-ai had no bank, so the routine check never showed the gap."""
from __future__ import annotations

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; no-network guard)

import answers_check as ac  # noqa: E402
from test_answers_check import EVIDENCE, GOOD, HOLDS  # noqa: E402


def tree(tmp_path):
    root = tmp_path / "js"
    (root / "profile" / "answers").mkdir(parents=True)
    (root / "profile" / "lenses").mkdir()
    (root / "profile" / "evidence.yaml").write_text(EVIDENCE)
    (root / "profile" / "PROFILE-HOLDS.md").write_text(HOLDS)
    (root / "profile" / "answers" / "ai-engineer.md").write_text(GOOD, encoding="utf-8")
    for family in ("ai-engineer", "founding-engineer", "post-training"):
        (root / "profile" / "lenses" / f"{family}.md").write_text(f"# {family}\n")
    (root / "profile" / "lenses" / "README.md").write_text("# lenses\n")
    return root


def test_a_default_run_names_every_family_without_a_bank(tmp_path, capsys):
    root = tree(tmp_path)
    assert ac.main(["--root", str(root)]) == 2
    out = capsys.readouterr().out
    assert "missing: profile/answers/founding-engineer.md" in out
    assert "missing: profile/answers/post-training.md" in out
    assert "README" not in out
    assert "1 clean, 0 with findings, 2 missing, of 3" in out


def test_a_default_run_with_every_family_banked_is_clean(tmp_path, capsys):
    root = tree(tmp_path)
    for family in ("founding-engineer", "post-training"):
        (root / "profile" / "answers" / f"{family}.md").write_text(GOOD.replace("ai-engineer", family), encoding="utf-8")
    assert ac.main(["--root", str(root)]) == 0
    assert "0 missing, of 3" in capsys.readouterr().out
