"""Tests for the long-bullet allowance in scripts/render_resume.py: a facts.yaml `rules:` entry, ruled by
the owner, that lets named bullets (keyed by their exact evidence-id set) run past MAX_CLAIM_LINES up to their
own max_lines (never more than 4). Synthetic, FICTIONAL data only (the Riley Moreno fixtures)."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import render_resume as rr  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "resume"
GOOD = FIX / "good.yaml"
EVIDENCE = FIX / "evidence.yaml"
CPL = 40                   # chars per line for these tests: four 8-letter words fill one line
PATH = "cv.sections.Experience[0].highlights[0]"


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def bullet(lines: int, ids: str = "nw-rag-01,nw-eval-01") -> str:
    """A fictional bullet that the estimate wraps to exactly `lines` lines at CPL."""
    text = " ".join(["measured"] * (4 * lines))
    assert rr.estimate_lines(text, CPL) == lines
    return f"{text} <!-- ev:{ids} -->"


def resume(lines: int, ids: str = "nw-rag-01,nw-eval-01") -> dict:
    """The fixture cut to one experience entry with one bullet, so only that bullet meets the narrow CPL."""
    data = load(GOOD)
    entry = data["cv"]["sections"]["Experience"][0]
    entry.pop("summary", None)
    entry["highlights"] = [bullet(lines, ids)]
    data["cv"].pop("headline", None)
    data["cv"]["sections"] = {"Experience": [entry]}
    return data


def ruling(items, **ruled) -> dict:
    block = {"by": rr.OWNER_TOKEN, "on": "2030-01-15", "basis": "fictional test ruling"}
    block.update(ruled)
    return {"rule": "Long bullets: the listed bullets may run past two lines", "ruled": block,
            "long_bullets": items}


def facts(*entries) -> dict:
    return {"rules": [{"rule": "an unrelated guard", "evidence": ["nw-role"]}, *entries]}


RULED = facts(ruling([{"evidence": ["nw-eval-01", "nw-rag-01"], "max_lines": 4, "label": "NW-01"}]))


def lint(data, facts_data):
    return rr.lint_resume(data, known_evidence=rr.load_evidence_ids(EVIDENCE), chars_per_line=CPL,
                          held=[], facts=facts_data)


def codes(report) -> list[str]:
    return [i.code for i in report.errors]


@pytest.mark.parametrize("lines", [3, 4])
def test_an_allowed_bullet_passes_with_a_note_naming_the_ruling(lines):
    report = lint(resume(lines), RULED)
    assert report.errors == [], [str(e) for e in report.errors]
    [note] = report.notes
    assert note.code == "long-bullet-allowed" and note.path == PATH
    assert "2030-01-15" in note.message and "facts.yaml rules[1]" in note.message
    assert "NW-01" in note.message and f"about {lines} lines" in note.message
    assert str(note).startswith("NOTE long-bullet-allowed ")


def test_the_evidence_set_matches_in_any_order():
    report = lint(resume(3, "nw-eval-01, nw-rag-01"), RULED)
    assert report.errors == [] and len(report.notes) == 1


def test_an_allowed_bullet_still_errors_past_its_allowed_max():
    report = lint(resume(5), RULED)
    [err] = [e for e in report.errors if e.code == "bullet-too-long"]
    assert err.path == PATH and "about 5 lines" in err.message
    assert "limit 4 lines" in err.message and "facts.yaml rules[1]" in err.message
    assert report.notes == []


def test_a_bullet_allowed_three_lines_errors_at_four():
    three = facts(ruling([{"evidence": ["nw-rag-01", "nw-eval-01"], "max_lines": 3}]))
    assert lint(resume(3), three).errors == []
    assert "bullet-too-long" in codes(lint(resume(4), three))


@pytest.mark.parametrize("ids", ["nw-rag-01", "nw-rag-01,nw-eval-01,nw-serve-01", "nw-serve-01"])
def test_an_unlisted_long_bullet_still_errors(ids):
    report = lint(resume(3, ids), RULED)
    [err] = [e for e in report.errors if e.code == "bullet-too-long"]
    assert "limit 2 lines" in err.message and report.notes == []


def test_the_allowance_covers_bullets_only_not_a_summary():
    data = resume(2)
    data["cv"]["sections"]["Experience"][0]["summary"] = bullet(3)
    report = lint(data, RULED)
    assert [e.path for e in report.errors if e.code == "bullet-too-long"] == ["cv.sections.Experience[0].summary"]


def test_no_facts_or_no_ruling_means_the_two_line_limit():
    for facts_data in (None, {}, facts()):
        assert "bullet-too-long" in codes(lint(resume(3), facts_data))


def test_a_short_listed_bullet_gets_no_note():
    report = lint(resume(2), RULED)
    assert report.errors == [] and report.notes == []


GOOD_ITEM = {"evidence": ["nw-rag-01", "nw-eval-01"], "max_lines": 3}


@pytest.mark.parametrize("entry, words", [
    (ruling([dict(GOOD_ITEM, max_lines=5)]), "max_lines"),
    (ruling([dict(GOOD_ITEM, max_lines=2)]), "max_lines"),
    (ruling([dict(GOOD_ITEM, max_lines="3")]), "max_lines"),
    (ruling([dict(GOOD_ITEM, max_lines=True)]), "max_lines"),
    (ruling([{"evidence": ["nw-rag-01"]}]), "max_lines"),
    (ruling([dict(GOOD_ITEM, evidence=[])]), "evidence"),
    (ruling([dict(GOOD_ITEM, evidence="nw-rag-01")]), "evidence"),
    (ruling([dict(GOOD_ITEM, evidence=["nw-rag-01", "bad id!"])]), "evidence"),
    (ruling([dict(GOOD_ITEM, evidence=["nw-rag-01", "nw-rag-01"])]), "evidence"),
    (ruling([dict(GOOD_ITEM, lines=3)]), "unknown key"),
    (ruling([dict(GOOD_ITEM, label=7)]), "label"),
    (ruling(["nw-rag-01"]), "mapping"),
    (ruling([]), "non-empty list"),
    (ruling({"evidence": ["nw-rag-01"], "max_lines": 3}), "non-empty list"),
    (ruling([GOOD_ITEM, dict(GOOD_ITEM, evidence=["nw-eval-01", "nw-rag-01"])]), "twice"),
    (ruling([GOOD_ITEM], by="jarvis"), "by"),
    (ruling([GOOD_ITEM], on="soon"), "on"),
    (ruling([GOOD_ITEM], basis=""), "basis"),
    ({"rule": "no ruling block", "long_bullets": [GOOD_ITEM]}, "ruled"),
    ({"rule": "bare on", "ruled": {"by": rr.OWNER_TOKEN, True: "2030-01-15", "basis": "x"},
      "long_bullets": [GOOD_ITEM]}, "quote"),
])
def test_a_malformed_allowance_is_an_error_and_allows_nothing(entry, words):
    report = lint(resume(3), facts(entry))
    bad = [e for e in report.errors if e.code == "long-bullet-rule"]
    assert bad and bad[0].path.startswith("facts.yaml rules[1]"), [str(e) for e in report.errors]
    assert words in " ".join(e.message for e in bad)
    assert "bullet-too-long" in codes(report) and report.notes == []


def test_the_same_bullet_in_two_rulings_is_an_error():
    report = lint(resume(3), facts(ruling([GOOD_ITEM]), ruling([dict(GOOD_ITEM, max_lines=4)])))
    assert "long-bullet-rule" in codes(report)


def test_a_dated_on_parses_as_a_date_object():
    import datetime as dt
    entry = ruling([GOOD_ITEM])
    entry["ruled"]["on"] = dt.date(2030, 1, 15)
    assert lint(resume(3), facts(entry)).errors == []


def test_lint_does_not_mutate_the_facts():
    data = facts(ruling([GOOD_ITEM]))
    before = copy.deepcopy(data)
    lint(resume(3), data)
    assert data == before


def _write(tmp_path: Path, data: dict, facts_data: dict | None) -> tuple[Path, Path]:
    src = tmp_path / "resume.yaml"
    src.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    fpath = tmp_path / "facts.yaml"
    if facts_data is not None:
        fpath.write_text(yaml.safe_dump(facts_data, sort_keys=False), encoding="utf-8")
    return src, fpath


def _long_resume() -> dict:
    """A bullet long enough to wrap past two lines at the house template width."""
    data = load(GOOD)
    cpl = rr.estimate_chars_per_line(rr.load_template())
    text = "Built a careful pipeline for evaluating agents"
    while rr.estimate_lines(text, cpl) < 3:
        text += " and measured it again"
    data["cv"]["sections"]["Experience"][0]["highlights"][0] = f"{text} <!-- ev:nw-rag-01,nw-eval-01 -->"
    assert 2 < rr.estimate_lines(text, rr.estimate_chars_per_line(rr.load_template())) <= 4
    return data


def test_cli_lint_honours_the_facts_ruling_and_prints_the_note(tmp_path, capsys):
    src, fpath = _write(tmp_path, _long_resume(), RULED)
    common = ["lint", "--input", str(src), "--evidence", str(EVIDENCE), "--density", "house", "--rulings", str(tmp_path / "no-rulings.yaml")]
    assert rr.main([*common, "--facts", str(fpath)]) == 0
    out = capsys.readouterr().out
    assert f"NOTE long-bullet-allowed {PATH}" in out and "1 note(s)" in out
    unruled = tmp_path / "unruled.yaml"
    unruled.write_text(yaml.safe_dump(facts(), sort_keys=False), encoding="utf-8")
    assert rr.main([*common, "--facts", str(unruled)]) == 1
    assert "bullet-too-long" in capsys.readouterr().out


def test_cli_lint_fails_on_a_malformed_ruling(tmp_path, capsys):
    src, fpath = _write(tmp_path, _long_resume(), facts(ruling([dict(GOOD_ITEM, max_lines=6)])))
    assert rr.main(["lint", "--input", str(src), "--evidence", str(EVIDENCE), "--facts", str(fpath),
                    "--density", "house", "--rulings", str(tmp_path / "no-rulings.yaml")]) == 1
    assert "long-bullet-rule" in capsys.readouterr().out


def test_cli_lint_without_a_facts_file_notes_it_and_keeps_the_limit(tmp_path, capsys):
    src, fpath = _write(tmp_path, _long_resume(), None)
    assert rr.main(["lint", "--input", str(src), "--evidence", str(EVIDENCE), "--facts", str(fpath),
                    "--density", "house", "--rulings", str(tmp_path / "no-rulings.yaml")]) == 1
    out = capsys.readouterr().out
    assert "no long-bullet rulings" in out and "bullet-too-long" in out


def test_render_lint_stage_honours_the_ruling(tmp_path, monkeypatch):
    """render() runs the same lint with the same facts file; stop it before the toolchain."""
    src, fpath = _write(tmp_path, _long_resume(), RULED)
    logged: list[str] = []

    def stop(*_a, **_k):
        raise rr.ToolchainError("stopped after lint (test)")

    monkeypatch.setattr(rr, "render_rendercv", stop)
    with pytest.raises(rr.ToolchainError):
        rr.render(src, tmp_path / "out", evidence_path=EVIDENCE, facts_path=fpath, rulings_path=tmp_path / "no-rulings.yaml",
                  log=logged.append)
    assert any(line.startswith("NOTE long-bullet-allowed") for line in logged)
    assert not any(line.startswith("FAIL lint") for line in logged)
