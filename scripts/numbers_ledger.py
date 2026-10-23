#!/usr/bin/env python3
"""numbers_ledger.py: the numbers of a governed employer render only from the ledger's measured, released rows.

Policy: a figure on a page is "solid" only when it is MEASURED and has a recorded source and conditions. Targets,
estimates and unsourced figures stay out, and internal numbers (pass rates, usage, cost, business figures) stay
unrevealed; only engineering-method numbers and public releases render, sparingly. The ledger's status column carries
that decision; this script follows that column.

Which employer is governed is config (`policy.numbers_ledger.employer_pattern`, a regex; empty means no line is read).
Source ids that mark a figure as a target or estimate are config too (`policy.numbers_ledger.blocked_source_ids`).

The ledger is a markdown file at `policy.numbers_ledger.path` under the data folder (the corpus-keeper writes it).
This script reads only its `## Ledger table`, one number per row:
  | N-id | number as written | metric | kind | conditions | source | status |
  kind: measured, target or estimate; status: released or held.

For a page (resume.yaml, a letter, any text) it finds every number on a governed-employer line (an Experience or
Projects entry whose company or name matches the employer pattern, or any line or paragraph naming that employer) and
sorts it:
  - ok       the number matches a measured, released row (its N-id is named);
  - blocked  it matches a target, estimate or held row (or a row whose source is a blocked source id);
  - unknown  it matches no row ("not in the ledger": ask the corpus-keeper; a warning, never a silent pass).
Years, months in dates, version strings (v8.1.3), "3D"/"2D" and model names are not numbers here.
A bare figure ("3") matches a row only when a word of the row's metric sits within 60 characters of it; "1,962" is
never a year; "near-100%" and "about-20%" are read as figures.
Not covered: a paragraph that describes the employer's work without naming it ("at my last startup") is
not read, so letters must name the company on any line with one of its figures; numbers written as words ("four
releases", "nine servers") read as prose unless a ledger row writes them the same way. The rubric's reader checks
and the editor still read every line.

Usage:
  python3 scripts/numbers_ledger.py list [--json]
  python3 scripts/numbers_ledger.py check <resume.yaml | letter.md | text> [--ledger L] [--json]
Exit 0 when nothing is blocked (unknowns are warnings), 1 when a number is blocked, 2 on a usage error. No network.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from agency_paths import DATA_ROOT as JS_ROOT  # noqa: E402
import agency_config as cfg  # noqa: E402
LEDGER_REL = str(cfg.get("policy.numbers_ledger.path", "profile/corpus/numbers-ledger.md"))
LEDGER = JS_ROOT / LEDGER_REL
EVIDENCE = JS_ROOT / "profile" / "evidence.yaml"
EV_COMMENT = re.compile(r"<!--.*?-->", re.S)
MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
# a number with its unit: 3.8 s, 10 s, 60 fps, 30%, 2.6s, 74 PRs, 1,244 commits, 5x, 8-12 s, 20-minute
NUM = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?(?:\s*(?:-|–|to)\s*\d[\d,]*(?:\.\d+)?)?)"
                 r"(?:\s*-?\s*(%|×|[a-z]+))?(?![\w])", re.I)
UNITS = {"x": "x", "×": "x", "%": "%", "percent": "%", "ms": "ms", "millisecond": "ms", "milliseconds": "ms",
         "s": "s", "sec": "s", "secs": "s", "second": "s", "seconds": "s", "min": "min", "mins": "min",
         "minute": "min", "minutes": "min", "h": "h", "hr": "h", "hrs": "h", "hour": "h", "hours": "h",
         "k": "k", "m": "m", "fps": "fps"}
FILLER = {"of", "to", "and", "or", "if", "at", "in", "on", "the", "a", "an", "for", "with", "by", "from", "than",
          "vs", "per", "is", "are", "was", "were", "as", "be", "into", "out", "up", "down", "under", "over"}
NOT_A_NUMBER = re.compile(r"^(?:19|20)\d\d$")


def _norm(s: str) -> str:
    """One spelling per figure: "3.8s", "3.8 sec" and "3.8 seconds" are the same; "1,244" is "1244"."""
    toks = numbers_in(s, raw=True)
    return " ".join(toks) if toks else re.sub(r"\s+", "", s.lower())


def _employer_re() -> re.Pattern[str] | None:
    """The governed employer's name pattern from config; None when none is set (no line is governed)."""
    pat = str(cfg.get("policy.numbers_ledger.employer_pattern", "") or "")
    return re.compile(pat, re.I) if pat else None


def _blocked_source_re() -> re.Pattern[str] | None:
    ids = [str(i) for i in cfg.get("policy.numbers_ledger.blocked_source_ids", []) or []]
    return re.compile(r"\b(?:" + "|".join(re.escape(i) for i in ids) + r")\b") if ids else None


def _discretion() -> dict[str, str]:
    try:
        import yaml
        ev = (yaml.safe_load(EVIDENCE.read_text(encoding="utf-8")) or {}).get("evidence") or {}
    except (OSError, ValueError):
        return {}
    return {k: str((v or {}).get("discretion") or "resume-ok") for k, v in ev.items() if isinstance(v, dict)}


def load(path: Path = LEDGER) -> list[dict[str, str]] | None:
    """The ledger rows, or None when the file or its `## Ledger table` is missing. Each row notes whether one of its
    source ids may be cited on a page (`resume_ok`; evidence.yaml discretion), since a page cites only resume-ok ids."""
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^##\s+Ledger table\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    if not m:
        return None
    rows = []
    for line in m.group(1).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 7 or not re.match(r"NUM-\d+", cells[0], re.I):
            continue
        rows.append({"id": cells[0], "number": cells[1], "metric": cells[2], "kind": cells[3].lower(),
                     "conditions": cells[4], "source": cells[5], "status": cells[6].lower()})
    disc = _discretion()
    for r in rows:
        ids = re.findall(r"\bev-[a-z0-9-]+\b", r["source"])
        r["resume_ok"] = (not disc) or any(disc.get(i, "missing").startswith("resume-ok") for i in ids)
    return rows


def released(row: dict[str, str]) -> bool:
    blocked = _blocked_source_re()
    return (row["kind"].startswith("measured") and row["status"].startswith("released") and bool(row["conditions"])
            and bool(re.search(r"\bev-[a-z0-9-]+\b", row["source"])) and not (blocked and blocked.search(row["source"])))


WORDS = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                         "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
_W = "|".join(WORDS)


def words_to_digits(text: str, units: set[str] | None = None, all_words: bool = False) -> str:
    """Numbers written in words, where a ledger row could stand behind them: "two of three" -> "2 of 3" always, and
    "eight gates" -> "8 gates" when the noun is a unit some ledger row uses (so "one owner per file" stays prose)."""
    text = re.sub(rf"\b({_W})\s+of\s+({_W})\b", lambda m: f"{WORDS[m.group(1).lower()]} of {WORDS[m.group(2).lower()]}",
                  text, flags=re.I)
    if units:
        text = re.sub(rf"\b({_W})\s+([a-z]+)\b",
                      lambda m: (f"{WORDS[m.group(1).lower()]} {m.group(2)}" if m.group(2).lower() in units
                                 and m.group(1).lower() != "one" else m.group(0)), text, flags=re.I)
    elif all_words:                  # a ledger row: every number word is a figure ("seven signed Windows releases")
        text = re.sub(rf"\b({_W})\b(?=\s+[a-z])", lambda m: WORDS[m.group(1).lower()], text, flags=re.I)
    return text


def ledger_units(rows: list[dict[str, str]] | None) -> set[str]:
    out: set[str] = set()
    for r in rows or []:
        for tok in numbers_in(words_to_digits(r["number"], all_words=True)):
            parts = tok.split()
            if len(parts) == 2 and parts[1].isalpha() and len(parts[1]) > 2:
                out.add(parts[1].lower())
    return out


def numbers_in(text: str, raw: bool = False) -> list[str]:
    """The figures in a text, each with its unit (normalized when raw=True: "3.8 s", "1244 commits", "8-12 s").
    Not figures: years, dates, version strings (v8.1.3), 3D/2D, ids and labels (GPT-4a, ev-ps-029, base 72,
    section 2b), and a bare digit glued to a single letter that is no unit."""
    text = EV_COMMENT.sub(" ", text)
    text = re.sub(rf"\b{MONTHS}\s+\d{{4}}\b", " ", text, flags=re.I)      # Feb 2026
    text = re.sub(r"\b\d{4}-\d\d(?:-\d\d)?(?:T[\d:]+Z?)?\b", " ", text)   # 2026-02, ISO times
    text = re.sub(r"\bv?\d+(?:\.\d+){2,}\b", " ", text)                     # v8.1.3
    text = re.sub(r"\b[23]D\b", " ", text)                                   # 3D, 2D
    text = re.sub(r"\b(near|nearly|about|around|roughly|over|under|sub|almost)-(?=\d)", r"\1 ", text,
                  flags=re.I)                                            # near-100%: a qualifier, not a label
    text = re.sub(r"\b[A-Za-z]+(?:-[A-Za-z]+)*-?\d+(?:\.\d+)?[a-z]?\b", " ", text)   # GPT-4a, ev-ps-029, HL7
    text = re.sub(r"\b(?:base|iteration|section|step|row|item|question|round|level|phase|day|week|CS|S)\s+\d+[a-z]?\b",
                  " ", text, flags=re.I)
    out = []
    # "55 of 56 criteria", "2 of 3": one figure, as the ledger writes it
    for m in re.finditer(r"(?<![\w.])(\d[\d,]*)\s+of\s+(\d[\d,]*)(?:\s+(?!of\b|and\b|to\b)([a-z]+))?", text, re.I):
        n = f"{m.group(1)} of {m.group(2)}"
        if raw:
            n = re.sub(r"(\d),(\d)", r"\1\2", n)
        out.append(n)
    text = re.sub(r"(?<![\w.])\d[\d,]*\s+of\s+\d[\d,]*", " ", text)
    for m in NUM.finditer(text):
        n, unit = m.group(1).strip(), (m.group(2) or "").strip()
        low = unit.lower()
        if not re.search(r"\d,\d", n) and NOT_A_NUMBER.match(n.rstrip(",")):   # 2026 and "2026," are years; 1,962 never is
            continue
        if low and len(low) == 1 and low not in UNITS and low != "%":
            continue                                                         # 2b, 4a: labels
        if low in FILLER:
            unit = low = ""
        if raw:
            n = re.sub(r"\s*(?:–|to)\s*", "-", re.sub(r"(\d),(\d)", r"\1\2", n)).replace(" ", "")
            unit = UNITS.get(low, low)
        out.append((n + (" " + unit if unit else "")).strip())
    return out


def _phrase(s: str) -> str:
    s = EV_COMMENT.sub(" ", s).lower().replace("–", "-")
    s = re.sub(r"(\d),(\d)", r"\1\2", s)
    return " " + re.sub(r"[^a-z0-9.%-]+", " ", s).strip() + " "


METRIC_STOP = {"with", "from", "that", "this", "into", "over", "under", "after", "before", "their", "they", "them",
               "which", "while", "where", "when", "than", "then", "were", "have", "been", "each", "every", "only"}


METRIC_WINDOW = 60     # characters on each side of the figure that must hold a word of the row's metric


def _same_metric(row: dict[str, str], here: str) -> bool:
    """A row whose figure is a bare number ("3") also needs one word of its metric ("Acme team she led") within
    METRIC_WINDOW characters of the figure, so a list count or another company's figure in the same paragraph is never
    taken for it."""
    fig = _phrase(row["number"])
    if re.search(r"[a-z]", fig):
        return True                                         # the figure phrase itself carries words ("38 of 47 runs")
    words = {w for w in re.findall(r"[a-z]{4,}", (row.get("metric") or "").lower()) if w not in METRIC_STOP}
    if not words:
        return True
    for m in re.finditer(re.escape(fig), here):
        window = here[max(0, m.start() - METRIC_WINDOW): m.end() + METRIC_WINDOW]
        if any(f" {w}" in window for w in words):
            return True
    return False


def classify(num: str, rows: list[dict[str, str]] | None, line: str = "") -> tuple[str, str]:
    """A figure with a unit matches a row carrying the same figure and unit. A bare figure ("38") matches only a row
    whose whole figure phrase ("38 of 47") stands in the same line, so a list number or a count of something else is
    never taken for a ledger row."""
    if rows is None:
        return "unknown", f"no ledger yet ({LEDGER_REL}, ## Ledger table)"
    key = _norm(num)
    if " " in key:
        hits = [r for r in rows if key in set(numbers_in(words_to_digits(r["number"], all_words=True), raw=True))]
    else:
        here = _phrase(line)
        hits = [r for r in rows if key in set(numbers_in(words_to_digits(r["number"], all_words=True), raw=True))
                and _phrase(r["number"]).strip() and _phrase(r["number"]) in here and _same_metric(r, here)]
    if not hits:
        return "unknown", "not in the ledger: the corpus-keeper records it (measured, with source and conditions) or it goes"
    good = [r for r in hits if released(r)]
    citable = [r for r in good if r.get("resume_ok", True)]
    if citable:     # the row's conditions travel with the OK: the sentence must say what the row measured
        return "ok", f"{citable[0]['id']} (conditions: {citable[0]['conditions'][:140]}; the sentence must not change them)"
    if good:
        return "blocked", (f"{good[0]['id']} is released, but its sources ({good[0]['source']}) are internal-only: "
                           "the corpus-keeper names a resume-ok source first")
    r = hits[0]
    return "blocked", f"{r['id']} is {r['kind']}, {r['status']} (only measured, released numbers render)"


def df_lines(path: Path) -> list[dict[str, str]]:
    """Every governed-employer line of a page: entries of a resume.yaml, or paragraphs of a text file naming the employer."""
    text = path.read_text(encoding="utf-8")
    out: list[dict[str, str]] = []
    emp = _employer_re()
    if emp is None:
        return out
    if path.suffix in (".yaml", ".yml"):
        import yaml
        data = yaml.safe_load(text) or {}
        secs = ((data.get("cv") or {}).get("sections") or {}) if isinstance(data, dict) else {}
        for title, entries in secs.items():
            for e in entries or []:
                if isinstance(e, dict):
                    head = " ".join(str(e.get(k) or "") for k in ("company", "name", "institution"))
                    parts = [str(e.get("summary") or "")] + [str(h) for h in (e.get("highlights") or [])]
                    for p in parts:
                        if p and (emp.search(head) or emp.search(p)):
                            out.append({"where": f"{title} / {head.strip() or '?'}", "text": p})
                elif isinstance(e, str) and emp.search(e):
                    out.append({"where": title, "text": e})
        return out
    paras = re.split(r"\n\s*\n", re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S))
    return [{"where": f"paragraph {i}", "text": p} for i, p in enumerate(paras, 1) if emp.search(p)]


def check(path: Path, ledger: Path = LEDGER) -> dict[str, Any]:
    rows = load(ledger)
    found = []
    units = ledger_units(rows)
    for line in df_lines(path):
        said = words_to_digits(line["text"], units)
        for num in numbers_in(said):
            verdict, why = classify(num, rows, said)
            found.append({"where": line["where"], "number": num, "verdict": verdict, "why": why,
                          "line": EV_COMMENT.sub("", line["text"]).strip()[:160], "raw": line["text"]})
    return {"file": str(path), "ledger": str(ledger), "ledger_rows": None if rows is None else len(rows),
            "numbers": found, "blocked": sum(f["verdict"] == "blocked" for f in found),
            "unknown": sum(f["verdict"] == "unknown" for f in found)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--ledger", type=Path, default=LEDGER)
    ls.add_argument("--json", action="store_true")
    c = sub.add_parser("check")
    c.add_argument("file", type=Path)
    c.add_argument("--ledger", type=Path, default=LEDGER)
    c.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        rows = load(a.ledger)
        if rows is None:
            print(f"no ledger table in {a.ledger}")
            return 0
        if a.json:
            print(json.dumps(rows, indent=2))
        else:
            for r in rows:
                state = ("RELEASED" if r.get("resume_ok", True) else "RELEASED/internal") if released(r) else r["kind"] + "/" + r["status"]
                print(f"{r['id']:<8} {state:<20} {r['number']} · {r['metric']}")
        return 0
    if not a.file.is_file():
        print(f"no such file: {a.file}", file=sys.stderr)
        return 2
    res = check(a.file, a.ledger)
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        for f in res["numbers"]:
            tag = {"ok": "OK   ", "blocked": "BLOCK", "unknown": "WARN "}[f["verdict"]]
            print(f"{tag} {f['where']}: {f['number']}: {f['why']}")
        rows = "no ledger" if res["ledger_rows"] is None else f"{res['ledger_rows']} ledger rows"
        print(f"numbers ledger: {len(res['numbers'])} governed-employer numbers, {res['blocked']} blocked, "
              f"{res['unknown']} not in the ledger ({rows})")
    return 1 if res["blocked"] else 0


if __name__ == "__main__":
    sys.exit(main())
