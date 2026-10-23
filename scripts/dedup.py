#!/usr/bin/env python3
"""Dedup keys, cross-source duplicates, repost detection, the seen ledger and ghost scores (design §4).

Keys (per job record)
  ct   sha1(normalize_company(company) | normalize_title(title))[:16]       company + title
  ctl  sha1(ct | normalize_location(location))[:16]                         + location
  req  "<company_slug>:<req_id lowercased>"  (None without a req id)
  jd   jd_hash (sha1 of the whitespace-collapsed JD text)

Duplicates (open records only: status not closed/skipped)
  Two records are the same job when they share ctl AND (come from different sources OR have the
  same JD), or share req AND come from different sources. Two postings on one board with one req id
  but different titles are sibling roles, not duplicates. The canonical record is the one furthest
  along (applied > tailoring > queued > fit_checked > discovered), then the better source
  (ATS board > job-alert emails > referral/prospect/manual > adzuna/himalayas > hn), then earliest first_seen.
  The others get duplicate_of: <canonical id>; still-untouched ones (discovered/fit_checked) are
  set to status skipped.

Seen ledger: state/seen.jsonl, append-only. One row when an id is first seen ("first_seen") and
one when its jd hash changes ("jd_changed"): {at, event, id, company_slug, ct, ctl, req, jd}.

Reposts: rows written on an EARLIER DAY than this id's own first sighting count (same-day siblings
are multi-location clones, not reposts). reposted = another id already carried this exact JD, or
the company/title had >= 2 earlier ids (3+ postings in all). repost_count = distinct earlier ids
sharing the JD or the company/title.

ghost_score (0.0-1.0; research §1: age > 45-60 days unchanged, reposted >= 3x, missing req id)
  unchanged_days = days since jd_changed_at, else posted_at, else first_seen
  stale  = clamp((unchanged_days - 45) / 15, 0, 1)          0 until 45 days, 1 from 60 days
  repost = 0 (none) | 0.5 (1-2 earlier postings) | 1.0 (3+)
  no_req = 1 when req_id is missing, else 0
  ghost_score = round(0.50 * stale + 0.35 * repost + 0.15 * no_req, 2)

Cross-portal view: the canonical record of a duplicate group carries `sightings` [{portal, url, first_seen}]
for every source it was seen on (2 or more), oldest first; every record that is not a duplicate carries `similar` [ids]:
same company slug, same hunt_score.title_family, first seen within SIMILAR_DAYS. Neither changes a job's status.
(`seen_on`, the list of platform ids job_alerts.py keeps, is unchanged.)

CLI:  python dedup.py [--root R] [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_ats import SUPPORTED_VENDORS, iso_utc, load_jobs, parse_ts, save_job  # noqa: E402
from js_common import JS_ROOT, append_jsonl, normalize_company, normalize_title, read_jsonl  # noqa: E402

SOURCE_RANK = {"greenhouse": 0, "lever": 0, "ashby": 0, "workday": 0, "jibe": 0, "sitemap": 0, "amazon": 0,
               "linkedin-alert": 1,
               "glassdoor-alert": 1, "ziprecruiter-alert": 1, "wttj-alert": 1, "hiringcafe-alert": 1, "yc-alert": 1,
               "email-alert": 1,
               "referral": 2, "prospect": 2, "manual": 2, "adzuna": 3, "himalayas": 3, "hn": 4}
STATUS_RANK = {"applied": 0, "tailoring": 1, "queued": 2, "fit_checked": 3, "discovered": 4}
EXCLUDED_STATUSES = ("closed", "skipped")
TRACKED = ("status", "duplicate_of", "posted_at", "reposted", "repost_count", "repost_of", "ghost_score", "ghost_reasons",
           "sightings", "similar")
SIMILAR_DAYS = 30               #: "similar jobs coming up": same company, same title family, opened within 30 days
SIMILAR_MAX = 10
SOURCE_PORTAL = {"hn": "hacker-news"}       # other sources are their platforms.yaml id, or <id>-alert


def _h(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


_CITY_ALIASES = {"sf": "san francisco", "nyc": "new york", "new york city": "new york", "la": "los angeles"}


def normalize_location(location: Any) -> str:
    """Sorted set of cities (remote parts keep their region: 'remote:us'), format-independent:
    'US, CA, Santa Clara' == 'Santa Clara, CA'."""
    out: set[str] = set()
    for part in re.split(r"\s*(?:\||;|\bor\b)\s*", str(location or "")):
        part = part.strip()
        if not part:
            continue
        low = part.lower()
        if re.search(r"\bremote\b", low):
            # keep the region: "Remote (US)" and "United Kingdom (Remote)" are different jobs
            region = re.sub(r"[^a-z0-9]+", " ", re.sub(r"\bremote\b", " ", low))
            region = re.sub(r"\b(?:friendly|travel|required|first|only|based|in|within|the)\b", " ", region)
            region = re.sub(r"\s+", " ", region).strip()
            out.add(f"remote:{region}" if region else "remote")
            continue
        m = re.match(r"^[A-Z]{2},\s*[A-Z]{2},\s*(.+)$", part)          # Workday "US, CA, Santa Clara"
        if m:
            low = m.group(1).lower()
        m = re.match(r"^(?:on-?site|hybrid|in[- ]person)\s*\((.+)\)\s*$", low)
        if m:
            low = m.group(1)
        city = re.split(r",|\(", low)[0]
        city = re.sub(r"[^a-z0-9]+", " ", city).strip()
        city = _CITY_ALIASES.get(city, city)
        if city:
            out.add(city)
    return "|".join(sorted(out))


def keys(rec: dict[str, Any]) -> dict[str, str | None]:
    ct = _h(normalize_company(rec.get("company") or rec.get("company_slug") or ""),
            normalize_title(rec.get("title") or ""))
    req = rec.get("req_id")
    return {"ct": ct, "ctl": _h(ct, normalize_location(rec.get("location"))),
            "req": f"{rec.get('company_slug')}:{str(req).strip().lower()}" if req not in (None, "") else None,
            "jd": rec.get("jd_hash") or None}


# ------------------------------------------------------------------------------ duplicates
def _canonical_order(r: dict[str, Any]) -> tuple:
    return (STATUS_RANK.get(str(r.get("status")), 5), SOURCE_RANK.get(str(r.get("source")), 3),
            str(r.get("first_seen") or ""), str(r.get("id")))


def find_duplicates(records: list[dict[str, Any]]) -> dict[str, str]:
    """id -> canonical id for every open record that duplicates another open record."""
    open_recs = [r for r in records if r.get("status") not in EXCLUDED_STATUSES]
    parent = {r["id"]: r["id"] for r in open_recs}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    by_ctl: dict[str, list[dict]] = defaultdict(list)
    by_req: dict[str, list[dict]] = defaultdict(list)
    for r in open_recs:
        k = keys(r)
        by_ctl[k["ctl"]].append(r)
        if k["req"]:
            by_req[k["req"]].append(r)
    for group in by_ctl.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a.get("source") != b.get("source") or (a.get("jd_hash") and a.get("jd_hash") == b.get("jd_hash")):
                    union(a["id"], b["id"])
    for group in by_req.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a.get("source") != b.get("source"):
                    union(a["id"], b["id"])
    # Scout lesson L-0862df0f: a job-alert copy (LinkedIn and the rest) folds into the company-ATS record of the same
    # company and title whatever its location string, but only when exactly one open ATS record carries that title
    by_ct: dict[str, list[dict]] = defaultdict(list)
    for r in open_recs:
        by_ct[keys(r)["ct"]].append(r)
    for group in by_ct.values():
        boards = [r for r in group if str(r.get("source") or "") in SUPPORTED_VENDORS]
        alerts = [r for r in group if str(r.get("source") or "").endswith("-alert") or r.get("source") == "email-alert"]
        if len(boards) == 1:
            for a in alerts:
                union(boards[0]["id"], a["id"])
    clusters: dict[str, list[dict]] = defaultdict(list)
    for r in open_recs:
        clusters[find(r["id"])].append(r)
    out: dict[str, str] = {}
    for members in clusters.values():
        if len(members) < 2:
            continue
        canon = min(members, key=_canonical_order)
        for m in members:
            if m["id"] != canon["id"]:
                out[m["id"]] = canon["id"]
    return out


# ------------------------------------------------------------------------------ reposts
def _day(value: Any) -> str:
    dt = parse_ts(value)
    return dt.strftime("%Y-%m-%d") if dt else ""


def repost_info(rec: dict[str, Any], k: dict[str, Any], ledger: list[dict[str, Any]]) -> dict[str, Any]:
    own_first = next((row["at"] for row in ledger if row.get("id") == rec["id"] and row.get("event") == "first_seen"),
                     rec.get("first_seen"))
    cutoff = _day(own_first)
    jd_ids: list[str] = []
    ct_ids: list[str] = []
    for row in ledger:
        rid = row.get("id")
        if rid == rec["id"] or not cutoff or _day(row.get("at")) >= cutoff:
            continue
        if k["jd"] and row.get("jd") == k["jd"] and rid not in jd_ids:
            jd_ids.append(rid)
        if row.get("ct") == k["ct"] and rid not in ct_ids:
            ct_ids.append(rid)
    earlier = list(dict.fromkeys(jd_ids + ct_ids))
    reposted = bool(jd_ids) or len(ct_ids) >= 2
    info: dict[str, Any] = {"reposted": reposted, "repost_count": len(earlier)}
    if reposted:
        info["repost_of"] = (jd_ids or ct_ids)[0]
    return info


# ------------------------------------------------------------------------------ ghost score
def ghost_score(rec: dict[str, Any], repost_count: int, now: datetime) -> tuple[float, list[str]]:
    reasons: list[str] = []
    ref = parse_ts(rec.get("jd_changed_at")) or parse_ts(rec.get("posted_at")) or parse_ts(rec.get("first_seen"))
    stale = 0.0
    if ref is not None:
        days = (now - ref).total_seconds() / 86400
        stale = min(1.0, max(0.0, (days - 45) / 15))
        if days > 45:
            reasons.append(f"JD unchanged {int(days)} days (>45)")
    repost = 0.0 if repost_count <= 0 else (0.5 if repost_count < 3 else 1.0)
    if repost_count:
        reasons.append(f"posted {repost_count} time(s) before")
    no_req = 0.0 if rec.get("req_id") not in (None, "") else 1.0
    if no_req:
        reasons.append("no requisition id")
    return round(0.50 * stale + 0.35 * repost + 0.15 * no_req, 2), reasons


# ------------------------------------------------------------------------------ orchestration
def portal_of(rec: dict[str, Any]) -> str:
    """The targets/platforms.yaml id a record came through: its alert `platform`, else company-ats for a polled ATS
    board, else the source (an <id>-alert source loses its suffix)."""
    if rec.get("platform"):
        return str(rec["platform"])
    src = str(rec.get("source") or "unknown")
    if src in SUPPORTED_VENDORS:
        return "company-ats"
    return SOURCE_PORTAL.get(src, src[:-6] if src.endswith("-alert") else src)


def _family(title: str) -> str:
    import hunt_score                         # lazy: hunt_score's title_family is the one definition
    return hunt_score.title_family(title)


def cross_portal(records: list[dict[str, Any]], dups: dict[str, str]) -> None:
    """: the canonical record of each duplicate group carries `sightings` [{portal, url, first_seen}] (2 or more),
    every non-duplicate carries `similar` [ids]; neither touches status or any other field."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        groups[dups.get(r["id"], r["id"])].append(r)
    for r in records:
        if r["id"] in dups:
            r.pop("sightings", None)
            r.pop("similar", None)
            continue
        seen, out = set(), []
        for m in sorted(groups.get(r["id"], [r]), key=lambda x: (str(x.get("first_seen") or ""), str(x["id"]))):
            item = {"portal": portal_of(m), "url": m.get("source_url"), "first_seen": m.get("first_seen")}
            key = (item["portal"], item["url"])
            if key not in seen:
                seen.add(key)
                out.append(item)
        if len(out) >= 2:
            r["sightings"] = out
        else:
            r.pop("sightings", None)
    heads = [r for r in records if r["id"] not in dups]
    by_family: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in heads:
        by_family[(str(r.get("company_slug") or ""), _family(r.get("title") or ""))].append(r)
    for r in heads:
        mine = parse_ts(r.get("first_seen")) or parse_ts(r.get("posted_at"))
        near = []
        for o in by_family[(str(r.get("company_slug") or ""), _family(r.get("title") or ""))]:
            other = parse_ts(o.get("first_seen")) or parse_ts(o.get("posted_at"))
            if o is not r and mine and other and abs((mine - other).days) <= SIMILAR_DAYS:
                near.append(str(o["id"]))
        if near:
            r["similar"] = sorted(near)[:SIMILAR_MAX]
        else:
            r.pop("similar", None)


def ledger_path(root: Path) -> Path:
    return Path(root) / "state" / "seen.jsonl"


def apply_dedup(records: list[dict[str, Any]], ledger: Iterable[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Annotate records in place; return {"new_rows": [...], "summary": {...}} (rows not yet written)."""
    ledger = list(ledger)
    latest_jd: dict[str, str | None] = {}
    for row in ledger:
        if row.get("id"):
            latest_jd[row["id"]] = row.get("jd")
    new_rows: list[dict[str, Any]] = []
    summary = {"records": len(records), "duplicates": 0, "reposts": 0, "ghost_high": 0, "ledger_rows": 0}
    for rec in records:
        k = keys(rec)
        info = repost_info(rec, k, ledger + new_rows)
        rec["reposted"] = info["reposted"]
        rec["repost_count"] = info["repost_count"]
        if "repost_of" in info:
            rec["repost_of"] = info["repost_of"]
        score, reasons = ghost_score(rec, info["repost_count"], now)
        rec["ghost_score"], rec["ghost_reasons"] = score, reasons
        summary["reposts"] += int(info["reposted"])
        summary["ghost_high"] += int(score >= 0.5)
        base = {"id": rec["id"], "company_slug": rec.get("company_slug"), "ct": k["ct"], "ctl": k["ctl"],
                "req": k["req"], "jd": k["jd"]}
        if rec["id"] not in latest_jd:
            new_rows.append({"at": rec.get("first_seen") or iso_utc(now), "event": "first_seen", **base})
        elif latest_jd[rec["id"]] != k["jd"]:
            new_rows.append({"at": iso_utc(now), "event": "jd_changed", **base})
        latest_jd[rec["id"]] = k["jd"]
    dups = find_duplicates(records)
    by_id = {r["id"]: r for r in records}
    for dup_id, canon_id in dups.items():               # an alert canonical takes its ATS twin's posted date (L-0862df0f)
        canon, dup = by_id[canon_id], by_id[dup_id]
        if not canon.get("posted_at") and dup.get("posted_at") and str(dup.get("source") or "") in SUPPORTED_VENDORS:
            canon["posted_at"] = dup["posted_at"]
    for dup_id, canon_id in dups.items():
        rec = next(r for r in records if r["id"] == dup_id)
        if rec.get("duplicate_of") != canon_id:
            summary["duplicates"] += 1
        rec["duplicate_of"] = canon_id
        if rec.get("status") in ("discovered", "fit_checked", None, ""):
            rec["status"] = "skipped"
    cross_portal(records, dups)
    summary["seen_on_2plus"] = sum(1 for r in records if len(r.get("sightings") or []) >= 2)
    summary["ledger_rows"] = len(new_rows)
    return {"new_rows": new_rows, "summary": summary}


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    now = now or datetime.now(timezone.utc)
    jobs = load_jobs(args.root)
    records = list(jobs.values())
    before = {r["id"]: {f: r.get(f) for f in TRACKED} for r in records}
    out = apply_dedup(records, read_jsonl(ledger_path(args.root)), now)
    changed = [r for r in records if {f: r.get(f) for f in TRACKED} != before[r["id"]]]
    if not args.dry_run:
        for r in changed:
            save_job(args.root, r)
        for row in out["new_rows"]:
            append_jsonl(ledger_path(args.root), row)
    s = out["summary"]
    mode = "dry-run (nothing written)" if args.dry_run else "written"
    print(f"dedup {mode}: {s['records']} records, {s['duplicates']} new duplicates, {s['reposts']} reposts, "
          f"{s['ghost_high']} ghost>=0.5, {s['ledger_rows']} ledger rows, {len(changed)} records updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
