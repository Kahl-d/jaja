#!/usr/bin/env python3
"""corpus_audit.py - the corpus-keeper's periodic audit of profile/ (read only).

    python3 scripts/corpus_audit.py [--today YYYY-MM-DD] [--json] [--root JS_DIR]

Keeper findings (the corpus-keeper fixes them in its own files; exit 1 while any remain):
  links      a corpus/<dir>/ that no facts.yaml `corpus:` link and no projects-index.md row reaches; an
             index row whose file does not exist; a corpus file with frontmatter but no index row
  index      a projects-index.md row that differs from its file's frontmatter (kind, id, dates, status,
             discretion, lenses; the title cell may be a truncated start of the title)
  status     an employment or education entry whose status differs from its corpus file's; a
             needs-confirmation entry whose conflicts are all ruled (confirm it, or say what is open)
  stale      a corpus or CORPUS-STATUS.md line that treats ruled conflicts as open (pending, awaiting,
             to confirm, waits for the owner, an item under a "... questions" heading) and does
             not cite the ruling (the word "ruled"); a timeline `uncertain` note naming only ruled conflicts
  conflicts  a conflict id named in facts.yaml entries, the corpus or CORPUS-STATUS.md that facts.yaml
             does not define
  timeline   an item missing from a month inside its range, or listed in a month outside it; an
             unknown id in `active`; months that end before the month of --today
  titles     an employment or demo `title_render` that PROFILE.md does not render and that carries no
             `profile_pending`; a `profile_pending` entry that PROFILE.md already renders (drop the flag)
A ruling whose `ruled:` block has a `note:` still waits on a condition (for example "held until the
presentation is confirmed"): lines that name it are listed for the owner, never called stale, until a valid
`condition_met:` block (by the owner, "on", basis, the owner's words; check_evidence.py's rule) settles it.

Lists (no effect on the exit code): for the owner, the open conflicts, rulings that wait on a condition,
needs-confirmation entries (with the conflict they wait on, or "no conflict recorded"), "to confirm"
notes and uncertain timeline notes; waiting on profile-editor, the entries with `profile_pending`.
Deterministic, no network, writes nothing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Iterator

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as cfg  # noqa: E402
import check_evidence as ce  # noqa: E402  (one validator for the owner's ruling blocks)
import profile_titles as pt  # noqa: E402
from js_common import JS_ROOT, cli_date, today as _today  # noqa: E402

CID_RE = re.compile(r"\bC\d{2,3}\b")
# How the owner is named in a note that waits on them: identity.owner_label and identity.owner_token, and "owner".
_NAMES = "|".join(sorted({re.escape(str(n).strip()) for n in (cfg.get("identity.owner_label", ""), cfg.get("identity.owner_token", ""),
                                                           "owner") if str(n).strip()}, key=len, reverse=True))
OPEN_RE = re.compile(rf"\b(?:pending|awaiting|to confirm|unconfirmed|unresolved|until (?:{_NAMES})|waits? (?:for|on) (?:{_NAMES})|"
                     rf"(?:{_NAMES})'?s (?:ruling|yes|call|decision)|needs[- ](?:{_NAMES})|open questions?|open decisions?)\b"
                     r"|\[confirm\b", re.I)
TO_CONFIRM_RE = re.compile(r"\bto confirm\b|\[confirm\b", re.I)
RULED_RE = re.compile(r"\bruled\b", re.I)
MONTH_RE = re.compile(r"^\d{4}-\d{2}")
STATUS = {"confirmed", "needs-confirmation"}
ENTRY_SECTIONS = ("identity", "education", "employment", "awards", "publications", "presentations_and_demos")
CHECK_ORDER = ("links", "index", "status", "stale", "conflicts", "timeline", "titles")


def finding(check: str, where: str, message: str, line: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"check": check, "where": where, "message": message}
    if line is not None:
        out["line"] = line
    return out


def _frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    try:
        meta = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        return {}
    return meta if isinstance(meta, dict) else {}


def _strings(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)
    elif node is not None:
        yield str(node)


def _refs(node: Any) -> set[str]:
    return {c for s in _strings(node) for c in CID_RE.findall(s)}


def _excerpt(line: str, match: re.Match[str] | None = None, width: int = 160) -> str:
    """The line, or a window around the match when the line is longer than `width`."""
    line = line.strip()
    if len(line) <= width:
        return line
    if match is None or match.end() <= width - 20:
        return line[:width] + " ..."
    start = max(0, match.start() - (width - 60))
    return "... " + line[start:start + width] + (" ..." if start + width < len(line) else "")


def _ym(value: Any) -> str:
    s = str(value or "").strip()
    return s[:7] if MONTH_RE.match(s) else s


class Profile:
    """facts.yaml, the conflict sets, the corpus files and the index rows of one department root."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.dir = self.root / "profile"
        path = self.dir / "facts.yaml"
        self.facts: dict[str, Any] = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.is_file() else {}
        self.open: set[str] = set()
        self.ruled: set[str] = set()
        self.conditional: dict[str, str] = {}
        self.topics: dict[str, str] = {}
        for c in self.facts.get("conflicts") or []:
            if not isinstance(c, dict) or not c.get("id"):
                continue
            cid = str(c["id"])
            self.topics[cid] = str(c.get("topic") or "")
            if c.get("status") == "ruled":
                self.ruled.add(cid)
                note = (c.get("ruled") or {}).get("note") if isinstance(c.get("ruled"), dict) else None
                if note and not ("condition_met" in c and not ce.condition_met_problems(cid, c["condition_met"])):
                    self.conditional[cid] = str(note)
            else:
                self.open.add(cid)
        self.known = self.open | self.ruled
        self.settled = self.ruled - set(self.conditional)
        self.files: dict[str, dict[str, Any]] = {}
        corpus = self.dir / "corpus"
        if corpus.is_dir():
            for p in sorted(corpus.rglob("*.md")):
                meta = _frontmatter(p.read_text(encoding="utf-8"))
                if meta.get("id") and meta.get("kind"):
                    self.files[p.relative_to(self.dir).as_posix()] = meta
        self.rows = self._index_rows()

    def _index_rows(self) -> list[dict[str, Any]]:
        path = self.dir / "projects-index.md"
        rows: list[dict[str, Any]] = []
        if not path.is_file():
            return rows
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) != 8 or cells[0] == "Kind" or set(cells[0]) <= set("-: "):
                continue
            keys = ("kind", "id", "title", "dates", "status", "discretion", "lenses", "file")
            row = dict(zip(keys, cells))
            row["file"] = row["file"].strip("`")
            row["line"] = n
            rows.append(row)
        return rows

    def entries(self) -> Iterator[tuple[str, dict[str, Any]]]:
        """(path, mapping) for every record that carries a `status` in the entry sections."""
        def walk(node: Any, path: str) -> Iterator[tuple[str, dict[str, Any]]]:
            if isinstance(node, dict):
                if "status" in node:
                    yield path, node
                for k, v in node.items():
                    if isinstance(v, (dict, list)):
                        yield from walk(v, f"{path}.{k}")
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    label = v.get("id") if isinstance(v, dict) and v.get("id") else i
                    yield from walk(v, f"{path}[{label}]")
        for section in ENTRY_SECTIONS:
            yield from walk(self.facts.get(section), section)


# --------------------------------------------------------------------------- checks
def check_links(p: Profile) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    reached: set[str] = set()
    for _, rel in pt.iter_corpus_links(p.facts):
        parts = Path(rel).parts
        if len(parts) >= 2 and parts[0] == "corpus":
            reached.add(parts[1])
    for r in p.rows:
        parts = Path(r["file"]).parts
        if len(parts) >= 2 and parts[0] == "corpus":
            reached.add(parts[1])
        if not (p.dir / r["file"]).is_file():
            out.append(finding("links", f"projects-index.md:{r['line']}", f"row {r['id']} points at {r['file']}, which does not exist"))
    corpus = p.dir / "corpus"
    for d in sorted(x for x in corpus.iterdir() if x.is_dir() and not x.name.startswith((".", "_"))) if corpus.is_dir() else []:
        if d.name not in reached:
            out.append(finding("links", f"corpus/{d.name}/", "not reached by a facts.yaml `corpus:` link or a projects-index.md row"))
    indexed = {r["file"] for r in p.rows}
    for rel in p.files:
        if rel not in indexed:
            out.append(finding("links", rel, "has frontmatter but no projects-index.md row"))
    return out


def check_index(p: Profile) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in p.rows:
        meta = p.files.get(r["file"])
        if meta is None:
            continue
        dates = meta.get("dates") if isinstance(meta.get("dates"), dict) else {}
        want = {"kind": str(meta.get("kind")), "id": str(meta.get("id")),
                "dates": f"{dates.get('start')} to {dates.get('end')}", "status": str(meta.get("status")),
                "discretion": str(meta.get("discretion")), "lenses": ", ".join(str(x) for x in meta.get("lenses") or [])}
        diffs = [f"{k} {r[k]!r} (frontmatter {v!r})" for k, v in want.items() if r[k] != v]
        title = str(meta.get("title") or "")
        if not title.startswith(r["title"]):
            diffs.append(f"title {r['title']!r} is not the start of {title!r}")
        if diffs:
            out.append(finding("index", f"projects-index.md:{r['line']}", f"row {r['id']}: " + "; ".join(diffs)))
    return out


def check_status(p: Profile) -> tuple[list[dict[str, Any]], list[str]]:
    out: list[dict[str, Any]] = []
    owner: list[str] = []
    for section in ("employment", "education"):
        for i, e in enumerate(p.facts.get(section) or []):
            if not isinstance(e, dict) or not e.get("corpus"):
                continue
            meta = p.files.get(str(e["corpus"]))
            fs, cs = str(e.get("status") or ""), str((meta or {}).get("status") or "")
            if meta is not None and fs in STATUS and cs in STATUS and fs != cs:
                out.append(finding("status", f"facts.yaml {section}[{e.get('id', i)}]",
                                   f"is {fs}; {e['corpus']} frontmatter says {cs}"))
    for path, e in p.entries():
        if str(e.get("status")) != "needs-confirmation":
            continue
        refs = _refs(e)
        waiting = sorted(refs & (p.open | set(p.conditional)))
        if waiting:
            owner.append(f"facts.yaml {path}: waits on {', '.join(waiting)}")
        elif refs & p.settled:
            out.append(finding("status", f"facts.yaml {path}", "needs-confirmation, but every conflict it names is ruled "
                               f"({', '.join(sorted(refs & p.known))}): set confirmed, or name what is still open"))
        else:
            owner.append(f"facts.yaml {path}: needs-confirmation, no conflict recorded: confirm or correct")
    return out, owner


def check_lines(p: Profile) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Stale open notes and unknown conflict ids in the corpus and CORPUS-STATUS.md; "to confirm" notes."""
    stale: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    to_confirm: list[str] = []
    files = sorted((p.dir / "corpus").rglob("*.md")) if (p.dir / "corpus").is_dir() else []
    files.append(p.dir / "CORPUS-STATUS.md")
    for path in files:
        if not path.is_file():
            continue
        rel = path.relative_to(p.dir).as_posix()
        section = ""
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.startswith("## "):
                section = line[3:].strip().lower()
            ids = set(CID_RE.findall(line))
            for u in sorted(ids - p.known):
                unknown.append(finding("conflicts", f"{rel}:{n}", f"names {u}, which facts.yaml conflicts does not define", n))
            mark = OPEN_RE.search(line)
            marked = bool(mark) or "question" in section
            if ids and ids <= p.settled and marked and not RULED_RE.search(line):
                stale.append(finding("stale", f"{rel}:{n}", f"treats ruled {', '.join(sorted(ids))} as open: {_excerpt(line, mark)}", n))
            elif (confirm := TO_CONFIRM_RE.search(line)):
                to_confirm.append(f"{rel}:{n}: {_excerpt(line, confirm)}")
    facts_path = p.dir / "facts.yaml"
    if facts_path.is_file():
        for n, line in enumerate(facts_path.read_text(encoding="utf-8").splitlines(), 1):
            if line.startswith("conflicts:"):
                break
            if (confirm := TO_CONFIRM_RE.search(line)):
                to_confirm.append(f"facts.yaml:{n}: {_excerpt(line, confirm)}")
    for path, e in p.entries():
        for u in sorted(_refs(e) - p.known):
            unknown.append(finding("conflicts", f"facts.yaml {path}", f"names {u}, which facts.yaml conflicts does not define"))
    return stale, unknown, to_confirm


def check_timeline(p: Profile, today: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    out: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    notes: list[str] = []
    tl = p.facts.get("timeline") if isinstance(p.facts.get("timeline"), dict) else {}
    items = [i for i in tl.get("items") or [] if isinstance(i, dict) and i.get("id")]
    months = [m for m in tl.get("months") or [] if isinstance(m, dict) and m.get("month")]
    ids = {str(i["id"]) for i in items}
    for m in months:
        month, active = _ym(m["month"]), {str(a) for a in m.get("active") or []}
        for a in sorted(active - ids):
            out.append(finding("timeline", f"facts.yaml timeline.months[{month}]", f"active names {a}, which is not a timeline item"))
        for it in items:
            start, end = _ym(it.get("start")), _ym(it.get("end"))
            inside = start <= month and (end == "present" or month <= end)
            if inside and str(it["id"]) not in active:
                out.append(finding("timeline", f"facts.yaml timeline.months[{month}]", f"{it['id']} ({start} to {end}) is missing from active"))
            elif not inside and str(it["id"]) in active:
                out.append(finding("timeline", f"facts.yaml timeline.months[{month}]", f"{it['id']} ({start} to {end}) is listed outside its range"))
    if months:
        last, now = max(_ym(m["month"]) for m in months), today.strftime("%Y-%m")
        if last < now:
            out.append(finding("timeline", "facts.yaml timeline.months", f"the months end at {last}; add months through {now}"))
    for it in items:
        note = it.get("uncertain")
        if not note:
            continue
        refs = set(CID_RE.findall(str(note)))
        if refs and refs <= p.settled:
            stale.append(finding("stale", f"facts.yaml timeline.items[{it['id']}]",
                                 f"uncertain names only ruled {', '.join(sorted(refs))}: {note}"))
        else:
            notes.append(f"timeline {it['id']}: {note}")
    return out, stale, notes


def check_titles(p: Profile) -> tuple[list[dict[str, Any]], list[str]]:
    out: list[dict[str, Any]] = []
    waiting: list[str] = []
    profile_md = p.dir / "PROFILE.md"
    text = profile_md.read_text(encoding="utf-8") if profile_md.is_file() else None
    for section, header in (("employment", True), ("presentations_and_demos", False)):
        for e in p.facts.get(section) or []:
            if not isinstance(e, dict) or "title_render" not in e:
                continue
            r = str(e["title_render"])
            where = f"facts.yaml {section}[{e.get('id')}]"
            rendered = text is not None and (f"### {r} · " in text if header else (r in text or (r[:1].lower() + r[1:]) in text))
            if e.get("profile_pending"):
                waiting.append(f"{where}: {e['profile_pending']}")
                if rendered:
                    out.append(finding("titles", where, "PROFILE.md renders this title now: drop profile_pending"))
            elif text is not None and not rendered:
                out.append(finding("titles", where, f"title_render {r!r} is not rendered in PROFILE.md "
                                   "(new: add profile_pending and hand it to profile-editor; changed: coordinate first)"))
    return out, waiting


def audit(root: Path | str = JS_ROOT, today: date | None = None) -> dict[str, Any]:
    today = today or _today()
    p = Profile(Path(root))
    status, needs = check_status(p)
    stale_lines, unknown, to_confirm = check_lines(p)
    timeline, stale_notes, timeline_notes = check_timeline(p, today)
    titles, waiting = check_titles(p)
    keeper = check_links(p) + check_index(p) + status + stale_lines + stale_notes + unknown + timeline + titles
    keeper.sort(key=lambda f: CHECK_ORDER.index(f["check"]))
    return {
        "date": today.isoformat(),
        "keeper": keeper,
        "owner": {
            "open_conflicts": [f"{c}: {p.topics[c]}" for c in sorted(p.open)],
            "conditional_rulings": [f"{c}: {p.conditional[c]}" for c in sorted(p.conditional)],
            "needs_confirmation": needs,
            "to_confirm": to_confirm,
            "timeline_notes": timeline_notes,
        },
        "waiting": waiting,
    }


def render(report: dict[str, Any]) -> str:
    lines = [f"corpus audit {report['date']} (profile/)", "", f"Keeper findings: {len(report['keeper'])}"]
    lines += [f"  {f['check']:<9} {f['where']}: {f['message']}" for f in report["keeper"]]
    lines += ["", "For the owner (lists; no effect on the exit code):"]
    titles = {"open_conflicts": "open conflicts", "conditional_rulings": "rulings waiting on a condition",
              "needs_confirmation": "needs-confirmation entries", "to_confirm": "to-confirm notes",
              "timeline_notes": "uncertain timeline notes"}
    for key, label in titles.items():
        items = report["owner"][key]
        lines.append(f"  {label} ({len(items)})")
        lines += [f"    {x}" for x in items]
    lines.append(f"Waiting on profile-editor (profile_pending): {len(report['waiting'])}")
    lines += [f"  {x}" for x in report["waiting"]]
    n_owner = sum(len(v) for v in report["owner"].values())
    lines.append(f"corpus_audit: {len(report['keeper'])} finding(s) for the keeper; {n_owner} item(s) for the owner; "
                 f"{len(report['owner']['open_conflicts'])} open conflict(s)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Audit profile/ for the corpus-keeper (read only).")
    ap.add_argument("--today", type=cli_date, help="date of the run (YYYY-MM-DD); default today")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--root", default=str(JS_ROOT), help="department root holding profile/ (default: this department)")
    a = ap.parse_args(argv)
    report = audit(Path(a.root), a.today)
    print(json.dumps(report, indent=2) if a.json else render(report))
    return 1 if report["keeper"] else 0


if __name__ == "__main__":
    sys.exit(main())
