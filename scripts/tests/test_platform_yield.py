"""platform_yield.py: per-platform yield from jobs/ and applications/ (trailing window by first_seen),
written in place into targets/platforms.yaml, and the registry consistency check."""
from __future__ import annotations

from datetime import date

import shutil
from pathlib import Path

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW

import fetch_ats as fa  # noqa: E402
import job_alerts as ja  # noqa: E402
import platform_yield as py  # noqa: E402
from js_common import JS_ROOT, fm, write_record  # noqa: E402

REGISTRY = """\
# comment kept
platforms:
  - id: linkedin
    name: LinkedIn Jobs
    url: https://www.linkedin.com/jobs/
    access: [email-alert, browser]
    adapter: scripts/job_alerts.py
    alert_senders: [jobalerts-noreply@alerts.example.test]
    tos: {quote: "no bots", url: "https://www.linkedin.com/legal/user-agreement", read_on: 2026-09-25, verified: "yes"}
    agent_terms: none
    sponsorship_filter: none
    account_needed: true
    alert_setup: "search, alert on"
    setup_priority: P0
    status: probation
    yield: {fetched: 0, new: 0, queued: 0, applied: 0, interviews: 0}   # written by platform_yield.py
    yield_as_of: null
    last_reviewed: 2026-09-25
  - id: company-ats
    name: Company ATS boards
    url: https://boards-api.greenhouse.io
    access: [api]
    adapter: scripts/fetch_ats.py
    alert_senders: []
    tos: {quote: "public", url: "https://docs.greenhouse.io/job-board.html", read_on: 2026-09-25, verified: "yes"}
    agent_terms: none
    sponsorship_filter: none
    account_needed: false
    alert_setup: none
    setup_priority: none
    status: active
    yield: {fetched: 0, new: 0, queued: 0, applied: 0, interviews: 0}
    yield_as_of: null
    last_reviewed: 2026-09-25
"""


def job(root, jid, source, first_seen, status="discovered", **extra):
    rec = fa.make_record({"name": "Acme", "slug": "acme"}, title="ML Engineer", source=source,
                         source_url=f"https://example.com/{jid}", apply_url=None, ats_vendor="unknown",
                         posted_at=None, location="San Francisco, CA", remote="onsite", comp=(None, None, None),
                         employment_type=None, jd_text="JD", now=NOW)
    rec.update({"id": jid, "first_seen": first_seen, "status": status, **extra})
    fa.save_job(root, rec)


def application(root, slug, job_id, *events):
    lines = "\n".join(f"- {at} · {kind} · manual · conf=1.00 · test" for at, kind in events)
    write_record(root / "applications" / slug / "application.md",
                 {"id": slug, "job": f"[[{job_id}]]", "company": "Acme", "title": "ML Engineer", "stage": "applied",
                  "created": "2026-09-20", "updated": "2026-09-24"}, f"## Events\n{lines}\n")


@pytest.fixture
def world(root):
    (root / "targets").mkdir()
    (root / "targets" / "platforms.yaml").write_text(REGISTRY, encoding="utf-8")
    job(root, "a", "linkedin-alert", "2026-09-20T10:00:00Z", "queued", platform="linkedin", fit_verdict="apply")
    job(root, "b", "linkedin-alert", "2026-09-21T10:00:00Z", "skipped", platform="linkedin", duplicate_of="c")
    job(root, "c", "greenhouse", "2026-09-19T10:00:00Z", "applied", seen_on=["linkedin"], application="[[acme-c]]")
    job(root, "d", "hiringcafe-alert", "2026-08-01T10:00:00Z", "queued", platform="hiringcafe")
    job(root, "e", "hn", "2026-09-22T10:00:00Z", "fit_checked")
    job(root, "f", "manual", "2026-09-23T10:00:00Z", platform="wellfound")
    application(root, "acme-c", "c", ("2026-09-21T10:00:00Z", "applied"), ("2026-09-24T10:00:00Z", "screen"))
    application(root, "acme-a", "a", ("2026-09-22T10:00:00Z", "applied"))
    return root


def test_yield_by_platform_counts_sightings_new_jobs_and_outcomes(world):
    y = py.compute(world, date(2026, 9, 25), days=30)
    assert y["linkedin"] == {"fetched": 3, "new": 1, "queued": 1, "applied": 1, "interviews": 0}
    assert y["company-ats"] == {"fetched": 1, "new": 1, "queued": 1, "applied": 1, "interviews": 1}
    assert y["hacker-news"] == {"fetched": 1, "new": 1, "queued": 0, "applied": 0, "interviews": 0}
    assert y["wellfound"]["new"] == 1
    assert "hiringcafe" not in y                               # first seen 55 days before the run


def test_write_rewrites_only_the_yield_lines(world, capsys):
    assert py.main(["--root", str(world), "--today", "2026-09-25", "--write"]) == 0
    text = (world / "targets" / "platforms.yaml").read_text(encoding="utf-8")
    assert text.startswith("# comment kept")
    assert "    yield: {fetched: 3, new: 1, queued: 1, applied: 1, interviews: 0}   # written by platform_yield.py" in text
    assert text.count("yield_as_of: '2026-09-25'") == 2
    data = fm.parse_yaml(text)
    assert data["platforms"][1]["yield"] == {"fetched": 1, "new": 1, "queued": 1, "applied": 1, "interviews": 1}
    out = capsys.readouterr().out
    assert "linkedin" in out and "wrote" in out


def test_check_flags_broken_entries(root):
    (root / "targets").mkdir()
    bad = REGISTRY.replace("access: [email-alert, browser]", "access: [scrape]").replace(
        "alert_senders: []", "alert_senders: [jobalerts-noreply@alerts.example.test]").replace(
        "    status: active\n", "    status: live\n")
    (root / "targets" / "platforms.yaml").write_text(bad, encoding="utf-8")
    errors, _ = py.check(root)
    text = "\n".join(errors)
    assert "linkedin: access 'scrape'" in text
    assert "company-ats: status 'live'" in text
    assert "jobalerts-noreply@alerts.example.test is listed under linkedin and company-ats" in text
    for pid in ("glassdoor", "ziprecruiter", "wttj", "hiringcafe", "yc"):
        assert f"{pid}: job_alerts.py has a parser for it but targets/platforms.yaml has no entry" in text


def test_the_committed_registry_passes_the_check():
    errors, _warnings = py.check(JS_ROOT)
    assert errors == []


def test_the_committed_registry_covers_every_named_platform():
    platforms = ja.load_platforms(JS_ROOT)
    named = ["linkedin", "glassdoor", "ziprecruiter", "wttj", "hiringcafe", "yc", "himalayas", "wellfound",
             "builtin", "indeed", "dice", "jobright", "trueup", "levels-fyi", "handshake", "vc-portfolio",
             "remotive", "remoteok", "simplify", "hacker-news", "adzuna", "company-ats"]
    assert set(named) <= set(platforms)
    for pid in ja.DEDICATED:
        assert platforms[pid]["adapter"] == "scripts/job_alerts.py" and platforms[pid]["alert_senders"]


# ----------------------------------------------------------------------------- status proposals awaiting the Recruiter
def _registry_with(root, marker: str) -> None:
    """The fixture registry with LinkedIn moved from probation to active and the given `proposed:` line."""
    (root / "targets").mkdir(exist_ok=True)
    text = REGISTRY.replace("    status: probation\n", "    status: active\n" + marker, 1)
    (root / "targets" / "platforms.yaml").write_text(text, encoding="utf-8")


def _about_proposals(messages):
    return [m for m in messages if "propos" in m]


def test_a_status_proposal_waits_quietly_until_the_monday_review(root):
    _registry_with(root, "    proposed: {from: probation, date: 2026-12-03}\n")   # a Thursday watch
    errors, warnings = py.check(root, date(2026, 12, 7))                              # the Monday review
    assert _about_proposals(errors + warnings) == []
    errors, warnings = py.check(root, date(2026, 12, 8))
    assert _about_proposals(errors) == []
    (msg,) = _about_proposals(warnings)
    assert msg.startswith("linkedin:") and "2026-12-07" in msg and "probation" in msg


def test_a_proposal_made_on_a_monday_is_ratified_that_day(root):
    _registry_with(root, "    proposed: {from: probation, date: 2026-11-02}\n")   # a Monday
    assert _about_proposals(py.check(root, date(2026, 11, 2))[1]) == []
    assert len(_about_proposals(py.check(root, date(2026, 11, 3))[1])) == 1


@pytest.mark.parametrize("marker", ["    proposed: {from: live, date: 2026-12-03}\n",
                                    "    proposed: {from: probation, date: someday}\n",
                                    "    proposed: yes\n",
                                    "    proposed: {from: active, date: 2026-12-03}\n"])
def test_a_malformed_proposal_is_an_error(root, marker):
    _registry_with(root, marker)
    errors, _ = py.check(root, date(2026, 12, 3))
    assert [e for e in errors if e.startswith("linkedin: proposed")]


# --------------------------------------------------------------------------- the next monthly watch
@pytest.mark.parametrize("today, nxt", [
    (date(2026, 9, 3), date(2026, 10, 3)),       # the watch day itself: the 3rd of next month
    (date(2026, 9, 25), date(2026, 10, 3)),
    (date(2026, 12, 3), date(2027, 1, 3)),       # December wraps to January of the next year
    (date(2026, 12, 31), date(2027, 1, 3)),
    (date(2027, 1, 31), date(2027, 2, 3)),
    (date(2026, 10, 1), date(2026, 10, 3)),      # before this month's watch: it is still ahead
])
def test_next_watch_is_the_next_3rd_after_today(today, nxt):
    assert py.next_watch(today) == nxt


def test_the_watch_day_matches_the_routine_schedule():
    from js_common import CODE_ROOT                      # the routine is a system file, shipped with the code
    spec = fm.parse_yaml((CODE_ROOT / "routines" / "js-platform-watch.yaml").read_text(encoding="utf-8"))
    minute, hour, dom, month, dow = str(spec["schedule"]).split()
    assert (int(dom), month, dow) == (py.WATCH_DAY, "*", "*")


def test_the_yield_run_prints_the_next_watch_date(world, capsys):
    assert py.main(["--root", str(world), "--today", "2026-12-03"]) == 0
    assert "next monthly watch: 2027-01-03" in capsys.readouterr().out
    assert py.main(["--root", str(world), "--today", "2026-12-03", "--json"]) == 0
    import json
    assert json.loads(capsys.readouterr().out)["next_watch"] == "2027-01-03"


def test_the_check_prints_the_next_watch_date(root, capsys):
    (root / "targets").mkdir()
    (root / "targets" / "platforms.yaml").write_text(REGISTRY, encoding="utf-8")
    py.main(["--check", "--root", str(root), "--today", "2026-12-03"])
    assert "next monthly watch: 2027-01-03" in capsys.readouterr().out


# ------------------------------------------------------------------ agent_terms: the status gate by script
# (platform-scout evals 15 and 16: a platform whose only agent access is a credentials/auto-apply gateway was
# set to probation with access [browser] although the card says never probation; card text alone did not hold)
NEVER_PROBATION = "never probation/active: set manual (the owner reads it personally) or blocked"


def _linkedin(root, status: str, agent_terms: str | None, access: str = "[email-alert, browser]") -> None:
    (root / "targets").mkdir(exist_ok=True)
    text = REGISTRY.replace("    status: probation\n", f"    status: {status}\n", 1).replace(
        "access: [email-alert, browser]", f"access: {access}", 1)
    head, sep, tail = text.partition("    agent_terms: none\n")
    text = head + (f"    agent_terms: {agent_terms}\n" if agent_terms is not None else "") + tail
    (root / "targets" / "platforms.yaml").write_text(text, encoding="utf-8")


def _linkedin_errors(root):
    """LinkedIn's errors, less the adapter one (the test root has no scripts/)."""
    return [e for e in py.check(root, date(2026, 10, 13))[0] if e.startswith("linkedin") and "adapter" not in e]


def test_agent_terms_is_a_required_field(root):
    _linkedin(root, "probation", None)
    (msg,) = _linkedin_errors(root)
    assert msg == "linkedin: missing agent_terms"


def test_agent_terms_takes_one_of_three_words(root):
    _linkedin(root, "manual", "maybe")
    (msg,) = _linkedin_errors(root)
    assert "agent_terms 'maybe'" in msg and all(w in msg for w in ("none", "names-agents", "gateway-only"))


@pytest.mark.parametrize("status", ["probation", "active"])
@pytest.mark.parametrize("agent_terms", ["names-agents", "gateway-only"])
def test_terms_that_name_agents_or_offer_only_a_gateway_are_never_probation_or_active(root, status, agent_terms):
    _linkedin(root, status, agent_terms)
    (msg,) = _linkedin_errors(root)
    assert msg.startswith("linkedin:") and agent_terms in msg and status in msg and NEVER_PROBATION in msg


def test_the_eval_regression_a_gateway_only_platform_on_probation_with_browser_access(root):
    """evalrun15/16 Swiftmatch: terms allow agents only through a credentials/auto-apply gateway."""
    _linkedin(root, "probation", "gateway-only", access="[browser]")
    assert any(NEVER_PROBATION in e for e in _linkedin_errors(root))


@pytest.mark.parametrize("status, agent_terms", [("manual", "names-agents"), ("blocked", "names-agents"),
                                                 ("manual", "gateway-only"), ("blocked", "gateway-only"),
                                                 ("probation", "none"), ("active", "none"),
                                                 ("manual", "none"), ("blocked", "none")])
def test_allowed_status_and_agent_terms_pairs_pass(root, status, agent_terms):
    _linkedin(root, status, agent_terms)
    assert _linkedin_errors(root) == []


# --------------------------------------------------- a proposed retirement keeps the ask to switch off its source
SOURCES = """\
sources:
  - id: greenhouse
    kind: ats-json
    enabled: true
  - id: linkedin-alert
    kind: email-alert
    enabled: {enabled}
"""
SWITCH_OFF = "ask job-scout to switch off linkedin-alert after ratification"


def _retired(root, marker="    proposed: {from: probation, date: 2026-12-03}\n", status="manual",
             enabled="true") -> list[str]:
    (root / "targets").mkdir(exist_ok=True)
    text = REGISTRY.replace("    status: probation\n", f"    status: {status}\n" + marker, 1)
    (root / "targets" / "platforms.yaml").write_text(text, encoding="utf-8")
    (root / "sources.yaml").write_text(SOURCES.format(enabled=enabled), encoding="utf-8")
    errors, warnings = py.check(root, date(2026, 12, 3))
    assert [e for e in errors if e.startswith("linkedin") and "adapter" not in e] == []
    return warnings


def test_a_proposed_retirement_with_its_alert_source_on_warns_to_ask_job_scout(root):
    (msg,) = [w for w in _retired(root) if "job-scout" in w]
    assert msg == f"linkedin: {SWITCH_OFF}"


def test_a_proposed_blocked_status_also_asks(root):
    assert any(SWITCH_OFF in w for w in _retired(root, status="blocked"))


@pytest.mark.parametrize("kwargs", [{"enabled": "false"},                                  # already off
                                    {"marker": ""},                                         # no proposal
                                    {"status": "active"}])                                  # a promotion
def test_no_switch_off_ask_otherwise(root, kwargs):
    assert not [w for w in _retired(root, **kwargs) if "job-scout" in w]


# ----------------------------------------------------------- the platform-scout eval fixtures stay check-clean
EVAL_FIXTURES = Path(__file__).resolve().parents[2] / "evals" / "platform-scout" / "fixtures"


@pytest.mark.parametrize("case", sorted(q.parent.parent.name for q in EVAL_FIXTURES.glob("*/targets/platforms.yaml")))
def test_every_platform_scout_eval_fixture_passes_the_check(tmp_path, case):
    """The eval sandbox copies scripts/ next to the fixture; each case starts from a clean --check."""
    sandbox = tmp_path / case
    shutil.copytree(EVAL_FIXTURES / case, sandbox)
    (sandbox / "scripts").symlink_to(jc.CODE_ROOT / "scripts", target_is_directory=True)
    errors, _ = py.check(sandbox, date(2026, 10, 13))
    assert errors == []
    for entry in fm.parse_yaml((sandbox / "targets" / "platforms.yaml").read_text(encoding="utf-8"))["platforms"]:
        assert entry["agent_terms"] in py.AGENT_TERMS


def test_the_committed_registry_carries_agent_terms_on_every_entry():
    for pid, entry in ja.load_platforms(JS_ROOT).items():
        assert entry.get("agent_terms") in py.AGENT_TERMS, pid          


@pytest.mark.parametrize("source", ["jibe", "sitemap", "amazon"])
def test_careers_site_vendors_count_toward_the_company_ats_platform(source):
    assert py.platform_of({"source": source}) == "company-ats"
    assert py.platform_of({"source": "greenhouse"}) == py.platform_of({"source": source})
