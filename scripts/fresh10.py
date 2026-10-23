#!/usr/bin/env python3
"""fresh10.py: the day's ten freshest roles worth a page, for the focused lane.

The daily list: posted within the window, fit at the focused bar, years-gated, not applied, warm path named.
Deterministic, from the job records only:
  fresh       posted_at within the window (default 72 h). first_seen never counts (D7: a board's first scan is a
              baseline, not a posting date); a record with no posted_at is not fresh.
  open        status not applied, skipped, closed or rejected; gate not fail; no application past fit_checked.
  years       fit_gates.required_years on the JD: an ask of up to policy.fit_gates.cold_max_years is in;
              more is a STRETCH only with a warm path, else out; policy.fit_gates.reject_years or more is the fit gate.
  fit         a fit report's score at or over policy.tiers.focused_fit ranks first; an unscored role with a hunt
              score at or over the cut (policy.fresh10.hunt_cut) may fill the list, marked "score first" (the
              fit-scorer runs before anything else).
  warm path   strategy_review.warm_paths: prospects at the company with warmth 1+ or tagged referrer; else "none on
              record" (the prospector looks first).
  target      an unscored role needs a focus profile for its title (focus_profiles.pick) or a target word in it
              (policy.fresh10.target_words; empty = any title), and none of policy.fresh10.off_target_words;
              off-target titles are left to the fit-scorer, never listed. A scored role stands on its fit.
  location    a gate in review because the location could not be placed is listed only when the location names the
              US or remote-US; a role abroad is out.
  employer    a record whose "company" is a post headline or an agency with an unnamed client ("Hiring: ...", a name
              with a colon, "via ... Recruitment", "unnamed client") is out: there is no employer to research or to ask.
  stopped     a job or application named in state/recruiter/stopped.yaml (a stop with its source) is out.
Rank: by expected interviews per application. The base is the MEASURED interview rate of the
channel the role would go through, read live by interviews.rates (warm: a named warm path, applied through a
person; cold: applied through the ATS; a rejection is not an interview), shrunk toward the overall rate with the
weight of 10 applications,
(k + 10 p0) / (n + 10), so a channel with 2 cases cannot claim 75%. It is multiplied by
stated PRIORS (not measured; each one moves to a measured rate when its n reaches 10): freshness (posted within 24 h
x1.2, within 48 h x1.0, else x0.8), years (STRETCH x0.5), sponsorship (policy.fresh10.sponsorship_prior: a weight for
no, unknown and yes), fit (fit/4.5; unscored x0.85). At most 2 roles per company (--per-company), so one big board cannot fill the day.
Usage: python3 scripts/fresh10.py [--today YYYY-MM-DD] [--hours 72] [--n 10] [--write] [--json]
--write saves state/daily/<date>-fresh10.md. Read only otherwise. No network.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as cfg  # noqa: E402
import fetch_ats  # noqa: E402
import fit_gates  # noqa: E402

from agency_paths import DATA_ROOT as JS_ROOT  # noqa: E402
TZ = ZoneInfo(str(cfg.get("identity.timezone", "UTC")))         # your zone: the list is dated and timed in it
LA = TZ                                                         # the old name, still imported by fresh10_people.py
CLOSED = {"applied", "skipped", "closed", "rejected", "archived", "expired"}
PAST_FIT = {"tailored", "applied", "confirmation", "screen", "oa", "interviewing", "offer", "rejected", "withdrawn"}
HUNT_CUT = int(cfg.get("policy.fresh10.hunt_cut", 69))     # an unscored role needs a hunt score at or over this
FOCUSED_FIT = float(cfg.get("policy.tiers.focused_fit", 4.0))
SPONSORSHIP_PRIOR = {"no": 1.0, "yes": 1.0, "unknown": 1.0, **{
    str(k): float(v) for k, v in (cfg.get("policy.fresh10.sponsorship_prior", {}) or {}).items()}}
NO_EMPLOYER = re.compile(r"(^hiring\b|:|\bunnamed\b|\bclient\b|\bvia\b.*\b(recruit|staffing|talent)|\bstealth\b)", re.I)
US = re.compile(r"\b(united states|usa|u\.s\.|us|remote|[A-Z]{2}\b|alabama|alaska|arizona|arkansas|california|colorado|"
                r"connecticut|delaware|florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|"
                r"maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|new hampshire|"
                r"new jersey|new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|"
                r"rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|"
                r"west virginia|wisconsin|wyoming|san francisco|seattle|boston|austin|chicago)\b", re.I)


def _words(path: str) -> "re.Pattern | None":
    """A whole-word, case-insensitive pattern over a configured word list, or None when the list is empty."""
    words = [str(w) for w in (cfg.get(path, []) or []) if str(w).strip()]
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I) if words else None


TARGET_WORD = _words("policy.fresh10.target_words")           # title words that make an unscored role worth scoring
OFF_TARGET_WORD = _words("policy.fresh10.off_target_words")   # title words that never do
ABROAD = re.compile(r"\b(CN|china|uk|united kingdom|england|nottingham|london|india|canada|germany|france|japan|"
                    r"singapore|israel|ireland|netherlands|poland|brazil|mexico|korea|taiwan|australia)\b", re.I)


def _when(value: Any) -> datetime | None:
    s = str(value or "").strip()
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _stage(root: Path, rec: dict[str, Any]) -> str | None:
    link = str(rec.get("application") or "").strip("[]' \"")
    app = root / "applications" / link / "application.md" if link else None
    if not app or not app.is_file():
        return None
    for line in app.read_text(encoding="utf-8").splitlines()[:40]:
        if line.startswith("stage:"):
            return line.split(":", 1)[1].strip()
    return None


def stopped(root: Path) -> set[str]:
    p = root / "state" / "recruiter" / "stopped.yaml"
    if not p.is_file():
        return set()
    import yaml
    rows = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("stopped") or []
    return {str(r.get(k)) for r in rows if isinstance(r, dict) for k in ("job", "application") if r.get(k)}


ENGINEER_MONTHS = fit_gates.ENGINEER_MONTHS   #: one count, kept in the hunt gate
COLD_MAX_YEARS = fit_gates.COLD_MAX_YEARS     # up to this many years asked goes cold; one band, kept in the gate


EARLY_JD = re.compile(r"\bearly[- ]career\b|\bnew (?:college )?grad|\bentry[- ]level\b|\brecent (?:college )?grad|"
                      r"\buniversity grad", re.I)
EARLY_LADDER = re.compile(r"\b(?:senior|sr\.?)\s+(?:analyst|associate)\b", re.I)   # consulting ladders start "Senior" early
LEVEL_YEARS = {"senior": 5, "lead": 5, "staff": 8, "principal": 8}
# A title is dual-level only when it names a lower level, or names the role twice at two levels (a split on every "/"
# would read "Senior AI/ML Engineer" as dual-level and reopen senior roles to cold applications).
LOWER_LEVEL = re.compile(r"\b(?:engineer|scientist|developer)\s+(?:i|ii|iii)\b|\bmid[- ]level\b|\bmid\b(?![- ]market)|"
                         r"\bjunior\b|\bjr\.?\b|\bentry[- ]level\b", re.I)
SENIOR_WORD = re.compile(r"\b(?:senior|sr\.?|staff|principal|distinguished|lead)\b", re.I)
LEVEL_WORDS = re.compile(r"\b(?:senior|sr\.?|staff|principal|distinguished|lead|junior|jr\.?|mid[- ]level|mid|entry[- ]level|"
                         r"i{1,3})\b|[(),.:-]", re.I)
ROLE_NOUN = re.compile(r"\b(?:engineer|scientist|developer|architect|analyst)\b", re.I)


def implied_years(title: str, stated: int | None, jd: str | None = None) -> tuple[int | None, bool]:
    """The years a title implies when the JD states none (stated priors, never a parse), read with
    fetch_ats.estimate_seniority: Senior or Lead about 5, Staff or Principal 8. Never implied when the title also names a
    lower level ("Software Engineer and Senior Software Engineer"), when the JD says early-career, new grad or entry
    level, or on a Senior Analyst or Associate ladder. Returns (years, True when implied); a stated ask wins."""
    if stated is not None:
        return stated, False
    years = LEVEL_YEARS.get(fetch_ats.estimate_seniority(title or ""))
    if years is None or EARLY_LADDER.search(title or "") or (jd and EARLY_JD.search(jd)):
        return None, False
    split = [p for p in re.split(r"\s+(?:and|or)\s+|\s*/\s*", title or "") if p.strip()]
    if any(LOWER_LEVEL.search(p) and not SENIOR_WORD.search(p) for p in split):
        return None, False                       # "Engineer II/Senior", "Mid or Senior": the lower level is open to a junior candidate
    roles = [p for p in split if ROLE_NOUN.search(p)]
    cores: dict[str, set[bool]] = {}
    for p in roles:                              # the role phrase without its level words, and whether it is senior
        core = " ".join(LEVEL_WORDS.sub(" ", p.lower()).split())
        cores.setdefault(core, set()).add(bool(SENIOR_WORD.search(p)))
    if any(levels == {True, False} for levels in cores.values()):
        return None, False                       # "Software Engineer and Senior Software Engineer": one role, two levels
    return years, True                           # a part with no level of its own inherits the title's


def years_rule(years: int | None, warm: bool = False) -> str | None:
    """A role asking up to the cold band (policy.fit_gates.cold_max_years) goes cold (in, warm or not); a role asking
    more goes only through a person inside (a warm path: STRETCH), else OUT; the reject bar
    (policy.fit_gates.reject_years) or more is the fit gate (OUT either way)."""
    if fit_gates.cold_years(years):            # one cold band with the gate's note
        return None
    if fit_gates.REJECT_YEARS is not None and years >= fit_gates.REJECT_YEARS:
        return "OUT"
    return "STRETCH" if warm else "OUT"


def on_target(title: str, lens: str | None) -> bool:
    """An unscored title is worth scoring when a focus profile covers it or it names a target word (any title when no
    target words are configured), and it names no off-target word."""
    hit = bool(lens) or TARGET_WORD is None or bool(TARGET_WORD.search(title))
    return hit and not (OFF_TARGET_WORD and OFF_TARGET_WORD.search(title))


def placed_abroad(rec: dict[str, Any]) -> bool:
    """True when the gate could not place the location and the text names somewhere outside the US."""
    reasons = " ".join(str(x) for x in rec.get("gate_reasons") or [])
    loc = str(rec.get("location") or "")
    if "location" not in reasons:
        return False
    return bool(ABROAD.search(loc)) or not US.search(loc)


def channel_rates(root: Path, now: datetime) -> dict[str, Any]:
    """The measured channel rates, from the one shared source."""
    import interviews
    return interviews.rates(root, now)


def expected(rates: dict[str, Any], warm: bool, hours_old: float, stretch: bool, sponsorship: str,
             fit: float | None) -> float:
    p = rates["warm"] if warm else rates["cold"]
    p *= 1.2 if hours_old <= 24 else (1.0 if hours_old <= 48 else 0.8)
    p *= 0.5 if stretch else 1.0
    p *= SPONSORSHIP_PRIOR.get(sponsorship, SPONSORSHIP_PRIOR["unknown"])
    p *= (fit / 4.5) if fit is not None else 0.85
    return round(p, 4)


def pick(root: Path, now: datetime, hours: int = 72, n: int = 10, per_company: int = 2) -> dict[str, Any]:
    rates = channel_rates(root, now)
    jobs = fetch_ats.load_jobs(root)
    import focus_profiles
    stop = stopped(root)
    from company_index import CompanyIndex
    import strategy_review
    index = CompanyIndex.load(root)
    since = now - timedelta(hours=hours)
    rows, counts = [], {"jobs": len(jobs), "fresh": 0, "open": 0, "years_out": 0, "below_fit": 0, "abroad": 0,
                        "off_target": 0, "stopped": 0, "no_employer": 0}
    for jid, rec in jobs.items():
        posted = _when(rec.get("posted_at"))
        if not posted or not (since <= posted <= now + timedelta(hours=1)):
            continue
        counts["fresh"] += 1
        if str(rec.get("status") or "") in CLOSED or str(rec.get("gate") or "") == "fail":
            continue
        if _stage(root, rec) in PAST_FIT:
            continue
        link = str(rec.get("application") or "").strip("[]' \"")
        if jid in stop or (link and link in stop):
            counts["stopped"] += 1
            continue
        if NO_EMPLOYER.search(str(rec.get("company") or "")):
            counts["no_employer"] += 1
            continue
        if placed_abroad(rec):
            counts["abroad"] += 1
            continue
        counts["open"] += 1
        jd = rec.get("_jd_text") or fetch_ats.jd_text_from_body(rec.get("_body") or "")
        years, implied = implied_years(str(rec.get("title") or ""), fit_gates.required_years(jd), jd)
        slug = str(rec.get("company_slug") or "")
        warm = strategy_review.warm_paths(root, index, slug) if slug and years_rule(years) else []
        rule = years_rule(years, bool(warm))
        if rule == "OUT":
            counts["years_out"] += 1
            continue
        fit, hunt = _num(rec.get("fit_score")), _num(rec.get("hunt_score"))
        if fit is not None and fit < FOCUSED_FIT:
            counts["below_fit"] += 1
            continue
        if fit is None and (hunt is None or hunt < HUNT_CUT):
            counts["below_fit"] += 1
            continue
        lens = (focus_profiles.pick(root, str(rec.get("title") or "")) or [{}])[0].get("family")
        title = str(rec.get("title") or "")
        if fit is None and not on_target(title, lens):
            counts["off_target"] += 1
            continue
        if not warm:
            warm = strategy_review.warm_paths(root, index, slug) if slug else []
        spons = str(rec.get("sponsorship") or "unknown").lower()
        exp = expected(rates, bool(warm), (now - posted).total_seconds() / 3600, rule == "STRETCH", spons, fit)
        rows.append({"id": jid, "company": rec.get("company"), "title": rec.get("title"), "expected": exp,
                     "jd_chars": len(jd or ""),
                     "posted": posted.astimezone(TZ).strftime("%Y-%m-%d %H:%M"), "fit": fit, "hunt": hunt,
                     "years": years, "years_implied": implied, "stretch": rule == "STRETCH", "warm": warm, "lens": lens,
                     "apply_url": rec.get("apply_url") or rec.get("source_url"),
                     "next": ("score first (fit-scorer)" if fit is None else "role package") +
                             ("; STRETCH: only through the person named" if rule == "STRETCH" else "") +
                             ("" if warm else "; prospector: find a person")})
    rows.sort(key=lambda r: (-r["expected"], -(r["fit"] or 0), -(r["hunt"] or 0)))
    chosen, per = [], {}
    for r in rows:
        key = str(r["company"] or "").lower()
        if per.get(key, 0) >= per_company:
            continue
        per[key] = per.get(key, 0) + 1
        chosen.append(r)
        if len(chosen) == n:
            break
    return {"date": now.astimezone(TZ).date().isoformat(), "hours": hours, "counts": counts, "rates": rates,
            "rows": chosen, "candidates": len(rows)}


MIN_JD = 200                  # jd_capture.MIN_JD: shorter text is a teaser, not a JD the fit-scorer can read


def to_score(res: dict[str, Any], n: int) -> list[dict[str, Any]]:
    """The Fresh 10 rows the fit-scorer should take today, best expected first: unscored and with a
    captured JD (without one the scorer cannot read the role; jd_capture fetches it first)."""
    return [r for r in res["rows"] if r.get("fit") is None and (r.get("jd_chars") or 0) >= MIN_JD][:n]


def render(res: dict[str, Any]) -> str:
    c = res["counts"]
    r_ = res["rates"]
    out = [f"# Fresh 10 · {res['date']}", "",
           f"Posted in the last {res['hours']} h (posted_at, never first_seen): {c['fresh']} of {c['jobs']} jobs; "
           f"{c['stopped']} stopped; {c['no_employer']} with no named employer; {c['abroad']} abroad; {c['open']} open and not past fit; {c['years_out']} asked more years "
           f"than the cold band allows, or the reject bar; {c['below_fit']} below the fit or hunt bar; {c['off_target']} unscored with no focus profile; "
           f"{res['candidates']} qualify. Regenerate: `python3 scripts/fresh10.py --write`.", "",
           f"Ranked by expected interviews. Measured channel rates: warm {r_['warm_k']} of {r_['warm_n']} replied, "
           f"cold ATS {r_['cold_k']} of {r_['cold_n']} (shrunk toward the overall rate: {r_['warm']:.0%} and {r_['cold']:.0%}); freshness, years, "
           "sponsorship and fit are stated priors until each has 10 measured cases. A role with no warm path is "
           "ranked as cold: naming a person first moves it up.", "",
           "| # | company · role | focus | posted | exp. interview | fit | hunt | years asked | warm path | next |",
           "|---|---|---|---|--:|--:|--:|---|---|---|"]
    for i, r in enumerate(res["rows"], 1):
        role = f"[{r['company']} · {r['title']}]({r['apply_url']})" if r["apply_url"] else f"{r['company']} · {r['title']}"
        yrs = "not stated" if r["years"] is None else (f"{r['years']} (STRETCH)" if r["stretch"] else str(r["years"]))
        yrs += " implied by title" if r.get("years_implied") else ""
        warm = ", ".join(r["warm"]) if r["warm"] else "none on record"
        fit = "unscored" if r["fit"] is None else f"{r['fit']:.1f}"
        hunt = "" if r["hunt"] is None else f"{r['hunt']:.0f}"
        out.append(f"| {i} | {role} (`{r['id']}`) | {r['lens'] or 'n/a'} | {r['posted']} | {r['expected']:.1%} | {fit} | {hunt} "
                   f"| {yrs} | {warm} "
                   f"| {r['next']} |")
    if not res["rows"]:
        out.append("| | none qualify today | | | | | | | | |")
    if any(not r["warm"] for r in res["rows"]):
        out += ["", f"People for the rows with no warm path (candidates to verify, never prospect records): "
                    f"state/daily/{res['date']}-fresh10-people.md. Regenerate: `python3 scripts/fresh10_people.py --net --write`."]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--today", default=None, help="run as of the end of this day (your time zone); default now")
    ap.add_argument("--hours", type=int, default=72)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--per-company", type=int, default=2)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--to-score", type=int, default=None, metavar="N",
                    help="print only the job ids the fit-scorer should take today (at most N, unscored, JD captured)")
    a = ap.parse_args(argv)
    now = (datetime.combine(date.fromisoformat(a.today), datetime.max.time(), TZ) if a.today
           else datetime.now(TZ))
    res = pick(a.root, now, a.hours, a.n, a.per_company)
    if a.to_score is not None:
        for r in to_score(res, a.to_score):
            print(f"{r['id']}\t{r['company']} · {r['title']}\t{r['expected']:.1%}")
        return 0
    if a.json:
        print(json.dumps(res, indent=2, default=str))
        return 0
    text = render(res)
    if a.write:
        p = a.root / "state" / "daily" / f"{res['date']}-fresh10.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        print(f"wrote {p.relative_to(a.root)}")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
