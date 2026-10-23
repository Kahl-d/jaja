"""strategy_review counts by the recorded path and channel: a recruiter-submitted application is never filed under
cold apply, its weekly floor comes from targets/goals.yaml before the retired target.yaml, and it reads the shared
learning record (state/learn). Fixture trees only."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import interviews as iv  # noqa: E402
import learn  # noqa: E402
import strategy_review as sr  # noqa: E402
import tracker  # noqa: E402

DAY = date(2026, 10, 6)
PEOPLE = {"lee-ref": {"id": "lee-ref", "tags": ["referrer"]}, "rita": {"id": "rita", "company_type": "recruiting-firm"}}


@pytest.mark.parametrize("meta, want", [
    ({"path": "person", "applied_via": "recruiter", "prospects": []}, "recruiter firm"),     # a recruiter firm submitted it
    ({"path": "person", "applied_via": "referral"}, "referral"),
    ({"path": "person", "via_prospect": "lee-ref"}, "referral"),
    ({"path": "person", "via_prospect": "rita", "applied_via": "ats"}, "recruiter firm"),
    ({"path": "person", "channel": "post"}, "post author"),
    ({"path": "person", "channel": "recruiter", "applied_via": "ats"}, "recruiter firm"),
    ({"path": "person", "applied_via": "email"}, "person (kind not recorded)"),
    ({"path": "cold", "applied_via": "referral", "prospects": ["lee-ref"]}, "cold apply"),       # the path decides
    ({"path": "unknown", "applied_via": "ats"}, "unknown"),
    ({"applied_via": "ats", "prospects": ["lee-ref"]}, "referral"),                             # no path: as before
    ({"applied_via": "ats"}, "cold apply"),
])
def test_channel_reads_path_then_channel(meta, want):
    assert sr._channel(meta, PEOPLE) == want


def app(root: Path, slug: str, **meta) -> None:
    d = root / "applications" / slug
    d.mkdir(parents=True)
    head = {"type": "application", "id": slug, "company": slug.title(), "title": "AI Engineer", **meta}
    events = [tracker.format_event("2026-10-02T17:00:00Z", "applied", "manual", 1.0, "x"),
              tracker.format_event("2026-10-03T17:00:00Z", "screen", "manual", 1.0, "x")]
    (d / "application.md").write_text("---\n" + "".join(f"{k}: {json.dumps(v)}\n" for k, v in head.items())
                                      + "---\n## Events\n" + "\n".join(events) + "\n", encoding="utf-8")


def test_the_review_files_recruiter_screens_by_path_and_reads_learn_and_goals(tmp_path, monkeypatch):
    monkeypatch.setattr(iv, "rates", lambda root, now: {"warm": 0.2, "cold": 0.01, "warm_k": 2, "warm_n": 4,
                                                         "cold_k": 0, "cold_n": 38, "unknown_n": 2})
    app(tmp_path, "initech", path="person", applied_via="recruiter", prospects=[])
    app(tmp_path, "globex", path="person", applied_via="recruiter", prospects=[])
    (tmp_path / "state" / "recruiter").mkdir(parents=True)
    (tmp_path / "state" / "recruiter" / "target.yaml").write_text("weekly_interviews: 2\n", encoding="utf-8")
    (tmp_path / "targets").mkdir()
    (tmp_path / "targets" / "goals.yaml").write_text("floors:\n  interviews_booked: {daily: null, weekly: 3}\n",
                                                     encoding="utf-8")
    learn.add(tmp_path, "recruiter", "both screens came through recruiter submits", "tracker.py metrics by path",
              day=DAY)
    r = sr.review(tmp_path, DAY)
    assert set(r["windows"]["7d"]["by"]["channel"]) == {"recruiter firm"}
    assert r["windows"]["7d"]["by"]["channel"]["recruiter firm"]["screens"] == 2
    assert r["interviews"]["target"] == 3                                  # goals.yaml, not target.yaml's 2
    assert r["learned"]["by_status"] == {"proposed": 1, "tried": 0, "kept": 0}
    text = sr.render(r)
    assert "weekly floor 3 (targets/goals.yaml; no ceiling)" in text
    assert "## Learned (state/learn, scripts/learn.py)" in text
    assert "proposed 2026-10-06 by recruiter: both screens came through recruiter submits" in text


def test_the_floor_falls_back_to_target_yaml_only_without_goals(tmp_path, monkeypatch):
    monkeypatch.setattr(iv, "rates", lambda root, now: {"warm": 0.2, "cold": 0.01, "warm_k": 0, "warm_n": 0,
                                                         "cold_k": 0, "cold_n": 0, "unknown_n": 0})
    (tmp_path / "state" / "recruiter").mkdir(parents=True)
    (tmp_path / "state" / "recruiter" / "target.yaml").write_text("weekly_interviews: 2\n", encoding="utf-8")
    v = sr.interviews_view(tmp_path, DAY, {"totals": {"applications": 0}})
    assert v["target"] == 2
