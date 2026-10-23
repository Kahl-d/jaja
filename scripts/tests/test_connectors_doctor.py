"""connectors_doctor.py: every connector's state as a record, red/amber/green, never a secret.

No network and no real tokens: everything lives in tmp dirs; the MCP probe is injected.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from comms_helpers import offline  # noqa: F401  (autouse: no network)
import connectors_doctor as cd
import gmail_auth as ga

NOW = datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc)
SECRETS = ("ya29.FAKE-ACCESS-TOKEN", "1//FAKE-REFRESH-TOKEN", "GOCSPX-FAKE-CLIENT-SECRET", "FAKE-ADZUNA-KEY-123",
           "FAKE-ADZUNA-ID-456", "fc-FAKE-FIRECRAWL-KEY-789")
READ_SCOPES = [ga.SCOPE_READONLY, ga.SCOPE_LABELS]


@pytest.fixture(autouse=True)
def _no_keychain(monkeypatch):
    """Never ask the real macOS Keychain from a test."""
    monkeypatch.setattr(cd, "firecrawl_keychain_has", lambda: False)


def ok_probe():
    return {"ok": True, "tools": ["jobs_search", "prospects_list"], "missing": []}


@pytest.fixture
def env(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir(mode=0o700)
    os.chmod(cfg, 0o700)
    js = tmp_path / "os" / "js"
    os_root = tmp_path / "os"
    (js / "state" / "harvest").mkdir(parents=True)
    (os_root / "work" / "approvals").mkdir(parents=True)
    (os_root / ".mcp.json").write_text(json.dumps({"mcpServers": {"job-search": {"command": "uv"}}}))
    (os_root / "company" / "policies").mkdir(parents=True)
    (os_root / "company" / "policies" / "budgets.yaml").write_text(
        "connectors:\n  firecrawl: {credits_per_month: 900, credits_per_day: 100}\n")
    return {"cfg": cfg, "js": js, "os": os_root, "env": {}}


def run(env, probe=ok_probe, **kw):
    return cd.check_all(js_root=env["js"], os_root=env["os"], config_dir=env["cfg"], env=env["env"], now=NOW,
                        mcp_probe=probe, **kw)


def by_name(report):
    return {c["name"]: c for c in report["connectors"]}


def write_token(env, kind, scopes, authorized=NOW - timedelta(days=1), refresh=True):
    data = {"token": SECRETS[0], "client_id": "x.apps.googleusercontent.com", "client_secret": SECRETS[2],
            "token_uri": "https://oauth2.googleapis.com/token", "scopes": scopes}
    if refresh:
        data["refresh_token"] = SECRETS[1]
    path = env["cfg"] / ga.TOKEN_KINDS[kind]["file"]
    ga.write_token(path, json.dumps(data), authorized_at=authorized)
    return path


def write_secret(env, kind="installed"):
    p = env["cfg"] / "gmail_client_secret.json"
    p.write_text(json.dumps({kind: {"client_id": "x", "client_secret": SECRETS[2]}}))
    os.chmod(p, 0o600)
    return p


def healthy(env):
    write_secret(env)
    write_token(env, "read", READ_SCOPES)
    write_token(env, "calendar", [ga.SCOPE_CALENDAR])
    write_token(env, "send", [ga.SCOPE_SEND])
    (env["cfg"] / "x_client.json").write_text('{"client_id": "x"}')
    os.chmod(env["cfg"] / "x_client.json", 0o600)
    for kind, scopes in (("read", ["offline.access", "tweet.read", "users.read"]),
                         ("write", ["dm.read", "dm.write", "follows.write", "offline.access", "tweet.read", "tweet.write",
                                    "users.read"])):
        xp = env["cfg"] / f"x_token_{kind}.json"
        xp.write_text(json.dumps({"access_token": SECRETS[0], "refresh_token": SECRETS[1], "scopes": scopes,
                                  "username": "examplehandle"}))
        os.chmod(xp, 0o600)
    hunt = env["js"] / "state" / "hunt"
    hunt.mkdir(parents=True, exist_ok=True)
    (hunt / "review-latest.json").write_text(json.dumps({
        "now": "2026-09-25T14:30:00Z", "coverage": {"open": 10, "scored": 10, "share": 1.0},
        "freshness": {"measured": 5, "median_hours": 2.0, "within_target": 4, "target_hours": 3}, "counts": {"listed": 3}}))
    (env["js"] / "state" / "harvest" / "gmail.state.json").write_text(json.dumps(
        {"last_internal_date": "2026-09-25T08:00:00+00:00", "runs": [{"at": "2026-09-25T08:05:00+00:00", "new": 3}]}))
    li = env["js"] / "profile" / "_inbox" / "linkedin-export"
    li.mkdir(parents=True)
    (li / "Basic_LinkedIn_Data_2026-09-20.zip").write_bytes(b"PK")
    (env["js"] / "state" / "harvest" / "linkedin-2026-09-21.json").write_text("{}")
    env["env"].update({"ADZUNA_APP_ID": SECRETS[4], "ADZUNA_APP_KEY": SECRETS[3], "FIRECRAWL_API_KEY": SECRETS[5]})
    for n, source in enumerate(("indeed-connector", "ziprecruiter-connector", "dice-connector")):
        _job(env, f"acme-conn-{n}", source, "2026-09-24T18:00:00Z")


def approval(env, status="approved", receipts="", channel="email", expires="2026-09-30"):
    path = env["os"] / "work" / "approvals" / "A-2026-0007.md"
    path.write_text(
        "---\n"
        f"id: A-2026-0007\naction: send-email\nrequested_by: messenger\nstatus: {status}\n"
        f"created: 2026-09-24\nexpires: {expires}\ndecided_at: 2026-09-25\nproject: job-search\n"
        f"items:\n  - {{id: O-2026-0001, channel: {channel}, target: a@example.test}}\n"
        "---\n# x\n\n## Exact content\n\nhi\n\n## Receipts\n" + receipts)
    return path


# ----------------------------------------------------------------------------- whole report
def test_fresh_machine_is_red_with_actions(env):
    rep = run(env)
    c = by_name(rep)
    assert rep["overall"] == "red"
    assert c["gmail-client-secret"]["state"] == "red"
    assert c["gmail-read-token"]["state"] == "red"
    assert "gmail_auth.py login" in c["gmail-read-token"]["action"]
    assert c["gmail-compose-token"]["state"] == "green"          # absent is the safe default
    assert c["calendar-token"]["state"] == "amber"
    assert c["linkedin-export"]["state"] == "amber"
    assert c["adzuna"]["state"] == "amber"
    assert c["firecrawl"]["state"] == "amber" and "section 10" in c["firecrawl"]["action"]
    assert c["mcp-job-search"]["state"] == "green"
    for name in ("claude-ai-gmail", "claude-ai-calendar"):
        assert c[name]["state"] == "verify-in-session"
        assert c[name]["summary"] == "configured in claude.ai, verify in session"


def test_healthy_setup_is_green(env):
    healthy(env)
    rep = run(env)
    states = {n: c["state"] for n, c in by_name(rep).items()}
    assert rep["overall"] == "green", states
    c = by_name(rep)
    assert c["gmail-read-token"]["checks"]["last_harvest"] == "2026-09-25"
    assert c["linkedin-export"]["checks"]["latest_export_date"] == "2026-09-20"


# ----------------------------------------------------------------------------- gmail tokens
def test_token_that_is_not_chmod_600_is_red(env):
    healthy(env)
    os.chmod(env["cfg"] / "gmail_token_read.json", 0o644)
    c = by_name(run(env))["gmail-read-token"]
    assert c["state"] == "red" and "600" in c["summary"]


def test_client_secret_not_600_is_red(env):
    healthy(env)
    os.chmod(env["cfg"] / "gmail_client_secret.json", 0o644)
    assert by_name(run(env))["gmail-client-secret"]["state"] == "red"


def test_read_token_carrying_compose_scope_is_red(env):
    healthy(env)
    write_token(env, "read", READ_SCOPES + [ga.SCOPE_COMPOSE])
    c = by_name(run(env))["gmail-read-token"]
    assert c["state"] == "red" and "compose" in c["summary"]


def test_expired_read_token_is_red_and_expiring_is_amber(env):
    healthy(env)
    write_token(env, "read", READ_SCOPES, authorized=NOW - timedelta(days=8))
    assert by_name(run(env))["gmail-read-token"]["state"] == "red"
    write_token(env, "read", READ_SCOPES, authorized=NOW - timedelta(days=6, hours=12))
    assert by_name(run(env))["gmail-read-token"]["state"] == "amber"


def test_stale_harvest_is_amber(env):
    healthy(env)
    (env["js"] / "state" / "harvest" / "gmail.state.json").write_text(json.dumps(
        {"runs": [{"at": "2026-09-01T08:05:00+00:00"}]}))
    c = by_name(run(env))["gmail-read-token"]
    assert c["state"] == "amber" and c["checks"]["last_harvest"] == "2026-09-01"


def test_compose_token_without_an_approved_draft_is_amber(env):
    healthy(env)
    write_token(env, "compose", [ga.SCOPE_COMPOSE])
    c = by_name(run(env))["gmail-compose-token"]
    assert c["state"] == "amber" and "no approved draft approval" in c["summary"]
    approval(env)
    c = by_name(run(env))["gmail-compose-token"]
    assert c["state"] == "green" and c["checks"]["approved_draft_approvals"] == ["A-2026-0007"]


def test_compose_token_after_drafts_were_created_is_amber_again(env):
    healthy(env)
    write_token(env, "compose", [ga.SCOPE_COMPOSE])
    approval(env, receipts="- 2026-09-25 gmail drafts created: r-123\n")
    assert by_name(run(env))["gmail-compose-token"]["state"] == "amber"
    approval(env, expires="2026-09-20")
    assert by_name(run(env))["gmail-compose-token"]["state"] == "amber"
    approval(env, status="pending")
    assert by_name(run(env))["gmail-compose-token"]["state"] == "amber"


def test_compose_token_not_600_is_red(env):
    healthy(env)
    p = write_token(env, "compose", [ga.SCOPE_COMPOSE])
    approval(env)
    os.chmod(p, 0o640)
    assert by_name(run(env))["gmail-compose-token"]["state"] == "red"


# ----------------------------------------------------------------------------- other connectors
def test_linkedin_export_not_yet_parsed_is_amber(env):
    healthy(env)
    (env["js"] / "state" / "harvest" / "linkedin-2026-09-21.json").unlink()
    c = by_name(run(env))["linkedin-export"]
    assert c["state"] == "amber" and "linkedin_export.py parse" in c["action"]


def test_old_linkedin_export_is_amber(env):
    healthy(env)
    li = env["js"] / "profile" / "_inbox" / "linkedin-export"
    (li / "Basic_LinkedIn_Data_2026-09-20.zip").rename(li / "Basic_LinkedIn_Data_2026-07-01.zip")
    assert by_name(run(env))["linkedin-export"]["state"] == "amber"


def test_mcp_probe_failure_or_missing_registration_is_red(env):
    healthy(env)
    bad = run(env, probe=lambda: {"ok": False, "error": "ImportError: no module named mcp"})
    assert by_name(bad)["mcp-job-search"]["state"] == "red"
    (env["os"] / ".mcp.json").write_text(json.dumps({"mcpServers": {}}))
    c = by_name(run(env))["mcp-job-search"]
    assert c["state"] == "red" and "sync.py" in c["action"]


def test_skip_mcp(env):
    healthy(env)
    rep = run(env, probe=None)
    assert by_name(rep)["mcp-job-search"]["state"] == "skipped"
    assert rep["overall"] == "green"


# ----------------------------------------------------------------------------- outputs
def test_main_writes_json_and_markdown_and_exits_by_state(env, capsys, monkeypatch):
    monkeypatch.setattr(cd, "default_probe", ok_probe)
    args = ["--root", str(env["js"]), "--os-root", str(env["os"]), "--config-dir", str(env["cfg"])]
    assert cd.main(args, now=NOW, env=env["env"]) == 1                     # fresh machine: red
    healthy(env)
    assert cd.main(args, now=NOW, env=env["env"]) == 0
    data = json.loads((env["js"] / "state" / "connectors.json").read_text())
    assert data["overall"] == "green" and data["generated_at"].startswith("2026-09-25")
    md = (env["js"] / "state" / "connectors.md").read_text()
    assert md.startswith(cd.MD_START) and md.rstrip().endswith(cd.MD_END)
    assert "| gmail-read-token | green |" in md
    assert "verify-in-session" in md


def test_never_prints_or_writes_secrets(env, capsys, monkeypatch):
    healthy(env)
    write_token(env, "compose", [ga.SCOPE_COMPOSE])
    monkeypatch.setattr(cd, "default_probe", ok_probe)
    args = ["--root", str(env["js"]), "--os-root", str(env["os"]), "--config-dir", str(env["cfg"])]
    cd.main(args, now=NOW, env=env["env"])
    cd.main(args + ["--json"], now=NOW, env=env["env"])
    out = capsys.readouterr()
    written = (env["js"] / "state" / "connectors.json").read_text() + (env["js"] / "state" / "connectors.md").read_text()
    for secret in SECRETS:
        assert secret not in out.out and secret not in out.err and secret not in written


def test_legacy_single_token_is_flagged(env):
    healthy(env)
    assert "gmail-legacy-token" not in by_name(run(env))
    legacy = env["cfg"] / "gmail_token.json"
    legacy.write_text(json.dumps({"refresh_token": SECRETS[1], "scopes": "compose"}))
    os.chmod(legacy, 0o600)
    c = by_name(run(env))["gmail-legacy-token"]
    assert c["state"] == "amber" and "rm " in c["action"] and c["checks"]["scopes"] == ["compose"]
    os.chmod(legacy, 0o644)
    assert by_name(run(env))["gmail-legacy-token"]["state"] == "red"
    rep = json.dumps(run(env))
    assert SECRETS[1] not in rep


# ----------------------------------------------------------------------------- firecrawl 
def _usage(env, credits, day="2026-09-25"):
    p = env["js"] / "state" / "firecrawl" / "usage.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"at": f"{day}T10:00:00Z", "day": day, "endpoint": "scrape", "host": "x.com",
                             "credits": credits, "status": "ok"}) + "\n")


def test_firecrawl_key_in_the_keychain_is_green_and_its_credits_are_counted(env, monkeypatch):
    monkeypatch.setattr(cd, "firecrawl_keychain_has", lambda: True)
    _usage(env, 12)
    c = by_name(run(env))["firecrawl"]
    assert c["state"] == "green" and c["checks"]["key"] == "keychain"
    assert c["checks"]["credits_month"] == "12 of 900" and c["checks"]["credits_today"] == "12 of 100"


def test_firecrawl_near_or_over_its_monthly_budget_is_amber(env):
    env["env"]["FIRECRAWL_API_KEY"] = SECRETS[5]
    _usage(env, 850)
    c = by_name(run(env))["firecrawl"]
    assert c["state"] == "amber" and "850 of 900" in c["summary"]


def test_firecrawl_without_a_budget_line_is_amber_and_says_so(env):
    env["env"]["FIRECRAWL_API_KEY"] = SECRETS[5]
    (env["os"] / "company" / "policies" / "budgets.yaml").write_text("updated: 2026-09-28\n")
    c = by_name(run(env))["firecrawl"]
    assert c["state"] == "amber" and "budget" in c["summary"]


def test_calendar_token_green_names_what_it_serves_and_a_broader_scope_is_flagged(env):
    healthy(env)
    cal = by_name(run(env))["calendar-token"]
    assert cal["state"] == "green" and "calendar_events.py" in cal["summary"]
    write_token(env, "calendar", [ga.SCOPE_CALENDAR, "https://www.googleapis.com/auth/calendar"])
    cal = by_name(run(env))["calendar-token"]
    assert cal["state"] == "amber" and "broader than calendar.events" in cal["summary"]
    assert "login --calendar" in cal["action"]


def test_send_token_amber_when_absent_green_with_gmail_send_only_red_when_broader(env):
    healthy(env)
    (env["cfg"] / ga.TOKEN_KINDS["send"]["file"]).unlink()
    c = by_name(run(env))["gmail-send-token"]
    assert c["state"] == "amber" and "login --send" in c["action"]
    write_token(env, "send", [ga.SCOPE_SEND])
    c = by_name(run(env))["gmail-send-token"]
    assert c["state"] == "green" and "gmail_send.py" in c["summary"]
    write_token(env, "send", [ga.SCOPE_SEND, READ_SCOPES[0]])
    c = by_name(run(env))["gmail-send-token"]
    assert c["state"] == "red" and "login --send" in c["action"]


def _x_token(env, kind, scopes):
    env["cfg"].mkdir(parents=True, exist_ok=True)
    path = env["cfg"] / f"x_token_{kind}.json"
    path.write_text(json.dumps({"access_token": SECRETS[0], "refresh_token": SECRETS[1], "scopes": scopes,
                                "expires_at": "2026-09-25T10:00:00Z", "username": "examplehandle", "user_id": "9"}))
    os.chmod(path, 0o600)
    return path


def test_x_rows_amber_when_absent_green_when_logged_in_red_when_the_read_token_can_write(env):
    c = by_name(run(env))
    assert c["x-client"]["state"] == "amber" and "section 14" in c["x-client"]["action"]
    assert c["x-read-token"]["state"] == "amber" and "x_api.py login" in c["x-read-token"]["action"]
    assert c["x-write-token"]["state"] == "amber" and "login --write" in c["x-write-token"]["action"]
    (env["cfg"] / "x_client.json").write_text('{"client_id": "x"}')
    os.chmod(env["cfg"] / "x_client.json", 0o600)
    _x_token(env, "read", ["offline.access", "tweet.read", "users.read"])
    _x_token(env, "write", ["dm.read", "dm.write", "follows.write", "offline.access", "tweet.read", "tweet.write",
                            "users.read"])
    c = by_name(run(env))
    assert (c["x-client"]["state"], c["x-read-token"]["state"], c["x-write-token"]["state"]) == ("green",) * 3
    assert "@examplehandle" in c["x-read-token"]["summary"]
    _x_token(env, "read", ["offline.access", "tweet.read", "users.read", "tweet.write"])
    assert by_name(run(env))["x-read-token"]["state"] == "red"
    report = json.dumps(run(env))
    assert SECRETS[0] not in report and SECRETS[1] not in report


def _review(env, **over):
    data = {"now": "2026-09-25T14:30:00Z", "coverage": {"open": 100, "scored": 100, "share": 1.0},
            "freshness": {"measured": 20, "median_hours": 2.0, "within_target": 15, "target_hours": 3},
            "counts": {"new": 20, "listed": 8}}
    data.update(over)
    path = env["js"] / "state" / "hunt" / "review-latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_hunt_rows_green_when_fresh_and_covered_amber_otherwise(env):
    healthy(env)
    (env["js"] / "state" / "hunt" / "review-latest.json").unlink()
    c = by_name(run(env))
    assert c["hunt-review"]["state"] == "amber" and "hunt_review.py" in c["hunt-review"]["action"]
    _review(env)
    c = by_name(run(env))
    assert c["hunt-review"]["state"] == "green" and c["hunt-freshness"]["state"] == "green"
    _review(env, freshness={"measured": 18, "median_hours": 36.9, "within_target": 0, "target_hours": 3})
    c = by_name(run(env))
    assert c["hunt-freshness"]["state"] == "amber" and "36.9" in c["hunt-freshness"]["summary"]
    _review(env, coverage={"open": 100, "scored": 60, "share": 0.6})
    assert by_name(run(env))["hunt-review"]["state"] == "amber"
    _review(env, now="2026-09-20T14:30:00Z")
    assert "old" in by_name(run(env))["hunt-review"]["summary"]


def test_l_6800106c_x_credits_red_after_a_402_until_a_successful_call(env):
    assert "x-credits" not in by_name(run(env))                      # no X call yet: nothing to say
    led = env["js"] / "state" / "x" / "usage.jsonl"
    led.parent.mkdir(parents=True)
    led.write_text(json.dumps({"at": "2026-10-01T19:10:34Z", "day": "2026-10-01", "endpoint": "search",
                               "status": 402, "usd": 0.0}) + "\n")
    row = by_name(run(env))["x-credits"]
    assert row["state"] == "red" and "402" in row["summary"] and "2026-10-01" in row["summary"]
    assert "credits" in row["action"]
    with led.open("a") as fh:
        fh.write(json.dumps({"at": "2026-10-02T08:00:00Z", "day": "2026-10-02", "endpoint": "post", "status": 200,
                             "usd": 0.005}) + "\n")
    row = by_name(run(env))["x-credits"]
    assert row["state"] == "green" and "2026-10-01" in row["summary"]          # the last 402 stays on record


# connector side: one row per Claude board connector, from the newest job recorded through it.
def _job(env, name, source, first_seen):
    jobs = env["js"] / "jobs"
    jobs.mkdir(exist_ok=True)
    (jobs / f"{name}.md").write_text(f"---\nid: {name}\nsource: {source}\nplatform: {source}\n"
                                     f"first_seen: '{first_seen}'\n---\n\nbody source: dice-connector\n")


def test_k183_board_connector_rows_amber_never_amber_stale_green_recent(env):
    c = by_name(run(env))
    for name in ("indeed-connector", "ziprecruiter-connector", "dice-connector"):
        assert c[name]["state"] == "amber" and "Claude Settings, Connectors" in c[name]["action"]
    _job(env, "acme-in-1", "indeed-connector", (NOW - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    _job(env, "acme-zr-1", "ziprecruiter-connector", (NOW - timedelta(days=9)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    _job(env, "acme-zr-2", "ziprecruiter-connector", (NOW - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    _job(env, "acme-man-1", "manual", NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))   # a body mention never counts
    c = by_name(run(env))
    assert c["indeed-connector"]["state"] == "green"
    assert c["ziprecruiter-connector"]["state"] == "amber" and "9 days" in c["ziprecruiter-connector"]["summary"]
    assert c["dice-connector"]["state"] == "amber" and "no job ever" in c["dice-connector"]["summary"]
