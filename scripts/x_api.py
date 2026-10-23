#!/usr/bin/env python3
"""X (Twitter) for the job search through the official X API. Reads are budgeted; every write waits for the owner.

    python3 scripts/x_api.py login [--write]          # you sign in yourself in your browser (OAuth 2.0 + PKCE)
    python3 scripts/x_api.py status                   # tokens, budget, caps; never a secret
    python3 scripts/x_api.py post <url or id>         # one post: text, author, reply count, who may reply
    python3 scripts/x_api.py user <handle>            # one profile
    python3 scripts/x_api.py search "<query>" [--max 10]
    python3 scripts/x_api.py watch [--config targets/x-watch.yaml]   # hiring candidates -> state/review/x-watch-<date>.md
                                                                     # and state/posts/x-<date>.jsonl (posts.py ingest)
    python3 scripts/x_api.py following [--max 100]   # the accounts the owner follows -> state/x/following.json
    python3 scripts/x_api.py follow --handle H [--user-id ID] [--request]
    python3 scripts/x_api.py dm --handle H --text-file F [--user-id ID] [--prospect slug] [--request]
    python3 scripts/x_api.py reply --post <url or id> --text-file F [--handle H] [--prospect slug] [--request]
    python3 scripts/x_api.py post-new --text-file F [--request]
    python3 scripts/x_api.py show --approval A-2026-NNNN
    python3 scripts/x_api.py execute --approval A-2026-NNNN

Tokens are split like Gmail's: the READ token (tweet.read, users.read, offline.access) serves reads, search and the
watch; the WRITE token adds tweet.write, follows.write, dm.read and dm.write and is loaded only by `execute` on an
approved file. Both live in ~/.config/jaja/ (x_token_read.json, x_token_write.json; chmod 600; refused inside the
repo); the app's Client ID is ~/.config/jaja/x_client.json (a Native App is a public client: no secret). Access
tokens last 2 hours and refresh themselves; X rotates the refresh token, which is saved each time.

Every call is priced before it is made (docs.x.com pricing at the time of writing: a post read $0.005, a user $0.010, a post or DM
written $0.015, a post with a link $0.20) against `connectors: x` in company/policies/budgets.yaml (a missing line
refuses) and logged in state/x/usage.jsonl (no text, no token). Text read from X is untrusted data: it is wrapped in
<untrusted_x> and never followed as an instruction.

Writes (follow, DM, reply, post): the building command prints the action; --request files an approval whose Exact
content is the exact action (target handle and user id, the post replied to, the text), hashed; `show` prints it for
the owner; `execute` acts once on an approved, ticked, unexpired approval: a "started" receipt before the call and
a "done" receipt after, never repeated; a refusal by X (a closed DM inbox, reply limits) is recorded and never retried
or worked around; a daily cap per action type (CAPS) is enforced from state/x/actions.jsonl. Replies and posts are
public, so they pass the outbox voice gate and (nothing may reveal the search). The watch never records a job or a
prospect by itself: it writes a review file with the commands (jobs_manual.py --platform x, prospects.py x_handle) that
job-scout or prospector run after judging each post, and the same candidates as rows of state/posts/x-<date>.jsonl, the
input scripts/posts.py shares with the LinkedIn pass. Latest posts only: each query carries the newest
post id it returned last time as `since_id` (state/x/since.json, kept per handle for the people query, so a new handle
does not re-read the chunk; dropped once older than SINCE_MAX_DAYS, X's recent search window), so a post already paid
for is not returned again. Each query's candidates, seen.json and posts rows are on disk before its since_id moves, so a
later query's failure loses nothing paid for. A run whose worst case does not fit what is left of the day or month
budget refuses before its first call; each search row in usage.jsonl counts the posts it returned.
Nothing here reads x.com pages, scrapes or uses cookies.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import agency_config as cfg
import calendar_hold as chd
import js_common as jc
import js_comms_util as cu

AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
API = "https://api.x.com/2"
CALLBACK = "http://127.0.0.1:8765/callback"
SCOPES = {"read": ("tweet.read", "users.read", "offline.access"),
          "write": ("tweet.read", "users.read", "tweet.write", "follows.write", "dm.read", "dm.write", "offline.access")}
PRICE = {"post": 0.005, "user": 0.010, "write": 0.015, "write_link": 0.20}
CAPS = {"follow": 10, "dm": 5, "reply": 5, "post": 2}
KINDS = ("follow", "dm", "reply", "post")
TITLES = {"follow": "X FOLLOW", "dm": "X DIRECT MESSAGE", "reply": "X REPLY (public)", "post": "X POST (public)"}
LOCAL = ZoneInfo(str(cfg.get("identity.timezone", "UTC")))
HANDLE = re.compile(r"^[a-z0-9_]{1,15}$")
POST_URL = re.compile(r"^https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/[^/?#]+/status(?:es)?/(\d{5,25})")
HIRING = re.compile(r"\b(?:hiring|join (?:our|my) team|(?:is|are|am|'re|we're) looking for|open roles?|"
                    r"growing (?:the|our|my) team|looking for (?:a|an|our (?:first|next)) )", re.I)
ROLE = re.compile(r"\b(?:engineers?|engineering|developers?|researchers?|scientists?|ML|AI|LLMs?|agents?|agentic|"
                  r"infra(?:structure)?|founding)\b", re.I)
POST_FIELDS = "created_at,public_metrics,author_id,conversation_id,reply_settings,entities"   # entities: expanded links
USER_FIELDS = "username,name,description,public_metrics,created_at"   # counts give the post rate (posts.py: a bot)
QUERY_LIMIT = 512                 # X recent search: a query is at most 512 characters (self-serve)
SINCE_MAX_DAYS = 6                # recent search reads 7 days back; an older since_id is dropped, not sent
SNOWFLAKE_EPOCH_MS = 1288834974657


class XError(RuntimeError):
    """Why an X call or action cannot go ahead (in words; never a token)."""


# --------------------------------------------------------------------------- HTTP
def _http(method: str, url: str, *, headers: dict | None = None, params: dict | None = None,
          json_body: Any = None, form: dict | None = None) -> tuple[int, dict]:
    """urllib call; (status, parsed JSON or {}). HTTP errors come back as their status, never raised."""
    import urllib.error
    import urllib.request
    if params:
        url = f"{url}?{urlencode(params)}"
    data, hdrs = None, dict(headers or {})
    if json_body is not None:
        data, hdrs["Content-Type"] = json.dumps(json_body).encode("utf-8"), "application/json"
    elif form is not None:
        data, hdrs["Content-Type"] = urlencode(form).encode("utf-8"), "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw) if raw.strip() else {}
        except ValueError:
            return exc.code, {"detail": raw[:200]}


def _detail(body: dict) -> str:
    errs = body.get("errors") if isinstance(body, dict) else None
    text = (body or {}).get("detail") or (body or {}).get("title") or (errs[0].get("message") if errs else "") or ""
    return cu.one_line(str(text), 200)


# --------------------------------------------------------------------------- tokens
def _cfg() -> Path:
    import gmail_auth
    return gmail_auth.config_dir()


def token_path(kind: str) -> Path:
    return _cfg() / f"x_token_{kind}.json"


def client_path() -> Path:
    return _cfg() / "x_client.json"


def _login_cmd(kind: str) -> str:
    return "python3 scripts/x_api.py login" + (" --write" if kind == "write" else "")


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def save_token(kind: str, data: dict[str, Any]) -> Path:
    import gmail_auth
    path = token_path(kind)
    gmail_auth.assert_outside_repo(path)
    gmail_auth.ensure_private_dir(path.parent)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return path


def _load(kind: str) -> dict[str, Any]:
    path = token_path(kind)
    if not path.is_file():
        raise XError(f"no {kind} X token; run: {_login_cmd(kind)} (SETUP-CONNECTORS.md section 14)")
    return json.loads(path.read_text(encoding="utf-8"))


def _client_id() -> str:
    path = client_path()
    try:
        cid = json.loads(path.read_text(encoding="utf-8")).get("client_id")
    except (OSError, ValueError):
        cid = None
    if not cid:
        raise XError(f"no X Client ID at {path}: save {{\"client_id\": \"...\"}} there (x_client.json, chmod 600)")
    return str(cid)


def _check_scopes(kind: str, granted: list[str]) -> None:
    missing = [s for s in SCOPES[kind] if s not in granted]
    if missing:
        raise XError(f"the {kind} login lacks {missing}; run: {_login_cmd(kind)}")
    if kind == "read" and any(s.endswith(".write") for s in granted):
        raise XError("the read login carries write scopes; refusing (write scopes never sit in the read token)")


def token_status(kind: str, now: datetime | None = None) -> dict[str, Any]:
    path = token_path(kind)
    if not path.is_file():
        return {"kind": kind, "exists": False, "action": f"log in: {_login_cmd(kind)}"}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"kind": kind, "exists": True, "username": data.get("username"), "scopes": data.get("scopes"),
            "access_expires_at": data.get("expires_at"), "has_refresh_token": bool(data.get("refresh_token")),
            "authorized_at": data.get("authorized_at"),
            "mode": oct(os.stat(path).st_mode & 0o777)}


def login(kind: str, *, http: Callable = _http, open_url: Callable[[str], Any] | None = None,
          wait_for_code: Callable[[str], tuple[str, str]] | None = None, now: datetime | None = None) -> dict[str, Any]:
    """OAuth 2.0 authorization code with PKCE on the loopback callback. you consent in your own browser."""
    if kind not in SCOPES:
        raise XError(f"unknown token kind {kind!r}")
    now = now or datetime.now(timezone.utc)
    client_id = _client_id()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    state = secrets.token_urlsafe(16)
    url = AUTHORIZE_URL + "?" + urlencode({"response_type": "code", "client_id": client_id, "redirect_uri": CALLBACK,
                                           "scope": " ".join(SCOPES[kind]), "state": state,
                                           "code_challenge": challenge, "code_challenge_method": "S256"})
    (open_url or _open_browser)(url)
    code, got = (wait_for_code or _wait_for_code)(state)
    if got != state:
        raise XError("the state returned by X does not match; nothing saved (start the login again)")
    status, tok = http("POST", TOKEN_URL, form={"grant_type": "authorization_code", "code": code,
                                                "redirect_uri": CALLBACK, "code_verifier": verifier,
                                                "client_id": client_id})
    if status != 200 or not tok.get("access_token"):
        raise XError(f"X refused the login ({status}): {_detail(tok)}")
    granted = sorted(str(tok.get("scope") or "").split())
    _check_scopes(kind, granted)
    if not tok.get("refresh_token"):
        raise XError("X returned no refresh token (offline.access missing); nothing saved")
    status, me = http("GET", f"{API}/users/me", headers={"Authorization": f"Bearer {tok['access_token']}"})
    if status != 200:
        raise XError(f"X did not return the signed-in account ({status}): {_detail(me)}")
    save_token(kind, {"access_token": tok["access_token"], "refresh_token": tok["refresh_token"], "scopes": granted,
                      "expires_at": _stamp(now + timedelta(seconds=int(tok.get("expires_in") or 7200))),
                      "user_id": me["data"]["id"], "username": me["data"]["username"], "authorized_at": _stamp(now)})
    return token_status(kind, now)


def _open_browser(url: str) -> None:
    import webbrowser
    print(f"Opening X's consent page. If it does not open, visit:\n{url}", file=sys.stderr)
    webbrowser.open(url)


def _wait_for_code(state: str, timeout: int = 300) -> tuple[str, str]:
    """One request on 127.0.0.1:8765/callback; returns (code, state)."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs, urlsplit
    got: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = parse_qs(urlsplit(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"jaja: X authorization received. You can close this tab.")

        def log_message(self, *a):  # quiet
            pass

    server = HTTPServer(("127.0.0.1", 8765), Handler)
    server.timeout = timeout
    server.handle_request()
    server.server_close()
    if "error" in got:
        raise XError(f"X consent was not given: {got.get('error')}")
    if "code" not in got:
        raise XError("no authorization code arrived on the callback (timed out?)")
    return got["code"], got.get("state", "")


def access_token(kind: str, *, http: Callable = _http, now: datetime | None = None) -> str:
    """The bearer for `kind`, refreshed when it is within a minute of expiry. Never falls back to the other token."""
    now = now or datetime.now(timezone.utc)
    data = _load(kind)
    _check_scopes(kind, list(data.get("scopes") or []))
    try:
        expires = datetime.fromisoformat(str(data.get("expires_at")).replace("Z", "+00:00"))
    except ValueError:
        expires = now
    if expires - timedelta(seconds=60) > now:
        return str(data["access_token"])
    if not data.get("refresh_token"):
        raise XError(f"the {kind} X token expired and has no refresh token; run: {_login_cmd(kind)}")
    status, tok = http("POST", TOKEN_URL, form={"grant_type": "refresh_token", "refresh_token": data["refresh_token"],
                                                "client_id": _client_id()})
    if status != 200 or not tok.get("access_token"):
        raise XError(f"refreshing the {kind} X token failed ({status}); run: {_login_cmd(kind)}")
    data.update({"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token") or data["refresh_token"],
                 "expires_at": _stamp(now + timedelta(seconds=int(tok.get("expires_in") or 7200)))})
    save_token(kind, data)
    return str(data["access_token"])


# --------------------------------------------------------------------------- budget
def _day(now: datetime) -> str:
    return now.astimezone(LOCAL).date().isoformat()


def budget_caps(os_root: Path) -> dict[str, float]:
    import yaml
    path = Path(os_root) / "company" / "policies" / "budgets.yaml"
    try:
        line = ((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("connectors") or {}).get("x")
    except OSError:
        line = None
    if not isinstance(line, dict) or "usd_per_day" not in line or "usd_per_month" not in line:
        raise XError("no `connectors: x: {usd_per_month, usd_per_day}` line in company/policies/budgets.yaml; "
                     "nothing is called without a budget")
    return {"usd_per_day": float(line["usd_per_day"]), "usd_per_month": float(line["usd_per_month"])}


def _ledger(js: Path, name: str) -> Path:
    return Path(js) / "state" / "x" / name


def spent(js: Path, now: datetime) -> dict[str, float]:
    day = _day(now)
    out = {"today": 0.0, "month": 0.0}
    path = _ledger(js, "usage.jsonl")
    if path.is_file():
        for row in jc.read_jsonl(path):
            if str(row.get("day", "")).startswith(day[:7]):
                out["month"] += float(row.get("usd") or 0)
                if row.get("day") == day:
                    out["today"] += float(row.get("usd") or 0)
    return out


class Client:
    """Budgeted calls with one token kind."""

    def __init__(self, kind: str, *, http: Callable = _http, now: datetime | None = None, js: Path | None = None,
                 os_root: Path | None = None):
        self.kind, self.http = kind, http
        self.now = now or datetime.now(timezone.utc)
        self.js = Path(js) if js else jc.JS_ROOT
        self.os_root = Path(os_root) if os_root else jc.OS_ROOT

    def call(self, method: str, path: str, *, endpoint: str, worst: float, actual: Callable[[dict], float],
             params: dict | None = None, json_body: Any = None) -> tuple[int, dict]:
        caps = budget_caps(self.os_root)
        used = spent(self.js, self.now)
        if used["today"] + worst > caps["usd_per_day"] or used["month"] + worst > caps["usd_per_month"]:
            raise XError(f"X budget: {endpoint} could cost ${worst:.3f}; used today ${used['today']:.3f} of "
                         f"${caps['usd_per_day']:g}, this month ${used['month']:.3f} of ${caps['usd_per_month']:g}")
        token = access_token(self.kind, http=self.http, now=self.now)
        status, body = self.http(method, f"{API}{path}", headers={"Authorization": f"Bearer {token}"}, params=params,
                                 json_body=json_body)
        usd = round(actual(body), 4) if 200 <= status < 300 else 0.0
        row = {"at": _stamp(self.now), "day": _day(self.now), "endpoint": endpoint, "status": status, "usd": usd}
        if isinstance(body.get("data"), list):                # yield: the posts (or accounts) a read returned
            row["results"] = len(body["data"])
        if status == 402:                                    # Scout lesson L-6800106c: the account's credits are spent
            row["note"] = "credits depleted"
        jc.append_jsonl(_ledger(self.js, "usage.jsonl"), row)
        return status, body


def credit_state(js: Path) -> dict[str, Any]:
    """From state/x/usage.jsonl (Scout lesson L-6800106c): `depleted` while the last 402 (X: credits depleted) has no
    successful call after it, `ok` once one has, `unknown` before any call. The last 402 stays on record."""
    path = _ledger(js, "usage.jsonl")
    last_402, last_ok = None, None
    for row in (jc.read_jsonl(path) if path.is_file() else []):
        status = int(row.get("status") or 0)
        if status == 402:
            last_402 = {"at": row.get("at"), "endpoint": row.get("endpoint")}
        elif 200 <= status < 300:
            last_ok = row.get("at")
    if last_402 is None:
        state = "ok" if last_ok else "unknown"
    else:
        state = "ok" if last_ok and str(last_ok) > str(last_402["at"]) else "depleted"
    return {"state": state, "last_402": last_402, "last_ok": last_ok}


# --------------------------------------------------------------------------- reads
def post_id(ref: str) -> str:
    ref = str(ref or "").strip()
    if re.fullmatch(r"\d{5,25}", ref):
        return ref
    m = POST_URL.match(ref)
    if not m:
        raise XError(f"not an X post URL or id: {ref!r}")
    return m.group(1)


def _users(body: dict) -> dict[str, dict]:
    return {u["id"]: u for u in ((body.get("includes") or {}).get("users") or [])}


def _post_row(p: dict, users: dict[str, dict]) -> dict[str, Any]:
    u = users.get(str(p.get("author_id")), {})
    handle = u.get("username") or "i"
    m = p.get("public_metrics") or {}
    return {"id": p["id"], "url": f"https://x.com/{handle}/status/{p['id']}", "author": f"@{handle}",
            "author_name": u.get("name"), "created_at": p.get("created_at"), "reply_settings": p.get("reply_settings"),
            "conversation_id": p.get("conversation_id"), "reply_count": m.get("reply_count"),
            "like_count": m.get("like_count"), "retweet_count": m.get("retweet_count"),
            "quote_count": m.get("quote_count"), "impression_count": m.get("impression_count"),
            "text": jc.wrap_untrusted(p.get("text") or "", "x"), "_raw": p.get("text") or "",
            "_links": [str(e.get("expanded_url")) for e in ((p.get("entities") or {}).get("urls") or [])
                       if e.get("expanded_url")],
            "_bio": cu.one_line(u.get("description") or "", 300), "_rate": posts_per_day(u)}


def posts_per_day(user: dict) -> float | None:
    """The author's posts a day over the account's life (public_metrics.tweet_count over its age), when X returned both;
    posts.py reads a very high rate as a bot. None when either is missing (missing is not zero)."""
    count = (user.get("public_metrics") or {}).get("tweet_count")
    try:
        born = datetime.fromisoformat(str(user.get("created_at")).replace("Z", "+00:00"))
    except ValueError:
        return None
    if not isinstance(count, int) or isinstance(count, bool):
        return None
    days = max(1.0, (datetime.now(timezone.utc) - born).total_seconds() / 86400)
    return round(count / days, 1)


def _cost(body: dict) -> float:
    data = body.get("data")
    n = len(data) if isinstance(data, list) else (1 if data else 0)
    return n * PRICE["post"] + len(_users(body)) * PRICE["user"]


def _check(status: int, body: dict, what: str) -> None:
    if not 200 <= status < 300:
        raise XError(f"X answered {status} for {what}: {_detail(body)}")


def _public(row: dict) -> dict:
    return {k: v for k, v in row.items() if not k.startswith("_")}


def read_post(client: Client, ref: str) -> dict[str, Any]:
    pid = post_id(ref)
    status, body = client.call("GET", f"/tweets/{pid}", endpoint="post", worst=PRICE["post"] + PRICE["user"],
                               actual=_cost, params={"tweet.fields": POST_FIELDS, "expansions": "author_id",
                                                     "user.fields": USER_FIELDS})
    _check(status, body, f"post {pid}")
    return _public(_post_row(body["data"], _users(body)))


def read_user(client: Client, handle: str) -> dict[str, Any]:
    h = _handle(handle)
    status, body = client.call("GET", f"/users/by/username/{h}", endpoint="user", worst=PRICE["user"],
                               actual=lambda b: PRICE["user"] if b.get("data") else 0.0,
                               params={"user.fields": "description,public_metrics,created_at,url,location,verified,"
                                                      "receives_your_dm"})
    _check(status, body, f"@{h}")
    u = body["data"]
    return {"id": u["id"], "handle": f"@{u['username']}", "name": u.get("name"), "url": f"https://x.com/{u['username']}",
            "location": u.get("location"), "followers": (u.get("public_metrics") or {}).get("followers_count"),
            "receives_your_dm": u.get("receives_your_dm"),
            "description": jc.wrap_untrusted(u.get("description") or "", "x")}


def following(client: Client, max_results: int = 100) -> dict[str, Any]:
    """The accounts the owner follows, written to state/x/following.json. Priced as one
    user read per account; a 402 (credits depleted) or any refusal raises and writes nothing. Bios are untrusted text
    and stay data: network_map only matches role words in them."""
    me = str(_load(client.kind).get("user_id") or "")
    if not me.isdigit():
        raise XError(f"the {client.kind} token carries no user id; run: {_login_cmd(client.kind)}")
    n = max(1, min(1000, int(max_results)))
    status, body = client.call("GET", f"/users/{me}/following", endpoint="following", worst=n * PRICE["user"],
                               actual=lambda b: len(b.get("data") or []) * PRICE["user"],
                               params={"max_results": n, "user.fields": "description,name,username"})
    _check(status, body, "following")
    users = [{"id": str(u.get("id")), "name": u.get("name"), "username": u.get("username"),
              "description": cu.one_line(u.get("description") or "", 300)} for u in body.get("data") or []]
    path = _ledger(client.js, "following.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(json.dumps({"fetched_at": _stamp(client.now), "users": users}, indent=1, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
    return {"accounts": len(users), "path": str(path), "next_token": (body.get("meta") or {}).get("next_token")}


def _search_raw(client: Client, query: str, max_results: int, since_id: str | None = None) -> list[dict]:
    n = max(10, min(100, int(max_results)))
    params = {"query": query, "max_results": n, "tweet.fields": POST_FIELDS, "expansions": "author_id",
              "user.fields": USER_FIELDS}
    if since_id:
        params["since_id"] = since_id
    status, body = client.call("GET", "/tweets/search/recent", endpoint="search",
                               worst=n * (PRICE["post"] + PRICE["user"]), actual=_cost, params=params)
    _check(status, body, "search")
    users = _users(body)
    return [_post_row(p, users) for p in body.get("data") or []]


def search(client: Client, query: str, max_results: int = 10) -> list[dict[str, Any]]:
    return [_public(r) for r in _search_raw(client, query, max_results)]


# --------------------------------------------------------------------------- watch
def hiring_signal(text: str) -> bool:
    return bool(HIRING.search(text or "")) and bool(ROLE.search(text or ""))


def watch_plan(js: Path, config: Path) -> dict[str, Any]:
    """The searches a watch run makes and their worst-case cost (every result a post and an author), with no call."""
    import yaml
    cfg = yaml.safe_load(Path(config).read_text(encoding="utf-8")) or {}
    n = int(cfg.get("max_results") or 10)
    people = [_handle(h) for h in cfg.get("people") or []]
    import people_watch                                   # HUNT A6: the people watch adds its active X handles
    people += [h for h in people_watch.x_handles(js) if h not in people]
    runs = [(q, "people on the watch list") for q in people_watch.x_queries(people)]   # first, the focus
    skip = deny_handles(js)
    runs += [(exclude(str(q.get("q")), skip), str(q.get("why") or "")) for q in cfg.get("queries") or [] if q.get("q")]
    return {"runs": runs, "max_results": n, "worst_usd": len(runs) * n * (PRICE["post"] + PRICE["user"])}


def deny_handles(js: Path) -> list[str]:
    """The accounts on targets/x-deny.yaml (news, job boards, agencies, bots), in file order."""
    import posts
    return list(posts.load_deny(js)["accounts"])


def exclude(query: str, handles: list[str], limit: int = QUERY_LIMIT) -> str:
    """The search with `-from:<handle>` for each denied account, in order, while the query stays within X's limit (a
    handle that does not fit is still dropped by posts.py; its posts just cost a read). A people query is never given
    these (it names its own accounts)."""
    out = query
    for h in handles:
        part = f" -from:{h}"
        if f"-from:{h}" not in out and len(out) + len(part) <= limit:
            out += part
    return out


def post_age_days(pid: str, now: datetime) -> float | None:
    """A post id's age in days, read from the id itself (X ids are snowflakes: milliseconds since X's epoch << 22)."""
    try:
        ms = (int(pid) >> 22) + SNOWFLAKE_EPOCH_MS
    except (TypeError, ValueError):
        return None
    return (now - datetime.fromtimestamp(ms / 1000, tz=timezone.utc)).total_seconds() / 86400


def since_keys(query: str) -> list[str]:
    """Where a query's newest post id is kept: one key per handle for a people query (`from:<handle>`), so adding or
    dropping a people-watch handle, which rewrites the chunk's text, keeps the chunk's place; the text otherwise."""
    base = re.sub(r"\s+-from:[A-Za-z0-9_]{1,15}", "", query)    # the deny exclusions are not the query's identity
    handles = re.findall(r"(?<![-\w])from:([A-Za-z0-9_]{1,15})", base)
    return [f"from:{h.lower()}" for h in handles] or [base]


def _post_day(created_at: Any) -> str | None:
    """The post's day in your time zone (the department's day), from X's UTC created_at."""
    try:
        return _day(datetime.fromisoformat(str(created_at).replace("Z", "+00:00")))
    except ValueError:
        return None


def watch(client: Client, config: Path, *, today: date) -> dict[str, Any]:
    """Run the watch list; write the hiring candidates to state/review/x-watch-<date>.md and state/posts/x-<date>.jsonl.
    Records no job and no prospect. Each query asks only for posts newer than the newest it returned before."""
    plan = watch_plan(client.js, config)
    n, runs = plan["max_results"], plan["runs"]
    if isinstance(client, Client):                        # every real run; a test's stand-in client has no ledger
        caps, used = budget_caps(client.os_root), spent(client.js, client.now)
        if (used["today"] + plan["worst_usd"] > caps["usd_per_day"]
                or used["month"] + plan["worst_usd"] > caps["usd_per_month"]):
            raise XError(f"X budget: this watch could cost ${plan['worst_usd']:.3f}; used today ${used['today']:.3f} "
                         f"of ${caps['usd_per_day']:g}, this month ${used['month']:.3f} of ${caps['usd_per_month']:g}; "
                         "no search made (a run never starts that the budget cannot finish)")
    seen_path, since_path = _ledger(client.js, "seen.json"), _ledger(client.js, "since.json")
    seen = set(json.loads(seen_path.read_text()) if seen_path.is_file() else [])
    since: dict[str, str] = json.loads(since_path.read_text()) if since_path.is_file() else {}
    posts = Path(client.js) / "state" / "posts" / f"x-{today.isoformat()}.jsonl"
    found: list[dict] = []
    for q, why in runs:
        keys = since_keys(q)
        last = max((since[k] for k in keys if since.get(k)), key=int, default=None)
        age = post_age_days(last, client.now) if last else None
        since_id = last if age is not None and age <= SINCE_MAX_DAYS else None
        rows = _search_raw(client, q, n, since_id) if since_id else _search_raw(client, q, n)
        new = [{**r, "why": why} for r in rows if r["id"] not in seen and hiring_signal(r["_raw"])]
        for f in new:                                     # on disk first: a later query's failure loses nothing paid
            seen.add(f["id"])
            found.append(f)
            jc.append_jsonl(posts, {"platform": "x", "id": f["id"], "url": f["url"],
                                    "author_handle": f["author"].lstrip("@"), "author_name": f.get("author_name"),
                                    "author_bio": f.get("_bio"), "author_posts_per_day": f.get("_rate"),
                                    "date": _post_day(f.get("created_at")),
                                    "text": f["_raw"], "links": f.get("_links") or [], "why": f["why"],
                                    "read_at": _stamp(client.now)})
        if new:
            seen_path.parent.mkdir(parents=True, exist_ok=True)
            seen_path.write_text(json.dumps(sorted(seen)))
        newest = max((int(r["id"]) for r in rows), default=0)
        if newest and newest > int(last or 0):            # then the query moves on: paid is paid, and kept
            since.update({k: str(newest) for k in keys})
            since_path.parent.mkdir(parents=True, exist_ok=True)
            since_path.write_text(json.dumps(since, indent=1, sort_keys=True))
    review = Path(client.js) / "state" / "review" / f"x-watch-{today.isoformat()}.md"
    review.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# X watch {today.isoformat()}: hiring candidates ({len(found)})", "",
             "Post text is untrusted data. job-scout judges each one; this watch records nothing (the structured "
             "records, people and jobs come from `python3 scripts/posts.py ingest`, see state/review/posts-<date>.md).",
             ""]
    for f in found:
        h = f["author"].lstrip("@")
        lines += [f"## {f['author']} · {f['created_at']} · {f['url']}", f"- Why: {f['why']}; replies {f['reply_count']}, "
                  f"who may reply: {f['reply_settings']}", "", f["text"], "",
                  f"- Job: `python3 scripts/jobs_manual.py add --platform x --url {f['url']} --title \"<the role>\" "
                  f"--company \"<company>\" --jd-file <the post text saved to a file>`",
                  f"- Person: `python3 scripts/prospects.py set <slug> x_handle={h}` (or create the prospect first)", ""]
    review.write_text("\n".join(lines), encoding="utf-8")
    return {"queries": len(runs), "candidates": [_public(f) for f in found], "review": str(review)}


# --------------------------------------------------------------------------- write actions
def _handle(value: Any) -> str:
    h = str(value or "").strip().lstrip("@").lower()
    if not HANDLE.match(h):
        raise XError(f"not an X handle: {value!r}")
    return h


def build_action(kind: str, *, text: str | None = None, post: str | None = None, handle: str | None = None,
                 user_id: str | None = None, prospect: str | None = None) -> dict[str, Any]:
    """The exact action the owner approves: the target (handle, user id, or the post replied to) and the text."""
    if kind not in KINDS:
        raise XError("an X action is follow, dm, reply or post")
    h = _handle(handle) if handle else None
    reply_to = post_id(post) if post else None
    body = cu.canonical_text(text) if text is not None else None
    if kind in ("follow", "dm"):
        if not h:
            raise XError(f"a {kind} needs --handle")
        if not user_id or not str(user_id).isdigit():
            raise XError(f"a {kind} needs the account's user id (resolved from the handle with the read token)")
    if kind == "reply" and not reply_to:
        raise XError("a reply needs the post it answers (--post)")
    if kind != "follow":
        if not body or not body.strip():
            raise XError(f"a {kind} needs text")
        limit = 10000 if kind == "dm" else 280
        if len(body) > limit:
            raise XError(f"the text is {len(body)} characters; X allows {limit}")
        import outbox
        issues = outbox.lint_draft(f"x-{kind}", body)
        if issues:
            raise XError("the text fails the voice gate: " + "; ".join(issues))
    return {"action": kind, "text": body if kind != "follow" else None, "in_reply_to": reply_to, "handle": h,
            "user_id": str(user_id) if user_id else None, "prospect": prospect or None}


def _cost_of(action: dict[str, Any]) -> float:
    if action["action"] in ("reply", "post") and re.search(r"https?://", action.get("text") or ""):
        return PRICE["write_link"]
    return PRICE["write"]


def _target(action: dict[str, Any]) -> str:
    if action["action"] == "reply":
        return f"https://x.com/{action.get('handle') or 'i'}/status/{action['in_reply_to']}"
    if action["action"] == "post":
        return "your X timeline"
    return f"@{action['handle']}"


def render(action: dict[str, Any]) -> str:
    """The action as the owner reads it before approving."""
    kind = action["action"]
    lines = [f"{TITLES[kind]} from your X account"]
    if kind in ("follow", "dm"):
        lines.append(f"To: @{action['handle']} (user id {action['user_id']})")
    if kind == "reply":
        lines.append(f"In reply to: {_target(action)}")
    if action.get("text") is not None:
        lines += ["", action["text"], ""]
    seen = {"follow": "public: the follow shows on both profiles", "dm": "private: only the recipient sees it",
            "reply": "public: anyone on X sees it under your name, beneath the post",
            "post": "public: anyone on X sees it on your timeline"}[kind]
    lines += [f"Who sees it: {seen}", f"Daily cap: cap {CAPS[kind]} a day", f"Cost: about ${_cost_of(action):.3f}"]
    if action.get("prospect"):
        lines.append(f"Logged as a touch for: {action['prospect']}")
    return "\n".join(lines)


def request(roots: cu.Roots, action: dict[str, Any], *, today: date | None = None,
            requested_by: str = "role:prospector") -> tuple[str, Path]:
    today = today or jc.today()
    body = json.dumps(action, indent=2, sort_keys=True, ensure_ascii=False)
    item = cu.ApprovalItem(id=f"x-{jc.short_hash(body)}", channel="x", target=_target(action),
                           subject=TITLES[action["action"]], body=body, label=f"{TITLES[action['action']]}: {_target(action)}")
    public = action["action"] in ("reply", "post")
    effect = {"follow": f"Your X account follows @{action.get('handle')}; they are notified.",
              "dm": f"X delivers this direct message to @{action.get('handle')} from your account.",
              "reply": "X publishes this reply under the post, publicly, under your name. It can be deleted later "
                       "but may already have been seen.",
              "post": "X publishes this post on your timeline, publicly. It can be deleted later but may already "
                      "have been seen."}[action["action"]]
    why = f"An approval-gated X action (x_api.py). Read it with `x_api.py show --approval <id>`."
    return cu.write_approval(roots, action="publish" if public else "other", items=[item], requested_by=requested_by,
                             today=today, why=why, effect=effect, target=f"x: {_target(action)}")


def approved_action(appr: cu.Approval) -> dict[str, Any]:
    items = [i for i in appr.items if i.get("channel") == "x" and str(i.get("id", "")).startswith("x-")]
    if len(items) != 1 or len(appr.items) != 1:
        raise XError("the approval must hold exactly one X item")
    block = appr.blocks.get(str(items[0]["id"]))
    try:
        action = json.loads(str(block).split("\n\n", 1)[1])
    except (IndexError, ValueError, AttributeError):
        raise XError("the item's Exact content is not an X action") from None
    if action.get("action") not in KINDS:
        raise XError("the item's Exact content is not an X action")
    return action


def _paths(approval_path: Path) -> tuple[Path, Path]:
    os_root = Path(approval_path).resolve().parents[2]
    prefix = str(cfg.get("identity.vault_prefix", "") or "").strip("/")
    if os_root == jc.OS_ROOT.resolve():
        return jc.JS_ROOT, os_root
    return (os_root / prefix if prefix else os_root), os_root


def execute(http: Callable, approval_path: Path, *, today: date | None = None,
            now: datetime | None = None) -> dict[str, Any]:
    today = today or jc.today()
    now = now or datetime.now(timezone.utc)
    approval_path = Path(approval_path)
    appr = cu.parse_approval(approval_path)
    problems = cu.approval_problems(appr, today)
    if problems:
        raise XError("; ".join(problems))
    action = approved_action(appr)
    kind, item_id = action["action"], str(appr.items[0]["id"])
    if not appr.decisions.get(item_id):
        raise XError("the item is unticked in the approval")
    js, os_root = _paths(approval_path)
    import outbox                        # a hold (state/outbox-*hold*.md) binds this send path too, as gmail_send
    listed = outbox.holds(js)
    held = [x for x in (item_id, str(appr.meta.get("id") or "")) if x in listed]
    if held:
        raise XError(f"{', '.join(held)} is on hold (listed in state/{listed[held[0]]}); it is never sent")
    receipts = cu.section_text(appr.body, "Receipts") or ""
    done = re.search(rf"x {kind} (\S+) done for {re.escape(item_id)}\b", receipts)
    if done:
        return {"id": done.group(1), "existing": True}
    if re.search(rf"refused by X \(\d+\) for {re.escape(item_id)}\b", receipts):
        raise XError("X refused this action earlier (see Receipts); it is not retried or worked around")
    if re.search(rf"x {kind} started for {re.escape(item_id)}\b", receipts):
        raise XError("this action started and was not confirmed: check X (the post, the DM thread or the follow) "
                     "before anything else; it is never repeated by this script")
    day = _day(now)
    actions_log = _ledger(js, "actions.jsonl")
    used = sum(1 for r in (jc.read_jsonl(actions_log) if actions_log.is_file() else [])
               if r.get("day") == day and r.get("action") == kind)
    if used >= CAPS[kind]:
        raise XError(f"daily cap reached: {used} {kind}(s) today, cap {CAPS[kind]}; try tomorrow")
    token = _load("write")                                   # the write token only; fails before anything else
    client = Client("write", http=http, now=now, js=js, os_root=os_root)
    me = str(token.get("user_id") or "")
    path, body = {"follow": (f"/users/{me}/following", {"target_user_id": action["user_id"]}),
                  "dm": (f"/dm_conversations/with/{action['user_id']}/messages", {"text": action["text"]}),
                  "reply": ("/tweets", {"text": action["text"], "reply": {"in_reply_to_tweet_id": action["in_reply_to"]}}),
                  "post": ("/tweets", {"text": action["text"]})}[kind]
    stamp = jc.run_stamp(today)
    cost = _cost_of(action)
    cu.append_receipt(approval_path, f"{stamp} x {kind} started for {item_id}")
    status, resp = client.call("POST", path, endpoint=kind, worst=cost, actual=lambda b: cost, json_body=body)
    if not 200 <= status < 300:
        cu.append_receipt(approval_path, f"{stamp} x {kind} refused by X ({status}) for {item_id}: {_detail(resp)}")
        raise XError(f"X refused the {kind} ({status}): {_detail(resp)}; not retried")
    data = resp.get("data") or {}
    rid = str(data.get("id") or data.get("dm_event_id") or (f"@{action['handle']}" if kind == "follow" else "ok"))
    cu.append_receipt(approval_path, f"{stamp} x {kind} {rid} done for {item_id} ({_target(action)})")
    jc.append_jsonl(actions_log, {"at": _stamp(now), "day": day, "action": kind, "id": rid, "item": item_id,
                                  "approval": appr.meta.get("id")})
    if action.get("prospect") and kind in ("dm", "reply"):
        jc.log_touch({"at": _stamp(now), "prospect": action["prospect"], "channel": f"x-{kind}", "direction": "out",
                      "kind": "message" if kind == "dm" else "reply", "status": "sent", "x_id": rid,
                      "source": "x_api", "approval_id": appr.meta.get("id")}, root=js)
    return {"id": rid, "existing": False}


# --------------------------------------------------------------------------- CLI
def _status(js: Path, os_root: Path, now: datetime) -> dict[str, Any]:
    try:
        caps = budget_caps(os_root)
    except XError as exc:
        caps = {"error": str(exc)}
    used = spent(js, now)
    log = _ledger(js, "actions.jsonl")
    rows = list(jc.read_jsonl(log)) if log.is_file() else []
    return {"tokens": {k: token_status(k, now) for k in SCOPES}, "client_id_file": client_path().is_file(),
            "budget": {**caps, "used_today": round(used["today"], 4), "used_month": round(used["month"], 4)},
            "credits": credit_state(js),
            "caps": {k: {"cap": CAPS[k], "used_today": sum(1 for r in rows if r.get("day") == _day(now)
                                                           and r.get("action") == k)} for k in KINDS}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="job-search root (default: this department)")
    common.add_argument("--os-root", help="runtime root (default: derived)")
    common.add_argument("--today")
    lg = sub.add_parser("login", parents=[common])
    lg.add_argument("--write", action="store_true", help="the write token (follow, DM, reply, post)")
    sub.add_parser("status", parents=[common])
    fo = sub.add_parser("following", parents=[common])
    fo.add_argument("--max", type=int, default=100, help="accounts to read (1 to 1000; priced per account)")
    p = sub.add_parser("post", parents=[common])
    p.add_argument("ref")
    u = sub.add_parser("user", parents=[common])
    u.add_argument("handle")
    s = sub.add_parser("search", parents=[common])
    s.add_argument("query")
    s.add_argument("--max", type=int, default=10)
    w = sub.add_parser("watch", parents=[common])
    w.add_argument("--config", default="targets/x-watch.yaml")
    w.add_argument("--dry-run", action="store_true", help="list the searches and their worst-case cost; no call")
    for name in ("follow", "dm", "reply", "post-new"):
        a = sub.add_parser(name, parents=[common])
        a.add_argument("--handle")
        a.add_argument("--user-id")
        a.add_argument("--post")
        a.add_argument("--text-file")
        a.add_argument("--text")
        a.add_argument("--prospect")
        a.add_argument("--request", action="store_true", help="file the approval")
    for name in ("show", "execute"):
        a = sub.add_parser(name, parents=[common])
        a.add_argument("--approval", required=True, help="path or A-id in work/approvals/")
    args = ap.parse_args(argv)
    js = Path(args.root) if args.root else jc.JS_ROOT
    os_root = Path(args.os_root) if args.os_root else (jc.os_root_for(js) if args.root else jc.OS_ROOT)
    roots = cu.Roots(js=js, os=os_root)
    today = jc.parse_date(args.today) if args.today else jc.today()
    now = datetime.now(timezone.utc)

    def out(obj: Any) -> None:
        print(json.dumps(obj, indent=2, ensure_ascii=False))

    try:
        if args.cmd == "login":
            out(login("write" if args.write else "read"))
            return 0
        if args.cmd == "status":
            out(_status(js, os_root, now))
            return 0
        if args.cmd == "watch" and args.dry_run:
            cfg = Path(args.config) if Path(args.config).is_absolute() else js / args.config
            plan = watch_plan(js, cfg)
            out({"note": "dry run: no call made, no token read", "queries": len(plan["runs"]),
                 "searches": [q for q, _ in plan["runs"]], "max_results": plan["max_results"],
                 "worst_usd_per_run": round(plan["worst_usd"], 4), "budget": budget_caps(os_root)})
            return 0
        if args.cmd == "following":
            out(following(Client("read", now=now, js=js, os_root=os_root), args.max))
            return 0
        if args.cmd in ("post", "user", "search", "watch"):
            client = Client("read", now=now, js=js, os_root=os_root)
            if args.cmd == "post":
                out(read_post(client, args.ref))
            elif args.cmd == "user":
                out(read_user(client, args.handle))
            elif args.cmd == "search":
                out(search(client, args.query, args.max))
            else:
                cfg = Path(args.config) if Path(args.config).is_absolute() else js / args.config
                out(watch(client, cfg, today=today))
            return 0
        if args.cmd in ("follow", "dm", "reply", "post-new"):
            kind = "post" if args.cmd == "post-new" else args.cmd
            text = Path(args.text_file).read_text(encoding="utf-8") if args.text_file else args.text
            uid = args.user_id
            if kind in ("follow", "dm") and args.handle and not uid:
                uid = read_user(Client("read", now=now, js=js, os_root=os_root), args.handle)["id"]
            action = build_action(kind, text=text, post=args.post, handle=args.handle, user_id=uid,
                                  prospect=args.prospect)
            print(render(action))
            if args.request:
                aid, path = request(roots, action, today=today)
                print(json.dumps({"approval": aid, "path": str(path)}), file=sys.stderr)
            return 0
        path = chd._approval_path(roots, args.approval)
        appr = cu.parse_approval(path)
        if args.cmd == "show":
            print(render(approved_action(appr)))
            return 0
        problems = cu.approval_problems(appr, today)
        if problems:                                   # before any token is loaded
            raise XError("; ".join(problems))
        approved_action(appr)
        out(execute(_http, path, today=today))
        return 0
    except (XError, chd.HoldError) as exc:
        out({"error": str(exc)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
