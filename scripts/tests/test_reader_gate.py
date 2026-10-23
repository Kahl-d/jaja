"""Reader-model gate: a focused or dream page whose Role Package fails `reader_model.py check` is blocked, in
apply_batch (status BLOCKED, "reader model: <first error>") and in rubric_check (predicate B20 FAIL). A package
written before reader models existed (no section 6, or no package file) is a WARN, never a block (new packages get
section 6 by the role-analyst card). The mass tier is exempt."""
from __future__ import annotations

import shutil

import pytest

from test_apply_pack import SLUG, TODAY, make_tree, write  # noqa: F401
from test_reader_model import GOOD
from test_rubric_check import files, matrix, resume  # noqa: F401  (pytest fixture `files`)

import apply_batch as ab  # noqa: E402
import rubric_check as rc  # noqa: E402
from js_common import fm  # noqa: E402

NO_SECTION = "# Role Package\n## 1 Title and lens\nAI engineer.\n## 8 Questions for the owner\nNone.\n"
BAD = GOOD.replace('W2 | "evals that catch regressions before release" | https://example.com/talk, 2026-06-02\n', "")


# ----------------------------------------------------------------------------- apply_batch
@pytest.fixture
def js(tmp_path):
    return make_tree(tmp_path / "job-search")


def _package(js, text):
    write(js / "research" / "targets" / f"{SLUG}.md", text)


def _batch(js, tier):
    assert ab.main(["--root", str(js), "--today", TODAY.isoformat(), "--tier", tier, SLUG]) in (0, 1)
    meta, body = fm.load(next((js / "state" / "apply-batches").glob("*.md")))
    return meta, body


@pytest.mark.parametrize("tier", ["focused", "dream"])
def test_focused_and_dream_block_a_failing_reader_model(js, tier):
    _package(js, BAD)
    meta, body = _batch(js, tier)
    assert meta["counts"]["blocked"] == 1 and meta["counts"]["ready"] == 0
    assert "reader model: Wants: 2 rows" in body


@pytest.mark.parametrize("tier", ["focused", "dream"])
def test_a_package_without_section_6_is_a_warning_not_a_block(js, tier):
    _package(js, NO_SECTION)
    meta, body = _batch(js, tier)
    assert meta["counts"]["ready"] == 1 and meta["counts"]["blocked"] == 0
    assert "reader model: none (written before" in body and "the role-analyst adds it on the next pass)" in body


def test_a_focused_page_with_no_package_file_is_a_warning(js):
    meta, body = _batch(js, "focused")
    assert meta["counts"]["ready"] == 1 and "reader model: no Role Package research/targets/" in body


def test_a_dream_page_with_no_package_file_is_blocked(js):
    """Full research is required on every dream page."""
    meta, body = _batch(js, "dream")
    assert meta["counts"]["blocked"] == 1 and meta["counts"]["ready"] == 0
    assert f"no role package: run /role-package {SLUG}" in body


def test_a_clean_reader_model_lets_the_page_through(js):
    _package(js, GOOD)
    meta, _ = _batch(js, "dream")
    assert meta["counts"]["ready"] == 1


def test_mass_is_exempt(js):
    _package(js, BAD)
    meta, _ = _batch(js, "mass")
    assert meta["counts"]["ready"] == 1


# ----------------------------------------------------------------------------- rubric_check B20
def _paths(files, tmp_path, package_text):
    paths = files(resume())
    m = tmp_path / "acme-ai-202610.matrix.yaml"
    shutil.move(paths["matrix"], m)
    paths["matrix"] = m
    if package_text is not None:
        (tmp_path / "acme-ai-202610.md").write_text(package_text, encoding="utf-8")
    return paths


def _b20(rep):
    return next((p for p in rep["predicates"] if p["id"] == "B20"), None)


@pytest.mark.parametrize("tier", ["focused", "dream"])
def test_rubric_b20_fails_a_bad_reader_model(files, tmp_path, tier):
    paths = _paths(files, tmp_path, BAD)
    rep = rc.run(resume=paths["resume"], base=paths["base"], matrix=paths["matrix"], jd=paths["jd"], tier=tier)
    p = _b20(rep)
    assert p["status"] == "FAIL" and "Wants: 2 rows" in p["findings"][0]["detail"] and "B20" in rep["failed"]


def test_rubric_b20_passes_a_clean_reader_model(files, tmp_path):
    paths = _paths(files, tmp_path, GOOD)
    rep = rc.run(resume=paths["resume"], base=paths["base"], matrix=paths["matrix"], jd=paths["jd"], tier="focused")
    assert _b20(rep)["status"] == "PASS"


def test_rubric_b20_warns_for_focused_when_no_package_sits_next_to_the_matrix(files, tmp_path):
    paths = _paths(files, tmp_path, None)
    rep = rc.run(resume=paths["resume"], base=paths["base"], matrix=paths["matrix"], jd=paths["jd"], tier="focused")
    assert _b20(rep)["status"] == "WARN" and "no Role Package" in _b20(rep)["findings"][0]["detail"]
    assert "B20" not in rep["failed"]


def test_rubric_b20_fails_for_dream_when_no_package_sits_next_to_the_matrix(files, tmp_path):
    paths = _paths(files, tmp_path, None)
    rep = rc.run(resume=paths["resume"], base=paths["base"], matrix=paths["matrix"], jd=paths["jd"], tier="dream")
    p = _b20(rep)
    assert p["status"] == "FAIL" and "B20" in rep["failed"]
    assert p["findings"][0]["detail"] == "no role package: run /role-package acme-ai-202610"


def test_rubric_b20_warns_on_a_package_written_before_reader_models(files, tmp_path):
    paths = _paths(files, tmp_path, NO_SECTION)
    rep = rc.run(resume=paths["resume"], base=paths["base"], matrix=paths["matrix"], jd=paths["jd"], tier="focused")
    p = _b20(rep)
    assert p["status"] == "WARN" and "B20" not in rep["failed"]
    assert p["findings"][0]["detail"] == "reader model: none (written before reader models existed; the role-analyst adds it on the next pass)"


@pytest.mark.parametrize("tier", ["mass", None])
def test_rubric_b20_is_absent_for_mass_and_no_tier(files, tmp_path, tier):
    paths = _paths(files, tmp_path, BAD)
    rep = rc.run(resume=paths["resume"], base=paths["base"], matrix=paths["matrix"], jd=paths["jd"], tier=tier)
    assert _b20(rep) is None
