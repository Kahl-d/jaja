"""screener_check.py: the AI screener's screener.md is complete and its verdict follows its own numbers."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import screener_check as sc  # noqa: E402

RESUME = "basics: {name: Test}\n"
SHA = hashlib.sha256(RESUME.encode()).hexdigest()


def page(verdict="PASS", score=9, rank=1, pool=5, sha=SHA, rows=None, reasons=3, missing="- none",
         copied="- none", mustfix=None) -> str:
    rows = rows if rows is not None else (
        [f"| {rank} | candidate | the page | {score} |"]
        + [f"| {r} | S{i} | synthetic profile {i} | 6 |" for i, r in
           enumerate([r for r in range(1, pool + 1) if r != rank], start=1)])
    parts = ["---", f"verdict: {verdict}", f"score: {score}", f"rank: {rank}", f"pool_size: {pool}",
             f"resume_sha256: {sha}", "date: 2026-10-05", "---", "", "## Pool",
             "| rank | who | profile | score |", "|---|---|---|---|", *rows, "", "## Top reasons",
             *[f"{n}. reason {n}, quoting \"a line\"" for n in range(1, reasons + 1)], "",
             "## Requirements without evidence", missing, "", "## Copied or stuffed lines", copied, ""]
    if mustfix is not None:
        parts += ["## Must-fix", mustfix, ""]
    return "\n".join(parts)


def vdir(tmp_path: Path, text: str) -> Path:
    v = tmp_path / "v"
    v.mkdir(parents=True)
    (v / "resume.yaml").write_text(RESUME, encoding="utf-8")
    (v / "screener.md").write_text(text, encoding="utf-8")
    return v


def test_a_complete_pass_is_clean(tmp_path):
    assert sc.check(vdir(tmp_path, page())) == []


def test_a_complete_revise_with_must_fix_is_clean(tmp_path):
    assert sc.check(vdir(tmp_path, page("REVISE", score=6, rank=4, mustfix="- lead with the eval line"))) == []


def test_missing_file_is_a_finding(tmp_path):
    v = tmp_path / "v"
    v.mkdir()
    assert any("screener.md missing" in f for f in sc.check(v))


@pytest.mark.parametrize("kw,expect", [
    ({"verdict": "APPROVE"}, "verdict"),
    ({"verdict": "PASS if"}, "verdict"),
    ({"score": 11}, "score"),
    ({"score": 0}, "score"),
    ({"pool": 4, "rank": 1}, "pool_size"),
    ({"pool": 8, "rank": 1}, "pool_size"),
    ({"rank": 6, "pool": 5, "verdict": "REVISE", "score": 5, "mustfix": "- x"}, "rank"),
])
def test_bad_numbers_and_words(tmp_path, kw, expect):
    found = sc.check(vdir(tmp_path, page(**kw)))
    assert any(expect in f for f in found), found


@pytest.mark.parametrize("kw", [{"rank": 3}, {"score": 7}])
def test_pass_needs_top_two_and_eight(tmp_path, kw):
    found = sc.check(vdir(tmp_path, page(**kw)))
    assert any("PASS needs" in f for f in found), found


def test_revise_on_numbers_that_pass_is_a_finding(tmp_path):
    found = sc.check(vdir(tmp_path, page("REVISE", score=9, rank=1, mustfix="- x")))
    assert any("REVISE" in f and "PASS" in f for f in found), found


def test_revise_needs_must_fix(tmp_path):
    found = sc.check(vdir(tmp_path, page("REVISE", score=6, rank=4)))
    assert any("Must-fix" in f for f in found), found


def test_exactly_three_top_reasons(tmp_path):
    for n in (2, 4):
        found = sc.check(vdir(tmp_path / str(n), page(reasons=n)))
        assert any("Top reasons" in f for f in found), found


@pytest.mark.parametrize("kw,section", [({"missing": ""}, "Requirements without evidence"),
                                         ({"copied": ""}, "Copied or stuffed lines")])
def test_empty_lists_must_say_none(tmp_path, kw, section):
    found = sc.check(vdir(tmp_path, page(**kw)))
    assert any(section in f for f in found), found


def test_a_missing_section_is_named(tmp_path):
    text = page().replace("## Copied or stuffed lines", "## Other")
    assert any("Copied or stuffed lines" in f for f in sc.check(vdir(tmp_path, text)))


def test_pool_rows_must_match_pool_size_and_rank(tmp_path):
    rows = ["| 2 | candidate | the page | 9 |", "| 1 | S1 | x | 9 |", "| 3 | S2 | x | 6 |",
            "| 4 | S3 | x | 6 |", "| 5 | S4 | x | 6 |"]
    found = sc.check(vdir(tmp_path, page(rows=rows)))            # front matter says rank 1
    assert any("candidate" in f and "rank" in f for f in found), found
    found = sc.check(vdir(tmp_path / "b", page(rows=rows[:4], rank=2)))
    assert any("Pool" in f and "rows" in f for f in found), found


def test_no_candidate_row(tmp_path):
    rows = [f"| {r} | S{r} | x | 6 |" for r in range(1, 6)]
    assert any("candidate" in f for f in sc.check(vdir(tmp_path, page(rows=rows))))


def test_stale_resume_hash(tmp_path):
    found = sc.check(vdir(tmp_path, page(sha="0" * 64)))
    assert any("resume_sha256" in f for f in found), found


def test_bad_front_matter(tmp_path):
    found = sc.check(vdir(tmp_path, "---\nverdict: [PASS\n---\n"))
    assert any("front matter" in f for f in found), found


def test_cli_exit_codes(tmp_path, capsys):
    good = vdir(tmp_path, page())
    assert sc.main([str(good)]) == 0
    bad = vdir(tmp_path / "x", page(score=3))
    assert sc.main([str(bad)]) == 1
    assert sc.main([str(tmp_path / "nowhere")]) == 2
    out = capsys.readouterr().out
    assert "OK" in out and "FINDINGS" in out
