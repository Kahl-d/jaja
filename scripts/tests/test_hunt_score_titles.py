"""hunt_score, two scout lessons.
Alert copies: a LinkedIn alert copy (`-li-` id) with posted_at null, or a posted_at taken from the alert, looks fresh (the
alert date) while the same seat has been open for weeks: it gets no freshness points.
Forward-deployed titles: a Forward Deployed title with no AI word took the full on-role family on the title alone (a C#
configuration job at a compliance SaaS scored 70): the JD's duties decide."""
from __future__ import annotations

from datetime import timedelta

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import hunt_score as hs  # noqa: E402
from test_hunt_score import JD, TODAY, _ctx, _job, world  # noqa: F401

NON_AI_JD = ("Configure and support Examplesoft, our healthcare compliance SaaS, for enterprise customers. C#, .NET, SQL Server, "
               "REST integrations and customer onboarding. Travel to customer sites.")


# ----------------------------------------------------------------------------- alert copies
def test_an_li_copy_with_no_posting_date_gets_no_freshness(world):
    rec = _job(world, "acme-li-4465486094", "AI Engineer", "tiny-ai", posted=None, save=False)
    rec.update({"posted_at": None, "first_seen": f"{TODAY.isoformat()}T06:00:00Z", "source": "linkedin-alert"})
    out = hs.score(rec, _ctx(world))
    assert out["components"]["freshness"] == 0.0
    assert any("alert" in r for r in out["hunt_reasons"])


def test_an_li_copy_whose_date_is_the_alert_date_gets_no_freshness(world):
    rec = _job(world, "acme-li-1", "AI Engineer", "tiny-ai", posted=f"{TODAY.isoformat()}T08:00:00Z", save=False)
    rec["alert_received_at"] = f"{TODAY.isoformat()}T19:45:06Z"
    assert hs.score(rec, _ctx(world))["components"]["freshness"] == 0.0


def test_an_li_copy_with_its_own_posting_date_keeps_freshness(world):
    """A dedup fold gives the copy its ATS twin's posted_at: then the date is real."""
    rec = _job(world, "acme-li-2", "AI Engineer", "tiny-ai", posted=(TODAY - timedelta(days=1)).isoformat(), save=False)
    rec["alert_received_at"] = f"{(TODAY - timedelta(days=9)).isoformat()}T19:45:06Z"
    assert hs.score(rec, _ctx(world))["components"]["freshness"] == 1.0


def test_a_board_record_with_no_posting_date_is_unchanged(world):
    rec = _job(world, "acme-123", "AI Engineer", "tiny-ai", save=False)
    rec.update({"posted_at": None, "first_seen": f"{TODAY.isoformat()}T06:00:00Z"})
    assert hs.score(rec, _ctx(world))["components"]["freshness"] == 1.0


# ----------------------------------------------------------------------------- forward-deployed titles
def test_an_fde_title_with_a_non_ai_jd_drops(world):
    s, why = hs.role_fit("Forward Deployed Engineer (Examplesoft) - Remote, US", NON_AI_JD)
    assert s <= 0.4 and "duties" in why


def test_a_non_ai_fde_role_never_reaches_70(world):
    rec = _job(world, "examplesoft-fde-1", "Forward Deployed Engineer (Examplesoft) - Remote, US", "mid-co",
               jd=NON_AI_JD, location="Remote (United States)", save=False)
    assert hs.score(rec, _ctx(world))["hunt_score"] < 70


def test_two_ai_terms_cap_at_0_7_and_three_keep_full_marks():
    two = "Deploy our platform for customers; integrate LLM features and build RAG pipelines."
    three = "Deploy LLM agents for customers, own evaluation and RAG pipelines in Python."
    assert hs.role_fit("Forward Deployed Engineer", two)[0] == 0.7
    assert hs.role_fit("Forward Deployed Engineer", three)[0] == 1.0


def test_an_ai_word_in_the_title_or_no_jd_yet():
    assert hs.role_fit("Forward Deployed AI Engineer", NON_AI_JD)[0] == 1.0          # the title itself says AI
    s, why = hs.role_fit("Forward Deployed Engineer", "")
    assert s == 0.5 and "not captured" in why


def test_an_li_copy_with_a_recorded_hiring_post_is_dated_by_the_post(world):
    """The alert's own date never counts, but a hiring post within 7 days does."""
    rec = _job(world, "acme-li-2", "AI Engineer", "tiny-ai", posted=None, save=False)
    rec.update({"posted_at": None, "first_seen": f"{TODAY.isoformat()}T06:00:00Z", "source": "linkedin-alert",
                "hiring_signal": {"url": "https://x.com/acme/status/9", "date": (TODAY - timedelta(days=1)).isoformat(),
                                  "by": "founder"}})
    out = hs.score(rec, _ctx(world))
    assert out["components"]["freshness"] == 1.0
    assert any("hiring post by founder" in r for r in out["hunt_reasons"])
