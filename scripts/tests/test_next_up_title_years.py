"""NEXT-UP: a Senior or Staff title with no stated years implies them, the same prior Fresh 10 uses
(fresh10.implied_years), so the two lists never disagree about one role; the reason says the title implies it."""
from __future__ import annotations

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import next_up as nu  # noqa: E402
from test_next_up import NOW, _app, _job, _note, _rows, world  # noqa: E402


def test_a_senior_title_with_no_stated_years_is_out_cold_and_a_stretch_through_a_note(tmp_path):
    root = world(tmp_path)
    _job(root, "sen-1", "Senior Co", "senco", title="Senior AI Engineer")
    _app(root, "senco-ai-202610", "Senior Co", "sen-1")
    row = _rows(nu.build(root, NOW))["Senior Co"]
    assert row["state"] == "BLOCKED" and row["years"] == 5 and row["years_implied"] is True
    assert "the title 'Senior AI Engineer' implies about 5 years, above the owner's years count, and no person inside" in row["why"]
    assert "asks 5" not in row["why"] and "JD was not read" not in row["why"]
    _note(root, "O-77", "pat-sen", "senco")
    row = _rows(nu.build(root, NOW))["Senior Co"]
    assert row["state"] == "PEOPLE FIRST" and row["stretch"] is True


def test_a_plain_title_is_unchanged(tmp_path):
    root = world(tmp_path)
    _job(root, "pl-1", "Plain Co", "plainco", title="AI Engineer")
    _app(root, "plainco-ai-202610", "Plain Co", "pl-1")
    row = _rows(nu.build(root, NOW))["Plain Co"]
    assert row["state"] != "BLOCKED" and row["years_implied"] is False


def test_a_staff_title_with_a_note_is_blocked_and_the_reason_does_not_deny_the_person(tmp_path):
    """the years ceiling is the fit gate even through a person inside; the reason must not say "no person"."""
    root = world(tmp_path)
    _job(root, "st-1", "Staff Co", "staffco", title="Staff AI Engineer")
    _app(root, "staffco-ai-202610", "Staff Co", "st-1")
    _note(root, "O-88", "pat-staff", "staffco")
    row = _rows(nu.build(root, NOW))["Staff Co"]
    assert row["state"] == "BLOCKED" and row["years"] == 8 and row["notes"] == ["O-88"]
    assert "no person inside" not in row["why"] and "even through a person inside" in row["why"]
    assert "the title 'Staff AI Engineer' implies about 8 years" in row["why"]


def test_stated_years_win_over_a_senior_title(tmp_path, monkeypatch):
    import fit_gates
    monkeypatch.setattr(fit_gates, "required_years", lambda jd: 2)
    root = world(tmp_path)
    _job(root, "sen-2", "Stated Co", "statedco", title="Senior AI Engineer")
    _app(root, "statedco-ai-202610", "Stated Co", "sen-2")
    row = _rows(nu.build(root, NOW))["Stated Co"]
    assert row["years"] == 2 and row["years_implied"] is False and row["state"] != "BLOCKED"
