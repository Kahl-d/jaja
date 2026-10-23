"""tracker.py: append-only application event log, stage projection (overrides win), ghost flags,
`new` (application folder from a job), `metrics`."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)
from disc_harness import NOW

import fetch_ats as fa  # noqa: E402
import tracker as tr  # noqa: E402
from js_common import OS_ROOT, fm  # noqa: E402

NO_PDF = ["--resume", "none", "--reason", "fixture: no PDF in this test"]   # an applied event names its page



def ts(days_ago: float, now=NOW) -> str:
    return (now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def ev(days_ago, type_, source="email", conf=0.9, evidence="x"):
    return tr.Event(ts(days_ago), type_, source, conf, evidence)


def stage(events, **kw):
    return tr.project(events, NOW, **kw)["stage"]


# ----------------------------------------------------------------------------- the log line
def test_event_line_format_is_exact_and_single_line():
    line = tr.format_event("2026-09-24T13:00:00Z", "applied", "manual", 1.0, "The owner confirmed\nin chat")
    assert line == "- 2026-09-24T13:00:00Z · applied · manual · conf=1.00 · The owner confirmed in chat"


def test_blank_evidence_still_round_trips():
    line = tr.format_event("2026-09-24T13:00:00Z", "applied", "manual", 1.0, "")
    assert line == "- 2026-09-24T13:00:00Z · applied · manual · conf=1.00 · -"
    assert tr.parse_events("## Events\n" + line.rstrip() + "\n")[0].type == "applied"
    assert tr.parse_events("## Events\n- 2026-09-24T13:00:00Z · applied · manual · conf=1.00 ·\n")[0].evidence == ""


def test_parse_events_round_trips_and_ignores_other_lines():
    body = ("# X\n\n## Events\n"
            "- 2026-09-01T10:00:00Z · applied · manual · conf=1.00 · via Greenhouse\n"
            "some stray text\n"
            "- 2026-09-03T08:00:00Z · screen · email · conf=0.95 · Recruiter: let's talk · Tuesday?\n\n## Notes\n")
    events = tr.parse_events(body)
    assert [(e.at, e.type, e.source, e.conf) for e in events] == [
        ("2026-09-01T10:00:00Z", "applied", "manual", 1.0), ("2026-09-03T08:00:00Z", "screen", "email", 0.95)]
    assert events[1].evidence == "Recruiter: let's talk · Tuesday?"


def test_stage_enum_matches_the_schema():
    schema = fm.parse_yaml((__import__("agency_paths").SCHEMAS_DIR / "application.yaml").read_text())
    assert tr.STAGES == schema["enums"]["stage"]


# ----------------------------------------------------------------------------- projection
def test_simple_progression():
    assert stage([ev(10, "discovered"), ev(9, "applied"), ev(8, "confirmation_received"),
                  ev(3, "recruiter_screen")]) == "screen"


def test_events_are_ordered_by_time_not_by_line():
    assert stage([ev(1, "interview"), ev(20, "applied"), ev(5, "assessment")]) == "interviewing"
    assert stage([ev(1, "interview"), ev(20, "applied"), ev(5, "rejected")]) == "interviewing"
    assert stage([ev(5, "override:screen", "manual"), ev(20, "applied"), ev(10, "offer")]) == "screen"


def test_rejection_is_terminal_until_a_later_progression_event():
    assert stage([ev(10, "applied"), ev(5, "rejected")]) == "rejected"
    assert stage([ev(10, "applied"), ev(5, "rejected"), ev(2, "interview")]) == "interviewing"


def test_override_wins_over_earlier_misfires_and_later_lower_events():
    events = [ev(20, "applied"), ev(10, "rejected"), ev(9, "override:interviewing", "manual", 1.0),
              ev(4, "confirmation_received")]
    assert stage(events) == "interviewing"


def test_override_can_move_the_stage_backwards():
    events = [ev(20, "applied"), ev(10, "offer"), ev(9, "override:screen", "manual", 1.0)]
    assert stage(events) == "screen"


def test_on_hold_pauses_until_progress():
    assert stage([ev(20, "applied"), ev(10, "on_hold")]) == "on_hold"
    assert stage([ev(20, "applied"), ev(10, "on_hold"), ev(2, "screen")]) == "screen"


@pytest.mark.parametrize("events,expected", [
    ([ev(89, "applied")], "applied"),
    ([ev(90, "applied")], "ghosted"),
    ([ev(100, "applied"), ev(59, "interview")], "interviewing"),
    ([ev(100, "applied"), ev(60, "interview")], "ghosted"),
    ([ev(100, "applied"), ev(44, "final_round")], "final"),
    ([ev(100, "applied"), ev(45, "final_round")], "ghosted"),
    ([ev(300, "applied"), ev(200, "offer")], "offer"),
    ([ev(100, "applied"), ev(10, "follow_up", "manual")], "ghosted"),   # our own nudge is not their reply
    ([ev(100, "applied"), ev(10, "email_in")], "applied"),               # their reply resets the clock
])
def test_ghost_thresholds_by_stage(events, expected):
    assert stage(events) == expected


def test_ghost_after_days_and_last_inbound_override_the_defaults():
    assert stage([ev(31, "applied")], ghost_after_days=30) == "ghosted"
    assert stage([ev(120, "applied")], last_inbound_at=ts(3)) == "applied"
    out = tr.project([ev(120, "applied")], NOW)
    assert out["ghosted"] is True and out["ghost_from"] == "applied"


# ----------------------------------------------------------------------------- new / event CLI
def _job(root, **kw):
    fields = dict(title="Applied AI Engineer", source="greenhouse",
                  source_url="https://job-boards.greenhouse.io/acme/jobs/1", apply_url=None,
                  ats_vendor="greenhouse", posted_at="2026-09-22T09:00:00Z", location="San Francisco, CA",
                  remote="onsite", comp=(200000, 250000, "USD"), employment_type="full-time",
                  jd_text="Build agents. Sponsorship: we do sponsor visas!", now=NOW, req_id="1234")
    fields.update(kw)
    rec = fa.make_record({"name": "Acme", "slug": "acme"}, **fields)
    fa.upsert_jobs([rec], root, NOW)
    return rec["id"]


def test_new_application_takes_the_dossier_slug_and_keeps_the_folder_id(root):
    # REQ-9: the application's company_slug is the join key (the dossier's); its folder name is an id, from the job
    (root / "companies").mkdir()
    (root / "companies" / "acme.md").write_text('---\ntype: company\nslug: acme-inc\nname: Acme\n---\n')
    job_id = _job(root)
    assert tr.main(["--root", str(root), "new", "--job", job_id], now=NOW) == 0
    props, _ = fm.load(root / "applications" / "acme-applied-ai-engineer-202609" / "application.md")
    assert props["company_slug"] == "acme-inc"


def test_new_creates_the_application_folder(root):
    job_id = _job(root)
    assert tr.main(["--root", str(root), "new", "--job", job_id], now=NOW) == 0
    slug = "acme-applied-ai-engineer-202609"
    folder = root / "applications" / slug
    assert sorted(p.name for p in folder.iterdir()) == ["application.md", "fit.md", "jd.md", "outcome.md"]
    props, body = fm.load(folder / "application.md")
    for key in ("id", "job", "company", "title", "stage", "created", "updated"):
        assert props.get(key) not in (None, ""), key
    assert props["id"] == slug and props["job"] == f"[[{job_id}]]" and props["stage"] == "discovered"
    assert props["outcome"] == "pending"
    assert tr.parse_events(body)[0].type == "discovered"
    fit_text = (folder / "fit.md").read_text()
    for heading in ("## Hard gates", "## Strongest matches", "## Biggest gaps", "## Sponsorship evidence",
                    "## Score and verdict"):
        assert heading in fit_text
    assert "<untrusted_jd>" in (folder / "jd.md").read_text()
    job = fa.load_jobs(root)[job_id]
    assert job["application"] == f"[[{slug}]]" and job["status"] == "queued"


@pytest.mark.parametrize("status", ["fit_checked", "queued", "tailoring"])
def test_new_starts_at_fit_checked_once_the_job_was_scored(root, status):
    # fit-scorer sets the job status (queued for a priority score) before it calls `new`.
    from js_common import write_record
    job_id = _job(root)
    path = root / "jobs" / f"{job_id}.md"
    props, body = fm.load(path)
    props["status"] = status
    write_record(path, props, body)
    assert tr.main(["--root", str(root), "new", "--job", job_id], now=NOW) == 0
    props, body = fm.load(root / "applications" / "acme-applied-ai-engineer-202609" / "application.md")
    assert props["stage"] == "fit_checked" and tr.parse_events(body)[0].type == "fit_checked"


def test_new_reuses_the_folder_fit_scorer_prepared_and_keeps_its_files(root):
    from js_common import write_record
    job_id = _job(root)
    folder = root / "applications" / "acme-applied-ai-engineer-202609"
    write_record(folder / "jd.md", {"job": f"[[{job_id}]]"}, "# authored jd\n")
    write_record(folder / "fit.md", {"job": f"[[{job_id}]]", "fit_score": 4.7}, "# authored fit\n")
    assert tr.main(["--root", str(root), "new", "--job", job_id], now=NOW) == 0
    assert not (root / "applications" / "acme-applied-ai-engineer-202609-2").exists()
    assert "authored fit" in (folder / "fit.md").read_text() and "authored jd" in (folder / "jd.md").read_text()
    assert (folder / "application.md").is_file()


def test_new_still_avoids_a_folder_prepared_for_another_job(root):
    from js_common import write_record
    job_id = _job(root)
    folder = root / "applications" / "acme-applied-ai-engineer-202609"
    write_record(folder / "jd.md", {"job": "[[some-other-job]]"}, "# other\n")
    assert tr.main(["--root", str(root), "new", "--job", job_id], now=NOW) == 0
    assert (root / "applications" / "acme-applied-ai-engineer-202609-2" / "application.md").is_file()


def test_new_is_idempotent_and_avoids_slug_collisions(root):
    first = _job(root)
    tr.main(["--root", str(root), "new", "--job", first], now=NOW)
    tr.main(["--root", str(root), "new", "--job", first], now=NOW)
    assert len(list((root / "applications").iterdir())) == 1
    second = _job(root, source_url="https://job-boards.greenhouse.io/acme/jobs/2", req_id="5678")
    tr.main(["--root", str(root), "new", "--job", second], now=NOW)
    assert sorted(p.name for p in (root / "applications").iterdir()) == [
        "acme-applied-ai-engineer-202609", "acme-applied-ai-engineer-202609-2"]


def test_new_dry_run_writes_nothing(root, capsys):
    job_id = _job(root)
    assert tr.main(["--root", str(root), "new", "--job", job_id, "--dry-run"], now=NOW) == 0
    assert not (root / "applications").exists()
    assert "acme-applied-ai-engineer-202609" in capsys.readouterr().out


def test_event_subcommand_appends_and_reprojects(root):
    job_id = _job(root)
    tr.main(["--root", str(root), "new", "--job", job_id], now=NOW)
    slug = "acme-applied-ai-engineer-202609"
    later = NOW + timedelta(days=1)
    assert tr.main(["--root", str(root), "event", slug, "applied", *NO_PDF, "--evidence", "The owner applied via Greenhouse"],
                   now=later) == 0
    props, body = fm.load(root / "applications" / slug / "application.md")
    assert props["stage"] == "applied" and str(props["applied_at"]) == "2026-09-25"
    assert body.count(" · ") == 8                     # two event lines, four separators each
    tr.main(["--root", str(root), "event", slug, "override:screen", "--evidence", "recruiter call booked"],
            now=later + timedelta(hours=1))
    props, _ = fm.load(root / "applications" / slug / "application.md")
    assert props["stage"] == "screen"
    job = fa.load_jobs(root)[job_id]
    assert job["status"] == "applied"


def test_event_can_record_how_it_was_applied(root):
    job_id = _job(root)
    tr.main(["--root", str(root), "new", "--job", job_id], now=NOW)
    slug = "acme-applied-ai-engineer-202609"
    tr.main(["--root", str(root), "event", slug, "applied", *NO_PDF, "--via", "referral"], now=NOW)
    props, _ = fm.load(root / "applications" / slug / "application.md")
    assert props["applied_via"] == "referral"
    with pytest.raises(SystemExit):
        tr.main(["--root", str(root), "event", slug, "applied", *NO_PDF, "--via", "carrier-pigeon"], now=NOW)


def test_project_subcommand_writes_inferred_ghosting(root):
    job_id = _job(root)
    tr.main(["--root", str(root), "new", "--job", job_id], now=NOW)
    slug = "acme-applied-ai-engineer-202609"
    tr.main(["--root", str(root), "event", slug, "applied", *NO_PDF], now=NOW)
    assert tr.main(["--root", str(root), "project"], now=NOW + timedelta(days=91)) == 0
    props, _ = fm.load(root / "applications" / slug / "application.md")
    assert props["stage"] == "ghosted" and props["outcome"] == "ghosted"


# ----------------------------------------------------------------------------- metrics
def _app(root, slug, job_source, applied_via, events, posted_at="2026-08-01T00:00:00Z"):
    job_id = _job(root, source=job_source, source_url=f"https://example.com/{slug}", req_id=slug,
                  posted_at=posted_at)
    folder = root / "applications" / slug
    folder.mkdir(parents=True)
    body = "# app\n\n## Events\n" + "".join(tr.format_event(at, t, "manual", 1.0, "e") + "\n" for at, t in events)
    props = {"id": slug, "job": f"[[{job_id}]]", "company": "Acme", "title": "t", "stage": "discovered",
            "applied_via": applied_via, "created": "2026-08-01", "updated": "2026-08-01"}
    (folder / "application.md").write_text(fm.dump(props, body))


def test_metrics(root):
    _app(root, "a1", "greenhouse", "ats", [("2026-08-02T00:00:00Z", "applied"), ("2026-08-06T00:00:00Z", "screen"),
                                           ("2026-08-08T00:00:00Z", "confirmation_received"),  # late, lower
                                           ("2026-08-16T00:00:00Z", "interview")])           # latency 24 h
    _app(root, "a2", "greenhouse", "referral", [("2026-08-05T00:00:00Z", "applied"),
                                                ("2026-08-07T00:00:00Z", "rejected")])        # latency 96 h
    _app(root, "a3", "hn", "ats", [("2026-08-01T12:00:00Z", "applied")])                    # latency 12 h
    _app(root, "a4", "adzuna", "ats", [("2026-08-01T00:00:00Z", "discovered")])              # never applied
    m = tr.metrics(root, NOW)
    assert m["applications"] == 4
    assert m["funnel"]["discovered"] == 4 and m["funnel"]["applied"] == 3
    assert m["funnel"]["screen"] == 1 and m["funnel"]["interviewing"] == 1 and m["funnel"]["offer"] == 0
    assert m["outcomes"]["rejected"] == 1
    assert m["response_rate_by_source"] == {"greenhouse": {"applied": 2, "responded": 2, "rate": 1.0},
                                            "hn": {"applied": 1, "responded": 0, "rate": 0.0}}
    assert m["response_rate_by_applied_via"]["ats"] == {"applied": 2, "responded": 1, "rate": 0.5}
    assert m["median_days_in_stage"]["applied"] == 3.0          # a1: 4 days to screen, a2: 2 days to rejected
    assert m["median_days_in_stage"]["screen"] == 10.0
    assert m["apply_latency_hours"] == {"median": 24.0, "within_72h": 2, "measured": 3}


def test_apply_latency_ignores_impossible_negative_values(root):
    _app(root, "b1", "greenhouse", "ats", [("2026-08-01T00:00:00Z", "applied")], posted_at="2026-08-03T00:00:00Z")
    assert tr.metrics(root, NOW)["apply_latency_hours"] == {"median": None, "within_72h": 0, "measured": 0}


def test_metrics_on_an_empty_department(root):
    m = tr.metrics(root, NOW)
    assert m["applications"] == 0 and m["apply_latency_hours"]["measured"] == 0


# ----------------------------------------------------------------------------- --today (eval defect, 2026-09-25)
@pytest.fixture
def clock_2030(monkeypatch):
    """The wall clock says 2030-01-01; with --today every computation must use that day instead."""
    from datetime import datetime, timezone
    monkeypatch.setattr(tr, "wall_clock", lambda: datetime(2030, 1, 1, 9, 0, tzinfo=timezone.utc), raising=False)


def test_project_today_drives_ghosting_and_the_updated_date(root, clock_2030, capsys):
    _app(root, "g1", "greenhouse", "ats", [("2026-06-01T10:00:00Z", "applied")])
    path = root / "applications" / "g1" / "application.md"
    assert tr.main(["--root", str(root), "project", "--today", "2026-07-01"]) == 0
    props, _ = fm.load(path)
    assert (props["stage"], str(props["updated"])) == ("applied", "2026-07-01")
    assert tr.main(["--root", str(root), "--today", "2026-09-10", "project"]) == 0      # global position too
    props, _ = fm.load(path)
    assert (props["stage"], props["outcome"], str(props["updated"])) == ("ghosted", "ghosted", "2026-09-10")


def test_project_without_today_reads_the_wall_clock(root, clock_2030):
    _app(root, "g2", "greenhouse", "ats", [("2029-12-01T10:00:00Z", "applied")])
    assert tr.main(["--root", str(root), "project"]) == 0
    props, _ = fm.load(root / "applications" / "g2" / "application.md")
    assert (props["stage"], str(props["updated"])) == ("applied", "2030-01-01")


def test_metrics_today_sets_generated_at_and_the_ghost_count(root, clock_2030, capsys):
    import json
    _app(root, "g3", "greenhouse", "ats", [("2026-06-01T10:00:00Z", "applied")])
    assert tr.main(["--root", str(root), "metrics", "--json", "--today", "2026-07-01"]) == 0
    early = json.loads(capsys.readouterr().out)
    assert early["generated_at"].startswith("2026-07-01") and early["outcomes"]["ghosted"] == 0
    assert tr.main(["--root", str(root), "metrics", "--json", "--today", "2026-09-10"]) == 0
    assert json.loads(capsys.readouterr().out)["outcomes"]["ghosted"] == 1


def test_event_without_at_is_stamped_on_today(root, clock_2030):
    _app(root, "g4", "greenhouse", "ats", [("2026-06-01T10:00:00Z", "discovered")])
    assert tr.main(["--root", str(root), "event", "g4", "applied", *NO_PDF, "--today", "2026-07-01"]) == 0
    props, body = fm.load(root / "applications" / "g4" / "application.md")
    assert tr.parse_events(body)[-1].at.startswith("2026-07-01")
    assert str(props["applied_at"]) == "2026-07-01" and str(props["updated"]) == "2026-07-01"


def test_today_rejects_a_malformed_date(root):
    with pytest.raises(SystemExit):
        tr.main(["--root", str(root), "project", "--today", "July 1"])


# ----------------------------------------------------------------------------- unparseable lines (eval defect, 2026-09-25)
def _raw_app(root, slug, stage, event_lines):
    folder = root / "applications" / slug
    folder.mkdir(parents=True)
    # type: first, as every write stamps it (graph_links.py); a legacy record gains the line on its first write
    props = {"type": "application", "id": slug, "job": "[[none]]", "company": "Example Robotics", "title": "ML Engineer", "stage": stage,
            "created": "2026-09-01", "updated": "2026-09-20"}
    body = "# app\n\n## Events\n" + "".join(line + "\n" for line in event_lines) + "\n## Notes\n\n- a note, not an event\n"
    (folder / "application.md").write_text(fm.dump(props, body))
    return folder / "application.md"


BAD = "- 2026-09-20T17:00:00Z · screen · email · recruiter call booked"          # no conf= field


def _bad_line_no(path):
    return next(i for i, line in enumerate(path.read_text().splitlines(), 1) if line == BAD)


def test_project_refuses_to_move_a_stage_backwards_over_unparsed_lines(root, capsys):
    path = _raw_app(root, "h1", "screen", [tr.format_event("2026-09-01T10:00:00Z", "discovered", "manual", 1.0, "x"),
                                           BAD])
    before = path.read_bytes()
    assert tr.main(["--root", str(root), "project", "--today", "2026-09-25"]) != 0
    out = capsys.readouterr()
    assert path.read_bytes() == before                                     # stage screen kept, file untouched
    assert f"applications/h1/application.md:{_bad_line_no(path)}" in out.out + out.err
    assert "screen" in out.out and "discovered" in out.out and "--force" in out.out
    assert tr.main(["--root", str(root), "project", "--today", "2026-09-25", "--force"]) == 0
    assert fm.load(path)[0]["stage"] == "discovered"


def test_project_still_moves_forward_and_warns_when_lines_are_unparsed(root, capsys):
    path = _raw_app(root, "h2", "discovered", [tr.format_event("2026-09-10T10:00:00Z", "applied", "manual", 1.0, "x"),
                                               BAD])
    where = f"applications/h2/application.md:{_bad_line_no(path)}"          # before project rewrites the header
    assert tr.main(["--root", str(root), "project", "--today", "2026-09-25"]) == 0
    err = capsys.readouterr().err
    assert fm.load(path)[0]["stage"] == "applied"
    assert f"{where}: unparseable event line" in err


def test_every_command_reports_unparsed_lines(root, capsys):
    import json
    path = _raw_app(root, "h3", "applied", [tr.format_event("2026-09-10T10:00:00Z", "applied", "manual", 1.0, "x"), BAD])
    where = f"applications/h3/application.md:{_bad_line_no(path)}"
    assert tr.main(["--root", str(root), "metrics", "--today", "2026-09-25"]) == 0
    assert where in capsys.readouterr().err
    assert tr.main(["--root", str(root), "metrics", "--json", "--today", "2026-09-25"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [w["where"] for w in data["warnings"]] == [where] and data["warnings"][0]["line"] == BAD
    assert tr.main(["--root", str(root), "event", "h3", "screen", "--today", "2026-09-25"]) == 0
    assert where in capsys.readouterr().err


def test_clean_logs_raise_no_warnings_and_overrides_still_move_backwards(root, capsys):
    path = _raw_app(root, "h4", "screen", [tr.format_event("2026-09-10T10:00:00Z", "screen", "email", 0.9, "x"),
                                           tr.format_event("2026-09-11T10:00:00Z", "override:applied", "manual", 1.0,
                                                           "Owner: screen was a mix-up")])
    assert tr.main(["--root", str(root), "project", "--today", "2026-09-25"]) == 0
    assert fm.load(path)[0]["stage"] == "applied" and "warning" not in capsys.readouterr().err


# ----------------------------------------------------------------------------- ghosted is inferred (2026-09-25)
@pytest.mark.parametrize("type_", ["ghosted", "Ghosted", "override:ghosted"])
def test_event_refuses_ghosted_without_an_override(root, capsys, type_):
    _app(root, "gh1", "greenhouse", "ats", [("2026-06-01T10:00:00Z", "applied")])
    path = root / "applications" / "gh1" / "application.md"
    before = path.read_bytes()
    assert tr.main(["--root", str(root), "event", "gh1", type_, "--today", "2026-07-01"]) == 2
    out = capsys.readouterr()
    text = out.out + out.err
    assert "inferred" in text and "tracker.py project" in text and "--override" in text
    assert path.read_bytes() == before


def test_event_ghosted_with_override_records_the_owners_words_in_the_evidence(root, capsys):
    _app(root, "gh2", "greenhouse", "ats", [("2026-06-01T10:00:00Z", "applied")])
    words = "Owner, 2026-07-01: they went silent after the screen, count it as ghosted"
    assert tr.main(["--root", str(root), "event", "gh2", "ghosted", "--today", "2026-07-01",
                    "--evidence", "no reply to two follow-ups", "--override", words]) == 0
    props, body = fm.load(root / "applications" / "gh2" / "application.md")
    last = tr.parse_events(body)[-1]
    assert last.type == "ghosted" and f"override: {words}" in last.evidence
    assert "no reply to two follow-ups" in last.evidence and props["stage"] == "ghosted"


def test_event_override_flag_needs_words(root, capsys):
    _app(root, "gh3", "greenhouse", "ats", [("2026-06-01T10:00:00Z", "applied")])
    assert tr.main(["--root", str(root), "event", "gh3", "ghosted", "--override", "  "]) == 2


def test_append_event_refuses_ghosted_for_library_callers_too(root):
    _app(root, "gh4", "greenhouse", "ats", [("2026-06-01T10:00:00Z", "applied")])
    with pytest.raises(tr.TrackerError):
        tr.append_event(root, "gh4", "ghosted", now=NOW)


def test_event_help_says_ghosting_is_inferred(capsys):
    with pytest.raises(SystemExit):
        tr.main(["event", "--help"])
    assert "--override" in capsys.readouterr().out

# ----------------------------------------------------------------------------- updated follows the run date (2026-09-25)
def test_event_sets_updated_to_the_run_date(root, clock_2030):
    _app(root, "u1", "greenhouse", "ats", [("2026-08-02T10:00:00Z", "applied")])
    assert tr.main(["--root", str(root), "event", "u1", "screen", "--today", "2026-09-28"]) == 0
    assert str(fm.load(root / "applications" / "u1" / "application.md")[0]["updated"]) == "2026-09-28"


def test_event_held_by_unparsed_lines_still_bumps_updated(root, capsys):
    path = _raw_app(root, "u2", "screen", [tr.format_event("2026-09-01T10:00:00Z", "discovered", "manual", 1.0, "x"),
                                           BAD])
    assert tr.main(["--root", str(root), "event", "u2", "note", "--evidence", "called Dana",
                    "--today", "2026-09-26"]) == 3
    props, body = fm.load(path)
    assert props["stage"] == "screen" and str(props["updated"]) == "2026-09-26"
    assert tr.parse_events(body)[-1].type == "note"


def test_new_sets_created_and_updated_to_the_run_date(root, clock_2030):
    job_id = _job(root)
    assert tr.main(["--root", str(root), "new", "--job", job_id, "--today", "2026-09-27"]) == 0
    folder = next((root / "applications").iterdir())
    props, _ = fm.load(folder / "application.md")
    assert str(props["created"]) == str(props["updated"]) == "2026-09-27"


# ----------------------------------------------------------------------------- next action follows the projected stage
APP = "acme-applied-ai-engineer-202609"


def _next(root, slug=APP):
    props, _ = fm.load(root / "applications" / slug / "application.md")
    at = props.get("next_action_at")
    return props.get("next_action"), (str(at) if at else None)


def _event(root, type_, when, slug=APP):
    pdf = NO_PDF if tr.EVENT_STAGE.get(type_) == "applied" else []
    assert tr.main(["--root", str(root), "event", slug, type_, *pdf, "--at", when.strftime("%Y-%m-%dT%H:%M:%SZ")],
                   now=when) == 0


def test_new_gives_the_first_stage_its_next_action(root):
    tr.main(["--root", str(root), "new", "--job", _job(root)], now=NOW)
    assert _next(root) == ("score fit (fit-scorer)", "2026-09-24")


def test_new_at_fit_checked_asks_for_tailoring_not_scoring(root):
    from js_common import write_record
    job_id = _job(root)
    path = root / "jobs" / f"{job_id}.md"
    props, body = fm.load(path)
    props["status"] = "queued"
    write_record(path, props, body)
    tr.main(["--root", str(root), "new", "--job", job_id], now=NOW)
    assert _next(root) == ("tailor the resume (resume-tailor)", "2026-09-24")


def test_next_action_and_date_follow_every_stage(root):
    tr.main(["--root", str(root), "new", "--job", _job(root)], now=NOW)          # Thursday 2026-09-24
    _event(root, "fit_checked", NOW)
    assert _next(root) == ("tailor the resume (resume-tailor)", "2026-09-24")
    _event(root, "tailored", NOW + timedelta(days=1))
    assert _next(root) == ("apply: apply pack, the owner submits (apply-assist)", "2026-09-25")
    _event(root, "applied", NOW + timedelta(days=1, hours=2))                     # Friday: 5 business days on
    assert _next(root) == (tr.FOLLOW_UP, "2026-10-02")
    _event(root, "recruiter_screen", NOW + timedelta(days=8))
    assert _next(root) == ("prep the round (prep-coach)", None)                  # the round's date is not known
    _event(root, "interview", NOW + timedelta(days=12))
    assert _next(root) == ("prep the round (prep-coach)", None)
    _event(root, "offer", NOW + timedelta(days=20))
    assert _next(root) == ("negotiate (negotiator)", None)                       # the deadline lives in offer.md
    _event(root, "rejected", NOW + timedelta(days=21))
    assert _next(root) == (None, None)


def test_a_reply_after_applying_moves_the_follow_up_date(root):
    tr.main(["--root", str(root), "new", "--job", _job(root)], now=NOW)
    _event(root, "applied", NOW + timedelta(days=1))                              # Friday 2026-09-25
    assert _next(root)[1] == "2026-10-02"
    _event(root, "email_in", NOW + timedelta(days=7))                             # Thursday 2026-10-01
    assert _next(root) == (tr.FOLLOW_UP, "2026-10-08")


def test_a_next_action_a_role_wrote_survives_reprojection_until_the_stage_moves(root):
    from js_common import write_record
    tr.main(["--root", str(root), "new", "--job", _job(root)], now=NOW)
    _event(root, "applied", NOW + timedelta(days=1))
    path = root / "applications" / APP / "application.md"
    props, body = fm.load(path)
    props["next_action"], props["next_action_at"] = "recruiter call with Dana (prep-coach preps)", "2026-10-01"
    write_record(path, props, body)
    assert tr.main(["--root", str(root), "project"], now=NOW + timedelta(days=2)) == 0
    assert _next(root) == ("recruiter call with Dana (prep-coach preps)", "2026-10-01")
    _event(root, "recruiter_screen", NOW + timedelta(days=3))
    assert _next(root) == ("prep the round (prep-coach)", None)


def test_project_repairs_a_score_fit_next_action_left_behind_by_later_events(root):
    _app(root, "a1", "greenhouse", "ats", [("2026-09-10T00:00:00Z", "fit_checked"),
                                           ("2026-09-14T00:00:00Z", "tailored"), ("2026-09-16T12:00:00Z", "applied")])
    path = root / "applications" / "a1" / "application.md"
    props, body = fm.load(path)
    props.update(stage="applied", next_action="score fit (fit-scorer)", next_action_at="2026-09-10")
    path.write_text(fm.dump(props, body))
    assert tr.main(["--root", str(root), "project"], now=NOW) == 0
    assert _next(root, "a1") == (tr.FOLLOW_UP, "2026-09-23")   # Wed 16th + 5 business days


def test_a_round_date_added_to_the_prep_action_is_kept_until_the_stage_moves(root):
    from js_common import write_record
    tr.main(["--root", str(root), "new", "--job", _job(root)], now=NOW)
    _event(root, "applied", NOW + timedelta(days=1))
    _event(root, "recruiter_screen", NOW + timedelta(days=3))
    path = root / "applications" / APP / "application.md"
    props, body = fm.load(path)
    props["next_action_at"] = "2026-10-01"                                         # the round's date, from the invite
    write_record(path, props, body)
    assert tr.main(["--root", str(root), "project"], now=NOW + timedelta(days=4)) == 0
    assert _next(root) == ("prep the round (prep-coach)", "2026-10-01")
    _event(root, "interview", NOW + timedelta(days=9))
    assert _next(root) == ("prep the round (prep-coach)", None)                  # a new round: its date is unknown


# ----------------------------------------------------------------------------- the ghost sweep writes (tracker-ops eval 02)
def test_project_states_the_ghost_thresholds_from_the_code(root, capsys):
    _app(root, "t1", "greenhouse", "ats", [("2026-09-01T10:00:00Z", "applied")])
    assert tr.main(["--root", str(root), "project", "--today", "2026-09-10"]) == 0
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if ln.startswith("ghost thresholds"))
    for stage, days in tr.GHOST_DAYS.items():
        assert f"{stage} {days}" in line
    assert "90" in line and "60" in line and "45" in line


def test_project_writes_each_ghost_flag_with_days_and_the_threshold(root, capsys):
    _app(root, "t2", "greenhouse", "ats", [("2026-06-01T10:00:00Z", "applied"), ("2026-06-20T10:00:00Z", "final_round")])
    _app(root, "t3", "greenhouse", "ats", [("2026-09-01T10:00:00Z", "applied")])
    # pinned: --today names another day than the run's own, so the projection is as of 12:00 UTC that day (D-1)
    assert tr.main(["--root", str(root), "project", "--today", "2026-09-28"],
                   now=datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)) == 0
    out = capsys.readouterr().out
    assert "t2: ghosted (inferred from final: 100 days since the last activity, threshold 45)" in out
    assert fm.load(root / "applications" / "t2" / "application.md")[0]["stage"] == "ghosted"   # written, not a dry run
    assert "t3: applied" in out and "ghost flags: 1" in out


def test_project_lists_the_follow_ups_that_are_due(root, capsys):
    _app(root, "t4", "greenhouse", "ats", [("2026-09-15T17:00:00Z", "applied")])     # 5 business days: 2026-09-22
    _app(root, "t5", "greenhouse", "ats", [("2026-09-25T17:00:00Z", "applied")])
    assert tr.main(["--root", str(root), "project", "--today", "2026-09-28"]) == 0
    out = capsys.readouterr().out
    assert "follow-ups due (+5 business days without inbound: find a person, once): t4 (applied, due 2026-09-22)" in out
    assert "t5 (" not in out


def test_metrics_states_the_ghost_thresholds_too(root, capsys):
    assert tr.main(["--root", str(root), "metrics", "--today", "2026-09-10"]) == 0
    assert "ghost thresholds" in capsys.readouterr().out


# ----------------------------------------------------------------------------- the job's application link (card step, now scripted)
def _unlink(root, job_id, value=None):
    from js_common import write_record
    path = root / "jobs" / f"{job_id}.md"
    props, body = fm.load(path)
    props["application"] = value
    write_record(path, props, body)
    return path


def test_new_links_the_job_even_when_the_application_already_exists(root):
    job_id = _job(root)
    tr.main(["--root", str(root), "new", "--job", job_id], now=NOW)
    path = _unlink(root, job_id)
    assert tr.main(["--root", str(root), "new", "--job", job_id], now=NOW) == 0
    assert fm.load(path)[0]["application"] == "[[acme-applied-ai-engineer-202609]]"


def test_an_applied_event_links_the_job_to_its_application(root):
    job_id = _job(root)
    tr.main(["--root", str(root), "new", "--job", job_id], now=NOW)
    path = _unlink(root, job_id)
    slug = "acme-applied-ai-engineer-202609"
    assert tr.main(["--root", str(root), "event", slug, "applied", *NO_PDF, "--evidence", "Owner in chat"], now=NOW) == 0
    props, body = fm.load(path)
    assert props["application"] == f"[[{slug}]]" and props["status"] == "applied"
    assert props["title"] == "Applied AI Engineer" and "<untrusted_jd>" in body      # the rest of the record kept
    from check_records import check_file
    assert check_file(path) == []


def test_a_job_linked_to_another_application_is_not_relinked(root):
    job_id = _job(root)
    tr.main(["--root", str(root), "new", "--job", job_id], now=NOW)
    path = _unlink(root, job_id, "[[acme-referral-record-202609]]")      # the first of two records wins
    tr.main(["--root", str(root), "event", "acme-applied-ai-engineer-202609", "applied", *NO_PDF], now=NOW)
    assert fm.load(path)[0]["application"] == "[[acme-referral-record-202609]]"


def test_a_dry_run_event_does_not_link_the_job(root):
    job_id = _job(root)
    tr.main(["--root", str(root), "new", "--job", job_id], now=NOW)
    path = _unlink(root, job_id)
    tr.main(["--root", str(root), "event", "acme-applied-ai-engineer-202609", "applied", *NO_PDF, "--dry-run"], now=NOW)
    assert fm.load(path)[0]["application"] is None


# ----------------------------------------------------------------------------- applied: the stated submission time
def test_manual_applied_takes_at_as_the_stated_submission_time(root, capsys):
    tr.main(["--root", str(root), "new", "--job", _job(root)], now=NOW)
    assert tr.main(["--root", str(root), "event", APP, "applied", *NO_PDF, "--at", "2026-09-24T09:15:00-07:00",
                    "--evidence", "Owner in chat 2026-09-24 09:40 PT: 'applied at 9:15'"],
                   now=NOW + timedelta(days=1)) == 0
    assert "warning" not in capsys.readouterr().err
    props, body = fm.load(root / "applications" / APP / "application.md")
    last = tr.parse_events(body)[-1]
    assert (last.at, last.type, last.source) == ("2026-09-24T09:15:00-07:00", "applied", "manual")
    assert last.evidence == "time: stated; Owner in chat 2026-09-24 09:40 PT: 'applied at 9:15'; resume none: fixture: no PDF in this test"
    assert str(props["applied_at"]) == "2026-09-24"                  # the stated day, not the run day (09-25)


@pytest.mark.parametrize("type_", ["applied", "submitted"])
def test_manual_applied_without_at_warns_and_records_the_clock_as_its_time_source(root, capsys, type_):
    tr.main(["--root", str(root), "new", "--job", _job(root)], now=NOW)
    capsys.readouterr()
    assert tr.main(["--root", str(root), "event", APP, type_, *NO_PDF, "--evidence", "the owner said they applied"], now=NOW) == 0
    err = capsys.readouterr().err
    assert f"warning: manual {type_} event on {APP} without --at" in err and "run clock" in err
    _props, body = fm.load(root / "applications" / APP / "application.md")
    last = tr.parse_events(body)[-1]
    assert last.at == "2026-09-24T13:00:00Z"
    assert last.evidence == "time: clock (no --at; submission time not stated); the owner said they applied; resume none: fixture: no PDF in this test"


def test_the_time_source_is_only_for_manual_applied_events(root, capsys):
    tr.main(["--root", str(root), "new", "--job", _job(root)], now=NOW)
    capsys.readouterr()
    tr.main(["--root", str(root), "event", APP, "applied", *NO_PDF, "--source", "email", "--evidence", "msg 1"], now=NOW)
    tr.main(["--root", str(root), "event", APP, "confirmation_received", "--evidence", "msg 2"], now=NOW)
    assert "warning" not in capsys.readouterr().err
    _props, body = fm.load(root / "applications" / APP / "application.md")
    assert [e.evidence for e in tr.parse_events(body)[-2:]] == ["msg 1; resume none: fixture: no PDF in this test", "msg 2"]


def test_an_unparseable_at_is_refused_and_nothing_is_written(root, capsys):
    tr.main(["--root", str(root), "new", "--job", _job(root)], now=NOW)
    before = (root / "applications" / APP / "application.md").read_text()
    assert tr.main(["--root", str(root), "event", APP, "applied", *NO_PDF, "--at", "this morning"], now=NOW) == 2
    assert "not an ISO timestamp" in capsys.readouterr().err
    assert (root / "applications" / APP / "application.md").read_text() == before


# ----------------------------------------------------------------------------- terminal stages by event time
# The rule: events replay by their stated time. A terminal stage (rejected, ghosted, withdrawn) is reopened only
# by a stage event stated strictly AFTER it whose stage is the company re-engaging (tr.REOPENS: screen, oa,
# interviewing, final, offer, accepted), or by override:<stage>. Our own pre-response steps (discovered,
# fit_checked, tailored, applied, confirmation) never reopen one; `discovered` never resumes on_hold either.
RUN = NOW + timedelta(days=2)                                                     # 2026-09-26T13:00Z


def test_backlog_record_projects_rejected_though_discovered_is_stamped_last():
    events = [tr.Event("2026-09-26T13:00:00Z", "discovered", "manual", 1.0, "created from [[job]]"),
              tr.Event("2026-08-17T16:00:00Z", "applied", "manual", 1.0, "time: stated"),
              tr.Event("2026-09-24T18:00:00Z", "rejected", "email", 0.95, "msg 1")]
    out = tr.project(events, RUN)
    assert out["stage"] == "rejected"
    assert out["max_progress"] == "applied"
    assert [s for s, _ in out["transitions"]] == ["applied", "rejected"]


@pytest.mark.parametrize("terminal", ["rejected", "withdrawn", "override:ghosted"])
@pytest.mark.parametrize("later", ["discovered", "fit_checked", "tailored", "applied", "confirmation_received"])
def test_pre_response_steps_never_reopen_a_terminal_stage(terminal, later):
    assert stage([ev(30, "applied"), ev(10, terminal), ev(2, later)]) == terminal.split(":")[-1]


@pytest.mark.parametrize("terminal", ["rejected", "withdrawn", "override:ghosted"])
@pytest.mark.parametrize("later,expected", [("recruiter_screen", "screen"), ("assessment", "oa"),
                                            ("interview", "interviewing"), ("final_round", "final"),
                                            ("offer", "offer")])
def test_a_later_company_reengagement_reopens_a_terminal_stage(terminal, later, expected):
    assert stage([ev(30, "applied"), ev(10, terminal), ev(2, later)]) == expected


def test_a_reopen_stated_before_the_terminal_event_does_not_reopen_whatever_the_log_order():
    # logged last, but the screen invite is stated before the rejection
    assert stage([ev(30, "applied"), ev(5, "rejected"), ev(8, "recruiter_screen")]) == "rejected"
    # same instant: not after, so the terminal stage holds
    assert stage([ev(30, "applied"), ev(5, "recruiter_screen"), ev(5, "rejected")]) == "rejected"
    assert stage([ev(30, "applied"), ev(5, "rejected"), ev(5, "recruiter_screen")]) == "rejected"


def test_out_of_order_stated_times_replay_by_time():
    events = [ev(1, "discovered"), ev(3, "rejected"), ev(40, "applied"), ev(20, "recruiter_screen"),
              ev(35, "confirmation_received")]
    out = tr.project(events, NOW)
    assert out["stage"] == "rejected" and out["max_progress"] == "screen"
    assert [s for s, _ in out["transitions"]] == ["applied", "confirmation", "screen", "rejected"]


def test_an_override_still_reopens_a_terminal_stage():
    assert stage([ev(30, "applied"), ev(10, "rejected"), ev(2, "override:applied", "manual", 1.0)]) == "applied"


def test_discovered_does_not_resume_on_hold_but_later_progress_does():
    assert stage([ev(30, "fit_checked"), ev(10, "on_hold"), ev(2, "discovered")]) == "on_hold"
    assert stage([ev(30, "fit_checked"), ev(10, "on_hold"), ev(2, "tailored")]) == "tailored"


def test_a_late_discovered_stamp_is_not_activity_after_applying():
    # a backlog record created today for a 100-day-old silent application is ghosted, not freshly active
    out = tr.project([ev(0, "discovered"), ev(100, "applied")], NOW)
    assert out["stage"] == "ghosted" and out["ghost_from"] == "applied"


def test_a_live_application_is_unaffected():
    events = [ev(12, "discovered"), ev(10, "applied"), ev(8, "confirmation_received"), ev(3, "recruiter_screen"),
              ev(1, "interview")]
    out = tr.project(events, NOW)
    assert out["stage"] == "interviewing"
    assert [s for s, _ in out["transitions"]] == ["discovered", "applied", "confirmation", "screen", "interviewing"]


def test_backlog_sequence_through_the_cli_projects_rejected(root, capsys):
    tr.main(["--root", str(root), "new", "--job", _job(root)], now=RUN)
    assert tr.main(["--root", str(root), "event", APP, "applied", *NO_PDF, "--at", "2026-08-17T09:00:00-07:00",
                    "--evidence", "backlog: the owner's confirmation mail"], now=RUN) == 0
    assert tr.main(["--root", str(root), "event", APP, "rejected", "--source", "email", "--at",
                    "2026-09-24T18:00:00Z", "--evidence", "msg r1"], now=RUN) == 0
    props, _ = fm.load(root / "applications" / APP / "application.md")
    assert (props["stage"], props["outcome"], str(props["applied_at"])) == ("rejected", "rejected", "2026-08-17")
    assert (props["next_action"], props["next_action_at"]) == (None, None)
    capsys.readouterr()
    assert tr.main(["--root", str(root), "project", "--dry-run"], now=RUN) == 0
    assert f"{APP}: rejected" in capsys.readouterr().out


# ----------------------------------------------------------------------------- new --discovered-at (backlog records)
def test_new_discovered_at_stamps_the_stated_discovery_time(root):
    assert tr.main(["--root", str(root), "new", "--job", _job(root), "--discovered-at", "2026-08-10"], now=RUN) == 0
    props, body = fm.load(root / "applications" / APP / "application.md")
    first = tr.parse_events(body)[0]
    assert (first.at, first.type) == ("2026-08-10", "discovered")
    assert "discovered-at stated" in first.evidence
    assert str(props["created"]) == "2026-09-26"                       # the record's own dates stay the run date
    assert str(props["next_action_at"]) == "2026-08-10"                # the stage began on the stated day
    tr.main(["--root", str(root), "event", APP, "applied", *NO_PDF, "--at", "2026-08-17T16:00:00Z"], now=RUN)
    tr.main(["--root", str(root), "event", APP, "rejected", "--at", "2026-09-24T18:00:00Z"], now=RUN)
    _props, body = fm.load(root / "applications" / APP / "application.md")
    out = tr.project(tr.parse_events(body), RUN)
    assert [s for s, _ in out["transitions"]] == ["discovered", "applied", "rejected"]


@pytest.mark.parametrize("value,why", [("last month", "not an ISO"), ("2026-09-27", "after the run clock")])
def test_new_discovered_at_is_refused_when_unparseable_or_in_the_future(root, capsys, value, why):
    job_id = _job(root)
    assert tr.main(["--root", str(root), "new", "--job", job_id, "--discovered-at", value], now=RUN) == 2
    assert why in capsys.readouterr().err
    assert not (root / "applications").exists() or not any((root / "applications").iterdir())


# ----------------------------------------------------------------------------- the department day (REQ-3, D-2)
EVENING = datetime(2026, 9, 28, 1, 50, tzinfo=timezone.utc)      # 18:50 in San Francisco on 2026-09-27


def test_run_now_keeps_the_wall_clock_on_the_department_day():
    assert tr.run_now(date(2026, 9, 27), EVENING) == EVENING
    assert tr.run_now(None, EVENING) == EVENING


def test_run_now_takes_noon_utc_for_any_other_day_even_the_utc_date():
    assert tr.run_now(date(2026, 9, 28), EVENING) == datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    assert tr.run_now(date(2026, 9, 20), EVENING) == datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def test_an_evening_projection_stamps_the_department_day(root):
    _app(root, "t4", "greenhouse", "ats", [("2026-09-01T10:00:00Z", "applied")])
    assert tr.main(["--root", str(root), "project"], now=EVENING) == 0
    assert str(fm.load(root / "applications" / "t4" / "application.md")[0]["updated"]) == "2026-09-27"


# SETUP item 1a (2026-10-02): an overdue queue item whose posting is still live is re-queued with a fresh due date.
# `requeued` is a bookkeeping event: it names why the item is still worth the work (evidence required), moves the
# pre-application due date (discovered, fit_checked, tailored) to its own day, and is never a stage or activity.
def test_a_requeued_event_moves_the_due_date_and_nothing_else(root):
    job_id = _job(root)
    tr.main(["--root", str(root), "new", "--job", job_id], now=NOW)
    slug = "acme-applied-ai-engineer-202609"
    before, _ = fm.load(root / "applications" / slug / "application.md")
    later = NOW + timedelta(days=5)
    assert tr.main(["--root", str(root), "event", slug, "requeued", "--evidence",
                    "posting live: the greenhouse job API answers 200 (2026-09-29)"], now=later) == 0
    props, _ = fm.load(root / "applications" / slug / "application.md")
    assert props["stage"] == before["stage"] == "discovered" and props["next_action"] == before["next_action"]
    assert str(before["next_action_at"]) == NOW.date().isoformat() and str(props["next_action_at"]) == later.date().isoformat()


def test_a_requeue_is_not_activity_and_a_later_stage_counts_from_its_own_day():
    events = [ev(10, "discovered", "manual"), ev(4, "requeued", "manual", 1.0, "posting live")]
    proj = tr.project(events, NOW)
    assert proj["last_activity"] == tr._instant(ts(10)) and proj["stage"] == "discovered"
    assert tr.next_action_for(proj, NOW)[1] == tr._day(tr._instant(ts(4))).isoformat()
    proj = tr.project(events + [ev(2, "fit_checked", "manual")], NOW)
    assert tr.next_action_for(proj, NOW)[1] == tr._day(tr._instant(ts(2))).isoformat()


def test_a_requeue_needs_its_evidence_and_a_stage_that_waits_on_us(root):
    job_id = _job(root)
    tr.main(["--root", str(root), "new", "--job", job_id], now=NOW)
    slug = "acme-applied-ai-engineer-202609"
    with pytest.raises(tr.TrackerError, match="evidence"):
        tr.append_event(root, slug, "requeued", now=NOW)
    tr.append_event(root, slug, "applied", now=NOW, resume={"note": "fixture", "resume_none_reason": "fixture"})
    with pytest.raises(tr.TrackerError, match="discovered, fit_checked or tailored"):
        tr.append_event(root, slug, "requeued", evidence="posting live", now=NOW)


# item 1a review (finding 6): a requeue buys one day, so a daily requeue could hide a stuck item; two per stage, then
# the item is worked, held or retired
def test_a_third_requeue_in_the_same_stage_is_refused(root):
    job_id = _job(root)
    tr.main(["--root", str(root), "new", "--job", job_id], now=NOW)
    slug = "acme-applied-ai-engineer-202609"
    for d in (1, 2):
        tr.append_event(root, slug, "requeued", evidence="posting live", now=NOW + timedelta(days=d))
    with pytest.raises(tr.TrackerError, match="requeued twice"):
        tr.append_event(root, slug, "requeued", evidence="posting live", now=NOW + timedelta(days=3))
    tr.append_event(root, slug, "fit_checked", now=NOW + timedelta(days=3))      # a new stage starts its own count
    tr.append_event(root, slug, "requeued", evidence="posting live", now=NOW + timedelta(days=4))
