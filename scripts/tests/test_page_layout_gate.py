"""A page whose rendered layout FAILs is never marked ready or packed for an application.

apply_pack.check_variant (and so `apply_pack.py build`, apply_batch and next_up's READY) refuses a variant unless the
render folder holds layout-report.json from scripts/page_layout_check.py with verdict PASS for the exact PDF it ships
(name and sha256), and unless the company recruiter's recruiter.md carries `layout: PASS` (the recruiter Read
page-1.png). The mass tier's base fallback refuses a base PDF without a current PASS in
resumes/bases/general/layout/<classic|inline>/layout-report.json.

Fixtures reuse the fictional trees of test_apply_pack.py and test_apply_batch.py (Rowan Quill Example).
"""
from __future__ import annotations

import hashlib
import json
import shutil

import pytest
import yaml

from test_apply_pack import (INLINE_BYTES, INLINE_PDF, PDF_BYTES, PDF_NAME, SLUG, TODAY, V, ap, build, make_tree,  # noqa: F401
                             pack_path, set_front, write)
from test_apply_batch import BASE_PDF, mass_ready, rubric, run, sheet  # noqa: F401  (rubric is a fixture)

import apply_batch as ab  # noqa: E402


@pytest.fixture
def js(tmp_path):
    return make_tree(tmp_path / "job-search")


def report_path(root):
    return root / V / "layout-report.json"


def edit_report(root, **changes):
    path = report_path(root)
    data = json.loads(path.read_text())
    data.update(changes)
    path.write_text(json.dumps(data))


def refused(root, capsys, *needles):
    assert build(root) == 1
    out = capsys.readouterr().out
    assert out.startswith("REFUSED"), out[:300]
    for n in needles:
        assert n in out, (n, out[:400])
    assert not pack_path(root).exists()
    return out


# --------------------------------------------------------------------------- the render's layout report
def test_a_current_layout_pass_and_a_recruiter_layout_pass_build_ready(js, capsys):
    assert build(js) == 0
    assert capsys.readouterr().out.startswith("READY")


def test_a_missing_layout_report_refuses_naming_the_command(js, capsys):
    report_path(js).unlink()
    out = refused(js, capsys, "page layout", "layout-report.json missing")
    assert f"python3 scripts/page_layout_check.py {V}/{PDF_NAME}" in out


def test_a_layout_fail_refuses_with_its_failures(js, capsys):
    edit_report(js, verdict="FAIL", passed=False, failures=["bottom_whitespace: bottom is 7.8% emptier than the top"])
    refused(js, capsys, "page layout FAIL", "bottom_whitespace")


def test_a_report_for_another_render_refuses(js, capsys):
    edit_report(js, pdf_sha256="0" * 64)
    refused(js, capsys, "different render", "re-run")


def test_a_report_for_another_pdf_refuses(js, capsys):
    edit_report(js, pdf="Old_Resume.pdf")
    refused(js, capsys, "Old_Resume.pdf", "re-run")


def test_an_unreadable_report_refuses(js, capsys):
    report_path(js).write_text("{not json")
    refused(js, capsys, "unreadable")


def test_the_inline_render_needs_its_own_layout_pass(js, capsys):
    idir = js / V / "inline"
    idir.mkdir(parents=True, exist_ok=True)
    (idir / INLINE_PDF).write_bytes(INLINE_BYTES)
    sha = hashlib.sha256(INLINE_BYTES).hexdigest()
    write(idir / "meta.yaml", yaml.safe_dump({"built_at": "2026-09-24T12:00:00", "status": "ok",
                                              "source": f"{V}/resume.yaml", "pdf": INLINE_PDF, "pdf_sha256": sha}))
    write(idir / "ats-report.json", json.dumps({"pdf": f"{V}/inline/{INLINE_PDF}", "checked_at": "2026-09-24T12:40:00",
                                               "passed": True, "vendor": "oracle", "failures": []}))
    args = ["--root", str(js), "build", SLUG, "--today", TODAY.isoformat(), "--pdf", "inline"]
    assert ap.main(args) == 1                                         # the classic report does not cover the inline PDF
    assert "inline/layout-report.json missing" in capsys.readouterr().out
    write(idir / "layout-report.json", json.dumps({"pdf": INLINE_PDF, "pdf_sha256": sha, "verdict": "PASS",
                                                  "passed": True, "failures": []}))
    assert ap.main(args) == 0


# --------------------------------------------------------------------------- the recruiter read the page
def test_a_recruiter_review_without_a_layout_line_refuses(js, capsys):
    path = js / V / "recruiter.md"
    meta, body = ap.fm.load(path)
    meta.pop("layout")
    path.write_text(ap.fm.dump(meta, body), encoding="utf-8")
    refused(js, capsys, "recruiter.md", "layout", "page-1.png")


@pytest.mark.parametrize("verdict", ["APPROVE", "GAP: ask the owner"])
def test_a_recruiter_layout_fail_blocks_even_an_approve(js, capsys, verdict):
    set_front(js / V / "recruiter.md", verdict=verdict, owner_decision="send it anyway, 2026-10-05",
              layout="FAIL: Experience packed, bottom fifth empty")
    refused(js, capsys, "layout FAIL")


# --------------------------------------------------------------------------- READY elsewhere
def test_next_up_does_not_call_a_layout_fail_ready(js):
    import next_up
    ctx = ap.load_ctx(js, TODAY)
    app, *_ = ap.load_application(js, SLUG)
    assert next_up.signed(ctx, SLUG, app) is None                      # the PASS page ships: READY
    edit_report(js, verdict="FAIL", passed=False, failures=["header_gaps: cluttered"])
    why = next_up.signed(ctx, SLUG, app)                               # signed() names its last try (inline) when both refuse
    assert why is not None and why.startswith("refused: ")
    with pytest.raises(ap.Refusal, match="page layout FAIL"):
        ap.check_variant(ctx, SLUG, app, "classic")


def test_a_batch_refuses_a_variant_whose_layout_fails(js):
    edit_report(js, verdict="FAIL", passed=False, failures=["bottom_whitespace: short page"])
    assert run(js, "--tier", "focused", SLUG) == 1
    _, body = sheet(js)
    assert "REFUSED" in body and "page layout FAIL" in body


def test_the_mass_base_needs_a_current_layout_pass(js, rubric):
    mass_ready(js)
    shutil.rmtree(js / "resumes/bases/general/layout")
    assert run(js, SLUG) == 1
    _, body = sheet(js)
    assert "REFUSED" in body and "page layout" in body and "promote to focused" in body


def test_the_mass_base_with_a_failing_layout_is_refused(js, rubric):
    mass_ready(js)
    path = js / "resumes/bases/general/layout/classic/layout-report.json"
    data = json.loads(path.read_text())
    data.update(verdict="FAIL", passed=False, failures=["header_gaps: cluttered"])
    path.write_text(json.dumps(data))
    assert run(js, SLUG) == 1
    _, body = sheet(js)
    assert "page layout FAIL" in body and "header_gaps" in body


def test_the_mass_base_with_a_layout_pass_is_ready(js, rubric):
    mass_ready(js)
    assert run(js, SLUG) == 0


# --------------------------------------------------------------------------- end to end with the real checker
@pytest.mark.skipif(not (shutil.which("pdftotext") and shutil.which("pdftoppm")), reason="poppler not installed")
def test_the_checkers_own_report_is_what_the_pack_reads(js, capsys):
    import page_layout_check as plc
    from test_page_layout_check import make_pdf, resume_page
    pdf = js / V / PDF_NAME
    for page, code in ((resume_page(), 0), (resume_page(end_at=500.0), 1)):
        pdf.write_bytes(make_pdf([page]))
        meta_path = js / V / "meta.yaml"
        meta = yaml.safe_load(meta_path.read_text())
        meta["pdf_sha256"] = hashlib.sha256(pdf.read_bytes()).hexdigest()
        meta_path.write_text(yaml.safe_dump(meta, sort_keys=False))
        assert plc.main([str(pdf)]) == code
        capsys.readouterr()
        assert build(js) == code
        out = capsys.readouterr().out
        assert out.startswith("READY") if code == 0 else ("bottom_whitespace" in out and out.startswith("REFUSED"))
