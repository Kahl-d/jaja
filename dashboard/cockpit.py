"""cockpit.py: the cockpit's data. Read only. One panel per decision:

  next          D1  the actions with the most expected interviews per hour of the owner's time: prep for a round within
                    72 h first, then replies owed to a person, approved notes not yet sent, notes waiting on the
                    owner's approval, follow-ups due, and the top NEXT-UP rows (scripts/next_up.py)
  week          D2  interviews booked this week against the target; warm moves sent this week; warm moves waiting
  works         D3  each channel as k of n with a Wilson 95% range (gray under 10 cases); applied as gray volume
  coming        D4  rounds in the next 7 days (job rounds only), undated rounds, approvals and tokens that expire
  pipeline      D5  every deal from applied on: stage, days in stage, next step, the person
  machine       D6  one line: the last hunt run, routines late, connectors that need attention

It reuses the dashboard's snapshot (dash_api.build: the crm block, follow-ups, the clock) and runs next_up inside the
same jobs_loaded context, so no record is parsed twice and both pages read the same numbers.
"""
from __future__ import annotations

import math
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_SCRIPTS = str(Path(__file__).resolve().parents[1] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import agency_config as cfg  # noqa: E402
import js_common as jc  # noqa: E402

PT = jc.DEPT_TZ
# Every `open` path is relative to the folder that holds the agency data's parent vault: this prefix (empty when the
# data folder is the vault root) is put in front of a record's path.
JS_REL = str(cfg.get("policy.cockpit.open_prefix", ""))
# A document that explains how to renew connector tokens, shown beside the expiring tokens (empty: none).
CONNECTOR_DOC = str(cfg.get("policy.cockpit.connector_setup_doc", ""))
ENOUGH = 10
SENDABLE = {"linkedin-connect", "linkedin-message", "email"}
LIVE = ("applied", "confirmation", "screen", "oa", "interviewing", "final", "offer")
HOURS = {"reply": 0.25, "send": 0.1, "approve": 0.15, "follow_up": 0.2}
NOTE_STEP = 0.2          # stated prior: a note is one step toward a referral, never the referral


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """The Wilson score interval for k of n (None when n is 0): the honest range for a small sample."""
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round(max(0.0, (c - h) / d), 3), round(min(1.0, (c + h) / d), 3)


def channel(k: int, n: int) -> dict[str, Any]:
    r = wilson(k, n)
    return {"k": k, "n": n, "low": r[0] if r else None, "high": r[1] if r else None, "enough": n >= ENOUGH}


def _people(js: Path) -> dict[str, dict[str, Any]]:
    return {str(m.get("id") or p.stem): m for p, m, _ in jc.iter_records(Path(js) / "prospects")}


def notes(js: Path) -> list[dict[str, Any]]:
    """Outbox items the owner acts on: approved and not sent, or waiting on the owner's approval."""
    people = _people(js)
    out = []
    for p, m, _ in jc.iter_records(Path(js) / "outbox"):
        status, chan = str(m.get("status") or ""), str(m.get("channel") or "")
        if status not in ("approved", "pending") or chan not in SENDABLE:
            continue
        person = people.get(str(m.get("prospect") or "")) or {}
        to = person.get("name") or m.get("to") or m.get("prospect")
        if str(to).startswith("http"):
            to = str(m.get("prospect") or to)
        if status == "approved" and str(m.get("kind") or "") != "intro":
            continue                             # conditional replies and follow-ups are not "send now"
        out.append({"id": str(m.get("id") or p.stem), "status": status, "channel": chan, "approval": m.get("approval"),
                    "to": to, "company": person.get("company"), "company_slug": person.get("company_slug"),
                    "created": str(m.get("created") or ""), "path": f"outbox/{p.name}"})
    return out


def replies(js: Path, today: date, warm: float) -> list[dict[str, Any]]:
    """Replies owed to a person who wrote about a role (follow_ups.collect), one per sender and subject (two copies
    of one pitch are one), fresher first; each opens its Gmail thread (a read link, nothing is sent from it)."""
    import follow_ups
    copies: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for f in follow_ups.collect(Path(js), today)["due"]:
        if f.get("kind") != "portal-message":
            continue
        subject = re.sub(r"^\s*(re|fwd?):\s*", "", str(f.get("title") or ""), flags=re.I)
        # one person, one pitch: the address and the display name (a relay such as LinkedIn InMail sends for many
        # people from one address); with no name, copies are never merged
        name = str(f.get("name") or "").strip()
        key = (str(f.get("who") or ""), name.lower() or f"thread:{f.get('thread')}", subject.lower())
        copies.setdefault(key, []).append({**f, "subject": subject})
    out = []
    for group in copies.values():
        group.sort(key=lambda f: str(f.get("due") or ""))
        first, f = group[0], group[-1]                  # the newest copy ranks and links; the oldest says how long
        days = int(f.get("days_overdue") or 0)          # so a follow-up copy cannot hide a long wait
        waited = int(first.get("days_overdue") or 0)
        fresh = 1.0 if days <= 3 else (0.7 if days <= 10 else 0.4)      # stated prior: a stale thread cools
        who = str(f.get("who") or "")
        name = str(f.get("name") or "").strip()
        who = (f"{name} (LinkedIn InMail)" if name else "LinkedIn InMail") if who.endswith("@linkedin.com") else who
        wait = _days("waiting", waited) + (f", {_days('followed up', days)} ago" if waited > days else "")
        thread = f.get("thread")
        out.append(_act("reply", f["subject"][:80], f"{who}, {wait}", warm * fresh, HOURS["reply"], None,
                        str(f.get("due") or ""), portal=f.get("portal"), company=str(f.get("company") or "") or None,
                        link=f"https://mail.google.com/mail/u/0/#all/{thread}" if thread else None))
    return out


def _days(lead: str, n: int) -> str:
    return f"{lead} {n} day{'' if n == 1 else 's'}"


def approvals(os_root: Path, today: date, js: Path | None = None) -> list[dict[str, Any]]:
    """Approvals still waiting to be used, both expiring 7 days after creation unless `expires:` says otherwise:
    pending (the owner's decision), and approved with an outbox item still unsent (the owner sends it). An approval file has
    no "sent" status, so an approved one counts only when its outbox items show one unsent; one with no outbox record
    (an email sent by gmail_send, a profile edit) makes no claim either way."""
    unsent = set()
    for _p, m, _ in jc.iter_records(Path(js) / "outbox") if js else []:
        if str(m.get("status") or "") == "approved" and m.get("approval"):
            unsent.add(str(m["approval"]))
    out = []
    for p, m, _ in jc.iter_records(Path(os_root) / "work" / "approvals"):
        status = str(m.get("status") or "")
        if status not in ("pending", "approved") or (status == "approved" and str(m.get("id") or p.stem) not in unsent):
            continue
        try:
            exp = date.fromisoformat(str(m["expires"])) if m.get("expires") else \
                date.fromisoformat(str(m.get("created"))[:10]) + timedelta(days=7)
        except (KeyError, ValueError):
            exp = None
        out.append({"id": str(m.get("id") or p.stem), "status": status, "expires": exp.isoformat() if exp else None,
                    "open": f"work/approvals/{p.name}"})
    return out


def _act(kind: str, what: str, why: str, chance: float, hours: float | None, open_: str | None, created: str = "",
         **extra: Any) -> dict[str, Any]:
    """One action: its chance of leading to an interview, the hours it takes, and the rank key (chance per hour)."""
    per_hour = chance / hours if hours else chance
    return {"kind": kind, "what": what, "why": why, "chance": round(chance, 4), "minutes": round(hours * 60) if hours else None,
            "per_hour": round(per_hour, 4), "open": (JS_REL + open_ if open_ and not open_.startswith("work/") else open_),
            "created": created, **extra}


def actions(snap: dict[str, Any], nu: dict[str, Any] | None, note_rows: list[dict[str, Any]], rates: dict[str, Any],
            now: datetime, reply_rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Every candidate action, best first. A round within 72 hours always leads."""
    warm, cold = rates.get("warm") or 0.0, rates.get("cold") or 0.0
    crm = snap.get("crm") or {}
    band = crm.get("band48") or {}
    out = []
    horizon = (now + timedelta(hours=72)).astimezone(PT).strftime("%Y-%m-%d %H:%M")
    for r in band.get("rounds") or []:
        if r.get("when") and r["when"] <= horizon:
            out.append(_act("prep", f"{r['company']} round {r['when']} PT",
                            "a round is the goal; " + ("prep pack ready" if r.get("pack") else "no prep pack yet"),
                            99.0, None, f"applications/{r['slug']}/application.md", deal=r.get("slug"), company=r.get("company")))
    for f in snap.get("follow_ups") or []:
        if f.get("kind") == "application":
            out.append(_act("follow up", str(f.get("company") or ""), f"{f.get('title') or ''} ({f.get('stage')})",
                            cold * 0.5, HOURS["follow_up"], None, str(f.get("due") or ""), company=f.get("company")))
    people_first = {str(r.get("company") or "").lower() for r in (nu or {}).get("rows") or [] if r["state"] == "PEOPLE FIRST"}
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    out += list(reply_rows or [])
    for n in note_rows:                          # one action per company and status: the notes go out together
        if str(n.get("company") or "").lower() in people_first:
            continue                             # its PEOPLE FIRST row already says "send these first"
        groups.setdefault((n["status"], str(n.get("company") or n["to"])), []).append(n)
    for (status, company), ns in groups.items():
        ids, names = ", ".join(n["id"] for n in ns), ", ".join(dict.fromkeys(str(n["to"]) for n in ns))
        oldest = min(n["created"] for n in ns)
        if status == "approved":
            out.append(_act("send", f"{len(ns)} approved note{'s' if len(ns) > 1 else ''} to {names} ({company})",
                            f"{ids}; you send them yourself", warm * NOTE_STEP, HOURS["send"] * len(ns), ns[0]["path"],
                            oldest, company=ns[0].get("company")))
        else:
            approvals = ", ".join(dict.fromkeys(str(n.get("approval") or n["id"]) for n in ns))
            out.append(_act("decide", f"{approvals}: {names} ({company})", f"{ids} wait on your approval",
                            warm * NOTE_STEP, HOURS["approve"], f"work/approvals/{ns[0].get('approval')}.md"
                            if ns[0].get("approval") else ns[0]["path"], oldest, company=ns[0].get("company")))
    firsts: dict[str, list[dict[str, Any]]] = {}
    for r in (nu or {}).get("rows") or []:
        if r["per_hour"] <= 0 or r["state"] not in ("READY", "PEOPLE FIRST", "PAGE"):
            continue
        if r["state"] == "PEOPLE FIRST":          # one action per company: the same notes open every role there
            firsts.setdefault(str(r["company"]), []).append(r)
            continue
        out.append(_act({"READY": "apply", "PAGE": "finish page"}[r["state"]], f"{r['company']} · {r['title']}", r["why"],
                        r["expected"], r["expected"] / r["per_hour"] if r["per_hour"] else None,
                        f"applications/{r['slug']}/application.md" if r.get("slug") else f"jobs/{r['job']}.md",
                        state=r["state"], company=r.get("company")))
    for company, rs in firsts.items():
        best = max(rs, key=lambda r: r["per_hour"])
        roles = best["title"] if len(rs) == 1 else f"{len(rs)} roles, best {best['title']}"
        out.append(_act("people first", f"{company} ({roles})", best["why"], best["expected"],
                        best["expected"] / best["per_hour"] if best["per_hour"] else None,
                        f"applications/{best['slug']}/application.md" if best.get("slug") else f"jobs/{best['job']}.md",
                        state="PEOPLE FIRST", company=company))
    out.sort(key=lambda a: (-a["per_hour"], a["created"] or "0000", a["what"]))
    return out


def week(snap: dict[str, Any], note_rows: list[dict[str, Any]], now: datetime, reply_rows: list[dict[str, Any]]) -> dict[str, Any]:
    crm = snap.get("crm") or {}
    kpi = next((k for k in crm.get("kpis") or [] if k.get("key") == "interviews"), {})
    today = now.astimezone(PT).date()
    monday = (today - timedelta(days=today.weekday())).isoformat()
    sent = [t for t in crm.get("touches") or [] if t.get("label") == "sent" and str(t.get("day") or "") >= monday]
    return {"booked": kpi.get("value"), "target": kpi.get("target"), "last_week": kpi.get("last_week"),
            "items": kpi.get("items") or [], "warm_sent": len(sent), "since": monday,
            "waiting": {"approved_notes": sum(n["status"] == "approved" for n in note_rows),
                        "pending_notes": sum(n["status"] == "pending" for n in note_rows),
                        "replies_owed": len(reply_rows)}}


def works(snap: dict[str, Any], rates: dict[str, Any]) -> dict[str, Any]:
    funnel = {x["step"]: x for x in ((snap.get("overview") or {}).get("funnel") or [])}
    return {"warm": channel(rates.get("warm_k", 0), rates.get("warm_n", 0)),
            "cold": channel(rates.get("cold_k", 0), rates.get("cold_n", 0)),
            "applied": (funnel.get("applied") or {}).get("count"),
            "replies": [{"via": k, "k": v.get("replied") if isinstance(v, dict) else None,
                         "n": v.get("applied") if isinstance(v, dict) else None}
                        for k, v in ((snap.get("overview") or {}).get("response_by_via") or {}).items()]}


def coming(snap: dict[str, Any], connectors: list[dict[str, Any]] | None, now: datetime,
           approval_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    crm = snap.get("crm") or {}
    band = crm.get("band48") or {}
    today = now.astimezone(PT).date()
    soon = (today + timedelta(days=7)).isoformat()
    expiring, by_day = [], {}
    for x in approval_rows or []:
        if x["expires"] and today.isoformat() <= x["expires"] <= soon:
            by_day.setdefault((x["expires"], x["status"]), []).append(x["id"])
    for (day, status), ids in by_day.items():
        ids.sort()
        what = "approved, not sent yet" if status == "approved" else "waiting on your decision"
        expiring.append({"what": f"{len(ids)} approval{'s' if len(ids) > 1 else ''} {what} ({ids[0]}"
                                 f"{' to ' + ids[-1] if len(ids) > 1 else ''})", "when": day, "path": "work/approvals/"})
    tokens: dict[str, list[str]] = {}
    for c in connectors or []:
        # green reads "good until <PT date>", amber "re-auth due <PT date>" (connectors_doctor)
        m = re.search(r"(?:until|due) (\d{4}-\d{2}-\d{2})", str(c.get("summary") or ""))
        if m and m.group(1) <= soon:
            tokens.setdefault(m.group(1), []).append(str(c["name"]).replace("-token", ""))
    for day, names in tokens.items():                      # one line per day: they are renewed in one sitting
        expiring.append({"what": f"{', '.join(sorted(names))} tokens (re-authorize{'; ' + CONNECTOR_DOC if CONNECTOR_DOC else ''})",
                         "when": day, "path": CONNECTOR_DOC})
    expiring.sort(key=lambda e: (e["when"], e["what"]))
    return {"clock": (snap.get("clock") or {}).get("line"), "calendar_read": band.get("calendar_read"),
            "rounds": [r for r in band.get("rounds") or [] if r.get("day") and r["day"] <= soon],
            "undated": band.get("rounds_undated") or [], "hidden": band.get("calendar_other") or 0,
            "expiring": expiring, "connectors_read": connectors is not None}


def pipeline(snap: dict[str, Any]) -> list[dict[str, Any]]:
    deals = [d for d in (snap.get("crm") or {}).get("pipeline") or [] if d.get("stage") in LIVE]
    deals.sort(key=lambda d: (-LIVE.index(d["stage"]), -(d.get("days_in_stage") or 0)))
    out = []
    for d in deals:
        nxt = re.sub(r"\s*\([^)]*\)$", "", str(d.get("next") or ""))     # the role that does it is the agency's business
        nxt = {"follow up if no reply": "follow up"}.get(nxt, nxt)
        out.append({**{k: d.get(k) for k in ("slug", "company", "title", "stage", "days_in_stage", "next_at", "warm",
                                             "stopped", "years_flag", "segment", "tier")},
                    "next": nxt + (f" · {str(d['next_at'])[5:10]}" if d.get("next_at") else ""),
                    "open": f"{JS_REL}applications/{d.get('slug')}/application.md"})
    return out


def machine(os_root: Path, connectors: list[dict[str, Any]] | None, today: date) -> dict[str, Any]:
    """The last hunt-boards verdict and the watchdog's state line, from their receipts; connectors in red."""
    def last(routine: str, pattern: str) -> str | None:
        folder = Path(os_root) / "logs" / "runs" / routine
        files = sorted(folder.glob("*.md")) if folder.is_dir() else []
        for f in reversed(files[-2:]):
            hits = re.findall(pattern, f.read_text(encoding="utf-8"), re.M)
            if hits:
                return hits[-1]
        return None
    hunt = last("js-hunt-boards", r"^## Run (\S+) · (ok|warn|failed)$")
    watchdog = last("watchdog", r"^- state (.+)$")
    red = [c["name"] for c in connectors or [] if c.get("state") == "red"]
    return {"hunt": {"at": hunt[0], "status": hunt[1]} if isinstance(hunt, tuple) else None,
            "watchdog": watchdog if isinstance(watchdog, str) else None, "red": red,
            "connectors_read": connectors is not None}


def company_facts(snap: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Company name (lowercased) to its segment and tier, from the CRM accounts and the application rows.
    A name nobody has a segment or tier for maps to None, never to a guess."""
    facts: dict[str, dict[str, Any]] = {}
    for c in (snap.get("crm") or {}).get("companies") or []:
        facts[str(c.get("name") or "").lower()] = {"segment": c.get("segment"), "tier": c.get("tier"), "slug": c.get("slug")}
    for a in snap.get("applications") or []:
        key = str(a.get("company") or "").lower()
        cur = facts.setdefault(key, {"segment": None, "tier": None, "slug": a.get("company_slug")})
        cur["segment"] = cur["segment"] or a.get("segment")
        cur["tier"] = cur["tier"] or a.get("tier")
    facts.pop("", None)
    return facts


def _annotate(rows: list[dict[str, Any]], facts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    for r in rows:
        f = facts.get(str(r.get("company") or "").lower()) or {}
        r.setdefault("company", r.get("company"))
        r["segment"] = r.get("segment") or f.get("segment")
        r["tier"] = r.get("tier") or f.get("tier")
    return rows


BIG = {"big-tech", "enterprise"}
WARM_KINDS = {"people first", "send", "decide"}
OUT_KINDS = {"reply", "send", "decide"}


def modes(acts: list[dict[str, Any]], deals: list[dict[str, Any]], snap: dict[str, Any]) -> list[dict[str, Any]]:
    """The working modes: what the owner works on today, each with the rule that picks its rows and its counts.
    The page filters with the same fields (segment, tier, kind, warm); a mode with no record source says so."""
    def pick(a_rule: Any, d_rule: Any) -> dict[str, int]:
        return {"actions": sum(1 for a in acts if a_rule(a)), "deals": sum(1 for d in deals if d_rule(d))}
    portals = (snap.get("portals") or {}).get("portals") or []
    return [
        {"id": "everything", "label": "Everything", "rule": "every action and deal", "wired": True,
         **pick(lambda a: True, lambda d: True)},
        {"id": "dream", "label": "Dream companies", "rule": "company tier dream (targets/companies.yaml)", "wired": True,
         **pick(lambda a: a.get("tier") == "dream", lambda d: d.get("tier") == "dream")},
        {"id": "startups", "label": "Startups", "rule": "company segment startup", "wired": True,
         **pick(lambda a: a.get("segment") == "startup", lambda d: d.get("segment") == "startup")},
        {"id": "big", "label": "Big companies", "rule": "company segment big-tech or enterprise", "wired": True,
         **pick(lambda a: a.get("segment") in BIG, lambda d: d.get("segment") in BIG)},
        {"id": "referrals", "label": "Referrals and people", "rule": "notes to send or decide, people-first roles, deals with a warm path", "wired": True,
         **pick(lambda a: a.get("kind") in WARM_KINDS, lambda d: bool(d.get("warm")))},
        {"id": "outreach", "label": "Outreach", "rule": "replies owed, notes to send or decide", "wired": True,
         **pick(lambda a: a.get("kind") in OUT_KINDS, lambda d: False)},
        {"id": "portals", "label": "Portals", "rule": "the portal digest (Portals tab)", "wired": True, "tab": "portals",
         "count": len(portals), "actions": 0, "deals": 0},
        {"id": "posts", "label": "Posts", "rule": "LinkedIn posts: the plan and history are prose notes, not records",
         "wired": False, "source": "the LinkedIn notes under state/linkedin/", "actions": 0, "deals": 0},
    ]


def _safe(errors: list[str], what: str, fn: Any, default: Any) -> Any:
    """One block's failure is shown on the page as what could not be read, never a blank page."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{what} could not be read ({type(exc).__name__}: {exc})")
        return default


def build(js: Path, os_root: Path, now: datetime, snap: dict[str, Any], jobs: dict[str, Any] | None = None,
          connectors: list[dict[str, Any]] | None = None, nu: dict[str, Any] | None = None) -> dict[str, Any]:
    import interviews
    errors: list[str] = []
    if nu is None:
        def run_next_up() -> dict[str, Any]:
            import dash_api
            import next_up
            with dash_api.jobs_loaded(jobs):
                return next_up.build(Path(js), now)
        nu = _safe(errors, "NEXT-UP", run_next_up, None)
    today = now.astimezone(PT).date()
    rates = _safe(errors, "the channel rates", lambda: interviews.rates(Path(js), now),
                  {"warm": 0.0, "cold": 0.0, "warm_k": 0, "warm_n": 0, "cold_k": 0, "cold_n": 0})
    note_rows = _safe(errors, "the outbox", lambda: notes(Path(js)), [])
    reply_rows = _safe(errors, "the mail", lambda: replies(Path(js), today, rates.get("warm") or 0.0), [])
    approval_rows = _safe(errors, "the approvals", lambda: approvals(Path(os_root), today, Path(js)), [])
    acts = _safe(errors, "the actions", lambda: actions(snap, nu, note_rows, rates, now, reply_rows), [])
    facts = company_facts(snap)
    _annotate(acts, facts)
    deals = _annotate(_safe(errors, "the pipeline", lambda: pipeline(snap), []), facts)
    if connectors is None:
        errors.append("the connectors could not be read: token expiry dates are not shown")
    return {"generated_at": now.isoformat(), "generated_pt": now.astimezone(PT).strftime("%m-%d %H:%M"),
            "clock": (snap.get("clock") or {}).get("line"),
            "mode": "Applying paused outside the focused lanes" if (nu or {}).get("applying") != "open"
            else "Applying open",
            "next": acts[:5], "more": acts[5:40],
            "week": _safe(errors, "this week", lambda: week(snap, note_rows, now, reply_rows), None),
            "works": _safe(errors, "what works", lambda: works(snap, rates), None),
            "coming": _safe(errors, "the next 7 days", lambda: coming(snap, connectors, now, approval_rows), None),
            "pipeline": deals,
            "modes": _safe(errors, "the modes", lambda: modes(acts, deals, snap), []),
            "machine": _safe(errors, "the machine line", lambda: machine(os_root, connectors, today), None),
            "next_up_counts": (nu or {}).get("counts"), "snapshot_at": snap.get("generated_at"), "errors": errors}


def read_connectors(js: Path, os_root: Path) -> list[dict[str, Any]] | None:
    """connectors_doctor.check_all without the MCP probe (read only); None when they cannot be read."""
    try:
        import os as _os
        import connectors_doctor as cd
        import gmail_auth
        report = cd.check_all(Path(js), Path(os_root), gmail_auth.config_dir().expanduser(), dict(_os.environ),
                              datetime.now(timezone.utc), None)
        return report.get("connectors") if isinstance(report, dict) else None
    except Exception:  # noqa: BLE001 - the page and the brief say they were not read
        return None


def main(argv: list[str] | None = None) -> int:
    """`python3 dashboard/cockpit.py --top 5`: the cockpit's Next 5 as plain lines, for the morning brief (the day plan
    opens with the five actions, not a long list). Read only; builds the same data the page shows."""
    import argparse
    import dash_api
    ap = argparse.ArgumentParser(description=main.__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=Path(jc.JS_ROOT))
    ap.add_argument("--top", type=int, default=5)
    a = ap.parse_args(argv)
    if a.top < 1:
        ap.error("--top must be 1 or more")              # a negative --top once printed every row
    now = datetime.now(timezone.utc)
    os_root = jc.os_root_for(a.root)
    jobs = dash_api.load_jobs(a.root)
    snap = dash_api.build(a.root, os_root, now, jobs=jobs, load=False)
    c = build(a.root, os_root, now, snap, jobs=jobs, connectors=read_connectors(a.root, os_root))
    print(f"Next {a.top} (by the chance of an interview per hour; the cockpit: http://127.0.0.1:8765/cockpit)")
    for i, act in enumerate((c["next"] + c["more"])[:a.top], 1):
        when = "round first" if act["kind"] == "prep" else f"{act['chance']:.0%}, {act['minutes']} min"
        print(f"{i}. {act['kind']}: {act['what']} ({when}). {act['why']}")
    for e in c.get("errors") or []:
        print(f"not read: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
