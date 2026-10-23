#!/usr/bin/env python3
"""Outbound drafts and their approval files. Nothing here sends anything.

    python3 scripts/outbox.py create --batch B-2026-09-24-1 [--requester role:messenger]   (staged drafts)
    python3 scripts/outbox.py draft --prospect maya-chen --channel email --body-file reply.txt [--kind reply]
    python3 scripts/outbox.py draft --channel email --kind official --to dso@example.org --subject "..." --body-file dso.txt
    python3 scripts/outbox.py batch --ids O-2026-0001,O-2026-0002 | --all-drafted [--requester role:prospector]
    python3 scripts/outbox.py apply-decisions --approval ../../../../work/approvals/A-2026-0001.md | --all
    python3 scripts/outbox.py mark-sent --id O-2026-0001 [--at 2026-09-25T17:00:00Z]
    python3 scripts/outbox.py list [--status pending]

create --batch   files every staged draft in state/drafts/<batch>/<slug>.md (frontmatter per the messenger
                 role card: prospect, channel, kind, to, subject, thread_id, attachments, evidence_ids, checks,
                 supersedes, ...; body = the exact message) as outbox items and requests approval; if any
                 draft fails a check, nothing is filed. Requester: --requester, else from the batch prefix
                 (P- prospector, L- linkedin-editor, otherwise messenger). The approval's why and effect
                 follow the batch prefix and channels (B- inbound replies, F- follow-ups, P- outbound referral
                 asks or connection notes, L- LinkedIn profile edits, posts, comments); --why overrides.
                 Profile-edit drafts carry `field` and `old_text`; the approval shows before and after.
                 Comments target a post: `to:` the post URL, `author:` the post's author; no prospect is
                 needed, created or changed (a missing one is noted in checks). At most 10 comments a day
                 (pending, approved or sent, plus the owner's own comments logged in touches/), never
                 identical texts, at most 1,250 characters.
draft            writes one outbox/O-YYYY-NNNN.md (status drafted) with the exact message as the body; email
                 replies inherit the thread from the prospect record. Kind `official` is mail to the DSO, an
                 attorney or an employer's HR (legal-visa's drafts): email only, --to and --subject required,
                 never a --prospect; no prospect is created or changed and mark-sent logs no touch.
lint             runs create's checks on the staged drafts of a batch and files nothing (create runs the same
                 checks, so a draft that fails lint is never filed), including the message predicates M1, M2, M4,
                 M5, M7, M9 and M13 (message_issues; playbooks/outreach.md §12) on drafts to a person, and M16
                 (a comment or post never asks for a job; the phrases are policy.outreach.job_ask_patterns)
                 on comments and posts: prints
                 {file: issues} and exits 1 when any draft has an issue. The voice gate (voice_issues) is
                 playbooks/outreach.md §10 as code: banned phrases (style.banned_phrases; not in comments, which keep their
                 own wording, nor in official letters), em- and en-dashes when the style bans them, model leakage, reveal bridges, negative
                 parallelism, staccato stacks, sincerity announcements, 3+ AI-tell words in one paragraph.
batch            lints every draft (no em-dashes, no unfilled placeholders, nothing addressed to an agent,
                 never identical LinkedIn texts, connection notes <= 200 chars on a free account, nothing
                 public that reveals the search) and writes one approval file per action
                 (send-email | post-linkedin | edit-linkedin-profile | edit-portal-profile | publish) in
                 <runtime>/work/approvals/A-YYYY-NNNN.md, status pending,
                 requested_by role:messenger (role:legal-visa when every draft is official; --requester
                 overrides), with an overall content_hash and one hash per item.
apply-decisions  reads the owner's decision (WORKFLOW.md §2.4): approved -> ticked items `approved`, unticked
                 `rejected`; refuses everything if the approval text changed, is expired or undated, and
                 refuses any item whose content (body or attachment bytes) changed since the request.
                 rejected -> every item `rejected`; expired -> `expired` (redraft with supersedes); withdrawn (the
                 requester retracted it) -> every item still pending becomes `withdrawn`, never one already sent.
can-send         the pre-send check (send_problems): exits 1 with the reasons when the draft is not approved, is
                 held (its `hold:` field, or a row in a state/outbox-*hold*.md file) or, as a first touch, fails
                 first_touch_issues; run it before sending.
mark-sent        after the owner (or the OS sender) actually sent an approved item: logs the touch,
                 updates the prospect (last_outbound_at, outbound_count, follow_ups_sent, next_touch_at),
                 and appends a receipt to the approval file. Refuses a held draft or a first touch that fails
                 first_touch_issues (send_problems), whatever its status.
Email items become Gmail drafts via `gmail_pull.py draft --approval <file>`; LinkedIn items are pasted
by the owner (or browser-assisted with the owner watching).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import agency_config as cfg
import graph_links
import js_common as jc
import js_comms_util as cu
import prospects as pr

CHANNELS = ("email", "linkedin-message", "linkedin-connect", "linkedin-comment", "linkedin-post", "linkedin-profile-edit",
            "portal-profile", "github-publish")
PUBLIC_CHANNELS = ("linkedin-post", "linkedin-comment", "linkedin-profile-edit", "portal-profile", "github-publish",
                   "x-post", "x-reply")   # x-*: scripts/x_api.py checks its public text with lint_draft
# A portal profile edit (`to: <portal id>/<field>`, the portal in state/presence/portals.yaml) and a file replaced on a
# GitHub repo (`repo`, `path`, `branch`, and the live `blob_sha` it replaces, pinned). Both are public: the voice gate
# and the holds (profile/PROFILE-HOLDS.md) are checked, and no rule is waived. The owner applies both themselves.
HOLDS_CHANNELS = ("portal-profile", "github-publish")


def approval_risk(channels: list[str]) -> str:
    """The approval's risk: high when anything in it is public (seen by many, indexed, hard to take back), medium when
    one person reads it (email, a message, a connection note), low only for an empty or internal batch (public items
    must never default to "low")."""
    if any(c in PUBLIC_CHANNELS for c in channels):
        return "high"
    return "medium" if channels else "low"
PORTAL_TO = re.compile(r"^[a-z0-9][a-z0-9-]*/[a-z0-9_.-]+$")
REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$")
BLOB_SHA = re.compile(r"^[0-9a-f]{40}$")
PERSON_CHANNELS = ("email", "linkedin-message", "linkedin-connect")   # these need and update a prospect
# Comments target a post and profile edits a field: a prospect link is optional and never created or changed.
COMMENT_LIMIT = 1250                               # LinkedIn's comment length limit (characters)
COMMENTS_PER_DAY = int(cfg.get("policy.outreach.comments_per_day", 10))   # LinkedIn comments per day, all states
STATUSES = ("drafted", "pending", "approved", "rejected", "expired", "sent", "replied", "bounced", "superseded")
STAGED_EXTRA = ("batch", "label", "evidence_ids", "conflicts_touched", "personalization", "sponsorship_variant",
                "windows", "field", "old_text", "author", "visibility", "repo", "path", "branch", "blob_sha")
KINDS = ("intro", "reply", "follow_up", "thank_you", "scheduling", "coffee", "other", "official")
OFFICIAL = "official"                              # the DSO, an attorney, an employer's HR: never a job prospect
CONNECT_NOTE_LIMIT = {False: 200, True: 300}      # free vs Premium LinkedIn (checked 2026)
FREE_PERSONAL_NOTES_PER_MONTH = 5
PLACEHOLDER = re.compile(r"\{\{?\s*[\w .]+\s*\}?\}|\[(first ?name|last ?name|name|company|role|title|job)\]|<(first ?name|name|company)>|(?-i:\bXX+\b|\bTODO\b|\bTBD\b)", re.I)


def _alternation(path: str) -> "re.Pattern[str] | None":
    """One case-insensitive regex from a config list of regex fragments (None when the list is empty)."""
    parts = [str(x) for x in (cfg.get(path) or []) if str(x).strip()]
    return re.compile("|".join(f"(?:{x})" for x in parts), re.I) if parts else None


# Phrases that must not appear in public text because they reveal a job search (policy.outreach.confidential_patterns).
CONFIDENTIAL = _alternation("policy.outreach.confidential_patterns")

# The voice gate: playbooks/outreach.md §10 as code. The structural tells (reveal bridges, negative parallelism,
# staccato stacks, sincerity announcements, per-paragraph density) are adapted, in our own patterns, from the
# linkedin-humanizer rules of sergebulaev/linkedin-skills v1.1.14 (MIT, Copyright (c) 2026 Sergey Bulaev).
BANNED = _alternation("style.banned_phrases")     # §10's banned phrases (regex fragments from the style config)
BANNED_EXEMPT_CHANNELS = ("linkedin-comment",)   # comments keep their own wording (linkedin-comment-playbook.md)
BANNED_EXEMPT_KINDS = (OFFICIAL,)                # a letter to a DSO or HR keeps its formal courtesy
LEAKAGE = re.compile(r"oaicite|contentreference\[|\bturn\d+(search|view|news)\d+|\bas an ai( language)? model\b|"
                     r"\bas of my (last|latest)( knowledge)? (update|cutoff)\b|\bknowledge cutoff\b|"
                     r"\[your (name|company|title|role)\]", re.I)
REVEAL_BRIDGE = re.compile(r"(?:^|(?<=[.!?:] ))(?:the (?:result|outcome|answer|lesson|catch|kicker|truth|twist)\?|"
                           r"here'?s (?:what|how|why|the thing)\b|(?:plot twist|spoiler)[:?])", re.I | re.M)
NEG_PARALLEL = re.compile(r"\bit'?s not (?:just |about )?[^,.;\n]{1,40}, it'?s\b|"
                          r"\b(?:isn'?t|is not) [^,.;\n]{1,40}, it'?s\b|"
                          r"\bnot just [^,.;\n]{1,40}(?:,|;)? but\b|"
                          r"\bthis isn'?t [^.\n]{1,40}\. this is\b", re.I)
STACCATO = re.compile(r"\bno [^.!?\n]{1,25}\. no [^.!?\n]{1,25}\. (?:just|only) |"
                      r"(?:^|(?<=[.!?] ))(?:[A-Za-z']+(?: [A-Za-z']+)?\. ){2}[A-Za-z']+(?: [A-Za-z']+)?\.(?=\s|$)", re.I | re.M)
SINCERITY = re.compile(r"\b(?:let me be (?:honest|real|direct)|to be (?:honest|direct)|i'?ll be (?:honest|real)|honestly\?|"
                       r"real talk|can i be vulnerable|unpopular opinion|i'?ll say the quiet part)\b", re.I)
_TELL = [str(x) for x in (cfg.get("style.ai_tell_words") or []) if str(x).strip()]
TELL_WORDS = re.compile(r"\b(?:" + "|".join(_TELL) + r")\b", re.I) if _TELL else None   # style.ai_tell_words
TELL_DENSITY = int(cfg.get("style.ai_tell_density", 3))   # markers in one paragraph that make it read machine-written


def voice_issues(channel: str, text: str, kind: str | None = None) -> list[str]:
    """The voice rules a script can decide (§10); models still judge everything else."""
    issues: list[str] = []
    if BANNED and channel not in BANNED_EXEMPT_CHANNELS and kind not in BANNED_EXEMPT_KINDS:
        m = BANNED.search(text)
        if m:
            issues.append(f"banned phrase '{m.group(0)}' (playbooks/outreach.md §10)")
    if cfg.get("style.no_en_dash", False) and "–" in text:
        issues.append("en-dash (–) in the text: use a hyphen in ranges and a comma or full stop between clauses")
    for pattern, name, fix in (
            (LEAKAGE, "model leakage", "delete it; it is text no person writes"),
            (REVEAL_BRIDGE, "reveal bridge", "delete the bridge and let the next sentence stand"),
            (NEG_PARALLEL, "negative parallelism", "say the positive claim as a plain statement"),
            (STACCATO, "staccato stack", "write full sentences"),
            (SINCERITY, "sincerity announcement", "state the fact without announcing honesty")):
        m = pattern.search(text)
        if m:
            issues.append(f"{name} '{m.group(0).strip()}': {fix}")
    for n, paragraph in enumerate((p for p in re.split(r"\n\s*\n", text) if p.strip()) if TELL_WORDS else (), 1):
        hits = [h.group(0) for h in TELL_WORDS.finditer(paragraph)]
        if len(hits) >= TELL_DENSITY:
            issues.append(f"paragraph {n} has {len(hits)} AI-tell words ({', '.join(hits)}): rewrite the paragraph")
    return issues


class OutboxError(RuntimeError):
    """A draft or approval failed a check; nothing was changed."""


# --------------------------------------------------------------------------- lint
# M15: terms under NDA never go out in any channel. policy.nda.always_terms lists the standing ones; further terms live
# in the gitignored profile/private-holds.txt, one per line under a line `# nda`, until the next comment line; issues
# never echo a private term.
NDA_ALWAYS = tuple(str(t) for t in (cfg.get("policy.nda.always_terms") or []) if str(t).strip())


def nda_terms(root: Path | None = None) -> list[str]:
    """The `# nda` block of profile/private-holds.txt (missing file: [])."""
    path = Path(root or jc.JS_ROOT) / "profile" / "private-holds.txt"
    if not path.is_file():
        return []
    out: list[str] = []
    inside = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            inside = stripped.lstrip("#").strip().lower() == "nda"
            continue
        if inside and stripped:
            out.append(stripped)
    return out


def nda_issues(text: str, root: Path | None = None) -> list[str]:
    issues = [f"M15: '{t}' is under NDA; nothing about it goes out" for t in NDA_ALWAYS
              if re.search(rf"\b{re.escape(t)}\b", text, re.I)]
    for n, term in enumerate(nda_terms(root), 1):
        if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.I):
            issues.append(f"M15: NDA term {n} of the `# nda` block in profile/private-holds.txt is in the text")
    return issues


# M16: a comment or post never asks for a job; it says what the author thinks about the post's subject. Public
# comments and posts only; a private message may mention a resume. The phrases are policy.outreach.job_ask_patterns.
JOB_ASK_CHANNELS = ("linkedin-comment", "linkedin-post", "x-reply", "x-post")
JOB_ASK = _alternation("policy.outreach.job_ask_patterns")


def lint_draft(channel: str, body: str, premium: bool = False, kind: str | None = None,
               root: Path | None = None) -> list[str]:
    issues: list[str] = []
    text = body or ""
    if not text.strip():
        return ["empty draft"]
    issues += nda_issues(text, root)
    if cfg.get("style.no_em_dash", False) and "—" in text:
        issues.append("em-dash (—) in the text: the style rule is no em-dashes")
    issues += voice_issues(channel, text, kind)
    if channel in HOLDS_CHANNELS:
        import check_holds
        import profile_titles as pt
        issues += [f"held text: {pt.shown(h.held)} ({h.pass_}; profile/PROFILE-HOLDS.md)"
                   for h in check_holds.check_text(text, markdown=False) if not h.gap]
    m = PLACEHOLDER.search(text)
    if m:
        issues.append(f"unfilled placeholder: {m.group(0)}")
    _, hits = cu.strip_injections(text)
    if hits:
        issues.append(f"text addressed to an agent (possible injection): {hits[0]}")
    if channel in PUBLIC_CHANNELS:
        c = CONFIDENTIAL.search(text) if CONFIDENTIAL else None
        if c:
            issues.append(f"confidential search: public text must not say '{c.group(0)}'")
    if JOB_ASK and channel in JOB_ASK_CHANNELS:
        ask = JOB_ASK.search(text.replace("\u2019", "'"))
        if ask:
            issues.append(f"M16: a comment or post never asks for a job ('{ask.group(0)}'); give your view on the post's "
                          f"subject from your real work")
    if channel == "linkedin-comment" and len(cu.canonical_text(text)) > COMMENT_LIMIT:
        issues.append(f"comment is {len(cu.canonical_text(text))} chars; LinkedIn allows {COMMENT_LIMIT}")
    if channel == "linkedin-connect":
        limit = CONNECT_NOTE_LIMIT[bool(premium)]
        if len(cu.canonical_text(text)) > limit:
            issues.append(f"connection note is {len(cu.canonical_text(text))} chars; LinkedIn allows {limit}"
                          + ("" if premium else " on a free account"))
    return issues


# --------------------------------------------------------------------------- message predicates
# The message predicates a script can decide, as code; playbooks/outreach.md §12 is the
# text. Issues read "M<n>: ...". M3, M6, M8, M10, M11 (in private), M12 and M14 stay with the messenger and
# the reviewer. A first touch is kind `intro` or a connection note.
LINKEDIN_ASK_CHARS = int(cfg.get("policy.outreach.linkedin_ask_chars", 400))   # M1
LINKEDIN_MAX_CHARS = int(cfg.get("policy.outreach.linkedin_max_chars", 600))
FIRST_EMAIL_WORDS = tuple(cfg.get("policy.outreach.first_email_words", (60, 120)))   # M2
TOUCH_CAP = int(cfg.get("policy.outreach.touch_cap", 3))   # M9: touches to one person since their last reply
IDENTICAL_DAYS = int(cfg.get("policy.outreach.identical_days", 30))   # M13
NO_CAP_KINDS = ("reply", "scheduling", "thank_you", OFFICIAL)   # answers to the other person, not new touches
LINK = re.compile(r"https?://|\bwww\.|\blinkedin\.com/", re.I)


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _since_last_reply(roots: cu.Roots, slug: str, pmeta: dict[str, Any]) -> int:
    """Outbound touches to `slug` after the last inbound one (touches/*.jsonl, plus the record's reply stamps)."""
    rows = [t for t in jc.iter_touches(roots.js) if t.get("prospect") == slug]
    stamps = [str(t.get("at") or "") for t in rows if t.get("direction") == "in"]
    stamps += [str(pmeta.get(k)) for k in ("last_reply_at", "last_inbound_at") if pmeta.get(k)]
    last_in = max(stamps, default="")
    return sum(1 for t in rows if t.get("direction") == "out" and str(t.get("at") or "") > last_in)


def _sent_recently(roots: cu.Roots, text: str, today: date) -> str | None:
    """The id of an outbox item sent in the last IDENTICAL_DAYS days with the same words, if any."""
    key = _norm(text)
    if len(key) < 20 or not roots.outbox.is_dir():
        return None
    for path in sorted(roots.outbox.glob("O-*.md")):
        meta, body = cu.read_record(path)
        sent = jc.parse_date(str(meta.get("sent_at") or "")[:10]) if meta.get("status") == "sent" else None
        if sent and 0 <= (today - sent).days <= IDENTICAL_DAYS and _norm(body) == key:
            return str(meta.get("id") or path.stem)
    return None


def message_issues(roots: cu.Roots, meta: dict[str, Any], body: str, today: date | None = None) -> list[str]:
    """M1, M2, M4, M5, M7, M9 and M13 on one draft to a person (R4 §C); [] for posts, comments and edits."""
    today = today or jc.today()
    channel, kind, text = meta.get("channel") or "", meta.get("kind") or "reply", body or ""
    if channel not in PERSON_CHANNELS or kind == OFFICIAL:
        return []
    issues: list[str] = []
    first = kind == "intro" or channel == "linkedin-connect"
    chars, words, asks = len(cu.canonical_text(text)), len(text.split()), text.count("?")
    if channel == "linkedin-message":
        if chars > LINKEDIN_MAX_CHARS:
            issues.append(f"M1: LinkedIn message is {chars} chars; {LINKEDIN_MAX_CHARS} at most")
        elif kind == "intro" and chars > LINKEDIN_ASK_CHARS:
            issues.append(f"M1: a first LinkedIn ask is {chars} chars; keep it under {LINKEDIN_ASK_CHARS}")
    lo, hi = FIRST_EMAIL_WORDS
    if channel == "email" and kind == "intro" and not lo <= words <= hi:
        issues.append(f"M2: a first email is {words} words; the band is {lo} to {hi}")
    if channel in ("email", "linkedin-message") and kind == "intro" and asks != 1:
        issues.append(f"M4: a first touch carries exactly one ask as a question; found {asks} question marks")
    elif (channel == "linkedin-connect" or kind == "follow_up") and asks > 1:
        issues.append(f"M4: one ask only; found {asks} question marks")
    if first:
        if LINK.search(text):
            issues.append(f"M5: no link on a first touch ('{LINK.search(text).group(0)}'); offer it instead")
        if meta.get("attachments"):
            issues.append("M5: no attachment on a first touch; the resume is offered, not attached")
    if first and text.strip():                     # a connection without a note is no pitch
        issues += first_touch_issues(channel, text)
    slug = meta.get("prospect")
    pmeta: dict[str, Any] = {}
    if slug and (roots.prospects / f"{slug}.md").exists():
        pmeta = cu.read_record(roots.prospects / f"{slug}.md")[0]
    name = str(pmeta.get("name") or "").split()
    if name and not re.search(rf"\b{re.escape(name[0])}\b", text, re.I):
        issues.append(f"M7: the first name '{name[0]}' is not in the message")
    if slug and kind not in NO_CAP_KINDS:
        n = _since_last_reply(roots, slug, pmeta)
        if n >= TOUCH_CAP:
            issues.append(f"M9: {n} touches to {slug} since the last reply; three is the cap, never a fourth")
    same = _sent_recently(roots, text, today)
    if same:
        issues.append(f"M13: identical to {same}, sent in the last {IDENTICAL_DAYS} days")
    return issues


# A first touch is about them and asks one real question; the sender's own background waits for turn 2 and the ask for
# rapport. SELF_OPENER catches a message that opens with the sender; PITCH_FORMULA (style.pitch_phrases) lists the
# stock pitch wording that gives a cold message away.
SELF_OPENER = re.compile(r"^(?:(?:at|while at|back at|during) [^,.]{1,40},? )?(?:i|i'm|i am|i've|i have|my|we|we're)\b", re.I)
_PITCH = [str(x) for x in (cfg.get("style.pitch_phrases") or []) if str(x).strip()]
PITCH_FORMULA = re.compile(r"\b(?:" + "|".join(_PITCH) + r")", re.I) if _PITCH else None
GREETING = re.compile(r"^\s*(?:hi|hey|hello|dear)\b[^,\n]{0,40}[,!]?\s*", re.I)


def first_touch_issues(channel: str, text: str) -> list[str]:
    """M17-M19: a first touch opens a conversation about them, never a pitch."""
    issues: list[str] = []
    asks = text.count("?")
    if channel == "linkedin-connect" and asks != 1:
        issues.append(f"M17: a first touch asks them one real question about their work; found {asks}")
    body = GREETING.sub("", text.strip(), count=1)
    if SELF_OPENER.search(body):
        issues.append("M18: the first touch opens with the sender ('" + body.split(".")[0][:60] + "'); open with them")
    m = PITCH_FORMULA.search(text) if PITCH_FORMULA else None
    if m:
        issues.append(f"M19: pitch formula '{m.group(0)}' in a first touch; your work waits for turn 2, the ask for rapport")
    return issues


# Holds: an approved first touch that fails first_touch_issues must not be recorded as sent, so mark_sent checks more
# than the status and the content hash. A draft is HELD when its record carries a non-empty
# `hold:` field (the reason), or when a hold file state/outbox-*hold*.md lists its id as the first cell of a table row
# (`| O-YYYY-NNNN | ...`; an approval A-YYYY-NNNN or a gmail_send item mail-... holds that send); a row is released
# by deleting it or by writing `released` in it. send_problems is the one
# pre-send check: mark_sent refuses on it, and `outbox.py can-send --id O-...` runs it before anyone sends.
HOLD_GLOB = "outbox-*hold*.md"
HOLD_ROW = re.compile(r"^\|\s*([OA]-\d{4}-\d{3,}|mail-[\w.-]+)\s*\|")   # a draft, an approval or a Gmail item


def holds(root: Path | None = None) -> dict[str, str]:
    """{draft id: the hold file (under state/) that lists it} from every state/outbox-*hold*.md."""
    folder = Path(root or jc.JS_ROOT) / "state"
    out: dict[str, str] = {}
    for path in sorted(folder.glob(HOLD_GLOB)) if folder.is_dir() else []:
        for line in path.read_text(encoding="utf-8").splitlines():
            m = HOLD_ROW.match(line)
            if m and not re.search(r"\breleased\b", line, re.I):
                out.setdefault(m.group(1), path.name)
    return out


def send_problems(roots: cu.Roots, meta: dict[str, Any], body: str) -> list[str]:
    """Why this draft must not go out, or be recorded as sent, now ([] when nothing stops it): a hold (the record's
    `hold:` field or a hold file) and, on a first touch to a person, first_touch_issues."""
    problems: list[str] = []
    draft_id = str(meta.get("id") or "")
    if str(meta.get("hold") or "").strip():
        problems.append(f"on hold: {str(meta['hold']).strip()}")
    listed = holds(roots.js).get(draft_id)
    if listed:
        problems.append(f"on hold: listed in state/{listed}")
    channel, kind, text = meta.get("channel") or "", meta.get("kind") or "reply", body or ""
    if channel in PERSON_CHANNELS and kind != OFFICIAL and (kind == "intro" or channel == "linkedin-connect") \
            and text.strip():
        problems += first_touch_issues(channel, text)
    return problems


# --------------------------------------------------------------------------- drafts
def draft_hash(roots: cu.Roots, meta: dict[str, Any], body: str) -> str:
    return cu.draft_content_hash(roots.js, meta, body)


def _draft_path(roots: cu.Roots, draft_id: str) -> Path:
    return roots.outbox / f"{draft_id}.md"


def load_draft(roots: cu.Roots, draft_id: str) -> tuple[Path, dict[str, Any], str]:
    path = _draft_path(roots, draft_id)
    if not path.is_file():
        raise OutboxError(f"no draft {draft_id} in {roots.outbox}")
    meta, body = cu.read_record(path)
    return path, meta, body


def _load_prospect(roots: cu.Roots, slug: str) -> tuple[Path, dict[str, Any], str]:
    path = roots.prospects / f"{slug}.md"
    if not path.is_file():
        raise OutboxError(f"no prospect {slug}")
    meta, body = cu.read_record(path)
    return path, meta, body


def create_draft(roots: cu.Roots, *, prospect: str | None, channel: str, body: str, subject: str | None = None,
                 kind: str = "reply", to: str | None = None, thread_id: str | None = None,
                 in_reply_to: str | None = None, references: str | None = None, today: date | None = None,
                 premium: bool = False, attachments: list[str] | None = None, supersedes: str | None = None,
                 extra: dict[str, Any] | None = None, checks: list[str] | None = None) -> Path:
    today = today or jc.today()
    if channel not in CHANNELS:
        raise OutboxError(f"channel {channel!r} not in {CHANNELS}")
    if kind not in KINDS:
        raise OutboxError(f"kind {kind!r} not in {KINDS}")
    if kind == OFFICIAL:
        if channel != "email":
            raise OutboxError("official mail goes by email (channel email)")
        if prospect:
            raise OutboxError("official mail (the DSO, an attorney, an employer's HR) never links a job prospect; "
                              "drop --prospect")
        if not str(to or "").strip():
            raise OutboxError("official mail needs --to (the address from the source record)")
        if not str(subject or "").strip():
            raise OutboxError("official mail needs --subject")
    elif channel in PERSON_CHANNELS and not prospect:
        raise OutboxError(f"a {channel} draft needs --prospect")
    if channel == "linkedin-comment" and not re.match(r"^https?://\S+$", str(to or "").strip()):
        raise OutboxError("a linkedin-comment draft needs `to:` the post URL")
    extra = dict(extra or {})
    if channel == "portal-profile":
        to = str(to or "").strip()
        if not PORTAL_TO.match(to):
            raise OutboxError("a portal-profile draft needs `to:` <portal id>/<field>, lower case (e.g. wellfound/about)")
        import portal_registry                    # targets/platforms.yaml is the one source per portal
        try:
            portals = portal_registry.surface_ids(roots.js)
        except FileNotFoundError:
            portals = set()
        if to.split("/", 1)[0] not in portals:
            raise OutboxError(f"portal {to.split('/', 1)[0]!r} is not a portal in targets/platforms.yaml (portal_registry)")
        extra.setdefault("field", to.split("/", 1)[1])
    if channel == "github-publish":
        for key, rx in (("repo", REPO), ("path", re.compile(r"^[^\s/][^\s]*$")), ("branch", re.compile(r"^\S+$")),
                        ("blob_sha", BLOB_SHA)):
            if not rx.match(str(extra.get(key) or "")):
                raise OutboxError(f"a github-publish draft needs `{key}` (repo owner/name, path, branch, and the live "
                                  f"40-hex blob_sha it replaces, pinned)")
        to = f"github:{extra['repo']}@{extra['branch']}/{extra['path']}"
    pmeta: dict[str, Any] = {}
    checks = list(checks or [])
    if prospect and channel not in PERSON_CHANNELS and not (roots.prospects / f"{prospect}.md").is_file():
        checks.append(f"no prospect record {prospect}; filed without a prospect link (none is created)")
        prospect = None
    if prospect:
        ppath, pmeta, pbody = _load_prospect(roots, prospect)
        if pmeta.get("status") == "do_not_contact":
            raise OutboxError(f"{prospect} is do_not_contact: {pmeta.get('do_not_contact_reason')}")
    rc = pmeta.get("reply_context") or {}
    if channel == "email":
        to = to or (rc.get("to") if rc.get("channel") == "email" else None) or (pmeta.get("emails") or [None])[0]
        if not to:
            raise OutboxError(f"{prospect} has no email address; pass --to")
        if rc.get("channel") == "email":
            thread_id = thread_id or rc.get("thread_id")
            in_reply_to = in_reply_to or rc.get("message_id")
            references = references or " ".join(x for x in (rc.get("references"), rc.get("message_id")) if x) or None
            if not subject and rc.get("subject"):
                subject = rc["subject"] if re.match(r"^\s*re:", rc["subject"], re.I) else f"Re: {rc['subject']}"
        if not subject:
            raise OutboxError("an email draft needs a subject (no thread to reply to)")
    elif channel in PERSON_CHANNELS:
        to = to or pmeta.get("linkedin_url") or pmeta.get("name")
        if channel == "linkedin-message" and rc.get("channel") == "linkedin-message":
            thread_id = thread_id or rc.get("thread_id")
        subject = subject or None
    else:
        to = to or channel
    attachments = [str(a) for a in attachments or []]
    try:
        cu.attachment_digests(roots.js, attachments)
    except FileNotFoundError as exc:
        raise OutboxError(str(exc)) from exc
    old = None
    if supersedes:
        opath, ometa, obody = load_draft(roots, supersedes)
        if ometa.get("status") not in ("drafted", "rejected", "expired"):
            raise OutboxError(f"{supersedes} is {ometa.get('status')}; only drafted, rejected or expired items are superseded")
        old = (opath, ometa, obody)
    text = cu.canonical_text(body) + "\n"
    lint = lint_draft(channel, text, premium=premium, kind=kind, root=roots.js)

    def render(draft_id: str) -> str:
        meta = {"id": draft_id, "prospect": prospect, "channel": channel, "kind": kind, "to": to, "subject": subject,
                "thread_id": thread_id, "in_reply_to": in_reply_to, "references": references,
                "attachments": attachments, "created": today, "status": "drafted", "approval": None,
                "content_hash": None, "checks": list(checks or []) + [f"LINT: {i}" for i in lint],
                "supersedes": supersedes, "gmail_draft_id": None, "sent_at": None}
        meta.update({k: v for k, v in (extra or {}).items() if k in STAGED_EXTRA and v is not None})
        meta["content_hash"] = draft_hash(roots, meta, text)
        graph_links.stamp("outbox", roots.js, meta)            # type: outbox, links: prospect (+ application)
        return cu.dump_record(meta, text)

    draft_id, path = cu.create_numbered(roots.outbox, "O", today.year, render)
    if old:
        opath, ometa, obody = old
        ometa.update({"status": "superseded", "superseded_by": draft_id})
        cu.write_record(opath, ometa, obody)
    if prospect and channel in PERSON_CHANNELS and pmeta.get("status") in ("new", "triaged"):
        pmeta["status"], pmeta["updated"] = "drafted", today
        cu.write_record(ppath, pmeta, pbody)
    return path


def list_drafts(roots: cu.Roots, status: Iterable[str] | None = None) -> list[dict[str, Any]]:
    wanted = set(status or ())
    out = []
    for p in sorted(roots.outbox.glob("O-*.md")) if roots.outbox.is_dir() else []:
        meta, _ = cu.read_record(p)
        if not wanted or meta.get("status") in wanted:
            out.append(meta)
    return out


# --------------------------------------------------------------------------- approvals
# Profile edits (channel linkedin-profile-edit) get an approval of their own, action `edit-linkedin-profile`
# (an enum value in schemas/approval.yaml), targeted linkedin:profile/<field>.
PROFILE_ACTION = "edit-linkedin-profile"
PORTAL_ACTION = "edit-portal-profile"
ACTION = {"email": "send-email", "linkedin-profile-edit": PROFILE_ACTION, "portal-profile": PORTAL_ACTION,
          "github-publish": "publish"}
WHY_PRESENCE = ("Public profile batch: {what}, so every surface says what the record says. Nothing here is "
                "sent to anyone.")
WHY = "Reply batch from the inbound backlog: people who reached out to you."
WHY_BY_PREFIX = {
    "B": WHY,
    "F": "Follow-up batch: nudges to people you already wrote to who have not answered yet.",
    "P": "Outbound batch from the prospector: {what} to people found on a warm path; "
         "none of them wrote to you first.",
    "L": "LinkedIn batch from the linkedin-editor: {what}. Nothing here answers anyone's message.",
}
WHY_OFFICIAL = ("Official mail drafted by legal-visa (roles/legal-visa.md) about the owner's work authorization, to "
                "the DSO, an attorney or an employer; not a job prospect, so no CRM record changes.")
EFFECT = {
    "send-email": ("Each ticked email becomes a Gmail DRAFT in its original thread "
                   "(`python3 scripts/gmail_pull.py draft --approval <this file>`, which needs the separate compose "
                   "login). Nothing is sent until the owner presses Send in Gmail; then `outbox.py mark-sent` logs it."),
    "post-linkedin": ("Ticked items stay in outbox/ for the owner to paste into LinkedIn themselves, or to send "
                      "browser-assisted with the owner watching, one action at a time (no Premium: <= 30 messages "
                      "and <= 20 connection requests a day, about 5 personalized connection notes a month, never "
                      "identical texts). Afterwards `outbox.py mark-sent` logs each one."),
}


EFFECT_PROFILE = ("Nothing is sent to anyone. The owner applies each ticked profile edit themselves on LinkedIn (or "
                  "browser-assisted with the owner watching), one field at a time, replacing the Before text with the "
                  "After text shown for that field. Afterwards `outbox.py mark-sent` records each one.")
EFFECT_PORTAL = ("Nothing is sent to anyone. The owner applies each ticked portal edit themselves on that portal, one field at "
                 "a time, replacing the Before text with the After text shown. Afterwards `outbox.py mark-sent` records each one.")
EFFECT_GITHUB = ("No agent pushes anything. The owner (or the OS sender, acting on this approved file) replaces each ticked file "
                 "on GitHub with the exact content shown, only if its live blob is still the pinned sha; a different sha "
                 "means the file changed since the draft: stop and redraft. Afterwards `outbox.py mark-sent` records each one.")
EFFECT_PUBLIC = ("Ticked posts and comments are public once the owner publishes them themselves (or browser-assisted with "
                 "the owner watching), one at a time, at most " + str(COMMENTS_PER_DAY) + " comments a day. Afterwards `outbox.py mark-sent` logs each one.")


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" + ("" if n == 1 else "s")


def _batch_prefix(metas: list[dict[str, Any]]) -> str | None:
    prefixes = {str(m.get("batch") or "")[:2] for m in metas}
    if len(prefixes) == 1:
        (prefix,) = prefixes
        if len(prefix) == 2 and prefix[1] == "-" and prefix[0] in WHY_BY_PREFIX:
            return prefix[0]
    return None


def derive_why(metas: list[dict[str, Any]]) -> str:
    """The approval's "why" from the batch prefix of its drafts (B- replies, F- follow-ups, P- prospector
    outbound, L- LinkedIn editor), else from their kinds and channels."""
    channels = [str(m.get("channel") or "") for m in metas]
    kinds = [str(m.get("kind") or "") for m in metas]
    if kinds and all(k == OFFICIAL for k in kinds):
        return WHY_OFFICIAL
    if channels and all(c in HOLDS_CHANNELS for c in channels):
        parts = [_count(channels.count(c), noun) for c, noun in (("portal-profile", "portal profile edit"),
                                                                 ("github-publish", "GitHub file")) if channels.count(c)]
        return WHY_PRESENCE.format(what=" and ".join(parts))
    prefix = _batch_prefix(metas)
    if prefix is None:
        if channels and all(c in PUBLIC_CHANNELS for c in channels):
            prefix = "L"
        elif kinds and all(k == "follow_up" for k in kinds):
            prefix = "F"
        elif kinds and all(k == "intro" for k in kinds):
            prefix = "P"
        else:
            prefix = "B"
    if prefix == "P":
        connects = channels.count("linkedin-connect")
        others = len(channels) - connects
        parts = ([_count(connects, "connection note")] if connects else []) + \
                ([_count(others, "referral ask or first message")] if others else [])
        return WHY_BY_PREFIX["P"].format(what=" and ".join(parts) or "first messages")
    if prefix == "L":
        fields = [str(m.get("field") or m.get("to") or "") for m in metas if m.get("channel") == "linkedin-profile-edit"]
        parts = []
        if fields:
            parts.append(_count(len(fields), "profile edit") + f" ({', '.join(f for f in fields if f)}), "
                         "each shown before and after")
        for channel, noun in (("linkedin-post", "post"), ("linkedin-comment", "comment"),
                              ("linkedin-message", "message"), ("linkedin-connect", "connection note")):
            if channels.count(channel):
                parts.append(_count(channels.count(channel), noun))
        return WHY_BY_PREFIX["L"].format(what=", ".join(parts) or "LinkedIn items")
    return WHY_BY_PREFIX[prefix]


def profile_target(metas: list[dict[str, Any]]) -> str:
    return ", ".join(f"linkedin:profile/{m.get('field') or m.get('to') or 'unknown'}" for m in metas)


def profile_effect(items: list[tuple[dict[str, Any], str]]) -> str:
    lines = [EFFECT_PROFILE]
    for meta, body in items:
        old = cu.one_line(meta.get("old_text"), 3000) if meta.get("old_text") else "(empty)"
        lines.append(f"- {meta.get('field') or meta.get('to')}: \"{old}\" becomes \"{cu.one_line(body, 3000)}\"")
    return "\n".join(lines)


def derive_effect(action: str, metas: list[dict[str, Any]], bodies: list[str] | None = None) -> str:
    if action == PROFILE_ACTION and metas and all(m.get("channel") == "linkedin-profile-edit" for m in metas):
        return profile_effect(list(zip(metas, bodies or [""] * len(metas))))
    if action == PORTAL_ACTION:
        lines = [EFFECT_PORTAL]
        for meta, body in zip(metas, bodies or [""] * len(metas)):
            old = cu.one_line(meta.get("old_text"), 3000) if meta.get("old_text") else "(empty)"
            lines.append(f"- {meta.get('to')}: \"{old}\" becomes \"{cu.one_line(body, 3000)}\"")
        return "\n".join(lines)
    if action == "publish":
        return "\n".join([EFFECT_GITHUB] + [f"- {m.get('to')}: only if its live blob is still {m.get('blob_sha')}"
                                              for m in metas])
    if action != "post-linkedin":
        return EFFECT[action]
    channels = {str(m.get("channel") or "") for m in metas}
    parts = []
    if "linkedin-profile-edit" in channels:
        parts.append(EFFECT_PROFILE)
    if channels & {"linkedin-post", "linkedin-comment"}:
        parts.append(EFFECT_PUBLIC)
    if channels - {"linkedin-profile-edit", "linkedin-post", "linkedin-comment"}:
        parts.append(EFFECT["post-linkedin"])
    return " ".join(parts)


COMMENT_TOUCH_CHANNELS = ("linkedin-comment",)


def comments_on(roots: cu.Roots, day: date) -> int:
    """LinkedIn comments already requested (pending or approved, created that day) or sent that day, plus comments
    the owner posted themselves and logged in touches/ that day (without them the daily ceiling could be passed).
    A touch keyed by an outbox id, or pointing at an outbox draft, is the same comment and is not counted again."""
    n, ids = 0, set()
    for m in list_drafts(roots, ("pending", "approved", "sent")):
        if m.get("channel") != "linkedin-comment":
            continue
        ids.add(str(m.get("id")))
        when = jc.parse_date(m.get("sent_at")) if m.get("status") == "sent" else jc.parse_date(m.get("created"))
        n += int(when == day)
    for t in jc.iter_touches(roots.js):
        if t.get("channel") not in COMMENT_TOUCH_CHANNELS or t.get("direction") == "in":
            continue
        if str(t.get("key")) in ids or str(t.get("draft_path") or "").startswith("outbox/"):
            continue
        n += int(jc.parse_date(str(t.get("at") or "")[:10]) == day)
    return n


def comment_ceiling_problem(roots: cu.Roots, day: date, new: int) -> str | None:
    if not new:
        return None
    already = comments_on(roots, day)
    if already + new > COMMENTS_PER_DAY:
        return (f"{new} comments in this request and {already} already requested, sent or logged on {day}: at most "
                f"{COMMENTS_PER_DAY} comments a day; split them across days")
    return None


def _label(roots: cu.Roots, meta: dict[str, Any]) -> str:
    if meta.get("channel") == "linkedin-comment":
        return f"comment on {meta['author']}'s post" if meta.get("author") else f"comment on {meta.get('to')}"
    if not meta.get("prospect"):
        return meta.get("channel") or "item"
    try:
        _, p, _ = _load_prospect(roots, meta["prospect"])
    except OutboxError:
        return meta["prospect"]
    return f"{p.get('name')} ({p['company']})" if p.get("company") else str(p.get("name"))


def request_approval(roots: cu.Roots, draft_ids: Iterable[str], *, requested_by: str = "role:messenger",
                     ticket: str | None = None, today: date | None = None, why: str | None = None,
                     force: bool = False, premium: bool = False) -> list[tuple[str, Path]]:
    today = today or jc.today()
    loaded = []
    problems: list[str] = []
    for draft_id in draft_ids:
        path, meta, body = load_draft(roots, draft_id)
        if meta.get("status") != "drafted" or meta.get("approval"):
            problems.append(f"{draft_id} is {meta.get('status')} and already in approval {meta.get('approval')}"
                            if meta.get("approval") else f"{draft_id} is {meta.get('status')}, not drafted")
            continue
        issues = lint_draft(meta["channel"], body, premium=premium, kind=meta.get("kind"), root=roots.js)
        if issues and not force:
            problems.append(f"{draft_id}: " + "; ".join(issues))
        try:
            cu.attachment_digests(roots.js, meta.get("attachments"))
        except FileNotFoundError as exc:
            problems.append(f"{draft_id}: {exc}")
        info = [c for c in meta.get("checks") or [] if not str(c).startswith("LINT: ")]
        meta["checks"] = info + [f"LINT: {i}" for i in issues]
        meta["content_hash"] = draft_hash(roots, meta, body)
        loaded.append((path, meta, body))
    linkedin = [(m["id"], cu.canonical_text(b).lower()) for _, m, b in loaded if m["channel"].startswith("linkedin")]
    earlier = {cu.canonical_text(b).lower(): m["id"] for m in list_drafts(roots, ("approved", "sent"))
               if str(m.get("channel", "")).startswith("linkedin") for _, b in [cu.read_record(_draft_path(roots, m["id"]))]}
    seen: dict[str, str] = {}
    for draft_id, text in linkedin:
        other = seen.get(text) or earlier.get(text)
        if other:
            problems.append(f"{draft_id} and {other} have identical texts (LinkedIn rule: never identical texts)")
        seen[text] = draft_id
    connects = sum(1 for _, m, _ in loaded if m["channel"] == "linkedin-connect")
    ceiling = comment_ceiling_problem(roots, today, sum(1 for _, m, _ in loaded if m["channel"] == "linkedin-comment"))
    if ceiling:
        problems.append(ceiling)
    if problems:
        raise OutboxError("refused: " + " | ".join(problems))
    groups: dict[str, list[tuple[Path, dict[str, Any], str]]] = {}
    for item in loaded:
        groups.setdefault(ACTION.get(item[1]["channel"], "post-linkedin"), []).append(item)
    created = []
    for action, items in sorted(groups.items()):
        metas = [m for _, m, _ in items]
        effect = derive_effect(action, metas, [b for _, _, b in items])
        if action == "post-linkedin" and connects > FREE_PERSONAL_NOTES_PER_MONTH and not premium:
            effect += f" Note: {connects} connection notes exceed the ~{FREE_PERSONAL_NOTES_PER_MONTH}/month free allowance."
        approval_items = [cu.ApprovalItem(id=m["id"], channel=m["channel"], target=m["to"], body=b,
                                          subject=m.get("subject") or "", label=m.get("label") or _label(roots, m),
                                          prospect=m.get("prospect"), thread_id=m.get("thread_id"),
                                          checks=[str(c) for c in m.get("checks") or []],
                                          attachments=cu.attachment_digests(roots.js, m.get("attachments")),
                                          edit_field=m.get("field") or None,
                                          old_text=None if m.get("old_text") is None else str(m["old_text"]))
                          for _, m, b in items]
        target = profile_target(metas) if action == PROFILE_ACTION else (
            ", ".join(f"portal:{m.get('to')}" for m in metas) if action == PORTAL_ACTION else (
                ", ".join(str(m.get("to")) for m in metas) if action == "publish" else None))
        aid, apath = cu.write_approval(roots, action=action, items=approval_items, requested_by=requested_by,
                                       target=target, risk=approval_risk([str(m.get("channel") or "") for m in metas]),
                                       why=why or derive_why(metas), effect=effect, today=today, ticket=ticket,
                                       draft_refs=[f"outbox/{p.name}" for p, _, _ in items])
        for path, meta, body in items:
            meta["approval"], meta["status"] = aid, "pending"
            cu.write_record(path, meta, body)
        created.append((aid, apath))
    return created


def infer_requester(batch: str) -> str:
    return {"P": "role:prospector", "L": "role:linkedin-editor"}.get(str(batch)[:1], "role:messenger")


def requester_for(roots: cu.Roots, draft_ids: Iterable[str]) -> str:
    """role:legal-visa when every draft is official mail, else role:messenger (the batch command's default)."""
    kinds = []
    for draft_id in draft_ids:
        try:
            kinds.append(load_draft(roots, draft_id)[1].get("kind"))
        except OutboxError:
            return "role:messenger"
    return "role:legal-visa" if kinds and all(k == OFFICIAL for k in kinds) else "role:messenger"


def _staged(roots: cu.Roots, batch: str) -> list[Path]:
    folder = roots.js / "state" / "drafts" / batch
    return sorted(p for p in folder.glob("*.md") if not p.name.startswith(("_", ".")) and p.name != "README.md") \
        if folder.is_dir() else []


def lint_batch(roots: cu.Roots, batch: str, premium: bool = False, today: date | None = None) -> dict[str, list[str]]:
    """The checks `create` runs on each staged draft, without filing anything: {file name: issues}."""
    staged = _staged(roots, batch)
    if not staged:
        raise OutboxError(f"no staged drafts in state/drafts/{batch}/")
    out: dict[str, list[str]] = {}
    for path in staged:
        meta, body = cu.read_record(path)
        issues = lint_draft(meta.get("channel") or "", body, premium=premium, kind=meta.get("kind"), root=roots.js)
        issues += message_issues(roots, meta, body, today)
        if issues:
            out[path.name] = issues
    return out


def create_batch(roots: cu.Roots, batch: str, *, requested_by: str | None = None, today: date | None = None,
                 premium: bool = False, why: str | None = None) -> dict[str, Any]:
    """File every staged draft of a batch and request approval; all-or-nothing on checks."""
    today = today or jc.today()
    staged = _staged(roots, batch)
    if not staged:
        raise OutboxError(f"no staged drafts in state/drafts/{batch}/")
    problems, seen, loaded = [], {}, []
    for path in staged:
        meta, body = cu.read_record(path)
        channel = meta.get("channel")
        if channel not in CHANNELS:
            problems.append(f"{path.name}: channel {channel!r} not in {CHANNELS}")
            continue
        for issue in (lint_draft(channel, body, premium=premium, kind=meta.get("kind"), root=roots.js)
                      + message_issues(roots, meta, body, today)):
            problems.append(f"{path.name}: {issue}")
        try:
            cu.attachment_digests(roots.js, meta.get("attachments"))
        except FileNotFoundError as exc:
            problems.append(f"{path.name}: {exc}")
        if channel.startswith("linkedin"):
            key = cu.canonical_text(body).lower()
            if key in seen:
                problems.append(f"{path.name} and {seen[key]} have identical texts (LinkedIn rule: never identical texts)")
            seen[key] = path.name
        loaded.append((path, meta, body))
    ceiling = comment_ceiling_problem(roots, today, sum(1 for _, m, _ in loaded if m.get("channel") == "linkedin-comment"))
    if ceiling:
        problems.append(ceiling)
    if problems:
        raise OutboxError("nothing filed; fix these drafts: " + " | ".join(problems))
    ids = []
    for path, meta, body in loaded:
        out = create_draft(roots, prospect=meta.get("prospect"), channel=meta["channel"], body=body,
                           subject=meta.get("subject"), kind=meta.get("kind") or "reply", to=meta.get("to"),
                           thread_id=meta.get("thread_id"), today=today, premium=premium,
                           attachments=meta.get("attachments"), supersedes=meta.get("supersedes"),
                           extra={**meta, "batch": batch}, checks=[str(c) for c in meta.get("checks") or []])
        ids.append(out.stem)
    created = request_approval(roots, ids, requested_by=requested_by or infer_requester(batch), today=today,
                               why=why, premium=premium)
    return {"batch": batch, "items": ids,
            "approvals": [{"approval": aid, "path": str(p), "items": len(cu.parse_approval(p).items)} for aid, p in created]}


def _prospect_back_to_triaged(roots: cu.Roots, slug: str | None, today: date) -> None:
    if not slug:
        return
    try:
        path, meta, body = _load_prospect(roots, slug)
    except OutboxError:
        return
    if meta.get("status") == "drafted":
        meta["status"], meta["updated"] = "triaged", today
        cu.write_record(path, meta, body)


def apply_decisions(roots: cu.Roots, approval_path: Path, *, today: date | None = None) -> dict[str, Any]:
    today = today or jc.today()
    appr = cu.parse_approval(Path(approval_path))
    status = str(appr.meta.get("status") or "")
    report: dict[str, Any] = {"approval": appr.id, "status": status, "approved": [], "rejected": [], "expired": [],
                              "withdrawn": [], "refused": [], "problems": [], "past_expiry": False}
    expires = jc.parse_date(appr.meta.get("expires"))
    if status == "pending":
        report["past_expiry"] = bool(expires and today > expires)
        return report
    ids = [str(i.get("id")) for i in appr.items]
    if status in ("rejected", "expired", "withdrawn"):          # withdrawn: the requester retracted it
        for draft_id in ids:
            try:
                path, meta, body = load_draft(roots, draft_id)
            except OutboxError:
                continue
            if meta.get("status") != "pending" or meta.get("approval") != appr.id:
                continue
            meta["status"] = status
            report[status].append(draft_id)
            _prospect_back_to_triaged(roots, meta.get("prospect"), today)
            cu.write_record(path, meta, body)
        return report
    if status != "approved":
        report["problems"].append(f"unknown status {status!r}")
        return report
    problems = cu.approval_problems(appr, today)
    if problems:
        report["problems"] = problems
        return report
    for item in appr.items:
        draft_id = str(item.get("id"))
        try:
            path, meta, body = load_draft(roots, draft_id)
        except OutboxError as exc:
            report["refused"].append({"id": draft_id, "reason": str(exc)})
            continue
        if meta.get("status") != "pending" or meta.get("approval") != appr.id:
            continue                                          # already applied
        if draft_hash(roots, meta, body) != item.get("content_hash"):
            meta["checks"] = list(meta.get("checks") or []) + ["content hash changed after the approval request"]
            cu.write_record(path, meta, body)
            report["refused"].append({"id": draft_id, "reason": "content hash changed since the approval request "
                                                                "(body or attachment); request a new approval"})
            continue
        if appr.decisions.get(draft_id):
            meta["status"], meta["approved_at"] = "approved", today
            report["approved"].append(draft_id)
        else:
            meta["status"] = "rejected"
            report["rejected"].append(draft_id)
            _prospect_back_to_triaged(roots, meta.get("prospect"), today)
        cu.write_record(path, meta, body)
    return report


def mark_sent(roots: cu.Roots, draft_id: str, *, at: str | None = None, message_id: str | None = None,
              today: date | None = None) -> dict[str, Any]:
    today = today or jc.today()
    path, meta, body = load_draft(roots, draft_id)
    if meta.get("status") != "approved":
        raise OutboxError(f"{draft_id} is {meta.get('status')}; only approved drafts can be marked sent")
    if draft_hash(roots, meta, body) != meta.get("content_hash"):
        raise OutboxError(f"{draft_id} changed after approval; it cannot be recorded as the approved text")
    problems = send_problems(roots, meta, body)
    if problems:
        raise OutboxError(f"{draft_id} must not go out: " + "; ".join(problems) + " (rewrite it and file it for a new yes)")
    at_iso = cu.to_utc_iso(at) if at else jc.run_stamp(today, z=True)
    channel = meta["channel"]
    touch = None
    if meta.get("prospect") and channel in PERSON_CHANNELS:
        ppath, pmeta, pbody = _load_prospect(roots, meta["prospect"])
        event = {"key": draft_id, "at": at_iso, "direction": "out", "channel": channel, "source": "outbound",
                 "label": None, "kind": meta.get("kind"), "person": {"name": pmeta.get("name"), "emails": [],
                                                                        "linkedin_url": "", "company": None, "title": None},
                 "subject": meta.get("subject") or "", "summary": cu.one_line(body, 160), "text": "",
                 "thread_id": meta.get("thread_id"), "fingerprint": pr.fingerprint(body), "tags": []}
        pbody, touch = pr._apply(pmeta, pbody, event, today)
        pmeta.update(pr.score_prospect(pmeta, today))
        cu.write_record(ppath, pmeta, pbody)
        if touch:
            touch.update({"draft_path": f"outbox/{path.name}", "approval_id": meta.get("approval"), "source": "outbox",
                          "kind": meta.get("kind") or touch.get("kind")})
            jc.log_touch(touch, root=roots.js)
    meta.update({"status": "sent", "sent_at": at_iso, "message_id": message_id or meta.get("message_id")})
    cu.write_record(path, meta, body)
    approval = meta.get("approval")
    apath = roots.approvals / f"{approval}.md" if approval else None
    if apath and apath.is_file():
        cu.append_receipt(apath, f"{jc.run_stamp(today)} recorded as sent by outbox.py mark-sent: {draft_id} "
                                 f"({channel} to {meta.get('to')})")
    return {"id": draft_id, "status": "sent", "sent_at": at_iso, "touch_logged": bool(touch)}


# --------------------------------------------------------------------------- CLI
def _roots(args: argparse.Namespace) -> cu.Roots:
    js = Path(args.root) if args.root else jc.JS_ROOT
    return cu.Roots(js=js, os=Path(args.os_root) if args.os_root else (jc.os_root_for(js) if args.root else jc.OS_ROOT))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="job-search root (default: this department)")
    common.add_argument("--os-root", help="runtime root for work/approvals (default: derived)")
    common.add_argument("--today", help="override today's date (YYYY-MM-DD)")
    c = sub.add_parser("create", parents=[common], help="file the staged drafts of a batch and request approval")
    c.add_argument("--batch", required=True, help="state/drafts/<batch>/")
    c.add_argument("--requester", help="role:<name> (default from the batch prefix)")
    c.add_argument("--why", help="override the approval's why (default from the batch prefix: B- inbound replies, "
                   "F- follow-ups, P- outbound referral asks or connection notes, L- LinkedIn profile edits and posts)")
    c.add_argument("--premium", action="store_true")
    lt = sub.add_parser("lint", parents=[common], help="run create's checks on a staged batch; files nothing")
    lt.add_argument("--batch", required=True, help="state/drafts/<batch>/")
    lt.add_argument("--premium", action="store_true")
    d = sub.add_parser("draft", parents=[common])
    d.add_argument("--prospect")
    d.add_argument("--channel", required=True, choices=CHANNELS)
    d.add_argument("--body-file", required=True)
    d.add_argument("--subject")
    d.add_argument("--kind", default="reply", choices=KINDS)
    d.add_argument("--to")
    d.add_argument("--premium", action="store_true", help="LinkedIn Premium (300-char connection notes)")
    b = sub.add_parser("batch", parents=[common])
    b.add_argument("--ids", help="comma-separated draft ids")
    b.add_argument("--all-drafted", action="store_true")
    b.add_argument("--channel", choices=CHANNELS)
    b.add_argument("--why", help="override the approval's why (default derived from the drafts' batch, kind, channel)")
    b.add_argument("--ticket")
    b.add_argument("--requester", default=None,
                   help="role:<name> that asks (default role:messenger; role:legal-visa when every draft is official)")
    b.add_argument("--force", action="store_true", help="request approval despite lint issues (they stay visible)")
    b.add_argument("--premium", action="store_true")
    a = sub.add_parser("apply-decisions", parents=[common])
    a.add_argument("--approval")
    a.add_argument("--all", action="store_true", help="every approval file that references outbox drafts")
    m = sub.add_parser("mark-sent", parents=[common])
    m.add_argument("--id", required=True)
    m.add_argument("--at")
    m.add_argument("--message-id")
    cs = sub.add_parser("can-send", parents=[common], help="exit 0 when an approved draft may go out now, else 1")
    cs.add_argument("--id", required=True)
    li = sub.add_parser("list", parents=[common])
    li.add_argument("--status")
    args = ap.parse_args(argv)
    roots = _roots(args)
    today = jc.parse_date(args.today) if args.today else jc.today()
    try:
        if args.cmd == "create":
            print(json.dumps(create_batch(roots, args.batch, requested_by=args.requester, today=today,
                                          premium=args.premium, why=args.why), indent=2))
            return 0
        if args.cmd == "lint":
            issues = lint_batch(roots, args.batch, premium=args.premium, today=today)
            print(json.dumps(issues, indent=2))
            return 1 if issues else 0
        if args.cmd == "draft":
            path = create_draft(roots, prospect=args.prospect, channel=args.channel,
                                body=Path(args.body_file).read_text(encoding="utf-8"), subject=args.subject,
                                kind=args.kind, to=args.to, today=today, premium=args.premium)
            meta, _ = cu.read_record(path)
            print(json.dumps({"id": meta["id"], "path": str(path), "checks": meta["checks"]}, indent=2))
            return 0
        if args.cmd == "batch":
            ids = [x.strip() for x in (args.ids or "").split(",") if x.strip()]
            if args.all_drafted:
                ids += [m["id"] for m in list_drafts(roots, ("drafted",)) if not m.get("approval")
                        and (not args.channel or m["channel"] == args.channel) and m["id"] not in ids]
            created = request_approval(roots, ids, today=today, why=args.why, ticket=args.ticket, force=args.force,
                                       premium=args.premium, requested_by=args.requester or requester_for(roots, ids))
            print(json.dumps([{"approval": aid, "path": str(p), "items": len(cu.parse_approval(p).items)}
                              for aid, p in created], indent=2))
            return 0
        if args.cmd == "apply-decisions":
            paths = [Path(args.approval)] if args.approval else sorted(roots.approvals.glob("A-*.md")) if args.all else []
            reports = [apply_decisions(roots, p, today=today) for p in paths
                       if cu.parse_approval(p).meta.get("project") == "job-search" or args.approval]
            print(json.dumps(reports if len(reports) != 1 else reports[0], indent=2, default=str))
            return 2 if any(r["refused"] or r["problems"] for r in reports) else 0
        if args.cmd == "mark-sent":
            print(json.dumps(mark_sent(roots, args.id, at=args.at, message_id=args.message_id, today=today), indent=2))
            return 0
        if args.cmd == "can-send":
            _, meta, body = load_draft(roots, args.id)
            problems = ([] if meta.get("status") == "approved" else [f"status {meta.get('status')}, not approved"])
            problems += send_problems(roots, meta, body)
            print(json.dumps({"id": args.id, "can_send": not problems, "problems": problems}, indent=2))
            return 1 if problems else 0
        if args.cmd == "list":
            rows = list_drafts(roots, [x.strip() for x in args.status.split(",")] if args.status else None)
            print(cu.format_table([{k: r.get(k) for k in ("id", "prospect", "channel", "to", "status", "approval")}
                                   for r in rows], ["id", "prospect", "channel", "to", "status", "approval"]))
            return 0
    except OutboxError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
