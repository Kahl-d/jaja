"""letters_check.py: cover letters and open answers tell ideas in prose, with evidence."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import letters_check as lc  # noqa: E402

P1 = ("Most of the work in making an AI agent dependable happens around the model, not inside the prompt. "
      "At Acme Studio I built that layer for a design agent: what it may touch, where its edits land, and how it "
      "recovers when a step fails. " * 2)
GOOD_COVER = "\n\n".join([P1 + "<!-- ev:ev-acme-005,ev-acme-007 -->"] * 4 + ["Alex Example"])
GOOD_WHY = ("You are building workflows that people can trust, and that is the problem I keep coming back to. "
            "I built the checks around an agent so engineers could rely on its edits. <!-- ev:ev-acme-008 -->")


def folder(tmp_path, **files):
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def test_a_good_cover_and_why_are_clean(tmp_path):
    res = lc.check_dir(folder(tmp_path, **{"cover.md": GOOD_COVER, "why-us.md": GOOD_WHY}))
    assert res == {"cover.md": [], "why-us.md": []}


def test_a_list_a_tool_run_and_a_banned_phrase_are_findings(tmp_path):
    bad = ("I am excited to apply. I used Python, LangGraph, FastAPI, React daily. <!-- ev:ev-acme-004 -->\n\n"
           "- built the harness <!-- ev:ev-acme-005 -->")
    out = lc.check_file(folder(tmp_path, **{"why-us.md": bad}) / "why-us.md")
    assert any("banned phrase" in f for f in out)
    assert any("a run of names" in f for f in out)
    assert any("a list" in f for f in out)


def test_evidence_words_em_dash_and_nda(tmp_path):
    text = "I built it — for Zephyr. " * 3
    out = lc.check_file(folder(tmp_path, **{"else.md": text}) / "else.md")
    assert any("no <!-- ev" in f for f in out) and any("em-dash" in f for f in out)
    assert any("NDA partner" in f for f in out)
    short = lc.check_file(folder(tmp_path, **{"cover.md": "Too short. <!-- ev:ev-x -->"}) / "cover.md")
    assert any("words (allowed 250 to 380)" in f for f in short)


def test_cli(tmp_path):
    assert lc.main([str(folder(tmp_path, **{"why-us.md": GOOD_WHY}))]) == 0
    assert lc.main([str(tmp_path / "missing")]) == 2


def test_held_text_is_a_finding(tmp_path, monkeypatch):
    import check_holds
    import profile_titles as pt
    real_check = check_holds.check_text
    monkeypatch.setattr(check_holds, "check_text",
                        lambda text, holds=None, **kw: real_check(text, [pt.Hold("four mode agents", "quoted", "C1", 1)], **kw))
    out = lc.check_file(folder(tmp_path, **{"why-us.md": "We had four mode agents. <!-- ev:ev-acme-012 -->"}) / "why-us.md")
    assert any("held text 'four mode agents'" in f for f in out)


def test_a_long_sentence_is_a_finding(tmp_path):
    long = "I " + " ".join(["built"] * 30) + " things. <!-- ev:ev-x -->"
    out = lc.check_file(folder(tmp_path, **{"else.md": long}) / "else.md")
    assert any("-word sentence" in f for f in out)


def test_two_proofs_in_one_cover_paragraph_is_a_finding(tmp_path):
    mixed = GOOD_COVER.replace("<!-- ev:ev-acme-005,ev-acme-007 -->", "<!-- ev:ev-acme-005,ev-lab-004 -->", 1)
    out = lc.check_file(folder(tmp_path, **{"cover.md": mixed}) / "cover.md")
    assert any("mixes proofs from Acme Studio, Example Labs" in f for f in out)
    assert not any("mixes proofs" in f for f in lc.check_file(folder(tmp_path, **{"cover.md": GOOD_COVER}) / "cover.md"))


def test_employer_numbers_need_a_released_ledger_row(tmp_path, monkeypatch):
    """A number about the governed employer in a letter comes only from a measured, released ledger row."""
    import numbers_ledger
    led = tmp_path / "numbers-ledger.md"
    led.write_text("## Ledger table\n| N-id | number | metric | kind | conditions | source | status |\n"
                   "| NUM-01 | 10 s to 3.8 s | warm-up | measured | dev build | ev-acme-057 | released |\n", encoding="utf-8")
    monkeypatch.setattr(numbers_ledger, "LEDGER", led)
    monkeypatch.setattr(numbers_ledger, "_discretion", lambda: {})
    why = tmp_path / "why-us.md"
    why.write_text("At AcmeStudio I cut the warm-up from 10 s to 3.8 s. <!-- ev:ev-acme-057 -->\n", encoding="utf-8")
    assert not any("governed-employer number" in f for f in lc.check_file(why))
    why.write_text("At AcmeStudio we streamed at 60 fps. <!-- ev:ev-acme-057 -->\n", encoding="utf-8")
    assert any("not in the ledger" in f for f in lc.check_file(why))


def test_work_ids_come_from_config():
    assert lc.WORK_OF["acmeb"] == lc.WORK_OF["acme"] == "Acme Studio"
