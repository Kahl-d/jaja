"""apply_pack.py fit gate (charter section 7): an E3-and-above application needs a scored fit; E2 mass needs none."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import apply_pack as ap  # noqa: E402
import test_apply_pack as tap  # noqa: E402
import yaml  # noqa: E402
from test_apply_pack import SLUG, build, doc, pack, write  # noqa: E402

js = tap.js   # the pytest fixture: a ready pack's records


def companies(root: Path, **acme) -> None:
    entry = {"name": "Acme Robotics", "slug": "acme", **acme}
    write(root / "targets" / "companies.yaml", yaml.safe_dump({"companies": [entry]}))


def test_a_mass_role_needs_no_fit(js):
    companies(js, tier="watch", segment="startup")
    build(js)
    meta, _, _ = pack(js)
    assert meta["status"] == "ready" and not any("scored fit" in b for b in meta["blockers"])


def test_a_dream_company_without_a_fit_is_blocked(js):
    companies(js, tier="dream", segment="startup")
    build(js)
    meta, _, _ = pack(js)
    assert meta["status"] == "blocked"
    [b] = [b for b in meta["blockers"] if "scored fit" in b]
    assert b.startswith("effort E4 (dream company") and f"applications/{SLUG}/fit.md" in b


def test_a_big_company_or_a_warm_path_without_a_fit_is_blocked(js):
    companies(js, segment="big-tech")
    build(js)
    assert any(b.startswith("effort E3 (big company") for b in pack(js)[0]["blockers"])
    companies(js, segment="startup")
    app = js / "applications" / SLUG / "application.md"
    app.write_text(app.read_text(encoding="utf-8").replace("\nstage:", "\npath: person\nvia_prospect: jane-doe\nstage:", 1),
                   encoding="utf-8")
    build(js)
    assert any(b.startswith("effort E4 (warm path") for b in pack(js)[0]["blockers"])


def test_a_scored_fit_md_or_job_fit_clears_the_gate(js):
    companies(js, tier="dream")
    write(js / "applications" / SLUG / "fit.md", doc({"application": SLUG, "fit_score": 4.2}, "# Fit\n"))
    build(js)
    assert not any("scored fit" in b for b in pack(js)[0]["blockers"])
    (js / "applications" / SLUG / "fit.md").write_text(doc({"fit_score": None}, "# Fit\n"), encoding="utf-8")
    assert ap.fit_score_of(js, SLUG, {"fit_score": "4.6"}) == 4.6
    assert ap.fit_score_of(js, SLUG, {"fit_score": None}) is None
    assert ap.fit_score_of(js, SLUG, {"fit_score": True}) is None


def test_fit_blocker_leaves_gate_fails_to_the_gates(tmp_path):
    assert ap.fit_blocker(tmp_path, "x", {}, {"gate": "fail"}, "acme") is None
    assert ap.fit_blocker(tmp_path, "x", {"tier": "focused"}, {}, "acme").startswith("effort E3 (heavy alignment")
