#!/usr/bin/env python3
"""cards.py - the morning flashcards (agency charter §3 steps 3-5): the sweep's items ranked, each with why it matters,
one proposed action and an effort level, numbered so the owner can answer "yes 1-12, no 4". Read only on the records; it
writes the cards file and nothing else: no message is drafted here (each quick card leaves `draft:` for the Recruiter)
and nothing is sent (a yes is executed by the agent in that session, charter safety lines).

Input: state/recruiter/sweep-<date>.json (scripts/recruiter_sweep.py --write). Signals read from the records: the dream list
(targets/companies.yaml tier dream, strategy_review.dream_slugs), company tier and H-1B count, and prospects at the
company (warm: strategy_review.is_warm; in conversation: status replied, in_conversation or interviewing; personal: a
`relation:` that starts "personal" or "warm referral", or the referrer tag; never do_not_contact).

Rank (top first):
  tier 0  posts and people: hiring posts, people and startups the posts lane found, and the
          recruiter card: a post or mail from an outside recruiter on targets/agency-recruiters.yaml (charter 0.1
          item 1; the lead goes to the named employer; rows on hold are counted, not carded); a "just raised"
          Form D lead (scripts/funding_signals.py) at a watchlist company
  tier 1  people in conversation: mail from people (outreach, interviews, offers, networking), follow-ups and answers
          owed, drafts and approvals waiting on the owner, and every other place the owner's work waits (one human queue): HOME
          needs-you rows for job-search, routine runs that said NEEDS_YOU, and the open setup list
  tier 2  focused-lane listings and applications: a dream company, a warm path, a personal contact or a person in
          conversation at the company (charter §6)
  tier 3  every other listing (gate pass, then gate review), application with a due next action, and "just raised"
          lead at a company not on the watchlist
  within a tier: hunt_score (0-100; else fit_score x 20) high first, then location (the owner's preferred areas in order, policy.location.preferred;
  then the rest of the US, then unknown; charter §1), then the item key.
Actions: quick = comment, reply, reach-out, connect, quick-apply-base, decide (an approval file); deep = research,
tailored page. Quick cards are numbered first, then deep cards; on a yes, quick cards run at once and deep cards go to
the day queue (scripts/day_queue.py build).
Effort (charter §7: effort follows importance): E1 a decision or a nudge already drafted; E2 a reply, a reach-out, a
base-resume application, a gate review; E3 a high-fit (hunt 70+ or fit 4.0+) or big-company role (tier strong, or 100+
H-1B approvals in 3 years) needing alignment, an assessment, a post or message at a dream or warm company; E4 a role at
a dream company or through a warm or personal path (research and a tailored page); E5 an offer.
Listings are capped at --per-company (default 5, in rank order: 89 new roles at one company in one day make 5 cards) and, outside
the focused tier, at --max-listings (default 60) cards. No cap on applying (charter §7): every tier-3 quick-apply-base
listing below that cut is folded into ONE quick card, "[mass batch] N base-resume applications on employer sites"
(its `items` hold every listing); a yes on it queues all N as mass items (day_queue.py build). Other listings below the
cut are counted, never dropped silently (they stay itemised in the sweep file). A listing whose job id or company plus
title is already an application at applied or later is skipped and counted ("already applied"). Mail that is
information only (ATS confirmations, rejections) and approved outbox items (the sender's) are counted, not carded.

Output (--write, into --out, default state/recruiter/): cards-<date>.md (the flashcards; each card ends with an empty
`decision:` line, and a `decisions:` line at the top takes a batch answer like "yes 1-12, no 4") and cards-<date>.json
(the same cards, for day_queue.py). Without --write: a summary.

CLI:  python3 scripts/cards.py [--root JS] [--date YYYY-MM-DD | --sweep PATH] [--out DIR] [--max-listings N] [--write] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as cfg  # noqa: E402
import js_common as jc  # noqa: E402
from js_common import JS_ROOT, cli_date, dept_day, fm  # noqa: E402

DEFAULT_OUT = Path("state") / "recruiter"
QUICK = ("comment", "reply", "reach-out", "connect", "quick-apply-base", "decide")
DEEP = ("research", "tailored page")
MESSAGE_ACTIONS = ("comment", "reply", "reach-out", "connect")
PEOPLE_MAIL = {"recruiter_outreach", "founder_outreach", "hiring_manager_outreach", "referral_intro", "networking",
               "interview_invite", "interview_scheduling", "review"}
INFO_MAIL = {"ats_confirmation", "rejection"}
CONVERSATION = {"replied", "in_conversation", "interviewing"}
HIGH_HUNT, HIGH_FIT, BIG_H1B = 70.0, 4.0, 100
OWNER = str(cfg.get("identity.owner_label", "you") or "you")
#: the owner's preferred areas, best first: [{name, pattern (regex on the location text), kinds (fit_gates location kinds)}]
LOC_PREFERRED = [p for p in (cfg.get("policy.location.preferred", []) or []) if isinstance(p, dict)]
LOC_NAMES = tuple(str(p.get("name") or "preferred") for p in LOC_PREFERRED) + ("rest of US", "location unknown")
LOC_UNKNOWN = len(LOC_NAMES) - 1
DECISION_WORDS = {"yes": "yes", "y": "yes", "no": "no", "n": "no", "later": "later", "hold": "later"}


# --------------------------------------------------------------------------- signals
class Signals:
    """Company-level signals from the records (dream, tier, people), resolved through company_index."""

    def __init__(self, root: Path):
        self.notes: list[str] = []
        try:
            from company_index import CompanyIndex
            self.index = CompanyIndex.load(root)
        except Exception as exc:  # noqa: BLE001 - names then compare by slugify
            self.index, _ = None, self.notes.append(f"company index unreadable ({type(exc).__name__})")
        try:
            import strategy_review as sr
            self.dream = set(sr.dream_slugs(root, self.index))
            self._is_warm = sr.is_warm
        except Exception as exc:  # noqa: BLE001
            self.dream, self._is_warm = set(), (lambda m: False)
            self.notes.append(f"dream list unreadable ({type(exc).__name__}): no dream signal today")
        self.tier: dict[str, str] = {}
        try:
            from fetch_ats import load_companies
            for c in load_companies(root):
                if c.get("slug"):
                    self.tier[self.slug(str(c["slug"]))] = str(c.get("tier") or "")
        except Exception as exc:  # noqa: BLE001
            self.notes.append(f"targets/companies.yaml unreadable ({type(exc).__name__})")
        self.people: dict[str, dict[str, list[str]]] = {}
        for path, meta, _ in jc.iter_records(root / "prospects"):
            if str(meta.get("status") or "") == "do_not_contact":
                continue
            slug = self.slug(str(meta.get("company_slug") or meta.get("company") or ""))
            if not slug:
                continue
            pid = str(meta.get("id") or path.stem)
            sig = self.people.setdefault(slug, {"warm": [], "conversation": [], "personal": []})
            if self._is_warm(meta):
                sig["warm"].append(pid)
            if str(meta.get("status") or "") in CONVERSATION:
                sig["conversation"].append(pid)
            rel = str(meta.get("relation") or "").lower()
            if rel.startswith(("personal", "warm referral")) or "referrer" in (meta.get("tags") or []):
                sig["personal"].append(pid)

    def slug(self, name: str) -> str:
        name = str(name or "").strip()
        if not name:
            return ""
        if self.index is not None:
            try:
                return self.index.resolve(name)[0] or jc.slugify(name)
            except Exception:  # noqa: BLE001
                pass
        return jc.slugify(name)

    def of(self, item: dict[str, Any]) -> dict[str, Any]:
        slug = self.slug(item.get("company_slug") or item.get("company") or "")
        people = self.people.get(slug, {"warm": [], "conversation": [], "personal": []}) if slug else \
            {"warm": [], "conversation": [], "personal": []}
        h1b = item.get("h1b_approvals_3y")
        big = self.tier.get(slug) == "strong" or (isinstance(h1b, (int, float)) and h1b >= BIG_H1B)
        return {"slug": slug, "dream": bool(slug) and slug in self.dream, "big": bool(big), **people}


# --------------------------------------------------------------------------- location and score
def location_tier(text: Any) -> int:
    """The index of the first preferred area (policy.location.preferred) the text matches, then the rest of the US
    (remote US included), then unknown or outside the US (the last index, LOC_UNKNOWN)."""
    loc = str(text or "").strip()
    if not loc:
        return LOC_UNKNOWN
    try:
        import fit_gates
        parts = [p for p in re.split(r"\s*(?:\||;|\bor\b|/(?=\s))\s*", loc) if p.strip()]
        kinds = [fit_gates._location_kind(p) for p in parts]
    except Exception:  # noqa: BLE001
        kinds = []
    for i, area in enumerate(LOC_PREFERRED):
        pattern = str(area.get("pattern") or "")
        if any(k in kinds for k in area.get("kinds") or []) or (pattern and re.search(pattern, loc, re.I)):
            return i
    if any(k in ("us-other", "us-unspecified", "remote-us") for k in kinds) or re.search(
            r"\b(united states|usa|remote us|us remote)\b", loc, re.I):
        return len(LOC_PREFERRED)
    return LOC_UNKNOWN


def score_of(item: dict[str, Any]) -> float:
    for key, scale in (("hunt_score", 1.0), ("fit_score", 20.0)):
        v = item.get(key)
        if isinstance(v, (int, float)):
            return float(v) * scale
    return 0.0


# --------------------------------------------------------------------------- the card
def plan(item: dict[str, Any], sig: dict[str, Any]) -> tuple[int, str, str, list[str]] | None:
    """(tier, action, effort, why) for one sweep item, or None when it is not carded."""
    warm_any = sig["warm"] or sig["personal"] or sig["conversation"]
    why: list[str] = []
    if sig["dream"]:
        why.append("dream company (targets/companies.yaml tier dream)")
    if sig["personal"]:
        why.append(f"personal contact: {', '.join(sig['personal'][:3])}")
    if sig["conversation"]:
        why.append(f"in conversation: {', '.join(sig['conversation'][:3])}")
    if sig["warm"] and not sig["personal"]:
        why.append(f"warm path: {', '.join(sig['warm'][:3])}")
    kind = item.get("kind")
    up = "E3" if (sig["dream"] or warm_any) else "E2"
    if item.get("recruiter") and not (kind == "mail" and item.get("label") in INFO_MAIL):
        if item.get("recruiter_held"):
            return None                                   # on hold until the owner says: counted, never carded
        return 0, "reply" if kind == "mail" else "reach-out", up, [
            f"an outside recruiter who places AI engineers: {item['recruiter']} (targets/agency-recruiters.yaml); "
            "the lead goes to the named employer"] + why
    if kind in ("hiring_post", "person", "startup"):
        lead = {"hiring_post": "a hiring post: a person to talk to while it is fresh (charter §5)",
                "person": "a person who can be a door to a job (charter §5)",
                "startup": "a new startup to look at (charter §5)"}[kind]
        action = {"hiring_post": "reach-out", "person": "connect", "startup": "research"}[kind]
        return 0, action, up, [lead] + why
    if kind == "funding":
        amount = f"${item['amount']:,}" if isinstance(item.get("amount"), (int, float)) else "amount not stated"
        lead = (f"just raised ({amount}, Form D {item.get('file_date')}): hiring before posting; find the founder or "
                "hiring lead and the roles (charter 0.1, the finding mandate)")
        extra = [f"{item['open_jobs']} open jobs on file"] if item.get("open_jobs") else []
        return (0 if item.get("target") else 3), "research", "E2", [lead] + extra + why
    if kind == "mail":
        label = item.get("label")
        if label in INFO_MAIL:
            return None
        if label == "offer":
            return 1, "research", "E5", ["an offer: comparison and negotiation (the negotiator)"] + why
        if label == "ats_assessment":
            return 1, "research", "E3", ["an assessment to take: prep first"] + why
        if label in ("interview_invite", "interview_scheduling"):
            return 1, "reply", "E2", [f"{label.replace('_', ' ')}: an interview is the goal"] + why
        if label in PEOPLE_MAIL:
            return 1, "reply", up, [f"{label.replace('_', ' ')}: a person wrote"] + why
        return 1, "reply", "E1", [f"{label}: read and decide"] + why
    if kind == "follow_up":
        fk = item.get("follow_up_kind")
        overdue = f"due {item.get('due')}, {item.get('days_overdue')} days overdue"
        if fk == "application":
            return 1, "reach-out", "E2", [f"a silent application: find a person at the company and reach them "
                                          f"(charter §6); {overdue}"] + why
        return 1, "reply", "E1", [f"{fk}: {item.get('next') or 'a nudge or an answer owed'}; {overdue}"] + why
    if kind == "approval":
        return 1, "decide", "E1", [f"approval waiting on {OWNER}: {item.get('action')} → {item.get('target')}; "
                                   f"expires {item.get('expires')}"] + why
    if kind == "outbox":
        if item.get("status") not in ("pending", "drafted"):
            return None
        action = "reach-out" if item.get("outbox_kind") == "intro" else "reply"
        return 1, action, "E1", [f"a draft waiting on {OWNER} ({item.get('outbox_kind')}, {item.get('channel')})"] + why
    if kind == "needs_you":
        return 1, "decide", "E1", [f"HOME needs you: {item.get('why') or 'waiting on ' + OWNER}"] + why
    if kind == "routine_flag":
        return 1, "decide", "E1", [f"routine {item.get('routine')} asked for {OWNER} ({item.get('status') or 'flag'}): "
                                   f"{item.get('detail')}"] + why
    if kind == "setup":
        first = ", ".join(item.get("first") or []) or ", ".join((item.get("open") or [])[:3])
        return 1, "decide", "E1", [f"setup list: {len(item.get('open') or [])} items only {OWNER} can do; first {first} "
                                   f"(each unblocks a portal or a channel)"] + why
    if kind in ("listing", "application"):
        tier = 2 if (sig["dream"] or warm_any) else 3
        if kind == "application":
            nxt = str(item.get("next_action") or "")
            action = "reach-out" if re.search(r"follow|nudge|reach|ping", nxt, re.I) else "research"
            return tier, action, "E2", [f"next action due {item.get('next_action_at')}: {nxt or 'not stated'}"] + why
        score = score_of(item)
        if item.get("hunt_score") is not None:
            why.append(f"hunt score {item['hunt_score']}")
        if item.get("hiring_signal"):
            why.append("a hiring post names this role")
        if item.get("gate") == "review":
            return tier, "research", "E2", ["gate review open: settle the gate first"] + why
        if sig["dream"] or warm_any:
            return tier, "tailored page", "E4", why + ["effort follows importance: research and a tailored page"]
        high = score >= HIGH_HUNT if item.get("hunt_score") is not None else score >= HIGH_FIT * 20
        if sig["big"] or high:
            why.append("big company: heavy alignment" if sig["big"] else "high fit")
            return tier, "tailored page", "E3", why
        return tier, "quick-apply-base", "E2", why + ["a normal role the base fits: the base resume"]
    return None


def title_of(item: dict[str, Any]) -> str:
    if item.get("recruiter"):
        return f"[recruiter {item['recruiter']}] " + _title(item)
    return _title(item)


def _title(item: dict[str, Any]) -> str:
    k = item.get("kind")
    if k in ("hiring_post", "person", "startup"):
        bits = [item.get("author") or "unknown author"]
        if item.get("author_title"):
            bits[0] += f" ({item['author_title']})"
        bits += [x for x in (item.get("company"), item.get("role_hint")) if x]
        return f"[{item.get('platform')} {k.replace('_', ' ')}] " + " · ".join(str(b) for b in bits)
    if k == "funding":
        return f"[just raised] {item.get('company')} · Form D {item.get('file_date')}"
    if k == "mail":
        return f"[mail {item.get('label')}] {jc_clip(item.get('from'))}: {item.get('subject')}"
    if k == "follow_up":
        return f"[follow-up {item.get('follow_up_kind')}] {item.get('name') or item.get('who')}" + \
            (f" · {item['company']}" if item.get("company") else "")
    if k == "approval":
        return f"[approval {item.get('id')}] {item.get('action')} → {item.get('target')}"
    if k == "outbox":
        return f"[draft {item.get('id')}] {item.get('outbox_kind')} · {item.get('channel')} · " \
               f"{item.get('prospect') or item.get('to')}"
    if k == "needs_you":
        return f"[needs you] {item.get('title')}"
    if k == "routine_flag":
        return f"[routine {item.get('routine')}] {item.get('run')} {item.get('status')}"
    if k == "setup":
        return f"[setup list] {len(item.get('open') or [])} open: {', '.join((item.get('open') or [])[:8])}"
    if k == "application":
        return f"[application] {item.get('company')} · {item.get('title')} ({item.get('stage')})"
    return f"[{item.get('source')}] {item.get('company')} · {item.get('title')} · {item.get('location') or 'location not given'}"


def jc_clip(text: Any, n: int = 60) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:n]


def build(root: Path, sweep: dict[str, Any], max_listings: int = 60, per_company: int = 5) -> dict[str, Any]:
    signals = Signals(root)
    cands, counted = [], {"information only": 0, "approved drafts (the sender's)": 0, "listings below the cut": 0}
    applied = applied_index(root)
    for group, items in (sweep.get("groups") or {}).items():
        if group == "scam":
            continue
        for item in items or []:
            if already_applied(item, applied, signals):
                counted["already applied"] = counted.get("already applied", 0) + 1
                continue
            sig = signals.of(item)
            p = plan(item, sig)
            if p is None:
                counted["approved drafts (the sender's)" if item.get("kind") == "outbox" else "information only"] += 1
                continue
            tier, action, effort, why = p
            loc_text = item.get("location") or item.get("location_hint") or ""
            lt = location_tier(loc_text)
            if lt < LOC_UNKNOWN or loc_text:
                why = why + [LOC_NAMES[lt]]
            cands.append({"item_key": item["key"], "kind": item.get("kind"), "group": group, "tier": tier,
                          "action": action, "mode": "quick" if action in QUICK else "deep", "effort": effort,
                          "score": round(score_of(item), 2), "location_tier": lt, "why": why, "title": title_of(item),
                          "url": item.get("url"), "job": item.get("job"), "slug": item.get("slug"),
                          "company": item.get("company"), "company_slug": sig["slug"] or None,
                          "dream": sig["dream"], "warm": sig["warm"] + sig["personal"] + sig["conversation"]})
    order = lambda c: (c["tier"], -c["score"], c["location_tier"], c["item_key"])  # noqa: E731
    cands.sort(key=order)
    kept, mass, per, folded = [], 0, {}, []
    counted["listings beyond the per-company cap"] = 0
    for c in cands:
        if c["kind"] == "listing":
            company = c["company_slug"] or str(c["company"] or "")
            per[company] = per.get(company, 0) + 1
            if per_company and per[company] > per_company:
                counted["listings beyond the per-company cap"] += 1
                continue
            if c["tier"] == 3:
                mass += 1
                if mass > max_listings:
                    if c["action"] == "quick-apply-base":
                        folded.append(c)                 # charter §7, no cap: into the one mass batch card
                    else:
                        counted["listings below the cut"] += 1
                    continue
        kept.append(c)
    quick = [c for c in kept if c["mode"] == "quick"] + mass_batch_card(folded)
    deep = [c for c in kept if c["mode"] == "deep"]
    date = str(sweep.get("date"))
    for n, c in enumerate(quick + deep, 1):
        c["n"], c["id"] = n, f"C-{date}-{n:03d}"
    return {"type": "cards", "date": date, "sweep": f"sweep-{date}.json", "since": sweep.get("since"),
            "cards": quick + deep, "quick": len(quick), "deep": len(deep), "counted": counted,
            "notes": signals.notes, "max_listings": max_listings, "per_company": per_company,
            "header": header(root, date)}


# --------------------------------------------------------------------------- the header
def header(root: Path, day: Any) -> list[str]:
    """What the cards are aimed at and what was learned, shown before the cards: progress
    against today's and this week's floors (targets/goals.yaml), the job clock, interview rounds in the next 7 days,
    LESSONS re-checks due, experiments running with n per arm (lessons.py), the learning rows still proposed and the
    items the last evening wrap left for the owner (evening_wrap.py). Each part degrades to one line saying why it is
    missing; none stops the cards."""
    from datetime import date as _date, timedelta
    day = _date.fromisoformat(str(day)) if not isinstance(day, _date) else day
    L = ["## Goals and learning", ""]

    def part(fn: Any, name: str) -> None:
        try:
            L.extend(fn())
        except Exception as exc:  # noqa: BLE001 - a missing part is said, never fatal
            L.append(f"- {name}: unreadable ({type(exc).__name__}: {jc_clip(exc, 120)})")
    try:
        import lessons as ls
    except Exception as exc:  # noqa: BLE001
        return L + [f"- lessons.py unreadable ({type(exc).__name__}): no goals or learning today"]
    part(lambda: [f"- {ls.clock_line(root, day)}"], "job clock")

    def goals() -> list[str]:
        g = ls.goal_lines(ls.progress(root, day))
        return ["- " + g[0]] + ["  " + x for x in g[1:]]
    part(goals, "goals")

    def rounds() -> list[str]:
        r = ls.rounds(root, day)

        def one(x: dict[str, Any], when: bool = True) -> str:
            text = f"{x.get('company') or x.get('slug')} {x.get('stage')}" + (f" {x.get('when')}" if when else "")
            return text + (f" ({x['flag']})" if x.get("flag") else "")      # a flag is shown, never dropped
        line = [f"- Interview rounds in the next {ls.ROUND_DAYS} days ({r['source']}): "
                + ("; ".join(one(x) for x in r["rounds"]) or "none on record")]
        if r["undated"]:
            line.append("  - no round time on record (`tracker.py event --round-at`): "
                        + "; ".join(one(x, when=False) for x in r["undated"]))
        return line
    part(rounds, "interview rounds")

    def due() -> list[str]:
        rows, _ = ls.lessons(root)
        d = ls.due(rows, day)
        return [f"- Re-checks due (LESSONS.md): {len(d)}"] + [f"  - {x['recheck_on']}: {x['what']} -> {x['change']}"
                                                              for x in d[:5]]
    part(due, "re-checks")

    def running() -> list[str]:
        from datetime import datetime as _dt, time, timezone
        ex, problems = ls.experiments(root, _dt.combine(day, time(23, 59), timezone.utc))
        why = f" ({'; '.join(problems)})" if problems and not ex else ""
        return [f"- Experiments: {len(ex)} ({sum(e['ready'] for e in ex)} ready to decide){why}"] + [
            f"  - {e['id']}: {e['state']}; {ls.arms_text(e)}" for e in ex if e["result"] in (None, "")]
    part(running, "experiments")

    def learned() -> list[str]:
        lr = ls.learned(root)
        since = (day - timedelta(days=1)).isoformat()
        fresh = [x for x in lr["current"] if x["status"] == "proposed" and x["date"] >= since]
        return [f"- Learned since {since} (state/learn, proposed): {len(fresh)}"] + [
            f"  - {x['role']}: {x['observation']}" for x in fresh[:8]]
    part(learned, "learned")

    def wrap() -> list[str]:
        import evening_wrap as ew
        path = ew.last_wrap(root, day)
        if path is None:
            return ["- Last evening wrap: none written yet (`scripts/evening_wrap.py --write`)"]
        items = ew.decide_items(path)
        return [f"- Last evening wrap ({path.name}): {len(items)} for {OWNER} to decide"] + [f"  - {x}" for x in items[:8]]
    part(wrap, "evening wrap")
    return L


# --------------------------------------------------------------------------- applied before, and the mass batch card
APPLIED_OR_LATER = ("applied", "confirmation", "screen", "oa", "interviewing", "final", "offer", "accepted", "rejected",
                    "ghosted", "withdrawn")
MASS_BATCH = "mass-apply-batch"
MASS_SHOWN = 12


def _title_key(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def applied_index(root: Path) -> dict[str, set]:
    """Job ids, slugs and (company, title) pairs of every application at applied or later (so a role already applied to is never carded
    for a second application). Read from applications/<slug>/application.md."""
    idx: dict[str, set] = {"jobs": set(), "slugs": set(), "pairs": set()}
    folder = root / "applications"
    for path in sorted(folder.glob("*/application.md")) if folder.is_dir() else []:
        try:
            meta, _ = fm.load(path)
        except Exception:  # noqa: BLE001 - an unreadable record is not proof of an application
            continue
        if str(meta.get("stage") or "") not in APPLIED_OR_LATER:
            continue
        idx["slugs"].add(path.parent.name)
        job = re.sub(r"^\[\[|\]\]$", "", str(meta.get("job") or "")).strip()
        if job:
            idx["jobs"].add(job)
        company = jc.slugify(str(meta.get("company_slug") or meta.get("company") or ""))
        if company and meta.get("title"):
            idx["pairs"].add((company, _title_key(meta.get("title"))))
    return idx


def already_applied(item: dict[str, Any], idx: dict[str, set], signals: "Signals | None" = None) -> bool:
    """A listing whose job id, slug or company plus title is already an application at applied or later. An
    application item (a due next action, a follow-up) is never skipped: it is about the one already sent."""
    if item.get("kind") != "listing":
        return False
    if str(item.get("job") or "") in idx["jobs"] or str(item.get("slug") or "") in idx["slugs"]:
        return True
    names = {jc.slugify(str(item.get("company_slug") or "")), jc.slugify(str(item.get("company") or ""))}
    if signals is not None:
        names.add(jc.slugify(signals.slug(item.get("company_slug") or item.get("company") or "")))
    title = _title_key(item.get("title"))
    return bool(title) and any((n, title) in idx["pairs"] for n in names if n)


def mass_batch_card(folded: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Charter §7 (no cap): every tier-3 quick-apply-base listing below the --max-listings cut, folded into ONE quick
    card. A yes on it queues each listing as a mass item (day_queue.py build); the apply lane submits them on the
    employer's own site with the base resume. [] when nothing was folded."""
    if not folded:
        return []
    items = [{k: c.get(k) for k in ("item_key", "job", "slug", "url", "company", "company_slug", "title", "effort",
                                    "score")} for c in folded]
    n = len(items)
    return [{"item_key": "mass-batch", "kind": "mass_batch", "group": "listings", "tier": 3, "action": MASS_BATCH,
             "mode": "quick", "effort": "E2", "score": 0.0, "location_tier": LOC_UNKNOWN,
             "why": [f"yes = submit all {n} base-resume applications on employer sites (charter §7: no cap; effort "
                     "follows importance, these are the roles the base fits)",
                     "LinkedIn Easy Apply, Indeed Apply and ZipRecruiter 1-Click roles come back as \"Your clicks\""],
             "title": f"[mass batch] {n} base-resume applications on employer sites", "url": None, "job": None,
             "slug": None, "company": None, "company_slug": None, "dream": False, "warm": [], "items": items}]


def mass_batch_lines(card: dict[str, Any]) -> list[str]:
    """The batch card's item list on the morning page: the first MASS_SHOWN, then where the rest are."""
    if card.get("action") != MASS_BATCH:
        return []
    items = card.get("items") or []
    out = [f"- items ({len(items)}):"]
    out += [f"  - {i.get('title')} · `{i.get('item_key')}`" for i in items[:MASS_SHOWN]]
    if len(items) > MASS_SHOWN:
        out.append(f"  - and {len(items) - MASS_SHOWN} more (cards-<date>.json, this card's `items`)")
    return out


# --------------------------------------------------------------------------- render and decisions
def render(res: dict[str, Any]) -> str:
    q, d = res["quick"], res["deep"]
    rng = lambda a, b: f"{a}-{b}" if b > a else str(a)  # noqa: E731
    L = [f"# Morning cards {res['date']}", "",
         f"{q + d} cards from the sweep since {res['since']}: quick {q}" + (f" (cards {rng(1, q)})" if q else "")
         + f", deep {d}" + (f" (cards {rng(q + 1, q + d)})" if d else "") + ".",
         "Answer in one line, for example \"yes 1-12, no 4\"; the Recruiter writes it on the `decisions:` line or on "
         "each card. A yes runs a quick card at once in this session; a deep card goes to the day queue "
         "(`scripts/day_queue.py build`). Nothing goes out without that yes.", ""]
    counted = {k: v for k, v in res["counted"].items() if v}
    if counted:
        L.append("Not carded (still itemised in the sweep file): " + ", ".join(f"{k} {v}" for k, v in counted.items()) + ".")
    L += [f"Note: {n}" for n in res["notes"]]
    L += [""] + res["header"] if res.get("header") else []
    L += ["", "decisions:", ""]
    for head, mode in (("## Quick cards", "quick"), ("## Deep work (day queue on yes)", "deep")):
        L += [head, ""]
        for c in (c for c in res["cards"] if c["mode"] == mode):
            L += [f"### {c['n']} · {c['title']}", f"- id: {c['id']} · item `{c['item_key']}`"
                  + (f" · {c['url']}" if c.get("url") else ""),
                  f"- why: {'; '.join(c['why']) or 'listed in the sweep'}",
                  f"- action: {c['action']} ({c['mode']}) · effort {c['effort']}"]
            if c["action"] in MESSAGE_ACTIONS:
                L.append("- draft:")
            L += mass_batch_lines(c)
            L += ["- decision:", ""]
    return "\n".join(L) + "\n"


def parse_answer(text: str) -> dict[int, str]:
    """"yes 1-12, no 4" -> {1: yes, ..., 12: yes, 4: no}; read left to right, a later word wins."""
    out: dict[int, str] = {}
    word = None
    for tok in re.findall(r"[A-Za-z]+|\d+\s*-\s*\d+|\d+", str(text or "")):
        low = tok.lower()
        if low in DECISION_WORDS:
            word = DECISION_WORDS[low]
            continue
        if word is None or not tok[0].isdigit():
            continue
        a, _, b = tok.replace(" ", "").partition("-")
        for n in range(int(a), int(b or a) + 1):
            out[n] = word
    return out


def decisions(md: str) -> dict[int, str]:
    """Card number -> yes, no or later: the top `decisions:` line first, then each card's own `decision:` line wins."""
    top = re.search(r"^decisions:[ \t]*(.*)$", md, re.M)
    out = parse_answer(top.group(1)) if top else {}
    current = None
    for line in md.splitlines():
        m = re.match(r"^### (\d+) · ", line)
        if m:
            current = int(m.group(1))
            continue
        m = re.match(r"^- decision:[ \t]*(\S.*)?$", line)
        if m and current is not None and m.group(1):
            word = DECISION_WORDS.get(m.group(1).strip().split()[0].lower().strip(".,;:"))
            if word:
                out[current] = word
    return out


def atomic_write(path: Path, text: str) -> None:
    import os
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def later_batch(out: Path, res: dict[str, Any]) -> tuple[Path | None, Path | None, dict[str, Any]]:
    """After the owner answered today's cards, a later sweep's new items go to cards-<date>-b<k>.md/.json (k = 2, 3 ...):
    only items no earlier batch today carded, numbered on from the last card, so "yes 31-35" stays unambiguous.
    day_queue.py build reads every batch of the day."""
    date = res["date"]
    earlier = sorted(out.glob(f"cards-{date}.json")) + sorted(out.glob(f"cards-{date}-b*.json"),
                                                             key=lambda p: int(re.sub(r"\D", "", p.stem.rsplit("-b", 1)[1]) or 0))
    seen, last, k = set(), 0, 1
    for p in earlier:
        try:
            old = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        seen |= {c.get("item_key") for c in old.get("cards") or []}
        last = max([last] + [int(c.get("n") or 0) for c in old.get("cards") or []])
        if "-b" in p.stem:
            k = max(k, int(p.stem.rsplit("-b", 1)[1]))
    fresh = [c for c in res["cards"] if c["item_key"] not in seen]
    if not fresh:
        return None, None, res
    for n, c in enumerate(fresh, last + 1):
        c["n"], c["id"] = n, f"C-{date}-{n:03d}"
    batch = dict(res, cards=fresh, quick=sum(c["mode"] == "quick" for c in fresh),
                 deep=sum(c["mode"] == "deep" for c in fresh), batch=k + 1)
    return out / f"cards-{date}-b{k + 1}.md", out / f"cards-{date}-b{k + 1}.json", batch


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--root", type=Path, default=JS_ROOT)
    p.add_argument("--date", type=cli_date, default=None, help="the sweep's day (default: today in the owner's time zone)")
    p.add_argument("--sweep", type=Path, default=None, help="a sweep json (default: <out>/sweep-<date>.json)")
    p.add_argument("--out", type=Path, default=None, help="folder (default: <root>/state/recruiter)")
    p.add_argument("--max-listings", type=int, default=60, help="tier-3 listings carded (default 60)")
    p.add_argument("--per-company", type=int, default=5, help="listings carded per company (default 5; 0 = no cap)")
    p.add_argument("--write", action="store_true")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    out = a.out or a.root / DEFAULT_OUT
    day = a.date or dept_day(datetime.now(timezone.utc))
    path = a.sweep or out / f"sweep-{day.isoformat()}.json"
    try:
        sweep = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"refused: no sweep to read at {path} ({exc}); run scripts/recruiter_sweep.py --write first", file=sys.stderr)
        return 2
    res = build(a.root, sweep, a.max_listings, a.per_company)
    if a.json:
        print(json.dumps(res, indent=1, default=str))
    else:
        by_effort: dict[str, int] = {}
        for c in res["cards"]:
            by_effort[c["effort"]] = by_effort.get(c["effort"], 0) + 1
        print(f"Cards {res['date']}: {len(res['cards'])} (quick {res['quick']}, deep {res['deep']}); by effort "
              + ", ".join(f"{k} {v}" for k, v in sorted(by_effort.items()))
              + "; not carded " + ", ".join(f"{k} {v}" for k, v in res["counted"].items()))
    if a.write:
        md, js = out / f"cards-{res['date']}.md", out / f"cards-{res['date']}.json"
        if md.is_file() and decisions(md.read_text(encoding="utf-8")):
            md, js, res = later_batch(out, res)          # answered cards are never overwritten; new ones go to a batch
            if md is None:
                print(f"nothing new: every card is already in today's answered cards ({out}/cards-{res['date']}*.md)")
                return 0
        meta = {"type": "cards", "date": res["date"], "built_by": "scripts/cards.py", "sweep": res["sweep"],
                "counts": {"quick": res["quick"], "deep": res["deep"]}}
        atomic_write(md, fm.dump(meta, render(res)))
        atomic_write(js, json.dumps(res, indent=1, ensure_ascii=False, default=str) + "\n")
        print(f"wrote {md}\nwrote {js}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
