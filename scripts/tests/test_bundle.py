"""bundle.py: pursuits/<P>/BUNDLE.md, the one page the owner reviews before approving."""
from __future__ import annotations

import hashlib
import json
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agency_config as cfg  # noqa: E402
import bundle as bu  # noqa: E402

PDF = bu.RESUME_PDF

TODAY = "2026-09-27"
P = "acme-via-rae-lin-202609"
CLOCK = """
employer: Example Corp
employment_end: 2026-10-01
employment_end_confirmed: true
clock_days: 90
basis: 'The candidate, 2026-09-25: "My current job ends in a week"'
new_employment_start: null
visa_allowance_days: null
visa_allowance_source: null
phases:
  - {weeks: [1, 13], name: search}
"""


def w(path: Path, text: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return path


def doc(meta: dict, body: str = "") -> str:
    return "---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n" + body


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def variant(root: Path, vid: str, *, signed: bool = True, ats_after_build: bool = True) -> Path:
    v = root / "resumes" / "variants" / vid
    y = w(v / "resume.yaml", f"cv:\n  name: {cfg.get('identity.name')}\n  variant: {vid}\n")
    w(v / PDF, b"%PDF " + vid.encode())
    w(v / "meta.yaml", yaml.safe_dump({"built_at": "2026-09-27T20:00:00", "pdf": PDF}))
    w(v / "review.md", doc({"target": f"resumes/variants/{vid}", "round": 3, "verdict": "approved",
                            "variant_hash": "sha256:" + (sha(y) if signed else "0" * 64)}, "# Review\n"))
    w(v / "ats-report.json", json.dumps({"pdf": f"resumes/variants/{vid}/{PDF}", "passed": True,
                                         "checked_at": "2026-09-27T20:05:00" if ats_after_build else "2026-09-27T19:00:00",
                                         "failures": [], "jd": f"applications/{vid}/jd.md"}))
    return v


def approval(os_root: Path, aid: str, action: str, expires: str, status: str = "pending", items: list | None = None,
             **extra):
    w(os_root / "work" / "approvals" / f"{aid}.md",
      doc({"type": "approval", "id": aid, "action": action, "requested_by": "role:messenger", "status": status,
           "created": "2026-09-26", "expires": expires, "project": "job-search", "items": items or [], **extra},
          "# Approval\n"))


def outbox(root: Path, oid: str, channel: str, approval_id: str, body: str, **meta):
    w(root / "outbox" / f"{oid}.md", doc({"type": "outbox", "id": oid, "prospect": meta.pop("prospect", None),
                                          "channel": channel, "status": "pending", "approval": approval_id,
                                          "attachments": meta.pop("attachments", []), **meta}, body))


def fixture(root: Path, os_root: Path) -> None:
    w(root / "clock.yaml", CLOCK)
    w(root / "prospects" / "rae-lin.md", doc({"id": "rae-lin", "name": "Rae Lin"}))
    w(root / "prospects" / "sam-ode.md", doc({"id": "sam-ode", "name": "Sam Ode"}))
    w(root / "applications" / "acme-ml-engineer-202609" / "application.md",
      doc({"id": "acme-ml-engineer-202609", "company": "Acme", "resume_variant": None}))
    w(root / "pursuits" / P / "pursuit.md", doc({
        "type": "pursuit", "id": P, "title": "Acme via Rae Lin, introduced by Sam Ode", "company": "Acme",
        "company_slug": "acme", "kind": "referral", "referrer": "rae-lin", "introducer": "sam-ode", "contacts": ["rae-lin"],
        "applications": ["acme-ml-engineer-202609"], "master_variant": "acme-referral-master-202609", "stage": "review",
        "waiting_on": "owner", "created": "2026-09-26", "updated": "2026-09-27"}, "# Acme\n\n## Decisions\n\n## Log\n"))
    variant(root, "acme-referral-master-202609")
    variant(root, "acme-ml-engineer-202609")
    approval(os_root, "A-2026-0101", "post-linkedin", "2026-10-04")
    approval(os_root, "A-2026-0102", "edit-linkedin-profile", "2026-10-03")
    approval(os_root, "A-2026-0103", "edit-linkedin-profile", "2026-10-02", status="withdrawn")
    approval(os_root, "A-2026-0104", "post-linkedin", "2026-10-05")
    outbox(root, "O-2026-0201", "linkedin-connect", "A-2026-0101", "x" * 150 + "\n", prospect="rae-lin")
    outbox(root, "O-2026-0202", "linkedin-message", "A-2026-0101", "Hello Rae.\n", prospect="rae-lin")
    outbox(root, "O-2026-0203", "linkedin-message", "A-2026-0104", "Hi.\n", prospect="someone-else")
    outbox(root, "O-2026-0204", "linkedin-message", "A-2026-0101", "Old.\n", prospect="rae-lin", status="withdrawn")
    apply_list = "apply list: state/linkedin/apply-list.md (the order, field by field)"
    for oid, first, label in (("O-2026-0301", "required correction", "About: replace the summary"),
                              ("O-2026-0302", "recommended: search terms", "(recommended) Headline"),
                              ("O-2026-0303", "optional addition", "OPTIONAL: teaching entry"),
                              ("O-2026-0304", "apply on or after 2026-10-02 only", "VERSION B: end date")):
        outbox(root, oid, "linkedin-profile-edit", "A-2026-0102", "text\n", checks=[first, apply_list], label=label)
    outbox(root, "O-2026-0305", "linkedin-profile-edit", "A-2026-0103", "text\n", checks=["required correction"])
    w(root / "hub" / "REVIEW-LIST.md", """
        # Review list

        | # | decision | why | routed to |
        |---|---|---|---|
        | 1 | **Name the product in the note** | best fit | messenger |
        | 2 | Widget title stays | default | done |
        | 3 | Tense rule | true | closed unless you say otherwise |

        ## Decisions only the owner can make

        | # | question | answer |
        |---|---|---|
        | 7 | Keep Widget? | answered: keep |
        | 8 | Which email? | open |
        """)


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    os_root = tmp_path / "os"
    root = os_root / "js"
    fixture(root, os_root)
    return root, os_root


def page(roots, today: str = TODAY) -> str:
    root, os_root = roots
    return bu.render(root, os_root, P, bu.cli_date(today))


def section(text: str, n: int) -> str:
    part = text.split(f"\n## {n}. ", 1)[1]
    return part.split("\n## ", 1)[0]


# ----------------------------------------------------------------------------- the page
def test_the_page_has_the_header_and_six_sections_in_order(roots):
    text = page(roots)
    first = text.split("\n", 1)[0]
    assert first.startswith("<!-- GENERATED by scripts/bundle.py")
    heads = [l for l in text.splitlines() if l.startswith("## ")]
    assert heads == ["## 1. Dates", "## 2. Public profile first", "## 3. Resumes", "## 4. Messages",
                     "## 5. Decisions still open", "## 6. Checks"]
    assert "—" not in text                                             # no em-dash in anything the owner reads


def test_the_page_is_deterministic(roots):
    assert page(roots) == page(roots)


# ----------------------------------------------------------------------------- 1. dates
@pytest.mark.parametrize("today,line", [
    ("2026-09-27", "- a send today, 2026-09-27, is 4 days before the last working day"),
    ("2026-10-01", "- a send today, 2026-10-01, is on the last working day"),
    ("2026-10-04", "- a send today, 2026-10-04, is 3 days after the last working day"),
])
def test_dates_place_today_against_the_last_working_day(roots, today, line):
    s = section(page(roots, today), 1)
    assert "- last working day: 2026-10-01 (clock.yaml `employment_end`, employer Example Corp)" in s
    assert line in s


def test_approval_expiries_come_soonest_first_and_an_expired_one_says_so(roots):
    s = section(page(roots), 1)
    a102, a101 = s.index("A-2026-0102 · edit-linkedin-profile · pending · expires 2026-10-03 (in 6 days)"), \
        s.index("A-2026-0101 · post-linkedin · pending · expires 2026-10-04 (in 7 days)")
    assert a102 < a101
    assert "A-2026-0104" not in s                                           # not this pursuit's
    assert "A-2026-0103" not in s                                           # withdrawn
    approval(roots[1], "A-2026-0105", "edit-portal-profile", "2026-10-09")         # highest id, latest expiry
    s = section(page(roots), 1)
    assert s.index("A-2026-0102") < s.index("A-2026-0101") < s.index("A-2026-0105")
    assert "A-2026-0102 · edit-linkedin-profile · pending · EXPIRED on 2026-10-03 (2 days ago)" in \
        section(page(roots, "2026-10-05"), 1)


def test_a_missing_clock_is_named_not_skipped(roots):
    (roots[0] / "clock.yaml").unlink()
    assert "- missing: clock.yaml (the Recruiter; `python3 scripts/clock.py set`)" in section(page(roots), 1)


def test_present_check_absent_is_named_as_not_run(roots, monkeypatch, tmp_path):
    monkeypatch.setattr(bu, "PRESENT_CHECK", tmp_path / "nowhere" / "present_check.py")
    assert "- present_check: not run: scripts/present_check.py is not in this tree (an optional check)" \
        in section(page(roots), 1)


@pytest.mark.parametrize("code,word", [(0, "clear"), (1, "STOP")])
def test_present_check_present_is_run_on_the_master_and_the_messages(roots, monkeypatch, tmp_path, code, word):
    stub = w(tmp_path / "bin" / "present_check.py",
             f"import sys\nprint('present_check: ' + ' '.join(sys.argv[1:]))\nsys.exit({code})\n")
    monkeypatch.setattr(bu, "PRESENT_CHECK", stub)
    s = section(page(roots), 1)
    line = next(l for l in s.splitlines() if l.startswith("- present_check:"))
    assert line.startswith(f"- present_check: {word} (exit {code})")
    assert "--send-date 2026-09-27" in line and "resumes/variants/acme-referral-master-202609/resume.yaml" in line
    assert "outbox/O-2026-0202.md" in line


# ----------------------------------------------------------------------------- 2. public profile
def test_public_profile_items_are_counted_by_their_labels(roots):
    s = section(page(roots), 2)
    assert ("- A-2026-0102 · edit-linkedin-profile · 4 items · apply list `state/linkedin/apply-list.md` · "
            "required 1 · recommended 1 · optional 1 · other 1") in s
    assert "  - required: O-2026-0301" in s and "  - recommended: O-2026-0302" in s
    assert "  - optional: O-2026-0303" in s and "  - other: O-2026-0304" in s
    assert "A-2026-0103" not in s and "A-2026-0101" not in s               # withdrawn; not a public-profile action


def test_a_publish_without_outbox_items_names_its_draft_and_which_replaces_which(roots):
    approval(roots[1], "A-2026-0106", "publish", "2026-10-02", draft_ref="inline: this file, Exact content section")
    approval(roots[1], "A-2026-0107", "publish", "2026-10-04", draft_ref="state/presence/github/README.md",
             supersedes="A-2026-0106 (approve ONE of the two)")
    s = section(page(roots), 2)
    assert ("- A-2026-0106 · publish · no outbox items; draft: inline: this file, Exact content section · "
            "superseded by A-2026-0107") in s
    assert ("- A-2026-0107 · publish · no outbox items; draft: state/presence/github/README.md · "
            "supersedes A-2026-0106 (approve ONE of the two)") in s


def test_no_pending_public_edit_is_said(roots):
    for f in (roots[1] / "work" / "approvals").glob("*.md"):
        f.write_text(f.read_text().replace("action: edit-linkedin-profile", "action: other"))
    assert "- none: no pending edit-linkedin-profile, edit-portal-profile or publish approval" in section(page(roots), 2)


# ----------------------------------------------------------------------------- 3. resumes
def test_resumes_list_the_master_first_with_hash_verdict_and_ats(roots):
    s = section(page(roots), 3)
    m, a = s.index("master `acme-referral-master-202609`"), s.index("variant `acme-ml-engineer-202609`")
    assert m < a
    y = roots[0] / "resumes" / "variants" / "acme-referral-master-202609" / "resume.yaml"
    assert f"resume.yaml sha256 {sha(y)}" in s
    assert "review.md approved (round 3), signs the current hash" in s
    assert "ats_check passed (0 failures, checked 2026-09-27T20:05:00)" in s
    assert f"PDF `resumes/variants/acme-referral-master-202609/{PDF}`" in s


def test_a_review_that_signs_an_old_hash_is_stale(roots):
    y = roots[0] / "resumes" / "variants" / "acme-ml-engineer-202609" / "resume.yaml"
    y.write_text(y.read_text() + "  changed: true\n")
    s = section(page(roots), 3)
    assert "STALE: review.md signs sha256 " in s and f"resume.yaml is now {sha(y)}" in s


def test_an_ats_report_older_than_the_build_is_stale(roots):
    variant(roots[0], "acme-ml-engineer-202609", ats_after_build=False)
    assert ("ats_check passed, STALE: checked 2026-09-27T19:00:00, before the build of 2026-09-27T20:00:00"
            in section(page(roots), 3))


def test_a_missing_variant_is_named(roots):
    import shutil
    shutil.rmtree(roots[0] / "resumes" / "variants" / "acme-ml-engineer-202609")
    assert "- missing: resumes/variants/acme-ml-engineer-202609/ (resume-tailor)" in section(page(roots), 3)


def test_a_review_header_that_is_not_yaml_is_read_by_line_and_named(roots):
    r = roots[0] / "resumes" / "variants" / "acme-referral-master-202609" / "review.md"
    r.write_text(r.read_text().replace("round: 3", "round: 3 (order: reordered)"))
    s = section(page(roots), 3)
    assert "review.md approved (round 3 (order: reordered)), signs the current hash" in s
    assert "note: review.md front matter is not valid YAML; read line by line (resume-editor)" in s


# ----------------------------------------------------------------------------- 4. messages
def test_messages_to_the_referrer_show_length_limit_approval_and_attachment(roots):
    s = section(page(roots), 4)
    assert ('- O-2026-0201 · linkedin-connect · to "Rae Lin" (referrer) · 150 characters, limit 200 (free account; '
            "outbox.py) · approval A-2026-0101 pending · attachments: none") in s
    assert ('- O-2026-0202 · linkedin-message · to "Rae Lin" (referrer) · 10 characters, no limit in outbox.py · '
            "approval A-2026-0101 pending · attachments: none (the master PDF "
            f"`resumes/variants/acme-referral-master-202609/{PDF}` is not attached)") in s
    assert "O-2026-0203" not in s                                            # to someone else
    assert "O-2026-0204" not in s                                            # withdrawn


def test_a_note_over_the_limit_and_a_missing_approval_are_flagged(roots):
    outbox(roots[0], "O-2026-0201", "linkedin-connect", "A-2026-0999", "y" * 201 + "\n", prospect="rae-lin")
    s = section(page(roots), 4)
    assert "201 characters, OVER the limit of 200 (free account; outbox.py)" in s
    assert "approval A-2026-0999 missing: work/approvals/A-2026-0999.md" in s


def test_an_attached_master_is_listed(roots):
    pdf = f"resumes/variants/acme-referral-master-202609/{PDF}"
    outbox(roots[0], "O-2026-0202", "email", "A-2026-0101", "Hello.\n", prospect="sam-ode",
           attachments=[f"{pdf} sha256:abc"])
    assert f'O-2026-0202 · email · to "Sam Ode" (introducer)' in section(page(roots), 4)
    assert f"attachments: `{pdf}`" in section(page(roots), 4)


# ----------------------------------------------------------------------------- 5. decisions
def test_open_decisions_are_the_rows_not_closed_answered_or_done(roots):
    s = section(page(roots), 5)
    assert '- in "Review list":\n  - #1 · Name the product in the note · messenger' in s
    assert '- in "Decisions only the owner can make":\n  - #8 · Which email? · open' in s
    assert "#2" not in s and "#3" not in s and "#7" not in s
    assert "routed to" not in s and "question" not in s                    # header rows are not decisions


def test_no_review_list_is_named(roots):
    (roots[0] / "hub" / "REVIEW-LIST.md").unlink()
    assert "- missing: hub/REVIEW-LIST.md (the orchestrator)" in section(page(roots), 5)


# ----------------------------------------------------------------------------- 6. checks
def test_every_section_names_the_command_that_reproduces_it(roots):
    s = section(page(roots), 6)
    for n, cmd in ((1, "python3 scripts/clock.py status"), (2, "python3 scripts/outbox.py list"),
                   (3, "shasum -a 256 resumes/variants/acme-referral-master-202609/resume.yaml"),
                   (3, "python3 scripts/ats_check.py check --pdf resumes/variants/acme-ml-engineer-202609/"
                       f"{PDF} --jd applications/acme-ml-engineer-202609/jd.md"),
                   (4, "awk 'f>=2;/^---$/{f++}' outbox/O-2026-0201.md | wc -m"), (5, "grep -n '^|' hub/REVIEW-LIST.md"),
                   (6, f"python3 scripts/bundle.py {P} --print")):
        assert f"- {n}: `{cmd}" in s, cmd


# ----------------------------------------------------------------------------- the file
def test_main_writes_once_then_reports_unchanged_and_print_writes_nothing(roots, capsys):
    root, os_root = roots
    args = [P, "--root", str(root), "--os-root", str(os_root), "--today", TODAY]
    out = root / "pursuits" / P / "BUNDLE.md"
    assert bu.main(args + ["--print"]) == 0 and not out.exists()
    assert "## 6. Checks" in capsys.readouterr().out
    assert bu.main(args) == 0 and out.is_file()
    assert "wrote pursuits/" in capsys.readouterr().out
    assert bu.main(args) == 0
    assert "unchanged" in capsys.readouterr().out


def test_an_unknown_pursuit_exits_2(roots, capsys):
    root, os_root = roots
    assert bu.main(["nope", "--root", str(root), "--os-root", str(os_root)]) == 2
    assert "pursuits/nope/pursuit.md" in capsys.readouterr().err
