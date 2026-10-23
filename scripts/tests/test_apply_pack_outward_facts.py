"""Two outward facts on every application form:
- the written work-authorization answer is the short line alone, never run together with a note written beside it
  in legal/timers.md;
- an employer whose role has ended reads its real dates on every form: never "present", and never the current
  employer once the role has ended; the clock logic is untouched."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

from layout import JS  # noqa: E402
sys.path.insert(0, str(JS / "scripts"))
import apply_pack as ap  # noqa: E402


def test_the_written_visa_answer_is_the_short_line_alone():
    text = ("## Answer scripts\n\n- Written: \"I'm on a work permit.\"\n\n"
            "- Ruling note: kept short on purpose; the long explanation lives in the case file.\n\n## Watch list\n")
    assert ap.parse_legal(text).written == "I'm on a work permit."


def _ended_role(end_from_clock: bool = False) -> ap.Role:
    return ap.Role("df", "Founding Engineer", "Acme Labs", "2026-02", "2026-10", {(2026, m) for m in range(2, 10)},
                   "experience", True, set(), end_from_clock=end_from_clock)


def test_an_ended_employer_is_not_the_current_employer():
    rows = ap.experience_rows(None, "s", [_ended_role()])
    current = next(r for r in rows if r.question == "Current company and title")
    assert current.answer.startswith("None: not currently employed")
    assert "Founding Engineer, Acme Labs (2026-02 to 2026-10)" in current.answer
    assert "present" not in current.answer.lower() and "(since" not in current.answer
    assert "never reported as the current employer" in current.source and ap.ASK not in current.answer


def test_the_role_history_shows_the_real_end_not_present():
    for from_clock in (False, True):
        [row] = ap.role_rows([_ended_role(from_clock)], "h")
        assert row.answer.startswith("2026-02 to 2026-10") and "present" not in row.answer.lower()
        assert ap.role_span(_ended_role(from_clock)) == "Acme Labs 2026-02 to 2026-10"


def test_a_role_that_is_still_running_stays_the_current_employer():
    live = ap.Role("x", "ML Engineer", "Northwind", "2025-01", "present", {(2026, 1)}, "experience", True, set())
    current = next(r for r in ap.experience_rows(None, "s", [live]) if r.question == "Current company and title")
    assert current.answer == "ML Engineer, Northwind (since 2025-01)"


@pytest.mark.real_records
def test_the_real_facts_give_an_ended_role_its_end_and_no_present():
    ctx = ap.load_ctx(JS, date(2026, 10, 8))
    roles = ap.load_roles(ctx)
    ended = [r for r in roles if r.kind == "experience" and r.end not in ("present", "")]
    answers = [r.answer for r in ap.experience_rows(ctx, "s", roles) + ap.role_rows(roles, "h")]
    for role in ended:
        assert not any(role.org in a and "present" in a.lower() for a in answers), role.id


def test_the_clock_end_replaces_a_present_in_facts_without_showing_present():
    # facts still saying `present` plus a confirmed, passed last day (the old shape) must read the real end
    ctx = ap.load_ctx(JS, date(2026, 10, 8))
    ctx.facts = {"employment": [{"id": "e", "org": "Acme Labs", "title": "Founding Engineer",
                                 "title_render": "Founding Engineer", "start": "2026-02", "end": "present",
                                 "kind": "experience"}]}
    ctx.clock = {"employer": "Acme Labs", "employment_end": date(2026, 10, 2), "employment_end_confirmed": True}
    roles = ap.load_roles(ctx)
    assert roles and roles[0].end == "2026-10" and roles[0].end_from_clock
    assert "present" not in ap.role_span(roles[0])
    current = next(r for r in ap.experience_rows(ctx, "s", roles) if r.question == "Current company and title")
    assert current.answer.startswith("None: not currently employed")


def _facts_ctx(day: date, end: str):
    ctx = ap.load_ctx(JS, day)
    ctx.facts = {"employment": [{"id": "e", "org": "Acme Labs", "title": "Founding Engineer",
                                 "title_render": "Founding Engineer", "start": "2026-02", "end": end,
                                 "kind": "experience"}]}
    ctx.clock = {"employer": "Acme Labs", "employment_end": date(2026, 10, 2), "employment_end_confirmed": True}
    return ctx


def test_facts_ending_in_the_clock_month_never_count_the_partial_last_month():
    # the real end (2026-10) is written into facts; the count still stops at September (last day 2026-10-02)
    for day in (date(2026, 10, 6), date(2026, 12, 31)):
        [role] = ap.load_roles(_facts_ctx(day, "2026-10"))
        assert role.end == "2026-10" and not role.end_from_clock
        assert role.months == {(2026, m) for m in range(2, 10)}, day
    # before the last day it counts like a running role: through the last full month
    [role] = ap.load_roles(_facts_ctx(date(2026, 9, 20), "2026-10"))
    assert role.months == {(2026, m) for m in range(2, 9)}
