"""numbers_ledger.py: a governed employer's numbers render only from measured, released ledger rows."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numbers_ledger as nl  # noqa: E402

@pytest.fixture(autouse=True)
def _no_evidence_file(tmp_path, monkeypatch):
    """The ledger rows cite fictional ids: read no evidence.yaml (no discretion data means every id is resume-ok),
    so a data folder's own evidence never decides these cases. A test that needs discretion sets its own."""
    monkeypatch.setattr(nl, "EVIDENCE", tmp_path / "no-evidence.yaml")


LEDGER = """# Acme Studio numbers ledger
## Analysis
| NUM-99 | 999 s | ignored: not in the ledger table | measured | x | ev-ps-001 | released |
## Ledger table
| N-id | number as written | metric | kind | conditions | source | status |
|---|---|---|---|---|---|---|
| NUM-01 | 10 s to 3.8 s | cold knowledge warm-up | measured | dev build, 2026-08, 5 runs | ev-ps-057 | released |
| NUM-02 | 2.6 s to under 100 ms | marketplace first open | measured | live app, 2026-07 | ev-ps-037 | held |
| NUM-03 | 8-12 s to under 5 s | first-message latency | target | checklist line | ev-dfl-032, C64 | held |
| NUM-04 | 74 PRs | PRs authored | measured | GitHub export 2026-09-29 | ev-dfai-033 | released |
## Notes
"""


def page(tmp_path, bullets, company="Acme Studio AI"):
    p = tmp_path / "resume.yaml"
    p.write_text(yaml.safe_dump({"cv": {"sections": {"Experience": [
        {"company": company, "summary": "AI co-pilot <!-- ev:ev-web-005 -->", "highlights": bullets},
        {"company": "Example Labs", "highlights": ["Voice at 420 ms p95 voice-to-voice"]}]}}}), encoding="utf-8")
    return p


def ledger(tmp_path):
    p = tmp_path / "numbers-ledger.md"
    p.write_text(LEDGER, encoding="utf-8")
    return p


def test_only_the_ledger_table_is_read(tmp_path):
    rows = nl.load(ledger(tmp_path))
    assert [r["id"] for r in rows] == ["NUM-01", "NUM-02", "NUM-03", "NUM-04"]
    assert [nl.released(r) for r in rows] == [True, False, False, True]


def test_a_measured_released_number_passes_and_names_its_row(tmp_path):
    res = nl.check(page(tmp_path, ["Cut the cold warm-up from 10 s to 3.8 s <!-- ev:ev-ps-057 -->"]), ledger(tmp_path))
    assert res["blocked"] == 0 and res["unknown"] == 0
    assert {f["why"].split()[0] for f in res["numbers"]} == {"NUM-01"} and "conditions:" in res["numbers"][0]["why"]


def test_targets_held_rows_and_blocked_sources_are_refused(tmp_path):
    res = nl.check(page(tmp_path, ["Cut first-message latency from 8-12s to under 5s",
                                   "First open from 2.6 s to under 100 ms"]), ledger(tmp_path))
    assert res["blocked"] >= 2
    assert nl.main(["check", res["file"], "--ledger", str(ledger(tmp_path))]) == 1


def test_a_number_not_in_the_ledger_warns_and_other_employers_are_not_read(tmp_path):
    res = nl.check(page(tmp_path, ["Served 60 fps streams"]), ledger(tmp_path))
    assert res["unknown"] == 1 and res["blocked"] == 0
    assert not any("420" in f["number"] for f in res["numbers"])     # Example Labs is not the governed employer


def test_no_ledger_means_every_number_warns_never_blocks(tmp_path):
    res = nl.check(page(tmp_path, ["Cut warm-up from 10 s to 3.8 s"]), tmp_path / "missing.md")
    assert res["ledger_rows"] is None and res["blocked"] == 0 and res["unknown"] == 2


def test_dates_versions_and_model_names_are_not_numbers():
    assert nl.numbers_in("Shipped v8.1.3 in Feb 2026 on 2026-02-14 for 3D RF with GPT-4 <!-- ev:ev-ps-101 -->") == []
    assert nl.numbers_in("74 PRs, 1,244 commits, 30% faster") == ["74 PRs", "1,244 commits", "30 %"]


def test_letters_are_read_by_paragraph(tmp_path):
    letter = tmp_path / "cover.md"
    letter.write_text("---\ntitle: x\n---\nAt AcmeStudio I cut warm-up from 10 s to 3.8 s.\n\nAt Example Labs we hit 420 ms.\n",
                      encoding="utf-8")
    res = nl.check(letter, ledger(tmp_path))
    assert [f["where"] for f in res["numbers"]] == ["paragraph 1", "paragraph 1"] and res["blocked"] == 0


def test_a_released_number_resting_only_on_internal_evidence_is_blocked(tmp_path, monkeypatch):
    ev = tmp_path / "evidence.yaml"
    ev.write_text(yaml.safe_dump({"evidence": {"ev-ps-057": {"discretion": "internal-only"},
                                               "ev-dfai-033": {"source": "x"}}}), encoding="utf-8")
    monkeypatch.setattr(nl, "EVIDENCE", ev)
    res = nl.check(page(tmp_path, ["Cut the warm-up from 10 s to 3.8 s", "Authored 74 PRs"]), ledger(tmp_path))
    verdicts = {f["number"]: f["verdict"] for f in res["numbers"]}
    assert verdicts["3.8 s"] == "blocked" and verdicts["74 PRs"] == "ok"
    assert any("internal-only" in f["why"] for f in res["numbers"])


def test_a_figure_matches_only_with_its_own_unit_never_by_digits_alone(tmp_path):
    res = nl.check(page(tmp_path, ["Cut cost 74x", "Grew to 74 users", "Took 3.8 seconds to warm"]), ledger(tmp_path))
    verdicts = {f["number"]: f["verdict"] for f in res["numbers"]}
    assert verdicts["74 x"] == "unknown" and verdicts["74 users"] == "unknown"     # NUM-04 is 74 PRs, not 74 of anything
    assert verdicts["3.8 seconds"] == "ok"                                            # same figure, another spelling


def test_labels_and_ids_are_not_figures():
    assert nl.numbers_in("Bio 2b, section 4a, base 72, ev-ps-029, the 20-minute talk") == ["20 minute"]


def test_n_of_m_is_one_figure(tmp_path):
    led = tmp_path / "l.md"
    led.write_text("## Ledger table\n| N-id | n | metric | kind | conditions | source | status |\n"
                   "| NUM-20 | 55 of 56 | criteria agreed | measured | A/B run | ev-ps-057 | released |\n", encoding="utf-8")
    assert nl.numbers_in("matched a grader (55 of 56 criteria)") == ["55 of 56"]
    res = nl.check(page(tmp_path, ["The judge matched a grader (55 of 56 criteria)"]), led)
    assert [(f["number"], f["verdict"]) for f in res["numbers"]] == [("55 of 56", "ok")]


def test_numbers_in_words_are_read_where_a_ledger_unit_follows(tmp_path):
    led = tmp_path / "l.md"
    led.write_text("## Ledger table\n| N-id | n | metric | kind | conditions | source | status |\n"
                   "| NUM-10 | 2 of 3 | sources agree | measured | rule | ev-ps-057 | released |\n"
                   "| NUM-11 | 8 gates | rubric gates | measured | rule | ev-ps-057 | released |\n", encoding="utf-8")
    res = nl.check(page(tmp_path, ["A rubric of eight gates; a verdict needed two of three sources; one owner per file"]), led)
    assert sorted((f["number"], f["verdict"]) for f in res["numbers"]) == [("2 of 3", "ok"), ("8 gates", "ok")]


def test_a_ledger_row_written_in_words_matches_the_page(tmp_path):
    led = tmp_path / "l.md"
    led.write_text("## Ledger table\n| N-id | n | metric | kind | conditions | source | status |\n"
                   "| NUM-121 | seven signed Windows releases (v8.0.5 to v8.1.3) | releases | measured | signed | ev-ps-057 "
                   "| released |\n", encoding="utf-8")
    res = nl.check(page(tmp_path, ["Shipped the app and seven signed Windows releases"]), led)
    assert [(f["number"], f["verdict"]) for f in res["numbers"]] == [("7 signed", "ok")]


def test_a_bare_figure_row_needs_its_metric_words_in_the_line():
    # every bare "3" in a governed paragraph used to read OK as a row about one team's size
    rows = [{"id": "NUM-118", "number": "3", "metric": "Acme team she led", "kind": "measured",
             "conditions": "x", "source": "ev-in-001", "status": "released", "resume_ok": True}]
    assert nl.classify("3", rows, "led a team of 3 at Acme")[0] == "ok"
    assert nl.classify("3", rows, "built 3 internal tools for the RF app")[0] == "unknown"
    far = "built 3 internal tools for the RF app over a long and careful quarter of work; " + "x " * 40 + "the team"
    assert nl.classify("3", rows, far)[0] == "unknown"                    # the metric word must sit near the figure


def test_a_comma_number_is_never_a_year_and_near_100_is_read():
    # "1,962" was read as the year 1962 and "near-100%" was dropped as a label like GPT-4
    assert nl.numbers_in("ran 1,962 checks across the app") == ["1,962 checks"]
    assert nl.numbers_in("released in 2026 to users") == []
    assert nl.numbers_in("IEEE IMS 2026, Boston") == []                    # a year before a comma stays a year
    assert nl.numbers_in("reached near-100% coverage") == ["100 %"]          # the gate writes "30 %" too
    assert nl.numbers_in("cut to sub-second, about-20% faster") == ["20 %"]
    assert nl.numbers_in("GPT-4 and HL7a") == []
