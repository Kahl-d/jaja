#!/usr/bin/env python3
"""The hunt triage score: a deterministic 0 to 100 on every open job, so the best finds surface within hours.

    python3 scripts/hunt_score.py [--root R] [--today YYYY-MM-DD] [--dry-run] [--top 20] [--json]

It ranks; it never decides. The fit-scorer's full report (1.0 to 5.0, `roles/fit-scorer.md`) stays the score that
gates an application; this one decides the order the fit-scorer works in and what the daily fresh-roles review
(hunt_review.py) shows first. Scored: jobs whose status is discovered, fit_checked or queued and whose gate did not
fail. Written to the job record: `hunt_score` (0 to 100), `hunt_reasons` (why, one short line per component) and
`hunt_scored_at` (the day the current score was set).

The word lists that say which titles are on-role (AI_TITLE, HARD_OFF, SOFT_OFF, GENERIC, TITLE_CORE, RESEARCH_INFRA,
CORE, AI_IN_TITLE, FDE_TITLE, JD_CORE, JD_AI_TERMS), the seniority scores and years caps, and the location preference
all come from `policy.hunt` in the owner's config (agency_config); with no config the lists are empty and the
role-fit rules that depend on them stay neutral.

Components, each 0 to 1, times its weight (DEFAULTS; `targets/hunt.yaml` may override `weights`, `cut`,
`weeks_to_offer`, `dream_floor`; the weights must be exactly these keys):
  role       role fit, from the title and, for a generic title, the JD (role_fit): an on-role title (AI_TITLE) 1.0, or
             0.8 with a soft off-role word in it; a generic title (GENERIC) by its JD: 4 or more JD_AI_TERMS 0.9, 2 or
             3 0.5, fewer 0.1; no JD yet 0.5; any other roles.md family 0.6; an off-role title (HARD_OFF) 0.0, even
             with an on-role word.
             A research, scientist or infra title (RESEARCH_INFRA) with no core word in it (TITLE_CORE) is capped at
             0.5, or at 0.8 when its JD names RESEARCH_JD_MIN or more JD_CORE terms: the fit-scorer's reports showed
             such titles score well on the title and badly on the full report.
             A forward-deployed title (FDE_TITLE) with no on-role word in it (AI_IN_TITLE) takes its role fit from the
             JD's duties: 3 or more JD_AI_TERMS keep it, exactly 2 cap it at 0.7, 1 or none cap it at 0.4, no JD yet 0.5.
  skills     of the owner's skills (profile/skills-taxonomy.yaml + the built-in lexicon) the JD names, the share with
             evidence behind them; fewer than 3 named or no JD: 0.5 (neutral, said in the reasons)
  seniority  Staff, Principal, Lead, Director or Head, Senior, and the rest each take the score set in
             `policy.hunt.seniority` (top, senior, other); the years asked in the JD cap it (the largest figure in a
             sentence about experience, or any "N+ years") by the `policy.hunt.seniority.years` table of
             [minimum years, cap] rows; level codes read too: PMTS and LMTS as Staff/Principal, SMTS and L5 as Senior
  location   a gate pass in the Bay Area, remote in the US, elsewhere in California or the rest of the US takes its
             `policy.hunt.location_part` value (location_part, read with fit_gates._location_kind, the gate's own
             reading; the best place of a multi-location posting counts); a gate review 0.6
  visa       the record's sponsorship signal, else the company's stance, raised to 0.8 by an H-1B history
  freshness  1.0 for two days from the latest of posted_at (else first_seen), a hiring post within 7 days or a relist
             with a new req id or URL, 0.6 at a week, 0.25 at two weeks, 0.1 at three, 0.05 after a month. A LinkedIn
             alert copy (an id with -li-) whose posted_at is null or falls on its alert's day is dated by the alert,
             not the posting, so it gets 0 (a copy folded onto its ATS twin by dedup carries the twin's real
             posted_at and scores normally)
  warm       a prospect at the company in conversation (or the job came through one) 1.0; any prospect 0.6
  tier       dream 1.0, strong 0.8, good 0.6, fallback 0.3, not on the watch list 0.5
  stage      a founding title 1.0; a new startup 0.9 (a Form D within FUNDING_DAYS in state/signals/funding-*.json, or
             JD words such as seed stage, Series A or B, Y Combinator, recently raised); a big company 0.8
             (companies.yaml segment big-tech, ai-lab, enterprise or giant, or BIG_H1B+ recent approvals); else 0.5
FIT HISTORY lowers a job whose company and title family (title_family) already have fit reports that
were mostly skips: FIT_FAMILY_MAX x the skip share when the family has 2 or more reports, else FIT_COMPANY_MAX x the
skip share when the company has 3 or more; never when any report there is apply or priority, never for the job's own
report, and waived when the title names a core strength (CORE) that none of the skipped titles named. The
fit-scorer's verdicts teach the triage what the full reports keep rejecting.
Two rules after the sum: the ROLE GATE holds a job whose role fit is under ROLE_MIN one point under the cut,
whatever tier, freshness or weights say (an off-role job never reaches the owner through the cut); THE OWNER'S PICK
lifts a job an application names (applications/<slug>/application.md `job: [[id]]`) to at least the cut.
HIRING POST: a job a person's hiring post on X or LinkedIn names (`hiring_signal.url`, or the job's own
source_url, is an x.com status or a linkedin.com post; scripts/posts.py records both) gets POST_BOOST points and at least
the cut, because the post has a real person to talk to; the role gate still holds an off-role post under the cut, and
the owner's skips win: no boost at a company whose fit reports (the FIT HISTORY above) hold a skip and no apply or priority.
The clock multiplies the sum, because a role that cannot close before the clock ends is worth less as a whole:
  score = sum x (clock_floor + (1 - clock_floor) x clock), clock = the chance an offer lands before the end (clock.yaml):
  clamp((days_left - weeks_to_offer x 7) / 30, 0, 1), weeks by segment (startup, mid, giant; companies.yaml `segment`
  big-tech, ai-lab or enterprise -> giant, startup -> startup, growth -> mid; else mid, never the tier), never below
  `dream_floor` for dream-tier companies. Early in the clock every segment is 1.0 (apply to the slow giants now);
  late, the fast startups keep their weight.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clock as clk  # noqa: E402
import fetch_ats as fa  # noqa: E402
import js_common as jc  # noqa: E402
import agency_config as cfg_  # noqa: E402


def _words(key: str) -> list[str]:
    """policy.hunt.<key>: the owner's list (regex fragments or plain words); empty when not configured."""
    return [str(w) for w in (cfg_.get(f"policy.hunt.{key}", []) or [])]


def _rx(key: str) -> re.Pattern[str]:
    """A whole-word, case-insensitive alternation of the configured words; matches nothing when the list is empty."""
    words = _words(key)
    return re.compile(r"\b(" + "|".join(words) + r")\b", re.I) if words else re.compile(r"(?!x)x")


DEFAULTS: dict[str, Any] = {
    # fresh roles first, and founding seats, new startups and big companies before the middle
    "weights": {"role": 28, "skills": 15, "seniority": 10, "location": 5, "visa": 10, "freshness": 18, "warm": 6,
                "tier": 3, "stage": 5},
    "clock_floor": 0.5,
    "cut": 69,               # the score at or above which a job goes to the fit-scorer's queue
    "weeks_to_offer": {"startup": 3, "mid": 5, "giant": 8},
    "dream_floor": 0.2,
}
OPEN = ("discovered", "fit_checked", "queued")
TIER = {"dream": 1.0, "strong": 0.8, "good": 0.6, "fallback": 0.3}
STANCE = {"sponsors": 1.0, "cap-exempt": 1.0, "sponsors-selectively": 0.8, "unknown": 0.6, "does-not-sponsor": 0.2}
ENGAGED = ("in_conversation", "replied", "interviewing")
TOP = re.compile(r"\b(staff|principal|distinguished|director|head of|vp|vice president|lead|pmts|lmts)\b", re.I)
SENIOR = re.compile(r"\b(senior|sr\.?|smts|l5)\b", re.I)          # level codes some employers use: SMTS, L5
PLUS_YEARS = re.compile(r"\b(\d{1,2})\s*\+\s*years?\b", re.I)
YEARS = re.compile(r"\b(\d{1,2})\s*\+?\s*(?:or more\s+)?years?\b", re.I)
ROLE_MIN = 0.3                  # role fit under this is held under the cut
FIT_FAMILY_MAX = 12             # points off when every report in the company's title family was a skip
FIT_COMPANY_MAX = 6             # the same at company level, when the family itself has too few reports
FIT_VERDICTS = ("skip", "maybe", "apply", "priority")
FOUNDING = re.compile(r"\b(founding|first (?:ai |ml |software )?engineer|employee #?\s?\d{1,2})\b", re.I)
NEW_STARTUP = re.compile(r"\b(pre-seed|seed[- ]stage|seed round|series [ab]\b|early[- ]stage startup|y combinator|"
                         r"\(yc [swfx]\d{2}\)|just raised|recently raised|stealth startup)", re.I)   # not "founding team": big
#                 companies use it for new products
FUNDING_DAYS = 90               # a Form D filed within this many days marks a new round (state/signals/funding-*.json)
BIG_H1B = 100                   # FY2025 initial H-1B approvals at or above this read as a big company (companies.yaml)
STAGE = {"founding": 1.0, "new startup": 0.9, "big company": 0.8, "other": 0.5}
POST_BOOST = 8                  #: a person's hiring post outranks a portal listing (points, then at least the cut)
POST_URL = re.compile(r"^https?://(?:www\.|mobile\.)?(?:(?:x|twitter)\.com/[^/?#]+/status(?:es)?/\d+|"
                      r"linkedin\.com/(?:posts/[^/?#]+|feed/update/urn:li:(?:activity|share|ugcPost):\d+))", re.I)
CORE = _rx("core")                       # the owner's core strengths: fit history never outweighs them
LEVELS = re.compile(r"\b(senior|sr|junior|jr|staff|principal|lead|distinguished|i{1,3}|iv|v|l\d)\b", re.I)
AI_TITLE = _rx("ai_title")               # titles that are on-role
HARD_OFF = _rx("hard_off")               # titles that are off-role whatever else they say
SOFT_OFF = _rx("soft_off")               # words that lower an on-role title and sink an otherwise unknown one
GENERIC = _rx("generic_title")           # titles too generic to judge without the JD
TITLE_CORE = _rx("title_core")           # a core word in a research or infra title keeps its full marks
RESEARCH_INFRA = _rx("research_infra")   # research, scientist or infra words that cap a title without a core word
JD_CORE = tuple(w.lower() for w in _words("jd_core"))
RESEARCH_JD_MIN = 3
FDE_TITLE = _rx("fde_title")
AI_IN_TITLE = _rx("ai_in_title")
JD_AI_TERMS = tuple(w.lower() for w in _words("jd_ai_terms"))
SENIORITY = {"top": 0.9, "senior": 0.9, "other": 0.9, "years": []}
SENIORITY.update({k: v for k, v in (cfg_.get("policy.hunt.seniority", {}) or {}).items() if k in SENIORITY})


class HuntError(RuntimeError):
    """The score cannot be computed (a bad config)."""


# --------------------------------------------------------------------------- context
def _config(root: Path) -> dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULTS))
    path = Path(root) / "targets" / "hunt.yaml"
    if path.is_file():
        import yaml
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if "weights" in raw:
            w = raw["weights"] or {}
            unknown = sorted(set(w) - set(DEFAULTS["weights"]))
            if unknown:
                raise HuntError(f"targets/hunt.yaml: unknown weights {unknown}; allowed {sorted(DEFAULTS['weights'])}")
            cfg["weights"] = {k: float(w.get(k, 0)) for k in DEFAULTS["weights"]}
        for key in ("cut", "dream_floor", "clock_floor"):
            if key in raw:
                cfg[key] = float(raw[key])
        if "weeks_to_offer" in raw:
            cfg["weeks_to_offer"].update({k: float(v) for k, v in (raw["weeks_to_offer"] or {}).items()})
    total = sum(cfg["weights"].values())
    if total <= 0:
        raise HuntError("the weights sum to 0")
    cfg["weights"] = {k: v * 100.0 / total for k, v in cfg["weights"].items()}
    return cfg


def _prospects(root: Path) -> dict[str, str]:
    """company_slug -> 'engaged' or 'known'."""
    out: dict[str, str] = {}
    folder = Path(root) / "prospects"
    if not folder.is_dir():
        return out
    for p in folder.glob("*.md"):
        if p.name.startswith(("_", ".")) or p.name == "README.md":
            continue
        try:
            meta, _ = jc.read_record(p)
        except Exception:        # an unreadable prospect never blocks the score
            continue
        slug = str(meta.get("company_slug") or "").strip()
        if slug:
            level = "engaged" if meta.get("status") in ENGAGED else "known"
            if out.get(slug) != "engaged":
                out[slug] = level
    return out


def _taxonomy(root: Path) -> Any:
    import ats_check
    path = Path(root) / "profile" / "skills-taxonomy.yaml"
    return ats_check.load_taxonomy(path if path.is_file() else None)


def build_context(root: Path, *, today: date) -> dict[str, Any]:
    root = Path(root)
    cfg = _config(root)
    try:
        c = clk.load(root)
        days_left = clk.status(c, today)["days_left"] if c else None
    except Exception:
        days_left = None
    return {**cfg, "today": today, "days_left": days_left, "title_filter": fa.load_title_filter(root),
            "companies": {c["slug"]: c for c in fa.load_companies(root)}, "prospects": _prospects(root),
            "picks": _picks(root), "fit_history": _fit_history(root),
            "taxonomy": _taxonomy(root), "funding": _funding(root, today)}


def _funding(root: Path, today: date) -> dict[str, str]:
    """{company slug: Form D file date} from the newest state/signals/funding-*.json, rounds within FUNDING_DAYS."""
    files = sorted((Path(root) / "state" / "signals").glob("funding-*.json"))
    if not files:
        return {}
    try:
        signals = json.loads(files[-1].read_text(encoding="utf-8")).get("signals") or []
    except (ValueError, OSError):
        return {}
    out: dict[str, str] = {}
    for sig in signals:
        filed = jc.parse_date(str(sig.get("file_date") or ""))
        if not filed or not 0 <= (today - filed).days <= FUNDING_DAYS:
            continue
        for m in sig.get("matched_by") or []:
            if str(m).startswith("target:"):
                slug = str(m).split(":", 1)[1]
                out[slug] = max(out.get(slug, ""), filed.isoformat())
    return out


# --------------------------------------------------------------------------- components
def clock_weight(segment: str, days_left: int | None, *, dream: bool, cfg: dict[str, Any] | None = None) -> float:
    cfg = cfg or DEFAULTS
    if days_left is None:
        return 1.0
    weeks = float(cfg["weeks_to_offer"].get(segment, cfg["weeks_to_offer"]["mid"]))
    w = max(0.0, min(1.0, (days_left - weeks * 7) / 30.0))
    return max(w, float(cfg["dream_floor"])) if dream else w


HIRING_SIGNAL_DAYS = 7


def fresh_source(rec: dict[str, Any], today: date) -> tuple[int | None, str]:
    """(age in days, what dates it): the LATEST of (a) the first posting date (posted_at, else first_seen; none for a
    board-wide republish); (b) `hiring_signal: {url, date, by}`, a dated hiring post naming the role or its
    family, within HIRING_SIGNAL_DAYS; (c) `relist: {date, req_id | url}` only with a NEW req id or URL. Never updated_at
    or an alert copy's date."""
    found: list[tuple[int, str]] = []
    alert_copy = "-li-" in str(rec.get("id") or "") and (            # dated by its alert
        not rec.get("posted_at") or _same_day(rec.get("posted_at"), rec.get("alert_received_at")))
    if not rec.get("posted_unknown") and not alert_copy:
        for key in ("posted_at", "first_seen"):
            ts = fa.parse_ts(rec.get(key))
            if ts:
                found.append((max(0, (today - ts.date()).days), f"posted ({key})"))
                break
    sig = rec.get("hiring_signal") if isinstance(rec.get("hiring_signal"), dict) else {}
    ts = fa.parse_ts(sig.get("date")) if sig.get("url") else None
    if ts and 0 <= (today - ts.date()).days <= HIRING_SIGNAL_DAYS:
        found.append(((today - ts.date()).days, f"hiring post by {sig.get('by') or 'the company'} ({sig['url']})"))
    rl = rec.get("relist") if isinstance(rec.get("relist"), dict) else {}
    new_req = rl.get("req_id") and str(rl["req_id"]) != str(rec.get("req_id") or "")
    new_url = rl.get("url") and rl["url"] not in (rec.get("source_url"), rec.get("apply_url"))
    ts = fa.parse_ts(rl.get("date")) if (new_req or new_url) else None
    if ts:
        found.append((max(0, (today - ts.date()).days), "relist with a new " + ("req id" if new_req else "posting URL")))
    if not found:
        return None, ("alert copy" if alert_copy else "")
    return min(found, key=lambda f: f[0])


def _age_days(rec: dict[str, Any], today: date) -> int | None:
    return fresh_source(rec, today)[0]


def _same_day(a: Any, b: Any) -> bool:
    ta, tb = fa.parse_ts(a), fa.parse_ts(b)
    return bool(ta and tb and ta.date() == tb.date())


def _freshness(days: int | None) -> float:
    """Full marks for two days, 0.6 at a week, 0.25 at two weeks, 0.1 at three, 0.05 after a month."""
    if days is None:
        return 0.5
    for d0, d1, v0, v1 in ((2, 7, 1.0, 0.6), (7, 14, 0.6, 0.25), (14, 21, 0.25, 0.1)):
        if days <= d1:
            return 1.0 if days <= 2 else v0 + (v1 - v0) * (days - d0) / (d1 - d0)
    return 0.1 if days <= 30 else 0.05


BIG_SEGMENTS = {"big-tech", "ai-lab", "enterprise", "giant"}   # companies.yaml segments (segment_companies.py) -> giant


def clock_segment(company: dict[str, Any] | None) -> tuple[str, str]:
    """The weeks_to_offer segment (startup, mid, giant) and its reason, from ONE source: companies.yaml `segment`
    (segment_companies.py set). big-tech, ai-lab or enterprise reads as giant, startup as startup, growth as mid;
    no segment is mid, whatever the tier (a dream tier is how much the owner wants it, not its size)."""
    seg = str((company or {}).get("segment") or "")
    if seg in BIG_SEGMENTS:
        return "giant", f"segment {seg}" + (" (giant)" if seg != "giant" else "")
    if seg == "startup":
        return "startup", "segment startup"
    if seg == "growth":                                                # private, late-stage, mid-sized
        return "mid", "segment growth (mid)"
    if not company:
        return "mid", "segment mid (not on the watch list)"
    return "mid", "segment mid (not set in companies.yaml)" if not seg else f"segment mid ({seg} not a known kind)"


def _stage(title: str, jd: str, slug: str, company: dict[str, Any] | None, ctx: dict[str, Any]) -> tuple[float, str]:
    """A founding seat, a new startup (JD words or a recent Form D), a big company, or the middle."""
    if FOUNDING.search(title):
        return STAGE["founding"], "stage: founding seat"
    filed = (ctx.get("funding") or {}).get(slug)
    if filed:
        return STAGE["new startup"], f"stage: new startup (funding round {filed}, SEC Form D)"
    c = company or {}
    if c.get("segment") in BIG_SEGMENTS or (c.get("h1b_approvals_recent") or 0) >= BIG_H1B:   # never the tier
        return STAGE["big company"], "stage: big company"
    m = NEW_STARTUP.search(jd)
    if m:
        return STAGE["new startup"], f"stage: new startup ('{m.group(0)}' in the JD)"
    return STAGE["other"], "stage: neither founding, new startup nor big company"


def _seniority(title: str, jd: str) -> tuple[float, str]:
    if TOP.search(title):
        s, why = float(SENIORITY["top"]), "Staff/Principal/Lead title"
    elif SENIOR.search(title):
        s, why = float(SENIORITY["senior"]), "Senior title"
    else:
        s, why = float(SENIORITY["other"]), "level fits"
    sentences = re.split(r"(?<=[.;!?])\s+|\n+", jd or "")
    years = [int(y) for x in sentences if re.search(r"experience", x, re.I) for y in YEARS.findall(x)
             if 0 < int(y) <= 15]
    years += [int(y) for x in sentences for y in PLUS_YEARS.findall(x) if 0 < int(y) <= 15]   # "8+ years building"
    if years:
        need = max(years)          # "3+ years ... and a Master's with 4+ years": the bar is the larger figure
        why = f"{why}; asks {need}+ years"
        for floor_years, cap in sorted(((float(r[0]), float(r[1])) for r in SENIORITY["years"]), reverse=True):
            if need >= floor_years:
                s = min(s, cap)
                break
    return s, why


def _skills(jd: str, taxonomy: Any) -> tuple[float, str]:
    if not (jd or "").strip():
        return 0.5, "skills: JD not captured yet (neutral)"
    found = [t for t in taxonomy.terms() if t.find(jd)]
    if len(found) < 3:
        return 0.5, f"skills: only {len(found)} known skill terms in the JD (neutral)"
    have = [t for t in found if t.evidence]
    return len(have) / len(found), f"skills: {len(have)} of {len(found)} JD skills evidenced"


def role_fit(title: str, jd: str, title_filter: Any = None) -> tuple[float, str]:
    """(0 to 1, why) for the owner's target roles, as the policy.hunt word lists define them."""
    s, why = _role_base(title, jd, title_filter)
    if s >= 0.9 and FDE_TITLE.search(title or "") and not AI_IN_TITLE.search(title or ""):
        if not str(jd or "").strip():
            return 0.5, "forward-deployed title, JD not captured yet (duties unread)"
        n = sum(1 for t in JD_AI_TERMS if t in str(jd).lower())
        if n == 2:
            return min(s, 0.7), f"forward-deployed title, the JD's duties name 2 AI terms"
        if n < 2:
            return min(s, 0.4), f"forward-deployed title, but the JD's duties are not AI work ({n} AI terms)"
    research = RESEARCH_INFRA.search(title or "")
    if s > 0.5 and research and not TITLE_CORE.search(title or ""):
        n = sum(1 for t in JD_CORE if t in str(jd or "").lower())
        if n >= RESEARCH_JD_MIN:
            return min(s, 0.8), f"research/infra title ({research.group(0).lower()}), but the JD names the owner's core ({n} terms)"
        return 0.5, (f"research/infra title ({research.group(0).lower()}) without the owner's core "
                     f"in the title or the JD ({n} terms)")
    return s, why


def _role_base(title: str, jd: str, title_filter: Any = None) -> tuple[float, str]:
    off = HARD_OFF.search(title or "")
    if off:
        return 0.0, f"off-role title ({off.group(0).lower()})"
    if not _words("ai_title") and not _words("generic_title"):
        return 0.5, "no role lists configured (policy.hunt): neutral"
    soft = SOFT_OFF.search(title or "")
    if AI_TITLE.search(title or ""):
        return (0.8, f"AI title with a {soft.group(0).lower()} word") if soft else (1.0, "AI title")
    if soft:
        return 0.1, f"off-role title ({soft.group(0).lower()})"
    if GENERIC.search(title or ""):
        if not str(jd or "").strip():
            return 0.5, "generic title, JD not captured yet"
        text = str(jd).lower()
        n = sum(1 for t in JD_AI_TERMS if t in text)
        if n >= 4:
            return 0.9, f"generic title, AI work in the JD ({n} AI terms)"
        return (0.5, f"generic title, some AI in the JD ({n})") if n >= 2 else \
            (0.1, f"generic title, the JD is not AI work ({n} AI terms)")
    if title_filter is not None and title_filter.matches(title or ""):
        return 0.6, "a roles.md family"
    return 0.15, "outside the target families"


def title_family(title: str) -> str:
    """'Applied Scientist II, Sponsored Products' -> 'applied scientist': the head before a team suffix, the first of
    slash alternatives, without level words."""
    head = re.split(r",|\s[-\u2013]\s|\(|:|\|", str(title or ""), maxsplit=1)[0]
    head = head.split("/")[0]
    head = LEVELS.sub(" ", head.lower())
    return re.sub(r"[^a-z0-9+#]+", " ", head).strip()


def _fit_history(root: Path) -> list[dict[str, str]]:
    """Every fit-scorer report (a verdict with a numeric fit_score; fit_gates' gate-fail skips carry none):
    {id, company, family, verdict}."""
    return fit_history_from(meta for _, meta, _ in jc.iter_records(Path(root) / "jobs"))


def fit_history_from(records: Any) -> list[dict[str, str]]:
    """_fit_history over job records already loaded (next_up.py reads the jobs once)."""
    out: list[dict[str, str]] = []
    for meta in records:
        v = str(meta.get("fit_verdict") or "")
        if v in FIT_VERDICTS and isinstance(meta.get("fit_score"), (int, float)):   # a gate fail has no score
            out.append({"id": str(meta.get("id")), "company": str(meta.get("company_slug") or ""),
                        "family": title_family(meta.get("title") or ""), "verdict": v,
                        "title": str(meta.get("title") or "")})
    return out


def fit_penalty(rec: dict[str, Any], history: list[dict[str, str]]) -> tuple[int, str | None]:
    slug, fam, rid = str(rec.get("company_slug") or ""), title_family(rec.get("title") or ""), str(rec.get("id"))
    at_company = [h for h in history if h["company"] == slug and h["id"] != rid]
    if any(h["verdict"] in ("apply", "priority") for h in at_company if h["family"] == fam):
        return 0, None
    family = [h for h in at_company if h["family"] == fam]
    mine = {m.group(0).lower() for m in CORE.finditer(str(rec.get("title") or ""))}
    theirs = {m.group(0).lower() for h in (family if len(family) >= 2 else at_company) if h["verdict"] == "skip"
              for m in CORE.finditer(h.get("title", ""))}
    if mine - theirs and (len(family) >= 2 or len(at_company) >= 3):
        return 0, f"fit history waived: {', '.join(sorted(mine - theirs))} is a core strength the skipped roles lacked"
    if len(family) >= 2:
        skips = sum(h["verdict"] == "skip" for h in family)
        pts = round(FIT_FAMILY_MAX * skips / len(family))
        return pts, (f"fit history: {skips} of {len(family)} fit reports for {fam} at {slug} were skips; -{pts}"
                     if pts else None)
    if len(at_company) >= 3 and not any(h["verdict"] in ("apply", "priority") for h in at_company):
        skips = sum(h["verdict"] == "skip" for h in at_company)
        pts = round(FIT_COMPANY_MAX * skips / len(at_company))
        return pts, (f"fit history: {skips} of {len(at_company)} fit reports at {slug} were skips; -{pts}"
                     if pts else None)
    return 0, None


def skipped_company(rec: dict[str, Any], history: list[dict[str, str]]) -> str | None:
    """Why a post must not lift this job, or None: this job's own fit verdict is skip, or a fit
    report at the company is a skip and none there is apply or priority."""
    if rec.get("fit_verdict") == "skip":
        return "its own fit report is a skip"
    slug, rid = str(rec.get("company_slug") or ""), str(rec.get("id"))
    at = [h for h in history if h["company"] == slug and h["id"] != rid]
    skips = sum(h["verdict"] == "skip" for h in at)
    if skips and not any(h["verdict"] in ("apply", "priority") for h in at):
        return f"{skips} skip fit report(s) at {slug} and none apply or priority"
    return None


def from_post(rec: dict[str, Any]) -> str | None:
    """The X or LinkedIn hiring post a person wrote for this job (its hiring_signal url, else the job's own
    source_url), or None. A board listing, an alert or an HN thread is never a post here."""
    sig = rec.get("hiring_signal") if isinstance(rec.get("hiring_signal"), dict) else {}
    for url in (sig.get("url"), rec.get("source_url")):
        if url and POST_URL.match(str(url)):
            return str(url)
    return None


def _picks(root: Path) -> dict[str, str]:
    """job id -> the application folder naming it (the owner's picks)."""
    out: dict[str, str] = {}
    folder = Path(root) / "applications"
    for p in sorted(folder.glob("*/application.md")) if folder.is_dir() else []:
        try:
            meta, _ = jc.read_record(p)
        except Exception:        # an unreadable application never blocks the score
            continue
        m = re.search(r"\[\[([^\]|]+)", str(meta.get("job") or ""))
        if m:
            out[m.group(1).strip()] = p.parent.name
    return out


LOCATION_PART = {"bay": 1.0, "remote-us": 1.0, "california": 1.0, "us": 1.0}
LOCATION_PART.update({k: float(v) for k, v in (cfg_.get("policy.hunt.location_part", {}) or {}).items()
                      if k in LOCATION_PART})
LOCATION_REVIEW = 0.6
_CA = re.compile(r"(?<![a-z])(?:ca|california)(?![a-z])")


def location_place(rec: dict[str, Any]) -> str:
    """The best place of a posting for the tie-break: bay, remote-us, california, us, or other (cannot place it).
    Parts are split and read as fit_gates.gate_location reads them; a remote record counts as remote in the US
    when a part is in the US."""
    import fit_gates
    loc = str(rec.get("location") or "").strip()
    parts = [p for p in re.split(r"\s*(?:\||;|\bor\b|/(?=\s))\s*", loc) if p.strip()]
    places = set()
    for part in parts:
        kind = fit_gates._location_kind(part)
        if kind in ("bay", "remote-us"):
            places.add(kind)
        elif kind in ("us-other", "us-unspecified"):
            places.add("remote-us" if rec.get("remote") == "remote" else
                       "california" if _CA.search(part.lower()) else "us")
    for place in ("bay", "remote-us", "california", "us"):
        if place in places:
            return place
    return "other"


def location_part(rec: dict[str, Any]) -> tuple[float, str]:
    """(location part, reason): a gate pass by place (LOCATION_PART, from policy.hunt.location_part; a pass the place
    cannot be read for takes the US value), a gate review LOCATION_REVIEW."""
    if rec.get("gate") != "pass":
        return LOCATION_REVIEW, "location or other gate: review"
    place = location_place(rec)
    value = LOCATION_PART.get(place, LOCATION_PART["us"])
    said = {"bay": "Bay Area", "remote-us": "remote in the US", "california": "California outside the Bay Area",
            "us": "US outside California"}.get(place, "place not read")
    return value, f"location: gate pass, {said}"


def score(rec: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """{hunt_score, hunt_reasons, components} for one job record (pure)."""
    w = ctx["weights"]
    title = str(rec.get("title") or "")
    jd = str(rec.get("_jd_text") or "")
    slug = str(rec.get("company_slug") or "")
    company = ctx["companies"].get(slug)
    parts: dict[str, float] = {}
    why: list[str] = []

    parts["role"], t = role_fit(title, jd, ctx.get("title_filter"))
    why.append(f"role: {t}")
    parts["skills"], s = _skills(jd, ctx["taxonomy"])
    why.append(s)
    parts["seniority"], s = _seniority(title, jd)
    why.append(f"seniority: {s}")
    parts["location"], s = location_part(rec)
    why.append(s)

    sig = str(rec.get("sponsorship") or "unknown")
    stance = str((company or {}).get("sponsorship_stance") or "unknown")
    if sig == "yes":
        parts["visa"], v = 1.0, "visa: sponsor signal yes"
    elif sig == "no":
        parts["visa"], v = 0.2, "visa: says no sponsorship (a flag)"
    else:
        parts["visa"], v = STANCE.get(stance, 0.6), f"visa: company stance {stance}"
    if (rec.get("h1b_approvals_3y") or 0) and parts["visa"] < 0.8:
        parts["visa"], v = 0.8, f"{v}; H-1B sponsor history"
    why.append(v)

    days, dated_by = fresh_source(rec, ctx["today"])
    parts["freshness"] = _freshness(days)
    why.append((f"fresh: {days} day(s) old" + (f" ({dated_by})" if not dated_by.startswith("posted") else ""))
               if days is not None else "fresh: date unknown")
    if dated_by == "alert copy":
        parts["freshness"] = 0.0
        why[-1] = ("fresh: a LinkedIn alert copy dated by its alert, not its posting: no freshness points")

    parts["stage"], s = _stage(title, jd, slug, company, ctx)
    why.append(s)

    warm = "engaged" if rec.get("prospect") else ctx["prospects"].get(slug)
    parts["warm"] = {"engaged": 1.0, "known": 0.6}.get(warm or "", 0.0)
    if warm:
        why.append("warm: a prospect in conversation" if warm == "engaged" else "warm: a prospect at the company")

    tier = str((company or {}).get("tier") or "")
    segment, seg_why = clock_segment(company)
    parts["tier"] = TIER.get(tier, 0.5)
    why.append(f"tier {tier or 'none'}")
    clock = clock_weight(segment, ctx["days_left"], dream=tier == "dream", cfg=ctx)
    left = ctx["days_left"]
    why.append(f"{seg_why}, {left} days left: clock {clock:.2f}" if left is not None else f"{seg_why}: clock unknown")

    floor = float(ctx.get("clock_floor", DEFAULTS["clock_floor"]))
    total = int(round(sum(w[k] * parts[k] for k in w) * (floor + (1 - floor) * clock)))
    cut = int(round(float(ctx.get("cut", DEFAULTS["cut"]))))
    pts, hist = fit_penalty(rec, ctx.get("fit_history") or [])
    if hist:
        total -= pts
        why.append(hist)
    if parts["role"] < ROLE_MIN:
        total = min(total, cut - 1)
        why.append(f"role gate: role fit {parts['role']:.2f} is under {ROLE_MIN}; never reaches the cut")
    pick = (ctx.get("picks") or {}).get(str(rec.get("id")))
    if pick and total < cut:
        total = cut
        why.append(f"the owner's pick: applications/{pick} names this job; raised to the cut")
    post = from_post(rec)
    skipped = post and skipped_company(rec, ctx.get("fit_history") or [])
    if skipped:
        why.append(f"hiring post {post}: no post boost, {skipped}")
    elif post and parts["role"] >= ROLE_MIN:
        total = min(100, max(total + POST_BOOST, cut))
        why.append(f"hiring post: {post} has a person to talk to: +{POST_BOOST}, at least the cut")
    return {"hunt_score": total, "hunt_reasons": why,
            "components": {**{k: round(v, 3) for k, v in parts.items()}, "clock": round(clock, 3)}}


# --------------------------------------------------------------------------- run
def scoreable(rec: dict[str, Any]) -> bool:
    return rec.get("status") in OPEN and rec.get("gate") != "fail"


def apply_scores(records: list[dict[str, Any]], ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """Score records in place (the pipeline's triage stage calls this); returns them ranked."""
    out = []
    for rec in records:
        if not scoreable(rec):
            continue
        res = score(rec, ctx)
        # hunt_scored_at is the day the current score was set; a run that finds the same score and reasons leaves
        # the record as it was (restamping every open job on every run would churn every record)
        if (rec.get("hunt_score") != res["hunt_score"] or rec.get("hunt_reasons") != res["hunt_reasons"]
                or not rec.get("hunt_scored_at")):
            rec.update({"hunt_score": res["hunt_score"], "hunt_reasons": res["hunt_reasons"],
                        "hunt_scored_at": ctx["today"]})
        out.append(rec)
    out.sort(key=lambda r: (-r["hunt_score"], str(r.get("id"))))
    return out


def run(root: Path, *, today: date, dry_run: bool = False) -> list[dict[str, Any]]:
    root = Path(root)
    ctx = build_context(root, today=today)
    jobs = fa.load_jobs(root)
    if dry_run:
        jobs = {k: dict(v) for k, v in jobs.items()}
    ranked = apply_scores(list(jobs.values()), ctx)
    if not dry_run:
        for rec in ranked:
            fa.save_job(root, rec)
    return ranked


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=jc.JS_ROOT)
    ap.add_argument("--today", help="run date YYYY-MM-DD (default: today, local)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    today = jc.parse_date(args.today) if args.today else jc.today()
    try:
        ranked = run(args.root, today=today, dry_run=args.dry_run)
    except HuntError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    rows = [{"id": r["id"], "hunt_score": r["hunt_score"], "company": r.get("company"), "title": r.get("title"),
             "status": r.get("status"), "reasons": r["hunt_reasons"]} for r in ranked[:args.top]]
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
    else:
        mode = "dry run, nothing written" if args.dry_run else "written"
        print(f"hunt_score {today.isoformat()} ({mode}): {len(ranked)} open jobs scored")
        for r in rows:
            print(f"{r['hunt_score']:>3}  {r['company']} · {r['title']}  [{r['id']}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
