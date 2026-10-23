"""x_api.py watch for the posts pipeline latest posts only (since_id per query), a run that the budget
cannot finish never starts, every hiring candidate lands in state/posts/x-<date>.jsonl for posts.py, and each search row
in the usage ledger counts the posts it returned. Every call goes to FakeX; nothing touches the network."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from comms_helpers import offline  # noqa: F401  (autouse: no network; puts scripts/ on sys.path)
import x_api as xa
from test_x_api import NOW, SAM, SAM_POST, TODAY, FakeX, _post_payload, _save, env  # noqa: F401

LINK = "https://jobs.ashbyhq.com/acme/job-0001"


def _snowflake(at: datetime) -> str:
    return str((int(at.timestamp() * 1000) - xa.SNOWFLAKE_EPOCH_MS) << 22)


def _cfg(env, queries=("hiring agents",)):
    cfg = env["js"] / "x-watch.yaml"
    cfg.write_text("queries:\n" + "".join(f"  - {{q: '{q}', why: test}}\n" for q in queries) + "max_results: 10\n")
    return cfg


def _answer(posts):
    return (200, {"data": posts, "includes": {"users": [SAM]}, "meta": {"result_count": len(posts)}})


def _hiring(pid, **kw):
    p = dict(_post_payload(pid=pid)["data"], **kw)
    p["entities"] = {"urls": [{"url": "https://t.co/abc", "expanded_url": LINK}]}
    return p


def test_each_query_asks_only_for_posts_newer_than_its_last_run(env):
    _save(env, "read")
    cfg = _cfg(env)
    first = _snowflake(NOW - timedelta(hours=5))
    fake = FakeX({("GET", "/2/tweets/search/recent"): _answer([_hiring(first)])})
    c = xa.Client("read", http=fake, now=NOW, js=env["js"], os_root=env["os"])
    xa.watch(c, cfg, today=TODAY)
    assert "since_id" not in fake.calls[0]["params"] and "entities" in fake.calls[0]["params"]["tweet.fields"]
    assert json.loads((env["js"] / "state" / "x" / "since.json").read_text()) == {"hiring agents": first}
    fake2 = FakeX({("GET", "/2/tweets/search/recent"): _answer([])})
    xa.watch(xa.Client("read", http=fake2, now=NOW, js=env["js"], os_root=env["os"]), cfg, today=TODAY)
    assert fake2.calls[0]["params"]["since_id"] == first


def test_a_since_id_older_than_the_search_window_is_dropped(env):
    _save(env, "read")
    cfg = _cfg(env)
    (env["js"] / "state" / "x").mkdir(parents=True)
    old = _snowflake(NOW - timedelta(days=10))
    (env["js"] / "state" / "x" / "since.json").write_text(json.dumps({"hiring agents": old}))
    assert xa.post_age_days(old, NOW) == pytest.approx(10, abs=0.01)
    fake = FakeX({("GET", "/2/tweets/search/recent"): _answer([])})
    xa.watch(xa.Client("read", http=fake, now=NOW, js=env["js"], os_root=env["os"]), cfg, today=TODAY)
    assert "since_id" not in fake.calls[0]["params"]


def test_a_run_the_budget_cannot_finish_never_starts(env):
    _save(env, "read")
    cfg = _cfg(env, ("hiring agents", "hiring evals"))                 # worst case 2 x 10 x $0.015 = $0.30
    (env["js"] / "state" / "x").mkdir(parents=True)
    (env["js"] / "state" / "x" / "usage.jsonl").write_text(
        json.dumps({"at": "2026-09-29T20:00:00Z", "day": "2026-09-29", "endpoint": "search", "usd": 2.75}) + "\n")
    fake = FakeX({("GET", "/2/tweets/search/recent"): _answer([])})
    with pytest.raises(xa.XError, match="no search made"):
        xa.watch(xa.Client("read", http=fake, now=NOW, js=env["js"], os_root=env["os"]), cfg, today=TODAY)
    assert fake.calls == []


def test_hiring_candidates_land_in_the_posts_file_and_reads_are_counted(env):
    _save(env, "read")
    pid = _snowflake(NOW - timedelta(hours=2))
    fake = FakeX({("GET", "/2/tweets/search/recent"): _answer([_hiring(pid), dict(_post_payload(pid="2104966899176235999")[
        "data"], text="Loved this conference")])})
    xa.watch(xa.Client("read", http=fake, now=NOW, js=env["js"], os_root=env["os"]), _cfg(env), today=TODAY)
    rows = [json.loads(x) for x in (env["js"] / "state" / "posts" / "x-2026-09-29.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    r = rows[0]
    assert (r["platform"], r["id"], r["author_handle"], r["author_name"]) == ("x", pid, "sam_rivera", "Sam Rivera")
    assert r["links"] == [LINK] and r["author_bio"] == SAM["description"] and r["date"] == "2026-09-29"
    assert r["text"].startswith("We're hiring engineers") and "<untrusted" not in r["text"]
    usage = [json.loads(x) for x in (env["js"] / "state" / "x" / "usage.jsonl").read_text().splitlines()]
    assert usage[-1]["results"] == 2 and "text" not in usage[-1]


def test_a_post_dated_by_pacific_time():
    assert xa._post_day("2026-09-30T03:00:00.000Z") == "2026-09-29"


def test_looking_for_a_role_counts_as_hiring():
    assert xa.hiring_signal("Looking for a founding engineer to build AI agents with us")
    assert not xa.hiring_signal("Looking for a good coffee spot in SF")


# ----------------------------------------------------------------------------- review fixes
def test_fix1_a_failure_at_query_2_keeps_query_1s_paid_posts_on_disk(env):
    """Each query's candidates, seen and posts rows are on disk before its since_id moves; a later failure loses none."""
    _save(env, "read")
    cfg = _cfg(env, ("hiring agents", "hiring evals"))
    pid = _snowflake(NOW - timedelta(hours=3))

    def route(call):
        if call["params"]["query"] == "hiring agents":
            return _answer([_hiring(pid)])
        return (503, {"title": "Service Unavailable"})
    fake = FakeX({("GET", "/2/tweets/search/recent"): route})
    with pytest.raises(xa.XError, match="503"):
        xa.watch(xa.Client("read", http=fake, now=NOW, js=env["js"], os_root=env["os"]), cfg, today=TODAY)
    rows = [json.loads(x) for x in (env["js"] / "state" / "posts" / "x-2026-09-29.jsonl").read_text().splitlines()]
    assert [r["id"] for r in rows] == [pid]
    assert pid in json.loads((env["js"] / "state" / "x" / "seen.json").read_text())
    assert json.loads((env["js"] / "state" / "x" / "since.json").read_text()) == {"hiring agents": pid}


def test_fix3_adding_a_people_watch_handle_keeps_the_people_query_since(env):
    """since_id for people is kept per handle, so a new handle in the chunk does not re-read (and re-pay) 6 days."""
    _save(env, "read")
    cfg = env["js"] / "x-watch.yaml"
    cfg.write_text("queries: []\npeople: [sam_rivera]\nmax_results: 10\n")
    pid = _snowflake(NOW - timedelta(hours=4))
    fake = FakeX({("GET", "/2/tweets/search/recent"): _answer([_hiring(pid)])})
    xa.watch(xa.Client("read", http=fake, now=NOW, js=env["js"], os_root=env["os"]), cfg, today=TODAY)
    cfg.write_text("queries: []\npeople: [sam_rivera, new_poster]\nmax_results: 10\n")
    fake2 = FakeX({("GET", "/2/tweets/search/recent"): _answer([])})
    xa.watch(xa.Client("read", http=fake2, now=NOW, js=env["js"], os_root=env["os"]), cfg, today=TODAY)
    params = fake2.calls[0]["params"]
    assert "from:new_poster" in params["query"] and params.get("since_id") == pid
