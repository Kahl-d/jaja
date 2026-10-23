"""apply_pack.py: one apply pack per editor-approved application, every answer copied from a record.

Fixtures describe a FICTIONAL person (Rowan Quill Example); example.com and 555-01xx are reserved for
fiction. Two guard tests read the real department files read-only: the real PROFILE-HOLDS.md (so no
static text in the pack can ever carry a held string) and the real profile (the answer bank prints no
held text and no em-dash). The guard trees also get a fictional profile/private-holds.txt (never the real
one), so the private-holds path is exercised too and its held strings print masked.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import apply_pack as ap  # noqa: E402
import held_safe as hs  # noqa: E402  (tests/: compare real holds without printing a private one)
from js_common import fm  # noqa: E402

from layout import JS as JS_REAL  # noqa: E402
from layout import OS_REAL  # noqa: E402
TODAY = date(2026, 9, 26)
SLUG = "acme-applied-ai-engineer-202609"
JOB = "acme-r123"
V = f"resumes/variants/{SLUG}"
PDF_NAME = "Rowan_Quill_Example_Resume.pdf"
PDF_BYTES = b"%PDF-1.4 fictional resume for tests\n"

FACTS = {
    "identity": {
        "full_name": "Rowan Quill Example",
        "emails": {"job_search_primary": {"address": "rowan.example@example.com", "status": "confirmed"},
                   "not_for_applications": [{"address": "old.rowan@example.com", "why": "old"}]},
        "phone": {"value": "+1 (000) 010-0199", "status": "confirmed"},
        "location": {"value": "Oakland, CA", "status": "confirmed"},
        "links": {
            "linkedin": {"value": "linkedin.com/in/rowan-example", "status": "confirmed"},
            "github": {"value": "github.com/rowan-example", "status": "confirmed"},
            "website": {"value": "rowan-site.example.com", "status": "needs-confirmation",
                        "note": "on old resumes; not verified live (C45)"},
        },
        "work_authorization": {"value": "internal only", "discretion": "internal-only"},
    },
    "education": [
        {"id": "edu-ms", "degree": "Master of Arts", "major": "Applied Machine Learning",
         "school": "Harborview State University", "location": "Seattle, WA", "start": "2024-08",
         "conferred": date(2026, 5, 15), "gpa": "3.90 (30 units)", "status": "confirmed"},
        {"id": "edu-bs", "degree": "Bachelor of Science", "major": "Computer Science",
         "school": "Lakeside Institute of Technology", "start": "2016-08", "end": "2020-05",
         "status": "needs-confirmation", "conflict": "C14"},
    ],
    "employment": [
        {"id": "exp-northwind", "org": "Northwind Robotics", "title": "Machine Learning Engineer",
         "title_render": "Machine Learning Engineer", "start": "2025-01", "end": "present", "kind": "experience",
         "status": "confirmed", "corpus": "corpus/northwind/README.md"},
        {"id": "exp-contoso", "org": "Contoso Health (Contoso Inc)",
         "title": "Data Scientist (old resume says 'Data Wrangler' - C15)", "title_render": "Data Scientist",
         "start": "2021-06", "end": "2024-12", "kind": "experience", "status": "needs-confirmation",
         "corpus": "corpus/contoso/README.md"},
        {"id": "exp-lab", "org": "Vision Lab (advisor Dr. Example), Harborview State University",
         "title": "Teaching Assistant", "title_render": "Teaching Assistant",
         "start": "2023-01", "end": "2024-05", "kind": "experience", "status": "confirmed",
         "corpus": "corpus/lab/README.md"},
        {"id": "exp-ta", "org": "Harborview State University, Department of Computer Science",
         "title": "Teaching Assistant", "title_render": "Teaching Assistant", "start": "2023-08",
         "end": "2024-05", "kind": "teaching", "status": "confirmed"},
        {"id": "exp-intern", "org": "Tinyco", "title": "Product Team (internship, presumed)",
         "title_render": "Product Team (internship)", "start": "2019-06", "end": "2019-08",
         "kind": "experience", "status": "needs-confirmation"},
    ],
    "conflicts": [
        {"id": "C14", "topic": "bachelor month form", "status": "awaiting-owner"},
        {"id": "C15", "topic": "Contoso title", "status": "awaiting-owner"},
        {"id": "C45", "topic": "Personal website", "status": "awaiting-owner"},
    ],
}

HOLDS = """---
title: Profile holds (fixture)
---

# Profile holds

| Conflict | Held text (exact) | Where it came from | Why held | What unlocks it |
|---|---|---|---|---|
| C15 | "Data Wrangler" | old resume | title conflict | the owner rules |
| C07 | "Contoso Inc" | NDA | name variant | the owner rules |
| C45 | "rowan-site.example.com" | old resumes | not verified live | the owner checks the URL |
| C99 | "Mars Colony" | test | test hold | never |

## Skills removed

| Term | Why held | What unlocks it |
|---|---|---|
| Kubernetes | no corpus narrative | a project that used it |
"""

# Flow mappings exactly like the real profile/skills-taxonomy.yaml: an unquoted comma inside a skill name
# splits it into extra null keys ("SQL (Postgres" + "SQLite" + "DuckDB)"), which the reader must rejoin.
TAXONOMY = """\
schema_version: 1
groups:
  languages:
    - {skill: Python, level: expert, last_used: 2026-09, evidence: [ev-nw-001, ev-ct-002]}
    - {skill: SQL (Postgres, SQLite, DuckDB), level: strong, last_used: 2026-09, evidence: [ev-ct-002]}
    - {skill: Rust, level: familiar, last_used: 2026-04, evidence: [ev-x-001], confirm: true}
  ml:
    - {skill: PyTorch, level: strong, last_used: 2026-05, evidence: [ev-lab-003]}
    - {skill: Kubernetes, level: strong, last_used: 2026-01, evidence: [ev-nw-002]}
    - {skill: Swift, level: strong, last_used: 2026-08, evidence: [ev-nw-001], discretion: internal-only}
    - {skill: Next.js, level: strong, last_used: 2026-09, evidence: [ev-side-001]}
    - {skill: MATLAB, level: working, last_used: 2024-01, evidence: [ev-lab-003]}
"""

LEGAL = """---
title: Legal timers (fixture)
updated: 2026-09-25
---
Information only, not legal advice; questions for an attorney or the DSO are in `legal/questions.md`.

## Clocks

| clock | date |
|---|---|
| automatic extension end | 2027-03-03 |

## Answer scripts

- Form: "Are you legally authorized to work in the US?" → **Yes**. "Will you now or in the future require sponsorship?" → **Yes**.
- Recruiter, spoken (under a minute): "Yes, in the future, not to start. I hold work authorization through a student-training extension and
  working under the automatic extension while it is pending."
- Written (3 sentences): "Yes, I will need sponsorship in the future, not to start. I hold work authorization through a student-training extension
  that is pending renewal. The next H-1B registration is expected around March 2027."

## Watch list

- nothing new
"""

WRITTEN = ("Yes, I will need sponsorship in the future, not to start. I hold work authorization through a student-training extension "
           "that is pending renewal. The next H-1B registration is expected around March 2027.")

RESUME = {
    "sidecar": {"lens": "applied-ml", "base_of": "general", "job": f"jobs/{JOB}.md"},   # one-base model
    "cv": {
        "name": "Rowan Quill Example",
        "phone": "+1 (000) 010-0199",
        "email": "rowan.example@example.com",
        "sections": {
            "Education": [{"institution": "Harborview State University", "area": "Applied Machine Learning",
                           "degree": "Master of Arts", "location": "Seattle, WA",
                           "start_date": "2024-08", "end_date": "2026-05"}],
            "Experience": [
                {"company": "Northwind Robotics", "position": "Machine Learning Engineer",
                 "location": "Oakland, CA", "start_date": "2025-01", "end_date": "present",
                 "highlights": ["Built a thing for a team <!-- ev:ev-nw-001 -->"]},
                {"company": "Contoso Health", "position": "Data Scientist", "location": "Seattle, WA",
                 "start_date": "2021-06", "end_date": "2024-12",
                 "highlights": ["Built another thing <!-- ev:ev-ct-002 -->"]},
            ],
        },
    },
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def doc(meta: dict, body: str = "") -> str:
    return fm.dump(meta, body)


def make_tree(root: Path) -> Path:
    """A complete, READY-able department tree for one Greenhouse application."""
    p = root / "profile"
    write(p / "facts.yaml", yaml.safe_dump(FACTS, sort_keys=False))
    write(p / "PROFILE-HOLDS.md", HOLDS)
    write(p / "skills-taxonomy.yaml", TAXONOMY)
    write(p / "corpus/northwind/README.md", "Northwind work [ev:ev-nw-001] and more [ev:ev-nw-002].\n")
    write(p / "corpus/contoso/README.md", "Contoso work [ev:ev-ct-002].\n")
    write(p / "corpus/lab/README.md", "Lab work [ev:ev-lab-003].\n")
    write(root / "legal/timers.md", LEGAL)
    write(root / "legal/checks/acme.md", doc(
        {"company": "acme", "legal_entity": "Acme Robotics Inc", "e_verify": "yes", "stance": "sponsors",
         "verdict": "clear", "checked_at": "2026-09-25"},
        "Information only, not legal advice; questions for an attorney or the DSO are in `legal/questions.md`.\n"))
    write(root / f"jobs/{JOB}.md", doc(
        {"id": JOB, "company": "Acme Robotics", "company_slug": "acme", "title": "Applied AI Engineer",
         "req_id": "r123", "source": "greenhouse", "source_url": "https://boards.greenhouse.io/acme/jobs/123",
         "apply_url": "https://boards.greenhouse.io/acme/jobs/123", "ats_vendor": "greenhouse",
         "posted_at": "2026-09-20", "location": "San Francisco, CA", "remote": "hybrid", "status": "tailoring",
         "application": f"[[{SLUG}]]"},
        "# Applied AI Engineer at Acme Robotics\n"))
    write(root / f"applications/{SLUG}/application.md", doc(
        {"id": SLUG, "job": f"[[{JOB}]]", "company": "Acme Robotics", "company_slug": "acme",
         "title": "Applied AI Engineer", "stage": "tailored", "round": 0, "applied_at": None, "applied_via": None,
         "resume_variant": None, "cover_variant": None, "prospects": [], "thread_ids": [],
         "outcome": "pending", "created": "2026-09-24", "updated": "2026-09-25"},
        "# Applied AI Engineer at Acme Robotics\n\n## Events\n"
        "- 2026-09-24T12:00:00Z · fit_checked · manual · conf=1.00 · created\n"
        "- 2026-09-25T12:00:00Z · tailored · manual · conf=1.00 · review approved\n"))
    vdir = root / V
    resume_text = yaml.safe_dump(RESUME, sort_keys=False)
    write(vdir / "resume.yaml", resume_text)
    (vdir / PDF_NAME).write_bytes(PDF_BYTES)
    write(vdir / "meta.yaml", yaml.safe_dump(
        {"lens": "applied-ml", "base_of": "general", "job": f"jobs/{JOB}.md", "built_at": "2026-09-24T12:00:00",
         "engine": "rendercv", "pages": 1, "max_pages": 1, "status": "ok", "failures": [], "hash": "abc",
         "source": f"{V}/resume.yaml", "pdf": PDF_NAME, "pdf_sha256": sha(PDF_BYTES), "docx": None},
        sort_keys=False))
    write(vdir / "ats-report.json", json.dumps(
        {"pdf": f"{V}/{PDF_NAME}", "checked_at": "2026-09-24T12:30:00", "passed": True, "vendor": "greenhouse",
         "failures": [], "warnings": []}, indent=2))
    write(vdir / "layout-report.json", json.dumps(          # page_layout_check.py PASS for this exact PDF
        {"pdf": PDF_NAME, "pdf_sha256": sha(PDF_BYTES), "verdict": "PASS", "passed": True, "failures": []}, indent=2))
    write(vdir / "review.md", doc(
        {"target": SLUG, "variant_hash": sha(resume_text.encode()), "round": 2, "verdict": "approved",
         "reviewer": "resume-editor@0.1.1", "signed_at": "2026-09-25T10:00:00"},
        "## Findings\n\n### E-01 · Medium · S4 vague verb\n- state: FIXED\n"))
    write(vdir / "recruiter.md", doc(
        {"type": "recruiter-review", "slug": SLUG, "resume_sha256": sha(resume_text.encode()), "verdict": "APPROVE",
         "layout": "PASS: balanced, headers clear, fills the page"},
        "Approved for the req.\n"))
    write(vdir / "cover.md", "Dear Acme team,\n\nA short cover letter.\n\nRowan Quill Example\n")
    write(vdir / "why-us.md", "Why Acme: a short note.\n")
    write(root / "state/accounts.yaml", ap.ACCOUNTS_HEADER + yaml.safe_dump(
        {"schema_version": 1, "updated": "2026-09-25", "accounts": []}, sort_keys=False))
    return root


@pytest.fixture
def js(tmp_path) -> Path:
    return make_tree(tmp_path / "job-search")


def build(root: Path, slug: str = SLUG, today: date = TODAY) -> int:
    return ap.main(["--root", str(root), "build", slug, "--today", today.isoformat()])


def lint(root: Path, slug: str = SLUG) -> int:
    return ap.main(["--root", str(root), "lint", slug])


def pack_path(root: Path, slug: str = SLUG) -> Path:
    return root / "applications" / slug / "apply-pack.md"


def pack(root: Path, slug: str = SLUG) -> tuple[dict, str, str]:
    text = pack_path(root, slug).read_text(encoding="utf-8")
    meta, body = fm.loads(text)
    return meta, body, text


def section(body: str, heading: str) -> str:
    """The text under `## heading` up to the next `## ` heading."""
    marker = f"## {heading}"
    assert marker in body, f"missing section {marker!r}"
    tail = body.split(marker, 1)[1]
    return tail.split("\n## ", 1)[0]


def set_front(path: Path, **changes) -> None:
    meta, body = fm.load(path)
    meta.update(changes)
    path.write_text(fm.dump(meta, body), encoding="utf-8")


def set_private(root: Path, data: dict) -> None:
    write(root / "profile/private.yaml", yaml.safe_dump(data, sort_keys=False))


# ----------------------------------------------------------------------------- build: the happy path
def test_build_writes_a_ready_pack_from_records(js, capsys):
    assert build(js) == 0
    out = capsys.readouterr().out
    assert out.startswith("READY")
    meta, body, text = pack(js)
    assert meta["type"] == "apply-pack"
    assert meta["status"] == "ready" and meta["blockers"] == []
    assert meta["application"] == SLUG and meta["job"] == JOB and meta["built_on"] == "2026-09-26"
    assert meta["portal"] == "greenhouse"
    resume_sha = sha((js / V / "resume.yaml").read_bytes())
    assert meta["variant_hash"] == resume_sha
    assert meta["pdf"] == f"{V}/{PDF_NAME}" and meta["pdf_sha256"] == sha(PDF_BYTES)
    # the posting and the contact block, as the records give them
    posting = section(body, "Posting")
    assert "Acme Robotics" in posting and "Applied AI Engineer" in posting
    assert "https://boards.greenhouse.io/acme/jobs/123" in posting
    contact = section(body, "Contact block")
    for value in ("Rowan Quill Example", "rowan.example@example.com", "+1 (000) 010-0199", "Oakland, CA",
                  "https://linkedin.com/in/rowan-example", "https://github.com/rowan-example"):
        assert value in contact, value
    assert "old.rowan@example.com" not in text                       # never a not-for-applications email
    assert "internal only" not in text                                # facts work_authorization never rendered
    # legal answers verbatim, with their source
    legal = section(body, "Work authorization and sponsorship")
    assert "| Are you legally authorized to work in the US? | Yes | legal/timers.md ## Answer scripts |" in legal
    assert "| Will you now or in the future require sponsorship? | Yes | legal/timers.md ## Answer scripts |" in legal
    assert WRITTEN in legal
    # the resume, its approval and the ATS verdict
    files = section(body, "Resume, cover letter and why-us")
    assert f"{V}/{PDF_NAME}" in files and resume_sha in files
    assert "approved" in files and "round 2" in files
    assert "PASS" in files and "2026-09-24T12:30:00" in files
    assert f"{V}/cover.md" in files and f"{V}/why-us.md" in files
    # work history comes from the approved resume, dates as ISO months
    history = section(body, "Work history and education (from the approved resume)")
    assert "| Machine Learning Engineer | Northwind Robotics | Oakland, CA | 2025-01 | present |" in history
    assert "Harborview State University" in history
    # vendor notes and the checklist
    assert "## Portal notes: Greenhouse" in body
    checklist = section(body, "Checklist (the owner ticks)")
    assert "- [ ] " in checklist and "the owner clicks Submit" in checklist
    assert "—" not in text
    assert set(meta["files"]) >= {f"applications/{SLUG}/application.md", f"jobs/{JOB}.md", "profile/facts.yaml",
                                  "legal/timers.md", f"{V}/review.md", f"{V}/{PDF_NAME}", f"{V}/cover.md"}
    assert meta["files"] == sorted(meta["files"])


def test_build_is_deterministic(js):
    assert build(js) == 0
    first = pack_path(js).read_bytes()
    assert build(js) == 0
    assert pack_path(js).read_bytes() == first


def test_lint_passes_on_a_fresh_pack(js, capsys):
    assert build(js) == 0
    capsys.readouterr()
    assert lint(js) == 0
    assert "clean" in capsys.readouterr().out


# ----------------------------------------------------------------------------- legal-visa answers
def test_legal_answers_are_copied_verbatim_including_unusual_wording(js):
    text = LEGAL.replace('"Are you legally authorized to work in the US?" → **Yes**',
                         '"Are you, today, legally authorised to work in the US?" → **Yes, through a student-training extension**')
    write(js / "legal/timers.md", text)
    assert build(js) == 0
    legal = section(pack(js)[1], "Work authorization and sponsorship")
    assert "| Are you, today, legally authorised to work in the US? | Yes, through a student-training extension |" in legal


def test_parse_legal_accepts_an_ascii_arrow_and_reads_both_scripts():
    parsed = ap.parse_legal(LEGAL.replace("→", "->"))
    assert parsed.authorization == ("Are you legally authorized to work in the US?", "Yes")
    assert parsed.sponsorship == ("Will you now or in the future require sponsorship?", "Yes")
    assert parsed.written == WRITTEN
    assert parsed.spoken.startswith("Yes, in the future, not to start. I hold work authorization through a student-training extension and working")


def test_parse_legal_reads_plain_lines_sub_headings_and_tables():
    plain = """## Answer scripts

Form: "Are you legally authorized to work in the US?" → **Yes**. "Will you now or in the future require sponsorship?" → **Yes**.

### Written (3 sentences)
Yes, I will need sponsorship in the future, not to start. I hold work authorization through a student-training extension
that is pending renewal.

### Recruiter, spoken
"Yes, in the future, not to start."

## Watch list
- "Is this a pair?" → **No, it is outside the section**
"""
    parsed = ap.parse_legal(plain)
    assert parsed.authorization == ("Are you legally authorized to work in the US?", "Yes")
    assert parsed.sponsorship == ("Will you now or in the future require sponsorship?", "Yes")
    assert parsed.written == ("Yes, I will need sponsorship in the future, not to start. I hold work authorization through a student-training "
                              "extension that is pending renewal.")
    assert parsed.spoken == "Yes, in the future, not to start."
    assert len(parsed.pairs) == 2
    table = ("## Answer scripts\n\n| Question | Answer |\n|---|---|\n"
             "| Are you legally authorized to work in the US? | Yes |\n"
             "| Will you now or in the future require sponsorship? | Yes |\n")
    parsed = ap.parse_legal(table)
    assert parsed.authorization[1] == "Yes" and parsed.sponsorship[1] == "Yes"
    assert ap.parse_legal("## Clocks\n- nothing\n").state == "no-section"


def test_missing_legal_file_marks_both_fields_and_exits_1(js, capsys):
    (js / "legal/timers.md").unlink()
    assert build(js) == 1
    assert capsys.readouterr().out.startswith("BLOCKED")
    meta, body, _ = pack(js)
    assert meta["status"] == "blocked"
    legal = section(body, "Work authorization and sponsorship")
    assert legal.count(ap.LEGAL_MISSING) >= 2
    assert any("legal-visa answer missing" in b for b in meta["blockers"])
    assert lint(js) == 1


def test_a_missing_sponsorship_pair_is_missing_not_guessed(js):
    write(js / "legal/timers.md", LEGAL.replace(
        ' "Will you now or in the future require sponsorship?" → **Yes**.', ""))
    assert build(js) == 1
    legal = section(pack(js)[1], "Work authorization and sponsorship")
    assert "| Are you legally authorized to work in the US? | Yes |" in legal
    assert f"| Will you now or in the future require sponsorship? | {ap.LEGAL_MISSING} |" in legal


def test_a_sponsorship_answer_other_than_yes_blocks(js):
    write(js / "legal/timers.md", LEGAL.replace("require sponsorship?\" → **Yes**", "require sponsorship?\" → **No**"))
    assert build(js) == 1
    meta, body, _ = pack(js)
    assert "| Will you now or in the future require sponsorship? | No |" in body      # still verbatim
    assert any("sponsorship answer" in b and "is not Yes" in b for b in meta["blockers"])


def test_lint_flags_a_legal_answer_that_changed_after_the_build(js, capsys):
    assert build(js) == 0
    write(js / "legal/timers.md", LEGAL.replace("expected around March 2027", "expected in early 2027"))
    capsys.readouterr()
    assert lint(js) == 1
    assert "legal/timers.md" in capsys.readouterr().out


# ----------------------------------------------------------------------------- the editorial gate
def _no_review(root):
    (root / V / "review.md").unlink()


def _changes_requested(root):
    set_front(root / V / "review.md", verdict="changes-requested")


def _open_finding(root):
    path = root / V / "review.md"
    path.write_text(path.read_text() + "\n### E-02 · High · T7 evidence\n- state: OPEN\n")


def _resume_edited(root):
    path = root / V / "resume.yaml"
    path.write_text(path.read_text() + "# edited after the review\n")


@pytest.mark.parametrize("breaker, reason", [
    (_no_review, "review.md"), (_changes_requested, "changes-requested"),
    (_open_finding, "OPEN"), (_resume_edited, "hash"),
])
def test_build_refuses_a_variant_that_is_not_editor_approved(js, capsys, breaker, reason):
    breaker(js)
    assert build(js) == 1
    out = capsys.readouterr().out
    assert out.startswith("REFUSED") and reason in out
    assert not pack_path(js).exists()


def test_a_refusal_leaves_the_old_pack_and_lint_fails_on_it(js, capsys):
    assert build(js) == 0
    _resume_edited(js)
    assert build(js) == 1
    assert pack_path(js).exists()
    capsys.readouterr()
    assert lint(js) == 1
    assert "no longer editor-approved" in capsys.readouterr().out


def test_a_pdf_replaced_after_the_render_is_refused(js, capsys):
    (js / V / PDF_NAME).write_bytes(b"%PDF-1.4 a different file\n")
    assert build(js) == 1
    assert "pdf_sha256" in capsys.readouterr().out
    assert not pack_path(js).exists()


# ----------------------------------------------------------------------------- the ATS verdict
def _ats_fail(root):
    path = root / V / "ats-report.json"
    data = json.loads(path.read_text())
    data.update(passed=False, failures=["em-dash in text"])
    path.write_text(json.dumps(data))


def _ats_missing(root):
    (root / V / "ats-report.json").unlink()


def _ats_stale(root):
    path = root / V / "ats-report.json"
    data = json.loads(path.read_text())
    data["checked_at"] = "2026-09-23T09:00:00"            # before meta.yaml built_at
    path.write_text(json.dumps(data))


def _ats_other_pdf(root):
    path = root / V / "ats-report.json"
    data = json.loads(path.read_text())
    data["pdf"] = f"{V}/Old_Resume.pdf"
    path.write_text(json.dumps(data))


@pytest.mark.parametrize("breaker, shown", [
    (_ats_fail, "FAIL"), (_ats_missing, "missing"), (_ats_stale, "older than"), (_ats_other_pdf, "different PDF"),
])
def test_an_ats_verdict_that_is_not_a_current_pass_blocks(js, breaker, shown):
    breaker(js)
    assert build(js) == 1
    meta, body, _ = pack(js)
    assert meta["status"] == "blocked"
    assert shown in section(body, "Resume, cover letter and why-us")
    assert any("ATS" in b for b in meta["blockers"])


# No cover letters unless a specific role asks. cover.md and why-us.md are optional; the
# application's own `cover_letter: required` or `why_us: required` (its form asks) keeps each a blocker.
@pytest.mark.parametrize("name, label", [("cover.md", "Cover letter"), ("why-us.md", "Why us")])
def test_a_missing_cover_or_why_us_is_not_included_and_never_blocks(js, name, label):
    (js / V / name).unlink()
    assert build(js) == 0
    meta, body, _ = pack(js)
    assert meta["status"] == "ready" and not any(name in b for b in meta["blockers"])
    row = next(ln for ln in section(body, "Resume, cover letter and why-us").splitlines() if ln.startswith(f"| {label} |"))
    assert "not included" in row and "only when the role asks" in row


@pytest.mark.parametrize("name, field", [("cover.md", "cover_letter"), ("why-us.md", "why_us")])
def test_a_form_that_requires_it_keeps_the_blocker(js, name, field):
    set_front(js / f"applications/{SLUG}/application.md", **{field: "required"})
    (js / V / name).unlink()
    assert build(js) == 1
    assert any(name in b and "required" in b for b in pack(js)[0]["blockers"])


# ----------------------------------------------------------------------------- contact block rules
def test_website_stays_empty_while_c45_is_open(js):
    assert build(js) == 0
    _, body, text = pack(js)
    assert "rowan-site.example.com" not in text
    assert "C45" in section(body, "Contact block")


def test_website_renders_once_confirmed_and_c45_is_ruled(js):
    facts = yaml.safe_load((js / "profile/facts.yaml").read_text())
    facts["identity"]["links"]["website"] = {"value": "rowan.example.com", "status": "confirmed"}
    facts["conflicts"] = [c for c in facts["conflicts"] if c["id"] != "C45"] + [
        {"id": "C45", "topic": "Personal website", "status": "ruled"}]
    write(js / "profile/facts.yaml", yaml.safe_dump(facts, sort_keys=False))
    assert build(js) == 0
    assert "https://rowan.example.com" in section(pack(js)[1], "Contact block")


def test_name_parts_come_from_full_name_unless_private_legal_name_overrides(js):
    assert build(js) == 0
    contact = section(pack(js)[1], "Contact block")
    assert "| First name | Rowan |" in contact and "| Middle name | Quill |" in contact
    assert "| Last name | Example |" in contact
    set_private(js, {"legal_name": {"first": "Rowan", "middle": "", "last": "Quill Example"}})
    assert build(js) == 0
    contact = section(pack(js)[1], "Contact block")
    assert "| Last name | Quill Example | profile/private.yaml legal_name |" in contact


# ----------------------------------------------------------------------------- the owner's private answers
def test_private_answers_absent_become_ask_owner_and_eeo_defaults_to_decline(js):
    assert build(js) == 0
    meta, body, _ = pack(js)
    screening = section(body, "Screening answers")
    for question in ("Desired salary", "Willing to relocate?", "Remote, hybrid or onsite?", "Notice period",
                     "Earliest start date"):
        row = next(line for line in screening.splitlines() if line.startswith(f"| {question} |"))
        assert f"| {ap.ASK} |" in row, row
    eeo = section(body, "Voluntary self-identification (EEO)")
    assert eeo.count(ap.DECLINE) == 5
    ask = section(body, "Ask the owner")
    assert "Desired salary" in ask and meta["status"] == "ready"


def test_private_answers_present_are_copied_verbatim(js):
    set_private(js, {"salary_expectation": "USD 180,000 to 200,000 base, open on equity",
                     "relocation": "Yes, anywhere in the US", "remote_preference": "Hybrid in the Bay Area",
                     "notice_period": "2 weeks", "may_contact_current_employer": "No",
                     "eeo": {"gender": "Female", "hispanic_latino": "decline", "race": "Two or more races",
                             "veteran": "Not a veteran", "disability": "decline"}})
    assert build(js) == 0
    body = pack(js)[1]
    screening = section(body, "Screening answers")
    assert "| Desired salary | USD 180,000 to 200,000 base, open on equity | profile/private.yaml salary_expectation |" in screening
    assert "| Notice period | 2 weeks | profile/private.yaml notice_period |" in screening
    assert "| May we contact your current employer? | No |" in screening
    eeo = section(body, "Voluntary self-identification (EEO)")
    assert "| Gender | Female | profile/private.yaml eeo.gender |" in eeo
    assert "| Race | Two or more races |" in eeo and eeo.count(ap.DECLINE) == 2


def test_the_example_private_yaml_parses_and_as_copied_answers_nothing(js):
    example = JS_REAL / "profile" / "private.yaml.example"
    data = yaml.safe_load(example.read_text())
    assert set(data) >= {"salary_expectation", "relocation", "remote_preference", "notice_period", "eeo"}
    assert set(data["eeo"]) == {key for key, _ in ap.EEO_FIELDS}
    assert all(v == "decline" for v in data["eeo"].values())
    shutil.copyfile(example, js / "profile/private.yaml")
    assert build(js) == 0
    body = pack(js)[1]
    assert f"| Desired salary | {ap.ASK} |" in section(body, "Screening answers")
    assert section(body, "Voluntary self-identification (EEO)").count(ap.DECLINE) == 5
    assert "| First name | Rowan | derived" in section(body, "Contact block")


def test_an_unreadable_private_yaml_blocks_instead_of_reading_as_empty(js):
    write(js / "profile/private.yaml", "salary_expectation: [unclosed\n")
    assert build(js) == 1
    assert any("private.yaml" in b for b in pack(js)[0]["blockers"])


def test_a_held_string_typed_into_private_yaml_blocks_and_lint_names_it(js, capsys):
    set_private(js, {"relocation": "Yes, even to the Mars Colony"})
    assert build(js) == 1
    assert any("held text" in b for b in pack(js)[0]["blockers"])
    capsys.readouterr()
    assert lint(js) == 1
    assert "Mars Colony" in capsys.readouterr().out


# ----------------------------------------------------------------------------- clock.yaml
def test_start_date_and_notice_come_from_clock_yaml_when_it_exists(js):
    set_private(js, {"notice_period": "3 weeks"})
    assert build(js) == 0
    screening = section(pack(js)[1], "Screening answers")
    assert f"| Earliest start date | {ap.ASK} |" in screening
    assert "| Notice period | 3 weeks | profile/private.yaml notice_period |" in screening
    write(js / "clock.yaml", yaml.safe_dump({"earliest_start": "2026-10-19", "notice_period": "2 weeks"}))
    assert build(js) == 0
    screening = section(pack(js)[1], "Screening answers")
    assert "| Earliest start date | 2026-10-19 | clock.yaml earliest_start |" in screening
    assert "| Notice period | 2 weeks | clock.yaml notice_period |" in screening


def test_the_90_day_clock_gives_start_and_notice_only_once_employment_end_is_confirmed(js):
    # clock.yaml as scripts/clock.py writes it: employment_end, provisional until the owner confirms it
    clock = {"updated": "2026-09-25", "employer": "Northwind Robotics", "employment_end": date(2026, 10, 2),
             "employment_end_confirmed": False, "clock_days": 90, "new_employment_start": None}
    write(js / "clock.yaml", yaml.safe_dump(clock, sort_keys=False))
    assert build(js) == 0
    body = pack(js)[1]
    screening = section(body, "Screening answers")
    assert f"| Earliest start date | {ap.ASK} (if employment_end 2026-10-02 holds: 2026-10-05) |" in screening
    assert f"| Notice period | {ap.ASK} (none needed if employment_end 2026-10-02 holds) |" in screening
    assert "Earliest start date" in section(body, "Ask the owner")
    clock["employment_end_confirmed"] = True
    write(js / "clock.yaml", yaml.safe_dump(clock, sort_keys=False))
    assert build(js) == 0
    screening = section(pack(js)[1], "Screening answers")
    assert "| Earliest start date | 2026-10-05 | clock.yaml employment_end 2026-10-02" in screening
    assert "| Notice period | None needed: the current job ends 2026-10-02 |" in screening


def test_after_a_confirmed_end_the_resume_saying_present_does_not_block_and_months_stop_at_the_end(js, capsys):
    # After the last day the role reads its real end, never "present", and is not the
    # current employer; a resume still saying Present is stale but does not block the pack.
    write(js / "clock.yaml", yaml.safe_dump({"employer": "Northwind Robotics", "employment_end": date(2026, 10, 2),
                                             "employment_end_confirmed": True}, sort_keys=False))
    assert build(js, today=date(2026, 10, 20)) == 0
    meta, body, _ = pack(js)
    assert not any("resume-tailor" in b for b in meta["blockers"])
    assert "an ended role is not the current employer" in body
    screening = section(body, "Screening answers")
    assert "| Earliest start date | Immediately (from 2026-10-20) |" in screening
    assert "| Notice period | None: the last working day was 2026-10-02 |" in screening
    capsys.readouterr()
    assert ap.main(["--root", str(js), "answers", "--today", "2026-10-20"]) == 0
    out = capsys.readouterr().out
    total = next(line for line in out.splitlines() if line.startswith("| Years of professional experience |"))
    assert "5 years 4 months (64 months)" in total and "Northwind Robotics 2025-01 to 2026-10" in total
    current = next(line for line in out.splitlines() if line.startswith("| Current company and title |"))
    assert "None: not currently employed" in current and "2025-01 to 2026-10" in current
    assert "present" not in current.split("|")[2].lower() and "(since 2025-01)" not in current


# ----------------------------------------------------------------------------- how did you hear
@pytest.mark.parametrize("source, platform, expected", [
    ("greenhouse", None, "Company careers page"),
    ("jibe", None, "Company careers page"),                     # a Jibe career site
    ("sitemap", None, "Company careers page"),                  # a careers site read from its sitemap
    ("acmejobs", None, "Company careers page"),                 # a configured company-specific board source
    ("hn", None, "Hacker News"),
    ("linkedin-alert", None, "LinkedIn"),
    ("linkedin-alert", "linkedin", "LinkedIn"),
    ("glassdoor-alert", "glassdoor", "Glassdoor"),
    ("wttj-alert", "wttj", "Welcome to the Jungle"),
    ("yc-alert", "yc", "Y Combinator Work at a Startup"),
    ("himalayas", "himalayas", "Himalayas"),
    ("manual", "company-ats", "Company careers page"),
    ("referral", None, "Employee referral"),
    ("manual", "welcome to the jungle", "Welcome to the Jungle"),
    ("manual", "YC Work at a Startup", "Y Combinator Work at a Startup"),
    ("manual", None, ap.ASK),
    ("email-alert", None, ap.ASK),
])
def test_how_did_you_hear_comes_from_the_job_source_or_platform(js, source, platform, expected):
    changes = {"source": source}
    if platform:
        changes["platform"] = platform
    set_front(js / f"jobs/{JOB}.md", **changes)
    assert build(js) == 0
    screening = section(pack(js)[1], "Screening answers")
    row = next(line for line in screening.splitlines() if line.startswith("| How did you hear about us? |"))
    assert f"| {expected}" in row, row


def test_a_platform_name_comes_from_targets_platforms_yaml_when_it_exists(js):
    set_front(js / f"jobs/{JOB}.md", source="email-alert", platform="builtin")
    write(js / "targets/platforms.yaml", yaml.safe_dump({"platforms": [{"id": "builtin", "name": "Built In"}]}))
    assert build(js) == 0
    meta, body, _ = pack(js)
    row = next(line for line in section(body, "Screening answers").splitlines()
               if line.startswith("| How did you hear about us? |"))
    assert "| Built In | " in row and "targets/platforms.yaml" in row
    assert "targets/platforms.yaml" in meta["files"]


REGISTRY = {"platforms": [
    {"id": "company-ats", "name": "Company ATS boards (Greenhouse, Lever, Ashby, Workday)",
     "url": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs", "access": ["api"]},
    {"id": "wttj", "name": "Welcome to the Jungle (formerly Otta)", "url": "https://www.welcometothejungle.com/en/jobs",
     "access": ["email-alert", "browser"]},
    {"id": "dice", "name": "Dice", "url": "https://www.dice.com/jobs", "access": ["email-alert", "manual"]},
]}


def test_browser_assistance_follows_rule_3_and_the_platform_registry(js):
    # an employer's own ATS form: allowed only on pages the owner opens, with the owner present; the owner clicks Apply and Submit
    assert build(js) == 0
    meta, body, _ = pack(js)
    assert meta["browser_assist"] == "yes"
    portal = section(body, "Portal and account")
    assert "pages the owner opens" in portal and "rule 3" in portal
    # a site whose terms name AI agents (access manual): the portal is read from the registry's host
    write(js / "targets/platforms.yaml", yaml.safe_dump(REGISTRY, sort_keys=False))
    set_front(js / f"jobs/{JOB}.md", ats_vendor="unknown", apply_url="https://www.dice.com/job-detail/abc-123",
              source="email-alert", platform="dice")
    assert build(js) == 0
    meta, body, _ = pack(js)
    assert meta["portal"] == "dice" and meta["browser_assist"] == "no"
    portal = section(body, "Portal and account")
    assert "Portal: Dice" in portal and "not allowed" in portal and "access manual" in portal
    assert "the owner fills this form personally" in section(body, "Checklist (the owner ticks)")
    # registry names drop their parenthetical notes when they answer "how did you hear"
    set_front(js / f"jobs/{JOB}.md", ats_vendor="greenhouse", apply_url="https://boards.greenhouse.io/acme/jobs/123",
              source="wttj-alert", platform="wttj")
    assert build(js) == 0
    row = next(line for line in section(pack(js)[1], "Screening answers").splitlines()
               if line.startswith("| How did you hear about us? |"))
    assert "| Welcome to the Jungle | " in row and "Otta" not in row


# ----------------------------------------------------------------------------- portal and accounts
def _workday(root):
    set_front(root / f"jobs/{JOB}.md", ats_vendor="workday",
              apply_url="https://acme.wd5.myworkdayjobs.com/External/job/Remote/Applied-AI-Engineer_R123")


def test_workday_needs_an_account_and_shows_the_record_once_it_exists(js, capsys):
    _workday(js)
    assert build(js) == 0
    body = pack(js)[1]
    account = section(body, "Portal and account")
    assert "no record" in account and "apply_pack.py account" in account
    assert "## Portal notes: Workday" in body and "password" in body
    assert ap.main(["--root", str(js), "account", "--platform", "workday", "--tenant", "acme",
                    "--company", "Acme Robotics", "--exists", "yes", "--email", "rowan.example@example.com",
                    "--created-on", "2026-09-26", "--today", "2026-09-26"]) == 0
    assert build(js) == 0
    account = section(pack(js)[1], "Portal and account")
    assert "| workday | acme | yes | rowan.example@example.com | 2026-09-26 |" in account


def test_portal_is_read_from_the_apply_url_when_the_vendor_is_unknown(js):
    set_front(js / f"jobs/{JOB}.md", ats_vendor="unknown",
              apply_url="https://acme.wd5.myworkdayjobs.com/External/job/R123")
    assert build(js) == 0
    meta, body, _ = pack(js)
    assert meta["portal"] == "workday"
    assert "apply URL host" in section(body, "Portal and account")


def test_a_credential_in_accounts_yaml_blocks_and_lint_flags_it(js, capsys):
    write(js / "state/accounts.yaml", yaml.safe_dump({"schema_version": 1, "accounts": [
        {"platform": "workday", "tenant": "acme", "company": "Acme Robotics", "account_exists": True,
         "email_used": "rowan.example@example.com", "password": "hunter2"}]}))
    assert build(js) == 1
    assert "hunter2" not in pack_path(js).read_text()
    assert any("credential" in b for b in pack(js)[0]["blockers"])
    capsys.readouterr()
    assert lint(js) == 1


def test_account_command_upserts_and_refuses_credentials(js, capsys):
    args = ["--root", str(js), "account", "--platform", "icims", "--tenant", "acme", "--company", "Acme Robotics",
            "--exists", "yes", "--email", "rowan.example@example.com", "--today", "2026-09-26"]
    assert ap.main(args) == 0
    assert ap.main(args[:-2] + ["--notes", "portal asks for a cover letter upload", "--today", "2026-09-27"]) == 0
    data = yaml.safe_load((js / "state/accounts.yaml").read_text())
    rows = [a for a in data["accounts"] if a["platform"] == "icims"]
    assert len(rows) == 1 and rows[0]["notes"] == "portal asks for a cover letter upload"
    assert data["updated"] == "2026-09-27"
    assert (js / "state/accounts.yaml").read_text().startswith(ap.ACCOUNTS_HEADER)
    capsys.readouterr()
    assert ap.main(args[:-2] + ["--notes", "password: hunter2", "--today", "2026-09-27"]) == 1
    assert "never" in capsys.readouterr().err.lower()
    assert "hunter2" not in (js / "state/accounts.yaml").read_text()


# ----------------------------------------------------------------------------- visa gate and stage
def test_the_visa_gate_blocks_without_a_clear_legal_check(js):
    (js / "legal/checks/acme.md").unlink()
    assert build(js) == 1
    assert any("legal/checks/acme.md" in b for b in pack(js)[0]["blockers"])
    write(js / "legal/checks/acme.md", doc({"company": "acme", "e_verify": "no", "verdict": "blocked"}, "x\n"))
    assert build(js) == 1
    assert any("blocked" in b for b in pack(js)[0]["blockers"])


def test_an_application_already_applied_is_blocked(js):
    set_front(js / f"applications/{SLUG}/application.md", stage="applied")
    assert build(js) == 1
    assert any("applied" in b for b in pack(js)[0]["blockers"])


def test_an_unknown_application_is_refused(js, capsys):
    assert build(js, slug="nope-202609") == 1
    assert capsys.readouterr().out.startswith("REFUSED")


# ----------------------------------------------------------------------------- lint
def test_lint_flags_em_dashes_held_text_and_hand_edits(js, capsys):
    assert build(js) == 0
    path = pack_path(js)
    path.write_text(path.read_text() + "\nNote: Data Wrangler — see above\n")
    capsys.readouterr()
    assert lint(js) == 1
    out = capsys.readouterr().out
    assert "em-dash" in out and "Data Wrangler" in out and "hand-edited" in out


def test_lint_flags_a_referenced_file_that_disappeared(js, capsys):
    assert build(js) == 0
    (js / V / "why-us.md").unlink()
    capsys.readouterr()
    assert lint(js) == 1
    assert "why-us.md" in capsys.readouterr().out


def test_lint_reports_a_missing_pack(js, capsys):
    assert lint(js) == 1
    assert "no apply-pack.md" in capsys.readouterr().out


# ----------------------------------------------------------------------------- static text guards
# A fictional private holds list for the guard trees: the real profile/private-holds.txt is never copied.
PRIVATE_FIXTURE = "# fictional private holds\nsigning bonus of 9,876 fictional units\n\nstipend of 4,321 fictional units\n"
PRIVATE_VALUES = ("signing bonus of 9,876 fictional units", "stipend of 4,321 fictional units")


def guard_tree(tmp_path: Path) -> Path:
    """A fictional tree holding the real PROFILE-HOLDS.md and the fictional private list beside it."""
    root = make_tree(tmp_path / "job-search")
    shutil.copyfile(JS_REAL / "profile" / "PROFILE-HOLDS.md", root / "profile" / "PROFILE-HOLDS.md")
    write(root / "profile" / "private-holds.txt", PRIVATE_FIXTURE)
    return root


def test_static_pack_text_carries_no_real_held_string(tmp_path, capsys):
    root = guard_tree(tmp_path)
    assert set(PRIVATE_VALUES) <= set(ap.load_held(root / "profile" / "PROFILE-HOLDS.md"))   # the private path is live
    for vendor in ("greenhouse", "workday", "lever", "ashby", "smartrecruiters", "icims"):
        set_front(root / f"jobs/{JOB}.md", ats_vendor=vendor)
        assert build(root) == 0, vendor
        capsys.readouterr()
        assert lint(root) == 0, capsys.readouterr().out


def test_the_employers_own_posting_text_is_not_held_against_the_owner(tmp_path, capsys):
    # the real holds list has "Deep Learning", "Azure", "AI Lead" and "United States" as disputed claims about
    # the owner; the same words in the employer's title, company or location are the employer's text, not his claim
    root = guard_tree(tmp_path)
    # a pipe (escaped in table cells) and an apostrophe plus a colon (quoted and doubled in YAML) must not
    # defeat the masking
    set_front(root / f"jobs/{JOB}.md", title="Deep Learning Engineer \u2014 Azure AI Lead | O'Brien's team: Agents",
              location="Remote, United States", company="Acme Robotics")
    assert build(root) == 0, pack_path(root).read_text()
    capsys.readouterr()
    assert lint(root) == 0, capsys.readouterr().out
    # a held string that is the owner's own text still blocks
    set_private(root, {"relocation": "Yes, anywhere in the United States"})
    assert build(root) == 1
    capsys.readouterr()
    assert lint(root) == 1
    assert "United States" in capsys.readouterr().out


def test_a_private_hold_in_the_owners_text_blocks_and_lint_names_it_masked(tmp_path, capsys):
    root = guard_tree(tmp_path)
    assert build(root) == 0
    capsys.readouterr()
    set_private(root, {"salary_expectation": f"USD 1 base plus a {PRIVATE_VALUES[1]}"})
    assert build(root) == 1                   # the pack (gitignored) is blocked; its findings never print the value
    assert any("held text" in b for b in pack(root)[0]["blockers"])
    capsys.readouterr()
    assert lint(root) == 1
    out, err = capsys.readouterr()
    assert "held text <private hold #2> on line" in out and "(profile/private-holds.txt)" in out
    assert ap.main(["--root", str(root), "answers", "--today", "2026-09-26"]) == 0
    bank, bank_err = capsys.readouterr()      # the answer bank withholds the row and names the private list
    assert "| Desired salary | withheld: contains held text (profile/private-holds.txt) |" in bank
    leaked = [v for v in PRIVATE_VALUES if v in out + err + bank + bank_err]
    ok = not leaked
    assert ok, f"{len(leaked)} fictional private hold(s) printed"


def test_portal_notes_cover_every_vendor_and_platform_and_are_clean():
    schema = yaml.safe_load((__import__("agency_paths").SCHEMAS_DIR / "job.yaml").read_text())
    vendors = [v for v in schema["enums"]["ats_vendor"] if v not in ("unknown", "other")]
    held = ap.load_held(JS_REAL / "profile" / "PROFILE-HOLDS.md")
    for key in vendors + list(ap.PLATFORMS):
        notes = ap.portal_notes(key)
        assert notes, key
        assert "—" not in notes, key
        hits = [hs.shown(h) for h in held if h in notes]        # the real holds include private ones: never printed
        ok = not hits
        assert ok, (key, hits)


# ----------------------------------------------------------------------------- the answer bank
def test_answers_prints_the_bank_with_a_source_per_answer(js, capsys):
    assert ap.main(["--root", str(js), "answers", "--today", "2026-09-26"]) == 0
    out = capsys.readouterr().out
    assert "| Are you legally authorized to work in the US? | Yes | legal/timers.md ## Answer scripts |" in out
    assert WRITTEN in out
    assert f"| Desired salary | {ap.ASK} |" in out
    assert "Master of Arts in Applied Machine Learning, Harborview State University" in out
    # 2021-06..2024-12 (43) + 2025-01..2026-08 (20; 09-26 is a partial month): no lab (a university), no internship
    total = next(line for line in out.splitlines() if line.startswith("| Years of professional experience |"))
    assert "| 5 years 3 months (63 months): Northwind Robotics 2025-01 to present; Contoso Health 2021-06 to 2024-12 |" in total
    assert "Tinyco" not in total and "Vision Lab" not in total
    current = next(line for line in out.splitlines() if line.startswith("| Current company and title |"))
    assert "Machine Learning Engineer, Northwind Robotics (since 2025-01)" in current
    python = next(line for line in out.splitlines() if line.startswith("| Python |"))
    assert "63 months" in python and "Northwind Robotics" in python and "Contoso Health" in python
    pytorch = next(line for line in out.splitlines() if line.startswith("| PyTorch |"))
    assert "17 months" in pytorch
    nextjs = next(line for line in out.splitlines() if line.startswith("| Next.js |"))
    assert "no role" in nextjs
    sql = next(line for line in out.splitlines() if line.startswith("| SQL (Postgres, SQLite, DuckDB) |"))
    assert "Contoso Health" in sql and "(43 months)" in sql
    lab = next(line for line in out.splitlines() if line.startswith("| Teaching Assistant, Vision Lab"))
    assert "| 2023-01 to 2024-05 (1 year 5 months; experience) |" in lab
    ta = next(line for line in out.splitlines() if line.startswith("| Teaching Assistant, Harborview"))
    assert "(10 months; teaching)" in ta
    for hidden in ("| Rust |", "| Kubernetes |", "| Swift |", "| MATLAB |", "Contoso Inc", "Data Wrangler"):
        assert hidden not in out, hidden
    assert "—" not in out
    for line in out.splitlines():
        if line.startswith("| ") and not line.startswith("| Question") and not line.startswith("|---"):
            assert line.count("|") >= 4, line


def test_answers_json_lists_rows_with_sources(js, capsys):
    assert ap.main(["--root", str(js), "answers", "--today", "2026-09-26", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows and all({"section", "question", "answer", "source"} <= set(r) for r in rows)
    sponsorship = next(r for r in rows if "sponsorship" in r["question"])
    assert sponsorship["answer"] == "Yes" and sponsorship["source"].startswith("legal/timers.md")


def bachelor_row(root: Path, capsys) -> str:
    assert ap.main(["--root", str(root), "answers", "--today", "2026-09-26"]) == 0
    return next(line for line in capsys.readouterr().out.splitlines() if line.startswith("| Bachelor's degree |"))


def test_an_open_education_conflict_keeps_the_year_and_a_ruled_one_gives_the_ruled_resume_dates(js, capsys):
    """I-018: the month form is withheld only while a conflict the entry names is open in facts.yaml; once
    the owner has ruled it, the row copies the entry's ruled resume_dates verbatim and names the ruling."""
    facts = yaml.safe_load((js / "profile/facts.yaml").read_text(encoding="utf-8"))
    facts["education"][1]["resume_dates"] = "Aug 2016 - May 2020"
    write(js / "profile/facts.yaml", yaml.safe_dump(facts, sort_keys=False))
    row = bachelor_row(js, capsys)                                # C14 awaiting-owner: year only, and why
    assert "| Bachelor of Science in Computer Science, Lakeside Institute of Technology, 2020 |" in row
    assert row.endswith("| profile/facts.yaml education edu-bs (year only: the month form is open, C14) |")

    facts["conflicts"][0] = {"id": "C14", "topic": "bachelor month form", "status": "ruled",
                             "ruled": {"by": "owner", "on": "2026-09-25", "ruling": "Aug 2016 - May 2020"}}
    write(js / "profile/facts.yaml", yaml.safe_dump(facts, sort_keys=False))
    row = bachelor_row(js, capsys)                                # ruled: the ruled month form, verbatim
    assert row == ("| Bachelor's degree | Bachelor of Science in Computer Science, Lakeside Institute of Technology, "
                   "Aug 2016 - May 2020 | profile/facts.yaml education edu-bs resume_dates (C14 ruled) |")

    facts["education"][1]["conflict"] = "C14, C99"                # a conflict facts.yaml does not list is not ruled
    write(js / "profile/facts.yaml", yaml.safe_dump(facts, sort_keys=False))
    row = bachelor_row(js, capsys)
    assert "Lakeside Institute of Technology, 2020 |" in row and "the month form is open, C99)" in row
    assert "May 2020" not in row

    facts["education"][1]["conflict"] = "C14"
    del facts["education"][1]["resume_dates"]                     # ruled, but no ruled form recorded: the year
    write(js / "profile/facts.yaml", yaml.safe_dump(facts, sort_keys=False))
    row = bachelor_row(js, capsys)
    assert row.endswith("| Bachelor of Science in Computer Science, Lakeside Institute of Technology, 2020 | "
                        "profile/facts.yaml education edu-bs |")


def test_answers_on_the_real_profile_call_a_conflict_open_only_while_facts_yaml_does(capsys):
    assert ap.main(["--root", str(JS_REAL), "answers", "--today", "2026-09-26"]) == 0
    out = capsys.readouterr().out
    facts = yaml.safe_load((JS_REAL / "profile" / "facts.yaml").read_text(encoding="utf-8"))
    ruled = {str(c.get("id")) for c in facts.get("conflicts") or [] if c.get("status") == "ruled"}
    called_open = set(re.findall(r"month form is open, ([^)]*)\)", out))
    assert not {c for group in called_open for c in re.findall(r"C\d{2}", group)} & ruled


def test_answers_on_the_real_profile_print_no_held_text_and_no_em_dash(capsys):
    assert ap.main(["--root", str(JS_REAL), "answers", "--today", "2026-09-26"]) == 0
    out = capsys.readouterr().out
    held = ap.load_held(JS_REAL / "profile" / "PROFILE-HOLDS.md")
    hits = [hs.shown(h) for h in held if h in out]            # the real holds include private ones: never printed
    ok = not hits
    assert ok, hits
    assert "—" not in out
    assert re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", out)              # the job-search email is in the contact rows


# ----------------------------------------------------------------------------- one credential detector everywhere
ACCOUNT_ARGS = ["account", "--platform", "workday", "--tenant", "acme", "--company", "Acme Robotics", "--exists",
                "yes", "--email", "rowan.example@example.com", "--today", "2026-09-27"]


@pytest.mark.parametrize("notes", [
    "password: Tidepool!2026", "my password is Tidepool!2026", "login Tidepool!2026", "pw Tidepool!2026",
    "passcode 884422", "pin 1234", "secret Tidepool!2026", "api key sk-" "proj-Ab3dE5fG7hJ9kL1mN3pQ",
    "token aB3dE5fG7hJ9kL1mN3pQrS", "My Password Is tidepool",
])
def test_account_refuses_every_credential_form_in_notes_and_never_echoes_it(js, capsys, notes):
    assert ap.main(["--root", str(js)] + ACCOUNT_ARGS + ["--notes", notes]) == 1
    err = capsys.readouterr().err
    assert "refused" in err and "credential" in err
    secret = notes.split()[-1]
    assert secret not in err and secret not in (js / "state/accounts.yaml").read_text()


@pytest.mark.parametrize("notes", ["created via SSO", "email rowan.example@example.com used",
                                   "session times out after 15 minutes: save each page"])
def test_account_keeps_benign_notes(js, notes):
    assert ap.main(["--root", str(js)] + ACCOUNT_ARGS + ["--notes", notes]) == 0
    data = yaml.safe_load((js / "state/accounts.yaml").read_text())
    assert [a["notes"] for a in data["accounts"]] == [notes]


def test_account_refuses_a_credential_in_the_company_and_a_tenant_that_is_not_a_slug(js, capsys):
    args = ["--root", str(js)] + ACCOUNT_ARGS
    bad_company = args.copy()
    bad_company[bad_company.index("Acme Robotics")] = "Acme, password: Tidepool!2026"
    assert ap.main(bad_company) == 1
    bad_tenant = args.copy()
    bad_tenant[bad_tenant.index("acme")] = "Tidepool!2026"
    assert ap.main(bad_tenant) == 1
    bad_email = args.copy()
    bad_email[bad_email.index("rowan.example@example.com")] = "pw Tidepool!2026"
    assert ap.main(bad_email) == 1
    bad_platform = args.copy()
    bad_platform[bad_platform.index("workday")] = "password: Tidepool!2026"
    assert ap.main(bad_platform) == 1
    err = capsys.readouterr().err
    assert "Tidepool!2026" not in err and "Tidepool" not in (js / "state/accounts.yaml").read_text()


def test_the_registry_check_in_build_and_lint_uses_the_same_detector(js, capsys):
    write(js / "state/accounts.yaml", yaml.safe_dump({"schema_version": 1, "accounts": [
        {"platform": "workday", "tenant": "acme", "company": "Acme Robotics", "account_exists": True,
         "email_used": "rowan.example@example.com", "created_on": "2026-09-26",
         "notes": "my password is Tidepool!2026"}]}))
    _workday(js)
    assert build(js) == 1
    meta, _, text = pack(js)
    assert "Tidepool!2026" not in text
    assert any("state/accounts.yaml" in b and "credential" in b for b in meta["blockers"])
    capsys.readouterr()
    assert lint(js) == 1
    out = capsys.readouterr().out
    assert "look like a credential" in out and "Tidepool!2026" not in out


def test_a_credential_typed_into_a_record_is_withheld_from_the_pack_and_blocks(js, capsys):
    set_private(js, {"salary_expectation": "my password is Tidepool!2026", "relocation": "Yes"})
    assert build(js) == 1
    meta, body, text = pack(js)
    assert "Tidepool!2026" not in text
    row = next(line for line in section(body, "Screening answers").splitlines() if line.startswith("| Desired salary"))
    assert "withheld" in row and "profile/private.yaml salary_expectation" in row
    assert any("profile/private.yaml salary_expectation" in b and "credential" in b for b in meta["blockers"])
    capsys.readouterr()
    assert lint(js) == 1
    assert "Tidepool!2026" not in capsys.readouterr().out
    # the answer bank withholds it too
    assert ap.main(["--root", str(js), "answers", "--today", TODAY.isoformat()]) == 0
    bank = capsys.readouterr().out
    assert "Tidepool!2026" not in bank and "withheld: looks like a credential" in bank


def test_lint_names_a_credential_typed_into_a_pack_by_hand(js, capsys):
    assert build(js) == 0
    path = pack_path(js)
    text = path.read_text(encoding="utf-8").replace("| Oakland, CA |", "| Oakland, CA pw Tidepool!2026 |", 1)
    path.write_text(text, encoding="utf-8")
    capsys.readouterr()
    assert lint(js) == 1
    out = capsys.readouterr().out
    assert "credential-like text on line" in out and "Contact block" in out and "Tidepool!2026" not in out


# ----------------------------------------------------------------------------- Ask the owner: one list, one count
def _every_answer_on_record(root: Path) -> None:
    set_private(root, {"salary_expectation": "USD 180,000 base", "relocation": "Yes", "remote_preference": "Hybrid",
                       "may_contact_current_employer": "No"})
    write(root / "clock.yaml", yaml.safe_dump({"earliest_start": "2026-10-19", "notice_period": "2 weeks"}))


def _ask_items(root: Path) -> list[str]:
    return [line for line in section(pack(root)[1], "Ask the owner").splitlines() if line.startswith("- ")]


def test_ask_owner_says_nothing_only_when_nothing_is_open(js, capsys):
    _every_answer_on_record(js)
    assert build(js) == 0
    assert "ask the owner: 0 item(s)" in capsys.readouterr().out
    assert _ask_items(js) == ["- nothing: every field has a record"]


def test_a_street_address_as_the_only_open_item_is_one_item_and_not_nothing(js, capsys):
    _every_answer_on_record(js)
    _workday(js)
    assert build(js) == 0
    assert "ask the owner: 1 item(s)" in capsys.readouterr().out
    assert _ask_items(js) == ["- Street address (Workday asks for it; it is not in the records)"]


def test_the_ask_count_covers_the_record_asks_and_the_street_address(js, capsys):
    _workday(js)                                         # private.yaml and clock.yaml absent: several asks
    assert build(js) == 0
    items = _ask_items(js)
    assert len(items) > 1 and items[-1].startswith("- Street address") and not any("nothing" in i for i in items)
    assert f"ask the owner: {len(items)} item(s)" in capsys.readouterr().out


# ----------------------------------------------------------------------------- the Location row names the work mode once
@pytest.mark.parametrize("location,remote,expected", [
    ("San Francisco, CA", "hybrid", "San Francisco, CA (hybrid)"),
    ("San Francisco, CA (hybrid) or Remote (US)", "hybrid", "San Francisco, CA (hybrid) or Remote (US)"),
    ("Hybrid - New York, NY", "hybrid", "Hybrid - New York, NY"),
    ("San Francisco, CA or Remote (US)", "hybrid", "San Francisco, CA or Remote (US) (hybrid)"),
    ("Remote - US", "remote", "Remote - US"),
    ("Oakland, CA (on-site)", "onsite", "Oakland, CA (on-site)"),
    ("Oakland, CA, in office 5 days", "onsite", "Oakland, CA, in office 5 days"),
    ("Oakland, CA", "onsite", "Oakland, CA (onsite)"),
    (None, "remote", "not stated (remote)"),
    ("Oakland, CA", None, "Oakland, CA"),
    ("Oakland, CA", "unknown", "Oakland, CA (work mode unknown)"),
])
def test_the_location_row_names_the_work_mode_once(js, location, remote, expected):
    set_front(js / f"jobs/{JOB}.md", location=location, remote=remote)
    assert build(js) == 0
    row = next(line for line in section(pack(js)[1], "Posting").splitlines() if line.startswith("| Location |"))
    assert row == f"| Location | {expected} | jobs/{JOB}.md |"


# ----------------------------------------------------------------------------- the one-base model (lane J14)
def test_a_variant_of_the_general_base_builds_and_its_sidecar_is_valid(js, capsys):
    """Every variant starts from resumes/bases/general (the owner, 2026-09-26): sidecar base_of: general is what the
    pack builds from, and render_resume.py's sidecar check accepts it."""
    import render_resume as rr
    data = yaml.safe_load((js / V / "resume.yaml").read_text(encoding="utf-8"))
    assert data["sidecar"]["base_of"] == "general"
    report = rr.LintReport()
    rr.check_sidecar(data["sidecar"], report)
    assert report.errors == []
    assert build(js) == 0 and capsys.readouterr().out.startswith("READY")


# ----------------------------------------------------------------------------- REQ-10 (P-REQ-5): portal accounts
@pytest.mark.parametrize("platform", ["wellfound", "indeed", "builtin", "jobright", "handshake"])
def test_the_new_portals_take_an_account_record(js, capsys, platform):
    args = ["--root", str(js), "account", "--platform", platform, "--company", platform, "--exists", "yes",
            "--email", "rowan.example@example.com", "--today", "2026-09-27"]
    assert ap.main(args) == 0, capsys.readouterr().err
    rows = [a for a in yaml.safe_load((js / "state/accounts.yaml").read_text())["accounts"] if a["platform"] == platform]
    assert len(rows) == 1 and rows[0]["tenant"] is None


@pytest.mark.parametrize("host,key", [("wellfound.com", "wellfound"), ("www.indeed.com", "indeed"),
                                      ("builtin.com", "builtin"), ("www.builtinsf.com", "builtin"),
                                      ("jobright.ai", "jobright"), ("app.joinhandshake.com", "handshake")])
def test_the_new_portals_are_known_by_their_apply_host(host, key):
    assert any(re.search(rx, host) and k == key for rx, k in ap.HOST_PORTAL), host


def test_the_platform_notes_carry_what_the_portal_research_found():
    assert "Jobright Agent stays off" in ap.portal_notes("jobright")
    assert "do not create an account" in ap.portal_notes("builtin").lower()
    assert "only for saved-search alerts" in ap.portal_notes("hiringcafe")      # reconciled with targets/platforms.yaml
    assert ap.PLATFORMS["hiringcafe"]["account"] == "optional"


# ----------------------------------------------------------------------------- REQ-14: the verdict of record
def _review(root: Path, round_value, body: str) -> None:
    path = root / V / "review.md"
    meta, _ = fm.load(path)
    meta["round"] = round_value
    path.write_text(fm.dump(meta, body), encoding="utf-8")


HISTORY_BELOW = """## Round 9 (bullet order), 2026-09-27: APPROVED (the verdict of record)

### Verdict (round 9)
APPROVED.

## Round 8, 2026-09-27: APPROVED (VOID: superseded by round 9 above; history only)

### E-11 · High · T8 an old finding
- state: OPEN

# Round 1 record (history; outcomes are in the table above)

# Editorial review: Acme, Applied AI Engineer

## Findings

### E-01 · High · T8 the first round's finding
- state: OPEN
"""


def test_open_states_in_history_sections_do_not_block_the_verdict_of_record(js, capsys):
    _review(js, 9, HISTORY_BELOW)                          # the 70273 layout: current round on top, history below
    assert build(js) == 0
    assert capsys.readouterr().out.startswith("READY")


def test_an_open_finding_in_the_current_round_still_refuses(js, capsys):
    _review(js, 9, HISTORY_BELOW.replace("APPROVED.\n", "APPROVED.\n\n### E-12 · Low · new\n- state: OPEN\n"))
    assert build(js) == 1
    out = capsys.readouterr().out
    assert out.startswith("REFUSED") and "1 OPEN finding(s) in round 9" in out


def test_the_current_round_appended_below_the_history_is_found(js, capsys):
    body = ("# Editorial review: Acme, rounds 1 to 7\n\n## Findings\n\n### E-01 · High · old\n- state: OPEN\n\n"
            "## Round 7\n\n### E-02 · Low · old\n- state: OPEN\n\n## Round 9\n\n### Verdict (round 9)\nAPPROVED.\n")
    _review(js, "9 (bullet order)", body)             # the layout with history first, round 9 last
    assert build(js) == 0
    _review(js, "9 (bullet order)", body + "\n### E-03 · Low · new\n- state: OPEN\n")
    capsys.readouterr()
    assert build(js) == 1 and "1 OPEN finding(s) in round 9" in capsys.readouterr().out


def test_without_a_current_round_heading_only_marked_history_is_skipped(js, capsys):
    body = ("## Findings\n\n### E-01 · Medium · S4 vague verb\n- state: FIXED\n\n"
            "## Round 1 header (kept; void on the round-2 hash)\n\n### E-00 · Low · old\n- state: OPEN\n")
    _review(js, 2, body)
    assert build(js) == 0
    _review(js, 2, body.replace("- state: FIXED", "- state: OPEN"))
    capsys.readouterr()
    assert build(js) == 1 and "1 OPEN finding(s)" in capsys.readouterr().out


def test_a_front_matter_that_is_not_yaml_is_refused_with_its_file_line(js, capsys):
    path = js / V / "review.md"
    path.write_text(path.read_text().replace("round: 2", "round: 9 (bullet order: reordered)"))
    assert build(js) == 1
    out = capsys.readouterr().out
    assert out.startswith("REFUSED") and "review.md front matter is not valid YAML at line" in out
    n = next(i for i, l in enumerate(path.read_text().splitlines(), 1) if l.startswith("round:"))
    assert f"at line {n}: `round: 9 (bullet order: reordered)`" in out


def test_live_open_counts_by_round():
    assert ap.live_open_findings(HISTORY_BELOW, 9) == 0
    assert ap.live_open_findings(HISTORY_BELOW, None) == 1         # no known round: only marked history is skipped
    assert ap.live_open_findings("## Findings\n- state: OPEN\n", 3) == 1


# ----------------------------------------------------------------------------- the Oracle portal
@pytest.mark.parametrize("url", ["https://www.careers.acme-motors.example/job/dearborn/ai-engineer/48560/100733472960",
                                 "https://apply.acme-motors.example/en/sites/CX_1/job/70273",
                                 "https://abcd.fa.em5.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/70273"])
def test_oracle_hosts_map_to_the_oracle_portal(js, url):
    set_front(js / f"jobs/{JOB}.md", ats_vendor="unknown", apply_url=url)
    build(js)
    meta, body, _ = pack(js)
    assert meta["portal"] == "oracle"
    assert "## Portal notes: Oracle Recruiting Cloud" in body
    assert "review each parsed Education and Experience field" in body


def test_an_oracle_ats_vendor_maps_to_the_oracle_portal(js):
    set_front(js / f"jobs/{JOB}.md", ats_vendor="oracle")
    build(js)
    assert pack(js)[0]["portal"] == "oracle"


def test_the_account_registry_accepts_an_oracle_tenant(js):
    assert ap.main(["--root", str(js), "account", "--platform", "oracle", "--tenant", "acme", "--company",
                    "Acme Robotics", "--exists", "yes", "--email", "rowan.example@example.com",
                    "--today", "2026-09-26"]) == 0


# ----------------------------------------------------------------------------- REQ-14: the upload choice
INLINE_PDF = "Rowan_Quill_Example_Resume_inline-dates.pdf"
INLINE_BYTES = b"%PDF-1.4 fictional resume, inline dates\n"


def _inline(root: Path, ats_passed: bool = True) -> None:
    idir = root / V / "inline"
    idir.mkdir(parents=True, exist_ok=True)
    (idir / INLINE_PDF).write_bytes(INLINE_BYTES)
    write(idir / "meta.yaml", yaml.safe_dump(
        {"built_at": "2026-09-24T12:00:00", "status": "ok", "source": f"{V}/resume.yaml", "pdf": INLINE_PDF,
         "pdf_sha256": sha(INLINE_BYTES), "docx": None}, sort_keys=False))
    write(idir / "ats-report.json", json.dumps(
        {"pdf": f"{V}/inline/{INLINE_PDF}", "checked_at": "2026-09-24T12:40:00", "passed": ats_passed,
         "vendor": "oracle", "failures": [] if ats_passed else ["reading order"], "warnings": []}))
    write(idir / "layout-report.json", json.dumps(
        {"pdf": INLINE_PDF, "pdf_sha256": sha(INLINE_BYTES), "verdict": "PASS", "passed": True, "failures": []}))


def build_pdf(root: Path, choice: str) -> int:
    return ap.main(["--root", str(root), "build", SLUG, "--today", TODAY.isoformat(), "--pdf", choice])


def test_pdf_inline_names_the_inline_render_and_records_the_choice(js, capsys):
    _inline(js)
    assert build_pdf(js, "inline") == 0
    meta, body, text = pack(js)
    assert meta["pdf_choice"] == "inline"
    assert meta["pdf"] == f"{V}/inline/{INLINE_PDF}" and meta["pdf_sha256"] == sha(INLINE_BYTES)
    files = section(body, "Resume, cover letter and why-us")
    assert f"| Resume PDF (upload this file, nothing else) | {V}/inline/{INLINE_PDF} | {V}/inline/meta.yaml pdf " \
           "(--pdf inline) |" in files
    assert "vendor oracle" in files                                 # the inline render's own ATS report
    assert f"--pdf inline --today {TODAY.isoformat()}" in text
    capsys.readouterr()
    assert lint(js) == 0                                            # lint rebuilds with the recorded choice


def test_pdf_classic_is_the_default_and_is_recorded(js):
    assert build(js) == 0
    meta = pack(js)[0]
    assert meta["pdf_choice"] == "classic" and meta["pdf"] == f"{V}/{PDF_NAME}"


def test_pdf_inline_without_an_inline_render_is_refused(js, capsys):
    assert build_pdf(js, "inline") == 1
    out = capsys.readouterr().out
    assert out.startswith("REFUSED") and f"{V}/inline/meta.yaml" in out


def test_pdf_inline_uses_the_inline_ats_verdict(js):
    _inline(js, ats_passed=False)
    assert build_pdf(js, "inline") == 1
    meta = pack(js)[0]
    assert any("ATS" in b and INLINE_PDF in b for b in meta["blockers"])


# --------------------------------------------------------------------------- variant_dir (P-5)
@pytest.mark.parametrize("value, expected", [
    ("[[acme-founding-engineer-202609]]", "resumes/variants/acme-founding-engineer-202609"),
    ("acme-founding-engineer-202609", "resumes/variants/acme-founding-engineer-202609"),
    ("resumes/variants/acme-ai-202609", "resumes/variants/acme-ai-202609"),
    ("resumes/variants/acme-ai-202609/resume.md", "resumes/variants/acme-ai-202609"),
    ("[[resumes/variants/acme-ai-202609/resume.md]]", "resumes/variants/acme-ai-202609"),
    (None, "resumes/variants/the-app-slug"),
])
def test_a_bare_variant_name_resolves_under_resumes_variants(tmp_path, value, expected):
    assert ap.variant_dir(tmp_path, "the-app-slug", {"resume_variant": value}) == tmp_path / expected


def test_an_absolute_variant_path_is_kept(tmp_path):
    target = tmp_path / "elsewhere" / "v1"
    assert ap.variant_dir(tmp_path, "x", {"resume_variant": str(target)}) == target


# ----------------------------------------------------------------------------- the company-recruiter gate
def _no_recruiter(root):
    (root / V / "recruiter.md").unlink()


def _recruiter_gap(root):
    set_front(root / V / "recruiter.md", verdict="GAP: ask the owner")


def _recruiter_old_hash(root):
    set_front(root / V / "recruiter.md", resume_sha256="0" * 64)


def _recruiter_advance(root):          # post-mortem 2026-10-02 change 6: TML shipped on "ADVANCE (narrow)"
    set_front(root / V / "recruiter.md", verdict="ADVANCE (narrow)")


def _recruiter_conditional(root):
    set_front(root / V / "recruiter.md", verdict="APPROVE if the form allows")


@pytest.mark.parametrize("breaker, reason", [
    (_no_recruiter, "recruiter.md missing"), (_recruiter_gap, "GAP"), (_recruiter_old_hash, "re-reads the current page"),
    (_recruiter_advance, "back to the Recruiter"), (_recruiter_conditional, "back to the Recruiter"),
])
def test_build_refuses_without_the_company_recruiters_approve(js, capsys, breaker, reason):
    breaker(js)
    assert build(js) == 1
    out = capsys.readouterr().out
    assert out.startswith("REFUSED") and reason in out


def test_a_gap_passes_once_the_owner_decides(js, capsys):
    set_front(js / V / "recruiter.md", verdict="GAP: ask the owner", owner_decision="2026-10-02: send it")
    assert build(js) == 0


# ----------------------------------------------------------------------------- S-10 item 2: every sitting portal records
def test_every_registry_portal_takes_an_account_record_and_the_profile_date(js, capsys):
    write(js / "targets/platforms.yaml", yaml.safe_dump({
        "platforms": [{"id": "examplejobs", "name": "examplejobs"}, {"id": "indeed-connector", "name": "Indeed connector"}],
        "presence_surfaces": [{"id": "samplecareers", "name": "Sample Careers"}]}))
    for pid in ("examplejobs", "indeed-connector", "samplecareers"):
        args = ["--root", str(js), "account", "--platform", pid, "--company", pid, "--exists", "yes",
                "--email", "rowan.example@example.com", "--today", "2026-10-02"]
        assert ap.main(args) == 0, capsys.readouterr().err
    assert ap.main(["--root", str(js), "account", "--platform", "examplejobs", "--company", "examplejobs", "--exists", "yes",
                    "--email", "rowan.example@example.com", "--profile-filled-on", "2026-10-02",
                    "--today", "2026-10-02"]) == 0
    rows = {a["platform"]: a for a in yaml.safe_load((js / "state/accounts.yaml").read_text())["accounts"]}
    assert rows["examplejobs"]["profile_filled_on"] == "2026-10-02"
    assert rows["samplecareers"]["profile_filled_on"] is None and rows["indeed-connector"]["tenant"] is None
    assert ap.validate_accounts(yaml.safe_load((js / "state/accounts.yaml").read_text()))[1] == []
    capsys.readouterr()
    assert ap.main(["--root", str(js), "account", "--platform", "no-such-portal", "--company", "x", "--exists", "yes",
                    "--email", "rowan.example@example.com"]) == 1
    assert "unknown platform" in capsys.readouterr().err


def test_account_keeps_the_first_created_on_and_rejects_a_bad_date(js, capsys):
    # review of H2's CS (A/B, finding 1): the profile sitting re-runs the closing command with --created-on today;
    # the account's first created_on must survive
    write(js / "targets/platforms.yaml", yaml.safe_dump({"platforms": [{"id": "wellfound", "name": "Wellfound"}]}))
    base = ["--root", str(js), "account", "--platform", "wellfound", "--company", "Wellfound", "--exists", "yes",
            "--email", "rowan.example@example.com"]
    assert ap.main(base + ["--created-on", "2026-10-01", "--today", "2026-10-01"]) == 0
    assert ap.main(base + ["--created-on", "2026-10-03", "--profile-filled-on", "2026-10-03", "--today", "2026-10-03"]) == 0
    row = yaml.safe_load((js / "state/accounts.yaml").read_text())["accounts"][0]
    assert (row["created_on"], row["profile_filled_on"]) == ("2026-10-01", "2026-10-03")
    capsys.readouterr()
    try:
        rc = ap.main(base + ["--profile-filled-on", "2026-13-01"])
    except SystemExit as exc:          # argparse rejects a bad date with exit 2
        rc = exc.code
    assert rc != 0
