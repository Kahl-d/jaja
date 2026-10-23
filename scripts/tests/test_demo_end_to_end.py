"""The demo data end to end through the real CLIs, on a temporary copy. Nothing is ever sent."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

CODE = Path(__file__).resolve().parents[2]
DEMO = CODE / "examples" / "demo-data"
TODAY = "2026-10-20"


@pytest.fixture
def env(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(DEMO, data)
    home = tmp_path / "home"
    home.mkdir()
    e = dict(os.environ, JOB_AGENCY_DEMO="1", JOB_AGENCY_DATA=str(data), JOB_AGENCY_RUNTIME=str(data / "runtime"),
             HOME=str(home), PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(CODE / "mcp"))
    e.pop("JOB_AGENCY_CONFIG", None)
    e["_data"] = str(data)
    return e


def run(env, script, *args, ok=True):
    e = {k: v for k, v in env.items() if k != "_data"}
    r = subprocess.run([sys.executable, str(CODE / "scripts" / script), *map(str, args)], capture_output=True, text=True,
                       env=e, cwd=str(CODE), timeout=300)
    if ok:
        assert r.returncode == 0, f"{script} {args}\n{r.stdout}\n{r.stderr}"
    return r


def fm(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text.split("---\n", 2)[1])


def test_fit_gates_sort_the_open_jobs_three_ways(env):
    data = Path(env["_data"])
    r = run(env, "fit_gates.py", "--root", data)
    assert "evaluated 4, pass 1, review 1, fail 2" in r.stdout
    assert fm(data / "jobs" / "globex-203.md")["gate"] == "pass"
    assert fm(data / "jobs" / "initech-302.md")["gate"] == "review"
    staff = fm(data / "jobs" / "globex-200.md")
    assert staff["gate"] == "fail" and staff["status"] == "skipped"
    assert any("seniority" in x for x in staff["gate_reasons"])
    citizen = fm(data / "jobs" / "globex-201.md")
    assert citizen["gate"] == "fail" and any("sponsorship" in x for x in citizen["gate_reasons"])
    # the three jobs that already carry an application are not re-gated
    assert fm(data / "jobs" / "acme-corp-100.md")["status"] == "applied"


def test_tracker_new_and_events_move_the_stage(env):
    data = Path(env["_data"])
    run(env, "fit_gates.py", "--root", data)
    run(env, "tracker.py", "--today", TODAY, "new", "--job", "globex-203")
    slug = "globex-machine-learning-engineer-202610"
    app = data / "applications" / slug / "application.md"
    assert fm(app)["stage"] == "discovered"
    run(env, "tracker.py", "--today", TODAY, "event", slug, "applied", "--source", "manual", "--via", "ats",
        "--resume", "none", "--reason", "demo: no PDF", "--evidence", "demo: applied by hand")
    run(env, "tracker.py", "--today", TODAY, "event", slug, "screen", "--source", "email", "--evidence", "demo: screen booked")
    meta = fm(app)
    assert meta["stage"] == "screen" and meta["applied_via"] == "ats"
    body = app.read_text(encoding="utf-8")
    assert body.count(" · applied · ") == 1 and " · screen · " in body
    r = run(env, "tracker.py", "--today", TODAY, "metrics")
    assert "screen" in r.stdout.lower()


def test_outbox_draft_and_approval_send_nothing(env):
    data = Path(env["_data"])
    body = data.parent / "reply.txt"
    body.write_text("Hi Omar,\n\nI read the dispatcher console notes and I built a similar retrieval service at Brightline "
                    "Systems. Could we find twenty minutes next week?\n\nSam\n", encoding="utf-8")
    r = run(env, "outbox.py", "draft", "--today", TODAY, "--prospect", "dana-whitfield", "--channel", "email",
            "--kind", "reply", "--subject", "Follow up", "--body-file", body)
    draft = json.loads(r.stdout)
    assert draft["id"] == "O-2026-0002", draft
    r = run(env, "outbox.py", "batch", "--today", TODAY, "--ids", draft["id"], "--why", "Demo reply for the owner to read")
    made = json.loads(r.stdout)
    approval = Path(made[0]["path"])
    assert approval.parent == data / "runtime" / "work" / "approvals"
    meta = fm(approval)
    assert meta["status"] == "pending" and meta["action"] == "send-email" and meta["decided_at"] is None
    item = fm(data / "outbox" / f"{draft['id']}.md")
    assert item["status"] == "pending" and not item.get("sent_at") and not item.get("gmail_draft_id")
    # the gate refuses: the owner has not said yes
    assert run(env, "outbox.py", "can-send", "--id", draft["id"], ok=False).returncode == 1
    assert run(env, "kernel/approvals.py", "verify", "--today", TODAY, approval, ok=False).returncode == 1
    # nothing went anywhere: no touch was logged for the draft (the demo's own four touches are all there is)
    rows = [ln for f in (data / "touches").glob("*.jsonl") for ln in f.read_text(encoding="utf-8").splitlines() if ln]
    assert len(rows) == 4 and all("O-2026-0002" not in ln for ln in rows)


def test_the_demo_approval_waits_for_the_owner(env):
    data = Path(env["_data"])
    meta = fm(data / "runtime" / "work" / "approvals" / "A-2026-0001.md")
    assert meta["status"] == "pending" and meta["decided_at"] is None
    assert run(env, "outbox.py", "can-send", "--id", "O-2026-0001", ok=False).returncode == 1


def test_dashboard_builds_from_the_demo(env):
    data = Path(env["_data"])
    run(env, "dashboard.py", "--root", data, "--today", TODAY)
    md = (data / "DASHBOARD.md").read_text(encoding="utf-8")
    assert "Needs Sam" in md and "A-2026-0001" in md
    assert "interviewing 1" in md
    state = json.loads((data / "state" / "dashboard.json").read_text(encoding="utf-8"))
    assert isinstance(state, dict) and state


def test_mcp_store_reads_and_drafts_without_sending(env):
    code = r'''
import json
from job_search_mcp.store import Store
s = Store.from_env()
apps = s.applications_list()
assert apps["total"] == 3, apps
assert {a["stage"] for a in apps["items"]} == {"confirmation", "screen", "interviewing"}
assert s.application_get("initech-llm-evaluation-engineer-202610")["meta"]["stage"] == "interviewing"
assert s.prospects_list()["total"] == 5
assert s.prospect_get("priya-nair")["meta"]["id"] == "priya-nair"
assert s.profile_facts(path="identity.full_name")["value"] == "Sam Rivera"
assert s.evidence_lookup(ids=["ev-sam-005"])
d = s.outbox_create("omar-haddad", "email", "Hi Omar,\n\nI would like to hear how the dispatcher console is tested.\n\nSam",
                    subject="A question about evaluation", kind="intro")
assert d["status"] == "drafted", d
a = s.approval_create("messenger", "Demo: first note to the hiring manager", outbox_ids=[d["id"]])
assert a["status"] == "pending", a
print(json.dumps({"draft": d["id"], "approval": a["approvals"][0]["approval"]}))
'''
    e = {k: v for k, v in env.items() if k != "_data"}
    e["JOB_SEARCH_TODAY"] = TODAY
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=e, cwd=str(CODE), timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    data = Path(env["_data"])
    assert fm(data / "runtime" / "work" / "approvals" / f"{out['approval']}.md")["status"] == "pending"
    assert fm(data / "outbox" / f"{out['draft']}.md").get("sent_at") in (None, "")


def test_the_repo_demo_folder_is_untouched_by_all_of_this():
    """The tests above write to copies only: the committed demo has no dashboard, no extra drafts, no DASHBOARD.md."""
    assert not (DEMO / "DASHBOARD.md").exists()
    assert sorted(p.name for p in (DEMO / "outbox").glob("O-*.md")) == ["O-2026-0001.md"]
    assert sorted(p.name for p in (DEMO / "runtime" / "work" / "approvals").glob("A-*.md")) == ["A-2026-0001.md"]
