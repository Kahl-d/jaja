"""clock.py: the 90-day job clock (clock.yaml): day N, days left, week, phase, unemployment days, set."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import clock as ck  # noqa: E402
import js_common as jc  # noqa: E402

CLOCK_YAML = """\
# The 90-day job clock (test copy of the department file).
updated: 2026-09-25
employer: Example Corp
employment_end: 2026-10-02            # last working day (provisional until the owner confirms)
employment_end_confirmed: false
clock_days: 90
basis: 'Alex, 2026-09-25: "My current job ends in a week, so after that we have 90 days."'
new_employment_start: null
# legal-visa sets this only from the DSO's written answer
visa_allowance_days: null
visa_allowance_source: null
stem_extension:
  form: I-765 (c)(3)(C), 24-month STEM OPT extension
  filed: 2026-07-27
  status: pending
  auto_extension_to: 2027-03-03
  source: brain/projects/stem-opt-extension.md
phases:                               # clock weeks: week 1 = days 1-7
  - {weeks: [1, 2], name: resumes and first wave}
  - {weeks: [3, 8], name: volume and interviews}
  - {weeks: [9, 11], name: finals and offers}
  - {weeks: [12, 13], name: negotiate and decide}
"""


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "job-search"
    r.mkdir()
    (r / "clock.yaml").write_text(CLOCK_YAML, encoding="utf-8")
    return r


def st(root: Path, day: str) -> dict:
    return ck.status(ck.load(root), date.fromisoformat(day))


def edit(root: Path, **changes) -> None:
    text = (root / "clock.yaml").read_text(encoding="utf-8")
    for key, value in changes.items():
        text = "\n".join(f"{key}: {value}" if line.startswith(f"{key}:") else line for line in text.splitlines()) + "\n"
    (root / "clock.yaml").write_text(text, encoding="utf-8")


# ----------------------------------------------------------------------------- status
def test_before_the_end_date_the_day_is_negative_and_nothing_is_used(root):
    s = st(root, "2026-09-25")
    assert (s["day"], s["days_left"], s["state"], s["week"]) == (-7, 97, "before-start", 0)
    assert s["phase"] == "before the clock starts"
    assert (s["clock_start"], s["clock_end"], s["label"]) == ("2026-10-03", "2026-12-31", "provisional")
    assert s["unemployment"]["days_used"] == 0 and s["unemployment"]["start"] == "2026-10-03"
    assert s["line"] == "Job clock: day -7 of 90 · 97 days left · ends 2026-12-31 (provisional) · starts 2026-10-03"


def test_the_last_working_day_is_day_zero(root):
    s = st(root, "2026-10-02")
    assert (s["day"], s["days_left"], s["state"], s["unemployment"]["days_used"]) == (0, 90, "before-start", 0)


def test_day_one_is_the_day_after_the_employment_end(root):
    s = st(root, "2026-10-03")
    assert (s["day"], s["days_left"], s["state"], s["week"]) == (1, 89, "running", 1)
    assert s["phase"] == "resumes and first wave" and s["unemployment"]["days_used"] == 1
    assert s["line"] == "Job clock: day 1 of 90 · 89 days left · ends 2026-12-31 (provisional)"


def test_day_ninety_is_the_last_day(root):
    s = st(root, "2026-12-31")
    assert (s["day"], s["days_left"], s["state"], s["week"]) == (90, 0, "running", 13)
    assert s["phase"] == "negotiate and decide" and s["unemployment"]["days_used"] == 90
    assert s["line"] == "Job clock: day 90 of 90 · last day · ends 2026-12-31 (provisional)"
    assert "1 day left" in st(root, "2026-12-30")["line"]


def test_after_day_ninety_the_clock_has_ended(root):
    s = st(root, "2027-01-05")
    assert (s["day"], s["days_left"], s["state"], s["week"], s["phase"]) == (95, -5, "ended", None, "ended")
    assert s["line"] == "Job clock: ended 2026-12-31 (provisional) · day 95 · 5 days past the end"
    assert s["unemployment"]["days_used"] == 95


@pytest.mark.parametrize("day,week,phase", [
    ("2026-10-09", 1, "resumes and first wave"), ("2026-10-10", 2, "resumes and first wave"),
    ("2026-10-17", 3, "volume and interviews"), ("2026-11-27", 8, "volume and interviews"),
    ("2026-11-28", 9, "finals and offers"), ("2026-12-18", 11, "finals and offers"),
    ("2026-12-19", 12, "negotiate and decide"), ("2026-12-26", 13, "negotiate and decide")])
def test_weeks_run_seven_days_from_day_one_and_carry_their_phase(root, day, week, phase):
    s = st(root, day)
    assert (s["week"], s["phase"], s["weeks"]) == (week, phase, 13)


def test_a_new_job_start_stops_the_unemployment_count(root):
    edit(root, new_employment_start="2026-11-16")
    before = st(root, "2026-11-01")
    assert before["state"] == "new-job" and before["unemployment"]["days_used"] == 30
    assert before["unemployment"]["days_total"] == 44 and before["unemployment"]["last_day"] == "2026-11-15"
    after = st(root, "2026-11-20")
    assert after["unemployment"]["days_used"] == 44 and after["new_job_day"] == 45
    assert after["line"] == "Job clock: stopped · new employment starts 2026-11-16 (day 45 of 90) · ends 2026-12-31 (provisional)"
    assert st(root, "2027-02-01")["unemployment"]["days_used"] == 44


def test_a_new_job_before_the_clock_starts_uses_no_days(root):
    edit(root, new_employment_start="2026-10-03")
    s = st(root, "2026-10-20")
    assert s["unemployment"]["days_used"] == 0 and s["unemployment"]["days_total"] == 0


def test_the_label_follows_the_confirmation(root):
    assert st(root, "2026-10-03")["label"] == "provisional"
    edit(root, employment_end_confirmed="true")
    s = st(root, "2026-10-03")
    assert s["label"] == "confirmed" and s["line"].endswith("ends 2026-12-31 (confirmed)")


def test_the_visa_allowance_counts_down_only_when_the_dso_answer_is_recorded(root):
    s = st(root, "2026-10-12")
    assert s["unemployment"]["allowance"] is None and s["unemployment"]["allowance_left"] is None
    edit(root, visa_allowance_days="150", visa_allowance_source='"DSO email 2026-10-05"')
    s = st(root, "2026-10-12")
    assert (s["unemployment"]["days_used"], s["unemployment"]["allowance"], s["unemployment"]["allowance_left"]) == (10, 150, 140)
    assert s["unemployment"]["allowance_source"] == "DSO email 2026-10-05"


def test_an_allowance_without_its_written_source_is_refused(root):
    edit(root, visa_allowance_days="150")
    with pytest.raises(ck.ClockError, match="visa_allowance_source"):
        ck.load(root)


@pytest.mark.parametrize("key,value,needle", [
    ("clock_days", "0", "clock_days"), ("employment_end", "soon", "employment_end"),
    ("employment_end_confirmed", "maybe", "employment_end_confirmed"),
    ("new_employment_start", "next month", "new_employment_start")])
def test_a_malformed_clock_is_refused_with_the_reason(root, key, value, needle):
    edit(root, **{key: value})
    with pytest.raises(ck.ClockError, match=needle):
        ck.load(root)


def test_phases_must_cover_every_week_once(root):
    text = (root / "clock.yaml").read_text().replace("weeks: [3, 8]", "weeks: [4, 8]")
    (root / "clock.yaml").write_text(text)
    with pytest.raises(ck.ClockError, match="week 3"):
        ck.load(root)


def test_a_missing_clock_is_not_an_empty_one(tmp_path, capsys):
    assert ck.load(tmp_path) is None
    assert ck.main(["status", "--root", str(tmp_path)]) == 2
    assert "clock.yaml not found" in capsys.readouterr().err


def test_the_fallback_yaml_parser_reads_the_same_clock(root, monkeypatch):
    expected = st(root, "2026-11-20")
    monkeypatch.setattr(jc.fm, "HAVE_YAML", False)
    assert st(root, "2026-11-20") == expected


# ----------------------------------------------------------------------------- CLI
def test_cli_status_prints_the_clock_line_first(root, capsys):
    assert ck.main(["status", "--root", str(root), "--today", "2026-10-03"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Job clock: day 1 of 90 · 89 days left · ends 2026-12-31 (provisional)"
    text = "\n".join(out)
    assert "week 1 · resumes and first wave" in text and "unemployment days used: 1" in text
    assert "visa allowance: not set" in text and "STEM OPT extension: pending" in text


def test_cli_status_json(root, capsys):
    assert ck.main(["status", "--root", str(root), "--today", "2026-12-31", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert (data["day"], data["days_left"], data["week"], data["label"]) == (90, 0, 13, "provisional")
    assert data["stem_extension"]["source"] == "brain/projects/stem-opt-extension.md"


def test_set_employment_end_keeps_comments_and_is_provisional_unless_confirmed(root, capsys):
    assert ck.main(["set", "--root", str(root), "--employment-end", "2026-10-09", "--today", "2026-09-26"]) == 0
    text = (root / "clock.yaml").read_text()
    assert "employment_end: 2026-10-09            # last working day (provisional until the owner confirms)" in text
    assert "# legal-visa sets this only from the DSO's written answer" in text and "updated: 2026-09-26" in text
    cfg = ck.load(root)
    assert cfg["employment_end"] == date(2026, 10, 9) and cfg["employment_end_confirmed"] is False
    assert "day -7 of 90" not in capsys.readouterr().out
    assert ck.main(["set", "--root", str(root), "--employment-end", "2026-10-02", "--confirmed",
                    "--today", "2026-09-27"]) == 0
    cfg = ck.load(root)
    assert cfg["employment_end"] == date(2026, 10, 2) and cfg["employment_end_confirmed"] is True
    assert cfg["basis"].startswith("Alex, 2026-09-25") and cfg["phases"][0]["name"] == "resumes and first wave"


def test_confirmed_needs_the_date_it_confirms(root, capsys):
    before = (root / "clock.yaml").read_text()
    assert ck.main(["set", "--root", str(root), "--confirmed"]) == 2
    assert "--employment-end" in capsys.readouterr().err and (root / "clock.yaml").read_text() == before


def test_set_new_employment_start_and_clear_it(root):
    assert ck.main(["set", "--root", str(root), "--new-employment-start", "2026-11-16", "--today", "2026-11-01"]) == 0
    assert ck.load(root)["new_employment_start"] == date(2026, 11, 16)
    assert ck.main(["set", "--root", str(root), "--new-employment-start", "none", "--today", "2026-11-02"]) == 0
    assert ck.load(root)["new_employment_start"] is None


def test_set_visa_allowance_needs_the_written_source(root, capsys):
    assert ck.main(["set", "--root", str(root), "--visa-allowance-days", "150"]) == 2
    assert "--allowance-source" in capsys.readouterr().err
    assert ck.main(["set", "--root", str(root), "--visa-allowance-days", "150",
                    "--allowance-source", "DSO email 2026-10-05 (dso@example.test)", "--today", "2026-10-05"]) == 0
    cfg = ck.load(root)
    assert cfg["visa_allowance_days"] == 150 and cfg["visa_allowance_source"] == "DSO email 2026-10-05 (dso@example.test)"


def _code(argv: list[str]) -> int:
    try:
        return ck.main(argv)
    except SystemExit as exc:                     # argparse rejects a malformed value with exit 2
        return int(exc.code)


@pytest.mark.parametrize("args", [["--employment-end", "2026-13-40"], ["--new-employment-start", "soon"],
                                  ["--visa-allowance-days", "-3", "--allowance-source", "x"], []])
def test_set_refuses_bad_input_and_writes_nothing(root, args):
    before = (root / "clock.yaml").read_text()
    assert _code(["set", "--root", str(root), *args]) == 2
    assert (root / "clock.yaml").read_text() == before


def test_set_keeps_a_hash_inside_a_quoted_value(root):
    assert ck.main(["set", "--root", str(root), "--visa-allowance-days", "150", "--allowance-source",
                    'DSO email "re: #STEM" 2026-10-05', "--today", "2026-10-05"]) == 0
    assert ck.main(["set", "--root", str(root), "--visa-allowance-days", "120", "--allowance-source",
                    "DSO email 2026-10-06", "--today", "2026-10-06"]) == 0
    cfg = ck.load(root)
    assert (cfg["visa_allowance_days"], cfg["visa_allowance_source"]) == (120, "DSO email 2026-10-06")


# ----------------------------------------------------------------------------- the department file
@pytest.mark.workspace
def test_the_department_clock_is_valid_and_points_at_the_stem_case():
    cfg = ck.load(jc.JS_ROOT)
    assert cfg is not None and cfg["clock_days"] > 0 and cfg["basis"]
    assert (jc.OS_ROOT / cfg["stem_extension"]["source"]).is_file()
    assert (cfg["visa_allowance_days"] is None) or cfg["visa_allowance_source"]
    text = (jc.JS_ROOT / "clock.yaml").read_text(encoding="utf-8")
    assert "IOE" not in text          # the department stores no immigration numbers (roles/legal-visa.md)
