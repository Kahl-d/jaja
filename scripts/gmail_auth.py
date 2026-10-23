#!/usr/bin/env python3
"""Google OAuth for the job-search scripts: installed-app flow, split tokens, stored outside the repo.

    python3 scripts/gmail_auth.py login              # READ token: gmail.readonly + gmail.labels
    python3 scripts/gmail_auth.py login --compose    # COMPOSE token: gmail.compose (drafts only)
    python3 scripts/gmail_auth.py login --calendar   # CALENDAR token: calendar.events (agenda, holds, invites)
    python3 scripts/gmail_auth.py login --send       # SEND token: gmail.send only (gmail_send.py execute)
    python3 scripts/gmail_auth.py status             # every token's age, scopes, re-auth date; no secrets

Why split: gmail.compose can send mail, so it must never sit in the token that unattended routines
(js-backlog-harvest, mail-triage) load. Harvests use only the READ token and fail closed when it is
missing, even if a compose token exists; a READ token carrying any other scope is refused. The
COMPOSE token is created only by an explicit `login --compose` and loaded only by
`gmail_pull.py draft --approval <file>` or `gmail_send.py execute` (draft mode) after the approval passed
its checks. The SEND token (gmail.send and nothing else: it cannot read, list or draft) is created only by
`login --send` and loaded only by `gmail_send.py execute` after the approval passed its checks; it is the
one script path that sends, one approved message at a time.

Files (all chmod 600 in a 700 folder, refused inside the repo; folder overridable with
$JAJA_CONFIG_DIR): ~/.config/jaja/gmail_token_read.json, gmail_token_compose.json,
calendar_token.json, gmail_token_send.json; client secret $GMAIL_CLIENT_SECRET or ~/.config/jaja/gmail_client_secret.json.
While the consent screen is in "Testing", Google expires refresh tokens 7 days after consent;
each token records `agency_reauth_due`, and `status` says when to log in again.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import agency_paths as _ap
import js_common as jc
import js_comms_util as cu

SCOPE_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
SCOPE_LABELS = "https://www.googleapis.com/auth/gmail.labels"
SCOPE_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
SCOPE_CALENDAR = "https://www.googleapis.com/auth/calendar.events"
SCOPE_SEND = "https://www.googleapis.com/auth/gmail.send"
SCOPES_READ = (SCOPE_READONLY, SCOPE_LABELS)
SCOPES_COMPOSE = (SCOPE_COMPOSE,)
SCOPES_SEND = (SCOPE_SEND,)
TOKEN_KINDS: dict[str, dict[str, Any]] = {
    "read": {"file": "gmail_token_read.json", "scopes": SCOPES_READ, "flag": "", "exclusive": True},
    "compose": {"file": "gmail_token_compose.json", "scopes": SCOPES_COMPOSE, "flag": " --compose", "exclusive": False},
    "calendar": {"file": "calendar_token.json", "scopes": (SCOPE_CALENDAR,), "flag": " --calendar", "exclusive": False},
    "send": {"file": "gmail_token_send.json", "scopes": SCOPES_SEND, "flag": " --send", "exclusive": True},
}
CONFIG_DIR = _ap.config_dir()
TESTING_REFRESH_DAYS = 7
SETUP_DOC = "docs/SETUP-CONNECTORS.md"   # the one setup page (steps 1-2)
LEGACY_TOKEN_NAME = "gmail_token" + ".json"   # pre-split single token: only ever checked for existence, never read


class AuthError(RuntimeError):
    """A human-readable reason the Google auth cannot proceed (never contains secrets)."""


# --------------------------------------------------------------------------- paths / permissions
def config_dir() -> Path:
    """The token folder. Inside an eval sandbox (a `.eval-sandbox` marker next to this scripts/ copy) the real folder is
    never used, even when JAJA_CONFIG_DIR is unset: the sandbox's empty `.no-config` is, so an eval run can never read a real token."""
    env = _ap.env("CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    root = Path(__file__).resolve().parents[1]   # the code root (an eval sandbox is its own)
    if (root / ".eval-sandbox").is_file():
        return root / ".no-config"
    return Path(CONFIG_DIR).expanduser()


def token_path(kind: str) -> Path:
    return config_dir() / TOKEN_KINDS[kind]["file"]


def client_secret_path(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    value = env.get("GMAIL_CLIENT_SECRET")
    return Path(value).expanduser() if value else config_dir() / "gmail_client_secret.json"


def _login_cmd(kind: str) -> str:
    return f"python3 scripts/gmail_auth.py login{TOKEN_KINDS[kind]['flag']}"


def assert_outside_repo(path: Path, repo_root: Path = jc.OS_ROOT) -> None:
    p, repo = Path(path).expanduser().resolve(), Path(repo_root).resolve()
    if p == repo or repo in p.parents:
        raise AuthError(f"credentials must live outside the repo ({repo}); use ~/.config/jaja/, not {p}")


def ensure_private_dir(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    return folder


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def check_permissions(path: Path, fix: bool = True) -> list[str]:
    """Warn (and by default repair) a secret file that is not 0600 or a folder that is not 0700."""
    warnings: list[str] = []
    path = Path(path)
    if path.exists() and _mode(path) != 0o600:
        warnings.append(f"{path} had mode {oct(_mode(path))}; expected 0o600" + (" (fixed)" if fix else ""))
        if fix:
            os.chmod(path, 0o600)
    folder = path.parent
    if folder.exists() and _mode(folder) != 0o700:
        warnings.append(f"{folder} had mode {oct(_mode(folder))}; expected 0o700" + (" (fixed)" if fix else ""))
        if fix:
            os.chmod(folder, 0o700)
    return warnings


def write_token(path: Path, token_json: str, *, authorized_at: datetime | None = None,
                repo_root: Path = jc.OS_ROOT) -> Path:
    """Atomically write a token with mode 0600, stamping the Testing-mode re-auth date."""
    path = Path(path).expanduser()
    assert_outside_repo(path, repo_root)
    ensure_private_dir(path.parent)
    data = json.loads(token_json)
    when = cu.parse_ts(authorized_at) or datetime.now(timezone.utc)
    data["agency_authorized_at"] = cu.to_utc_iso(when)
    data["agency_reauth_due"] = cu.to_utc_iso(when + timedelta(days=TESTING_REFRESH_DAYS))
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return path


# --------------------------------------------------------------------------- status
def token_status(kind: str, now: datetime | None = None, path: Path | None = None) -> dict[str, Any]:
    """Everything useful about one token except its secrets."""
    path = Path(path) if path else token_path(kind)
    base = {"kind": kind, "path": str(path)}
    if not path.exists():
        return {**base, "exists": False, "needs_reauth": True,
                "action": f"log in: {_login_cmd(kind)} (see {SETUP_DOC})"}
    data = json.loads(path.read_text(encoding="utf-8"))
    now = cu.parse_ts(now) or datetime.now(timezone.utc)
    authorized = cu.parse_ts(data.get("agency_authorized_at"))
    due = cu.parse_ts(data.get("agency_reauth_due")) or (
        authorized + timedelta(days=TESTING_REFRESH_DAYS) if authorized else None)
    needs = due is None or now >= due
    mode = _mode(path)
    return {
        **base,
        "exists": True,
        "mode": oct(mode),
        "private": mode == 0o600,
        "scopes": list(data.get("scopes") or []),
        "has_refresh_token": bool(data.get("refresh_token")),
        "authorized_at": cu.to_utc_iso(authorized) if authorized else None,
        "reauth_due": cu.to_utc_iso(due) if due else None,
        "days_left": (due - now).days if due else None,
        "needs_reauth": needs,
        "action": f"log in again now: {_login_cmd(kind)}" if needs else
                  f"ok; log in again by {cu.to_utc_iso(due)} ({_login_cmd(kind)})",
    }


def status_all(now: datetime | None = None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {kind: token_status(kind, now=now) for kind in TOKEN_KINDS}
    legacy = config_dir() / LEGACY_TOKEN_NAME
    out["legacy_single_token"] = {
        "path": str(legacy), "present": legacy.exists(),
        "note": ("not used by any script (the split read/compose tokens replaced it); deleting it is your call"
                 if legacy.exists() else "absent")}
    return out


# --------------------------------------------------------------------------- credentials
def _default_request() -> Any:
    try:
        from google.auth.transport.requests import Request
        return Request()
    except ImportError:  # pragma: no cover - requests missing; fall back to httplib2
        import google_auth_httplib2
        import httplib2
        return google_auth_httplib2.Request(httplib2.Http())


def _check_scopes(kind: str, granted: set[str]) -> None:
    spec = TOKEN_KINDS[kind]
    missing = [s for s in spec["scopes"] if s not in granted]
    if missing:
        raise AuthError(f"the {kind} token lacks {missing}; run: {_login_cmd(kind)}")
    extra = sorted(granted - set(spec["scopes"]))
    if spec["exclusive"] and extra:
        raise AuthError(f"the {kind} token carries extra scopes {extra}; refusing (write scopes must never sit in "
                        f"the unattended token). Delete {token_path(kind)} and run: {_login_cmd(kind)}")


def load_credentials(kind: str = "read", *, token: Path | None = None, refresh: bool = True,
                     request: Any = None, repo_root: Path = jc.OS_ROOT):
    """Credentials for one token kind; fails closed (never falls back to another token)."""
    if kind not in TOKEN_KINDS:
        raise AuthError(f"unknown token kind {kind!r}")
    token = Path(token) if token else token_path(kind)
    if not token.exists():
        if kind == "read" and token_path("compose").exists():
            raise AuthError("only the compose token exists; harvests and routines use the read-only token and "
                            f"never the compose token. Run: {_login_cmd('read')}")
        raise AuthError(f"no {kind} token at {token}; run: {_login_cmd(kind)} (see {SETUP_DOC})")
    assert_outside_repo(token, repo_root)
    for warning in check_permissions(token, fix=True):
        print(f"warning: {warning}", file=sys.stderr)
    data = json.loads(token.read_text(encoding="utf-8"))
    granted = set(data.get("scopes") or [])
    _check_scopes(kind, granted)
    from google.oauth2.credentials import Credentials
    creds = Credentials.from_authorized_user_info(data, scopes=sorted(granted))
    if refresh and not creds.valid:
        if not creds.refresh_token:
            raise AuthError(f"the {kind} token has no refresh token; run: {_login_cmd(kind)}")
        try:
            creds.refresh(request if request is not None else _default_request())
        except Exception as exc:
            raise AuthError(f"refreshing the {kind} token failed; in Testing mode refresh tokens expire 7 days after "
                            f"consent, so run: {_login_cmd(kind)}") from exc
        write_token(token, creds.to_json(), authorized_at=cu.parse_ts(data.get("agency_authorized_at")),
                    repo_root=repo_root)
    return creds


def run_consent_flow(client_secret: Path, scopes: tuple[str, ...] | list[str]):
    """Browser consent on a loopback port (installed-app flow). Needs google-auth-oauthlib."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise AuthError("the consent flow needs google-auth-oauthlib: pip3 install google-auth-oauthlib") from exc
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), scopes=list(scopes))
    return flow.run_local_server(
        port=0, open_browser=True, prompt="consent", access_type="offline",
        authorization_prompt_message="Opening Google's consent screen in your browser. If it does not open, visit:\n{url}",
        success_message="jaja: authorization complete. You can close this tab.")


def authorize(kind: str, client_secret: Path, *, flow_runner: Callable[[Path, tuple[str, ...]], Any] = run_consent_flow,
              now: datetime | None = None, repo_root: Path = jc.OS_ROOT) -> dict[str, Any]:
    if kind not in TOKEN_KINDS:
        raise AuthError(f"unknown token kind {kind!r}")
    client_secret = Path(client_secret).expanduser()
    if not client_secret.is_file():
        raise AuthError(f"OAuth client secret not found at {client_secret}. Create a Desktop OAuth client and "
                        f"download its JSON to that path (or set GMAIL_CLIENT_SECRET): see {SETUP_DOC}")
    for warning in check_permissions(client_secret, fix=True):
        print(f"warning: {warning}", file=sys.stderr)
    scopes = TOKEN_KINDS[kind]["scopes"]
    creds = flow_runner(client_secret, scopes)
    granted = set(getattr(creds, "granted_scopes", None) or creds.scopes or [])
    extra = sorted(granted - set(scopes))
    if TOKEN_KINDS[kind]["exclusive"] and extra:
        raise AuthError(f"Google granted extra scopes {extra} to the {kind} login; not saving. Remove the app at "
                        "https://myaccount.google.com/permissions and log in again")
    if not getattr(creds, "refresh_token", None):
        raise AuthError("Google returned no refresh token; remove the app at https://myaccount.google.com/permissions "
                        "and log in again")
    write_token(token_path(kind), creds.to_json(), authorized_at=now, repo_root=repo_root)
    return token_status(kind, now=now)


def build_service(kind: str = "read"):
    """googleapiclient resource for a token kind: read/compose/send -> Gmail v1, calendar -> Calendar v3."""
    creds = load_credentials(kind)
    from googleapiclient.discovery import build
    api, version = ("calendar", "v3") if kind == "calendar" else ("gmail", "v1")
    return build(api, version, credentials=creds, cache_discovery=False)


# --------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Log in to Google for the job-search scripts (split tokens).")
    ap.add_argument("action", nargs="?", choices=("login", "status"), default="login")
    which = ap.add_mutually_exclusive_group()
    which.add_argument("--compose", action="store_true", help="the gmail.compose token (Gmail drafts only)")
    which.add_argument("--calendar", action="store_true", help="the calendar.events token (agenda, holds, invites)")
    which.add_argument("--send", action="store_true", help="the gmail.send token (gmail_send.py execute only)")
    ap.add_argument("--status", action="store_true", help="same as the `status` action")
    ap.add_argument("--client-secret", help="OAuth client JSON (default: $GMAIL_CLIENT_SECRET or ~/.config/jaja/)")
    args = ap.parse_args(argv)
    if args.status or args.action == "status":
        print(json.dumps(status_all(), indent=2))
        return 0
    kind = "compose" if args.compose else "calendar" if args.calendar else "send" if args.send else "read"
    secret = Path(args.client_secret).expanduser() if args.client_secret else client_secret_path()
    try:
        status = authorize(kind, client_secret=secret)
    except AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(status, indent=2))
    print(f"Saved the {kind} token (mode 600). Testing-mode tokens expire after {TESTING_REFRESH_DAYS} days: "
          f"log in again before {status.get('reauth_due')}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
