"""mail_events.py — deterministic rows for state/mail-events.jsonl and the Handoffs checklist."""
from __future__ import annotations

import json
from datetime import date

import pytest

import comms_helpers  # noqa: F401  (puts scripts/ on sys.path)
from comms_helpers import clock_2030, offline  # noqa: F401  (autouse: no network; clock_2030 pins the wall clock)
import calendar_hold as chd
import js_common as jc
import js_comms_util as cu
import mail_events as me

TODAY = date(2026, 9, 28)
CLASSIFIED = "state/harvest/gmail-2026-09-28.classified.jsonl"

INVITE = "We'd like to invite you to the technical interview for the ML Engineer role."
REJECT = "We have decided to move forward with other candidates at this time."
CONFIRM = "Thank you for applying to the Agent Platform Engineer position (req 4471005)."
OFFER = "We are delighted to offer you the position of Applied AI Engineer."
SCHED = "Could you share two times that work for a 45 minute call next week?"
MOVE = "The hiring team would like to move forward on your application."


def _msg(mid, thread, label, body, *, direction="in", conf=0.95, internal="2026-09-28T16:00:00Z", snippet=None):
    return {"id": mid, "thread_id": thread, "message_id": f"<{mid}@fixtures.example.test>", "direction": direction,
            "internal_date": internal, "subject": f"subject {mid}", "from": "x@example.com",
            "body": body if direction == "in" else "", "snippet": snippet if snippet is not None else body[:200],
            "classification": {"label": label, "confidence": conf, "evidence": "sender ats", "level": "L1"}}


def _app(js, slug, events=(), thread_ids=()):
    lines = "".join(f"- {e}\n" for e in events)
    tids = "".join(f"\n- {t}" for t in thread_ids) or " []"
    path = js / "applications" / slug / "application.md"
    path.parent.mkdir(parents=True)
    path.write_text(f"---\nid: {slug}\nstage: applied\nthread_ids:{tids}\n---\n\n## Events\n{lines}", encoding="utf-8")


@pytest.fixture
def roots(tmp_path, clock_2030):
    """The wall clock is pinned to 2030-01-01 (clock_2030), so a run dated --today 2026-09-28 stamps that day at
    12:00:00Z on every real day, 2026-09-28 included (run_stamp takes the wall clock only when --today is today)."""
    js = tmp_path / "os" / "domains" / "career" / "projects" / "job-search"
    (tmp_path / "os" / "work" / "approvals").mkdir(parents=True)
    _app(js, "harborline-ml-engineer", thread_ids=["t-harb"],
         events=["2026-09-18 · applied · manual · conf=1.0 · owner's word",
                 "2026-09-25 · screen · email · conf=1.0 · message id m-harb-01"])
    _app(js, "cobaltline-llm-engineer", events=["2026-09-25 · applied · manual · conf=1.0 · owner's word"])
    _app(js, "lumenloop-agent-platform", events=["2026-09-28 · applied · manual · conf=1.0 · owner's word"])
    _app(js, "quillstone-platform")
    _app(js, "quillstone-search")
    _app(js, "orchard-applied-ai")
    msgs = [
        _msg("m-harb-01", "t-harb", "interview_invite", "Could we set up a 30-min recruiter screen?"),
        _msg("m-harb-02", "t-harb", "interview_invite", "Hi Sam,\nGreat talking last week. " + INVITE),
        _msg("m-cob-01", "t-cob", "rejection", "Hi Sam, thank you for your interest. " + REJECT),
        _msg("m-lum-01", "t-lum", "ats_confirmation", "Hi Sam, " + CONFIRM),
        _msg("m-orc-01", "t-orc", "offer", "Hi Sam, " + OFFER),
        _msg("m-sch-01", "t-harb", "interview_scheduling", "Hi Sam, " + SCHED),
        _msg("m-dan-01", "t-dan", "interview_invite", "Hi Sam, " + MOVE),
        _msg("m-out-01", "t-maya", "other", "", direction="out", snippet="Would Wed Sep 30 work for 20 minutes?"),
        _msg("m-out-02", "t-cold", "other", "", direction="out", snippet="Following up on my note."),
    ]
    path = js / CLASSIFIED
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(m) + "\n" for m in msgs), encoding="utf-8")
    return cu.Roots(js=js, os=tmp_path / "os")


def _row(mid, label, evidence, application, *, signal="thread", score=0.95, runner_up=None, runner_up_score=0.0,
         decision="auto", conf=0.95, **extra):
    row = {"message_id": mid, "label": label, "confidence": conf, "evidence": evidence, "application": application,
           "match": {"signal": signal, "score": score, "runner_up": runner_up, "runner_up_score": runner_up_score},
           "decision": decision, "prospect": None}
    row.update(extra)
    return row


def _add(roots, rows, capsys=None):
    report = me.add_rows(roots, roots.js / CLASSIFIED, rows, today=TODAY)
    return report


def _ledger(roots):
    return jc.read_jsonl(roots.js / "state" / "mail-events.jsonl")


def _cli(roots, *args):
    return me.main([*args, "--root", str(roots.js), "--today", TODAY.isoformat()])


# --------------------------------------------------------------------------- add: happy path
def test_a_valid_auto_row_is_appended_with_script_set_fields(roots):
    report = _add(roots, [_row("m-harb-02", "interview_invite", INVITE, "harborline-ml-engineer")])
    assert report.counts() == {"appended": 1, "skipped": 0, "downgraded": 0, "refused": 0}
    (row,) = _ledger(roots)
    assert row["decision"] == "auto" and row["application"] == "harborline-ml-engineer"
    assert row["at"] == "2026-09-28T16:00:00Z"            # the message's internal_date
    assert row["thread_id"] == "t-harb"                    # from the classified record
    assert row["logged_at"] == "2026-09-28T12:00:00Z"      # set by the script for the run date
    assert set(row) == set(me.FIELDS)


def test_a_row_can_name_the_rfc_message_id(roots):
    report = _add(roots, [_row("<m-lum-01@fixtures.example.test>", "ats_confirmation", CONFIRM,
                               "lumenloop-agent-platform", signal="req_id", score=0.9)])
    assert report.counts()["appended"] == 1
    assert _ledger(roots)[0]["thread_id"] == "t-lum"


def test_evidence_matches_across_line_breaks_and_surrounding_quotes(roots):
    report = _add(roots, [_row("m-harb-02", "interview_invite", '"Great talking last week. We\'d like"',
                               "harborline-ml-engineer")])
    assert report.counts()["appended"] == 1
    assert _ledger(roots)[0]["evidence"] == "Great talking last week. We'd like"


# --------------------------------------------------------------------------- rule (a): already in an application
def test_a_message_already_recorded_in_an_application_is_skipped(roots):
    report = _add(roots, [_row("m-harb-01", "interview_invite", "Could we set up a 30-min recruiter screen?",
                               "harborline-ml-engineer")])
    assert report.counts() == {"appended": 0, "skipped": 1, "downgraded": 0, "refused": 0}
    assert "skipped: already recorded in harborline-ml-engineer" in report.lines()[0]
    assert _ledger(roots) == []


def test_an_rfc_id_of_a_recorded_message_is_skipped_too(roots):
    report = _add(roots, [_row("<m-harb-01@fixtures.example.test>", "interview_invite",
                               "Could we set up a 30-min recruiter screen?", "harborline-ml-engineer")])
    assert report.counts()["skipped"] == 1


def test_an_id_that_only_prefixes_a_recorded_id_is_not_skipped(roots):
    _app(roots.js, "other-app", events=["2026-09-26 · note · email · conf=1.0 · message id m-cob-01x"])
    report = _add(roots, [_row("m-cob-01", "rejection", REJECT, "cobaltline-llm-engineer", signal="req_id", score=0.9)])
    assert report.counts()["appended"] == 1


# --------------------------------------------------------------------------- rule (b): duplicate in the ledger
def test_a_message_already_in_the_ledger_is_skipped_as_duplicate(roots):
    row = _row("m-cob-01", "rejection", REJECT, "cobaltline-llm-engineer", signal="req_id", score=0.9)
    _add(roots, [row])
    report = _add(roots, [row])
    assert report.counts() == {"appended": 0, "skipped": 1, "downgraded": 0, "refused": 0}
    assert "skipped: duplicate" in report.lines()[0]
    assert len(_ledger(roots)) == 1


def test_the_same_message_twice_in_one_batch_is_appended_once(roots):
    row = _row("m-cob-01", "rejection", REJECT, "cobaltline-llm-engineer", signal="req_id", score=0.9)
    report = _add(roots, [row, dict(row)])
    assert report.counts()["appended"] == 1 and report.counts()["skipped"] == 1


def test_a_correction_row_is_appended_for_a_recorded_message(roots):
    row = _row("m-cob-01", "rejection", REJECT, "cobaltline-llm-engineer", signal="req_id", score=0.9)
    _add(roots, [row])
    fix = _row("m-cob-01", "rejection", REJECT, "lumenloop-agent-platform", signal="req_id", score=0.9,
               decision="owner", correction="owner 2026-09-28: the req belongs to Lumenloop")
    report = _add(roots, [fix])
    assert report.counts()["appended"] == 1
    assert _ledger(roots)[-1]["correction"].startswith("owner 2026-09-28")


# --------------------------------------------------------------------------- rule (c): auto needs 0.75 and a 0.20 lead
def test_auto_on_a_prospect_match_is_downgraded_to_review(roots):
    row = _row("m-dan-01", "interview_invite", MOVE, "quillstone-platform", signal="prospect", score=0.5,
               runner_up="quillstone-search", runner_up_score=0.5)
    report = _add(roots, [row])
    assert report.counts() == {"appended": 1, "skipped": 0, "downgraded": 1, "refused": 0}
    assert "downgraded: decision auto -> review" in report.lines()[0]
    assert _ledger(roots)[0]["decision"] == "review"
    assert report.exit_code() == 0


def test_auto_without_a_020_lead_is_downgraded(roots):
    row = _row("m-lum-01", "ats_confirmation", CONFIRM, "lumenloop-agent-platform", signal="req_id", score=0.9,
               runner_up="quillstone-platform", runner_up_score=0.9)
    assert _add(roots, [row]).counts()["downgraded"] == 1
    assert _ledger(roots)[0]["decision"] == "review"


def test_auto_at_exactly_the_thresholds_stays_auto(roots):
    row = _row("m-harb-02", "interview_invite", INVITE, "harborline-ml-engineer", signal="thread", score=0.95,
               runner_up="quillstone-platform", runner_up_score=0.9)            # lead 0.05 -> review
    assert _add(roots, [row]).counts()["downgraded"] == 1
    row = _row("m-lum-01", "ats_confirmation", CONFIRM, "lumenloop-agent-platform", signal="req_id", score=0.9,
               runner_up="quillstone-platform", runner_up_score=0.5)            # lead 0.40 -> auto
    report = _add(roots, [row])
    assert report.counts()["downgraded"] == 0 and _ledger(roots)[-1]["decision"] == "auto"
    assert me.auto_allowed(0.95, 0.75) and not me.auto_allowed(0.5, 0.0) and me.auto_allowed(0.9, 0.0)


def test_auto_without_an_application_is_downgraded(roots):
    row = _row("m-orc-01", "offer", OFFER, None, signal="none", score=0.0)
    report = _add(roots, [row])
    assert report.counts()["downgraded"] == 1 and _ledger(roots)[0]["decision"] == "review"


def test_a_score_that_disagrees_with_its_signal_is_refused(roots):
    row = _row("m-dan-01", "interview_invite", MOVE, "quillstone-platform", signal="prospect", score=0.9)
    report = _add(roots, [row])
    assert report.counts()["refused"] == 1
    assert "prospect scores 0.50" in report.lines()[0]
    assert _ledger(roots) == []


def test_an_unknown_signal_is_refused(roots):
    row = _row("m-dan-01", "interview_invite", MOVE, "quillstone-platform", signal="prospect_link", score=0.5,
               decision="review")
    assert "signal 'prospect_link'" in _add(roots, [row]).lines()[0]


TIED = [{"application": "quillstone-platform", "signal": "prospect", "score": 0.5},
        {"application": "quillstone-search", "signal": "prospect", "score": 0.5}]


def test_a_review_row_below_the_threshold_is_appended_unchanged(roots):
    row = _row("m-dan-01", "interview_invite", MOVE, None, signal="prospect", score=0.5,
               runner_up="quillstone-search", runner_up_score=0.5, decision="review")
    row["match"]["candidates"] = TIED
    report = _add(roots, [row])
    assert report.counts() == {"appended": 1, "skipped": 0, "downgraded": 0, "refused": 0}


# --------------------------------------------------------------------------- tied candidates (mail-triage eval 02)
def test_a_tie_without_the_top_candidate_named_is_refused(roots):
    # The eval row: application null, runner_up one of the two tied applications, the other one lost.
    row = _row("m-dan-01", "interview_invite", MOVE, None, signal="prospect", score=0.5,
               runner_up="quillstone-search", runner_up_score=0.5, decision="review")
    report = _add(roots, [row])
    assert report.counts()["refused"] == 1
    assert "match.candidates" in report.lines()[0]
    assert _ledger(roots) == []


def test_every_tied_candidate_is_kept_in_the_ledger_and_the_needs_owner_line(roots):
    row = _row("m-dan-01", "interview_invite", MOVE, None, signal="prospect", score=0.5,
               runner_up="quillstone-search", runner_up_score=0.5, decision="review")
    row["match"]["candidates"] = list(reversed(TIED))
    assert _add(roots, [row]).counts()["appended"] == 1
    cands = _ledger(roots)[0]["match"]["candidates"]
    assert sorted((c["application"], c["score"], c["signal"]) for c in cands) == [
        ("quillstone-platform", 0.5, "prospect"), ("quillstone-search", 0.5, "prospect")]
    needs = me.handoffs(roots, day=TODAY).split("## Needs the owner", 1)[1]
    assert "quillstone-platform 0.50 (prospect)" in needs and "quillstone-search 0.50 (prospect)" in needs
    assert "(none)" not in needs


def test_a_three_way_tie_lists_all_three(roots):
    row = _row("m-dan-01", "interview_invite", MOVE, None, signal="prospect", score=0.5, decision="review",
               runner_up="quillstone-search", runner_up_score=0.5)
    row["match"]["candidates"] = TIED + [{"application": "orchard-applied-ai", "signal": "prospect", "score": 0.5}]
    assert _add(roots, [row]).counts()["appended"] == 1
    needs = me.handoffs(roots, day=TODAY).split("## Needs the owner", 1)[1]
    assert all(f"{slug} 0.50" in needs for slug in ("quillstone-platform", "quillstone-search", "orchard-applied-ai"))


@pytest.mark.parametrize("bad,why", [
    ([{"application": "nowhere", "signal": "prospect", "score": 0.5}], "no application nowhere"),
    ([{"application": "quillstone-platform", "signal": "prospect", "score": 0.9}], "prospect scores 0.50"),
    ([{"application": "quillstone-platform", "signal": "title", "score": 0.4}], "top candidate"),
    ([TIED[0], TIED[0]], "twice"),
])
def test_bad_candidates_are_refused(roots, bad, why):
    row = _row("m-dan-01", "interview_invite", MOVE, None, signal="prospect", score=0.5, decision="review")
    row["match"]["candidates"] = bad
    report = _add(roots, [row])
    assert report.counts()["refused"] == 1 and why in report.lines()[0], report.lines()


def test_candidates_are_derived_when_the_row_names_its_top_and_runner_up(roots):
    row = _row("m-dan-01", "interview_invite", MOVE, "quillstone-platform", signal="prospect", score=0.5,
               runner_up="quillstone-search", runner_up_score=0.5, decision="review")
    assert _add(roots, [row]).counts()["appended"] == 1
    cands = _ledger(roots)[0]["match"]["candidates"]
    assert [(c["application"], c["score"]) for c in cands] == [("quillstone-platform", 0.5), ("quillstone-search", 0.5)]


# --------------------------------------------------------------------------- rule (d): evidence is verbatim
def test_evidence_not_in_the_message_is_refused(roots):
    row = _row("m-harb-02", "interview_invite", "They want a technical interview", "harborline-ml-engineer")
    report = _add(roots, [row])
    assert report.counts()["refused"] == 1
    assert "evidence is not a verbatim sentence" in report.lines()[0]
    assert _ledger(roots) == []


def test_paraphrased_case_is_not_verbatim(roots):
    row = _row("m-cob-01", "rejection", REJECT.lower(), "cobaltline-llm-engineer", signal="req_id", score=0.9)
    assert _add(roots, [row]).counts()["refused"] == 1


def test_evidence_from_the_snippet_counts(roots):
    row = _row("m-orc-01", "offer", OFFER, "orchard-applied-ai", signal="req_id", score=0.9)
    rows = jc.read_jsonl(roots.js / CLASSIFIED)
    for r in rows:
        if r["id"] == "m-orc-01":
            r["body"] = ""                                       # body missing; the snippet still has it
    (roots.js / CLASSIFIED).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert _add(roots, [row]).counts()["appended"] == 1


def test_a_one_word_evidence_is_refused(roots):
    row = _row("m-harb-02", "interview_invite", "interview", "harborline-ml-engineer")
    assert "at least 3 words" in _add(roots, [row]).lines()[0]


def test_a_message_missing_from_the_classified_file_is_refused(roots):
    row = _row("m-nope", "interview_invite", INVITE, "harborline-ml-engineer")
    assert "not in" in _add(roots, [row]).lines()[0]


# --------------------------------------------------------------------------- rule (e): labels and fields
def test_an_unknown_label_is_refused(roots):
    row = _row("m-harb-02", "interview", INVITE, "harborline-ml-engineer")
    report = _add(roots, [row])
    assert report.counts()["refused"] == 1 and "label 'interview'" in report.lines()[0]


def test_a_label_that_differs_from_the_classified_file_is_refused(roots):
    row = _row("m-sch-01", "rejection", SCHED, "harborline-ml-engineer")
    line = _add(roots, [row]).lines()[0]
    assert "mail_classify.py resolve" in line


def test_missing_required_fields_are_refused(roots):
    row = _row("m-harb-02", "interview_invite", INVITE, "harborline-ml-engineer")
    del row["prospect"]
    del row["match"]["runner_up_score"]
    line = _add(roots, [row]).lines()[0]
    assert "missing" in line and "prospect" in line and "match.runner_up_score" in line


def test_an_unknown_application_is_refused(roots):
    row = _row("m-harb-02", "interview_invite", INVITE, "harborline-typo")
    assert "no application harborline-typo" in _add(roots, [row]).lines()[0]


def test_a_bad_decision_is_refused(roots):
    row = _row("m-harb-02", "interview_invite", INVITE, "harborline-ml-engineer", decision="attach")
    assert "decision 'attach'" in _add(roots, [row]).lines()[0]


def test_logged_at_from_the_caller_is_ignored(roots):
    row = _row("m-harb-02", "interview_invite", INVITE, "harborline-ml-engineer", logged_at="1999-01-01T00:00:00Z")
    _add(roots, [row])
    assert _ledger(roots)[0]["logged_at"] == "2026-09-28T12:00:00Z"


# --------------------------------------------------------------------------- CLI
def test_cli_add_prints_a_summary_and_exits_non_zero_on_refusal(roots, capsys):
    good = _row("m-harb-02", "interview_invite", INVITE, "harborline-ml-engineer")
    bad = _row("m-cob-01", "rejection", "made up sentence here", "cobaltline-llm-engineer", signal="req_id", score=0.9)
    rows = roots.js / "rows.jsonl"
    rows.write_text(json.dumps(bad) + "\n", encoding="utf-8")
    code = _cli(roots, "add", "--classified", str(roots.js / CLASSIFIED), "--row", json.dumps(good),
                "--rows", str(rows))
    out = capsys.readouterr().out
    assert code == 1
    assert out.strip().splitlines()[-1] == "appended 1, skipped 0, downgraded 0, refused 1"
    assert len(_ledger(roots)) == 1


def test_cli_add_exits_zero_when_nothing_is_refused(roots, capsys):
    code = _cli(roots, "add", "--classified", str(roots.js / CLASSIFIED),
                "--row", json.dumps(_row("m-harb-01", "interview_invite", "Could we set up a 30-min recruiter screen?",
                                         "harborline-ml-engineer")))
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "appended 0, skipped 1, downgraded 0, refused 0"


def test_cli_add_refuses_a_row_that_is_not_json(roots, capsys):
    code = _cli(roots, "add", "--classified", str(roots.js / CLASSIFIED), "--row", "{not json")
    assert code == 1 and "refused" in capsys.readouterr().out


def test_cli_relative_classified_path_resolves_under_root(roots, capsys, tmp_path, monkeypatch):
    # A decoy at the same relative path in the working directory (the real department folder has one: INBOX's
    # state/harvest/gmail-2026-09-28.classified.jsonl). --root must win over it.
    decoy = tmp_path / "cwd" / CLASSIFIED
    decoy.parent.mkdir(parents=True)
    decoy.write_text(json.dumps(_msg("m-other", "t-other", "other", "not the fixture")) + "\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path / "cwd")
    code = _cli(roots, "add", "--classified", CLASSIFIED,
                "--row", json.dumps(_row("m-harb-02", "interview_invite", INVITE, "harborline-ml-engineer")))
    assert code == 0 and len(_ledger(roots)) == 1


# --------------------------------------------------------------------------- handoffs
def _outbox(roots, oid, thread, status, **extra):
    rec = {"id": oid, "prospect": "maya-okafor", "channel": "email", "thread_id": thread, "status": status,
            "approval": "A-2026-0003", **extra}
    cu.write_record(roots.outbox / f"{oid}.md", rec, "Hi Maya\n")


def _hold(roots, mid, *, status="pending"):
    rec = next(r for r in jc.read_jsonl(roots.js / CLASSIFIED) if r["id"] == mid)
    payload = chd.build_payload(rec, start="2026-10-01T13:00", duration_min=60, application="harborline-ml-engineer")
    aid, path = chd.request_approval(roots, payload, today=TODAY)
    if status != "pending":
        cu.set_frontmatter(path, status=status)
    return aid


def _run_all(roots):
    rows = [
        _row("m-harb-02", "interview_invite", INVITE, "harborline-ml-engineer"),
        _row("m-sch-01", "interview_scheduling", SCHED, "harborline-ml-engineer"),
        _row("m-cob-01", "rejection", REJECT, "cobaltline-llm-engineer", signal="req_id", score=0.9),
        _row("m-orc-01", "offer", OFFER, "orchard-applied-ai", signal="req_id", score=0.9),
        _row("m-lum-01", "ats_confirmation", CONFIRM, "lumenloop-agent-platform", signal="req_id", score=0.9),
        _row("m-dan-01", "interview_invite", MOVE, "quillstone-platform", signal="prospect", score=0.5,
             runner_up="quillstone-search", runner_up_score=0.5),
    ]
    report = _add(roots, rows)
    assert report.counts()["refused"] == 0, report.lines()
    return report


def test_handoffs_list_every_item_one_line_each(roots):
    hold = _hold(roots, "m-harb-02")
    _outbox(roots, "O-2026-0001", "t-maya", "approved")
    _run_all(roots)
    text = me.handoffs(roots, day=TODAY)
    lines = text.splitlines()
    assert f"- [ ] messenger: interview_invite m-harb-02 · harborline-ml-engineer · hold {hold} (pending)" in lines
    assert "- [ ] messenger: interview_scheduling m-sch-01 · harborline-ml-engineer · no hold request" in lines
    assert "- [ ] messenger: interview_invite m-dan-01 · unmatched (review) · no hold request" in lines
    assert "- [ ] recruiter: rejection m-cob-01 · cobaltline-llm-engineer" in lines
    assert "- [ ] legal-visa: rejection m-cob-01 · cobaltline-llm-engineer" in lines
    for role in ("recruiter", "legal-visa", "negotiator"):
        assert f"- [ ] {role}: offer m-orc-01 · orchard-applied-ai" in lines
    assert "- [ ] messenger: O-2026-0001 -> m-out-01" in lines
    assert not any("m-out-02" in ln for ln in lines)          # no outbox item in that thread
    assert not any("m-lum-01" in ln for ln in lines)          # a confirmation needs no handoff
    needs = text.split("## Needs the owner", 1)[1]
    assert "m-dan-01" in needs and "quillstone-platform 0.50" in needs and "quillstone-search 0.50" in needs
    assert "offer m-orc-01" in needs


def test_handoffs_skip_withdrawn_holds(roots):
    _hold(roots, "m-harb-02", status="withdrawn")
    _run_all(roots)
    assert "- [ ] messenger: interview_invite m-harb-02 · harborline-ml-engineer · no hold request" in \
        me.handoffs(roots, day=TODAY).splitlines()


def test_handoffs_only_cover_the_given_day(roots):
    _run_all(roots)
    assert "m-cob-01" not in me.handoffs(roots, day=date(2026, 9, 29))
    assert "m-cob-01" in me.handoffs(roots, since=date(2026, 9, 27))


def test_handoffs_for_a_sent_item_and_a_rejected_item(roots):
    _outbox(roots, "O-2026-0001", "t-maya", "sent", message_id="m-out-01")
    _outbox(roots, "O-2026-0002", "t-cold", "rejected")
    text = me.handoffs(roots, day=TODAY)
    assert "- [ ] messenger: O-2026-0001 -> m-out-01 (already marked sent)" in text.splitlines()
    assert "m-out-02" not in text


def test_handoffs_with_nothing_to_hand_off(roots):
    text = me.handoffs(roots, day=TODAY)
    assert "## Handoffs" in text and "## Needs the owner" in text
    assert text.count("- none") == 2


def test_handoffs_suspected_scam_goes_to_the_owner(roots):
    rows = jc.read_jsonl(roots.js / CLASSIFIED)
    rows.append(_msg("m-scam-01", "t-scam", "suspected_scam", "Please send a passport photo and a $150 fee today."))
    (roots.js / CLASSIFIED).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    report = _add(roots, [_row("m-scam-01", "suspected_scam", "Please send a passport photo and a $150 fee today.",
                               None, signal="none", score=0.0, decision="review")])
    assert report.counts()["appended"] == 1
    text = me.handoffs(roots, day=TODAY)
    assert "suspected_scam m-scam-01" in text.split("## Needs the owner", 1)[1]
    assert "recruiter: suspected_scam" not in text


def test_cli_handoffs(roots, capsys):
    _run_all(roots)
    capsys.readouterr()
    code = me.main(["handoffs", "--root", str(roots.js), "--date", "2026-09-28"])
    out = capsys.readouterr().out
    assert code == 0 and "- [ ] recruiter: rejection m-cob-01 · cobaltline-llm-engineer" in out


def test_cli_rejects_a_bad_date(roots):
    with pytest.raises(SystemExit):
        me.main(["handoffs", "--root", str(roots.js), "--date", "Sep 28"])


# --------------------------------------------------------------------------- receipt (the js-mail-triage run receipt)
RECEIPT = ("logs", "runs", "js-mail-triage", "2026-09-28.md")


def _receipt(roots, *extra):
    return me.main(["receipt", "--root", str(roots.js), "--os-root", str(roots.os), "--date", "2026-09-28",
                    "--now", "2026-09-28T15:05:00", *extra])


def test_receipt_writes_the_watchdog_format_from_state(roots, capsys):
    hold = _hold(roots, "m-harb-02")
    _run_all(roots)
    review = roots.js / "state" / "review" / "mail-2026-09-28.md"
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text("# Mail review\n\n- m-dan-01 · interview_invite · candidates ... · decision:\n"
                      "- m-old-01 · rejection · decision: attach to cobaltline\n", encoding="utf-8")
    assert _receipt(roots) == 0
    text = roots.os.joinpath(*RECEIPT).read_text(encoding="utf-8")
    assert text.startswith("---\nroutine: js-mail-triage\ndate: 2026-09-28\n---")
    assert "## Run 2026-09-28T15:05:00" in text and "· ok" in text
    assert "- sentinel: NEEDS_YOU" in text
    assert "interview_invite 3" in text and "rejection 1" in text and "offer 1" in text      # counts by class
    assert "rows logged: 6" in text and "auto 5" in text and "review 1" in text
    assert f"calendar holds requested (pending): 1 ({hold})" in text
    assert "held for review: 1 ledger row (m-dan-01); 1 open item in state/review/mail-2026-09-28.md" in text
    wd = me._kernel_watchdog()
    when, path, how = wd.latest_receipt(roots.os, "js-mail-triage")
    assert how == "heading" and path.name == "2026-09-28.md"
    assert str(roots.os.joinpath(*RECEIPT)) in capsys.readouterr().out


def test_receipt_without_anything_for_the_owner_says_no_action(roots):
    _add(roots, [_row("m-lum-01", "ats_confirmation", CONFIRM, "lumenloop-agent-platform", signal="req_id",
                      score=0.9)])
    assert _receipt(roots) == 0
    text = roots.os.joinpath(*RECEIPT).read_text(encoding="utf-8")
    assert "- sentinel: NO_ACTION" in text and "calendar holds requested (pending): none" in text
    assert "held for review: none" in text


def test_receipt_needs_you_for_a_pending_hold_alone(roots):
    _hold(roots, "m-harb-02")
    assert _receipt(roots) == 0
    assert "- sentinel: NEEDS_YOU" in roots.os.joinpath(*RECEIPT).read_text(encoding="utf-8")


def test_receipt_warns_when_no_classified_harvest_exists_for_the_day(roots):
    (roots.js / CLASSIFIED).unlink()
    assert _receipt(roots) == 0
    text = roots.os.joinpath(*RECEIPT).read_text(encoding="utf-8")
    assert "· warn" in text and "no classified harvest for 2026-09-28" in text


def test_receipt_notes_are_appended_and_a_dry_run_writes_nothing(roots, capsys):
    assert _receipt(roots, "--dry-run", "--note", "Gmail token valid") == 0
    assert not roots.os.joinpath(*RECEIPT).exists()
    out = capsys.readouterr().out
    assert "Gmail token valid" in out and "sentinel" in out
    assert _receipt(roots, "--note", "Gmail token valid") == 0
    assert "- Gmail token valid" in roots.os.joinpath(*RECEIPT).read_text(encoding="utf-8")
