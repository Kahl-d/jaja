#!/usr/bin/env python3
"""next_up.py: NEXT-UP, the one ranked list of every role worth the owner's next hour, generated. It replaces a hand-kept table that
drifted from the other queues. This script is its one writer; the orchestrator's and the owner's calls
live in hub/NEXT-UP-overrides.yaml, which it reads and never writes.

Rows (read only from the records):
  applications/<slug>/  every folder before "applied" and not ended: a tracker record (application.md), or a
                        package-only folder whose fit.md scores 4.0 or more (the fit gate)
  Fresh 10              today's fresh roles (fresh10.pick) that have no folder yet
States (first word of the state column; agency_status counts them):
  READY         a page apply_pack would ship: the newest variant for the job (resumes/variants/<v>/meta.yaml or
                <v>/inline/meta.yaml `job:`) passes apply_pack.check_variant and check_recruiter, the same checks
                `apply_pack.py build` runs (editor signed on this resume's hash, a literal APPROVE from the company's
                recruiter); what is left is the pack and the owner's yes
  PAGE          a page exists but apply_pack refuses it today; the refusal's own words are the reason
  JOB           scored, no page yet (a package-only folder is listed only at fit 4.0 or more)
  SCORE         not scored yet (the fit-scorer first)
  PEOPLE FIRST  a first-contact note (outbox kind intro) to a person at the company is pending or approved and not
                sent: send it first, then apply through that person; conditional replies and
                follow-ups never count; an override `waive-people-first` lifts it
  BLOCKED       stopped (state/recruiter/stopped.yaml or an override `stop`), or a years ask above the owner's years count with no
                person inside, or 8 or more years even with one. A JD that states no years takes the title's
                prior, as in Fresh 10 (fresh10.implied_years): Senior or Lead about 5, Staff or Principal 8; never
                for a dual-level title, an early-career JD or an analyst/associate ladder
Rank: fresh10.expected (the MEASURED channel rate, warm or cold, from
interviews.rates, times the stated priors: freshness, years STRETCH, sponsorship, fit), then divided by the hours still
needed: READY 0.5, PEOPLE FIRST 0.25 (a note), JOB 3, SCORE 3.5. An override `pin` puts a row first (in rank order). A
people-first row is ranked at the warm rate: that is what sending the note buys.
 (importance is recalculated on every run; FOCUS picks from the top and never from stale things). On top of the
base, every constant at the top of this file:
  freshness   inside 48 h the fresh10 prior (x1.2 under 24 h, x1.0 under 48 h), then x0.8; the long tail is the decay below.
  age decay   full weight under FULL_WEIGHT_DAYS (7) of age, linear down to DECAY_FLOOR (0.3) at DECAY_END_DAYS (21), the floor
              after. Age is posted_at; a role with no posted date uses first_seen (shown as "seen"); a queue lead its day.
  warmth      a person-sourced row (a hiring post, a referral or prospect job, a focused or posts lead) x PERSON_BOOST; a
              person who wrote last within REPLY_DAYS at the company x REPLY_BOOST; an item due (the application's
              next_action_at, a prospect's next_touch_at at the company) x DUE_BOOST; the product is capped at BOOST_CAP.
  STALE       a role older than STALE_DAYS (30), or whose last_seen is older than LIVE_DAYS (7: the scan no longer sees it), or
              a lead older than STALE_DAYS that no lane has claimed, leaves the list for the "STALE" table below it, unless a
              person is live on it (a note to send, a reply, a due item, a hiring post), but never past STALE_HARD_DAYS (90). Stale rows are in res["stale"],
              never in res["rows"]: FOCUS and apply_batch read the list, so they never claim a stale row first.
  excluded    a job at "applied" or later (any record), closed or skipped: never a row.
  leads       hunt findings are first-class input: an open or claimed queue item of kind focused, posts or hunt in the last
              QUEUE_LOOKBACK_DAYS of state/recruiter/queue-*.yaml (`day_queue.py add`), a prospect with source hunt, and a job with
              source hunt (with no folder yet) are ranked beside the roles with their age. A queue or prospect lead has state LEAD;
              its hours come from its effort (EFFORT_HOURS); the rate is warm for focused and posts, cold for hunt, times LEAD_PRIOR
              (no role, fit or employer gate yet); a lead titled HELD or PARKED is not listed.
  chance      the "chance" column is the honest expected interviews (decayed, not boosted); the "/h" rate is the rank score (boosted).
: among the rows that are not pinned or BLOCKED, a role a person's hiring post on X or LinkedIn names
(hunt_score.from_post) comes before every portal listing, then the expected-interviews order; the years gate still holds,
and a company the owner's fit reports skipped gets no post-first place (hunt_score.skipped_company).
Mode: `applying: paused` lists every row with no due date; `applying: open` dates READY rows today.

Usage: python3 scripts/next_up.py [--root R] [--now ISO] [--json] [--focus [N]] [--write [PATH]]
       prints the list; --write saves it (default hub/NEXT-UP.md; switching the hub to this file is the orchestrator's call).
REFRESH: `python3 scripts/next_up.py --write --focus`  recalculates every row from the records, rewrites
       hub/NEXT-UP.md and prints the "FOCUS next" top 10 (rank, role, why, age). `scripts/dashboard.py` prints the same block
       (focus_next below) in DASHBOARD.md on every write. Stale rows land under "STALE" and are never claimed first.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_ats  # noqa: E402
import fit_gates  # noqa: E402
import fresh10  # noqa: E402
import hunt_score  # noqa: E402
import js_common as jc  # noqa: E402
import tracker as tr  # noqa: E402
from js_common import JS_ROOT, fm  # noqa: E402

OVERRIDES = Path("hub") / "NEXT-UP-overrides.yaml"
HOURS = {"READY": 0.5, "PEOPLE FIRST": 0.25, "PAGE": 1.0, "JOB": 3.0, "SCORE": 3.5}
FIRST_CONTACT = {"intro"}
BEFORE_APPLIED = {"discovered", "fit_checked", "tailored", "on_hold", None, ""}
UNSENT = {"pending", "approved"}
FIT_GATE = 4.0
HEADER = "| # | role | fit | why here | chance | posted | state | page | tier |"
#: freshness, warmth and staleness weights. Change a number here, nowhere else.
FULL_WEIGHT_DAYS = 7.0       # under this age a role keeps its full weight
DECAY_END_DAYS = 21.0        # linear decay reaches DECAY_FLOOR here
DECAY_FLOOR = 0.3
STALE_DAYS = 30.0            # older than this, or not live, is the STALE bucket
STALE_HARD_DAYS = 90.0       # older than this is stale even with a person live on it (the person stays in the people lanes)
LIVE_DAYS = 7.0              # last_seen older than this: the scans no longer see the posting
PERSON_BOOST = 1.5           # a hiring post, a referral or prospect job, a focused or posts lead
REPLY_BOOST = 2.0            # a person at the company wrote to the owner last, within REPLY_DAYS
REPLY_DAYS = 14
DUE_BOOST = 1.5              # next_action_at / next_touch_at is due
BOOST_CAP = 3.0
PERSON_SOURCES = {"prospect", "referral", "inbound", "inbound-new", "outbound", "event"}
HUNT_SOURCE = "hunt"
LEAD_KINDS = {"focused": True, "posts": True, "hunt": False}     # kind -> warm rate (people) or cold
LEAD_PRIOR = {"focused": 0.8, "posts": 0.4, "hunt": 0.4}         # a lead has no role, fit or employer gate yet: discounted
QUEUE_LOOKBACK_DAYS = 14
EFFORT_HOURS = {"E1": 0.5, "E2": 1.0, "E3": 2.0, "E4": 3.0, "E5": 5.0}
ON_HOLD_RE = re.compile(r"^\s*(held|parked|on hold)\b", re.I)    # a lead its own lane titled as held is not claimable
CLOSED_PROSPECT = {"closed", "do_not_contact"}
FOCUS_N = 10


def freshness(hours_old: float | None) -> float:
    """fresh10's prior inside 48 h; after that x0.8 and age_decay takes the long tail. Undated: 0.4."""
    if hours_old is None:
        return 0.4
    if hours_old <= 24:
        return 1.2
    if hours_old <= 48:
        return 1.0
    return 0.8


def age_decay(days: float | None) -> float:
    """: 1.0 under FULL_WEIGHT_DAYS, linear to DECAY_FLOOR at DECAY_END_DAYS, the floor after; unknown age 1.0."""
    if days is None or days <= FULL_WEIGHT_DAYS:
        return 1.0
    if days >= DECAY_END_DAYS:
        return DECAY_FLOOR
    return round(1.0 - (1.0 - DECAY_FLOOR) * (days - FULL_WEIGHT_DAYS) / (DECAY_END_DAYS - FULL_WEIGHT_DAYS), 4)


def age_label(days: float | None, basis: str = "posted") -> str:
    if days is None:
        return "not dated"
    return f"{int(days)} d" + ("" if basis == "posted" else f" ({basis})")


def _rank(value: Any) -> float:
    """A pin's rank as a number; a pin with no rank (or one that is not a number) goes first after the ranked pins."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 1e9


def load_overrides(root: Path) -> dict[str, Any]:
    path = Path(root) / OVERRIDES
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        data = {}
    rows = {str(o.get("id")): o for o in data.get("overrides") or [] if isinstance(o, dict) and o.get("id")}
    return {"applying": str(data.get("applying") or "paused"), "rows": rows}


def unsent_notes(root: Path, index: Any = None) -> dict[str, list[str]]:
    """company slug (resolved through company_index, so 'acme-corp' and 'acme' are one) -> outbox ids of first-contact
    notes to a person there that are pending or approved and not sent."""
    resolve = (lambda s: (index.resolve(s)[0] or s) if s else s) if index is not None else (lambda s: s)
    people = {}
    for path, meta, _ in jc.iter_records(Path(root) / "prospects"):
        people[str(meta.get("id") or path.stem)] = resolve(str(meta.get("company_slug") or ""))
    out: dict[str, list[str]] = {}
    for path, meta, _ in jc.iter_records(Path(root) / "outbox"):
        if str(meta.get("status") or "") in UNSENT and str(meta.get("kind") or "") in FIRST_CONTACT \
                and str(meta.get("channel") or "") != "linkedin-profile-edit":
            slug = people.get(str(meta.get("prospect") or ""))
            if slug:
                out.setdefault(slug, []).append(str(meta.get("id") or path.stem))
    return out


def page_dirs(root: Path) -> dict[str, list[str]]:
    """job id -> variant folder names for it, newest build first (meta.yaml at the folder's top or in inline/)."""
    import yaml
    found: dict[str, list[tuple[str, str]]] = {}
    base = Path(root) / "resumes" / "variants"
    for meta_path in sorted(list(base.glob("*/meta.yaml")) + list(base.glob("*/inline/meta.yaml"))):
        folder = meta_path.parent.parent if meta_path.parent.name == "inline" else meta_path.parent
        try:
            meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        job = Path(str(meta.get("job") or "")).stem
        if job:
            found.setdefault(job, []).append((str(meta.get("built_at") or ""), folder.name))
    return {j: list(dict.fromkeys(n for _, n in sorted(v, reverse=True))) for j, v in found.items()}


def signed(ctx: Any, name: str, app: dict[str, Any] | None) -> str | None:
    """None (or "inline" when only the inline render ships) when apply_pack would ship this page (check_variant, then
    check_recruiter, as `apply_pack.py build` does); else apply_pack's own refusal, prefixed "refused: "."""
    import apply_pack as ap
    if ctx is None:
        return "refused: apply_pack could not load its context"
    a = dict(app or {}, resume_variant=name)
    last = None
    for choice in ("classic", "inline"):
        try:
            ap.check_variant(ctx, name, a, choice)
            vdir = ap.variant_dir(ctx.root, name, a)
            ap.check_recruiter(vdir, ap.rel(ctx.root, vdir), ap.sha256_file(vdir / "resume.yaml"))
            return None if choice == "classic" else "inline"
        except ap.Refusal as exc:
            last = str(exc)
    return f"refused: {last}"


def page_state(ctx: Any, names: list[str], app: dict[str, Any] | None,
               linked: str | None = None) -> tuple[str | None, str | None, str | None, str]:
    """(READY or PAGE or None, the variant, the reason for a PAGE, the PDF choice). READY only when the page the
    application itself links (`linked`: apply_pack.variant_dir of the record, what `apply_pack.py build` and
    apply_batch use) ships; a signed page the record does not link is a PAGE until it is linked."""
    if not names and not linked:
        return None, None, None, "classic"
    first_refusal = None
    if linked:
        why = signed(ctx, linked, app)
        if why is None or why == "inline":
            return "READY", linked, None, "inline" if why == "inline" else "classic"
        first_refusal = why.removeprefix("refused: ")      # the linked page's own refusal leads
    for name in names:
        if name == linked:
            continue
        why = signed(ctx, name, app)
        if why is None or why == "inline":
            how = "the application links no page" if not linked else f"the application links {linked}"
            return "PAGE", name, (f"signed, but {how}: set resume_variant to {name} (tracker-ops), then it is READY"
                                  if app is not None else "signed; the role has no application folder yet: "
                                  "python3 scripts/tracker.py new --job <id>"), "inline" if why == "inline" else "classic"
        first_refusal = first_refusal or why.removeprefix("refused: ")
    return "PAGE", (linked or names[0]), first_refusal, "classic"


def _fit(folder: Path, job: dict[str, Any]) -> float | None:
    try:
        score = fm.load(folder / "fit.md")[0].get("score")
    except (OSError, ValueError):
        score = None
    return fresh10._num(score if score is not None else job.get("fit_score"))


def _job_id(folder: Path, meta: dict[str, Any]) -> str:
    raw = meta.get("job")
    if not raw:
        try:
            raw = fm.load(folder / "fit.md")[0].get("job")
        except (OSError, ValueError):
            raw = None
    return str(raw or "").strip("[]' \"")


def _days(now: datetime, when: datetime | None) -> float | None:
    return None if when is None else max(0.0, (now - when).total_seconds() / 86400)


def person_signals(root: Path, now: datetime, index: Any = None) -> dict[str, dict[str, str]]:
    """: company slug (resolved through company_index) -> {"reply": prospect id, "due": prospect id}. reply: a person
    there wrote last (last_inbound_at after last_outbound_at) within REPLY_DAYS; due: their next_touch_at is today or past."""
    resolve = (lambda s: (index.resolve(s)[0] or s) if s else s) if index is not None else (lambda s: s)
    out: dict[str, dict[str, str]] = {}
    for path, meta, _ in jc.iter_records(Path(root) / "prospects"):
        if str(meta.get("status") or "") in CLOSED_PROSPECT:
            continue
        slug = resolve(str(meta.get("company_slug") or ""))
        if not slug:
            continue
        pid = str(meta.get("id") or path.stem)
        inbound, outbound = fresh10._when(meta.get("last_inbound_at")), fresh10._when(meta.get("last_outbound_at"))
        if inbound and (_days(now, inbound) or 0) <= REPLY_DAYS and (outbound is None or outbound <= inbound):
            out.setdefault(slug, {}).setdefault("reply", pid)
        due = fresh10._when(meta.get("next_touch_at"))
        if due and due.date() <= now.date():
            out.setdefault(slug, {}).setdefault("due", pid)
    return out


def is_hunt(meta: dict[str, Any]) -> bool:
    """A record a hunt lane added: source hunt, or hunt among its sources or tags."""
    vals = [meta.get("source")] + list(meta.get("sources") or []) + list(meta.get("tags") or [])
    return any(str(v).strip().lower() == HUNT_SOURCE for v in vals if v)


def queue_leads(root: Path, now: datetime) -> list[dict[str, Any]]:
    """: open or claimed queue items of kind focused, posts or hunt from the last QUEUE_LOOKBACK_DAYS of
    state/recruiter/queue-*.yaml (what `day_queue.py add` writes), one per title: the earliest day is its age, the
    newest file's status is its status (done and blocked leave). Carried items are one lead."""
    import day_queue
    base = Path(root) / day_queue.DEFAULT_OUT
    first: dict[str, str] = {}
    latest: dict[str, dict[str, Any]] = {}
    for path in sorted(base.glob("queue-*.yaml")):
        m = re.fullmatch(r"queue-(\d{4}-\d{2}-\d{2})\.yaml", path.name)
        if not m or (now.date() - datetime.fromisoformat(m.group(1)).date()).days > QUEUE_LOOKBACK_DAYS:
            continue
        try:
            items = day_queue.load(path).get("items") or []
        except Exception:  # noqa: BLE001 - an unreadable queue file adds no leads
            continue
        for it in items:
            if it.get("kind") not in LEAD_KINDS:
                continue
            title = " ".join(str(it.get("title") or "").split())
            if not title:
                continue
            days = [d.group(1) for d in (re.match(r"Q-(\d{4}-\d{2}-\d{2})-", str(x or "")) for x in (it.get("id"), it.get("carried_from"))) if d]
            first[title] = min([first.get(title, m.group(1)), *days])
            latest[title] = it
    leads = []
    for title, it in latest.items():
        if it.get("status") not in ("open", "claimed"):
            continue
        day = datetime.fromisoformat(first[title]).replace(tzinfo=timezone.utc)
        leads.append({"id": str(it.get("id")), "title": title, "kind": it.get("kind"), "effort": it.get("effort") or "E2",
                      "claimed": it.get("status") == "claimed", "links": it.get("links") or {}, "note": it.get("note"),
                      "added": first[title], "age_days": _days(now, day)})
    return leads


def hunt_prospects(root: Path, now: datetime) -> list[dict[str, Any]]:
    """Prospects a hunt lane added (source hunt): a person to talk to, ranked as a lead with the prospect's age."""
    out = []
    for path, meta, _ in jc.iter_records(Path(root) / "prospects"):
        if not is_hunt(meta) or str(meta.get("status") or "") in CLOSED_PROSPECT:
            continue
        added = fresh10._when(meta.get("first_contact_at") or meta.get("created"))
        out.append({"id": str(meta.get("id") or path.stem), "title": " · ".join(
            str(x) for x in (meta.get("name"), meta.get("company"), meta.get("role_hint") or meta.get("title")) if x),
            "age_days": _days(now, added), "added": added.date().isoformat() if added else None,
            "due": bool(fresh10._when(meta.get("next_touch_at")) and fresh10._when(meta.get("next_touch_at")).date() <= now.date())})
    return out


def lead_row(key: str, title: str, kind: str, age: float | None, added: str | None, rates: dict[str, Any], hours: float,
             warm: bool, claimed: bool, due: bool, note: str | None, why: str) -> dict[str, Any]:
    """One LEAD row (a queue item or a hunt prospect). Same base: the measured rate, the unscored-fit and unknown-
    sponsorship priors, then age decay and warmth; stale (older than STALE_DAYS, unclaimed, nothing due) leaves the list."""
    base = fresh10.expected(rates, warm, 48.0, False, "unknown", None) * LEAD_PRIOR.get(kind, 0.4)
    decay = age_decay(age)
    boost = min(BOOST_CAP, (PERSON_BOOST if warm else 1.0) * (DUE_BOOST if due else 1.0))
    exp = round(base * decay, 4)
    stale = age is not None and age > STALE_DAYS and not claimed and not due
    return {"key": key, "job": key, "slug": None, "company": f"{kind} lead", "title": title.replace("·", "/"), "url": None,
            "fit": None, "years": None, "years_implied": False, "stretch": False, "warm": [], "notes": [], "state": "LEAD",
            "why": why + (f"; {note}" if note else ""), "expected": exp, "per_hour": round(exp * boost / hours, 4),
            "posted": added, "age_days": age, "age_basis": "added", "decay": decay, "boost": round(boost, 2), "pin": None,
            "override_note": None, "stage": None, "page": None, "pdf": "classic", "post": None,
            "stale": f"added {int(age)} d ago, older than {int(STALE_DAYS)} d, unclaimed" if stale else None}


def build(root: Path, now: datetime) -> dict[str, Any]:
    root = Path(root)
    jobs = fetch_ats.load_jobs(root)
    rates = fresh10.channel_rates(root, now)
    history = hunt_score.fit_history_from(jobs.values())       # the owner's skips win over a post
    over = load_overrides(root)
    stop = fresh10.stopped(root)
    from company_index import CompanyIndex
    import strategy_review
    import apply_pack
    index = CompanyIndex.load(root)
    notes = unsent_notes(root, index)
    signals = person_signals(root, now, index)
    page_of = page_dirs(root)
    try:
        ctx = apply_pack.load_ctx(root, now.astimezone(fresh10.TZ).date())
    except Exception:  # noqa: BLE001 - every page then reads PAGE with this reason
        ctx = None
    rows, seen_jobs = [], set()

    def row(key: str, job_id: str, rec: dict[str, Any], stage: str | None, fit: float | None, slug: str | None,
            app: dict[str, Any] | None = None) -> None:
        company_slug = str(rec.get("company_slug") or "")
        resolved = (index.resolve(company_slug)[0] or company_slug) if company_slug else ""
        jd = rec.get("_jd_text") or fetch_ats.jd_text_from_body(rec.get("_body") or "")
        # a Senior or Staff title with no stated ask implies years, as in Fresh 10 (one prior, two lists)
        years, implied = fresh10.implied_years(str(rec.get("title") or ""), fit_gates.required_years(jd) if jd else None, jd)
        warm = strategy_review.warm_paths(root, index, company_slug) if company_slug else []
        posted = fresh10._when(rec.get("posted_at"))
        hours_old = (now - posted).total_seconds() / 3600 if posted else None
        o = over["rows"].get(key) or over["rows"].get(job_id) or {}
        pending = [] if o.get("action") == "waive-people-first" else notes.get(resolved, [])
        rule = fresh10.years_rule(years, bool(warm) or bool(pending))   # a note to a person inside is the path
        linked = None
        if app is not None and slug and (Path(root) / "applications" / slug / "application.md").is_file():
            try:
                linked = apply_pack.variant_dir(Path(root), slug, app).name
                linked = linked if (Path(root) / "resumes" / "variants" / linked).is_dir() else None
            except Exception:  # noqa: BLE001 - an unreadable link reads as none
                linked = None
        pstate, variant, refusal, pdf = page_state(ctx, page_of.get(job_id, []), app, linked)
        if job_id in stop or (slug and slug in stop) or o.get("action") == "stop":
            state, why = "BLOCKED", "stopped" + (f" ({o.get('note')})" if o.get("note") else "")
        elif stage == "on_hold":
            state, why = "BLOCKED", "on hold in the tracker (its last event says why)"
        elif rule == "OUT":
            asks = (f"the title '{rec.get('title')}' implies about {years} years" + ("" if jd else " (the JD was not read)")
                    if implied else f"asks {years} years")
            inside = bool(warm) or bool(pending)          # the years ceiling is the fit gate even through a person
            state, why = "BLOCKED", (f"{asks}: the years ceiling is the fit gate, even through a person inside" if inside
                                     else f"{asks}, above the owner's years count, and no person inside")
        elif pending:
            state, why = "PEOPLE FIRST", f"send {', '.join(pending)} first, then apply through that person"
        elif pstate == "READY":
            state, why = "READY", f"page {variant} passes apply_pack: build the pack" + \
                (" with --pdf inline" if pdf == "inline" else "") + ", then the owner's yes"
        elif pstate == "PAGE":
            state, why = "PAGE", f"page {variant}: {refusal}"
        elif fit is None:
            state, why = "SCORE", "score first (fit-scorer)"
        else:
            state, why = "JOB", "role package and page"
        post = None if hunt_score.skipped_company(rec, history) else hunt_score.from_post(rec)
        if post and state != "BLOCKED":
            why += f"; hiring post {post}: a person to talk to"
        spons = str(rec.get("sponsorship") or "unknown").lower()
        base = fresh10.expected(rates, bool(warm) or bool(pending), 48.0, rule == "STRETCH", spons, fit) / 1.0
        #: age (posted, else first seen), then warmth and due items; stale leaves the list unless a person is live
        seen_first = fresh10._when(rec.get("first_seen"))
        age_days, basis = (_days(now, posted), "posted") if posted else (_days(now, seen_first), "seen")
        decay = age_decay(age_days)
        sig = signals.get(resolved, {})
        # the tracker's own pre-applied next_action_at is the day the stage began, not a deadline: only a hand-written
        # next action (not in tr.TRACKER_ACTIONS) or a posting deadline counts as due
        hand = str((app or {}).get("next_action") or "")
        due_app = fresh10._when((app or {}).get("next_action_at")) if hand and hand not in tr.TRACKER_ACTIONS else None
        due_job = fresh10._when(rec.get("deadline"))
        due = bool(sig.get("due")) or bool(due_app and due_app.date() <= now.date()) \
            or bool(due_job and 0 <= (due_job - now).total_seconds() <= 3 * 86400)
        person = bool(post) or str(rec.get("source") or "") in PERSON_SOURCES
        boost = min(BOOST_CAP, (PERSON_BOOST if person else 1.0) * (REPLY_BOOST if sig.get("reply") else 1.0)
                    * (DUE_BOOST if due else 1.0))
        live_person = bool(pending) or bool(post) or bool(sig.get("reply")) or due
        last_seen = fresh10._when(rec.get("last_seen"))
        not_live = last_seen is not None and (_days(now, last_seen) or 0) > LIVE_DAYS
        stale = None
        if state != "BLOCKED" and age_days is not None and basis == "posted" and age_days > STALE_HARD_DAYS:
            stale = f"posted {int(age_days)} d ago, older than {int(STALE_HARD_DAYS)} d even with a person on it"
        elif state != "BLOCKED" and not live_person:
            if age_days is not None and basis == "posted" and age_days > STALE_DAYS:
                stale = f"posted {int(age_days)} d ago, older than {int(STALE_DAYS)} d"
            elif not_live:
                stale = f"last seen {int(_days(now, last_seen))} d ago: no longer live in the scans"
        if boost > 1.0 and state != "BLOCKED":
            why += "; boost x%.1f (%s)" % (boost, ", ".join(x for x in (
                "person-sourced" if person else "", "reply from " + sig["reply"] if sig.get("reply") else "",
                "due" if due else "") if x))
        exp = round(base * freshness(hours_old) * decay, 4)             # 48 h gives fresh10's x1.0; then the age prior
        per_hour = 0.0 if state == "BLOCKED" else round(exp * boost / HOURS[state], 4)
        rows.append({"key": key, "job": job_id, "slug": slug, "company": rec.get("company"), "title": rec.get("title"),
                     "url": rec.get("apply_url") or rec.get("source_url"), "fit": fit, "years": years,
                     "years_implied": implied, "stretch": rule == "STRETCH", "warm": warm, "notes": pending, "state": state, "why": why,
                     "expected": exp, "per_hour": per_hour, "posted": posted.astimezone(fresh10.TZ).date().isoformat()
                     if posted else None, "pin": _rank(o.get("rank")) if o.get("action") == "pin" else None,
                     "override_note": o.get("note"), "stage": stage, "page": variant, "pdf": pdf, "post": post,
                     "age_days": age_days, "age_basis": basis, "decay": decay, "boost": round(boost, 2), "stale": stale})

    folders = sorted(p for p in (root / "applications").iterdir() if p.is_dir()) if (root / "applications").is_dir() else []
    folders.sort(key=lambda p: (not (p / "application.md").is_file(), p.name))   # a record wins over a fit-only folder
    for folder in folders:                       # a job that any record took past applied is never a row
        if (folder / "application.md").is_file():
            m = fm.load(folder / "application.md")[0]
            if str(m.get("stage") or "") not in BEFORE_APPLIED:
                seen_jobs.add(_job_id(folder, m))
    for folder in folders:
        slug = folder.name
        app = folder / "application.md"
        meta, stage = {}, None
        if app.is_file():
            meta, body = fm.load(app)
            stage = str(meta.get("stage") or "") or None
            if stage not in BEFORE_APPLIED:
                seen_jobs.add(_job_id(folder, meta))                 # its fit-only twin is not a second row
                continue
        job_id = _job_id(folder, meta)
        rec = jobs.get(job_id) or {}
        if job_id in seen_jobs:
            continue                                                    # one row per job (two folders can share one)
        if job_id:
            seen_jobs.add(job_id)
        if str(rec.get("status") or "") in ("closed", "skipped", "applied"):
            continue
        fit = _fit(folder, rec)
        if not app.is_file() and (fit is None or fit < FIT_GATE):
            continue                                                    # a package-only folder below the fit gate
        rec = dict(rec, company=rec.get("company") or meta.get("company"), title=rec.get("title") or meta.get("title"))
        row(slug, job_id, rec, stage, fit, slug, meta)
    applied_jobs = {j for j in (_job_id(f, fm.load(f / "application.md")[0]) for f in (root / "applications").glob("*")
                                if (f / "application.md").is_file()) if j}
    left_out = []                                                      # missing is not empty: a page never vanishes
    for job_id, names in sorted(page_of.items()):                       # a page with no folder (built from a package)
        variant = names[0]
        rec = jobs.get(job_id) or {}
        if job_id in seen_jobs or job_id in applied_jobs:
            continue
        status = str(rec.get("status") or "") if rec else "no job record"
        if not rec or status in ("closed", "skipped", "applied"):
            if status != "applied":
                left_out.append({"job": job_id, "page": variant, "why": status + (f", gate {rec.get('gate')}" if rec.get("gate") else "")})
            continue
        seen_jobs.add(job_id)
        row(job_id, job_id, rec, None, fresh10._num(rec.get("fit_score")), None)
    pick = fresh10.pick(root, now.astimezone(fresh10.TZ))
    for r in pick["rows"]:
        if r["id"] not in seen_jobs:
            row(r["id"], r["id"], jobs.get(r["id"]) or {}, None, r.get("fit"), None)
            seen_jobs.add(r["id"])
    #: a job a hunt lane added (source hunt) with no folder yet is ranked with its age; applied, closed, skipped and
    # gate-failed jobs are not
    for job_id, rec in sorted(jobs.items()):
        if job_id in seen_jobs or job_id in applied_jobs or not is_hunt(rec) \
                or str(rec.get("status") or "") in ("closed", "skipped", "applied") or str(rec.get("gate") or "") == "fail":
            continue
        seen_jobs.add(job_id)
        row(job_id, job_id, rec, None, fresh10._num(rec.get("fit_score")), None)
    leads: list[dict[str, Any]] = []
    for lead in queue_leads(root, now):
        if ON_HOLD_RE.match(lead["title"]):
            continue
        refs = [str(v).strip("[]' \"") for v in lead["links"].values() if isinstance(v, str)]
        if any(r and r in applied_jobs for r in refs):
            continue                                           # its job is applied: not a lead
        leads.append(lead_row(lead["id"], lead["title"], lead["kind"], lead["age_days"], lead["added"], rates,
                              EFFORT_HOURS.get(str(lead["effort"]), 1.0), LEAD_KINDS[lead["kind"]], lead["claimed"], False,
                              lead["note"], f"{lead['kind']} item {lead['id']} (day_queue.py add), effort {lead['effort']}"))
    for pr in hunt_prospects(root, now):
        leads.append(lead_row(f"prospect:{pr['id']}", pr["title"] or pr["id"], "hunt", pr["age_days"], pr["added"], rates, 1.0,
                              True, False, pr["due"], None, f"prospect {pr['id']} (source hunt)"))
    rows += leads
    stale = [r for r in rows if r.get("stale")]               #: parked below the list, never claimed first
    rows = [r for r in rows if not r.get("stale")]
    order = lambda r: (r["pin"] is None, r["pin"] or 0, r["state"] == "BLOCKED", r["post"] is None, -r["per_hour"],   # noqa: E731
                       -(r["fit"] or 0), str(r["key"]))
    rows.sort(key=order)
    stale.sort(key=lambda r: (-(r["age_days"] or 0), str(r["key"])))
    counts = {s: sum(r["state"] == s for r in rows) for s in ("READY", "PEOPLE FIRST", "PAGE", "JOB", "SCORE", "BLOCKED")}
    if any(r["state"] == "LEAD" for r in rows):
        counts["LEAD"] = sum(r["state"] == "LEAD" for r in rows)
    counts["STALE"] = len(stale)
    return {"now": now.isoformat(), "applying": over["applying"], "rates": rates, "rows": rows, "stale": stale,
            "left_out": left_out, "counts": counts}


def _cell(v: Any) -> str:
    return str(v if v is not None else "").replace("|", "/").replace("\n", " ")


def _row_line(i: str, r: dict[str, Any], applying: str) -> str:
    title = f"[{_cell(r['title'])}]({r['url']})" if r["url"] else _cell(r["title"])
    role = f"{_cell(r['company'])} · {title}" if r["state"] != "LEAD" else f"{_cell(r['company'])}: {title}"
    why = r["why"] + (f"; warm: {', '.join(r['warm'])}" if r["warm"] else "") + \
        ("; STRETCH" if r["stretch"] else "") + (f"; note: {_cell(r['override_note'])}" if r["override_note"] else "") + \
        (f"; STALE: {r['stale']}" if r.get("stale") else "")
    fit = f"{r['fit']:.1f}" if r["fit"] is not None else "unscored"
    tier = "pinned" if r["pin"] is not None else ""
    if applying == "open" and r["state"] == "READY":
        tier = (tier + " due today").strip()
    posted = f"{r['posted'] or 'not dated'} ({age_label(r.get('age_days'), r.get('age_basis') or 'posted')})" \
        if r.get("age_days") is not None else f"{r['posted'] or 'not dated'}"
    return (f"| {i} | {role} | {fit} | {_cell(why)} | {r['expected']:.1%}, {r['per_hour']:.1%}/h | {posted} | "
            f"{r['state']} `{r['job'] or r['key']}` | {r['page'] or r['slug'] or ''} | {tier} |")


def render(res: dict[str, Any], today: str) -> str:
    r_ = res["rates"]
    mode = ("Applying is PAUSED outside the focused lanes: ranked, nothing is due."
            if res["applying"] != "open" else "Applying is open: READY rows are due today.")
    c = res["counts"]
    lead = f" · LEAD {c['LEAD']}" if c.get("LEAD") else ""
    L = ["---", "title: NEXT-UP, the one ranked list, generated", "writer: scripts/next_up.py only; the orchestrator's and "
         "the owner's calls go in hub/NEXT-UP-overrides.yaml", f"generated: {res['now']}", "---", "",
         f"# NEXT-UP · {today}", "", mode,
         f"Rows: READY {c['READY']} · PEOPLE FIRST {c['PEOPLE FIRST']} · PAGE {c['PAGE']} · JOB {c['JOB']} · "
         f"SCORE {c['SCORE']}{lead} · "
         f"BLOCKED {c['BLOCKED']} · STALE {c.get('STALE', 0)} (parked below, never claimed first). Ranked by expected "
         f"interviews per hour: measured channel rates warm "
         f"{r_['warm_k']} of {r_['warm_n']}, cold {r_['cold_k']} of {r_['cold_n']} (shrunk), times stated priors, age decay "
         f"(full weight under {FULL_WEIGHT_DAYS:g} d, {DECAY_FLOOR:g} by {DECAY_END_DAYS:g} d), a warmth boost for a person "
         "(post, referral, reply, due item), divided by the hours still needed. "
         "Regenerate: `python3 scripts/next_up.py --write --focus`.", "",
         "## The list (top first)", "", HEADER,
         "|---|---|--:|---|--:|---|---|---|---|"]
    for i, r in enumerate(res["rows"], 1):
        L.append(_row_line(str(i), r, res["applying"]))
    if res.get("stale"):
        L += ["", f"## STALE (older than {STALE_DAYS:g} days, or no longer live in the scans; FOCUS never claims these first)",
              "", HEADER, "|---|---|--:|---|--:|---|---|---|---|"]
        L += [_row_line(f"S{i}", r, res["applying"]) for i, r in enumerate(res["stale"], 1)]
    if res.get("left_out"):
        L += ["", "Pages left out (their job is closed, skipped or missing; reopen the job record to list one again):"]
        L += [f"- `{x['job']}` · page {x['page']} · {x['why']}" for x in res["left_out"]]
    return "\n".join(L) + "\n"


def focus_next(res: dict[str, Any], n: int = FOCUS_N) -> list[dict[str, Any]]:
    """: the top n of the ranked list for FOCUS: rank, role, why, age, state, rate. The one function both NEXT-UP's
    --focus and the dashboard's "FOCUS next" block use. BLOCKED rows and stale rows are never in it."""
    out = []
    for r in res["rows"]:
        if r["state"] == "BLOCKED" or r.get("stale"):
            continue
        company = str(r.get("company") or "")
        out.append({"rank": len(out) + 1, "role": f"{company} · {r.get('title')}" if r["state"] != "LEAD"
                    else f"{company}: {r.get('title')}", "why": _cell(r["why"]), "age": age_label(
                        r.get("age_days"), r.get("age_basis") or "posted"), "age_days": r.get("age_days"),
                    "state": r["state"], "per_hour": r["per_hour"], "key": r["job"] or r["key"]})
        if len(out) >= n:
            break
    return out


def focus_lines(rows: list[dict[str, Any]], width: int = 110) -> list[str]:
    cut = lambda t: t if len(t) <= width else t[:width - 1].rstrip() + "…"          # noqa: E731
    return [f"{x['rank']:>2}. [{x['state']}] {cut(x['role'])} · {x['per_hour']:.1%}/h · age {x['age']} · {cut(x['why'])}"
            for x in rows]


def focus_for(root: Path, now: datetime, n: int = FOCUS_N) -> dict[str, Any]:
    """build() then focus_next(): {"rows": top n, "stale": count, "generated": iso}. Raises what build raises."""
    res = build(Path(root), now)
    return {"rows": focus_next(res, n), "stale": len(res.get("stale") or []), "generated": res["now"],
            "counts": res["counts"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--now", default=None, help="ISO instant (default: now)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write", nargs="?", const="hub/NEXT-UP.md", default=None, metavar="PATH")
    ap.add_argument("--focus", nargs="?", const=FOCUS_N, default=None, type=int, metavar="N",
                    help="print the FOCUS next top N (default 10): rank, role, why, age")
    args = ap.parse_args(argv)
    now = datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else datetime.now(timezone.utc)
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    res = build(args.root, now)
    text = render(res, now.astimezone(fresh10.TZ).date().isoformat())
    if args.json:
        print(json.dumps(res, indent=2, default=str))
    elif args.focus is None and not args.write:
        print(text, end="")
    if args.focus is not None and not args.json:
        print(f"FOCUS next (top {args.focus} of {len(res['rows'])} ranked; {len(res['stale'])} STALE parked):")
        print("\n".join(focus_lines(focus_next(res, args.focus))))
    if args.write:
        path = Path(args.root) / args.write
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        print(f"wrote {path} ({len(res['rows'])} rows)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
