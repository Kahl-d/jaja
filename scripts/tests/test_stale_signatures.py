"""stale_signatures.py: pages whose sign-off no longer matches the page, with a re-sign or retire call."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import stale_signatures as ss  # noqa: E402

YAML = "cv: {name: Test}\n"


def sha(text: str | bytes) -> str:
    return hashlib.sha256(text if isinstance(text, bytes) else text.encode()).hexdigest()


def variant(root: Path, name: str, review: dict | None = None, recruiter: dict | None = None,
            pdf: bytes | None = None) -> Path:
    v = root / "resumes" / "variants" / name
    v.mkdir(parents=True)
    (v / "resume.yaml").write_text(YAML, encoding="utf-8")
    if pdf is not None:
        (v / "AlexExample_Resume.pdf").write_bytes(pdf)
    for fname, meta in (("review.md", review), ("recruiter.md", recruiter)):
        if meta is not None:
            lines = "".join(f"{k}: {v_}\n" for k, v_ in meta.items())
            (v / fname).write_text(f"---\n{lines}---\n\nbody\n", encoding="utf-8")
    return v


def application(root: Path, slug: str, stage: str, resume_variant: str | None = None) -> None:
    a = root / "applications" / slug
    a.mkdir(parents=True)
    rv = f"resume_variant: {resume_variant}\n" if resume_variant else ""
    (a / "application.md").write_text(f"---\nid: {slug}\nstage: {stage}\n{rv}---\n", encoding="utf-8")


GOOD_REVIEW = {"verdict": "approved", "variant_hash": sha(YAML)}
GOOD_REC = {"verdict": "APPROVE", "resume_sha256": sha(YAML)}


def test_a_clean_page_is_not_listed(tmp_path, capsys):
    variant(tmp_path, "acme-ai-engineer-202610-b76", GOOD_REVIEW, GOOD_REC)
    assert ss.report(tmp_path) == []
    assert ss.main(["--root", str(tmp_path)]) == 0


def test_a_page_changed_after_signing_is_listed_with_each_stale_field(tmp_path):
    variant(tmp_path, "acme-ai-engineer-202610-b76", {"verdict": "approved", "variant_hash": "sha256:" + "a" * 64},
            {"verdict": "APPROVE", "resume_sha256": sha(YAML), "pdf_sha256": "b" * 64}, pdf=b"%PDF now")
    [row] = ss.report(tmp_path)
    assert row["stale"] == ["review.md variant_hash", "recruiter.md pdf_sha256"]


def test_an_approved_page_without_recruiter_approve_is_listed(tmp_path):
    variant(tmp_path, "a-202610-b76", GOOD_REVIEW)
    variant(tmp_path, "b-202610-b76", GOOD_REVIEW, {"verdict": "REVISE", "resume_sha256": sha(YAML)})
    variant(tmp_path, "c-202610-b76", GOOD_REVIEW, {"verdict": "'GAP: ask Alex'", "resume_sha256": sha(YAML),
                                                    "owner_decision": "send it, 2026-10-06"})
    variant(tmp_path, "d-202610-b76", {"verdict": "changes-requested", "variant_hash": sha(YAML)})
    rows = {r["variant"]: r["stale"] for r in ss.report(tmp_path)}
    assert rows == {"a-202610-b76": ["no recruiter APPROVE"], "b-202610-b76": ["no recruiter APPROVE"]}


def test_the_call_follows_the_application_stage(tmp_path):
    stale = {"verdict": "approved", "variant_hash": "c" * 64}
    variant(tmp_path, "acme-ai-202610-b76", stale)
    variant(tmp_path, "beta-ml-202610-b76", stale)
    variant(tmp_path, "delta-202610-x", stale)
    variant(tmp_path, "orphan-202609", stale)
    application(tmp_path, "acme-ai-202610", "fit_checked")                      # by folder-name prefix
    application(tmp_path, "beta-ml-202610", "applied", "resumes/variants/beta-ml-202610-b76/")
    application(tmp_path, "delta-202610", "on_hold")
    rows = {r["variant"]: (r["application"], r["call"], r["why"]) for r in ss.report(tmp_path)}
    assert rows["acme-ai-202610-b76"] == ("acme-ai-202610", "re-sign", "stage fit_checked")
    assert rows["beta-ml-202610-b76"] == ("beta-ml-202610", "retire", "stage applied")
    assert rows["delta-202610-x"] == ("delta-202610", "re-sign", "stage on_hold")
    assert rows["orphan-202609"] == ("", "retire", "no application uses this page")


def test_the_named_variant_wins_over_a_prefix(tmp_path):
    variant(tmp_path, "acme-ai-202610-b76", {"verdict": "approved", "variant_hash": "c" * 64})
    application(tmp_path, "acme-ai-202610", "applied")
    application(tmp_path, "acme-ai-202610-referral", "discovered", "resumes/variants/acme-ai-202610-b76")
    [row] = ss.report(tmp_path)
    assert row["application"] == "acme-ai-202610-referral" and row["call"] == "re-sign"


def test_an_unparsable_signature_is_listed(tmp_path):
    v = variant(tmp_path, "x-202610-b76", GOOD_REVIEW, GOOD_REC)
    (v / "recruiter.md").write_text("---\nverdict: [unclosed\n---\n", encoding="utf-8")
    [row] = ss.report(tmp_path)
    assert "recruiter.md unparsable" in row["stale"]


def test_render_and_exit_codes(tmp_path, capsys):
    variant(tmp_path, "x-202610-b76", {"verdict": "approved", "variant_hash": "d" * 64})
    assert ss.main(["--root", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert out.startswith("stale signatures: 1 page(s); 1 changed after signing")
    assert "| x-202610-b76 | - | - | review.md variant_hash; no recruiter APPROVE | retire |" in out
    assert chr(0x2014) not in out
    assert ss.main(["--root", str(tmp_path / "nowhere")]) == 2
