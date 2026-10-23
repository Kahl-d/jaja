"""Profile routing: a title -> its lens -> its profile base -> the PDF a mass application sends.

profile_route.py is the resolver; apply_batch.py (the mass base rows) and mass_page.py (the page's source) use it.
A profile is usable only with resume.yaml, a signed/<sha12>.yaml whose sha12 is sha256(resume.yaml)[:12], and its PDF;
anything else falls back to the general base, verified against resumes/bases/general/SHA256.txt.
Fixtures are fictional trees (a few lens files, a pinned general base, signed profile folders); the last tests read the
real lens files, read-only, to hold the title -> lens -> profile table."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

from layout import JS  # noqa: E402
sys.path.insert(0, str(JS / "scripts"))
import apply_batch as ab  # noqa: E402
import mass_page as mp  # noqa: E402
import profile_route as pr  # noqa: E402

import approvals  # noqa: E402
from js_common import fm  # noqa: E402
from test_apply_pack import JOB, SLUG, TODAY, V, make_tree, write  # noqa: E402,F401

DAY = TODAY.isoformat()

LENSES = {
    "software-engineer": ["Backend Engineer*", "Software Engineer, Backend*"],
    "forward-deployed": ["Forward Deployed Engineer*"],
    "ai-engineer": ["AI Engineer*", "Applied AI Engineer*"],
    "ml-infra": ["ML Infrastructure Engineer*"],
    "tool-use-and-integrations": ["Connectors Engineer*"],         # a lens with no profile package
}
GENERAL_PDF = b"%PDF-1.4 general base\n"
GENERAL_YAML = "cv:\n  name: Rowan Quill Example\nsidecar:\n  base_of: null\n"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def put_general(root: Path, pdf: bytes = GENERAL_PDF, yaml_text: str = GENERAL_YAML) -> None:
    base = root / "resumes" / "bases" / "general"
    base.mkdir(parents=True, exist_ok=True)
    (base / PDF).write_bytes(pdf)
    (base / "resume.yaml").write_text(yaml_text, encoding="utf-8")
    (base / "SHA256.txt").write_text(f"{sha(pdf)}  {PDF}\n{sha(yaml_text.encode())}  resume.yaml\n")
    (base / INLINE).write_bytes(b"%PDF-1.4 general inline\n")
    with (base / "SHA256.txt").open("a") as fh:
        fh.write(f"{sha(b'%PDF-1.4 general inline' + chr(10).encode())}  {INLINE}\n")


def put_lenses(root: Path) -> None:
    folder = root / "profile" / "lenses"
    folder.mkdir(parents=True, exist_ok=True)
    for family, titles in LENSES.items():
        listing = "\n".join(f"  - {t}" for t in titles)
        (folder / f"{family}.md").write_text(
            f"---\nfamily: {family}\nstatus: draft\ntitles_covered:\n{listing}\n"
            + ("specialist_words:\n  - forward deployed\n" if family == "forward-deployed" else "")
            + "---\n# lens\n", encoding="utf-8")


def put_profile(root: Path, slug: str, *, signed: bool = True, pdf: bool = True, resume: bool = True,
                inline: bool = False, layout: bool = False) -> Path:
    """A profile folder. signed: signed/<sha12>.yaml named for the resume.yaml bytes."""
    folder = root / "resumes" / "bases" / "profiles" / slug
    shutil.rmtree(folder, ignore_errors=True)                    # the folder is exactly what the arguments say
    folder.mkdir(parents=True, exist_ok=True)
    text = f"cv:\n  name: Rowan Quill Example\nsidecar:\n  lens: {slug}\n  base_of: general\n"
    if resume:
        (folder / "resume.yaml").write_text(text, encoding="utf-8")
    if signed:
        (folder / "signed").mkdir(exist_ok=True)
        (folder / "signed" / f"{sha(text.encode())[:12]}.yaml").write_text(text, encoding="utf-8")
    data = f"%PDF-1.4 profile {slug}\n".encode()
    if pdf:
        (folder / PDF).write_bytes(data)
    if inline:
        (folder / "inline").mkdir(exist_ok=True)
        (folder / "inline" / INLINE).write_bytes(b"%PDF-1.4 inline\n")
    if layout and pdf:
        (folder / "layout-report.json").write_text(json.dumps(
            {"pdf": PDF, "pdf_sha256": sha(data), "verdict": "PASS", "passed": True,
             "failures": []}))
    return folder


BASE_YAML = "cv:\n  name: Rowan Quill Example\n  sections:\n    education: [a]\n    experience: [b]\nsidecar:\n  base_of: null\n"


@pytest.fixture
def js(tmp_path, monkeypatch):
    """A fictional tree with no approved variant, the general base pinned with its layout PASS, no matrix, no legal/checks
    file and no fit score (so a mass row is an E2 base row)."""
    root = make_tree(tmp_path / "job-search")
    (root / V / "review.md").unlink()
    (root / "legal" / "checks" / "acme.md").unlink()
    put_general(root, b"%PDF-1.4 general base for tests\n", BASE_YAML)
    base = root / "resumes" / "bases" / "general"
    write(base / "layout" / "classic" / "layout-report.json", json.dumps(
        {"pdf": PDF, "pdf_sha256": sha((base / PDF).read_bytes()), "verdict": "PASS", "passed": True, "failures": []}))
    monkeypatch.setattr(ab, "answers_clean", lambda r, f=None: 0)
    monkeypatch.setattr(ab, "row_family", lambda r, t: "ai-engineer")
    monkeypatch.setattr(ab, "base_order_for", lambda key: "education-first")
    return root


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """approvals.decide stamps decided_at from the clock: the yes is given on the run's day."""
    import datetime as dt
    monkeypatch.setattr(approvals, "_now", lambda: dt.datetime(TODAY.year, TODAY.month, TODAY.day, 8, 30,
                                                                tzinfo=dt.timezone(dt.timedelta(hours=-7))))


def run(root, *args):
    return ab.main(["--root", str(root), "--today", DAY, *args])


def sheet(root, n=1):
    return fm.load(root / "state" / "apply-batches" / f"{DAY}-{n}.md")


def row_of(body):
    return next(line for line in body.splitlines() if SLUG in line and line.startswith("|"))


@pytest.fixture
def root(tmp_path) -> Path:
    r = tmp_path / "job-search"
    put_lenses(r)
    put_general(r)
    for slug in ("software-engineer", "forward-deployed", "ai-engineer"):
        put_profile(r, slug)
    return r


PDF = pr.PDF_NAMES["classic"]
INLINE = pr.PDF_NAMES["inline"]
GENERAL = f"resumes/bases/general/{PDF}"


def pdf_of(slug: str) -> str:
    return f"resumes/bases/profiles/{slug}/{PDF}"


# --------------------------------------------------------------------------- the resolver
def test_a_backend_title_sends_the_software_engineer_pdf(root):
    r = pr.resolve(root, "Senior Backend Engineer, AI Platform")
    assert (r.source, r.profile, r.lens, r.pdf) == ("profile", "software-engineer", "software-engineer",
                                                    pdf_of("software-engineer"))
    assert r.resume_yaml == "resumes/bases/profiles/software-engineer/resume.yaml"


def test_forward_deployed_sends_the_forward_deployed_pdf(root):
    r = pr.resolve(root, "Forward Deployed Engineer")
    assert (r.source, r.profile, r.pdf) == ("profile", "forward-deployed", pdf_of("forward-deployed"))


def test_an_unsigned_profile_falls_back_to_the_general_base(root):
    put_profile(root, "forward-deployed", signed=False)
    r = pr.resolve(root, "Forward Deployed Engineer")
    assert (r.source, r.profile, r.pdf) == ("general", None, GENERAL)
    assert "not usable" in r.why and "unsigned" in r.why and r.lens == "forward-deployed"


def test_a_missing_profile_folder_falls_back_to_the_general_base(root):
    r = pr.resolve(root, "ML Infrastructure Engineer")           # the lens maps to ml-infra; no folder exists
    assert (r.source, r.profile, r.pdf, r.lens) == ("general", None, GENERAL, "ml-infra")
    assert "ml-infra/resume.yaml is missing" in r.why


def test_a_profile_without_its_pdf_or_resume_is_not_usable(root):
    put_profile(root, "software-engineer", pdf=False)
    assert pr.resolve(root, "Backend Engineer").source == "general"
    assert not pr.profile_status(root, "software-engineer")[0]
    put_profile(root, "ai-engineer", resume=False, signed=False)
    assert pr.profile_status(root, "ai-engineer") == (False, "resumes/bases/profiles/ai-engineer/resume.yaml is missing")


def test_a_tampered_resume_yaml_falls_back_to_the_general_base(root):
    folder = root / "resumes" / "bases" / "profiles" / "software-engineer"
    assert pr.resolve(root, "Backend Engineer").source == "profile"
    with (folder / "resume.yaml").open("a") as fh:
        fh.write("# edited after it was signed\n")               # sha12 no longer names a signed copy
    r = pr.resolve(root, "Backend Engineer")
    assert (r.source, r.pdf) == ("general", GENERAL)
    assert "edited after it was signed" in r.why


def test_the_signed_file_must_be_named_for_the_resume_hash(root):
    folder = root / "resumes" / "bases" / "profiles" / "software-engineer"
    [signed] = (folder / "signed").glob("*.yaml")
    signed.rename(folder / "signed" / "000000000000.yaml")       # signed something else
    assert pr.resolve(root, "Backend Engineer").source == "general"


def test_a_lens_with_no_profile_package_and_an_unmatched_title_use_the_general_base(root):
    r = pr.resolve(root, "Connectors Engineer")
    assert (r.source, r.lens, r.pdf) == ("general", "tool-use-and-integrations", GENERAL)
    assert "no profile package for lens tool-use-and-integrations" in r.why
    r = pr.resolve(root, "Chief Gardener")
    assert (r.source, r.lens, r.pdf) == ("general", None, GENERAL) and "no lens matches" in r.why


def test_a_loose_word_overlap_is_not_a_match(root):
    # "Backend Developer" shares words with a software-engineer pattern (< 1.5): too loose to choose a resume
    picked = pr.focus_profiles.pick(root, "Backend Developer")
    assert picked and picked[0]["score"] < pr.MIN_SCORE
    r = pr.resolve(root, "Backend Developer")
    assert (r.source, r.lens) == ("general", None) and "too loose" in r.why


def test_the_general_pdf_hash_is_read_from_sha256_txt_and_checked(root):
    (root / "resumes/bases/general" / PDF).write_bytes(b"%PDF-1.4 someone edited me\n")
    with pytest.raises(pr.BaseNotPinned, match="does not match"):
        pr.resolve(root, "Chief Gardener")
    assert pr.resolve(root, "Chief Gardener", verify_general=False).pdf == GENERAL     # the caller pins it itself
    assert pr.resolve(root, "Backend Engineer").source == "profile"                      # a profile never reads it


def test_a_changed_pin_is_followed_not_a_hard_coded_hash(root):
    put_general(root, b"%PDF-1.4 the next promoted base\n", "cv:\n  name: Next\nsidecar:\n  base_of: null\n")
    assert pr.general_base(root) == (GENERAL, "resumes/bases/general/resume.yaml")


def test_a_missing_general_pin_is_refused(root):
    (root / "resumes/bases/general/SHA256.txt").write_text("")
    with pytest.raises(pr.BaseNotPinned, match="no pin"):
        pr.general_base(root)
    (root / "resumes/bases/general" / PDF).unlink()
    with pytest.raises(pr.BaseNotPinned, match="missing"):
        pr.general_base(root)


def test_inline_needs_the_profile_inline_pdf_and_the_general_inline_pin(root):
    assert pr.resolve(root, "Backend Engineer", pdf="inline").source == "general"
    put_profile(root, "software-engineer", inline=True)
    r = pr.resolve(root, "Backend Engineer", pdf="inline")
    assert (r.source, r.pdf) == ("profile", f"resumes/bases/profiles/software-engineer/inline/{INLINE}")
    assert pr.resolve(root, "Chief Gardener", pdf="inline").pdf.endswith(f"general/{INLINE}")


def test_the_override_forces_the_general_base_or_a_profile(root):
    assert pr.resolve(root, "Backend Engineer", profile="general").source == "general"
    forced = pr.resolve(root, "Chief Gardener", profile="forward-deployed")
    assert (forced.source, forced.profile) == ("profile", "forward-deployed")
    assert pr.resolve(root, "Chief Gardener", profile="ml-infra").source == "general"    # forced but not usable
    with pytest.raises(ValueError):
        pr.resolve(root, "Backend Engineer", profile="no-such-profile")


def test_the_cli_prints_the_route_and_status(root, capsys):
    assert pr.main(["Forward Deployed Engineer", "--root", str(root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["profile"] == "forward-deployed"
    assert pr.main(["--root", str(root), "--status"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"software-engineer +usable", out) and re.search(r"ml-infra +NOT usable", out)
    assert pr.main(["--root", str(root)]) == 2


# --------------------------------------------------------------------------- the table against the real lenses
REAL_TITLES = [
    ("Senior Backend Engineer, AI Platform", "software-engineer", "software-engineer"),
    ("Forward Deployed Engineer", "forward-deployed", "forward-deployed"),
    ("AI Engineer", "ai-engineer", "ai-engineer"),
    ("Founding Engineer", "founding-engineer", "founding-fullstack"),
    ("Full Stack AI Engineer", "full-stack-ai", "founding-fullstack"),
    ("AI Product Engineer", "product-engineer", "founding-fullstack"),
    ("ML Evaluation Engineer", "evaluation", "evals"),
    ("Research Engineer, NLP", "applied-ml-and-nlp", "ml-research"),
    ("Research Engineer", "research-engineer", "ml-research"),
    ("Voice AI Engineer", "voice-and-realtime-agents", "voice-realtime"),
    ("Software Engineer, Agent Harness", "agent-harness", "agents-harness"),
    ("ML Infrastructure Engineer", "ml-infra", "ml-infra"),
    ("LLM Inference Engineer", "inference-and-serving", "ml-infra"),
]


@pytest.mark.parametrize("title, lens, profile", REAL_TITLES)
@pytest.mark.real_records
def test_the_real_lens_table_routes_each_title_to_its_notes_profile(title, lens, profile):
    got_lens, _ = pr.lens_for(JS, title)
    assert got_lens == lens
    assert pr.LENS_TO_PROFILE[got_lens] == profile


@pytest.mark.real_records
def test_every_mapped_profile_is_one_of_the_nine_folders():
    slugs = set(pr.PROFILE_SLUGS)
    assert len(slugs) == 9                                        # the configured table names nine profiles


# --------------------------------------------------------------------------- apply_batch (mass base rows)
def seed_profile(js: Path, signed: bool = True, layout: bool = True, slug: str = "ai-engineer") -> Path:
    put_lenses(js)
    return put_profile(js, slug, signed=signed, layout=layout)


def test_an_e2_mass_row_sends_the_signed_profile_pdf(js):
    seed_profile(js)
    assert run(js, SLUG) == 0
    meta, body = sheet(js)
    row = row_of(body)
    assert pdf_of("ai-engineer") in row and "READY" in row
    assert "E2 profile resume (profile-ai-engineer)" in row and "profile base" in row
    assert meta["base_order"] == {SLUG: "profile-ai-engineer"}
    app = (js / "applications" / SLUG / "application.md").read_text()
    assert "base_order: profile-ai-engineer" in app


def test_an_e2_row_with_an_unsigned_profile_sends_the_general_base_and_says_why(js):
    seed_profile(js, signed=False)
    assert run(js, SLUG) == 0
    row = row_of(sheet(js)[1])
    assert GENERAL in row and pdf_of("ai-engineer") not in row
    assert "profile ai-engineer not usable" in row and "E2 base resume (education-first)" in row


def test_an_e2_row_whose_profile_page_failed_its_layout_uses_the_general_base(js):
    seed_profile(js, layout=False)
    assert run(js, SLUG) == 0
    row = row_of(sheet(js)[1])
    assert GENERAL in row and "page layout not checked" in row


def test_the_profile_flag_forces_the_general_base_and_a_tampered_profile_never_ships(js):
    folder = seed_profile(js)
    assert run(js, SLUG, "--profile", "general", "--dry-run") == 0
    assert run(js, SLUG, "--dry-run") == 0
    with (folder / "resume.yaml").open("a") as fh:
        fh.write("# tampered\n")
    assert run(js, SLUG) == 0
    row = row_of(sheet(js)[1])
    assert GENERAL in row and "edited after it was signed" in row


def test_a_tampered_general_base_is_still_refused_when_no_profile_applies(js):
    (js / "resumes/bases/general" / PDF).write_bytes(b"%PDF-1.4 tampered\n")
    assert run(js, SLUG) == 1
    assert "REFUSED" in row_of(sheet(js)[1])


def test_an_e3_row_uses_the_profile_resume_as_the_rubric_base(js, monkeypatch):
    seed_profile(js)
    seen = {}
    monkeypatch.setattr(ab, "row_effort", lambda r, j, o: ("E3", "test"))
    monkeypatch.setattr(ab, "scored_fit", lambda r, s, j: True)

    def fake_rubric(r, s, t, base=None):
        seen["base"] = base
        return 0, ""

    monkeypatch.setattr(ab, "rubric_on_base", fake_rubric)
    write(js / "research" / "targets" / f"{SLUG}.matrix.yaml", "rows: []\n")
    write(js / "applications" / SLUG / "jd.md", "# jd\n")
    run(js, SLUG)                                                # blocked on a missing file: the row still names its page
    assert seen["base"] == "resumes/bases/profiles/ai-engineer/resume.yaml"
    assert pdf_of("ai-engineer") in row_of(sheet(js)[1])


# --------------------------------------------------------------------------- mass_page (start from the profile)
def test_mass_page_starts_from_the_profile_base_not_the_general_base(root, monkeypatch):
    put_profile(root, "forward-deployed", layout=True)
    monkeypatch.setattr(mp, "JS", root)
    folder, order, why = mp.source_page("job-1", "Forward Deployed Engineer")
    assert folder == root / "resumes/bases/profiles/forward-deployed" and order == "profile-forward-deployed"
    assert "profile base" in why and (folder / "resume.yaml").is_file()


def test_mass_page_falls_back_to_the_general_base_when_the_profile_is_unusable(root, monkeypatch):
    put_profile(root, "forward-deployed", signed=False, layout=True)
    monkeypatch.setattr(mp, "JS", root)
    folder, order, why = mp.source_page("job-1", "Forward Deployed Engineer")
    assert folder == root / "resumes/bases/general" and order == "education-first"
    assert "not usable" in why
    folder, order, _ = mp.source_page("job-1", "")              # no title: no lens, the general base
    assert folder == root / "resumes/bases/general"
    put_profile(root, "forward-deployed", layout=False)         # signed but no layout PASS: not used either
    assert mp.source_page("job-1", "Forward Deployed Engineer")[0] == root / "resumes/bases/general"
