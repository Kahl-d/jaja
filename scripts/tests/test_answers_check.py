"""answers_check.py: the form-answer bank profile/answers/<family>.md is checked before anyone pastes from it:
the six sections are present, each within its word cap, every sentence carries an [ev:...] tag whose ids exist in
profile/evidence.yaml and are resume-ok, no em-dash, check_holds clean, status draft."""
from __future__ import annotations

from pathlib import Path

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import answers_check as ac  # noqa: E402

EVIDENCE = """schema_version: 1
evidence:
  ev-a-001: {source: x, note: harness}
  ev-a-002: {source: x, note: bench}
  ev-int-001: {source: x, note: secret, discretion: internal-only}
"""
HOLDS = "# Profile holds\n\n| Conflict | Held text (exact) | Where | Why | Unlock |\n|---|---|---|---|---|\n| C99 | \"Mars Colony\" | t | t | never |\n"

GOOD = """---
family: ai-engineer
status: draft for the owner
---
# Form answers: AI engineer (draft for the owner)

## Why this role (150 words max)
I built the harness for an agent that works on real designs. [ev:ev-a-001]
I also built the bench that proves it works. [ev:ev-a-002]

## A project you are proud of (200 words max)
The evaluation bench, which grades each run against the saved design file. [ev:ev-a-002]

## A technical challenge (200 words max)
Agents had to edit real designs without breaking them, so edits ran in a sandbox. [ev:ev-a-001]

## Why AI and agents (120 words max)
For the AI to produce the right things, you need to understand the components. [ev:ev-a-001]

## What you want next (100 words max)
More of this work: agents people rely on, with proof that they work. [ev:ev-a-001,ev-a-002]

## 100-word summary (100 words max)
Founding engineer who built an agent harness and an evaluation bench. [ev:ev-a-001,ev-a-002]
"""


@pytest.fixture
def js(tmp_path):
    root = tmp_path / "js"
    (root / "profile" / "answers").mkdir(parents=True)
    (root / "profile" / "evidence.yaml").write_text(EVIDENCE)
    (root / "profile" / "PROFILE-HOLDS.md").write_text(HOLDS)
    return root


def check(js, text, name="ai-engineer.md"):
    p = js / "profile" / "answers" / name
    p.write_text(text, encoding="utf-8")
    return ac.check_file(js, p)


def test_a_good_file_is_clean(js):
    assert check(js, GOOD) == []


def test_a_sentence_without_an_ev_tag_is_a_finding(js):
    bad = GOOD.replace("I also built the bench that proves it works. [ev:ev-a-002]", "I also built the bench that proves it works.")
    assert any("no [ev:" in f for f in check(js, bad))


def test_two_sentences_under_one_tag_is_a_finding(js):
    bad = GOOD.replace("I built the harness for an agent that works on real designs. [ev:ev-a-001]",
                       "I built the harness. It works on real designs. [ev:ev-a-001]")
    assert any("one sentence per [ev:" in f for f in check(js, bad))


def test_unknown_and_internal_only_ids_are_findings(js):
    bad = GOOD.replace("[ev:ev-a-002]\n\n## A project", "[ev:ev-zz-404]\n\n## A project").replace(
        "grades each run against the saved design file. [ev:ev-a-002]", "grades each run against the saved design file. [ev:ev-int-001]")
    found = check(js, bad)
    assert any("ev-zz-404" in f and "unknown" in f for f in found)
    assert any("ev-int-001" in f and "internal-only" in f for f in found)


def test_word_caps_are_enforced(js):
    long = " ".join(["word"] * 101) + ". [ev:ev-a-001]"
    bad = GOOD.replace("Founding engineer who built an agent harness and an evaluation bench. [ev:ev-a-001,ev-a-002]", long)
    assert any("100-word summary" in f and "101 words" in f for f in check(js, bad))


def test_missing_section_status_em_dash_and_held_text(js):
    bad = GOOD.replace("## What you want next (100 words max)\nMore of this work: agents people rely on, with proof that they work. [ev:ev-a-001,ev-a-002]\n", "")
    bad = bad.replace("status: draft for the owner", "status: final").replace("real designs.", "real designs \u2014 and Mars Colony.")
    found = check(js, bad)
    assert any("missing section: What you want next" in f for f in found)
    assert any("status" in f for f in found)
    assert any("em-dash" in f for f in found)
    assert any("held text" in f for f in found)


def test_cli_checks_every_file_and_exits_1_on_findings(js, capsys):
    check(js, GOOD)
    check(js, GOOD.replace("[ev:ev-a-002]\n\n## A project", "\n\n## A project"), name="evaluation.md")
    assert ac.main(["--root", str(js)]) == 1
    out = capsys.readouterr().out
    assert "evaluation.md" in out and "clean: profile/answers/ai-engineer.md" in out


def test_a_missing_bank_file_is_named_and_exits_2_never_a_traceback(js, capsys):
    """Four focus families have no bank file (founding-engineer among them): missing is
    not empty and not clean. The skills read exit 2 as 'no bank for this family; draft from the other inputs'."""
    (js / "profile" / "lenses").mkdir()
    (js / "profile" / "lenses" / "founding-engineer.md").write_text("# founding engineer\n")   # a family, no bank
    missing = js / "profile" / "answers" / "founding-engineer.md"
    assert ac.main(["--root", str(js), str(missing)]) == 2
    out = capsys.readouterr().out
    assert "missing: profile/answers/founding-engineer.md" in out and "clean" not in out.split("missing:")[0]
    check(js, GOOD)
    assert ac.main(["--root", str(js), str(js / "profile" / "answers" / "ai-engineer.md"), str(missing)]) == 2
    check(js, GOOD.replace("[ev:ev-a-002]\n\n## A project", "\n\n## A project"), name="evaluation.md")
    assert ac.main(["--root", str(js), str(js / "profile" / "answers" / "evaluation.md"), str(missing)]) == 1


def test_a_wrong_path_is_a_finding_not_a_missing_bank(js, capsys):
    """Exit 2 means 'this focus family has no bank' (profile/lenses/<family>.md exists); a typo, an unfilled <family>
    or a folder is a finding (exit 1), never a quiet draft without the bank."""
    (js / "profile" / "lenses").mkdir()
    (js / "profile" / "lenses" / "founding-engineer.md").write_text("# founding engineer\n")
    answers = js / "profile" / "answers"
    assert ac.main(["--root", str(js), str(answers / "founding-engineer.md")]) == 2
    (js / "profile" / "lenses" / "README.md").write_text("# lenses\n")      # a folder's README is no family
    for wrong in ("ai_engineer.md", "<family>.md", "README.md"):
        assert ac.main(["--root", str(js), str(answers / wrong)]) == 1
        assert "not a focus family" in capsys.readouterr().out
    assert ac.main(["--root", str(js), str(answers)]) == 1
    assert "not a bank file" in capsys.readouterr().out
