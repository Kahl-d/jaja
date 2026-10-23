"""tracker.py reconcile: mail-events rows attach only on a confirmed match.

An `auto` row from state/mail-events.jsonl was attached to an application although its thread was not in that
application's thread_ids. The guard: attach only by thread (the row's thread_id in the application's thread_ids)
or by req id / job URL at the auto threshold; everything else is held in state/review/mail-<date>.md."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from disc_harness import *  # noqa: F401,F403  (puts scripts/ on sys.path; fixtures; no-network guard)

import tracker as tr  # noqa: E402
from js_common import fm  # noqa: E402

NOW = datetime(2026, 9, 28, 19, 0, tzinfo=timezone.utc)
TODAY = "2026-09-28"
INVITE = "We'd like to invite you to the technical interview for the ML Engineer role"


def _app(root, slug, stage, events, thread_ids=()):
    folder = root / "applications" / slug
    folder.mkdir(parents=True)
    props = {"id": slug, "job": "[[none]]", "company": "Acme", "title": "ML Engineer", "stage": stage,
            "thread_ids": list(thread_ids), "created": "2026-09-01", "updated": "2026-09-20"}
    body = "# app\n\n## Events\n" + "".join(tr.format_event(at, t, "manual", 1.0, "seed") + "\n" for at, t in events)
    (folder / "application.md").write_text(fm.dump(props, body), encoding="utf-8")
    return folder / "application.md"


def _row(mid, label, application, *, thread="t-1", signal="thread", score=0.95, runner_up=None,
         runner_up_score=0.0, decision="auto", conf=0.95, evidence=INVITE, **extra):
    row = {"at": "2026-09-28T16:00:00Z", "message_id": mid, "thread_id": thread, "label": label,
           "confidence": conf, "evidence": evidence, "application": application,
           "match": {"signal": signal, "score": score, "runner_up": runner_up, "runner_up_score": runner_up_score},
           "decision": decision, "prospect": None, "logged_at": "2026-09-28T18:05:00Z"}
    row.update(extra)
    return row


def _ledger(root, *rows):
    path = root / "state" / "mail-events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _run(root, *args):
    return tr.main(["--root", str(root), "reconcile", "--today", TODAY, *args], now=NOW)


def _body(path):
    return fm.load(path)[1]


def _review(root):
    path = root / "state" / "review" / f"mail-{TODAY}.md"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


@pytest.fixture
def apps(root):
    return {
        "harb": _app(root, "harborline-ml-engineer-202609", "confirmation",
                     [("2026-09-10T17:00:00Z", "applied"), ("2026-09-11T17:00:00Z", "confirmation_received")],
                     thread_ids=["t-harb"]),
        "quill": _app(root, "quillstone-ml-engineer-202609", "screen",
                      [("2026-09-10T17:00:00Z", "applied"), ("2026-09-22T18:00:00Z", "screen")],
                      thread_ids=["t-quill"]),
        "cob": _app(root, "cobaltline-llm-engineer-202609", "applied", [("2026-09-15T17:00:00Z", "applied")]),
        "kite": _app(root, "kitefin-ai-engineer-202607", "final",
                     [("2026-07-01T17:00:00Z", "applied"), ("2026-09-20T18:00:00Z", "final_round")]),
    }


def test_a_thread_match_attaches_with_message_id_and_evidence(root, apps, capsys):
    _ledger(root, _row("m-harb", "interview_invite", "harborline-ml-engineer-202609", thread="t-harb"))
    assert _run(root) == 0
    body = _body(apps["harb"])
    last = tr.parse_events(body)[-1]
    assert last.type == "interviewing" and last.source == "email" and last.conf == 0.95
    assert last.at == "2026-09-28T16:00:00Z"
    assert "m-harb" in last.evidence and INVITE in last.evidence
    assert fm.load(apps["harb"])[0]["stage"] == "interviewing"          # re-projected
    out = capsys.readouterr().out
    assert "attached m-harb -> harborline-ml-engineer-202609" in out
    assert "attached 1, held 0, skipped 0" in out


def test_a_foreign_thread_on_an_auto_row_is_held_and_never_applied(root, apps, capsys):
    # The Quillstone trap: signal says thread, but the row's thread is not one of the application's threads.
    _ledger(root, _row("m-quill", "interview_invite", "quillstone-ml-engineer-202609", thread="t-other"))
    before = apps["quill"].read_text()
    assert _run(root) == 0
    assert apps["quill"].read_text() == before
    out = capsys.readouterr().out
    assert "held m-quill" in out and "t-other" in out and "thread_ids" in out
    review = _review(root)
    assert "m-quill" in review and "decision:" in review
    assert review.rstrip().endswith("decision:")                        # blank: the owner or job-lead decides


def test_a_low_score_auto_row_is_held(root, apps, capsys):
    # The Kitefin trap: a 0.40 title match marked auto.
    _ledger(root, _row("m-kite", "ats_confirmation", "kitefin-ai-engineer-202607", thread="t-kite",
                       signal="title", score=0.4, evidence="We have received your application for AI Engineer"))
    before = apps["kite"].read_text()
    assert _run(root) == 0
    assert apps["kite"].read_text() == before
    assert "held m-kite" in capsys.readouterr().out
    assert "m-kite" in _review(root)


def test_a_req_id_match_attaches_at_the_auto_threshold(root, apps):
    _ledger(root, _row("m-cob", "rejection", "cobaltline-llm-engineer-202609", thread="t-cob", signal="req_id",
                       score=0.9, evidence="we have decided to move forward with other candidates"))
    assert _run(root) == 0
    assert fm.load(apps["cob"])[0]["stage"] == "rejected"


def test_a_req_id_match_below_the_confidence_threshold_is_held(root, apps, capsys):
    _ledger(root, _row("m-cob", "rejection", "cobaltline-llm-engineer-202609", thread="t-cob", signal="req_id",
                       score=0.9, conf=0.6, evidence="we have decided to move forward with other candidates"))
    assert _run(root) == 0
    assert fm.load(apps["cob"])[0]["stage"] == "applied"
    assert "held m-cob" in capsys.readouterr().out


def test_a_req_id_match_without_a_020_lead_is_held(root, apps, capsys):
    _ledger(root, _row("m-cob", "rejection", "cobaltline-llm-engineer-202609", thread="t-cob", signal="req_id",
                       score=0.9, runner_up="quillstone-ml-engineer-202609", runner_up_score=0.9,
                       evidence="we have decided to move forward with other candidates"))
    assert _run(root) == 0
    assert fm.load(apps["cob"])[0]["stage"] == "applied"
    assert "held m-cob" in capsys.readouterr().out


def test_review_rows_are_held_and_never_applied(root, apps, capsys):
    _ledger(root, _row("m-dan", "interview_invite", None, thread="t-dan", signal="prospect", score=0.5,
                       runner_up="quillstone-ml-engineer-202609", runner_up_score=0.5, decision="review"))
    assert _run(root) == 0
    assert "held m-dan" in capsys.readouterr().out and "m-dan" in _review(root)
    for path in apps.values():
        assert "m-dan" not in path.read_text()


def test_a_duplicate_is_skipped(root, apps, capsys):
    row = _row("m-harb", "interview_invite", "harborline-ml-engineer-202609", thread="t-harb")
    _ledger(root, row)
    assert _run(root) == 0
    capsys.readouterr()
    assert _run(root) == 0                                              # second run: nothing new
    assert _body(apps["harb"]).count("m-harb") == 1
    out = capsys.readouterr().out
    assert "skipped m-harb: already recorded in harborline-ml-engineer-202609" in out
    assert "attached 0, held 0, skipped 1" in out


def test_an_out_of_sequence_stage_is_held(root, apps, capsys):
    # A confirmation email for an application already at screen: an earlier stage than the current one.
    _ledger(root, _row("m-q2", "ats_confirmation", "quillstone-ml-engineer-202609", thread="t-quill",
                       evidence="Thank you for applying to the ML Engineer position"))
    before = apps["quill"].read_text()
    assert _run(root) == 0
    assert apps["quill"].read_text() == before
    out = capsys.readouterr().out
    assert "held m-q2" in out and "confirmation" in out and "screen" in out


def test_an_event_that_would_revive_a_ghosted_application_is_held(root, apps, capsys):
    kite = _app(root, "kitefin-old-202605", "confirmation",
                [("2026-05-01T17:00:00Z", "applied"), ("2026-05-02T17:00:00Z", "confirmation_received")],
                thread_ids=["t-old"])
    _ledger(root, _row("m-old", "interview_invite", "kitefin-old-202605", thread="t-old"))
    before = kite.read_text()
    assert _run(root) == 0
    assert kite.read_text() == before
    assert "ghosted" in capsys.readouterr().out


def test_the_invite_round_comes_from_the_invites_words_or_is_held(root, apps, capsys):
    _ledger(root, _row("m-h1", "interview_invite", "harborline-ml-engineer-202609", thread="t-harb",
                       evidence="Could we set up a 30-min recruiter screen?"),
            _row("m-h2", "interview_invite", "harborline-ml-engineer-202609", thread="t-harb",
                 evidence="The hiring team would like to move forward on your application"))
    assert _run(root) == 0
    body = _body(apps["harb"])
    assert "m-h1" in body and "m-h2" not in body
    assert tr.parse_events(body)[-1].type == "screen"
    out = capsys.readouterr().out
    assert "held m-h2" in out and "--round m-h2=" in out
    capsys.readouterr()
    assert _run(root, "--round", "m-h2=interviewing") == 0              # the role names the round; the guard still ran
    assert tr.parse_events(_body(apps["harb"]))[-1].type == "interviewing"


def test_scheduling_and_offer_mail_are_note_events_without_a_stage_change(root, apps):
    _ledger(root, _row("m-s", "interview_scheduling", "harborline-ml-engineer-202609", thread="t-harb",
                       evidence="Could you share two times that work next week?"),
            _row("m-o", "offer", "harborline-ml-engineer-202609", thread="t-harb",
                 evidence="We are delighted to offer you the position"))
    assert _run(root) == 0
    props, body = fm.load(apps["harb"])
    assert props["stage"] == "confirmation"
    types = [e.type for e in tr.parse_events(body)]
    assert types[-2:] == ["email_in", "email_in"]
    assert "offer email received" in tr.parse_events(body)[-1].evidence


def test_suspected_scam_and_no_effect_labels_are_never_attached(root, apps, capsys):
    _ledger(root, _row("m-scam", "suspected_scam", "harborline-ml-engineer-202609", thread="t-harb",
                       evidence="Please send a passport photo and a fee today"),
            _row("m-news", "newsletter", "harborline-ml-engineer-202609", thread="t-harb",
                 evidence="This week in AI hiring news"))
    before = apps["harb"].read_text()
    assert _run(root) == 0
    assert apps["harb"].read_text() == before
    out = capsys.readouterr().out
    assert "held m-scam" in out and "no event m-news" in out


def test_only_the_latest_row_per_message_counts(root, apps):
    first = _row("m-x", "interview_invite", "quillstone-ml-engineer-202609", thread="t-harb", decision="review",
                 signal="prospect", score=0.5)
    fixed = _row("m-x", "interview_invite", "harborline-ml-engineer-202609", thread="t-harb",
                 correction="thread t-harb belongs to Example Robotics", logged_at="2026-09-28T18:30:00Z")
    _ledger(root, first, fixed)
    assert _run(root) == 0
    body = _body(apps["harb"])
    assert "m-x correction 2026-09-28T18:30:00Z" in body
    assert "m-x" not in apps["quill"].read_text()
    assert _run(root) == 0                                              # the mark makes it idempotent
    assert _body(apps["harb"]).count("m-x correction") == 1


def test_a_correction_that_moves_an_attached_message_notes_the_old_event(root, apps):
    first = _row("m-y", "interview_invite", "harborline-ml-engineer-202609", thread="t-harb")
    _ledger(root, first)
    assert _run(root) == 0
    fixed = _row("m-y", "interview_invite", "quillstone-ml-engineer-202609", thread="t-quill",
                 decision="owner", correction="Owner 2026-09-28: this invite is Quillstone's",
                 logged_at="2026-09-28T18:40:00Z")
    _ledger(root, first, fixed)
    assert _run(root) == 0
    props, body = fm.load(apps["harb"])
    harb = tr.parse_events(body)
    assert [e.type for e in harb[-2:]] == ["note", "override:confirmation"]      # the owner's ruling moves it back
    assert "supersedes interviewing" in harb[-2].evidence and props["stage"] == "confirmation"
    assert any(e.type == "interviewing" and "m-y" in e.evidence for e in harb)     # never removed
    quill = tr.parse_events(_body(apps["quill"]))
    assert quill[-1].type == "interviewing" and quill[-1].evidence.startswith("m-y correction 2026-09-28T18:40:00Z")
    assert _run(root) == 0                                              # done once: the mark is in the log
    assert _body(apps["quill"]).count("m-y correction") == 1


def test_a_correction_of_an_attached_message_without_owners_ruling_leaves_the_stage_to_the_owner(root, apps, capsys):
    first = _row("m-z", "interview_invite", "harborline-ml-engineer-202609", thread="t-harb")
    _ledger(root, first)
    assert _run(root) == 0
    fixed = _row("m-z", "interview_invite", "quillstone-ml-engineer-202609", thread="t-quill",
                 correction="the thread was misread", logged_at="2026-09-28T18:45:00Z")
    _ledger(root, first, fixed)
    capsys.readouterr()
    assert _run(root) == 0
    props, body = fm.load(apps["harb"])
    assert tr.parse_events(body)[-1].type == "note" and props["stage"] == "interviewing"
    assert "needs the owner" in capsys.readouterr().out


def test_a_correction_that_keeps_application_and_label_appends_nothing(root, apps, capsys):
    first = _row("m-k", "interview_invite", "harborline-ml-engineer-202609", thread="t-harb")
    _ledger(root, first)
    assert _run(root) == 0
    before = apps["harb"].read_text()
    _ledger(root, first, {**first, "correction": "confidence re-checked", "logged_at": "2026-09-28T18:50:00Z"})
    capsys.readouterr()
    assert _run(root) == 0
    assert apps["harb"].read_text() == before
    assert "keeps its application and label" in capsys.readouterr().out


def test_dry_run_writes_nothing(root, apps, capsys):
    _ledger(root, _row("m-harb", "interview_invite", "harborline-ml-engineer-202609", thread="t-harb"),
            _row("m-quill", "interview_invite", "quillstone-ml-engineer-202609", thread="t-other"))
    before = {k: p.read_text() for k, p in apps.items()}
    assert _run(root, "--dry-run") == 0
    assert {k: p.read_text() for k, p in apps.items()} == before
    assert not (root / "state" / "review").exists()
    out = capsys.readouterr().out
    assert "would attach m-harb" in out and "held m-quill" in out


def test_held_rows_are_listed_once_in_the_review_file(root, apps):
    _ledger(root, _row("m-quill", "interview_invite", "quillstone-ml-engineer-202609", thread="t-other"))
    _run(root)
    _run(root)
    assert _review(root).count("m-quill") == 1


def test_a_missing_ledger_is_reported_not_treated_as_empty(root, apps, capsys):
    assert _run(root) == 1
    assert "state/mail-events.jsonl" in capsys.readouterr().out
