#!/usr/bin/env python3
"""Deterministic hard gates on a job record + its JD text (design §2 fit-scorer, targets/roles.md §3-4, §8).

Feeds the fit-scorer; it does not replace it. evaluate() returns
  {gate: pass|fail|review, reasons: [...], evidence_lines: [...], sponsorship_signal, everify}
where reasons read "<category>: <why>" (categories: sponsorship, location, seniority, employment, employer)
and non-gating observations read "note: ...". Any fail -> fail; else any review -> review; else pass.

Sponsorship (per sentence; "U.S." abbreviations kept whole, curly quotes normalized). A missing visa is a risk to
weigh, not a reason to skip a role, so the old single block is split in two:
  - Hard (fail), core, always on: U.S. citizenship required/only, clearance requirements (active/obtain/hold ...
    secret/security clearance, clearance ... required), "U.S. person", green-card-only, and an employer refusing
    E-Verify, the Form I-983 or OPT/CPT candidates (STEM OPT needs both). Extended: roles.md §8 "Block
    (citizenship, clearance, export control)" when present, else the built-in HARD list.
  - Flag (a risk the fit-scorer weighs, never a skip), core: "no sponsorship", negated "sponsor" (unable to /
    will not / not currently able to ...), "sponsorship is not available", "not eligible for sponsorship",
    "without ... sponsorship", "Sponsorship: none". Extended: roles.md §8 "Block (no sponsorship)" when present,
    else the built-in FLAG list. A flag leaves the gate as it was and reads "flag: sponsorship: ..."; the
    sponsorship_signal is still "block", so the record says sponsorship "no" with its line.
  - Boost list: roles.md §8 "Boost" when present, else the built-in BOOST list.
  - Not a block (note only): "a plus", "not required", "beneficial", "without sponsorship for an
    export license", Anthropic's "... sponsor visas for every role and every candidate".
    "preferred" / "nice to have" softens a block to a note as well (weak-fit, roles.md §6).
  - Soft mentions (ITAR, export control, bare "security clearance" / "U.S. citizen") -> review.
  - "Must be authorized to work in the United States" alone is ambiguous: a note, never a block.
  - Record level: sponsorship "no" with JD/post evidence fails only when that evidence is a hard line (an HN
    "U.S. Citizens" header); otherwise it is flagged. companies.yaml does-not-sponsor is flagged; employer
    history alone never fails.
Location: any US location passes, onsite, hybrid or remote: the home region (policy.location.home_cities),
  remote-US, a US city elsewhere (a "note: location: ... relocation" line; a move is a new I-983 within 10
  days) and "United States" with no city. Non-US fails; remote with no country, worldwide, or no location ->
  review (a US W-2 employer is still needed).
Seniority: staff / principal / director+ / people manager / intern / new-grad / fellowship-residency
  fail unless allowed; "Senior / Staff" and "Lead" -> review; "Senior" is a stretch note; a JD asking
  policy.fit_gates.reject_years or more fails; up to policy.fit_gates.cold_max_years goes cold (a note says how
  many months the candidate is short of the ask); more years add a STRETCH note, only through a person;
  "PhD required" -> review. The candidate's experience (policy.fit_gates.engineer_months) and newest degree month
  (policy.fit_gates.degree_conferred) come from config.
Employment: contract / part-time / temporary / intern employment_type fails (STEM OPT needs a W-2
  employer; roles.md §4); 1099 / corp-to-corp wording -> review. E-Verify mentions are reported.
Employer: a staffing agency, recruiting firm or talent marketplace by name (generic words plus the firms listed
  in policy.employer.staffing_names), or a JD hiring for an unnamed client ("our client is", "on behalf of our
  client", "direct client"), fails; an IT-services consultancy (policy.employer.it_services_names) goes to
  review. Real, named employers only.
JD: a record tagged `jd-missing` (job_alerts.py and jobs_manual.py write it when no JD was captured)
  is held at review while its JD text is empty; a gate never passes a posting nobody read.

CLI:  python fit_gates.py [--root R] [--all] [--job ID] [--allow staff,contract] [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_ats import (estimate_seniority, jd_text_from_body, load_companies, load_jobs,  # noqa: E402
                       read_roles_config, save_job)
from js_common import JS_ROOT  # noqa: E402
import agency_config as agency_cfg  # noqa: E402

CORE_FLAG = [   # no sponsorship: flagged, never a skip
    r"\bno (?:h-?1b |visa |immigration )*sponsorship\b",
    r"\b(?:unable|not (?:currently )?able|cannot|can't|can not|won't|will not|do not|don't|does not|doesn't"
    r"|are not|is not|not in a position)\s+(?:currently\s+)?(?:to\s+)?(?:\w+\s+){0,3}?sponsor",
    r"\bsponsorship (?:is|will) not (?:be )?(?:available|provided|offered|considered)\b",
    r"\bnot eligible for (?:visa |immigration )?sponsorship\b",
    r"\bwithout (?:the need for |requiring |needing )?(?:current or future |future |any )?"
    r"(?:employer |visa |employment |immigration )?sponsorship\b",
    r"\b(?:visa )?sponsorship\s*[:\-–]\s*(?:no|none|not available|unavailable|n/?a)\b",
    r"\b(?:do|does|will|can|are) not (?:currently )?(?:offer|provide|support)[^.]{0,40}\bvisas?\b",
]
CORE_HARD = [   # hard gates: citizenship or clearance; an employer refusing E-Verify, the I-983 or OPT
    r"\bus citizen(?:ship)? (?:is )?(?:required|only)\b|\bmust be (?:a )?us citizens?\b"
    r"|\brequires? us citizenship\b|\bus citizens? only\b|\bcitizenship (?:is )?required\b",
    r"\brestricted to us citizens\b",
    r"\b(?:active|current|hold|obtain|maintain|possess)\b[^.]{0,40}\b(?:secret|top secret|ts/sci|security|dod"
    r"|government|public trust)\b[^.]{0,20}\bclearance\b",
    r"\b(?:secret|top secret|ts/sci)\b[^.]{0,20}\bclearance\b",
    r"\bclearance[^.]{0,40}\brequired\b",
    r"\bus persons?\b",
    r"\bgreen card holders?\b|\bpermanent residents? only\b",
    r"\b(?:do not|does not|don't|doesn't|will not|won't|cannot|can't|not)\s+(?:participate in|use|enroll in|enrolled in)"
    r"\s+e-?verify\b",
    r"\b(?:will not|won't|cannot|can't|do not|does not|don't|doesn't|unable to|not able to)\s+(?:sign|complete|support)"
    r"\s+(?:the\s+)?(?:form\s+)?i-?983\b",
    r"\b(?:no|not accepting|cannot accept|can't accept|do not accept|does not accept|don't accept|unable to (?:accept|"
    r"consider|hire|support))\s+(?:stem\s+)?(?:opt|cpt)\b",
    r"\b(?:stem\s+)?(?:opt|cpt)\b[^.]{0,30}\bnot (?:accepted|eligible|considered|supported)\b",
]
FLAG = [       # extended no-sponsorship list; replaced by roles.md §8 "Block (no sponsorship)" when present
    r"(?:require|requires|required|need|needs)[^.]{0,30}sponsorship[^.]{0,20}(?:now or in the future|currently or in the future)",
    r"work authori[sz]ation that does not require sponsorship",
]
HARD = [       # extended hard list; replaced by roles.md §8 "Block (citizenship, clearance, export control)"
    r"\bitar\b[^.]{0,80}(?:us person|citizen|permanent resident)",
    r"full scope poly",
]
BOOST = [      # replaced by targets/roles.md §8 "Boost" fences when present
    r"\b(?:we|we do|we will|we can) sponsor(?: visas)?\b",
    r"\bvisa sponsorship(?: support)?:? (?:is )?(?:available|offered|provided)\b",
    r"\b(?:provide|offer|support)s? (?:visa|immigration) (?:sponsorship|support)\b",
    r"\bsponsorship support\b",
    r"\bparticipates in e-?verify\b",
    r"\bh-?1b (?:sponsorship|transfers?) (?:is |are )?(?:available|welcome|ok|supported)\b",
    # "Visa Sponsorship : We provide support for relocation and visas"
    r"\bsupport for (?:relocation (?:and|&) )?visas?\b",
    r"\brelocation (?:and|&) visas?\b",
    r"\bvisa sponsorship\s*:\s*(?!no\b|none\b|not\b|n/?a\b|unavailable\b)(?:yes|we|available|provided|offered)\b",
]
REVIEW = [r"\bitar\b", r"\bexport[- ]controll?ed\b", r"\bsecurity clearance\b", r"\bus citizens?(?:hip)?\b",
          r"\bpublic trust\b"]
NOT_A_BLOCK = [r"\bnot (?:required|necessary|needed|mandatory)\b", r"\bis a plus\b", r"\bbeneficial\b",
               r"\b(?:export|government) licen[cs]e\b", r"\bdiscretion whether to apply\b",
               r"\bevery role and every candidate\b", r"\bfor every (?:role|candidate|position)\b",
               r"\bpreferred\b", r"\bnice to have\b"]
AMBIGUOUS = [r"\bauthori[sz]ed to work in the (?:us|united states)\b", r"\beligible to work in the (?:us|united states)\b",
             r"\blegally authori[sz]ed to work\b"]

def _config_list(path: str) -> list[str]:
    """A list of lower-case strings from the config (empty when unset)."""
    return [str(x).lower() for x in (agency_cfg.get(path, []) or []) if str(x).strip()]


# The home region: city names that always count, and cities that share a name with a non-US place and count only with
# the home state beside them. Both come from policy.location in the config (empty by default).
BAY_AREA = _config_list("policy.location.home_cities")
BAY_AREA_NEEDS_CA = _config_list("policy.location.home_cities_need_state")
HOME_STATE_WORDS = _config_list("policy.location.home_state_words")
NON_US = ["london", "uk", "united kingdom", "england", "scotland", "ireland", "ie", "dublin", "paris", "france", "germany",
          "belgrade", "serbia", "gurugram", "gurgaon", "noida", "chennai", "kolkata", "greece", "athens", "portugal",
          "lisbon", "porto", "doha", "qatar", "ottawa", "bulgaria", "sofia", "romania", "bucharest", "cluj",
          "são paulo", "sao paulo", "middle east", "edinburgh", "glasgow", "manchester", "apj", "anz", "nordics",
          "dach", "benelux", "norway", "oslo", "aarhus", "denmark", "copenhagen", "barcelona", "argentina",
          "buenos aires", "montréal", "lausanne", "geneva", "united arab emirates", "abu dhabi", "saudi arabia",
          "riyadh", "brussels", "belgium", "frankfurt", "hamburg", "cologne", "british columbia", "alberta",
          "calgary", "edmonton", "quebec", "waterloo", "halifax", "costa rica", "uruguay", "montevideo", "chile",
          "santiago", "peru", "lima", "italy", "milan", "rome", "melbourne", "new zealand", "auckland",
          "wellington", "finland", "helsinki", "czech", "prague", "hungary", "budapest", "austria", "vienna",
          "nigeria", "lagos", "kenya", "nairobi", "egypt", "cairo", "south africa", "cape town", "johannesburg",
          "turkey", "istanbul", "vietnam", "hanoi", "thailand", "bangkok", "philippines", "manila", "indonesia",
          "jakarta", "malaysia", "kuala lumpur", "hong kong", "colombia", "bogota", "bogotá", "medellin", "ukraine",
          "kyiv", "lithuania", "vilnius", "latvia", "riga", "estonia", "tallinn",
          "munich", "berlin", "zurich", "zürich", "switzerland", "ch", "amsterdam", "netherlands", "tokyo", "japan",
          "seoul", "korea", "singapore", "india", "bangalore", "bengaluru", "delhi", "hyderabad", "pune", "mumbai",
          "toronto", "canada", "can", "ontario", "vancouver", "montreal", "tel aviv", "israel", "sydney", "australia",
          "madrid", "spain", "stockholm", "sweden", "warsaw", "poland", "china", "shanghai", "beijing", "taiwan",
          "taipei", "mexico", "brazil", "emea", "apac", "europe", "eu", "latam", "cet", "gmt", "dubai", "uae", "intl"]
US_STATES = ["al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il", "in", "ia", "ks", "ky",
             "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd",
             "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy", "dc"]
US_WORDS = ["united states", "usa", "us", "u s", "america", "americas", "north america", "california", "new york",
            "washington", "texas", "massachusetts", "colorado", "illinois", "brooklyn", "seattle", "boston", "austin",
            "chicago", "los angeles", "denver", "atlanta", "san diego", "long beach", "dallas", "houston", "miami",
            "philadelphia", "pittsburgh", "portland", "salt lake city", "raleigh", "phoenix", "detroit", "irvine",
            "nyc", "new york city", "bellevue", "redmond", "kirkland", "baltimore", "charlotte", "columbus",
            "nashville", "princeton", "tysons", "mclean", "arlington", "reston", "herndon", "boulder", "ann arbor",
            "minneapolis", "st louis", "kansas city", "omaha", "honolulu", "colorado springs", "sacramento",
            "santa monica", "culver city", "orlando", "tampa", "indianapolis", "cincinnati", "cleveland", "madison",
            "durham", "jersey city", "hoboken", "stamford", "new haven", "providence"]
STATE_NAMES = ["alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware",
               "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
               "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota", "mississippi", "missouri",
               "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico", "new york",
               "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island",
               "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont", "virginia", "washington",
               "west virginia", "wisconsin", "wyoming", "district of columbia"]
FAIL_LEVELS = {"staff": "staff level", "principal": "principal level", "director": "director level",
               "executive": "executive level", "manager": "people-manager role", "intern": "internship",
               "new-grad": "new-grad program"}
OPEN_STATUSES = ("discovered", "fit_checked", None, "")
# An early-career seat whose graduation window includes the candidate's degree month goes to review, never fails.
# The month is the newest `conferred` in profile/facts.yaml education; policy.fit_gates.degree_conferred is the fallback.
DEFAULT_CONFERRED = str(agency_cfg.get("policy.fit_gates.degree_conferred", "") or "")[:7]
MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"), 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS.update({"spring": 5, "summer": 8, "fall": 12, "autumn": 12, "winter": 12})
GRAD_WORD = re.compile(r"\b(?:graduat\w*|degree|class of)\b", re.I)
FUTURE_GRAD = re.compile(r"\b(?:expected|on track|graduating|will graduate|pursuing|currently enrolled|anticipated)\b",
                         re.I)
GRAD_STOP = re.compile(r"\band be able\b|\bstart\w*\b|;", re.I)
DATE_POINT = re.compile(r"\b(?:(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?\s+(?:\d{1,2},?\s+)?)?(20\d\d)\b",
                        re.I)


def _compile(patterns: Iterable[str]) -> list[re.Pattern]:
    out = []
    for p in patterns:
        try:
            out.append(re.compile(p, re.I))
        except re.error as exc:
            print(f"warning: bad gate regex {p!r}: {exc}", file=sys.stderr)
    return out


@dataclass
class GateConfig:
    core_hard: list[re.Pattern]
    hard: list[re.Pattern]
    core_flag: list[re.Pattern]
    flag: list[re.Pattern]
    boost: list[re.Pattern]
    review: list[re.Pattern] = field(default_factory=lambda: _compile(REVIEW))
    not_a_block: list[re.Pattern] = field(default_factory=lambda: _compile(NOT_A_BLOCK))
    ambiguous: list[re.Pattern] = field(default_factory=lambda: _compile(AMBIGUOUS))
    allow: set[str] = field(default_factory=set)
    conferred: str = DEFAULT_CONFERRED


def default_config(allow: Iterable[str] = ()) -> GateConfig:
    return GateConfig(_compile(CORE_HARD), _compile(HARD), _compile(CORE_FLAG), _compile(FLAG), _compile(BOOST),
                      allow={a.strip() for a in allow if a.strip()})


ALLOW_KEYS = ("staff", "principal", "new-grad", "intern", "manager", "lead", "contract", "fellowship", "years")


def allow_default(root: Path) -> set[str]:
    """targets/fit-gates.yaml `allow_default`: the --allow values every run starts with. No file, no
    default allowance; a value that is not an --allow key is refused (a typo must not silently open a gate)."""
    import yaml
    path = Path(root) / "targets" / "fit-gates.yaml"
    if not path.is_file():
        return set()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    vals = data.get("allow_default") if isinstance(data, dict) else None
    if vals is None:
        return set()
    if not isinstance(vals, list) or not all(isinstance(v, str) and v in ALLOW_KEYS for v in vals):
        raise ValueError(f"{path}: allow_default must be a list of {', '.join(ALLOW_KEYS)}")
    return set(vals)


def load_config(root: Path, allow: Iterable[str] = (), use_defaults: bool = True) -> GateConfig:
    """roles.md §8 fenced lists replace the extended built-ins; the core lists always apply. The allowance is
    targets/fit-gates.yaml allow_default (unless use_defaults is False) plus the caller's `allow`."""
    cfg = default_config(allow)
    if use_defaults:
        cfg.allow |= allow_default(root)
    cfg.conferred = _conferred(root)
    roles = read_roles_config(root)
    if roles.get("authorization_block"):
        cfg.hard = _compile(roles["authorization_block"])
    if roles.get("sponsorship_block"):
        cfg.flag = _compile(roles["sponsorship_block"])
    if roles.get("sponsorship_boost"):
        cfg.boost = _compile(roles["sponsorship_boost"])
    return cfg


def _conferred(root: Path) -> str:
    """The newest conferred degree month in profile/facts.yaml (YYYY-MM), else DEFAULT_CONFERRED."""
    import yaml
    try:
        facts = yaml.safe_load((Path(root) / "profile" / "facts.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return DEFAULT_CONFERRED
    dates = [str(e.get("conferred"))[:7] for e in facts.get("education") or [] if isinstance(e, dict) and e.get("conferred")]
    return max(dates) if dates else DEFAULT_CONFERRED


def graduation_window(jd_text: str) -> tuple[str, str, str, bool] | None:
    """(start YYYY-MM, end YYYY-MM, sentence, future) for the first sentence that states a graduation window, else None.
    future = a seat for people who have not graduated yet ("expected by June 2027", "on track to graduate by",
    "currently pursuing ... graduating by"): the candidate already holds the degree, so a future window never includes it.
    Only the clause from the graduation word to a start-date clause counts ("degree by summer 2026 and be able to start
    before December 1, 2026" is a window ending 2026-08). "by"/"before" a date: open start; "class of YYYY": that year;
    two or more dates: first to last; one bare year: that year."""
    for sentence in split_sentences(str(jd_text or "")):
        m = GRAD_WORD.search(sentence)
        if not m:
            continue
        clause = sentence[m.start():]
        stop = GRAD_STOP.search(clause, 1)
        if stop:
            clause = clause[:stop.start()]
        points = [(int(y), MONTHS.get((mo or "").lower().rstrip("."), 0)) for mo, y in DATE_POINT.findall(clause)]
        if not points:
            points = [(int(y), MONTHS.get((mo or "").lower().rstrip("."), 0)) for mo, y in DATE_POINT.findall(sentence)]
        if not points:
            continue
        def ym(p, end):
            y, mo = p
            return f"{y}-{(mo or (12 if end else 1)):02d}"
        if re.search(r"\b(?:by|before|no later than)\b", clause, re.I) and len(points) == 1:
            end = ym(points[0], True)
            if FUTURE_GRAD.search(sentence):        # "expected by June 2027": still studying, not a past graduate
                return "0000-01", end, sentence, True
            return "0000-01", end, sentence, False    # "obtained by summer 2026": earlier graduates qualify too
        if len(points) == 1:
            return ym(points[0], False), ym(points[0], True), sentence, False
        return ym(points[0], False), ym(points[-1], True), sentence, False
    return None


# ------------------------------------------------------------------------------ text
_ABBREV = re.compile(r"\bU\.\s?S\.(?:\s?A\.)?|\b(?:e\.g|i\.e|etc|Inc|Sr|Jr|vs|No)\.", re.I)


def split_sentences(text: str) -> list[str]:
    """Lines, then sentences, then ';' clauses. 'U.S.' and similar abbreviations never end a sentence."""
    masked = _ABBREV.sub(lambda m: m.group(0).replace(".", "․"), str(text or ""))
    out: list[str] = []
    for line in masked.splitlines():
        for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(“])", line):
            for clause in sent.split(";"):
                clause = clause.strip().replace("․", ".")
                if clause:
                    out.append(clause)
    return out


def _match_text(sentence: str) -> str:
    s = sentence.replace("’", "'").replace("‘", "'")
    return re.sub(r"\bU\.\s?S\.?(?:\s?A\.?)?(?![a-z])", "US", s, flags=re.I)


def _first(patterns: list[re.Pattern], text: str) -> re.Match | None:
    for p in patterns:
        m = p.search(text)
        if m:
            return m
    return None


# ------------------------------------------------------------------------------ gates
Finding = tuple[str, str, str | None]          # (level: fail|review|note, reason, evidence line)


def gate_sponsorship(record: dict[str, Any], jd_text: str, cfg: GateConfig,
                     company: dict[str, Any] | None) -> tuple[list[Finding], str]:
    findings: list[Finding] = []
    signal = "none"
    blocked = boosted = ambiguous = False
    for sentence in split_sentences(jd_text):
        text = _match_text(sentence)
        hit = _first(cfg.core_hard + cfg.hard, text)
        flag = None if hit else _first(cfg.core_flag + cfg.flag, text)
        if hit or flag:
            if _first(cfg.not_a_block, text):
                findings.append(("note", f"not a block ({(hit or flag).group(0)!r} is softened)", sentence))
            elif hit:
                findings.append(("fail", f"sponsorship: {hit.group(0)!r}", sentence))
                blocked = True
            else:
                findings.append(("flag", f"sponsorship: {flag.group(0)!r} (a risk to weigh, not a skip)",
                                 sentence))
                blocked = True
            continue
        hit = _first(cfg.boost, text)
        if hit:
            findings.append(("note", f"sponsorship offered ({hit.group(0)!r})", sentence))
            boosted = True
            continue
        hit = _first(cfg.review, text)
        if hit:
            if _first(cfg.not_a_block, text):
                findings.append(("note", f"{hit.group(0)!r} mentioned, softened", sentence))
            else:
                findings.append(("review", f"sponsorship: mentions {hit.group(0)!r}", sentence))
            continue
        if _first(cfg.ambiguous, text):
            ambiguous = True
            findings.append(("note", "work-authorization line without a sponsorship statement; ask at first contact", None))
    evidence = str(record.get("sponsorship_evidence") or "")
    if record.get("sponsorship") == "no" and evidence and not evidence.startswith(("USCIS", "targets/")):
        if _first(cfg.core_hard + cfg.hard, _match_text(evidence)):
            findings.append(("fail", "sponsorship: the posting itself rules it out", evidence))
        else:
            findings.append(("flag", "sponsorship: the posting says it does not sponsor (a risk to weigh, "
                             "not a skip)", evidence))
        blocked = True
    stance = str((company or {}).get("sponsorship_stance") or "").lower().replace("_", "-")
    if stance == "does-not-sponsor":
        findings.append(("flag", "sponsorship: targets/companies.yaml says does-not-sponsor (a risk to weigh, "
                         "not a skip)", None))
        blocked = True
    elif stance == "cap-exempt":
        findings.append(("note", "cap-exempt employer (no H-1B lottery)", None))
    if blocked:
        signal = "block"
    elif boosted:
        signal = "boost"
    elif ambiguous:
        signal = "ambiguous"
    return findings, signal


def _location_kind(part: str) -> str:
    p = re.sub(r"\bu\.\s?s\.?(?:\s?a\.?)?(?![a-z])", "us", part.lower().strip())
    inner = re.match(r"^(?:on-?site|hybrid|in[- ]person|in[- ]office|office)\s*(?:\((.*)\))?\s*$", p)
    if inner:
        p = (inner.group(1) or "").strip()
        if not p or re.fullmatch(r"on-?site|hybrid|in[- ]person|in[- ]office", p):
            return "mode"
    words = lambda names: any(re.search(rf"(?<![a-z]){re.escape(n)}(?![a-z])", p) for n in names)  # noqa: E731
    is_ca = bool(HOME_STATE_WORDS) and bool(re.search(
        r"(?<![a-z])(?:" + "|".join(re.escape(w) for w in HOME_STATE_WORDS) + r")(?![a-z])", p))
    us = words(US_WORDS) or words(STATE_NAMES) or bool(re.search(r",\s*(?:" + "|".join(US_STATES) + r")\b", p)) or \
        bool(re.match(r"^us,\s*[a-z]{2},", p))
    if re.search(r"\bremote\b", p):
        region = re.sub(r"\s+", " ", re.sub(r"[^a-z ]+", " ", p.replace("remote", " "))).strip()
        if us or (BAY_AREA and words(BAY_AREA)) or region in US_STATES:          # "Remote - CA"
            return "remote-us"
        if words(NON_US):
            return "remote-nonus"
        if re.search(r"\b(?:worldwide|anywhere|global)\b", p):
            return "remote-anywhere"
        return "remote-unknown"
    if (BAY_AREA and words(BAY_AREA)) or (BAY_AREA_NEEDS_CA and words(BAY_AREA_NEEDS_CA) and is_ca):
        return "bay"
    if words(NON_US) and not us:
        return "non-us"
    if re.fullmatch(r"(?:the )?(?:united states(?: of america)?|usa|us|u\.s\.?)", p):
        return "us-unspecified"
    if us:
        return "us-other"
    return "unknown"


def gate_location(record: dict[str, Any], company: dict[str, Any] | None) -> list[Finding]:
    loc = str(record.get("location") or "").strip()
    if not loc:
        return [("review", "location: none given", None)]
    parts = [p for p in re.split(r"\s*(?:\||;|\bor\b|/(?=\s))\s*", loc) if p.strip()]
    kinds = [k for k in (_location_kind(p) for p in parts) if k != "mode"]
    if record.get("remote") == "remote" and not any(k.startswith("remote") for k in kinds):
        kinds = [{"us-other": "remote-us", "us-unspecified": "remote-us", "bay": "bay", "non-us": "remote-nonus"}
                 .get(k, "remote-unknown") for k in kinds] or ["remote-unknown"]
    if "bay" in kinds or "remote-us" in kinds:
        return []
    if "remote-unknown" in kinds or "remote-anywhere" in kinds:
        return [("review", "location: remote without a stated country (US employer and W-2 needed)", loc)]
    if "us-other" in kinds:   # anywhere in the US; relocation is fine (a new I-983 within 10 days)
        return [("note", "location: US outside the home region (open to relocating)", loc)]
    if "us-unspecified" in kinds:
        return [("note", "location: in the US, city not given", loc)]
    if "unknown" in kinds or not kinds:
        return [("review", "location: cannot place it", loc)]
    return [("fail", "location: outside the US", loc)]


# A range ("2-10", "2\u201310", "2 to 10", "3-10+") is matched whole, so group 1 is its lower bound; group 2 is the 100
# characters after "years", read by lookahead so a second ask in the same sentence is still found. Whitespace runs
# are bounded so a long run of spaces cannot make the match slow.
_YEARS = re.compile(r"(?:(?:minimum|at least)\s{1,3}(?:of\s{1,3})?)?(\d{1,2})\s{0,3}\+?\s{0,3}"
                    r"(?P<range>(?:[-\u2013\u2014]|to)\s{0,3}\d{1,2}\s{0,3}\+?\s{0,3})?(?:or more\s{1,3})?"
                    r"(?:(?:relevant|professional|full[- ]time|total|additional|related)\s{1,3})?"   # "7 or more relevant years"
                    r"(?:years?|yrs?)\b(?=(?P<tail>[^.;\n]{0,100}))", re.I)


_SPELLED = {w: str(i) for i, w in enumerate(
    ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"), 1)}
_SPELLED_YEARS = re.compile(r"\b(" + "|".join(_SPELLED) + r")\b(\s*\(\d{1,2}\))?(?=\s*\+?\s*(?:or more\s+)?(?:years?|yrs?)\b)",
                            re.I)


def spelled_to_digits(jd_text: str) -> str:
    """'Two years' -> '2 years', 'three+ years' -> '3+ years', 'two (2) years' -> '2 years' (a spelled-out figure
    must not be read as no years asked)."""
    return _SPELLED_YEARS.sub(lambda m: _SPELLED[m.group(1).lower()], str(jd_text or ""))


# Read the ask the way the reader means it, and lower it only when the JD's structure makes that certain; a reading
# too high routes a role through a person, a reading too low sends a cold application that gets a fast no.
_EXPERIENCE = re.compile(r"\b(?:experience|expertise)\b", re.I)
# "preferred" qualifies the years only as a label ("5+ years preferred", "(preferred)"), never as an adjective
# ("your preferred language"); "ideally" and "desired" qualify the domain that follows, not the years
_PREFERRED = re.compile(r"\b(?:(?<!your )(?<!the )(?<!our )(?<!their )(?<!a )preferred|a plus|nice[- ]to[- ]haves?|bonus)\b",
                        re.I)
# section headings: required, preferred, or benefits and pay (asks never sit there); any other heading keeps the
# section it is in. A heading is preferred when a preferred word comes before any required word.
_HEAD_WORDS = re.compile(
    r"\b(?P<pref>preferred|nice[- ]to[- ]haves?|bonus(?:es)?|pluses|desired|extra credit|great if|stand out|even better)\b"
    r"|\b(?P<about>benefits|perks|compensation)\b"     # "About the Role", "About OpenAI" open no skipped section
    r"|\b(?P<req>minimum|required|requirements?|must|basic|qualifications|education|experience|you(?:'ll|\u2019ll| will)? "
    r"(?:bring|need|have)|looking for|about you)\b", re.I)
_HEADING = re.compile(r"^\s*(?:#{1,6}\s+.*|\*\*[^*]+\*\*:?|[A-Z][^.!?]{0,70}:)\s*$")
_HISTORY = re.compile(r"(?:\bfor\s+(?:the\s+)?(?:past|last|over)|\bover\s+the\s+(?:past|last)|\bin\s+the\s+(?:past|last)"
                      r"|\bsince|\bin business for|\bvest(?:ed|ing|s)?\s+over|(?:\bwe\s+have|\bwe\u2019ve|\bwe've|\bour\s+\w+\s+(?:has|have))(?:\s+over)?)\s*$",
                      re.I)
_NOT_ASK = re.compile(r"^\s*(?:in a row|ago|old\b|of age|straight|running|track record|history|anniversary|serving)", re.I)
_DEGREES = (("master", re.compile(r"\bmaster(?:[\u2019']?s)?\b(?!\s+(?:of\s+none|the\b|data|file|list|class))|\badvanced degree"
                                  r"|\bm\.s\.?(?=\W|$)|\bms\b(?! office)", re.I)),
            ("bachelor", re.compile(r"\bbachelor(?:[\u2019']?s)?\b|\bb\.[sa]\.?(?=\W|$)|\bbs\b|\bundergraduate\b", re.I)),
            ("phd", re.compile(r"\bph\.?\s?d\.?(?=\W|$)|\bdoctora", re.I)))
# Degree kinds the candidate holds (identity.degrees_held: bachelor, master, phd); a JD with degree paths is read
# at the easiest path one of them meets.
DEGREES_HELD = tuple(str(d).lower() for d in (agency_cfg.get("identity.degrees_held", []) or []))
_WITH_DEGREE = re.compile(r"^[^,;]{0,60}?\bwith\s+(?:an?\s+)?(?=(?:bachelor|master|advanced degree|ph\.?\s?d|doctora|[bm]\.?s\b))",
                          re.I)


def _asks_in(sentence: str) -> list[dict[str, Any]]:
    """Every figure in a sentence that states years of experience asked of the candidate."""
    asks: list[dict[str, Any]] = []
    low = sentence.lower()
    for m in _YEARS.finditer(sentence):
        # only the 300 characters before a figure are read, so a long sentence stays linear
        n, head, tail, before = int(m.group(1)), m.group(0).lower(), m.group("tail"), sentence[max(0, m.start() - 300):m.start()]
        if n > 20 or _NOT_ASK.match(tail) or _HISTORY.search(before[-80:]):
            continue
        # the clause holding the figure, cut at punctuation, dashes and "and": a label there says preferred
        cut = max(0, m.start() - 300) + max([before.rfind(c) for c in ",;()\u2014\u2013"] + [before.lower().rfind(" and ")]) + 1
        ends = [i for i in (sentence.find(c, m.end()) for c in ",;()\u2014\u2013") if i >= 0]
        nxt = low.find(" and ", m.end())
        ends += [nxt] if nxt >= 0 else []
        end = min(ends) if ends else len(sentence)
        if _PREFERRED.search(sentence[cut:end]) or re.match(r"\s{0,3}\(\s{0,3}(?:preferred|a plus|nice[- ]to[- ]have)\s{0,3}\)\s{0,3}(?:$|[,;.])",
                                                            sentence[end:], re.I) or \
                re.search(r"\b(?:ideally|preferably|optimally)\s{0,3}$", before, re.I):   # "ideally 6+ years"
            continue
        marked = (head.startswith(("minimum", "at least")) or "+" in head or "or more" in head or bool(m.group("range"))
                  or bool(re.search(r"\b(?:more than|over|minimum of|at least)\s{0,3}$", before, re.I)))
        lead_in = re.sub(r"^[\s\-*\u2022\u25cf]+", "", before)
        listed = bool(asks) and m.start() - asks[-1]["end"] <= 80          # "3+ years of X, and 5+ years of Y"
        lead_in = lead_in if m.start() <= 300 else "x" * 31                 # a figure past 300 characters is not a lead
        with_degree = bool(_WITH_DEGREE.match(tail))                         # "6 years with a Master's degree"
        degree_led = bool(re.search(r"(?:degree|bachelor\S*|master\S*|ph\.?\s?d\.?|[bm]\.?s\.?)\s{0,3}(?:and|plus|\+|with)\s{0,3}$",
                                    before, re.I))                           # "Master's degree and 5 years"
        led_by_experience = bool(re.search(r"\b(?:experience|expertise)\s{1,3}(?:of\s{1,3})?$", before, re.I))   # "expertise of 3 years"
        # "..., with 8+ years in system software": a marked figure led by "with" and followed by what the years
        # are in, of or doing, is an ask however long the sentence before it
        with_led = marked and bool(re.search(r"\bwith\s{1,3}(?:at least\s{1,3}|a minimum of\s{1,3})?$", before, re.I)) and \
            bool(re.match(r"\s{0,3}(?:in|of|as|working|building|designing|developing|leading)\b", tail, re.I))
        if _EXPERIENCE.search(tail) or with_degree or degree_led or led_by_experience or with_led or \
                (marked and (len(lead_in) <= 30 or listed)):
            asks.append({"start": m.start(), "end": m.end(), "n": n, "with_degree": with_degree})
    return asks


def _sentence_ask(sentence: str) -> list[int]:
    """The asks of one sentence. With no degree named, every ask stands (the largest rules). With degree paths
    ("Bachelor's and 5+ years, or Master's and 3+ years"), the asks after one degree mention form that path (its
    largest ask), and the answer is the easiest path a Bachelor's or Master's holder meets (never a PhD-only path); an
    explicit no-degree alternative ("..., OR 7+ years") is not that path."""
    asks = _asks_in(sentence)
    mentions = sorted((d.start(), d.end(), k) for k, rx in _DEGREES for d in rx.finditer(sentence))
    if not asks or not mentions:
        return [a["n"] for a in asks]
    runs: list[list[tuple[int, int, str]]] = []                  # degree mentions named together: "BS/MS", "MS or PhD"
    for mention in mentions:
        if runs and mention[0] - runs[-1][-1][1] <= 12 and re.fullmatch(
                r"[\s/,]*(?:or|and)?[\s/,]*(?:an?\s+)?", sentence[runs[-1][-1][1]:mention[0]], re.I):
            runs[-1].append(mention)
        else:
            runs.append([mention])
    paths: dict[int, list[int]] = {}
    free: list[int] = []
    for a in asks:
        if a["with_degree"]:
            run = next((i for i, r in enumerate(runs) if r[0][0] >= a["end"]), None)
        else:
            run = next((i for i in range(len(runs) - 1, -1, -1) if runs[i][-1][1] <= a["start"]), None)
            if run is not None:
                gap = sentence[runs[run][-1][1]:a["start"]]
                # "..., OR 7+ years": the "or" directly before the figure is the no-degree alternative; an "or" in
                # the degree's field list ("CS or related field, 6+ years") is not
                if re.search(r"\bor\s{0,3}(?:(?:any\s{1,3})?equivalent\s{0,3}\(?\s{0,3})?"
                             r"(?:at least\s{1,3}|minimum(?:\s{1,3}of)?\s{1,3})?$", gap, re.I):
                    run = -1
        if run is None:
            free.append(a["n"])
        else:
            paths.setdefault(run, []).append(a["n"])
    held = {i: max(v) for i, v in paths.items() if i >= 0 and {k for *_, k in runs[i]} & set(DEGREES_HELD)}
    if held:
        return free + [min(held.values())]
    return free + [max(v) for i, v in paths.items() if i >= 0 and {k for *_, k in runs[i]} != {"phd"}] or \
        [a["n"] for a in asks]


def required_years(jd_text: str) -> int | None:
    """The years a JD requires: the largest ask among its required lines. A preferred section ("Preferred
    Qualifications", "Nice to have", "Bonus") or an about-the-company section is not read; a heading that is neither
    keeps the section it is in. A first preferred-sounding heading with no required heading before it and another
    preferred heading after it holds the requirements ("Desired Capabilities" then "Bonus Points")."""
    lines = [line.replace("\xa0", " ") for line in spelled_to_digits(jd_text).splitlines()]

    def head_kind(text: str) -> str | None:
        t = text.strip()
        short = (0 < len(t) <= 60 and t[-1:] not in ".,;" and not t.startswith(("-", "*", "\u2022", "\u25cf"))
                 and t[:1].isupper() and _HEAD_WORDS.search(t))
        if not (_HEADING.match(text) or short) or _YEARS.search(text):
            return None
        first = _HEAD_WORDS.search(t)
        if not first:
            return "neutral"
        return "pref" if first.group("pref") else "about" if first.group("about") else "req"
    heads = [(i, k) for i, k in ((i, head_kind(t)) for i, t in enumerate(lines)) if k]
    prefs = [n for n, (_, k) in enumerate(heads) if k == "pref"]
    if prefs and not any(k == "req" for _, k in heads[:prefs[0]]) and len(prefs) > 1:
        heads[prefs[0]] = (heads[prefs[0]][0], "req")
    kind_at = dict(heads)
    found: list[int] = []
    section = "req"
    for i, text in enumerate(lines):
        if i in kind_at:
            section = section if kind_at[i] == "neutral" else kind_at[i]
            continue
        if section != "req":
            continue
        # degree paths are read within one sentence; "e.g.", "i.e.", "etc." and "Ph.D." do not end one
        for sentence in re.split(r"(?<!e\.g\.)(?<!i\.e\.)(?<!etc\.)(?<!\.D\.)(?<!\.S\.)(?<=[.!?])\s+(?=[A-Z0-9])", text):
            found.extend(_sentence_ask(sentence))
    return max(found) if found else None


ENGINEER_MONTHS = int(agency_cfg.get("policy.fit_gates.engineer_months", 0) or 0)   # months of engineering experience held
COLD_MAX_YEARS = int(agency_cfg.get("policy.fit_gates.cold_max_years", 0) or 0)      # up to this many years asked goes cold
_REJECT = agency_cfg.get("policy.fit_gates.reject_years")
REJECT_YEARS = int(_REJECT) if _REJECT else None             # this many years asked, or more, fails (None: never)


def cold_years(years: int | None) -> bool:
    """An ask of up to COLD_MAX_YEARS (or within the candidate's engineer months) is a cold application; more years
    only through a person. fresh10.years_rule agrees."""
    return years is None or years <= COLD_MAX_YEARS or years * 12 <= ENGINEER_MONTHS


def gate_seniority(record: dict[str, Any], jd_text: str, cfg: GateConfig) -> list[Finding]:
    title = str(record.get("title") or "")
    t = title.lower()
    out: list[Finding] = []
    level = estimate_seniority(title)
    years = required_years(jd_text)
    # An early-career seat that asks 1 to COLD_MAX_YEARS years is a seat, not a program; a new-grad or college-grad
    # program, an internship, or an early-career title that asks no years stays a program. An ask within the
    # candidate's engineer months is their level; above it the years note below rules.
    early_seat = (level == "new-grad" and years is not None and 1 <= years <= COLD_MAX_YEARS
                  and not re.search(r"\bnew (?:college )?grad|\buniversity grad|\bcampus\b|\bintern", t))
    if re.search(r"\bfellows?(?:hip)?\b|\bresiden(?:cy|t)\b|\bapprentice", t) and "fellowship" not in cfg.allow:
        out.append(("fail", "seniority: fellowship/residency program", title))
    elif re.search(r"\bsenior staff\b", t) and "staff" not in cfg.allow:
        out.append(("fail", "seniority: senior staff level", title))
    elif level == "staff" and re.search(r"\b(?:senior|sr)\b", t) and "staff" not in cfg.allow:
        out.append(("review", "seniority: senior/staff band, check the level in the JD", title))
    elif level == "new-grad" and "new-grad" in cfg.allow and not early_seat:
        # allowed, never a block; the note says whether the stated graduation window covers the candidate's degree
        win = graduation_window(jd_text)
        if not win:
            msg = "new-grad program, allowed; the JD names no graduation window"
        elif _window_has(jd_text, cfg.conferred):
            msg = f"new-grad program, allowed; its graduation window includes {cfg.conferred} (the degree month)"
        else:
            msg = (f"new-grad program, allowed; its graduation window does not include {cfg.conferred} "
                   f"(the degree month): eligibility unconfirmed")
        out.append(("note", msg, win[2] if win else None))
    elif early_seat:
        if years * 12 <= ENGINEER_MONTHS:
            out.append(("note", f"early-career seat asking {years} years or more, within your ~{ENGINEER_MONTHS} "
                                f"engineer months", None))
        elif cold_years(years):
            out.append(("note", f"early-career seat asking {years} years or more, about {years * 12 - ENGINEER_MONTHS} "
                                f"months over your ~{ENGINEER_MONTHS} engineer months: a cold application", None))
    elif level == "new-grad" and "new-grad" not in cfg.allow and _window_has(jd_text, cfg.conferred):
        win = graduation_window(jd_text)
        out.append(("review", f"seniority: new-grad program, but the JD's graduation window includes {cfg.conferred} "
                              f"(the degree month)", win[2] if win else title))
    elif level in FAIL_LEVELS and level not in cfg.allow:
        out.append(("fail", f"seniority: {FAIL_LEVELS[level]}", title))
    elif level == "lead" and "lead" not in cfg.allow:
        out.append(("review", "seniority: lead, check whether it means people management", title))
    elif level == "senior":
        out.append(("note", "senior title: stretch band (roles.md §3)", None))
    if REJECT_YEARS and years is not None and years >= REJECT_YEARS and "years" not in cfg.allow:
        out.append(("fail", f"seniority: asks {years}+ years of experience", None))
    elif early_seat:
        pass                                    # the early-career note above already says it
    elif years is not None and years * 12 > ENGINEER_MONTHS and cold_years(years):
        # up to COLD_MAX_YEARS asked goes cold; the candidate is a few months short, so the page presents the
        # experience held as that of an experienced engineer
        out.append(("note", f"asks {years}+ years, about {years * 12 - ENGINEER_MONTHS} months over your "
                            f"~{ENGINEER_MONTHS} engineer months: a cold application, up to {COLD_MAX_YEARS} years "
                            f"asked", None))
    elif years is not None and years * 12 > ENGINEER_MONTHS:
        # over COLD_MAX_YEARS asked is no cold application; a STRETCH goes only through a person inside
        # (fresh10.years_rule and the CRM read the warm path)
        out.append(("note", f"asks {years}+ years, over the {COLD_MAX_YEARS} years a cold application allows: "
                            f"no cold application; STRETCH only through a person", None))
    phd = re.search(r"[^.\n]*\bph\.?\s?d\.?\b[^.\n]{0,60}\brequired\b[^.\n]*", str(jd_text or ""), re.I)
    if phd and not re.search(r"equivalent", phd.group(0), re.I):
        out.append(("review", "seniority: PhD required", phd.group(0).strip()))
    return out


def _window_has(jd_text: str, ym: str) -> bool:
    win = graduation_window(jd_text)
    return bool(win) and not win[3] and win[0] <= ym <= win[1]


def gate_employment(record: dict[str, Any], jd_text: str, cfg: GateConfig) -> list[Finding]:
    et = str(record.get("employment_type") or "").lower()
    out: list[Finding] = []
    if et in ("contract", "part-time", "temporary", "intern") and et not in cfg.allow:
        out.append(("fail", f"employment: {et} (STEM OPT needs a full-time W-2 employer)", None))
    m = re.search(r"[^.\n]*(?:\b1099\b[^.\n]{0,40}\b(?:contract|contractor|position|role|basis|employee|employment"
                  r"|engagement)\b|\b(?:contract|contractor|position|role)\b[^.\n]{0,40}\b1099\b"
                  r"|\bcorp[- ]to[- ]corp\b|\bc2c\b)[^.\n]*", str(jd_text or ""), re.I)
    if m and "contract" not in cfg.allow:
        out.append(("review", "employment: 1099 / corp-to-corp wording", m.group(0).strip()))
    return out


# Roles are worked at real, named employers. A staffing agency, recruiting firm, talent marketplace or reposter fails on
# its name (generic words here, named firms from policy.employer.staffing_names); a JD hiring for an unnamed client
# fails on its words; an IT-services consultancy (policy.employer.it_services_names) goes to review, since a
# client-delivery seat is not in-house product engineering. A lead through a recruiter is worked at the named,
# verified end employer, never at the agency's own record.
STAFFING_NAME = [
    r"\brecruit\w*", r"\bstaffing\b", r"\bheadhunt\w*", r"\btalent\b", r"\bplacements?\b", r"\bmanpower\b",
    r"\bhiring for\b", r"\bsearch (?:group|partners|firm)\b", r"\b(?:human resources?|hr) consultanc\w*",
    r"\(via\b"] + [str(x) for x in (agency_cfg.get("policy.employer.staffing_names", []) or [])]
IT_SERVICES_NAME = [str(x) for x in (agency_cfg.get("policy.employer.it_services_names", []) or [])]
STAFFING_JD = [
    r"\bon behalf of (?:our|a|one of our) (?:\w+ ){0,2}client\b",
    r"\b(?:our|my) client(?:,|\s+(?:is|a|an|has|seeks|based|located)\b)",
    r"\bdirect[- ]client\b", r"\bclient of ours\b",
    r"\bwe(?:'re|’re| are) an? (?:\w+ ){0,3}(?:staffing|recruiting|recruitment|talent) (?:agency|firm|company|partner)"]


_STAFFING_NAME, _IT_SERVICES_NAME, _STAFFING_JD = (_compile(STAFFING_NAME), _compile(IT_SERVICES_NAME),
                                                   _compile(STAFFING_JD))


def gate_employer(record: dict[str, Any], jd_text: str) -> list[Finding]:
    name = " ".join(str(record.get("company") or "").lower().split())
    out: list[Finding] = []
    hit = _first(_STAFFING_NAME, name)
    if hit:
        out.append(("fail", f"employer: staffing, recruiting or talent marketplace ({record.get('company')}); "
                            f"apply only at a real, named employer", None))
    elif _first(_IT_SERVICES_NAME, name):
        out.append(("review", f"employer: IT services / consulting ({record.get('company')}); a client-delivery "
                              f"seat is not in-house product engineering", None))
    m = _first(_STAFFING_JD, str(jd_text or ""))
    if m:
        s = str(jd_text)
        line = s[max(0, s.rfind("\n", 0, m.start()) + 1):m.end() + 160].split("\n")[0].strip()
        out.append(("fail", "employer: posted for an unnamed client", line))
    return out


def evaluate(record: dict[str, Any], jd_text: str | None = None, config: GateConfig | None = None,
             company: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or default_config()
    if jd_text is None:
        jd_text = record.get("_jd_text") or jd_text_from_body(record.get("_body") or "")
    spons, signal = gate_sponsorship(record, jd_text, cfg, company)
    findings = spons + gate_location(record, company) + gate_seniority(record, jd_text, cfg) + \
        gate_employment(record, jd_text, cfg) + gate_employer(record, jd_text)
    if "jd-missing" in (record.get("tags") or []) and not str(jd_text or "").strip():
        findings.append(("review", "jd: not captured yet (job-alert card or manual capture); read the posting and "
                                   "record the JD with jobs_manual.py before scoring", None))
    levels = {lv for lv, _, _ in findings}
    gate = "fail" if "fail" in levels else ("review" if "review" in levels else "pass")
    reasons = list(dict.fromkeys((f"{lv}: {r}" if lv in ("note", "flag") else r) for lv, r, _ in findings))
    evidence: list[str] = []
    for lv, _, ev in findings:
        if ev and lv != "note" and ev[:300] not in evidence:
            evidence.append(ev[:300])
    everify = bool(re.search(r"\be-?verify\b", str(jd_text or ""), re.I))
    if everify:
        reasons.append("note: E-Verify mentioned (STEM OPT needs an E-Verify employer)")
    return {"gate": gate, "reasons": reasons, "evidence_lines": evidence, "sponsorship_signal": signal,
            "everify": everify}


def apply_gates(records: list[dict[str, Any]], config: GateConfig,
                companies: list[dict[str, Any]] | None = None) -> dict[str, int]:
    by_slug = {c.get("slug"): c for c in companies or []}
    summary = {"evaluated": 0, "pass": 0, "review": 0, "fail": 0}
    for rec in records:
        jd = rec.get("_jd_text") or jd_text_from_body(rec.get("_body") or "")
        out = evaluate(rec, jd, config, by_slug.get(rec.get("company_slug")))
        if (rec.get("gate") == "fail" and out["gate"] != "fail" and rec.get("status") == "skipped"
                and rec.get("fit_verdict") == "skip"):
            rec["status"], rec["fit_verdict"] = "discovered", None   # the gate's own skip, undone (a fixed parser)
        summary["evaluated"] += 1
        summary[out["gate"]] += 1
        rec["gate"], rec["gate_reasons"], rec["gate_evidence"] = out["gate"], out["reasons"], out["evidence_lines"]
        rec["everify_mentioned"] = out["everify"]
        spons_lines = [ln for ln in out["evidence_lines"] if ln]
        if out["sponsorship_signal"] == "block":
            first = next((r for r in out["reasons"] if r.startswith(("sponsorship:", "flag: sponsorship:"))), None)
            if first and spons_lines and rec.get("sponsorship") != "no":
                rec["sponsorship"], rec["sponsorship_evidence"] = "no", spons_lines[0]
        elif out["sponsorship_signal"] == "boost" and (
                rec.get("sponsorship") in (None, "", "unknown")
                or str(rec.get("sponsorship_evidence") or "").startswith(("USCIS", "targets/"))):
            sent = next((s for s in split_sentences(jd) if _first(config.boost, _match_text(s))), None)
            if sent:
                rec["sponsorship"], rec["sponsorship_evidence"] = "yes", sent[:300]
        if out["gate"] == "fail":
            rec["fit_verdict"] = "skip"
            if rec.get("status") in OPEN_STATUSES:
                rec["status"] = "skipped"
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--all", action="store_true", help="re-evaluate every job, not only discovered/fit_checked")
    ap.add_argument("--job", action="append", default=[], help="only these job ids")
    ap.add_argument("--allow", default="", help="comma list: staff,principal,new-grad,intern,manager,lead,contract,fellowship,years")
    ap.add_argument("--no-default-allow", action="store_true",
                    help="ignore targets/fit-gates.yaml allow_default")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    cfg = load_config(args.root, allow=args.allow.split(","), use_defaults=not args.no_default_allow)
    jobs = list(load_jobs(args.root).values())
    if args.job:
        jobs = [j for j in jobs if j["id"] in set(args.job)]
    elif not args.all:
        jobs = [j for j in jobs if j.get("status") in OPEN_STATUSES]
    fields = ("gate", "gate_reasons", "gate_evidence", "everify_mentioned", "fit_verdict", "status",
              "sponsorship", "sponsorship_evidence")
    before = {j["id"]: {f: j.get(f) for f in fields} for j in jobs}
    s = apply_gates(jobs, cfg, load_companies(args.root))
    changed = [j for j in jobs if {f: j.get(f) for f in fields} != before[j["id"]]]
    if not args.dry_run:
        for j in changed:
            save_job(args.root, j)
    mode = " (dry-run, nothing written)" if args.dry_run else ""
    print(f"gates{mode}: evaluated {s['evaluated']}, pass {s['pass']}, review {s['review']}, fail {s['fail']}; "
          f"{len(changed)} records updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
