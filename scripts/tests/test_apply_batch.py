"""apply_batch.py: the mass-tier batch path. Given N slugs it builds every apply pack, lints each, and writes
one sheet state/apply-batches/<date>-<n>.md the Recruiter hands the owner: per role the link, the PDF path, the pack status
and the blockers. Nothing is submitted or sent without the owner's recorded yes.

Fixtures reuse the fictional tree of test_apply_pack.py (Rowan Quill Example)."""
from __future__ import annotations

import pytest

from test_apply_pack import JOB, PDF_NAME, SLUG, TODAY, V, make_tree, set_front, write  # noqa: F401

import apply_batch as ab  # noqa: E402
from js_common import fm  # noqa: E402

DAY = TODAY.isoformat()


@pytest.fixture
def js(tmp_path):
    return make_tree(tmp_path / "job-search")


def run(root, *args):
    return ab.main(["--root", str(root), "--today", DAY, *args])


def sheet(root, n=1):
    path = root / "state" / "apply-batches" / f"{DAY}-{n}.md"
    assert path.is_file(), f"no sheet {path}"
    meta, body = fm.load(path)
    return meta, body


def test_one_ready_slug_builds_lints_and_writes_the_sheet(js, capsys):
    assert run(js, SLUG) == 0
    assert (js / "applications" / SLUG / "apply-pack.md").is_file()
    meta, body = sheet(js)
    assert meta["type"] == "apply-batch" and meta["date"] == DAY and meta["tier"] == "mass"
    assert meta["counts"] == {"roles": 1, "ready": 1, "blocked": 0, "lint": 0, "refused": 0, "base_72": 0}
    row = next(line for line in body.splitlines() if SLUG in line and line.startswith("|"))
    assert "Acme Robotics" in row and "Applied AI Engineer" in row
    assert "https://boards.greenhouse.io/acme/jobs/123" in row
    assert f"{V}/{PDF_NAME}" in row and "READY" in row
    assert f"python3 scripts/tracker.py event {SLUG} applied --resume {V}/{PDF_NAME} --at" in body   # change 3
    assert "The agent submits (charter section 7)" in body and "Submit authority: none recorded" in body   # one yes, recorded
    assert "\u2014" not in body
    assert "1 ready" in capsys.readouterr().out


def test_a_second_batch_the_same_day_gets_the_next_number(js):
    run(js, SLUG)
    run(js, SLUG)
    sheet(js, 2)


def test_an_unknown_or_malformed_slug_is_refused_and_the_rest_still_runs(js, capsys):
    assert run(js, SLUG, "ghost-co-role-202609", "Bad Slug!") == 1
    meta, body = sheet(js)
    assert meta["counts"] == {"roles": 3, "ready": 1, "blocked": 0, "lint": 0, "refused": 2, "base_72": 0}
    ghost = next(line for line in body.splitlines() if "ghost-co-role-202609" in line and line.startswith("|"))
    assert "REFUSED" in ghost and "application.md not found" in ghost
    assert "not an application slug" in body


def test_a_blocked_pack_lists_its_blockers(js):
    set_front(js / "applications" / SLUG / "application.md", stage="applied")
    assert run(js, SLUG) == 1
    meta, body = sheet(js)
    assert meta["counts"]["blocked"] == 1 and meta["counts"]["ready"] == 0
    assert "already applied" in body
    assert "- none READY in this batch." in body and "tracker.py event" not in body


def test_dry_run_writes_nothing(js, capsys):
    assert run(js, SLUG, "--dry-run") == 0
    assert not (js / "applications" / SLUG / "apply-pack.md").exists()
    assert not (js / "state" / "apply-batches").exists()
    assert "dry-run" in capsys.readouterr().out


def test_slugs_from_a_file_and_the_tier_label(js, tmp_path):
    from test_reader_model import GOOD                  # a focused page needs a clean reader model
    write(js / "research" / "targets" / f"{SLUG}.md", GOOD)
    lst = tmp_path / "batch.txt"
    lst.write_text(f"# today's mass batch\n{SLUG}\n\n")
    assert run(js, "--file", str(lst), "--tier", "focused") == 0
    meta, _ = sheet(js)
    assert meta["tier"] == "focused" and meta["counts"]["roles"] == 1


def test_duplicate_slugs_run_once(js):
    run(js, SLUG, SLUG)
    meta, _ = sheet(js)
    assert meta["counts"]["roles"] == 1


def test_no_slugs_is_a_usage_error(js, capsys):
    assert run(js) == 2
    assert not (js / "state" / "apply-batches").exists()


# ----------------------------------------------------------------------------- tiers (resume-for "The three tiers")
import hashlib  # noqa: E402
import json  # noqa: E402

PDFN = ab.BASE_PDF["classic"]
BASE_PDF = b"%PDF-1.4 general base for tests\n"
BASE_YAML = "cv:\n  name: Rowan Quill Example\n"


def mass_ready(root, *, pin_ok=True):
    """No approved variant (review.md removed), but the general base is pinned and the slug has its matrix and JD."""
    (root / V / "review.md").unlink()
    base = root / "resumes" / "bases" / "general"
    base.mkdir(parents=True, exist_ok=True)
    (base / "resume.yaml").write_text(BASE_YAML)
    (base / PDFN).write_bytes(BASE_PDF)
    pdf_sha = hashlib.sha256(BASE_PDF if pin_ok else b"other").hexdigest()
    yaml_sha = hashlib.sha256(BASE_YAML.encode()).hexdigest()
    (base / "SHA256.txt").write_text(f"{pdf_sha}  {PDFN}\n{yaml_sha}  resume.yaml\n")
    write(base / "layout" / "classic" / "layout-report.json", json.dumps(      # a layout PASS for this PDF
        {"pdf": PDFN, "pdf_sha256": hashlib.sha256(BASE_PDF).hexdigest(), "verdict": "PASS",
         "passed": True, "failures": []}))
    write(root / f"research/targets/{SLUG}.matrix.yaml", "rows: []\n")
    write(root / f"applications/{SLUG}/jd.md", "---\ncompany: Acme Robotics\n---\n# JD\n")
    set_front(root / f"jobs/{JOB}.md", fit_score=4.2)     # E3 (scored): the matrix path; E2 has its own tests


@pytest.fixture
def rubric(monkeypatch):
    calls = []

    def fake(root, slug, today):
        calls.append(slug)
        return fake.result
    fake.result = (0, "")
    monkeypatch.setattr(ab, "rubric_on_base", fake)
    fake.calls = calls
    return fake


def test_mass_uses_base_72_when_no_variant_is_approved(js, rubric):
    mass_ready(js)
    assert run(js, SLUG) == 0
    meta, body = sheet(js)
    assert meta["counts"]["ready"] == 1 and meta["counts"]["base_72"] == 1
    row = next(line for line in body.splitlines() if SLUG in line and line.startswith("|"))
    assert f"resumes/bases/general/{PDFN}" in row and "READY" in row
    assert "general base" in row and "apply_pack.py answers" in row
    assert rubric.calls == [SLUG]


def test_focused_and_dream_never_fall_back_to_the_base(js, rubric):
    mass_ready(js)
    for tier in ("focused", "dream"):
        assert run(js, "--tier", tier, SLUG) == 1
    _, body = sheet(js, 2)
    assert "REFUSED" in body and "not editor-approved" in body and rubric.calls == []


def test_mass_without_a_matrix_is_refused_and_promoted(js, rubric):
    mass_ready(js)
    (js / f"research/targets/{SLUG}.matrix.yaml").unlink()
    assert run(js, SLUG) == 1
    _, body = sheet(js)
    assert "REFUSED" in body and "matrix" in body and "promote to focused" in body


def test_mass_with_a_failing_rubric_is_refused_and_promoted(js, rubric):
    mass_ready(js)
    rubric.result = (1, "B5 FAIL")
    assert run(js, SLUG) == 1
    _, body = sheet(js)
    assert "rubric_check on the general base failed" in body and "B5 FAIL" in body and "promote to focused" in body


def test_mass_refuses_a_base_pdf_that_does_not_match_its_pin(js, rubric):
    mass_ready(js, pin_ok=False)
    assert run(js, SLUG) == 1
    _, body = sheet(js)
    assert "SHA256.txt" in body and "REFUSED" in body


def test_mass_base_still_blocks_an_application_already_applied(js, rubric):
    mass_ready(js)
    set_front(js / "applications" / SLUG / "application.md", stage="applied")
    assert run(js, SLUG) == 1
    meta, body = sheet(js)
    assert meta["counts"]["blocked"] == 1 and "already applied" in body


def test_an_approved_variant_wins_over_the_base_in_mass(js, rubric):
    assert run(js, SLUG) == 0
    _, body = sheet(js)
    assert f"{V}/{PDF_NAME}" in body and rubric.calls == []


def test_failed_predicates_reads_rubric_checks_real_report_shape():
    report = {"predicates": [{"id": "B3", "status": "PASS"}, {"id": "B5", "status": "FAIL"}], "failed": ["B5"]}
    assert ab.failed_predicates(json.dumps(report)) == "B5 FAIL"
    assert ab.failed_predicates(json.dumps({"predicates": report["predicates"]})) == "B5 FAIL"
    assert ab.failed_predicates("not json") == ""


def test_the_base_72_rubric_runs_with_tier_mass(js, monkeypatch):
    import rubric_check
    seen = {}

    def fake_main(argv):
        seen["argv"] = list(argv)
        print(json.dumps({"predicates": [], "failed": []}))
        return 0
    monkeypatch.setattr(rubric_check, "main", fake_main)
    assert ab.rubric_on_base(js, SLUG, TODAY) == (0, "")
    argv = seen["argv"]
    assert argv[argv.index("--tier") + 1] == "mass"


def test_a_batch_never_logs_an_application_event(js):
    """The routine's rule: pages and packs only, never a submit: the event log is untouched."""
    path = js / "applications" / SLUG / "application.md"
    before = path.read_text()
    assert run(js, SLUG) == 0
    assert path.read_text() == before
