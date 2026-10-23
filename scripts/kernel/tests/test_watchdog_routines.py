"""watchdog.py: routine silence from receipts vs cron schedules; one inbox alert per silent routine per day."""
import datetime as dt
import json
import os
import re
import time
from pathlib import Path

import pytest

import frontmatter as fm
import sweep
import watchdog as wd

REPO = Path(__file__).resolve().parents[3]
NOW = dt.datetime(2026, 9, 25, 10, 30)  # a Friday, local time
# The PyPI package "watchdog" shares the name; conftest puts scripts/kernel first on sys.path.
assert Path(wd.__file__).resolve().parent == Path(__file__).resolve().parents[1], (
    f"imported {wd.__file__}: another 'watchdog' module shadows scripts/kernel/watchdog.py")


def spec(root: Path, name: str, schedule: str, *, enabled: bool = True, where: str = "routines",
         last_reviewed: str | None = None, owner: str = "ops-watchdog", file_stem: str | None = None) -> Path:
    folder = root / where
    folder.mkdir(parents=True, exist_ok=True)
    text = (f"name: {name}\npurpose: Test routine.\nruns_on: desktop\nengine: script\nschedule: \"{schedule}\"\n"
            f"action: script:scripts/x.py\ntier: T1\nenabled: {'true' if enabled else 'false'}\nowner: {owner}\n")
    if last_reviewed:
        text += f"last_reviewed: {last_reviewed}\n"
    path = folder / f"{file_stem or name}.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def receipt(root: Path, name: str, day: str, *stamps: str, text: str | None = None) -> Path:
    folder = root / "logs" / "runs" / name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{day}.md"
    body = text if text is not None else "".join(f"## Run {s} · ok\n- sentinel: NO_ACTION\n\n" for s in stamps)
    path.write_text(f"---\nroutine: {name}\ndate: {day}\n---\n# {name} receipts {day}\n\n{body}", encoding="utf-8")
    return path


def states(root, now: dt.datetime = NOW) -> dict[str, str]:
    return {r["name"]: r["state"] for r in wd.evaluate(root, now)["routines"]}


def inbox(root: Path) -> list[str]:
    folder = root / "work" / "inbox"
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


# --------------------------------------------------------------------------- cron
@pytest.mark.parametrize("expr, interval", [
    ("0 7 * * *", dt.timedelta(days=1)),        # morning-brief
    ("0 18 * * *", dt.timedelta(days=1)),       # evening-brief
    ("30 6 * * *", dt.timedelta(days=1)),       # nightly-sweep
    ("0 8-20 * * *", dt.timedelta(hours=1)),    # inbox-triage
    ("15 * * * *", dt.timedelta(hours=1)),      # watchdog
    ("0 17 * * 0", dt.timedelta(days=7)),       # weekly-review
    ("30 7 * * 1", dt.timedelta(days=7)),       # js-backlog-harvest
    ("30 18 * * 1-5", dt.timedelta(days=1)),    # js-evening-review, weekdays
    ("0 8 * * 1-5", dt.timedelta(days=1)),      # js-follow-up-sweep, weekdays
    ("0 7-22 * * *", dt.timedelta(hours=1)),    # js-mail-triage
    ("45 6 * * *", dt.timedelta(days=1)),       # js-morning-brief
    ("*/15 * * * *", dt.timedelta(minutes=15)),
    ("@hourly", dt.timedelta(hours=1)),
    ("@daily", dt.timedelta(days=1)),
])
def test_interval_of_the_schedules_in_use(expr, interval):
    assert wd.parse_cron(expr).interval() == interval


@pytest.mark.parametrize("expr", ["0 9 1 * *", "0 5 1 * *", "0 7 2 * *", "0 7 3 * *", "@monthly"])
def test_monthly_schedules_run_every_usual_month(expr):
    # the most common gap (a 31-day month), not the shortest (February's 28 days)
    assert wd.parse_cron(expr).interval() == dt.timedelta(days=31)
    assert wd.humanize(wd.parse_cron(expr).interval()) == "1mo"


def test_next_fire_is_strictly_after_and_respects_hours_days_and_months():
    daytime = wd.parse_cron("0 8-20 * * *")
    assert daytime.next_fire(dt.datetime(2026, 9, 25, 8, 0)) == dt.datetime(2026, 9, 25, 9, 0)
    assert daytime.next_fire(dt.datetime(2026, 9, 25, 20, 0)) == dt.datetime(2026, 9, 26, 8, 0)
    weekdays = wd.parse_cron("30 18 * * 1-5")
    assert weekdays.next_fire(dt.datetime(2026, 9, 25, 18, 31)) == dt.datetime(2026, 9, 28, 18, 30)  # Fri -> Mon
    assert wd.parse_cron("0 17 * * 0").next_fire(NOW) == dt.datetime(2026, 9, 27, 17, 0)
    assert wd.parse_cron("0 17 * * 7").next_fire(NOW) == dt.datetime(2026, 9, 27, 17, 0)  # 7 is Sunday too
    assert wd.parse_cron("0 17 * * sun").next_fire(NOW) == dt.datetime(2026, 9, 27, 17, 0)
    assert wd.parse_cron("0 9 1 * *").next_fire(NOW) == dt.datetime(2026, 10, 1, 9, 0)
    assert wd.parse_cron("0 9 1 jan *").next_fire(NOW) == dt.datetime(2027, 1, 1, 9, 0)
    assert wd.parse_cron("*/20 9 * * *").next_fire(dt.datetime(2026, 9, 25, 9, 20)) == dt.datetime(2026, 9, 25, 9, 40)
    # day-of-month and day-of-week both restricted: either matches (cron semantics)
    assert wd.parse_cron("0 9 1 * 0").next_fire(NOW) == dt.datetime(2026, 9, 27, 9, 0)


@pytest.mark.parametrize("expr, message", [
    ("", "5 fields"), ("* * * *", "5 fields"), ("* * * * * *", "5 fields"),
    ("61 * * * *", "minute: '61' is outside 0-59"), ("0 24 * * *", "hour: '24' is outside 0-23"),
    ("0 7 * * 8", "day of week: '8' is outside 0-7"), ("0 7 0 * *", "day of month: '0' is outside 1-31"),
    ("0 7 * 13 *", "month: '13' is outside 1-12"), ("5-1 * * * *", "minute: '5-1' is a reversed range"),
    ("a b c d e", "not a number"), ("*/0 * * * *", "not a valid entry"), ("0 7 31 2 *", "never fires"),
    ("@sometimes", "unknown schedule macro"),
])
def test_bad_schedules_are_refused(expr, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        wd.parse_cron(expr)


def test_every_routine_spec_in_the_repo_has_a_schedule_the_watchdog_can_read():
    specs = wd.load_routines(REPO)
    assert specs, "no routine specs found"
    for r in specs:
        assert "_error" not in r["meta"], r["path"]
        wd.parse_cron(str(r["meta"]["schedule"]))


# --------------------------------------------------------------------------- silent vs healthy
def test_daily_routine_is_ok_then_late_then_silent_after_twice_its_interval(tmp_path):
    spec(tmp_path, "morning-brief", "0 7 * * *")
    receipt(tmp_path, "morning-brief", "2026-09-23", "2026-09-23T06:58:00", "2026-09-23T07:02:10")
    assert states(tmp_path, dt.datetime(2026, 9, 24, 7, 10))["morning-brief"] == "OK"      # next run still in grace
    assert states(tmp_path, dt.datetime(2026, 9, 24, 12, 0))["morning-brief"] == "LATE"    # one run missed
    assert states(tmp_path, dt.datetime(2026, 9, 25, 7, 10))["morning-brief"] == "LATE"
    assert states(tmp_path, dt.datetime(2026, 9, 25, 7, 20))["morning-brief"] == "SILENT"  # two runs missed
    row = wd.evaluate(tmp_path, dt.datetime(2026, 9, 25, 7, 20))["routines"][0]
    assert row["last_receipt"] == "2026-09-23T07:02:10"
    assert row["receipt"] == "logs/runs/morning-brief/2026-09-23.md"
    assert row["next_due"] == "2026-09-24T07:00:00" and row["silent_after"] == "2026-09-25T07:15:00"
    assert row["interval"] == "1d"


def test_a_fresh_receipt_makes_it_healthy_again(tmp_path):
    spec(tmp_path, "morning-brief", "0 7 * * *")
    receipt(tmp_path, "morning-brief", "2026-09-23", "2026-09-23T07:02:10")
    receipt(tmp_path, "morning-brief", "2026-09-25", "2026-09-25T07:01:00")
    assert states(tmp_path, dt.datetime(2026, 9, 25, 7, 20))["morning-brief"] == "OK"


def test_hourly_daytime_routine_is_not_silent_overnight(tmp_path):
    spec(tmp_path, "inbox-triage", "0 8-20 * * *")
    receipt(tmp_path, "inbox-triage", "2026-09-24", "2026-09-24T19:00:05", "2026-09-24T20:00:04")
    assert states(tmp_path, dt.datetime(2026, 9, 25, 7, 55))["inbox-triage"] == "OK"
    assert states(tmp_path, dt.datetime(2026, 9, 25, 8, 30))["inbox-triage"] == "LATE"
    assert states(tmp_path, dt.datetime(2026, 9, 25, 9, 20))["inbox-triage"] == "SILENT"


def test_weekday_routine_is_not_silent_over_the_weekend(tmp_path):
    spec(tmp_path, "js-evening-review", "30 18 * * 1-5", where="routines")
    receipt(tmp_path, "js-evening-review", "2026-09-25", "2026-09-25T18:31:00")  # Friday
    assert states(tmp_path, dt.datetime(2026, 9, 27, 21, 0))["js-evening-review"] == "OK"  # Sunday night
    assert states(tmp_path, dt.datetime(2026, 9, 28, 19, 0))["js-evening-review"] == "LATE"
    assert states(tmp_path, dt.datetime(2026, 9, 29, 18, 50))["js-evening-review"] == "SILENT"


def test_timestamps_with_offsets_are_read_in_local_time(tmp_path):
    spec(tmp_path, "morning-brief", "0 7 * * *")
    stamp = dt.datetime(2026, 9, 25, 7, 1, 30).astimezone()  # local wall time with its offset
    utc = stamp.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    receipt(tmp_path, "morning-brief", "2026-09-25", utc)
    row = wd.evaluate(tmp_path, NOW)["routines"][0]
    assert row["last_receipt"] == "2026-09-25T07:01:30" and row["state"] == "OK"


def test_a_receipt_without_timestamps_falls_back_to_its_mtime_within_its_day(tmp_path):
    spec(tmp_path, "morning-brief", "0 7 * * *")
    path = receipt(tmp_path, "morning-brief", "2026-09-24", text="ran fine\n")
    when = time.mktime(dt.datetime(2026, 9, 24, 7, 3).timetuple())
    os.utime(path, (when, when))
    assert wd.evaluate(tmp_path, NOW)["routines"][0]["last_receipt"] == "2026-09-24T07:03:00"
    later = time.mktime(dt.datetime(2026, 9, 30, 9, 0).timetuple())  # touched later (a checkout): clamped to its day
    os.utime(path, (later, later))
    assert wd.evaluate(tmp_path, NOW)["routines"][0]["last_receipt"] == "2026-09-24T23:59:59"


def test_non_receipt_files_do_not_count(tmp_path):
    spec(tmp_path, "morning-brief", "0 7 * * *")
    folder = tmp_path / "logs" / "runs" / "morning-brief"
    folder.mkdir(parents=True)
    (folder / ".gitkeep").write_text("", encoding="utf-8")
    (folder / "notes.md").write_text("## Run 2026-09-25T07:00:00\n", encoding="utf-8")
    (folder / "2026-09-25notes.md").write_text("## Run 2026-09-25T07:00:00\n", encoding="utf-8")
    row = wd.evaluate(tmp_path, NOW)["routines"][0]
    assert row["last_receipt"] is None and row["state"] == "SILENT"


def test_an_enabled_routine_without_receipts_is_measured_from_its_review_day(tmp_path):
    spec(tmp_path, "morning-brief", "0 7 * * *", last_reviewed="2026-09-25")
    spec(tmp_path, "nightly-sweep", "30 6 * * *")  # no receipt and no last_reviewed: nothing vouches for it
    assert states(tmp_path, dt.datetime(2026, 9, 26, 6, 0)) == {"morning-brief": "OK", "nightly-sweep": "SILENT"}
    # a routine that never ran is SILENT once its first slot passes
    assert states(tmp_path, dt.datetime(2026, 9, 26, 12, 0))["morning-brief"] == "SILENT"
    assert states(tmp_path, dt.datetime(2026, 9, 27, 7, 20))["morning-brief"] == "SILENT"
    row = [r for r in wd.evaluate(tmp_path, dt.datetime(2026, 9, 27, 7, 20))["routines"] if r["name"] == "morning-brief"][0]
    assert row["measured_from"] == "2026-09-26T00:00:00" and "last_reviewed" in row["reason"]


def test_a_newer_review_resets_the_clock_after_a_pause(tmp_path):
    spec(tmp_path, "morning-brief", "0 7 * * *", last_reviewed="2026-09-25")
    receipt(tmp_path, "morning-brief", "2026-09-01", "2026-09-01T07:00:00")
    assert states(tmp_path, dt.datetime(2026, 9, 26, 6, 0))["morning-brief"] == "OK"


def test_disabled_routines_are_ignored(tmp_path, capsys):
    spec(tmp_path, "weekly-review", "0 17 * * 0", enabled=False)
    spec(tmp_path, "broken", "not a cron", enabled=False)
    report = wd.evaluate(tmp_path, NOW)
    assert {r["name"]: r["state"] for r in report["routines"]} == {"weekly-review": "OFF", "broken": "OFF"}
    assert report["state"] == "IDLE" and report["counts"]["enabled"] == 0
    assert wd.main(["--root", str(tmp_path), "--now", NOW.isoformat()]) == 0
    assert inbox(tmp_path) == []


def test_enabled_must_be_a_real_boolean(tmp_path):
    spec(tmp_path, "quoted", "0 7 * * *")
    path = tmp_path / "routines" / "quoted.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("enabled: true", 'enabled: "true"'), encoding="utf-8")
    spec(tmp_path, "unset", "0 7 * * *")
    path = tmp_path / "routines" / "unset.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("enabled: true\n", ""), encoding="utf-8")
    rows = {r["name"]: r for r in wd.evaluate(tmp_path, NOW)["routines"]}
    assert rows["quoted"]["state"] == "ERROR" and "true or false" in rows["quoted"]["reason"]
    assert rows["unset"]["state"] == "OFF"  # missing means the template default, false; lint flags the omission


def test_unwatchable_enabled_routines_are_errors(tmp_path):
    spec(tmp_path, "bad-cron", "every morning")
    spec(tmp_path, "../../escape", "0 7 * * *", file_stem="escape")
    (tmp_path / "routines" / "unreadable.yaml").write_text("name: [oops\n", encoding="utf-8")
    report = wd.evaluate(tmp_path, NOW)
    by_path = {Path(r["path"]).stem: r for r in report["routines"]}
    assert {k: r["state"] for k, r in by_path.items()} == {"bad-cron": "ERROR", "escape": "ERROR", "unreadable": "ERROR"}
    assert report["state"] == "ERROR"
    assert wd.main(["--root", str(tmp_path), "--now", NOW.isoformat()]) == 1
    assert inbox(tmp_path) == ["2026-09-25-watchdog-bad-cron.md", "2026-09-25-watchdog-escape.md",
                               "2026-09-25-watchdog-unreadable.md"]
    assert not (tmp_path / "escape").exists() and not (tmp_path.parent / "escape").exists()


def test_loader_reads_the_same_specs_as_the_sweep(tmp_path):
    spec(tmp_path, "morning-brief", "0 7 * * *")
    spec(tmp_path, "js-scout", "0 6 * * *")
    spec(tmp_path, "fixture-routine", "0 6 * * *", where="routines/fixtures")
    spec(tmp_path, "_draft", "0 6 * * *")
    mine = sorted(r["path"] for r in wd.load_routines(tmp_path))
    theirs = sorted(r["path"] for r in sweep.load_docs(tmp_path)["routines"])
    assert mine == theirs == ["routines/js-scout.yaml", "routines/morning-brief.yaml"]


def test_specs_come_from_the_code_root_and_receipts_from_the_runtime_root(tmp_path):
    from roots import Roots
    code, runtime = tmp_path / "code", tmp_path / "runtime"
    spec(code, "morning-brief", "0 7 * * *")
    receipt(runtime, "morning-brief", "2026-09-25", "2026-09-25T07:01:00")
    spec(runtime, "ignored", "0 7 * * *")                        # a spec in the runtime folder is not a routine
    roots = Roots(code, tmp_path / "data", runtime)
    assert states(roots) == {"morning-brief": "OK"}
    wd.check(roots, NOW)
    assert (runtime / "registry" / "watchdog.json").is_file() and not (code / "registry").exists()


# --------------------------------------------------------------------------- side effects
def test_a_silent_routine_gets_one_inbox_alert_per_day(tmp_path):
    spec(tmp_path, "morning-brief", "0 7 * * *", owner="ops-watchdog")
    receipt(tmp_path, "morning-brief", "2026-09-22", "2026-09-22T07:00:00")
    assert wd.main(["--root", str(tmp_path), "--now", "2026-09-25T10:30:00"]) == 1
    alert = tmp_path / "work" / "inbox" / "2026-09-25-watchdog-morning-brief.md"
    meta, body = fm.load(alert)
    assert (meta["source"], meta["routine"], meta["state"], str(meta["received"])) == (
        "alert", "morning-brief", "SILENT", "2026-09-25")
    assert "logs/runs/morning-brief/2026-09-22.md" in body and "python3 scripts/kernel/watchdog.py" in body
    # later the same day: no duplicate, even after triage moved the item out of the inbox
    alert.unlink()
    assert wd.main(["--root", str(tmp_path), "--now", "2026-09-25T11:30:00"]) == 1
    assert inbox(tmp_path) == []
    # the next day it alerts again
    assert wd.main(["--root", str(tmp_path), "--now", "2026-09-26T10:30:00"]) == 1
    assert inbox(tmp_path) == ["2026-09-26-watchdog-morning-brief.md"]


def test_an_existing_inbox_item_is_never_overwritten(tmp_path):
    spec(tmp_path, "morning-brief", "0 7 * * *")
    folder = tmp_path / "work" / "inbox"
    folder.mkdir(parents=True)
    (folder / "2026-09-25-watchdog-morning-brief.md").write_text("keep", encoding="utf-8")
    assert wd.main(["--root", str(tmp_path), "--now", NOW.isoformat()]) == 1
    assert (folder / "2026-09-25-watchdog-morning-brief.md").read_text(encoding="utf-8") == "keep"


def test_no_inbox_flag_reports_without_alerting(tmp_path):
    spec(tmp_path, "morning-brief", "0 7 * * *")
    assert wd.main(["--root", str(tmp_path), "--now", NOW.isoformat(), "--no-inbox"]) == 1
    assert inbox(tmp_path) == []
    assert wd.main(["--root", str(tmp_path), "--now", NOW.isoformat()]) == 1  # not marked as alerted
    assert inbox(tmp_path) == ["2026-09-25-watchdog-morning-brief.md"]


# --no-inbox is report only: it must not rewrite registry/watchdog.json.
def test_no_inbox_writes_nothing_at_all(tmp_path, capsys):
    spec(tmp_path, "morning-brief", "0 7 * * *")
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    assert wd.main(["--root", str(tmp_path), "--now", NOW.isoformat(), "--no-inbox", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "SILENT"           # the report is still printed
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == before
    assert not (tmp_path / "registry" / "watchdog.json").exists()


def test_no_inbox_leaves_an_existing_state_file_untouched(tmp_path):
    spec(tmp_path, "morning-brief", "0 7 * * *")
    state = tmp_path / "registry" / "watchdog.json"
    state.parent.mkdir(parents=True)
    state.write_text('{"keep": true}\n', encoding="utf-8")
    wd.main(["--root", str(tmp_path), "--now", NOW.isoformat(), "--no-inbox"])
    assert state.read_text(encoding="utf-8") == '{"keep": true}\n'


def test_no_inbox_refuses_receipt_since_a_receipt_is_a_write(tmp_path):
    spec(tmp_path, "morning-brief", "0 7 * * *")
    with pytest.raises(SystemExit):
        wd.main(["--root", str(tmp_path), "--now", NOW.isoformat(), "--no-inbox", "--receipt"])
    assert not (tmp_path / "logs").exists()


def test_state_json_and_table(tmp_path, capsys):
    spec(tmp_path, "morning-brief", "0 7 * * *", owner="ops-watchdog")
    spec(tmp_path, "weekly-review", "0 17 * * 0", enabled=False, owner="ops-watchdog")
    receipt(tmp_path, "morning-brief", "2026-09-25", "2026-09-25T07:00:30")
    assert wd.main(["--root", str(tmp_path), "--now", NOW.isoformat(), "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)
    saved = json.loads((tmp_path / "registry" / "watchdog.json").read_text(encoding="utf-8"))
    assert saved["_generated"].startswith("GENERATED by scripts/kernel/watchdog.py")
    for data in (printed, saved):
        assert data["state"] == "OK" and data["counts"] == {"enabled": 1, "ok": 1, "late": 0, "silent": 0, "error": 0, "off": 1}
        assert [r["name"] for r in data["routines"]] == ["morning-brief", "weekly-review"]
    assert wd.main(["--root", str(tmp_path), "--now", NOW.isoformat()]) == 0
    out = capsys.readouterr().out
    assert "morning-brief" in out and "OK" in out and "OFF" in out and "state OK" in out


def test_today_replays_a_past_day_at_noon(tmp_path, capsys):
    spec(tmp_path, "morning-brief", "0 7 * * *")
    receipt(tmp_path, "morning-brief", "2026-09-20", "2026-09-20T07:00:00")
    assert wd.main(["--root", str(tmp_path), "--today", "2026-09-21", "--json", "--no-inbox"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["now"] == "2026-09-21T12:00:00" and data["routines"][0]["state"] == "LATE"


# --------------------------------------------------------------------------- receipts
def test_receipt_writer_appends_sections_the_watchdog_reads(tmp_path, capsys):
    first = wd.write_receipt(tmp_path, "morning-brief", dt.datetime(2026, 9, 25, 7, 1, 5), status="ok",
                             sentinel="NEEDS_YOU", notes=["2 needs-you items", "inbox 3"])
    second = wd.write_receipt(tmp_path, "morning-brief", dt.datetime(2026, 9, 25, 18, 0, 2), status="ok")
    assert first == second == tmp_path / "logs" / "runs" / "morning-brief" / "2026-09-25.md"
    meta, body = fm.load(first)
    assert meta["routine"] == "morning-brief" and str(meta["date"]) == "2026-09-25"
    assert body.count("## Run ") == 2 and "- sentinel: NEEDS_YOU" in body and "- 2 needs-you items" in body
    latest, path, how = wd.latest_receipt(tmp_path, "morning-brief")
    assert latest == dt.datetime(2026, 9, 25, 18, 0, 2) and path == first and how == "heading"
    code = wd.main(["receipt", "nightly-sweep", "--root", str(tmp_path), "--status", "warn", "--sentinel", "NEEDS_YOU",
                    "--note", "lint: 1 finding", "--now", "2026-09-25T06:30:09"])
    assert code == 0
    assert "## Run 2026-09-25T06:30:09" in (tmp_path / "logs" / "runs" / "nightly-sweep" / "2026-09-25.md").read_text(encoding="utf-8")


def test_receipt_writer_refuses_names_that_are_not_slugs(tmp_path, capsys):
    assert wd.main(["receipt", "../../escape", "--root", str(tmp_path), "--status", "ok"]) == 1
    assert "slug" in capsys.readouterr().err
    assert not (tmp_path / "logs").exists()
    with pytest.raises(SystemExit):
        wd.main(["receipt", "morning-brief", "--root", str(tmp_path), "--status", "fine"])


def test_the_watchdog_can_leave_its_own_receipt(tmp_path):
    spec(tmp_path, "morning-brief", "0 7 * * *")
    assert wd.main(["--root", str(tmp_path), "--now", NOW.isoformat(), "--receipt"]) == 1
    text = (tmp_path / "logs" / "runs" / "watchdog" / "2026-09-25.md").read_text(encoding="utf-8")
    assert "## Run 2026-09-25T10:30:00" in text and "warn" in text and "morning-brief SILENT" in text
    assert "- sentinel: NEEDS_YOU" in text


def test_receipt_writer_can_file_under_a_named_day(tmp_path):
    # the sender files a run under the run request's day; the heading keeps the real time
    path = wd.write_receipt(tmp_path, "sender", dt.datetime(2026, 9, 25, 15, 31, 42), status="ok",
                            day=dt.date(2026, 10, 5))
    assert path.name == "2026-10-05.md"
    text = path.read_text(encoding="utf-8")
    assert "date: 2026-10-05" in text and "## Run 2026-09-25T15:31:42" in text


# A run killed at its time limit leaves nothing; so a run writes
# "started" first; a started run with no end after its grace is ERROR; a started line never counts as a fresh receipt.
START = dt.datetime(2026, 9, 25, 10, 0)


def _started(root, run_id="2026-09-25T10:00:00", notes=(), at=START):
    return wd.write_receipt(root, "hunt", at, status="started", notes=[f"run_id: {run_id}", "stages: fetch,triage", *notes])


def _hunt_spec(root):
    spec(root, "hunt", "0 7-22/3 * * *")
    receipt(root, "hunt", "2026-09-25", "2026-09-25T07:20:00")          # the 07:00 run finished


def test_started_is_a_status_the_receipt_writer_takes(tmp_path):
    assert "started" in wd.STATUSES
    text = _started(tmp_path).read_text()
    assert "· started" in text and "- run_id: 2026-09-25T10:00:00" in text


def test_a_started_run_with_its_end_is_fine(tmp_path):
    _hunt_spec(tmp_path)
    _started(tmp_path)
    wd.write_receipt(tmp_path, "hunt", START + dt.timedelta(minutes=25), status="ok", notes=["run_id: 2026-09-25T10:00:00"])
    assert states(tmp_path, START + dt.timedelta(hours=1))["hunt"] == "OK"


def test_a_started_run_inside_its_grace_is_running_not_an_error(tmp_path):
    _hunt_spec(tmp_path)
    _started(tmp_path)
    assert states(tmp_path, START + dt.timedelta(minutes=44))["hunt"] != "ERROR"


def test_a_started_run_with_no_end_after_45_minutes_is_an_error_with_an_alert(tmp_path):
    _hunt_spec(tmp_path)
    _started(tmp_path)
    report = wd.check(tmp_path, START + dt.timedelta(minutes=46))
    row = next(r for r in report["routines"] if r["name"] == "hunt")
    assert row["state"] == "ERROR" and "no end" in row["reason"] and "2026-09-25T10:00:00" in row["reason"]
    assert inbox(tmp_path) == ["2026-09-25-watchdog-hunt.md"]


def test_a_budget_moves_the_grace_to_budget_plus_25(tmp_path):
    _hunt_spec(tmp_path)
    _started(tmp_path, notes=["budget_min: 30"])
    assert states(tmp_path, START + dt.timedelta(minutes=50))["hunt"] != "ERROR"     # 30 + 25 = 55
    assert states(tmp_path, START + dt.timedelta(minutes=56))["hunt"] == "ERROR"


def test_a_started_line_never_counts_as_a_fresh_receipt(tmp_path):
    spec(tmp_path, "hunt", "0 7-22/3 * * *")
    receipt(tmp_path, "hunt", "2026-09-25", "2026-09-25T07:20:00")
    _started(tmp_path, at=dt.datetime(2026, 9, 25, 10, 0))
    last, _, _ = wd.latest_receipt(tmp_path, "hunt")
    assert last == dt.datetime(2026, 9, 25, 7, 20)
