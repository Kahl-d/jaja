"""Tests for scripts/render_resume.py: the ATS template, the evidence convention, the pre-render
lint, and real renders (RenderCV/Typst and the LaTeX fallback) with page budget, meta.yaml and
the Workday DOCX. Fixtures live in tests/fixtures/resume/ and describe a FICTIONAL person."""
from __future__ import annotations

import copy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import render_resume as rr  # noqa: E402

from layout import JS  # noqa: E402
FIX = Path(__file__).resolve().parent / "fixtures" / "resume"
GOOD = FIX / "good.yaml"
BROKEN = FIX / "broken.yaml"
EVIDENCE = FIX / "evidence.yaml"
FACTS = FIX / "facts.yaml"
NO_RULINGS = FIX / "no-rulings.yaml"       # absent on purpose: tests never read the real rulings
TEMPLATE = rr.RENDERCV_TEMPLATE
LATEX_DIR = rr.LATEX_TEMPLATE_DIR

needs_rendercv = pytest.mark.skipif(not rr.rendercv_available(), reason="RenderCV not installed")
needs_latex = pytest.mark.skipif(rr.find_pdflatex() is None, reason="pdflatex not installed")
needs_pandoc = pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")


def main(argv: list[str]) -> int:
    """rr.main with the fixture facts and no rulings file, so a test never reads the real data folder."""
    return rr.main([*argv, "--facts", str(FACTS), "--rulings", str(NO_RULINGS)])


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def known_ids() -> set[str]:
    return rr.load_evidence_ids(EVIDENCE)


def codes(report) -> list[str]:
    return [i.code for i in report.errors]


def pdf_text(pdf: Path) -> str:
    return subprocess.run(["pdftotext", "-enc", "UTF-8", str(pdf), "-"],
                          capture_output=True, text=True, check=True).stdout


def page_count(pdf: Path) -> int:
    import pypdf
    return len(pypdf.PdfReader(str(pdf)).pages)


# --------------------------------------------------------------------------- the template
def test_template_validates_against_rendercv_models():
    from rendercv.schema.models.design.built_in_design import built_in_design_adapter
    from rendercv.schema.models.locale.locale import locale_adapter
    from rendercv.schema.models.settings.settings import Settings

    t = load(TEMPLATE)
    design = built_in_design_adapter.validate_python(t["design"])   # extra keys are forbidden
    locale = locale_adapter.validate_python(t["locale"])
    Settings.model_validate(t["settings"])
    assert design.theme == "engineeringresumes"
    assert locale.month_abbreviations == ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    assert locale.present == "Present"


def test_template_is_single_column_ats_safe():
    d = load(TEMPLATE)["design"]
    assert d["page"]["size"] == "us-letter"
    assert d["typography"]["alignment"] == "left"            # no justification, no hyphenation
    body = float(str(d["typography"]["font_size"]["body"]).removesuffix("pt"))
    assert 10 <= body <= 11
    conn = d["header"]["connections"]
    assert conn["show_icons"] is False and conn["display_urls_instead_of_usernames"] is True
    assert d["links"]["show_external_link_icon"] is False
    assert d["entries"]["date_and_location_width"] == "0cm"  # no second column
    for name, tmpl in d["templates"].items():
        if isinstance(tmpl, dict):
            assert tmpl.get("date_and_location_column", "") == "", name
            assert tmpl.get("degree_column") is None, name
    assert d["templates"]["single_date"] == "MONTH_ABBREVIATION YEAR"
    assert d["templates"]["date_range"] == "START_DATE - END_DATE"   # Aug 2023 - May 2026
    assert "#box[DATE]" in d["templates"]["experience_entry"]["main_column"]


def test_template_text_has_no_em_dashes_or_typst_dash_sequences():
    t = load(TEMPLATE)

    def strings(node):
        if isinstance(node, dict):
            for v in node.values():
                yield from strings(v)
        elif isinstance(node, list):
            for v in node:
                yield from strings(v)
        elif isinstance(node, str):
            yield node

    for s in strings(t):
        assert "\u2014" not in s and "---" not in s and "--" not in s, s
    raw = TEMPLATE.read_text(encoding="utf-8")
    assert "\u2014" not in raw


# --------------------------------------------------------------------------- evidence convention
def test_parse_evidence_trailing_comment():
    assert rr.parse_evidence("Built X for Y <!-- ev:nw-rag-01 -->") == ("Built X for Y", ["nw-rag-01"])
    assert rr.parse_evidence("Did it <!--ev: a.1, b:2  c/3-->") == ("Did it", ["a.1", "b:2", "c/3"])
    assert rr.parse_evidence("No evidence here") == ("No evidence here", [])


def test_load_evidence_ids_accepts_mapping_and_list_shapes(tmp_path):
    ids = known_ids()
    assert {"nw-rag-01", "sk-ray", "pub-triage"} <= ids
    listed = tmp_path / "ev.yaml"
    listed.write_text("- {id: a-1, source: x}\n- {id: b-2, source: y}\n", encoding="utf-8")
    assert rr.load_evidence_ids(listed) == {"a-1", "b-2"}


# --------------------------------------------------------------------------- lint
def test_lint_good_fixture_has_no_errors():
    report = rr.lint_resume(load(GOOD), known_evidence=known_ids())
    assert report.errors == [], [str(e) for e in report.errors]
    assert "nw-rag-01" in report.evidence_ids and "hsu-degree" in report.evidence_ids


@pytest.mark.parametrize("position, code", [
    ("Principal Wizard", "held-title"),                                  # a held variant (passed to the lint below)
    ("Data Analyst (LinkedIn and PhD CVs say 'Data Associate' - to confirm)", "title-conflict-marker"),
    ("Lab Technician - proposed", "title-conflict-marker"),
])
def test_lint_blocks_held_titles_and_conflict_notes_in_positions(position, code):
    """A position copied from a facts.yaml `title` record instead of its `title_render` never renders."""
    data = load(GOOD)
    data["cv"]["sections"]["Experience"][0]["position"] = position
    report = rr.lint_resume(data, known_evidence=known_ids(), held=["Principal Wizard"])
    hits = [e for e in report.errors if e.code == code]
    assert hits and hits[0].path == "cv.sections.Experience[0].position"


def test_lint_blocks_a_held_publication_title():
    title = "Cache-Aware Retrieval for Support Agents: A Study of Staleness"      # fictional preprint, held
    data = load(GOOD)
    data["cv"]["sections"]["Publications"][0]["title"] = title
    report = rr.lint_resume(data, known_evidence=known_ids(), held=[title])
    assert [e.path for e in report.errors if e.code == "held-title"] == ["cv.sections.Publications[0].title"]


def test_lint_catches_missing_evidence_id():
    data = load(GOOD)
    hl = data["cv"]["sections"]["Experience"][0]["highlights"]
    hl[1] = hl[1].split("<!--")[0].strip()
    report = rr.lint_resume(data, known_evidence=known_ids())
    missing = [e for e in report.errors if e.code == "missing-evidence"]
    assert len(missing) == 1
    assert missing[0].path == "cv.sections.Experience[0].highlights[1]"


def test_lint_catches_unknown_and_misplaced_evidence():
    data = load(GOOD)
    hl = data["cv"]["sections"]["Experience"][0]["highlights"]
    hl[0] = hl[0].replace("ev:nw-rag-01", "ev:nope-999")
    hl[2] = "Moved batch inference <!-- ev:nw-serve-01 --> to a FastAPI service"
    report = rr.lint_resume(data, known_evidence=known_ids())
    assert "unknown-evidence" in codes(report)
    assert "evidence-not-trailing" in codes(report)


def test_lint_flags_yaml_colon_space_that_turns_a_bullet_into_a_mapping():
    data = yaml.safe_load(
        "cv:\n  name: A B\n  email: a@example.com\n  sections:\n    Experience:\n"
        "      - company: C\n        position: P\n        location: X\n"
        "        start_date: 2020-01\n        end_date: present\n        highlights:\n"
        "          - Built X <!-- ev: a-1 -->\n")
    report = rr.lint_resume(data)
    err = [e for e in report.errors if e.code == "not-a-string"]
    assert err and "quote" in err[0].message.lower()


def test_lint_reports_each_defect_in_the_broken_fixture():
    report = rr.lint_resume(load(BROKEN), known_evidence=known_ids())
    got = set(codes(report))
    assert {"em-dash", "table-content", "missing-email"} <= got
    text = "\n".join(str(e) for e in report.errors)
    assert "em-dash" in text and "email" in text


def test_lint_catches_formatting_traps():
    data = load(GOOD)
    hl = data["cv"]["sections"]["Experience"][0]["highlights"]
    hl.append("Cut costs - by caching embeddings <!-- ev:nw-rag-01 -->")          # nested bullet
    hl.append("Two  spaces in a bullet <!-- ev:nw-rag-01 -->")                   # double space
    hl.append("See [the site](https://example.com/x) for details <!-- ev:nw-rag-01 -->")
    hl.append("Layout trick #grid(columns: 2)[a] here <!-- ev:nw-rag-01 -->")     # raw Typst
    hl.append("Dash sequence --- here <!-- ev:nw-rag-01 -->")
    data["cv"]["sections"]["Projects"][0]["name"] = "[Tern](https://example.com/tern)"
    report = rr.lint_resume(data, known_evidence=known_ids())
    got = set(codes(report))
    assert {"nested-bullet", "double-space", "hidden-link", "raw-typst", "em-dash"} <= got
    hidden = [e.path for e in report.errors if e.code == "hidden-link"]
    assert "cv.sections.Projects[0].name" in hidden and len(hidden) == 2


def test_lint_catches_bullet_longer_than_two_lines():
    data = load(GOOD)
    long = " ".join(["Built a careful pipeline for evaluating agents"] * 8)
    data["cv"]["sections"]["Experience"][0]["highlights"][0] = long + " <!-- ev:nw-rag-01 -->"
    report = rr.lint_resume(data, known_evidence=known_ids())
    err = [e for e in report.errors if e.code == "bullet-too-long"]
    assert err and err[0].path == "cv.sections.Experience[0].highlights[0]"


def test_lint_catches_noncanonical_headings_design_overrides_and_photos():
    data = load(GOOD)
    secs = data["cv"]["sections"]
    secs["Work History"] = secs.pop("Experience")
    data["design"] = {"theme": "classic"}
    data["cv"]["photo"] = "me.jpg"
    report = rr.lint_resume(data, known_evidence=known_ids())
    got = set(codes(report))
    assert {"noncanonical-heading", "design-override", "photo"} <= got
    msg = next(str(e) for e in report.errors if e.code == "noncanonical-heading")
    assert "Experience" in msg


def test_lint_warns_when_sections_leave_the_house_order():
    data = load(GOOD)
    secs = data["cv"]["sections"]
    data["cv"]["sections"] = {"Experience": secs["Experience"], **{k: v for k, v in secs.items()
                                                                  if k != "Experience"}}
    report = rr.lint_resume(data, known_evidence=known_ids())
    assert report.errors == []
    assert "section-order" in [w.code for w in report.warnings]
    assert "section-order" not in [w.code for w in rr.lint_resume(load(GOOD)).warnings]


def test_lint_requires_location_and_month_precision_dates():
    data = load(GOOD)
    job = data["cv"]["sections"]["Experience"][0]
    del job["location"]
    job["start_date"] = 2023
    got = set(codes(rr.lint_resume(data, known_evidence=known_ids())))
    assert {"missing-location", "date-precision"} <= got


# --------------------------------------------------------------------------- preparation
def test_strip_for_render_removes_sidecar_comments_and_entry_evidence():
    clean = rr.strip_for_render(load(GOOD))
    assert "sidecar" not in clean
    job = clean["cv"]["sections"]["Experience"][0]
    assert "evidence" not in job
    assert all("<!--" not in h and "ev:" not in h for h in job["highlights"])
    assert job["highlights"][0].endswith("from 9 to 4 minutes")


def test_box_hyphenated_wraps_compounds_but_not_urls_or_emails():
    out = rr.box_hyphenated("A retrieval-augmented bot, see https://example.com/a-b or "
                            "a-b@example.com and example.com/x-y")
    assert out.startswith("A #box[retrieval-augmented] bot")
    assert "https://example.com/a-b" in out and "a-b@example.com" in out
    assert "example.com/x-y" in out and "#box[x-y]" not in out


def test_markdown_for_docx_puts_company_line_above_title():
    md = rr.resume_markdown(rr.strip_for_render(load(GOOD)))
    assert "riley.moreno@example.com" in md and "<!--" not in md
    assert md.index("Northwind Robotics") < md.index("Machine Learning Engineer")
    assert "## Experience" in md and "Mar 2023 - Present" in md
    assert "\u2014" not in md


# --------------------------------------------------------------------------- real renders
@pytest.fixture(scope="module")
def good_render(tmp_path_factory):
    if not rr.rendercv_available():
        pytest.skip("RenderCV not installed")
    out = tmp_path_factory.mktemp("good")
    rc = main(["render", "--input", str(GOOD), "--out", str(out), "--evidence", str(EVIDENCE)])
    return rc, out


@needs_rendercv
def test_render_good_fixture_is_one_page_with_meta(good_render):
    rc, out = good_render
    assert rc == 0
    meta = load(out / "meta.yaml")
    pdf = out / meta["pdf"]
    assert pdf.is_file() and page_count(pdf) == 1
    assert meta["lens"] == "applied-ml" and meta["base_of"] is None and meta["job"] is None
    assert meta["pages"] == 1 and meta["status"] == "ok" and meta["engine"] == "rendercv"
    assert str(meta["rendercv_version"]) == "2.8"
    assert meta["built_at"] and meta["hash"].startswith("sha256:")
    assert meta["sections"] == ["Education", "Skills", "Experience", "Projects", "Publications",
                                "Awards"]
    assert "nw-rag-01" in meta["evidence_ids"]
    text = pdf_text(pdf)
    assert "Mar 2023 - Present" in text and "riley.moreno@example.com" in text
    assert "<!--" not in text and "ev:" not in text and "#box" not in text
    assert "retrieval-augmented" in text                     # box keeps the compound intact


@needs_rendercv
def test_render_hash_is_deterministic(good_render, tmp_path):
    _, out = good_render
    assert main(["render", "--input", str(GOOD), "--out", str(tmp_path),
                    "--evidence", str(EVIDENCE)]) == 0
    assert load(tmp_path / "meta.yaml")["hash"] == load(out / "meta.yaml")["hash"]


@needs_rendercv
@needs_pandoc
def test_render_docx_for_workday(tmp_path):
    import docx

    assert main(["render", "--input", str(GOOD), "--out", str(tmp_path), "--docx",
                    "--evidence", str(EVIDENCE)]) == 0
    meta = load(tmp_path / "meta.yaml")
    path = tmp_path / meta["docx"]
    paras = [p.text for p in docx.Document(str(path)).paragraphs]
    text = "\n".join(paras)
    assert "riley.moreno@example.com" in text and "Experience" in paras
    company = next(i for i, p in enumerate(paras) if p.startswith("Northwind Robotics"))
    title = next(i for i, p in enumerate(paras) if p.startswith("Machine Learning Engineer"))
    assert company < title
    assert "\u2014" not in text and "<!--" not in text


@needs_rendercv
def test_render_broken_fixture_fails_lint_with_clear_messages(tmp_path, capsys):
    rc = main(["render", "--input", str(BROKEN), "--out", str(tmp_path),
                  "--evidence", str(EVIDENCE)])
    out = capsys.readouterr()
    msg = out.out + out.err
    assert rc == 1
    assert "em-dash" in msg and "table-content" in msg and "missing-email" in msg
    assert not list(tmp_path.glob("*.pdf"))


@needs_rendercv
def test_render_broken_forced_fails_page_budget_listing_longest_sections(tmp_path, capsys):
    rc = main(["render", "--input", str(BROKEN), "--out", str(tmp_path), "--force",
                  "--evidence", str(EVIDENCE)])
    msg = capsys.readouterr()
    msg = msg.out + msg.err
    assert rc == 1
    assert "page budget" in msg
    longest = msg.split("Longest sections:", 1)[1].strip()
    assert longest.startswith("Experience")
    assert not (tmp_path / "meta.yaml").exists()              # F80: a failed build never lands in V
    draft = Path(msg.split("failed draft kept at ", 1)[1].split()[0])
    meta = load(draft / "meta.yaml")
    assert meta["status"] == "failed" and meta["pages"] >= 2
    assert (draft / meta["pdf"]).is_file()                    # kept for inspection, outside V


def test_cli_lint_subcommand(capsys):
    assert main(["lint", "--input", str(GOOD), "--evidence", str(EVIDENCE)]) == 0
    assert main(["lint", "--input", str(BROKEN), "--evidence", str(EVIDENCE)]) == 1


# --------------------------------------------------------------------------- LaTeX fallback
def test_latex_template_files_keep_the_hardening_flags():
    pre = (LATEX_DIR / "ats-preamble.tex").read_text(encoding="utf-8")
    for flag in ("\\input{glyphtounicode}", "\\pdfgentounicode=1", "\\hyphenpenalty=10000",
                 "\\exhyphenpenalty=10000", "\\RaggedRight"):
        assert flag in pre, flag
    doc = (LATEX_DIR / "resume.tex.j2").read_text(encoding="utf-8")
    assert doc.index("\\begin{document}") < doc.index("\\pdfinterwordspaceon")
    fp = (LATEX_DIR / "faangpath" / "resume_faangpath.tex").read_text(encoding="utf-8")
    assert "\\pdfinterwordspaceon" in fp and "\\pdfgentounicode=1" in fp
    assert "tabular" not in fp.split("\\begin{document}", 1)[1]   # no table in the body
    assert (LATEX_DIR / "faangpath" / "resume.cls").is_file()


@needs_latex
def test_render_latex_engine_one_page_and_hardened(tmp_path):
    rc = main(["render", "--input", str(GOOD), "--out", str(tmp_path), "--engine", "latex",
                  "--keep-build", "--evidence", str(EVIDENCE)])
    assert rc == 0
    meta = load(tmp_path / "meta.yaml")
    assert meta["engine"] == "latex" and meta["pages"] == 1
    tex = next((tmp_path / "build").glob("*.tex")).read_text(encoding="utf-8")
    assert tex.index("\\begin{document}") < tex.index("\\pdfinterwordspaceon")
    text = pdf_text(tmp_path / meta["pdf"])
    assert "riley.moreno@example.com" in text and "Experience" in text
    assert "Mar 2023 - Present" in text
    assert "\u2014" not in text


def test_latex_escape_and_markdown():
    assert rr.latex_inline("R&D at 100% for $5 **fast** *now*") == \
        "R\\&D at 100\\% for \\$5 \\textbf{fast} \\emph{now}"
    out = rr.latex_inline("see https://example.com/a_b now")
    assert "\\href{https://example.com/a_b}{https://example.com/a\\_b}" in out


def test_copy_of_good_fixture_is_not_mutated_by_lint():
    data = load(GOOD)
    before = copy.deepcopy(data)
    rr.lint_resume(data, known_evidence=known_ids())
    rr.strip_for_render(data)
    assert data == before


# --------------------------------------------------------------------------- --date (2026-09-25)
@pytest.fixture
def clock_2030(monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(rr, "wall_clock", lambda: datetime(2030, 1, 1, 9, 0, 0), raising=False)


def test_stamp_uses_the_run_date_unless_it_is_today(clock_2030):
    from datetime import date
    assert rr.stamp(date(2026, 9, 28)) == "2026-09-28T12:00:00"
    assert rr.stamp(None) == "2030-01-01T09:00:00" == rr.stamp(date(2030, 1, 1))


@needs_rendercv
def test_render_date_sets_built_at(tmp_path, clock_2030):
    assert main(["render", "--input", str(GOOD), "--out", str(tmp_path), "--evidence", str(EVIDENCE),
                    "--date", "2026-09-28"]) == 0
    assert str(load(tmp_path / "meta.yaml")["built_at"]) == "2026-09-28T12:00:00"


def test_render_and_lint_reject_a_malformed_date(tmp_path):
    with pytest.raises(SystemExit):
        main(["render", "--input", str(GOOD), "--out", str(tmp_path), "--date", "28-09-2026"])


# --------------------------------------------------------------------------- year-only dates (2026-09-25)
def _year_only(start=2016, end=2018):
    data = load(GOOD)
    ed = data["cv"]["sections"]["Education"][0]
    ed["start_date"], ed["end_date"] = start, end
    return data


@pytest.mark.parametrize("start,end", [(2016, 2018), ("2016", "2018")])
def test_lint_accepts_a_year_only_range(start, end):
    assert "date-precision" not in codes(rr.lint_resume(_year_only(start, end), known_evidence=known_ids()))


def test_lint_accepts_a_year_only_single_date():
    data = load(GOOD)
    entry = next(e for e in data["cv"]["sections"]["Publications"] if e.get("date"))
    entry["date"] = 2022
    assert "date-precision" not in codes(rr.lint_resume(data, known_evidence=known_ids()))


@pytest.mark.parametrize("start,end", [(2016, "2018-05"), ("2016-09", 2018), (2016, "present")])
def test_lint_still_rejects_mixed_precision_inside_one_entry(start, end):
    assert "date-precision" in codes(rr.lint_resume(_year_only(start, end), known_evidence=known_ids()))


def test_format_entry_date_renders_year_only_ranges():
    template = rr.load_template()
    assert rr.format_entry_date({"start_date": 2017, "end_date": 2021}, template) == "2017 - 2021"
    assert rr.format_entry_date({"start_date": "2017", "end_date": "2021"}, template) == "2017 - 2021"


def _render_year_only(tmp_path, engine):
    src = tmp_path / "year-only.yaml"
    src.write_text(yaml.safe_dump(_year_only(), sort_keys=False), encoding="utf-8")
    out = tmp_path / engine
    rc = main(["render", "--input", str(src), "--out", str(out), "--engine", engine, "--evidence", str(EVIDENCE)])
    return rc, out / load(out / "meta.yaml")["pdf"]


@needs_rendercv
def test_rendercv_renders_a_year_only_range_that_passes_the_ats_date_rule(tmp_path):
    import ats_check as ac
    rc, pdf = _render_year_only(tmp_path, "rendercv")
    text = pdf_text(pdf)
    assert rc == 0 and "2016 - 2018" in text and "Mar 2023 - Present" in text
    assert ac.check_dates(text).status == "pass"


@needs_latex
def test_latex_renders_a_year_only_range_that_passes_the_ats_date_rule(tmp_path):
    import ats_check as ac
    rc, pdf = _render_year_only(tmp_path, "latex")
    text = pdf_text(pdf)
    assert rc == 0 and "2016 - 2018" in text and "Mar 2023 - Present" in text
    assert ac.check_dates(text).status == "pass"


# --------------------------------------------------------------------------- opt-in one-page density (resume lane, 2026-09-25)
EXTRA_BULLETS = [
    "Profiled the retrieval index and halved embedding refresh time for the support assistant <!-- ev:nw-rag-01 -->",
    "Added regression slices to the golden set so every release reports accuracy per product line <!-- ev:nw-eval-01 -->",
    "Tuned autoscaling on the inference service so p95 latency held through the weekly traffic peak <!-- ev:nw-serve-01 -->",
]


def _dense_fixture(tmp_path: Path, n: int = 14) -> Path:
    """The good fixture (fictional) with n more one-line bullets: too long for the house density."""
    data = load(GOOD)
    highlights = data["cv"]["sections"]["Experience"][0]["highlights"]
    for i in range(n):
        highlights.append(EXTRA_BULLETS[i % 3].replace(" <!--", f" (round {i + 1}) <!--"))
    path = tmp_path / "dense.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def _paths(node, prefix=()):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _paths(v, prefix + (k,))
    else:
        yield prefix, node


def test_the_one_page_density_changes_only_size_spacing_and_margins():
    house = rr.load_template()
    dense = rr.apply_density(house, "one-page")
    changed = {p: v for p, v in _paths(dense["design"]) if dict(_paths(house["design"])).get(p) != v}
    assert changed == {("page", "top_margin"): "0.4in", ("page", "bottom_margin"): "0.4in",
                       ("page", "left_margin"): "0.4in", ("page", "right_margin"): "0.4in",
                       ("typography", "line_spacing"): "0.4em", ("typography", "font_size", "body"): "10pt"}
    assert dense["locale"] == house["locale"] and dense["settings"] == house["settings"]
    assert rr.apply_density(house, "house") is house and rr.apply_density(house, None) is house
    assert house["design"]["typography"]["font_size"]["body"] == "10.5pt"      # the default is unchanged
    assert rr.estimate_chars_per_line(dense) > rr.estimate_chars_per_line(house)


def test_the_dense_design_validates_against_rendercv_models():
    from rendercv.schema.models.design.built_in_design import built_in_design_adapter
    design = rr.apply_density(rr.load_template(), "one-page")["design"]
    built_in_design_adapter.validate_python(design)


@pytest.mark.parametrize("overlay,words", [
    ({"typography": {"alignment": "justified"}}, "may change only"),
    ({"templates": {"date_range": "START_DATE to END_DATE"}}, "may change only"),
    ({"typography": {"font_size": {"body": "9pt"}}}, "floor"),
])
def test_a_density_cannot_touch_an_ats_rule(overlay, words):
    template = copy.deepcopy(rr.load_template())
    template["densities"]["bad"] = overlay
    with pytest.raises(rr.UsageError, match=words):
        rr.apply_density(template, "bad")


def test_an_unknown_density_or_the_latex_engine_is_a_usage_error(tmp_path, capsys):
    assert main(["render", "--input", str(GOOD), "--out", str(tmp_path), "--evidence", str(EVIDENCE),
                    "--density", "tiny"]) == 2
    assert main(["render", "--input", str(GOOD), "--out", str(tmp_path), "--evidence", str(EVIDENCE),
                    "--engine", "latex", "--density", "one-page"]) == 2
    assert "RenderCV engine only" in capsys.readouterr().err


@needs_rendercv
def test_a_resume_too_long_for_the_house_density_fits_one_page_dense_and_stays_ats_clean(tmp_path):
    import ats_check
    src = _dense_fixture(tmp_path)
    common = ["--input", str(src), "--evidence", str(EVIDENCE), "--facts", str(FACTS)]
    house = rr.render(src, tmp_path / "house", evidence_path=EVIDENCE, facts_path=FACTS, rulings_path=NO_RULINGS, log=lambda *_: None)
    assert not house.ok and not (tmp_path / "house").exists()                      # page budget; F80: V untouched
    house_meta = load(house.draft_dir / "meta.yaml")
    assert house_meta["pages"] == 2
    assert main(["render", *common, "--out", str(tmp_path / "dense"), "--density", "one-page"]) == 0
    meta = load(tmp_path / "dense" / "meta.yaml")
    assert meta["pages"] == 1 and meta["density"] == "one-page" and meta["status"] == "ok"
    assert house_meta["density"] == "house"
    assert meta["hash"] != house_meta["hash"]
    pdf = tmp_path / "dense" / meta["pdf"]
    assert meta["pdf_title"] == rr.pdf_title(rr.facts_name(FACTS)) == rr.pdf_metadata_title(pdf)   # a base: no lens
    text = pdf_text(pdf)
    assert "Mar 2023 - Present" in text                                             # dates inline
    assert "\nExperience\n" in f"\n{text}" and "E x p e r i e n c e" not in text    # no letter spacing
    assert ats_check.check_glyphs({"pdftotext": text}).status == "pass"            # no ligatures
    assert "(round 14)" in text


def test_cv_sidecar_opens_the_cv_headings():
    """A CV (sidecar doc: cv) may carry the standard academic CV headings; a resume keeps the eight."""
    cv_heads = ["Research Experience", "Teaching Experience", "Presentations", "Student Leadership"]
    data = load(GOOD)
    secs = data["cv"]["sections"]
    for h in cv_heads:
        secs[h] = copy.deepcopy(secs["Experience"]) if "Experience" in h else ["A line. <!-- ev:nw-rag-01 -->"]
    resume = rr.lint_resume(copy.deepcopy(data), known_evidence=known_ids())
    assert {str(e.path) for e in resume.errors if e.code == "noncanonical-heading"} >= {f"cv.sections.{h}" for h in cv_heads}
    data.setdefault("sidecar", {})["doc"] = "cv"
    cv = rr.lint_resume(copy.deepcopy(data), known_evidence=known_ids())
    assert not [e for e in cv.errors if e.code == "noncanonical-heading"], cv.errors
    assert not [e for e in cv.errors if e.code == "bad-sidecar"], cv.errors
    data["sidecar"]["doc"] = "poster"
    bad = rr.lint_resume(copy.deepcopy(data), known_evidence=known_ids())
    assert [e for e in bad.errors if e.code == "bad-sidecar" and "doc" in str(e.path)]


CLASSIC = rr.NAMED_TEMPLATES["classic"]


def test_fields_inside_typst_commands_are_typst_safe():
    """A ')' in a position or project name ends RenderCV's command match early (unclosed
    delimiter), and a '_' there is never escaped; the house template (markdown) is untouched."""
    clean = {"cv": {"name": "R", "sections": {
        "Experience": [{"company": "Acme", "position": "Lab Technician (part-time)", "date": "2025"}],
        "Projects": [{"name": "demo_app Weather Kit", "date": "2025"}]}}}
    built = rr.build_rendercv_input(clean, rr.load_template(CLASSIC))
    pos = built["cv"]["sections"]["Experience"][0]["position"]
    name = built["cv"]["sections"]["Projects"][0]["name"]
    assert "(" not in pos and ")" not in pos and "#sym.paren.l;" in pos and "#sym.paren.r;" in pos
    assert "demo\\_app" in name
    house = rr.build_rendercv_input(clean, rr.load_template())
    assert house["cv"]["sections"]["Projects"][0]["name"] == "demo_app Weather Kit"
    assert "#sym" not in house["cv"]["sections"]["Experience"][0]["position"]


@needs_rendercv
def test_classic_renders_parentheses_and_underscores(tmp_path):
    data = load(GOOD)
    secs = data["cv"]["sections"]
    secs["Experience"][0]["position"] = secs["Experience"][0]["position"] + " (part-time)"
    if secs.get("Projects"):
        secs["Projects"][0]["name"] = "demo_app " + secs["Projects"][0]["name"]
    src = tmp_path / "resume.yaml"
    src.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=10000), encoding="utf-8")
    out = tmp_path / "out"
    rc = main(["render", "--input", str(src), "--out", str(out), "--evidence", str(EVIDENCE),
                  "--template", str(CLASSIC), "--force"])
    meta = load(out / "meta.yaml")
    text = pdf_text(out / meta["pdf"])
    assert "(part-time)" in text and "demo_app" in text and "\\_" not in text and "#sym" not in text


def test_education_after_experience_is_the_house_order_too():
    # A years- or seniority-sensitive variant puts Experience before Education; the rest keeps the
    # house order. Education anywhere before Experience, or any other reorder, still warns.
    data = load(GOOD)
    secs = data["cv"]["sections"]
    assert list(secs).index("Education") < list(secs).index("Experience")       # the fixture is the house order
    rest = [k for k in secs if k != "Education"]
    at = rest.index("Experience") + 1
    data["cv"]["sections"] = {k: secs[k] for k in rest[:at] + ["Education"] + rest[at:]}
    report = rr.lint_resume(data, known_evidence=known_ids())
    assert report.errors == []
    assert "section-order" not in [w.code for w in report.warnings]
    data["cv"]["sections"] = {k: secs[k] for k in rest + ["Education"]}          # Education last: still after Experience
    assert "section-order" not in [w.code for w in rr.lint_resume(data, known_evidence=known_ids()).warnings]
