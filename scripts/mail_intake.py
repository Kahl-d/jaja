#!/usr/bin/env python3
"""Mail intake: the hourly collector that carries Gmail into the tracker (pull, classify and match mail into the
tracker on a schedule, never acting outward). Deterministic; nothing is sent or drafted.

    python3 scripts/mail_intake.py [--root R] [--os-root O] [--today D] [--since D] [--no-pull] [--dry-run]
                                   [--out DIR] [--json]

One run, in order:
  1. pull      gmail_pull.harvest with the READ token (incremental: history cursor); --no-pull skips it. A failed
               pull (no token, network) is a `warn` note; the steps below still run on the stored harvest.
  2. classify  every state/harvest/gmail-<day>*.jsonl of the window (day >= --since) gets its new rows classified
               and APPENDED to its .classified.jsonl twin. Stored rows are never reclassified or rewritten (their
               resolve answers, L3, stay byte for byte; the reclassify_safe.py merge rule: rows merged by message
               id, a stored row wins). The new rows' L3 requests go to state/harvest/l3-queue-<today>.jsonl (the
               offline agent queue; no mail text leaves the Mac). A file with no twin is classified whole.
  3. match     pipeline mail of the window (message day >= --since) is matched to applications and written to
               state/mail-events.jsonl through mail_events.add_rows (every rule of `mail_events.py add` applies),
               then `tracker.reconcile` attaches those rows (only them, held ones listed once in
               state/review/mail-<date>.md). Signals, per application (the strongest one counts):
                 thread         the message's thread is in the application's thread_ids (0.95)
                 company_title  the sender (display name, address, domain) or subject names the company, the
                                subject or body holds the application's title, and the message falls in the
                                application's date window (0.80)
                 company_sole   the company is named and dated as above and it has one application folder (0.75)
                 company        the company is named, but no title, or outside the date window (0.60)
               Date window: the application's applied day (applied_at, else its first applied event) minus one
               day, onwards; an ats_confirmation also no later than 7 days after it. An application with no applied
               day is never in the window (a confirmation for it is an unrecorded apply: review).
               Decision: ats_confirmation, ats_assessment and rejection are `auto` when the best candidate scores
               0.75 or more with a 0.20 lead and the label confidence is 0.75 or more, else `review`. Outreach
               labels are rows only in an application's thread (auto: an email_in note). interview_invite,
               interview_scheduling, offer and suspected_scam are always `review`: a flag for the owner, never a
               stage jump. No row: a Google Calendar notice (calendar-notification@google.com sends the
               Accepted, Declined and Reminder notes of invites already on the calendar), or a machine or
               scheduling-tool notice that names no application.
               Evidence is the classifier's verbatim sentence (else the snippet's first sentence); a message with
               neither is counted, never written.
  4. alerts    job_alert mail of the window's classified files goes to job_alerts.collect (its own ledger
               state/job-alerts.jsonl keeps it idempotent), jobs merged and saved as `job_alerts.py` does.
  5. people    only the window's people rows (a person sender, not a machine; outreach, networking, pipeline or
               review labels; and the owner's own outbound rows) go to prospects.ingest_events; never a whole old
               harvest file (an old harvest makes hundreds of junk records). Timeline keys keep it idempotent.
  6. receipt   one run section in <os-root>/logs/runs/js-mail-intake/<date>.md (watchdog format):
               sentinel NEEDS_YOU when an invite, scheduling ask, offer, scam or a new review row waits for the owner.

Idempotent: a second run over the same mail adds nothing (ledgers and timeline keys dedup by message id).
--dry-run writes nothing under --root or --os-root: it prints the plan (and with --out DIR writes
DIR/mail-intake-plan.json); people mail is counted, not ingested.
--since YYYY-MM-DD (default: the day before --today) bounds both the harvest files (by name) and the messages (by
their local day in the configured time zone, identity.timezone).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))
import js_common as jc  # noqa: E402
import js_comms_util as cu  # noqa: E402
import mail_classify as mc  # noqa: E402
import mail_events as me  # noqa: E402
import reclassify_safe as rs  # noqa: E402
import tracker as tr  # noqa: E402

ROUTINE = "js-mail-intake"
CONFIRM_DAYS = 7                    # an ATS confirmation arrives within days of the apply
EARLY_DAYS = 1                      # applied_at is a local day stamped by hand; one day of slack before it
AUTO_LABELS = ("ats_confirmation", "ats_assessment", "rejection")
OWNER_LABELS = ("interview_invite", "interview_scheduling", "offer", "suspected_scam")
OUTREACH = tuple(sorted(mc.PROSPECT_LABELS))
MATCH_LABELS = AUTO_LABELS + OWNER_LABELS + OUTREACH
PEOPLE_LABELS = frozenset(mc.PROSPECT_LABELS | mc.PIPELINE_LABELS | {mc.REVIEW})
NOT_A_COMPANY = frozenset({"inc", "llc", "ltd", "corp", "corporation", "co", "pbc", "the", "hiring", "team",
                           "recruiting", "talent", "careers", "jobs"})
TITLE_STOP = frozenset({"and", "the", "of", "a", "an", "for", "to", "in", "at"})
_TOKEN = re.compile(r"[a-z0-9]+")
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


# --------------------------------------------------------------------------- text
def tokens(text: Any, stop: frozenset[str] = frozenset()) -> list[str]:
    return [t for t in _TOKEN.findall(str(text or "").lower().replace("&", " and ")) if t not in stop]


def has_phrase(hay: list[str], phrase: list[str]) -> bool:
    n = len(phrase)
    return bool(n) and any(hay[i:i + n] == phrase for i in range(len(hay) - n + 1))


# --------------------------------------------------------------------------- applications
@dataclass
class App:
    slug: str
    company: list[str]
    company_alt: list[str]
    title: list[str]
    applied: date | None
    threads: set[str]
    company_key: str


def _applied_day(meta: dict[str, Any], body: str) -> date | None:
    day = jc.parse_date(meta.get("applied_at"))
    if day:
        return day
    days = [cu.local_date(e.at) for e in tr.parse_events(body) if e.type.lower() in ("applied", "submitted")]
    days = [d for d in days if d]
    return min(days) if days else None


def load_apps(root: Path) -> list[App]:
    out = []
    for slug, meta, body in tr.iter_applications(root) or []:
        company = tokens(meta.get("company"), NOT_A_COMPANY)
        alt = [t for t in str(meta.get("company_slug") or "").lower().split("-") if t and t not in NOT_A_COMPANY]
        title = tokens(re.sub(r"\(.*?\)", " ", str(meta.get("title") or "")), TITLE_STOP)
        out.append(App(slug, company, alt, title, _applied_day(meta, body),
                       {str(t) for t in meta.get("thread_ids") or []}, "".join(company or alt)))
    return out


# --------------------------------------------------------------------------- matching
def _sender_tokens(rec: dict[str, Any]) -> list[str]:
    name, email = cu.parse_address(rec.get("from"))
    local, _, domain = email.partition("@")
    labels = cu.registrable_domain(domain).split(".")[:-1] if domain else []
    return tokens(name) + ["|"] + tokens(local.split("+")[0]) + ["|"] + labels + ["|"] + tokens(rec.get("subject"))


def company_named(app: App, hay: list[str]) -> bool:
    if app.company and has_phrase(hay, app.company):
        return True
    if app.company_alt and has_phrase(hay, app.company_alt):
        return True
    return bool(app.company_key) and len(app.company_key) >= 4 and app.company_key in hay


def in_window(app: App, label: str, day: date | None) -> bool:
    if app.applied is None or day is None:
        return False
    if day < app.applied - timedelta(days=EARLY_DAYS):
        return False
    return not (label == "ats_confirmation" and day > app.applied + timedelta(days=CONFIRM_DAYS))


def candidates(rec: dict[str, Any], label: str, apps: list[App]) -> list[dict[str, Any]]:
    """Every application the message could belong to, strongest first: [{application, signal, score}]."""
    hay = _sender_tokens(rec)
    text = tokens(rec.get("subject"), TITLE_STOP) + ["|"] + tokens(rec.get("body") or rec.get("snippet"), TITLE_STOP)
    day = cu.local_date(rec.get("internal_date"))
    per_company: dict[str, int] = {}
    for a in apps:
        per_company[a.company_key] = per_company.get(a.company_key, 0) + 1
    out = []
    tid = str(rec.get("thread_id") or "")
    for a in apps:
        if tid and tid in a.threads:
            signal = "thread"
        elif not company_named(a, hay):
            continue
        elif in_window(a, label, day) and has_phrase(text, a.title):
            signal = "company_title"
        elif in_window(a, label, day) and per_company.get(a.company_key) == 1:
            signal = "company_sole"
        else:
            signal = "company"
        out.append({"application": a.slug, "signal": signal, "score": me.SIGNALS[signal]})
    out.sort(key=lambda c: (-c["score"], c["application"]))
    return out


def evidence_for(rec: dict[str, Any]) -> str | None:
    quote = str((rec.get("classification") or {}).get("evidence") or "")
    if quote and me.evidence_problem(quote, rec) is None:
        return " ".join(quote.split())
    for source in (rec.get("snippet"), rec.get("body")):
        for m in _SENTENCE.finditer(str(source or "")):
            sentence = " ".join(m.group(0).split())
            if len(sentence.split()) >= me.MIN_EVIDENCE_WORDS and me.evidence_problem(sentence, rec) is None:
                return sentence
    return None


def _calendar_notice(rec: dict[str, Any]) -> bool:
    return cu.parse_address(rec.get("from"))[1] in mc.CALENDAR_SENDERS


def _scheduling_notice(rec: dict[str, Any]) -> bool:
    _, email = cu.parse_address(rec.get("from"))
    return mc.is_machine_sender(email) or cu.registrable_domain(cu.email_domain(email)) in mc.SCHEDULING_DOMAINS


def plan_row(rec: dict[str, Any], apps: list[App]) -> tuple[dict[str, Any] | None, str]:
    """(mail-events row or None, why). The row is what `mail_events.py add` takes."""
    cls = rec.get("classification") or {}
    label = str(cls.get("label") or "")
    cands = candidates(rec, label, apps)
    if label in OUTREACH:
        cands = [c for c in cands if c["signal"] == "thread"]
        if not cands:
            return None, "outreach outside every application's threads (prospects)"
    if label == "interview_scheduling" and _calendar_notice(rec):
        return None, "Google Calendar notice (an invite already on the calendar)"
    if label == "interview_scheduling" and not cands and _scheduling_notice(rec):
        return None, "machine or scheduling-tool notice naming no application"
    evidence = evidence_for(rec)
    if evidence is None:
        return None, "no verbatim evidence sentence"
    conf = float(cls.get("confidence") or 0.0)
    top = cands[0] if cands else None
    runner = cands[1] if len(cands) > 1 else None
    ru_score = runner["score"] if runner else 0.0
    auto = (top is not None and (label in AUTO_LABELS or label in OUTREACH)
            and top["signal"] in ("thread",) + tr.AUTO_SIGNALS and me.auto_allowed(top["score"], ru_score)
            and conf + 1e-6 >= me.AUTO_MIN_SCORE)
    sole_top = top is not None and (runner is None or runner["score"] < top["score"] - 1e-6)
    row = {"message_id": str(rec.get("id")), "label": label, "confidence": conf, "evidence": evidence,
           "application": top["application"] if auto or sole_top else None,
           "match": {"signal": top["signal"] if top else "none", "score": top["score"] if top else 0.0,
                     "runner_up": runner["application"] if runner else None, "runner_up_score": ru_score,
                     "candidates": cands},
           "decision": "auto" if auto else "review", "prospect": None}
    return row, "auto" if auto else "review"


# --------------------------------------------------------------------------- classify (append-only merge)
def _day_of(path: Path) -> date | None:
    m = re.match(r"gmail-(\d{4}-\d{2}-\d{2})", path.name)
    return jc.parse_date(m.group(1)) if m else None


def window_sources(roots: cu.Roots, since: date) -> list[Path]:
    folder = roots.harvest
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob("gmail-*.jsonl") if not p.name.endswith(rs.CLASSIFIED_SUFFIX)
                  and (_day_of(p) or date.min) >= since)


@dataclass
class ClassifyResult:
    file: str
    new: int = 0
    l3_queued: int = 0
    kept: int = 0
    resolved_kept: int = 0


def classify_new(source: Path, target: Path, queue: Path, roots: cu.Roots,
                 portals: dict[str, str] | None) -> ClassifyResult:
    """Append the classification of `source`'s rows missing from `target` (written atomically; the stored lines
    stay byte for byte, asserted before the swap). `target` may differ from source's twin (a dry run's copy)."""
    res = ClassifyResult(file=target.name)
    stored = rs.parse_lines(target.read_bytes(), target) if target.is_file() else []
    raw = target.read_bytes() if target.is_file() else b""
    have = {str(row.get("id")) for _, row in stored if row and row.get("id") not in (None, "")}
    res.kept = len(have)
    res.resolved_kept = sum(1 for _, row in stored if row and rs.is_resolved(row.get("classification")))
    fresh = [r for r in jc.read_jsonl(source) if r.get("type") not in mc.PASSTHROUGH_TYPES
             and str(r.get("id")) not in have and r.get("id") not in (None, "")]
    if not fresh:
        return res
    crm = mc.CrmIndex.from_folder(roots.prospects)
    before = len(jc.read_jsonl(queue)) if queue.is_file() else 0
    with tempfile.TemporaryDirectory(prefix="mail-intake-") as tmp:
        src = Path(tmp) / "new.jsonl"
        src.write_text("".join(rs.serialize(r) for r in fresh), encoding="utf-8")
        out = mc.classify_file(src, decider=mc.AgentQueueBackend(queue), out=Path(tmp) / "out.jsonl", crm=crm,
                               portals=portals)
        new_lines = [rs.serialize(r) for r in jc.read_jsonl(out)]
    res.new = len(new_lines)
    res.l3_queued = (len(jc.read_jsonl(queue)) if queue.is_file() else 0) - before
    head = raw.decode("utf-8")
    if head and not head.endswith("\n"):
        head += "\n"
    data = (head + "".join(new_lines)).encode("utf-8")
    if not data.startswith(raw):
        raise rs.PreconditionError(f"{target}: the stored rows would change")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmpname = tempfile.mkstemp(dir=target.parent, prefix="." + target.name + ".", suffix=".tmp")
    try:
        with open(fd, "wb") as fh:
            fh.write(data)
        if (target.read_bytes() if target.is_file() else b"") != raw:
            raise rs.PreconditionError(f"{target}: changed on disk since it was read; run again")
        Path(tmpname).replace(target)
    finally:
        if Path(tmpname).exists():
            Path(tmpname).unlink()
    return res


# --------------------------------------------------------------------------- the run
@dataclass
class Report:
    dry_run: bool
    since: str
    pull: dict[str, Any] = field(default_factory=dict)
    classify: list[dict[str, Any]] = field(default_factory=list)
    match: dict[str, Any] = field(default_factory=dict)
    reconcile: dict[str, Any] = field(default_factory=dict)
    alerts: dict[str, Any] = field(default_factory=dict)
    people: dict[str, Any] = field(default_factory=dict)
    owner: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@contextmanager
def _workspace(roots: cu.Roots, dry_run: bool) -> Iterator[Path]:
    """The folder classified files are read from and written to: state/harvest, or a scratch copy in a dry run."""
    if not dry_run:
        yield roots.harvest
        return
    with tempfile.TemporaryDirectory(prefix="mail-intake-dry-") as tmp:
        yield Path(tmp)


def _pull(roots: cu.Roots, today: date, harvest_fn: Callable[..., dict[str, Any]] | None) -> dict[str, Any]:
    import gmail_pull as gp
    if harvest_fn is None:
        import gmail_auth
        try:
            service = gmail_auth.build_service("read")
        except gmail_auth.AuthError as exc:
            return {"error": f"gmail read login: {cu.one_line(str(exc), 160)}"}
        harvest_fn = lambda r, t: gp.harvest(service, r, today=t)  # noqa: E731
    try:
        out = harvest_fn(roots, today)
    except Exception as exc:                       # the stored harvest still gets matched
        return {"error": f"{type(exc).__name__}: {cu.one_line(str(exc), 160)}"}
    return {k: out.get(k) for k in ("mode", "listed", "new", "residue", "cursor_moved") if k in out}


def run(roots: cu.Roots, *, today: date, since: date, now: datetime, dry_run: bool = False, pull: bool = True,
        harvest_fn: Callable[..., dict[str, Any]] | None = None, portals: dict[str, str] | None = None) -> Report:
    rep = Report(dry_run=dry_run, since=since.isoformat())
    if pull and not dry_run:
        rep.pull = _pull(roots, today, harvest_fn)
        if "error" in rep.pull:
            rep.warnings.append(f"pull failed: {rep.pull['error']}")
    else:
        rep.pull = {"skipped": "dry-run" if dry_run else "--no-pull"}
    portals = mc.portal_index(roots.js) if portals is None else portals
    with _workspace(roots, dry_run) as work:
        queue = (work if dry_run else roots.harvest) / f"l3-queue-{today:%Y-%m-%d}.jsonl"
        files: list[Path] = []
        for source in window_sources(roots, since):
            twin = source.with_name(source.name[:-len(".jsonl")] + rs.CLASSIFIED_SUFFIX)
            target = twin if not dry_run else work / twin.name
            if dry_run and twin.is_file():
                shutil.copyfile(twin, target)
            try:
                res = classify_new(source, target, queue, roots, portals)
            except rs.PreconditionError as exc:
                rep.warnings.append(f"classify {source.name}: {exc}")
                continue
            rep.classify.append(res.__dict__)
            files.append(target)
        _match(roots, rep, files, since, today, now, dry_run)
        _alerts(roots, rep, files, now, dry_run)
        _people(roots, rep, files, since, today, dry_run)
    return rep


def _window_rows(path: Path, since: date) -> list[dict[str, Any]]:
    return [r for r in jc.read_jsonl(path) if isinstance(r.get("classification"), dict)
            and (cu.local_date(r.get("internal_date")) or date.min) >= since]


def _match(roots: cu.Roots, rep: Report, files: list[Path], since: date, today: date, now: datetime,
           dry_run: bool) -> None:
    apps = load_apps(roots.js)
    counts = {"rows": 0, "auto": 0, "review": 0, "no_row": 0, "appended": 0, "skipped": 0, "refused": 0,
              "awaiting_l3": 0}
    reasons: dict[str, int] = {}
    planned: list[dict[str, Any]] = []
    window_ids: set[str] = set()
    seen: set[str] = set()
    for path in files:
        rows = []
        for rec in _window_rows(path, since):
            mid = str(rec.get("id"))
            if mid in seen:
                continue
            seen.add(mid)
            label = rec["classification"].get("label")
            if label == mc.REVIEW:
                counts["awaiting_l3"] += 1
                continue
            if label not in MATCH_LABELS or rec.get("direction") == "out":
                continue
            row, why = plan_row(rec, apps)
            if row is None:
                counts["no_row"] += 1
                reasons[why] = reasons.get(why, 0) + 1
                continue
            window_ids.add(mid)
            counts[why] += 1
            rows.append(row)
        if not rows:
            continue
        added = me.add_rows(roots, path, rows, today=today, dry_run=dry_run)
        for o in added.outcomes:
            counts[o.status] += 1
            if o.status == "refused":
                rep.warnings.append(f"mail-events refused {o.message_id}: {o.reason}")
            if o.status == "appended" and o.row:
                planned.append(o.row)
                if o.row["label"] in OWNER_LABELS or o.row["decision"] == "review":
                    cands = ", ".join(c["application"] for c in o.row["match"]["candidates"]) or "no candidate"
                    rep.owner.append(f"{o.row['label']} {o.message_id} ({o.row['decision']}; {cands})")
    counts["no_row_reasons"] = reasons
    rep.match = counts
    _reconcile(roots, rep, planned, window_ids, today, now, dry_run)


def _reconcile(roots: cu.Roots, rep: Report, planned: list[dict[str, Any]], window_ids: set[str], today: date,
               now: datetime, dry_run: bool) -> None:
    root = roots.js
    if dry_run:
        listed = tr.listed_for_review(root)
        results = [tr.reconcile_row(root, row, now, {}, True, planned) for row in planned]
        held = [r for r in results if r.status == "held"]
        new_listed = sum(1 for r in held if not tr._id_re(r.message_id).search(listed))
    else:
        if not (root / tr.MAIL_LEDGER).is_file():
            rep.reconcile = {"note": "no state/mail-events.jsonl yet (no pipeline mail logged)"}
            return
        results, _review = tr.reconcile(root, now, today, dry_run=False, only=window_ids, skip_listed=True)
        new_listed = None
    by: dict[str, int] = {}
    attached: list[str] = []
    for r in results:
        by[r.status] = by.get(r.status, 0) + 1
        if r.status == "attached":
            attached.append(f"{r.message_id} -> {r.slug}: {r.reason}")
    rep.reconcile = {"counts": by, "attached": attached,
                     "held": [f"{r.message_id}: {cu.one_line(r.reason, 140)}" for r in results if r.status == "held"]}
    if new_listed is not None:
        rep.reconcile["would_list_in_review"] = new_listed


def _alerts(roots: cu.Roots, rep: Report, files: list[Path], now: datetime, dry_run: bool) -> None:
    import job_alerts as ja
    platforms = ja.load_platforms(roots.js)
    if not platforms or not files:
        rep.alerts = {"skipped": "no targets/platforms.yaml alert senders" if not platforms else "no files"}
        return
    run = ja.collect(roots.js, now, files=files, platforms=platforms, title_filter=ja.load_title_filter(roots.js))
    existing = ja.load_jobs(roots.js) if (roots.js / "jobs").is_dir() else {}
    out = ja.merge_into(existing, run.records, now)
    if not dry_run:
        for rid in dict.fromkeys(out["new"] + out["updated"] + out["known"]):
            ja.save_job(roots.js, existing[rid])
        ja.write_outputs(roots.js, run, now)
    rep.alerts = {"alerts": run.alerts, "already_processed": run.skipped, "jobs_new": len(out["new"]),
                  "jobs_resighted": len(out["updated"]), "review": len(run.review)}


def people_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        label = (r.get("classification") or {}).get("label")
        _, email = cu.parse_address(r.get("from"))
        if r.get("direction") == "out":
            out.append(r)
        elif label in PEOPLE_LABELS and label != mc.SCAM and not mc.is_machine_sender(email):
            out.append(r)
    return out


def _people(roots: cu.Roots, rep: Report, files: list[Path], since: date, today: date, dry_run: bool) -> None:
    import prospects as ps
    events: list[dict[str, Any]] = []
    n = 0
    for path in files:
        rows = people_rows(_window_rows(path, since))
        n += len(rows)
        rel = f"state/harvest/{path.name}"
        events += [{**ev, "source_file": rel} for ev in ps.gmail_events(rows)]
    if dry_run or not events:
        rep.people = {"rows": n, "events": len(events), "ingested": False if dry_run else bool(events)}
        return
    out = ps.ingest_events(roots, events, today)
    rep.people = {"rows": n, "events": len(events), "created": out.get("created"), "updated": out.get("updated"),
                  "new_events": out.get("new_events"), "errors": len(out.get("errors") or [])}


# --------------------------------------------------------------------------- receipt and CLI
def receipt_notes(rep: Report) -> tuple[list[str], str, str]:
    m, rc = rep.match, rep.reconcile.get("counts", {})
    new = sum(c["new"] for c in rep.classify)
    l3 = sum(c["l3_queued"] for c in rep.classify)
    notes = [f"pull: {json.dumps(rep.pull, sort_keys=True)}",
             f"classified: {new} new rows in {len(rep.classify)} files ({l3} to the L3 queue); stored rows kept",
             f"mail events: appended {m.get('appended', 0)} (auto {m.get('auto', 0)}, review {m.get('review', 0)}), "
             f"skipped {m.get('skipped', 0)}, refused {m.get('refused', 0)}, no row {m.get('no_row', 0)}, "
             f"awaiting L3 {m.get('awaiting_l3', 0)}",
             "tracker: " + (", ".join(f"{k} {v}" for k, v in sorted(rc.items())) or rep.reconcile.get("note", "none")),
             f"job alerts: {json.dumps(rep.alerts, sort_keys=True)}",
             f"people: {json.dumps(rep.people, sort_keys=True)}"]
    notes += [f"attached: {a}" for a in rep.reconcile.get("attached", [])]
    notes += [f"for the owner: {k}" for k in rep.owner]
    notes += [f"warning: {w}" for w in rep.warnings]
    status = "failed" if any(w.startswith("classify") for w in rep.warnings) else ("warn" if rep.warnings else "ok")
    return notes, status, "NEEDS_YOU" if rep.owner else "NO_ACTION"


def write_receipt(roots: cu.Roots, rep: Report, now: datetime) -> Path | None:
    notes, status, sentinel = receipt_notes(rep)
    if rep.dry_run:
        return None
    return me._kernel_watchdog().write_receipt(roots.os, ROUTINE, now, status=status, notes=notes, sentinel=sentinel)


def main(argv: list[str] | None = None, now: datetime | None = None,
         harvest_fn: Callable[..., dict[str, Any]] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", help="job-search root (default: this department)")
    ap.add_argument("--os-root", help="runtime root for the receipt (default: derived)")
    ap.add_argument("--today", type=jc.cli_date, default=None, help="run day YYYY-MM-DD (default: today)")
    ap.add_argument("--since", type=jc.cli_date, default=None, help="window start YYYY-MM-DD (default: --today - 1)")
    ap.add_argument("--no-pull", action="store_true", help="skip the Gmail harvest; use the stored harvest")
    ap.add_argument("--dry-run", action="store_true", help="write nothing under --root / --os-root; print the plan")
    ap.add_argument("--out", help="with --dry-run: write <out>/mail-intake-plan.json")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    js = Path(args.root) if args.root else jc.JS_ROOT
    roots = cu.Roots(js=js, os=Path(args.os_root) if args.os_root else (jc.os_root_for(js) if args.root else jc.OS_ROOT))
    today = args.today or jc.today()
    now = tr.run_now(today, now)
    since = args.since or (today - timedelta(days=1))
    rep = run(roots, today=today, since=since, now=now, dry_run=args.dry_run, pull=not args.no_pull,
              harvest_fn=harvest_fn)
    path = write_receipt(roots, rep, now)
    notes, status, sentinel = receipt_notes(rep)
    if args.dry_run and args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "mail-intake-plan.json").write_text(json.dumps(rep.as_dict(), indent=2, default=str) + "\n",
                                                   encoding="utf-8")
    if args.json:
        print(json.dumps({**rep.as_dict(), "status": status, "sentinel": sentinel,
                          "receipt": str(path) if path else None}, indent=2, default=str))
    else:
        print(f"## mail_intake {'dry-run (nothing written)' if args.dry_run else 'run'} · {status} · {sentinel}")
        for line in notes:
            print(f"- {line}")
        print(f"receipt: {path}" if path else "receipt: not written (dry-run)")
    return 2 if status == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
