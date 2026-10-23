"""x_api.py: X (Twitter) through the official API Every call goes to FakeX; nothing touches the network,
nothing is posted, followed or sent."""
from __future__ import annotations

import json
import stat
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from comms_helpers import offline  # noqa: F401  (autouse: no network; puts scripts/ on sys.path)
import js_comms_util as cu
import x_api as xa

TODAY = date(2026, 9, 29)
NOW = datetime(2026, 9, 29, 23, 0, tzinfo=timezone.utc)
SAM_POST = "2104966899176235272"
SAM = {"id": "11111", "username": "sam_rivera", "name": "Sam Rivera", "description": "Platform engineer at Initech"}
ME = {"id": "99999", "username": "alexexample", "name": "Alex"}


class FakeX:
    """Routes (method, path) to canned answers; records every call with its headers and body."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.calls: list[dict] = []

    def __call__(self, method, url, *, headers=None, params=None, json_body=None, form=None):
        path = urlsplit(url).path
        self.calls.append({"method": method, "path": path, "headers": headers or {}, "params": params or {},
                           "json": json_body, "form": form})
        key = (method, path)
        if key not in self.routes:
            raise AssertionError(f"unexpected {method} {path}")
        value = self.routes[key]
        return value(self.calls[-1]) if callable(value) else value

    def paths(self):
        return [(c["method"], c["path"]) for c in self.calls]


def _token_answer(scope):
    return (200, {"token_type": "bearer", "access_token": "AT-FAKE", "refresh_token": "RT-FAKE", "expires_in": 7200,
                  "scope": scope})


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = tmp_path / "home" / ".config" / "workspace"
    monkeypatch.setenv("JOB_AGENCY_CONFIG_DIR", str(cfg))
    os_root = tmp_path / "os"
    js = os_root / "domains" / "career" / "projects" / "job-search"
    js.mkdir(parents=True)
    (os_root / "company" / "policies").mkdir(parents=True)
    (os_root / "company" / "policies" / "budgets.yaml").write_text(
        "connectors:\n  x: {usd_per_month: 25, usd_per_day: 3}\n")
    return {"cfg": cfg, "os": os_root, "js": js, "roots": cu.Roots(js=js, os=os_root)}


def _client_file(env):
    env["cfg"].mkdir(parents=True, exist_ok=True)
    (env["cfg"] / "x_client.json").write_text(json.dumps({"client_id": "CLIENT-ID-FAKE"}))


def _save(env, kind, scopes=None, expires=NOW + timedelta(hours=1)):
    scopes = scopes or list(xa.SCOPES[kind])
    xa.save_token(kind, {"access_token": "AT-" + kind, "refresh_token": "RT-" + kind, "scopes": scopes,
                         "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"), "user_id": ME["id"],
                         "username": ME["username"], "authorized_at": "2026-09-29T22:00:00Z"})


def _post_payload(pid=SAM_POST, text="We're hiring engineers for Claude Platform: agents, evals, infra. DM me.",
                  replies=41, settings="everyone"):
    return {"data": {"id": pid, "text": text, "author_id": SAM["id"], "created_at": "2026-09-29T16:09:00.000Z",
                     "conversation_id": pid, "reply_settings": settings,
                     "public_metrics": {"reply_count": replies, "retweet_count": 12, "like_count": 300,
                                        "quote_count": 4, "impression_count": 90000}},
            "includes": {"users": [SAM]}}


# --------------------------------------------------------------------------- login and tokens
def test_scopes_are_split_and_the_read_token_cannot_write():
    assert set(xa.SCOPES["read"]) == {"tweet.read", "users.read", "offline.access"}
    assert {"tweet.write", "follows.write", "dm.write"} <= set(xa.SCOPES["write"])
    assert not any(s.endswith(".write") for s in xa.SCOPES["read"])


def test_login_runs_pkce_on_the_loopback_callback_and_saves_a_private_token(env):
    _client_file(env)
    opened = []

    def wait(state):
        return "CODE-1", state
    fake = FakeX({("POST", "/2/oauth2/token"): _token_answer("tweet.read users.read offline.access"),
                  ("GET", "/2/users/me"): (200, {"data": ME})})
    st = xa.login("read", http=fake, open_url=opened.append, wait_for_code=wait, now=NOW)
    q = parse_qs(urlsplit(opened[0]).query)
    assert opened[0].startswith(xa.AUTHORIZE_URL)
    assert q["client_id"] == ["CLIENT-ID-FAKE"] and q["redirect_uri"] == [xa.CALLBACK]
    assert q["code_challenge_method"] == ["S256"] and q["scope"] == [" ".join(xa.SCOPES["read"])]
    form = fake.calls[0]["form"]
    assert form["grant_type"] == "authorization_code" and form["code"] == "CODE-1" and len(form["code_verifier"]) >= 43
    path = xa.token_path("read")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert st["username"] == "alexexample" and st["has_refresh_token"] and "AT-FAKE" not in json.dumps(st)


def test_login_refuses_a_wrong_state_and_a_read_login_granted_write_scopes(env):
    _client_file(env)
    fake = FakeX({("POST", "/2/oauth2/token"): _token_answer("tweet.read users.read offline.access tweet.write"),
                  ("GET", "/2/users/me"): (200, {"data": ME})})
    with pytest.raises(xa.XError, match="state"):
        xa.login("read", http=fake, open_url=lambda u: None, wait_for_code=lambda s: ("C", "other"), now=NOW)
    with pytest.raises(xa.XError, match="write scopes"):
        xa.login("read", http=fake, open_url=lambda u: None, wait_for_code=lambda s: ("C", s), now=NOW)
    assert not xa.token_path("read").exists()


def test_login_needs_the_client_id_file(env):
    with pytest.raises(xa.XError, match="x_client.json"):
        xa.login("read", http=FakeX(), open_url=lambda u: None, wait_for_code=lambda s: ("C", s), now=NOW)


def test_an_expired_access_token_is_refreshed_and_the_rotated_token_saved(env):
    _client_file(env)
    _save(env, "read", expires=NOW - timedelta(minutes=5))
    fake = FakeX({("POST", "/2/oauth2/token"): (200, {"access_token": "AT-NEW", "refresh_token": "RT-NEW",
                                                     "expires_in": 7200, "scope": "tweet.read users.read offline.access"})})
    assert xa.access_token("read", http=fake, now=NOW) == "AT-NEW"
    assert fake.calls[0]["form"]["grant_type"] == "refresh_token" and fake.calls[0]["form"]["refresh_token"] == "RT-read"
    saved = json.loads(xa.token_path("read").read_text())
    assert saved["refresh_token"] == "RT-NEW" and saved["expires_at"] == "2026-09-30T01:00:00Z"


def test_a_missing_token_says_how_to_log_in_and_never_falls_back(env):
    _save(env, "write")
    with pytest.raises(xa.XError, match="x_api.py login"):
        xa.access_token("read", http=FakeX(), now=NOW)


# --------------------------------------------------------------------------- reads, budget
def test_read_post_returns_counts_reply_settings_and_the_text_as_untrusted(env):
    _save(env, "read")
    fake = FakeX({("GET", f"/2/tweets/{SAM_POST}"): (200, _post_payload())})
    c = xa.Client("read", http=fake, now=NOW, js=env["js"], os_root=env["os"])
    post = xa.read_post(c, f"https://x.com/sam_rivera/status/{SAM_POST}?s=20")
    assert post["id"] == SAM_POST and post["author"] == "@sam_rivera" and post["reply_count"] == 41
    assert post["reply_settings"] == "everyone" and post["created_at"] == "2026-09-29T16:09:00.000Z"
    assert post["text"].startswith("<untrusted_x>") and post["url"] == f"https://x.com/sam_rivera/status/{SAM_POST}"
    call = fake.calls[0]
    assert call["headers"]["Authorization"] == "Bearer AT-read"
    assert "public_metrics" in call["params"]["tweet.fields"] and call["params"]["expansions"] == "author_id"
    [row] = [json.loads(line) for line in (env["js"] / "state" / "x" / "usage.jsonl").read_text().splitlines()]
    assert row["endpoint"] == "post" and row["usd"] == pytest.approx(0.015) and "AT-" not in json.dumps(row)


def test_search_prices_the_worst_case_first_and_refuses_over_the_daily_budget(env):
    _save(env, "read")
    (env["js"] / "state" / "x").mkdir(parents=True)
    (env["js"] / "state" / "x" / "usage.jsonl").write_text(
        json.dumps({"at": "2026-09-29T20:00:00Z", "day": "2026-09-29", "endpoint": "search", "usd": 2.9}) + "\n")
    fake = FakeX()
    c = xa.Client("read", http=fake, now=NOW, js=env["js"], os_root=env["os"])
    with pytest.raises(xa.XError, match="budget"):
        xa.search(c, "hiring agents", max_results=50)
    assert fake.calls == []


def test_a_missing_budget_line_refuses(env):
    (env["os"] / "company" / "policies" / "budgets.yaml").write_text("connectors: {}\n")
    _save(env, "read")
    c = xa.Client("read", http=FakeX(), now=NOW, js=env["js"], os_root=env["os"])
    with pytest.raises(xa.XError, match="budgets.yaml"):
        xa.read_user(c, "sam_rivera")


def test_post_ids_come_from_urls_or_ids_only():
    assert xa.post_id(f"https://x.com/sam_rivera/status/{SAM_POST}/photo/1") == SAM_POST
    assert xa.post_id(f"https://twitter.com/a/status/{SAM_POST}") == SAM_POST and xa.post_id(SAM_POST) == SAM_POST
    with pytest.raises(xa.XError):
        xa.post_id("https://example.com/status/123")


# --------------------------------------------------------------------------- watch
def test_hiring_signal_needs_a_hiring_phrase_and_a_role_word():
    assert xa.hiring_signal("We're hiring engineers to build agents. DM me!")
    assert xa.hiring_signal("My team is looking for an ML infra engineer")
    assert not xa.hiring_signal("Great talk on agents today")
    assert not xa.hiring_signal("We are hiring a head of marketing")


def test_watch_writes_a_review_file_of_hiring_candidates_and_records_nothing(env):
    _save(env, "read")
    cfg = env["js"] / "x-watch.yaml"
    cfg.write_text("queries:\n  - {q: '(hiring OR \"join my team\") (agents OR evals) -is:retweet lang:en', why: agents}\n"
                   "people: [sam_rivera]\nmax_results: 10\n")
    posts = [dict(_post_payload()["data"]),
             dict(_post_payload()["data"], id="2104966899176235999", text="Loved this conference")]
    fake = FakeX({("GET", "/2/tweets/search/recent"): (200, {"data": posts, "includes": {"users": [SAM]},
                                                             "meta": {"result_count": 2}})})
    c = xa.Client("read", http=fake, now=NOW, js=env["js"], os_root=env["os"])
    out = xa.watch(c, cfg, today=TODAY)
    assert [p["id"] for p in out["candidates"]] == [SAM_POST]
    queries = [call["params"]["query"] for call in fake.calls]
    assert any("from:sam_rivera" in q for q in queries) and len(queries) == 2
    review = (env["js"] / "state" / "review" / "x-watch-2026-09-29.md").read_text()
    assert f"https://x.com/sam_rivera/status/{SAM_POST}" in review and "jobs_manual.py add" in review
    assert "<untrusted_x>" in review and not (env["js"] / "jobs").exists()


def test_watch_plan_prices_the_run_without_the_network(env, capsys):
    cfg = env["js"] / "x-watch.yaml"
    cfg.write_text("queries:\n  - {q: 'hiring agents', why: agents}\n  - {q: 'hiring evals', why: evals}\n"
                   "people: [sam_rivera]\nmax_results: 10\n")
    plan = xa.watch_plan(env["js"], cfg)
    assert [q for q, _ in plan["runs"]][1:] == ["hiring agents", "hiring evals"] and len(plan["runs"]) == 3   # people first
    assert plan["worst_usd"] == pytest.approx(3 * 10 * (xa.PRICE["post"] + xa.PRICE["user"]))
    assert xa.main(["watch", "--dry-run", "--root", str(env["js"]), "--os-root", str(env["os"]),
                    "--config", str(cfg)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["queries"] == 3 and out["worst_usd_per_run"] == pytest.approx(0.45)
    assert out["budget"]["usd_per_day"] > 0 and "dry run" in out["note"]
    assert not (env["js"] / "state" / "x").exists() and not (env["js"] / "state" / "review").exists()


# --------------------------------------------------------------------------- writes
def test_actions_hold_the_exact_target_and_text_and_refuse_bad_input():
    reply = xa.build_action("reply", text="Congrats on the move, Sam.", post=SAM_POST, handle="sam_rivera")
    assert reply == {"action": "reply", "text": "Congrats on the move, Sam.", "in_reply_to": SAM_POST,
                     "handle": "sam_rivera", "user_id": None, "prospect": None}
    follow = xa.build_action("follow", handle="@Sam_Rivera", user_id="11111")
    assert follow["handle"] == "sam_rivera" and follow["user_id"] == "11111" and follow["text"] is None
    for kind, kw, match in (("post", {"text": "x" * 281}, "280"), ("dm", {"text": "hi", "handle": "sam_rivera"}, "user id"),
                            ("reply", {"text": "Congrats"}, "post"), ("post", {"text": "Hi \u2014 there"}, "em-dash"),
                            ("follow", {"handle": "not a handle!", "user_id": "1"}, "handle"),
                            ("like", {"handle": "a"}, "follow, dm, reply or post")):
        with pytest.raises(xa.XError, match=match):
            xa.build_action(kind, **kw)


def test_public_text_must_not_reveal_the_search():
    with pytest.raises(xa.XError, match="confidential"):
        xa.build_action("reply", text="I'm open to work and looking for a new role.", post=SAM_POST, handle="sam_rivera")
    assert xa.build_action("dm", text="Hi Sam, I'm looking for my next role on agent tooling.", handle="sam_rivera",
                           user_id="11111")["action"] == "dm"                     # a DM is private


def test_request_files_an_approval_and_show_renders_it(env):
    act = xa.build_action("dm", text="Hi Sam, saw your post about Claude Platform.", handle="sam_rivera", user_id="11111",
                          prospect="sam-rivera")
    aid, path = xa.request(env["roots"], act, today=TODAY)
    appr = cu.parse_approval(path)
    assert aid == "A-2026-0001" and appr.hash_ok and xa.approved_action(appr) == act
    out = xa.render(act)
    for part in ("X DIRECT MESSAGE", "To: @sam_rivera", "Hi Sam, saw your post", "private", "cap 5 a day"):
        assert part in out, part
    assert "public" in xa.render(xa.build_action("reply", text="Congrats, Sam.", post=SAM_POST, handle="sam_rivera"))


def _approved(env, act):
    _, path = xa.request(env["roots"], act, today=TODAY)
    cu.set_frontmatter(path, status="approved", decided_at="2026-09-29", decided_via="chat")
    return path


def test_execute_refuses_unapproved_then_sends_the_approved_dm_once_and_logs_it(env):
    _save(env, "write")
    act = xa.build_action("dm", text="Hi Sam, saw your post about Claude Platform.", handle="sam_rivera", user_id="11111",
                          prospect="sam-rivera")
    _, path = xa.request(env["roots"], act, today=TODAY)
    fake = FakeX({("POST", "/2/dm_conversations/with/11111/messages"): (201, {"data": {"dm_event_id": "DM1",
                                                                                         "dm_conversation_id": "C1"}})})
    with pytest.raises(xa.XError, match="not approved"):
        xa.execute(fake, path, today=TODAY, now=NOW)
    assert fake.calls == []
    cu.set_frontmatter(path, status="approved", decided_at="2026-09-29", decided_via="chat")
    out = xa.execute(fake, path, today=TODAY, now=NOW)
    [call] = fake.calls
    assert call["json"] == {"text": "Hi Sam, saw your post about Claude Platform."}
    assert call["headers"]["Authorization"] == "Bearer AT-write" and out["id"] == "DM1"
    text = path.read_text()
    assert "x dm started for" in text and "x dm DM1 done for" in text
    assert xa.execute(fake, path, today=TODAY, now=NOW)["existing"] is True and len(fake.calls) == 1
    touches = [json.loads(line) for line in next((env["js"] / "touches").glob("*.jsonl")).read_text().splitlines()]
    assert touches[0]["prospect"] == "sam-rivera" and touches[0]["channel"] == "x-dm"


def test_execute_calls_the_right_endpoint_for_each_action(env):
    _save(env, "write")
    fake = FakeX({("POST", "/2/users/99999/following"): (200, {"data": {"following": True}}),
                  ("POST", "/2/tweets"): (201, {"data": {"id": "T1", "text": "x"}})})
    xa.execute(fake, _approved(env, xa.build_action("follow", handle="sam_rivera", user_id="11111")), today=TODAY, now=NOW)
    xa.execute(fake, _approved(env, xa.build_action("reply", text="Congrats, Sam.", post=SAM_POST, handle="sam_rivera")),
               today=TODAY, now=NOW)
    xa.execute(fake, _approved(env, xa.build_action("post", text="Shipping an eval harness this week.")),
               today=TODAY, now=NOW)
    assert fake.calls[0]["json"] == {"target_user_id": "11111"}
    assert fake.calls[1]["json"] == {"text": "Congrats, Sam.", "reply": {"in_reply_to_tweet_id": SAM_POST}}
    assert fake.calls[2]["json"] == {"text": "Shipping an eval harness this week."}


def test_a_closed_dm_inbox_is_reported_and_never_retried(env):
    _save(env, "write")
    path = _approved(env, xa.build_action("dm", text="Hi Sam.", handle="sam_rivera", user_id="11111"))
    fake = FakeX({("POST", "/2/dm_conversations/with/11111/messages"):
                  (403, {"title": "Forbidden", "detail": "You cannot send messages to this user."})})
    with pytest.raises(xa.XError, match="refused"):
        xa.execute(fake, path, today=TODAY, now=NOW)
    assert "refused by X (403)" in path.read_text()
    with pytest.raises(xa.XError, match="refused"):
        xa.execute(fake, path, today=TODAY, now=NOW)
    assert len(fake.calls) == 1


def test_a_started_action_without_its_done_line_is_never_repeated(env):
    _save(env, "write")
    path = _approved(env, xa.build_action("post", text="Shipping an eval harness this week."))
    boom = FakeX({("POST", "/2/tweets"): lambda call: (_ for _ in ()).throw(RuntimeError("network"))})
    with pytest.raises(RuntimeError):
        xa.execute(boom, path, today=TODAY, now=NOW)
    fake = FakeX({("POST", "/2/tweets"): (201, {"data": {"id": "T1"}})})
    with pytest.raises(xa.XError, match="check X"):
        xa.execute(fake, path, today=TODAY, now=NOW)
    assert fake.calls == []


def test_the_daily_cap_stops_the_next_action(env, monkeypatch):
    _save(env, "write")
    monkeypatch.setitem(xa.CAPS, "follow", 1)
    fake = FakeX({("POST", "/2/users/99999/following"): (200, {"data": {"following": True}})})
    xa.execute(fake, _approved(env, xa.build_action("follow", handle="a_one", user_id="1")), today=TODAY, now=NOW)
    with pytest.raises(xa.XError, match="cap"):
        xa.execute(fake, _approved(env, xa.build_action("follow", handle="b_two", user_id="2")), today=TODAY, now=NOW)
    assert len(fake.calls) == 1


def test_execute_never_loads_the_read_token_for_a_write(env):
    _save(env, "read")
    path = _approved(env, xa.build_action("post", text="Shipping an eval harness this week."))
    with pytest.raises(xa.XError, match="login --write"):
        xa.execute(FakeX(), path, today=TODAY, now=NOW)


def test_cli_execute_checks_the_approval_before_any_token(env, capsys, monkeypatch):
    _, path = xa.request(env["roots"], xa.build_action("post", text="Shipping an eval harness this week."), today=TODAY)
    monkeypatch.setattr(xa, "access_token", lambda *a, **k: pytest.fail("token loaded for an unapproved approval"))
    monkeypatch.setattr(xa, "_load", lambda *a, **k: pytest.fail("token file read for an unapproved approval"))
    assert xa.main(["execute", "--approval", str(path), "--root", str(env["js"]), "--os-root", str(env["os"]),
                    "--today", "2026-09-29"]) == 2
    assert "not approved" in capsys.readouterr().out


def test_status_shows_tokens_budget_and_caps_without_secrets(env, capsys):
    _client_file(env)
    _save(env, "read")
    assert xa.main(["status", "--root", str(env["js"]), "--os-root", str(env["os"]), "--today", "2026-09-29"]) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["tokens"]["read"]["exists"] and not data["tokens"]["write"]["exists"]
    assert data["client_id_file"] is True and data["budget"]["usd_per_day"] == 3
    assert "AT-read" not in out and "RT-read" not in out and "CLIENT-ID-FAKE" not in out


def test_an_approved_file_with_the_item_unticked_does_nothing(env):
    _save(env, "write")
    path = _approved(env, xa.build_action("post", text="Shipping an eval harness this week."))
    item = cu.parse_approval(path).items[0]["id"]
    path.write_text(path.read_text().replace(f"- [x] {item}", f"- [ ] {item}"))
    fake = FakeX()
    with pytest.raises(xa.XError, match="unticked"):
        xa.execute(fake, path, today=TODAY, now=NOW)
    assert fake.calls == [] and "started" not in path.read_text()


# X answers 402 when the account's credits are spent; status records the last 402 with its
# date until a successful call clears it (connectors_doctor shows X red meanwhile).
DEPLETED = (402, {"title": "CreditsDepleted", "detail": "Your enrolled account does not have any credits", "type":
                  "https://api.x.com/2/problems/credits"})


def test_a_402_is_logged_as_credits_depleted_and_status_shows_its_date(env, capsys):
    _client_file(env)
    _save(env, "read")
    fake = FakeX({("GET", "/2/tweets/search/recent"): DEPLETED})
    c = xa.Client("read", http=fake, now=NOW, js=env["js"], os_root=env["os"])
    status, _ = c.call("GET", "/tweets/search/recent", endpoint="search", worst=0.05, actual=lambda b: 0.05)
    assert status == 402
    [row] = [json.loads(line) for line in (env["js"] / "state" / "x" / "usage.jsonl").read_text().splitlines()]
    assert row["status"] == 402 and row["usd"] == 0.0 and row["note"] == "credits depleted"
    assert xa.credit_state(env["js"]) == {"state": "depleted", "last_402": {"at": "2026-09-29T23:00:00Z",
                                                                            "endpoint": "search"}, "last_ok": None}
    assert xa.main(["status", "--root", str(env["js"]), "--os-root", str(env["os"]), "--today", "2026-09-29"]) == 0
    assert json.loads(capsys.readouterr().out)["credits"]["state"] == "depleted"


def test_a_successful_call_after_the_402_clears_it_and_keeps_the_record(env):
    (env["js"] / "state" / "x").mkdir(parents=True)
    rows = [{"at": "2026-09-29T19:10:34Z", "day": "2026-09-29", "endpoint": "search", "status": 402, "usd": 0.0},
            {"at": "2026-09-30T08:00:00Z", "day": "2026-09-30", "endpoint": "post", "status": 200, "usd": 0.005}]
    (env["js"] / "state" / "x" / "usage.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert xa.credit_state(env["js"]) == {"state": "ok", "last_402": {"at": "2026-09-29T19:10:34Z",
                                                                      "endpoint": "search"},
                                          "last_ok": "2026-09-30T08:00:00Z"}
    (env["js"] / "state" / "x" / "usage.jsonl").unlink()
    assert xa.credit_state(env["js"]) == {"state": "unknown", "last_402": None, "last_ok": None}


# network_map reads X following from state/x/following.json; this read writes it (budgeted, read token).
def test_k195_following_writes_the_cache_and_prices_each_account(env):
    _save(env, "read")
    users = [{"id": "1", "name": "Kai Lee", "username": "kailee", "description": "Recruiting for agents teams"},
             {"id": "2", "name": "Bo Chen", "username": "bochen", "description": "Founder, Quarry"}]
    fake = FakeX({("GET", f"/2/users/{ME['id']}/following"): (200, {"data": users, "meta": {"result_count": 2}})})
    c = xa.Client("read", http=fake, now=NOW, js=env["js"], os_root=env["os"])
    out = xa.following(c, max_results=50)
    assert out["accounts"] == 2 and fake.calls[0]["params"]["max_results"] == 50
    data = json.loads((env["js"] / "state" / "x" / "following.json").read_text())
    assert [u["username"] for u in data["users"]] == ["kailee", "bochen"] and data["fetched_at"] == "2026-09-29T23:00:00Z"
    [row] = [json.loads(l) for l in (env["js"] / "state" / "x" / "usage.jsonl").read_text().splitlines()]
    assert row["endpoint"] == "following" and row["usd"] == pytest.approx(0.02)


def test_k195_following_at_402_writes_nothing_and_says_credits(env):
    _save(env, "read")
    fake = FakeX({("GET", f"/2/users/{ME['id']}/following"): DEPLETED})
    c = xa.Client("read", http=fake, now=NOW, js=env["js"], os_root=env["os"])
    with pytest.raises(xa.XError):
        xa.following(c)
    assert not (env["js"] / "state" / "x" / "following.json").exists()
    assert xa.credit_state(env["js"])["state"] == "depleted"
