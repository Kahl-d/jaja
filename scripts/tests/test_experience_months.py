"""experience_months.py: the one months-of-experience count every fit report cites (months, full months only)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1]
from layout import JS  # noqa: E402
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import experience_months as em  # noqa: E402
import fit_gates  # noqa: E402

FACTS = {"employment": [
    {"id": "exp-now", "org": "Northwind Robotics", "title": "Founding Engineer", "start": "2026-02", "end": "present",
     "kind": "experience"},
    {"id": "exp-mid", "org": "Contoso AI", "title": "AI Engineer", "start": "2025-08", "end": "2025-12",
     "kind": "experience"},
    {"id": "exp-old", "org": "Fabrikam", "title": "Data Analyst", "start": "2021-01", "end": "2022-09",
     "kind": "experience", "basis": "semester-long internship Jan-May 2021, then full-time"},
    {"id": "exp-lab", "org": "Harborview State University, NLP Lab", "title": "Researcher", "start": "2024-08",
     "end": "2025-12", "kind": "experience"},
    {"id": "exp-ta", "org": "Harborview State University", "title": "Teaching Assistant", "start": "2024-08",
     "end": "2025-08", "kind": "teaching"},
    {"id": "exp-int", "org": "Tailspin", "title": "Software Engineering Intern", "start": "2019-05", "end": "2019-07",
     "kind": "experience"}]}


def root(tmp_path: Path, clock: dict | None = None) -> Path:
    (tmp_path / "profile").mkdir(parents=True)
    (tmp_path / "profile" / "facts.yaml").write_text(yaml.safe_dump(FACTS), encoding="utf-8")
    if clock:
        (tmp_path / "clock.yaml").write_text(yaml.safe_dump(clock), encoding="utf-8")
    return tmp_path


def test_three_counts_one_per_wording(tmp_path):
    c = em.counts(root(tmp_path), date(2026, 9, 30))
    # industry: Fabrikam 21 + Contoso 5 + Northwind Feb..Sep 8; no university, teaching or intern title
    assert (c["industry"], c["excl_internships"], c["engineer_titled"]) == (34, 29, 13)
    assert [r["org"] for r in c["roles"]] == ["Northwind Robotics", "Contoso AI", "Fabrikam"]
    assert c["roles"][2]["internship_months"] == 5


def test_the_clock_end_stops_the_present_role(tmp_path):
    r = root(tmp_path, {"employer": "Northwind Robotics", "employment_end": "2026-10-02",
                        "employment_end_confirmed": True})
    assert em.counts(r, date(2026, 12, 1))["industry"] == 34      # ended 10-02: October is partial, never counted
    assert em.counts(root(tmp_path / "x"), date(2026, 12, 1))["industry"] == 36   # no clock: through November
    assert em.counts(root(tmp_path / "y"), date(2026, 11, 30))["industry"] == 36  # the last day completes November


@pytest.mark.real_records
def test_the_real_record_counts_full_months_only_and_is_stable_after_the_end_date():
    """Structure of the count on a real record: a later day within the same completed months gives the same figures."""
    base = em.counts(JS, date(2026, 9, 30))
    for day in (date(2026, 10, 6), date(2026, 12, 31)):
        c = em.counts(JS, day)
        assert (c["industry"], c["excl_internships"], c["engineer_titled"]) == \
            (base["industry"], base["excl_internships"], base["engineer_titled"]), day
    assert 0 < base["excl_internships"] <= base["industry"] and base["engineer_titled"] <= base["industry"]


def test_internship_months_reads_the_basis_line():
    assert len(em.internship_months("semester-long internship Jan-May 2021 (8th semester)")) == 5
    assert len(em.internship_months("internship June to August 2020")) == 3
    assert em.internship_months("full-time") == set() and em.internship_months(None) == set()


def test_the_line_and_check(tmp_path):
    r = root(tmp_path)
    c = em.counts(r, date(2026, 9, 30))
    line = em.line(c)
    assert line == "months: industry 34 · exclusive of internships 29 · engineer titles 13 (experience_months.py 2026-09-30)"
    fit = tmp_path / "fit.md"
    fit.write_text(f"---\nfit_score: 4.1\n---\n## Scores\n| C | 3 | asks 2 years |\n{line}\n", encoding="utf-8")
    assert em.check(fit, r) == []
    fit.write_text(fit.read_text().replace("industry 34", "industry 33"), encoding="utf-8")
    assert "counts (34, 29, 13)" in em.check(fit, r)[0]
    fit.write_text("C month arithmetic: Fabrikam 16 + Contoso 5 = 21\n", encoding="utf-8")
    assert "no `months:` line" in em.check(fit, r)[0]
    assert "no such file" in em.check(tmp_path / "nope.md", r)[0]


def test_cli(tmp_path, capsys):
    r = root(tmp_path)
    assert em.main(["--root", str(r), "--today", "2026-09-30"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("months: industry 34") and "Fabrikam, Data Analyst: 2021-01 to 2022-09 = 21" in out
    assert chr(0x2014) not in out
    assert em.main(["--root", str(r), "--today", "nope"]) == 2
    fit = tmp_path / "fit.md"
    fit.write_text("x\n", encoding="utf-8")
    assert em.main(["check", str(fit), "--root", str(r)]) == 1


def test_the_years_gate_constant_comes_from_the_config():
    """fit_gates.ENGINEER_MONTHS is policy.fit_gates.engineer_months (the owner's exclusive-of-internships count,
    kept in their own config); the count itself is computed from the record by experience_months.counts."""
    import agency_config
    assert fit_gates.ENGINEER_MONTHS == int(agency_config.get("policy.fit_gates.engineer_months"))
    assert isinstance(em.counts(JS, date(2026, 9, 30))["excl_internships"], int)


def test_fit_scorer_card_and_reference_cite_it():
    for rel in ("roles/fit-scorer.md", "roles/references/fit-scorer-reading.md"):
        text = (JS / rel).read_text(encoding="utf-8")
        assert "experience_months.py" in text, rel
    assert "internships at half" not in (JS / "roles" / "fit-scorer.md").read_text(encoding="utf-8")
