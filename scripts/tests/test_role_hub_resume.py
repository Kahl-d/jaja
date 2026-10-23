"""Rejection post-mortem 2026-10-02, change 3, on the role hubs: HUB.md reads the recorded variant (resume_variant as a
folder path or a [[wikilink]], resume_pdf and resume_sha256), never a folder guessed from the application slug, and
fit.md's `score` as well as `fit_score` (HUD's HUB said "unscored" against fit.md 4.2). Fixture tree only
(test_role_hub's)."""
from __future__ import annotations

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import role_hub as rh  # noqa: E402
from js_common import fm  # noqa: E402
from test_role_hub import A1, G1, dept, hub, section, w  # noqa: E402,F401  (the fixture department)


def _set(root, slug, **fields):
    path = root / "applications" / slug / "application.md"
    meta, body = fm.load(path)
    meta.update(fields)
    path.write_text(fm.dump(meta, body), encoding="utf-8")


def test_fit_md_score_is_read_as_well_as_fit_score(dept):
    w(dept / "applications" / G1 / "fit.md", "---\nscore: 4.2\nverdict: maybe\n---\nfit\n")
    rh.refresh(dept)
    fit = section(hub(dept, G1), "Fit")
    assert "score 4.2" in fit and "verdict maybe" in fit and "unscored" not in fit


def test_a_wikilink_variant_resolves_to_its_folder(dept):
    v = dept / "resumes" / "variants" / "gamma-ai-202609-b75"
    w(v / "meta.yaml", "base_of: general\neditorial_verdict: approved\n")
    _set(dept, G1, resume_variant="[[gamma-ai-202609-b75]]")
    rh.refresh(dept)
    resume = section(hub(dept, G1), "Resume variant")
    assert "[resumes/variants/gamma-ai-202609-b75/]" in resume and "editorial_verdict approved" in resume
    assert "missing: resumes/variants/[[" not in resume


def test_the_pdf_that_went_is_shown_with_its_hash(dept):
    _set(dept, A1, resume_pdf=f"resumes/variants/{A1}/inline/Alex_Example_Resume.pdf",
         resume_sha256="718dd8457145190c154ff0623e114ca53d9ae4d6be9409b040a363a5ade7de66")
    rh.refresh(dept)
    resume = section(hub(dept, A1), "Resume variant")
    assert f"- sent: [Alex_Example_Resume.pdf](../../resumes/variants/{A1}/inline/Alex_Example_Resume.pdf)" \
           " · sha256 718dd8457145" in resume


def test_no_folder_is_guessed_from_the_slug(dept):
    w(dept / "resumes" / "variants" / G1 / "meta.yaml", "base_of: general\n")   # a folder named like the slug
    rh.refresh(dept)
    resume = section(hub(dept, G1), "Resume variant")
    assert f"[resumes/variants/{G1}/]" not in resume
    assert "- missing: resume_variant not recorded" in resume and "--resume" in resume


def test_a_recorded_none_shows_its_reason(dept):
    _set(dept, G1, resume_none_reason="a referral form with no upload")
    rh.refresh(dept)
    resume = section(hub(dept, G1), "Resume variant")
    assert "- no PDF went: a referral form with no upload (resume_none_reason)" in resume


def test_variant_path_reads_every_recorded_form(dept):
    (dept / "resumes" / "variants" / "named-202609").mkdir(parents=True, exist_ok=True)
    (dept / "resumes" / "bases" / "general").mkdir(parents=True, exist_ok=True)
    assert rh.variant_path(dept, "[[named-202609#Summary|the page]]") == "resumes/variants/named-202609"
    assert rh.variant_path(dept, "named-202609") == "resumes/variants/named-202609"
    assert rh.variant_path(dept, "[[general]]") == "resumes/bases/general"
    assert rh.variant_path(dept, "resumes/variants/named-202609/inline/R.pdf") == "resumes/variants/named-202609"
    assert rh.variant_path(dept, "resumes/variants/../../secrets") is None
    assert rh.variant_path(dept, None) is None and rh.variant_path(dept, "") is None
