"""scripts/check_dossier.py: the company-researcher's self-audit of companies/<slug>.md, as a script.

A dossier can cite facts to sources that lack them while its reply says every citation matched. The checks: every [n] resolves to a numbered ## Sources entry; every entry has a URL or a
local path and an access date; every factual body line ends with a citation (angles, questions, headings,
blank lines, pasted code and "(to confirm)" lines exempt); a double-quoted phrase on a line that cites a
local file appears in that file. A clean fixture passes; each planted failure is reported with its line.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import check_dossier as cd  # noqa: E402

from layout import JS  # noqa: E402
CAREERS = """Source: https://acme.example/careers · captured 2026-09-27 (cached page)

# Careers at Acme AI

Acme AI builds agent infrastructure for operations teams: agents that read tickets, call
internal tools and hand work back to people. About 85 people. Headquartered in San Francisco.
"""

CLEAN = """---
slug: acme-ai
name: Acme AI
last_researched: 2026-09-28
---

# Acme AI

## Snapshot
- Acme AI builds agent infrastructure for operations teams. [1]
- About 85 people, headquartered in San Francisco. [1]

## Product
- Agents read tickets, "call
  internal tools" and "hand work back to people". [1]

## H-1B and LCA
`python3 scripts/h1b_lookup.py lookup "Acme AI, Inc." --today 2026-09-28` output (complete) [3]:
```json
{"h1b_approvals_3y": 12, "employer_matches": ["ACME AI INC"]}
```
- 12 H-1B approvals FY2024 to FY2026 for ACME AI INC. [3]

| Title | LCAs |
|---|---|
| Machine Learning Engineer | 3 [3] |

### Wages
- Median LCA wage $187,000, level III. [3][1]

## E-Verify
- Not searched yet (to confirm).

## Why this company for Alex
- They write about sandboxed execution [2]; Alex built snapshot rollback (ev-acme-004).

## Questions to ask them
- How do you gate releases?

## Sources
1. https://acme.example/careers (cached `research/cache/acme-ai/careers.md`), accessed 2026-09-28
2. https://acme.example/blog/sandboxing, accessed 2026-09-28
3. `legal/evidence/h1b/uscis_h1b_fy2026.csv` read by `scripts/h1b_lookup.py`,
   run 2026-09-28

## Superseded
- 2026-07-01: 48 approvals from a name search (replaced 2026-09-28).
"""


def build(tmp_path: Path, text: str = CLEAN) -> Path:
    js = tmp_path / "js"
    (js / "research" / "cache" / "acme-ai").mkdir(parents=True)
    (js / "research" / "cache" / "acme-ai" / "careers.md").write_text(CAREERS, encoding="utf-8")
    (js / "legal" / "evidence" / "h1b").mkdir(parents=True)
    (js / "legal" / "evidence" / "h1b" / "uscis_h1b_fy2026.csv").write_text(
        "fiscal_year,employer,approvals\n2026,ACME AI INC,4\n", encoding="utf-8")
    (js / "scripts").mkdir()
    (js / "scripts" / "h1b_lookup.py").write_text("# stand-in\n", encoding="utf-8")
    (js / "companies").mkdir()
    path = js / "companies" / "acme-ai.md"
    path.write_text(text, encoding="utf-8")
    return path


def kinds(problems) -> list[tuple[str, int]]:
    return [(p.kind, p.line) for p in problems]


def line_of(text: str, needle: str) -> int:
    return next(n for n, line in enumerate(text.splitlines(), 1) if needle in line)


# --------------------------------------------------------------------------- clean
def test_the_clean_fixture_passes(tmp_path, capsys):
    path = build(tmp_path)
    assert cd.check_file(path) == []
    assert cd.main([str(path)]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "check_dossier: 0 problem(s)"


# --------------------------------------------------------------------------- (1) citations resolve
def test_a_dangling_reference_is_reported(tmp_path):
    text = CLEAN.replace("level III. [3][1]", "level III. [3][4]")
    problems = cd.check_file(build(tmp_path, text))
    assert kinds(problems) == [("citation", line_of(text, "level III"))]
    assert "[4]" in problems[0].message


def test_citations_without_a_sources_section_all_dangle(tmp_path):
    text = CLEAN[:CLEAN.index("## Sources")]
    problems = cd.check_file(build(tmp_path, text))
    assert {p.kind for p in problems} == {"citation"}
    assert any("## Sources" in p.message for p in problems)


# --------------------------------------------------------------------------- (2) sources carry a locator and a date
def test_an_undated_source_is_reported(tmp_path):
    text = CLEAN.replace("2. https://acme.example/blog/sandboxing, accessed 2026-09-28",
                         "2. https://acme.example/blog/sandboxing, accessed recently")
    problems = cd.check_file(build(tmp_path, text))
    assert kinds(problems) == [("source", line_of(text, "2. https://acme.example/blog"))]
    assert "date" in problems[0].message


def test_a_source_with_neither_url_nor_path_is_reported(tmp_path):
    text = CLEAN.replace("2. https://acme.example/blog/sandboxing, accessed 2026-09-28",
                         "2. USCIS H-1B Employer Data Hub cache, name search, accessed 2026-09-28")
    problems = cd.check_file(build(tmp_path, text))
    assert kinds(problems) == [("source", line_of(text, "2. USCIS"))]
    assert "URL or a local path" in problems[0].message


def test_a_local_path_that_does_not_exist_is_reported(tmp_path):
    text = CLEAN.replace("research/cache/acme-ai/careers.md", "research/cache/acme-ai/jobs.md")
    problems = cd.check_file(build(tmp_path, text))
    assert ("source", line_of(text, "1. https://acme.example/careers")) in kinds(problems)
    assert any("research/cache/acme-ai/jobs.md" in p.message for p in problems)


# --------------------------------------------------------------------------- (3) factual lines end with a citation
def test_an_uncited_factual_line_is_listed_with_its_line_number(tmp_path):
    text = CLEAN.replace("## Product\n", "## Product\n- They raised a Series B in May.\n")
    problems = cd.check_file(build(tmp_path, text))
    assert kinds(problems) == [("uncited", line_of(text, "Series B"))]
    assert "## Product" in problems[0].message and "Series B" in problems[0].message


def test_a_wrapped_item_needs_the_citation_on_its_last_line(tmp_path):
    text = CLEAN.replace('"hand work back to people". [1]', '"hand work back to people".')
    text = text.replace('- Agents read tickets, "call', '- Agents read tickets [1], "call')
    problems = cd.check_file(build(tmp_path, text))
    assert kinds(problems) == [("uncited", line_of(text, "Agents read tickets"))]


def test_an_uncited_table_row_is_reported_and_the_header_is_not(tmp_path):
    text = CLEAN.replace("| Machine Learning Engineer | 3 [3] |", "| Machine Learning Engineer | 2 |")
    assert kinds(cd.check_file(build(tmp_path, text))) == [("uncited", line_of(text, "| Machine Learning"))]


@pytest.mark.parametrize("line", [
    "- Four interview rounds (to confirm).",
    "<!-- refreshed sections: H-1B -->",
    "### Subheading without a citation",
])
def test_exempt_lines(tmp_path, line):
    text = CLEAN.replace("## Product\n", f"## Product\n{line}\n")
    assert cd.check_file(build(tmp_path, text)) == []


def test_angles_questions_and_superseded_need_no_citation_but_other_sections_do(tmp_path):
    text = CLEAN.replace("## Questions to ask them\n", "## Questions to ask them\n- What is on the roadmap?\n")
    assert cd.check_file(build(tmp_path, text)) == []
    text = CLEAN.replace("## E-Verify\n", "## Board fields\n- ats_vendor greenhouse, token acmeai.\n\n## E-Verify\n")
    assert kinds(cd.check_file(build(tmp_path / "b", text))) == [("uncited", line_of(text, "ats_vendor greenhouse"))]


def test_a_citation_mid_line_is_not_an_ending_citation(tmp_path):
    text = CLEAN.replace("- 12 H-1B approvals FY2024 to FY2026 for ACME AI INC. [3]",
                         "- 12 H-1B approvals [3] FY2024 to FY2026, all engineering titles.")
    assert kinds(cd.check_file(build(tmp_path, text))) == [("uncited", line_of(text, "all engineering titles"))]


# --------------------------------------------------------------------------- (4) quoted phrases are in the cited file
def test_a_misquoted_phrase_is_reported(tmp_path):
    text = CLEAN.replace('"hand work back to people"', '"hand work back to humans"')
    problems = cd.check_file(build(tmp_path, text))
    assert kinds(problems) == [("quote", line_of(text, "Agents read tickets"))]
    assert "hand work back to humans" in problems[0].message and "careers.md" in problems[0].message


def test_quotes_are_case_sensitive_and_whitespace_normalised(tmp_path):
    ok = CLEAN.replace('"call\n  internal tools"', '"call  internal\n  tools"')
    assert cd.check_file(build(tmp_path, ok)) == []
    bad = CLEAN.replace('"call\n  internal tools"', '"Call internal tools"')
    assert [p.kind for p in cd.check_file(build(tmp_path / "b", bad))] == ["quote"]


def test_curly_quotes_are_checked_and_code_spans_and_urls_only_sources_are_not(tmp_path):
    text = CLEAN.replace('"hand work back to people"', "“hand work back to robots”")
    assert [p.kind for p in cd.check_file(build(tmp_path, text))] == ["quote"]
    url_only = CLEAN.replace("## Product\n", '## Product\n- The blog says "we sandbox every agent". [2]\n')
    assert cd.check_file(build(tmp_path / "u", url_only)) == []          # no local file to hold it against


def test_a_script_is_not_a_quotable_source(tmp_path):
    text = CLEAN.replace("- 12 H-1B approvals FY2024 to FY2026 for ACME AI INC. [3]",
                         '- 12 H-1B approvals FY2024 to FY2026 for "ACME AI INC". [3]')
    assert cd.check_file(build(tmp_path, text)) == []                     # found in the CSV, not the .py
    text = text.replace('"ACME AI INC"', '"stand-in"')
    assert [p.kind for p in cd.check_file(build(tmp_path / "b", text))] == ["quote"]


# --------------------------------------------------------------------------- CLI
def test_cli_lists_every_problem_and_exits_1(tmp_path, capsys):
    text = CLEAN.replace("level III. [3][1]", "level III. [3][9]")
    text = text.replace("## Product\n", "## Product\n- Uncited claim here.\n")
    path = build(tmp_path, text)
    assert cd.main([str(path)]) == 1
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1] == "check_dossier: 2 problem(s)"
    assert any(f":{line_of(text, 'Uncited claim')}: uncited:" in ln for ln in out)
    assert any(f":{line_of(text, 'level III')}: citation:" in ln for ln in out)


def test_cli_refuses_a_missing_file(tmp_path, capsys):
    assert cd.main([str(tmp_path / "nope.md")]) == 2
    assert "no such file" in capsys.readouterr().err


def test_root_option_resolves_local_paths(tmp_path):
    path = build(tmp_path)
    moved = tmp_path / "elsewhere.md"
    moved.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    assert any(p.kind == "source" for p in cd.check_file(moved))            # the root is guessed wrong
    assert cd.check_file(moved, root=path.parent.parent) == []


# --------------------------------------------------------------------------- (3b) every sentence carries its citation
def test_an_uncited_sentence_inside_a_cited_paragraph_is_reported(tmp_path):
    """A cited paragraph must not hide an uncited claim."""
    para = ("Acme AI builds agent infrastructure for operations teams [1]. Acme has 400 enterprise customers.\n"
            "About 85 people work there [1].\n")
    text = CLEAN.replace("## Product\n", f"## Product\n{para}\n")
    problems = cd.check_file(build(tmp_path, text))
    assert kinds(problems) == [("uncited", line_of(text, "400 enterprise customers"))]
    assert "400 enterprise customers" in problems[0].message and "sentence" in problems[0].message


def test_a_paragraph_whose_every_sentence_is_cited_passes(tmp_path):
    para = ("Acme AI builds agent infrastructure [1]. It has about 85 people [1]. The median LCA wage is "
            "$187,000 [3].\n")
    assert cd.check_file(build(tmp_path, CLEAN.replace("## Product\n", f"## Product\n{para}\n"))) == []


@pytest.mark.parametrize("sentence", [
    "Acme AI, Inc. builds agent infrastructure for operations teams [1].",
    "J. Smith leads the agents team in San Francisco [1].",
    "It is a U.S. company with about 85 people [1].",
    "Headcount grew 3.5 times in 2026 [1].",
    "Founded by a Ph.D. from Stanford, e.g. in agents [1].",
    "Series A closed in Sep. 2026 [1].",
    "Median LCA wage $187,000, level III. [3][1]",
])
def test_abbreviations_initials_numbers_and_trailing_citations_do_not_split_a_sentence(tmp_path, sentence):
    text = CLEAN.replace("## Product\n", f"## Product\n- {sentence}\n")
    assert cd.check_file(build(tmp_path, text)) == []


def test_a_list_item_with_an_uncited_first_sentence_is_reported(tmp_path):
    text = CLEAN.replace("## Product\n", "## Product\n- They raised a Series B in May. The team is 85 people [1].\n")
    assert kinds(cd.check_file(build(tmp_path, text))) == [("uncited", line_of(text, "Series B in May"))]


@pytest.mark.parametrize("item", [
    '- Their careers page says: "We hire slowly. We pay well. We ship weekly." [2]',
    '- The JD: “Right now that someone is a founder. That is the bottleneck.” [2]',
    '- "1. Quick questionnaire" [2]',
    '2. Ten minutes with the CEO [2]',
    '- 3. A take-home case study [2]',
    '- "3. A deployment case. A real customer situation. You build their workflow." [2]',
])
def test_a_quotation_or_an_enumerator_is_one_claim_with_one_citation(tmp_path, item):
    text = CLEAN.replace("## Product\n", f"## Product\n{item}\n")
    assert cd.check_file(build(tmp_path, text)) == []


@pytest.mark.parametrize("item", [
    '- Education: "BS in Computer Science or a related field. MS is a plus, not a substitute for shipping." [2]',
    '- Must-have: Python, "deep. You write production systems, not just scripts." [2]',
    '- Must-have: "Demonstrable experience building AI agents" and "show us what you built. Not what you read." [2]',
    '- Sponsorship: "Visa sponsorship is available for this position." (clear at role level) [2].',
])
def test_a_quoted_jd_line_with_one_citation_is_one_claim(tmp_path, item):
    """The shape of TARGET 1's packages (verbatim JD quotes): the quotation is one claim with one citation."""
    assert cd.check_file(build(tmp_path, CLEAN.replace("## Product\n", f"## Product\n{item}\n"))) == []


def test_an_uncited_sentence_beside_a_cited_quote_is_still_caught(tmp_path):
    item = '- The team is 40 engineers. Must-have: Python, "deep. You write production systems." [2]'
    text = CLEAN.replace("## Product\n", f"## Product\n{item}\n")
    problems = cd.check_file(build(tmp_path, text))
    assert kinds(problems) == [("uncited", line_of(text, "40 engineers"))] and "sentence" in problems[0].message


# an uncited sentence before a sentence with an inline quote
@pytest.mark.parametrize("item,needle", [
    ('Acme has 400 paying customers and raised a Series B. Acme builds "agent infrastructure" for operations '
     'teams [2].', "400 paying customers"),
    ('The tagline is "Agents that call people." Acme has 400 paying customers. It launched in May [2].',
     "400 paying customers"),
    ('Acme closed its Series A. The round was led by a large fund [2].', "Series A"),
    ('The tagline is "Agents that call people." Acme has 400 paying customers [2].', "tagline"),
])
def test_an_uncited_sentence_is_caught_beside_an_inline_quote_or_a_round_letter(tmp_path, item, needle):
    text = CLEAN.replace("## Product\n", f"## Product\n{item}\n")
    problems = cd.check_file(build(tmp_path, text))
    assert kinds(problems) == [("uncited", line_of(text, needle))] and "sentence" in problems[0].message, problems


def test_both_to_confirm_forms_exempt_a_line():
    import check_dossier as cd
    assert cd.TO_CONFIRM_RE.search("Cash balance: Unknown - to confirm")
    assert cd.TO_CONFIRM_RE.search("LCA wages (to confirm)")
    assert not cd.TO_CONFIRM_RE.search("We have yet to confirm nothing")
