#!/usr/bin/env python3
"""Mail-event rows for tracker-ops and mail-triage's Handoffs checklist. Scripts enforce, the model judges.

    python3 scripts/mail_events.py add --classified state/harvest/gmail-2026-09-28.classified.jsonl \
        --row '<json>' [--row '<json>' ...] [--rows rows.jsonl] [--today 2026-09-28]
    python3 scripts/mail_events.py handoffs --date 2026-09-28 | --since 2026-09-27 [--harvest <file> ...]
    python3 scripts/mail_events.py receipt --date 2026-09-28 [--note TEXT ...] [--status ok|warn|failed] [--dry-run]

add        validates each row and appends it to state/mail-events.jsonl (the only way rows are written).
           A row carries: message_id (Gmail id or RFC Message-ID), label, confidence, evidence, application
           (slug or null), match {signal, score, runner_up, runner_up_score}, decision (one of DECISIONS: auto,
           review, or the owner's own decision value), prospect (slug or null); optional `correction` ("<why>") for a corrected row; optional
           match.candidates [{application, signal, score}, ...]: every candidate application, required when
           the row names no application but has a scored match (a tie: list all tied applications). The
           ledger row always carries match.candidates (derived from application and runner_up otherwise). The script sets
           `at` (the message's internal_date), `thread_id` (the message's) and `logged_at` (the run time).
           Rules, in order:
             - a message_id already in any applications/*/application.md "## Events" -> skipped
               ("skipped: already recorded in <slug>"); already in mail-events.jsonl (or earlier in the same
               call) -> skipped as duplicate. A row with `correction` is exempt from both.
             - refused: a missing field; a label outside mail_classify.LABELS or different from the
               classified file's label (fix it with mail_classify.py resolve first); a decision outside
               DECISIONS; a confidence outside 0..1; an unknown application; a match signal other
               than thread 0.95, req_id 0.90, job_url 0.90, company_title 0.80, company_sole 0.75, company 0.60,
               prospect 0.50, title 0.40, none 0.00, or a score that is not its signal's weight (the company_*
               signals are mail_intake.py's: the sender or subject names the application's company, and for
               company_title its title too, in the application's date window; company_sole: the company's only
               application folder); a runner-up above the top score; a message not in the
               classified file; evidence that is not a verbatim sentence (3 words or more) of the message's
               body or snippet (whitespace and surrounding quotes are ignored; case is not).
             - decision auto without an application, a score of 0.75 or more and a lead of 0.20 or more
               over the runner-up is rewritten to review ("downgraded"). Downgraded rows are appended.
           Prints one line per row and `appended N, skipped N, downgraded N, refused N`; exits 1 if anything
           was refused.
handoffs   prints the reply's `## Handoffs` and `## Needs the owner` checklists for the rows logged that day
           (local Bay Area date of logged_at; --since: that day or later), one line per item and recipient:
           interview_invite and interview_scheduling -> messenger with the message id, plus every calendar hold
           approval (calendar_hold.py) whose payload names the message (withdrawn ones left out); rejection ->
           recruiter and legal-visa with the application slug; offer -> recruiter, legal-visa and negotiator (and
           Needs the owner); every outbound Gmail record (direction out) of that day's harvest files
           (state/harvest/gmail-<date>*.jsonl, or --harvest) in the thread of an approved or sent outbox item ->
           messenger as `O-id -> message id`; review rows and suspected_scam rows -> Needs the owner.
receipt    appends this run's section to <os-root>/logs/runs/js-mail-triage/<date>.md in the watchdog's
           receipt format (scripts/kernel/watchdog.py write_receipt: `## Run <ISO> · <status>`, a sentinel
           line, one note per line), computed from state, never from memory: counts by class from the day's
           classified harvest (state/harvest/gmail-<date>*.classified.jsonl), the mail-events rows logged that
           day by decision and how many are already attached in applications, rows held for review (review
           rows of the day, and open `decision:` items in state/review/mail-<date>.md), and the calendar holds
           requested and still pending. Sentinel NEEDS_YOU whenever a hold, a review row or item, an offer or a
           suspected scam is waiting for the owner, else NO_ACTION. Status ok, or warn when the day has no
           classified harvest (--status overrides); --note adds lines (re-auth, refusals); --now stamps the run
           (default: now, or 12:00 local on --date when it is another day); --dry-run prints and writes nothing.
--root / --os-root / --today as in the other scripts; eval sandboxes resolve through js_common.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import js_common as jc
import js_comms_util as cu
from mail_classify import LABELS

LEDGER = Path("state") / "mail-events.jsonl"
FIELDS = ("at", "message_id", "thread_id", "label", "confidence", "evidence", "application", "match", "decision",
          "prospect", "logged_at")
REQUIRED = ("message_id", "label", "confidence", "evidence", "application", "match", "decision", "prospect")
MATCH_FIELDS = ("signal", "score", "runner_up", "runner_up_score")
DECISIONS = ("auto", "review", "owner")      # the last is the owner's own decision
# The match rule (agents/mail-triage.md): a candidate scores its strongest signal; weights do not add.
SIGNALS = {"thread": 0.95, "req_id": 0.90, "job_url": 0.90, "company_title": 0.80, "company_sole": 0.75,
           "company": 0.60, "prospect": 0.50, "title": 0.40, "none": 0.0}
AUTO_MIN_SCORE = 0.75
AUTO_MIN_LEAD = 0.20
EPS = 1e-6
MIN_EVIDENCE_WORDS = 3
QUOTES = "\"'“”‘’`"
HOLD_LABELS = ("interview_invite", "interview_scheduling")
HOLD_GONE = ("withdrawn",)                    # a withdrawn hold request is not the thread's hold any more
SENT_STATES = ("approved", "sent")


class RowError(ValueError):
    """A row cannot be appended; the message is the reason."""


# --------------------------------------------------------------------------- lookups
def _ids(rec: dict[str, Any]) -> set[str]:
    return {str(rec[k]) for k in ("id", "message_id") if rec.get(k)}


def load_messages(paths: Iterable[Path]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for path in paths:
        for rec in jc.read_jsonl(Path(path)):
            key = str(rec.get("id") or rec.get("message_id") or "")
            if key and key not in seen:
                seen.add(key)
                out.append(rec)
    return out


def find_message(messages: list[dict[str, Any]], message_id: str) -> dict[str, Any] | None:
    for rec in messages:
        if message_id in _ids(rec):
            return rec
    return None


def _id_pattern(ids: Iterable[str]) -> re.Pattern | None:
    ids = [i for i in ids if i]
    if not ids:
        return None
    return re.compile(r"(?<![\w@-])(?:" + "|".join(re.escape(i) for i in sorted(ids, key=len, reverse=True))
                      + r")(?![\w@-])")


def recorded_in(roots: cu.Roots, ids: Iterable[str]) -> str | None:
    """The slug of the first application whose "## Events" names any of these message ids."""
    ids = list(ids)
    bare = [i.strip("<>") for i in ids if i.startswith("<")]
    pattern = _id_pattern(ids + bare)
    folder = roots.js / "applications"
    if pattern is None or not folder.is_dir():
        return None
    for path in sorted(folder.glob("*/application.md")):
        _, body = cu.read_record(path)
        if pattern.search(cu.section_text(body, "Events") or ""):
            return path.parent.name
    return None


def _collapse(text: str) -> str:
    return " ".join(str(text or "").split())


def evidence_problem(evidence: Any, rec: dict[str, Any]) -> str | None:
    quote = _collapse(evidence).strip(QUOTES).strip()
    if len(quote.split()) < MIN_EVIDENCE_WORDS:
        return (f"evidence must be a sentence copied from the message (at least {MIN_EVIDENCE_WORDS} words), "
                f"got {cu.one_line(str(evidence), 80)!r}")
    for source in (rec.get("body"), rec.get("snippet")):
        if quote in _collapse(source):
            return None
    return (f"evidence is not a verbatim sentence of message {rec.get('id')}'s body or snippet: "
            f"{cu.one_line(quote, 120)!r}; copy one sentence exactly")


def auto_allowed(score: float, runner_up_score: float) -> bool:
    return score + EPS >= AUTO_MIN_SCORE and (score - runner_up_score) + EPS >= AUTO_MIN_LEAD


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise RowError(f"{name} must be a number, got {value!r}")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise RowError(f"{name} must be a number, got {value!r}") from None


# --------------------------------------------------------------------------- add
@dataclass
class Outcome:
    message_id: str
    status: str                       # appended | skipped | refused
    reason: str = ""
    downgraded: bool = False
    row: dict[str, Any] | None = None

    def line(self) -> str:
        if self.status == "refused":
            return f"{self.message_id or '(no message_id)'} refused: {self.reason}"
        if self.status == "skipped":
            return f"{self.message_id} skipped: {self.reason}"
        row = self.row or {}
        where = row.get("application") or "no application"
        text = f"{self.message_id} appended: {row.get('label')} -> {where} ({row.get('decision')})"
        if row.get("correction"):
            text += " correction"
        if self.downgraded:
            text += f"; downgraded: {self.reason}"
        return text


@dataclass
class AddReport:
    outcomes: list[Outcome] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {"appended": sum(o.status == "appended" for o in self.outcomes),
                "skipped": sum(o.status == "skipped" for o in self.outcomes),
                "downgraded": sum(o.downgraded for o in self.outcomes),
                "refused": sum(o.status == "refused" for o in self.outcomes)}

    def lines(self) -> list[str]:
        return [o.line() for o in self.outcomes]

    def summary(self) -> str:
        c = self.counts()
        return f"appended {c['appended']}, skipped {c['skipped']}, downgraded {c['downgraded']}, refused {c['refused']}"

    def exit_code(self) -> int:
        return 1 if self.counts()["refused"] else 0


def _validate(roots: cu.Roots, row: dict[str, Any], rec: dict[str, Any] | None, classified: Path) -> dict[str, Any]:
    missing = [k for k in REQUIRED if k not in row]
    match = row.get("match")
    if isinstance(match, dict):
        missing += [f"match.{k}" for k in MATCH_FIELDS if k not in match]
    elif "match" in row:
        raise RowError("match must be an object {signal, score, runner_up, runner_up_score}")
    if missing:
        raise RowError(f"missing required field(s): {', '.join(missing)}")
    mid = str(row["message_id"] or "").strip()
    if not mid:
        raise RowError("message_id is empty")
    label = row["label"]
    if label not in LABELS:
        raise RowError(f"label {label!r} is not one of mail_classify's labels: {', '.join(LABELS)}")
    decision = row["decision"]
    if decision not in DECISIONS:
        raise RowError(f"decision {decision!r} is not one of {', '.join(DECISIONS)}")
    confidence = _number(row["confidence"], "confidence")
    if not 0.0 <= confidence <= 1.0:
        raise RowError(f"confidence {confidence} is outside 0..1")
    signal = match["signal"]
    if signal not in SIGNALS:
        raise RowError(f"match signal {signal!r} is not one of "
                       + ", ".join(f"{k} {v:.2f}" for k, v in SIGNALS.items()))
    score = _number(match["score"], "match.score")
    if abs(score - SIGNALS[signal]) > EPS:
        raise RowError(f"match signal {signal} scores {SIGNALS[signal]:.2f}, not {score:.2f} "
                       "(a candidate scores its strongest signal; weights do not add)")
    ru_score = 0.0 if match["runner_up_score"] is None else _number(match["runner_up_score"], "match.runner_up_score")
    if not any(abs(ru_score - w) <= EPS for w in SIGNALS.values()):
        raise RowError(f"match.runner_up_score {ru_score:.2f} is not a signal weight")
    if ru_score > score + EPS:
        raise RowError(f"runner-up score {ru_score:.2f} is above the top score {score:.2f}; swap them")
    for key in ("application", "prospect"):
        value = row[key]
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise RowError(f"{key} must be a slug or null, got {value!r}")
    app = row["application"]
    if app and not (roots.js / "applications" / app / "application.md").is_file():
        raise RowError(f"no application {app} (applications/{app}/application.md)")
    candidates = _candidates_of(roots, row, match, signal, score, ru_score)
    if rec is None:
        raise RowError(f"message {mid} not in {classified.name}")
    cls_label = (rec.get("classification") or {}).get("label")
    if cls_label is None:
        raise RowError(f"message {mid} has no classification in {classified.name}; run mail_classify.py classify")
    if label != cls_label:
        raise RowError(f"label {label} differs from the classified file's {cls_label}; correct the label with "
                       "mail_classify.py resolve first, then add the row")
    problem = evidence_problem(row["evidence"], rec)
    if problem:
        raise RowError(problem)
    out = {
        "at": cu.to_utc_iso(rec.get("internal_date")) or rec.get("internal_date"),
        "message_id": mid,
        "thread_id": rec.get("thread_id"),
        "label": label,
        "confidence": round(confidence, 3),
        "evidence": _collapse(row["evidence"]).strip(QUOTES).strip(),
        "application": app,
        "match": {"signal": signal, "score": SIGNALS[signal], "runner_up": match["runner_up"],
                  "runner_up_score": round(ru_score, 2), "candidates": candidates},
        "decision": decision,
        "prospect": row["prospect"],
    }
    if row.get("correction"):
        out["correction"] = cu.one_line(str(row["correction"]), 300)
    return out


def _signal_for(score: float) -> str | None:
    names = [k for k, w in SIGNALS.items() if abs(w - score) <= EPS and k != "none"]
    return names[0] if len(names) == 1 else None


def _candidates_of(roots: cu.Roots, row: dict[str, Any], match: dict[str, Any], signal: str, score: float,
                   ru_score: float) -> list[dict[str, Any]]:
    """Every candidate application with its score, highest first. Given as match.candidates (required when the
    row names no application but has a scored match, e.g. a tie), else derived from application and runner_up."""
    app, runner_up = row["application"], match.get("runner_up")
    given = match.get("candidates")
    if given in (None, []):
        if not app and (runner_up or score > EPS):
            raise RowError("the match has candidates but application is null: list every candidate in "
                           "match.candidates as [{application, signal, score}, ...] (a tie names all of them)")
        out = [{"application": app, "signal": signal, "score": SIGNALS[signal]}] if app else []
        if runner_up:
            out.append({"application": runner_up, "signal": _signal_for(ru_score), "score": round(ru_score, 2)})
        return out
    if not isinstance(given, list) or not all(isinstance(c, dict) for c in given):
        raise RowError("match.candidates must be a list of {application, signal, score}")
    out, seen = [], set()
    for c in given:
        slug, sig = c.get("application"), c.get("signal")
        if not isinstance(slug, str) or not slug.strip():
            raise RowError(f"match.candidates: application must be a slug, got {slug!r}")
        if slug in seen:
            raise RowError(f"match.candidates: {slug} is listed twice")
        seen.add(slug)
        if not (roots.js / "applications" / slug / "application.md").is_file():
            raise RowError(f"match.candidates: no application {slug} (applications/{slug}/application.md)")
        if sig not in SIGNALS or sig == "none":
            raise RowError(f"match.candidates: {slug} signal {sig!r} is not one of "
                           + ", ".join(k for k in SIGNALS if k != "none"))
        cscore = _number(c.get("score"), f"match.candidates {slug} score")
        if abs(cscore - SIGNALS[sig]) > EPS:
            raise RowError(f"match.candidates: {slug} signal {sig} scores {SIGNALS[sig]:.2f}, not {cscore:.2f}")
        out.append({"application": slug, "signal": sig, "score": SIGNALS[sig]})
    out.sort(key=lambda c: -c["score"])
    if abs(out[0]["score"] - score) > EPS:
        raise RowError(f"match.candidates: the top candidate scores {out[0]['score']:.2f} but match.score is "
                       f"{score:.2f}")
    top = {c["application"] for c in out if abs(c["score"] - score) <= EPS}
    if app and app not in top:
        raise RowError(f"application {app} is not a top candidate in match.candidates")
    if runner_up and not any(c["application"] == runner_up and abs(c["score"] - ru_score) <= EPS for c in out):
        raise RowError(f"match.runner_up {runner_up} {ru_score:.2f} is not in match.candidates with that score")
    return out


def add_rows(roots: cu.Roots, classified: Path, rows: Iterable[Any], *, today: date | None = None,
             dry_run: bool = False) -> AddReport:
    """Validate and append rows (see the module docstring). dry_run: the same outcomes, nothing appended."""
    ledger_path = roots.js / LEDGER
    messages = load_messages([classified]) if Path(classified).is_file() else []
    seen: set[str] = set()
    for row in jc.read_jsonl(ledger_path):
        seen |= {str(row.get("message_id") or "")}
    report = AddReport()
    logged_at = jc.run_stamp(today, z=True)
    for row in rows:
        if not isinstance(row, dict):
            report.outcomes.append(Outcome("", "refused", f"not a JSON object: {cu.one_line(str(row), 80)}"))
            continue
        mid = str(row.get("message_id") or "").strip()
        rec = find_message(messages, mid) if mid else None
        ids = (_ids(rec) if rec else set()) | ({mid} if mid else set())
        if not row.get("correction") and mid:
            slug = recorded_in(roots, ids)
            if slug:
                report.outcomes.append(Outcome(mid, "skipped", f"already recorded in {slug}"))
                continue
            if ids & seen:
                report.outcomes.append(Outcome(mid, "skipped", "duplicate (already in state/mail-events.jsonl)"))
                continue
        try:
            if not Path(classified).is_file():
                raise RowError(f"classified file not found: {classified}")
            clean = _validate(roots, row, rec, Path(classified))
        except RowError as exc:
            report.outcomes.append(Outcome(mid, "refused", str(exc)))
            continue
        outcome = Outcome(mid, "appended", row=clean)
        m = clean["match"]
        if clean["decision"] == "auto" and not (clean["application"] and auto_allowed(m["score"], m["runner_up_score"])):
            clean["decision"] = "review"
            outcome.downgraded = True
            outcome.reason = (f"decision auto -> review (score {m['score']:.2f}, lead "
                              f"{m['score'] - m['runner_up_score']:.2f}"
                              + ("" if clean["application"] else ", no application")
                              + f"; auto needs an application, {AUTO_MIN_SCORE:.2f} or more and a "
                                f"{AUTO_MIN_LEAD:.2f} lead)")
        clean["logged_at"] = logged_at
        if not dry_run:
            jc.append_jsonl(ledger_path, clean)
        seen |= ids
        report.outcomes.append(outcome)
    return report


# --------------------------------------------------------------------------- handoffs
def _harvest_day(path: Path) -> date | None:
    m = re.match(r"gmail-(\d{4}-\d{2}-\d{2})", path.name)
    return jc.parse_date(m.group(1)) if m else None


def _in_window(day: date | None, on: date | None, since: date | None) -> bool:
    if day is None:
        return False
    return day == on if on else day >= since  # type: ignore[operator]


def harvest_files(roots: cu.Roots, on: date | None, since: date | None) -> list[Path]:
    folder = roots.harvest
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob("gmail-*.jsonl") if _in_window(_harvest_day(p), on, since))


def _holds(roots: cu.Roots) -> list[tuple[str, str, set[str]]]:
    """(approval id, status, source message ids) for every calendar hold request in work/approvals."""
    import calendar_hold as chd
    out = []
    folder = roots.approvals
    for path in sorted(folder.glob("A-*.md")) if folder.is_dir() else []:
        try:
            appr = cu.parse_approval(path)
        except Exception:            # an unreadable approval is not a hold we can cite
            continue
        if not any(i.get("channel") == "calendar" for i in appr.items):
            continue
        try:
            payload = chd.approved_payload(appr)
        except (chd.HoldError, ValueError, IndexError):
            continue
        source = ((payload.get("extendedProperties") or {}).get("private") or {}).get("source_message_id")
        out.append((appr.id, str(appr.meta.get("status") or ""), {str(source)} if source else set()))
    return out


def _approved_items(roots: cu.Roots) -> dict[str, list[dict[str, Any]]]:
    by_thread: dict[str, list[dict[str, Any]]] = {}
    folder = roots.outbox
    for path in sorted(folder.glob("O-*.md")) if folder.is_dir() else []:
        item, _ = cu.read_record(path)
        if item.get("status") in SENT_STATES and item.get("thread_id"):
            by_thread.setdefault(str(item["thread_id"]), []).append(item)
    return by_thread


def _slug(row: dict[str, Any]) -> str:
    """The application a handoff names: only an attached row (auto or the owner's decision) has one."""
    if row.get("decision") == "review":
        return "unmatched (review)"
    return str(row.get("application") or "unmatched")


def _candidates(row: dict[str, Any]) -> str:
    m = row.get("match") or {}
    listed = m.get("candidates")
    if isinstance(listed, list) and listed:
        return ", ".join(f"{c.get('application')} {float(c.get('score') or 0):.2f}"
                         + (f" ({c['signal']})" if c.get("signal") else "") for c in listed if isinstance(c, dict))
    parts = []
    if row.get("application") or m.get("score"):
        parts.append(f"{row.get('application') or '(top candidate not named)'} {float(m.get('score') or 0):.2f} "
                     f"({m.get('signal')})")
    if m.get("runner_up"):
        parts.append(f"{m['runner_up']} {float(m.get('runner_up_score') or 0):.2f}")
    return ", ".join(parts) or "no candidate"


def handoff_lines(roots: cu.Roots, *, on: date | None = None, since: date | None = None,
                  harvest: list[Path] | None = None) -> tuple[list[str], list[str]]:
    rows = [r for r in jc.read_jsonl(roots.js / LEDGER) if _in_window(cu.local_date(r.get("logged_at")), on, since)]
    files = harvest if harvest is not None else harvest_files(roots, on, since)
    messages = load_messages(files)
    holds = _holds(roots) if any(r.get("label") in HOLD_LABELS for r in rows) else []
    hand: list[str] = []
    needs: list[str] = []
    for row in rows:
        label, mid, slug = row.get("label"), row.get("message_id"), _slug(row)
        if label in HOLD_LABELS:
            rec = find_message(messages, str(mid))
            ids = {str(mid)} | (_ids(rec) if rec else set())
            cited = [f"{aid} ({status})" for aid, status, src in holds if src & ids and status not in HOLD_GONE]
            hold = "hold " + ", ".join(cited) if cited else "no hold request"
            hand.append(f"- [ ] messenger: {label} {mid} · {slug} · {hold}")
        elif label == "rejection":
            for role in ("recruiter", "legal-visa"):
                hand.append(f"- [ ] {role}: rejection {mid} · {slug}")
        elif label == "offer":
            for role in ("recruiter", "legal-visa", "negotiator"):
                hand.append(f"- [ ] {role}: offer {mid} · {slug}")
            needs.append(f"- [ ] offer {mid} · {slug}: the owner confirms the written offer")
        if label == "suspected_scam":
            needs.append(f"- [ ] suspected_scam {mid}: likely scam, attached to no application")
        elif row.get("decision") == "review":
            needs.append(f"- [ ] review: {label} {mid} · candidates {_candidates(row)} · decide the application")
    items = _approved_items(roots)
    for rec in messages:
        if rec.get("direction") != "out" or str(rec.get("thread_id") or "") not in items:
            continue
        mid = rec.get("id") or rec.get("message_id")
        cands = items[str(rec["thread_id"])]
        approved = [m for m in cands if m.get("status") == "approved"]
        for item in approved or cands:
            note = ""
            if item.get("status") == "sent" and str(item.get("message_id") or "") in _ids(rec):
                note = " (already marked sent)"
            elif len(approved or cands) > 1:
                note = f" (ambiguous: {len(approved or cands)} items in thread {rec['thread_id']})"
            hand.append(f"- [ ] messenger: {item.get('id')} -> {mid}{note}")
    return hand, needs


def handoffs(roots: cu.Roots, *, day: date | None = None, since: date | None = None,
             harvest: list[Path] | None = None) -> str:
    if day is None and since is None:
        day = jc.today()
    hand, needs = handoff_lines(roots, on=day, since=since, harvest=harvest)
    window = day.isoformat() if day else f"since {since.isoformat()}"  # type: ignore[union-attr]
    out = [f"## Handoffs ({window}, from scripts/mail_events.py)"] + (hand or ["- none"])
    out += ["", "## Needs the owner (mail-events rows)"] + (needs or ["- none"])
    return "\n".join(out)


# --------------------------------------------------------------------------- receipt
ROUTINE = "js-mail-triage"
_DECISION_RE = re.compile(r"\bdecision:(?P<value>[^|\n]*)", re.I)
_UNDECIDED = {"", "-", "_", "__", "?", "tbd", "pending", "(blank)", "blank"}   # as dashboard.py reads them
NEEDS_OWNER_LABELS = ("offer", "suspected_scam")


def _kernel_watchdog() -> Any:
    """The OS kernel's watchdog module, loaded by path (a PyPI package of the same name must not shadow it)."""
    import importlib.util
    name = "os_kernel_watchdog"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, jc._KERNEL / "watchdog.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module                 # dataclasses in the module look themselves up here
    spec.loader.exec_module(module)
    return module


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def receipt_notes(roots: cu.Roots, day: date) -> tuple[list[str], bool, bool]:
    """(notes, needs_you, has_harvest) for the day, from state."""
    notes: list[str] = []
    files = sorted(p for p in roots.harvest.glob(f"gmail-{day.isoformat()}*.classified.jsonl")) \
        if roots.harvest.is_dir() else []
    labels: dict[str, int] = {}
    for rec in load_messages(files):
        label = str((rec.get("classification") or {}).get("label") or "unclassified")
        labels[label] = labels.get(label, 0) + 1
    if files:
        counts = ", ".join(f"{k} {v}" for k, v in sorted(labels.items(), key=lambda kv: (-kv[1], kv[0])))
        notes.append(f"classified: {', '.join(p.name for p in files)} ({_plural(sum(labels.values()), 'message')}): "
                     + (counts or "none"))
    else:
        notes.append(f"classified: no classified harvest for {day.isoformat()} (state/harvest/)")
    rows = [r for r in jc.read_jsonl(roots.js / LEDGER) if cu.local_date(r.get("logged_at")) == day]
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        latest[str(row.get("message_id"))] = row
    by = {d: sum(r.get("decision") == d for r in latest.values()) for d in DECISIONS}
    attachable = [r for r in latest.values() if r.get("decision") in ("auto", DECISIONS[-1]) and r.get("application")]
    attached = [r for r in attachable if recorded_in(roots, [str(r.get("message_id"))])]
    notes.append(f"mail events: rows logged: {len(rows)} ({len(latest)} messages; auto {by['auto']}, "
                 f"owner {by[DECISIONS[-1]]}, review {by['review']}); attached in applications: {len(attached)} "
                 f"of {len(attachable)} attachable (tracker-ops reconciles)")
    review_rows = [str(r.get("message_id")) for r in latest.values() if r.get("decision") == "review"]
    review_file = roots.js / "state" / "review" / f"mail-{day.isoformat()}.md"
    open_items = 0
    if review_file.is_file():
        open_items = sum(1 for line in review_file.read_text(encoding="utf-8").splitlines()
                         for m in [_DECISION_RE.search(line)] if m and m.group("value").strip().lower() in _UNDECIDED)
    held = []
    if review_rows:
        held.append(f"{len(review_rows)} ledger row{'s' if len(review_rows) != 1 else ''} ({', '.join(review_rows)})")
    if open_items:
        held.append(f"{_plural(open_items, 'open item')} in state/review/{review_file.name}")
    notes.append("held for review: " + ("; ".join(held) or "none"))
    pending = [aid for aid, status, _src in _holds(roots) if status == "pending"]
    notes.append(f"calendar holds requested (pending): " + (f"{len(pending)} ({', '.join(pending)})" if pending else "none"))
    flagged = [f"{r.get('label')} {r.get('message_id')}" for r in latest.values() if r.get("label") in NEEDS_OWNER_LABELS]
    if flagged:
        notes.append("for the owner: " + ", ".join(flagged))
    needs_you = bool(pending or review_rows or open_items or flagged)
    return notes, needs_you, bool(files)


def write_run_receipt(roots: cu.Roots, day: date, now: Any, *, status: str | None = None,
                      extra_notes: Iterable[str] = (), dry_run: bool = False) -> tuple[Path | None, str]:
    notes, needs_you, has_harvest = receipt_notes(roots, day)
    notes += [n for n in extra_notes if str(n).strip()]
    status = status or ("ok" if has_harvest else "warn")
    sentinel = "NEEDS_YOU" if needs_you else "NO_ACTION"
    text = "\n".join([f"## Run {now.isoformat(timespec='seconds')} · {status}", f"- sentinel: {sentinel}"]
                     + [f"- {n}" for n in notes])
    if dry_run:
        return None, text
    wd = _kernel_watchdog()
    return wd.write_receipt(roots.os, ROUTINE, now, status=status, notes=notes, sentinel=sentinel), text


# --------------------------------------------------------------------------- CLI
def _roots(args: Any) -> cu.Roots:
    js = Path(args.root) if args.root else jc.JS_ROOT
    return cu.Roots(js=js, os=Path(args.os_root) if args.os_root else (jc.os_root_for(js) if args.root else jc.OS_ROOT))


def _under(root: Path, value: str) -> Path:
    """A relative path resolves under --root first; the working directory is only the fallback, so a run from the
    department folder with --root elsewhere never reads the real state/harvest/ in its place."""
    p = Path(value)
    if p.is_absolute() or (root / p).exists():
        return p if p.is_absolute() else root / p
    return p if p.exists() else root / p


def _read_rows(args: Any, root: Path) -> list[Any]:
    rows: list[Any] = []
    for text in args.row or []:
        try:
            rows.append(json.loads(text))
        except json.JSONDecodeError as exc:
            rows.append(f"invalid JSON ({exc.msg}): {text}")
    for ref in args.rows or []:
        path = _under(root, ref)
        if not path.is_file():
            rows.append(f"rows file not found: {ref}")
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                rows.append(f"{path.name}:{n} invalid JSON ({exc.msg})")
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="job-search root (default: this department)")
    common.add_argument("--os-root", help="runtime root for work/approvals (default: derived)")
    a = sub.add_parser("add", parents=[common], help="validate rows and append them to state/mail-events.jsonl")
    a.add_argument("--classified", required=True, help="the classified harvest file holding the messages")
    a.add_argument("--row", action="append", help="one row as JSON (repeatable)")
    a.add_argument("--rows", action="append", help="a JSONL file of rows (repeatable)")
    a.add_argument("--today", type=jc.cli_date, default=None, help="run date YYYY-MM-DD (stamps logged_at)")
    h = sub.add_parser("handoffs", parents=[common], help="print the Handoffs and Needs the owner checklists")
    when = h.add_mutually_exclusive_group()
    when.add_argument("--date", type=jc.cli_date, default=None, help="the run day YYYY-MM-DD (default: today)")
    when.add_argument("--since", type=jc.cli_date, default=None, help="this day YYYY-MM-DD or later")
    h.add_argument("--harvest", action="append", help="harvest file(s) to read outbound mail from "
                   "(default: state/harvest/gmail-<date>*.jsonl)")
    h.add_argument("--today", type=jc.cli_date, default=None, help="accepted for symmetry; --date wins")
    r = sub.add_parser("receipt", parents=[common], help="append this run's js-mail-triage receipt (watchdog format)")
    r.add_argument("--date", type=jc.cli_date, default=None, help="the run day YYYY-MM-DD (default: today)")
    r.add_argument("--today", type=jc.cli_date, default=None, help="accepted for symmetry; --date wins")
    r.add_argument("--status", choices=("ok", "warn", "failed"), default=None,
                   help="run status (default: ok, or warn when the day has no classified harvest)")
    r.add_argument("--note", action="append", default=[], help="an extra receipt line (repeatable)")
    r.add_argument("--now", default=None, help="ISO timestamp of the run (naive = local time)")
    r.add_argument("--dry-run", action="store_true", help="print the section, write nothing")
    args = ap.parse_args(argv)
    roots = _roots(args)
    if args.cmd == "add":
        rows = _read_rows(args, roots.js)
        if not rows:
            print("refused: no rows given (--row or --rows)")
            print("appended 0, skipped 0, downgraded 0, refused 1")
            return 1
        report = add_rows(roots, _under(roots.js, args.classified), rows, today=args.today)
        for line in report.lines():
            print(line)
        print(report.summary())
        return report.exit_code()
    if args.cmd == "handoffs":
        day = args.date or (None if args.since else (args.today or jc.today()))
        harvest = [_under(roots.js, p) for p in args.harvest] if args.harvest else None
        print(handoffs(roots, day=day, since=args.since, harvest=harvest))
        return 0
    if args.cmd == "receipt":
        from datetime import datetime, time as dtime
        day = args.date or args.today or jc.today()
        if args.now:
            try:
                now = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
            except ValueError:
                ap.error(f"--now {args.now!r} is not an ISO timestamp")
            now = now.astimezone() if now.tzinfo else now
        elif day == jc.today():
            now = datetime.now().replace(microsecond=0)
        else:
            now = datetime.combine(day, dtime(12, 0))
        path, text = write_run_receipt(roots, day, now, status=args.status, extra_notes=args.note,
                                       dry_run=args.dry_run)
        print(text)
        print(f"receipt: {path}" if path else "receipt: not written (dry-run)")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
