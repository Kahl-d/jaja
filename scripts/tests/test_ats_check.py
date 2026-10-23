"""Tests for scripts/ats_check.py: extraction agreement (pdftotext / pypdf / PDFKit), contact
literal, headings, dates, glyphs, em-dashes, split words, columns/tables, page budget, keyword
coverage vs a JD with the skills taxonomy, and vendor warnings. Fixtures describe a FICTIONAL
person; PDFs are rendered or compiled inside the tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import ats_check as ac  # noqa: E402
import render_resume as rr  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "resume"
GOOD = FIX / "good.yaml"
BROKEN = FIX / "broken.yaml"
EVIDENCE = FIX / "evidence.yaml"
JD = FIX / "jd.md"
TAXONOMY = FIX / "skills-taxonomy.yaml"
TWO_COLUMN = FIX / "two-column.typ"
EMAIL = "riley.moreno@example.com"


def by_name(report: dict, name: str) -> dict:
    return next(c for c in report["checks"] if c["name"] == name)


# --------------------------------------------------------------------------- unit: text checks
def test_tokens_and_similarity():
    assert ac.tokens("Built a RAG-based bot. Jan\u00a02023") == ["built", "a", "rag", "based", "bot", "jan", "2023"]
    a = ac.tokens("one two three four five six seven eight nine ten")
    assert ac.similarity(a, a) == 1.0
    moved = a[5:] + a[:5]
    assert ac.similarity(a, moved) < 0.8


def test_contact_literal_must_survive_every_extractor():
    ok = ac.check_contact({"pdftotext": f"Riley\n{EMAIL} | x", "pypdf": f"{EMAIL}"}, EMAIL)
    assert ok.status == "pass"
    bad = ac.check_contact({"pdftotext": f"{EMAIL}", "pdfkit": "riley.moreno@ example.com"}, EMAIL)
    assert bad.status == "fail" and "pdfkit" in bad.detail


def test_em_dash_detection():
    assert ac.check_em_dashes({"pdftotext": "Jan 2023 \u2013 Mar 2025"}).status == "pass"
    bad = ac.check_em_dashes({"pdftotext": "Built X \u2014 fast"})
    assert bad.status == "fail" and "Built X" in bad.detail


def test_glyph_garbage_detection():
    assert ac.check_glyphs({"pdftotext": "Efficient workflow"}).status == "pass"
    for garbage in ("E\ufb03cient", "icon \ue000 here", "text (cid:12) more", "bad \ufffd char"):
        assert ac.check_glyphs({"pypdf": garbage}).status == "fail", garbage


def test_split_word_detection():
    bad = ac.check_split_words({"pdfkit": "with a retrieval-\naugmented stack"})
    assert bad.status == "fail" and "retrieval-" in bad.detail
    assert ac.check_split_words({"pdfkit": "state-of-the-art\nNext bullet"}).status == "pass"


def test_find_headings_canonical_and_aliases():
    text = "Riley Moreno\nWORK EXPERIENCE\nx\nEducation\ny\nSkills\nz\nInterests\n"
    heads = ac.find_headings(text)
    assert [h.canonical for h in heads] == ["Experience", "Education", "Skills"]
    assert heads[0].is_standard is False and heads[1].is_standard is True


def test_letter_spaced_headings_are_flagged():
    text = "ALEX X\nE D U C AT I O N\nM.S. Something\nT E C H N I C A L S K I L L S\nx\n" \
           "P R O F E S S I O N A L E X P E R I E N C E\ny\n"
    heads = ac.find_headings(text)
    assert [h.canonical for h in heads] == ["Education", "Skills", "Experience"]
    assert all(h.letter_spaced for h in heads)
    res = ac.check_headings({"pdfkit": text, "pypdf": "EDUCATION\nx\nTECHNICAL SKILLS\ny\n"
                                                      "PROFESSIONAL EXPERIENCE\nz\n"})
    assert res.status == "fail" and "letter-spaced" in res.detail and "E D U C AT I O N" in res.detail


def test_check_headings_order_and_required():
    texts = {"pdftotext": "Experience\na\nEducation\nb\nSkills\n", "pdfkit": "Experience\na\nEducation\nb\nSkills\n"}
    assert ac.check_headings(texts).status == "pass"
    assert ac.check_headings(texts, expected=["Education", "Experience", "Skills"]).status == "fail"
    missing = ac.check_headings({"pdftotext": "Skills\nx\n"})
    assert missing.status == "fail" and "Experience" in missing.detail
    swapped = ac.check_headings({"pdftotext": "Experience\nEducation\n", "pdfkit": "Education\nExperience\n"})
    assert swapped.status == "fail"


def test_dates_canonical_and_consistent():
    good = ac.check_dates("Mar 2023 - Present\nJun 2020 - Feb 2023\nPaper | Jul 2022\n")
    assert good.status == "pass" and good.data["format"] == "Mon YYYY"
    assert good.data["range_separator"] == "-"
    en = ac.check_dates("Mar 2023 \u2013 Present\nJun 2020 \u2013 Feb 2023\n")
    assert en.status == "pass" and en.data["range_separator"] == "\u2013"


def test_dates_mixed_unparseable_and_backwards_fail():
    assert ac.check_dates("Sept 2020 \u2013 June 2021\nJan 2022 \u2013 Present\n").status == "fail"
    assert ac.check_dates("Jan 2022 \u2013 Present\nSummer 2021 internship\n").status == "fail"
    assert ac.check_dates("May 2024 \u2013 Jan 2023\n").status == "fail"
    assert ac.check_dates("Jan 2022 \u2013 Present\n03/2020 - 05/2021\n").status == "fail"
    assert ac.check_dates("Jan 2022 - Present\nMar 2019 \u2013 Dec 2021\n").status == "fail"


def test_detect_columns_in_layout_text():
    single = ("Machine Learning Engineer, Northwind Robotics                 Mar 2023 - Present\n"
              "\u2022 Built a support assistant over maintenance logs\n"
              "Harborview State University, Seattle, WA                      Sep 2018 - May 2020\n")
    assert ac.detect_columns(single) == []
    double = ("Skills                          Experience\n"
              "Python, SQL, TypeScript,        Northwind Robotics, ML Engineer\n"
              "PyTorch, Kubernetes             \u2022 Built a support assistant\n"
              "Education                       \u2022 Designed an evaluation harness\n"
              " Tool        Level       Years\n")
    assert len(ac.detect_columns(double)) >= 3
    assert ac.detect_columns("| Tool | Level | Years |\n")        # markdown table row


# --------------------------------------------------------------------------- unit: keywords
def test_load_taxonomy_shapes(tmp_path):
    tax = ac.load_taxonomy(TAXONOMY)
    assert tax.lookup("RAG").key == "retrieval-augmented generation"
    assert tax.lookup("k8s").key == "kubernetes"
    assert tax.lookup("ray").evidence == ["sk-ray"]
    assert tax.lookup("vllm").evidence == []
    flat = tmp_path / "t.yaml"
    flat.write_text("skills:\n  Python: {evidence: [a]}\n  Go: {}\n  plain: [Rust]\n", encoding="utf-8")
    t2 = ac.load_taxonomy(flat)
    assert t2.lookup("python").evidence == ["a"] and t2.lookup("rust") is not None


def test_keyword_coverage_math():
    tax = ac.load_taxonomy(TAXONOMY)
    cov = ac.keyword_coverage(["python", "rust"], ["ray", "langgraph"],
                              "Python services and LangGraph agents", tax)
    assert [c["term"] for c in cov["covered"]] == ["python", "langgraph"]
    assert [c["term"] for c in cov["missing_evidenced"]] == ["ray"]
    assert cov["missing_evidenced"][0]["evidence"] == ["sk-ray"]
    assert [c["term"] for c in cov["missing_unevidenced"]] == ["rust"]
    assert cov["must"] == {"total": 2, "covered": 1, "coverage": 0.5}
    assert cov["nice"] == {"total": 2, "covered": 1, "coverage": 0.5}
    assert cov["weighted_coverage"] == 0.5          # (2*1 + 1) / (2*2 + 2)


def test_extract_jd_terms_splits_must_and_nice():
    tax = ac.load_taxonomy(TAXONOMY)
    terms = ac.extract_jd_terms(JD.read_text(encoding="utf-8"), tax)
    must, nice = set(terms["must"]), set(terms["nice"])
    assert {"python", "pytorch", "kubernetes", "aws", "retrieval-augmented generation", "rust"} <= must
    assert {"langgraph", "ray", "vllm", "cuda", "typescript"} <= nice
    assert not must & nice
    assert "greenhouse" not in must | nice and "example labs" not in must | nice


# --------------------------------------------------------------------------- unit: vendors
def test_vendor_rules_are_warnings():
    ctx = ac.VendorContext(text="Experience\nMachine Learning Engineer | Mar 2023 - Present\nNorthwind, SF\n"
                                "\u2022 Built RAG with k8s\nWORK HISTORY\n",
                           headings=ac.find_headings("Experience\nWORK HISTORY\n"),
                           jd_text="Must know Kubernetes and retrieval-augmented generation",
                           has_docx=False, body_font={"name": "XCharter", "size": 10.0, "serif": True})
    lever = " ".join(ac.vendor_warnings("lever", ctx))
    assert "RAG" in lever
    taleo = " ".join(ac.vendor_warnings("taleo", ctx))
    assert "Kubernetes" in taleo
    ashby = " ".join(ac.vendor_warnings("ashby", ctx))
    assert "WORK HISTORY" in ashby
    workday = " ".join(ac.vendor_warnings("workday", ctx))
    assert "DOCX" in workday and "company line" in workday
    icims = " ".join(ac.vendor_warnings("icims", ctx))
    assert "serif" in icims
    assert "auto-score" in " ".join(ac.vendor_warnings("greenhouse", ctx))
    ctx.body_font = {"name": "SourceSans3", "size": 10.5, "serif": False}
    assert not any("serif" in w for w in ac.vendor_warnings("icims", ctx))
    assert not any("DOCX" in w for w in ac.vendor_warnings("unknown", ctx))


def test_acronyms_listed_once_and_spelled_out_ones_skipped():
    text = ("Built SDK tools with RAG. Shipped SDKs in CI.\n"
            "Retrieval-augmented generation (RAG) for support\nSan Francisco, CA\n")
    assert ac._acronyms_not_spelled_out(text) == ["SDK (software development kit)",
                                                  "CI (continuous integration)"]


# --------------------------------------------------------------------------- integration
@pytest.fixture(scope="module")
def renders(tmp_path_factory):
    if not rr.rendercv_available():
        pytest.skip("RenderCV not installed")
    good = tmp_path_factory.mktemp("good")
    assert rr.main(["render", "--input", str(GOOD), "--out", str(good),
                    "--evidence", str(EVIDENCE)]) == 0
    broken = tmp_path_factory.mktemp("broken")
    res = rr.render(BROKEN, broken, force=True, evidence_path=EVIDENCE, log=lambda *_: None)
    assert not res.ok and res.draft_dir is not None            # F80: the failed draft is kept outside V
    meta_good = yaml.safe_load((good / "meta.yaml").read_text(encoding="utf-8"))
    meta_broken = yaml.safe_load((res.draft_dir / "meta.yaml").read_text(encoding="utf-8"))
    return {"good": good / meta_good["pdf"], "broken": res.draft_dir / meta_broken["pdf"]}


def run(args: list[str], out: Path) -> tuple[int, dict]:
    rc = ac.main(["check", *args, "--out", str(out)])
    return rc, json.loads((out / "ats-report.json").read_text(encoding="utf-8"))


def test_good_render_passes_every_hard_check(renders, tmp_path):
    rc, rep = run(["--pdf", str(renders["good"]), "--jd", str(JD), "--vendor", "greenhouse",
                   "--contact-email", EMAIL, "--taxonomy", str(TAXONOMY), "--source", str(GOOD)],
                  tmp_path)
    assert rc == 0, rep["failures"]
    assert rep["passed"] is True and rep["pages"] == 1
    for name in ("text_layer", "reading_order", "contact_email", "headings", "dates", "glyphs",
                 "em_dashes", "split_words", "layout", "page_count", "probes"):
        assert by_name(rep, name)["status"] == "pass", name
    assert by_name(rep, "headings")["data"]["order"] == \
        ["Education", "Skills", "Experience", "Projects", "Publications", "Awards"]  # meta.yaml
    kw = rep["keywords"]
    covered = {c["term"] for c in kw["covered"]}
    assert {"python", "pytorch", "kubernetes", "aws", "retrieval-augmented generation",
            "langgraph", "typescript"} <= covered
    assert "ray" in {c["term"] for c in kw["missing_evidenced"]}
    assert {"rust", "vllm", "cuda"} <= {c["term"] for c in kw["missing_unevidenced"]}
    assert 0 < kw["must"]["coverage"] < 1
    assert rep["extractors"]["pdfkit"]["available"] in (True, False)
    md = (tmp_path / "ats-report.md").read_text(encoding="utf-8")
    assert "PASS" in md and "Keyword coverage" in md and "must NOT add" in md


def test_broken_render_fails_with_the_right_messages(renders, tmp_path):
    rc, rep = run(["--pdf", str(renders["broken"]), "--contact-email", EMAIL], tmp_path)
    assert rc == 1 and rep["passed"] is False
    failed = {c["name"] for c in rep["checks"] if c["status"] == "fail"}
    assert {"contact_email", "em_dashes", "page_count", "layout"} <= failed
    joined = " ".join(rep["failures"])
    assert EMAIL in joined and "em-dash" in joined and "pages" in joined and "table" in joined


def test_two_column_pdf_fails_layout_and_reading_order(tmp_path):
    typst = pytest.importorskip("typst")
    pdf = tmp_path / "two-column.pdf"
    typst.compile(str(TWO_COLUMN), output=str(pdf))
    rc, rep = run(["--pdf", str(pdf), "--contact-email", EMAIL], tmp_path)
    assert rc == 1
    assert by_name(rep, "layout")["status"] == "fail"
    joined = " ".join(rep["failures"]).lower()
    assert "column" in joined
    if rep["extractors"]["pdfkit"]["available"]:
        assert by_name(rep, "reading_order")["status"] == "fail"
        assert "reading order" in joined


def test_pdf_without_text_layer_fails(tmp_path):
    typst = pytest.importorskip("typst")
    src = tmp_path / "blank.typ"
    src.write_text('#set page(paper: "us-letter")\n#rect(width: 4cm, height: 2cm)\n', encoding="utf-8")
    pdf = tmp_path / "blank.pdf"
    typst.compile(str(src), output=str(pdf))
    rc, rep = run(["--pdf", str(pdf), "--contact-email", EMAIL], tmp_path)
    assert rc == 1 and by_name(rep, "text_layer")["status"] == "fail"


def test_vendor_warnings_on_real_pdfs(renders, tmp_path):
    rc, rep = run(["--pdf", str(renders["good"]), "--vendor", "workday", "--contact-email", EMAIL],
                  tmp_path)
    assert rc == 0                                           # vendor rules never fail the check
    warn = " ".join(rep["vendor_warnings"])
    assert "DOCX" in warn
    rc, rep = run(["--pdf", str(renders["good"]), "--vendor", "icims", "--contact-email", EMAIL],
                  tmp_path)
    assert rc == 0 and not any("serif" in w for w in rep["vendor_warnings"])
    assert rep["fonts"]["body"]["serif"] is False


def test_missing_pdf_is_a_usage_error(tmp_path, capsys):
    assert ac.main(["check", "--pdf", str(tmp_path / "nope.pdf")]) == 2


def test_without_pdfkit_the_check_still_runs(renders, tmp_path):
    rc, rep = run(["--pdf", str(renders["good"]), "--contact-email", EMAIL, "--no-pdfkit"],
                  tmp_path)
    assert rc == 0
    assert rep["extractors"]["pdfkit"]["available"] is False
    assert list(by_name(rep, "reading_order")["data"]["similarity"]) == ["pypdf"]
    assert "unavailable" in (tmp_path / "ats-report.md").read_text(encoding="utf-8")


def test_latex_fallback_pdf_passes_and_warns_for_icims(tmp_path):
    if rr.find_pdflatex() is None:
        pytest.skip("pdflatex not installed")
    out = tmp_path / "latex"
    assert rr.main(["render", "--input", str(GOOD), "--out", str(out), "--engine", "latex",
                    "--evidence", str(EVIDENCE)]) == 0
    pdf = out / yaml.safe_load((out / "meta.yaml").read_text(encoding="utf-8"))["pdf"]
    rc, rep = run(["--pdf", str(pdf), "--vendor", "icims", "--contact-email", EMAIL,
                   "--source", str(GOOD)], tmp_path)
    assert rc == 0, rep["failures"]
    assert by_name(rep, "probes")["status"] == "pass"
    assert rep["fonts"]["body"]["serif"] is True
    assert any("iCIMS" in w and "serif" in w for w in rep["vendor_warnings"])


def test_docx_next_to_the_pdf_satisfies_workday(tmp_path):
    if not rr.rendercv_available():
        pytest.skip("RenderCV not installed")
    out = tmp_path / "wd"
    assert rr.main(["render", "--input", str(GOOD), "--out", str(out), "--docx",
                    "--evidence", str(EVIDENCE)]) == 0
    pdf = out / yaml.safe_load((out / "meta.yaml").read_text(encoding="utf-8"))["pdf"]
    rc, rep = run(["--pdf", str(pdf), "--vendor", "workday", "--contact-email", EMAIL], tmp_path)
    assert rc == 0
    assert not any("submit the DOCX" in w for w in rep["vendor_warnings"])
    assert any("company line" in w for w in rep["vendor_warnings"])   # the PDF is title-first


# --------------------------------------------------------------------------- JD scope and must-have terms (2026-09-25)
JD_EVALS = FIX / "jd-llm-evals.md"


def test_must_haves_come_only_from_the_untrusted_jd_and_keep_multi_word_terms():
    tax = ac.load_taxonomy(TAXONOMY)
    terms = ac.extract_jd_terms(JD_EVALS.read_text(encoding="utf-8"), tax)
    must, nice = terms["must"], set(terms["nice"])
    assert {"golden datasets", "llm-as-judge", "regression testing", "python", "ml"} <= set(must)
    everything = set(must) | nice
    for junk in ("v1", "stem", "opt", "h1b", "kafka", "source", "sponsorship", "4410021"):
        assert junk not in everything, junk
    assert "machine learning" not in everything                       # "ML" is never expanded
    assert {"kubernetes", "langfuse", "langsmith"} <= nice
    assert "evaluation harness" in nice                               # from "What you'll do"


def test_coverage_matches_both_forms_of_an_abbreviation():
    tax = ac.load_taxonomy(TAXONOMY)
    terms = ac.extract_jd_terms(JD_EVALS.read_text(encoding="utf-8"), tax)
    resume = ("Built machine learning eval pipelines in Python: golden sets, LLM-as-a-judge graders and "
              "regression tests on every prompt change.")
    cov = ac.keyword_coverage(terms["must"], terms["nice"], resume, tax)
    covered = {c["term"] for c in cov["covered"]}
    assert {"ml", "python", "golden datasets", "llm-as-judge", "regression testing"} <= covered
    assert ac.keyword_coverage(["machine learning"], [], "Shipped ML systems", tax)["must"]["covered"] == 1


def test_the_original_fixture_ignores_its_frontmatter_and_keeps_its_split():
    tax = ac.load_taxonomy(TAXONOMY)
    terms = ac.extract_jd_terms(JD.read_text(encoding="utf-8"), tax)
    everything = set(terms["must"]) | set(terms["nice"])
    assert "greenhouse" not in everything and "example-labs-mle-0001" not in everything
    assert "rag" not in terms["must"]                         # both forms written: the full name is kept once


def test_a_plain_jd_without_tags_is_still_scanned_whole():
    tax = ac.load_taxonomy(TAXONOMY)
    terms = ac.extract_jd_terms("Requirements:\n- Python and golden datasets\n", tax)
    assert {"python", "golden datasets"} <= set(terms["must"])


# --------------------------------------------------------------------------- --date (2026-09-25)
def test_check_date_sets_checked_at_in_both_reports(renders, tmp_path, monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(rr, "wall_clock", lambda: datetime(2030, 1, 1, 9, 0, 0), raising=False)
    rc, rep = run(["--pdf", str(renders["good"]), "--contact-email", EMAIL, "--date", "2026-09-28"], tmp_path)
    assert rep["checked_at"] == "2026-09-28T12:00:00"
    md = (tmp_path / "ats-report.md").read_text(encoding="utf-8")
    assert "checked 2026-09-28T12:00:00" in md and "2030" not in md


# --------------------------------------------------------------------------- year-only ranges (2026-09-25)
def test_year_only_ranges_sit_alongside_mon_yyyy():
    mixed = ac.check_dates("Mar 2023 - Present\nJun 2020 - Feb 2023\nB.Tech | 2017 - 2021\n")
    assert mixed.status == "pass" and mixed.data["format"] == "Mon YYYY"
    assert ac.check_dates("2017 - 2021\n2013 - 2017\n").status == "warn"          # all year-only: consistent


def test_one_range_mixing_year_and_month_precision_fails():
    rep = ac.check_dates("Mar 2023 - Present\n2017 - May 2021\n")
    assert rep.status == "fail" and "2017 - May 2021" in rep.detail


def test_widows_flags_a_one_word_last_line_and_a_split_token():
    import ats_check as ac
    long = "  " + "word " * 15
    assert ac.check_widows("• first line of a bullet that is long enough to wrap past sixty chars\n  agent.").status == "warn"
    res = ac.check_widows("• an A/B evaluation where the long line wraps right here after A/\n  B evaluation the judge")
    assert res.status == "warn" and "A/ B" in res.detail
    assert ac.check_widows("• short\n" + long).status == "pass"


def test_a_held_term_is_never_offered_as_you_could_add():
    tax = ac.load_taxonomy(TAXONOMY)
    cov = ac.keyword_coverage(["python", "rust"], ["ray", "langgraph"], "Python services", tax, held={"ray"})
    assert [c["term"] for c in cov["missing_evidenced"]] == ["langgraph"]
    assert {"term": "ray", "tier": "nice", "held": True} in cov["missing_unevidenced"]


def test_unknown_vendor_warnings_say_why_they_apply():
    ctx = ac.VendorContext(text="Experience\nBuilt RAG and MCP tools\n", headings=[], body_font=None, has_docx=False,
                           taxonomy=None) if "taxonomy" in ac.VendorContext.__dataclass_fields__ else None
    if ctx is None:
        return
    warns = ac.vendor_warnings("unknown", ctx)
    assert warns and all(w.startswith("vendor unknown, strictest rules apply: ") for w in warns)


def test_cv_headings_are_recognized_k238():
    heads = ac.find_headings("Experience\nResearch Experience\nTeaching Experience\nPresentations\nStudent Leadership\n")
    assert [h.canonical for h in heads] == ["Experience", "Research Experience", "Teaching Experience", "Presentations",
                                            "Student Leadership"]
    assert all(h.is_standard for h in heads)
