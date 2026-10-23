"""No applied event without the PDF. `tracker.py event <slug> applied` requires --resume <path or sha256> (or --resume none --reason
"<why>"); it writes resume_variant (the variant or base folder), resume_pdf and resume_sha256 into application.md and
names the PDF in the event's evidence. A hand submission is logged the same way, so a rejection can be traced to the page it saw. Fixture tree only."""
from __future__ import annotations

import hashlib
from datetime import timedelta

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW

import tracker as tr  # noqa: E402
from js_common import fm  # noqa: E402
from test_tracker import APP, _job  # noqa: E402  (the fixture job and its application slug)

AT = "2026-09-24T09:15:00-07:00"


def _pdf(root, rel, content=b"%PDF-1.4 page"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def _new(root):
    assert tr.main(["--root", str(root), "new", "--job", _job(root)], now=NOW) == 0


def _applied(root, *extra):
    return tr.main(["--root", str(root), "event", APP, "applied", "--at", AT, "--evidence", "Owner in chat", *extra],
                   now=NOW + timedelta(days=1))


def test_an_applied_event_without_a_resume_is_refused(root, capsys):
    _new(root)
    before = (root / "applications" / APP / "application.md").read_text()
    assert _applied(root) == 2
    err = capsys.readouterr().err
    assert "--resume" in err and "nothing was written" in err
    assert (root / "applications" / APP / "application.md").read_text() == before


def test_a_variant_pdf_path_records_the_folder_the_file_and_its_hash(root):
    _new(root)
    sha = _pdf(root, "resumes/variants/acme-ai-202609-b75/inline/AlexExample_Resume.pdf")
    assert _applied(root, "--resume", "resumes/variants/acme-ai-202609-b75/inline/AlexExample_Resume.pdf",
                    "--via", "ats") == 0
    props, body = fm.load(root / "applications" / APP / "application.md")
    assert props["resume_variant"] == "resumes/variants/acme-ai-202609-b75"
    assert props["resume_pdf"] == "resumes/variants/acme-ai-202609-b75/inline/AlexExample_Resume.pdf"
    assert props["resume_sha256"] == sha
    last = tr.parse_events(body)[-1]
    assert f"resume resumes/variants/acme-ai-202609-b75/inline/AlexExample_Resume.pdf sha256 {sha[:12]}" \
        in last.evidence


def test_a_hash_finds_the_pdf_under_resumes(root):
    """A hand submission from the download folder: the hash names which page went."""
    _new(root)
    sha = _pdf(root, "resumes/bases/general/AlexExample_Resume.pdf", b"%PDF base page")
    _pdf(root, "resumes/variants/other-202609/send/AlexExample_Resume.pdf", b"%PDF other page")
    assert _applied(root, "--resume", sha[:12]) == 0
    props, _ = fm.load(root / "applications" / APP / "application.md")
    assert props["resume_variant"] == "resumes/bases/general" and props["resume_sha256"] == sha


def test_an_unknown_hash_or_a_missing_file_is_refused(root, capsys):
    _new(root)
    assert _applied(root, "--resume", "deadbeefdeadbeef") == 2
    assert "no PDF under resumes/ has sha256 deadbeefdeadbeef" in capsys.readouterr().err
    assert _applied(root, "--resume", "resumes/variants/nope/x.pdf") == 2
    assert "no such file" in capsys.readouterr().err


def test_none_needs_a_reason_and_is_recorded(root, capsys):
    _new(root)
    assert _applied(root, "--resume", "none") == 2
    assert "--reason" in capsys.readouterr().err
    assert _applied(root, "--resume", "none", "--reason", "a referral form with no upload") == 0
    props, body = fm.load(root / "applications" / APP / "application.md")
    assert props.get("resume_variant") is None and props["resume_none_reason"] == "a referral form with no upload"
    assert "resume none: a referral form with no upload" in tr.parse_events(body)[-1].evidence


def test_resume_is_only_for_applied_events(root, capsys):
    _new(root)
    _applied(root, "--resume", "none", "--reason", "x")
    assert tr.main(["--root", str(root), "event", APP, "screen", "--resume", "none", "--reason", "x"], now=NOW) == 2
    assert "--resume is for applied events" in capsys.readouterr().err


def test_a_dry_run_checks_the_resume_and_writes_nothing(root):
    _new(root)
    _pdf(root, "resumes/bases/general/AlexExample_Resume.pdf")
    path = root / "applications" / APP / "application.md"
    before = path.read_text()
    assert _applied(root, "--resume", "resumes/bases/general/AlexExample_Resume.pdf", "--dry-run") == 0
    assert path.read_text() == before
    assert _applied(root, "--resume", "nope.pdf", "--dry-run") == 2


def test_a_hash_shared_by_two_folders_is_refused_and_names_both(root, capsys):
    """Review finding 2: the TML and xAI folders hold the same PDF; a hash alone must not pick one."""
    _new(root)
    sha = _pdf(root, "resumes/variants/tml-eval-202609/send/Resume.pdf", b"%PDF shared")
    _pdf(root, "resumes/variants/xai-evals-202609/send/Resume.pdf", b"%PDF shared")
    assert _applied(root, "--resume", sha[:12]) == 2
    err = capsys.readouterr().err
    assert "resumes/variants/tml-eval-202609" in err and "resumes/variants/xai-evals-202609" in err and "path" in err
    _pdf(root, "resumes/variants/solo-202609/send/A.pdf", b"%PDF solo")
    sha2 = _pdf(root, "resumes/variants/solo-202609/inline/A.pdf", b"%PDF solo")   # two copies, one folder: fine
    assert _applied(root, "--resume", sha2[:12]) == 0


def test_a_dry_run_shows_the_page_it_would_record(root, capsys):
    _new(root)
    _pdf(root, "resumes/bases/general/AlexExample_Resume.pdf")
    assert _applied(root, "--resume", "resumes/bases/general/AlexExample_Resume.pdf", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "would record resume resumes/bases/general/AlexExample_Resume.pdf sha256" in out
    assert "resume_variant resumes/bases/general" in out


def test_same_keeps_the_recorded_page_on_a_repeat_applied_event(root, capsys):
    """Review finding 4: a time correction appends applied again; --resume same keeps the page, never wipes it."""
    _new(root)
    assert _applied(root, "--resume", "same") == 2
    assert "nothing is recorded yet" in capsys.readouterr().err
    sha = _pdf(root, "resumes/bases/general/AlexExample_Resume.pdf")
    _applied(root, "--resume", "resumes/bases/general/AlexExample_Resume.pdf")
    assert _applied(root, "--resume", "same") == 0
    props, body = fm.load(root / "applications" / APP / "application.md")
    assert props["resume_sha256"] == sha and props["resume_variant"] == "resumes/bases/general"
    assert "resume same: resumes/bases/general/AlexExample_Resume.pdf" in tr.parse_events(body)[-1].evidence


def test_an_upper_case_pdf_is_found_and_a_link_out_of_the_tree_is_not(root, tmp_path):
    _new(root)
    sha = _pdf(root, "resumes/variants/up-202609/RESUME.PDF", b"%PDF upper")
    assert _applied(root, "--resume", sha[:12]) == 0
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF outside")
    link = root / "resumes" / "variants" / "link-202609" / "x.pdf"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    assert _applied(root, "--resume", hashlib.sha256(b"%PDF outside").hexdigest()[:12]) == 2


def test_a_reason_without_none_is_refused(root, capsys):
    _new(root)
    _pdf(root, "resumes/bases/general/AlexExample_Resume.pdf")
    assert _applied(root, "--resume", "resumes/bases/general/AlexExample_Resume.pdf", "--reason", "x") == 2
    assert "--reason goes with --resume none" in capsys.readouterr().err
