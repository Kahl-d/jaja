"""posts.py ingest reads the posts lane's sweep file (state/posts/sweep-<date>.jsonl, the format of
scripts/posts_sweep_check.py): its hiring_post rows become post records like the LinkedIn and X pass files, so a
LinkedIn lead the lane read in the owner's browser is parsed, deduped and made. Synthetic rows; no network."""
from __future__ import annotations

import hashlib
import json

from comms_helpers import offline  # noqa: F401  (autouse: no network; puts scripts/ on sys.path)
import test_posts
from test_posts import JO_LI, JO_X, TODAY

import posts

js = test_posts.js                 # the department fixture, shared with test_posts.py


def sweep_row(**kw) -> dict:
    url = kw.pop("url", JO_LI["url"])
    row = {"platform": "linkedin", "kind": "hiring_post", "url": url, "author": "Jo Park",
           "author_title": "Co-founder and CTO at Tiny AI", "author_url": "https://www.linkedin.com/in/jo-park-tiny/",
           "company": "Tiny AI", "posted_at": "2026-10-02T09:00:00-07:00", "seen_at": "2026-10-05T16:10:00Z",
           "text": JO_X["text"], "role_hint": "Founding AI Engineer", "location_hint": "San Francisco",
           "query": "li-04", "lead_id": hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]}
    row.update(kw)
    return row


def write(js, name: str, rows: list[dict]) -> None:
    (js / "state" / "posts" / name).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_sweep_rows_become_posts_and_people_rows_are_skipped(js):
    x_url = JO_X["url"]
    write(js, "sweep-2026-10-05.jsonl", [
        sweep_row(),
        sweep_row(platform="x", url=x_url, author_url="https://x.com/founder_jo", author_title="Co-founder & CTO @tinyai",
                  posted_at=None),
        sweep_row(kind="person", url="https://www.linkedin.com/in/sam-lee-giant/", text="")])
    assert ("sweep", js / "state" / "posts" / "sweep-2026-10-05.jsonl") in posts.input_files(js)
    rows, problems = posts.read_rows("sweep", js / "state" / "posts" / "sweep-2026-10-05.jsonl")
    assert problems == []
    li, x = rows
    assert li["platform"] == "linkedin" and li["key"] == posts.post_key("linkedin", JO_LI["url"])
    assert li["author"]["name"] == "Jo Park" and li["author"]["url"] == "https://www.linkedin.com/in/jo-park-tiny"
    assert li["date"] == "2026-10-02" and li["why"] == "sweep query: li-04"
    assert x["platform"] == "x" and x["author"]["handle"] == "founder_jo" and x["date"] == "2026-10-05"   # seen_at day


def test_ingest_reads_the_sweep_file_and_dedupes_against_the_pass_file(js):
    write(js, "linkedin-2026-10-05.jsonl", [JO_LI])                   # the same post in the old pass file
    write(js, "sweep-2026-10-05.jsonl", [sweep_row()])
    rep = posts.ingest(js, today=TODAY, write=False)
    assert rep["read"] == 2 and rep["new"] == 1 and rep["duplicates"] == 1 and rep["hiring"] == 1


def test_a_bad_sweep_row_is_reported_with_its_line(js):
    write(js, "sweep-2026-10-05.jsonl", [sweep_row(url="https://www.linkedin.com/in/not-a-post/")])
    rows, problems = posts.read_rows("sweep", js / "state" / "posts" / "sweep-2026-10-05.jsonl")
    assert rows == [] and problems and problems[0].startswith("sweep-2026-10-05.jsonl line 1:")
    assert posts.main(["check", "--root", str(js), "--file", str(js / "state" / "posts" / "sweep-2026-10-05.jsonl")]) != 0
