#!/usr/bin/env python3
"""The job-search CRM: one markdown file per person in prospects/ (schema schemas/prospect.yaml).

    python3 scripts/prospects.py ingest [FILE ...]      (classified Gmail JSONL, classified LinkedIn JSONL, outbound-<date>.jsonl;
                                                         default: everything in state/harvest/, the newest
                                                         LinkedIn export as its .classified.jsonl)
    python3 scripts/prospects.py score [--status new,triaged | --all] [--apply-baseline]
    python3 scripts/prospects.py select --batch-size 25 --priority P0,P1 [--format json]
    python3 scripts/prospects.py due
    python3 scripts/prospects.py list [--status ..] [--priority ..] [--company-type ..] [--role ..] [--source ..]
                                      [--company ..] [--since YYYY-MM-DD] [--tag ..] [--possible-duplicates]
                                      [--sponsorship ..] [--label ..] [--format table|json]
    python3 scripts/prospects.py set <id> key=value ... [--timeline TEXT] [--add-tag T] [--remove-tag T] [--note TEXT]
        e.g. set rae-lin warmth=2 fit_score=4.5 sponsorship_hint=yes priority=P1 status=triaged next_touch=2027-01-15
             set rae-lin status=do_not_contact do_not_contact_reason="asked to stop, 2027-01-10"   (one-way)
             set rae-lin applications+=acme-ml-engineer        set alex-kim-2 merge_into=alex-kim
             set parisa req_ids+=R-123 referral_window_ends=2027-01-31 hiring_post_url=https://x.com/a/status/1
        tags (--add-tag, tags+=, tags=) must be in schemas/tags.yaml; an unknown one refuses the change
    python3 scripts/prospects.py stats
    python3 scripts/prospects.py outcomes [--write]   (link via_prospect back; screen paths never P3; dry run default)
    python3 scripts/prospects.py reconcile --harvest FILE [FILE ...]   (classified Gmail and LinkedIn files)

ingest   turns classified Gmail harvests (outreach, referral and networking labels) and the parsed
         LinkedIn export into prospects, and attaches every later message (in or out, any label) from
         a known person to that person. Merging is conservative: same email, same LinkedIn URL, or same
         name AND matching company; a same-name match without corroboration stays a separate record
         tagged `possible-duplicate` (never merged), and conflicting LinkedIn URLs never merge. A
         LinkedIn notification email and the same message in the export count once (content
         fingerprint). Timelines are append-only; every new message is logged to touches/.
         Both channels take one route: a LinkedIn export with unclassified inbound messages is refused
         (exit 2): run `mail_classify.py classify --in <file> --l3 agent`, resolve, then ingest the
         .classified.jsonl. Suspected scams create no prospect and move no pipeline (tag only).
reconcile  never lose a sender: every inbound human sender with a people label (outreach, referral,
         networking) in the classified files, keyed by email address or, for LinkedIn (export touches and
         LinkedIn relay emails), by normalized profile URL (a name when there is no URL), is written to
         state/harvest/senders-<today>.txt and looked up in prospects/ (emails, linkedin_url, names).
         Prints `MISSING <key>` for each one without a prospect and exits 1 if there is any.
score    refreshes the computed fields only: warmth 0-3 (recency, personalization, sender seniority,
         named role, live conversation), fit 1-5 (role hint vs targets; sponsorship "no" is a weighed risk) and
         score_reasons, never over a hand-set (locked) value. Status and priority are prospect-triage's
         call (`set`): score prints them as suggested_priority with days_since_inbound/outbound per
         prospect and writes neither. --apply-baseline restores the old writes (priority, new -> triaged).
         Default pass: status new only; --status S[,S] or --all for recompute passes. A file is
         rewritten only when a computed field changed.
outcomes every application's via_prospect is listed on that prospect's `applications` (linked both ways), and a
         prospect whose path reached a screen or further (tracker.py max_progress) is raised to P1 if it sat at P2 or P3.
         score and ingest keep the same floor, so a rescoring never drops it; only
         do_not_contact is exempt. Prints linked, raised and missing (a via_prospect with no record); --write applies.
select   the next reply batch; due: a nudge 4 business days after our message, a second 5 after it (9), at most 2
         nudges, never later than a referral_window_ends; `windows` lists referral windows ending or lapsed within 7 days.
set      schema-validated edits; warmth, fit_score, priority and sponsorship_hint set by hand are locked
         against `score`, dated in `locked_on`, and marked in score_reasons as
         "<field> (set manually <date>: <value>)" by `set` and every later `score`, so the reasons never
         show the script's number for a value a person chose. A manual sponsorship_hint re-derives an
         unlocked fit: a plain "no" weighs it down by policy.sponsorship.weight and flags it; only
         citizenship, a clearance or an E-Verify, I-983 or OPT refusal (fit_gates.CORE_HARD) gives fit 1.0.
         do_not_contact can be set (with a reason) but never lifted by a script.
All filterable metadata is frontmatter, so Obsidian Bases (views/prospects.base) and dashboard.py
can query it. Scripts never send anything.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Any, Iterable

import yaml

import agency_config as cfg
import js_common as jc
import js_comms_util as cu
import mail_classify as mc
from company_index import CompanyIndex, extends_company
from company_index import derive as derive_slug

SCHEMA_ENUMS = {
    "company_type": ("bigtech", "ai-lab", "startup", "scaleup", "enterprise", "agency", "recruiting-firm",
                     "university", "nonprofit", "other", "unknown"),
    "role": ("recruiter", "sourcer", "founder", "hiring-manager", "engineer", "executive", "investor", "alumni",
             "other", "unknown"),
    "source": ("gmail-backlog", "linkedin-export", "linkedin-live", "referral", "event", "outbound", "inbound-new"),
    "priority": ("P0", "P1", "P2", "P3"),
    "status": ("new", "triaged", "drafted", "replied", "in_conversation", "interviewing", "dormant", "closed",
               "do_not_contact"),
    "sponsorship_hint": ("yes", "no", "unknown", "cap_exempt"),
}
MAX_FOLLOW_UPS = 2                 # two nudges, then stop
FOLLOW_UP_GAPS = (4, 5)            # business days after our latest message: 4, then 5 after the first nudge (day 9)
FOLLOW_UP_BUSINESS_DAYS = FOLLOW_UP_GAPS[0]
LEGACY_AUTO_DAYS = 5               # older records had next_touch_at set +5 days automatically; that is not a deliberate deferral


def follow_up_gap(sent: int) -> int:
    return FOLLOW_UP_GAPS[min(max(sent, 0), len(FOLLOW_UP_GAPS) - 1)]


def follow_up_cap(meta: dict) -> int:
    """A record's own max_follow_ups can only lower the global cap (older records carry 3)."""
    return min(int(meta.get("max_follow_ups") or MAX_FOLLOW_UPS), MAX_FOLLOW_UPS)
WINDOW_WARN_DAYS = 7         # `due` lists a referral window (some employers give a 30-day window from the invite) a week
                             # before it ends and for a week after it lapsed
STALE_DAYS = 180
SUMMARY_CAP = 160            # records hold summaries; full bodies stay in the local, git-ignored state/harvest/
LAST_INBOUND = "Last inbound (untrusted)"
ACTIVE_FOR_REPLY = ("new", "triaged")


def _cfg_list(path: str) -> list[str]:
    """A list of strings from the config (empty when unset)."""
    value = cfg.get(path, []) or []
    return [str(x) for x in value] if isinstance(value, (list, tuple)) else []


# Company lists that classify a sender's employer, compared by normalized name; the lists are the user's
# (policy.prospects.*), empty by default.
BIGTECH = {jc.normalize_company(x) for x in _cfg_list("policy.prospects.bigtech_companies")}
AI_LABS = {jc.normalize_company(x) for x in _cfg_list("policy.prospects.ai_lab_companies")}
RECRUITING_FIRM_NAME = re.compile(r"\b(staffing|recruit\w*|talent|search|headhunt\w*|placement|executive search)\b", re.I)
SCALEUP_CUES = re.compile(r"\b(series [c-g]|pre-ipo|unicorn|late[- ]stage)\b", re.I)


def _alt(words: list[str]) -> str:
    return "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True))


# Words that, in front of "engineer/scientist/...", make a job title worth recording as a role hint. The user's own
# fields live in policy.prospects.title_words; the lead-role fields ("head of <field>") in policy.prospects.lead_fields.
_TITLE_WORDS = _cfg_list("policy.prospects.title_words") or ["software", "backend", "back-end", "full-stack", "platform", "data"]
_LEAD_FIELDS = _cfg_list("policy.prospects.lead_fields") or ["engineering"]
TITLE_RE = re.compile(
    r"\b((?:senior|sr\.?|staff|principal|lead|founding|junior|mid-level)\s+)?"
    rf"((?:{_alt(_TITLE_WORDS)})\s+){{1,3}}"
    r"(engineer|scientist|researcher|developer|architect)(\s+(?:ii|iii|iv|l\d))?\b", re.I)
LEAD_ROLE_RE = re.compile(rf"\b(head|director|vp) of ({_alt(_LEAD_FIELDS)})\b|\bfounding (engineer|engineers)\b", re.I)
COMP_RE = re.compile(r"\$\s?\d{2,3}(?:[,.]\d{3})?\s?[kK]?\+?(?:\s?(?:-|–|to)\s?\$?\s?\d{2,3}(?:[,.]\d{3})?\s?[kK]?)?")
REMOTE_RE = re.compile(r"\b(fully remote|remote[- ](?:first|friendly|ok)|remote (?:in|across) the us|remote \(us\)|remote)\b", re.I)
HYBRID_RE = re.compile(r"\bhybrid\b", re.I)
ONSITE_RE = re.compile(r"\b(onsite|on-site|in[- ]office|in person|in-person)\b", re.I)
# Places recognised in a message as a location hint, first match wins (policy.location.hint_places replaces this list).
LOCATIONS = _cfg_list("policy.location.hint_places") or [
    "San Francisco", "New York", "NYC", "Seattle", "Austin", "Boston", "Los Angeles", "Chicago", "Denver", "London", "Remote"]
# Titles that count against fit, for example a level the user is not seeking (policy.prospects.avoid_title_regex).
_AVOID = str(cfg.get("policy.prospects.avoid_title_regex", "") or "")
AVOID_TITLE_RE = re.compile(_AVOID, re.I) if _AVOID else None
# Where the user would work without it counting against fit (policy.location.home_area, lower case; empty = no penalty).
HOME_AREA = {x.lower() for x in _cfg_list("policy.location.home_area")}
CONTRACT_RE = re.compile(r"\b(contract-to-hire|contract to hire|c2c|corp[- ]to[- ]corp|1099|w2 contract|\d+[- ]month contract|contract role)\b", re.I)
SPONSOR_NO = [re.compile(p, re.I) for p in (
    r"\b(unable|not able|cannot|can't|can not|won't|will not|do not|don't|does not|doesn't|are not able to|is not able to)\s+(to\s+)?(offer\s+|provide\s+|support\s+)?(any\s+)?(visa\s+|h-?1b\s+)?sponsor",
    r"\bsponsorship (is |will )?(not|no longer) (be )?(available|offered|provided|possible|supported)\b",
    r"\bno (visa |h-?1b )?sponsorship\b",
    r"\bwithout (the need for |requiring )?(visa |current or future )?sponsorship\b",
    r"\bmust be (a |an )?(u\.?s\.? citizen|us citizen|green card|permanent resident|authorized to work[^.]{0,40}without)",
    r"\b(us|u\.s\.) citizens? only\b|\b(usc|gc)\s*(/|or|and)\s*(gc|usc)( holders?)? only\b",
    r"\b(security|secret|ts/sci|top secret) clearance\b",
)]
SPONSOR_YES = [re.compile(p, re.I) for p in (
    r"\b(we|they|company|client|the team)\s+(do |will |can |are able to |is able to |happily |gladly )?(sponsor|support)\s+(h-?1b|visas?|visa sponsorship|work visas|stem opt|opt)\b",
    r"\b(visa|h-?1b) sponsorship (is )?(available|offered|provided|possible|supported)\b",
    r"\bsponsor(s|ing)? (h-?1b|visas|your visa)\b|\bsponsor your visa\b|\bwould sponsor\b",
    r"\bopen to sponsoring\b|\bcan transfer h-?1bs?\b|\btransfer h-?1bs?\b|\bsupport(s)? (stem )?opt\b",
)]
_SPECIFIC_EXTRA = _cfg_list("policy.prospects.specific_refs")      # the user's own employers, schools, projects
SPECIFIC_REF = re.compile(r"\b(" + "|".join([*(re.escape(x) for x in _SPECIFIC_EXTRA),
                                            r"your (post|talk|repo|github|article|paper|writing)", r"your work (on|at)"]) + r")\b", re.I)
GENERIC_GREETING = re.compile(r"^\W*(dear (candidate|applicant|sir|madam)|hi there|hello there|hello,|greetings)\b", re.I | re.M)
INTRO_REPLY = re.compile(r"\b(moving|move) (\w+ )?to bcc\b|\bthanks (so much )?for (the intro|connecting us|introducing)\b", re.I)

try:  # C loader: ~6x faster frontmatter parsing for list/stats at 1,000+ records
    _FAST_LOADER = yaml.CSafeLoader
except AttributeError:  # pragma: no cover
    _FAST_LOADER = yaml.SafeLoader


# --------------------------------------------------------------------------- records
def new_meta(slug: str, name: str, source: str, today: date) -> dict[str, Any]:
    return {
        "id": slug, "name": name, "headline": None, "title": None, "company": None, "company_slug": None,
        "company_slug_source": None,
        "company_type": "unknown", "role": "unknown", "seniority": "unknown", "location": None,
        "emails": [], "linkedin_url": None, "channels": [], "source": source, "sources": [source], "label": None,
        "first_contact_at": None, "last_inbound_at": None, "last_outbound_at": None,
        "inbound_count": 0, "outbound_count": 0,
        "role_hint": None, "comp_hint": None, "remote_hint": None, "location_hint": None,
        "sponsorship_hint": "unknown", "sponsorship_evidence": None, "personalization": 0.0,
        "warmth": 0, "fit_score": 3.0, "priority": "P3", "priority_locked": False, "score_reasons": [],
        "status": "new", "next_touch_at": None, "follow_ups_sent": 0, "max_follow_ups": MAX_FOLLOW_UPS,
        "thread_ids": [], "linkedin_threads": [], "applications": [], "tags": [], "possible_duplicates": [],
        "do_not_contact_reason": None, "reply_context": None, "created": today, "updated": today,
    }


COUNT_FIELDS = ("inbound_count", "outbound_count", "follow_ups_sent", "max_follow_ups")


def fill_lists_and_counts(meta: dict[str, Any]) -> dict[str, Any]:
    """Give a record written by hand (or by an older script) the list and count fields of new_meta, so the event
    code can append and increment: `outbox.py mark-sent` crashed on such records. Present values
    win; a missing or null list becomes [] (sources: [source]), a missing or null count its new_meta default.
    Other fields stay as they are: filling them would change the record's score."""
    template = new_meta(str(meta.get("id") or ""), str(meta.get("name") or ""), str(meta.get("source") or ""), date.min)
    for key, default in template.items():
        if meta.get(key) is not None:
            continue
        if isinstance(default, list):
            meta[key] = [meta["source"]] if key == "sources" and meta.get("source") else []
        elif key in COUNT_FIELDS:
            meta[key] = default
    return meta


def fast_meta(path: Path) -> dict[str, Any]:
    raw, _ = jc.fm.split(path.read_text(encoding="utf-8"))
    return (yaml.load(raw, Loader=_FAST_LOADER) or {}) if raw else {}


def iter_metas(roots: cu.Roots) -> Iterable[tuple[Path, dict[str, Any]]]:
    folder = roots.prospects
    if not folder.is_dir():
        return
    for p in sorted(folder.glob("*.md")):
        if p.name.startswith(("_", ".")) or p.name == "README.md":
            continue
        try:
            yield p, fast_meta(p)
        except (jc.fm.FrontmatterError, yaml.YAMLError) as exc:
            print(f"warning: skipping {p.name}: {exc}", file=sys.stderr)


def _s(value: Any) -> str:
    return "" if value is None else (value.isoformat() if hasattr(value, "isoformat") else str(value))


# --------------------------------------------------------------------------- text helpers
def fingerprint(text: str | None) -> str | None:
    norm = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()
    return jc.short_hash(norm[:120], n=12) if len(norm) >= 20 else None


def relay_message_text(body: str) -> str:
    """The personal message inside a LinkedIn notification email (header and footer dropped)."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", body or "") if p.strip()]
    keep = []
    for i, para in enumerate(paras):
        lines = [ln.strip() for ln in para.splitlines() if ln.strip()]
        if i == 0 and (any("linkedin.com/" in ln for ln in lines) or (len(lines) <= 3 and all(len(ln.split()) <= 8 for ln in lines))):
            continue
        if all(mc.FOOTER_LINE.match(ln) or re.match(r"^(this email was intended|you are receiving|© )", ln, re.I) for ln in lines):
            continue
        keep.append(" ".join(lines))
    return "\n\n".join(keep)


# Board relays. A job-board box that relays many recruiters from one shared address (mc.BOARD_PERSON_RELAYS, for example
# a board's seekerteam@ address) is keyed by the person, like the LinkedIn relays: no shared address on the record, the
# company from the classifier's identity (the invite's subject), the name from the display name ("<Name> at <Company>")
# when that is a person, else from the invite's "I'm <Name> and I've been using <Board> ...".
# A host under mc.BOARD_PERSON_RELAY_DOMAINS is the same kind of relay (an employer's applicant-tracking CRM, for example
# user-<hash>@<employer>.mail.<vendor>.com, where the hash names the recipient, not the writer); the company is the
# employer the host names (an identity read from a digest's text can be a job title), never the vendor. A team display
# name ("The Talent Acquisition team at <Employer>") is not a person: the note's own "My name is <Name>" names the
# writer; with no name in the note the team stays the record, one per employer.
BOARD_RELAY_SELF_NAME = re.compile(r"\bI(?:'|’)?m (?P<name>[A-Z][\w'.-]*(?: [A-Z][\w'.-]*){0,3}) and "
                                   r"I(?:'|’)?ve been using\b")
BOARD_RELAY_MY_NAME = re.compile(r"\b(?i:my name is) (?P<name>[A-Z][\w'.-]*(?: [A-Z][\w'.-]*){0,3})")
BOARD_RELAY_NOT_A_PERSON = frozenset({"inc", "llc", "ltd", "corp", "corporation", "co", "company", "group", "staffing",
                                      "solutions", "services", "technologies", "consulting", "partners",
                                      "international", "recruiting", "talent", "ziprecruiter",
                                      "team", "careers", "hiring", "recruitment", "hr", "people"})


def board_person_relay(email: str | None) -> bool:
    """A box that relays many people from one address: in mc.BOARD_PERSON_RELAYS, or on a host under a
    mc.BOARD_PERSON_RELAY_DOMAINS domain."""
    host = cu.email_domain(email or "")
    return email in mc.BOARD_PERSON_RELAYS or any(host.endswith("." + d) for d in mc.BOARD_PERSON_RELAY_DOMAINS)


def board_relay_employer(email: str | None) -> str | None:
    """The employer a relay host names (user-<hash>@acme.mail.clinchtalent.com gives "Acme"), else None."""
    host = cu.email_domain(email or "")
    sub = next((host[:-len(d) - 1] for d in mc.BOARD_PERSON_RELAY_DOMAINS if host.endswith("." + d)), "")
    return " ".join(p.capitalize() for p in sub.rsplit(".", 1)[-1].split("-") if p) or None


def board_relay_name(display: str | None, body: str | None, company: str | None) -> str | None:
    """The recruiter's name on a board relay's mail: the display name before " at <Company>" when it reads as a person
    (1 to 4 words, no company or team word, not the company itself), else the name the note gives ("I'm <Name> and
    ...", "My name is <Name>"), else the display name as it is."""
    head = re.split(r"\s+at\s+", str(display or "").strip().strip('"'), maxsplit=1)[0].strip()
    words = [w.strip(".,").lower() for w in head.split()]
    if head and 1 <= len(words) <= 4 and not set(words) & BOARD_RELAY_NOT_A_PERSON \
            and not (company and cu.companies_match(head, company)):
        return head
    m = BOARD_RELAY_SELF_NAME.search(body or "") or BOARD_RELAY_MY_NAME.search(body or "")
    return m.group("name").rstrip(".") if m else (head or None)


def hints(text: str, subject: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for source in (subject, text):
        m = TITLE_RE.search(source or "") or LEAD_ROLE_RE.search(source or "")
        if m:
            out["role_hint"] = re.sub(r"\s+", " ", m.group(0)).strip()
            break
    m = COMP_RE.search(text or "")
    if m:
        out["comp_hint"] = re.sub(r"\s+", " ", m.group(0)).strip()
    if REMOTE_RE.search(text or ""):
        out["remote_hint"] = "remote"
    if HYBRID_RE.search(text or ""):
        out["remote_hint"] = "hybrid"
    elif ONSITE_RE.search(text or "") and out.get("remote_hint") != "remote":
        out["remote_hint"] = "onsite"
    for loc in LOCATIONS:
        if re.search(rf"\b{re.escape(loc)}\b", text or "", re.I) and loc.lower() != "remote":
            out["location_hint"] = loc
            break
    out["contract"] = bool(CONTRACT_RE.search(text or ""))
    return out


def sponsorship_hint(text: str) -> tuple[str, str | None]:
    for sentence in cu.split_sentences(mc.unwrap(text or "")):
        if any(rx.search(sentence) for rx in SPONSOR_NO):
            return "no", cu.one_line(sentence, 200)
    for sentence in cu.split_sentences(mc.unwrap(text or "")):
        if any(rx.search(sentence) for rx in SPONSOR_YES):
            return "yes", cu.one_line(sentence, 200)
    return "unknown", None


def personalization(text: str, bulk: bool = False) -> float:
    score = 0.0
    if SPECIFIC_REF.search(text or ""):
        score += 1.0
    if mc.ADDRESSED.search((text or "")[:200]):
        score += 0.5
    if len(text or "") >= 350 and not bulk:
        score += 0.5
    if bulk or GENERIC_GREETING.search((text or "")[:80]):
        score -= 0.5
    return max(0.0, min(2.0, score))


# --------------------------------------------------------------------------- inference
def infer_company_type(company: str | None, domain: str | None, text: str, role: str | None) -> str:
    domain = (domain or "").lower()
    name = jc.normalize_company(company or "")
    if domain.endswith(".edu") or re.search(r"\b(university|college|institute of technology)\b", name):
        return "university"
    if name in AI_LABS or any(name == lab or name.startswith(lab + " ") for lab in AI_LABS):
        return "ai-lab"
    if name in BIGTECH or name.split(" ")[0] in BIGTECH and len(name.split()) <= 2:
        return "bigtech"
    if RECRUITING_FIRM_NAME.search(company or "") or RECRUITING_FIRM_NAME.search(cu.registrable_domain(domain).split(".")[0] if domain else ""):
        return "recruiting-firm"
    if role in ("recruiter", "sourcer") and mc.AGENCY.search(text or ""):
        return "recruiting-firm"
    if SCALEUP_CUES.search(text or ""):
        return "scaleup"
    if role == "founder" or mc.STARTUP_CUES.search(text or ""):
        return "startup"
    return "unknown"


def infer_role(identity_role: str | None, title: str | None, label: str | None) -> str:
    t = (title or "").lower()
    if re.search(r"\bsourc(er|ing)\b", t):
        return "sourcer"
    if identity_role in ("founder", "recruiter", "hiring-manager", "engineer", "investor"):
        return identity_role
    if identity_role == "executive":
        return "executive"
    return {"recruiter_outreach": "recruiter", "founder_outreach": "founder",
            "hiring_manager_outreach": "hiring-manager"}.get(label or "", "unknown" if not label else "other")


def infer_seniority(role: str, title: str | None) -> str:
    t = (title or "").lower()
    if role in ("founder", "executive") or re.search(r"\b(head|director|vp|vice president|chief|cto|ceo|founder|partner)\b", t):
        return "exec"
    if re.search(r"\b(senior|sr|lead|principal|staff|manager)\b", t):
        return "senior"
    if re.search(r"\b(junior|associate|coordinator|intern)\b", t):
        return "junior"
    return "mid" if t else "unknown"


def personal_domain_company(name: str | None, email: str | None) -> str | None:
    """Employer from the sender's domain, unless the domain is just the person's own name (luis@ortega.test)."""
    company = cu.company_from_domain(email or "")
    if not company:
        return None
    parts = set(cu.normalize_person_name(name).split())
    keys = cu.company_keys(company)
    if parts and (keys & parts or keys & {"".join(sorted(parts)), "".join(cu.normalize_person_name(name).split())}):
        return None
    return company


def choose_company(identity_company: str | None, domain_company: str | None) -> str | None:
    if identity_company and domain_company:
        same = cu.companies_match(identity_company, domain_company) or extends_company(identity_company, domain_company)
        return identity_company if same else domain_company
    return identity_company or domain_company


# --------------------------------------------------------------------------- events
def _event(**kw: Any) -> dict[str, Any]:
    base = {"key": None, "at": None, "direction": "in", "channel": "email", "source": "gmail-backlog", "label": None,
            "confidence": 0.0, "create": False, "count": True, "person": {}, "subject": "", "summary": "", "text": "",
            "thread_id": None, "message_id": None, "references": None, "reply_to": "", "tags": [],
            "fingerprint": None, "bulk": False, "identity_role": None, "portal": None}
    base.update(kw)
    return base


def gmail_events(rows: Iterable[dict], account_emails: Iterable[str] | None = None) -> list[dict[str, Any]]:
    accounts = {a.lower() for a in (account_emails or ())} | set(cu.ACCOUNT_EMAILS)
    events: list[dict[str, Any]] = []
    portals: dict[str, str] | None = None
    for row in rows:
        cls = row.get("classification") or mc.classify(row, decider=mc.ReviewBackend(), account_emails=accounts).to_dict()
        name, email = cu.parse_address(row.get("from"))
        direction = "out" if row.get("direction") == "out" or email in accounts else "in"
        at = row.get("internal_date") or cu.to_utc_iso(row.get("date"))
        recipients = cu.parse_address_list(row.get("to")) + cu.parse_address_list(row.get("cc"))
        portal = row.get("portal")                   #: set by mail_classify.classify_file; else resolved here
        if portal is None:
            portals = mc.portal_index() if portals is None else portals
            senders = [email] if direction == "in" else [r for _, r in recipients if r and r not in accounts]
            portal = next((p for p in (mc.portal_for(x, portals) for x in senders) if p), None)
        common = dict(at=at, source="gmail-backlog", subject=row.get("subject") or "", thread_id=row.get("thread_id"),
                      message_id=row.get("message_id"), references=row.get("references"),
                      bulk=bool(row.get("list_unsubscribe") or row.get("list_id")), portal=portal)
        if direction == "out":
            for rname, remail in recipients:
                if not remail or remail in accounts:
                    continue
                events.append(_event(key=f"gm-{row['id']}-{remail}", direction="out", channel="email", count=True,
                                     person={"name": rname, "emails": [remail], "linkedin_url": "",
                                             "company": cu.company_from_domain(remail), "title": None},
                                     summary=cu.one_line(row.get("snippet"), 160),
                                     fingerprint=fingerprint(row.get("snippet")), **common))
            continue
        identity = cls.get("identity") or {}
        body = row.get("body") or row.get("snippet") or ""
        label = cls.get("label")
        relay = email in mc.LINKEDIN_RELAYS or (cu.registrable_domain(cu.email_domain(email)) == "linkedin.com")
        board_relay = board_person_relay(email)                  # many people, one address: keyed by the person
        if relay:
            text = relay_message_text(body)
            urls = cu.find_linkedin_urls(body)
            person = {"name": name, "emails": [], "linkedin_url": urls[0] if urls else "",
                      "company": identity.get("company"), "title": identity.get("title")}
            channel, fp = "linkedin-message", fingerprint(text)
        elif board_relay:
            text = body
            company = board_relay_employer(email) or identity.get("company")
            person = {"name": board_relay_name(name, body, company), "emails": [], "linkedin_url": "",
                      "company": company, "title": identity.get("title"), "domain": ""}
            channel, fp = "email", None
        else:
            text = body
            person = {"name": name, "emails": [email] if email else [], "linkedin_url": "",
                      "company": choose_company(identity.get("company"), personal_domain_company(name, email)),
                      "title": identity.get("title"), "domain": cu.email_domain(email)}
            channel, fp = "email", None
        create = label in mc.PROSPECT_LABELS or (label == mc.REVIEW and bool(mc.JOB_VOCAB.search(text)))
        create = create and (relay or board_relay or not mc.is_machine_sender(email))   # a machine is never a person
        tags = ["needs-review"] if label == mc.REVIEW else []
        if label == "referral_intro" and not INTRO_REPLY.search(text):
            tags.append("referrer")
        events.append(_event(key=f"gm-{row['id']}", direction="in", channel=channel, label=label,
                             confidence=cls.get("confidence") or 0.0, create=create, person=person, text=text,
                             summary=cu.one_line(cu.strip_injections(text)[0] or row.get("snippet"), 160),
                             reply_to=row.get("reply_to") or "", tags=tags, fingerprint=fp,
                             identity_role=identity.get("role"), **common))
        if label == "referral_intro" and not relay:
            for rname, remail in recipients:
                if remail and remail not in accounts and remail != email:
                    events.append(_event(key=f"gm-{row['id']}-intro-{remail}", direction="in", channel="email",
                                         label="referral_intro", create=True, count=False, tags=["introduced"],
                                         person={"name": rname, "emails": [remail], "linkedin_url": "",
                                                 "company": cu.company_from_domain(remail), "title": None},
                                         summary=cu.one_line(f"introduced by {name or email}", 160), text="",
                                         **{**common, "at": at}))
    return events


_EMAIL_RE = re.compile(r"[^@\s<>]+@[^@\s<>]+\.[a-z]{2,}")


def outbound_events(rows: Iterable[dict], now: str | None = None) -> list[dict[str, Any]]:
    """People the prospector found (state/harvest/outbound-<date>.jsonl), and hiring-post authors (scripts/posts.py,
    outbound-posts-<date>.jsonl: x_handle and hiring_post_url too): new records, no messages."""
    events = []
    for row in rows:
        name = row.get("name") or ""
        url = cu.normalize_linkedin_url(row.get("linkedin_url") or row.get("linkedin"))
        email = str(row.get("email") or "").strip().lower() or None
        if email and not _EMAIL_RE.fullmatch(email):
            email = None                                          # "Unknown - to confirm", "none", "n/a": no address
        handle = None
        if row.get("x_handle"):
            try:
                handle = validate_field("x_handle", row["x_handle"])[1]
            except ValueError:
                handle = None                                     # not a handle: the row still makes its person
        source = row.get("source") if row.get("source") in ("outbound", "referral") else "outbound"
        post = str(row.get("hiring_post_url") or "")
        key = "added-" + jc.short_hash((url or email or (f"x:{handle}" if handle else "")
                                        or f"{cu.normalize_person_name(name)}|{row.get('company')}")
                                       + (f"|{post}" if post else ""))     # a poster's next hiring post: a new line
        extra = {k: row[k] for k in ("found_via", "relation", "req_id") if row.get(k)}
        if handle:
            extra["x_handle"] = handle
        if re.fullmatch(r"https?://\S+", post):
            extra["hiring_post_url"] = post
        if row.get("role") in SCHEMA_ENUMS["role"]:
            extra["role"] = row["role"]
        via = f"found via {row.get('found_via')}" if row.get("found_via") else "added"
        channel = "linkedin-message" if url and not email else ("x" if handle and not email else "email")
        events.append(_event(key=key, at=row.get("at") or now or jc.utc_now_iso(), direction="in", channel=channel,
                             source=source, count=False, create=True, kind="added",
                             person={"name": name, "emails": [email] if email else [], "linkedin_url": url,
                                     "company": row.get("company"), "title": row.get("title"), "x_handle": handle},
                             subject="", summary=cu.one_line(f"{via}; {row.get('relation') or 'outbound'}", 160),
                             extra=extra, channels=[c for c, have in (("email", email), ("linkedin", url), ("x", handle))
                                                    if have]))
    return events


class IngestError(RuntimeError):
    """An input file cannot be ingested as it is; nothing was written."""


def unclassified_hint(path: Path) -> str:
    return (f"{Path(path).name} has unclassified inbound LinkedIn messages: run "
            f"`python3 scripts/mail_classify.py classify --in {path} --l3 agent` first (answer its L3 queue with "
            f"`mail_classify.py resolve`), then ingest the .classified.jsonl file")


def linkedin_events(data: dict[str, Any]) -> list[dict[str, Any]]:
    people = {p["key"]: p for p in data.get("people") or []}
    events: list[dict[str, Any]] = []
    for t in data.get("touches") or []:
        p = people.get(t.get("person_key")) or {}
        text = t.get("text") or ""
        person = {"name": p.get("name") or t.get("person_name") or "", "emails": [p["email"]] if p.get("email") else [],
                  "linkedin_url": p.get("linkedin_url") or "", "company": p.get("company"), "title": p.get("position")}
        ev = _event(key=t["key"], at=t.get("at"), direction=t.get("direction", "in"), channel=t.get("channel"),
                    source="linkedin-export", subject=t.get("subject") or "", thread_id=t.get("conversation_id"),
                    text=text, summary=cu.one_line(cu.strip_injections(text)[0], 160), person=person,
                    fingerprint=fingerprint(text) if t.get("channel") == "linkedin-message" else None)
        if ev["direction"] == "in":
            done = t.get("classification")
            if not done:                                  # one route for both channels: classify, resolve, ingest
                raise IngestError(f"inbound LinkedIn message {t.get('key')} is not classified")
            label, confidence, identity = done.get("label"), done.get("confidence") or 0.0, done.get("identity") or {}
            ev["label"], ev["confidence"] = label, confidence
            ev["identity_role"] = identity.get("role")
            person["company"] = person["company"] or identity.get("company")
            person["title"] = person["title"] or identity.get("title")
            ev["create"] = label in mc.PROSPECT_LABELS or (label == mc.REVIEW and bool(mc.JOB_VOCAB.search(text)))
            if label == mc.REVIEW:
                ev["tags"] = ["needs-review"]
            if label == "referral_intro" and not INTRO_REPLY.search(text):
                ev["tags"] = ev["tags"] + ["referrer"]
        events.append(ev)
    return events


# --------------------------------------------------------------------------- matching
class Index:
    """Lookup tables over the current prospects for conservative identity resolution."""

    def __init__(self) -> None:
        self.by_email: dict[str, str] = {}
        self.by_url: dict[str, str] = {}
        self.by_x: dict[str, str] = {}                    #: a hiring-post author is found again by handle
        self.by_name: dict[str, list[str]] = {}
        self.metas: dict[str, dict[str, Any]] = {}

    def add(self, slug: str, meta: dict[str, Any]) -> None:
        if meta.get("merged_into"):
            return                                   # folded into another record: route mail there
        self.metas[slug] = meta
        for e in meta.get("emails") or []:
            self.by_email[str(e).lower()] = slug
        if meta.get("linkedin_url"):
            self.by_url[cu.normalize_linkedin_url(meta["linkedin_url"])] = slug
        if meta.get("x_handle"):
            self.by_x[str(meta["x_handle"]).lower().lstrip("@")] = slug
        key = cu.normalize_person_name(meta.get("name"))
        if key and slug not in self.by_name.setdefault(key, []):
            self.by_name[key].append(slug)

    def match(self, person: dict[str, Any]) -> tuple[str | None, list[str]]:
        for e in person.get("emails") or []:
            if e and e.lower() in self.by_email:
                return self.by_email[e.lower()], []
        url = cu.normalize_linkedin_url(person.get("linkedin_url"))
        if url and url in self.by_url:
            return self.by_url[url], []
        handle = str(person.get("x_handle") or "").lower().lstrip("@")
        if handle and handle in self.by_x:
            return self.by_x[handle], []
        key = cu.normalize_person_name(person.get("name"))
        candidates = self.by_name.get(key, []) if key else []
        possible = []
        for slug in candidates:
            meta = self.metas[slug]
            other_url = cu.normalize_linkedin_url(meta.get("linkedin_url"))
            if url and other_url and url != other_url:
                possible.append(slug)                            # conflicting profiles: two people
                continue
            if person.get("company") and meta.get("company") and cu.companies_match(person["company"], meta["company"]):
                return slug, []
            possible.append(slug)
        return None, possible


def _unique_slug(roots: cu.Roots, index: Index, name: str, company: str | None, fallback: str) -> str:
    taken = set(index.metas) | {p.stem for p in roots.prospects.glob("*.md")} if roots.prospects.is_dir() else set(index.metas)
    for candidate in (jc.slugify(name or fallback), jc.slugify(f"{name} {company}") if company else None):
        if candidate and candidate != "unknown" and candidate not in taken:
            return candidate
    base = jc.slugify(name or fallback)
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


# --------------------------------------------------------------------------- applying events
def _add_unique(lst: list, value: Any) -> None:
    if value and value not in lst:
        lst.append(value)


def _merge_person(meta: dict[str, Any], person: dict[str, Any], ev: dict[str, Any]) -> None:
    for e in person.get("emails") or []:
        _add_unique(meta["emails"], e.lower())
    url = cu.normalize_linkedin_url(person.get("linkedin_url"))
    if url and not meta.get("linkedin_url"):
        meta["linkedin_url"] = url
    company = person.get("company")
    if company:
        current = meta.get("company")
        if not current:
            meta["company"] = company
        elif cu.companies_match(current, company) and " " in company and " " not in current:
            meta["company"] = company                           # "Mosaicrobotics" -> "Mosaic Robotics"
        elif extends_company(company, current):
            meta["company"] = company                           # "Harborline" -> "Harborline Robotics"
        meta["company_slug"] = derive_slug(meta["company"])
    if person.get("title") and (not meta.get("title") or ev["source"] == "linkedin-export"):
        meta["title"] = person["title"]
    if meta.get("title"):
        meta["headline"] = f"{meta['title']} at {meta['company']}" if meta.get("company") else meta["title"]
    _add_unique(meta["sources"], ev["source"])
    if "channels" in ev:                                           # outbound rows: the contact points the row has
        for channel in ev["channels"]:
            _add_unique(meta["channels"], channel)
    else:
        _add_unique(meta["channels"], "email" if ev["channel"] == "email" else "linkedin")
    meta["channels"].sort()
    if ev["channel"] == "email" and ev.get("thread_id"):
        _add_unique(meta["thread_ids"], ev["thread_id"])
    elif ev["channel"] == "linkedin-message" and ev.get("thread_id") and ev["source"] == "linkedin-export":
        _add_unique(meta["linkedin_threads"], ev["thread_id"])
    for tag in ev.get("tags") or []:
        if tag == "referrer" and "introduced" in meta["tags"]:
            continue
        _add_unique(meta["tags"], tag)


def _timeline_line(ev: dict[str, Any], kind: str) -> str:
    parts = [ev["at"] or "?", ev["direction"] if ev.get("count", True) else (ev.get("kind") or "intro"), ev["channel"],
             ev.get("label") or kind, f"\"{cu.one_line(ev.get('subject'), 90)}\"", cu.one_line(ev.get("summary"), 160)]
    if ev.get("thread_id"):
        parts.append(f"thread {ev['thread_id']}")
    line = "- " + " · ".join(p for p in parts if p) + f" [k:{ev['key']}]"
    if ev.get("fingerprint"):
        line += f" [f:{ev['fingerprint']}]"
    return line


# The interview gate: an interview, scheduling, assessment or offer mail moves a prospect's stage only when it belongs
# to a live application; otherwise it goes to review and never changes a stage.
GATED_LABELS = ("interview_invite", "interview_scheduling", "ats_assessment", "offer")


def live_applications(roots: cu.Roots, today: date) -> list[dict[str, Any]]:
    """Every application folder whose projected stage is still open (not rejected, ghosted, withdrawn or accepted),
    through tracker.py's own loader and projection."""
    import tracker
    now = datetime(today.year, today.month, today.day, 23, 59, tzinfo=timezone.utc)
    out = []
    for slug, meta, body in tracker.iter_applications(roots.js) or []:
        proj = tracker.project(tracker.parse_events(body), now, meta.get("ghost_after_days"), meta.get("last_inbound_at"))
        if proj["stage"] in (*tracker.TERMINAL, "accepted"):
            continue
        out.append({"slug": slug, "company_slug": str(meta.get("company_slug") or ""),
                    "company_key": jc.normalize_company(str(meta.get("company") or "")),
                    "prospects": {str(x) for x in meta.get("prospects") or []},
                    "threads": {str(x) for x in meta.get("thread_ids") or []}})
    return out


def _live_application(meta: dict[str, Any], ev: dict[str, Any], live: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The live application this mail belongs to: linked from the prospect (applications) or to it (prospects), the same
    mail thread, or the same company. None when nothing matches."""
    pid = str(meta.get("id") or "")
    linked = {str(x) for x in meta.get("applications") or []}
    thread = str(ev.get("thread_id") or "")
    keys = {jc.normalize_company(str(c)) for c in (meta.get("company"), (ev.get("person") or {}).get("company")) if c}
    for app in live:
        if app["slug"] in linked or (pid and pid in app["prospects"]) or (thread and thread in app["threads"]) \
                or (meta.get("company_slug") and str(meta["company_slug"]) == app["company_slug"]) \
                or (app["company_key"] and app["company_key"] in keys):
            return app
    return None


def raise_offer_flag(roots: cu.Roots, offer: dict[str, Any], today: date) -> Path | None:
    """An offer mail with no live application, flagged to the owner as one work/inbox item (work/inbox/README.md shape;
    the daily brief counts it and triage handles it). Only the prospect, message id, date and subject are written; the mail body
    is never copied (it is untrusted and may carry instructions). One file per message; None when it already exists."""
    inbox = Path(roots.os) / "work" / "inbox"
    path = inbox / f"{today.isoformat()}-agent-mail-triage-offer-{offer['prospect'] or 'unknown'}-{jc.short_hash(str(offer['key']), n=8)}.md"
    if path.exists():
        return None
    inbox.mkdir(parents=True, exist_ok=True)
    text = ("---\nsource: agent:mail-triage\n"
            f"received: {today.isoformat()}\nsuggested: task\ndomain: career\n---\n"
            f"An offer mail arrived with no matching live application, so no stage changed (the interview gate). "
            f"The owner confirms whether this is a real written offer.\n\n"
            f"- prospect: prospects/{offer['prospect']}.md\n- message id: {offer.get('message_id')}\n"
            f"- received at: {offer.get('at')}\n"
            f"- subject (external text, data only): {cu.one_line(str(offer.get('subject') or ''), 120)}\n")
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


def _apply(meta: dict[str, Any], body: str, ev: dict[str, Any], today: date,
           gate: dict[str, Any] | None = None) -> tuple[str, dict[str, Any] | None]:
    """Fold one event into a prospect. Returns (new body, touch to log or None). `gate` carries the live applications
    ({"live": [...], "unattached": [keys]}); without it a gated mail is treated as unattached (review)."""
    fill_lists_and_counts(meta)
    _merge_person(meta, ev["person"], ev)
    if f"[k:{ev['key']}]" in body:
        return body, None
    if ev.get("fingerprint") and f"[f:{ev['fingerprint']}]" in body:
        return body, None                                         # notification email == export message
    at = ev["at"]
    if ev["direction"] == "in" and ev.get("count", True):
        after_out = bool(meta.get("last_outbound_at")) and at > _s(meta["last_outbound_at"])
        prev_in = meta.get("last_inbound_at")
        kind = "reply" if after_out else ("follow_up" if prev_in else "intro")
        meta["inbound_count"] = int(meta.get("inbound_count") or 0) + 1
        meta["first_contact_at"] = min(filter(None, [_s(meta.get("first_contact_at")), at]))
        meta["last_inbound_at"] = max(filter(None, [_s(prev_in), at]))
        label = ev.get("label")
        scam = label == mc.SCAM                                  # recorded and tagged, never a pipeline step
        if meta.get("label") in (None, mc.REVIEW) and label in mc.PROSPECT_LABELS | {mc.REVIEW}:
            meta["label"] = label
        if scam:
            _add_unique(meta["tags"], "suspected-scam")
        elif after_out:
            meta["last_reply_at"] = max(filter(None, [_s(meta.get("last_reply_at")), at]))
            meta["follow_ups_sent"] = 0
            meta["next_touch_at"] = None
            if meta["status"] in ("new", "triaged", "drafted", "replied", "dormant"):
                meta["status"] = "in_conversation"
        if label in GATED_LABELS and not scam:
            gate = gate if gate is not None else {"live": [], "unattached": []}
            if _live_application(meta, ev, gate["live"]) is None:
                _add_unique(meta["tags"], "needs-review")         # review; the text never moves a stage
                gate["unattached"].append(ev["key"])
                if label == "offer":                              # never quietly parked: the offer flag to the owner
                    gate.setdefault("offers", []).append({"key": ev["key"], "prospect": str(meta.get("id") or ""),
                                                          "message_id": ev.get("message_id"), "subject": ev.get("subject"),
                                                          "at": ev.get("at")})
            else:
                if label == "offer":
                    _add_unique(meta["tags"], "offer")
                if meta["status"] not in ("closed", "do_not_contact"):
                    meta["status"] = "interviewing"
        elif label == "rejection":
            _add_unique(meta["tags"], "rejected")
            if meta["status"] not in ("do_not_contact",):
                meta["status"] = "dormant"
        if not scam:                                              # no hints from it, no reply target for a draft
            _update_hints(meta, ev)
        if scam:
            pass
        elif ev["channel"] == "email":
            to = cu.parse_address(ev.get("reply_to"))[1] or (ev["person"].get("emails") or [None])[0]
            meta["reply_context"] = {"channel": "email", "to": to, "thread_id": ev.get("thread_id"),
                                     "message_id": ev.get("message_id"), "references": ev.get("references") or None,
                                     "subject": ev.get("subject"), "at": at}
        elif ev["channel"] in ("linkedin-message", "linkedin-connect"):
            meta["reply_context"] = {"channel": "linkedin-message", "to": meta.get("linkedin_url") or meta["name"],
                                     "thread_id": ev.get("thread_id") if ev["source"] == "linkedin-export" else None,
                                     "subject": ev.get("subject"), "at": at}
        if ev.get("text") or ev.get("summary"):
            kind_tag = "email" if ev["channel"] == "email" else "linkedin"
            ref = ev.get("message_id") or ev["key"]
            where = f" · full text: {ev['source_file']} (local only, not in git)" if ev.get("source_file") else ""
            pointer = f"- {at} · {ev['channel']} · {ref}{where}"
            summary = cu.one_line(ev.get("summary") or ev.get("text"), SUMMARY_CAP)
            body = cu.replace_section(body, LAST_INBOUND, pointer + "\n" + jc.wrap_untrusted(summary, kind_tag, limit=SUMMARY_CAP))
        status = "replied" if after_out else "delivered"
    elif ev["direction"] == "in":                                   # introduced or added: record, do not count
        kind, status = ev.get("kind") or "intro", None
        if kind != "added":
            meta["first_contact_at"] = min(filter(None, [_s(meta.get("first_contact_at")), at]))
        if meta.get("label") in (None, mc.REVIEW) and ev.get("label"):
            meta["label"] = ev.get("label")
        for key, value in (ev.get("extra") or {}).items():
            if key == "role":
                if meta.get("role") in (None, "unknown", "other"):
                    meta["role"] = value
            elif not meta.get(key):
                meta[key] = value
    else:
        awaiting = meta.get("last_outbound_at") and (not meta.get("last_inbound_at") or _s(meta["last_outbound_at"]) >= _s(meta["last_inbound_at"]))
        kind = ev.get("kind") or ("follow_up" if awaiting else "reply")
        meta["outbound_count"] = int(meta.get("outbound_count") or 0) + 1
        meta["last_outbound_at"] = max(filter(None, [_s(meta.get("last_outbound_at")), at]))
        if kind == "follow_up":
            meta["follow_ups_sent"] = int(meta.get("follow_ups_sent") or 0) + 1
        if meta["status"] in ("new", "triaged", "drafted", "dormant"):
            meta["status"] = "replied"
        sent_now = int(meta.get("follow_ups_sent") or 0)
        if meta["status"] == "replied" and sent_now < follow_up_cap(meta):
            meta["next_touch_at"] = jc.add_business_days(cu.local_date(at), follow_up_gap(sent_now))
        else:
            meta["next_touch_at"] = None
        status = "sent"
    body = jc.append_section_line(body, "Timeline", _timeline_line(ev, kind))
    meta["updated"] = today
    if status is None:
        return body, None
    touch_channel = "email" if ev["channel"] == "email" else ev["channel"]
    touch = {"at": at, "prospect": meta["id"], "channel": touch_channel, "direction": ev["direction"], "kind": kind,
             "status": status, "thread_id": ev.get("thread_id"), "summary": cu.one_line(ev.get("summary"), 160),
             "key": ev["key"], "source": ev["source"]}
    if ev.get("portal"):
        touch["portal"] = ev["portal"]              #: the portal the message came through
    return body, touch


def _update_hints(meta: dict[str, Any], ev: dict[str, Any]) -> None:
    text = ev.get("text") or ""
    h = hints(text, ev.get("subject") or "")
    for key in ("role_hint", "comp_hint", "remote_hint", "location_hint"):
        if h.get(key) and not meta.get(key):
            meta[key] = h[key]
    if h.get("contract"):
        _add_unique(meta["tags"], "contract")
    hint, evidence = sponsorship_hint(text)
    rank = {"no": 3, "yes": 2, "cap_exempt": 1, "unknown": 0}
    if "sponsorship_hint" not in (meta.get("locked_fields") or []) and rank[hint] > rank.get(meta.get("sponsorship_hint") or "unknown", 0):
        meta["sponsorship_hint"], meta["sponsorship_evidence"] = hint, evidence
    meta["personalization"] = max(float(meta.get("personalization") or 0.0), personalization(text, ev.get("bulk", False)))
    if meta.get("role") in (None, "unknown", "other") or ev.get("identity_role") in ("founder", "hiring-manager", "recruiter"):
        role = infer_role(ev.get("identity_role"), meta.get("title"), ev.get("label") or meta.get("label"))
        if role not in ("unknown",) and not (meta.get("role") not in (None, "unknown", "other") and role == "other"):
            meta["role"] = role
    domain = (ev["person"].get("domain") or "")
    ctype = infer_company_type(meta.get("company"), domain, text, meta.get("role"))
    if ctype != "unknown" and (meta.get("company_type") in (None, "unknown") or ctype in ("recruiting-firm", "ai-lab", "bigtech", "university")):
        meta["company_type"] = ctype
    meta["seniority"] = infer_seniority(meta.get("role") or "unknown", meta.get("title"))


# --------------------------------------------------------------------------- ingest
def ingest_events(roots: cu.Roots, events: list[dict[str, Any]], today: date | None = None) -> dict[str, Any]:
    today = today or jc.today()
    index = Index()
    records: dict[str, tuple[dict[str, Any], str]] = {}
    bad_records: list[dict[str, str]] = []
    for path in sorted(roots.prospects.glob("*.md")) if roots.prospects.is_dir() else []:
        if path.name.startswith(("_", ".")) or path.name == "README.md":
            continue
        try:
            meta, body = jc.read_record(path)
        except (jc.fm.FrontmatterError, yaml.YAMLError, UnicodeDecodeError) as exc:
            bad_records.append({"file": path.name, "error": cu.one_line(str(exc), 200)})
            print(f"warning: prospects/{path.name} is unreadable, left as is: {cu.one_line(str(exc), 200)}", file=sys.stderr)
            continue
        slug = str(meta.get("id") or path.stem)
        records[slug] = (with_defaults(meta, slug, today), body)
        index.add(slug, records[slug][0])
    report = {"events": len(events), "new_events": 0, "created": 0, "updated": 0, "skipped": 0,
              "outbound_unmatched": 0, "possible_duplicates": 0, "review_created": 0, "interview_unattached": 0,
              "bad_records": bad_records, "errors": []}
    gate = {"live": live_applications(roots, today), "unattached": []}
    touches: list[dict[str, Any]] = []
    changed: set[str] = set()
    for ev in sorted(events, key=lambda e: (e.get("at") or "", e.get("key") or "")):
        if not ev.get("at") or not ev.get("key"):
            report["skipped"] += 1
            continue
        undo: dict[str, tuple[dict[str, Any], str] | None] = {}
        counts = {k: v for k, v in report.items() if isinstance(v, int)}
        n_touches, changed_before = len(touches), set(changed)
        n_unattached, n_offers = len(gate["unattached"]), len(gate.get("offers") or [])
        try:
            _ingest_one(roots, index, records, report, touches, changed, ev, today, undo, gate)
        except Exception as exc:  # one bad event or record never sinks the batch
            for slug, prior in undo.items():
                if prior is None:
                    records.pop(slug, None)
                else:
                    records[slug] = prior
            report.update(counts)
            del touches[n_touches:]
            del gate["unattached"][n_unattached:]
            del gate.setdefault("offers", [])[n_offers:]
            changed.intersection_update(changed_before)
            index = _rebuild_index(records)
            report["errors"].append({"key": ev.get("key"), "error": f"{type(exc).__name__}: {exc}"})
            print(f"warning: event {ev.get('key')} not applied: {type(exc).__name__}: {exc}", file=sys.stderr)
    report["interview_unattached"] = len(gate["unattached"])
    report["offer_flags"] = [str(p_.relative_to(roots.os)) for p_ in (raise_offer_flag(roots, o, today)
                                                                    for o in gate.get("offers") or []) if p_]
    changed |= _unify_company_names(records)
    companies = CompanyIndex.load(roots.js)       # the dossier, then the watchlist, then derived (REQ-9)
    for slug in changed:
        meta, body = records[slug]
        if meta.get("company"):
            meta["company_slug"], meta["company_slug_source"] = companies.resolve(meta["company"], meta.get("emails"),
                                                                                  fuzzy=True)
        meta.update(score_prospect(meta, today, js_root=roots.js))
        cu.write_record(roots.prospects / f"{slug}.md", meta, body)
    for touch in touches:
        jc.log_touch(touch, root=roots.js)
    report["updated"] = len(changed) - report["created"]
    report["prospects"] = len(records)
    return report


def with_defaults(meta: dict[str, Any], slug: str, today: date) -> dict[str, Any]:
    """Give a record written by hand (or by an older script) the full field set of new_meta, the shape
    schemas/prospect.yaml describes. Present values win; only missing or null lists are filled."""
    source = meta.get("source") if meta.get("source") in SCHEMA_ENUMS["source"] else "gmail-backlog"
    base = new_meta(slug, str(meta.get("name") or slug), source, today)
    if not meta.get("sources"):
        base["sources"] = [meta["source"]] if meta.get("source") else []
    for key, default in base.items():
        value = meta.get(key)
        if key not in meta or (value is None and isinstance(default, list)):
            meta[key] = default
        elif isinstance(default, list) and not isinstance(value, list):
            meta[key] = [value]
    return meta


def _rebuild_index(records: dict[str, tuple[dict[str, Any], str]]) -> "Index":
    index = Index()
    for slug, (meta, _) in records.items():
        index.add(slug, meta)
    return index


def _ingest_one(roots: cu.Roots, index: "Index", records: dict[str, tuple[dict[str, Any], str]], report: dict[str, Any],
                touches: list[dict[str, Any]], changed: set[str], ev: dict[str, Any], today: date,
                undo: dict[str, tuple[dict[str, Any], str] | None], gate: dict[str, Any] | None = None) -> None:
    """Fold one event in. Before touching a record, its prior state goes to `undo` (None = created here)."""
    slug, possible = index.match(ev["person"])
    if slug is None:
        if ev["direction"] == "out":
            report["outbound_unmatched"] += 1
            return
        if not ev.get("create"):
            report["skipped"] += 1
            return
        name = cu.display_name(ev["person"].get("name")) or (ev["person"].get("emails") or ["unknown"])[0].split("@")[0]
        slug = _unique_slug(roots, index, name, ev["person"].get("company"), "unknown")
        meta = new_meta(slug, name, ev["source"], today)
        body = f"# {name}\n\n## Notes\n\n## Timeline\n"
        if possible:
            meta["possible_duplicates"] = sorted(possible)
            _add_unique(meta["tags"], "possible-duplicate")
            for other in possible:
                undo.setdefault(other, copy.deepcopy(records[other]))
                ometa, obody = records[other]
                _add_unique(ometa.setdefault("possible_duplicates", []), slug)
                _add_unique(ometa.setdefault("tags", []), "possible-duplicate")
                changed.add(other)
            report["possible_duplicates"] += 1
        if ev.get("label") == mc.REVIEW:
            report["review_created"] += 1
        undo[slug] = None
        records[slug] = (meta, body)
        report["created"] += 1
    undo.setdefault(slug, copy.deepcopy(records[slug]))
    meta, body = records[slug]
    before = body
    body, touch = _apply(meta, body, ev, today, gate)
    records[slug] = (meta, body)
    index.add(slug, meta)
    if body != before:
        report["new_events"] += 1
        changed.add(slug)
    if touch:
        touches.append(touch)


def _unify_company_names(records: dict[str, tuple[dict[str, Any], str]]) -> set[str]:
    """Give "Heliosystems" (from an email domain) the display name another record already has
    ("Helio Systems"), so one company reads the same everywhere."""
    display: dict[str, str] = {}
    for meta, _ in records.values():
        name = meta.get("company")
        if name and " " in name:
            for key in cu.company_keys(name):
                display.setdefault(key, name)
    changed = set()
    for slug, (meta, _) in records.items():
        name = meta.get("company")
        if name and " " not in name:
            better = next((display[k] for k in sorted(cu.company_keys(name)) if k in display), None)
            if better and better != name:
                meta["company"], meta["company_slug"] = better, derive_slug(better)
                if meta.get("title"):
                    meta["headline"] = f"{meta['title']} at {better}"
                changed.add(slug)
    return changed


def ingest(roots: cu.Roots, gmail_files: Iterable[Path] = (), linkedin_files: Iterable[Path] = (),
           today: date | None = None, account_emails: Iterable[str] | None = None,
           outbound_files: Iterable[Path] = ()) -> dict[str, Any]:
    events: list[dict[str, Any]] = []

    def rel(path: Path) -> str:
        try:
            return str(Path(path).resolve().relative_to(roots.js.resolve()))
        except ValueError:
            return str(path)

    for path in gmail_files:
        for ev in gmail_events(jc.read_jsonl(Path(path)), account_emails):
            events.append({**ev, "source_file": rel(path)})
    for path in linkedin_files:
        try:
            li = linkedin_events(read_linkedin(Path(path)))
        except IngestError:
            raise IngestError(unclassified_hint(path)) from None
        for ev in li:
            events.append({**ev, "source_file": rel(path)})
    for path in outbound_files:
        for ev in outbound_events(jc.read_jsonl(Path(path)), now=jc.run_stamp(today)):
            events.append({**ev, "source_file": rel(path)})
    return ingest_events(roots, events, today)


def read_linkedin(path: Path) -> dict[str, Any]:
    """Parsed export: linkedin_export's JSONL (typed lines) or the older single JSON document."""
    text = Path(path).read_text(encoding="utf-8")
    if Path(path).suffix == ".json":
        return json.loads(text)
    plural = {"person": "people", "touch": "touches", "conversation": "conversations", "connection": "connections"}
    data: dict[str, Any] = {v: [] for v in plural.values()}
    for row in jc.read_jsonl(Path(path)):
        kind = row.get("type")
        if kind == "meta":
            data.update({k: v for k, v in row.items() if k != "type"})
        elif kind in plural:
            data[plural[kind]].append(row)
    return data


def latest_linkedin(harvest: Path) -> list[Path]:
    """The newest parsed export in state/harvest, as its .classified.jsonl when that exists (a raw file with no
    classified copy is returned as is, and ingest refuses it with the classify hint)."""
    files = list(harvest.glob("linkedin-*.jsonl")) + list(harvest.glob("linkedin-*.json"))
    by_base: dict[str, list[Path]] = {}
    for f in files:
        by_base.setdefault(f.name.replace(".classified.jsonl", ".jsonl"), []).append(f)
    if not by_base:
        return []
    newest = by_base[sorted(by_base)[-1]]
    return [next((f for f in newest if f.name.endswith(".classified.jsonl")), newest[0])]


# --------------------------------------------------------------------------- reconcile
def sender_keys(rows: Iterable[dict], account_emails: Iterable[str] | None = None) -> set[str]:
    """Human senders with a people label: lower-case email, normalized LinkedIn profile URL, or name:<key>."""
    accounts = {a.lower() for a in (account_emails or ())} | set(cu.ACCOUNT_EMAILS)
    keys: set[str] = set()
    for r in rows:
        if r.get("type") not in (None, "touch") or r.get("direction", "in") != "in":
            continue
        label = (r.get("classification") or {}).get("label", r.get("label"))
        if label not in mc.PROSPECT_LABELS:
            continue
        if r.get("type") == "touch":                                   # LinkedIn export message
            url = cu.normalize_linkedin_url(r.get("person_key"))
            name = r.get("person_name")
        else:
            name, email = cu.parse_address(r.get("from_email") or r.get("from"))
            relay = email in mc.LINKEDIN_RELAYS or cu.registrable_domain(cu.email_domain(email)) == "linkedin.com"
            if board_person_relay(email):                              # many people, one address: the person's name
                identity = (r.get("classification") or {}).get("identity") or {}
                company = board_relay_employer(email) or identity.get("company")
                key = cu.normalize_person_name(board_relay_name(name, r.get("body") or r.get("snippet") or "", company))
                if key:
                    keys.add(f"name:{key}")
                continue
            if email and not relay:
                if email not in accounts:
                    keys.add(email)
                continue
            urls = cu.find_linkedin_urls(r.get("body") or r.get("snippet") or "")
            url = urls[0] if urls else ""
        if url:
            keys.add(url)
        elif cu.normalize_person_name(name):
            keys.add(f"name:{cu.normalize_person_name(name)}")
    return keys


def reconcile(roots: cu.Roots, files: Iterable[Path], today: date,
              account_emails: Iterable[str] | None = None) -> dict[str, Any]:
    keys: set[str] = set()
    for path in files:
        keys |= sender_keys(jc.read_jsonl(Path(path)), account_emails)
    known: set[str] = set()
    for path in sorted(roots.prospects.glob("*.md")) if roots.prospects.is_dir() else []:
        try:
            meta, _ = jc.read_record(path)
        except Exception:  # an unreadable record is reported by ingest; here it simply matches nothing
            continue
        known |= {str(e).strip().lower() for e in meta.get("emails") or []}
        if cu.normalize_linkedin_url(meta.get("linkedin_url")):
            known.add(cu.normalize_linkedin_url(meta.get("linkedin_url")))
        if cu.normalize_person_name(meta.get("name")):
            known.add(f"name:{cu.normalize_person_name(meta.get('name'))}")
    out = roots.harvest / f"senders-{today:%Y-%m-%d}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(f"{k}\n" for k in sorted(keys)), encoding="utf-8")
    return {"senders": sorted(keys), "missing": sorted(keys - known), "file": out}


def classify_input(path: Path) -> str:
    """gmail | linkedin | outbound, from the file name, else from the first row."""
    name = Path(path).name
    for prefix in ("gmail", "linkedin", "outbound"):
        if name.startswith(prefix):
            return prefix
    rows = jc.read_jsonl(Path(path))[:1]
    row = rows[0] if rows else {}
    if row.get("type") in ("meta", "person", "touch", "connection", "conversation"):
        return "linkedin"
    if "found_via" in row or ("name" in row and "from" not in row):
        return "outbound"
    return "gmail"


# --------------------------------------------------------------------------- scoring
def _days_since(value: Any, today: date) -> int | None:
    d = cu.local_date(value) if value else None
    return (today - d).days if d else None


def load_targets(roots: cu.Roots | None = None) -> dict[str, list[str]]:
    # The keyword lists that rank a role hint (policy.prospects.fit_keywords.high/mid/low), empty by default; the user's
    # targets/roles.md can add more under fit_keywords_<level>.
    targets = {level: [x.lower() for x in _cfg_list(f"policy.prospects.fit_keywords.{level}")] for level in ("high", "mid", "low")}
    roles_md = (roots.js if roots else jc.JS_ROOT) / "targets" / "roles.md"
    if roles_md.is_file():
        meta, _ = jc.read_record(roles_md)
        for level in ("high", "mid", "low"):
            extra = meta.get(f"fit_keywords_{level}")
            if isinstance(extra, list):
                targets[level] = targets[level] + [str(x).lower() for x in extra]
    return targets


# Sponsorship is a weighed risk, never a gate. Only citizenship, a clearance, or an employer refusing E-Verify, the
# I-983 or OPT fails, read with fit_gates.CORE_HARD (one list). The weight is policy.sponsorship.weight (0 = ignore).
SPONSOR_WEIGHT = float(cfg.get("policy.sponsorship.weight", 0.0) or 0.0)
SOFT_REASON = "sponsorship: no (a weighed risk, never a gate)"
HARD_PREFIX = "work authorization:"


def hard_authorization(evidence: Any) -> str | None:
    """The hard work-authorization requirement in a sponsorship "no" line, per fit_gates.CORE_HARD, or None."""
    import fit_gates as fg
    text = re.sub(r"\bu\.\s?s\.", "us", str(evidence or "").lower())
    for rx in fg.CORE_HARD:
        m = re.search(rx, text)
        if m:
            return m.group(0)
    return None


def _fit(meta: dict[str, Any], targets: dict[str, list[str]]) -> tuple[float, list[str]]:
    reasons = []
    hint = (meta.get("role_hint") or "").lower()
    hard = hard_authorization(meta.get("sponsorship_evidence")) if meta.get("sponsorship_hint") == "no" else None
    if hard:
        return 1.0, [f"{HARD_PREFIX} '{hard}' (a hard gate)"]
    has = lambda words: any(re.search(rf"\b{re.escape(w)}\b", hint) for w in words)  # noqa: E731
    if not hint:
        fit = 3.0
        reasons.append("no named role")
    elif has(targets["low"]):
        fit = 1.5
        reasons.append("role outside targets")
    elif has(targets["high"]):
        fit = 4.5
        reasons.append("top target role")
    elif has(targets["mid"]):
        fit = 3.5
        reasons.append("engineering role")
    else:
        fit = 3.0
    ctype = meta.get("company_type")
    fit += {"ai-lab": 0.5, "bigtech": 0.25, "recruiting-firm": -0.25}.get(ctype, 0.0)
    loc = (meta.get("location_hint") or "").lower()
    if HOME_AREA and loc and loc not in HOME_AREA and meta.get("remote_hint") != "remote":
        fit -= 1.0
        reasons.append(f"location {meta.get('location_hint')}")
    if "contract" in (meta.get("tags") or []):
        fit -= 0.75
        reasons.append("contract")
    if AVOID_TITLE_RE and AVOID_TITLE_RE.search(hint):
        fit -= 1.0
    if meta.get("sponsorship_hint") == "no":
        fit -= SPONSOR_WEIGHT
        reasons.append(SOFT_REASON)
    fit = max(1.0, min(5.0, round(fit * 2) / 2))
    return fit, reasons


MANUAL_MARK_RE = re.compile(r"^(warmth|fit_score|priority|sponsorship_hint) \(set manually")


def manual_fields(meta: dict[str, Any]) -> list[str]:
    """The LOCKABLE fields a person set by hand that hold a value (locked_fields; priority_locked for priority)."""
    locked = set(meta.get("locked_fields") or [])
    if meta.get("priority_locked"):
        locked.add("priority")
    return [k for k in LOCKABLE if k in locked and meta.get(k) is not None]


def manual_mark(meta: dict[str, Any], field: str) -> str:
    """'<field> (set manually <date>: <value>)', the date from locked_on (older locks: 'date not recorded')."""
    on = (meta.get("locked_on") or {}).get(field)
    return f"{field} (set manually{f' {on}' if on else ', date not recorded'}: {meta.get(field)})"


def compose_reasons(meta: dict[str, Any], warmth_lines: list[str], fit_lines: list[str]) -> list[str]:
    """score_reasons in a fixed order (warmth, fit, then priority and sponsorship marks) where every component a
    person set by hand is its manual mark, never the script's number; a manual fit clears the script's fit lines
    and keeps only a hard work-authorization line, a fact."""
    manual = manual_fields(meta)
    out = [manual_mark(meta, "warmth")] if "warmth" in manual else list(warmth_lines)
    if "fit_score" in manual:
        out += [r for r in fit_lines if str(r).startswith(HARD_PREFIX)] + [manual_mark(meta, "fit_score")]
    else:
        out += list(fit_lines)
    return out + [manual_mark(meta, f) for f in ("priority", "sponsorship_hint") if f in manual]


# Outcomes: a person whose path gave a screen or better is never P3. Floor P1,
# applied over any computed or hand-set priority except do_not_contact, from the furthest stage any linked application
# ever reached (tracker.py max_progress: a screen later rejected still counts).
OUTCOME_FLOOR = "P1"
OUTCOME_FROM = "screen"


def best_outcome(app_slugs: Iterable[Any] | None, js_root: Path | None = None) -> tuple[str, str] | None:
    """(stage, application slug) of the furthest stage at or past a screen among these applications, or None."""
    import tracker
    root = Path(js_root or jc.JS_ROOT)
    best: tuple[int, str, str] | None = None
    for slug in app_slugs or []:
        path = root / "applications" / str(slug) / "application.md"
        if not path.is_file():
            continue
        meta, body = jc.read_record(path)
        now = datetime.now(timezone.utc)
        top = tracker.project(tracker.parse_events(body), now, meta.get("ghost_after_days"))["max_progress"]
        rank = tracker.PROGRESS.index(top)
        if rank >= tracker.PROGRESS.index(OUTCOME_FROM) and (best is None or rank > best[0]):
            best = (rank, top, str(slug))
    return (best[1], best[2]) if best else None


def score_prospect(meta: dict[str, Any], today: date, targets: dict[str, list[str]] | None = None,
                   js_root: Path | None = None) -> dict[str, Any]:
    targets = targets or load_targets()
    days = _days_since(meta.get("last_inbound_at"), today)
    recency = 0.0 if days is None else 1.0 if days <= 14 else 0.75 if days <= 45 else 0.5 if days <= 90 else 0.25 if days <= STALE_DAYS else 0.0
    role, ctype = meta.get("role") or "unknown", meta.get("company_type") or "unknown"
    title = (meta.get("title") or "").lower()
    if role in ("founder", "executive", "hiring-manager"):
        seniority = 1.0
    elif role == "recruiter":
        seniority = 0.0 if ctype == "recruiting-firm" else (0.75 if re.search(r"\b(head|lead|director|senior|principal)\b", title) else 0.5)
    elif role == "sourcer":
        seniority = 0.0 if ctype == "recruiting-firm" else 0.25
    elif role in ("engineer", "alumni", "other", "investor"):
        seniority = 0.5
    else:
        seniority = 0.25
    named = 0.5 if meta.get("role_hint") else 0.0
    live = 1.0 if meta.get("status") in ("in_conversation", "interviewing") else 0.0
    pers = float(meta.get("personalization") or 0.0) / 2
    raw = recency + pers + seniority + named + live
    warmth = max(0, min(3, int(raw + 0.5)))
    warmth_line = f"warmth {raw:.2f}: recency {recency}, personal {pers}, sender {seniority}, role {named}, live {live}"
    fit, fit_reasons = _fit(meta, targets)
    stale = days is not None and days > STALE_DAYS
    fresh = days is not None and days <= 120
    if meta.get("status") == "do_not_contact":
        priority = "P3"
    elif any(str(r).startswith(HARD_PREFIX) for r in fit_reasons):
        priority = "P3"                                    # a hard gate; a plain "no sponsorship" only weighs
    elif meta.get("label") == "referral_intro" and fit >= 3 and not stale:
        priority = "P0"
    elif role in ("founder", "hiring-manager", "executive") and fit >= 4 and fresh:
        priority = "P0"
    elif role in ("founder", "hiring-manager", "executive") and fit >= 3:
        priority = "P1"
    elif role in ("recruiter", "sourcer") and ctype != "recruiting-firm" and fit >= 3.5 and not stale:
        priority = "P1"
    elif ctype == "recruiting-firm":
        priority = "P2" if fit >= 4 and not stale else "P3"
    elif stale:
        priority = "P3"
    elif fit >= 3:
        priority = "P2"
    else:
        priority = "P3"
    locked = set(meta.get("locked_fields") or [])
    if meta.get("priority_locked"):
        locked.add("priority")
    out = {"warmth": warmth, "fit_score": fit, "priority": priority,
           "score_reasons": compose_reasons(meta, [warmth_line], fit_reasons)}
    for key in ("warmth", "fit_score", "priority"):
        if key in locked and meta.get(key) is not None:
            out[key] = meta[key]
    outcome = best_outcome(meta.get("applications"), js_root) if meta.get("status") != "do_not_contact" else None
    if outcome:
        stage, slug = outcome
        if PRIORITY_ORDER.get(out["priority"], 9) > PRIORITY_ORDER[OUTCOME_FLOOR]:
            out["priority"] = OUTCOME_FLOOR
        out["score_reasons"] = out["score_reasons"] + [
            f"priority floor {OUTCOME_FLOOR}: {stage} via {slug} (a path that gave a screen is never P3)"]
    return out


def outcomes(roots: cu.Roots, today: date | None = None, write: bool = False) -> dict[str, Any]:
    """Link every application's via_prospect back onto that prospect (applications) and apply the outcome floor.
    Dry run unless write; a record is rewritten only when it changed."""
    import tracker
    today = today or jc.today()
    report: dict[str, Any] = {"linked": [], "raised": [], "missing": [], "write": write}
    by_prospect: dict[str, list[str]] = {}
    for slug, meta, _body in tracker.iter_applications(roots.js) or []:
        via = str(meta.get("via_prospect") or "").strip()
        if not via:
            continue
        if not (roots.prospects / f"{via}.md").is_file():
            report["missing"].append({"application": slug, "via_prospect": via})
            continue
        by_prospect.setdefault(via, []).append(slug)
    targets = load_targets(roots)
    for pid, slugs in sorted(by_prospect.items()):
        path = roots.prospects / f"{pid}.md"
        meta, body = jc.read_record(path)
        before = copy.deepcopy(meta)
        apps = [str(a) for a in meta.get("applications") or []]
        for slug in slugs:
            if slug not in apps:
                apps.append(slug)
                report["linked"].append({"id": pid, "application": slug})
        meta["applications"] = apps
        scored = score_prospect(meta, today, targets, js_root=roots.js)
        floored = any(r.startswith(f"priority floor {OUTCOME_FLOOR}:") for r in scored["score_reasons"])
        if floored and PRIORITY_ORDER.get(meta.get("priority"), 9) > PRIORITY_ORDER[OUTCOME_FLOOR]:
            report["raised"].append({"id": pid, "from": meta.get("priority"), "to": OUTCOME_FLOOR})
            meta["priority"] = OUTCOME_FLOOR                # only ever raised; triage's own priority is kept otherwise
        meta["score_reasons"] = scored["score_reasons"]
        if write and meta != before:
            meta["updated"] = today
            cu.write_record(path, meta, body)
    return report


COMPUTED_FIELDS = ("warmth", "fit_score", "score_reasons")


def score_all(roots: cu.Roots, today: date | None = None, apply_baseline: bool = False,
              statuses: Iterable[str] | None = ("new",)) -> dict[str, Any]:
    """Refresh COMPUTED_FIELDS on records whose status is in `statuses` (None: every record); status and
    priority are left to prospect-triage unless apply_baseline. Unchanged records are not rewritten."""
    today = today or jc.today()
    targets = load_targets(roots)
    wanted = None if statuses is None else sorted(set(statuses))
    counts: dict[str, Any] = {"selected": wanted or "all", "scored": 0, "changed": 0, "triaged": 0, "prospects": []}
    for path in sorted(roots.prospects.glob("*.md")) if roots.prospects.is_dir() else []:
        if path.name.startswith(("_", ".")) or path.name == "README.md":
            continue
        if wanted is not None and str(fast_meta(path).get("status") or "new") not in wanted:
            continue
        meta, body = jc.read_record(path)
        before = dict(meta)
        computed = score_prospect(meta, today, targets, js_root=roots.js)
        meta.update({k: computed[k] for k in COMPUTED_FIELDS})
        if apply_baseline:
            meta["priority"] = computed["priority"]
            if meta.get("status") == "new":
                meta["status"] = "triaged"
                counts["triaged"] += 1
        if meta != before:
            meta["updated"] = today
            cu.write_record(path, meta, body)
            counts["changed"] += 1
        counts["scored"] += 1
        counts["prospects"].append({
            "id": meta.get("id") or path.stem, "status": meta.get("status"), "priority": meta.get("priority"),
            "suggested_priority": computed["priority"], "warmth": meta.get("warmth"), "fit_score": meta.get("fit_score"),
            "days_since_inbound": _days_since(meta.get("last_inbound_at"), today),
            "days_since_outbound": _days_since(meta.get("last_outbound_at"), today)})
    return counts


# --------------------------------------------------------------------------- queries
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def _sort_key(m: dict[str, Any]) -> tuple:
    return (PRIORITY_ORDER.get(m.get("priority"), 9), -int(m.get("warmth") or 0), -float(m.get("fit_score") or 0),
            "" if not m.get("last_inbound_at") else "~" + "".join(chr(0x10FFFF - ord(c)) for c in _s(m["last_inbound_at"])),
            str(m.get("id")))


def select(roots: cu.Roots, batch_size: int = 25, priorities: Iterable[str] = ("P0", "P1"),
           statuses: Iterable[str] = ACTIVE_FOR_REPLY) -> list[dict[str, Any]]:
    priorities, statuses = set(priorities), set(statuses)
    pool = [m for _, m in iter_metas(roots)
            if m.get("priority") in priorities and m.get("status") in statuses
            and "possible-duplicate" not in (m.get("tags") or []) and m.get("status") != "do_not_contact"]
    return sorted(pool, key=_sort_key)[:batch_size]


def due(roots: cu.Roots, today: date | None = None) -> dict[str, list[dict[str, Any]]]:
    today = today or jc.today()
    result: dict[str, list[dict[str, Any]]] = {"due": [], "exhausted": [], "windows": []}
    for _, m in iter_metas(roots):
        window = jc.parse_date(m.get("referral_window_ends"))
        if window and abs((window - today).days) <= WINDOW_WARN_DAYS and m.get("status") not in ("closed", "do_not_contact"):
            result["windows"].append({"id": m.get("id"), "name": m.get("name"), "company": m.get("company"),
                                      "referral_window_ends": window.isoformat(), "days_left": (window - today).days,
                                      "req_ids": list(m.get("req_ids") or [])})
        if m.get("status") != "replied" or not m.get("last_outbound_at"):
            continue
        if m.get("last_inbound_at") and _s(m["last_inbound_at"]) > _s(m["last_outbound_at"]):
            continue
        sent = int(m.get("follow_ups_sent") or 0)
        out_day = cu.local_date(m["last_outbound_at"])
        auto = jc.add_business_days(out_day, follow_up_gap(sent))
        stored = jc.parse_date(m.get("next_touch_at"))
        due_date = stored if stored and stored > jc.add_business_days(out_day, max(LEGACY_AUTO_DAYS, follow_up_gap(sent))) \
            else auto                                    # a later hand-set day is a deferral; the old +5 is not
        if window and window >= cu.local_date(m["last_outbound_at"]):
            due_date = min(due_date, window)             # never due after the referral window closes
        if due_date > today:
            continue
        row = {"id": m.get("id"), "name": m.get("name"), "company": m.get("company"), "due_date": due_date.isoformat(),
               "nudge": sent + 1, "channel": (m.get("reply_context") or {}).get("channel") or
               ("email" if m.get("emails") else "linkedin-message"), "last_outbound_at": _s(m.get("last_outbound_at")),
               "days_overdue": (today - due_date).days}
        if window:
            row["referral_window_ends"] = window.isoformat()
        result["exhausted" if sent >= follow_up_cap(m) else "due"].append(row)
    for key in ("due", "exhausted"):
        result[key].sort(key=lambda r: (r["due_date"], r["id"]))
    result["windows"].sort(key=lambda r: (r["referral_window_ends"], r["id"]))
    return result


def _matches(m: dict[str, Any], filters: dict[str, Any]) -> bool:
    def any_of(key: str, value: Any) -> bool:
        wanted = filters.get(key)
        return not wanted or value in wanted
    if not any_of("status", m.get("status")) or not any_of("priority", m.get("priority")):
        return False
    if not any_of("company_type", m.get("company_type")) or not any_of("role", m.get("role")):
        return False
    if not any_of("sponsorship", m.get("sponsorship_hint")) or not any_of("label", m.get("label")):
        return False
    if filters.get("source") and not (set(filters["source"]) & ({m.get("source")} | set(m.get("sources") or []))):
        return False
    if filters.get("tag") and not set(filters["tag"]) <= set(m.get("tags") or []):
        return False
    if filters.get("channel") and not set(filters["channel"]) & set(m.get("channels") or []):
        return False
    if filters.get("company"):
        wanted = filters["company"]
        if not any(cu.companies_match(m.get("company"), w) or (m.get("company_slug") or "") == w for w in wanted):
            return False
    if filters.get("since"):
        d = cu.local_date(m.get("last_inbound_at")) if m.get("last_inbound_at") else None
        if not d or d < filters["since"]:
            return False
    return True


def list_prospects(roots: cu.Roots, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    filters = filters or {}
    return sorted((m for _, m in iter_metas(roots) if _matches(m, filters)), key=_sort_key)


def stats(roots: cu.Roots, today: date | None = None) -> dict[str, Any]:
    today = today or jc.today()
    metas = [m for _, m in iter_metas(roots)]
    def count(key: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for m in metas:
            v = str(m.get(key) or "none")
            out[v] = out.get(v, 0) + 1
        return dict(sorted(out.items()))
    by_source: dict[str, int] = {}
    for m in metas:
        for s in (m.get("sources") or [m.get("source")]):
            by_source[str(s)] = by_source.get(str(s), 0) + 1
    replied = [m for m in metas if int(m.get("outbound_count") or 0) > 0]
    responded = [m for m in replied if m.get("status") in ("in_conversation", "interviewing")]
    return {
        "total": len(metas), "by_status": count("status"), "by_priority": count("priority"), "by_role": count("role"),
        "by_company_type": count("company_type"), "by_label": count("label"), "by_sponsorship": count("sponsorship_hint"),
        "by_source": dict(sorted(by_source.items())),
        "funnel": {"harvested": len(metas), "triaged": sum(1 for m in metas if m.get("status") != "new"),
                   "replied": len(replied), "responded": len(responded),
                   "conversations": sum(1 for m in metas if m.get("status") == "in_conversation"),
                   "interviewing": sum(1 for m in metas if m.get("status") == "interviewing")},
        "possible_duplicates": sum(1 for m in metas if "possible-duplicate" in (m.get("tags") or [])),
        "needs_review": sum(1 for m in metas if "needs-review" in (m.get("tags") or [])),
        "do_not_contact": sum(1 for m in metas if m.get("status") == "do_not_contact"),
        "follow_ups_due": len(due(roots, today)["due"]),
    }


# --------------------------------------------------------------------------- set
INT_FIELDS = {"inbound_count", "outbound_count", "follow_ups_sent", "max_follow_ups"}
LIST_FIELDS = {"emails", "thread_ids", "linkedin_threads", "applications", "tags", "possible_duplicates", "channels", "sources",
               "req_ids"}
TIMESTAMP_FIELDS = {"first_contact_at", "last_inbound_at", "last_outbound_at", "last_reply_at"}
DATE_FIELDS = {"next_touch_at", "referral_window_ends"}
# Optional referral fields: req_ids (the exact requisitions a referrer needs; some employers cap referrals at a few per
# candidate), referral_window_ends (the invite's last day), hiring_post_url (the post that surfaced the
# person), last_reply_at (their last answer after one of ours; stamped on ingest, read by outbox.py M9).
REFERRAL_FIELDS = {"req_ids", "referral_window_ends", "hiring_post_url", "last_reply_at"}
# Optional introduction fields: introduced_by (who made the introduction) and asked_by (who asked the owner to
# talk to this person), each a prospect id. set_fields checks the record exists and is not the record itself.
INTRO_FIELDS = {"introduced_by", "asked_by"}
PROTECTED_FIELDS = {"id", "created", "updated", "locked_fields", "merged_into", "reply_context", "score_reasons",
                    "priority_locked", "locked_on"}
LOCKABLE = ("warmth", "fit_score", "priority", "sponsorship_hint")
FIELD_ALIASES = {"next_touch": "next_touch_at", "fit": "fit_score", "sponsorship": "sponsorship_hint"}


def _schema_fields() -> set[str]:
    schema = __import__("agency_paths").SCHEMAS_DIR / "prospect.yaml"
    names = set(new_meta("x", "x", "outbound", date(2026, 1, 1)))
    if schema.is_file():
        names |= set(jc.fm.parse_yaml(schema.read_text(encoding="utf-8")).get("fields") or [])
    return names | {"title", "label", "location_hint", "sponsorship_evidence", "found_via", "relation", "req_id"} | REFERRAL_FIELDS | INTRO_FIELDS


def validate_field(key: str, value: Any) -> tuple[str, Any]:
    """(canonical key, typed value) for one `key=value`, or ValueError. Schema enums and dates apply."""
    key = FIELD_ALIASES.get(key, key)
    if key in PROTECTED_FIELDS:
        raise ValueError(f"{key} is maintained by the script and cannot be set")
    if key not in _schema_fields():
        raise ValueError(f"unknown prospect field {key!r}")
    raw = value.strip() if isinstance(value, str) else value
    if raw in ("", "none", "null", None):
        if key in ("status", "priority", "warmth", "name", "source"):
            raise ValueError(f"{key} cannot be empty")
        return key, [] if key in LIST_FIELDS else None
    if key == "warmth":
        try:
            n = int(raw)
        except (TypeError, ValueError):
            raise ValueError("warmth must be an integer 0 (cold) to 3 (hot)") from None
        if not 0 <= n <= 3:
            raise ValueError("warmth must be an integer 0 (cold) to 3 (hot)")
        return key, n
    if key == "fit_score":
        try:
            f = float(raw)
        except (TypeError, ValueError):
            raise ValueError("fit_score must be a number 1.0 to 5.0") from None
        if not 1.0 <= f <= 5.0:
            raise ValueError("fit_score must be a number 1.0 to 5.0")
        return key, round(f, 2)
    if key in INT_FIELDS:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a non-negative integer") from None
        if n < 0:
            raise ValueError(f"{key} must be a non-negative integer")
        return key, n
    if key in SCHEMA_ENUMS:
        if str(raw) not in SCHEMA_ENUMS[key]:
            raise ValueError(f"{key} {raw!r} not in {list(SCHEMA_ENUMS[key])}")
        return key, str(raw)
    if key == "label":
        if str(raw) not in mc.LABELS:
            raise ValueError(f"label {raw!r} not in {list(mc.LABELS)}")
        return key, str(raw)
    if key in DATE_FIELDS:
        d = jc.parse_date(raw) if re.match(r"^\d{4}-\d{2}-\d{2}", str(raw)) else None
        if not d:
            raise ValueError(f"{key} must be a date YYYY-MM-DD")
        return key, d
    if key == "hiring_post_url":
        if not re.fullmatch(r"https?://\S+", str(raw)):
            raise ValueError("hiring_post_url must be an http(s) URL")
        return key, str(raw)
    if key in INTRO_FIELDS:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", str(raw)):
            raise ValueError(f"{key} must be a prospect id (a kebab-case slug), got {raw!r}")
        return key, str(raw)
    if key in TIMESTAMP_FIELDS:
        ts = cu.to_utc_iso(raw) if re.match(r"^\d{4}-\d{2}-\d{2}", str(raw)) else None
        if not ts:
            raise ValueError(f"{key} must be an ISO date or timestamp")
        return key, ts
    if key in LIST_FIELDS:
        items = raw if isinstance(raw, list) else [x.strip() for x in str(raw).split(",") if x.strip()]
        return key, items
    if key == "x_handle":
        m = re.fullmatch(r"(?:https?://(?:www\.)?(?:x|twitter)\.com/)?@?([A-Za-z0-9_]{1,15})/?", str(raw).strip())
        if not m:
            raise ValueError("x_handle must be an X handle (letters, digits, underscore; up to 15), with or without @")
        return key, m.group(1).lower()
    if key == "linkedin_url":
        url = cu.normalize_linkedin_url(raw)
        if not url:
            raise ValueError("linkedin_url must be a linkedin.com/in/ profile URL")
        return key, url
    return key, cu.one_line(str(raw), 300)


def parse_assignments(pairs: Iterable[str]) -> tuple[dict[str, Any], dict[str, list[str]], dict[str, list[str]], str | None]:
    """`key=value`, `key+=value`, `key-=value` and `merge_into=<slug>` -> (fields, adds, removes, merge_into)."""
    fields: dict[str, Any] = {}
    adds: dict[str, list[str]] = {}
    removes: dict[str, list[str]] = {}
    merge_into = None
    for pair in pairs:
        m = re.match(r"^([A-Za-z_]+)(\+=|-=|=)(.*)$", pair, re.S)
        if not m:
            raise ValueError(f"expected key=value, got {pair!r}")
        key, op, value = m.group(1), m.group(2), m.group(3)
        if key == "merge_into":
            merge_into = value.strip()
            continue
        key = FIELD_ALIASES.get(key, key)
        if op == "=":
            k, v = validate_field(key, value)
            fields[k] = v
            continue
        if key not in LIST_FIELDS:
            raise ValueError(f"{op} works only on list fields {sorted(LIST_FIELDS)}")
        target = adds if op == "+=" else removes
        target.setdefault(key, []).extend(x.strip() for x in value.split(",") if x.strip())
    return fields, adds, removes, merge_into


def _reasons_after_manual_set(roots: cu.Roots, meta: dict[str, Any], hint_changed: bool) -> list[str]:
    """score_reasons after a manual set: the components set by hand become their marks; the others keep the lines
    they were scored with, except that a changed sponsorship_hint re-derives an unlocked fit."""
    warmth_lines: list[str] = []
    fit_lines: list[str] = []
    for line in (str(r) for r in meta.get("score_reasons") or []):
        if not MANUAL_MARK_RE.match(line):
            (warmth_lines if line.startswith("warmth ") else fit_lines).append(line)
    if hint_changed:
        fit, fit_lines = _fit(meta, load_targets(roots))
        if "fit_score" not in manual_fields(meta):
            meta["fit_score"] = fit
    return compose_reasons(meta, warmth_lines, fit_lines)


def set_fields(roots: cu.Roots, slug: str, *, fields: dict[str, Any] | None = None, status: str | None = None,
               priority: str | None = None, label: str | None = None, add_tags: Iterable[str] = (),
               remove_tags: Iterable[str] = (), note: str | None = None, dnc: str | None = None,
               application: str | None = None, timeline: str | None = None,
               list_add: dict[str, list[str]] | None = None, list_remove: dict[str, list[str]] | None = None,
               today: date | None = None) -> dict[str, Any]:
    """The single writer for manual prospect changes. Validates every field; never lifts do_not_contact."""
    today = today or jc.today()
    path = roots.prospects / f"{slug}.md"
    if not path.is_file():
        raise FileNotFoundError(f"no prospect {slug}")
    clean: dict[str, Any] = {}
    for key, value in (fields or {}).items():
        k, v = validate_field(key, value)
        clean[k] = v
    for key, value in (("status", status), ("priority", priority), ("label", label)):
        if value:
            k, v = validate_field(key, value)
            clean[k] = v
    for key in INTRO_FIELDS & set(clean):
        if clean[key] == slug:
            raise ValueError(f"{key} must name another person; {slug!r} is its own id")
        if clean[key] and not (roots.prospects / f"{clean[key]}.md").is_file():
            raise ValueError(f"{key} {clean[key]!r} is not a prospect id (no prospects/{clean[key]}.md)")
    # tags are the controlled vocabulary (schemas/tags.yaml): an unknown one refuses the whole change
    jc.check_tags([*add_tags, *(list_add or {}).get("tags", []), *(clean.get("tags") or [])], roots.os)
    if dnc:
        clean["status"], clean["do_not_contact_reason"] = "do_not_contact", cu.one_line(dnc, 300)
    meta, body = cu.read_record(path)
    old_status = meta.get("status")
    new_status = clean.get("status", old_status)
    if old_status == "do_not_contact" and new_status != "do_not_contact":
        raise ValueError("this prospect is do_not_contact; only the owner lifts that, by editing the record by hand")
    if new_status == "do_not_contact" and not (clean.get("do_not_contact_reason") or meta.get("do_not_contact_reason")):
        raise ValueError("do_not_contact needs do_not_contact_reason=<evidence, dated>")
    now = jc.run_stamp(today)
    if new_status != old_status:
        body = jc.append_section_line(body, "Timeline", f"- {now} · note · status {old_status} → {new_status} (manual)")
        if new_status in ("do_not_contact", "closed", "dormant"):
            meta["next_touch_at"] = None
    locked = list(meta.get("locked_fields") or [])
    old_hint = meta.get("sponsorship_hint")
    for key, value in clean.items():
        meta[key] = value
        if key in LOCKABLE and key not in locked:
            locked.append(key)
    if locked:
        meta["locked_fields"] = locked
    if "priority" in clean:
        meta["priority_locked"] = True
    manual_now = [k for k in LOCKABLE if k in clean]
    if manual_now:
        meta["locked_on"] = {**(meta.get("locked_on") or {}), **{k: today for k in manual_now}}
        meta["score_reasons"] = _reasons_after_manual_set(
            roots, meta, "sponsorship_hint" in clean and clean["sponsorship_hint"] != old_hint)
    if "label" in clean:
        meta["tags"] = [t for t in meta.get("tags") or [] if t != "needs-review"]
    adds = {k: list(v) for k, v in (list_add or {}).items()}
    removes = {k: list(v) for k, v in (list_remove or {}).items()}
    adds.setdefault("tags", []).extend(add_tags)
    removes.setdefault("tags", []).extend(remove_tags)
    if application:
        adds.setdefault("applications", []).append(application)
    for key in set(adds) | set(removes):
        items = list(meta.get(key) or [])
        for v in adds.get(key, []):
            v = v.lower() if key == "emails" else v
            _add_unique(items, v)
        drop = set(removes.get(key, []))
        meta[key] = [v for v in items if v not in drop]
    if timeline:
        line = cu.one_line(timeline, 400)
        if not re.match(r"^\d{4}-\d{2}-\d{2}", line):
            line = f"{today.isoformat()} {line}"
        body = jc.append_section_line(body, "Timeline", f"- {line}")
    if note:
        body = jc.append_section_line(body, "Notes", f"- {today.isoformat()} {cu.one_line(note, 500)}")
    meta["updated"] = today
    cu.write_record(path, meta, body)
    return meta


STATUS_RANK = {s: i for i, s in enumerate(("new", "triaged", "drafted", "replied", "dormant", "in_conversation",
                                           "interviewing", "closed"))}


def merge_prospects(roots: cu.Roots, duplicate: str, canonical: str, today: date | None = None) -> dict[str, Any]:
    """Fold `duplicate` into `canonical` (a person recorded twice). The duplicate stays on disk,
    closed and pointing at the canonical record; timelines are appended, never rewritten."""
    today = today or jc.today()
    if duplicate == canonical:
        raise ValueError("merge_into must name a different prospect")
    dpath, cpath = roots.prospects / f"{duplicate}.md", roots.prospects / f"{canonical}.md"
    for p in (dpath, cpath):
        if not p.is_file():
            raise FileNotFoundError(f"no prospect {p.stem}")
    dmeta, dbody = cu.read_record(dpath)
    cmeta, cbody = cu.read_record(cpath)
    if cmeta.get("merged_into") or dmeta.get("merged_into"):
        raise ValueError("one of these records was already merged; merge into the surviving record")
    d_url, c_url = cu.normalize_linkedin_url(dmeta.get("linkedin_url")), cu.normalize_linkedin_url(cmeta.get("linkedin_url"))
    if d_url and c_url and d_url != c_url:
        raise ValueError("the two records have different LinkedIn profiles: they are different people")
    for key in ("emails", "thread_ids", "linkedin_threads", "applications", "channels", "sources", "tags"):
        items = list(cmeta.get(key) or [])
        for v in dmeta.get(key) or []:
            _add_unique(items, v)
        cmeta[key] = items
    for key in ("linkedin_url", "company", "company_slug", "title", "headline", "role_hint", "comp_hint", "remote_hint",
                "location_hint", "location", "reply_context"):
        if not cmeta.get(key) and dmeta.get(key):
            cmeta[key] = dmeta[key]
            if key == "company_slug":
                cmeta["company_slug_source"] = dmeta.get("company_slug_source")
    if cmeta.get("role") in (None, "unknown", "other") and dmeta.get("role") not in (None, "unknown", "other"):
        cmeta["role"] = dmeta["role"]
    cmeta["inbound_count"] = int(cmeta.get("inbound_count") or 0) + int(dmeta.get("inbound_count") or 0)
    cmeta["outbound_count"] = int(cmeta.get("outbound_count") or 0) + int(dmeta.get("outbound_count") or 0)
    for key, pick in (("first_contact_at", min), ("last_inbound_at", max), ("last_outbound_at", max)):
        values = [_s(v) for v in (cmeta.get(key), dmeta.get(key)) if v]
        cmeta[key] = pick(values) if values else None
    if "do_not_contact" in (cmeta.get("status"), dmeta.get("status")):
        cmeta["status"] = "do_not_contact"
        cmeta["do_not_contact_reason"] = cmeta.get("do_not_contact_reason") or dmeta.get("do_not_contact_reason")
    else:
        cmeta["status"] = max((cmeta.get("status"), dmeta.get("status")), key=lambda s: STATUS_RANK.get(s, -1))
    rank = {"no": 3, "yes": 2, "cap_exempt": 1, "unknown": 0}
    if rank.get(dmeta.get("sponsorship_hint"), 0) > rank.get(cmeta.get("sponsorship_hint"), 0):
        cmeta["sponsorship_hint"], cmeta["sponsorship_evidence"] = dmeta["sponsorship_hint"], dmeta.get("sponsorship_evidence")
    now = jc.run_stamp(today)
    for line in (cu.section_text(dbody, "Timeline") or "").splitlines():
        if line.startswith("- ") and line not in cbody:
            cbody = jc.append_section_line(cbody, "Timeline", line)
    cbody = jc.append_section_line(cbody, "Timeline", f"- {now} · note · merged {duplicate} into this record (manual)")
    for meta in (cmeta, dmeta):
        meta["possible_duplicates"] = [x for x in meta.get("possible_duplicates") or [] if x not in (duplicate, canonical)]
    for path, meta, body in _others_referencing(roots, duplicate, (duplicate, canonical)):
        meta["possible_duplicates"] = [x for x in meta["possible_duplicates"] if x != duplicate]
        if not meta["possible_duplicates"]:
            meta["tags"] = [t for t in meta.get("tags") or [] if t != "possible-duplicate"]
        cu.write_record(path, meta, body)
    if not cmeta["possible_duplicates"]:
        cmeta["tags"] = [t for t in cmeta.get("tags") or [] if t != "possible-duplicate"]
    dmeta.update({"merged_into": canonical, "possible_duplicates": [],
                  "tags": [t for t in dmeta.get("tags") or [] if t != "possible-duplicate"] + ["merged"]})
    if dmeta.get("status") != "do_not_contact":
        dmeta["status"] = "closed"
    dmeta["next_touch_at"] = None
    dbody = jc.append_section_line(dbody, "Timeline", f"- {now} · note · merged into {canonical} (manual)")
    cmeta["updated"] = dmeta["updated"] = today
    cmeta.update(score_prospect(cmeta, today, js_root=roots.js))
    cu.write_record(cpath, cmeta, cbody)
    cu.write_record(dpath, dmeta, dbody)
    return cmeta


def _others_referencing(roots: cu.Roots, slug: str, skip: tuple[str, ...]) -> list[tuple[Path, dict[str, Any], str]]:
    out = []
    for path in roots.prospects.glob("*.md"):
        if path.stem in skip:
            continue
        meta = fast_meta(path)
        if slug in (meta.get("possible_duplicates") or []):
            full, body = cu.read_record(path)
            out.append((path, full, body))
    return out


# --------------------------------------------------------------------------- CLI
def _csv(value: str | None) -> list[str] | None:
    return [v.strip() for v in value.split(",") if v.strip()] if value else None


LIST_COLUMNS = ["id", "name", "company", "role", "priority", "warmth", "fit", "status", "last_inbound", "next_touch"]


def _row(m: dict[str, Any]) -> dict[str, Any]:
    return {"id": m.get("id"), "name": m.get("name"), "company": m.get("company"), "role": m.get("role"),
            "priority": m.get("priority"), "warmth": m.get("warmth"), "fit": m.get("fit_score"),
            "status": m.get("status"), "last_inbound": _s(m.get("last_inbound_at"))[:10],
            "next_touch": _s(m.get("next_touch_at"))[:10]}


def _emit(rows: list[dict[str, Any]], fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(rows, indent=2, default=_s))
    else:
        print(cu.format_table([_row(r) for r in rows], LIST_COLUMNS))


_VALUE_OPTIONS = {"--status", "--priority", "--label", "--tag", "--add-tag", "--untag", "--remove-tag", "--note",
                  "--timeline", "--dnc", "--application", "--root", "--today"}
_ASSIGNMENT = re.compile(r"^[A-Za-z_]+(\+=|-=|=)")


def _split_assignments(argv: list[str]) -> list[str]:
    """argparse matches the `set` id and its key=value list together, so move every `key=value`
    token (wherever it appears) to just after the id, ahead of the options."""
    if "set" not in argv:
        return argv
    at = argv.index("set")
    head, tokens = argv[: at + 1], argv[at + 1:]
    ident, moved, rest, expect = None, [], [], False
    for token in tokens:
        if expect:
            rest.append(token)
            expect = False
        elif token in _VALUE_OPTIONS:
            rest.append(token)
            expect = True
        elif token.startswith("-"):
            rest.append(token)
        elif ident is None and not _ASSIGNMENT.match(token):
            ident = token
        elif _ASSIGNMENT.match(token):
            moved.append(token)
        else:
            rest.append(token)
    return head + ([ident] if ident else []) + moved + rest


def main(argv: list[str] | None = None) -> int:
    argv = _split_assignments(list(sys.argv[1:] if argv is None else argv))
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", help="job-search root (default: this department)")
    ap.add_argument("--today", help="override today's date (YYYY-MM-DD)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    parents = argparse.ArgumentParser(add_help=False)
    parents.add_argument("--root", default=argparse.SUPPRESS)
    parents.add_argument("--today", default=argparse.SUPPRESS)
    i = sub.add_parser("ingest", parents=[parents])
    i.add_argument("files", nargs="*", help="classified Gmail JSONL, parsed LinkedIn export, or outbound-<date>.jsonl")
    i.add_argument("--gmail", action="append", help="classified harvest JSONL (default: all in state/harvest)")
    i.add_argument("--linkedin", action="append", help="parsed export (default: the latest in state/harvest)")
    sc = sub.add_parser("score", parents=[parents])
    sc.add_argument("--apply-baseline", action="store_true",
                    help="old behavior: also write the computed priority and move new -> triaged")
    which = sc.add_mutually_exclusive_group()
    which.add_argument("--status", help="comma-separated statuses to score (default: new)")
    which.add_argument("--all", action="store_true", help="score every record (a recompute pass)")
    s = sub.add_parser("select", parents=[parents])
    s.add_argument("--batch-size", type=int, default=25)
    s.add_argument("--priority", default="P0,P1")
    s.add_argument("--status", default="new,triaged")
    s.add_argument("--format", choices=("table", "json"), default="table")
    d = sub.add_parser("due", parents=[parents])
    d.add_argument("--format", choices=("table", "json"), default="table")
    li = sub.add_parser("list", parents=[parents])
    for flag in ("status", "priority", "company-type", "role", "source", "tag", "sponsorship", "label", "channel"):
        li.add_argument(f"--{flag}")
    li.add_argument("--company", help="company name or slug (tolerant match)")
    li.add_argument("--possible-duplicates", action="store_true", help="only records tagged possible-duplicate")
    li.add_argument("--since")
    li.add_argument("--limit", type=int)
    li.add_argument("--format", choices=("table", "json"), default="table")
    st = sub.add_parser("set", parents=[parents], help="set <id> key=value ... (schema-validated)")
    st.add_argument("id")
    st.add_argument("assignments", nargs="*", help="key=value, key+=value, key-=value, merge_into=<canonical id>")
    st.add_argument("--status")
    st.add_argument("--priority")
    st.add_argument("--label")
    st.add_argument("--tag", "--add-tag", dest="tag", action="append", default=[],
                    help="add a tag; only tags allowed by schemas/tags.yaml")
    st.add_argument("--untag", "--remove-tag", dest="untag", action="append", default=[])
    st.add_argument("--note")
    st.add_argument("--timeline", help="append one line to the Timeline")
    st.add_argument("--dnc", metavar="REASON")
    st.add_argument("--application")
    sub.add_parser("stats", parents=[parents])
    oc = sub.add_parser("outcomes", parents=[parents], help="link via_prospect back; screen paths are never P3")
    oc.add_argument("--write", action="store_true", help="apply (default: dry run)")
    rc = sub.add_parser("reconcile", parents=[parents], help="check every human sender in classified files has a prospect")
    rc.add_argument("--harvest", nargs="+", required=True, help="classified Gmail and LinkedIn JSONL files")
    args = ap.parse_args(argv)
    roots = cu.Roots(js=Path(args.root) if getattr(args, "root", None) else jc.JS_ROOT)
    today = jc.parse_date(args.today) if getattr(args, "today", None) else jc.today()

    if args.cmd == "ingest":
        harvest = roots.harvest
        gmail, linked, outbound = [Path(p) for p in args.gmail or []], [Path(p) for p in args.linkedin or []], []
        for f in args.files:
            {"gmail": gmail, "linkedin": linked, "outbound": outbound}[classify_input(Path(f))].append(Path(f))
        if not (args.files or args.gmail or args.linkedin):
            gmail = sorted(harvest.glob("gmail-*.classified.jsonl"))
            linked = latest_linkedin(harvest)
            outbound = sorted(harvest.glob("outbound-*.jsonl"))
        try:
            report = ingest(roots, gmail, linked, today, outbound_files=outbound)
        except IngestError as exc:
            print(f"error: {exc}", file=sys.stderr)
            print(json.dumps({"error": str(exc)}, indent=2))
            return 2
        print(json.dumps(report, indent=2))
        return 0
    if args.cmd == "reconcile":
        result = reconcile(roots, [Path(f) for f in args.harvest], today)
        for key in result["missing"]:
            print(f"MISSING {key}")
        emails = sum(1 for k in result["senders"] if "@" in k)
        urls = sum(1 for k in result["senders"] if k.startswith("https://"))
        print(f"reconcile: {len(result['senders'])} senders ({emails} emails, {urls} profile URLs, "
              f"{len(result['senders']) - emails - urls} names), {len(result['missing'])} MISSING; "
              f"list: {result['file']}")
        return 1 if result["missing"] else 0
    if args.cmd == "outcomes":
        rep = outcomes(roots, today, write=args.write)
        for r in rep["linked"]:
            print(f"LINK {r['id']} applications += {r['application']}")
        for r in rep["raised"]:
            print(f"RAISE {r['id']} {r['from']} -> {r['to']}")
        for r in rep["missing"]:
            print(f"MISSING {r['application']}: via_prospect {r['via_prospect']} has no prospect record")
        print(f"linked: {len(rep['linked'])}")
        print(f"raised: {len(rep['raised'])}")
        print(f"missing: {len(rep['missing'])}")
        print("written" if args.write else "dry run: nothing written (--write applies)")
        return 0
    if args.cmd == "score":
        statuses = None if args.all else (_csv(args.status) or ["new"])
        bad = sorted(set(statuses or []) - set(SCHEMA_ENUMS["status"]))
        if bad:
            print(f"error: unknown status {', '.join(bad)} (one of {', '.join(SCHEMA_ENUMS['status'])})", file=sys.stderr)
            return 2
        print(json.dumps(score_all(roots, today, apply_baseline=args.apply_baseline, statuses=statuses),
                         indent=2, default=str))
        return 0
    if args.cmd == "select":
        rows = select(roots, args.batch_size, _csv(args.priority) or ("P0", "P1"), _csv(args.status) or ACTIVE_FOR_REPLY)
        _emit(rows, args.format)
        return 0
    if args.cmd == "due":
        result = due(roots, today)
        if args.format == "json":
            print(json.dumps(result, indent=2))
        else:
            print(cu.format_table(result["due"], ["id", "name", "company", "due_date", "nudge", "channel", "days_overdue"]))
            if result["exhausted"]:
                print("\nNo nudges left (consider `set --status dormant`): " + ", ".join(r["id"] for r in result["exhausted"]))
            if result["windows"]:
                print("\nReferral windows (apply through the invite before it ends; negative = lapsed):")
                print(cu.format_table(result["windows"], ["id", "name", "company", "referral_window_ends", "days_left"]))
        return 0
    if args.cmd == "list":
        tags = (_csv(args.tag) or []) + (["possible-duplicate"] if args.possible_duplicates else [])
        filters = {"status": _csv(args.status), "priority": _csv(args.priority), "company_type": _csv(args.company_type),
                   "role": _csv(args.role), "source": _csv(args.source), "tag": tags or None,
                   "sponsorship": _csv(args.sponsorship), "label": _csv(args.label), "channel": _csv(args.channel),
                   "company": _csv(args.company), "since": jc.parse_date(args.since) if args.since else None}
        rows = list_prospects(roots, filters)
        _emit(rows[: args.limit] if args.limit else rows, args.format)
        return 0
    if args.cmd == "set":
        try:
            fields, adds, removes, merge_into = parse_assignments(args.assignments)
            if merge_into:
                if fields or adds or removes or args.status or args.priority or args.tag or args.untag or args.dnc:
                    raise ValueError("merge_into is a separate operation; run it on its own")
                meta = merge_prospects(roots, args.id, merge_into, today)
            else:
                meta = set_fields(roots, args.id, fields=fields, status=args.status, priority=args.priority,
                                  label=args.label, add_tags=args.tag, remove_tags=args.untag, note=args.note,
                                  dnc=args.dnc, application=args.application, timeline=args.timeline,
                                  list_add=adds, list_remove=removes, today=today)
        except (ValueError, FileNotFoundError) as exc:
            print(json.dumps({"error": str(exc)}, indent=2))
            return 2
        print(json.dumps(_row(meta), indent=2, default=_s))
        return 0
    if args.cmd == "stats":
        print(json.dumps(stats(roots, today), indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
