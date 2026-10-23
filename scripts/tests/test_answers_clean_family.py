"""apply_batch.answers_clean is scoped to the row's own focus family (ticket 4): a missing or dirty bank of another
family blocks no row; a missing bank of the row's own family blocks that row (missing is not clean)."""
from __future__ import annotations

from disc_harness import *  # noqa: F401,F403

import apply_batch as ab  # noqa: E402
from test_answers_check import EVIDENCE, GOOD, HOLDS  # noqa: E402

LENS = "---\nfamily: {f}\ntitles_covered: [{t}]\nstatus: draft\n---\n# {f}\n"


def tree(tmp_path):
    root = tmp_path / "js"
    (root / "profile" / "answers").mkdir(parents=True)
    (root / "profile" / "lenses").mkdir()
    (root / "profile" / "evidence.yaml").write_text(EVIDENCE)
    (root / "profile" / "PROFILE-HOLDS.md").write_text(HOLDS)
    for fam, titles in (("applied-ml-and-nlp", "Machine Learning Engineer*"), ("founding-engineer", "Founding Engineer*"),
                        ("post-training", "Post-Training*"), ("ai-engineer", "AI Engineer*")):
        (root / "profile" / "lenses" / f"{fam}.md").write_text(LENS.format(f=fam, t=titles))
    (root / "profile" / "answers" / "applied-ml-and-nlp.md").write_text(GOOD.replace("ai-engineer", "applied-ml-and-nlp"),
                                                                        encoding="utf-8")
    ab._ANSWERS.clear()
    return root


def test_the_row_family_is_the_best_focus_profile_for_its_title(tmp_path):
    root = tree(tmp_path)
    assert ab.row_family(root, "Machine Learning Engineer, API Platform") == "applied-ml-and-nlp"
    assert ab.row_family(root, "Founding Engineer") == "founding-engineer"
    assert ab.row_family(root, "Chief Gardener") is None


def test_a_clean_applied_ml_row_passes_while_other_families_banks_are_missing(tmp_path):
    root = tree(tmp_path)
    assert ab.answers_clean(root) == 2                                    # the whole bank: three families missing
    assert ab.answers_clean(root, "applied-ml-and-nlp") == 0              # this row's own family: clean
    assert ab.answers_clean(root, "founding-engineer") == 2               # a founding row is blocked: missing


def test_a_dirty_bank_of_another_family_does_not_block(tmp_path):
    root = tree(tmp_path)
    (root / "profile" / "answers" / "ai-engineer.md").write_text("no frontmatter, no sections\n", encoding="utf-8")
    ab._ANSWERS.clear()
    assert ab.answers_clean(root, "applied-ml-and-nlp") == 0
    assert ab.answers_clean(root, "ai-engineer") == 1


def test_the_result_is_cached_per_root_and_family(tmp_path):
    root = tree(tmp_path)
    assert ab.answers_clean(root, "applied-ml-and-nlp") == 0
    (root / "profile" / "answers" / "applied-ml-and-nlp.md").unlink()
    assert ab.answers_clean(root, "applied-ml-and-nlp") == 0              # cached for the run
    assert ab.answers_clean(root, "post-training") == 2                   # another family is its own entry
