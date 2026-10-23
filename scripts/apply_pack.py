#!/usr/bin/env python3
"""apply_pack.py - every answer an application form asks, copied from a record, one pack per application.

The submit runs on the owner's yes given in that session (charter §7): for mass applications one batch yes, recorded
in one approval file (scripts/kernel/approvals.py decide), lets the apply lane fill and submit every row it lists on
the employer's own site; a LinkedIn Easy Apply, Indeed Apply or ZipRecruiter 1-Click form stays the owner's click. This script
prepares every answer deterministically so each application takes minutes, is consistent and is truthful (owner:
roles/apply-assist.md; the mass loop: lanes/apply/AGENT.md and scripts/apply_batch.py, which picks the base page and
its order for an E2 row). It never submits. Nothing is guessed: a field no record answers says "ask the owner".

Subcommands (all take --root, the job-search folder, before the subcommand; default: this department):

  build <slug> [--today D] [--pdf classic|inline]
                               write applications/<slug>/apply-pack.md. --pdf picks the render to upload
                               (classic: the variant's meta.yaml PDF, the default; inline: inline/, dates after
                               each title, the stronger reading order for Oracle); the pack records it.
                               exit 0  READY: every gate holds; the pack is safe to fill a form from.
                               exit 1  BLOCKED: written, with the blockers at the top (for example
                                       "legal-visa answer missing", an ATS verdict that is not a current
                                       PASS, no clear visa check, already applied); or REFUSED: nothing
                                       written, because the resume variant is not editor-approved (review.md
                                       missing or its front matter not YAML (the line is named), not approved,
                                       OPEN findings in the verdict of record, hash mismatch, PDF changed after
                                       the render) or the application is unknown. The verdict of record is the
                                       front matter plus the current round's findings: a heading marked history,
                                       VOID, superseded or kept never counts, and when a heading names the
                                       front matter's round only that round's findings do ).
  lint <slug> [<slug> ...]     exit 1 on findings: held strings (profile/PROFILE-HOLDS.md), em-dashes, work
                               authorization answers that differ from legal/timers.md, a referenced file
                               that is missing, a blocker, a variant no longer approved, a pack that
                               differs from a fresh build (stale or hand-edited), a credential-like field
                               or value in state/accounts.yaml, credential-like text in the pack's record
                               tables. The held and em-dash rules govern the owner's text: the employer's own
                               posting text (company, title, location) is masked first.
  answers [--today D] [--json] the reusable answer bank (common screening questions), a source per answer.
  account --platform P [--tenant T] --company C --exists yes|no --email E [--created-on D] [--notes N]
                               record in state/accounts.yaml whether a portal account exists. Never a
                               password, PIN, one-time code, security answer, recovery code or key: notes and
                               company that look like one are refused, and the tenant must be a company slug.

Credentials: one detector, scripts/credential_check.py (find_credential), checks every free-text value this
script writes or checks: account notes and company, every string in state/accounts.yaml (build and lint),
every record value a pack or the answer bank copies (a hit is withheld, "withheld: looks like a
credential", and blocks the build naming the record), and lint's scan of a pack's record tables. Messages
name the record and the kind of hit, never the secret.

Where each field comes from (the pack names the source on every row):
  posting            jobs/<id>.md (company, title, apply_url, ats_vendor or the apply URL host, source, platform)
  contact block      profile/facts.yaml identity (name, job-search email, phone, location); links only when
                     confirmed and not held by an open conflict (the website stays empty while a conflict on it is open)
  work authorization legal/timers.md "## Answer scripts" (legal-visa), verbatim: pairs written as
                     "<question>" -> **<answer>** (or the arrow character), and bullets whose label says
                     Written or Spoken. Missing file or pair: "legal-visa answer missing", exit 1.
  start and notice   clock.yaml (earliest_start, notice_period) when it exists; notice falls back to
                     profile/private.yaml notice_period; else "ask the owner"
  salary, relocation profile/private.yaml (the owner's file, gitignored): salary_expectation, relocation,
  remote, contact    remote_preference, may_contact_current_employer; unset: "ask the owner"
  EEO                profile/private.yaml eeo.{gender, hispanic_latino, race, veteran, disability};
                     unset or "decline": "Decline to answer", shown as the owner's choice, never a guess
  education          profile/facts.yaml education: degree, major, school; the date is `conferred`, else the
                     entry's `resume_dates` verbatim once every conflict it names is ruled, else the end year
                     with the open conflict named in the source
  how did you hear   the job's platform or source
  resume             resumes/variants/<slug>/ (or its inline/ render): meta.yaml PDF + pdf_sha256, review.md
                     approved variant_hash,
                     ats-report.json verdict, cover.md and why-us.md when present (optional since unless the
                     application says cover_letter: required or why_us: required); work history from the approved resume.yaml
  page layout: the render folder's layout-report.json (scripts/page_layout_check.py) is a PASS for the
                     exact PDF shipped (name and sha256), and recruiter.md says `layout: PASS` (the company recruiter
                     Read page-1.png); otherwise REFUSED, so next_up never calls the page READY either
  visa gate          legal/checks/<company>.md verdict and e_verify
  fit                charter section 7: effort E3 and above (effort.py: a big or dream company, a warm path, a focused
                     or dream page) needs a scored fit (applications/<slug>/fit.md fit_score); E2 mass needs none
  AI policy          companies/<slug>.md or targets/companies.yaml `ai_policy` {binds, rule, url, quote, read_on}, read
                     before the essays (charter section 7): shown when the pack carries an essay or a policy is
                     recorded; binds yes with an agent-drafted cover or why-us blocks until `first_draft: owner`
  account            state/accounts.yaml (platform, tenant, account_exists, email_used, created_on, notes)

Dates the script generates are ISO months (2025-08), so no generated date can collide with a held string;
a ruled `resume_dates` is copied from the record as the owner ruled it (the held check still reads it).
Standard library plus PyYAML; no network.
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as acfg  # noqa: E402
import profile_titles as pt  # noqa: E402
from credential_check import find_credential  # noqa: E402
from js_common import JS_ROOT, cli_date, fm, normalize_company  # noqa: E402

ASK = "ask the owner"
DECLINE = "Decline to answer"
LEGAL_MISSING = "legal-visa answer missing"
PACK = "apply-pack.md"
LEGAL_FILE = "legal/timers.md"
LEGAL_SOURCE = "legal/timers.md ## Answer scripts"
AUTH_Q = "Are you legally authorized to work in the US?"
SPONSOR_Q = "Will you now or in the future require sponsorship?"
# Policy: the leading word each recorded legal answer must carry before a pack may be built ("" = no such rule).
AUTH_MUST_START = str(acfg.get("policy.apply.authorization_answer_starts_with", "") or "").lower()
SPONSOR_MUST_START = str(acfg.get("policy.apply.sponsorship_answer_starts_with", "") or "").lower()
REQUIRE_EVERIFY = bool(acfg.get("policy.apply.require_everify_employer", False))
EM_DASH = "\u2014"
NO_EM_DASH = bool(acfg.get("style.no_em_dash", False))        # the owner's style rule: no em-dash in a pack
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

APPLIED_STAGES = ("applied", "confirmation", "screen", "oa", "interviewing", "final", "offer", "accepted")
CLOSED_STAGES = ("rejected", "withdrawn", "ghosted")

EEO_FIELDS = (("gender", "Gender"), ("hispanic_latino", "Hispanic or Latino"), ("race", "Race"),
              ("veteran", "Veteran status"), ("disability", "Disability"))
PRIVATE_QUESTIONS = (("salary_expectation", "Desired salary"), ("relocation", "Willing to relocate?"),
                     ("remote_preference", "Remote, hybrid or onsite?"),
                     ("may_contact_current_employer", "May we contact your current employer?"))
CLOCK_START_KEYS = ("earliest_start", "earliest_start_date", "start.earliest", "availability.earliest_start")
CLOCK_NOTICE_KEYS = ("notice_period", "notice", "availability.notice_period", "notice.period")

ACADEMIC_RE = re.compile(r"\b(" + "|".join(["University", "Institute", "College", "School",
                         *(re.escape(str(o)) for o in acfg.get("policy.apply.academic_orgs", []) or [])]) + r")\b", re.I)
INTERN_RE = re.compile(r"\bintern(ship)?\b", re.I)
EVIDENCE_RE = re.compile(r"ev-[a-z0-9]+(?:-[a-z0-9]+)*")
DOCUMENT_EVIDENCE = ("ev-resume-", "ev-doc-", "ev-id-", "ev-web-", "ev-sop-", "ev-ja-")   # documents, not work
DEGREE_RANK = ((re.compile(r"\b(doctor|ph\.?\s?d)", re.I), 3, "Doctorate"),
               (re.compile(r"\bmaster", re.I), 2, "Master's degree"),
               (re.compile(r"\bbachelor", re.I), 1, "Bachelor's degree"))

ACCOUNT_KEYS = ("platform", "tenant", "company", "account_exists", "email_used", "created_on", "profile_filled_on",
                "notes")
CREDENTIAL_KEY_RE = re.compile(r"pass|pwd|secret|token|otp|pin|code|answer|recovery|security|credential|2fa|mfa",
                               re.I)
WITHHELD_CREDENTIAL = "withheld: looks like a credential"
NOTHING_TO_ASK = "- nothing: every field has a record"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

ACCOUNTS_HEADER = """\
# state/accounts.yaml: which job portals the owner has an account on, per platform or per company tenant.
# NEVER a password, PIN, one-time code, security answer or recovery code: only whether the account
# exists and which email it is registered to. Owner: apply-assist, through
#   python3 scripts/apply_pack.py account --platform <p> [--tenant <company_slug>] --company "<name>" \\
#       --exists yes|no --email <address> [--created-on YYYY-MM-DD] [--profile-filled-on YYYY-MM-DD]
#       [--notes "<portal quirk>"]
# Fields per entry: platform (workday, icims, taleo, successfactors, smartrecruiters, greenhouse, lever,
#   ashby, oracle, or any id in targets/platforms.yaml, platforms or presence_surfaces; else other); tenant (the
#   company slug for per-company portals such as Workday and iCIMS, null for a platform-wide account); company;
#   account_exists (true or false); email_used; created_on (YYYY-MM-DD or null); profile_filled_on (the day a
#   portal sitting filled the profile, YYYY-MM-DD, or null); notes (portal quirks only, never credentials).
#   scripts/apply_pack.py shows the entry in each pack.
"""

# --------------------------------------------------------------------------- portals and platforms
# General notes per portal: what the form asks and where in the pack the answer is. The live page wins.
PORTALS: dict[str, dict[str, Any]] = {
    "greenhouse": {
        "name": "Greenhouse", "account": "none", "address": False,
        "fields": [
            ("First name, Last name", "Contact block"),
            ("Email, Phone, Location (city)", "Contact block"),
            ("Resume/CV", "attach the approved PDF (Resume section); never paste resume text instead"),
            ("Cover letter", "attach or paste cover.md (the owner's choice)"),
            ("LinkedIn Profile, Website, GitHub", "Contact block (a withheld link stays empty)"),
            ("Employer questions: work authorization, sponsorship, salary, start date, how did you hear",
             "Work authorization and sponsorship; Screening answers"),
            ("Voluntary self-identification (US): gender, Hispanic or Latino, race, veteran, disability",
             "Voluntary self-identification (EEO)"),
        ],
        "quirks": [
            "Guest application on one page, no account. A MyGreenhouse autofill offer is optional and never needed.",
            "Employer questions differ per job and some are required: read every one; answer only from this "
            "pack; everything else goes to the owner.",
            "The owner clicks Submit Application; a confirmation page and usually an email from the company follow.",
        ],
    },
    "lever": {
        "name": "Lever", "account": "none", "address": False,
        "fields": [
            ("Resume/CV (upload first)", "the approved PDF; Lever parses it and prefills fields: check every "
                                         "prefilled value against the Contact block"),
            ("Full name (one field)", "Contact block: Full name"),
            ("Email, Phone, Current location", "Contact block"),
            ("Current company", "Screening answers: Current company and title"),
            ("Links: LinkedIn, GitHub, Portfolio, Other website", "Contact block"),
            ("Additional information (free text)", "cover.md when the form has no cover letter field"),
            ("Employer questions", "Work authorization and sponsorship; Screening answers"),
            ("US equal employment opportunity survey (voluntary)", "Voluntary self-identification (EEO)"),
        ],
        "quirks": [
            "Guest application on one page, no account; the name is a single Full name field.",
            "The resume parse can put the wrong text in a field: fix it from this pack before moving on.",
            "The owner clicks Submit application; a confirmation email usually follows.",
        ],
    },
    "ashby": {
        "name": "Ashby", "account": "none", "address": False,
        "fields": [
            ("Name, Email, Phone", "Contact block"),
            ("Resume (an Autofill from resume option can overwrite fields)",
             "the approved PDF; re-check every field it fills"),
            ("LinkedIn, Website, GitHub", "Contact block"),
            ("Location (autocomplete)", "Contact block: Location"),
            ("Employer questions", "Work authorization and sponsorship; Screening answers"),
            ("Voluntary EEO survey (US)", "Voluntary self-identification (EEO)"),
        ],
        "quirks": [
            "Guest application, no account; questions are set per job.",
            "The owner clicks Submit Application; a confirmation email usually follows.",
        ],
    },
    "workday": {
        "name": "Workday", "account": "per-company", "address": True,
        "fields": [
            ("Create Account or Sign In (email, password)",
             "The owner creates the account and types the password personally; apply-assist never sees it"),
            ("My Information: how did you hear, worked here before", "Screening answers"),
            ("My Information: legal name (first, middle, last)", "Contact block"),
            ("My Information: address", "not in records: the owner types it"),
            ("My Information: email, phone device type, country phone code, phone number",
             "Contact block (split phone fields)"),
            ("My Experience: work experience, education",
             "Work history and education; Workday's resume parse fills these: fix every mis-parsed entry"),
            ("My Experience: resume upload, websites, skills", "the approved PDF; Contact block links"),
            ("Application Questions", "Work authorization and sponsorship; Screening answers; anything else "
                                      "goes to the owner"),
            ("Voluntary Disclosures: gender, race or ethnicity, veteran status, terms",
             "Voluntary self-identification (EEO); the owner ticks the terms personally"),
            ("Self Identify: disability form (name, date, answer)",
             "EEO section: Disability; the owner enters their own name and date"),
            ("Review, Submit", "The owner"),
        ],
        "quirks": [
            "Each company runs its own Workday tenant, so each needs its own account (with email verification).",
            "Sessions time out: save each step. Date fields take MM/YYYY, so 2025-08 is typed 08/2025.",
            "Workday parses a DOCX best (resumes/templates/README.md), but apply-assist uploads only the approved "
            "PDF; uploading the DOCX from the same render instead is the owner's call and action.",
        ],
    },
    "oracle": {
        "name": "Oracle Recruiting Cloud", "account": "per-company", "address": True,
        "fields": [
            ("Email, then the terms or privacy consent", "Contact block: Email; the owner ticks the consent personally"),
            ("One-time verification code sent to that email", "The owner reads the code and types it; apply-assist "
                                                              "never reads his mail for it"),
            ("Resume autofill (upload to prefill the form)", "the approved PDF (Resume section), then review each "
                                                             "parsed Education and Experience field"),
            ("Contact information: name, email, phone, address", "Contact block; the address is not in records: "
                                                                 "The owner types it"),
            ("Education and Experience (prefilled from the resume)", "Work history and education: fix every "
                                                                     "mis-parsed entry"),
            ("Application questions: work authorization, sponsorship, how did you hear",
             "Work authorization and sponsorship; Screening answers; anything else goes to the owner"),
            ("Supporting documents: cover letter", "cover.md, if the form asks for one"),
            ("Diversity and disability disclosures (voluntary)", "Voluntary self-identification (EEO)"),
            ("E-signature (full name typed), Submit", "The owner"),
        ],
        "quirks": [
            "Each company runs its own Oracle site, so the candidate profile is per company. Some companies list jobs on "
            "their own careers site and run the application on a separate apply host (Oracle).",
            "Sign-in is usually the email plus a one-time code rather than a password; the owner types the code.",
            "The autofill parses the PDF into Education and Experience: review each parsed Education and Experience "
            "field before moving on. The inline-dates PDF (`build --pdf inline`) reads in a cleaner order.",
        ],
    },
    "smartrecruiters": {
        "name": "SmartRecruiters", "account": "optional", "address": False,
        "fields": [
            ("I'm interested (opens the form)", "no answer needed"),
            ("First name, Last name, Email, Confirm email, City, Phone", "Contact block"),
            ("Resume upload (parsed into experience and education)",
             "the approved PDF; check each parsed entry against Work history and education"),
            ("Experience and education entries", "Work history and education"),
            ("Web links (LinkedIn, website)", "Contact block"),
            ("Message to the hiring team", "why-us.md or cover.md (the owner's choice)"),
            ("Screening questions", "Work authorization and sponsorship; Screening answers"),
            ("Privacy consent checkbox", "The owner ticks it personally"),
        ],
        "quirks": [
            "Usually no account; an optional candidate profile may be offered: skip it unless the owner wants it.",
            "The owner clicks the final Submit; a confirmation email usually follows.",
        ],
    },
    "icims": {
        "name": "iCIMS", "account": "per-company", "address": True,
        "fields": [
            ("Email first, then Create Account (password)",
             "The owner creates the account and types the password personally; record it afterwards"),
            ("Contact information and address", "Contact block; the street address is the owner's to type"),
            ("Resume upload (parsed)", "the approved PDF; check the parsed entries"),
            ("Work history and education", "Work history and education"),
            ("Job-specific questions", "Work authorization and sponsorship; Screening answers"),
            ("EEO questions (voluntary)", "Voluntary self-identification (EEO)"),
            ("Electronic signature", "The owner types their own name"),
        ],
        "quirks": [
            "A candidate portal per company, several pages long.",
            "Some portals ask for a salary number: answer only from Screening answers, else the owner.",
        ],
    },
    "taleo": {
        "name": "Taleo", "account": "per-company", "address": True,
        "fields": [
            ("New user registration (username, password)", "The owner creates the account personally"),
            ("Personal information and address", "Contact block; the street address is the owner's"),
            ("Resume upload (parsed) and work history", "the approved PDF; Work history and education"),
            ("Prescreening questions", "Work authorization and sponsorship; Screening answers"),
            ("EEO questions (voluntary)", "Voluntary self-identification (EEO)"),
            ("Electronic signature", "The owner types their own name"),
        ],
        "quirks": ["An account per company and a long multi-page form: save as you go."],
    },
    "successfactors": {
        "name": "SuccessFactors", "account": "per-company", "address": True,
        "fields": [
            ("Create account (email, password)", "The owner creates the account personally"),
            ("Profile: contact information and address", "Contact block; the street address is the owner's"),
            ("Resume upload (parsed) and work history", "the approved PDF; Work history and education"),
            ("Application questions", "Work authorization and sponsorship; Screening answers"),
            ("EEO questions (voluntary) and data privacy consent", "EEO section; the owner gives consent personally"),
        ],
        "quirks": ["An account per company; the profile parse needs checking field by field."],
    },
}

PLATFORMS: dict[str, dict[str, Any]] = {
    "linkedin": {
        "name": "LinkedIn", "account": "platform", "address": False,
        "fields": [
            ("Apply on company website", "follow the apply URL to the company's portal and use that portal's notes"),
            ("Easy Apply", "not filled by apply-assist (AGENTS.md rule 3: no Easy Apply automation): the owner "
                           "applies personally with this pack open"),
        ],
        "quirks": [
            "Confidential search: no Open to Work, nothing public; no message to anyone without an approved draft.",
        ],
    },
    "wttj": {
        "name": "Welcome to the Jungle", "account": "platform", "address": False,
        "fields": [
            ("Apply on the company site", "the company's portal: use that portal's notes"),
            ("Apply on the platform", "The owner's platform profile; its questions from this pack or from the owner"),
        ],
        "quirks": ["Most postings send you to the company's own portal."],
    },
    "yc": {
        "name": "Y Combinator Work at a Startup", "account": "platform", "address": False,
        "fields": [
            ("Apply (sends the owner's platform profile and a short note to the founders)",
             "why-us.md as the draft note; the owner reviews it and sends it personally"),
        ],
        "quirks": ["The note is outward text in the owner's name: their words and their click."],
    },
    "glassdoor": {
        "name": "Glassdoor", "account": "platform", "address": False,
        "fields": [
            ("Apply on company site", "the company's portal: use that portal's notes"),
            ("Easy Apply on the platform", "a platform quick apply is a submit: the owner's own action"),
        ],
        "quirks": ["Prefer the company's own portal when the posting offers it."],
    },
    "ziprecruiter": {
        "name": "ZipRecruiter", "account": "platform", "address": False,
        "fields": [
            ("Apply on company site", "the company's portal: use that portal's notes"),
            ("1-Click or quick apply", "a platform quick apply is a submit: the owner's own action"),
        ],
        "quirks": ["Prefer the company's own portal when the posting offers it."],
    },
    "hiringcafe": {
        "name": "HiringCafe", "account": "optional", "address": False,
        "fields": [("Apply", "postings link out to the company's own portal: use that portal's notes")],
        "quirks": ["A search site; nothing is filled on the platform itself. An account is needed only for saved-search "
                   "alerts (targets/platforms.yaml alert_setup), never to apply."],
    },
    # the portals of research/portals/, their account type and what the research found
    "wellfound": {
        "name": "Wellfound", "account": "platform", "address": False,
        "fields": [("Apply on Wellfound", "The owner's Wellfound Jobs Profile and any note to the company: his words and his "
                                          "click"),
                   ("Apply on company site", "the company's portal: use that portal's notes")],
        "quirks": ["Founders and recruiters write from @wellfound.us; messages sit at wellfound.com/jobs/messages "
                   "(research/portals/wellfound.md)."],
    },
    "indeed": {
        "name": "Indeed", "account": "platform", "address": False,
        "fields": [("Apply on company site", "the company's portal: use that portal's notes"),
                   ("Indeed Apply on the platform", "a platform quick apply is a submit: the owner's own action")],
        "quirks": ["Keep the profile at \"Employers can't find you\" (the default) until the owner says the search is public; "
                   "employers write from indeed.com, indeedemail.com and indeedmail.com (research/portals/indeed.md)."],
    },
    "builtin": {
        "name": "Built In", "account": "none", "address": False,
        "fields": [("Apply", "the company's own portal: use that portal's notes")],
        "quirks": ["Do not create an account: an account lists the owner in an employer database by default, and its AI "
                   "Assistant can apply for him (research/portals/builtin.md)."],
    },
    "jobright": {
        "name": "Jobright", "account": "platform", "address": False,
        "fields": [("Apply", "the company's own portal from the posting: use that portal's notes")],
        "quirks": ["For its H1B sponsorship filter and recommendations only: the Jobright Agent stays off, and so do "
                   "Autofill and the Gmail connection; it submits applications for him if switched on "
                   "(research/portals/jobright.md)."],
    },
    "handshake": {
        "name": "Handshake (university career network)", "account": "platform", "address": False,
        "fields": [("Apply on Handshake", "The owner's Handshake profile; its questions from this pack or from the owner"),
                   ("Apply on company site", "the company's portal: use that portal's notes")],
        "quirks": ["Keep \"Make profile public and shareable outside of Handshake\" unchecked: public profiles are indexed "
                   "by Google; alumni access is requested through the school's career office (research/portals/handshake.md)."],
    },
    "himalayas": {
        "name": "Himalayas", "account": "optional", "address": False,
        "fields": [("Apply", "postings usually link to the company's own portal: use that portal's notes")],
        "quirks": ["A remote-jobs board; a platform profile is optional."],
    },
}

GENERIC_PORTAL: dict[str, Any] = {
    "name": "unknown portal", "account": "unknown", "address": False,
    "fields": [
        ("Contact fields", "Contact block"),
        ("Resume", "the approved PDF only"),
        ("Questions", "Work authorization and sponsorship; Screening answers; anything else goes to the owner"),
        ("Voluntary self-identification", "Voluntary self-identification (EEO)"),
        ("Attestations, signatures, consent boxes", "The owner"),
    ],
    "quirks": ["Read each page before filling it; stop and ask the owner at anything this pack does not cover."],
}

HOST_PORTAL = (
    (r"(^|\.)greenhouse\.io$", "greenhouse"), (r"(^|\.)lever\.co$", "lever"), (r"(^|\.)ashbyhq\.com$", "ashby"),
    (r"(^|\.)(myworkdayjobs|myworkdaysite|workday)\.com$", "workday"),
    (r"(^|\.)smartrecruiters\.com$", "smartrecruiters"), (r"(^|\.)icims\.com$", "icims"),
    (r"(^|\.)taleo\.net$", "taleo"), (r"(successfactors\.(com|eu)|sapsf\.com)$", "successfactors"),
    (r"(^|\.)linkedin\.com$", "linkedin"), (r"(^|\.)(welcometothejungle|otta)\.com$", "wttj"),
    (r"(^|\.)workatastartup\.com$", "yc"), (r"(^|\.)glassdoor\.com$", "glassdoor"),
    (r"(^|\.)ziprecruiter\.com$", "ziprecruiter"), (r"(^|\.)hiring\.cafe$", "hiringcafe"),
    (r"(^|\.)himalayas\.app$", "himalayas"), (r"(^|\.)wellfound\.com$", "wellfound"), (r"(^|\.)indeed\.com$", "indeed"),
    (r"(^|\.)builtin(sf|nyc|la|boston|chicago|seattle|austin|colorado)?\.com$", "builtin"), (r"(^|\.)jobright\.ai$", "jobright"),
    (r"(^|\.)joinhandshake\.com$", "handshake"),
    (r"(^|\.)oraclecloud\.com$", "oracle"),
    *((str(h["pattern"]), str(h["portal"])) for h in acfg.get("policy.apply.portal_hosts", []) or []
      if isinstance(h, dict) and h.get("pattern") and h.get("portal")),      # configured company career hosts
)

PLATFORM_ALIASES = {        # spellings seen in records -> targets/platforms.yaml ids (job_alerts.py)
    "linkedin-jobs": "linkedin", "welcome-to-the-jungle": "wttj", "welcometothejungle": "wttj", "otta": "wttj",
    "yc-work-at-a-startup": "yc", "work-at-a-startup": "yc", "workatastartup": "yc", "waas": "yc",
    "y-combinator": "yc", "ycombinator": "yc", "zip-recruiter": "ziprecruiter", "hiring-cafe": "hiringcafe",
    "himalayas-app": "himalayas", "angel.co": "wellfound", "angellist": "wellfound", "built-in": "builtin",
    "joinhandshake": "handshake",
}
BOARD_SOURCES = ("greenhouse", "lever", "ashby", "workday", "jibe", "sitemap",
                 "smartrecruiters", "icims", "taleo", "successfactors",
                 "company-ats", *(str(x) for x in acfg.get("policy.apply.company_board_sources", []) or []))
SOURCE_HEARD = {
    "adzuna": "Job board (Adzuna)",
    "vc-portfolio": "VC portfolio job board",
    "hn": "Hacker News (Who is hiring thread)",
    "prospect": "Recruiter or founder outreach (the application's prospects name the person)",
    "referral": "Employee referral (the application's prospects name the referrer)",
}
ACCOUNT_PLATFORMS = tuple(PORTALS) + tuple(PLATFORMS) + ("other",)
PDF_CHOICES = ("classic", "inline")
LAYOUT_REPORT = "layout-report.json"        # written by scripts/page_layout_check.py beside the PDF


class Refusal(Exception):
    """`build` refuses: nothing is written."""


# --------------------------------------------------------------------------- small helpers
def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def norm_hash(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.split()[0] if text else ""
    return text.split(":", 1)[1] if text.startswith("sha256:") else text


def one_line(value: Any) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()


def cell(value: Any) -> str:
    return one_line(value).replace("|", "\\|")


def blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def link_target(value: Any) -> str:
    m = re.match(r"^\[\[([^\]|#]+)", str(value or "").strip())
    return m.group(1).strip() if m else str(value or "").strip()


def norm_key(value: Any) -> str:
    return re.sub(r"[\s_.]+", "-", str(value or "").strip().lower()).strip("-")


def ym(value: Any) -> str | None:
    """An ISO month (2025-08), 'present', or None."""
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return f"{value.year:04d}-{value.month:02d}"
    text = str(value).strip()
    if text.lower() == "present":
        return "present"
    m = re.match(r"^(\d{4})-(\d{2})", text)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def months_between(start: str, end: str, today: date) -> set[tuple[int, int]]:
    y1, m1 = (int(x) for x in start.split("-"))
    y2, m2 = (today.year, today.month) if end == "present" else (int(x) for x in end.split("-"))
    out: set[tuple[int, int]] = set()
    y, m = y1, m1
    while (y, m) <= (y2, m2):
        out.add((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def full_months(start: str, end: str, today: date, last_day: date | None = None) -> set[tuple[int, int]]:
    """The months worked, counting full months only (forms never count a partial month, never
    rounded up): a role still running drops the current month until its last day, and a role whose last day is known
    (the clock's employment end) drops that month unless it ended on the month's last day. A YYYY-MM end with no day
    counts its month."""
    months = months_between(start, end, today)
    partial = last_day if last_day is not None else (today if end == "present" else None)
    if partial is not None and partial.day != calendar.monthrange(partial.year, partial.month)[1]:
        months.discard((partial.year, partial.month))
    return months


def span_words(months: int) -> str:
    """17 -> '1 year 5 months'; 8 -> '8 months'."""
    years, rest = divmod(months, 12)
    parts = [f"{years} year{'s' if years != 1 else ''}"] if years else []
    if rest or not years:
        parts.append(f"{rest} month{'s' if rest != 1 else ''}")
    return " ".join(parts)


def span(months: int) -> str:
    """64 -> '5 years 4 months (64 months)'; 8 -> '8 months'."""
    return span_words(months) + (f" ({months} months)" if months >= 12 else "")


def read_yaml(path: Path) -> tuple[Any, str]:
    if not path.is_file():
        return None, "absent"
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")), "ok"
    except yaml.YAMLError as exc:
        first = str(exc).strip().splitlines()[0] if str(exc).strip() else "parse error"
        return None, f"unreadable ({first})"


def dig(data: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    return data


def scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, date, datetime)) and not isinstance(value, bool) and not blank(value)


def rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def load_held(path: Path) -> list[str]:
    """Every exact held string in PROFILE-HOLDS.md (profile_titles.load_held)."""
    return pt.load_held(path)


POSTING_FIELDS = ("Company", "Title", "Apply URL", "Location", "Req id", "Posted")


def posting_values(text: str) -> list[str]:
    """The employer's own text as the pack renders it: the cells of the Posting table (company, title, apply
    URL, location, req id, posted date)."""
    values: list[str] = []
    in_posting = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_posting = line.strip() == "## Posting"
            continue
        if in_posting and line.startswith("| "):
            cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
            if len(cells) >= 2 and cells[0] in POSTING_FIELDS and len(cells[1]) >= 3:
                values += [cells[1], cells[1].replace("\\|", "|")]      # the cell form and the raw form (heading)
    return sorted(set(values), key=len, reverse=True)


def content_findings(text: str, held: list[str]) -> tuple[list[tuple[int, str]], list[int]]:
    """(held hits, em-dash lines) in a pack. The held list and the em-dash rule govern the owner's text, so the
    employer's posting text is masked first (a title such as "Deep Learning Engineer" or a location ending in
    the country name is the employer's wording, not a claim), and the frontmatter's company and title lines
    are skipped for the same reason. Everything else, the owner's answers included, is checked."""
    values = posting_values(text)
    lines = text.splitlines()
    front = bool(lines) and lines[0] == "---"
    hits: list[tuple[int, str]] = []
    dashes: list[int] = []
    for n, line in enumerate(lines, 1):
        if front and n > 1 and line == "---":
            front = False
            continue
        if front and re.match(r"^(company|title):", line):
            continue
        for value in values:
            line = line.replace(value, "<posting>")
        hits += [(n, h) for h in pt.held_in(line, held)]           # emphasis or spacing cannot split a hold
        if NO_EM_DASH and EM_DASH in line:
            dashes.append(n)
    return hits, dashes


def dump_pack(meta: dict[str, Any], body: str) -> str:
    """Frontmatter plus body; long values stay on one line so every posting value appears whole."""
    raw = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, default_flow_style=False, width=1_000_000)
    return f"---\n{raw}---\n\n{body}"


# --------------------------------------------------------------------------- legal-visa's answer scripts
@dataclass
class Legal:
    state: str                                     # ok | absent | no-section | unreadable
    pairs: list[tuple[str, str]] = field(default_factory=list)
    authorization: tuple[str, str] | None = None
    sponsorship: tuple[str, str] | None = None
    written: str | None = None
    spoken: str | None = None
    sha256: str | None = None


PAIR_RE = re.compile(r"\"([^\"\n]+)\"\s*(?:\u2192|->)\s*\*\*([^*\n]+)\*\*")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
QUOTES = ("\"", "\u201c", "\u201d")


def _answer_section(text: str) -> str | None:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if m and m.group(2).strip().lower() == "answer scripts":
            level, out = len(m.group(1)), []
            for nxt in lines[i + 1:]:
                h = HEADING_RE.match(nxt)
                if h and len(h.group(1)) <= level:
                    break
                out.append(nxt)
            return "\n".join(out)
    return None


def _blocks(section: str) -> list[tuple[str | None, str]]:
    """(sub-heading, text) for every bullet item or paragraph of the section; wrapped lines are joined with
    one space. Tables are left to _table_pairs."""
    blocks: list[tuple[str | None, str]] = []
    heading: str | None = None
    cur: list[Any] | None = None
    for raw in section.splitlines():
        h = HEADING_RE.match(raw)
        bullet = re.match(r"^ {0,3}[-*+]\s+(.*)$", raw)
        if h or not raw.strip() or raw.lstrip().startswith("|") or bullet:
            if cur is not None:
                blocks.append((cur[0], cur[1]))
                cur = None
            if h:
                heading = h.group(2).strip()
            elif bullet:
                cur = [heading, bullet.group(1).strip()]
            continue
        if cur is None:
            cur = [heading, raw.strip()]
        else:
            cur[1] += " " + raw.strip()
    if cur is not None:
        blocks.append((cur[0], cur[1]))
    return blocks


def _table_pairs(section: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    cols: tuple[int, int] | None = None
    for line in section.splitlines():
        if not line.strip().startswith("|"):
            cols = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cols is None:
            low = [c.lower() for c in cells]
            q = next((i for i, c in enumerate(low) if c.startswith("question")), None)
            a = next((i for i, c in enumerate(low) if c.startswith("answer")), None)
            cols = (q, a) if q is not None and a is not None else (-1, -1)
            continue
        if cols[0] < 0 or set("".join(cells)) <= set("-: "):
            continue
        if max(cols) < len(cells):
            question, answer = cells[cols[0]].strip("*\" "), cells[cols[1]].strip("* ")
            if question and answer:
                pairs.append((question, answer))
    return pairs


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] in QUOTES and text[-1] in QUOTES:
        return text[1:-1].strip()
    return text


def parse_legal(text: str) -> Legal:
    """legal-visa's answers, verbatim: every '"<question>" -> **<answer>**' pair (bullets or plain lines),
    a question/answer table, and the scripts in blocks labelled Written or Spoken (by the text before the
    first colon, or by the sub-heading above the block)."""
    section = _answer_section(text)
    if section is None:
        return Legal("no-section")
    pairs: list[tuple[str, str]] = []
    written = spoken = None
    for heading, block in _blocks(section):
        found = [(q.strip(), a.strip()) for q, a in PAIR_RE.findall(block)]
        pairs += [p for p in found if p not in pairs]
        if found:
            continue
        label, sep, rest = block.partition(":")
        if sep and len(label) <= 80 and re.search(r"written|spoken", label, re.I):
            script = _unquote(rest)
        elif heading:
            label, script = heading, _unquote(block)
        else:
            continue
        if "written" in label.lower() and written is None:
            written = script
        elif "spoken" in label.lower() and spoken is None:
            spoken = script
    pairs += [p for p in _table_pairs(section) if p not in pairs]
    auth = next((p for p in pairs if "sponsor" not in p[0].lower() and re.search(r"authori[sz]", p[0].lower())), None)
    spon = next((p for p in pairs if "sponsor" in p[0].lower()), None)
    digest = hashlib.sha256(section.strip().encode("utf-8")).hexdigest()
    return Legal("ok", pairs, auth, spon, written or None, spoken or None, digest)


def load_legal(root: Path) -> Legal:
    path = root / LEGAL_FILE
    if not path.is_file():
        return Legal("absent")
    try:
        return parse_legal(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return Legal("unreadable")


def legal_rows(legal: Legal, section: str) -> list[Row]:
    auth = legal.authorization or (AUTH_Q, LEGAL_MISSING)
    spon = legal.sponsorship or (SPONSOR_Q, LEGAL_MISSING)
    rows = [Row(section, auth[0], auth[1], LEGAL_SOURCE), Row(section, spon[0], spon[1], LEGAL_SOURCE)]
    rows += [Row(section, q, a, LEGAL_SOURCE) for q, a in legal.pairs if (q, a) not in (auth, spon)]
    return rows


def written_script(legal: Legal) -> str:
    """legal-visa's written script as a pack prints it (withheld when it looks like a credential)."""
    if not legal.written:
        return LEGAL_MISSING
    return WITHHELD_CREDENTIAL if find_credential(legal.written) else legal.written


def withhold_credentials(rows: list["Row"], blockers: list[str]) -> list["Row"]:
    """The rows with every credential-looking answer withheld; one blocker per record that holds one."""
    out = []
    for row in rows:
        reason = find_credential(row.answer)
        if reason:
            blockers.append(f"{row.source} holds text that looks like a credential ({reason}): remove it from the "
                            "record, then rebuild")
            row = Row(row.section, row.question, WITHHELD_CREDENTIAL, row.source)
        out.append(row)
    return out


def legal_blockers(legal: Legal) -> list[str]:
    out: list[str] = []
    if legal.state == "absent":
        return [f"{LEGAL_MISSING}: {LEGAL_FILE} not found (legal-visa writes it: visa-check, timers mode)"]
    if legal.state in ("no-section", "unreadable"):
        return [f"{LEGAL_MISSING}: {LEGAL_FILE} has no readable '## Answer scripts' section (legal-visa)"]
    if legal.authorization is None:
        out.append(f"{LEGAL_MISSING}: no work authorization question in {LEGAL_SOURCE} (legal-visa)")
    elif AUTH_MUST_START and not legal.authorization[1].lower().startswith(AUTH_MUST_START):
        out.append(f"the work authorization answer in {LEGAL_SOURCE} is not {AUTH_MUST_START.capitalize()}: legal-visa "
                   "and the owner confirm it before any form")
    if legal.sponsorship is None:
        out.append(f"{LEGAL_MISSING}: no sponsorship question in {LEGAL_SOURCE} (legal-visa)")
    elif SPONSOR_MUST_START and not legal.sponsorship[1].lower().startswith(SPONSOR_MUST_START):
        out.append(f"the sponsorship answer in {LEGAL_SOURCE} is not {SPONSOR_MUST_START.capitalize()} (policy): "
                   "escalate to legal-visa and the owner")
    return out


# --------------------------------------------------------------------------- the records a pack reads
@dataclass
class Row:
    section: str
    question: str
    answer: str
    source: str

    def md(self) -> str:
        return f"| {cell(self.question)} | {cell(self.answer)} | {cell(self.source)} |"


@dataclass
class Role:
    id: str
    title: str | None
    org: str
    start: str
    end: str
    months: set[tuple[int, int]]
    kind: str
    industry: bool
    cited: set[str]
    end_from_clock: bool = False


@dataclass
class Ctx:
    root: Path
    today: date
    facts: dict[str, Any]
    held: list[str]
    holds_ok: bool
    private: dict[str, Any]
    private_state: str
    clock: dict[str, Any]
    clock_state: str
    legal: Legal
    accounts: list[dict[str, Any]]
    accounts_state: str
    accounts_problems: list[str]
    used: set[str] = field(default_factory=set)
    registry: dict[str, dict[str, Any]] | None = None


def validate_accounts(data: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if data is None:
        return [], []
    if not isinstance(data, dict):
        return [], ["state/accounts.yaml is not a mapping"]
    problems = [f"state/accounts.yaml: credential-like top-level field '{k}': remove it (the registry never "
                "stores credentials)" for k in data if k not in ("schema_version", "updated", "accounts")
                and CREDENTIAL_KEY_RE.search(str(k))]
    entries = data.get("accounts") or []
    if not isinstance(entries, list):
        return [], problems + ["state/accounts.yaml: accounts is not a list"]
    good: list[dict[str, Any]] = []
    for i, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            problems.append(f"state/accounts.yaml: entry {i} is not a mapping")
            continue
        bad = False
        for key in entry:
            if key in ACCOUNT_KEYS:
                continue
            bad = True
            if CREDENTIAL_KEY_RE.search(str(key)):
                problems.append(f"state/accounts.yaml: credential-like field '{key}' in entry {i}: remove it "
                                "(the registry never stores credentials)")
            else:
                problems.append(f"state/accounts.yaml: unknown field '{key}' in entry {i}")
        for key in ACCOUNT_KEYS:
            reason = find_credential(entry.get(key))
            if reason:
                bad = True
                what = f"the notes of entry {i} look" if key == "notes" else f"field '{key}' of entry {i} looks"
                problems.append(f"state/accounts.yaml: {what} like a credential ({reason}): remove it (the "
                                "registry never stores credentials)")
        if not bad:
            good.append(entry)
    return good, problems


def load_ctx(root: Path, today: date) -> Ctx:
    facts, state = read_yaml(root / "profile" / "facts.yaml")
    if not isinstance(facts, dict):
        raise Refusal(f"profile/facts.yaml is {state if state != 'ok' else 'not a mapping'}")
    holds = root / "profile" / "PROFILE-HOLDS.md"
    private, private_state = read_yaml(root / "profile" / "private.yaml")
    if private_state == "ok" and private is not None and not isinstance(private, dict):
        private_state = "unreadable (not a mapping)"
    clock, clock_state = read_yaml(root / "clock.yaml")
    if clock_state == "ok" and clock is not None and not isinstance(clock, dict):
        clock_state = "unreadable (not a mapping)"
    acc_data, acc_state = read_yaml(root / "state" / "accounts.yaml")
    accounts, problems = validate_accounts(acc_data) if acc_state == "ok" else ([], [])
    if acc_state.startswith("unreadable"):
        problems = [f"state/accounts.yaml {acc_state}"]
    ctx = Ctx(root=root, today=today, facts=facts, held=load_held(holds), holds_ok=holds.is_file(),
              private=private if isinstance(private, dict) else {}, private_state=private_state,
              clock=clock if isinstance(clock, dict) else {}, clock_state=clock_state,
              legal=load_legal(root), accounts=accounts, accounts_state=acc_state, accounts_problems=problems)
    ctx.used.add("profile/facts.yaml")
    for relpath, ok in (("profile/PROFILE-HOLDS.md", ctx.holds_ok), (LEGAL_FILE, ctx.legal.state != "absent"),
                        ("profile/private.yaml", private_state == "ok"), ("clock.yaml", clock_state == "ok"),
                        ("state/accounts.yaml", acc_state == "ok")):
        if ok:
            ctx.used.add(relpath)
    return ctx


@dataclass
class Employment:
    """clock.yaml (scripts/clock.py): the current job's last working day and whether the owner confirmed it."""
    employer: str
    end: date
    confirmed: bool
    new_start: date | None


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value).strip()) else None
    except ValueError:
        return None


def clock_employment(ctx: Ctx) -> Employment | None:
    end = _as_date(ctx.clock.get("employment_end"))
    if end is None:
        return None
    return Employment(one_line(ctx.clock.get("employer")), end, ctx.clock.get("employment_end_confirmed") is True,
                      _as_date(ctx.clock.get("new_employment_start")))


def same_company(a: Any, b: Any) -> bool:
    x, y = normalize_company(a or ""), normalize_company(b or "")
    return bool(x and y) and (re.search(rf"\b{re.escape(x)}\b", y) is not None
                              or re.search(rf"\b{re.escape(y)}\b", x) is not None)


def ended_here(ctx: Ctx, org: Any) -> Employment | None:
    """The confirmed clock.yaml employment that has ended at this organization, else None."""
    emp = clock_employment(ctx)
    if emp and emp.confirmed and ctx.today > emp.end and same_company(emp.employer, org):
        return emp
    return None


def next_weekday(day: date) -> date:
    day = date.fromordinal(day.toordinal() + 1)
    while day.weekday() >= 5:
        day = date.fromordinal(day.toordinal() + 1)
    return day


def open_conflicts(facts: dict[str, Any]) -> set[str]:
    return {str(c.get("id")) for c in facts.get("conflicts") or []
            if isinstance(c, dict) and str(c.get("status") or "") == "awaiting-owner"}


def ruled_conflicts(facts: dict[str, Any]) -> set[str]:
    return {str(c.get("id")) for c in facts.get("conflicts") or []
            if isinstance(c, dict) and str(c.get("status") or "") == "ruled"}


def conflict_refs(entry: dict[str, Any]) -> list[str]:
    """The conflict ids a facts entry names in `conflict` or `conflicts`, sorted."""
    named = " ".join(one_line(entry.get(k)) for k in ("conflict", "conflicts"))
    return sorted(set(re.findall(r"\bC\d{2}\b", named)))


def safe_org(org: Any) -> str:
    text = re.sub(r"\s*\([^)]*\)", "", str(org or ""))
    return re.sub(r"\s+", " ", text).strip(" ,;")


def load_roles(ctx: Ctx) -> list[Role]:
    roles: list[Role] = []
    for entry in ctx.facts.get("employment") or []:
        if not isinstance(entry, dict):
            continue
        start, end = ym(entry.get("start")), ym(entry.get("end"))
        if not start or start == "present" or not end:
            continue
        try:
            title = pt.render_title(entry, ctx.held)
        except pt.UnrenderableTitle:
            title = None
        cited: set[str] = set()
        corpus = entry.get("corpus")
        if corpus:
            folder = (ctx.root / "profile" / str(corpus)).parent
            if folder.is_dir() and folder != ctx.root / "profile":
                for path in sorted(folder.rglob("*.md")):
                    cited |= set(EVIDENCE_RE.findall(path.read_text(encoding="utf-8", errors="replace")))
        cited = {e for e in cited if not e.startswith(DOCUMENT_EVIDENCE)}
        industry = (entry.get("kind") == "experience" and not ACADEMIC_RE.search(str(entry.get("org") or ""))
                    and not INTERN_RE.search(title or "") and not INTERN_RE.search(str(entry.get("title") or "")))
        # The role reads its real end outward ("present" only while it runs). The confirmed
        # clock.yaml last day also decides the month count: facts saying `present` or the clock's own month
        # (2026-10) stop at that last day, and its partial month never counts; before the last day
        # the role counts like a running one.
        emp = clock_employment(ctx)
        emp = emp if emp and emp.confirmed and same_company(emp.employer, entry.get("org")) else None
        counted, last_day, from_clock = end, None, False
        if emp and end in ("present", f"{emp.end.year:04d}-{emp.end.month:02d}"):
            if ctx.today > emp.end:
                from_clock = end == "present"
                end = counted = f"{emp.end.year:04d}-{emp.end.month:02d}"
                last_day = emp.end
            else:
                counted = "present"
        months = full_months(start, counted, ctx.today, last_day)
        roles.append(Role(str(entry.get("id") or ""), title, safe_org(entry.get("org")), start, end,
                          months, str(entry.get("kind") or ""), industry, cited,
                          end_from_clock=from_clock))
    return roles


def role_span(role: Role) -> str:
    return f"{role.org} {role.start} to {role.end}"   #: the real end, never "present" for an ended role


# --------------------------------------------------------------------------- answer rows (shared by pack and bank)
def private_row(ctx: Ctx, key: str, label: str, section: str) -> Row:
    value = ctx.private.get(key)
    if blank(value) or isinstance(value, (dict, list)):
        where = "unset" if ctx.private_state == "ok" else ("not found" if ctx.private_state == "absent"
                                                           else ctx.private_state)
        return Row(section, label, ASK, f"profile/private.yaml {key} {where}")
    return Row(section, label, one_line(value), f"profile/private.yaml {key}")


def start_row(ctx: Ctx, section: str) -> Row:
    label = "Earliest start date"
    for key in CLOCK_START_KEYS:
        value = dig(ctx.clock, key)
        if scalar(value):
            return Row(section, label, one_line(value), f"clock.yaml {key}")
    emp = clock_employment(ctx)
    if emp is None:
        where = "clock.yaml not found" if ctx.clock_state == "absent" else (
            "clock.yaml has no employment_end" if ctx.clock_state == "ok" else f"clock.yaml {ctx.clock_state}")
        return Row(section, label, ASK, where)
    end = emp.end.isoformat()
    if emp.new_start:
        return Row(section, label, ASK, f"clock.yaml new_employment_start {emp.new_start.isoformat()}: a new job is recorded")
    if ctx.today > emp.end:
        value = f"Immediately (from {next_weekday(date.fromordinal(ctx.today.toordinal() - 1)).isoformat()})"
        how = "has passed"
    else:
        value = next_weekday(emp.end).isoformat()
        how = "the first weekday after that last working day"
    if not emp.confirmed:
        return Row(section, label, f"{ASK} (if employment_end {end} holds: {value})",
                   "clock.yaml employment_end is provisional: employment_end_confirmed false")
    return Row(section, label, value, f"clock.yaml employment_end {end} (confirmed): {how}")


def notice_row(ctx: Ctx, section: str) -> Row:
    label = "Notice period"
    for key in CLOCK_NOTICE_KEYS:
        value = dig(ctx.clock, key)
        if scalar(value):
            return Row(section, label, one_line(value), f"clock.yaml {key}")
    emp = clock_employment(ctx)
    if emp and emp.confirmed and not emp.new_start:
        end = emp.end.isoformat()
        value = (f"None needed: the current job ends {end}" if ctx.today <= emp.end
                 else f"None: the last working day was {end}")
        return Row(section, label, value, f"clock.yaml employment_end {end} (confirmed)")
    value = ctx.private.get("notice_period")
    if scalar(value):
        return Row(section, label, one_line(value), "profile/private.yaml notice_period")
    if emp and not emp.new_start:
        end = emp.end.isoformat()
        return Row(section, label, f"{ASK} (none needed if employment_end {end} holds)",
                   "clock.yaml employment_end is provisional: employment_end_confirmed false")
    return Row(section, label, ASK, "no notice_period in clock.yaml or profile/private.yaml")


def availability_rows(ctx: Ctx, section: str) -> list[Row]:
    rows = [start_row(ctx, section), notice_row(ctx, section)]
    rows += [private_row(ctx, key, label, section) for key, label in PRIVATE_QUESTIONS]
    return rows


def eeo_rows(ctx: Ctx, section: str) -> list[Row]:
    eeo = ctx.private.get("eeo") if isinstance(ctx.private.get("eeo"), dict) else {}
    rows = []
    for key, label in EEO_FIELDS:
        value = eeo.get(key)
        if blank(value):
            rows.append(Row(section, label, DECLINE, f"default: profile/private.yaml eeo.{key} unset"))
        elif one_line(value).lower() in ("decline", "declined", "decline to answer"):
            rows.append(Row(section, label, DECLINE, f"profile/private.yaml eeo.{key}: decline"))
        else:
            rows.append(Row(section, label, one_line(value), f"profile/private.yaml eeo.{key}"))
    return rows


def _identity(ctx: Ctx) -> dict[str, Any]:
    ident = ctx.facts.get("identity")
    return ident if isinstance(ident, dict) else {}


def _value(node: Any) -> str:
    if isinstance(node, dict):
        node = node.get("value", node.get("address"))
    return one_line(node) if scalar(node) else ""


def contact_rows(ctx: Ctx, section: str) -> list[Row]:
    ident = _identity(ctx)
    full = one_line(ident.get("full_name"))
    rows = [Row(section, "Full name", full or ASK, "profile/facts.yaml identity.full_name")]
    legal_name = ctx.private.get("legal_name") if isinstance(ctx.private.get("legal_name"), dict) else {}
    if scalar(legal_name.get("first")) and scalar(legal_name.get("last")):
        parts = (one_line(legal_name.get("first")), one_line(legal_name.get("middle")) if scalar(
            legal_name.get("middle")) else "", one_line(legal_name.get("last")))
        source = "profile/private.yaml legal_name"
    else:
        words = full.split()
        parts = (words[0], " ".join(words[1:-1]), words[-1]) if len(words) >= 2 else (full, "", "")
        source = "derived from profile/facts.yaml identity.full_name (first, middle, last words)"
    for label, value in zip(("First name", "Middle name", "Last name"), parts):
        rows.append(Row(section, label, value or ("(none)" if label == "Middle name" else ASK), source))
    emails = ident.get("emails") if isinstance(ident.get("emails"), dict) else {}
    email = _value(emails.get("job_search_primary"))
    rows.append(Row(section, "Email", email or ASK, "profile/facts.yaml identity.emails.job_search_primary"))
    phone = _value(ident.get("phone"))
    rows.append(Row(section, "Phone", phone or ASK, "profile/facts.yaml identity.phone"))
    m = re.match(r"^\+(\d{1,3})\s*(.*\S)\s*$", phone)
    if m:
        rows.append(Row(section, "Phone (split fields)", f"country code +{m.group(1)}; number {m.group(2)}",
                        "derived from profile/facts.yaml identity.phone"))
    location = _value(ident.get("location"))
    rows.append(Row(section, "Location", location or ASK, "profile/facts.yaml identity.location"))
    links = ident.get("links") if isinstance(ident.get("links"), dict) else {}
    opened = open_conflicts(ctx.facts)
    for key, label in (("linkedin", "LinkedIn"), ("github", "GitHub"), ("website", "Website")):
        link = links.get(key)
        source = f"profile/facts.yaml identity.links.{key}"
        if not isinstance(link, dict) or not _value(link):
            rows.append(Row(section, label, "leave empty (no record)", source))
            continue
        refs = set(re.findall(r"\bC\d{2}\b", " ".join(one_line(link.get(k)) for k in ("note", "conflict"))))
        held_by = sorted(refs & opened)
        status = one_line(link.get("status"))
        if held_by:
            rows.append(Row(section, label, f"leave empty (withheld until the owner rules {', '.join(held_by)})", source))
        elif status != "confirmed":
            rows.append(Row(section, label, f"leave empty (status {status or 'unknown'})", source))
        else:
            value = _value(link)
            rows.append(Row(section, label, value if re.match(r"^https?://", value) else f"https://{value}", source))
    return rows


def education_rows(ctx: Ctx, section: str) -> list[Row]:
    ranked = []
    for i, entry in enumerate(ctx.facts.get("education") or []):
        if not isinstance(entry, dict):
            continue
        degree = one_line(entry.get("degree"))
        rank, label = next(((r, lab) for rx, r, lab in DEGREE_RANK if rx.search(degree)), (0, "Degree"))
        ranked.append((-rank, i, label, entry))
    ranked.sort(key=lambda x: (x[0], x[1]))
    ruled = ruled_conflicts(ctx.facts)
    rows: list[Row] = []
    for n, (_, _, label, entry) in enumerate(ranked):
        degree, major, school = (one_line(entry.get(k)) for k in ("degree", "major", "school"))
        source = f"profile/facts.yaml education {entry.get('id')}"
        refs = conflict_refs(entry)
        unsettled = [c for c in refs if c not in ruled]   # awaiting the owner, or an id facts.yaml does not list
        if entry.get("conferred"):
            when = f"conferred {one_line(entry.get('conferred'))}"
        elif scalar(entry.get("resume_dates")) and not unsettled:
            when = one_line(entry.get("resume_dates"))     # the ruled month form, verbatim
            source += " resume_dates" + (f" ({', '.join(refs)} ruled)" if refs else "")
        else:
            when = (ym(entry.get("end")) or "")[:4]
            if unsettled:
                source += f" (year only: the month form is open, {', '.join(unsettled)})"
        text = f"{degree} in {major}, {school}" + (f", {when}" if when else "")
        if n == 0:
            rows.append(Row(section, "Highest level of education", f"{label}: {text}", source))
        rows.append(Row(section, label, text, source))
        if scalar(entry.get("gpa")):
            rows.append(Row(section, f"GPA ({degree})", one_line(entry.get("gpa")), source))
    return rows


def experience_rows(ctx: Ctx, section: str, roles: list[Role]) -> list[Row]:
    industry = [r for r in roles if r.industry and r.title]
    months: set[tuple[int, int]] = set().union(*(r.months for r in industry)) if industry else set()
    answer = (f"{span(len(months))}: " + "; ".join(role_span(r) for r in industry)) if industry else ASK
    rows = [Row(section, "Years of professional experience", answer,
                "profile/facts.yaml employment: industry roles (not a university, institute, teaching or internship)")]
    #: an ended role is never "present" outward. A role is current only while
    # its end is "present" (load_roles turns "present" into the clock's end once the confirmed last day has passed).
    current = next((r for r in roles if r.end == "present" and r.kind == "experience" and r.title), None)
    if current:
        rows.append(Row(section, "Current company and title", f"{current.title}, {current.org} (since {current.start})",
                        f"profile/facts.yaml employment {current.id}"))
    else:
        last = max((r for r in roles if r.kind == "experience" and r.title), key=lambda r: (r.end, r.start),
                   default=None)
        if last:
            rows.append(Row(section, "Current company and title", not_current_answer(last.title, last.org, last.start,
                                                                                    last.end),
                            f"profile/facts.yaml employment {last.id} (ended {last.end}; never reported as "
                            f"the current employer)"))
    return rows


def not_current_answer(title: Any, org: Any, start: Any, end: Any) -> str:
    """The answer to 'current company' when the last role has ended: none, with the real dates."""
    return f"None: not currently employed. Most recent: {title}, {org} ({start} to {end})"


def role_rows(roles: list[Role], section: str) -> list[Row]:
    rows = []
    for r in roles:
        if not r.title:
            rows.append(Row(section, f"(role {r.id})", "withheld: no render-safe title", f"profile/facts.yaml employment {r.id}"))
            continue
        kind = r.kind + (", industry" if r.industry else "")
        rows.append(Row(section, f"{r.title}, {r.org}", f"{r.start} to {r.end} ({span_words(len(r.months))}; {kind})",
                        f"profile/facts.yaml employment {r.id}" + ("; end from clock.yaml employment_end (confirmed)"
                                                                    if r.end_from_clock else "")))
    return rows


def skill_rows(ctx: Ctx, roles: list[Role], section: str) -> list[Row]:
    data, _ = read_yaml(ctx.root / "profile" / "skills-taxonomy.yaml")
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, dict):
        return []
    ctx.used.add("profile/skills-taxonomy.yaml")
    rows = []
    for items in groups.values():
        for skill in items or []:
            if not isinstance(skill, dict) or skill.get("confirm") or skill.get("discretion"):
                continue
            level = one_line(skill.get("level"))
            if level not in ("expert", "strong"):
                continue
            name = skill_name(skill)
            evidence = {str(e) for e in skill.get("evidence") or []} - {""}
            evidence = {e for e in evidence if not e.startswith(DOCUMENT_EVIDENCE)}
            linked = [r for r in roles if r.title and evidence & r.cited]
            tail = f"{level}; last used {one_line(skill.get('last_used')) or 'unknown'}"
            if linked:
                months = set().union(*(r.months for r in linked))
                answer = f"{span(len(months))} in roles: " + "; ".join(role_span(r) for r in linked) + f" ({tail})"
            else:
                answer = f"no role cites it: projects only ({tail})"
            rows.append(Row(section, name, answer, "profile/skills-taxonomy.yaml; roles whose corpus cites its evidence"))
    return rows


SKILL_FIELDS = ("skill", "level", "last_used", "evidence", "lenses", "confirm", "note", "discretion")


def skill_name(entry: dict[str, Any]) -> str:
    """The skill as written. In a flow mapping an unquoted comma splits the name ('{skill: SQL (Postgres,
    SQLite, DuckDB), ...}' parses as skill 'SQL (Postgres' plus null keys 'SQLite' and 'DuckDB)'): rejoin the
    null keys that follow `skill`, in order, until the next known field."""
    parts = [one_line(entry.get("skill"))]
    seen = False
    for key, value in entry.items():
        if key == "skill":
            seen = True
            continue
        if not seen:
            continue
        if key in SKILL_FIELDS or value is not None:
            break
        parts.append(one_line(key))
    return ", ".join(p for p in parts if p)


HEARD_TABLE = (
    ("greenhouse, lever, ashby, workday, company-ats or another company board", "Company careers page"),
    ("platform linkedin, or source linkedin-alert", "LinkedIn"),
    ("platform wttj, or source wttj-alert", "Welcome to the Jungle"),
    ("platform yc, or source yc-alert", "Y Combinator Work at a Startup"),
    ("any other platform id (glassdoor, ziprecruiter, hiringcafe, himalayas, builtin, ...)",
     "the platform's name from targets/platforms.yaml"),
    ("adzuna", SOURCE_HEARD["adzuna"]), ("hn", SOURCE_HEARD["hn"]),
    ("prospect", SOURCE_HEARD["prospect"]), ("referral", SOURCE_HEARD["referral"]),
    ("manual or email-alert, with no platform", ASK),
)


def _host(url: Any) -> str:
    host = (urlparse(one_line(url)).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def platform_registry(ctx: Ctx) -> dict[str, dict[str, Any]]:
    """{id: {name, access, host}} from targets/platforms.yaml (platform-scout's registry), when it exists."""
    if ctx.registry is None:
        data, state = read_yaml(ctx.root / "targets" / "platforms.yaml")
        entries = data.get("platforms") if isinstance(data, dict) else None
        ctx.registry = {}
        for e in entries or []:
            if isinstance(e, dict) and e.get("id"):
                access = e.get("access")
                ctx.registry[norm_key(e["id"])] = {
                    "name": safe_org(e.get("name")) or norm_key(e["id"]),
                    "access": [norm_key(a) for a in (access if isinstance(access, list) else [access]) if a],
                    "host": _host(e.get("url")),
                }
    if ctx.registry:
        ctx.used.add("targets/platforms.yaml")
    return ctx.registry


def platform_name(ctx: Ctx, pid: str) -> tuple[str, str]:
    """(display name, where the name came from) for a platform id."""
    pid = PLATFORM_ALIASES.get(pid, pid)
    if pid == "company-ats":
        return "Company careers page", ""
    if pid in SOURCE_HEARD:
        return SOURCE_HEARD[pid], ""
    registry = platform_registry(ctx)
    if pid in registry:
        return registry[pid]["name"], " (name from targets/platforms.yaml)"
    if pid in PLATFORMS:
        return PLATFORMS[pid]["name"], ""
    return pid, " (id only: not in targets/platforms.yaml)"


def heard_row(ctx: Ctx, job: dict[str, Any], job_rel: str, section: str) -> Row:
    label = "How did you hear about us?"
    platform = norm_key(job.get("platform"))
    if platform:
        name, how = platform_name(ctx, platform)
        return Row(section, label, name, f"{job_rel} platform {platform}{how}")
    source = norm_key(job.get("source"))
    base = source[:-len("-alert")] if source.endswith("-alert") else source
    if base in BOARD_SOURCES:
        return Row(section, label, "Company careers page", f"{job_rel} source {source} (the company's own board)")
    if base in SOURCE_HEARD:
        return Row(section, label, SOURCE_HEARD[base], f"{job_rel} source {source}")
    if base and base != "email" and (PLATFORM_ALIASES.get(base, base) in PLATFORMS or base in platform_registry(ctx)):
        name, how = platform_name(ctx, base)
        return Row(section, label, name, f"{job_rel} source {source}{how}")
    return Row(section, label, ASK, f"{job_rel} source {source or 'missing'} names no place")


def worked_here_row(ctx: Ctx, company: str, section: str) -> Row:
    label = "Have you worked for this company before?"
    key = normalize_company(company)
    if not key:
        return Row(section, label, ASK, "the job names no company")
    hits = [str(e.get("id")) for e in ctx.facts.get("employment") or [] if isinstance(e, dict)
            and re.search(rf"\b{re.escape(key)}\b", normalize_company(e.get("org") or ""))]
    if hits:
        return Row(section, label, ASK, f"profile/facts.yaml employment {', '.join(hits)} mentions {company}")
    return Row(section, label, "No", f"profile/facts.yaml employment (no entry names {company})")


# --------------------------------------------------------------------------- the answer bank
def scrub(rows: list[Row], held: list[str]) -> list[Row]:
    out = []
    for row in rows:
        if pt.held_in(row.question, held):
            continue
        in_answer, in_source = pt.held_in(row.answer, held), pt.held_in(row.source, held)
        if in_answer or in_source:
            src = pt.held_sources(in_answer + in_source)
            row = Row(row.section, row.question, f"withheld: contains held text ({src})",
                      src if in_source else row.source)
        elif find_credential(row.answer):
            row = Row(row.section, row.question, f"{WITHHELD_CREDENTIAL} (remove it from the record)", row.source)
        out.append(row)
    return out


def answer_bank(ctx: Ctx) -> list[Row]:
    roles = load_roles(ctx)
    legal = ctx.legal
    rows = contact_rows(ctx, "Contact")
    rows += legal_rows(legal, "Work authorization and sponsorship")
    rows.append(Row("Work authorization and sponsorship", "Free-text visa explanation (written script)",
                    legal.written or LEGAL_MISSING, f"{LEGAL_SOURCE} (Written)"))
    rows.append(Row("Work authorization and sponsorship", "Recruiter call (spoken script)",
                    legal.spoken or LEGAL_MISSING, f"{LEGAL_SOURCE} (Spoken)"))
    rows += availability_rows(ctx, "Availability and preferences")
    rows += education_rows(ctx, "Education")
    rows += experience_rows(ctx, "Experience", roles)
    rows += role_rows(roles, "Roles")
    rows += skill_rows(ctx, roles, "Years with a skill")
    rows += eeo_rows(ctx, "Voluntary self-identification (EEO)")
    rows += [Row("How did you hear about us", f"job source {src}", answer, "jobs/<id>.md source or platform")
             for src, answer in HEARD_TABLE]
    return scrub(rows, ctx.held)


SECTION_NOTES = {
    "Work authorization and sponsorship": "legal-visa's scripts, copied verbatim. A form's rewording takes the "
        "same answer only when it asks the same thing; any other visa question: the written script if it fits, "
        "otherwise ask the owner.",
    "Experience": "Months count every calendar month from a role's start to its end (present = this month). "
        "Industry roles leave out universities, institutes, teaching and internships; say fewer when a question "
        "means full-time only.",
    "Years with a skill": "Expert and strong skills only. Months are the span of the roles whose corpus cites "
        "the skill's evidence: an upper bound on use, so round down and say fewer when the use was shorter.",
    "Voluntary self-identification (EEO)": "The owner's own choice for each row; unset keys default to declining.",
}


def render_answers(rows: list[Row], ctx: Ctx) -> str:
    lines = [f"# Answer bank ({ctx.today.isoformat()})", "",
             "Reusable answers for application forms, each copied from the record named in Source. "
             f"\"{ASK}\" means no record holds the answer. Rebuild: `python3 scripts/apply_pack.py answers`.", ""]
    if not ctx.holds_ok:
        lines += ["Warning: profile/PROFILE-HOLDS.md is missing, so held text could not be filtered.", ""]
    current = None
    for row in rows:
        if row.section != current:
            current = row.section
            lines += ["", f"## {current}", ""]
            if current in SECTION_NOTES:
                lines += [SECTION_NOTES[current], ""]
            lines += ["| Question | Answer | Source |", "|---|---|---|"]
        lines.append(row.md())
    return "\n".join(lines).replace("\n\n\n", "\n\n").strip() + "\n"


# --------------------------------------------------------------------------- the variant and its gates
@dataclass
class Variant:
    dir: Path
    rel: str
    resume_sha: str
    review: dict[str, Any]
    meta: dict[str, Any]
    pdf: Path
    pdf_rel: str
    pdf_sha: str
    docx: Path | None
    resume: dict[str, Any]
    ats_ok: bool = False
    ats_line: str = ""
    rdir: Path | None = None           # the render folder: the variant (classic) or its inline/


def variant_dir(root: Path, slug: str, app: dict[str, Any]) -> Path:
    raw = link_target(app.get("resume_variant"))
    if raw:
        path = Path(raw)
        if len(path.parts) == 1 and not path.is_absolute():        # '[[slug]]' or 'slug': a variant folder's name
            return root / "resumes" / "variants" / path.name
        if path.suffix:
            path = path.parent
        return path if path.is_absolute() else root / path
    return root / "resumes" / "variants" / slug


def _same_file(root: Path, source: Any, target: Path) -> bool:
    path = Path(str(source))
    candidates = [path] if path.is_absolute() else [root / path, JS_ROOT / path]
    return any(c.resolve() == target.resolve() for c in candidates)


def _dt(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text).strip().replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
HISTORY_MARK = re.compile(r"\bhistory\b|\bvoid\b|\bsuperseded\b|\(kept\b", re.I)
ROUND_NAMED = re.compile(r"\brounds?\s+(\d+)(?:\s*(?:to|-)\s*(\d+))?", re.I)


def current_round(value: Any) -> int | None:
    m = re.match(r"\s*(\d+)", str(value if value is not None else ""))
    return int(m.group(1)) if m else None


def _heading_state(title: str, current: int | None) -> str | None:
    """history (VOID, superseded, kept, history), current (names the current round), or None (neutral)."""
    if HISTORY_MARK.search(title):
        return "history"
    if current is not None and any(int(a) <= current <= int(b or a) for a, b in ROUND_NAMED.findall(title)):
        return "current"
    return None


def live_open_findings(body: str, current: int | None) -> int:
    """`state: OPEN` lines that belong to the verdict of record Each line takes the state of its nearest
    enclosing heading that is not neutral. When some heading names the current round, only those lines are live;
    otherwise every line outside a history section is. A history section never counts as live."""
    lines = body.splitlines()
    marks = [_heading_state(m.group(2), current) for m in (HEADING.match(l) for l in lines) if m]
    has_current = "current" in marks
    stack: list[tuple[int, str | None]] = []
    n = 0
    for line in lines:
        m = HEADING.match(line)
        if m:
            level = len(m.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, _heading_state(m.group(2), current)))
            continue
        if "state: OPEN" not in line:
            continue
        state = next((st for _, st in reversed(stack) if st), None)
        if state == "current" or (state is None and not has_current):
            n += 1
    return n


def _yaml_line(path: Path, exc: Exception) -> str:
    """A front matter YAML error as `at line N: <the line>` in file lines (the YAML error counts from the ---)."""
    m = re.search(r"line (\d+)", str(exc))
    if not m:
        return f"({one_line(exc)[:200]})"
    n = int(m.group(1)) + 1
    lines = path.read_text(encoding="utf-8").splitlines()
    return f"at line {n}: `{lines[n - 1].strip()}`" if 0 < n <= len(lines) else f"(line {n})"


def check_recruiter(vdir: Path, vrel: str, resume_sha: str) -> None:
    """The gate on the company recruiter: this company's senior recruiter APPROVEs the page before it reaches the owner.
    recruiter.md's front matter
    `verdict` is APPROVE on this resume's sha256 (`resume_sha256`); a "GAP: ask the owner" verdict passes only with
    `owner_decision:` (the owner's own words and date: send it anyway). A page with no recruiter.md is refused."""
    path = vdir / "recruiter.md"
    if not path.is_file():
        raise Refusal(f"no company-recruiter review: {vrel}/recruiter.md missing (run /page-review)")
    try:
        rec, _ = fm.load(path)
    except fm.FrontmatterError as exc:
        raise Refusal(f"{vrel}/recruiter.md front matter is not valid YAML {_yaml_line(path, exc)}") from None
    verdict = one_line(rec.get("verdict"))
    if norm_hash(rec.get("resume_sha256")) != resume_sha:
        raise Refusal(f"{vrel}/recruiter.md reviewed resume {(norm_hash(rec.get('resume_sha256')) or 'missing')[:12]}, "
                      f"not {resume_sha[:12]}: the company recruiter re-reads the current page")
    if re.fullmatch(r"APPROVED?\.?", verdict.strip(), re.I) or \
            (verdict.upper().startswith("GAP") and not blank(rec.get("owner_decision"))):
        return check_recruiter_layout(rec, vrel)       # only a literal APPROVE ships 
    if verdict.upper().startswith("GAP"):
        why = "the Recruiter puts the gap to the owner; the decision goes in owner_decision:"
    elif verdict.upper().startswith("REVISE"):
        why = "REVISE goes back to the tailor"
    else:       # "ADVANCE (narrow)", "APPROVE if ...": a condition the form may not meet is the Recruiter's to settle
        why = "anything but a literal APPROVE goes back to the Recruiter, never to the owner"
    raise Refusal(f"company recruiter's verdict is {verdict or 'missing'} in {vrel}/recruiter.md: " + why)


def check_recruiter_layout(rec: dict[str, Any], vrel: str) -> None:
    """The layout gate: the company recruiter Read the rendered page-1.png and judged its layout; a layout FAIL blocks even an
    APPROVE (and a GAP with the owner's decision: the decision is about content, never about a broken page)."""
    layout = one_line(rec.get("layout"))
    if not layout:
        raise Refusal(f"{vrel}/recruiter.md has no `layout:` verdict (the recruiter Reads page-1.png from "
                      "scripts/page_layout_check.py and writes `layout: PASS` or `layout: FAIL <what is wrong>`)")
    if not re.match(r"PASS\b", layout, re.I):
        raise Refusal(f"company recruiter's layout FAIL in {vrel}/recruiter.md: {layout[:200]}; "
                      "the page goes back to the tailor and the renderer")


def layout_refusal(report: Path, shown: str, pdf: Path, pdf_sha: str, pdf_rel: str,
                   out_rel: str | None = None) -> str | None:
    """Why this PDF may not ship on its layout, or None when `report` (page_layout_check.py's
    layout-report.json) is a PASS for exactly this PDF (its name and sha256). out_rel: the --out folder the
    report belongs in when it is not beside the PDF (the base's two PDFs share one folder)."""
    cmd = f"python3 scripts/page_layout_check.py {pdf_rel}" + (f" --out {out_rel}" if out_rel else "")
    rerun = f"re-run {cmd}"
    if not report.is_file():
        return f"page layout not checked: {shown} missing ({cmd})"
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return f"page layout report {shown} unreadable: {rerun}"
    if not isinstance(data, dict):
        return f"page layout report {shown} unreadable: {rerun}"
    named = Path(one_line(data.get("pdf"))).name
    if named != pdf.name:
        return f"page layout report {shown} is for {named or 'no PDF'}, not {pdf.name}: {rerun}"
    measured = norm_hash(data.get("pdf_sha256"))
    if measured != pdf_sha:
        return (f"page layout report {shown} measured a different render (sha256 {(measured or 'missing')[:12]}, "
                f"not {pdf_sha[:12]}): {rerun}")
    if data.get("verdict") != "PASS" or data.get("passed") is not True:
        failures = "; ".join(one_line(f) for f in data.get("failures") or []) or "no failure detail"
        return f"page layout FAIL in {shown}: {failures[:300]}"
    return None


def check_variant(ctx: Ctx, slug: str, app: dict[str, Any], pdf_choice: str = "classic") -> Variant:
    vdir = variant_dir(ctx.root, slug, app)
    vrel = rel(ctx.root, vdir)
    resume = vdir / "resume.yaml"
    if not resume.is_file():
        raise Refusal(f"variant not editor-approved: {vrel}/resume.yaml missing (resume-tailor)")
    resume_sha = sha256_file(resume)
    review_path = vdir / "review.md"
    if not review_path.is_file():
        raise Refusal(f"variant not editor-approved: {vrel}/review.md missing (resume-editor signs it)")
    try:
        review, review_body = fm.load(review_path)
    except fm.FrontmatterError as exc:
        raise Refusal(f"variant not editor-approved: {vrel}/review.md front matter is not valid YAML "
                      f"{_yaml_line(review_path, exc)} (resume-editor)") from None
    verdict = one_line(review.get("verdict"))
    if verdict != "approved":
        raise Refusal(f"variant not editor-approved: {vrel}/review.md verdict {verdict or 'missing'} (resume-editor)")
    cur = current_round(review.get("round"))
    open_n = live_open_findings(review_body, cur)
    if open_n:
        where = f" in round {cur}" if any(_heading_state(m.group(2), cur) == "current" for m in
                                          (HEADING.match(l) for l in review_body.splitlines()) if m) else ""
        raise Refusal(f"variant not editor-approved: {vrel}/review.md has {open_n} OPEN finding(s){where}")
    approved = norm_hash(review.get("variant_hash"))
    if approved != resume_sha:
        raise Refusal(f"variant not editor-approved: {vrel}/resume.yaml sha256 {resume_sha[:12]} differs from "
                      f"review.md variant_hash {(approved or 'missing')[:12]} (the resume changed after the review; "
                      "resume-editor re-reviews the new hash)")
    if blank(review.get("signed_at")):
        raise Refusal(f"variant not editor-approved: {vrel}/review.md is approved but unsigned (signed_at empty)")
    check_recruiter(vdir, vrel, resume_sha)
    rdir = vdir if pdf_choice == "classic" else vdir / "inline"      # --pdf classic|inline
    rrel = rel(ctx.root, rdir)
    meta, state = read_yaml(rdir / "meta.yaml")
    if not isinstance(meta, dict):
        how = "render the variant first (render_resume.py)" if pdf_choice == "classic" else \
            "no inline render: render it with render_resume.py --template inline"
        raise Refusal(f"{rrel}/meta.yaml {state}: {how}")
    if meta.get("status") != "ok":
        raise Refusal(f"{rrel}/meta.yaml status {meta.get('status')}: the last render failed (resume-tailor)")
    if meta.get("source") and not _same_file(ctx.root, meta["source"], resume):
        raise Refusal(f"{rrel}/meta.yaml source {meta['source']} is not this variant's resume.yaml: re-render")
    pdf_name = one_line(meta.get("pdf"))
    if not pdf_name or not (rdir / pdf_name).is_file():
        raise Refusal(f"{rrel}/meta.yaml names PDF {pdf_name or '(none)'} but it is missing: re-render")
    pdf = rdir / pdf_name
    pdf_sha = sha256_file(pdf)
    recorded = norm_hash(meta.get("pdf_sha256"))
    if recorded != pdf_sha:
        raise Refusal(f"{rrel}/{pdf_name} sha256 {pdf_sha[:12]} differs from meta.yaml pdf_sha256 "
                      f"{(recorded or 'missing')[:12]}: the PDF changed after the render; re-render and re-check")
    why = layout_refusal(rdir / LAYOUT_REPORT, f"{rrel}/{LAYOUT_REPORT}", pdf, pdf_sha, f"{rrel}/{pdf_name}")
    if why:
        raise Refusal(why)
    docx_name = one_line(meta.get("docx"))
    docx = rdir / docx_name if docx_name and (rdir / docx_name).is_file() else None
    resume_data, _ = read_yaml(resume)
    variant = Variant(vdir, vrel, resume_sha, review, meta, pdf, f"{rrel}/{pdf_name}", pdf_sha, docx,
                      resume_data if isinstance(resume_data, dict) else {}, rdir=rdir)
    ctx.used |= {f"{vrel}/resume.yaml", f"{vrel}/review.md", f"{rrel}/meta.yaml", f"{rrel}/{pdf_name}",
                 f"{rrel}/{LAYOUT_REPORT}"}
    if docx:
        ctx.used.add(f"{rrel}/{docx_name}")
    variant.ats_ok, variant.ats_line = ats_verdict(ctx, variant)
    return variant


def ats_verdict(ctx: Ctx, v: Variant) -> tuple[bool, str]:
    rdir = v.rdir or v.dir
    path = rdir / "ats-report.json"
    if not path.is_file():
        return False, "missing: run ats_check.py on this PDF (resume-tailor)"
    ctx.used.add(f"{rel(ctx.root, rdir)}/ats-report.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False, "ats-report.json unreadable: re-run ats_check.py"
    checked = one_line(data.get("checked_at"))
    report_pdf = Path(one_line(data.get("pdf"))).name
    built = one_line(v.meta.get("built_at"))
    if report_pdf != v.pdf.name:
        return False, f"for a different PDF ({report_pdf or 'none named'}): re-run ats_check.py on {v.pdf.name}"
    if data.get("passed") is not True:
        failures = "; ".join(one_line(f) for f in data.get("failures") or []) or "no failure detail"
        return False, f"FAIL (checked {checked}): {failures[:300]}"
    c, b = _dt(checked), _dt(built)
    if c and b and c < b:
        return False, f"PASS but older than the render (checked {checked}, built {built}): re-run ats_check.py"
    return True, f"PASS (checked {checked}, vendor {one_line(data.get('vendor')) or 'unknown'})"


AI_POLICY_BINDS = ("yes", "no")


def ai_policy(ctx: Ctx, company_slug: str) -> tuple[dict[str, Any] | None, str]:
    """The employer's candidate AI policy (charter section 7: read it before any essay) and where it came from:
    companies/<slug>.md frontmatter `ai_policy`, else targets/companies.yaml (the entry with this slug) `ai_policy`.
    The record is {binds: yes|no, rule, url, quote, read_on}; (None, where it was looked for) when none is recorded."""
    dossier = ctx.root / "companies" / f"{company_slug}.md"
    if company_slug and dossier.is_file():
        meta = fm.load(dossier)[0] or {}
        if isinstance(meta.get("ai_policy"), dict):
            ctx.used.add(f"companies/{company_slug}.md")
            return meta["ai_policy"], f"companies/{company_slug}.md ai_policy"
    targets = ctx.root / "targets" / "companies.yaml"
    if company_slug and targets.is_file():
        data, _ = read_yaml(targets)
        for entry in (data or {}).get("companies") or [] if isinstance(data, dict) else []:
            if isinstance(entry, dict) and norm_key(entry.get("slug")) == company_slug \
                    and isinstance(entry.get("ai_policy"), dict):
                ctx.used.add("targets/companies.yaml")
                return entry["ai_policy"], "targets/companies.yaml ai_policy"
    return None, f"targets/companies.yaml ai_policy (none recorded for {company_slug or 'this company'})"


def ai_policy_rows(policy: dict[str, Any] | None, where: str, essays: list[str], app: dict[str, Any]
                   ) -> tuple[list[str], list[str]]:
    """(the pack's "Candidate AI policy" lines, blockers). Shown whenever the pack carries an essay or a policy is
    recorded; a binding policy with an agent-drafted essay blocks until the owner's own first draft is on record
    (`first_draft: owner` in the application)."""
    if policy is None and not essays:
        return [], []
    blockers: list[str] = []
    lines = ["## Candidate AI policy (read before any essay; charter section 7)", "", "| Field | Value | Source |",
             "|---|---|---|"]
    if policy is None:
        lines.append(f"| Policy | not recorded: read the employer's candidate AI guidance before any essay is used, then "
                     f"record it | {where} |")
        return lines, blockers
    binds = one_line(policy.get("binds")).lower()
    lines += [f"| Binds essays | {cell(binds or 'unknown')} | {where} |",
              f"| Rule | {cell(policy.get('rule') or 'not recorded')} | {where} |",
              f"| Read | {cell(policy.get('url') or 'no url')} on {cell(policy.get('read_on') or 'unknown')} | {where} |"]
    if binds not in AI_POLICY_BINDS:
        blockers.append(f"{where}: binds is {binds or 'missing'!r}, not yes or no: company-researcher records it")
    elif binds == "yes" and essays and one_line(app.get("first_draft")).lower() != "owner":
        blockers.append(f"candidate AI policy binds ({one_line(policy.get('url'))}): {', '.join(essays)} are agent "
                        "drafts; the owner writes the first draft personally, the agent only edits, then the application "
                        "records `first_draft: owner`")
    return lines, blockers


def visa_gate(ctx: Ctx, company_slug: str) -> tuple[str, list[str]]:
    relp = f"legal/checks/{company_slug}.md"
    path = ctx.root / relp
    if not company_slug or not path.is_file():
        return f"missing: {relp}", [f"visa gate: {relp} missing (legal-visa runs visa-check company {company_slug})"]
    try:
        meta, _ = fm.load(path)
    except fm.FrontmatterError:
        return "unreadable", [f"visa gate: {relp} unreadable (legal-visa)"]
    ctx.used.add(relp)
    verdict = one_line(meta.get("verdict"))
    ev = meta.get("e_verify")
    ev = "yes" if ev is True else "no" if ev is False else (one_line(ev).lower() or "unknown")
    line = f"{verdict or 'no verdict'} (E-Verify {ev}; checked {one_line(meta.get('checked_at')) or 'unknown'})"
    blockers = []
    if verdict == "blocked":
        blockers.append(f"visa gate: {relp} verdict blocked (legal-visa; only the owner rules on it)")
    elif verdict not in ("clear", "clear-with-questions"):
        blockers.append(f"visa gate: {relp} has no clear verdict ({verdict or 'missing'})")
    if ev == "no" and REQUIRE_EVERIFY:
        blockers.append(f"visa gate: {relp} says e_verify no (policy: the employer must be an E-Verify employer)")
    if verdict == "clear-with-questions":
        line += ": read its questions before the first recruiter call"
    return line, blockers


MODE_WORDS = {"remote": r"\bremote\b", "hybrid": r"\bhybrid\b",
              "onsite": r"\bon[-\s]?site\b|\bin[-\s](?:office|person)\b"}


def location_cell(location: Any, remote: Any) -> str:
    """The Posting table's Location: the posting's own words, plus the work mode in brackets only when those
    words do not already say it ("Springfield, ST (hybrid) or Remote (US)" stays as it is)."""
    place = one_line(location) or "not stated"
    mode = one_line(remote).lower() if scalar(remote) else ""
    if not mode:
        return cell(place)
    if mode == "unknown":
        return cell(f"{place} (work mode unknown)")
    if location and re.search(MODE_WORDS.get(mode, rf"\b{re.escape(mode)}\b"), place, re.I):
        return cell(place)
    return cell(f"{place} ({mode})")


def portal_for(ctx: Ctx, job: dict[str, Any], job_rel: str) -> tuple[str, str]:
    vendor = norm_key(job.get("ats_vendor"))
    if vendor in PORTALS:
        return vendor, f"{job_rel} ats_vendor"
    host = (urlparse(one_line(job.get("apply_url"))).hostname or "").lower()
    for pattern, key in HOST_PORTAL:
        if host and re.search(pattern, host):
            return key, f"the apply URL host {host}"
    bare = host[4:] if host.startswith("www.") else host
    for pid, entry in platform_registry(ctx).items() if bare else ():
        if entry["host"] and (bare == entry["host"] or bare.endswith("." + entry["host"])):
            return pid, f"the apply URL host {host} (targets/platforms.yaml {pid})"
    return "unknown", f"no known ats_vendor and apply URL host {host or '(none)'}"


def portal_spec(key: str, ctx: Ctx | None = None) -> dict[str, Any]:
    if key in PORTALS or key in PLATFORMS:
        return PORTALS.get(key) or PLATFORMS[key]
    entry = platform_registry(ctx).get(key) if ctx is not None else None
    if entry:
        return {**GENERIC_PORTAL, "name": entry["name"], "account": "platform"}
    return GENERIC_PORTAL


def browser_rule(ctx: Ctx, portal: str) -> tuple[bool, str]:
    """AGENTS.md rule 3 for this portal: may apply-assist fill the form in the owner's browser?"""
    entry = platform_registry(ctx).get(portal) if portal not in PORTALS else None
    name = portal_spec(portal, ctx)["name"]
    if entry and "manual" in entry["access"] and "browser" not in entry["access"]:
        return False, (f"Browser assistance: not allowed. {name} is access manual in targets/platforms.yaml (its "
                       "terms name AI agents): the owner fills this form personally with the pack open; apply-assist only "
                       "answers his questions from the pack.")
    if portal == "linkedin":
        return True, ("Browser assistance: reading only, on pages the owner opens (AGENTS.md rule 3); Easy Apply is "
                      "The owner's own action, never filled by an agent.")
    return True, ("Browser assistance: allowed only on the application pages the owner opens, with the owner present "
                  "(AGENTS.md rule 3); the owner signs in and clicks Apply and Submit personally.")


def portal_notes(key: str, spec: dict[str, Any] | None = None) -> str:
    spec = spec or portal_spec(key)
    lines = [f"## Portal notes: {spec['name']}", "",
             "General notes kept in scripts/apply_pack.py; the live page wins when it differs.", "",
             "| What the form asks | Where the answer is |", "|---|---|"]
    lines += [f"| {cell(q)} | {cell(a)} |" for q, a in spec["fields"]]
    lines += [""] + [f"- {q}" for q in spec["quirks"]]
    return "\n".join(lines)


def account_block(ctx: Ctx, portal: str, company_slug: str, company: str, email: str) -> list[str]:
    spec = portal_spec(portal, ctx)
    policy = spec["account"]
    tenant = company_slug if policy == "per-company" else None
    matches = [a for a in ctx.accounts if norm_key(a.get("platform")) == portal
               and (tenant is None or norm_key(a.get("tenant")) == tenant)]
    if policy == "per-company":
        matches = [a for a in matches if norm_key(a.get("tenant")) == tenant]
    lines = []
    text = {
        "none": f"No account needed: {spec['name']} takes guest applications.",
        "optional": f"Usually no account needed; {spec['name']} may offer an optional profile.",
        "platform": f"{spec['name']} uses the owner's own platform account.",
        "per-company": f"{spec['name']} needs an account for each company. The owner creates it and types the password "
                       "himself; apply-assist never sees, stores or enters a password.",
        "unknown": "Unknown whether an account is needed: read the first page, then ask the owner.",
    }[policy]
    lines.append(text)
    if matches:
        lines += ["", "| Platform | Tenant | Account exists | Email used | Created on | Notes |", "|---|---|---|---|---|---|"]
        for a in matches:
            exists = "yes" if a.get("account_exists") is True else "no" if a.get("account_exists") is False else "unknown"
            lines.append(f"| {cell(a.get('platform'))} | {cell(a.get('tenant') or '-')} | {exists} | "
                         f"{cell(a.get('email_used'))} | {cell(a.get('created_on') or '-')} | {cell(a.get('notes'))} |")
    elif policy in ("per-company", "platform"):
        where = f"{portal}/{tenant}" if tenant else portal
        lines += ["", f"state/accounts.yaml has no record for {where}. After the owner has an account, record it: "
                      f"`python3 scripts/apply_pack.py account --platform {portal}"
                      + (f" --tenant {tenant}" if tenant else "")
                      + f" --company \"{company}\" --exists yes --email {email or '<email>'} "
                        f"--created-on {ctx.today.isoformat()}`"]
    return lines


# --------------------------------------------------------------------------- build
def load_application(root: Path, slug: str) -> tuple[dict[str, Any], str, dict[str, Any], str]:
    if not SLUG_RE.match(slug or ""):
        raise Refusal(f"'{slug}' is not an application slug")
    app_path = root / "applications" / slug / "application.md"
    if not app_path.is_file():
        raise Refusal(f"no application {slug}: applications/{slug}/application.md not found")
    app, _ = fm.load(app_path)
    job_id = link_target(app.get("job"))
    if not job_id or not SLUG_RE.match(job_id):
        raise Refusal(f"applications/{slug}/application.md has no job link")
    job_rel = f"jobs/{job_id}.md"
    if not (root / job_rel).is_file():
        raise Refusal(f"job {job_id} not found ({job_rel})")
    job, _ = fm.load(root / job_rel)
    return app, job_id, job, job_rel


def fit_score_of(root: Path, slug: str, job: dict[str, Any]) -> float | None:
    """The scored fit: applications/<slug>/fit.md `fit_score`, else the job's `fit_score`; None when unscored."""
    meta: dict[str, Any] = {}
    path = root / "applications" / slug / "fit.md"
    if path.is_file():
        try:
            meta, _ = fm.load(path)
        except fm.FrontmatterError:
            meta = {}
    for value in ((meta or {}).get("fit_score"), job.get("fit_score")):
        if isinstance(value, bool):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def fit_blocker(root: Path, slug: str, app: dict[str, Any], job: dict[str, Any], company_slug: str) -> str | None:
    """Charter section 7: an E2 mass role (the base fits) needs no fit.md; E3 and above (a big or dream company, a
    warm path, a focused or dream page) apply only with a scored fit. effort.py decides the level."""
    if fit_score_of(root, slug, job) is not None:
        return None
    import effort as ef
    from fetch_ats import load_companies
    company = next((c for c in load_companies(root) if norm_key(c.get("slug")) == company_slug), None)
    warm = one_line(app.get("path")) == "person" or not blank(app.get("via_prospect")) or bool(app.get("prospects"))
    heavy = one_line(app.get("tier")).lower() in ("focused", "dream")
    level, why = ef.effort(None, tier=(company or {}).get("tier"), warm=warm, big=ef.is_big(company),
                           heavy_alignment=heavy, gate=job.get("gate"))
    if level in ("E1", "E2"):
        return None
    return (f"effort {level} ({why}) needs a scored fit: applications/{slug}/fit.md has no fit_score (charter "
            "section 7: the fit-scorer writes fit.md for E3 and above; /score-fit)")


def _words(path: Path) -> int:
    return len(path.read_text(encoding="utf-8", errors="replace").split())


def build_pack(root: Path, slug: str, today: date, pdf_choice: str = "classic") -> tuple[str, dict[str, Any]]:
    """The pack text and its frontmatter. Raises Refusal when nothing may be written."""
    if pdf_choice not in PDF_CHOICES:
        raise Refusal(f"--pdf {pdf_choice!r}: one of {', '.join(PDF_CHOICES)}")
    ctx = load_ctx(root, today)
    app, job_id, job, job_rel = load_application(root, slug)
    ctx.used |= {f"applications/{slug}/application.md", job_rel}
    v = check_variant(ctx, slug, app, pdf_choice)
    rrel = rel(root, v.rdir or v.dir)
    pdf_from = f"{rrel}/meta.yaml pdf" + ("" if pdf_choice == "classic" else f" (--pdf {pdf_choice})")
    company = one_line(job.get("company") or app.get("company"))[:120]
    title = one_line(job.get("title") or app.get("title"))[:160]
    company_slug = norm_key(app.get("company_slug") or job.get("company_slug"))
    blockers: list[str] = []

    if not ctx.holds_ok:
        blockers.append("profile/PROFILE-HOLDS.md missing: the held-text check cannot run")
    blockers += legal_blockers(ctx.legal)
    if ctx.private_state not in ("ok", "absent"):
        blockers.append(f"profile/private.yaml {ctx.private_state}: fix it (missing is not empty)")
    if ctx.clock_state not in ("ok", "absent"):
        blockers.append(f"clock.yaml {ctx.clock_state}")
    blockers += ctx.accounts_problems
    stage = one_line(app.get("stage"))
    if stage in APPLIED_STAGES:
        blockers.append(f"application stage is {stage}: already applied; never apply twice (tracker-ops)")
    elif stage in CLOSED_STAGES:
        blockers.append(f"application stage is {stage}: the application is closed")
    elif stage == "on_hold":
        blockers.append("application stage is on_hold: the owner resumes it first")
    fit_block = fit_blocker(root, slug, app, job, company_slug)
    if fit_block:
        blockers.append(fit_block)
    visa_line, visa_blockers = visa_gate(ctx, company_slug)
    blockers += visa_blockers
    if not v.ats_ok:
        blockers.append(f"ATS check is not a current PASS for {v.pdf.name}: {v.ats_line}")
    cover_rel = link_target(app.get("cover_variant")) or f"{v.rel}/cover.md"
    files_rows = []
    for label, relp, owner, field in (("Cover letter", cover_rel, "resume-tailor", "cover_letter"),
                                      ("Why us", f"{v.rel}/why-us.md", "resume-tailor", "why_us")):
        path = root / relp
        required = one_line(app.get(field)).lower() == "required"
        if path.is_file():
            ctx.used.add(relp)
            files_rows.append(f"| {label} | {relp} ({_words(path)} words) | {owner}; reviewed with the variant |")
        elif required:
            files_rows.append(f"| {label} | missing: the form requires it | {owner} writes it |")
            blockers.append(f"{relp} missing and required ({field}: required in the application; {owner} writes it, "
                            "the editor reviews it with the variant)")
        else:
            files_rows.append(f"| {label} | not included (only when the role asks; set `{field}: required` in the "
                              f"application to require it) | none |")

    essays = [relp for relp in (cover_rel, f"{v.rel}/why-us.md") if (root / relp).is_file()]
    policy, policy_where = ai_policy(ctx, company_slug)
    policy_lines, policy_blockers = ai_policy_rows(policy, policy_where, essays, app)
    blockers += policy_blockers
    portal, portal_how = portal_for(ctx, job, job_rel)
    spec = portal_spec(portal, ctx)
    browser_ok, browser_line = browser_rule(ctx, portal)
    contact = contact_rows(ctx, "Contact block")
    email = next((r.answer for r in contact if r.question == "Email"), "")
    legal = legal_rows(ctx.legal, "Work authorization and sponsorship")
    roles = load_roles(ctx)
    screening = [start_row(ctx, "s"), notice_row(ctx, "s")]
    screening += [private_row(ctx, key, label, "s") for key, label in PRIVATE_QUESTIONS]
    screening += [heard_row(ctx, job, job_rel, "s"), worked_here_row(ctx, company, "s")]
    screening += [r for r in education_rows(ctx, "s") if r.question == "Highest level of education"]
    screening += [r for r in experience_rows(ctx, "s", roles) if r.question == "Years of professional experience"]
    experience = ((v.resume.get("cv") or {}).get("sections") or {}).get("Experience") or []
    education = ((v.resume.get("cv") or {}).get("sections") or {}).get("Education") or []
    current = next((e for e in experience if isinstance(e, dict) and ym(e.get("end_date")) == "present"), None)
    ended = ended_here(ctx, strip_comments(current.get("company"))) if current else None
    #: after the last day the role is never the current employer and never
    # "Present" outward; a resume still saying Present is stale (its owner rewrites it to the real end), not a blocker.
    if current and not ended:
        screening.append(Row("s", "Current company and title",
                             f"{strip_comments(current.get('position'))}, {strip_comments(current.get('company'))} "
                             f"(since {ym(current.get('start_date'))})", f"{v.rel}/resume.yaml Experience (approved)"))
    else:
        last = current or next((e for e in experience if isinstance(e, dict) and ym(e.get("start_date"))), None)
        if last:
            last_end = f"{ended.end.year:04d}-{ended.end.month:02d}" if ended else ym(last.get("end_date"))
            stale = (" (the resume still says Present: stale after the last day)"
                     if ym(last.get("end_date")) == "present" else "")
            screening.append(Row("s", "Current company and title",
                                 not_current_answer(strip_comments(last.get("position")), strip_comments(last.get("company")),
                                                    ym(last.get("start_date")), last_end),
                                 f"{v.rel}/resume.yaml Experience (approved); an ended role is not the current "
                                 f"employer{stale}"))
    eeo = eeo_rows(ctx, "EEO")
    contact, legal, screening, eeo = (withhold_credentials(rows, blockers) for rows in (contact, legal, screening, eeo))
    if ctx.legal.written and written_script(ctx.legal) == WITHHELD_CREDENTIAL:
        blockers.append(f"{LEGAL_SOURCE} (Written) holds text that looks like a credential "
                        f"({find_credential(ctx.legal.written)}): legal-visa removes it, then rebuild")
    history_rows = resume_rows(experience, ("position", "company", "location"), f"{v.rel}/resume.yaml Experience",
                               blockers)
    school_rows = resume_rows(education, ("degree", "area", "institution", "location"),
                              f"{v.rel}/resume.yaml Education", blockers)
    asks = [r for r in contact + screening if r.answer.startswith(ASK)]

    def render(blockers: list[str]) -> str:
        status = "READY" if not blockers else f"BLOCKED ({len(blockers)} blocker{'s' if len(blockers) != 1 else ''})"
        out = [f"# Apply pack: {title} at {company}", "",
               f"Built {today.isoformat()} by scripts/apply_pack.py from the records named in each Source column. "
               f"Nothing here is a guess: a field no record answers says \"{ASK}\". The owner clicks Submit personally.", "",
               f"Status: **{status}**. Check before use: `python3 scripts/apply_pack.py lint {slug}`", "",
               "## Blockers", ""]
        out += [f"- {cell(b)}" for b in blockers] or ["- none"]
        ask_lines = [f"- {r.question}" + (f": {hint}" if (hint := r.answer[len(ASK):].strip(" ()")) else "")
                     + f" ({r.source})" for r in asks]
        if spec.get("address"):
            ask_lines.append(f"- Street address ({spec['name']} asks for it; it is not in the records)")
        out += ["", "## Ask the owner", ""]
        out += ask_lines or [NOTHING_TO_ASK]
        out += ["", "## Posting", "", "| Field | Value | Source |", "|---|---|---|",
                f"| Company | {cell(company)} | {job_rel} |", f"| Title | {cell(title)} | {job_rel} |",
                f"| Apply URL | {cell(job.get('apply_url') or job.get('source_url') or ASK)} | {job_rel} apply_url |",
                f"| Location | {location_cell(job.get('location'), job.get('remote'))} | {job_rel} |",
                f"| Req id | {cell(job.get('req_id') or 'none')} | {job_rel} |",
                f"| Posted | {cell(job.get('posted_at') or 'unknown')} | {job_rel} |",
                f"| Application stage | {cell(stage or 'unknown')} | applications/{slug}/application.md |",
                f"| Visa check | {cell(visa_line)} | legal/checks/{company_slug}.md |", "",
                "## Portal and account", "", f"Portal: {spec['name']} (from {portal_how}).", "", browser_line, ""]
        out += account_block(ctx, portal, company_slug, company, email)
        out += ["", "## Contact block", "", "| Field | Value | Source |", "|---|---|---|"]
        out += [r.md() for r in contact]
        out += ["", "## Work authorization and sponsorship", "",
                "Copied verbatim from legal/timers.md \"## Answer scripts\" (legal-visa's file). A form's rewording "
                "takes the same answer only when it asks the same thing; any other visa question is answered from "
                "the written script if it fits, otherwise by the owner.", "",
                "| Question (legal-visa's wording) | Answer | Source |", "|---|---|---|"]
        out += [r.md() for r in legal]
        out += ["", "Free-text visa explanation (legal-visa's written script):", "",
                f"> {cell(written_script(ctx.legal))}", "",
                "## Screening answers", "", "| Question | Answer | Source |", "|---|---|---|"]
        out += [r.md() for r in screening]
        out += ["", "## Voluntary self-identification (EEO)", "",
                "The owner's own choice on every row; unset keys default to declining. Never a guess.", "",
                "| Question | Answer | Source |", "|---|---|---|"]
        out += [r.md() for r in eeo]
        out += ([""] + policy_lines) if policy_lines else []
        out += ["", "## Resume, cover letter and why-us", "", "| Item | Value | Source |", "|---|---|---|",
                f"| Resume PDF (upload this file, nothing else) | {v.pdf_rel} | {pdf_from} |",
                f"| PDF sha256 | {v.pdf_sha} | equals {rrel}/meta.yaml pdf_sha256 |",
                f"| Editor approval | approved, round {cell(v.review.get('round'))}, signed "
                f"{cell(v.review.get('signed_at'))}, variant_hash {v.resume_sha} | {v.rel}/review.md |",
                f"| ATS check | {cell(v.ats_line)} | {rrel}/ats-report.json |"]
        out += files_rows
        if v.docx:
            out.append(f"| DOCX from the same render (only if the owner chooses to upload it personally) | "
                       f"{rrel}/{v.docx.name} | {rrel}/meta.yaml docx |")
        out += ["", "## Work history and education (from the approved resume)", "",
                "| Title | Company | Location | From | To |", "|---|---|---|---|---|"]
        out += history_rows
        out += ["", "| Degree | Field | School | Location | From | To |", "|---|---|---|---|---|---|"]
        out += school_rows
        out += ["", portal_notes(portal, spec), "", "## Checklist (the owner ticks)", ""]
        auth = next((r.answer for r in legal[:1]), LEGAL_MISSING)
        spon = next((r.answer for r in legal[1:2]), LEGAL_MISSING)
        out += [
            "- [ ] The posting is live at the apply URL today; company and title match this pack",
            *([] if browser_ok else [f"- [ ] the owner fills this form personally ({spec['name']}: no browser assistance)"]),
            f"- [ ] Account: {'the owner signed in or created it personally' if spec['account'] in ('per-company', 'platform') else 'none needed (guest)'}",
            "- [ ] Contact fields match the Contact block",
            f"- [ ] Resume: {v.pdf.name} uploaded (sha256 {v.pdf_sha[:12]}), and no other file",
            "- [ ] Cover letter and why-us used as written, unless the owner edits them personally",
            f"- [ ] Work authorization: {cell(auth)}; sponsorship: {cell(spon)} (legal-visa's answers, verbatim)",
            "- [ ] Start date, notice, salary, relocation and remote answered from this pack or by the owner",
            "- [ ] Voluntary self-identification: the EEO rows above, or the owner's own choice on the page",
            "- [ ] Every question this pack does not cover answered by the owner (apply-assist lists them in its reply)",
            "- [ ] Attestations, e-signatures and consent boxes completed by the owner personally",
            "- [ ] the owner clicks Submit",
            "- [ ] Confirmation captured (page text or email) and handed to tracker-ops for the applied event",
            "", f"Rebuild: `python3 scripts/apply_pack.py build {slug}"
                + ("" if pdf_choice == "classic" else f" --pdf {pdf_choice}") + f" --today {today.isoformat()}`",
        ]
        return "\n".join(out) + "\n"

    def compose(blockers: list[str]) -> tuple[str, dict[str, Any]]:
        meta = {
            "type": "apply-pack", "application": slug, "job": job_id, "company": company, "title": title,
            "built_on": today.isoformat(), "built_by": "scripts/apply_pack.py",
            "status": "blocked" if blockers else "ready", "blockers": blockers, "portal": portal,
            "browser_assist": "yes" if browser_ok else "no",
            "variant": v.rel, "variant_hash": v.resume_sha, "pdf_choice": pdf_choice, "pdf": v.pdf_rel,
            "pdf_sha256": v.pdf_sha,
            "legal_sha256": ctx.legal.sha256, "files": sorted(ctx.used),
        }
        return dump_pack(meta, render(blockers)), meta

    text, meta = compose(blockers)
    hits, dashes = content_findings(text, ctx.held)
    content_issues = []
    if hits:
        content_issues.append(f"held text in the pack ({len(hits)} hit(s)): run lint to see the lines; fix the "
                              "record it came from")
    if dashes:
        content_issues.append(f"em-dash in the pack on {len(dashes)} line(s): fix the record it came from")
    if content_issues:
        text, meta = compose(blockers + content_issues)
    return text, meta


def resume_rows(entries: list[Any], keys: tuple[str, ...], source: str, blockers: list[str]) -> list[str]:
    """Table rows for the approved resume's entries (text cells, then From and To as ISO months); a cell that looks
    like a credential is withheld and blocks the build."""
    rows = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        cells = []
        for key in keys:
            value = strip_comments(e.get(key))
            reason = find_credential(value)
            if reason:
                blockers.append(f"{source} {key} holds text that looks like a credential ({reason}): resume-tailor "
                                "removes it, then rebuild")
                value = WITHHELD_CREDENTIAL
            cells.append(cell(value))
        cells += [ym(e.get("start_date")) or "", ym(e.get("end_date")) or ""]
        rows.append("| " + " | ".join(cells) + " |")
    return rows


def strip_comments(value: Any) -> str:
    return one_line(re.sub(r"<!--.*?-->", "", one_line(value)))


def ask_items(text: str) -> list[str]:
    """The pack's Ask the owner items (the record fields to ask, then the street address a portal needs); none when
    the section says nothing is open."""
    section = text.split("## Ask the owner", 1)[1].split("\n## ", 1)[0] if "## Ask the owner" in text else ""
    return [line for line in section.splitlines() if line.startswith("- ") and line != NOTHING_TO_ASK]


def cmd_build(root: Path, slug: str, today: date, pdf_choice: str = "classic") -> int:
    path = root / "applications" / slug / PACK
    try:
        text, meta = build_pack(root, slug, today, pdf_choice)
    except Refusal as exc:
        print(f"REFUSED {slug}: {exc}")
        if path.is_file():
            print(f"  applications/{slug}/{PACK} from an earlier build is now stale: lint fails on it until a "
                  "build succeeds")
        return 1
    path.write_text(text, encoding="utf-8")
    relp = f"applications/{slug}/{PACK}"
    if meta["status"] == "ready":
        print(f"READY {relp} (portal {meta['portal']}; ask the owner: {len(ask_items(text))} item(s))")
        return 0
    print(f"BLOCKED {relp}")
    for b in meta["blockers"]:
        print(f"  blocker: {b}")
    return 1


# --------------------------------------------------------------------------- lint
def _section_text(body: str, heading: str) -> str | None:
    marker = f"## {heading}\n"
    if marker not in body:
        return None
    return body.split(marker, 1)[1].split("\n## ", 1)[0].strip()


CREDENTIAL_SCAN = ("Ask the owner", "Portal and account", "Contact block", "Work authorization and sponsorship",
                   "Screening answers", "Voluntary self-identification (EEO)",
                   "Work history and education (from the approved resume)")


def pack_credential_findings(text: str) -> list[str]:
    """Credential-looking text in the sections of a pack that copy records (table rows, the written script and the
    Ask the owner list); generated notes, the employer's posting and the hashes are not scanned."""
    findings, section = [], None
    for n, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if section in CREDENTIAL_SCAN and line.startswith(("|", ">", "- ")):
            reason = find_credential(line)
            if reason:
                findings.append(f"credential-like text on line {n} ({section}): {reason}; rebuild the pack, and "
                                "remove it from the record it came from")
    return findings


def lint_pack(root: Path, slug: str) -> list[str]:
    path = root / "applications" / slug / PACK
    if not path.is_file():
        return [f"no {PACK} for {slug}: run `python3 scripts/apply_pack.py build {slug}` first"]
    text = path.read_text(encoding="utf-8")
    try:
        meta, body = fm.loads(text)
    except fm.FrontmatterError as exc:
        return [f"{PACK} frontmatter unreadable: {exc}"]
    findings: list[str] = []
    held = load_held(root / "profile" / "PROFILE-HOLDS.md")
    if not (root / "profile" / "PROFILE-HOLDS.md").is_file():
        findings.append("profile/PROFILE-HOLDS.md missing: the held-text check cannot run")
    hits, dashes = content_findings(text, held)
    findings += [f"em-dash on line {n}" for n in dashes]
    findings += [f"held text {pt.shown(h)} on line {n} ({pt.held_sources([h])})" for n, h in hits]
    for relp in meta.get("files") or []:
        if not (root / str(relp)).is_file():
            findings.append(f"referenced file missing: {relp}")
    if meta.get("status") != "ready":
        findings += [f"blocker: {b}" for b in meta.get("blockers") or []] or ["status is not ready"]
    legal = load_legal(root)
    stored = _section_text(body, "Work authorization and sponsorship")
    fresh_rows = "\n".join(r.md() for r in withhold_credentials(legal_rows(legal, "x"), []))
    written = f"> {cell(written_script(legal))}"
    if legal.state != "ok" or legal.authorization is None or legal.sponsorship is None:
        findings.append(f"{LEGAL_MISSING}: {LEGAL_SOURCE}")
    if stored is None or fresh_rows not in stored or written not in stored:
        findings.append(f"work authorization answers in the pack differ from {LEGAL_SOURCE}: rebuild the pack")
    findings += pack_credential_findings(text)
    acc_data, acc_state = read_yaml(root / "state" / "accounts.yaml")
    if acc_state.startswith("unreadable"):
        findings.append(f"state/accounts.yaml {acc_state}")
    findings += validate_accounts(acc_data)[1] if acc_state == "ok" else []
    built = cli_date_or_none(meta.get("built_on"))
    if built is None:
        findings.append("built_on missing or not a date: rebuild the pack")
    else:
        try:
            fresh, _ = build_pack(root, slug, built, str(meta.get("pdf_choice") or "classic"))
        except Refusal as exc:
            reason = str(exc)
            if reason.startswith("variant not editor-approved: "):
                findings.append("variant is no longer editor-approved: " + reason.split(": ", 1)[1])
            else:
                findings.append(f"cannot rebuild: {reason}")
        else:
            if fresh != text:
                a, b = fresh.splitlines(), text.splitlines()
                n = next((i for i, (x, y) in enumerate(zip(a, b), 1) if x != y), min(len(a), len(b)) + 1)
                findings.append(f"{PACK} is stale or hand-edited: it differs from a fresh build (built_on "
                                f"{built.isoformat()}) at line {n}; rebuild it")
    seen: set[str] = set()
    return [f for f in findings if not (f in seen or seen.add(f))]


def cli_date_or_none(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def cmd_lint(root: Path, slugs: list[str]) -> int:
    bad = 0
    for slug in slugs:
        findings = lint_pack(root, slug)
        for f in findings:
            print(f"FINDING {slug}: {f}")
        if findings:
            bad += 1
            print(f"lint: {len(findings)} finding(s) in {slug}")
        else:
            meta, _ = fm.load(root / "applications" / slug / PACK)
            print(f"lint: clean {slug} (built {meta.get('built_on')})")
    return 1 if bad else 0


# --------------------------------------------------------------------------- account registry
def shown(value: Any) -> str:
    """A value quoted in a refusal, unless it looks like a credential (then it is never printed)."""
    return "(a value that looks like a credential, not shown)" if find_credential(value) else repr(value)


def account_platforms(root: Path) -> tuple[str, ...]:
    """ACCOUNT_PLATFORMS plus every id in targets/platforms.yaml (platforms and presence_surfaces), so each portal
    sitting can record its account (S-10 item 2)."""
    data, _ = read_yaml(Path(root) / "targets" / "platforms.yaml")
    data = data if isinstance(data, dict) else {}
    ids = [norm_key(e.get("id")) for key in ("platforms", "presence_surfaces")
           for e in (data.get(key) or []) if isinstance(e, dict) and e.get("id")]
    return ACCOUNT_PLATFORMS + tuple(i for i in ids if i and i not in ACCOUNT_PLATFORMS)


def cmd_account(root: Path, args: argparse.Namespace) -> int:
    platform = norm_key(args.platform)
    known = account_platforms(root)
    if platform not in known:
        print(f"refused: unknown platform {shown(args.platform)} (one of: {', '.join(known)})", file=sys.stderr)
        return 1
    tenant = norm_key(args.tenant) or None
    if tenant and not SLUG_RE.match(tenant):
        print("refused: --tenant must be the company slug (lowercase letters, digits and hyphens)", file=sys.stderr)
        return 1
    if not EMAIL_RE.match(args.email or ""):
        print(f"refused: {shown(args.email)} is not an email address", file=sys.stderr)
        return 1
    notes = one_line(args.notes) if args.notes is not None else None
    for flag, value in (("--notes", notes), ("--company", one_line(args.company))):
        reason = find_credential(value)
        if reason:
            print(f"refused: {flag} looks like a credential ({reason}); state/accounts.yaml never stores passwords, "
                  "PINs, codes, keys or security answers", file=sys.stderr)
            return 1
    path = root / "state" / "accounts.yaml"
    data, state = read_yaml(path)
    if state.startswith("unreadable"):
        print(f"refused: state/accounts.yaml {state}", file=sys.stderr)
        return 1
    entries, problems = validate_accounts(data)
    if problems:
        print("refused: fix state/accounts.yaml first (it must never hold a credential): " + "; ".join(problems),
              file=sys.stderr)
        return 1
    entry = next((e for e in entries if norm_key(e.get("platform")) == platform
                  and (norm_key(e.get("tenant")) or None) == tenant), None)
    if entry is None:
        entry = {k: None for k in ACCOUNT_KEYS}
        entry.update(platform=platform, tenant=tenant, notes="")
        entries.append(entry)
    entry.update(company=one_line(args.company), account_exists=args.exists == "yes", email_used=args.email)
    if args.created_on is not None and not entry.get("created_on"):   # the first date stays (a profile sitting
        entry["created_on"] = args.created_on.isoformat()             # re-runs the command with today's date)
    if getattr(args, "profile_filled_on", None) is not None:
        entry["profile_filled_on"] = args.profile_filled_on.isoformat()
    if notes is not None:
        entry["notes"] = notes
    entries = [{k: e.get(k) for k in ACCOUNT_KEYS} for e in entries]
    entries.sort(key=lambda e: (str(e.get("platform")), str(e.get("tenant") or "")))
    doc = {"schema_version": 1, "updated": args.today.isoformat(), "accounts": entries}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ACCOUNTS_HEADER + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"recorded {platform}/{tenant or '-'}: account_exists {args.exists}, email {args.email}")
    return 0


# --------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT, help="the job-search folder (default: this department)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="write applications/<slug>/apply-pack.md")
    b.add_argument("slug")
    b.add_argument("--today", type=cli_date, default=date.today(), help="run date YYYY-MM-DD (default: today)")
    b.add_argument("--pdf", choices=PDF_CHOICES, default="classic",
                   help="which render to upload: classic (the variant's meta.yaml PDF) or inline (inline/, dates "
                        "after each title; the stronger reading order for Oracle)")
    li = sub.add_parser("lint", help="check one or more packs; exit 1 on findings")
    li.add_argument("slugs", nargs="+")
    an = sub.add_parser("answers", help="print the reusable answer bank with a source per answer")
    an.add_argument("--today", type=cli_date, default=date.today())
    an.add_argument("--json", action="store_true")
    ac = sub.add_parser("account", help="record a portal account in state/accounts.yaml (never a password)")
    ac.add_argument("--platform", required=True)
    ac.add_argument("--tenant", default=None, help="company slug for per-company portals (Workday, iCIMS, Taleo)")
    ac.add_argument("--company", required=True)
    ac.add_argument("--exists", required=True, choices=("yes", "no"))
    ac.add_argument("--email", required=True)
    ac.add_argument("--created-on", type=cli_date, default=None)
    ac.add_argument("--profile-filled-on", type=cli_date, default=None,
                    help="the day a portal sitting filled the profile (portal_sitting, sitting_order read it)")
    ac.add_argument("--notes", default=None, help="portal quirks only; never credentials")
    ac.add_argument("--today", type=cli_date, default=date.today())
    args = ap.parse_args(argv)
    root = args.root.resolve()
    if args.cmd == "build":
        return cmd_build(root, args.slug, args.today, args.pdf)
    if args.cmd == "lint":
        return cmd_lint(root, args.slugs)
    if args.cmd == "account":
        return cmd_account(root, args)
    try:
        ctx = load_ctx(root, args.today)
    except Refusal as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    rows = answer_bank(ctx)
    if not ctx.holds_ok:
        print("warning: profile/PROFILE-HOLDS.md is missing; held text could not be filtered", file=sys.stderr)
    if args.json:
        print(json.dumps([row.__dict__ for row in rows], indent=2, ensure_ascii=False))
    else:
        print(render_answers(rows, ctx), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
