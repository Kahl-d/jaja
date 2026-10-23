"""The shared learning record (loop step 5): scripts/learn.py writes one validated JSON line per observation to
state/learn/<role>.jsonl (date, role, observation, evidence, change, status) and lists them. The CLI is stable: every
lane card's "Learned" step calls `learn.py add --role R --observation ... --evidence ... [--change ...]`. Fixture
trees only."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import learn  # noqa: E402

DAY = date(2026, 10, 6)


def cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPTS / "learn.py"), "--root", str(root), *args],
                          capture_output=True, text=True, timeout=60)


def test_add_writes_one_row_with_exactly_the_six_keys(tmp_path):
    row, written = learn.add(tmp_path, "recruiter", "Initech screen came through a recruiter submit",
                             "applications/initech-founding-engineer-202609/application.md", day=DAY)
    assert written
    lines = (tmp_path / "state" / "learn" / "recruiter.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    got = json.loads(lines[0])
    assert sorted(got) == sorted(learn.KEYS)
    assert got == {"date": "2026-10-06", "role": "recruiter", "observation": row["observation"],
                   "evidence": row["evidence"], "change": "", "status": "proposed"}


def test_the_same_row_on_the_same_day_is_written_once(tmp_path):
    args = (tmp_path, "resume", "Experience-first page got a screen", "tracker event 2026-10-06")
    assert learn.add(*args, day=DAY)[1] is True
    assert learn.add(*args, day=DAY)[1] is False
    assert learn.add(*args, status="tried", day=DAY)[1] is True          # a status move appends, never rewrites
    rows, problems = learn.read(tmp_path)
    assert problems == [] and [r["status"] for r in rows] == ["proposed", "tried"]
    assert [r["status"] for r in learn.current(rows)] == ["tried"]


@pytest.mark.parametrize("kw, why", [
    ({"role": "Recruiter"}, "kebab-case"),
    ({"observation": "  "}, "observation is empty"),
    ({"evidence": ""}, "evidence is empty"),
    ({"status": "done"}, "status"),
    ({"observation": "two\nlines"}, "one line"),
])
def test_add_refuses_a_row_that_breaks_the_schema(tmp_path, kw, why):
    base = {"role": "recruiter", "observation": "x", "evidence": "y"}
    with pytest.raises(learn.LearnError, match=why):
        learn.add(tmp_path, **{**base, **kw}, day=DAY)
    assert not (tmp_path / "state" / "learn").exists()                   # nothing was written


def test_read_reports_bad_lines_and_never_reads_them_as_rows(tmp_path):
    folder = tmp_path / "state" / "learn"
    folder.mkdir(parents=True)
    good = {"date": "2026-10-06", "role": "prep", "observation": "o", "evidence": "e", "change": "", "status": "kept"}
    (folder / "prep.jsonl").write_text(json.dumps(good) + "\n{not json\n"
                                       + json.dumps({**good, "extra": 1}) + "\n"
                                       + json.dumps({**good, "role": "hunt"}) + "\n", encoding="utf-8")
    rows, problems = learn.read(tmp_path)
    assert rows == [good]
    assert [p["line"] for p in problems] == [2, 3, 4]
    assert "unknown key extra" in problems[1]["errors"][0] and "written in prep.jsonl" in problems[2]["errors"][0]


def test_cli_add_and_list(tmp_path):
    r = cli(tmp_path, "add", "--role", "tracker-ops", "--observation", "acme-x rejected 2026-10-06: no reason given",
            "--evidence", "message id 1a2b", "--change", "", "--date", "2026-10-06")
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("wrote state/learn/tracker-ops.jsonl")
    r = cli(tmp_path, "add", "--role", "tracker-ops", "--observation", "acme-x rejected 2026-10-06: no reason given",
            "--evidence", "message id 1a2b", "--date", "2026-10-06")
    assert r.returncode == 0 and r.stdout.startswith("already recorded")
    bad = cli(tmp_path, "add", "--role", "Tracker Ops", "--observation", "x", "--evidence", "y")
    assert bad.returncode == 2 and "refused" in bad.stderr
    listed = cli(tmp_path, "list", "--json")
    assert listed.returncode == 0
    data = json.loads(listed.stdout)
    assert [r["role"] for r in data["rows"]] == ["tracker-ops"] and data["invalid"] == []
    text = cli(tmp_path, "list", "--status", "proposed").stdout
    assert "tracker-ops · proposed: acme-x rejected" in text
