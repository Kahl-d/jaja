#!/usr/bin/env python3
"""portal_sitting.py - the prep for one portal sitting with the owner present. Writes
state/portal-sittings/<portal-id>.md with five parts:

  1. Link and steps   the registry entry's url, alert_setup and next step (targets/platforms.yaml, or its
                      presence_surfaces row), the rows of the plan file (policy.portal_sitting.plan_file) that name the
                      id in brackets, backticks or parentheses, and the census rows (policy.portal_sitting.census_file)
                      tagged [<id>]
  2. Fields and values every field the portal asks, with its value and source: identity, links and education from
                      profile/facts.yaml (apply_pack's own row builders, so withheld links stay withheld), the work
                      authorization answers from legal/timers.md and the recorded sentence verbatim, salary, the
                      live base PDF (base_label), the LinkedIn master's sections (state/linkedin/LINKEDIN.md) by heading.
                      profile/private.yaml is never opened: its fields read "from private.yaml: <key>" and the owner
                      enters them personally (legal name, relocation, remote preference, contacting the current
                      employer, the EEO answers). field_profile trims it: a setup sitting (connector,
                      API key, OAuth) or a subscription (account_needed false, or "subscribes by email") lists the
                      sign-in email only
  3. Profile text     a slot naming the focus profile the portal's roles map to: the titles of jobs recorded
                      from that portal matched against each profile/lenses/*.md titles_covered, the most matched lens
                      wins, policy.portal_sitting.default_lens when none match. The text is drafted by linkedin-editor
                      or the writer; this script never writes it
  4. Alert settings   the plan file's shared queries paragraph verbatim, and the sender to confirm on the first alert
                      (alert-mail portals), the connector-search skill (connectors), or the reply senders (firms)
  5. After            `python3 scripts/apply_pack.py account ...` to record the account (state/accounts.yaml), so
                      portal_digest drops it from "Setup pending"

Logins, passwords, one-time codes and every Save or Submit are the owner's own: no credential field is ever
listed. Read-only on every record; writes only state/portal-sittings/.

CLI:  python3 scripts/portal_sitting.py [--root R] [--today YYYY-MM-DD] <portal-id>
      python3 scripts/portal_sitting.py [--root R] --all     (every sitting in policy.portal_sitting.order, else the
                                                              registry's order; ids with no registry record are skipped
                                                              and named)
      python3 scripts/portal_sitting.py [--root R] --index   (one status per portal, computed from the records;
                                                              writes the status block of INDEX.md and a sheet for every
                                                              portal that needs a sitting and has none)

Status (--index), first rule that holds wins, never typed by hand:
  no sitting      nothing for the owner to do: an api or feed with setup_priority none, a blocked portal, an account
                  state not-wanted (declined, none exists, or invite-only: the registry's evidence says which), or a
                  presence surface that is not a portal (website, code, research)
  manual only     the owner reads it personally (control manual, or registry status manual), with no setup step at P0
                  or P1; or no account is needed and no alert mail exists (account_needed false, no alert_senders)
  tracker linked  account done, alerts on or arriving, and job_alerts.py maps its alert senders
  alert set       alerts on or arriving (presence alerts, else alerts_on), nothing maps the mail yet
  account exists  the account is done (state/accounts.yaml platform-wide entry, else the registry's presence account)
  to create       anything else: an account, a key, a connector or a registration still to make
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as cfg  # noqa: E402
import apply_pack as ap  # noqa: E402
from js_common import JS_ROOT, os_root_for, cli_date, iter_records, today  # noqa: E402

OUT_DIR = Path("state") / "portal-sittings"
# The order `--all` writes the sittings in (reach first): portal ids from policy.portal_sitting.order; empty means the
# registry's own order.
ORDER = tuple(str(x) for x in (cfg.get("policy.portal_sitting.order", []) or []))
PLAN_FILE = str(cfg.get("policy.portal_sitting.plan_file", "") or "")        # setup rows and the shared alert queries
CENSUS_FILE = str(cfg.get("policy.portal_sitting.census_file", "") or "")    # rows tagged [<portal id>]
SETUP_DOC = str(cfg.get("policy.cockpit.connector_setup_doc", "") or "")     # how to renew connector tokens
# Gmail is not a job portal: its sitting is the re-login of the department's OAuth app
BUILT_IN = {"gmail": {"id": "gmail", "name": "Gmail and Calendar re-login",
                      "url": "https://myaccount.google.com/connections",
                      "alert_setup": "Google re-consent for the department's OAuth app"
                                     + (f": {SETUP_DOC}" if SETUP_DOC else "")
                                     + " (python3 scripts/gmail_auth.py login, in the owner's browser).",
                      "control": "oauth"}}
PRIVATE_FIELDS = (("Legal name, as on your documents", "legal_name"), ("Willing to relocate?", "relocation"),
                  ("Remote, hybrid or onsite?", "remote_preference"),
                  ("May we contact your current employer?", "may_contact_current_employer"))
DEFAULT_LENS = str(cfg.get("policy.portal_sitting.default_lens", "") or "")   # the lens when no recorded job matches
AUTH_RULING = str(cfg.get("policy.portal_sitting.authorization_ruling", "") or "")   # hub/rulings.yaml id: the quote after "||"
SALARY_RULING = str(cfg.get("policy.portal_sitting.salary_ruling", "") or "")        # hub/rulings.yaml id: its "reading"
BASE_DIR = Path("resumes") / "bases" / "general"


def _rows(table: list[tuple[str, str, str]]) -> list[str]:
    out = ["| Field | Value | Source |", "|---|---|---|"]
    for label, value, source in table:
        cell = lambda s: str(s).replace("|", "/").replace("\n", " ")  # noqa: E731
        out.append(f"| {cell(label)} | {cell(value)} | {cell(source)} |")
    return out


def _yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def find_entry(root: Path, pid: str) -> dict[str, Any] | None:
    if pid in BUILT_IN:
        return BUILT_IN[pid]
    data = _yaml(root / "targets" / "platforms.yaml")
    for key in ("platforms", "presence_surfaces"):
        for e in data.get(key) or []:
            if isinstance(e, dict) and e.get("id") == pid:
                return e
    return None


def _is_surface(root: Path, pid: str) -> bool:
    data = _yaml(root / "targets" / "platforms.yaml")
    return any(isinstance(e, dict) and e.get("id") == pid for e in data.get("presence_surfaces") or [])


KIT = OUT_DIR / "_PROFILE-KIT.md"
NO_TEXT = re.compile(r"no profile text needed", re.I)
APPROVAL_ID = re.compile(r"\bA-\d{4}-\d{4}\b")
HAND_OFF = {"linkedin-profile-edit": ".agents/skills/linkedin-assist"}   # the owner pastes and clicks


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def kit_focus(root: Path) -> str | None:
    """The kit's focus profile (its `focus:` line names profile/lenses/<lens>.md), or None without a kit."""
    try:
        m = re.search(r"^focus:\s*(profile/lenses/[\w-]+\.md)", (root / KIT).read_text(encoding="utf-8"), re.M)
    except OSError:
        return None
    return m.group(1) if m else None


def kit_note(root: Path, entry: dict[str, Any]) -> tuple[str, str] | None:
    """(heading, paragraph) of the kit's section 6 note for this portal: a bold heading naming it (by id, or a prefix
    of its name; "A and B" names two; "Recruiter firms" names every recruiter-firm surface), else None."""
    try:
        text = (root / KIT).read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"^## 6\..*?$(.*?)(?=^## )", text, re.M | re.S)
    if not m:
        return None
    pid, name = str(entry.get("id") or ""), _norm(str(entry.get("name") or ""))
    kind = (entry.get("presence") or {}).get("kind") or entry.get("kind")
    for para in re.split(r"\n\s*\n", m.group(1)):
        h = re.match(r"\s*\*\*(.+?)\*\*", para)
        if not h:
            continue
        head = h.group(1).strip().rstrip(".")
        for part in re.split(r"\s+and\s+", head):
            pn, first = _norm(part), part.split()[0].lower()
            if (pn and (pn == _norm(pid) or name.startswith(pn) or first == pid)
                    or (pn == "recruiterfirms" and kind == "recruiter-firm")):
                return head, para.strip()
    return None


def approval_progress(root: Path, aid: str) -> dict[str, Any] | None:
    """The approval's items (work/approvals/<id>.md) against outbox sent_at: applied (with dates) and left."""
    try:
        path = os_root_for(Path(root)) / "work" / "approvals" / f"{aid}.md"   # the runtime root's work/
    except IndexError:
        return None
    try:
        m = re.match(r"^---\n(.*?)\n---", path.read_text(encoding="utf-8"), re.S)
        meta = yaml.safe_load(m.group(1)) if m else {}
    except (OSError, yaml.YAMLError):
        return None
    items = [i for i in (meta or {}).get("items") or [] if isinstance(i, dict) and i.get("id")]
    ids = [str(i["id"]) for i in items]
    channels = sorted({str(i.get("channel")) for i in items if i.get("channel")})
    if not ids:
        return None
    sent, left = [], []
    for oid in ids:
        hit = sorted((Path(root) / "outbox").glob(f"{oid}*.md"))
        try:
            om = re.match(r"^---\n(.*?)\n---", hit[0].read_text(encoding="utf-8"), re.S) if hit else None
            ometa = (yaml.safe_load(om.group(1)) if om else {}) or {}
        except (OSError, yaml.YAMLError):
            ometa = {}
        at = ometa.get("sent_at")
        (sent if at else left).append((oid, str(at)[:10] if at else None))
    return {"id": aid, "total": len(ids), "sent": sent, "left": [o for o, _ in left], "channels": channels,
            "expires": str(meta.get("expires") or "") or None}


def profile_approval(root: Path, entry: dict[str, Any]) -> dict[str, Any] | None:
    linked = ((entry.get("presence") or {}).get("linked") or {}).get("approval")
    m = APPROVAL_ID.search(str(linked or ""))
    return approval_progress(root, m.group(0)) if m else None


def _table_rows(path: Path, pattern: re.Pattern) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [ln for ln in lines if ln.startswith("|") and pattern.search(ln)]


def plan_rows(root: Path, pid: str) -> list[str]:
    tag = re.escape(pid)
    plan = _table_rows(root / PLAN_FILE, re.compile(rf"\(\s*{tag}\s*\)|\[\s*{tag}\s*\]|`{tag}`")) if PLAN_FILE else []
    census = _table_rows(root / CENSUS_FILE, re.compile(rf"\[{tag}\]")) if CENSUS_FILE else []
    out = []
    for name, rows in ((PLAN_FILE, plan), (CENSUS_FILE, census)):
        for row in rows:
            cells = [c.strip().strip("*") for c in row.strip("|").split("|")]
            out.append(f"- From {name}: " + " · ".join(c for c in cells[1:] if c))
    return out


def shared_queries(root: Path) -> str:
    if not PLAN_FILE:
        return ""
    try:
        text = (root / PLAN_FILE).read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r"Shared alert queries.*?(?:\n\s*\n|$)", text, re.S)
    return re.sub(r"\s+", " ", m.group(0)).strip() if m else ""


def _ruling(root: Path, rid: str) -> dict[str, Any]:
    data = _yaml(root / "hub" / "rulings.yaml")
    for r in (data.get("rulings") if isinstance(data, dict) else data) or []:
        if isinstance(r, dict) and r.get("id") == rid:
            return r
    return {}


def authorization_sentence(root: Path) -> str:
    """The work-authorization sentence in the owner's words: the part of the configured ruling's quote after "||"."""
    return str(_ruling(root, AUTH_RULING).get("quote") or "").split("||")[-1].strip() if AUTH_RULING else ""


def salary_answer(root: Path) -> str:
    """The configured ruling's reading without its "Reading (...):" prefix and its "Supersedes" note."""
    reading = str(_ruling(root, SALARY_RULING).get("reading") or "") if SALARY_RULING else ""
    reading = re.sub(r"^Reading(?: \([^)]*\))?:\s*", "", reading)
    return re.sub(r"\s*Supersedes[^.]*\.?\s*$", "", reading).strip()


BASE_STATUS = re.compile(r"\*\*Status: the base\.\*\* Iteration (\d+)")


def base_label(root: Path) -> str:
    """The live base's iteration from its README status line, never a typed number."""
    try:
        m = BASE_STATUS.search((Path(root) / BASE_DIR / "README.md").read_text(encoding="utf-8"))
    except OSError:
        m = None
    return (f"base {m.group(1)} ({BASE_DIR.as_posix()}/README.md status line)" if m
            else f"the base in {BASE_DIR.as_posix()} (iteration not on record)")


SUBSCRIBE = re.compile(r"\bsubscribes? by email\b", re.I)


def field_profile(entry: dict[str, Any]) -> str:
    """Which fields a sitting lists, from the registry entry: setup (a connector, an API key or an OAuth re-login: no
    form, only the sign-in email), subscription (account_needed false, or alert_setup says the owner subscribes by email:
    the email only), else profile (every field)."""
    if str(entry.get("control") or "") in ("connector", "api", "oauth"):
        return "setup"
    if entry.get("account_needed") is False or SUBSCRIBE.search(str(entry.get("alert_setup") or "")):
        return "subscription"
    return "profile"


def sheet_kind(root: Path, entry: dict[str, Any]) -> str:
    """field_profile, except a profile portal the kit's section 6 says needs no profile text is "kit-none"."""
    kind = field_profile(entry)
    note = kit_note(root, entry) if kind == "profile" else None
    return "kit-none" if note and NO_TEXT.search(note[1]) else kind


def tenant_rows(root: Path, vendor: str) -> list[str]:
    """One line per watch-list employer on that ATS (tier dream, strong or good) with its board's site URL."""
    data = _yaml(root / "targets" / "companies.yaml")
    applied: dict[str, str] = {}                   # an applied ATS application proves the employer's account exists
    for path in sorted((root / "applications").glob("*/application.md")):
        try:
            m = re.match(r"^---\n(.*?)\n---", path.read_text(encoding="utf-8"), re.S)
            meta = (yaml.safe_load(m.group(1)) if m else {}) or {}
        except (OSError, yaml.YAMLError):
            continue
        slug, at = str(meta.get("company_slug") or ""), str(meta.get("applied_at") or "")[:10]
        if slug and at and str(meta.get("applied_via") or "") == "ats":
            applied[slug] = min(applied.get(slug, at), at)
    out = []
    for c in data.get("companies") or []:
        if not isinstance(c, dict) or str(c.get("ats_vendor")) != vendor or c.get("tier") not in ("dream", "strong", "good"):
            continue
        parts = str(c.get("board_token") or "").split("/")
        site = f"https://{parts[0]}/{parts[2]}" if len(parts) == 3 else str(c.get("careers_url") or "")
        has = f", account exists: applied {applied[c.get('slug')]}" if c.get("slug") in applied else ""
        out.append(f"  - {c.get('name')} ({c.get('tier')}, --tenant {c.get('slug')}{has}): {site} (Candidate Home: "
                   "Sign In on that site)")
    return out


def field_rows(root: Path, entry: dict[str, Any], kind: str | None = None) -> list[tuple[str, str, str]]:
    facts = _yaml(root / "profile" / "facts.yaml")
    ctx = SimpleNamespace(facts=facts, private={})        # private.yaml is never opened
    rows = [(r.question, r.answer, r.source) for r in ap.contact_rows(ctx, "contact")]
    if (kind or field_profile(entry)) != "profile":      # no phone, salary or EEO for a subscription
        return [r for r in rows if "mail" in r[0].lower()][:1] or rows[:1]
    rows += [(label, f"from private.yaml: {key}", "the owner enters it (profile/private.yaml, never copied here)")
             for label, key in PRIVATE_FIELDS]
    rows += [(r.question, r.answer, r.source) for r in ap.education_rows(ctx, "education")]
    legal = ap.load_legal(root)
    rows += [(r.question, r.answer, r.source) for r in ap.legal_rows(legal, "auth")]
    sentence = authorization_sentence(root)
    if sentence:
        rows.append(("Work authorization, in your words", sentence, f"hub/rulings.yaml {AUTH_RULING}"))
    rows.append(("Desired salary", salary_answer(root) or "from private.yaml: salary_expectation",
                 (f"{SALARY_RULING} " if SALARY_RULING else "") + "(see private.yaml: salary_expectation)"))
    for key, label in ap.EEO_FIELDS:
        rows.append((f"Voluntary self-identification: {label}", f"from private.yaml: eeo.{key}",
                     "the owner set these answers and enters them personally"))
    pdfs = sorted((root / BASE_DIR).glob("*.pdf")) if (root / BASE_DIR).is_dir() else []
    classic = [p for p in pdfs if "inline" not in p.name] or pdfs
    rows.append(("Resume to upload", str(classic[0].relative_to(root)) if classic else ap.ASK,
                 base_label(root)))
    prog = profile_approval(root, entry)                  # the profile is an approval batch
    if prog:
        rows.append(("Profile", approval_line(prog), f"work/approvals/{prog['id']}.md; outbox sent_at"))
    return [r for r in rows if not ap.CREDENTIAL_KEY_RE.search(r[0]) or r[0].startswith("Voluntary")]


def approval_line(prog: dict[str, Any]) -> str:
    days = sorted({d for _, d in prog["sent"] if d})
    n, total, left = len(prog["sent"]), prog["total"], prog["left"]
    if not left:
        return f"{prog['id']}: {n} of {total} items applied (last {days[-1]}); read only in this sitting"
    when = (days[0] if len(days) == 1 else f"{days[0]} to {days[-1]}") if days else "none yet"
    return (f"{prog['id']}: {n} of {total} items applied {when}; {len(left)} left: {', '.join(left)} (applied in "
            f"this sitting, in order)" + (f"; the approval expires {prog['expires']}" if prog.get("expires") else ""))


def _lens_patterns(root: Path) -> dict[str, list[str]]:
    out = {}
    for path in sorted((root / "profile" / "lenses").glob("*.md")):
        m = re.match(r"^---\n(.*?)\n---", path.read_text(encoding="utf-8"), re.S)
        try:
            meta = yaml.safe_load(m.group(1)) if m else {}
        except yaml.YAMLError:
            meta = {}
        meta = meta if isinstance(meta, dict) else {}
        pats = [re.sub(r"\s*\(.*?\)\s*", "", str(p)).strip().lower() for p in meta.get("titles_covered") or []]
        out[path.stem] = [p for p in pats if p]
    return out


def focus_lens(root: Path, pid: str) -> tuple[str, int, int]:
    """(lens, jobs matched to it, jobs from the portal)."""
    titles = [str(meta.get("title") or "") for _, meta, _ in iter_records(root / "jobs")
              if meta.get("platform") == pid or any(isinstance(s, dict) and s.get("portal") == pid
                                                    for s in meta.get("sightings") or [])]
    lenses = _lens_patterns(root)
    counts: Counter[str] = Counter()
    for t in titles:
        tl = t.lower()
        for lens, pats in lenses.items():
            if any(fnmatch.fnmatch(tl, p) for p in pats):
                counts[lens] += 1
    if not counts:
        return DEFAULT_LENS, 0, len(titles)
    lens, n = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    return lens, n, len(titles)


def alert_lines(root: Path, entry: dict[str, Any]) -> list[str]:
    control = entry.get("control")
    if control == "connector":
        return ["- no alert mail: searched in sessions by the connector-search skill (.agents/skills/connector-search)."]
    if control == "oauth":
        return ["- none: this sitting restores the mail channel every alert arrives on."]
    lines = []
    q = shared_queries(root)
    senders = [str(s) for s in entry.get("alert_senders") or []]
    inbox = [str(s) for s in entry.get("inbox_senders") or []]
    if control == "alert-mail":
        if q:
            lines.append(f"- Queries, locations, cadence ({PLAN_FILE}): {q} Cadence: daily, by email.")
        confirm = entry.get("alert_sender_confirmed") is False
        for s in senders:
            lines.append(f"- Sender: {s}" + (" (confirm on the first alert)" if confirm else ""))
        if not senders:
            lines.append("- Sender: confirm on the first alert, then add it to targets/platforms.yaml alert_senders.")
    else:
        lines.append(f"- No saved-search alerts (control {control or 'unknown'}).")
        for s in inbox:
            lines.append(f"- Watch for replies from {s} (portal tag {entry.get('id')}).")
        if entry.get("sender_note"):
            lines.append(f"- Sender: {entry['sender_note']}.")
    if entry.get("fraud_filter"):
        lines.append(f"- Fraud filter: {entry['fraud_filter']}")
    return lines


def render(root: Path, entry: dict[str, Any], day: date) -> str:
    pid, name = str(entry["id"]), str(entry.get("name") or entry["id"])
    url = entry.get("url") or (entry.get("presence") or {}).get("url") or ""
    steps = str(entry.get("alert_setup") or entry.get("owner_once") or "").strip()
    nxt = entry.get("next_step") or (entry.get("presence") or {}).get("next_step")
    note = entry.get("sitting_note") or (entry.get("presence") or {}).get("sitting_note")
    tenants = tenant_rows(root, str(entry["per_tenant"])) if entry.get("per_tenant") else None
    lines = [f"# Portal sitting: {name} ({pid})", ""] + ([f"> {note}", ""] if note else []) + [
             f"Prepared {day.isoformat()} by scripts/portal_sitting.py. The owner is present; every login, code, Save "
             "and Submit is the owner's own click. Nothing here is a credential.", "",
             "## 1. Link and steps", "",
             f"- Link: one Candidate Home per employer (below); Workday's help page: {url}" if tenants is not None
             else f"- Link: {url}"]
    if tenants is not None:
        lines += [f"- Employers on the watch list ({entry['per_tenant']}, tier dream, strong or good; each sign-in is "
                  "made on that employer's site):"] + (tenants or ["  - none on the watch list"])
    if entry.get("data_export"):
        lines.append(f"- Data export: {entry['data_export']}")
    prog = profile_approval(root, entry)
    skills = sorted({HAND_OFF[c] for c in (prog or {}).get("channels") or [] if c in HAND_OFF})
    if skills:
        lines.append(f"- How: the hand-off in {', '.join(skills)}: the agent shows each approved item's text, "
                     "the owner pastes and clicks, the agent reads it back read-only and records it with "
                     "`python3 scripts/outbox.py mark-sent --id <O-id>`. The agent never types, clicks or sends in "
                     "LinkedIn; the alert steps below are the owner's too.")
    if note:                                       # someone else owns this profile: the note and the link only
        return "\n".join(lines + ["", "Sections 2 to 5 do not apply: see the note above."]) + "\n"
    if steps:
        lines += ["- Steps (targets/platforms.yaml alert_setup):"] + [f"  {ln}" for ln in steps.splitlines()]
    if nxt:
        lines.append(f"- Next step on record: {nxt}")
    lines += plan_rows(root, pid)
    lines += ["", "## 2. Fields and values", ""]
    if entry.get("control") == "oauth":
        lines += _rows([("Google account", "the department's Gmail account" + (f" ({SETUP_DOC})" if SETUP_DOC else ""),
                         SETUP_DOC or "-")])
    else:
        lines += _rows(field_rows(root, entry, sheet_kind(root, entry)))
    kind = sheet_kind(root, entry)
    knote = kit_note(root, entry)
    lines += ["", "## 3. Profile text", ""]
    if kind == "kit-none":
        lines += [f"- No profile text: {KIT.as_posix()} section 6, '{knote[0]}' says none is needed."]
    elif kind == "profile" and profile_approval(root, entry):
        pa = profile_approval(root, entry)
        lines += [f"- Text: the approved items of {pa['id']}, never the kit: " +
                  (f"{len(pa['left'])} left ({', '.join(pa['left'])})" if pa["left"] else
                   f"all {pa['total']} applied; read only") + f" (work/approvals/{pa['id']}.md, outbox/)."]
    elif kind == "profile":
        lens, n, total = focus_lens(root, pid)
        focus = kit_focus(root)
        lean = (f"{n} of {total} jobs recorded from this portal match profile/lenses/{lens}.md" if n and lens else
                f"no recorded job from this portal matches a lens ({total} jobs)")
        lines += [f"- Focus profile: the kit's focus ({focus}) wins; {lean}." if focus else
                  f"- Focus profile: " + (f"profile/lenses/{lens}.md; " if lens else "none set; ") + f"{lean}.",
                  f"- Text: {KIT.as_posix()} section 6, '{knote[0]}', then sections 1 to 5" if knote else
                  f"- Text: {KIT.as_posix()} sections 1 to 5 (no section 6 note for this portal)"]
    else:
        lines += [f"- No profile text: this sitting is a {'subscription' if kind == 'subscription' else 'setup step'} "
                  "with no profile to fill."]
    lines += ["", "## 4. Alert settings", ""]
    lines += alert_lines(root, entry)
    lines += ["", "## 5. After the sitting", ""]
    if entry.get("control") == "oauth":
        lines.append("- `python3 scripts/connectors_doctor.py` shows the Gmail and Calendar tokens green.")
    else:
        facts = _yaml(root / "profile" / "facts.yaml")
        emails = ((facts.get("identity") or {}).get("emails") or {})
        email = ap._value(emails.get("job_search_primary")) or "<email used>"
        tenant = " --tenant COMPANY_SLUG" if entry.get("per_tenant") else ""
        company = "COMPANY_NAME" if tenant else name
        import portal_digest
        st, _ = platform_status(entry, portal_digest._accounts(root)[0], surface=_is_surface(root, pid))
        created = "" if st in ("account exists", "tracker linked") else " --created-on $(date +%F)"   # never a false date
        pres = entry if _is_surface(root, pid) else (entry.get("presence") or {})
        unsure = bool(created) and (pres.get("account") or {}).get("state") == "unknown" and not tenant
        prog = profile_approval(root, entry) if kind == "profile" else None
        profile, why = "", ""
        if kind == "profile" and prog and not prog["left"]:
            profile = f" --profile-filled-on {max(d for _, d in prog['sent'] if d)}"
        elif kind == "profile":
            profile = " --profile-filled-on $(date +%F)"
            why = (f" (after the {len(prog['left'])} item{'s' if len(prog['left']) != 1 else ''} left "
                   f"{'are' if len(prog['left']) != 1 else 'is'} applied)" if prog and prog["left"]
                   else " (the profile date once the profile text is saved)")
        lines += [f"- `python3 scripts/apply_pack.py account --platform {pid}{tenant} --company \"{company}\" --exists yes "
                  f"--email {email}{created}{profile}`" + why +
                  (" (only if the account was created in this sitting; else omit --created-on)" if unsure else "") +
                  ("; replace COMPANY_SLUG and COMPANY_NAME with the employer's from the list in section 1, and omit "
                   "--created-on for an employer marked account exists" if tenant else ""),
                  "- Then `python3 scripts/portal_digest.py` no longer lists it under Setup pending."]
    return "\n".join(lines) + "\n"


def write(root: Path, pid: str, day: date) -> Path | None:
    entry = find_entry(root, pid)
    if entry is None:
        return None
    path = root / OUT_DIR / f"{pid}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(render(root, entry, day), encoding="utf-8")
    os.replace(tmp, path)
    return path


STATUSES = ("to create", "account exists", "alert set", "tracker linked", "manual only", "no sitting")
NEEDS_SHEET = ("to create", "account exists", "alert set", "tracker linked")
NOT_PORTAL_KINDS = frozenset({"website", "code", "research"})
BEGIN = "<!-- status:begin (generated by scripts/portal_sitting.py --index; do not edit by hand) -->"
END = "<!-- status:end -->"


def platform_status(entry: dict[str, Any], wide: dict[str, str], surface: bool = False) -> tuple[str, str]:
    """(status, why) for one registry entry; wide is portal_digest._accounts' platform-wide account map."""
    import portal_digest
    import portal_registry
    pid = str(entry.get("id"))
    pres = entry if surface else (entry.get("presence") or {})
    control = str(entry.get("control") or "")
    priority = str(entry.get("setup_priority") or "none")
    kind = pres.get("kind")
    if pid in wide:
        account, acc_src = wide[pid], "state/accounts.yaml"
    else:
        account = (pres.get("account") or {}).get("state") or "unknown"
        acc_src = "registry presence account"
    alerts = (pres.get("alerts") or {}).get("state")
    alert_src = "registry presence alerts"
    if alerts not in portal_digest.ALERTS_DONE and entry.get("alerts_on"):
        alerts, alert_src = "on", f"alerts_on {entry.get('alerts_on')}"
    if surface and kind in NOT_PORTAL_KINDS:
        return "no sitting", f"presence surface ({kind})"
    if str(entry.get("status") or "") == "blocked":
        return "no sitting", "status blocked"
    if account == "not-wanted":     # declined by the owner, none exists, or invite-only: the registry's evidence says which
        return "no sitting", f"no account (registry account state not-wanted, {acc_src})"
    if control == "api" and priority not in portal_digest.SETUP_PRIORITY:
        return "no sitting", f"control api, setup_priority {priority}"
    no_account = entry.get("account_needed") is False
    if no_account and control not in ("connector", "api") and not entry.get("alert_senders"):
        return "manual only", f"no account needed and no alert mail (control {control}, setup_priority {priority})"
    manual = control == "manual" or str(entry.get("status") or "") == "manual"   # the owner reads it personally
    if manual and kind not in portal_registry.FIRM_KINDS and priority not in portal_digest.SETUP_PRIORITY:
        return "manual only", f"control {control}, status {entry.get('status') or 'not set'}, setup_priority {priority}"
    done = account in portal_digest.ACCOUNT_DONE
    if done and alerts in portal_digest.ALERTS_DONE:
        if entry.get("alert_senders") and "job_alerts" in str(entry.get("adapter") or ""):
            return "tracker linked", f"account {account} ({acc_src}); alerts {alerts}; parsed by job_alerts.py"
        return "alert set", f"account {account} ({acc_src}); alerts {alerts} ({alert_src}); no parser mapped"
    if alerts in portal_digest.ALERTS_DONE:
        return "alert set", f"alerts {alerts} ({alert_src}); account {account}"
    if done:
        gap = "the sitting still turns them on" if alerts == "off" else "the sitting sets them"
        return "account exists", f"account {account} ({acc_src}); alerts {alerts or 'not recorded'}: {gap}"
    what = ("connector to add" if control == "connector" else "register" if kind in portal_registry.FIRM_KINDS
            else "API key" if control == "api" else "alert subscription (no account needed)" if no_account
            else "account")
    return "to create", f"{what}; account {account}"


def index_rows(root: Path) -> list[dict[str, Any]]:
    import portal_digest
    root = Path(root)
    data = _yaml(root / "targets" / "platforms.yaml")
    wide, _ = portal_digest._accounts(root)
    sheets = root / OUT_DIR
    out = []
    for key in ("platforms", "presence_surfaces"):
        for e in data.get(key) or []:
            if not isinstance(e, dict) or not e.get("id"):
                continue
            status, why = platform_status(e, wide, surface=key == "presence_surfaces")
            pid = str(e["id"])
            out.append({"id": pid, "name": str(e.get("name") or pid), "status": status, "why": why,
                        "surface": key == "presence_surfaces", "priority": e.get("setup_priority"),
                        "sheet": f"{pid}.md" if (sheets / f"{pid}.md").is_file() else None})
    return out


def status(root: Path) -> dict[str, str]:
    """{portal id: status} for every platform and presence surface: the one import for scripts/sitting_order.py."""
    return {r["id"]: r["status"] for r in index_rows(root)}


def count_line(rows: list[dict[str, Any]]) -> str:
    c = Counter(r["status"] for r in rows)
    return f"{len(rows)} portals: " + ", ".join(f"{c[s]} {s}" for s in STATUSES)


def render_status(rows: list[dict[str, Any]], day: date) -> str:
    lines = [BEGIN, "", f"## Status by portal ({day.isoformat()}, computed from the records)", "",
             "Each status comes from targets/platforms.yaml, state/accounts.yaml and the sheets here, by the rules in "
             "`scripts/portal_sitting.py`; nothing in this block is typed. The order of the sittings is ORDER.md (`python3 scripts/sitting_order.py`).",
             "", count_line(rows) + ".", "",
             "Key: to create means an account, key, connector, registration or alert subscription is still to make; "
             "account exists means the account is there but the alerts are not (the sitting still has work); "
             "alert set means alerts arrive but no script reads them; tracker linked means alerts arrive and "
             "job_alerts.py turns them into job records; manual only means the owner reads the site personally, with no "
             "setup step this week; no sitting means there is nothing for the owner to do. \"exists-inferred\" means the "
             "account was inferred from the owner's mail, not confirmed.", "",
             "| id | portal | status | sheet | from |", "|---|---|---|---|---|"]
    cell = lambda v: str(v).replace("|", "/").replace("\n", " ")  # noqa: E731
    for r in sorted(rows, key=lambda r: (STATUSES.index(r["status"]), r["id"])):
        lines.append(f"| {r['id']} | {cell(r['name'])} | {r['status']} | {r['sheet'] or ''} | {cell(r['why'])} |")
    return "\n".join(lines + ["", END]) + "\n"


def write_index(root: Path, day: date) -> list[str]:
    """Generate the missing sheets, then write INDEX.md's status block (kept outside the block byte for byte).
    Returns the ids whose sheets were generated."""
    root = Path(root)
    made = [r["id"] for r in index_rows(root) if r["status"] in NEEDS_SHEET and not r["sheet"]]
    for pid in made:
        write(root, pid, day)
    path = root / OUT_DIR / "INDEX.md"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    block = render_status(index_rows(root), day)
    if BEGIN in text and END in text:
        head, rest = text.split(BEGIN, 1)
        text = head + block + rest.split(END, 1)[1].lstrip("\n")
    else:
        text = text + ("" if text.endswith("\n") or not text else "\n") + "\n" + block
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return made


def main(argv: list[str] | None = None) -> int:
    ap_ = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap_.add_argument("--root", type=Path, default=JS_ROOT)
    ap_.add_argument("--today", type=cli_date)
    ap_.add_argument("--all", action="store_true")
    ap_.add_argument("--index", action="store_true", help="statuses into INDEX.md; sheets for portals missing one")
    ap_.add_argument("portal", nargs="?")
    args = ap_.parse_args(argv)
    day = args.today or today()
    root = Path(args.root)
    if args.index:
        made = write_index(root, day)
        print(count_line(index_rows(root)))
        print(f"generated {len(made)} sheets" + (": " + ", ".join(made) if made else ""))
        return 0
    if args.all:
        skipped = []
        order = ORDER or tuple(str(e["id"]) for key in ("platforms", "presence_surfaces")
                               for e in _yaml(root / "targets" / "platforms.yaml").get(key) or []
                               if isinstance(e, dict) and e.get("id"))
        for pid in order:
            path = write(root, pid, day)
            if path:
                print(f"wrote {pid} {path.relative_to(root)}")
            else:
                skipped.append(pid)
        if skipped:
            print("skipped (not in the registry): " + ", ".join(skipped))
        return 0
    if not args.portal:
        ap_.error("name a portal id or pass --all")
    path = write(root, args.portal, day)
    if path is None:
        print(f"refused: {args.portal} is not in targets/platforms.yaml (platforms or presence_surfaces)",
              file=sys.stderr)
        return 2
    print(f"wrote {args.portal} {path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
