"""presence_check.py: every public surface in state/presence/portals.yaml says the current truth."""
from __future__ import annotations

import hashlib
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import presence_check as pc  # noqa: E402

TODAY = "2026-09-28"
CLOCK = """
employer: Acme Corp
employment_end: 2026-10-01
employment_end_confirmed: true
clock_days: 90
basis: 'Alex, 2026-09-25: the current job ends 2026-10-01'
new_employment_start: null
visa_allowance_days: null
visa_allowance_source: null
phases:
  - {weeks: [1, 13], name: search}
"""


def w(path: Path, text: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return path


def clean(root: Path) -> dict:
    pdf = w(root / "resumes" / "bases" / "general" / "AlexExample_Resume.pdf", b"%PDF base 69")
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    w(root / "profile" / "PROFILE.md", "---\nstatus: approved\nupdated: 2026-09-27\n---\n# Profile\n")
    w(root / "profile" / "facts.yaml", """
        conflicts:
          - {id: C1, ruled: {by: owner, "on": 2026-09-26, basis: chat}}
        """)
    w(root / "clock.yaml", CLOCK)
    w(root / "state" / "linkedin" / "live.md", "Founding Engineer\nAcme Corp AI\nFeb 2026 - Present\n")
    data = {"schema_version": 1, "base_resume": {"iteration": 69, "pdf_sha256": sha}, "surfaces": [
        {"id": "linkedin", "kind": "profile-network", "account": {"state": "exists"},
         "profile": {"state": "current", "checked_on": "2026-09-27"}, "resume": {"uploaded": "unknown"},
         "next_refresh": "2026-10-02",
         "inbox": {"email_senders": [{"sender": "messages-noreply@linkedin.com", "count": 13}],
                   "in_app": "linkedin.com/messaging", "last_swept": "2026-09-27"}},
        {"id": "wellfound", "kind": "job-board", "account": {"state": "exists-inferred"},
         "profile": {"state": "current", "checked_on": "2026-09-27"},
         "resume": {"uploaded": {"iteration": 69, "sha256": sha}, "uploaded_on": "2026-09-27"},
         "next_refresh": "after the content pack",
         "inbox": {"email_senders": [{"sender": "team@mail.wellfound.com", "count": 34}], "in_app": "wellfound.com/jobs/messages",
                   "last_swept": "2026-09-27"}},
        {"id": "research-presence", "kind": "research", "account": {"state": "exists"},
         "profile": {"state": "current", "checked_on": "2026-09-27"}, "resume": {"uploaded": "none"},
         "next_refresh": "after LinkedIn", "inbox": {"email_senders": [], "in_app": "n/a"}},
    ]}
    w(root / "state" / "presence" / "portals.yaml", yaml.safe_dump(data, sort_keys=False))
    return data


def save(root: Path, data: dict) -> None:
    (root / "state" / "presence" / "portals.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def run(root: Path, today: str = TODAY) -> int:
    return pc.main(["--root", str(root), "--today", today])


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "js"
    r.mkdir()
    return r


def test_the_clean_tracker_passes(root, capsys):
    clean(root)
    assert run(root) == 0
    out = capsys.readouterr().out
    assert "presence_check: 0 flags" in out
    assert "note: wellfound: next_refresh is a condition, not a date" in out     # missing is not empty


@pytest.mark.parametrize("plant,expect", [
    ("a", "linkedin: refresh due (next_refresh 2026-09-20)"),
    ("b", "wellfound: the resume there is not the current base"),
    ("c", "linkedin: profile checked 2026-09-25, before the last change to PROFILE.md or a facts ruling (2026-09-27)"),
    ("e", "wellfound: unwatched: an account and an inbox, and no sender the classifier routes"),
    ("f", "linkedin: inbox last swept 2026-09-19, more than 8 days ago"),
])
def test_each_planted_drift_is_one_flag(root, capsys, plant, expect):
    data = clean(root)
    ln, wf = data["surfaces"][0], data["surfaces"][1]
    if plant == "a":
        ln["next_refresh"] = "2026-09-20"
    elif plant == "b":
        wf["resume"]["uploaded"]["sha256"] = "0" * 64
    elif plant == "c":
        ln["profile"]["checked_on"] = "2026-09-25"
    elif plant == "e":
        wf["inbox"]["email_senders"] = [{"sender": "hello@marketing.example.test", "count": 1}]
    elif plant == "f":
        ln["inbox"]["last_swept"] = "2026-09-19"
    save(root, data)
    assert run(root) == 1
    out = capsys.readouterr().out
    assert f"presence_check: {expect}" in out and "presence_check: 1 flag" in out


def test_present_on_the_employer_after_the_last_day_is_a_note_not_a_flag(root, capsys):
    # Present on the old employer after the last day is a reminder, never a flag (the owner may keep it on purpose).
    clean(root)
    assert run(root, "2026-10-01") == 0
    capsys.readouterr()
    assert run(root, "2026-10-02") == 0
    out = capsys.readouterr().out
    assert "still shows Acme Corp as current" in out and "end it there unless that is deliberate" in out


def test_a_stale_base_hash_in_the_tracker_itself_is_a_flag(root, capsys):
    data = clean(root)
    data["base_resume"]["pdf_sha256"] = "f" * 64
    save(root, data)
    assert run(root) == 1
    assert "the tracker's base_resume.pdf_sha256 is not the current base PDF" in capsys.readouterr().out


def test_a_missing_tracker_is_not_a_pass(root, capsys):
    assert run(root) == 2
    assert "portals.yaml" in capsys.readouterr().out


def test_summary_counts_the_states_for_the_dashboard(root):
    data = clean(root)
    data["surfaces"][1]["profile"]["state"] = "stale"
    data["surfaces"][1]["inbox"]["email_senders"] = []
    save(root, data)
    s = pc.summary(root, pc.cli_date(TODAY))
    assert (s["surfaces"], s["current"], s["stale"], s["contradicts"], s["unwatched"]) == (3, 2, 1, 0, 1)
    assert s["flags"] == 1


def test_a_facts_ruling_after_the_check_makes_the_profile_stale(root, capsys):
    clean(root)
    w(root / "profile" / "facts.yaml", """
        conflicts:
          - {id: C9, ruled: {by: owner, "on": 2026-09-28, basis: chat}}
        """)
    assert run(root) == 1
    assert "linkedin: profile checked 2026-09-27, before the last change to PROFILE.md or a facts ruling (2026-09-28)" \
        in capsys.readouterr().out
