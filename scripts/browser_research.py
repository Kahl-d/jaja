#!/usr/bin/env python3
"""browser_research.py - the guardrails a script can hold for reading people in the owner's browser.

The department may find the exact person and read them, LinkedIn included, in the owner's browser. Reading only;
everything outward stays behind the owner's approval. The skill
`research-person` (browser mode) and `prospect-search` Mode B call this script at three moments:

  view         BEFORE opening a LinkedIn profile. Allowed only for ONE person's profile (`linkedin.com/in/<handle>`
               and its sub-pages), never a search, list, company-people page or feed; only for a person someone
               named (`--named-by`: the owner, a standing order, a target file); at most DAILY_VIEWS different people per
               department day (America/Los_Angeles); at least MIN_GAP_SECONDS after the previous view. An allowed
               view appends one line to state/browser/views.jsonl; the same person again that day is not
               counted twice. A page off LinkedIn needs no view and is not logged.
  click-check  BEFORE any click. Default deny: reading controls pass (see more, show all, a section tab), every
               control that signals to the person is refused by name (Connect, Follow, Message, React, Comment,
               Repost, Share, Send, I'm interested, Save, Apply, Endorse, More...), anything unknown is refused.
               One exception: a PLAIN connect or follow on LinkedIn (Connect, Follow,
               + Follow, and "Send without a note", the no-note connect's second click) runs only with
               `python3 scripts/pace.py check` exit 0 and the owner's yes recorded, act by act. The lane passes
               `--allow-connect --yes <where the owner's yes is recorded: the approval id or the cards decision>`; this script then runs pace.py's check itself (linkedin, connect or
               follow) and refuses on anything but go. After the click lands, `python3 scripts/pace.py record
               --platform linkedin --action <connect|follow>`. A note, a message, a comment or anything else stays
               refused.
  verify       AFTER reading ("&" reads as "and" in a title; a middle initial or initials for given names is a
               compatible name, never a conflict): identity from two or more INDEPENDENT sources (different publishers; LinkedIn is one)
               on name, employer, title and location. VERIFIED only when every field matches in at least two
               independent sources and none differs; PROBABLE when a field is only compatible (one phrase inside the
               other) or seen in one source; CONFLICT when a field differs; UNVERIFIED below two sources. Prints
               the match table and the confidence line for the brief.
  job-view     BEFORE opening a LinkedIn JOB page to read its description: allowed only for ONE
               job page (`linkedin.com/jobs/view/<id>`), never a search, collection or feed; only for a job on today's
               read list (state/jd/read-list-<day>.json, written by jd_capture.py) whose URL names the same posting;
               at most JOB_VIEWS_PER_DAY different jobs a department day, MIN_GAP_SECONDS apart. Logged to
               state/browser/job-views.jsonl; the same job again that day is not counted twice.
  status       today's view count and the room left.

A script cannot stop a click it is not asked about; the procedure makes these calls mandatory, and a PreToolUse hook
on the browser tools would be the hard enforcement (proposed, not built: hooks need the owner's permission).

CLI:  python3 browser_research.py [--root R] view --person NAME --url URL --named-by WHO [--lane L] [--now ISO]
      python3 browser_research.py [--root R] job-view --job ID --url URL [--now ISO]
      python3 browser_research.py [--root R] click-check "<label>" [--allow-connect --yes REF] [--now ISO]
      python3 browser_research.py [--root R] verify <observations.yaml|json>
      python3 browser_research.py [--root R] status [--now ISO]
Exit: view and job-view 0 allowed, 3 refused; click-check 0 allowed, 2 refused; verify 0 VERIFIED, 1 PROBABLE or UNVERIFIED,
3 CONFLICT; status 0.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from js_common import JS_ROOT, append_jsonl, dept_day, normalize_company, read_jsonl  # noqa: E402
import agency_config as cfg  # noqa: E402
import pace  # noqa: E402  (the hard counter for outward acts)

DAILY_VIEWS = int(cfg.get("policy.browser.daily_profile_views", 25))        # at most this many different profiles a day
MIN_GAP_SECONDS = int(cfg.get("policy.browser.min_gap_seconds", 20))     # human pace between profile views
LOG = Path("state") / "browser" / "views.jsonl"
JOB_VIEWS_PER_DAY = int(cfg.get("policy.browser.daily_job_views", 20))     # at most this many named JD reads a day
JOB_LOG = Path("state") / "browser" / "job-views.jsonl"
READ_LIST = Path("state") / "jd"
JOB_PAGE = re.compile(r"^/jobs/view/(?:[A-Za-z0-9%\-]*-)?(\d+)/?$")
FIELDS = ("name", "employer", "title", "location")

PROFILE = re.compile(r"^/in/([A-Za-z0-9\-_%]+)(?:/|$)")
FORBIDDEN = re.compile(r"\b(connect|follow|message|like|react|comment|repost|share|send|interested|save|apply|"
                       r"endorse|recommend|invite|more|block|report|withdraw|accept|ignore)\b", re.I)
PLAIN_CONNECT = {"connect": "connect", "follow": "follow", "+ follow": "follow", "send without a note": "connect"}
CONNECT_RULE = ("a plain connect or follow runs only with `python3 scripts/pace.py check` exit 0 and the owner's yes "
                "recorded (pass --allow-connect --yes <ref>)")
READING = re.compile(r"^\W*(?:(?:\.\.\.|…)?\s*see more|see less|show (?:all|more)\b.*|about|experience|education|"
                     r"activity|posts|featured|skills|recommendations received|licenses? (?:&|and) certifications|"
                     r"projects|publications|comments|all activity)\W*$", re.I)


def _day(now: datetime) -> str:
    return dept_day(now).isoformat()


def _handle(url: str) -> str | None:
    p = urlparse(url)
    if not p.netloc.lower().endswith("linkedin.com"):
        return None
    m = PROFILE.match(p.path)
    return m.group(1).lower() if m else ""


def status(root: Path, now: datetime) -> dict[str, Any]:
    day = _day(now)
    rows = [r for r in read_jsonl(Path(root) / LOG) if r.get("day") == day]
    people = sorted({r["handle"] for r in rows})
    return {"day": day, "views_today": len(people), "room": max(0, DAILY_VIEWS - len(people)), "people": people,
            "last_view_at": rows[-1]["at"] if rows else None}


def view(root: Path, *, person: str, url: str, named_by: str, now: datetime, lane: str = "") -> tuple[bool, str]:
    handle = _handle(url)
    if handle is None:
        return True, "not LinkedIn: read it; no view is counted and nothing is logged"
    if not handle:
        return False, f"refused: {url} is not one person's profile (linkedin.com/in/<handle>); never a search, list or feed"
    if not str(named_by or "").strip():
        return False, "refused: only named people are read; say who named this person (--named-by)"
    root, now = Path(root), now.astimezone(timezone.utc)
    all_rows = read_jsonl(root / LOG)
    s = status(root, now)
    if handle in s["people"]:
        return True, f"allowed: {person} already viewed today; not counted again ({s['views_today']} of {DAILY_VIEWS})"
    if all_rows:
        last = datetime.fromisoformat(all_rows[-1]["at"])
        gap = (now - last).total_seconds()
        if gap < MIN_GAP_SECONDS:
            return False, f"refused: human pace; wait {int(MIN_GAP_SECONDS - gap) + 1} s after the last view"
    if s["views_today"] >= DAILY_VIEWS:
        return False, f"refused: {DAILY_VIEWS} people already viewed on {s['day']}; the cap resets tomorrow"
    append_jsonl(root / LOG, {"day": s["day"], "at": now.isoformat(), "handle": handle, "person": person,
                              "url": url, "named_by": named_by.strip(), "lane": lane})
    return True, f"allowed: {person} ({s['views_today'] + 1} of {DAILY_VIEWS} today)"


def _job_id(url: str) -> str | None:
    """The LinkedIn posting id of one job page; '' for any other LinkedIn page; None off LinkedIn."""
    p = urlparse(url)
    if not p.netloc.lower().endswith("linkedin.com"):
        return None
    m = JOB_PAGE.match(p.path)
    return m.group(1) if m else ""


def job_view(root: Path, *, job: str, url: str, now: datetime) -> tuple[bool, str]:
    lid = _job_id(url)
    if lid is None:
        return True, "not LinkedIn: read it by hand; no view is counted and nothing is logged"
    if not lid:
        return False, f"refused: {url} is not one job page (linkedin.com/jobs/view/<id>); never a search, list or feed"
    root, now = Path(root), now.astimezone(timezone.utc)
    day = _day(now)
    path = root / READ_LIST / f"read-list-{day}.json"
    try:
        listed = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    except ValueError:
        listed = []
    entry = next((r for r in listed if isinstance(r, dict) and r.get("id") == job), None)
    if entry is None or _job_id(str(entry.get("url") or "")) != lid:
        return False, (f"refused: {job} with posting {lid} is not on today's read list ({path.relative_to(root)}); "
                       "run jd_capture.py run first")
    rows = read_jsonl(root / JOB_LOG)
    today = [r for r in rows if r.get("day") == day]
    if any(r.get("job") == job for r in today):
        return True, f"allowed: {job} already opened today; not counted again"
    if rows:
        gap = (now - datetime.fromisoformat(rows[-1]["at"])).total_seconds()
        if gap < MIN_GAP_SECONDS:
            return False, f"refused: human pace; wait {int(MIN_GAP_SECONDS - gap) + 1} s after the last job page"
    if len({r.get("job") for r in today}) >= JOB_VIEWS_PER_DAY:
        return False, f"refused: {JOB_VIEWS_PER_DAY} job pages already read on {day}; the rest wait for tomorrow"
    append_jsonl(root / JOB_LOG, {"day": day, "at": now.isoformat(), "job": job, "linkedin_id": lid, "url": url})
    return True, f"allowed: {job} ({len({r.get('job') for r in today}) + 1} of {JOB_VIEWS_PER_DAY} today); read only"


def click_check(label: str, *, allow_connect: bool = False, yes: str = "", root: Path | None = None,
                now: datetime | None = None) -> tuple[bool, str]:
    """Reading controls pass; a plain LinkedIn connect or follow passes only with allow_connect, a recorded yes and
    pace.py's check at go; every other control that signals to the person is refused."""
    text = re.sub(r"\s+", " ", str(label or "")).strip()
    if READING.match(text):
        return True, f"reading control: '{text}'"
    action = PLAIN_CONNECT.get(text.lower())
    if action and allow_connect:
        if not str(yes or "").strip():
            return False, (f"refused: '{text}' needs the owner's yes recorded (--yes <approval id or cards decision>); "
                           f"{CONNECT_RULE}")
        ok, why = pace.check(Path(root or JS_ROOT), "linkedin", action,
                             (now or datetime.now(timezone.utc)).astimezone(timezone.utc))
        if not ok:
            return False, f"refused: '{text}' signals to the person ({action}); pace.py check: {why}"
        return True, (f"allowed: plain {action} on LinkedIn ({why}; yes: {yes.strip()}); after it lands run "
                      f"`python3 scripts/pace.py record --platform linkedin --action {action}`")
    m = FORBIDDEN.search(text)
    if m:
        if action:
            return False, f"refused: '{text}' signals to the person ({m.group(0)}); {CONNECT_RULE}"
        return False, f"refused: '{text}' signals to the person ({m.group(0)}); reading only"
    return False, f"refused: unknown control '{text}'; only reading controls are clicked"


_STATES = {"alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar", "california": "ca", "colorado": "co",
           "connecticut": "ct", "delaware": "de", "florida": "fl", "georgia": "ga", "hawaii": "hi", "idaho": "id",
           "illinois": "il", "indiana": "in", "iowa": "ia", "kansas": "ks", "kentucky": "ky", "louisiana": "la",
           "maine": "me", "maryland": "md", "massachusetts": "ma", "michigan": "mi", "minnesota": "mn",
           "mississippi": "ms", "missouri": "mo", "montana": "mt", "nebraska": "ne", "nevada": "nv",
           "new hampshire": "nh", "new jersey": "nj", "new mexico": "nm", "new york": "ny", "north carolina": "nc",
           "north dakota": "nd", "ohio": "oh", "oklahoma": "ok", "oregon": "or", "pennsylvania": "pa",
           "rhode island": "ri", "south carolina": "sc", "south dakota": "sd", "tennessee": "tn", "texas": "tx",
           "utah": "ut", "vermont": "vt", "virginia": "va", "washington": "wa", "west virginia": "wv",
           "wisconsin": "wi", "wyoming": "wy"}
_PLACE_NOISE = re.compile(r"\b(greater|metropolitan|metro|area|region|bay|united states|usa|us)\b")


def _words(text: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(text or "").lower())).strip()


def normalize(field: str, value: Any) -> str:
    if field == "employer":
        return normalize_company(str(value or ""))
    if field == "location":
        s = _words(value)
        for name, code in sorted(_STATES.items(), key=lambda kv: -len(kv[0])):
            s = re.sub(rf"\b{name}\b", code, s)
        s = _PLACE_NOISE.sub(" ", s)
        s = re.sub(r"\b(" + "|".join(sorted(set(_STATES.values()))) + r")\b", " ", s)
        return re.sub(r"\s+", " ", s).strip()
    if field == "title":
        return _words(str(value or "").replace("&", " and "))      # "Founder & CEO" is "Founder and CEO"
    return _words(value)


def _publisher(obs: dict[str, Any]) -> str:
    pub = str(obs.get("publisher") or "").lower().strip()
    if not pub:
        pub = urlparse(str(obs.get("url") or "")).netloc.lower()
    return pub[4:] if pub.startswith("www.") else pub


def _compare(a: str, b: str) -> str:
    if a == b:
        return "match"
    if a and b and (re.search(rf"(?:^| ){re.escape(a)}(?: |$)", b) or re.search(rf"(?:^| ){re.escape(b)}(?: |$)", a)):
        return "compatible"
    return "differs"


def _compare_names(a: str, b: str) -> str:
    """Names (normalized): the same surname plus given names that differ only by a middle initial ("mark h ren" and
    "mark ren") or written as initials ("yd tsai", "y d tsai" and "yun da tsai") are compatible; else _compare."""
    if a == b:
        return "match"
    ta, tb = a.split(), b.split()
    if len(ta) >= 2 and len(tb) >= 2 and ta[-1] == tb[-1]:
        ga, gb = ta[:-1], tb[:-1]
        full_a, full_b = [t for t in ga if len(t) > 1], [t for t in gb if len(t) > 1]
        if full_a and full_a == full_b:
            return "compatible"                                   # a middle initial on one side
        for x, y in ((ga, gb), (gb, ga)):
            initials = "".join(t[0] for t in y)
            as_initials = "".join(x) if all(len(t) == 1 for t in x) else (x[0] if len(x) == 1 and len(x[0]) <= 3 else "")
            if as_initials and as_initials == initials:
                return "compatible"                               # "yd" or "y d" for "yun da"
    return _compare(a, b)


def verify(observations: list[dict[str, Any]]) -> dict[str, Any]:
    by_pub: dict[str, dict[str, Any]] = {}
    for obs in observations:                                   # one publisher is one source, whatever its page count
        by_pub.setdefault(_publisher(obs), {}).update({k: v for k, v in obs.items() if v not in (None, "")})
    pubs = [p for p in by_pub if p]
    rows, states = [], {}
    for field in FIELDS:
        seen = [(p, by_pub[p][field]) for p in pubs if by_pub[p].get(field)]
        values = [normalize(field, v) for _, v in seen]
        if len(seen) < 2:
            state = "one source" if seen else "no source"
        else:
            cmp = _compare_names if field == "name" else _compare
            pair = {cmp(a, b) for i, a in enumerate(values) for b in values[i + 1:]}
            state = "differs" if "differs" in pair else "compatible" if "compatible" in pair else "match"
        states[field] = state
        rows.append(f"| {field} | " + "; ".join(f"{p}: {v}" for p, v in seen) + f" | {state} |")
    if len(pubs) < 2:
        verdict = "UNVERIFIED"
    elif any(s == "differs" for s in states.values()):
        verdict = "CONFLICT"
    elif all(s == "match" for s in states.values()):
        verdict = "VERIFIED"
    else:
        verdict = "PROBABLE"
    weak = [f for f, s in states.items() if s != "match"]
    line = f"Identity: {verdict} (sources: {', '.join(pubs) or 'none'}" + \
           (f"; not matched: {', '.join(f'{f} {states[f]}' for f in weak)})" if weak else "; every field matched)")
    table = "| field | seen | state |\n|---|---|---|\n" + "\n".join(rows)
    return {"verdict": verdict, "states": states, "sources": pubs, "line": line, "table": table}


def _now(text: str | None) -> datetime:
    if not text:
        return datetime.now(timezone.utc)
    t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _load(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        data = json.loads(text)
    else:
        import yaml
        data = yaml.safe_load(text)
    data = data.get("observations", data) if isinstance(data, dict) else data
    return [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in d.items()} for d in data or []]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(JS_ROOT), help="job-search department root")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("view")
    v.add_argument("--person", required=True)
    v.add_argument("--url", required=True)
    v.add_argument("--named-by", required=True)
    v.add_argument("--lane", default="")
    v.add_argument("--now")
    j = sub.add_parser("job-view")
    j.add_argument("--job", required=True)
    j.add_argument("--url", required=True)
    j.add_argument("--now")
    c = sub.add_parser("click-check")
    c.add_argument("label")
    c.add_argument("--allow-connect", action="store_true",
                   help="a plain LinkedIn connect or follow, with --yes and pace.py check at go")
    c.add_argument("--yes", default="", help="where the owner's yes for this act is recorded (approval id, cards decision)")
    c.add_argument("--now")
    ve = sub.add_parser("verify")
    ve.add_argument("file")
    s = sub.add_parser("status")
    s.add_argument("--now")
    args = ap.parse_args(argv)
    root = Path(args.root)
    if args.cmd == "view":
        ok, msg = view(root, person=args.person, url=args.url, named_by=args.named_by, now=_now(args.now),
                       lane=args.lane)
        print(msg)
        return 0 if ok else 3
    if args.cmd == "job-view":
        ok, msg = job_view(root, job=args.job, url=args.url, now=_now(args.now))
        print(msg)
        return 0 if ok else 3
    if args.cmd == "click-check":
        ok, msg = click_check(args.label, allow_connect=args.allow_connect, yes=args.yes, root=root,
                              now=_now(args.now))
        print(msg)
        return 0 if ok else 2
    if args.cmd == "verify":
        result = verify(_load(Path(args.file)))
        print(result["table"] + "\n\n" + result["line"])
        return {"VERIFIED": 0, "CONFLICT": 3}.get(result["verdict"], 1)
    print(json.dumps(status(root, _now(args.now)), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
