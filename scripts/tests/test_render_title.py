"""The PDF metadata title: '<facts name>, Resume', never an em-dash, in both engines. With the one-base model the
lens is an internal emphasis label: a base (base_of null) and a variant of the general base (base_of: general) never
show it (title_lens); only a variant of another designed base would read '<facts name>, Resume (<Lens>)'.

A resume PDF whose title carries a name variant and an em-dash puts both in the one line every PDF viewer and many
ATS show first. render_resume.py builds the title from profile/facts.yaml (identity.name_on_resumes.canonical) plus
"Resume", and sets it in the RenderCV settings and the LaTeX hyperref setup. Fixtures describe a FICTIONAL person;
the ATS rules (inline dates, plain headings, no ligatures, interword spaces) are re-checked on the same renders.
"""
from __future__ import annotations

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
GOOD, EVIDENCE, FACTS = FIX / "good.yaml", FIX / "evidence.yaml", FIX / "facts.yaml"
DASHES = "‐‑‒–—―−⸺⸻"
LIGATURES = "ﬀﬁﬂﬃﬄﬅﬆ"

needs_rendercv = pytest.mark.skipif(not rr.rendercv_available(), reason="RenderCV not installed")
needs_latex = pytest.mark.skipif(rr.find_pdflatex() is None, reason="pdflatex not installed")


def title_of(pdf: Path) -> str | None:
    import pypdf
    return pypdf.PdfReader(str(pdf)).metadata.title


def pdfinfo_title(pdf: Path) -> str | None:
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    return next((line.split(":", 1)[1].strip() for line in out.splitlines() if line.startswith("Title:")), None)


def render(tmp_path: Path, engine: str, *, data: dict | None = None, facts: Path | None = FACTS) -> tuple[int, Path, dict]:
    src = GOOD
    if data is not None:
        src = tmp_path / "resume.yaml"
        src.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    out = tmp_path / engine
    args = ["render", "--input", str(src), "--out", str(out), "--engine", engine, "--evidence", str(EVIDENCE),
            "--keep-build", "--rulings", str(tmp_path / "no-rulings.yaml")]
    if facts is not None:
        args += ["--facts", str(facts)]
    rc = rr.main(args)
    meta = yaml.safe_load((out / "meta.yaml").read_text(encoding="utf-8"))
    return rc, out / meta["pdf"], meta


# --------------------------------------------------------------------------- the title text
@pytest.mark.parametrize("name, lens, want", [
    ("Riley Moreno", "agentic-systems", "Riley Moreno, Resume (Agentic Systems)"),
    ("Riley Moreno", "ai-software", "Riley Moreno, Resume (AI Software)"),
    ("Riley Moreno", "applied-ml", "Riley Moreno, Resume (Applied ML)"),
    ("Riley Moreno", "research-nlp", "Riley Moreno, Resume (Research NLP)"),
    ("Riley Moreno", None, "Riley Moreno, Resume"),
    ("Riley Moreno", "", "Riley Moreno, Resume"),
])
def test_pdf_title_is_the_name_resume_and_the_lens(name, lens, want):
    assert rr.pdf_title(name, lens) == want


@pytest.mark.parametrize("name", ["Riley M Moreno — Resume", "Riley–Moreno", 'Riley "R" Moreno\\', "Riley  #Moreno {x}"])
def test_pdf_title_never_carries_a_dash_variant_or_a_character_that_breaks_typst_or_latex(name):
    title = rr.pdf_title(name, "applied—ml")
    assert not any(c in title for c in DASHES + '"\\{}#%$&~^_<>|'), title
    assert "  " not in title and title.endswith(")")


@pytest.mark.parametrize("sidecar, want", [
    ({"lens": "applied-ml", "base_of": None, "job": None}, None),                      # a base: no lens
    ({"lens": "applied-ml", "base_of": "general", "job": "jobs/acme-1.md"}, None),     # a variant of the general base
    ({"lens": None, "base_of": "general", "job": None}, None),
    ({"lens": "applied-ml", "base_of": "staff-ml-platform", "job": None}, "applied-ml"),  # another designed base
    (None, None), ("not a mapping", None),
])
def test_the_title_lens_is_dropped_for_bases_and_variants_of_the_general_base(sidecar, want):
    assert rr.title_lens(sidecar) == want
    assert rr.pdf_title("Riley Moreno", rr.title_lens(sidecar)) == (
        "Riley Moreno, Resume" if want is None else "Riley Moreno, Resume (Applied ML)")


def test_facts_name_reads_the_canonical_name(tmp_path):
    assert rr.facts_name(FACTS) == "Riley Moreno"
    only_full = tmp_path / "facts.yaml"
    only_full.write_text("identity:\n  full_name: Sam Okafor\n", encoding="utf-8")
    assert rr.facts_name(only_full) == "Sam Okafor"
    assert rr.facts_name(tmp_path / "missing.yaml") is None
    broken = tmp_path / "broken.yaml"
    broken.write_text("identity: [unclosed\n", encoding="utf-8")
    assert rr.facts_name(broken) is None


@pytest.mark.real_records
def test_the_real_facts_file_yields_a_name():
    name = rr.facts_name(JS / "profile" / "facts.yaml")
    assert isinstance(name, str) and name.strip()


# --------------------------------------------------------------------------- real renders
@needs_rendercv
def test_rendercv_writes_the_facts_title_and_meta_records_it(tmp_path):
    rc, pdf, meta = render(tmp_path, "rendercv")
    assert rc == 0
    assert title_of(pdf) == pdfinfo_title(pdf) == meta["pdf_title"] == "Riley Moreno, Resume"


@needs_latex
def test_latex_writes_the_same_title(tmp_path):
    rc, pdf, meta = render(tmp_path, "latex")
    assert rc == 0
    assert title_of(pdf) == pdfinfo_title(pdf) == meta["pdf_title"] == "Riley Moreno, Resume"


@needs_rendercv
def test_the_facts_name_wins_over_a_variant_in_the_resume_and_the_render_says_so(tmp_path, capsys):
    data = yaml.safe_load(GOOD.read_text(encoding="utf-8"))
    data["cv"]["name"] = "R. Moreno"                      # a variant the YAML author typed
    del data["sidecar"]["lens"]
    rc, pdf, _ = render(tmp_path, "rendercv", data=data)
    assert rc == 0 and title_of(pdf) == "Riley Moreno, Resume"
    assert "cv.name 'R. Moreno' differs from the facts name 'Riley Moreno'" in capsys.readouterr().out


@needs_rendercv
def test_without_a_facts_name_the_title_falls_back_to_the_resume_name(tmp_path, capsys):
    rc, pdf, _ = render(tmp_path, "rendercv", facts=tmp_path / "no-facts.yaml")
    assert rc == 0 and title_of(pdf) == "Riley Moreno, Resume"
    assert "facts name" in capsys.readouterr().out


@needs_rendercv
@needs_latex
def test_the_ats_rules_still_hold_on_both_engines(tmp_path):
    """Inline dates, one-word headings on their own line, no ligature glyphs in the text layer, no dash
    variants, and pdfinterwordspaceon right after begin{document} in the LaTeX build."""
    for engine in ("rendercv", "latex"):
        rc, pdf, _ = render(tmp_path, engine)
        text = subprocess.run(["pdftotext", "-enc", "UTF-8", str(pdf), "-"], capture_output=True, text=True).stdout
        lines = [ln.strip() for ln in text.splitlines()]
        assert rc == 0, engine
        assert any("Machine Learning Engineer" in ln and "Mar 2023 - Present" in ln for ln in lines), engine
        assert {"Education", "Skills", "Experience", "Projects"} <= set(lines), engine
        assert not any(c in text for c in LIGATURES), engine
        assert "—" not in text, engine
    tex = next((tmp_path / "latex" / "build").glob("*.tex")).read_text(encoding="utf-8")
    assert tex.index("\\begin{document}") < tex.index("\\pdfinterwordspaceon")
    assert "pdftitle={Riley Moreno, Resume}" in tex
