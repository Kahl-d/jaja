"""prospects.py: a manual `set` never leaves score_reasons contradicting the record.

Before: `set warmth=3` wrote warmth 3 and locked it, but score_reasons still said "warmth 1.25: recency ...",
the script's old number. Now every overridden component (warmth, fit_score, priority, sponsorship_hint) is
marked `<field> (set manually <date>: <value>)` by `set` and by every later `score`, the date being the day it
was set (`locked_on`, script-maintained). A manual sponsorship_hint re-gates an unlocked fit, because fit derives
from it (a plain sponsorship "no" weighs the fit down; only a hard work-authorization line gates). Other components keep the lines they were scored with.
"""
from __future__ import annotations

import json
import re
from datetime import date

import pytest

import comms_helpers as ch  # noqa: F401  (puts scripts/ on sys.path)
from comms_helpers import offline  # noqa: F401  (autouse: no network)
import js_common as jc
import js_comms_util as cu
import prospects as pr

TODAY = date(2026, 9, 24)
MARK = re.compile(r"^(warmth|fit_score|priority|sponsorship_hint) \(set manually ")


def _event(key, name, *, company="Acme", title="Recruiter", email=None,
           text="Hi Alex, we're hiring an ML Engineer on my team. Open to a chat?"):
    return {"key": key, "at": "2026-09-20T17:00:00Z", "direction": "in", "channel": "email",
            "source": "gmail-backlog", "label": "recruiter_outreach", "confidence": 0.9, "create": True,
            "person": {"name": name, "emails": [email or f"{key}@acme.test"], "linkedin_url": "", "company": company,
                       "title": title},
            "subject": "Role", "summary": text[:100], "text": text, "thread_id": key, "message_id": f"<{key}@x>",
            "references": "", "reply_to": "", "tags": [], "fingerprint": None}


@pytest.fixture
def roots(tmp_path):
    js = tmp_path / "os" / "domains" / "career" / "projects" / "job-search"
    js.mkdir(parents=True)
    r = cu.Roots(js=js, os=tmp_path / "os")
    pr.ingest_events(r, [_event("rae", "Rae Lin")], today=TODAY)
    return r


def cli(roots, *args, today="2026-09-24"):
    return pr.main(["--root", str(roots.js), "--today", today, *args])


def load(roots, slug="rae-lin"):
    return jc.read_record(roots.prospects / f"{slug}.md")[0]


def test_the_ingested_record_starts_with_computed_reasons(roots):
    meta = load(roots)
    assert re.match(r"^warmth \d+\.\d\d: recency", meta["score_reasons"][0])
    assert meta["fit_score"] == 4.5 and "top target role" in meta["score_reasons"]


def test_set_marks_every_overridden_component_and_drops_the_old_numbers(roots, capsys):
    assert cli(roots, "set", "rae-lin", "warmth=3", "fit_score=4.0", "priority=P1", "sponsorship_hint=yes") == 0
    meta = load(roots)
    assert meta["score_reasons"] == [
        "warmth (set manually 2026-09-24: 3)",
        "fit_score (set manually 2026-09-24: 4.0)",
        "priority (set manually 2026-09-24: P1)",
        "sponsorship_hint (set manually 2026-09-24: yes)",
    ]
    assert not any(re.match(r"^warmth \d", r) for r in meta["score_reasons"])
    assert {k: str(v) for k, v in meta["locked_on"].items()} == {
        "warmth": "2026-09-24", "fit_score": "2026-09-24", "priority": "2026-09-24", "sponsorship_hint": "2026-09-24"}


def test_a_single_manual_value_keeps_the_other_components_as_scored(roots):
    before = load(roots)
    assert pr.set_fields(roots, "rae-lin", fields={"warmth": 3}, today=TODAY)
    meta = load(roots)
    assert meta["score_reasons"] == ["warmth (set manually 2026-09-24: 3)", *before["score_reasons"][1:]]
    assert meta["fit_score"] == before["fit_score"] and meta["warmth"] == 3


def test_the_priority_flag_is_marked_like_a_field(roots):
    assert cli(roots, "set", "rae-lin", "--priority", "P0") == 0
    assert "priority (set manually 2026-09-24: P0)" in load(roots)["score_reasons"]


def test_a_later_score_run_keeps_the_values_and_the_marks_with_the_day_they_were_set(roots, capsys):
    assert cli(roots, "set", "rae-lin", "warmth=3", "priority=P1") == 0
    assert cli(roots, "score", "--all", today="2026-09-30") == 0
    meta = load(roots)
    assert (meta["warmth"], meta["priority"]) == (3, "P1")
    assert meta["score_reasons"][0] == "warmth (set manually 2026-09-24: 3)"
    assert "priority (set manually 2026-09-24: P1)" in meta["score_reasons"]
    assert "top target role" in meta["score_reasons"]                     # the unlocked fit is still explained
    assert not any(re.match(r"^warmth \d", r) for r in meta["score_reasons"])


def test_setting_sponsorship_no_reweighs_an_unlocked_fit(roots):
    """A manual "no" weighs the fit down by 0.5 and flags it; it never gates."""
    assert load(roots)["fit_score"] == 4.5
    pr.set_fields(roots, "rae-lin", fields={"sponsorship_hint": "no"}, today=TODAY)
    meta = load(roots)
    assert meta["fit_score"] == 4.0
    assert pr.SOFT_REASON in meta["score_reasons"] and "top target role" in meta["score_reasons"]
    assert meta["score_reasons"][-1] == "sponsorship_hint (set manually 2026-09-24: no)"
    assert re.match(r"^warmth \d", meta["score_reasons"][0])           # warmth was not touched


def test_a_manual_fit_survives_sponsorship_no_and_no_stale_sponsorship_line_is_left(roots):
    """A manual fit clears the script's sponsorship line; the manual marks say what a person chose."""
    pr.set_fields(roots, "rae-lin", fields={"fit_score": 4.0}, today=TODAY)
    pr.set_fields(roots, "rae-lin", fields={"sponsorship_hint": "no"}, today=date(2026, 9, 25))
    meta = load(roots)
    assert meta["fit_score"] == 4.0
    assert meta["score_reasons"][1:] == ["fit_score (set manually 2026-09-24: 4.0)",
                                         "sponsorship_hint (set manually 2026-09-25: no)"]


def test_setting_other_fields_leaves_score_reasons_alone(roots):
    before = load(roots)["score_reasons"]
    pr.set_fields(roots, "rae-lin", fields={"next_touch_at": "2026-10-01"}, status="triaged", today=TODAY)
    assert load(roots)["score_reasons"] == before


def test_a_lock_without_a_recorded_date_says_so():
    meta = {"id": "x", "name": "X", "role": "recruiter", "status": "triaged", "warmth": 2, "fit_score": 3.0,
            "locked_fields": ["warmth"], "last_inbound_at": "2026-09-20T17:00:00Z"}
    reasons = pr.score_prospect(meta, TODAY)["score_reasons"]
    assert reasons[0] == "warmth (set manually, date not recorded: 2)"


def test_locked_on_is_maintained_by_the_script(roots, capsys):
    before = (roots.prospects / "rae-lin.md").read_text()
    assert cli(roots, "set", "rae-lin", "locked_on=2020-01-01") == 2
    assert "maintained by the script" in json.loads(capsys.readouterr().out)["error"]
    assert (roots.prospects / "rae-lin.md").read_text() == before
