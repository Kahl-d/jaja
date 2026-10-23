"""browser_research.py: the guardrails a script can hold for reading people in the owner's browser."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import browser_research as br  # noqa: E402
import js_common as jc  # noqa: E402

MORNING = datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc)          # 09:00 in San Francisco
PROFILE = "https://www.linkedin.com/in/jane-doe-12ab/"


def _view(root, url=PROFILE, person="Jane Doe", named_by="ORDERS TARGET 1", now=MORNING, lane="RESEARCH"):
    return br.view(root, person=person, url=url, named_by=named_by, now=now, lane=lane)


# --------------------------------------------------------------------------- view: what counts as one named person
def test_a_named_profile_view_is_allowed_and_logged(tmp_path):
    ok, msg = _view(tmp_path)
    assert ok, msg
    rows = jc.read_jsonl(tmp_path / "state" / "browser" / "views.jsonl")
    assert len(rows) == 1 and rows[0]["handle"] == "jane-doe-12ab" and rows[0]["named_by"] == "ORDERS TARGET 1"
    assert rows[0]["day"] == "2026-09-28" and rows[0]["lane"] == "RESEARCH"


@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/search/results/people/?keywords=acme%20ml",
    "https://www.linkedin.com/company/acme-corp/people/",
    "https://www.linkedin.com/mynetwork/",
    "https://www.linkedin.com/feed/",
    "https://www.linkedin.com/in/",
])
def test_search_lists_and_feeds_are_refused(tmp_path, url):
    ok, msg = _view(tmp_path, url=url)
    assert not ok and "one person's profile" in msg
    assert not (tmp_path / "state" / "browser" / "views.jsonl").exists()


def test_a_person_nobody_named_is_refused(tmp_path):
    ok, msg = _view(tmp_path, named_by="  ")
    assert not ok and "named" in msg


def test_a_second_view_sooner_than_the_pace_allows_is_refused(tmp_path):
    assert _view(tmp_path)[0]
    ok, msg = _view(tmp_path, url="https://www.linkedin.com/in/john-roe/", person="John Roe",
                    now=MORNING + timedelta(seconds=10))
    assert not ok and "wait" in msg
    assert _view(tmp_path, url="https://www.linkedin.com/in/john-roe/", person="John Roe",
                 now=MORNING + timedelta(seconds=20))[0]


def test_the_twenty_sixth_person_of_the_day_is_refused(tmp_path):
    for n in range(br.DAILY_VIEWS):
        ok, msg = _view(tmp_path, url=f"https://www.linkedin.com/in/person-{n}/", person=f"P {n}",
                        now=MORNING + timedelta(minutes=n))
        assert ok, msg
    ok, msg = _view(tmp_path, url="https://www.linkedin.com/in/person-25/", person="P 25",
                    now=MORNING + timedelta(minutes=30))
    assert not ok and "25" in msg


def test_the_same_person_again_today_is_not_counted_twice(tmp_path):
    assert _view(tmp_path)[0]
    ok, msg = _view(tmp_path, url=PROFILE + "details/experience/", now=MORNING + timedelta(minutes=2))
    assert ok and "already" in msg
    assert br.status(tmp_path, MORNING + timedelta(minutes=3))["views_today"] == 1


def test_the_cap_resets_on_the_department_day_not_the_utc_day(tmp_path):
    late = datetime(2026, 9, 29, 6, 30, tzinfo=timezone.utc)            # 23:30 on 2026-09-28 in San Francisco
    for n in range(br.DAILY_VIEWS):
        assert _view(tmp_path, url=f"https://www.linkedin.com/in/p-{n}/", person=f"P {n}",
                     now=late - timedelta(minutes=60 - n))[0]
    assert not _view(tmp_path, url="https://www.linkedin.com/in/p-x/", person="P X", now=late)[0]
    next_morning = datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)   # 08:00 on 2026-09-29
    assert _view(tmp_path, url="https://www.linkedin.com/in/p-x/", person="P X", now=next_morning)[0]


def test_a_page_off_linkedin_needs_no_view_and_is_not_logged(tmp_path):
    ok, msg = _view(tmp_path, url="https://research.acme.test/people/jane-doe")
    assert ok and "not LinkedIn" in msg
    assert not (tmp_path / "state" / "browser" / "views.jsonl").exists()


def test_status_reports_the_room_left(tmp_path):
    _view(tmp_path)
    s = br.status(tmp_path, MORNING + timedelta(minutes=1))
    assert (s["views_today"], s["room"], s["day"]) == (1, br.DAILY_VIEWS - 1, "2026-09-28")


# --------------------------------------------------------------------------- clicks: reading only, default deny
@pytest.mark.parametrize("label", ["Connect", "Follow", "Message", "Like", "React", "Comment", "Repost", "Send",
                                   "I'm interested", "Save", "Apply", "Easy Apply", "Endorse", "More",
                                   "+ Follow", "Send without a note", "Share"])
def test_signal_sending_clicks_are_refused(label):
    ok, reason = br.click_check(label)
    assert not ok and "signals to the person" in reason, reason


@pytest.mark.parametrize("label", ["…see more", "see more", "Show all 12 experiences", "Show all", "About",
                                   "Experience", "Education", "Activity", "Posts", "Featured", "Show all posts"])
def test_reading_controls_are_allowed(label):
    ok, reason = br.click_check(label)
    assert ok, reason


def test_an_unknown_control_is_refused_by_default():
    ok, reason = br.click_check("Open to")
    assert not ok and "unknown" in reason


# --------------------------------------------------------------------------- identity: two independent sources
def _obs(publisher, **fields):
    return {"publisher": publisher, "url": f"https://{publisher}/x", "date": "2026-09-28", **fields}


BOTH = dict(name="Sam Referrer", employer="Acme Corp", title="Senior Manager, AI Research",
            location="Dearborn, Michigan")


def test_verified_needs_every_field_matched_in_two_independent_sources():
    v = br.verify([_obs("linkedin.com", **BOTH),
                   _obs("research.acme.test", name="Sam Referrer", employer="Acme Corp", title="Senior Manager, AI Research",
                        location="Dearborn, MI")])
    assert v["verdict"] == "VERIFIED", v
    assert v["line"].startswith("Identity: VERIFIED")
    assert "linkedin.com" in v["line"] and "research.acme.test" in v["line"]


def test_the_same_publisher_twice_is_one_source():
    v = br.verify([_obs("linkedin.com", **BOTH), _obs("linkedin.com", **BOTH)])
    assert v["verdict"] == "UNVERIFIED"


def test_a_field_seen_in_only_one_source_is_probable_not_verified():
    v = br.verify([_obs("linkedin.com", **BOTH),
                   _obs("scholar.google.com", name="Sam Referrer", employer="Acme Corp")])
    assert v["verdict"] == "PROBABLE" and "title" in v["line"] and "location" in v["line"]


def test_a_partial_title_is_probable():
    v = br.verify([_obs("linkedin.com", **BOTH),
                   _obs("research.acme.test", **{**BOTH, "title": "Manager, AI Research"})])
    assert v["verdict"] == "PROBABLE" and "title" in v["line"]


def test_a_field_that_differs_is_a_conflict():
    v = br.verify([_obs("linkedin.com", **BOTH), _obs("research.acme.test", **{**BOTH, "employer": "Globex Industries"})])
    assert v["verdict"] == "CONFLICT" and "employer" in v["line"]


def test_one_source_is_unverified():
    assert br.verify([_obs("linkedin.com", **BOTH)])["verdict"] == "UNVERIFIED"


def test_verify_prints_the_match_table_for_the_brief():
    v = br.verify([_obs("linkedin.com", **BOTH), _obs("research.acme.test", **BOTH)])
    table = v["table"]
    assert table.splitlines()[0].startswith("| field |") and "| employer |" in table and "match" in table


# --------------------------------------------------------------------------- CLI
def test_cli_view_status_and_click(tmp_path, capsys):
    assert br.main(["--root", str(tmp_path), "view", "--person", "Jane Doe", "--url", PROFILE,
                    "--named-by", "ORDERS TARGET 1", "--now", MORNING.isoformat()]) == 0
    assert br.main(["--root", str(tmp_path), "view", "--person", "X", "--url",
                    "https://www.linkedin.com/search/results/people/?keywords=x", "--named-by", "K",
                    "--now", (MORNING + timedelta(minutes=5)).isoformat()]) == 3
    assert br.main(["--root", str(tmp_path), "click-check", "Connect"]) == 2
    assert br.main(["--root", str(tmp_path), "click-check", "Show all"]) == 0
    capsys.readouterr()
    assert br.main(["--root", str(tmp_path), "status", "--now", (MORNING + timedelta(minutes=6)).isoformat()]) == 0
    assert json.loads(capsys.readouterr().out)["views_today"] == 1


def test_cli_verify_reads_a_yaml_file_and_exits_by_verdict(tmp_path, capsys):
    f = tmp_path / "obs.yaml"
    f.write_text("observations:\n"
                 "  - {publisher: linkedin.com, url: 'https://www.linkedin.com/in/x', date: 2026-09-28, name: A B, "
                 "employer: Acme, title: Engineer, location: Austin}\n"
                 "  - {publisher: acme.com, url: 'https://acme.com/team', date: 2026-09-28, name: A B, "
                 "employer: Acme, title: Engineer, location: Austin}\n", encoding="utf-8")
    assert br.main(["--root", str(tmp_path), "verify", str(f)]) == 0
    assert "Identity: VERIFIED" in capsys.readouterr().out


# --------------------------------------------------------------------------- job-view: a JD read from today's list
JOB = "https://www.linkedin.com/jobs/view/4457496614/"


def _list(root, *items, day="2026-09-28"):
    d = root / "state" / "jd"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"read-list-{day}.json").write_text(json.dumps(
        [{"id": jid, "url": url, "hunt_score": 80} for jid, url in items]))


def test_a_job_on_todays_read_list_may_be_opened_and_is_logged(tmp_path):
    _list(tmp_path, ("openai-li-4457496614", "https://www.linkedin.com/jobs/view/4457496614"))
    ok, msg = br.job_view(tmp_path, job="openai-li-4457496614", url=JOB, now=MORNING)
    assert ok, msg
    rows = jc.read_jsonl(tmp_path / "state" / "browser" / "job-views.jsonl")
    assert [(r["job"], r["day"], r["linkedin_id"]) for r in rows] == [("openai-li-4457496614", "2026-09-28",
                                                                       "4457496614")]


@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/jobs/search/?keywords=ai%20engineer",
    "https://www.linkedin.com/jobs/collections/recommended/",
    "https://www.linkedin.com/in/jane-doe-12ab/",
    "https://www.linkedin.com/feed/",
])
def test_linkedin_pages_other_than_one_job_are_refused(tmp_path, url):
    _list(tmp_path, ("j", JOB))
    ok, msg = br.job_view(tmp_path, job="j", url=url, now=MORNING)
    assert not ok and "one job page" in msg


def test_a_job_not_on_todays_list_or_a_different_posting_is_refused(tmp_path):
    _list(tmp_path, ("j", JOB))
    ok, msg = br.job_view(tmp_path, job="other", url=JOB, now=MORNING)
    assert not ok and "read list" in msg
    ok, msg = br.job_view(tmp_path, job="j", url="https://www.linkedin.com/jobs/view/111/", now=MORNING)
    assert not ok and "read list" in msg
    ok, msg = br.job_view(tmp_path, job="j", url=JOB, now=MORNING + timedelta(days=1))   # yesterday's list
    assert not ok


def test_job_views_keep_human_pace_and_twenty_a_day(tmp_path):
    items = [(f"j{i}", f"https://www.linkedin.com/jobs/view/{1000 + i}/") for i in range(21)]
    _list(tmp_path, *items)
    assert br.job_view(tmp_path, job="j0", url=items[0][1], now=MORNING)[0]
    ok, msg = br.job_view(tmp_path, job="j1", url=items[1][1], now=MORNING + timedelta(seconds=5))
    assert not ok and "human pace" in msg
    for i in range(1, 20):
        ok, msg = br.job_view(tmp_path, job=f"j{i}", url=items[i][1], now=MORNING + timedelta(minutes=i))
        assert ok, msg
    ok, msg = br.job_view(tmp_path, job="j20", url=items[20][1], now=MORNING + timedelta(minutes=30))
    assert not ok and "20 job pages" in msg
    assert br.job_view(tmp_path, job="j3", url=items[3][1], now=MORNING + timedelta(minutes=31))[0]   # again: free


def test_a_job_page_off_linkedin_needs_no_view(tmp_path):
    ok, msg = br.job_view(tmp_path, job="z", url="https://www.ziprecruiter.com/c/Zed/Job/1", now=MORNING)
    assert ok and "not LinkedIn" in msg
    assert not (tmp_path / "state" / "browser" / "job-views.jsonl").exists()


def test_cli_job_view(tmp_path, capsys):
    _list(tmp_path, ("j", JOB))
    assert br.main(["--root", str(tmp_path), "job-view", "--job", "j", "--url", JOB,
                    "--now", MORNING.isoformat()]) == 0
    assert br.main(["--root", str(tmp_path), "job-view", "--job", "nope", "--url", JOB,
                    "--now", (MORNING + timedelta(minutes=5)).isoformat()]) == 3


# --------------------------------------------------------------------------- verify: format variants 
# A person matched across several publishers yet landed in CONFLICT: "Founder & CEO" vs "Founder and CEO" (normalize
# dropped "&" but kept "and") and "Mark H. Rowe" vs "Mark Rowe" (a middle initial); the same for "Lin-Wei Park" vs "LW Park".
MARK = dict(name="Mark Rowe", employer="Initech Labs", title="Founder and CEO", location="San Francisco, CA")


def test_an_ampersand_and_the_word_and_are_the_same_title():
    v = br.verify([_obs("linkedin.com", **MARK), _obs("initech-labs.test", **{**MARK, "title": "Founder & CEO"})])
    assert v["states"]["title"] == "match" and v["verdict"] == "VERIFIED"


@pytest.mark.parametrize("a, b", [("Mark H. Rowe", "Mark Rowe"), ("Lin-Wei Park", "LW Park"), ("Lin-Wei Park", "L. W. Park"),
                                  ("J. Rowe", "John Rowe")])
def test_a_middle_initial_or_initials_is_compatible_not_a_conflict(a, b):
    v = br.verify([_obs("linkedin.com", **{**MARK, "name": a}), _obs("initech-labs.test", **{**MARK, "name": b})])
    assert v["states"]["name"] == "compatible" and v["verdict"] == "PROBABLE"


@pytest.mark.parametrize("a, b", [("Mark Rowe", "Mark Chan"), ("Lin-Wei Park", "LW Cho"), ("Mark Rowe", "Mary Rowe"),
                                  ("Mane Lee", "Mary Ann Nell Eve Lee")])       # a four-letter word is a name, not initials
def test_a_different_surname_or_given_name_still_differs(a, b):
    v = br.verify([_obs("linkedin.com", **{**MARK, "name": a}), _obs("initech-labs.test", **{**MARK, "name": b})])
    assert v["states"]["name"] == "differs" and v["verdict"] == "CONFLICT"
