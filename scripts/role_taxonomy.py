#!/usr/bin/env python3
"""role_taxonomy.py: the market's role categories, measured from the hunt's own job records.

Roles keep splitting into specialisms: under one broad title (say "AI engineer") employers hire specialists more often
than generalists. The recruiter's job is to see which categories exist and are growing, and to keep a focus profile ready
for each one the candidate can truthfully fill.

For every job record in jobs/*.md (title, first seen, status), this script asks focus_profiles.pick which focus profile
the title belongs to, then reports:
  - per profile: postings in the last 30 days, the 30 days before, and the change (which categories are growing);
  - the titles no profile covers, as their most common distinctive words and two-word phrases (a new category forming);
  - the share of fresh postings (first seen in 30 days) each profile holds.
The Lead Recruiter reads it weekly (lanes/recruiter/AGENT.md) and asks the role-analyst for a new focus profile when an
uncovered phrase keeps growing and the candidate's record can truthfully fill it.

Usage: python3 scripts/role_taxonomy.py [--root R] [--today YYYY-MM-DD] [--top 25] [--write] [--json]
--write saves state/recruiter/role-taxonomy-<date>.md. Read only otherwise. No network.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import focus_profiles as fp  # noqa: E402

from agency_paths import DATA_ROOT as JS_ROOT  # noqa: E402
FM = re.compile(r"\A---\n(.*?)\n---\n", re.S)
STOP = fp.STOP | {"remote", "hybrid", "onsite", "us", "usa", "new", "york", "san", "francisco", "sf", "nyc", "ii", "iii",
                  "2026", "2025", "intern", "internship", "with", "in", "on", "at", "or", "&", "-", "/"}


def load_titles(root: Path) -> list[dict[str, Any]]:
    import yaml
    rows = []
    for p in sorted((root / "jobs").glob("*.md")):
        try:
            m = FM.match(p.read_text(encoding="utf-8", errors="replace"))
            loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
            front = yaml.load(m.group(1), Loader=loader) if m else {}  # noqa: S506 (a safe loader)
        except Exception:  # noqa: BLE001  (a broken record is skipped, counted below)
            front = None
        if not isinstance(front, dict) or not front.get("title"):
            continue
        seen = str(front.get("first_seen") or front.get("posted_at") or front.get("discovered") or "")[:10]
        rows.append({"id": p.stem, "title": str(front["title"]), "seen": seen, "status": front.get("status")})
    return rows


def _d(s: str) -> date | None:
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def taxonomy(root: Path, today: date, top: int = 25) -> dict[str, Any]:
    rows = load_titles(root)
    lenses = fp.lenses(root)
    cache: dict[str, str | None] = {}
    recent, prior, fresh = Counter(), Counter(), Counter()
    uncovered_words, uncovered_pairs = Counter(), Counter()
    by_family = Counter()
    for r in rows:
        t = r["title"]
        if t not in cache:
            picks = fp.pick(root, t) if lenses else []
            cache[t] = picks[0]["family"] if picks else None
        fam = cache[t] or "(none)"
        by_family[fam] += 1
        d = _d(r["seen"])
        if d:
            if today - timedelta(days=30) < d <= today:
                recent[fam] += 1
                fresh[fam] += 1
            elif today - timedelta(days=60) < d <= today - timedelta(days=30):
                prior[fam] += 1
        if fam == "(none)":
            words = [w for w in re.findall(r"[a-z0-9+]+", t.lower()) if w not in STOP and len(w) > 1]
            uncovered_words.update(set(words))
            uncovered_pairs.update({f"{a} {b}" for a, b in zip(words, words[1:])})
    fams = sorted(set(by_family) | set(recent), key=lambda f: (-recent[f], f))
    table = [{"family": f, "all": by_family[f], "last_30d": recent[f], "prior_30d": prior[f],
              "change": recent[f] - prior[f]} for f in fams]
    total_recent = sum(recent.values()) or 1
    return {"today": today.isoformat(), "jobs": len(rows), "profiles": len(lenses), "table": table,
            "fresh_share": {f: round(fresh[f] / total_recent, 3) for f in fresh},
            "uncovered": by_family["(none)"],
            "uncovered_words": uncovered_words.most_common(top), "uncovered_phrases": uncovered_pairs.most_common(top)}


def render(res: dict[str, Any]) -> str:
    out = [f"# Role taxonomy · {res['today']} ", "",
           f"{res['jobs']} job records, {res['profiles']} focus profiles; {res['uncovered']} titles match no profile.", "",
           "| focus profile | all | last 30 days | prior 30 days | change |", "|---|--:|--:|--:|--:|"]
    for r in res["table"]:
        out.append(f"| {r['family']} | {r['all']} | {r['last_30d']} | {r['prior_30d']} | {r['change']:+d} |")
    out += ["", "## Titles no profile covers: the most common phrases (a category forming?)", ""]
    out += [f"- {p} ({n})" for p, n in res["uncovered_phrases"]]
    out += ["", "## ... and words", ""] + [f"- {w} ({n})" for w, n in res["uncovered_words"]]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--today", default=None)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    today = date.fromisoformat(a.today) if a.today else date.today()
    res = taxonomy(a.root, today, a.top)
    if a.json:
        print(json.dumps(res, indent=2))
        return 0
    text = render(res)
    if a.write:
        p = a.root / "state" / "recruiter" / f"role-taxonomy-{today.isoformat()}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        print(f"wrote {p.relative_to(a.root)}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
