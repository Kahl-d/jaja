"""dash_api.py: the data layer of the job agency dashboard. Read only, except `run_write`, which runs the
agency's own scripts.

Every number comes from the agency's loaders, never from a new reading of a record format:
  applications  scripts/tracker.py (iter_folders, folder_kind, parse_events, project, metrics, next_action_for)
  jobs          scripts/fetch_ats.py load_jobs and load_companies (targets/companies.yaml)
  follow-ups    scripts/follow_ups.py collect
  portals       scripts/portal_digest.py digest; names from scripts/portal_registry.py
  pursuits      scripts/pursuits.py rows
  tiers         scripts/agency_status.py batches (state/apply-batches/*.md), else the rule (see `tier_for`)
  role family   scripts/focus_profiles.py pick on each title
  the Scout     scripts/scout_board.py row_ids (the ROW lines of hub/lanes/HUNT.md)
  clock         scripts/dashboard.py clock_status (clock.yaml through scripts/clock.py)
  records       js_common.iter_records / read_record for prospects and company dossiers; tracker's fm for fit.md
Two hub tables have no script reader, so they are read here by their header row: hub/NEXT-UP.md (the orchestrator's ranked list)
and the H1 example names of targets/segments/*.md (the only place a segment names member companies).

Writes (`command_for`, `run_write`): a stage event runs `scripts/tracker.py event`; attaching a person to a role runs
`scripts/prospects.py set <person> applications+=<slug>`; a person's tag runs `scripts/prospects.py set --tag`; an
application or job tag runs `dashboard/dash_tags.py` (state/dashboard-tags.yaml); a company segment runs
`scripts/segment_companies.py set` (companies.yaml is the one source). Nothing outward
exists here: no send, post, submit or connector call.
"""
from __future__ import annotations

import contextlib
import json
import re
import shlex
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
JS_DEFAULT = HERE.parent
SCRIPTS = JS_DEFAULT / "scripts"
for _p in (str(HERE), str(SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import yaml  # noqa: E402

import agency_config as cfg  # noqa: E402
import agency_status  # noqa: E402
import dash_tags  # noqa: E402
import dashboard as js_dash  # noqa: E402  (scripts/dashboard.py)
import fetch_ats as fa  # noqa: E402
import focus_profiles as fp  # noqa: E402
import follow_ups  # noqa: E402
import hunt_score  # noqa: E402
import js_common as jc  # noqa: E402
import portal_digest  # noqa: E402
import portal_registry  # noqa: E402
import pursuits as pursuit_records  # noqa: E402
import scout_board  # noqa: E402
import tracker as tr  # noqa: E402

SEGMENTS = dash_tags.SEGMENTS
TIERS = ("dream", "focused", "mass")
DREAM_FIT = float(cfg.get("policy.tiers.dream_fit", 4.5))      # fit score that makes a role dream-tier when nothing is recorded
FOCUSED_FIT = float(cfg.get("policy.tiers.focused_fit", 4.0))  # fit score that makes a role focused-tier
APPLIED = frozenset(tr.PROGRESS[tr.PROGRESS.index("applied"):])
INTERVIEW = frozenset({"screen", "oa", "interviewing", "final", "offer", "accepted"})
OPEN_STAGES = frozenset({"discovered", "fit_checked", "tailored"})
FRESH_HOURS = 72
CLOSED_JOB = ("closed", "skipped")
# prospect company_type (schemas/prospect.yaml) -> dashboard segment
PROSPECT_TYPE_SEGMENT = {"startup": "startup", "scaleup": "growth", "bigtech": "big-tech", "ai-lab": "ai-lab",
                         "enterprise": "enterprise"}                     # scaleup is growth
# targets/companies.yaml `segment:` (the hunt scripts read startup | mid | giant) -> dashboard segment
WATCHLIST_SEGMENT = {"startup": "startup", "growth": "growth", "giant": "big-tech", "big-tech": "big-tech",
                     "ai-lab": "ai-lab", "enterprise": "enterprise"}                  # "growth" is a segment of its own
STARTUP_STAGE = re.compile(r"\b(pre-seed|seed|series [abc]\b|y ?combinator|\byc\b)", re.I)
QUEUE_STATES = ("READY", "PACKAGE", "JOB", "BLOCKED")
QUEUE_NEXT = {"READY": "apply: the page is ready, the owner submits", "PACKAGE": "build the page from the package",
              "JOB": "package, then the page", "BLOCKED": "the owner decides"}
STAGE_EVENTS = ("applied", "confirmation_received", "screen", "oa", "interview", "final", "offer", "rejected",
                "withdrawn", "on_hold", "email_in", "follow_up", "note")
APPLIED_VIA = tuple(tr.APPLIED_VIA)
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,160}$")
WRITE_LOG = Path("state") / "dashboard-writes.jsonl"


# ============================================================================ helpers
def _day(value: Any) -> date | None:
    return jc.parse_date(value)


def _ts(value: Any) -> datetime | None:
    return fa.parse_ts(value)


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def obsidian(os_root: Path, path: Path | None) -> str | None:
    """obsidian://open for a file inside the vault (the vault root; the vault name is its folder name)."""
    if path is None:
        return None
    try:
        rel = Path(path).resolve().relative_to(Path(os_root).resolve())
    except ValueError:
        return None
    return f"obsidian://open?vault={quote(Path(os_root).resolve().name)}&file={quote(rel.as_posix())}"


@contextlib.contextmanager
def jobs_loaded(jobs: dict[str, dict[str, Any]] | None, memo: dict[str, Any] | None = None) -> Iterator[None]:
    """Hand the jobs already loaded to the loaders that would re-read jobs/ (load_jobs reads every file: about 9 s on
    5,000 postings), for the length of one snapshot build."""
    if jobs is None:
        yield
        return
    saved = (fa.load_jobs, tr.load_jobs, tr.metrics)
    fa.load_jobs = tr.load_jobs = lambda root: jobs  # type: ignore[assignment]
    memo = {} if memo is None else memo              # one dict across a build's blocks

    def metrics_once(root: Any, now: Any) -> Any:     # one tracker.metrics per build: the funnel, fresh10, interviews
        key = str(Path(root).resolve())
        if key not in memo:
            memo[key] = saved[2](root, now)
        return memo[key]
    tr.metrics = metrics_once  # type: ignore[assignment]
    try:
        yield
    finally:
        fa.load_jobs, tr.load_jobs, tr.metrics = saved


def load_jobs(js: Path) -> dict[str, dict[str, Any]] | None:
    """jobs/ through the on-disk parse cache (dashboard/jobs_cache.py): a restart re-parses only changed files."""
    import jobs_cache
    return jobs_cache.load(js) if (Path(js) / "jobs").is_dir() else None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 3) if den else None


# ============================================================================ companies and segments
class Companies:
    """The watchlist (tier), dossiers, prospects' company_type, the segment lenses and the dashboard's segment picks."""

    def __init__(self, js: Path, prospects: list[dict[str, Any]], overlay: dict[str, str]) -> None:
        self.js = Path(js)
        self.jobs: dict[str, dict[str, Any]] | None = None     # set by build to the loaded jobs/
        self.watch = {str(c.get("slug")): c for c in fa.load_companies(js) if c.get("slug")}
        self.dossiers = {str(m.get("slug") or p.stem): m for p, m, _ in jc.iter_records(Path(js) / "companies")}
        self.overlay = overlay
        self.lens_names = self._lens_names(js)
        votes: dict[str, Counter] = defaultdict(Counter)
        for m in prospects:
            seg = PROSPECT_TYPE_SEGMENT.get(str(m.get("company_type") or ""))
            if seg and m.get("company_slug"):
                votes[str(m["company_slug"])][seg] += 1
        self.prospect_votes = votes

    def _evidence(self):
        if getattr(self, "_ev", None) is None:
            import segment_companies
            self._ev = segment_companies.Evidence(self.js, jobs=self.jobs)     # the build's jobs, not a re-read
        return self._ev

    @staticmethod
    def _lens_names(js: Path) -> dict[str, str]:
        """normalized company name -> segment, from the H1 example list of each targets/segments/<segment>.md."""
        out: dict[str, str] = {}
        folder = Path(js) / "targets" / "segments"
        for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
            if path.stem not in SEGMENTS:
                continue
            m = re.search(r"^# [^(\n]*\(([^)\n]*)\)", path.read_text(encoding="utf-8"), re.M)
            if not m:
                continue
            names = m.group(1).split(":", 1)[-1]
            for name in names.split(","):
                key = jc.normalize_company(name.replace("...", ""))
                if key and not key.startswith(("consulting", "seed", "roughly")):
                    out[key] = path.stem
        return out

    def tier(self, slug: str | None) -> str | None:
        if not slug:
            return None
        c = self.watch.get(slug) or {}
        return c.get("tier") or (self.dossiers.get(slug) or {}).get("tier")

    def segment(self, slug: str | None, name: str | None = None) -> tuple[str, str]:
        """(segment, source) in this order: companies.yaml `segment` (the owner's own picks included), the segment lens examples,
        the dossier's funding stage (seed to Series C or YC = startup), 100 or more FY2025 H-1B approvals on the
        watchlist (hunt_score.BIG_H1B, the hunt's own "big company" signal = big-tech), prospects' company_type, else
        unknown."""
        slug = slug or ""
        watch = WATCHLIST_SEGMENT.get(str((self.watch.get(slug) or {}).get("segment") or ""))
        if watch:
            return watch, "companies.yaml"
        keys = {k for k in (jc.normalize_company(name or ""), jc.normalize_company(slug.replace("-", " "))) if k}
        for key in keys:
            for lens_key, seg in self.lens_names.items():
                if key == lens_key or key.startswith(lens_key + " ") or lens_key.startswith(key + " "):
                    return seg, "segment lens"
        stage = str((self.dossiers.get(slug) or {}).get("stage") or "")
        if STARTUP_STAGE.search(stage):
            return "startup", "dossier stage"
        if slug not in self.watch:                     # off the watchlist, the recorded evidence (dossier,
            found, src = self._evidence().decide(name or slug, slug)      # job records), as strategy_review reads it
            if found:
                return found, str(src)
        if (_num((self.watch.get(slug) or {}).get("h1b_approvals_recent")) or 0) >= hunt_score.BIG_H1B:
            return "big-tech", "H-1B volume"
        votes = self.prospect_votes.get(slug)
        if votes:
            return votes.most_common(1)[0][0], "prospects"
        return "unknown", "none"


def _companies_with(c: "Companies", jobs: dict[str, dict[str, Any]] | None) -> "Companies":
    c.jobs = jobs
    return c


# ============================================================================ tiers and families
def tier_for(slug: str, fit: float | None, company_tier: str | None, batch_tier: dict[str, str],
             recorded: str | None = None, recorded_source: str | None = None) -> tuple[str, str]:
    """A role's tier: the tier recorded on the application (`tracker.py tier`; an inferred backfill says
    so), else the newest apply-batch sheet that lists it (agency_status.batches), else the NEXT-UP rule: dream =
    fit at DREAM_FIT or more, or a dream-tier company; focused = fit from FOCUSED_FIT up to DREAM_FIT; mass = everything
    else (thresholds: policy.tiers.dream_fit and policy.tiers.focused_fit)."""
    if recorded in TIERS:
        return recorded, ("inferred (backfill)" if str(recorded_source or "").startswith("inferred") else "recorded")
    if batch_tier.get(slug) in TIERS:
        return batch_tier[slug], "batch sheet"
    if (fit is not None and fit >= DREAM_FIT) or company_tier == "dream":
        return "dream", "rule"
    if fit is not None and fit >= FOCUSED_FIT:
        return "focused", "rule"
    return "mass", "rule"


class Families:
    def __init__(self, js: Path) -> None:
        self.js = Path(js)
        self.memo: dict[str, str] = {}

    def __call__(self, title: str | None) -> str:
        t = str(title or "").strip()
        if not t:
            return "unmatched"
        if t not in self.memo:
            ranked = fp.pick(self.js, t)
            self.memo[t] = ranked[0]["family"] if ranked else "unmatched"
        return self.memo[t]


# ============================================================================ applications
def _first_responses(events: list[tr.Event], applied_at: datetime | None,
                     last_inbound: Any) -> tuple[datetime | None, datetime | None]:
    """(first reply, first interview). A reply is any inbound event or a company stage (screen onwards, or a
    rejection); a confirmation is the ATS, not a reply. An interview is screen, oa, interviewing, final, offer or
    accepted. A company can move first (a recruiter books the screen, then the resume goes in), so events before the
    applied time count too; the days are then 0 (see `_days`)."""
    reply = interview = None
    for e in sorted(events, key=lambda e: (_ts(e.at) is None, _ts(e.at) or datetime.min.replace(tzinfo=timezone.utc))):
        at = _ts(e.at)
        if at is None:
            continue
        typ = e.type.strip().lower()
        target = typ.split(":", 1)[1] if typ.startswith("override:") else tr.EVENT_STAGE.get(typ)
        if reply is None and (typ in tr.INBOUND or target in tr.RESPONDED):
            reply = at
        if interview is None and target in INTERVIEW:
            interview = at
    inbound = _ts(last_inbound)
    if inbound and (applied_at is None or inbound >= applied_at) and (reply is None or inbound < reply):
        reply = inbound
    return reply, interview


def _days(at: datetime | None, applied_at: datetime | None) -> float | None:
    """Days from the application to `at`; 0 when the company moved before the application went in."""
    if at is None or applied_at is None:
        return None
    return max(0.0, round((at - applied_at).total_seconds() / 86400, 1))


READ_ERRORS: set[str] = set()      # files whose frontmatter does not parse (reported as a data gap)


def meta_of(path: Path) -> dict[str, Any]:
    """A record's frontmatter through the kernel reader; an unreadable one is noted and read as empty."""
    try:
        return tr.fm.load(path)[0]
    except Exception:  # noqa: BLE001 - one bad file must not blank the dashboard
        READ_ERRORS.add(str(path))
        return {}


def _package_meta(folder: Path) -> tuple[dict[str, Any], Path | None]:
    for name in ("fit.md", "jd.md"):
        p = folder / name
        if p.is_file():
            meta = meta_of(p)
            if meta:
                return meta, p
    return {}, None


def _fit_score(folder: Path, job: dict[str, Any]) -> float | None:
    p = folder / "fit.md"
    if p.is_file():
        meta = meta_of(p)
        for key in ("score", "fit_score"):
            if _num(meta.get(key)) is not None:
                return _num(meta.get(key))
    return _num(job.get("fit_score"))


def application_rows(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    js, os_root, now, jobs = ctx["js"], ctx["os_root"], ctx["now"], ctx["jobs"] or {}
    companies, families, overlay = ctx["companies"], ctx["families"], ctx["tags"]
    due = {r["who"]: r for r in ctx["follow"]["due"] if r.get("kind") == "application"}
    people = ctx["people_by_app"]
    listed = tr.package_only_slugs(js)
    rows = []
    for folder in tr.iter_folders(js):
        slug = folder.name
        kind = tr.folder_kind(folder, listed)
        if kind == "application":
            path = folder / "application.md"
            meta, body = tr.fm.load(path)
            events = tr.parse_events(body)
            proj = tr.project(events, now, meta.get("ghost_after_days"), meta.get("last_inbound_at"))
            stage, top = proj["stage"], proj["max_progress"]
            action, when = tr.next_action_for(proj, now)
        else:
            meta, path = _package_meta(folder)
            events, proj, stage, top = [], None, kind, None
            action, when = None, None
        job_id = tr._link_target(meta.get("job")) or meta.get("job_id")
        job = jobs.get(str(job_id or ""), {})
        cslug = str(meta.get("company_slug") or job.get("company_slug") or "") or None
        company = meta.get("company") or job.get("company") or (slug.split("-")[0] if slug else "")
        title = meta.get("title") or job.get("title") or ""
        fit = _fit_score(folder, job)
        ctier = companies.tier(cslug)
        tier, tier_src = tier_for(slug, fit, ctier, ctx["batch_tier"], meta.get("tier"), meta.get("tier_source"))
        segment, seg_src = companies.segment(cslug, company)
        applied_at = proj["applied_at"] if proj else None
        reply, interview = _first_responses(events, applied_at, meta.get("last_inbound_at"))
        last = max(events, key=lambda e: _ts(e.at) or datetime.min.replace(tzinfo=timezone.utc)) if events else None
        attached = sorted({p["id"]: p for p in people.get(slug, [])}.values(), key=lambda p: p["id"])
        rows.append({
            "slug": slug, "kind": kind, "company": company, "company_slug": cslug, "title": title,
            "stage": stage, "max_progress": top, "applied": bool(top in APPLIED),
            "applied_at": applied_at.date().isoformat() if applied_at else None,
            "applied_via": meta.get("applied_via"), "fit": fit, "tier": tier, "tier_source": tier_src,
            "company_tier": ctier, "segment": segment, "segment_source": seg_src, "family": families(title),
            "first_reply_at": reply.isoformat() if reply else None,
            "first_interview_at": interview.isoformat() if interview else None,
            "days_to_reply": _days(reply, applied_at), "days_to_interview": _days(interview, applied_at),
            "last_event": {"at": last.at, "type": last.type} if last else None,
            "next_action": meta.get("next_action") or action, "next_action_at": str(meta.get("next_action_at") or when or "") or None,
            "follow_up_due": (due.get(slug) or {}).get("due"),
            "people": attached, "tags": overlay["tags"]["application"].get(slug, []),
            "job": job_id, "job_link": obsidian(os_root, Path(js) / "jobs" / f"{job_id}.md") if job else None,
            "posted": str(_day(job.get("posted_at")) or "") or None,
            "sponsorship": job.get("sponsorship"), "link": obsidian(os_root, path),
        })
    return rows


# ============================================================================ the overview
def funnel(rows: list[dict[str, Any]], metrics: dict[str, Any] | None, jobs: dict[str, dict] | None,
           folders: dict[str, int]) -> list[dict[str, Any]]:
    """discovered (postings in jobs/, duplicates out) -> queued (application folders: every role the agency took on)
    -> applied -> replied -> screen -> interview -> offer. Applied, screen, interview and offer are tracker.py metrics'
    funnel (stages reached); replied is counted from the same event logs (`_first_responses`)."""
    f = (metrics or {}).get("funnel") or {}
    steps = [
        ("discovered", None if jobs is None else sum(1 for j in jobs.values() if not j.get("duplicate_of"))),
        ("queued", folders.get("folders", 0)),
        ("applied", f.get("applied", 0)),
        ("replied", sum(1 for r in rows if r["applied"] and r["first_reply_at"])),
        ("screen", f.get("screen", 0)),
        ("interview", f.get("interviewing", 0)),
        ("offer", f.get("offer", 0)),
    ]
    out, prev = [], None
    applied = f.get("applied", 0)
    for name, n in steps:
        out.append({"step": name, "count": n, "rate_from_previous": _rate(n or 0, prev) if prev else None,
                    "rate_from_applied": _rate(n or 0, applied) if name in ("replied", "screen", "interview", "offer")
                    else None})
        prev = n
    return out


def breakdown(rows: list[dict[str, Any]], key: str, order: tuple[str, ...]) -> list[dict[str, Any]]:
    """Per value of `key`: applied (reached applied), open (an application record not yet applied and not ended),
    packages (package-only folders) and the companies applied to."""
    buckets: dict[str, dict[str, Any]] = {}
    for r in rows:
        b = buckets.setdefault(r[key] or "unknown", {"value": r[key] or "unknown", "applied": 0, "open": 0,
                                                     "packages": 0, "companies": set()})
        if r["applied"]:
            b["applied"] += 1
            b["companies"].add(r["company_slug"] or r["company"])
        elif r["kind"] == "application" and r["stage"] in OPEN_STAGES:
            b["open"] += 1
        elif r["kind"] != "application":
            b["packages"] += 1
    rank = {v: i for i, v in enumerate(order)}
    out = [{**b, "companies": len(b["companies"]), "total": b["applied"] + b["open"] + b["packages"]}
           for b in buckets.values()]
    return sorted(out, key=lambda b: (rank.get(b["value"], len(order)), -b["total"], b["value"]))


def dream_coverage(rows: list[dict[str, Any]], companies: Companies) -> dict[str, Any]:
    dream = sorted(s for s, c in companies.watch.items() if c.get("tier") == "dream")
    applied = [r for r in rows if r["applied"]]
    at_dream = {r["company_slug"] for r in applied if r["company_slug"] in set(dream)}
    touched = {r["company_slug"] for r in rows if r["company_slug"] in set(dream)}
    return {"dream_companies": len(dream), "applied_companies": len(at_dream),
            "share_of_dream_applied": _rate(len(at_dream), len(dream)),
            "with_any_work": len(touched),
            "applications_at_dream": sum(1 for r in applied if r["company_slug"] in set(dream)),
            "applications": len(applied),
            "share_of_applications_at_dream": _rate(sum(1 for r in applied if r["company_slug"] in set(dream)),
                                                    len(applied)),
            "applied_names": sorted(at_dream), "watchlist_tiers": dict(Counter(str(c.get("tier")) for c in
                                                                              companies.watch.values()))}


def speed(rows: list[dict[str, Any]], today: date) -> dict[str, Any]:
    applied = [r for r in rows if r["applied"] and r["applied_at"]]

    def one(field: str) -> dict[str, Any]:
        days = [r[field] for r in applied if r[field] is not None]
        waiting = [(today - date.fromisoformat(r["applied_at"])).days for r in applied if r[field] is None]
        return {"measured": len(days), "median_days": _median(days),
                "within_7": sum(1 for d in days if d <= 7), "within_14": sum(1 for d in days if 7 < d <= 14),
                "over_14": sum(1 for d in days if d > 14), "none_yet": len(waiting),
                "oldest_wait_days": max(waiting) if waiting else None}

    return {"applied": len(applied), "reply": one("days_to_reply"), "interview": one("days_to_interview")}


def weeks(rows: list[dict[str, Any]], jobs: dict[str, dict] | None, js: Path, today: date) -> dict[str, Any]:
    """The last 7 days (today and the 6 before) against the 7 before them, on the department's day."""
    this_start, last_start = today - timedelta(days=6), today - timedelta(days=13)

    def which(d: date | None) -> str | None:
        if d is None or d > today or d < last_start:
            return None
        return "this" if d >= this_start else "last"

    def dept(value: Any) -> date | None:
        ts = _ts(value)
        return jc.dept_day(ts) if ts and ts.tzinfo else _day(value)

    counts = {k: Counter() for k in ("applied", "replies", "interviews", "discovered", "outreach_sent", "packages")}
    for r in rows:
        counts["applied"][which(_day(r["applied_at"]))] += 1
        counts["replies"][which(dept(r["first_reply_at"]))] += 1
        counts["interviews"][which(dept(r["first_interview_at"]))] += 1
    for j in (jobs or {}).values():
        if not j.get("duplicate_of"):
            counts["discovered"][which(dept(j.get("posted_at")))] += 1     # the posting date, never first_seen
    for t in jc.iter_touches(js):
        if t.get("direction") == "out" and t.get("status") in (None, "sent"):
            counts["outreach_sent"][which(dept(t.get("at")))] += 1
    for f in tr.iter_folders(js):
        created = None
        for name in ("application.md", "fit.md", "jd.md"):
            if (f / name).is_file():
                m = meta_of(f / name)
                created = _day(m.get("created") or m.get("scored_at") or m.get("snapshot_at"))
                break
        counts["packages"][which(created)] += 1
    return {"this_start": this_start.isoformat(), "last_start": last_start.isoformat(), "today": today.isoformat(),
            "rows": [{"metric": k, "this": v.get("this", 0), "last": v.get("last", 0),
                      "delta": v.get("this", 0) - v.get("last", 0)} for k, v in counts.items()]}


def gaps(rows: list[dict[str, Any]], companies: Companies, metrics: dict[str, Any] | None,
         jobs: dict[str, dict] | None, batch_tier: dict[str, str], prospects: list[dict[str, Any]],
         now: datetime) -> list[str]:
    out = []
    watch = companies.watch
    no_seg = sum(1 for c in watch.values() if not c.get("segment"))
    if no_seg:
        out.append(f"targets/companies.yaml: {no_seg} of {len(watch)} companies carry no `segment:`; segments here come "
                   f"from the dashboard picks, the segment lenses' named examples, dossier funding stage and prospects.")
    src = Counter(r["segment_source"] for r in rows)
    unknown = sum(1 for r in rows if r["segment"] == "unknown")
    if unknown:
        out.append(f"segment unknown for {unknown} of {len(rows)} roles (sources: "
                   + ", ".join(f"{k} {v}" for k, v in src.most_common()) + "); set one per company in Applications.")
    tiers = Counter(str(c.get("tier")) for c in watch.values())
    if tiers.get("dream", 0) > len(watch) / 2:
        out.append(f"targets/companies.yaml marks {tiers['dream']} of {len(watch)} companies `tier: dream`, "
                   f"so a dream-tier company is not a short list and the tier rule calls most roles dream.")
    if not batch_tier:
        out.append("state/apply-batches/ has no sheets, so every tier here is derived by the tier rule from fit and "
                   "company tier, not read from a batch.")
    if rows and not any(r["people"] for r in rows):
        out.append("no role has a person attached (application `prospects:` and prospect `applications:` are empty "
                   "apart from pursuits); attach people from Applications or People.")
    elif rows:
        none = sum(1 for r in rows if r["applied"] and not r["people"])
        if none:
            out.append(f"{none} applied roles have no person attached.")
    if jobs is not None:
        undated = sum(1 for j in jobs.values() if not _ts(j.get("posted_at")) and not j.get("duplicate_of"))
        if undated:   # fresh and 'posted this week' count posting dates only, so these never count as fresh
            out.append(f"{undated} postings carry no posting date: they never count as fresh or as posted this week.")
        unscored = sum(1 for j in jobs.values() if j.get("fit_score") in (None, "") and j.get("status") not in CLOSED_JOB
                       and not j.get("duplicate_of"))
        out.append(f"{unscored} open postings in jobs/ have no fit score (the hunt's backlog).")
    if metrics and metrics.get("warnings"):
        out.append(f"{len(metrics['warnings'])} event lines do not parse and are not counted (tracker.py warnings).")
    twins = Counter((str(r["company_slug"] or r["company"]).lower(), str(r["title"]).strip().lower())
                    for r in rows if r["kind"] == "application")
    dup = sorted(f"{c}: {t}" for (c, t), n in twins.items() if n > 1)
    if dup:
        out.append(f"{len(dup)} roles have more than one application record (same company and title), so they count "
                   f"twice: " + "; ".join(dup[:5]) + ".")
    if READ_ERRORS:
        out.append(f"{len(READ_ERRORS)} files have frontmatter that does not parse and were read as empty: "
                   + ", ".join(sorted(Path(p).relative_to(Path(p).parents[2]).as_posix() for p in READ_ERRORS)[:5]))
    if prospects:
        typed = sum(1 for m in prospects if PROSPECT_TYPE_SEGMENT.get(str(m.get("company_type") or "")))
        out.append(f"{typed} of {len(prospects)} prospects carry a usable company_type.")
    return out


# ============================================================================ queue, jobs, people, portals
def next_up(js: Path, os_root: Path, today: date, jobs: dict[str, dict] | None) -> dict[str, Any] | None:
    """hub/NEXT-UP.md: the table rows by their header, the waiting paragraph, the mass
    queue ids (enriched from jobs/)."""
    path = Path(js) / "hub" / "NEXT-UP.md"
    if not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    header: list[str] | None = None
    rows, notes, mass, section = [], {}, [], None
    for line in lines:
        if line.startswith("## "):
            section = line[3:].strip().lower()
            continue
        if line.startswith("**") and ":**" in line:
            label = line[2:line.index(":**")].strip()
            notes[label] = line[line.index(":**") + 3:].strip()
            continue
        if notes and not line.startswith(("|", "#", "-")) and line.strip() and section == "the list (top first)":
            last = list(notes)[-1]
            notes[last] += " " + line.strip()
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cells and cells[0] == "#":
                header = [c.lower() for c in cells]
                continue
            if header is None or re.match(r"^[\s:|-]+$", line.strip().strip("|")) or not re.match(r"^\d+[a-z]?$", cells[0]):
                continue
            cell = dict(zip(header, cells))
            company, _, role = (cell.get("role") or "").partition(" · ")
            state_text = cell.get("state") or ""
            state = next((s for s in QUEUE_STATES if s in state_text), "")
            posted = re.search(r"\d{4}-\d{2}-\d{2}", cell.get("posted") or "")
            age = (today - date.fromisoformat(posted.group(0))).days if posted else None
            fit = re.search(r"\d\.\d+", cell.get("fit") or "")
            rows.append({"rank": cells[0], "company": company.strip(), "role": role.strip(),
                         "fit": float(fit.group(0)) if fit else None, "fit_text": cell.get("fit"),
                         "why": cell.get("why here"), "chance": cell.get("chance"), "posted": cell.get("posted"),
                         "age_days": age,
                         "freshness": None if age is None else "fresh" if age <= 14 else "aging" if age <= 45 else "old",
                         "state": state or None, "state_text": state_text, "page": cell.get("page"),
                         "tier": (cell.get("tier") or "").lower() or None,
                         "next": QUEUE_NEXT.get(state, state_text)})
        elif section == "mass queue" and line.startswith("- "):
            jid = line[2:].split()[0] if line[2:].split() else ""
            j = (jobs or {}).get(jid)
            mass.append({"id": jid, "company": (j or {}).get("company"), "title": (j or {}).get("title"),
                         "fit": _num((j or {}).get("fit_score")), "status": (j or {}).get("status"),
                         "in_jobs": j is not None,
                         "link": obsidian(os_root, Path(js) / "jobs" / f"{jid}.md") if j is not None else None})
    return {"rows": rows, "notes": notes, "mass": mass, "link": obsidian(os_root, path),
            "counts": dict(Counter(r["state"] or "other" for r in rows))}


def job_rows(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    js, os_root, now, jobs = ctx["js"], ctx["os_root"], ctx["now"], ctx["jobs"] or {}
    companies, overlay = ctx["companies"], ctx["tags"]
    scout = set(ctx["scout_ids"])
    out = []
    for jid, j in jobs.items():
        seen, posted = _ts(j.get("first_seen")), _ts(j.get("posted_at"))
        hours = int((now - seen).total_seconds() // 3600) if seen else None
        posted_hours = (now - posted).total_seconds() / 3600 if posted else None
        cslug = str(j.get("company_slug") or "") or None
        lo, hi = _num(j.get("comp_min")), _num(j.get("comp_max"))
        out.append({
            "id": jid, "company": j.get("company"), "company_slug": cslug, "title": j.get("title"),
            "fit": _num(j.get("fit_score")), "verdict": j.get("fit_verdict"), "hunt": _num(j.get("hunt_score")),
            "status": j.get("status"), "posted": str(_day(j.get("posted_at")) or "") or None,
            "first_seen": seen.isoformat() if seen else None, "hours_since_seen": hours,
            "posted_at": posted.isoformat() if posted else None,
            # fresh means posted inside 72 h; a board's first scan stamps first_seen on every open role
            "fresh": posted_hours is not None and -1 <= posted_hours <= FRESH_HOURS, "company_tier": companies.tier(cslug),
            "sponsorship": j.get("sponsorship"), "location": j.get("location"), "remote": j.get("remote"),
            "comp": f"${lo / 1000:.0f}K to ${hi / 1000:.0f}K" if lo and hi else None,
            "scout": jid in scout, "duplicate_of": j.get("duplicate_of"),
            "application": tr._link_target(j.get("application")), "tags": overlay["tags"]["job"].get(jid, []),
            "url": j.get("apply_url") or j.get("source_url"), "link": obsidian(os_root, j.get("_path")),
        })
    return out


def query_jobs(rows: list[dict[str, Any]], params: dict[str, str]) -> dict[str, Any]:
    """Filter and page the job rows: q, fresh=1, min_fit, company_tier, sponsorship, scout=1, open=1, verdict,
    duplicates=1 (shown only when asked), sort (fit | recent | hunt), limit (max 500), offset."""
    q = (params.get("q") or "").strip().lower()
    min_fit = _num(params.get("min_fit"))
    tiers = {t for t in (params.get("company_tier") or "").split(",") if t}
    spons = {s for s in (params.get("sponsorship") or "").split(",") if s}
    verdicts = {v for v in (params.get("verdict") or "").split(",") if v}

    def keep(r: dict[str, Any]) -> bool:
        if r["duplicate_of"] and params.get("duplicates") != "1":
            return False
        if params.get("fresh") == "1" and not r["fresh"]:
            return False
        if params.get("scout") == "1" and not r["scout"]:
            return False
        if params.get("open") == "1" and r["status"] in CLOSED_JOB:
            return False
        if min_fit is not None and (r["fit"] is None or r["fit"] < min_fit):
            return False
        if tiers and (r["company_tier"] or "none") not in tiers:
            return False
        if spons and str(r["sponsorship"] or "unknown") not in spons:
            return False
        if verdicts and str(r["verdict"] or "unscored") not in verdicts:
            return False
        if q and q not in " ".join(str(r[k] or "") for k in ("id", "company", "title", "location")).lower() \
                and q not in " ".join(r["tags"]):
            return False
        return True

    hit = [r for r in rows if keep(r)]
    sort = params.get("sort") or "fit"
    if sort == "recent":
        hit.sort(key=lambda r: (r["posted_at"] or "", r["first_seen"] or "", r["id"]), reverse=True)   # newest posting first
    elif sort == "hunt":
        hit.sort(key=lambda r: (-(r["hunt"] or -1), r["id"]))
    else:
        hit.sort(key=lambda r: (-(r["fit"] if r["fit"] is not None else -1), r["first_seen"] or "", r["id"]))
    try:
        limit = max(1, min(int(params.get("limit") or 200), 500))
        offset = max(0, int(params.get("offset") or 0))
    except ValueError:
        limit, offset = 200, 0
    return {"total": len(hit), "offset": offset, "limit": limit, "rows": hit[offset:offset + limit]}


def _referrer_why(m: dict[str, Any], pursuit_roles: dict[str, str], watch_roles: dict[str, str]) -> str | None:
    pid = str(m.get("id"))
    if pid in pursuit_roles:
        return pursuit_roles[pid]
    if m.get("source") == "referral":
        return "source: referral"
    rel = str(m.get("relation") or "")
    if re.search(r"referral|introduced by", rel, re.I):
        return f"relation: {rel[:80]}"
    if watch_roles.get(pid) == "referrer":
        return "people watch: referrer"
    return None


def people(ctx: dict[str, Any], app_rows: list[dict[str, Any]]) -> dict[str, Any]:
    js, os_root = ctx["js"], ctx["os_root"]
    by_person: dict[str, list[str]] = defaultdict(list)
    for r in app_rows:
        for p in r["people"]:
            by_person[p["id"]].append(r["slug"])
    pursuit_roles: dict[str, str] = {}
    for prow in ctx["pursuits"]:
        meta = prow.get("_meta") or {}
        if meta.get("referrer"):
            pursuit_roles[str(meta["referrer"])] = f"referrer, pursuit {prow['id']}"
        if meta.get("introducer"):
            pursuit_roles.setdefault(str(meta["introducer"]), f"introducer, pursuit {prow['id']}")
    watch = ctx["people_watch"] or []
    watch_roles = {}
    names = {jc.normalize_company(m.get("name") or ""): str(m.get("id")) for m in ctx["prospects"]}
    for w in watch:
        pid = str(w.get("prospect") or "") or names.get(jc.normalize_company(w.get("name") or ""))
        if pid:
            watch_roles[pid] = str(w.get("role") or "")
            w["_prospect"] = pid
    due = {r["who"]: r.get("due") for r in ctx["follow"]["due"] if r.get("kind") == "prospect"}
    # who introduced whom (prospects.py INTRO_FIELDS introduced_by, asked_by); the reverse edges are derived here
    introduced: dict[str, list[str]] = {}
    asked_for: dict[str, list[str]] = {}
    for m in ctx["prospects"]:
        if m.get("introduced_by"):
            introduced.setdefault(str(m["introduced_by"]), []).append(str(m.get("id")))
        if m.get("asked_by"):
            asked_for.setdefault(str(m["asked_by"]), []).append(str(m.get("id")))
    rows = []
    for m in ctx["prospects"]:
        pid = str(m.get("id"))
        last = max((d for d in (_day(m.get("last_inbound_at")), _day(m.get("last_outbound_at"))) if d), default=None)
        why = _referrer_why(m, pursuit_roles, watch_roles)
        rows.append({"id": pid, "name": m.get("name"), "company": m.get("company"), "role": m.get("role"),
                     "status": m.get("status"), "priority": m.get("priority"), "warmth": m.get("warmth"),
                     "source": m.get("source"), "last_touch": last.isoformat() if last else None,
                     "next_touch_at": str(_day(m.get("next_touch_at")) or "") or None, "follow_up_due": due.get(pid),
                     "referrer": why, "tags": [str(t) for t in m.get("tags") or []],
                     "applications": sorted(set(by_person.get(pid, []))), "link": obsidian(os_root, m.get("_path")),
                     "linkedin_url": str(m.get("linkedin_url") or "") or None,
                     "introduced_by": str(m.get("introduced_by") or "") or None, "asked_by": str(m.get("asked_by") or "") or None,
                     "introduced": sorted(introduced.get(pid, [])), "asked_for": sorted(asked_for.get(pid, []))})
    rows.sort(key=lambda r: (str(r["priority"] or "P9"), -(int(r["warmth"] or 0)), str(r["name"] or "")))
    watch_rows = [{"name": w.get("name"), "company": w.get("company"), "role": w.get("role"), "why": w.get("why"),
                   "status": w.get("status"), "x_handle": w.get("x_handle"), "linkedin_url": w.get("linkedin_url"),
                   "prospect": w.get("_prospect"), "added": str(w.get("added") or "")} for w in watch]
    pursuits = [{k: p.get(k) for k in ("id", "title", "company_slug", "kind", "stage", "waiting_on", "next_action",
                                       "next_action_at", "referrer")} | {"link": p.get("link")} for p in ctx["pursuits"]]
    return {"prospects": rows, "watch": watch_rows, "pursuits": pursuits,
            "counts": {"prospects": len(rows), "referrers": sum(1 for r in rows if r["referrer"]),
                       "connected": sum(1 for r in rows if r["introduced_by"] or r["asked_by"] or r["introduced"] or r["asked_for"]),
                       "attached": sum(1 for r in rows if r["applications"]), "watch": len(watch_rows),
                       "pursuits": len(pursuits), "follow_ups_due": sum(1 for r in rows if r["follow_up_due"])}}


def portals(ctx: dict[str, Any]) -> dict[str, Any] | None:
    js, now = ctx["js"], ctx["now"]
    try:
        d = portal_digest.digest(js, now, follow=lambda root, day: ctx["follow"])
    except Exception as exc:  # noqa: BLE001 - the digest belongs to another script; degrade, do not crash the page
        return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    names = {str(e.get("id")): e.get("name") for e in portal_registry.load(js).get("platforms") or []}
    for r in d["portals"]:
        r["name"] = names.get(r["id"]) or r["id"]
        r["setup_hint"] = portal_digest._setup_hint(r) if r["setup_pending"] else None
    waiting = [{k: r.get(k) for k in ("portal", "company", "title", "stage", "due", "days_overdue", "next")}
               for r in ctx["follow"]["due"] if r.get("kind") == "portal-message"]
    return {**d, "waiting": waiting, "names": names}


def _portal_inventory(js: Path) -> dict[str, Any]:
    """The portal inventory: clicks and read-back findings (portals_inv.py); an error is shown, not raised."""
    try:
        import portals_inv
        return portals_inv.load(js)
    except Exception as exc:  # noqa: BLE001 - the page says it was not read
        return {"read": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}", "portals": [], "counts": None}


# ============================================================================ snapshot
def _people_by_app(app_meta: dict[str, dict[str, Any]], prospects: list[dict[str, Any]],
                   pursuits: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Every person attached to a role: the application's `prospects:`, the prospect's `applications:`, and a pursuit's
    people for the applications it names."""
    names = {str(m.get("id")): m.get("name") for m in prospects}
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for slug, meta in app_meta.items():
        for pid in meta.get("prospects") or []:
            out[slug].append({"id": str(pid), "name": names.get(str(pid)), "via": "application"})
    for m in prospects:
        for slug in m.get("applications") or []:
            out[str(slug)].append({"id": str(m.get("id")), "name": m.get("name"), "via": "prospect"})
    for p in pursuits:
        meta = p.get("_meta") or {}
        ids = [meta.get("referrer"), meta.get("introducer"), *(meta.get("contacts") or [])]
        for slug in meta.get("applications") or []:
            for pid in [i for i in ids if i]:
                out[str(slug)].append({"id": str(pid), "name": names.get(str(pid)), "via": f"pursuit {p['id']}"})
    return out


def _yaml_rows(path: Path, key: str) -> list[dict[str, Any]] | None:
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [r for r in (data.get(key) if isinstance(data, dict) else data) or [] if isinstance(r, dict)]


def scouts(js: Path) -> dict[str, Any]:
    """The newest saved scout coverage (scout_coverage.summary); never recomputed per request."""
    import scout_coverage
    return scout_coverage.summary(js)


def build(js: Path, os_root: Path, now: datetime, jobs: dict[str, dict] | None = None,
          load: bool = True) -> dict[str, Any]:
    """The whole snapshot. `jobs` may be passed in (the server caches them); with load=False and no jobs the job-based
    numbers are absent (None), never zero."""
    js, os_root = Path(js), Path(os_root)
    if jobs is None and load:
        jobs = load_jobs(js)
    READ_ERRORS.clear()
    import concurrent.futures
    import crm
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    calendar_future = pool.submit(crm.calendar_window, js, now)    # network, in parallel with the local reads
    pool.shutdown(wait=False)
    today = jc.dept_day(now)
    prospects = []
    for p, m, _ in jc.iter_records(js / "prospects"):
        prospects.append({**m, "id": m.get("id") or p.stem, "_path": p})
    pursuits = []
    if (js / "pursuits").is_dir():
        for row in pursuit_records.rows(js):
            path = js / "pursuits" / row["id"] / "pursuit.md"
            pursuits.append({**row, "_meta": pursuit_records.load(js, row["id"])[0], "link": obsidian(os_root, path)})
    overlay = dash_tags.load(js)
    app_meta = {slug: meta for slug, meta, _ in tr.iter_applications(js) or []}
    batch_tier: dict[str, str] = {}
    for b in agency_status.batches(js):
        for slug in b.get("slugs") or []:
            batch_tier[str(slug)] = str(b.get("tier") or "mass")
    memo: dict[str, Any] = {}
    with jobs_loaded(jobs, memo):
        metrics = tr.metrics(js, now) if app_meta else None
        follow = follow_ups.collect(js, today)
        ctx = {"js": js, "os_root": os_root, "now": now, "jobs": jobs, "tags": overlay, "follow": follow,
               "companies": _companies_with(Companies(js, prospects, overlay["company_segments"]), jobs), "families": Families(js),
               "batch_tier": batch_tier, "prospects": prospects, "pursuits": pursuits,
               "people_by_app": _people_by_app(app_meta, prospects, pursuits),
               "people_watch": _yaml_rows(js / "targets" / "people-watch.yaml", "people"),
               "scout_ids": scout_board.row_ids(js)}
        rows = application_rows(ctx)
        portal = portals(ctx) if jobs is not None else None
    folders = tr.folder_counts(js)
    applied_rows = [r for r in rows if r["applied"]]
    fam_order = tuple(sorted({r["family"] for r in rows}))
    snapshot = {
        "generated_at": fa.iso_utc(now), "today": today.isoformat(),
        "vault": os_root.resolve().name, "clock": js_dash.clock_status(js, today),
        "overview": {
            "funnel": funnel(rows, metrics, jobs, folders),
            "folders": folders,
            "by_tier": breakdown(rows, "tier", TIERS),
            "by_segment": breakdown(rows, "segment", (*SEGMENTS, "unknown")),
            "by_family": breakdown(rows, "family", fam_order),
            "by_company": sorted(Counter(r["company"] for r in applied_rows).items(), key=lambda kv: (-kv[1], kv[0])),
            "dream": dream_coverage(rows, ctx["companies"]),
            "speed": speed(rows, today),
            "weeks": weeks(rows, jobs, js, today),
            "response_by_source": (metrics or {}).get("response_rate_by_source"),
            "response_by_via": (metrics or {}).get("response_rate_by_applied_via"),
            "apply_latency_hours": (metrics or {}).get("apply_latency_hours"),
            "outcomes": (metrics or {}).get("outcomes"),
            "follow_ups_due": len(follow["due"]),
            "gaps": gaps(rows, ctx["companies"], metrics, jobs, batch_tier, prospects, now),
            "scouts": scouts(js),
        },
        "applications": rows,
        "queue": next_up(js, os_root, today, jobs),
        "people": people(ctx, rows),
        "portals": portal,
        "portal_inventory": _portal_inventory(js),
        "follow_ups": [{k: r.get(k) for k in ("kind", "who", "company", "title", "stage", "due", "days_overdue",
                                              "portal", "next")} for r in follow["due"]],
        "choices": {"events": STAGE_EVENTS, "via": APPLIED_VIA, "segments": SEGMENTS,
                    "prospects": [{"id": m["id"], "name": m.get("name"), "company": m.get("company")} for m in prospects],
                    "applications": [{"slug": r["slug"], "company": r["company"], "title": r["title"]} for r in rows]},
        "jobs_loaded": jobs is not None,
    }
    with jobs_loaded(jobs, memo):                      # fresh10 and interviews reuse this build's jobs and metrics
        snapshot["crm"] = crm.build(js, os_root, now, snapshot, prospects=prospects,
                                    calendar=lambda first, last: calendar_future.result())
    snapshot["_jobs"] = job_rows(ctx) if jobs is not None else []
    snapshot["crm"]["companies"] = crm.companies(list(ctx["companies"].watch.values()), snapshot["_jobs"],
                                                 snapshot["applications"], prospects, snapshot["crm"].get("touches"))
    snapshot["jobs_summary"] = None if jobs is None else {
        "total": len(jobs), "fresh_72h": sum(1 for r in snapshot["_jobs"] if r["fresh"] and not r["duplicate_of"]),
        "no_posted_date": sum(1 for r in snapshot["_jobs"] if not r["posted_at"] and not r["duplicate_of"]),
        "scout": sum(1 for r in snapshot["_jobs"] if r["scout"]),
        "scored": sum(1 for r in snapshot["_jobs"] if r["fit"] is not None)}
    return snapshot


# ============================================================================ writes (the agency's scripts only)
class WriteRefused(ValueError):
    """The write was refused before any command ran."""


def _id(value: Any, what: str) -> str:
    v = str(value or "").strip()
    if not ID_RE.match(v):
        raise WriteRefused(f"{what} is not a record id: {v!r}")
    return v


def _one_line(value: Any, limit: int, what: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        raise WriteRefused(f"{what} is required")
    if len(text) > limit:
        raise WriteRefused(f"{what} is longer than {limit} characters")
    return text


def command_for(action: dict[str, Any], js: Path) -> list[str]:
    """The exact argv a write runs (relative script paths, run from the department folder). --root is added only when
    the dashboard serves another tree than the scripts' own (tests, a copy)."""
    js = Path(js).resolve()
    root = [] if js == JS_DEFAULT.resolve() else ["--root", str(js)]
    op = str(action.get("op") or "")
    if op == "stage":
        slug = _id(action.get("slug"), "application")
        if not (js / "applications" / slug).is_dir():
            raise WriteRefused(f"no folder applications/{slug}/")
        etype = str(action.get("type") or "")
        if etype not in STAGE_EVENTS:
            raise WriteRefused(f"event type must be one of {', '.join(STAGE_EVENTS)}")
        argv = ["scripts/tracker.py", *root, "event", slug, etype, "--source", "manual",
                "--evidence", _one_line(action.get("evidence"), 240, "evidence (what happened, with its source)")]
        if action.get("at"):
            at = str(action["at"]).strip()
            if not _ts(at):
                raise WriteRefused("at must be an ISO time, e.g. 2026-01-15T09:30:00-08:00")
            argv += ["--at", at]
        if action.get("via"):
            if action["via"] not in APPLIED_VIA:
                raise WriteRefused(f"via must be one of {', '.join(APPLIED_VIA)}")
            argv += ["--via", str(action["via"])]
        if etype == "applied":                    # an applied event names the page that went
            resume = _one_line(action.get("resume"), 300, "resume (the PDF path under resumes/, its sha256, or none)")
            argv += ["--resume", resume]
            if resume.lower() == "none":
                argv += ["--reason", _one_line(action.get("reason"), 200, "reason (why no PDF went)")]
        return argv
    if op in ("attach", "detach"):
        person, slug = _id(action.get("person"), "person"), _id(action.get("slug"), "application")
        if not (js / "prospects" / f"{person}.md").is_file():
            raise WriteRefused(f"no prospect prospects/{person}.md")
        if not (js / "applications" / slug).is_dir():
            raise WriteRefused(f"no folder applications/{slug}/")
        return ["scripts/prospects.py", *root, "set", person, f"applications{'+=' if op == 'attach' else '-='}{slug}"]
    if op in ("person-tag", "person-untag"):
        person = _id(action.get("person"), "person")
        tag = _one_line(action.get("tag"), 40, "tag")
        return ["scripts/prospects.py", *root, "set", person, "--tag" if op == "person-tag" else "--untag", tag]
    if op in ("tag", "untag"):
        kind = str(action.get("kind") or "")
        if kind not in dash_tags.KINDS:
            raise WriteRefused(f"kind must be one of {', '.join(dash_tags.KINDS)}")
        return ["dashboard/dash_tags.py", *root, "add" if op == "tag" else "remove", kind, _id(action.get("id"), kind),
                _one_line(action.get("tag"), 40, "tag").lower()]
    if op == "segment":
        company = _id(action.get("company"), "company")
        seg = str(action.get("segment") or "")
        if seg not in (*SEGMENTS, "none"):
            raise WriteRefused(f"segment must be one of {', '.join(SEGMENTS)} or none")
        # one source: the owner's pick is written into targets/companies.yaml (segment_source is the owner's source id)
        return ["scripts/segment_companies.py", *root, "set", company, seg, "--source", "owner"]
    raise WriteRefused(f"unknown write {op!r}")


def display(argv: list[str]) -> str:
    return " ".join(shlex.quote(a) for a in ["python3", *argv])


def run_write(action: dict[str, Any], js: Path, timeout: int = 120) -> dict[str, Any]:
    argv = command_for(action, js)
    proc = subprocess.run([sys.executable, *argv], cwd=str(JS_DEFAULT), capture_output=True, text=True,
                          timeout=timeout, stdin=subprocess.DEVNULL)
    result = {"command": display(argv), "exit": proc.returncode, "ok": proc.returncode == 0,
              "stdout": (proc.stdout or "")[-4000:], "stderr": (proc.stderr or "")[-4000:]}
    log = Path(js) / WRITE_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": fa.iso_utc(datetime.now(timezone.utc)), "op": action.get("op"),
                             "command": result["command"], "exit": proc.returncode}, ensure_ascii=False) + "\n")
    return result
