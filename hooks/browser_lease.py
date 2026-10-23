#!/usr/bin/env python3
"""
browser_lease.py - each lane drives its own Chrome window; text never lands in another lane's tab.

Why: with two lanes in ONE Chrome window, Claude in Chrome's `computer` keystrokes go to the window's ACTIVE
tab, not to the lane's own tab. So parallel lanes each get their own Chrome window (or their own Chrome
profile), identified and attached to that lane.

  Focus rule (always): a session must bring its own tab forward (`computer` screenshot with its own
    tabId) before its first click, type or fill, and again after any other session touched Chrome.
  Mode (<state dir>/browser-mode, one word; windows mode asks for the focus once per turn):
    shared  (default) one keyboard per browser: while another session holds that browser's lease
            (used within the TTL), a session's input calls are denied; wait and retry.
    windows each lane works in its own window, so sessions never deny each other; only the focus
            rule applies. Switch to it only after a two-window test passed (text landed in the
            right tab with both lanes typing).
  Binding: a lane row in hub/programme.json may carry "browser": "<deviceId>" (a separate Chrome
    profile). That lane must select_browser that deviceId first and may not select another.
  Reads (tabs_context_mcp, read_page, get_page_text, find, console, network, list_connected_browsers,
    shortcuts_list) are always open. Stop releases what the session holds.

State: <state dir>/browser-lease.json, flock-guarded. The state dir is OS_BROWSER_LEASE_DIR, else config
policy.browser_lease.state_dir, else ~/.claude/browser-lease.
Log: browser-lease.log beside it. TTL 90 s idle (OS_BROWSER_LEASE_TTL). Fail-open on any error.
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from agency_paths import DATA_ROOT as WORKSPACE  # noqa: E402
import agency_config as cfg  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lane_common import in_agency_session  # noqa: E402
STATE_DIR = Path(os.environ.get("OS_BROWSER_LEASE_DIR")
                 or os.path.expanduser(str(cfg.get("policy.browser_lease.state_dir", "~/.claude/browser-lease"))))
LEASE = STATE_DIR / "browser-lease.json"
LOCK = STATE_DIR / "browser-lease.lock"
LOG = STATE_DIR / "browser-lease.log"
MODE = STATE_DIR / "browser-mode"

PREFIX = "mcp__claude-in-chrome__"
READS = {"tabs_context_mcp", "read_page", "get_page_text", "find", "read_console_messages",
         "read_network_requests", "list_connected_browsers", "shortcuts_list"}
FOCUS_ACTIONS = {"screenshot", "zoom"}
KEYSTROKES = {"type", "key"}   # these go to the focused tab of the window, not to a tab id


def ttl() -> float:
    try:
        return float(os.environ.get("OS_BROWSER_LEASE_TTL", "90"))
    except ValueError:
        return 90.0


def mode() -> str:
    try:
        m = MODE.read_text(encoding="utf-8").strip().lower()
        return m if m in {"shared", "windows"} else "shared"
    except Exception:
        return "shared"


def lane_of(session_id: str) -> tuple[str, str | None]:
    """(label, bound browser deviceId or None) for this session."""
    sid = session_id.lower()
    declared = os.environ.get("OS_LANE", "").strip()
    try:
        for prog in WORKSPACE.glob("hub/programme.json"):
            data = json.loads(prog.read_text(encoding="utf-8"))
            for row in data.get("lanes", []) or []:
                scope = str(row.get("scope") or "")
                ids = {str(s).strip().lower().removeprefix("local_") for s in row.get("session_ids") or []}
                if (declared and scope.lower() == declared.lower()) or sid in ids:
                    return scope or sid[:8], (str(row["browser"]) if row.get("browser") else None)
    except Exception:
        pass
    return declared or f"session {sid[:8]}", None


def log(line: str) -> None:
    try:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {line}\n")
    except Exception:
        pass


def deny(reason: str) -> int:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    return 0


def load() -> dict:
    try:
        data = json.loads(LEASE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and "browsers" in data else {}
    except Exception:
        return {}


def save(state: dict) -> None:
    tmp = LEASE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    tmp.replace(LEASE)


FOCUS_MSG = ("browser_lease: bring your own tab forward first. Keystrokes and clicks go to the tab in front, "
             "and another session has used Chrome since you last did. Call mcp__claude-in-chrome__computer with "
             "action \"screenshot\" and YOUR OWN tabId, check it is your page, then retry this call.")


def pre_tool_use(payload: dict, sid: str) -> int:
    tool = str(payload.get("tool_name") or "")
    if not tool.startswith(PREFIX):
        return 0
    name = tool[len(PREFIX):]
    if name in READS:
        return 0
    ti = payload.get("tool_input") or {}
    action = str(ti.get("action") or "")
    who, bound = lane_of(sid)
    db = load()
    browsers = db.setdefault("browsers", {})
    sessions = db.setdefault("sessions", {})
    focus = db.setdefault("focus", {})   # sid -> {"device", "tab", "seq"}; seq = browser touch count at focus

    if name == "select_browser":
        dev = str(ti.get("deviceId") or "")
        if bound and dev != bound:
            log(f"DENY-WRONG-BROWSER {who} select {dev} bound {bound}")
            return deny(f"browser_lease: {who} is attached to browser {bound} (hub/programme.json). "
                        f"Call select_browser with deviceId {bound}; never another lane's Chrome.")
        sessions[sid] = dev or "default"
        save(db)
        log(f"SELECT {who} -> {sessions[sid]}")
        return 0
    if name == "switch_browser" and bound:
        return deny(f"browser_lease: {who} is attached to browser {bound}: call select_browser with that deviceId.")

    device = sessions.get(sid) or "default"
    if bound and device != bound:
        log(f"DENY-NOT-ATTACHED {who} {name}/{action}")
        return deny(f"browser_lease: {who} drives its own Chrome. First call "
                    f"mcp__claude-in-chrome__select_browser with deviceId {bound}, then retry.")

    now = time.time()
    b = browsers.setdefault(device, {"holder": None, "label": None, "last_used": 0, "seq": 0, "last_sid": None})
    if mode() == "shared" and b.get("holder") and b["holder"] != sid and now - float(b.get("last_used") or 0) < ttl():
        other = b.get("label") or b["holder"][:8]
        wait = int(ttl() - (now - float(b.get("last_used") or 0))) + 1
        log(f"DENY {who} {name}/{action} on {device} held-by {other}")
        return deny(f"browser_lease: this Chrome ({device}) is in use by {other}: one keyboard per Chrome window "
                    f"until each lane has its own window. Wait about {wait} s and retry this same call; "
                    f"reads stay open.")

    mine = focus.get(sid) or {}
    # shared: another session acting since you focused may have changed the tab in front.
    # windows: each lane has its own window; focusing once per turn is enough for clicks, but KEYSTROKES
    # (type, key) go to the window's focused tab, so they need a fresh focus when another session touched
    # Chrome since this one focused ("dirty"), or when they target a tab other than the focused one
    # (otherwise typed text lands in whichever tab happens to be in front).
    keystroke = name == "computer" and action in KEYSTROKES
    tab = ti.get("tabId")
    if mode() == "shared":
        stale = b.get("last_sid") not in (None, sid)
    else:
        stale = keystroke and (bool(mine.get("dirty")) or (tab is not None and mine.get("tab") not in (None, tab)))
    fresh = mine.get("device") == device and not stale
    focusing = name == "computer" and action in FOCUS_ACTIONS and tab is not None
    if not fresh and not focusing:
        if b.get("holder") != sid:
            b.update(holder=sid, label=who, last_used=now)
        save(db)
        log(f"DENY-UNFOCUSED {who} {name}/{action}")
        return deny(FOCUS_MSG)
    if focusing and (not fresh or mine.get("tab") != tab or mine.get("dirty")):
        focus[sid] = {"device": device, "tab": tab, "dirty": False}
        log(f"FOCUS {who} on {device} tab {tab}")
    for other, f in focus.items():                    # every other lane on this Chrome must refocus to type
        if other != sid and isinstance(f, dict) and f.get("device") == device:
            f["dirty"] = True
    b.update(holder=sid, label=who, last_used=now, last_sid=sid, seq=int(b.get("seq") or 0) + 1)
    save(db)
    return 0


def stop(sid: str) -> int:
    db = load()
    changed = bool((db.get("focus") or {}).pop(sid, None))
    for device, b in (db.get("browsers") or {}).items():
        if b.get("holder") == sid:
            log(f"RELEASE {b.get('label')} on {device} (turn ended)")
            b.update(holder=None, label=None, last_used=0)
            changed = True
    if changed:
        save(db)
    return 0


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    sid = str(payload.get("session_id") or "").strip().lower().removeprefix("local_")
    if not sid:
        return 0
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with LOCK.open("a") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            if payload.get("hook_event_name") == "Stop":
                return stop(sid)
            if not in_agency_session(Path(str(payload.get("cwd") or os.getcwd())), payload):
                return 0        # a user-scope plugin fires in every folder: another project's Chrome use is not ours
            return pre_tool_use(payload, sid)
    except Exception as exc:  # fail-open: a broken lease must never stop work
        log(f"ERROR {type(exc).__name__}: {exc}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
