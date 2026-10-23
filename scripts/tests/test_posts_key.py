"""posts.py post key: one LinkedIn post, one record, whatever URL form it is read in.

The same post arrives as /posts/<slug>-share-<id>-<tail>/ (copy link), /feed/update/urn:li:share:<id>/,
/feed/update/urn:li:activity:<id>/ or urn:li:ugcPost:<id>. Keying the copy-link share form by its URL would make a re-read
as /feed/update/urn:li:share:<id>/ miss the record and fall to the cross-platform match (counted as "the same
post on the other platform", with an alias row). So:
1. the key is linkedin:<id> for every form carrying that number (-share-<id> is urn:li:share:<id>);
2. old ledger rows are keyed again on read from their URL (no migration), and their made rows follow;
3. the activity id is a different number from the share id for one post, so those forms are never matched by key: a
   LinkedIn re-read joins its record on the same author_url and the same words, as a duplicate, not a cross-platform one.
Fixtures: fixtures/posts/ledger-2026-10-05.jsonl is a synthetic ledger (31 rows: 15 X records, 13 X made rows, 2 LinkedIn
records keyed by URL, 1 LinkedIn made row); fixtures/posts/linkedin-2026-10-05.jsonl is the pass file those 2 LinkedIn
records came from. Nothing touches the network."""
from __future__ import annotations

import json
import re
import shutil
from datetime import date
from pathlib import Path

import pytest

from comms_helpers import offline  # noqa: F401  (autouse: no network; puts scripts/ on sys.path)
import js_common as jc
import posts

TODAY = date(2026, 10, 6)
FIX = Path(__file__).resolve().parent / "fixtures" / "posts"
LI_ROWS = [json.loads(line) for line in (FIX / "linkedin-2026-10-05.jsonl").read_text(encoding="utf-8").splitlines() if line]
PIA = next(r for r in LI_ROWS if r["author_name"].startswith("Priya"))
SHARE_ID = re.compile(r"-share-(\d+)-")


def _share_form(row):
    """The row as read from the feed: /feed/update/urn:li:share:<the number in its copy link>/."""
    return dict(row, url=f"https://www.linkedin.com/feed/update/urn:li:share:{SHARE_ID.search(row['url']).group(1)}/")


@pytest.fixture
def js(tmp_path):
    root = tmp_path / "os" / "area" / "group" / "projects" / "job-search"
    for d in ("targets", "prospects", "jobs", "state/posts", "state/x", "outbox", "touches", "profile"):
        (root / d).mkdir(parents=True)
    shutil.copy(FIX / "x-deny.yaml", root / "targets" / "x-deny.yaml")
    (root / "targets" / "platforms.yaml").write_text("platforms:\n  - {id: linkedin, name: LinkedIn, alert_senders: []}\n")
    (root / "targets" / "companies.yaml").write_text("companies: []\n")
    (root / "targets" / "people-watch.yaml").write_text("people: []\n")
    shutil.copy(FIX / "linkedin-2026-10-05.jsonl", root / "state" / "posts" / "linkedin-2026-10-05.jsonl")
    shutil.copy(FIX / "ledger-2026-10-05.jsonl", posts.ledger_path(root))
    return root


def _reread(js, rows):
    path = js / "state" / "posts" / "linkedin-2026-10-06.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


# ----------------------------------------------------------------------------- 1. the key
def test_the_copy_link_and_the_feed_share_form_are_one_key():
    for row in LI_ROWS:
        n = SHARE_ID.search(row["url"]).group(1)
        assert posts.post_key("linkedin", row["url"]) == f"linkedin:{n}"
        assert posts.post_key("linkedin", _share_form(row)["url"]) == f"linkedin:{n}"
    assert posts.post_key("linkedin", PIA["url"]) == "linkedin:7500000000000000136"


def test_ugcpost_and_activity_forms_and_the_last_number_in_the_slug():
    ugc = "https://www.linkedin.com/posts/jo-park_we-are-hiring-ugcPost-7380000000000000005-AbCd/"
    assert posts.post_key("linkedin", ugc) == posts.post_key(
        "linkedin", "https://www.linkedin.com/feed/update/urn:li:ugcPost:7380000000000000005/") == "linkedin:7380000000000000005"
    slug = "https://www.linkedin.com/posts/jo-park_share-your-work-activity-7380000000000000006-Zz9q?utm_source=share"
    assert posts.post_key("linkedin", slug) == "linkedin:7380000000000000006"
    assert posts.post_key("linkedin", "https://www.linkedin.com/feed/update/urn:li:activity:7380000000000000003/"
                          "?commentUrn=urn:li:share:1") == "linkedin:7380000000000000003"
    # a URL with no post number keeps its URL key, as before
    assert posts.post_key("linkedin", "https://www.linkedin.com/posts/jo-park_hello/?x=1") == \
        "linkedin:https://www.linkedin.com/posts/jo-park_hello"


# ----------------------------------------------------------------------------- 2. old rows, keyed again on read
def test_the_ledger_rows_are_keyed_again_on_read_and_their_made_rows_follow(js):
    led = posts.load_ledger(js)
    li = {k: r for k, r in led["records"].items() if r["platform"] == "linkedin"}
    assert set(li) == {"linkedin:7500000000000000136", "linkedin:7500000000000000618"}
    pia = li["linkedin:7500000000000000136"]
    assert pia["key"] == "linkedin:7500000000000000136" and pia["url"] == PIA["url"]
    assert (pia["prospect"], pia["made_on"]) == ("priya-shah", "2026-10-05")       # the made row, old key
    assert len(led["records"]) == 17 and sum(k.startswith("x:") for k in led["records"]) == 15
    assert not led["alias"] and not any(k.startswith("linkedin:http") for k in led["records"])
    assert posts.pending(js, led, set()) == []                     # the made Pia record is not retried


def test_two_old_url_keys_of_one_post_are_one_record(tmp_path):
    root = tmp_path / "js"
    old = "https://www.linkedin.com/posts/jo-park_hiring-share-7380000000000000008-AbCd/"
    twin = "https://www.linkedin.com/posts/jo-park_hiring-share-7380000000000000008-QrSt/"
    rows = [{"key": "linkedin:" + old.rstrip("/"), "platform": "linkedin", "url": old, "fingerprint": "a", "hiring": True},
            {"key": "linkedin:" + twin.rstrip("/"), "platform": "linkedin", "url": twin, "fingerprint": "a", "hiring": True},
            {"key": "linkedin:" + twin.rstrip("/"), "made": True, "prospect": "jo-park", "job": None,
             "made_on": "2026-10-05"}]
    for r in rows:
        jc.append_jsonl(posts.ledger_path(root), r)
    led = posts.load_ledger(root)
    assert list(led["records"]) == ["linkedin:7380000000000000008"]
    rec = led["records"]["linkedin:7380000000000000008"]
    assert rec["url"] == old and rec["urls"] == [old, twin] and rec["prospect"] == "jo-park"


# ----------------------------------------------------------------------------- the proof on the fixture rows
def test_proof_a_reread_of_todays_two_linkedin_rows_in_the_feed_share_form_makes_no_record(js):
    before = posts.ledger_path(js).read_bytes()
    path = _reread(js, [_share_form(r) for r in LI_ROWS])
    rep = posts.ingest(js, today=TODAY, files=[path], write=True)
    assert (rep["read"], rep["new"], rep["duplicates"], rep["cross_platform"]) == (2, 0, 2, 0)
    assert (rep["hiring"], rep["prospects"], rep["jobs"], rep["retried"]) == (0, 0, 0, 0) and not rep["errors"]
    assert posts.ledger_path(js).read_bytes() == before            # not one row appended
    assert not list((js / "prospects").glob("*.md")) and not list((js / "jobs").rglob("*.md"))
    assert len(posts.load_ledger(js)["records"]) == 17


def test_a_feed_share_reread_with_the_text_cut_short_still_meets_its_record_by_id(js):
    """The only other rescue is the fingerprint (the first 40 words); a feed read that shows the post cut at "...see more"
    has other words, and the old key made a second record of the same post."""
    cut = [dict(_share_form(r), text=" ".join(r["text"].split()[:12]) + " ...see more") for r in LI_ROWS]
    rep = posts.ingest(js, today=TODAY, files=[_reread(js, cut)])
    assert (rep["new"], rep["duplicates"], rep["cross_platform"]) == (0, 2, 0)


def test_guard_a_reread_in_the_copy_link_form_is_still_a_plain_duplicate(js):
    before = posts.ledger_path(js).read_bytes()
    rep = posts.ingest(js, today=TODAY, files=[_reread(js, LI_ROWS)], write=True)
    assert (rep["new"], rep["duplicates"], rep["cross_platform"]) == (0, 2, 0)
    assert posts.ledger_path(js).read_bytes() == before


# ----------------------------------------------------------------------------- 3. activity form: author and words
def test_a_reread_in_the_activity_form_joins_its_record_on_author_url_and_words(js):
    activity = dict(PIA, url="https://www.linkedin.com/feed/update/urn:li:activity:7500000000000000700/")
    rep = posts.ingest(js, today=TODAY, files=[_reread(js, [activity])], write=True)
    assert (rep["new"], rep["duplicates"], rep["cross_platform"], rep["prospects"], rep["jobs"]) == (0, 1, 0, 0, 0)
    led = posts.load_ledger(js)
    assert len(led["records"]) == 17
    assert led["alias"] == {"linkedin:7500000000000000700": "linkedin:7500000000000000136"}
    pia = led["records"]["linkedin:7500000000000000136"]
    assert pia["urls"] == [PIA["url"], activity["url"]] and pia["platforms"] == ["linkedin"]
    size = posts.ledger_path(js).stat().st_size
    again = posts.ingest(js, today=TODAY, files=[_reread(js, [activity])], write=True)
    assert (again["new"], again["duplicates"], again["cross_platform"]) == (0, 1, 0)
    assert posts.ledger_path(js).stat().st_size == size              # the alias answers it now: nothing appended


def test_the_same_name_and_words_from_another_profile_are_their_own_post(js):
    other = dict(PIA, url="https://www.linkedin.com/feed/update/urn:li:activity:7500000000000000701/",
                 author_url="https://www.linkedin.com/in/priya-shah-2/")
    rep = posts.ingest(js, today=TODAY, files=[_reread(js, [other])])
    assert (rep["new"], rep["duplicates"], rep["cross_platform"]) == (1, 0, 0)


def test_guard_the_cross_platform_match_is_unchanged(js):
    x = {"url": "https://x.com/priyashah/status/1975000000000000042", "author_handle": "priyashah",
         "author_name": PIA["author_name"], "date": "2026-10-05", "text": PIA["text"]}
    path = js / "state" / "posts" / "x-2026-10-06.jsonl"
    path.write_text(json.dumps(x) + "\n", encoding="utf-8")
    rep = posts.ingest(js, today=TODAY, files=[path])
    assert (rep["new"], rep["duplicates"], rep["cross_platform"]) == (0, 0, 1)
