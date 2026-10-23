"""crm.py: the CRM block of the dashboard snapshot (Home and Pipeline, interviews first). Read only. dash_api.build attaches it as snapshot["crm"].

  header     the clock line and the mode line ("Applications: focused lanes only ..."), once
  kpis       interviews booked this week against the weekly floor first (interviews.py; the floor from
             targets/goals.yaml through js_common.weekly_interview_floor, state/recruiter/target.yaml only when goals.yaml
             is missing; "target not set" when neither sets one), then applied, interviews per application warm vs cold,
             screens, days to apply
  band48     the next 48 hours: calendar rounds with their local times (calendar_events agenda; a failed read is None,
             shown "not read", never 0; an event that matches no deal is private: counted, never shown), tracker rounds the calendar does not show at their round_at (a held round is not a coming one; with no
             round_at it is listed as "round date not on record"), approvals
             pending and not expired (expired ones listed apart), replies owed to pipeline contacts only (the rest
             listed apart), follow-ups due
  updates    tracker events of the last 7 days, newest first, with local times; the page filters by the last visit
  findings   at most 3 rule lines, each with n; "too few to say" under 10; the dream-share line stays hidden until
             the owner rules the dream list
  plot       events per clock day (day 0 = before the clock starts)
  tasks      round prep, approvals, follow-ups due, next actions due today
  pipeline   one card per deal from Scored to Offer: days in the current stage (from the event that entered it), years
             asked with STRETCH (above the owner's engineer months, through a person) and OUT (cold), the lane that claimed it (hub/lanes/FOCUSED*.md
             `claims:`), warm path names, stopped (state/recruiter/stopped.yaml)
"""
from __future__ import annotations

import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

_SCRIPTS = str(Path(__file__).resolve().parents[1] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import agency_config as cfg  # noqa: E402
import interviews  # noqa: E402
import js_common as jc  # noqa: E402
import tracker as tr  # noqa: E402

PT = ZoneInfo(str(cfg.get("identity.timezone", "UTC") or "UTC"))   # the owner's clock (identity.timezone)
MODE_LINE = "Applications: focused lanes only"
STAGES = ("fit_checked", "tailored", "applied", "confirmation", "screen", "oa", "interviewing", "final", "offer")
ROUND_STAGES = {"screen", "oa", "interviewing", "final"}
ENOUGH = 10
EVENT_LABEL = {"applied": "applied", "submitted": "applied", "confirmation_received": "confirmation", "screen": "screen",
               "oa": "assessment", "interviewing": "interview", "final": "final round", "offer": "offer",
               "rejected": "rejected", "email_in": "reply", "reply": "reply", "recruiter_reply": "reply",
               "resume_ready": "page ready", "tailored": "page ready", "withdrawn": "withdrawn"}


APPLIED_TYPES = frozenset({"applied", "submitted"})


def counted_events(body: str) -> list[Any]:
    """The events a count may see. An application is applied once: a later applied or submitted
    line is a record update (a record backfill can add a second one), never a second application. Rounds
    follow interviews.booked: a same-stage event within ROUND_GAP_DAYS of a round's first event is that round (its held
    line), a later one is a new round, a rejection or hold ends it, and a reschedule ([reschedule]) is never a round."""
    out, applied, open_round = [], False, None
    for e in sorted(tr.parse_events(body), key=lambda e: tr.parse_ts(e.at) or datetime.min.replace(tzinfo=timezone.utc)):
        typ = e.type.strip().lower()
        if typ in APPLIED_TYPES:
            if applied:
                continue
            applied = True
        stage = typ.split(":", 1)[1] if typ.startswith("override:") else tr.EVENT_STAGE.get(typ)
        if stage in interviews.ROUND_ENDS:
            open_round = None
        if stage in ROUND_STAGES:                  # interviews.booked's rule: a same-stage event within
            day = tr._day(tr.parse_ts(e.at)) if tr.parse_ts(e.at) else None     # ROUND_GAP_DAYS of the round's first
            if tr.round_info(e.evidence)["reschedule"] or day is None:          # is the same round; a reschedule is
                continue                                                        # never a round of its own
            if open_round and open_round[0] == stage and (day - open_round[1]).days <= interviews.ROUND_GAP_DAYS:
                open_round = None
                continue
            open_round = (stage, day)
        out.append(e)
    return out


def pt(at: Any) -> str | None:
    """'2026-10-02T01:00:00Z' -> '10-01 18:00' in the owner's time zone; a date-only stamp keeps its date."""
    if at is None:
        return None
    s = str(at)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s[5:]
    when = tr.parse_ts(s)
    return when.astimezone(PT).strftime("%m-%d %H:%M") if when else None


def _front(path: Path) -> dict[str, Any]:
    try:
        return jc.fm.load(path)[0]
    except (OSError, ValueError):
        return {}


def approvals(os_root: Path, today: date) -> dict[str, Any]:
    """Pending approvals split by expiry (approvals expire 7 days after creation; `expires:` when written)."""
    live, expired = [], []
    folder = Path(os_root) / "work" / "approvals"
    for p in sorted(folder.glob("A-*.md")) if folder.is_dir() else []:
        meta = _front(p)
        if str(meta.get("status") or "") != "pending":
            continue
        exp = meta.get("expires")
        try:
            exp_day = date.fromisoformat(str(exp)) if exp else date.fromisoformat(str(meta.get("created"))) + timedelta(days=7)
        except ValueError:
            exp_day = None
        row = {"id": meta.get("id") or p.stem, "action": meta.get("action"), "target": meta.get("target"),
               "expires": exp_day.isoformat() if exp_day else None, "created": str(meta.get("created") or ""),
               "path": str(p)}
        (expired if exp_day and exp_day < today else live).append(row)
    return {"pending": len(live), "expired": expired, "live": live, "groups": _approval_groups(live, today)}


def approval_label(r: dict[str, Any]) -> str:
    """What an approval holds, in words. outbox.py files every LinkedIn item (connection notes and messages too) under
    the schema's one LinkedIn action, post-linkedin, so the drawer names what is inside."""
    action, target = str(r.get("action") or "other"), str(r.get("target") or "")
    if action == "post-linkedin":
        if re.search(r"linkedin-(connect|message)", target):
            return "LinkedIn: connection notes and messages (action post-linkedin)"
        return "LinkedIn post"
    return action


AGE_BUCKETS = (("today", 0, 0), ("1 to 2 days", 1, 2), ("3 to 6 days", 3, 6), ("7 days or more", 7, 10**6))


def _approval_groups(live: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """Pending approvals by kind (biggest first), then by age since created, oldest bucket last."""
    kinds: dict[str, list[dict[str, Any]]] = {}
    for r in live:
        kinds.setdefault(approval_label(r), []).append(r)
    out = []
    for kind, rows in sorted(kinds.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        ages = []
        for label, lo, hi in AGE_BUCKETS:
            items = []
            for r in rows:
                try:
                    age = (today - date.fromisoformat(r["created"])).days
                except ValueError:
                    age = None
                if age is not None and lo <= age <= hi:
                    items.append(r)
            if items:
                ages.append({"age": label, "items": sorted(items, key=lambda r: r["id"])})
        undated = [r for r in rows if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["created"])]
        if undated:
            ages.append({"age": "date not on record", "items": undated})
        out.append({"kind": kind, "label": kind, "count": len(rows), "ages": ages})
    return out


def lane_claims(js: Path) -> dict[str, str]:
    """application slug -> lane scope, from each FOCUSED lane file's frontmatter `claims:` list."""
    out: dict[str, str] = {}
    for p in sorted((Path(js) / "hub" / "lanes").glob("FOCUSED*.md")):
        meta = _front(p)
        for slug in meta.get("claims") or []:
            out.setdefault(str(slug), str(meta.get("scope") or p.stem))
    return out


def years_flag(years: int | None, warm: bool = False) -> str | None:
    """fresh10's rule: within his ~29 engineer months is in; above it a STRETCH only through a person
    (deal_warmth "warm"), else OUT."""
    if years is None:
        return None
    import fresh10
    return fresh10.years_rule(years, warm)


def _calendar_reader(first: date, last: date) -> list[dict[str, Any]] | None:
    try:
        import calendar_events
        return calendar_events.agenda(calendar_events.calendar_service(), first, last)["events"]
    except Exception:                                  # no token, no network, a refused read: missing, never empty
        return None


REPLIED = {"replied", "in_conversation", "interviewing"}                   # an insider's prospect status: a reply
COMPANY_REPLIED = ROUND_STAGES | {"offer"}                                 # the deal's stage: the company replied


def _inside(meta: dict[str, Any], deal: dict[str, Any]) -> bool:
    """At the deal's company on a positive match only: the slugs, else the company names (jc.normalize_company).
    Missing data is never inside (missing is not empty)."""
    at, slug = str(meta.get("company_slug") or ""), str(deal.get("company_slug") or "")
    if at and slug:
        return at == slug
    name, company = jc.normalize_company(str(meta.get("company") or "")), jc.normalize_company(str(deal.get("company") or ""))
    return bool(name and company and name == company)


def deal_warmth(entries: list[Any], people: dict[str, dict[str, Any]], deal: dict[str, Any]) -> str | None:
    """How warm the deal's path is. "warm": a warm person (strategy_review.is_warm: warmth 1+ or a referrer,
    never do_not_contact) at the company, or a warm outsider whose introduction is made: an insider named on the same
    pursuit, an insider who replied, or the company's reply (the deal at screen or later). "intro pending": only a warm
    outsider. None: cold. An insider marked do_not_contact never counts."""
    import strategy_review
    rows = [(e, people.get(str(_person(e, people).get("id"))) or {}) for e in entries or []]
    inside = [(e, m) for e, m in rows if _inside(m, deal) and str(m.get("status") or "") != "do_not_contact"]
    if any(strategy_review.is_warm(m) for _, m in inside):
        return "warm"
    outsiders = [(e, m) for e, m in rows if strategy_review.is_warm(m) and not _inside(m, deal)]
    if not outsiders:
        return None
    via = lambda e: str(e.get("via") or "") if isinstance(e, dict) else ""
    pursuits = {via(e) for e, _ in outsiders if via(e).startswith("pursuit ")}
    made = (any(via(e) in pursuits for e, _ in inside)
            or any(str(m.get("status") or "") in REPLIED for _, m in inside)
            or str(deal.get("stage") or "") in COMPANY_REPLIED)
    return "warm" if made else "intro pending"


def _warm_names(entries: list[Any], people: dict[str, dict[str, Any]], state: str | None) -> list[str]:
    """The deal's people by name; under "intro pending" the warm outsiders carry the words."""
    import strategy_review
    out = []
    for e in entries or []:
        who = _person(e, people)
        meta = people.get(str(who.get("id"))) or {}
        name = str(who.get("name") or who.get("id"))
        out.append(f"{name} (intro pending)" if state == "intro pending" and strategy_review.is_warm(meta) else name)
    return out


def _person(entry: Any, people: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """An application's people entry (a {id, name, via} row, or a bare prospect id) as {id, name}."""
    if isinstance(entry, dict):
        return {"id": entry.get("id"), "name": entry.get("name") or (people.get(str(entry.get("id"))) or {}).get("name")}
    return {"id": entry, "name": (people.get(str(entry)) or {}).get("name")}


MEETING_WORDS = re.compile(r"\b(chat|call|interview|screen|intro|sync|meet|meeting|coffee|round|onsite|zoom)\b")
OWNER_WORDS = {w for w in [str(cfg.get("identity.owner_label", "") or "").lower(), *str(cfg.get("identity.name", "") or "").lower().split()[:1]] if w and w != "you"}
LIVE_DEAL_STAGES = frozenset({"applied", "confirmation", "screen", "oa", "interviewing", "final", "offer"})
GENERIC_TAIL = {"ai", "lab", "labs", "inc", "technologies", "technology", "tech", "systems", "company", "corp", "motor", "hq"}


def company_keys(name: str) -> list[str]:
    """The normalized company name, then without its generic tail ('Acme AI' -> 'acme', 'Example Corp (incl.
    Cloud)' -> 'example corp'); keys under 4 letters are never used alone."""
    norm = jc.normalize_company(name)
    words = norm.split()
    if "incl" in words:
        words = words[:words.index("incl")]
    while len(words) > 1 and words[-1] in GENERIC_TAIL:
        words = words[:-1]
    return [k for k in dict.fromkeys([norm, " ".join(words)]) if len(k) >= 4]


def _match(title: str, apps: list[dict[str, Any]], people: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """The deal a calendar event is about. The privacy gate: only a meeting title (a meeting word, the owner's own name, or
    "<>") on a deal the owner applied to, naming the company or a person on it (full name, or first name), is a round; any
    other event ("Sam birthday dinner", "Acme dealership oil change") is never shown."""
    low = jc.normalize_company(title)
    meeting = MEETING_WORDS.search(low) is not None or "<>" in str(title) or any(re.search(rf"\b{re.escape(w)}\b", low) for w in OWNER_WORDS)
    if not meeting:                            # "Acme dealership oil change" is not an Acme round
        return None
    apps = [a for a in apps if a.get("stage") in LIVE_DEAL_STAGES]     # a round belongs to a deal the owner applied to
    for a in apps:
        for key in company_keys(str(a.get("company") or "")):
            if re.search(rf"\b{re.escape(key)}\b", low):
                return a
    for a in apps:
        for entry in a.get("people") or []:
            full = jc.normalize_company(str(_person(entry, people).get("name") or ""))
            first = full.split()[0] if full.split() else ""
            if (len(full.split()) >= 2 and re.search(rf"\b{re.escape(full)}\b", low)) or \
                    (meeting and len(first) >= 3 and re.search(rf"\b{re.escape(first)}\b", low)):
                return a                       # "Pat / the owner: Chat" is the Acme call; a dinner is not
    return None


def _round_at(js: Path, slug: str, stage: str | None) -> str | None:
    """The round time on the latest event of this round stage (tracker.round_info), else None. A later event logged
    without --round-at turns a dated round into "round date not on record": a gap is shown, never a round hidden."""
    try:
        body = (Path(js) / "applications" / slug / "application.md").read_text(encoding="utf-8")
    except OSError:
        return None
    found = None
    for ev in sorted(tr.parse_events(body), key=lambda e: tr.parse_ts(e.at) or datetime.min.replace(tzinfo=timezone.utc)):
        kind = ev.type.strip().lower()
        if (kind.split(":", 1)[1] if kind.startswith("override:") else tr.EVENT_STAGE.get(kind)) == stage:
            found = tr.round_info(ev.evidence)["round_at"]     # the latest event of the stage decides
    return found


def band48(js: Path, os_root: Path, now: datetime, apps: list[dict[str, Any]], follow_ups: list[dict[str, Any]],
           people: dict[str, dict[str, Any]], calendar: Callable[[date, date], Any] = _calendar_reader) -> dict[str, Any]:
    today = now.astimezone(PT).date()
    events = calendar(today, today + timedelta(days=6))
    packs = {p.name for p in (Path(js) / "prep" / "packs").iterdir() if p.is_dir()} if (Path(js) / "prep" / "packs").is_dir() else set()
    rounds, matched, other, undated = [], set(), 0, []
    for ev in events or []:
        start = str(ev.get("start") or "")
        end = tr.parse_ts(ev.get("end") or start)
        if end and end < now:                          # already over
            continue
        deal = _match(str(ev.get("title") or ev.get("summary") or ""), apps, people)
        if not deal:                                   # an event with no deal is private, never a round;
            other += 1                                 # only its count is kept, never its title
            continue
        matched.add(deal["slug"])
        rounds.append({"when": start[:16].replace("T", " "), "day": start[:10], "title": None,   # never the event's own
                       "company": deal.get("company"), "slug": deal["slug"],                     # words
                       "pack": deal["slug"] in packs, "source": "calendar"})
    for a in apps:
        if a.get("stage") in ROUND_STAGES and a["slug"] not in matched:
            # the round's own time comes from its event's round_at (tracker.py event --round-at); a
            # round already held is not a coming one, and with no time on record it is a data gap, never a coming round
            at = _round_at(js, a["slug"], a.get("stage"))
            when = tr.parse_ts(at) if at else None
            if when is None:
                undated.append({"company": a.get("company"), "slug": a["slug"], "stage": a.get("stage")})
            elif when >= now:
                local = when.astimezone(PT)
                rounds.append({"when": local.strftime("%Y-%m-%d %H:%M"), "day": local.date().isoformat(),
                               "title": f"{a.get('company')} {a.get('stage')}", "company": a.get("company"),
                               "slug": a["slug"], "pack": a["slug"] in packs, "source": "tracker"})
    pipeline = {jc.normalize_company(str(a.get("company") or "")) for a in apps if a.get("stage") in STAGES}
    owed = [f for f in follow_ups if "answer owed" in str(f.get("next") or "")]
    replies = [f for f in owed if jc.normalize_company(str(f.get("company") or "")) in pipeline - {""}]
    appr = approvals(os_root, today)
    horizon = (today + timedelta(days=2)).isoformat()
    return {"calendar_read": events is not None, "rounds": rounds, "calendar_other": other, "rounds_undated": undated,
            "rounds_48h": [r for r in rounds if r["day"] and r["day"] <= horizon],
            "replies_waiting": len(replies), "replies_other": [f for f in owed if f not in replies],
            "approvals": appr["pending"], "approvals_expired": appr["expired"], "approval_groups": appr["groups"],
            "follow_ups_due": sum(1 for f in follow_ups if f.get("due") and str(f["due"]) <= today.isoformat())}


def _stage_entered(js: Path, now: datetime) -> dict[str, str]:
    """application -> the local day its current stage began: the event that entered it."""
    out = {}
    for slug, meta, body in tr.iter_applications(js) or []:
        proj = tr.project(tr.parse_events(body), now, meta.get("ghost_after_days"), meta.get("last_inbound_at"))
        if proj["transitions"]:
            out[slug] = tr._day(proj["transitions"][-1][1]).isoformat()
    return out


def _years(js: Path, job_id: str | None, cache: dict[str, int | None]) -> int | None:
    if not job_id:
        return None
    if job_id not in cache:
        import fit_gates
        p = Path(js) / "jobs" / f"{job_id}.md"
        cache[job_id] = fit_gates.required_years(fit_gates.jd_text_from_body(jc.fm.load(p)[1])) if p.is_file() else None
    return cache[job_id]


def pipeline(js: Path, now: datetime, apps: list[dict[str, Any]], people: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    import fresh10
    today = now.astimezone(PT).date()
    since, claims, stopped, cache = _stage_entered(js, now), lane_claims(js), fresh10.stopped(js), {}
    out = []
    for a in apps:
        if a.get("stage") not in STAGES:
            continue
        job = str(a.get("job") or "")
        years = _years(js, job, cache)
        warmth = deal_warmth(a.get("people") or [], people, a)
        entered = since.get(a["slug"])
        out.append({"slug": a["slug"], "company": a.get("company"), "title": a.get("title"), "stage": a["stage"],
                    "days_in_stage": ((today - date.fromisoformat(entered)).days if entered and entered <= today.isoformat()
                                      else None), "since": entered,       # a future-dated stage is missing, never negative
                    "years": years, "years_flag": years_flag(years, warmth == "warm"), "intro": warmth if warmth == "intro pending" else None, "lane": claims.get(a["slug"]),
                    "warm": _warm_names(a.get("people") or [], people, warmth),
                    "stopped": a["slug"] in stopped or job in stopped, "fit": a.get("fit"), "tier": a.get("tier"),
                    "segment": a.get("segment"), "next": a.get("next_action"), "next_at": a.get("next_action_at"),
                    "link": a.get("link")})
    return out


def sample(k: int, n: int) -> str:
    """'2 of 2 (small sample)': a rate always carries its n; under ENOUGH it says so."""
    return f"{k} of {n}" + (" (small sample)" if n < ENOUGH else "")


def findings(r: dict[str, Any], latency: dict[str, Any] | None) -> list[dict[str, Any]]:
    lat = latency or {}
    out = [{"key": "warm-vs-cold", "n": min(r["warm_n"], r["cold_n"]),
            "text": f"Warm paths: {sample(r['warm_k'], r['warm_n'])} reached an interview; cold ATS: "
                    f"{sample(r['cold_k'], r['cold_n'])}."},
           {"key": "speed", "n": lat.get("measured") or 0,
            "text": f"Applying speed: median {round((lat.get('median') or 0) / 24, 1)} days from posting to applying; "
                    f"{lat.get('within_72h', 0)} of {lat.get('measured', 0)} within 72 h."}]
    for f in out:
        f["enough"] = f["n"] >= ENOUGH
    return out                                         # the dream-share line waits for the owner's dream-list ruling


def _not_read(first: date, last: date) -> None:
    return None


def calendar_window(js: Path, now: datetime) -> list[dict[str, Any]] | None:
    """The 7 days Home shows (today in the owner's time zone and six more), from the live calendar for the real department only; a fixture
    tree or a failed read is None. dash_api starts this in a thread at the top of a build (the Google client import
    alone takes about 2 s), then hands the result to build()."""
    if Path(js).resolve() != Path(jc.JS_ROOT).resolve():
        return None
    today = now.astimezone(PT).date()
    return _calendar_reader(today, today + timedelta(days=6))


def _people_report(js: Path, day: str) -> tuple[str | None, dict[str, list[str]]]:
    """job id -> candidate names from state/daily/<day>-fresh10-people.md (H1's report: leads, never identities)."""
    path = Path(js) / "state" / "daily" / f"{day}-fresh10-people.md"
    if not path.is_file():
        return None, {}
    out: dict[str, list[str]] = {}
    for ln in path.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in ln.strip().strip("|").split("|")] if ln.startswith("|") else []
        m = re.search(r"\(`([^`]+)`\)", cells[1]) if len(cells) > 2 else None
        if m:
            out[m.group(1)] = [] if cells[2].lower().startswith("none") else re.findall(r"\*\*([^*]+)\*\*", cells[2])
    return path.relative_to(js).as_posix(), out


def _fresh10_pick(root: Path, now: datetime) -> dict[str, Any]:
    import fresh10
    return fresh10.pick(root, now)


def jobs(js: Path, now: datetime, pick: Callable[[Path, datetime], dict[str, Any]] = _fresh10_pick,
         names: dict[str, str] | None = None) -> dict[str, Any]:
    """The Jobs page's Fresh 10, computed once per snapshot build: fresh10.pick's rows in its order, each
    with expected interviews, STRETCH and STOPPED marks and the people report's candidates "to verify". A failed pick
    is missing (rows None, error said), never an empty list."""
    import fresh10
    try:
        res = pick(Path(js), now)
    except Exception as exc:                           # unreadable jobs: say so
        return {"rows": None, "error": str(exc), "source": "fresh10.pick", "people_report": None}
    report, people = _people_report(Path(js), res.get("date") or now.astimezone(PT).date().isoformat())
    stopped = fresh10.stopped(Path(js))
    r = res.get("rates") or {}
    rows = []
    for row in res.get("rows") or []:
        marks = (["STRETCH"] if row.get("stretch") else []) + (["STOPPED"] if row.get("id") in stopped else [])
        rows.append({**row, "warm": [(names or {}).get(w, w) for w in row.get("warm") or []], "marks": marks, "expected_text": f"{(row.get('expected') or 0) * 100:.1f}%",
                     "to_verify": None if report is None else people.get(row.get("id"), [])})
    return {"date": res.get("date"), "hours": res.get("hours"), "counts": res.get("counts"), "candidates": res.get("candidates"),
            "source": f"fresh10.pick at the snapshot build ({now.astimezone(PT).strftime('%Y-%m-%d %H:%M')} {cfg.get('identity.timezone_label', '')})",
            "rates_text": "warm " + sample(r.get("warm_k", 0), r.get("warm_n", 0)) + ", cold " + sample(r.get("cold_k", 0), r.get("cold_n", 0)),
            "people_report": report, "rows": rows, "error": None}


CLOSED_JOB = {"closed", "applied", "skipped", "rejected", "filled", "expired"}
DEAL_RANK = {st: i for i, st in enumerate(("offer", "final", "interviewing", "oa", "screen", "confirmation", "applied",
                                           "tailored", "fit_checked"))}


def companies(watch: list[dict[str, Any]], job_rows: list[dict[str, Any]], apps: list[dict[str, Any]],
              prospects: list[dict[str, Any]], touch_rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """ accounts: every company on the watchlist, with a deal, or with a person. Open roles leave out closed,
    applied, skipped and duplicate postings. Order: the furthest live deal first, then open roles, then the name."""
    acc: dict[str, dict[str, Any]] = {}

    def get(slug: str, name: Any) -> dict[str, Any]:
        if slug not in acc:
            acc[slug] = {"slug": slug, "name": str(name or slug), "tier": None, "segment": None, "roles": [], "deals": [],
                         "people": [], "last_touch": None, "activity": []}
        return acc[slug]
    for c in watch:
        if c.get("slug"):
            a = get(str(c["slug"]), c.get("name"))
            a["tier"], a["segment"] = c.get("tier"), c.get("segment")
    for d in apps:
        slug = str(d.get("company_slug") or jc.normalize_company(str(d.get("company") or "")).replace(" ", "-"))
        a = get(slug, d.get("company"))
        a["deals"].append({"slug": d.get("slug"), "title": d.get("title"), "stage": d.get("stage")})
        a["segment"] = a["segment"] or (d.get("segment") if d.get("segment") not in (None, "unknown") else None)
    for j in job_rows:
        if j.get("company_slug") in acc and str(j.get("status") or "") not in CLOSED_JOB and not j.get("duplicate_of"):
            acc[j["company_slug"]]["roles"].append({"id": j.get("id"), "title": j.get("title"), "fit": j.get("fit"),
                                                    "status": j.get("status"), "link": j.get("link")})
    for m in prospects:
        slug = str(m.get("company_slug") or "")
        if not slug:
            continue
        a = get(slug, m.get("company"))
        a["people"].append({"id": m.get("id"), "name": m.get("name"), "warmth": m.get("warmth"), "status": m.get("status")})
        when = tr.parse_ts(m.get("last_inbound_at")) if m.get("last_inbound_at") else None
        t = tr._day(tr._instant(m.get("last_inbound_at"))).isoformat() if when else None     # a local date
        if t and (a["last_touch"] is None or t > a["last_touch"]):
            a["last_touch"] = t
    for t in touch_rows or []:                         # the account's activity, newest first
        if t.get("company_slug") in acc:
            acc[t["company_slug"]]["activity"].append(t)
    out = []
    for a in acc.values():
        a["activity"] = a["activity"][:30]
        a["roles"].sort(key=lambda r: (-(r["fit"] or 0), str(r["id"])))
        a["people"].sort(key=lambda p: (-(p["warmth"] or 0), str(p["name"])))
        a.update(open_roles=len(a["roles"]), applications=len(a["deals"]), contacts=len(a["people"]))
        out.append(a)
    best = lambda a: min((DEAL_RANK.get(d["stage"], 99) for d in a["deals"]), default=99)
    return sorted(out, key=lambda a: (best(a), -a["open_roles"], a["name"].lower()))


def touches(js: Path, now: datetime, people: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Sent and received touches (touches/*.jsonl), newest first: an outbound touch shows only when sent
    (drafts never); an inbound one always. Each carries the person, their company and a local time."""
    import json
    out = []
    for f in sorted((Path(js) / "touches").glob("*.jsonl")) if (Path(js) / "touches").is_dir() else []:
        for ln in f.read_text(encoding="utf-8").splitlines():
            try:
                t = json.loads(ln)
            except ValueError:
                continue
            out_dir = str(t.get("direction") or "") == "out"
            if out_dir and str(t.get("status") or "") != "sent":
                continue
            when = tr.parse_ts(t.get("at"))
            if not when:
                continue
            person = people.get(str(t.get("prospect") or "")) or {}
            channel, subject = str(t.get("channel") or "touch"), t.get("subject")
            out.append({"at": t["at"], "at_pt": pt(t["at"]), "day": tr._day(tr._instant(t["at"])).isoformat(),
                        "label": "sent" if out_dir else "received", "prospect": t.get("prospect"),
                        "name": person.get("name") or t.get("prospect"), "company": person.get("company") or person.get("name") or t.get("prospect"),
                        "company_slug": person.get("company_slug"), "channel": channel,
                        "title": f"{channel}: {subject}" if subject else channel})
    out.sort(key=lambda x: tr.parse_ts(x["at"]), reverse=True)
    return out


TIMELINE_LINE = re.compile(r"^- (\S+)\s+(?:·\s+)?(.*)$")


def timelines(js: Path, touch_rows: list[dict[str, Any]] | None = None) -> dict[str, list[dict[str, str]]]:
    """prospect id -> its "## Timeline" lines, newest first; a timed line in the owner's time zone ("09-28 14:30"), a dated one
    kept as its date ("09-28")."""
    out: dict[str, list[dict[str, str]]] = {}
    for path, meta, body in jc.iter_records(Path(js) / "prospects"):
        lines, on = [], False
        for ln in body.splitlines():
            if ln.startswith("## "):
                on = ln.strip() == "## Timeline"
                continue
            m = TIMELINE_LINE.match(ln.strip()) if on else None
            if m and re.match(r"\d{4}-\d{2}-\d{2}", m.group(1)):
                lines.append({"at": m.group(1), "when": pt(m.group(1)) or m.group(1)[5:10], "text": m.group(2).strip()})
        pid = str(meta.get("id") or path.stem)
        logged = {str(tr.parse_ts(x["at"]))[:16] for x in lines if tr.parse_ts(x["at"]) and "T" in x["at"]}
        for t in touch_rows or []:                     # touches, unless the file already logs that minute
            if t.get("prospect") == pid and str(tr.parse_ts(t["at"]))[:16] not in logged:
                lines.append({"at": t["at"], "when": t["at_pt"], "text": f"{t['label']} · {t['title']}"})
        lines.sort(key=lambda x: tr.parse_ts(x["at"]) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        out[pid] = lines
    return out


def build(js: Path, os_root: Path, now: datetime, snap: dict[str, Any], prospects: list[dict[str, Any]] | None = None,
          calendar: Callable[[date, date], Any] | None = None, fresh_pick: bool = True) -> dict[str, Any]:
    js, os_root = Path(js), Path(os_root)
    if calendar is None:                               # the live calendar only for the real department; a fixture
        calendar = _calendar_reader if js.resolve() == Path(jc.JS_ROOT).resolve() else _not_read      # tree: not read
    today = now.astimezone(PT).date()
    apps = snap.get("applications") or []
    people = {str(m.get("id")): m for m in prospects or [] if m.get("id")}
    o = snap.get("overview") or {}
    funnel = {x["step"]: x for x in o.get("funnel") or []}
    week = {x["metric"]: x for x in ((o.get("weeks") or {}).get("rows") or [])}
    booked, rates, target = interviews.booked(js, today), interviews.rates(js, now), jc.weekly_interview_floor(js)
    by_slug = {a["slug"]: a for a in apps}
    booked_items = [{**i, "company": (by_slug.get(i["slug"]) or {}).get("company"),
                     "via": (by_slug.get(i["slug"]) or {}).get("applied_via") or "not recorded",
                     "people": [_person(p, people).get("name") for p in (by_slug.get(i["slug"]) or {}).get("people") or []]}
                    for i in booked["items"] if i["week"] == "this_week"]
    lat = o.get("apply_latency_hours") or {}
    events = []
    cutoff = (today - timedelta(days=7)).isoformat()
    for slug, meta, body in tr.iter_applications(js) or []:
        for e in counted_events(body):
            when = tr.parse_ts(e.at)
            typ = e.type.strip().lower()
            if when and typ in EVENT_LABEL and tr._day(when).isoformat() >= cutoff:
                events.append({"slug": slug, "company": meta.get("company"), "title": meta.get("title"), "at": e.at,
                               "at_pt": pt(e.at), "label": EVENT_LABEL[typ]})
    touch_rows = touches(js, now, people)
    events += [{"slug": None, "company": t["company"], "title": f"{t['name']} · {t['title']}" if t["name"] != t["company"] else t["title"],
                "at": t["at"], "at_pt": t["at_pt"], "label": t["label"]} for t in touch_rows if t["day"] >= cutoff]
    events.sort(key=lambda e: tr.parse_ts(e["at"]), reverse=True)
    clock = snap.get("clock") or {}
    start = date.fromisoformat(clock["clock_start"]) if clock.get("clock_start") else None
    plot: dict[str, dict[str, int]] = {}
    for slug, meta, body in tr.iter_applications(js) or []:
        for e in counted_events(body):
            kind = {"applied": "applied", "submitted": "applied", "email_in": "reply", "reply": "reply",
                    "recruiter_reply": "reply"}.get(e.type.strip().lower()) or (
                "round" if tr.EVENT_STAGE.get(e.type.strip().lower()) in ROUND_STAGES else None)
            when = tr.parse_ts(e.at)
            if kind and when and start:
                d = str(max((tr._day(when) - start).days + 1, 0))
                plot.setdefault(d, {"applied": 0, "reply": 0, "round": 0})[kind] += 1
    b = band48(js, os_root, now, apps, snap.get("follow_ups") or [], people, calendar)
    deals = pipeline(js, now, apps, people)
    tasks = [{"what": f"Prep: {r['company']} {r['when'] or '(time not on record)'}" + (" (prep pack)" if r["pack"] else " (no prep pack)"),
              "slug": r["slug"]} for r in b["rounds_48h"] if r["slug"]]       # a calendar entry with no deal is no round
    tasks += [{"what": f"Decide {b['approvals']} approvals waiting", "slug": None}] if b["approvals"] else []
    tasks += [{"what": f"{b['follow_ups_due']} follow-ups due", "slug": None}] if b["follow_ups_due"] else []
    tasks += [{"what": f"{d['company']}: {d['next']}", "slug": d["slug"]} for d in deals
              if d["next_at"] and str(d["next_at"]) <= today.isoformat() and not d["stopped"]][:5]
    return {
        "header": {"clock": clock.get("line"), "mode": MODE_LINE},
        "kpis": [
            {"key": "interviews", "label": "Interviews booked this week", "value": booked["this_week"], "target": target,
             "note": f"target {target}" if target else "target not set", "last_week": booked["last_week"],
             "items": booked_items},
            {"key": "applied", "label": "Applied", "value": (funnel.get("applied") or {}).get("count"),
             "trend": (week.get("applied") or {}).get("delta"), "note": f"{(week.get('applied') or {}).get('this', 0)} this week"},
            {"key": "interview_rate", "label": "Interviews per application, warm vs cold", "value": f"{rates['warm_k']}/{rates['warm_n']} vs {rates['cold_k']}/{rates['cold_n']}",
             "n": rates["warm_n"] + rates["cold_n"],
             "note": "warm " + sample(rates["warm_k"], rates["warm_n"]) + ", cold " + sample(rates["cold_k"], rates["cold_n"])},
            {"key": "screens", "label": "Screens", "value": (funnel.get("screen") or {}).get("count"),
             "n": (funnel.get("applied") or {}).get("count"), "trend": (week.get("interviews") or {}).get("delta")},
            {"key": "days_to_apply", "label": "Days to apply (median)",
             "value": round(lat["median"] / 24, 1) if lat.get("median") is not None else None, "n": lat.get("measured"),
             "note": f"{lat.get('within_72h', 0)} within 72 h"}],
        "band48": b, "updates": events[:60], "findings": findings(rates, lat), "plot": plot, "tasks": tasks,
        "pipeline": deals, "stages": list(STAGES), "today": today.isoformat(), "timelines": timelines(js, touch_rows), "touches": touch_rows[:300],
        "jobs": jobs(js, now, names={k: str(v.get("name") or k) for k, v in people.items()}) if fresh_pick else None,
    }
