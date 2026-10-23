"""The morning brief runs the gmail-sync read-and-record pass once a day (its step 2c), so the mail line says that, with the time and the next run,
read from the routine spec (routines/js-morning-brief.yaml: schedule, enabled, a gmail-sync step) or from the installed
prompt the spec names ("prompt in <path>"). After a run it names the receipt. Without a morning pass the hourly-routine wording
stays. Fixture tree only (test_dashboard_status's)."""
from __future__ import annotations

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

from test_dashboard_status import populate, run  # noqa: E402


def _owner_line(root):
    text = (root / "DASHBOARD.md").read_text()
    section = text[text.index("## Status"):text.index("## Needs Alex")]
    return next(ln for ln in section.splitlines() if ln.startswith("- Waiting on Alex:"))


def _routines(root, morning: str):
    (root / "routines").mkdir(exist_ok=True)
    (root / "routines" / "js-mail-triage.yaml").write_text("name: js-mail-triage\nenabled: false\n", encoding="utf-8")
    (root / "routines" / "js-morning-brief.yaml").write_text(morning, encoding="utf-8")


def test_the_morning_pass_from_the_routine_spec(root, os_root):
    populate(root, os_root)                                     # the fixture's now: 2026-09-24 13:00 UTC = 06:00 PT
    _routines(root, 'name: js-morning-brief\nschedule: "45 6 * * *"\nenabled: true\n'
                    'purpose: "... step 2c runs the gmail-sync skill once (read and record only)"\n')
    run(root, os_root)
    line = _owner_line(root)
    assert ("mail review: the mail pass has not run yet (no receipt in logs/runs/js-mail-triage/); the morning "
            "brief runs it daily at 06:45 PT (gmail-sync, read and record only), next 2026-09-24 06:45 PT") in line
    assert "hourly routine" not in line and "daily mail check" not in line


def test_the_morning_pass_from_the_installed_prompt_the_spec_names(root, os_root, tmp_path):
    populate(root, os_root)
    prompt = tmp_path / "SKILL.md"
    prompt.write_text("2c. Run the gmail-sync skill once ... \n", encoding="utf-8")
    _routines(root, f'# prompt in {prompt}\nname: js-morning-brief\nschedule: "45 6 * * *"\nenabled: true\n'
                    'connectors: []\n')
    run(root, os_root)
    assert "the morning brief runs it daily at 06:45 PT" in _owner_line(root)


def test_after_a_run_the_receipt_is_named_with_the_daily_pass(root, os_root):
    populate(root, os_root)
    _routines(root, 'name: js-morning-brief\nschedule: "45 6 * * *"\nenabled: true\npurpose: gmail-sync step\n')
    rec = os_root / "logs" / "runs" / "js-mail-triage" / "2026-09-24.md"
    rec.parent.mkdir(parents=True)
    rec.write_text("receipt\n", encoding="utf-8")
    run(root, os_root)
    line = _owner_line(root)
    assert "mail-triage last ran 2026-09-24 (logs/runs/js-mail-triage/2026-09-24.md)" in line
    assert "the morning brief runs it daily at 06:45 PT" in line and "has not run" not in line


def test_without_a_morning_pass_the_k70_wording_stays(root, os_root):
    populate(root, os_root)
    _routines(root, 'name: js-morning-brief\nschedule: "45 6 * * *"\nenabled: true\nconnectors: []\n')
    run(root, os_root)
    assert "the hourly routine is off," in _owner_line(root)


def test_a_header_that_wraps_the_prompt_path_is_read(root, os_root, tmp_path):
    """The real spec wraps it: '... task `js-morning-brief` (prompt in' / '# ~/.claude/.../SKILL.md); runs_on ...'."""
    populate(root, os_root)
    prompt = tmp_path / "SKILL.md"
    prompt.write_text("2c. Run the gmail-sync skill once \n", encoding="utf-8")
    _routines(root, f"# Installed as the scheduled task `js-morning-brief` (prompt in\n# {prompt}); runs_on desktop\n"
                    'name: js-morning-brief\nschedule: "45 6 * * *"\nenabled: true\nconnectors: []\n')
    run(root, os_root)
    assert "the morning brief runs it daily at 06:45 PT" in _owner_line(root)


def test_open_items_keep_the_daily_pass_and_a_stale_receipt_says_missed_runs():
    """Review findings 5 and 6."""
    import dashboard as db
    from datetime import datetime, timezone
    run_ = {"receipt": None, "last_run": None, "routine_enabled": False, "gmail_pulled": None, "gmail_new": None,
            "morning": {"time": "06:45", "next": "2026-10-03 06:45"}}
    assert "the morning brief runs it daily at 06:45 PT" in db.mail_line(2, run_)
    stale = {**run_, "last_run": "2026-09-29", "receipt": "logs/runs/js-mail-triage/2026-09-29.md"}
    now = datetime(2026, 10, 2, 20, 0, tzinfo=timezone.utc)                    # 13:00 PT, after today's 06:45 slot
    assert "missed 3 daily runs (2026-09-30 to 2026-10-02)" in db.mail_line(0, stale, now)
    fresh = {**run_, "last_run": "2026-10-02", "receipt": "logs/runs/js-mail-triage/2026-10-02.md"}
    assert "missed" not in db.mail_line(0, fresh, now)


def test_no_prompt_file_and_no_spec_step_means_no_claim(root, os_root):
    """Review finding 4: a missing installed prompt never makes the line claim a morning pass."""
    populate(root, os_root)
    _routines(root, '# prompt in /nonexistent/SKILL.md\nname: js-morning-brief\nschedule: "45 6 * * *"\nenabled: true\n')
    run(root, os_root)
    assert "morning brief runs it" not in _owner_line(root)
