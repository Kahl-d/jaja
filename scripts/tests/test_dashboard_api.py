"""dashboard/dash_api.py, dash_tags.py and serve.py: the job agency dashboard's numbers on known data, its
writes through the agency's own scripts, and the local server's guards (127.0.0.1, host check, token, confirm)."""
from __future__ import annotations

import json
import socket
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest

from layout import JS  # noqa: E402
for p in (str(JS / "scripts"), str(JS / "dashboard")):
    if p not in sys.path:
        sys.path.insert(0, p)

import dash_api as da  # noqa: E402
import dash_tags  # noqa: E402
import serve  # noqa: E402
import tracker as tr  # noqa: E402
from js_common import fm  # noqa: E402

NOW = datetime(2026, 10, 5, 19, 0, tzinfo=timezone.utc)      # 12:00 PDT, department day 2026-10-05

CLOCK = """employer: Initech
employment_end: 2026-10-02
employment_end_confirmed: true
clock_days: 90
new_employment_start: null
visa_allowance_days: null
visa_allowance_source: null
phases:
  - {weeks: [1, 2], name: resumes and first wave}
  - {weeks: [3, 8], name: volume and interviews}
  - {weeks: [9, 11], name: finals and offers}
  - {weeks: [12, 13], name: negotiate and decide}
"""

COMPANIES = """companies:
  - {name: Acme AI, slug: acme, tier: dream, sponsorship_stance: sponsors}
  - {name: Beta, slug: beta, tier: good, segment: startup}
  - {name: Kappa Corp, slug: kappa, tier: strong}
  - {name: Omega, slug: omega, tier: dream}
"""

NEXT_UP = """---
title: NEXT-UP
---

# NEXT-UP

**Submitted so far (2):** Acme AI Engineer, Beta Agent Engineer.

## The list (top first)

| # | role | fit | why here | chance | posted | state | page | tier |
|---|---|---|---|---|---|---|---|---|
| 1 | Omega · AI Engineer, Platform | 4.6 | agents | sponsors | 2026-10-01 | READY | 01 | dream |
| 2 | Kappa Corp · ML Engineer | 4.1 (capped) | infra | | 2026-08-01 | PACKAGE (gated) | needs a page | focused |
| 2a | Zeta · Founding Engineer | 3.9 | founding | | old | BLOCKED: Alex's call | | mass |

**Waiting on a person or a date (not ranked):** Pat (Drew).

## Mass queue
(tier mass)
- delta-job
- not-a-job
"""


def _w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _job(js: Path, jid: str, **meta) -> None:
    base = {"id": jid, "company": "", "company_slug": "", "title": "", "status": "fit_checked",
            "first_seen": "2026-10-04T10:00:00Z", "posted_at": "2026-10-01", "sponsorship": "unknown"}
    base.update(meta)
    _w(js / "jobs" / f"{jid}.md", fm.dump(base, "# job\n"))


def _app(js: Path, slug: str, meta: dict, events: list[tuple[str, str]]) -> None:
    lines = "\n".join(tr.format_event(at, t, "manual", 1.0, "fixture") for at, t in events)
    _w(js / "applications" / slug / "application.md",
       fm.dump({"id": slug, "stage": "discovered", "created": "2026-09-20", "updated": "2026-10-01", **meta},
               f"# {slug}\n\n## Events\n{lines}\n"))


@pytest.fixture()
def tree(tmp_path):
    os_root = tmp_path / "workspace"
    js = os_root / "domains" / "career" / "projects" / "job-search"
    _w(js / "clock.yaml", CLOCK)
    _w(js / "targets" / "companies.yaml", COMPANIES)
    _w(js / "targets" / "segments" / "ai-lab.md", "---\nsegment: ai-lab\n---\n\n# Segment: AI labs (Acme AI, Zeta Labs ...)\n")
    _w(js / "targets" / "people-watch.yaml", "people:\n  - {name: Pat Lee, company: beta, role: referrer, why: refers, status: watch}\n")
    _w(js / "profile" / "lenses" / "ai-engineer.md",
       "---\nfamily: ai-engineer\ntitles_covered: ['AI Engineer*']\nstatus: draft\n---\n\n## 1. x\n")
    _w(js / "hub" / "NEXT-UP.md", NEXT_UP)
    _job(js, "acme-job", company="Acme AI", company_slug="acme", title="AI Engineer", fit_score=4.6,
         fit_verdict="priority", sponsorship="yes", first_seen="2026-10-05T08:00:00Z", posted_at="2026-10-04")
    _job(js, "beta-job", company="Beta", company_slug="beta", title="Agent Engineer", fit_score=4.2, fit_verdict="apply",
         first_seen="2026-09-20T08:00:00Z", posted_at="2026-09-20")
    _job(js, "delta-job", company="Delta", company_slug="delta", title="Data Wrangler", fit_score=3.6,
         fit_verdict="maybe", first_seen="2026-09-21T08:00:00Z", posted_at="2026-09-21")
    _job(js, "acme-dup", company="Acme AI", company_slug="acme", title="AI Engineer", duplicate_of="acme-job",
         posted_at="2026-10-04")
    _job(js, "kappa-job", company="Kappa Corp", company_slug="kappa", title="ML Engineer", status="skipped",
         first_seen="2026-09-25T08:00:00Z", posted_at="2026-09-25")
    # acme: applied this week, screen two days later; beta: applied last week, no reply; delta: not applied
    _app(js, "acme-ai-engineer-202610", {"job": "[[acme-job]]", "company": "Acme AI", "company_slug": "acme",
                                         "title": "AI Engineer"},
         [("2026-09-30T17:00:00Z", "fit_checked"), ("2026-10-01T17:00:00Z", "applied"), ("2026-10-03T17:00:00Z", "screen")])
    _app(js, "beta-agent-engineer-202609", {"job": "[[beta-job]]", "company": "Beta", "company_slug": "beta",
                                            "title": "Agent Engineer", "applied_via": "ats"},
         [("2026-09-23T17:00:00Z", "fit_checked"), ("2026-09-24T17:00:00Z", "applied")])
    _app(js, "delta-data-wrangler-202610", {"job": "[[delta-job]]", "company": "Delta", "company_slug": "delta",
                                            "title": "Data Wrangler"}, [("2026-10-04T17:00:00Z", "fit_checked")])
    _w(js / "applications" / "kappa-ml-engineer-202609" / "fit.md",
       fm.dump({"job": "[[kappa-job]]", "company": "Kappa Corp", "company_slug": "kappa", "title": "ML Engineer",
                "score": 4.1, "scored_at": "2026-09-30"}, "# fit\n"))
    _w(js / "state" / "package-only.yaml", "slugs:\n- kappa-ml-engineer-202609\n")
    _w(js / "prospects" / "pat-lee.md", fm.dump({"id": "pat-lee", "name": "Pat Lee", "company": "Beta",
                                                 "company_slug": "beta", "company_type": "startup", "role": "engineer",
                                                 "source": "referral", "status": "replied", "priority": "P1",
                                                 "warmth": 2, "applications": ["beta-agent-engineer-202609"]},
                                                "# Pat Lee\n\n## Timeline\n"))
    _w(js / "prospects" / "sam-roe.md", fm.dump({"id": "sam-roe", "name": "Sam Roe", "company": "Acme AI",
                                                 "company_slug": "acme", "role": "recruiter", "source": "gmail-backlog",
                                                 "status": "new", "priority": "P2", "warmth": 1},
                                                "# Sam Roe\n\n## Timeline\n"))
    _w(js / "touches" / "2026-10.jsonl", json.dumps({"at": "2026-10-02T10:00:00Z", "direction": "out", "status": "sent",
                                                    "prospect": "pat-lee", "channel": "email"}) + "\n")
    return os_root, js


@pytest.fixture()
def snap(tree):
    os_root, js = tree
    return da.build(js, os_root, NOW)


# ------------------------------------------------------------------------------------------------ overview numbers
def test_funnel_counts_and_rates(snap):
    f = {x["step"]: x for x in snap["overview"]["funnel"]}
    assert f["discovered"]["count"] == 4                     # 5 postings, one duplicate left out
    assert f["queued"]["count"] == 4                         # 3 application records + 1 package-only folder
    assert f["applied"]["count"] == 2
    assert f["replied"]["count"] == 1 and f["replied"]["rate_from_applied"] == 0.5
    assert f["screen"]["count"] == 1 and f["interview"]["count"] == 0 and f["offer"]["count"] == 0
    assert f["applied"]["rate_from_previous"] == 0.5


def test_segments_with_their_sources(snap):
    rows = {r["slug"]: r for r in snap["applications"]}
    assert (rows["acme-ai-engineer-202610"]["segment"], rows["acme-ai-engineer-202610"]["segment_source"]) == ("ai-lab", "segment lens")
    assert (rows["beta-agent-engineer-202609"]["segment"], rows["beta-agent-engineer-202609"]["segment_source"]) == ("startup", "companies.yaml")
    assert rows["kappa-ml-engineer-202609"]["segment"] == "unknown"
    seg = {b["value"]: b for b in snap["overview"]["by_segment"]}
    assert seg["ai-lab"]["applied"] == 1 and seg["startup"]["applied"] == 1
    assert seg["unknown"]["packages"] == 1 and seg["unknown"]["open"] == 1      # kappa package, delta open


def test_dream_coverage(snap):
    d = snap["overview"]["dream"]
    assert d["dream_companies"] == 2 and d["applied_companies"] == 1 and d["share_of_dream_applied"] == 0.5
    assert d["applications_at_dream"] == 1 and d["share_of_applications_at_dream"] == 0.5
    assert d["applied_names"] == ["acme"]


def test_tiers_rule_and_batch_sheet(tree):
    os_root, js = tree
    s = da.build(js, os_root, NOW)
    tiers = {r["slug"]: (r["tier"], r["tier_source"]) for r in s["applications"]}
    assert tiers["acme-ai-engineer-202610"] == ("dream", "rule")           # fit 4.6 and a dream company
    assert tiers["beta-agent-engineer-202609"] == ("focused", "rule")      # fit 4.2
    assert tiers["delta-data-wrangler-202610"] == ("mass", "rule")         # fit 3.6
    _w(js / "state" / "apply-batches" / "2026-10-04-1.md",
       fm.dump({"type": "apply-batch", "tier": "mass", "slugs": ["beta-agent-engineer-202609"]}, "# batch\n"))
    s = da.build(js, os_root, NOW)
    beta = next(r for r in s["applications"] if r["slug"] == "beta-agent-engineer-202609")
    assert (beta["tier"], beta["tier_source"]) == ("mass", "batch sheet")
    by_tier = {b["value"]: b for b in s["overview"]["by_tier"]}
    assert by_tier["mass"]["applied"] == 1 and by_tier["dream"]["applied"] == 1


def test_families_from_focus_profiles(snap):
    fam = {b["value"]: b for b in snap["overview"]["by_family"]}
    assert fam["ai-engineer"]["applied"] == 1
    assert fam["unmatched"]["applied"] == 1 and fam["unmatched"]["open"] == 1 and fam["unmatched"]["packages"] == 1


def test_speed_and_weeks(snap):
    sp = snap["overview"]["speed"]
    assert sp["applied"] == 2
    assert sp["reply"]["median_days"] == 2.0 and sp["reply"]["within_7"] == 1 and sp["reply"]["none_yet"] == 1
    assert sp["reply"]["oldest_wait_days"] == 11                             # beta: 2026-09-24 to 2026-10-05
    assert sp["interview"]["median_days"] == 2.0
    wk = {r["metric"]: r for r in snap["overview"]["weeks"]["rows"]}
    assert snap["overview"]["weeks"]["this_start"] == "2026-09-29"
    assert (wk["applied"]["this"], wk["applied"]["last"]) == (1, 1)
    assert (wk["replies"]["this"], wk["interviews"]["this"]) == (1, 1)
    assert (wk["discovered"]["this"], wk["discovered"]["last"]) == (1, 1)   # acme-job this week, kappa-job the week before
    assert wk["outreach_sent"]["this"] == 1


def test_company_moving_first_counts_as_a_reply_at_zero_days(tree):
    os_root, js = tree
    _app(js, "zeta-founding-engineer-202610", {"company": "Zeta", "company_slug": "zeta", "title": "Founding Engineer"},
         [("2026-10-02T16:00:00Z", "screen"), ("2026-10-02T18:00:00Z", "applied")])
    s = da.build(js, os_root, NOW)
    z = next(r for r in s["applications"] if r["slug"] == "zeta-founding-engineer-202610")
    assert z["days_to_reply"] == 0.0 and z["days_to_interview"] == 0.0
    f = {x["step"]: x["count"] for x in s["overview"]["funnel"]}
    assert f["replied"] == 2 and f["screen"] == 2


def test_people_attached_and_referrers(snap):
    beta = next(r for r in snap["applications"] if r["slug"] == "beta-agent-engineer-202609")
    assert [(p["id"], p["via"]) for p in beta["people"]] == [("pat-lee", "prospect")]
    P = snap["people"]
    pat = next(p for p in P["prospects"] if p["id"] == "pat-lee")
    assert pat["referrer"] == "people watch: referrer" or pat["referrer"] == "source: referral"
    assert pat["applications"] == ["beta-agent-engineer-202609"]
    assert P["counts"]["referrers"] == 1 and P["counts"]["watch"] == 1 and P["watch"][0]["prospect"] == "pat-lee"


def test_queue_from_next_up(snap):
    q = snap["queue"]
    assert [r["rank"] for r in q["rows"]] == ["1", "2", "2a"]
    one, two, three = q["rows"]
    assert (one["company"], one["role"], one["fit"], one["state"], one["tier"]) == ("Omega", "AI Engineer, Platform", 4.6, "READY", "dream")
    assert one["age_days"] == 4 and one["freshness"] == "fresh" and one["next"].startswith("apply")
    assert two["fit"] == 4.1 and two["state"] == "PACKAGE" and two["freshness"] == "old"
    assert three["state"] == "BLOCKED" and three["age_days"] is None
    assert [m["id"] for m in q["mass"]] == ["delta-job", "not-a-job"]
    assert q["mass"][0]["in_jobs"] and not q["mass"][1]["in_jobs"]
    assert "Waiting on a person or a date (not ranked)" in q["notes"]


def test_jobs_query_filters(snap):
    rows = snap["_jobs"]
    assert {r["id"] for r in rows if r["fresh"]} == {"acme-job", "acme-dup"}
    out = da.query_jobs(rows, {"fresh": "1"})
    assert [r["id"] for r in out["rows"]] == ["acme-job"]                   # the duplicate is hidden unless asked
    assert da.query_jobs(rows, {"min_fit": "4", "sort": "fit"})["total"] == 2
    assert [r["id"] for r in da.query_jobs(rows, {"company_tier": "dream"})["rows"]] == ["acme-job"]
    assert da.query_jobs(rows, {"sponsorship": "yes"})["total"] == 1
    assert da.query_jobs(rows, {"open": "1"})["total"] == 3                 # kappa is skipped
    assert da.query_jobs(rows, {"q": "wrangler"})["rows"][0]["id"] == "delta-job"


def test_obsidian_links(snap):
    acme = next(r for r in snap["applications"] if r["slug"] == "acme-ai-engineer-202610")
    assert acme["link"] == ("obsidian://open?vault=workspace&file=domains/career/projects/job-search/applications/"
                            "acme-ai-engineer-202610/application.md")
    assert snap["vault"] == "workspace"


def test_gaps_name_what_is_missing(snap):
    gaps = " | ".join(snap["overview"]["gaps"])
    assert "3 of 4 companies carry no `segment:`" in gaps
    assert "state/apply-batches/ has no sheets" in gaps


def test_absent_jobs_folder_is_not_zero(tree):
    os_root, js = tree
    import shutil
    shutil.rmtree(js / "jobs")
    s = da.build(js, os_root, NOW)
    f = {x["step"]: x["count"] for x in s["overview"]["funnel"]}
    assert f["discovered"] is None and s["jobs_summary"] is None and s["portals"] is None


# ------------------------------------------------------------------------------------------------ writes
def test_commands_are_the_agency_scripts(tree):
    _, js = tree
    root = ["--root", str(js.resolve())]
    assert da.command_for({"op": "stage", "slug": "beta-agent-engineer-202609", "type": "screen",
                           "evidence": "recruiter invite  2026-10-05\n"}, js) == \
        ["scripts/tracker.py", *root, "event", "beta-agent-engineer-202609", "screen", "--source", "manual",
         "--evidence", "recruiter invite 2026-10-05"]
    assert da.command_for({"op": "stage", "slug": "delta-data-wrangler-202610", "type": "applied", "evidence": "x",
                           "at": "2026-10-05T09:00:00-07:00", "via": "referral",
                           "resume": "resumes/bases/general/AlexExample_Resume.pdf"}, js)[-6:] == \
        ["--at", "2026-10-05T09:00:00-07:00", "--via", "referral",
         "--resume", "resumes/bases/general/AlexExample_Resume.pdf"]          # post-mortem change 3
    assert da.command_for({"op": "attach", "person": "sam-roe", "slug": "acme-ai-engineer-202610"}, js) == \
        ["scripts/prospects.py", *root, "set", "sam-roe", "applications+=acme-ai-engineer-202610"]
    assert da.command_for({"op": "detach", "person": "pat-lee", "slug": "beta-agent-engineer-202609"}, js)[-1] == \
        "applications-=beta-agent-engineer-202609"
    assert da.command_for({"op": "person-tag", "person": "pat-lee", "tag": "ghost-risk"}, js)[-2:] == ["--tag", "ghost-risk"]
    assert da.command_for({"op": "tag", "kind": "job", "id": "acme-job", "tag": "Warm-Path"}, js) == \
        ["dashboard/dash_tags.py", *root, "add", "job", "acme-job", "warm-path"]
    assert da.command_for({"op": "segment", "company": "kappa", "segment": "enterprise"}, js) == \
        ["scripts/segment_companies.py", *root, "set", "kappa", "enterprise", "--source", "owner"]
    shown = da.display(da.command_for({"op": "stage", "slug": "acme-ai-engineer-202610", "type": "note",
                                       "evidence": "Alex's call"}, js))
    assert shown.startswith("python3 scripts/tracker.py --root ") and "'Alex'\"'\"'s call'" in shown
    assert da.command_for({"op": "tag", "kind": "application", "id": "x", "tag": "t"}, da.JS_DEFAULT)[:2] == \
        ["dashboard/dash_tags.py", "add"]                                    # no --root for the scripts' own tree


@pytest.mark.parametrize("action, words", [
    ({"op": "stage", "slug": "acme-ai-engineer-202610", "type": "ghosted", "evidence": "x"}, "event type"),
    ({"op": "stage", "slug": "acme-ai-engineer-202610", "type": "screen", "evidence": "  "}, "evidence"),
    ({"op": "stage", "slug": "nope-202610", "type": "screen", "evidence": "x"}, "no folder"),
    ({"op": "stage", "slug": "acme-ai-engineer-202610", "type": "applied", "evidence": "x", "at": "yesterday"}, "ISO"),
    ({"op": "attach", "person": "nobody", "slug": "acme-ai-engineer-202610"}, "no prospect"),
    ({"op": "attach", "person": "../etc", "slug": "acme-ai-engineer-202610"}, "not a record id"),
    ({"op": "segment", "company": "kappa", "segment": "giant"}, "segment must be"),
    ({"op": "send", "to": "x"}, "unknown write"),
])
def test_refusals(tree, action, words):
    _, js = tree
    with pytest.raises(da.WriteRefused, match=words):
        da.command_for(action, js)


def test_tag_and_segment_writes_rebuild(tree):
    os_root, js = tree
    res = da.run_write({"op": "tag", "kind": "application", "id": "acme-ai-engineer-202610", "tag": "warm-path"}, js)
    assert res["ok"], res
    res = da.run_write({"op": "segment", "company": "kappa", "segment": "enterprise"}, js)
    assert res["ok"], res
    bad = da.run_write({"op": "tag", "kind": "job", "id": "no-such-job", "tag": "x"}, js)
    assert not bad["ok"] and bad["exit"] == 2 and "no record" in bad["stderr"]
    assert dash_tags.load(js)["tags"]["application"] == {"acme-ai-engineer-202610": ["warm-path"]}
    s = da.build(js, os_root, NOW)
    rows = {r["slug"]: r for r in s["applications"]}
    assert rows["acme-ai-engineer-202610"]["tags"] == ["warm-path"]
    assert (rows["kappa-ml-engineer-202609"]["segment"], rows["kappa-ml-engineer-202609"]["segment_source"]) == \
        ("enterprise", "companies.yaml")                                      # one source: targets/companies.yaml
    assert "segment_source: owner" in (js / "targets" / "companies.yaml").read_text()
    log = (js / "state" / "dashboard-writes.jsonl").read_text().splitlines()
    assert len(log) == 3 and json.loads(log[-1])["exit"] == 2
    assert len((js / "state" / "dashboard-tags.log.jsonl").read_text().splitlines()) == 1   # the tag; segments live in companies.yaml


def test_stage_and_attach_run_the_scripts(tree):
    os_root, js = tree
    res = da.run_write({"op": "stage", "slug": "beta-agent-engineer-202609", "type": "screen",
                        "evidence": "recruiter screen invite, fixture", "at": "2026-10-05T16:00:00Z"}, js)
    assert res["ok"], res
    assert "stage screen" in res["stdout"]
    res = da.run_write({"op": "attach", "person": "sam-roe", "slug": "acme-ai-engineer-202610"}, js)
    assert res["ok"], res
    s = da.build(js, os_root, NOW)
    rows = {r["slug"]: r for r in s["applications"]}
    assert rows["beta-agent-engineer-202609"]["stage"] == "screen"
    assert [p["id"] for p in rows["acme-ai-engineer-202610"]["people"]] == ["sam-roe"]


def test_tags_cli_refuses_bad_input(tree):
    _, js = tree
    assert dash_tags.main(["--root", str(js), "add", "application", "acme-ai-engineer-202610", "Has Space"]) == 2
    assert dash_tags.main(["--root", str(js), "remove", "application", "acme-ai-engineer-202610", "never-set"]) == 2
    assert not (js / dash_tags.TAGS_FILE).exists()


# ------------------------------------------------------------------------------------------------ the server
def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def server(tree):
    os_root, js = tree
    port = _free_port()
    cache = serve.Cache(js, os_root)
    httpd = serve.ThreadingHTTPServer(("127.0.0.1", port), serve.make_handler(cache, "tok", port))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}", js
    httpd.shutdown()
    httpd.server_close()


def _req(url, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={**({"Content-Type": "application/json"} if body is not None else {}),
                                                        **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_server_serves_and_guards_writes(server):
    base, js = server
    code, body = _req(base + "/")
    assert code == 200 and b'content="tok"' in body
    code, body = _req(base + "/api/snapshot")
    snap = json.loads(body)
    assert code == 200 and "_jobs" not in snap and len(snap["applications"]) == 4
    code, body = _req(base + "/api/jobs?min_fit=4")
    assert json.loads(body)["total"] == 2
    action = {"op": "tag", "kind": "job", "id": "acme-job", "tag": "warm-path"}
    assert _req(base + "/api/write", {"action": action, "confirm": True})[0] == 403          # no token
    assert _req(base + "/api/write", {"action": action}, {"X-Dashboard-Token": "tok"})[0] == 400  # no confirm
    assert _req(base + "/api/snapshot", headers={"Host": "attacker.example"})[0] == 403
    code, body = _req(base + "/api/preview", {"action": action}, {"X-Dashboard-Token": "tok"})
    assert code == 200 and json.loads(body)["command"].startswith("python3 dashboard/dash_tags.py --root ")
    assert not (js / dash_tags.TAGS_FILE).exists()                                         # preview runs nothing
    code, body = _req(base + "/api/write", {"action": action, "confirm": True}, {"X-Dashboard-Token": "tok"})
    assert code == 200 and json.loads(body)["ok"]
    snap = json.loads(_req(base + "/api/snapshot")[1])                                      # rebuilt on the change
    code, body = _req(base + "/api/jobs?q=warm-path")
    assert json.loads(body)["rows"][0]["tags"] == ["warm-path"]
    code, body = _req(base + "/api/preview", {"action": {"op": "send"}}, {"X-Dashboard-Token": "tok"})
    assert code == 400


def test_server_binds_loopback_only():
    src = (JS / "dashboard" / "serve.py").read_text(encoding="utf-8")
    assert 'ThreadingHTTPServer(("127.0.0.1", args.port)' in src


def test_no_em_dashes_in_the_dashboard():
    for p in sorted((JS / "dashboard").iterdir()):
        if p.is_file() and p.suffix in (".py", ".js", ".css", ".html", ".md"):
            assert chr(0x2014) not in p.read_text(encoding="utf-8"), p.name


def test_duplicate_application_records_are_a_gap(tree):
    os_root, js = tree
    _app(js, "acme-ai-engineer-202609", {"company": "Acme AI", "company_slug": "acme", "title": "AI Engineer"},
         [("2026-09-28T17:00:00Z", "fit_checked")])
    gaps = " | ".join(da.build(js, os_root, NOW)["overview"]["gaps"])
    assert "1 roles have more than one application record" in gaps and "acme: ai engineer" in gaps


def test_a_recorded_tier_wins_and_an_inferred_backfill_says_so():
    """R-S2: the tier recorded with tracker.py tier comes first; an inferred backfill is labelled, never 'recorded'."""
    assert da.tier_for("s", 3.2, None, {"s": "mass"}, "dream", "tier rule: dream board") == ("dream", "recorded")
    assert da.tier_for("s", 4.1, None, {}, "focused", "inferred tier rule, backfill") == (
        "focused", "inferred (backfill)")
    assert da.tier_for("s", 4.1, None, {}, None, None) == ("focused", "rule")
