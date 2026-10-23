"""fit_gates, two lessons from live scouting:
1. "Visa Sponsorship : We provide support for relocation and visas" read as sponsorship null;
2. the new-grad gate failed early-career seats whose graduation window includes the candidate's degree month
   (the persona's is 2026-05), e.g. "Early Career Immediate Start" ("degree by summer 2026")."""
from __future__ import annotations

from pathlib import Path

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import fit_gates as fg  # noqa: E402
from test_fit_gates import gate, job  # noqa: F401

from layout import JS  # noqa: E402
@pytest.mark.parametrize("line", [
    "Visa Sponsorship : We provide support for relocation and visas.",          # verbatim
    "We offer support for relocation and visas to the right candidate.",
    "Relocation and visas: covered for this role.",
    "Visa sponsorship: yes, for this role.",
])
def test_offered_sponsorship_lines_are_a_boost(line):
    assert gate(line)["sponsorship_signal"] == "boost"


@pytest.mark.parametrize("line", [
    "Visa Sponsorship : No.",
    "Visa sponsorship: not available for this role.",
    "We do not provide support for relocation and visas.",
])
def test_a_negative_line_is_never_a_boost(line):
    assert gate(line)["sponsorship_signal"] != "boost"


@pytest.mark.real_records
def test_the_live_roles_md_boost_list_reads_support_for_visas_too():
    """targets/roles.md §8 Boost REPLACES the built-in list when present, so it must carry the new patterns."""
    cfg = fg.load_config(JS)
    assert fg.evaluate(job(), "Visa Sponsorship : We provide support for relocation and visas.", config=cfg)[
        "sponsorship_signal"] == "boost"


# ----------------------------------------------------------------------------- graduation window
EARLY_CAREER = ("Early Career Software Engineer, Immediate Start. You will have completed your degree by summer 2026 and be "
          "able to start before December 1, 2026.")


def test_a_new_grad_seat_whose_window_includes_2026_05_goes_to_review():
    out = gate(EARLY_CAREER, title="Software Engineer, Early Career (Immediate Start)")
    assert out["gate"] == "review"
    assert any("graduation window includes 2026-05" in r for r in out["reasons"])


@pytest.mark.parametrize("jd", [
    "We are hiring recent graduates who graduated between December 2025 and August 2026.",
    "Open to candidates graduating in 2025 or 2026 with a BS or MS.",
    "Class of 2026 graduates are encouraged to apply.",
])
def test_windows_that_include_may_2026(jd):
    assert gate(jd, title="New Grad Software Engineer")["gate"] == "review"


@pytest.mark.parametrize("jd", [
    "You must be graduating between December 2026 and June 2027.",
    "Graduated between June 2024 and March 2026.",
    "This is a new grad program for 2027 graduates.",
    "",                                                                          # no window named: still fails
])
def test_windows_that_exclude_may_2026_still_fail(jd):
    out = gate(jd, title="New Grad Software Engineer")
    assert out["gate"] == "fail" and "seniority: new-grad program" in out["reasons"]


def test_the_conferred_date_comes_from_facts_yaml(tmp_path):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "facts.yaml").write_text(
        "education:\n  - {id: ed-a, conferred: 2024-06-01}\n  - {id: ed-b, conferred: 2027-01-15}\n")
    assert fg.load_config(tmp_path).conferred == "2027-01"
    assert fg.load_config(tmp_path / "nowhere").conferred == fg.DEFAULT_CONFERRED
    assert fg.default_config().conferred == fg.DEFAULT_CONFERRED == "2026-05"      # the persona's policy.fit_gates.degree_conferred


# found on live records ("University Grad 2027"): an EXPECTED degree is a future graduate, not an open window
@pytest.mark.parametrize("jd", [
    "Bachelor's or Master's degree in Computer Science, expected by June 2027.",                  # verbatim shape
    "On track to graduate by June 2029.",
    "You are currently pursuing a degree, graduating by December 2026.",
])
def test_an_expected_future_degree_is_not_an_open_window(jd):
    out = gate(jd, title="University Grad Software Engineer")
    assert out["gate"] == "fail" and "seniority: new-grad program" in out["reasons"]


def test_a_degree_obtained_by_a_date_still_includes_earlier_graduates():
    jd = "A Bachelor's or Master's degree in computer science, obtained by summer 2026, or equivalent work experience."
    assert gate(jd, title="Software Engineer, Early Career")["gate"] == "review"
