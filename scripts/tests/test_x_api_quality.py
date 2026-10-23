"""x_api.py watch quality (2026-10-05): the searches ask for first-person hiring words on US roles and leave out the
accounts on targets/x-deny.yaml (`-from:`), within X's 512 characters; the people query runs first; since_id stays keyed
by the query without its exclusions; each candidate row carries the author's post rate (posts.py reads a feed as a bot).
The real targets/x-watch.yaml is read with no call; every other call goes to FakeX. No spend, no network."""
from __future__ import annotations

import json
import re
from datetime import timedelta

from comms_helpers import offline  # noqa: F401  (autouse: no network; puts scripts/ on sys.path)
import js_common as jc
import posts
import x_api as xa
from test_x_api import NOW, SAM, TODAY, FakeX, _post_payload, _save, env  # noqa: F401
from test_x_api_posts import _snowflake

DENY = ("accounts:\n  - {x_handle: wic_jobs, kind: aggregator, why: test}\n"
        "  - {x_handle: marketspulse24h, kind: news, why: test}\nallow: []\n")


def test_the_real_watch_plan_is_people_first_first_person_us_and_skips_the_deny_list():
    plan = xa.watch_plan(jc.JS_ROOT, jc.JS_ROOT / "targets" / "x-watch.yaml")
    runs = [q for q, _ in plan["runs"]]
    assert len(runs) <= 3 and plan["worst_usd"] <= 0.45 + 1e-9
    assert plan["runs"][0][1] == "people on the watch list" and "from:" in runs[0]
    deny = posts.load_deny(jc.JS_ROOT)["accounts"]
    for q in runs[1:]:
        assert len(q) <= xa.QUERY_LIMIT, len(q)
        assert "\"we're hiring\"" in q and not re.search(r"\(hiring OR", q)        # never a bare `hiring`
        assert all(op in q for op in ("-is:retweet", "-is:quote", "-is:reply", "lang:en"))
        assert all(f"-{w}" in q for w in ("India", "Paris", "London", "Europe", "\"work permit\""))
        assert all(f"-from:{h}" in q for h in deny), [h for h in deny if f"-from:{h}" not in q]


def test_a_watch_run_reads_the_people_first_and_excludes_denied_accounts(env):
    _save(env, "read")
    (env["js"] / "targets").mkdir()
    (env["js"] / "targets" / "x-deny.yaml").write_text(DENY)
    cfg = env["js"] / "x-watch.yaml"
    cfg.write_text("queries:\n  - {q: '\"we''re hiring\" agents', why: agents}\npeople: [sam_rivera]\nmax_results: 10\n")
    fake = FakeX({("GET", "/2/tweets/search/recent"): (200, {"data": [], "meta": {"result_count": 0}})})
    xa.watch(xa.Client("read", http=fake, now=NOW, js=env["js"], os_root=env["os"]), cfg, today=TODAY)
    queries = [c["params"]["query"] for c in fake.calls]
    assert queries[0] == "(from:sam_rivera) -is:retweet"
    assert queries[1] == "\"we're hiring\" agents -from:wic_jobs -from:marketspulse24h"


def test_since_id_is_kept_by_the_query_without_its_deny_exclusions():
    assert xa.since_keys("\"we're hiring\" agents -from:wic_jobs -from:marketspulse24h") == ["\"we're hiring\" agents"]
    assert xa.since_keys("(from:sam_rivera OR from:Tankotz) -is:retweet") == ["from:sam_rivera", "from:tankotz"]
    assert xa.exclude("q", ["a" * 15], limit=10) == "q"                              # never past the limit


def test_a_candidate_row_carries_the_post_rate_and_a_feed_reads_as_a_bot(env):
    _save(env, "read")
    cfg = env["js"] / "x-watch.yaml"
    cfg.write_text("queries:\n  - {q: 'hiring agents', why: agents}\nmax_results: 10\n")
    feed = dict(SAM, public_metrics={"tweet_count": 400000}, created_at="2024-10-05T00:00:00.000Z")
    pid = _snowflake(NOW - timedelta(hours=2))
    post = dict(_post_payload(pid=pid)["data"])
    fake = FakeX({("GET", "/2/tweets/search/recent"): (200, {"data": [post], "includes": {"users": [feed]},
                                                             "meta": {"result_count": 1}})})
    xa.watch(xa.Client("read", http=fake, now=NOW, js=env["js"], os_root=env["os"]), cfg, today=TODAY)
    assert "public_metrics" in fake.calls[0]["params"]["user.fields"]
    row = json.loads((env["js"] / "state" / "posts" / f"x-{TODAY.isoformat()}.jsonl").read_text().splitlines()[0])
    assert row["author_posts_per_day"] > posts.BOT_POSTS_PER_DAY
    author = posts.normalize_row(row, "x")["author"]
    assert posts.poster_kind(author, row["text"])[0] == "bot"
