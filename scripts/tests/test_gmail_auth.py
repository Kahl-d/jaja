"""gmail_auth.py: split tokens (read vs compose vs calendar), storage outside the repo, file
permissions, re-auth reminders. No real OAuth: the consent flow is injected, Google is never called."""
from __future__ import annotations

import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from comms_helpers import offline  # noqa: F401  (autouse: no network)
import gmail_auth as ga
import js_common as jc

SECRETS = ("ya29.FAKE-ACCESS", "1//FAKE-REFRESH", "GOCSPX-FAKE-SECRET")
READ = "https://www.googleapis.com/auth/gmail.readonly"
LABELS = "https://www.googleapis.com/auth/gmail.labels"
COMPOSE = "https://www.googleapis.com/auth/gmail.compose"


def _creds(scopes, expiry=None):
    from google.oauth2.credentials import Credentials
    return Credentials(token=SECRETS[0], refresh_token=SECRETS[1], token_uri="https://oauth2.googleapis.com/token",
                       client_id="123-fake.apps.googleusercontent.com", client_secret=SECRETS[2],
                       scopes=list(scopes), expiry=expiry)


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    folder = tmp_path / "home" / ".config" / "workspace"
    monkeypatch.setenv("JOB_AGENCY_CONFIG_DIR", str(folder))
    return folder


# --------------------------------------------------------------------------- scopes and paths
def test_read_and_compose_scopes_are_split_and_nothing_can_send():
    assert ga.SCOPES_READ == (READ, LABELS)
    assert ga.SCOPES_COMPOSE == (COMPOSE,)
    assert ga.SCOPE_CALENDAR == "https://www.googleapis.com/auth/calendar.events"
    every = ga.SCOPES_READ + ga.SCOPES_COMPOSE + (ga.SCOPE_CALENDAR,)
    assert not any(s.endswith(("gmail.send", "gmail.modify")) or s == "https://mail.google.com/" for s in every)


def test_default_token_paths_are_separate_files_under_config_outside_the_repo(monkeypatch):
    monkeypatch.delenv("JOB_AGENCY_CONFIG_DIR", raising=False)
    read, compose = ga.token_path("read"), ga.token_path("compose")
    assert (read.name, compose.name) == ("gmail_token_read.json", "gmail_token_compose.json")
    assert ga.token_path("calendar").name == "calendar_token.json"
    assert str(read.parent).endswith(os.path.join(".config", "job-agency"))
    for p in (read, compose, ga.client_secret_path({})):
        assert jc.OS_ROOT.resolve() not in p.resolve().parents


def test_client_secret_path_honours_the_env_variable(tmp_path):
    target = tmp_path / "cs.json"
    assert ga.client_secret_path({"GMAIL_CLIENT_SECRET": str(target)}) == target


# --------------------------------------------------------------------------- writing tokens
@pytest.mark.needs_google
def test_write_token_is_0600_in_a_0700_directory_and_records_the_auth_date(cfg):
    path = cfg / "gmail_token_read.json"
    ga.write_token(path, _creds(ga.SCOPES_READ).to_json(), authorized_at=datetime(2026, 9, 20, 17, 0, tzinfo=timezone.utc))
    assert _mode(path) == 0o600 and _mode(cfg) == 0o700
    data = json.loads(path.read_text())
    assert data["agency_authorized_at"] == "2026-09-20T17:00:00Z"
    assert data["agency_reauth_due"] == "2026-09-27T17:00:00Z"


@pytest.mark.needs_google
def test_write_token_refuses_a_path_inside_the_repo(tmp_path):
    repo = tmp_path / "repo"
    inside = repo / "domains" / "gmail_token_read.json"
    with pytest.raises(ga.AuthError, match="outside the repo"):
        ga.write_token(inside, _creds(ga.SCOPES_READ).to_json(), repo_root=repo)
    assert not inside.exists()


@pytest.mark.needs_google
def test_check_permissions_repairs_a_readable_token(cfg):
    path = cfg / "gmail_token_read.json"
    ga.write_token(path, _creds(ga.SCOPES_READ).to_json())
    os.chmod(path, 0o644)
    os.chmod(cfg, 0o755)
    warnings = ga.check_permissions(path, fix=True)
    assert warnings and any("0o644" in w for w in warnings)
    assert _mode(path) == 0o600 and _mode(cfg) == 0o700
    assert ga.check_permissions(path, fix=True) == []


# --------------------------------------------------------------------------- loading: fail closed
@pytest.mark.needs_google
def test_read_path_refuses_when_only_the_compose_token_exists(cfg):
    ga.write_token(ga.token_path("compose"), _creds(ga.SCOPES_COMPOSE).to_json())
    with pytest.raises(ga.AuthError, match="compose"):
        ga.load_credentials("read")


@pytest.mark.needs_google
def test_read_path_refuses_a_token_that_carries_a_write_scope(cfg):
    ga.write_token(ga.token_path("read"), _creds(ga.SCOPES_READ + ga.SCOPES_COMPOSE).to_json())
    with pytest.raises(ga.AuthError, match="gmail.compose"):
        ga.load_credentials("read")


@pytest.mark.needs_google
def test_compose_path_needs_its_own_token(cfg):
    ga.write_token(ga.token_path("read"), _creds(ga.SCOPES_READ).to_json())
    with pytest.raises(ga.AuthError, match="login --compose"):
        ga.load_credentials("compose")


def test_missing_token_explains_how_to_log_in(cfg):
    with pytest.raises(ga.AuthError, match="gmail_auth.py login"):
        ga.load_credentials("read")


@pytest.mark.needs_google
def test_expired_access_token_is_refreshed_and_rewritten_privately(cfg, monkeypatch):
    from google.oauth2.credentials import Credentials
    path = ga.token_path("read")
    ga.write_token(path, _creds(ga.SCOPES_READ, expiry=datetime(2026, 9, 1)).to_json(),
                   authorized_at=datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc))
    os.chmod(path, 0o644)

    def fake_refresh(self, request):
        self.token, self.expiry = "ya29.NEW-ACCESS", datetime(2099, 1, 1)
    monkeypatch.setattr(Credentials, "refresh", fake_refresh)
    creds = ga.load_credentials("read", request=object())
    data = json.loads(path.read_text())
    assert creds.token == data["token"] == "ya29.NEW-ACCESS"
    assert data["agency_authorized_at"] == "2026-09-22T09:00:00Z" and _mode(path) == 0o600


# --------------------------------------------------------------------------- login + status
def _flow(record):
    def run(client_secret, scopes):
        record.append((client_secret, tuple(scopes)))
        return _creds(scopes)
    return run


@pytest.mark.needs_google
def test_login_read_saves_only_the_read_token(cfg, tmp_path):
    secret = tmp_path / "cs.json"
    secret.write_text(json.dumps({"installed": {"client_id": "x"}}))
    calls = []
    status = ga.authorize("read", client_secret=secret, flow_runner=_flow(calls))
    assert calls[0][1] == ga.SCOPES_READ
    assert ga.token_path("read").exists() and not ga.token_path("compose").exists()
    assert status["kind"] == "read" and status["scopes"] == list(ga.SCOPES_READ)


@pytest.mark.needs_google
def test_login_compose_writes_a_separate_token(cfg, tmp_path):
    secret = tmp_path / "cs.json"
    secret.write_text("{}")
    calls = []
    ga.authorize("compose", client_secret=secret, flow_runner=_flow(calls))
    assert calls[0][1] == ga.SCOPES_COMPOSE
    assert ga.token_path("compose").exists() and not ga.token_path("read").exists()
    assert _mode(ga.token_path("compose")) == 0o600


@pytest.mark.needs_google
def test_login_read_refuses_to_save_extra_granted_write_scopes(cfg, tmp_path):
    secret = tmp_path / "cs.json"
    secret.write_text("{}")
    widened = lambda cs, scopes: _creds(tuple(scopes) + ga.SCOPES_COMPOSE)  # noqa: E731
    with pytest.raises(ga.AuthError, match="extra scopes"):
        ga.authorize("read", client_secret=secret, flow_runner=widened)
    assert not ga.token_path("read").exists()


@pytest.mark.needs_google
def test_token_status_reports_the_weekly_reauth_without_secrets(cfg):
    path = ga.token_path("read")
    ga.write_token(path, _creds(ga.SCOPES_READ).to_json(), authorized_at=datetime(2026, 9, 20, 17, 0, tzinfo=timezone.utc))
    fresh = ga.token_status("read", now=datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc))
    assert fresh["exists"] and fresh["private"] and not fresh["needs_reauth"] and fresh["days_left"] == 3
    stale = ga.token_status("read", now=datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc))
    assert stale["needs_reauth"] and "gmail_auth.py login" in stale["action"]
    missing = ga.token_status("compose")
    assert not missing["exists"] and "login --compose" in missing["action"]
    assert not any(s in json.dumps([fresh, stale, missing]) for s in SECRETS)


@pytest.mark.needs_google
def test_status_cli_lists_every_token_and_never_prints_secrets(cfg, capsys):
    ga.write_token(ga.token_path("read"), _creds(ga.SCOPES_READ).to_json())
    ga.write_token(ga.token_path("compose"), _creds(ga.SCOPES_COMPOSE).to_json())
    assert ga.main(["status"]) == 0
    out = capsys.readouterr()
    status = json.loads(out.out)
    assert set(status) == {"read", "compose", "calendar", "send", "legacy_single_token"}
    assert status["read"]["exists"] and status["compose"]["exists"] and not status["calendar"]["exists"]
    assert not any(s in out.out + out.err for s in SECRETS)


def test_missing_oauthlib_gives_an_install_hint(tmp_path, monkeypatch):
    secret = tmp_path / "cs.json"
    secret.write_text("{}")
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", None)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", None)
    with pytest.raises(ga.AuthError, match="pip3 install google-auth-oauthlib"):
        ga.run_consent_flow(secret, ga.SCOPES_READ)


def test_setup_pointer_is_the_departments_single_setup_page():
    assert ga.SETUP_DOC == "docs/SETUP-CONNECTORS.md"
    assert (jc.CODE_ROOT / ga.SETUP_DOC).is_file()


def test_the_legacy_single_token_is_never_read(cfg, capsys):
    legacy = cfg / "gmail_token.json"
    cfg.mkdir(parents=True)
    legacy.write_text("{not json: this file must never be parsed")
    status = ga.status_all()
    assert status["legacy_single_token"]["present"] is True and "not used" in status["legacy_single_token"]["note"]
    with pytest.raises(ga.AuthError, match="no read token"):
        ga.load_credentials("read")
    assert all(ga.token_path(k).name != "gmail_token.json" for k in ga.TOKEN_KINDS)
    comms = ("gmail_pull.py", "mail_classify.py", "linkedin_export.py", "prospects.py", "outbox.py",
             "calendar_hold.py", "js_comms_util.py")
    for name in comms:
        assert "gmail_token.json" not in (Path(ga.__file__).resolve().parent / name).read_text(encoding="utf-8"), name


def test_missing_client_secret_points_to_the_setup_guide(cfg, tmp_path):
    with pytest.raises(ga.AuthError, match="SETUP-CONNECTORS.md"):
        ga.authorize("read", client_secret=tmp_path / "nope.json", flow_runner=lambda *a: pytest.fail("no flow"))


# --------------------------------------------------------------------------- nothing can send
def test_only_gmail_send_calls_the_send_endpoint_once_after_its_approval_checks():
    """gmail_send.py is the one script that sends. Every other script stays send-free, and in
    gmail_send.py the one send call sits in execute(), after the approval checks and the 'started' receipt."""
    scripts = Path(ga.__file__).resolve().parent
    offenders = []
    for py in sorted(scripts.glob("*.py")):
        if py.name == "gmail_send.py":
            continue
        text = py.read_text(encoding="utf-8")
        for needle in (".send(", "messages().send", "drafts().send"):
            if needle in text:
                offenders.append(f"{py.name}: {needle}")
    assert offenders == []
    src = (scripts / "gmail_send.py").read_text(encoding="utf-8")
    assert src.count(".send(") == 1 and src.count("messages().send(") == 1 and "drafts().send" not in src
    body = src.split("\ndef execute(", 1)[1].split("\ndef ", 1)[0]
    at = body.index("messages().send(")
    assert body.index("approval_problems(") < at and body.index("decisions.get(") < at
    assert body.index('gmail send started for {item_id}")') < at and body.index('sha256"]') < at


# --------------------------------------------------------------------------- the send token
SEND = "https://www.googleapis.com/auth/gmail.send"


def test_the_send_token_is_its_own_file_with_gmail_send_only(cfg):
    assert ga.SCOPES_SEND == (SEND,)
    assert ga.token_path("send").name == "gmail_token_send.json"
    assert SEND not in ga.SCOPES_READ + ga.SCOPES_COMPOSE + (ga.SCOPE_CALENDAR,)   # no other token can send
    assert "send" in ga.status_all()


@pytest.mark.needs_google
def test_login_send_writes_a_separate_send_only_token_and_refuses_extra_scopes(cfg, tmp_path):
    secret = tmp_path / "cs.json"
    secret.write_text("{}")
    calls = []
    status = ga.authorize("send", client_secret=secret, flow_runner=_flow(calls))
    assert calls[0][1] == (SEND,) and status["scopes"] == [SEND]
    assert ga.token_path("send").exists() and not ga.token_path("read").exists() and _mode(ga.token_path("send")) == 0o600
    ga.token_path("send").unlink()
    widened = lambda cs, scopes: _creds(tuple(scopes) + (READ,))  # noqa: E731
    with pytest.raises(ga.AuthError, match="extra scopes"):
        ga.authorize("send", client_secret=secret, flow_runner=widened)
    assert not ga.token_path("send").exists()


@pytest.mark.needs_google
def test_a_send_token_carrying_any_other_scope_is_refused_at_load(cfg):
    ga.write_token(ga.token_path("send"), _creds((SEND, READ)).to_json())
    with pytest.raises(ga.AuthError, match="extra scopes"):
        ga.load_credentials("send", refresh=False)


@pytest.mark.needs_google
def test_the_read_path_never_falls_back_to_the_send_token(cfg):
    ga.write_token(ga.token_path("send"), _creds((SEND,)).to_json())
    with pytest.raises(ga.AuthError, match="no read token"):
        ga.load_credentials("read", refresh=False)


def test_cli_login_send_flag(cfg, tmp_path, monkeypatch):
    secret = tmp_path / "cs.json"
    secret.write_text("{}")
    seen = []
    monkeypatch.setattr(ga, "authorize", lambda kind, client_secret: seen.append(kind) or {"reauth_due": "x"})
    assert ga.main(["login", "--send", "--client-secret", str(secret)]) == 0 and seen == ["send"]


def test_an_eval_sandbox_never_reaches_the_real_token_folder(tmp_path, monkeypatch):
    """SETUP-H3 finding 2026-10-01: an eval run read the real calendar token because JOB_AGENCY_CONFIG_DIR was unset."""
    import importlib.util
    import shutil
    sandbox = tmp_path / "sandbox"
    (sandbox / "scripts").mkdir(parents=True)
    src = Path(__file__).resolve().parents[1] / "gmail_auth.py"
    shutil.copy2(src, sandbox / "scripts" / "gmail_auth.py")
    (sandbox / ".eval-sandbox").write_text("x", encoding="utf-8")
    monkeypatch.delenv("JOB_AGENCY_CONFIG_DIR", raising=False)
    spec = importlib.util.spec_from_file_location("ga_sandbox", sandbox / "scripts" / "gmail_auth.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.config_dir() == sandbox / ".no-config"
    assert not mod.token_path("read").exists()
