"""SETUP-FINAL A28: every Google token (read, send, calendar) warns two days before Testing mode expires it, so the
brief and the cockpit say it in time; on 2026-10-04 the send and calendar tokens stayed green to the last day."""
from __future__ import annotations

from datetime import timedelta

import pytest

from test_connectors_doctor import NOW, READ_SCOPES, _no_keychain, by_name, env, ga, healthy, run, write_token  # noqa: F401


@pytest.mark.parametrize("kind, name, scopes", [("read", "gmail-read-token", READ_SCOPES),
                                                ("send", "gmail-send-token", None),
                                                ("calendar", "calendar-token", None)])
def test_a_token_two_days_from_expiry_is_amber_and_names_the_login(env, kind, name, scopes):
    healthy(env)
    scopes = scopes or [ga.SCOPE_SEND if kind == "send" else ga.SCOPE_CALENDAR]
    write_token(env, kind, scopes, authorized=NOW - timedelta(days=5, minutes=1))  # 7-day expiry: under 2 days left
    c = by_name(run(env))[name]
    assert c["state"] == "amber" and "log in again" in (c.get("action") or c.get("next") or str(c))
    write_token(env, kind, scopes, authorized=NOW - timedelta(days=2))            # 5 days left
    assert by_name(run(env))[name]["state"] == "green"
    # CS12 F5: amber starts at 48 hours, where sitting_order stops calling the token ok (days_left >= 2), not at 71
    write_token(env, kind, scopes, authorized=NOW - timedelta(days=4, hours=1))   # 2 days 23 hours left
    assert by_name(run(env))[name]["state"] == "green"
    write_token(env, kind, scopes, authorized=NOW - timedelta(days=5))            # exactly 2 days left
    assert by_name(run(env))[name]["state"] == "green"


def test_the_freshness_advice_reads_the_boards_schedule_from_its_spec(tmp_path):
    """CS12 F7: the advice said "every 3 h, 07:00 to 22:00" whatever the spec said."""
    import connectors_doctor as cd
    (tmp_path / "routines").mkdir()
    (tmp_path / "routines" / "js-hunt-boards.yaml").write_text('name: js-hunt-boards\nschedule: "0 6-23/2 * * *"\n')
    assert '"0 6-23/2 * * *"' in cd._boards_advice(tmp_path)
    assert "no readable spec" in cd._boards_advice(tmp_path / "missing")
