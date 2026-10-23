"""mail_intake.py: classified mail reaches the tracker (ATS confirmations that arrive must reach their application). Fixture mail only; the Gmail pull is a stub; nothing leaves the machine."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; no-network guard)

import job_alerts as ja  # noqa: E402
import mail_intake as mi  # noqa: E402
import tracker as tr  # noqa: E402
from js_common import fm  # noqa: E402

NOW = datetime(2026, 10, 5, 23, 30, tzinfo=timezone.utc)          # 16:30 in the persona's time zone (PT)
DAY = "2026-10-05"
SRC = f"state/harvest/gmail-{DAY}.jsonl"
CLS = f"state/harvest/gmail-{DAY}.classified.jsonl"

ACME_CONFIRM = "Thank you for applying to the ML Engineer role at Acme."
CINDER_CONFIRM = "Thanks for applying to the Agent Engineer position at Cinder."
BOREALIS_REJECT = "We have decided not to move forward with your application."
INVITE = "Could we set up a 30 minute recruiter screen next week?"
OUTREACH = "Would you be open to a short call about the role this week?"
RESOLVED = "Unfortunately the team has chosen other candidates for this role."


def _app(js: Path, slug: str, company: str, title: str, applied: str | None):
    folder = js / "applications" / slug
    folder.mkdir(parents=True)
    meta = {"id": slug, "job": "[[none]]", "company": company, "company_slug": company.lower(), "title": title,
            "stage": "applied" if applied else "fit_checked", "applied_at": applied, "thread_ids": [],
            "created": "2026-10-01", "updated": "2026-10-03"}
    events = [("2026-10-01T17:00:00Z", "fit_checked")] + ([(f"{applied}T17:00:00Z", "applied")] if applied else [])
    body = "# app\n\n## Events\n" + "".join(tr.format_event(at, t, "manual", 1.0, "seed") + "\n" for at, t in events)
    path = folder / "application.md"
    path.write_text(fm.dump(meta, body), encoding="utf-8")
    return path


def _msg(mid, sender, subject, body, label, evidence, conf=0.97, **cls_extra):
    return {"id": mid, "thread_id": f"t-{mid}", "message_id": f"<{mid}@fixtures.example>", "direction": "in",
            "internal_date": f"{DAY}T18:00:00Z", "from": sender, "subject": subject, "body": body,
            "snippet": body[:200],
            "classification": {"label": label, "confidence": conf, "evidence": evidence, "level": "L1", **cls_extra}}


def _bare(row):
    return {k: v for k, v in row.items() if k != "classification"}


@pytest.fixture
def tree(tmp_path):
    js = tmp_path / "os" / "domains" / "career" / "projects" / "job-search"
    (tmp_path / "os" / "work" / "approvals").mkdir(parents=True)
    (js / "prospects").mkdir(parents=True)
    apps = {
        "acme_ml": _app(js, "acme-ml-engineer-202610", "Acme", "ML Engineer", "2026-10-05"),
        "acme_ds": _app(js, "acme-data-scientist-202610", "Acme", "Data Scientist", "2026-10-05"),
        "borealis": _app(js, "borealis-ai-engineer-202610", "Borealis", "AI Engineer", "2026-10-02"),
        "cinder_a": _app(js, "cinder-agent-engineer-202609", "Cinder", "Agent Engineer", "2026-10-04"),
        "cinder_b": _app(js, "cinder-agent-engineer-202610", "Cinder", "Agent Engineer", "2026-10-05"),
    }
    msgs = [
        _msg("m-acme", "Acme Hiring Team <no-reply@ashbyhq.com>", "Thanks for applying to Acme!",
             ACME_CONFIRM + " We will review your profile soon.", "ats_confirmation", ACME_CONFIRM),
        _msg("m-cinder", "Cinder Hiring Team <no-reply@ashbyhq.com>", "Thanks for applying to Cinder!",
             CINDER_CONFIRM, "ats_confirmation", CINDER_CONFIRM),
        _msg("m-bor", "no-reply@us.greenhouse-mail.io", "Borealis Application Status",
             "Hi Alex, thank you for your interest. " + BOREALIS_REJECT, "rejection", BOREALIS_REJECT, conf=0.95),
        _msg("m-inv", "Dana Recruiter <dana@acme.test>", "Next steps at Acme", "Hi Alex, " + INVITE,
             "interview_invite", INVITE, conf=0.9),
        _msg("m-rec", "Rae Lin <rae@talentco.test>", "AI Engineer role at Quarry",
             "Hi Alex, I am a recruiter at Talentco hiring an AI Engineer for Quarry. " + OUTREACH,
             "recruiter_outreach", OUTREACH, conf=0.9),
        # an L3 answer written by `mail_classify.py resolve`: it must survive every intake run byte for byte
        _msg("m-l3", "Erin Lead <erin@quarry.test>", "Re: your application", "Hi Alex. " + RESOLVED, "rejection",
             RESOLVED, conf=0.9, level="L3", backend="agent", resolved_on=DAY),
    ]
    (js / "state" / "harvest").mkdir(parents=True)
    (js / SRC).write_text("".join(json.dumps(_bare(m)) + "\n" for m in msgs), encoding="utf-8")
    (js / CLS).write_text("".join(json.dumps(m, sort_keys=True) + "\n" for m in msgs), encoding="utf-8")
    return {"js": js, "os": tmp_path / "os", "apps": apps}


def _run(tree, *extra, harvest_fn=None):
    argv = ["--root", str(tree["js"]), "--os-root", str(tree["os"]), "--today", DAY, "--json", *extra]
    if harvest_fn is None:
        argv.append("--no-pull")
    return mi.main(argv, now=NOW, harvest_fn=harvest_fn)


def _ledger(tree):
    return [json.loads(x) for x in (tree["js"] / "state" / "mail-events.jsonl").read_text().splitlines() if x.strip()]


def _events(path):
    return tr.parse_events(fm.load(path)[1])


def _review(tree):
    path = tree["js"] / "state" / "review" / f"mail-{DAY}.md"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def test_a_confirmation_attaches_to_the_application_it_names(tree, capsys):
    assert _run(tree) == 0
    last = _events(tree["apps"]["acme_ml"])[-1]
    assert last.type == "confirmation_received" and last.source == "email" and "m-acme" in last.evidence
    assert fm.load(tree["apps"]["acme_ml"])[0]["stage"] == "confirmation"
    assert all("m-acme" not in e.evidence for e in _events(tree["apps"]["acme_ds"]))   # same company, other title
    row = next(r for r in _ledger(tree) if r["message_id"] == "m-acme")
    assert row["decision"] == "auto" and row["match"]["signal"] == "company_title"
    assert row["match"]["runner_up"] == "acme-data-scientist-202610"


def test_an_ambiguous_confirmation_goes_to_review_and_is_never_attached(tree):
    before = {k: tree["apps"][k].read_text() for k in ("cinder_a", "cinder_b")}
    assert _run(tree) == 0
    assert {k: tree["apps"][k].read_text() for k in before} == before
    row = next(r for r in _ledger(tree) if r["message_id"] == "m-cinder")
    assert row["decision"] == "review" and row["application"] is None
    assert {c["application"] for c in row["match"]["candidates"]} == {"cinder-agent-engineer-202609",
                                                                      "cinder-agent-engineer-202610"}
    assert "m-cinder" in _review(tree) and _review(tree).count("m-cinder") == 1


def test_a_company_only_match_is_held_never_attached(tree):
    """The company's one recorded folder may not be the role the mail is about (a second, unrecorded
    application there), so a company_sole match is a review row, never an event."""
    assert _run(tree) == 0
    assert fm.load(tree["apps"]["borealis"])[0]["stage"] != "rejected"
    assert not any("m-bor" in (e.evidence or "") for e in _events(tree["apps"]["borealis"]))
    row = next(r for r in _ledger(tree) if r["message_id"] == "m-bor")
    assert row["decision"] == "review"


def test_an_interview_invite_is_a_flag_for_the_owner_never_a_stage_jump(tree, capsys):
    assert _run(tree) == 0
    out = json.loads(capsys.readouterr().out)
    assert fm.load(tree["apps"]["acme_ml"])[0]["stage"] == "confirmation"            # not screen
    row = next(r for r in _ledger(tree) if r["message_id"] == "m-inv")
    assert row["decision"] == "review"
    assert out["sentinel"] == "NEEDS_YOU" and any("interview_invite m-inv" in k for k in out["owner"])
    receipt = tree["os"] / "logs" / "runs" / "js-mail-intake" / f"{DAY}.md"
    text = receipt.read_text()
    assert "sentinel: NEEDS_YOU" in text and "for the owner: interview_invite m-inv" in text


def test_a_second_run_adds_nothing(tree):
    assert _run(tree) == 0
    assert (tree["js"] / "state" / "review" / f"mail-{DAY}.md").is_file() and list((tree["js"] / "prospects").glob("*.md"))
    state = {p: p.read_bytes() for p in tree["js"].rglob("*") if p.is_file()}
    assert _run(tree) == 0
    assert {p: p.read_bytes() for p in tree["js"].rglob("*") if p.is_file()} == state       # every file, people too


def test_stored_l3_answers_survive_and_new_mail_is_appended(tree):
    stored = (tree["js"] / CLS).read_text().splitlines(keepends=True)
    new = {"id": "m-new", "thread_id": "t-new", "direction": "in", "internal_date": f"{DAY}T20:00:00Z",
           "from": "Borealis Hiring Team <no-reply@ashbyhq.com>", "subject": "Thanks for applying to Borealis!",
           "body": "We have received your application for AI Engineer at Borealis.",
           "snippet": "We have received your application for AI Engineer at Borealis."}

    def harvest(roots, today):                      # the stub Gmail pull appends one message to today's harvest
        with (roots.js / SRC).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(new) + "\n")
        return {"mode": "history", "listed": 1, "new": 1}

    assert _run(tree, harvest_fn=harvest) == 0
    after = (tree["js"] / CLS).read_text().splitlines(keepends=True)
    assert after[:len(stored)] == stored                                    # byte for byte, L3 row included
    l3 = json.loads(next(x for x in after if '"m-l3"' in x))["classification"]
    assert l3["resolved_on"] == DAY and l3["label"] == "rejection"
    added = [json.loads(x) for x in after[len(stored):]]
    assert [r["id"] for r in added] == ["m-new"] and added[0]["classification"]["label"]


def test_dry_run_writes_nothing_and_reports_the_plan(tree, capsys, tmp_path):
    before = {p: p.read_bytes() for p in tree["js"].rglob("*") if p.is_file()}
    assert _run(tree, "--dry-run", "--out", str(tmp_path / "plan")) == 0
    assert {p: p.read_bytes() for p in tree["js"].rglob("*") if p.is_file()} == before
    assert not (tree["os"] / "logs").exists()
    plan = json.loads((tmp_path / "plan" / "mail-intake-plan.json").read_text())
    assert plan["reconcile"]["counts"]["attached"] == 1
    assert any(a.startswith("m-acme -> acme-ml-engineer-202610") for a in plan["reconcile"]["attached"])


def test_job_alerts_go_to_job_alerts_collect(tree, monkeypatch):
    seen = {}

    def collect(root, now, files=None, **kw):
        seen["files"] = [Path(f).name for f in files]
        return ja.AlertRun(files=[str(f) for f in files])

    monkeypatch.setattr(ja, "load_platforms", lambda root: {"linkedin": {"alert_senders": ["x@example.com"]}})
    monkeypatch.setattr(ja, "collect", collect)
    monkeypatch.setattr(ja, "load_title_filter", lambda root: None)
    assert _run(tree) == 0
    assert seen["files"] == [Path(CLS).name]


def test_people_rows_only_are_ingested(tree):
    rows = [json.loads(x) for x in (tree["js"] / CLS).read_text().splitlines()]
    picked = {r["id"] for r in mi.people_rows(rows)}
    assert picked == {"m-inv", "m-l3", "m-rec"}                       # machine ATS senders never become people


def test_a_google_calendar_notice_is_no_row(tree):
    rec = _msg("m-cal", '"dana@acme.test (Google Calendar)" <calendar-notification@google.com>',
               "Accepted: Dana / Alex: Chat @ Mon Oct 6, 2026", "Dana has accepted this invitation today.",
               "interview_scheduling", "Dana has accepted this invitation today.", conf=0.78)
    row, why = mi.plan_row(rec, mi.load_apps(tree["js"]))
    assert row is None and "Google Calendar" in why
