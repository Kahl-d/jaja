"""check_holds.py and the shared held-text matching in profile_titles.py .

A plain substring check is blind to Markdown emphasis and split markers: a held string can be
split by bold markers or a line break ("**+2.1 Macro-F1** over BERT-base"). These tests plant
held strings under every kind of emphasis and split, check the passes that must find them (exact, plain,
loose), the ones that must not fire (word boundaries, the ruled phone and title forms), the gap register
rule for term holds, the CLI, and that profile_titles and render_resume lint share the same matching.
The fixture holds are fictional-shaped copies of real rows; two tests read the real profile read-only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import check_holds as chk  # noqa: E402
import profile_titles as pt  # noqa: E402
import render_resume as rr  # noqa: E402
import held_safe as hs  # noqa: E402  (tests/: compare real holds without printing a private one)

from layout import JS  # noqa: E402
REAL_HOLDS = JS / "profile" / "PROFILE-HOLDS.md"
FIX = Path(__file__).resolve().parent / "fixtures" / "resume"

HOLDS = """\
---
title: Profile holds (fixture)
---
# Profile holds (fixture)

| Conflict | Held text (exact) | Where it came from | Why held | What unlocks it |
|---|---|---|---|---|
| C02 | "+2.1 Macro-F1 over BERT-base"; "0.97 precision on target classes" | resume | preprint | never |
| C15 | "Data Associate" | LinkedIn | title conflict | a ruling |
| C25 | "cutting visibility from 5-30 s to under 1 s" | resume | unmeasured | a measurement |
| C31 | "Best AI Emerging Technology" | CV | variant | never |
| C14 | "Aug 2017 - Aug 2021" | resume | wrong month | never |
| C44 | "Alex M Example"; "+1-555-0100" | resume | variants | never |
| C46 | "Student Leader, Residential Life" as the sole title | CV | variant | never |
| C09 | "LangSmith" | resume | unconfirmed | a yes |
| C03 | "LoRA/PEFT"; "4-bit" | resume | unconfirmed | a project |

## Skills removed

| Term | Why held | What unlocks it |
|---|---|---|
| RLHF/DPO | no corpus narrative | a project that used it |
| vLLM | no corpus narrative | a project that used it |
| LangSmith | held under C09 | C09 yes |
"""


@pytest.fixture
def holds_file(tmp_path) -> Path:
    path = tmp_path / "PROFILE-HOLDS.md"
    path.write_text(HOLDS, encoding="utf-8")
    return path


@pytest.fixture
def holds(holds_file) -> list[pt.Hold]:
    return pt.load_holds(holds_file)


def passes(text: str, holds: list[pt.Hold]) -> dict[str, str]:
    """held string -> the pass that found it (gap notes excluded)."""
    return {h.held: h.pass_ for h in chk.check_text(text, holds) if not h.gap}


# --------------------------------------------------------------------------- loading
def test_load_holds_keeps_kind_and_row_and_load_held_is_unchanged(holds_file, holds):
    by_text = {h.text: h for h in holds}
    assert by_text["+2.1 Macro-F1 over BERT-base"].kind == "quoted"
    assert by_text["+2.1 Macro-F1 over BERT-base"].ref == "C02"
    assert by_text["vLLM"].kind == "term" and by_text["vLLM"].ref == "vLLM"
    assert {"RLHF/DPO", "RLHF", "DPO"} <= {h.text for h in holds if h.kind == "term"}
    assert pt.load_held(holds_file) == list(dict.fromkeys(h.text for h in holds))
    assert all(h.line > 0 for h in holds)


def test_real_holds_load_through_the_same_parser():
    held = pt.load_held(REAL_HOLDS)
    assert held, "the real holds list is empty"
    diff = hs.same_without_values(sorted(set(held)), sorted({h.text for h in pt.load_holds(REAL_HOLDS)}),
                                  ("load_held", "load_holds"))
    assert not diff, diff


# --------------------------------------------------------------------------- the passes
@pytest.mark.parametrize("text", [
    "Beat the baseline: **+2.1 Macro-F1** over BERT-base.",               # bold splits it (the real miss)
    "Beat the baseline: *+2.1 Macro-F1* over BERT-base.",                 # italic
    "Beat the baseline: __+2.1 Macro-F1__ over BERT-base.",               # underscore bold
    "Beat the baseline: _+2.1 Macro-F1_ over BERT-base.",                 # underscore italic
    "Beat the baseline: `+2.1 Macro-F1` over BERT-base.",                 # code span
    "Beat the baseline: [+2.1 Macro-F1](https://example.com/x) over BERT-base.",   # link text
    "Beat the baseline: +2.1 Macro-F1 over\nBERT-base.",                  # a hard line wrap
    "> Beat the baseline: +2.1 Macro-F1 over\n> BERT-base.",              # a wrapped blockquote
    "Beat the baseline: +2.1 Macro-F1 <!-- ev:x --> over BERT-base.",     # an evidence comment inside
    "Beat the baseline: +2.1 Macro-F1 over BERT-base.",              # a non-breaking space
    "Beat the baseline: +2.1 **Macro**-F1 over BERT-**base**.",           # emphasis inside words
])
def test_the_plain_pass_finds_a_held_string_that_emphasis_or_a_split_hides(text, holds):
    assert "+2.1 Macro-F1 over BERT-base" not in text                     # the old check misses it
    assert passes(text, holds).get("+2.1 Macro-F1 over BERT-base") == "plain"


def test_the_exact_pass_is_the_old_substring_check(holds):
    found = chk.check_text("Worked as a Data Associate in 2021.", holds)
    assert [(h.held, h.pass_, h.line, h.ref) for h in found] == [("Data Associate", "exact", 1, "C15")]


@pytest.mark.parametrize("text, held", [
    ("cutting visibility from 5-30s to under 1s", "cutting visibility from 5-30 s to under 1 s"),   # spacing
    ("BEST AI EMERGING TECHNOLOGY winner", "Best AI Emerging Technology"),                        # case
    ("B.Tech, Aug 2017 – Aug 2021", "Aug 2017 - Aug 2021"),                                  # en-dash
    ("B.Tech, Aug 2017 — Aug 2021", "Aug 2017 - Aug 2021"),                                  # em-dash
    ("B.Tech, Aug 2017-Aug 2021", "Aug 2017 - Aug 2021"),                                         # no spaces
])
def test_the_loose_pass_normalizes_whitespace_case_and_typographic_punctuation(text, held, holds):
    assert passes(text, holds).get(held) == "loose"


@pytest.mark.parametrize("text", [
    "Extracted metadata associated with every record.",          # 'Data Associate' inside other words
    "LinkedIn: linkedin.com/in/alexm-example",                    # 'Alex M Example' is not this URL
    "Phone: +1 (555) 0100",                                   # the ruled phone form 
    "Resident Assistant (Student Leader), Residential Life, Example University",   # the ruled title form 
    "0.97 precision on target class labels was not measured",   # a different phrase ('classes')
    "Wrote snake_case_helpers and a tool_use parser.",           # intra-word underscores stay
    "Portfolio: alexmexampleportfolio.example.com and alexmexample.example.com",   # words never fuse
    "Worked as a DataAssociate",                                  # two words fused into one are not the hold
])
def test_no_pass_fires_on_ruled_forms_or_inside_other_words(text, holds):
    assert passes(text, holds) == {}


def test_a_case_variant_hold_does_not_double_report_the_same_place(holds_file, tmp_path):
    two = tmp_path / "two.md"
    two.write_text(HOLDS.replace('"Alex M Example";', '"Alex M Example"; "ALEX M EXAMPLE";'), encoding="utf-8")
    found = chk.check_text("# Alex M Example\n", pt.load_holds(two))
    assert [(h.held, h.pass_) for h in found] == [("Alex M Example", "exact")]


def test_short_held_strings_match_loosely_only_in_their_own_case(holds):
    """Case folding would turn a short hold into noise ('ALMA' is held, 'Alma' is right): only 12+ characters
    fold case. Spacing around punctuation is forgiven at any length."""
    assert passes("Uses vllm and langsmith daily.", holds) == {}
    assert passes("Uses vLLM daily.", holds) == {"vLLM": "exact"}
    assert passes("Fine-tuning (LoRA / PEFT) in 4 - bit.", holds) == {"LoRA/PEFT": "loose", "4-bit": "loose"}
    assert passes("Fine-tuning (lora / peft) in 4 bit mode.", holds) == {}      # case differs; no dash


def test_each_occurrence_is_reported_once_by_the_strictest_pass_that_finds_it(holds):
    found = chk.check_text("Data Associate\n**Data** Associate\ndata associate here\n", holds)
    assert [(h.held, h.pass_, h.line) for h in found] == [
        ("Data Associate", "exact", 1), ("Data Associate", "plain", 2), ("Data Associate", "loose", 3)]


def test_hits_carry_the_line_where_they_start(holds):
    text = "---\ntitle: x\n---\n# Resume\n\nLine five.\nBeat it by **+2.1 Macro-F1** over\nBERT-base.\n"
    (hit,) = chk.check_text(text, holds)
    assert (hit.pass_, hit.line) == ("plain", 7)


def test_frontmatter_is_checked_exactly_but_not_loosely(holds):
    """Frontmatter is metadata (file lists, notes), not rendered text: the exact pass reads it, the others do not."""
    text = "---\ntitle: BEST AI EMERGING TECHNOLOGY notes\nnote: Data Associate\n---\nBody.\n"
    assert passes(text, holds) == {"Data Associate": "exact"}
    assert passes(text.replace("---\n", "", 1).replace("---\n", ""), holds) == {
        "Data Associate": "exact", "Best AI Emerging Technology": "loose"}


# --------------------------------------------------------------------------- gap register
GAP_DOC = """\
# Master profile

## Skills
Python, PyTorch.

## Coverage matrix
| Competency | Status |
|---|---|
| Inference | partial (no model serving; no vLLM) |

Gaps recorded, not filled: RLHF/DPO and post-training.

### Date audit
All dates checked.

## Open decisions for the owner
None.
"""


def test_a_term_named_in_the_gap_register_is_a_recorded_gap_not_a_claim(holds):
    found = chk.check_text(GAP_DOC, holds, markdown=True)
    assert found and all(h.gap for h in found)
    assert {h.held for h in found} == {"vLLM", "RLHF/DPO", "RLHF", "DPO"}


def test_the_same_term_outside_the_gap_register_fails(holds):
    doc = GAP_DOC.replace("Python, PyTorch.", "Python, PyTorch, vLLM.")
    bad = [h for h in chk.check_text(doc, holds, markdown=True) if not h.gap]
    assert [(h.held, h.line) for h in bad] == [("vLLM", 4)]


def test_quoted_held_text_fails_even_inside_the_gap_register(holds):
    doc = GAP_DOC.replace("All dates checked.", "Data Associate stays off (C15).")
    bad = [h for h in chk.check_text(doc, holds, markdown=True) if not h.gap]
    assert [h.held for h in bad] == ["Data Associate"]


def test_a_term_that_is_also_quoted_text_is_never_a_gap(holds):
    """LangSmith is a Term row and quoted under C09: quoted wins, so the gap register cannot excuse it."""
    doc = GAP_DOC.replace("no vLLM", "no LangSmith")
    bad = [h for h in chk.check_text(doc, holds, markdown=True) if not h.gap]
    assert [h.held for h in bad] == ["LangSmith"]


def test_the_gap_rule_applies_to_markdown_only(holds):
    assert [h for h in chk.check_text(GAP_DOC, holds, markdown=False) if not h.gap]


# --------------------------------------------------------------------------- the CLI
def test_cli_exits_0_on_a_clean_file_and_1_on_a_hit(tmp_path, holds_file, capsys):
    clean = tmp_path / "clean.md"
    clean.write_text("# Resume\n\nBuilt a voice agent at 420 ms p95 voice-to-voice.\n", encoding="utf-8")
    dirty = tmp_path / "dirty.md"
    dirty.write_text("# Resume\n\nBeat it by **+2.1 Macro-F1** over BERT-base.\n", encoding="utf-8")
    assert chk.main(["--holds", str(holds_file), str(clean)]) == 0
    assert "OK" in capsys.readouterr().out
    assert chk.main(["--holds", str(holds_file), str(clean), str(dirty)]) == 1
    out = capsys.readouterr().out
    assert "dirty.md:3" in out and "plain" in out and "+2.1 Macro-F1 over BERT-base" in out and "C02" in out


def test_cli_json_lists_every_hit_with_its_pass_and_line(tmp_path, holds_file, capsys):
    f = tmp_path / "r.yaml"
    f.write_text('cv:\n  highlights:\n    - "Title: Data Associate"\n    - "**+2.1 Macro-F1** over BERT-base"\n',
                 encoding="utf-8")
    assert chk.main(["--holds", str(holds_file), "--json", str(f)]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and data["held_count"] == len(pt.load_held(holds_file))
    hits = data["files"][0]["hits"]
    assert [(h["held"], h["pass"], h["line"], h["ref"]) for h in hits] == [
        ("Data Associate", "exact", 3, "C15"), ("+2.1 Macro-F1 over BERT-base", "plain", 4, "C02")]


def test_cli_refuses_a_missing_file_or_holds_list(tmp_path, holds_file, capsys):
    assert chk.main(["--holds", str(holds_file), str(tmp_path / "nope.md")]) == 2
    f = tmp_path / "a.md"
    f.write_text("text\n", encoding="utf-8")
    assert chk.main(["--holds", str(tmp_path / "missing.md"), str(f)]) == 2      # missing is not empty
    assert "missing" in capsys.readouterr().err.lower()


def test_the_real_profile_is_clean(capsys):
    """PROFILE.md names held skills only as recorded gaps in its Coverage matrix (gap notes, not hits).
    The real holds include private ones: the output is consumed and hits are named without their text."""
    code = chk.main([str(JS / "profile" / "PROFILE.md")])
    capsys.readouterr()
    found = chk.check_file(JS / "profile" / "PROFILE.md", pt.load_holds(REAL_HOLDS))
    bad = [f"PROFILE.md:{h.line}: {h.pass_} {hs.shown(h.held)} ({h.kind} {h.ref})" for h in found if not h.gap]
    ok = code == 0 and not bad
    assert ok, f"check_holds exit {code}; {len(bad)} hit(s):\n" + "\n".join(bad)


# --------------------------------------------------------------------------- the private holds list 
# profile/private-holds.txt (gitignored) sits beside PROFILE-HOLDS.md: one held string per line, so a figure can
# be held without being printed in a tracked file. The planted value here is fictional.
PRIVATE = "# held privately: never copied anywhere\n\nhousing stipend of 1,234 fictional units\n  # indented comment\n"
PLANTED = "housing stipend of 1,234 fictional units"


@pytest.fixture
def private_file(holds_file) -> Path:
    path = holds_file.parent / pt.PRIVATE_HOLDS_NAME
    path.write_text(PRIVATE, encoding="utf-8")
    return path


def test_private_holds_load_beside_the_holds_list(holds_file, private_file):
    holds = pt.load_holds(holds_file)
    private = [h for h in holds if h.text == PLANTED]
    assert len(private) == 1 and private[0].kind == "quoted" and private[0].ref == pt.PRIVATE_HOLDS_NAME
    assert private[0].line == 3 and private[0].entry == 1                     # line in the file; entry number
    assert not any(h.text.startswith("#") or not h.text for h in holds)      # comments and blank lines ignored
    assert PLANTED in pt.load_held(holds_file)
    assert pt.title_problems(f"Resident Assistant ({PLANTED})", pt.load_held(holds_file))


def test_an_absent_private_file_changes_nothing(tmp_path, holds_file):
    before = pt.load_holds(holds_file)
    assert not (holds_file.parent / pt.PRIVATE_HOLDS_NAME).exists()
    assert pt.load_held(holds_file) == list(dict.fromkeys(h.text for h in before))
    assert PLANTED not in pt.load_held(holds_file)
    (holds_file.parent / pt.PRIVATE_HOLDS_NAME).write_text("# only a comment\n\n", encoding="utf-8")
    assert pt.load_holds(holds_file) == before                                   # comments only: no change


def test_private_holds_never_stand_in_for_a_missing_holds_list(tmp_path):
    (tmp_path / pt.PRIVATE_HOLDS_NAME).write_text(PLANTED + "\n", encoding="utf-8")
    assert pt.load_holds(tmp_path / "PROFILE-HOLDS.md") == []                   # missing is not empty


@pytest.mark.parametrize("text,pass_", [
    (f"Resident Assistant; {PLANTED}.\n", "exact"),
    ("Resident Assistant; **housing stipend** of 1,234 fictional units.\n", "plain"),
    ("Resident Assistant; housing stipend of 1,234\nfictional units.\n", "plain"),
])
def test_check_holds_catches_a_planted_private_hold(tmp_path, holds_file, private_file, capsys, text, pass_):
    f = tmp_path / "draft.md"
    f.write_text("# Draft\n\n" + text, encoding="utf-8")
    assert passes(f.read_text(encoding="utf-8"), pt.load_holds(holds_file)) == {PLANTED: pass_}
    assert chk.main(["--holds", str(holds_file), str(f)]) == 1
    out, err = capsys.readouterr()
    assert "draft.md:3" in out and pass_ in out and pt.PRIVATE_HOLDS_NAME in out
    assert "<private hold #1>" in out and PLANTED not in out + err and repr(PLANTED) not in out + err


# --------------------------------------------------------------------------- a private hold is never printed 
# Every output that names a held string names a private one `<private hold #N>`, N its entry number in the file
# (comments and blank lines not counted). Both values below are fictional.
SECOND = "relocation bonus of 5,678 fictional units"
TWO_PRIVATE = f"# fictional\n{PLANTED}\n\n# another comment\n{SECOND}\n"


@pytest.fixture
def two_private(holds_file) -> Path:
    path = holds_file.parent / pt.PRIVATE_HOLDS_NAME
    path.write_text(TWO_PRIVATE, encoding="utf-8")
    return path


def _absent(text: str) -> bool:
    return all(v not in text and json.dumps(v)[1:-1] not in text for v in (PLANTED, SECOND))


def test_private_holds_are_numbered_by_entry_and_shown_masked(holds_file, two_private):
    holds = {h.text: h for h in pt.load_holds(holds_file)}
    assert (holds[PLANTED].entry, holds[SECOND].entry) == (1, 2)
    assert (holds[PLANTED].line, holds[SECOND].line) == (2, 5)
    assert pt.shown(PLANTED) == "<private hold #1>" and pt.shown(SECOND) == "<private hold #2>"
    assert pt.shown("Data Associate") == "'Data Associate'"                   # a PROFILE-HOLDS.md hold: its text
    assert holds["Data Associate"].entry == 0
    assert pt.held_sources([SECOND]) == "profile/private-holds.txt"
    assert pt.held_sources(["Data Associate", SECOND]) == "profile/PROFILE-HOLDS.md and profile/private-holds.txt"


def test_check_holds_text_report_masks_private_holds(tmp_path, holds_file, two_private, capsys):
    f = tmp_path / "draft.md"
    f.write_text(f"# Draft\n\nA {SECOND}; also **Data** Associate.\n", encoding="utf-8")
    assert chk.main(["--holds", str(holds_file), str(f)]) == 1
    out, err = capsys.readouterr()
    assert "<private hold #2>" in out and "'Data Associate'" in out
    assert _absent(out + err), "a private hold reached the text report"


def test_check_holds_json_masks_private_holds(tmp_path, holds_file, two_private, capsys):
    f = tmp_path / "draft.md"
    f.write_text(f"# Draft\n\n{PLANTED}\n\nhousing stipend of 1,234\nfictional units\n", encoding="utf-8")
    assert chk.main(["--holds", str(holds_file), "--json", str(f)]) == 1
    out, err = capsys.readouterr()
    assert _absent(out + err), "a private hold reached the JSON report"
    hits = json.loads(out)["files"][0]["hits"]
    assert [(h["held"], h["private_entry"], h["pass"]) for h in hits] == [
        ("<private hold #1>", 1, "exact"), ("<private hold #1>", 1, "plain")]
    assert all(h["ref"] == pt.PRIVATE_HOLDS_NAME for h in hits)


def test_check_holds_json_keeps_a_public_hold_readable(tmp_path, holds_file, two_private, capsys):
    f = tmp_path / "draft.md"
    f.write_text("# Draft\n\nData Associate\n", encoding="utf-8")
    assert chk.main(["--holds", str(holds_file), "--json", str(f)]) == 1
    hit = json.loads(capsys.readouterr().out)["files"][0]["hits"][0]
    assert (hit["held"], hit["private_entry"]) == ("Data Associate", None)


def test_every_load_held_caller_sees_a_private_hold_and_prints_it_masked(tmp_path, holds_file, two_private):
    import apply_pack as ap
    prof = holds_file.parent
    sys.path.insert(0, str(JS / "profile"))
    import check_evidence as ce
    held = pt.load_held(holds_file)
    assert PLANTED in ap.load_held(holds_file) and SECOND in held
    facts = {"employment": [{"id": "exp-ra", "title": "Resident Assistant", "title_render": f"RA, {SECOND}"}]}
    problems = ce.check_facts(facts, held, prof)
    assert any("<private hold #2>" in p for p in problems) and _absent("\n".join(problems))
    title_msgs = pt.title_problems(f"RA, **relocation bonus** of 5,678 fictional units", held)
    assert title_msgs == ["held text <private hold #2> (plain match: emphasis, spacing or punctuation split it)"]
    assert pt.held_notes(f"x {PLANTED} y", held) == ["<private hold #1>"]
    view = pt.render_view({"title": "RA", "title_render": f"RA, {PLANTED}"}, held=held)
    assert "<private hold #1>" in view["title_flag"] and _absent(view["title_flag"])
    with pytest.raises(pt.UnrenderableTitle) as exc:
        pt.render_title({"id": "exp-ra", "title_render": f"RA, {SECOND}"}, held)
    assert "<private hold #2>" in str(exc.value) and _absent(str(exc.value))


def test_render_lint_masks_private_holds(holds_file, two_private):
    held = pt.load_held(holds_file)
    data = _good()
    data["cv"]["sections"]["Experience"][0]["position"] = f"Engineer, {PLANTED}"
    data["cv"]["sections"]["Experience"][0]["highlights"][0] = f"Won a **relocation bonus** of 5,678 fictional units"
    report = rr.lint_resume(data, held=held)
    msgs = [str(e) for e in report.errors if e.code.startswith("held")]
    assert any("held-title" in m and "<private hold #1>" in m for m in msgs) and any("<private hold #2> (plain)" in m for m in msgs)
    assert all("profile/private-holds.txt" in m for m in msgs)
    assert _absent("\n".join(msgs)), "a private hold reached a render lint message"


def test_render_lint_cli_masks_private_holds(tmp_path, holds_file, two_private, capsys, monkeypatch):
    held = pt.load_held(holds_file)
    monkeypatch.setattr(pt, "load_held", lambda *a, **k: held)
    data = _good()
    data["cv"]["sections"]["Experience"][0]["highlights"][0] = f"{SECOND} <!-- ev:nw-rag-01 -->"
    f = tmp_path / "resume.yaml"
    f.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    assert rr.main(["lint", "--input", str(f)]) == 1
    out, err = capsys.readouterr()
    assert "<private hold #2>" in out + err and _absent(out + err)


# --------------------------------------------------------------------------- the same matching everywhere
@pytest.mark.parametrize("title", ["**Data** Associate", "Data  Associate", "*Data Associate*", "Data\nAssociate"])
def test_title_problems_and_render_title_see_through_emphasis(title):
    assert any("Data Associate" in p for p in pt.title_problems(title, held=["Data Associate"]))
    with pytest.raises(pt.UnrenderableTitle):
        pt.render_title({"title": "x", "title_render": title}, held=["Data Associate"])
    assert any("Data Associate" in p for p in pt.raw_title_problems(title, held=["Data Associate"]))


def test_title_problems_keep_the_exact_message_for_plain_titles():
    assert pt.title_problems("Healthcare Data Associate", held=["Data Associate"]) == ["held text 'Data Associate'"]
    assert pt.title_problems("Data Analyst", held=["Data Associate"]) == []


def _good() -> dict:
    return yaml.safe_load((FIX / "good.yaml").read_text(encoding="utf-8"))


def test_render_lint_blocks_a_bold_split_held_string_in_a_bullet():
    data = _good()
    data["cv"]["sections"]["Experience"][0]["highlights"][0] = (
        "Beat the baseline by **+2.1 Macro-F1** over BERT-base <!-- ev:nw-rag-01 -->")
    report = rr.lint_resume(data, held=["+2.1 Macro-F1 over BERT-base"])
    hits = [e for e in report.errors if e.code == "held-text"]
    assert [e.path for e in hits] == ["cv.sections.Experience[0].highlights[0]"]
    assert "+2.1 Macro-F1 over BERT-base" in hits[0].message and "plain" in hits[0].message


@pytest.mark.parametrize("field, value", [
    (("website",), "https://alexmexample.example.com"),
    (("name",), "Alex M Example"),
    (("sections", "Skills", 3, "details"), "FastAPI, vLLM, AWS <!-- ev:nw-serve-01 -->"),
    (("sections", "Projects", 0, "summary"), "A *Data Associate* dashboard <!-- ev:tern-repo -->"),
])
def test_render_lint_checks_every_rendered_field_not_only_titles(field, value):
    data = _good()
    node = data["cv"]
    for key in field[:-1]:
        node = node[key]
    node[field[-1]] = value
    report = rr.lint_resume(data, held=["alexmexample.example.com", "Alex M Example", "vLLM", "Data Associate"])
    assert "held-text" in [e.code for e in report.errors], [str(e) for e in report.errors]


def test_render_lint_titles_still_report_held_title_once():
    data = _good()
    data["cv"]["sections"]["Experience"][0]["position"] = "**Data** Associate"
    report = rr.lint_resume(data, held=["Data Associate"])
    codes = [(e.code, e.path) for e in report.errors if e.code.startswith("held")]
    assert codes == [("held-title", "cv.sections.Experience[0].position")]


def test_render_lint_good_fixture_is_clean_against_the_real_holds():
    report = rr.lint_resume(_good())
    assert [str(e) for e in report.errors if e.code.startswith("held")] == []


def test_apply_pack_lint_and_answer_scrub_see_through_emphasis():
    import apply_pack as ap
    text = "# Pack\n\n## Answers\n\n| Question | Answer |\n|---|---|\n| Current title | **Data** Associate |\n"
    hits, _ = ap.content_findings(text, ["Data Associate"])
    assert hits == [(7, "Data Associate")]
    (row,) = ap.scrub([ap.Row("Roles", "Current title", "*Data* Associate, Acme", "profile/facts.yaml")],
                      ["Data Associate"])
    assert row.answer.startswith("withheld")


def test_a_yaml_comment_in_frontmatter_is_not_a_gap_heading(holds):
    doc = "---\ntitle: x\n# Gaps are listed below\n---\n## Skills\n\nPython, vLLM.\n"
    assert chk.gap_spans(doc) and chk.gap_spans(doc, chk.frontmatter_span(doc)[1]) == []
    assert [(h.held, h.gap) for h in chk.check_text(doc, holds)] == [("vLLM", False)]


# --------------------------------------------------------------------------- digit boundaries 
# A held figure hit inside a URL slug's longer digit run. The figure below is fictional; the private list holds it
# in every form the loader reads (one line each): with and without the thousands comma and the dollar sign, and $K.
FIGURE_FORMS = ["$47,000", "47,000", "$47000", "47000", "$47K", "$47k"]


@pytest.fixture
def figure_holds(holds_file) -> list[pt.Hold]:
    (holds_file.parent / pt.PRIVATE_HOLDS_NAME).write_text("\n".join(FIGURE_FORMS) + "\n", encoding="utf-8")
    return pt.load_holds(holds_file)


@pytest.mark.parametrize("text", [
    "Apply: https://jobs.example.com/ml-engineer-4470000\n",              # the slug case: a longer digit run
    "Apply: https://jobs.example.com/req/470001\n",                       # the figure's digits then more digits
    "Budget of 147,000 units.\n",                                         # preceded by a digit
    "A 47,000,000 dataset.\n",                                            # a separator continues the number
    "A 1,047,000 dataset.\n",                                             # a separator precedes it
    "Token cap 470000 per day.\n",                                        # a longer figure
])
def test_a_held_figure_inside_a_longer_number_is_not_a_hit(text, figure_holds):
    assert chk.check_text(text, figure_holds) == []


@pytest.mark.parametrize("text,held", [
    ("A stipend of 47000 a year.\n", "47000"),
    ("A stipend of 47,000 a year.\n", "47,000"),
    ("A stipend of $47,000.\n", "$47,000"),                               # a sentence-ending period is not a digit
    ("Range 47,000-52,000.\n", "47,000"),                                 # a dash is not a separator
    ("Stipend: **47000**\n", "47000"),                                    # emphasis around it
    ("Stipend ($47K) paid monthly.\n", "$47K"),
    ("stipend=47000&x=1\n", "47000"),                                     # in a URL, bounded by non-digits
])
def test_the_real_figure_standing_alone_still_hits(text, held, figure_holds):
    found = {h.held for h in chk.check_text(text, figure_holds)}
    assert held in found


def test_the_cli_passes_the_slug_and_fails_the_figure(tmp_path, holds_file, figure_holds, capsys):
    slug, alone = tmp_path / "slug.md", tmp_path / "alone.md"
    slug.write_text("# Links\n\n[the posting](https://jobs.example.com/ml-engineer-4470000)\n", encoding="utf-8")
    alone.write_text("# Notes\n\nThe stipend was $47,000.\n", encoding="utf-8")
    assert chk.main(["--holds", str(holds_file), str(slug)]) == 0
    assert chk.main(["--holds", str(holds_file), str(alone)]) == 1
    assert "alone.md:3" in capsys.readouterr().out


def test_digit_boundaries_do_not_loosen_text_holds_ending_in_a_digit():
    # the separator rule is for numeric holds only: "HL7 v2.5" still claims the held "HL7 v2"
    assert [h.pass_ for h in pt.held_hits("Built HL7 v2.5 feeds.", ["HL7 v2"])] == ["exact"]
    assert pt.held_hits("Built HL7 v25 feeds.", ["HL7 v2"]) == []           # a digit continues it: not the hold
    assert [h.pass_ for h in pt.held_hits("scored 0.642.", ["0.642"])] == ["exact"]
    assert pt.held_hits("scored 0.6425.", ["0.642"]) == []


def test_a_dossier_company_fact_section_names_a_held_term_as_a_note(tmp_path):
    import check_holds as ch
    import profile_titles as pt
    holds = [pt.Hold("Docker", "term", "held skill", 1)]
    co = tmp_path / "companies"
    co.mkdir()
    dossier = co / "acme.md"
    dossier.write_text("# Acme\n\n## Stack\nPython, Docker [1]\n\n## Why this company for the owner\nHe ships Docker [2]\n",
                       encoding="utf-8")
    hits = ch.check_file(dossier, holds)
    assert [h.gap for h in hits] == [True, False]       # the company's stack is a note; a claim about the owner fails
    other = tmp_path / "notes.md"
    other.write_text("## Stack\nDocker\n", encoding="utf-8")
    assert [h.gap for h in ch.check_file(other, holds)] == [False]


def test_a_matrix_quotes_the_employer_in_its_jd_fields_only(tmp_path):
    import check_holds as ch
    import profile_titles as pt
    holds = [pt.Hold("LangSmith", "quoted", "C09", 1)]
    m = tmp_path / "x.matrix.yaml"
    m.write_text("rows:\n- id: P9\n  text: Experience with LangSmith or similar\n  terms:\n  - LangSmith\n"
                 "  answer: 'line: we used LangSmith'\n  ceiling: ''\n", encoding="utf-8")
    hits = ch.check_file(m, holds)
    assert [h.gap for h in hits] == [True, True, False]     # JD text and terms are notes; our answer fails


# --------------------------------------------------------------------------- held mode names
MODE_HOLDS = """\
# Holds (fixture)

| Evidence | Held text (exact) | Why held | What unlocks it |
|---|---|---|---|
| ev-001, ev-002 | The four mode names: "Plan, Code, Agent, Debug"; "(Plan, Code, Agent, Debug)"; "Plan, Code, Agent and Debug" | ruling | agreement |
| C15 | "Data Associate" | title | ruling |
"""


@pytest.fixture
def mode_holds(tmp_path) -> list[pt.Hold]:
    path = tmp_path / "PROFILE-HOLDS.md"
    path.write_text(MODE_HOLDS, encoding="utf-8")
    return pt.load_holds(path)


def mode_found(text: str, holds: list[pt.Hold]) -> list[str]:
    return [h.held for h in chk.check_text(text, holds) if h.pass_ == chk.MODE_PASS and not h.gap]


def test_the_mode_names_are_read_from_the_holds_list(mode_holds):
    assert list(chk.mode_names(mode_holds)) == ["Plan", "Code", "Agent", "Debug"]
    assert chk.mode_names([h for h in mode_holds if h.text == "Data Associate"]) == {}


@pytest.mark.parametrize("text, expected", [
    ("only Agent mode can change a design", ["Agent mode"]),                        # the miss that prompted it
    ("Debug mode repaired failed runs", ["Debug mode"]),
    ("the agent ran in plan mode first, then CODE MODE", ["Plan mode", "Code mode"]),   # any case
    ("only **Agent** mode can change a design", ["Agent mode"]),                    # emphasis splits it
    ("only Agent\nmode can change a design", ["Agent mode"]),                      # a line wrap splits it
    ("only [Agent](https://example.com) mode can edit", ["Agent mode"]),            # link text
])
def test_a_single_held_mode_name_is_flagged_as_name_mode(text, expected, mode_holds):
    assert mode_found(text, mode_holds) == expected


@pytest.mark.parametrize("text", [
    "the agent worked against a file-level sandbox",
    "the failure mode was a timeout and the Agent modes list is gone",               # 'modes', not 'mode'
    "a codemode flag and a planmode flag",                                           # fused words
    "the product has four operating modes with tool gating",                       # the allowed wording
])
def test_neutral_sentences_are_not_flagged_for_mode_names(text, mode_holds):
    assert mode_found(text, mode_holds) == []
    assert not [h for h in chk.check_text(text, mode_holds) if not h.gap]


def test_the_list_form_is_still_an_exact_hit_and_the_cli_fails_on_a_single_name(tmp_path, mode_holds, capsys):
    path = tmp_path / "PROFILE-HOLDS.md"
    path.write_text(MODE_HOLDS, encoding="utf-8")
    page = tmp_path / "page.md"
    page.write_text("Built Plan, Code, Agent and Debug.\nOnly Agent mode can change a design.\n", encoding="utf-8")
    assert chk.main([str(page), "--holds", str(path)]) == 1
    out = capsys.readouterr().out
    assert "page.md:1: exact" in out and "page.md:2: mode" in out
    assert "(exact 1, plain 0, loose 0, mode 1)" in out
    clean = tmp_path / "clean.md"
    clean.write_text("the agent worked against a file-level sandbox\n", encoding="utf-8")
    assert chk.main([str(clean), "--holds", str(path)]) == 0
    capsys.readouterr()
    assert chk.main([str(page), "--holds", str(path), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["files"][0]["counts"]["mode"] == 1 and data["ok"] is False


def test_a_mode_name_in_a_matrix_jd_field_is_a_note_and_ours_is_a_hit(tmp_path, mode_holds):
    jd = "- text: Experience with Agent mode workflows\n  answer: ok\n"
    found = chk.check_text(jd, mode_holds, markdown=False, matrix=True)
    assert [(h.held, h.gap) for h in found if h.pass_ == chk.MODE_PASS] == [("Agent mode", True)]
    ours = chk.check_text("- text: Experience\n  answer: Debug mode fixed runs\n", mode_holds, markdown=False, matrix=True)
    assert [(h.held, h.gap) for h in ours if h.pass_ == chk.MODE_PASS] == [("Debug mode", False)]


def test_the_real_holds_flag_each_real_mode_name():
    real = pt.load_holds(REAL_HOLDS)
    names = list(chk.mode_names(real))
    for name in names:                      # whatever mode names the real holds list, each is flagged as "<Name> mode"
        assert mode_found(f"only {name} mode can change a design", real) == [f"{name} mode"]
    assert mode_found("the agent worked against a file-level sandbox", real) == []
