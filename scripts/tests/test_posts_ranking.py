"""A hiring post with a real person outranks a portal listing. hunt_score gives a job a person's X or
LinkedIn hiring post names POST_BOOST points and at least the cut (the role gate still holds an off-role post); next_up
lists post rows before listing rows (pins and the years gate still come first). Synthetic records only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import fetch_ats as fa  # noqa: E402
import hunt_score as hs  # noqa: E402
import next_up as nu  # noqa: E402
import tracker as tr  # noqa: E402
from js_common import fm  # noqa: E402
from test_hunt_score import TODAY, _ctx, _job, world  # noqa: F401

X_POST = "https://x.com/founder_jo/status/1975000000000000001"
LI_POST = "https://www.linkedin.com/posts/jo-park_hiring-activity-7380000000000000001-AbCd"


# ----------------------------------------------------------------------------- what counts as a post
def test_from_post_reads_x_and_linkedin_posts_only():
    assert hs.from_post({"hiring_signal": {"url": X_POST, "date": "2026-09-28", "by": "@founder_jo"}}) == X_POST
    assert hs.from_post({"source_url": LI_POST}) == LI_POST
    assert hs.from_post({"source_url": "https://www.linkedin.com/feed/update/urn:li:activity:7380000000000000002/"})
    assert hs.from_post({"source_url": "https://www.linkedin.com/jobs/view/4012345678/"}) is None     # a listing
    assert hs.from_post({"source_url": "https://jobs.ashbyhq.com/acme/123"}) is None
    assert hs.from_post({"hiring_signal": {"url": "https://news.ycombinator.com/item?id=1"}}) is None  # only X and LinkedIn posts count


# ----------------------------------------------------------------------------- hunt_score
def test_a_post_job_outranks_the_same_listing_and_says_why(world):
    ctx = _ctx(world)
    listing = _job(world, "mid-1", "AI Engineer, Agents", "mid-co", save=False)
    post = _job(world, "mid-2", "AI Engineer, Agents", "mid-co", save=False,
                hiring_signal={"url": X_POST, "date": (TODAY - timedelta(days=1)).isoformat(), "by": "@founder_jo"})
    a, b = hs.score(listing, ctx), hs.score(post, ctx)
    assert b["hunt_score"] >= min(100, a["hunt_score"] + hs.POST_BOOST) or b["hunt_score"] == 100
    assert b["hunt_score"] > a["hunt_score"]
    assert any("has a person to talk to" in r and X_POST in r for r in b["hunt_reasons"])
    ranked = hs.apply_scores([dict(listing), dict(post)], ctx)
    assert [r["id"] for r in ranked] == ["mid-2", "mid-1"]


def test_a_weak_post_job_reaches_the_cut(world):
    ctx = _ctx(world)
    jd = "Build product features with LLM calls and RAG pipelines. 3+ years of experience with TypeScript."
    rec = _job(world, "mid-3", "Software Engineer", "mid-co", jd=jd, posted=(TODAY - timedelta(days=20)).isoformat(),
               save=False, source_url=LI_POST)
    assert hs.score(rec, ctx)["hunt_score"] >= int(round(ctx["cut"]))


def test_an_off_role_post_stays_under_the_cut(world):
    ctx = _ctx(world)
    rec = _job(world, "mid-4", "Account Executive", "mid-co", save=False,
               hiring_signal={"url": X_POST, "date": TODAY.isoformat(), "by": "@founder_jo"})
    out = hs.score(rec, ctx)
    assert out["hunt_score"] < int(round(ctx["cut"]))
    assert not any("has a person to talk to" in r for r in out["hunt_reasons"])


# ----------------------------------------------------------------------------- next_up
NOW = datetime(2026, 10, 4, 17, 0, tzinfo=timezone.utc)
JD = "We build LLM agents and evaluation harnesses in Python for our customers. " * 6


def _nu_job(root, jid, slug, posted, **extra):
    rec = fa.make_record({"name": slug.title(), "slug": slug}, title="AI Engineer", source="ashby",
                         source_url=f"https://jobs.ashbyhq.com/{slug}/{jid}", apply_url=None, ats_vendor="ashby",
                         posted_at=posted, location="San Francisco, CA", remote="onsite", comp=(None, None, None),
                         employment_type=None, jd_text=JD, now=NOW)
    rec.update({"id": jid, "status": "discovered", "fit_score": 4.3, "gate": "pass", "sponsorship": "yes", **extra})
    fa.save_job(root, rec)


def _nu_app(root, slug, company, job):
    path = root / "applications" / slug / "application.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = tr.format_event("2026-10-02T17:00:00Z", "fit_checked", "manual", 1.0, "e")
    meta = {"id": slug, "company": company, "title": "AI Engineer", "job": f"[[{job}]]", "stage": "fit_checked"}
    path.write_text(fm.dump(meta, f"# {slug}\n\n## Events\n{line}\n"))


def _nu_world(tmp_path):
    root = tmp_path / "nu"
    for d in ("jobs", "applications", "targets", "state", "hub", "prospects", "outbox"):
        (root / d).mkdir(parents=True)
    (root / "targets" / "companies.yaml").write_text("companies: []\n")
    _nu_job(root, "fresh-1", "fresh", "2026-10-04T08:00:00Z")                     # a listing, hours old
    _nu_app(root, "fresh-ai-202610", "Fresh", "fresh-1")
    _nu_job(root, "posted-1", "posted", "2026-09-10T08:00:00Z",                   # older, but a person posted it
            hiring_signal={"url": X_POST, "date": "2026-09-10", "by": "@founder_jo"})
    _nu_app(root, "posted-ai-202610", "Posted", "posted-1")
    return root


def test_next_up_lists_the_post_row_before_a_fresher_listing(tmp_path):
    root = _nu_world(tmp_path)
    rows = nu.build(root, NOW)["rows"]
    by = {r["company"]: r for r in rows}
    assert by["Fresh"]["per_hour"] > by["Posted"]["per_hour"]          # the listing would win on expected interviews
    assert [r["company"] for r in rows][:2] == ["Posted", "Fresh"]
    assert by["Posted"]["post"] == X_POST and "a person to talk to" in by["Posted"]["why"]
    assert by["Fresh"]["post"] is None


def test_a_pin_still_comes_before_a_post_row(tmp_path):
    root = _nu_world(tmp_path)
    (root / "hub" / "NEXT-UP-overrides.yaml").write_text(
        "applying: paused\noverrides:\n  - {id: fresh-ai-202610, action: pin, rank: 1, by: lead, date: 2026-10-05}\n")
    assert [r["company"] for r in nu.build(root, NOW)["rows"]][:2] == ["Fresh", "Posted"]


# ----------------------------------------------------------------------------- a skipped company gets no post boost
def test_no_post_boost_at_a_company_the_fit_reports_skipped(world):
    """The owner's skips win over a post: the fit-scorer's skip reports at a company (the fit history hunt_score reads)
    stop the post boost there; a company with no skip keeps it."""
    _job(world, "mid-old", "Backend Engineer", "mid-co", fit_verdict="skip", fit_score=2.1)
    ctx = _ctx(world)
    sig = {"url": X_POST, "date": TODAY.isoformat(), "by": "@founder_jo"}
    skipped = _job(world, "mid-5", "AI Engineer, Agents", "mid-co", save=False, hiring_signal=sig)
    out = hs.score(skipped, ctx)
    assert not any("has a person to talk to" in r for r in out["hunt_reasons"])
    assert any("skip" in r and "no post boost" in r for r in out["hunt_reasons"])
    fresh = _job(world, "tiny-5", "AI Engineer, Agents", "tiny-ai", save=False, hiring_signal=sig)
    assert any("has a person to talk to" in r for r in hs.score(fresh, ctx)["hunt_reasons"])


def test_next_up_gives_no_post_first_place_at_a_skipped_company(tmp_path):
    root = _nu_world(tmp_path)
    _nu_job(root, "posted-0", "posted", "2026-09-01T08:00:00Z", fit_verdict="skip", fit_score=2.0, status="skipped")
    rows = nu.build(root, NOW)["rows"]
    assert [r["company"] for r in rows][:2] == ["Fresh", "Posted"]
    assert {r["company"]: r for r in rows}["Posted"]["post"] is None
