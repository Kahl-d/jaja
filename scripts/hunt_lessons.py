#!/usr/bin/env python3
"""hunt_lessons.py - the loop, read only: what the Scout learned today and whether SETUP answered it.

The setup lane learns from the Scout's daily work and improves the agency. The Scout posts in hub/lanes/HUNT.md
(orders in hub/lanes/HUNT-ORDERS.md):
  `LEARNED <what happened> | <evidence: command, output, job id> | <what the agency should change>`
and, at the end of a day, a post headed `DAY REVIEW` whose bullets name finds by source, time sinks and broken tools.
SETUP answers in hub/lanes/SETUP.md with a line containing ` answer: <id> FIX|NO-FIX <note>`.

One row per LEARNED line and per DAY REVIEW bullet:
  id      L-<first 8 hex of sha1 of the line, whitespace collapsed>: stable whatever the post order; SETUP cites it
  date    the date of the nearest "### YYYY-MM-DD" post heading above the line, else the first date in the line
  source  LEARNED: the first word of <what happened> after a leading article (the, a, an); DAY REVIEW: the bullet's
          text before its first colon
  kind    an explicit tag (`LEARNED [fail|slow|works] ...`) wins; else keywords, in this order: slow (took, minutes,
          hours, time sink, waste, timeout, stalled, slow), works (works, worked, found, finds, well, faster, good, best,
          caught), fail (fail, error, broken, no receipt, missing, empty, refused, crash, 404, blocked, wrong, missed);
          none of them: unknown. A heuristic: the Scout can always tag the line.
  answer  FIX, NO-FIX, or empty (open), with SETUP's note
An answer whose id matches no current lesson is listed as orphaned: the id is a hash of the line, so a line the Scout
edits after posting gets a new id (posts are meant to be append-only).

CLI:  python3 scripts/hunt_lessons.py [--root JS] [--open] [--json]
      prints a Markdown table and "lessons: N (open M)"; never writes a file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from js_common import JS_ROOT  # noqa: E402

HUNT = Path("hub") / "lanes" / "HUNT.md"
SETUP = Path("hub") / "lanes" / "SETUP.md"
HEADING_RE = re.compile(r"^###\s+(\d{4}-\d{2}-\d{2})\b(.*)$")
DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
LEARNED_RE = re.compile(r"^\s*LEARNED\b\s*(?:\[(fail|slow|works)\]\s*)?(.*)$", re.I)
ANSWER_RE = re.compile(r"\banswer:\s*(L-[0-9a-f]{8})\s+(FIX|NO-FIX)\b\s*(.*)$")
KIND_WORDS = (
    ("slow", re.compile(r"\b(took|minutes?|hours?|time sinks?|wastes?d?|timeouts?|stalled|slow)\b", re.I)),
    ("works", re.compile(r"\b(works|worked|found|finds|well|faster|good|best|caught)\b", re.I)),
    ("fail", re.compile(r"\b(fail\w*|errors?|broken|no receipt|missing|empty|refused|crash\w*|404|blocked|wrong|missed)\b", re.I)),
)


def lesson_id(line: str) -> str:
    return "L-" + hashlib.sha1(" ".join(line.split()).encode("utf-8")).hexdigest()[:8]


ARTICLES = frozenset({"the", "a", "an"})


def source_of(what: str) -> str:
    words = what.split()
    while len(words) > 1 and words[0].lower() in ARTICLES:
        words = words[1:]
    return words[0] if words else ""


def kind_of(text: str, tag: str | None = None) -> str:
    if tag:
        return tag.lower()
    for kind, rx in KIND_WORDS:
        if rx.search(text):
            return kind
    return "unknown"


def parse_hunt(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    date, in_review = "", False
    for raw in text.splitlines():
        line = raw.rstrip()
        h = HEADING_RE.match(line)
        if h:
            date, in_review = h.group(1), "DAY REVIEW" in h.group(2).upper()
            continue
        if line.startswith("#"):
            in_review = False
            continue
        m = LEARNED_RE.match(line)
        if m:
            parts = [p.strip() for p in m.group(2).split("|")]
            what = parts[0]
            rows.append({"id": lesson_id(line), "type": "LEARNED",
                         "date": date or (DATE_RE.search(line).group(1) if DATE_RE.search(line) else ""),
                         "source": source_of(what), "kind": kind_of(what, m.group(1)),
                         "what": what, "evidence": parts[1] if len(parts) > 1 else "",
                         "change": " | ".join(parts[2:]) if len(parts) > 2 else "", "line": line.strip()})
            in_review = False                            # a LEARNED line ends a DAY REVIEW's bullet list
            continue
        if in_review and line.lstrip().startswith("- "):
            body = line.lstrip()[2:].strip()
            source = body.split(":", 1)[0].strip() if ":" in body else "day review"
            rows.append({"id": lesson_id(line), "type": "DAY REVIEW", "date": date, "source": source,
                         "kind": kind_of(body), "what": body, "evidence": "", "change": "", "line": line.strip()})
        elif in_review and line.strip() and not line.lstrip().startswith("- "):
            in_review = False                            # the review's bullet list ended
    return rows


def parse_answers(text: str) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        m = ANSWER_RE.search(line)
        if m:
            out[m.group(1)] = (m.group(2), m.group(3).strip())
    return out


def collect(root: Path) -> dict[str, Any]:
    hunt, setup = Path(root) / HUNT, Path(root) / SETUP
    rows = parse_hunt(hunt.read_text(encoding="utf-8")) if hunt.is_file() else None
    answers = parse_answers(setup.read_text(encoding="utf-8")) if setup.is_file() else {}
    for r in rows or []:
        verdict, note = answers.get(r["id"], ("", ""))
        r["answer"], r["answer_note"] = verdict, note
    ids = {r["id"] for r in rows or []}
    orphans = [(i, v, n) for i, (v, n) in answers.items() if i not in ids]
    return {"rows": rows or [], "source_present": rows is not None, "orphans": orphans,
            "open": sum(1 for r in rows or [] if not r["answer"])}


def cell(text: str, limit: int = 140) -> str:
    t = " ".join(str(text).split()).replace("|", "/").replace("\u2014", "-")
    return t if len(t) <= limit else t[: limit - 3] + "..."


def render(res: dict[str, Any], only_open: bool) -> str:
    if not res["source_present"]:
        return f"lessons: no data yet ({HUNT} not found)\n"
    rows = [r for r in res["rows"] if not (only_open and r["answer"])]
    L = ["| id | date | source | kind | line | answer |", "|---|---|---|---|---|---|"]
    L += [f"| {r['id']} | {r['date']} | {cell(r['source'], 40)} | {r['kind']} | {cell(r['what'])} | "
          f"{(r['answer'] + ': ' + cell(r['answer_note'], 60)) if r['answer'] else 'open'} |" for r in rows]
    L.append(f"\nlessons: {len(res['rows'])} (open {res['open']})")
    if res["orphans"]:
        L.append("answers with no matching lesson (edited line?): "
                 + ", ".join(f"{i} {v}" for i, v, _ in res["orphans"]))
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--root", type=Path, default=JS_ROOT)
    p.add_argument("--open", action="store_true", help="only lessons SETUP has not answered")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    res = collect(args.root)
    if args.json:
        if args.open:
            res = {**res, "rows": [r for r in res["rows"] if not r["answer"]]}
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(render(res, args.open))
    return 0


if __name__ == "__main__":
    sys.exit(main())
