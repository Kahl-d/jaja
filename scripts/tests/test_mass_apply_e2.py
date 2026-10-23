"""apply_batch.py, the E2 mass path (charter): a base-resume application needs no fit.md, no matrix and no
per-company visa file; it is gated on fit_gates, not applied before, a clean answers bank and the employer's own site.
One recorded batch yes (approvals.py decide) is the submit authority; the sheet is its record. Each row records
base_order. Fixtures reuse the fictional tree of test_apply_pack.py (Rowan Quill Example)."""
from __future__ import annotations

import hashlib
import json

import pytest

from test_apply_pack import JOB, SLUG, TODAY, V, make_tree, set_front, write  # noqa: F401

import apply_batch as ab  # noqa: E402
import approvals  # noqa: E402  (the kernel's, on sys.path through js_common)
from js_common import fm  # noqa: E402

DAY = TODAY.isoformat()
BASE_PDF = b"%PDF-1.4 base for tests\n"
BASE_YAML = "cv:\n  name: Rowan Quill Example\n  sections:\n    education: [a]\n    experience: [b]\nsidecar:\n  base_of: null\n"
EXP_YAML = "cv:\n  name: Rowan Quill Example\n  sections:\n    experience: [b]\n    education: [a]\nsidecar:\n  base_of: general\n"
EXP_PDF = b"%PDF-1.4 experience first\n"


def _pin(folder, files):
    folder.mkdir(parents=True, exist_ok=True)
    lines = []
    for name, data in files.items():
        (folder / name).write_bytes(data)
        lines.append(f"{hashlib.sha256(data).hexdigest()}  {name}")
    (folder / "SHA256.txt").write_text("\n".join(lines) + "\n")


def _layout(folder, pdf_bytes, name="AlexExample_Resume.pdf"):
    write(folder / "layout" / "classic" / "layout-report.json", json.dumps(
        {"pdf": name, "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(), "verdict": "PASS", "passed": True,
         "failures": []}))


@pytest.fixture
def js(tmp_path, monkeypatch):
    """No approved variant, the base pinned with its layout PASS, NO matrix, NO legal/checks file, no fit score."""
    root = make_tree(tmp_path / "job-search")
    (root / V / "review.md").unlink()
    (root / "legal" / "checks" / "acme.md").unlink()
    base = root / "resumes" / "bases" / "general"
    _pin(base, {"resume.yaml": BASE_YAML.encode(), "AlexExample_Resume.pdf": BASE_PDF})
    _layout(base, BASE_PDF)
    monkeypatch.setattr(ab, "answers_clean", lambda r, f=None: 0)
    monkeypatch.setattr(ab, "row_family", lambda r, t: "ai-engineer")
    monkeypatch.setattr(ab, "base_order_for", lambda key: "education-first")
    return root


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """approvals.decide stamps decided_at from the clock: the owner says yes on the run's day, 08:30 Pacific."""
    import datetime as dt
    monkeypatch.setattr(approvals, "_now", lambda: dt.datetime(TODAY.year, TODAY.month, TODAY.day, 8, 30,
                                                                tzinfo=dt.timezone(dt.timedelta(hours=-7))))


def run(root, *args):
    return ab.main(["--root", str(root), "--today", DAY, *args])


def sheet(root, n=1):
    return fm.load(root / "state" / "apply-batches" / f"{DAY}-{n}.md")


def row_of(body):
    return next(line for line in body.splitlines() if SLUG in line and line.startswith("|"))


def test_an_e2_base_row_is_ready_with_no_matrix_no_fit_and_no_visa_file(js):
    assert not (js / f"research/targets/{SLUG}.matrix.yaml").exists()
    assert run(js, SLUG) == 0
    meta, body = sheet(js)
    row = row_of(body)
    assert "READY" in row and "resumes/bases/general/AlexExample_Resume.pdf" in row
    assert "E2 base resume (education-first)" in row
    assert "visa check pending" in row and "I hold work authorization through a student-training extension." in row
    assert meta["base_order"] == {SLUG: "education-first"}
    app, _ = fm.load(js / "applications" / SLUG / "application.md")
    assert app["base_order"] == "education-first"                       # the order used, recorded


def test_dry_run_writes_no_base_order(js):
    assert run(js, SLUG, "--dry-run") == 0
    assert "base_order" not in fm.load(js / "applications" / SLUG / "application.md")[0]


@pytest.mark.parametrize("stage", ["applied", "screen", "rejected"])
def test_an_application_already_applied_is_blocked(js, stage):
    set_front(js / "applications" / SLUG / "application.md", stage=stage)
    assert run(js, SLUG) == 1
    assert "already applied" in row_of(sheet(js)[1])


def test_a_gate_fail_is_refused_and_any_us_location_passes(js):
    set_front(js / f"jobs/{JOB}.md", location="Austin, TX")             # any US location passes
    assert run(js, SLUG) == 0
    set_front(js / f"jobs/{JOB}.md", location="London, UK")
    assert run(js, SLUG) == 1
    row = row_of(sheet(js, 2)[1])
    assert "REFUSED" in row and "fit_gates fail" in row


def test_a_staffing_employer_is_refused(js):
    set_front(js / f"jobs/{JOB}.md", company="Contoso Talent Solutions")
    assert run(js, SLUG) == 1
    assert "staffing, recruiting or talent marketplace" in row_of(sheet(js)[1])


def test_a_dirty_answers_bank_blocks(js, monkeypatch):
    monkeypatch.setattr(ab, "answers_clean", lambda r, f=None: 1)
    assert run(js, SLUG) == 1
    assert "answers bank not clean" in row_of(sheet(js)[1])


def test_the_real_answers_check_runs_once_per_root(js, monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(ab, "base_order_for", lambda key: "education-first")
    ab._ANSWERS.clear()
    assert ab.answers_clean(js) == 2                                     # no bank files: missing is not clean
    assert run(js, SLUG) == 1 and "no focus family" in row_of(sheet(js)[1])    # no lens: no family, no bank to trust


def test_a_one_click_link_is_the_owners_click(js):
    set_front(js / f"jobs/{JOB}.md", apply_url="https://www.linkedin.com/jobs/view/123")
    assert run(js, SLUG) == 1
    meta, body = sheet(js)
    assert "CLICK" in row_of(body) and "LinkedIn Easy Apply" in body and "## Your clicks" in body
    assert meta["clicks"] == 1 and meta["counts"]["ready"] == 0


def test_a_blocked_visa_check_still_blocks(js):
    write(js / "legal/checks/acme.md", "---\ncompany: acme\nverdict: blocked\ne_verify: yes\n---\nx\n")
    assert run(js, SLUG) == 1
    assert "verdict blocked" in row_of(sheet(js)[1])
    write(js / "legal/checks/acme.md", "---\ncompany: acme\nverdict: clear\ne_verify: no\n---\nx\n")
    assert run(js, SLUG) == 1
    assert "e_verify no" in row_of(sheet(js, 2)[1])


def test_e3_and_above_need_a_scored_fit(js):
    assert run(js, "--effort", "E3", SLUG) == 1
    row = row_of(sheet(js)[1])
    assert "REFUSED" in row and "needs a scored fit" in row and "promote to focused" in row


def _ab(root, state="on", folder=None):
    write(root / "targets" / "mass.yaml", f"base_ab: {state}\n" + (f"experience_first: {folder}\n" if folder else ""))


def test_with_the_ab_switch_off_every_row_is_education_first(js, monkeypatch):
    monkeypatch.setattr(ab, "base_order_for", lambda key: "experience-first")
    for state in (None, "off"):                                          # no targets/mass.yaml, then base_ab: off
        if state:
            _ab(js, state)
        assert run(js, SLUG) == 0
    for n in (1, 2):
        row = row_of(sheet(js, n)[1])
        assert "education-first" in row and "TODO" not in row
    assert fm.load(js / "applications" / SLUG / "application.md")[0]["base_order"] == "education-first"


def test_the_experience_first_order_without_a_usable_copy_falls_back_with_a_todo(js, monkeypatch):
    monkeypatch.setattr(ab, "base_order_for", lambda key: "experience-first")
    _ab(js)
    assert run(js, SLUG) == 0
    row = row_of(sheet(js)[1])
    assert "TODO" in row and "education-first used" in row
    assert fm.load(js / "applications" / SLUG / "application.md")[0]["base_order"] == "education-first"


def test_the_experience_first_copy_is_used_when_it_is_order_only_pinned_and_laid_out(js, monkeypatch):
    monkeypatch.setattr(ab, "base_order_for", lambda key: "experience-first")
    _ab(js)
    copy = js / "resumes" / "bases" / "general-experience-first"
    _pin(copy, {"resume.yaml": EXP_YAML.encode(), "AlexExample_Resume.pdf": EXP_PDF})
    _layout(copy, EXP_PDF)
    assert run(js, SLUG) == 0
    assert "resumes/bases/general-experience-first/AlexExample_Resume.pdf" in row_of(sheet(js)[1])
    assert fm.load(js / "applications" / SLUG / "application.md")[0]["base_order"] == "experience-first"
    _pin(copy, {"resume.yaml": EXP_YAML.replace("[b]", "[c]").encode(), "AlexExample_Resume.pdf": EXP_PDF})
    assert run(js, SLUG) == 0                                            # a word changed: not an order-only copy
    assert "not the base's words in another order" in row_of(sheet(js, 2)[1])


def test_a_signed_variant_folder_can_be_the_experience_first_copy(js, monkeypatch):
    monkeypatch.setattr(ab, "base_order_for", lambda key: "experience-first")
    folder = "resumes/variants/base-experience-first-test"
    _ab(js, "on", folder)
    v = js / folder
    write(v / "resume.yaml", EXP_YAML)
    (v / "send").mkdir(parents=True)
    (v / "send" / "AlexExample_Resume.pdf").write_bytes(EXP_PDF)
    write(v / "send" / "layout-report.json", json.dumps(
        {"pdf": "AlexExample_Resume.pdf", "pdf_sha256": hashlib.sha256(EXP_PDF).hexdigest(), "verdict": "PASS",
         "passed": True, "failures": []}))
    review = {"verdict": "approved", "variant_hash": hashlib.sha256(EXP_YAML.encode()).hexdigest(),
              "pdf_sha256": hashlib.sha256(EXP_PDF).hexdigest()}
    write(v / "review.md", fm.dump(review, "# review\n"))
    assert run(js, SLUG) == 0
    assert f"{folder}/send/AlexExample_Resume.pdf" in row_of(sheet(js)[1])
    write(v / "review.md", fm.dump({**review, "verdict": "revise"}, "# review\n"))
    assert run(js, SLUG) == 0
    assert "not an approved verdict" in row_of(sheet(js, 2)[1])


def test_the_ab_split_is_deterministic_and_uses_both_orders():
    picks = [ab.base_order_for(f"job-{i}") for i in range(200)]
    assert picks == [ab.base_order_for(f"job-{i}") for i in range(200)]
    assert 70 < picks.count("education-first") < 130


def test_a_missing_base_layout_report_is_measured_not_rendered(js, monkeypatch):
    import page_layout_check
    (js / "resumes/bases/general/layout/classic/layout-report.json").unlink()
    seen = []

    def fake(pdf, out_dir=None, max_pages=None, checked_at=None):
        seen.append(pdf)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "layout-report.json").write_text(json.dumps(
            {"pdf": pdf.name, "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(), "verdict": "PASS",
             "passed": True, "failures": []}))
        return {}
    monkeypatch.setattr(page_layout_check, "check_pdf", fake)
    assert run(js, SLUG) == 0
    assert seen == [js / "resumes/bases/general/AlexExample_Resume.pdf"]
    assert (js / "resumes/bases/general/layout/classic/layout-report.json").is_file()
    assert (js / "resumes/bases/general/resume.yaml").read_text() == BASE_YAML    # the base itself untouched


# ----------------------------------------------------------------------------- the one yes
def _yes(os_root, items, quote="yes 1-12, no 4"):
    aid, path, _ = approvals.decide(os_root, items, "chat", quote, "yes", target="mass batch",
                                    lines=[f"{i} · a role" for i in items], today=TODAY)
    return aid, path


def test_a_recorded_yes_covering_the_job_marks_submit_and_prints_the_receipt(js, tmp_path):
    os_root = tmp_path / "workspace"
    aid, _ = _yes(os_root, [JOB])
    assert run(js, "--approval", aid, "--os-root", str(os_root), SLUG) == 0
    meta, body = sheet(js)
    assert meta["approval"] == aid and f"READY · submit on {aid}" in row_of(body)
    assert f'Submit authority: {aid}, the owner\'s words: "yes 1-12, no 4".' in body
    assert f"approvals.py receipt work/approvals/{aid}.md --result sent --message-id \"{SLUG}:" in body
    assert "never a second approval" in body


def test_a_yes_that_does_not_list_the_row_submits_nothing(js, tmp_path):
    os_root = tmp_path / "workspace"
    aid, _ = _yes(os_root, ["some-other-job"])
    run(js, "--approval", aid, "--os-root", str(os_root), SLUG)
    row = row_of(sheet(js)[1])
    assert "submit on" not in row and f"not covered by {aid}: no submit" in row


def test_a_yes_from_an_earlier_day_does_not_cover_today(js, tmp_path):
    os_root = tmp_path / "workspace"
    aid, _ = _yes(os_root, [JOB])
    ab.main(["--root", str(js), "--today", "2026-09-27", "--approval", aid, "--os-root", str(os_root), SLUG])
    _, body = fm.load(js / "state" / "apply-batches" / "2026-09-27-1.md")
    assert "the yes was given on 2026-09-26, not today" in body and "submit on" not in row_of(body)


def test_an_expired_or_missing_yes_is_refused(js, tmp_path):
    os_root = tmp_path / "workspace"
    aid, _ = _yes(os_root, [JOB])
    ab.main(["--root", str(js), "--today", "2026-10-30", "--approval", aid, "--os-root", str(os_root), SLUG])
    _, body = fm.load(js / "state" / "apply-batches" / "2026-10-30-1.md")
    assert "REFUSED" in body.split("Submit authority:", 1)[1].splitlines()[0] and "submit on" not in row_of(body)
    run(js, "--approval", "A-2026-0999", "--os-root", str(os_root), SLUG)
    assert "no approval file" in sheet(js)[1]


def test_an_outbox_approval_decided_in_chat_passes_the_outbox_checks(tmp_path):
    """The outbox sends use the same recorded yes: decide on an outbox batch, then approval_problems is empty."""
    import js_comms_util as cu
    roots = cu.Roots(js=tmp_path / "js", os=tmp_path / "workspace")
    items = [cu.ApprovalItem(id=f"O-2026-000{n}", channel="email", target=f"p{n}@example.com", body=f"Hi {n}",
                             subject="Hello") for n in (1, 2)]
    aid, path = cu.write_approval(roots, action="send-email", items=items, requested_by="role:messenger", why="w",
                                  effect="e", today=TODAY)
    assert cu.approval_problems(cu.parse_approval(path), TODAY)       # pending: not yet
    approvals.decide(roots.os, ["O-2026-0001"], "chat", "send the first one", "yes", approval=aid,
                     today=TODAY)
    appr = cu.parse_approval(path)
    assert cu.approval_problems(appr, TODAY) == []
    assert appr.decisions == {"O-2026-0001": True, "O-2026-0002": False}


def test_the_application_schema_carries_base_order_and_lint_checks_its_enum(js):
    import lint
    import yaml
    import js_common as jc
    schema = yaml.safe_load((__import__("agency_paths").SCHEMAS_DIR / "application.yaml").read_text(encoding="utf-8"))
    assert "base_order" in schema["fields"]
    assert schema["enums"]["base_order"] == ["education-first", "experience-first"]
    run(js, SLUG)
    meta, _ = fm.load(js / "applications" / SLUG / "application.md")
    assert not [f for f in lint.validate(meta, schema, "a.md", "application") if "base_order" in str(f)]
    bad = lint.validate({**meta, "base_order": "skills-first"}, schema, "a.md", "application")
    assert any("base_order" in str(f) for f in bad)
