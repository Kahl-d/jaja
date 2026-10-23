"""js_common.os_root_for: with --root pointing at a marked eval sandbox and no --os-root, every script
writes approvals inside the sandbox, never three folders above it (an eval defect)."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import comms_helpers as ch  # noqa: F401  (puts scripts/ on sys.path)
from comms_helpers import offline  # noqa: F401  (autouse: no network)
import calendar_hold as chd
import gmail_pull as gp
import js_common as jc
import js_comms_util as cu
import outbox as ob
import prospects as pr

TODAY = date(2026, 9, 24)


def _sandbox(tmp_path: Path) -> Path:
    box = tmp_path / "a" / "b" / "c" / "sandbox"
    box.mkdir(parents=True)
    (box / ".eval-sandbox").write_text(str(__import__("agency_paths").KERNEL_DIR), encoding="utf-8")
    return box


def test_os_root_for_a_marked_sandbox_is_the_sandbox(tmp_path):
    box = _sandbox(tmp_path)
    assert jc.os_root_for(box) == box
    dept = tmp_path / "os" / "a" / "b" / "c" / "data"
    dept.mkdir(parents=True)
    assert jc.os_root_for(dept) == tmp_path / "os"
    assert jc.os_root_for(jc.JS_ROOT) == jc.OS_ROOT


def test_outbox_create_in_a_marked_sandbox_files_the_approval_inside_it(tmp_path, capsys):
    box = _sandbox(tmp_path)
    pr.ingest_events(cu.Roots(js=box, os=box), [
        {"key": "gm-sam", "at": "2026-08-28T17:04:11Z", "direction": "in", "channel": "email", "source": "gmail-backlog",
         "label": "recruiter_outreach", "confidence": 0.9, "create": True,
         "person": {"name": "Sam Rivera", "emails": ["sam.rivera@acme.example.test"], "linkedin_url": "",
                    "company": "Acme Corp", "title": "Technical Recruiter"},
         "subject": "Senior ML Engineer at Acme Corp", "summary": "Hi Alex", "text": "Hi Alex",
         "thread_id": "t-sam", "message_id": "<m1@acme.example.test>", "references": "", "reply_to": "", "tags": []}], today=TODAY)
    staged = box / "state" / "drafts" / "B-2026-09-24-1"
    staged.mkdir(parents=True)
    jc.write_record(staged / "sam-rivera.md", {"batch": "B-2026-09-24-1", "prospect": "sam-rivera", "channel": "email",
                                              "kind": "reply"},
                    "Hi Sam,\n\nThanks for the note about the ML role. Thursday afternoon works for me.\n\nAlex")
    assert ob.main(["create", "--batch", "B-2026-09-24-1", "--root", str(box), "--today", "2026-09-24"]) == 0
    assert json.loads(capsys.readouterr().out)["approvals"]
    assert list((box / "work" / "approvals").glob("A-*.md"))
    assert not (tmp_path / "work").exists()


def test_calendar_hold_request_in_a_marked_sandbox_stays_inside_it(tmp_path, capsys):
    box = _sandbox(tmp_path)
    assert chd.main(["build", "--root", str(box), "--today", "2026-09-24", "--title", "Interview: Acme Corp",
                     "--start", "2026-09-29T17:00:00Z", "--request", "--manual", "sandbox check, no source email"]) == 0
    assert list((box / "work" / "approvals").glob("A-*.md")) and not (tmp_path / "work").exists()


def test_gmail_pull_roots_for_a_marked_sandbox(tmp_path):
    box = _sandbox(tmp_path)
    roots = gp._roots(argparse.Namespace(root=str(box), os_root=None))
    assert roots.os == box and roots.approvals == box / "work" / "approvals"


# --------------------------------------------------------------------------- run_stamp
from comms_helpers import clock_2030  # noqa: E402,F401


def test_run_stamp_uses_the_run_day_when_it_is_not_today(clock_2030):
    assert jc.run_stamp(date(2026, 9, 28)) == "2026-09-28T12:00:00+00:00"
    assert jc.run_stamp(date(2026, 9, 28), z=True) == "2026-09-28T12:00:00Z"
    assert jc.run_stamp(None) == "2030-01-01T09:00:00+00:00" == jc.run_stamp(date(2030, 1, 1))
    assert jc.run_stamp(None, z=True) == "2030-01-01T09:00:00Z"


def test_dashboard_and_pipeline_default_os_root_follows_root(tmp_path):
    import subprocess, sys
    from pathlib import Path
    scripts = Path(__file__).resolve().parents[1]
    sb = tmp_path / "sb"
    (sb / "work" / "approvals").mkdir(parents=True)
    (sb / ".eval-sandbox").write_text(str(scripts.parents[4] / "system" / "scripts"), encoding="utf-8")
    for name in ("dashboard.py", "pipeline.py"):
        src = (scripts / name).read_text(encoding="utf-8")
        assert "os_root_for(Path(args.root))" in src and 'default=None' in src, name
    out = subprocess.run([sys.executable, "-B", str(scripts / "dashboard.py"), "--root", str(sb), "--dry-run"],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert not (scripts.parents[4] / "sb").exists()
