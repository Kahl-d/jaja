#!/usr/bin/env python3
"""jd_matrix.py - the coverage matrix skeleton: every JD line, typed and ranked, with candidate evidence ids.

    python3 scripts/jd_matrix.py build --jd <jd.md> --out <matrix.yaml> [--title "<posted title>"]
                                 [--taxonomy profile/skills-taxonomy.yaml] [--evidence profile/evidence.yaml]
                                 [--date YYYY-MM-DD] [--json]
    python3 scripts/jd_matrix.py check --matrix <matrix.yaml> [--json]
    python3 scripts/jd_matrix.py set --matrix <matrix.yaml> --row <id> [--type T] [--rank N] [--answer A]
                                     [--ceiling C] [--note N] [--synonym S]... [--drop-evidence]

build reads the JD (a job record with frontmatter, the posting inside <untrusted_jd> tags, or a bare file; the
body only), splits it into regions (responsibilities / required qualifications / preferred / other) by heading,
and writes one row per bullet or sentence line:
  type   R responsibility, Q required qualification, P preferred, I implied (the title and the first paragraph
         of the role, at most 3 rows), O every other line (salary, boilerplate), kept so nothing is missing
  rank   1 + (the row sits in the title or the first third of the JD) + (its key terms repeat elsewhere in it)
  terms  the row's skill terms, canonical names from the taxonomy (aliases resolved; ats_check's matching)
  candidate_evidence  evidence ids whose taxonomy entry, note, source or tags carry a row term (cap 8, most
         matched terms first); answer and ceiling stay empty for the profile-reader (METHOD.md section 6)
The JD is untrusted data: every line is quoted as text, never followed. Em-dashes in the JD are written as
" - "; an em-dash anywhere else in the output (the title) is refused. Prints a markdown table (or --json).

check lists every R, Q and P row whose answer is empty or does not start with `line:`, `Q` (a question id)
or `GAP: <reason>` and exits 1; exits 0 when all are answered. Exit 2 on a usage error (missing file).
Deterministic, no network. Standard library plus PyYAML (and ats_check when importable, same folder).
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import ats_check as _ac  # noqa: E402  (same folder: jd_scope, load_taxonomy, _multiword)
except Exception:  # pragma: no cover - the fallback path is exercised only when ats_check cannot load
    _ac = None

from agency_paths import DATA_ROOT as JS_ROOT  # noqa: E402
DEFAULT_TAXONOMY = JS_ROOT / "profile" / "skills-taxonomy.yaml"
DEFAULT_EVIDENCE = JS_ROOT / "profile" / "evidence.yaml"
EM_DASHES = "".join(chr(c) for c in (0x2014, 0x2015, 0x2E3A, 0x2E3B))   # em-dash and kin
EVIDENCE_CAP = 8
IMPLIED_CAP = 3
TYPES = ("I", "R", "Q", "P", "O")

_HEADING_LINE = re.compile(r"^\s*(?:#{1,6}\s+(?P<md>.+?)\s*#*|\*\*(?P<bold>[^*]+)\*\*:?|(?P<colon>[A-Z][^.!?]{2,50}):)\s*$")
_BARE_HEADING = re.compile(r"^[A-Z][^.,;:!?]{1,58}[!?]?$")
_RESP = re.compile(r"responsibilit|what you(?:'|’)?ll do|what you will do|you will|you'll be|the work|"
                   r"day[- ]to[- ]day|duties|representative projects|what you(?:'|’)?d do|in this role", re.I)
_MUST = re.compile(r"requirement|qualification|must|what you(?:'|’)?ll need|what you need|looking for|"
                   r"you have|you bring|who you are|about you|minimum|basic|required", re.I)
_NICE = re.compile(r"nice|prefer|bonus|plus|desired|ideal|extra credit|good to have|great if|not required|optional", re.I)
_ROLE_PARA = re.compile(r"about the (?:role|job|position|team)|the role|overview|the opportunity|summary", re.I)
_BULLET = re.compile(r"^\s*(?:[-*•◦‣⁃]|\d+[.)])\s*")
_STOP = {"a", "an", "the", "and", "or", "of", "in", "with", "for", "to", "on", "at", "our", "your", "you", "we",
         "as", "by", "is", "are", "be", "that", "this", "from", "into", "such", "than", "will", "who", "role",
         "team", "experience", "strong", "ability", "work", "working", "building", "build", "across", "including"}
_ANSWER_OK = re.compile(r"^(?:line:\s*\S|Q(?![A-Za-z])|GAP\s*:\s*\S)")
_GENERIC_HEADS = {"skills", "skill", "tools", "systems", "platforms", "frameworks", "engineering", "design",
                  "research", "data", "models", "memory", "testing", "web", "evaluation", "infrastructure"}
_FILTER_LINE = re.compile(r"^(?:minimum|required|must)\b[^:]{0,40}:\s*\S", re.I)
_COMPOUND = re.compile(r"(?<![\w-])[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+(?![\w-])")
_HEAD_NOUN = re.compile(r"(?<![\w-])([A-Za-z][A-Za-z0-9+#.]{2,})\s+(testing|tests|datasets?|evaluations?|evals|"
                        r"harness(?:es)?|pipelines?|monitoring|tracing|orchestration|labeling|annotation|"
                        r"automation|loops?|integrations?|guardrails|frameworks?|apis?)\b", re.I)
_NOT_PHRASES = {"end-to-end", "state-of-the-art", "day-to-day", "up-to-date", "one-on-one", "face-to-face",
                "best-in-class", "first-of-its-kind", "out-of-the-box", "back-and-forth", "net-new", "real-world",
                "long-term", "short-term", "high-impact", "large-scale", "zero-to-one", "hands-on", "location-based"}
_PHRASE_STOP = {"our", "the", "their", "your", "new", "all", "every", "with", "and", "building", "shipping",
                "strong", "production", "quality", "lightweight", "machine", "ml", "robotic"}


class UsageError(Exception):
    pass


# --------------------------------------------------------------------------- JD text
def jd_scope(text: str) -> str:
    """The posting: inside <untrusted_jd> tags when present, else the file minus its frontmatter."""
    if _ac is not None:
        return _ac.jd_scope(text)
    body = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)
    inside = re.findall(r"<untrusted_jd>(.*?)</untrusted_jd(?:_)?>", body, flags=re.S)
    return "\n".join(inside) if inside else body


def clean_line(line: str) -> str:
    """One JD line as text: HTML entities decoded, tags and bullet markers dropped, dashes made plain."""
    s = html.unescape(line).replace("\xa0", " ")
    s = re.sub(r"</?untrusted_[a-z_]*>", "", s)
    s = re.sub(r"<[^>]+>", "", s)
    for d in EM_DASHES:
        s = s.replace(d, " - ")
    s = re.sub(r"\s+", " ", s).strip()
    return _BULLET.sub("", s).strip()


def frontmatter_title(text: str) -> str | None:
    m = re.match(r"\A---\n(.*?)\n---\n", text, flags=re.S)
    if m:
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            fm = {}
        if isinstance(fm, dict) and isinstance(fm.get("title"), str):
            return fm["title"].strip()
    h1 = re.search(r"^#\s+(.+?)\s*$", text, flags=re.M)
    if h1:
        head = h1.group(1)
        return head.split(":", 1)[1].strip() if ":" in head else head.strip()
    return None


def heading_kind(line: str, next_is_bullet: bool) -> str | None:
    """'R' | 'Q' | 'P' | 'other' when the line is a heading, else None."""
    m = _HEADING_LINE.match(line)
    head = (m.group("md") or m.group("bold") or m.group("colon") or "").strip() if m else ""
    if not head:
        bare = line.strip()
        words = bare.split()
        if (_BARE_HEADING.match(bare) and len(words) <= 7
                and (next_is_bullet or _RESP.search(bare) or _MUST.search(bare) or _NICE.search(bare)
                     or _ROLE_PARA.search(bare) or len(words) <= 4)):
            head = bare
    if not head:
        return None
    if _NICE.search(head):
        return "P"
    if _MUST.search(head):
        return "Q"
    if _RESP.search(head):
        return "R"
    return "role" if _ROLE_PARA.search(head) else "other"


def split_regions(body: str) -> list[dict[str, Any]]:
    """Every non-empty JD line in order: {text, region, heading, bullet, pos} with region R/Q/P/role/other."""
    raw = [html.unescape(x).replace("\xa0", " ") for x in body.splitlines()]
    out: list[dict[str, Any]] = []
    region, heading, pending, seen_bullet = "other", "", False, False
    for i, line in enumerate(raw):
        stripped = line.strip()
        if not stripped:
            continue
        if _BULLET.fullmatch(stripped):            # "- " alone: the bullet's text is on the next line
            pending = True
            continue
        bullet = bool(_BULLET.match(line)) or pending
        pending = False
        nxt = next((x.strip() for x in raw[i + 1:] if x.strip()), "")
        kind = None if bullet else heading_kind(line, bool(_BULLET.match(nxt)))
        if kind:
            region, heading, seen_bullet = kind, clean_line(line), False
            continue
        if not bullet and seen_bullet and region in ("R", "Q", "P"):
            region, heading, seen_bullet = "other", "", False   # a paragraph after the list ends the section
        seen_bullet = seen_bullet or bullet
        text = clean_line(line)
        if text:
            out.append({"text": text, "region": region, "heading": heading, "bullet": bullet})
    for pos, row in enumerate(out):
        row["pos"] = pos
    return out


# --------------------------------------------------------------------------- taxonomy and evidence
class _Term:
    def __init__(self, name: str, aliases: list[str], evidence: list[str]):
        self.name, self.aliases, self.evidence = name, aliases, evidence
        self.key = name.lower()
        self._rx = [_surface_rx(s) for s in self.surfaces()]

    def surfaces(self) -> list[str]:
        return [self.name] + self.aliases

    def find(self, text: str) -> str | None:
        for rx in self._rx:
            m = rx.search(text)
            if m:
                return m.group(0)
        return None


def _surface_rx(surface: str) -> re.Pattern[str]:
    parts = [re.escape(p) for p in re.split(r"[\s\-]+", surface.strip()) if p]
    tail = r"(?:s|es)?" if surface[-1:].isalpha() else ""
    return re.compile(r"(?<![A-Za-z0-9+#])" + r"[\s\-]*".join(parts) + tail + r"(?![A-Za-z0-9+#])", re.I)


def _walk_taxonomy(node: Any):
    if isinstance(node, list):
        for item in node:
            yield from _walk_taxonomy(item)
    elif isinstance(node, dict):
        name = node.get("skill") or node.get("name") or node.get("term")
        if isinstance(name, str):
            al = node.get("aliases") or node.get("synonyms") or []
            ev = node.get("evidence") or node.get("evidence_ids") or []
            yield _Term(name, [str(a) for a in (al if isinstance(al, list) else [al])],
                        [str(e) for e in (ev if isinstance(ev, list) else [ev])])
            return
        for value in node.values():
            if isinstance(value, (list, dict)):
                yield from _walk_taxonomy(value)


def _name_surfaces(name: str) -> list[str]:
    """Extra surfaces a taxonomy name implies: the head before a parenthetical ("SQL" from "SQL (Postgres,
    SQLite, DuckDB)") and the parenthetical's capitalized items (Postgres, DeBERTa-v3), never its plain words."""
    m = re.fullmatch(r"\s*(.+?)\s*\((.+)\)\s*", name)
    if not m:
        return []
    out = [] if m.group(1).lower() in _GENERIC_HEADS else [m.group(1)]
    for item in re.split(r",|;|/", m.group(2)):
        item = item.strip()
        if len(item) >= 2 and item[0].isupper():
            out.append(item)
    return out


def load_terms(path: Path | None) -> list[Any]:
    """Taxonomy terms (objects with name, surfaces(), find(), evidence). ats_check's Taxonomy when it loads
    (taxonomy over its built-in lexicon), else this file's reader of the same YAML shape. A taxonomy name's
    head and capitalized parenthetical items become aliases, and a lexicon term that is now a surface of a
    taxonomy term is dropped so each skill resolves to one canonical name."""
    if _ac is not None:
        terms = _ac.load_taxonomy(Path(path) if path else None).terms()
    elif path and Path(path).is_file():
        terms = list(_walk_taxonomy(yaml.safe_load(Path(path).read_text(encoding="utf-8"))))
    else:
        terms = []
    canonical = [t for t in terms if getattr(t, "source", "taxonomy") == "taxonomy"]
    taken = {s.lower() for t in canonical for s in t.surfaces()}
    for term in canonical:
        for s in _name_surfaces(term.name):
            if s.lower() not in taken:
                term.aliases.append(s)
                taken.add(s.lower())
    out = []
    for term in terms:
        if getattr(term, "source", "taxonomy") != "taxonomy" and term.name.lower() in taken:
            continue
        if hasattr(term, "_rx"):
            term._rx = [_surface_rx(s) for s in term.surfaces()]
        out.append(term)
    return out


def load_evidence(path: Path | None) -> dict[str, str]:
    """evidence id -> searchable text (note, source, tags, locator)."""
    if not path or not Path(path).is_file():
        return {}
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    entries = data.get("evidence", data) if isinstance(data, dict) else {}
    out: dict[str, str] = {}
    if isinstance(entries, dict):
        for eid, spec in entries.items():
            if not str(eid).startswith("ev-") or not isinstance(spec, dict):
                continue
            parts = []
            for key in ("note", "notes", "source", "locator", "tags", "text"):
                val = spec.get(key)
                if isinstance(val, list):
                    parts.append(" ".join(str(v) for v in val))
                elif val:
                    parts.append(str(val))
            out[str(eid)] = " ".join(parts)
    return out


def jd_phrases(text: str) -> list[str]:
    """Skill phrases the taxonomy may not name, as the JD writes them (lowercased): hyphenated compounds
    (computer-use, browser-control, full-stack) and "<modifier> harness|automation|loops|evals|..." pairs."""
    out = [m.group(0) for m in _COMPOUND.finditer(text) if m.group(0).lower() not in _NOT_PHRASES
           and not re.fullmatch(r"[\d-]+", m.group(0))]
    for m in _HEAD_NOUN.finditer(text):
        if m.group(1).lower() not in _PHRASE_STOP and m.group(1).lower() not in _STOP:
            out.append(f"{m.group(1)} {m.group(2)}")
    seen, keep = set(), []
    for p in out:
        if p.lower() not in seen:
            seen.add(p.lower())
            keep.append(p.lower())
    return keep


def row_terms(text: str, terms: list[Any]) -> list[Any]:
    """Terms found in a row, taxonomy terms first (canonical names) then JD phrases not already covered,
    each in order of first appearance."""
    hits = []
    for term in terms:
        found = term.find(text)
        if found:
            hits.append((text.lower().find(found.lower()), term))
    hits.sort(key=lambda h: (h[0], h[1].name))
    seen, out = set(), []
    for _, term in hits:
        if term.name not in seen:
            seen.add(term.name)
            out.append(term)
    covered = " ".join(s.lower() for t in out for s in t.surfaces())
    for phrase in jd_phrases(text):
        if not any(t.find(phrase) for t in out) and phrase not in covered:
            out.append(_Term(phrase, [], []))
    return out


def candidate_evidence(term_names: list[str], terms: list[Any], evidence: dict[str, str], text: str = "",
                       cap: int = EVIDENCE_CAP) -> list[str]:
    """Evidence ids for a row's canonical terms: the taxonomy entry's ids plus notes/tags/source that carry a
    surface of the term; sorted by matched-term count, then id; capped."""
    by_name = {t.name: t for t in terms}
    score: dict[str, int] = {}
    for name in term_names:
        term = by_name.get(name) or _Term(name, [], [])      # a JD phrase: matched by surface only
        matched: set[str] = set(term.evidence)
        for eid, blob in evidence.items():
            if eid not in matched and term.find(blob):
                matched.add(eid)
        for eid in matched:
            score[eid] = score.get(eid, 0) + 1
    ranked = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))
    return [eid for eid, _ in ranked[:cap]]


# --------------------------------------------------------------------------- rows and rank
def _content_words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9+#]{3,}", text) if w.lower() not in _STOP}


def key_terms(text: str, terms: list[Any]) -> list[str]:
    """Surfaces whose repetition elsewhere in the JD counts: the row's taxonomy terms (as written) and its
    hyphenated compounds (computer-use, zero-to-one)."""
    out = [t.find(text) for t in row_terms(text, terms)] + jd_phrases(text)
    seen, keys = set(), []
    for k in out:
        if k and k.lower() not in seen:
            seen.add(k.lower())
            keys.append(k)
    return keys


BOILERPLATE = re.compile(r"^(about (us|the company|the team at|[A-Z][\w.&-]*$)|who we are|our (mission|story|values)|"
                         r"benefits|perks|what we offer|compensation|equal (opportunity|employment)|eeo|"
                         r"diversity|accommodations?|privacy)", re.I)


def rank_row(text: str, pos: int, total: int, title: str, body: str, terms: list[Any], heading: str = "") -> int:
    """1 + (in the title or the first third) + (its key terms repeat). Company boilerplate (About us, benefits, EEO)
    stays at 1 wherever it sits: an About-us paragraph at the top is not the job."""
    if heading and BOILERPLATE.match(heading.strip()):
        return 1
    rank = 1
    title_words = _content_words(title)
    if (title_words & _content_words(text)) or pos < max(1, total / 3):
        rank += 1
    for key in key_terms(text, terms):
        rx = _surface_rx(key)
        if len(rx.findall(body)) > len(rx.findall(text)):
            rank += 1
            break
    return rank


def implied_rows(title: str, lines: list[dict[str, Any]]) -> tuple[list[str], int | None]:
    """The title, then up to two sentences of the role paragraph (the first paragraph under an 'about the role'
    heading, else the first non-bullet line of the JD); and the position of the paragraph consumed."""
    out = [title] if title else []
    para = next((r for r in lines if r["region"] == "role" and not r["bullet"]), None)
    if para is None:
        para = next((r for r in lines if not r["bullet"] and r["region"] == "other"
                     and not _FILTER_LINE.match(r["text"]) and len(r["text"].split()) >= 6), None)
    if para is not None:
        for sent in re.split(r"(?<=[.!?])\s+", para["text"]):
            if len(out) >= IMPLIED_CAP:
                break
            if sent.strip():
                out.append(sent.strip())
    return out[:IMPLIED_CAP], (para["pos"] if para is not None else None)


def build_matrix(jd: Path, title: str | None, taxonomy_path: Path | None, evidence_path: Path | None,
                 today: str | None = None) -> dict[str, Any]:
    raw = Path(jd).read_text(encoding="utf-8")
    body = jd_scope(raw)
    posted = title or frontmatter_title(raw) or ""
    if any(d in posted for d in EM_DASHES):
        raise UsageError("the title carries an em-dash; use a plain hyphen")
    terms = load_terms(taxonomy_path)
    evidence = load_evidence(evidence_path)
    lines = split_regions(body)
    body_text = clean_line(" ".join(r["text"] for r in lines))
    rows: list[dict[str, Any]] = []
    counters = {t: 0 for t in TYPES}

    def add(kind: str, text: str, pos: int, heading: str = "") -> None:
        counters[kind] += 1
        found = row_terms(text, terms)
        names = [t.name for t in found]
        rows.append({
            "id": f"{kind}{counters[kind]}", "type": kind,
            "rank": rank_row(text, pos, len(lines), posted, body_text, terms, heading),
            "heading": heading, "text": text, "terms": names, "synonyms": [],
            "candidate_evidence": candidate_evidence(names, terms, evidence, text),
            "answer": "", "ceiling": "",
        })

    implied, consumed = implied_rows(posted, lines)
    for text in implied:
        add("I", text, 0, "implied")
    for r in lines:
        if r["pos"] == consumed:
            continue                                   # already the I rows
        kind = r["region"] if r["region"] in ("R", "Q", "P") else "O"
        if kind == "O" and _FILTER_LINE.match(r["text"]):
            kind = "Q"                                 # a literal filter (minimum education, required field)
        add(kind, r["text"], r["pos"], r["heading"] or ("filter" if kind == "Q" else ""))
    order = {t: i for i, t in enumerate(TYPES)}
    rows.sort(key=lambda r: (order[r["type"]], int(r["id"][1:])))
    return {"jd": str(jd), "title": posted, "built": today or date.today().isoformat(),
            "normalized": "em-dashes written as ' - '; HTML entities decoded", "allowed_ngrams": [], "rows": rows}


def assert_no_em_dash(text: str, what: str) -> None:
    if any(d in text for d in EM_DASHES):
        raise UsageError(f"{what} would contain an em-dash; refused")


def write_matrix(matrix: dict[str, Any], out: Path) -> None:
    text = yaml.safe_dump(matrix, sort_keys=False, allow_unicode=True, width=1000)
    assert_no_em_dash(text, f"the matrix {out}")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(text, encoding="utf-8")


def markdown_table(matrix: dict[str, Any]) -> str:
    lines = [f"# {matrix['title']}  (jd: {matrix['jd']}, built {matrix['built']})", "",
             "| id | type | rank | terms | evidence | text |", "|---|---|---|---|---|---|"]
    for r in matrix["rows"]:
        text = r["text"] if len(r["text"]) <= 60 else r["text"][:57].rstrip() + "..."
        lines.append(f"| {r['id']} | {r['type']} | {r['rank']} | {', '.join(r['terms'])} | "
                     f"{len(r['candidate_evidence'])} | {text.replace('|', '/')} |")
    counts = {t: sum(1 for r in matrix["rows"] if r["type"] == t) for t in TYPES}
    lines += ["", "rows: " + ", ".join(f"{t} {n}" for t, n in counts.items())]
    return "\n".join(lines)


# --------------------------------------------------------------------------- check
def check_matrix(matrix: dict[str, Any]) -> list[dict[str, Any]]:
    bad = []
    for r in matrix.get("rows") or []:
        if r.get("type") not in ("R", "Q", "P"):
            continue
        answer = str(r.get("answer") or "").strip()
        if not answer:
            why = "empty answer"
        elif not _ANSWER_OK.match(answer):
            why = "answer must start with line:, Q<n> or GAP: <reason>"
        else:
            continue
        bad.append({"id": r.get("id"), "type": r.get("type"), "text": r.get("text", ""), "answer": answer, "why": why})
    return bad


# --------------------------------------------------------------------------- set
def set_row(matrix: dict[str, Any], row_id: str, *, type_: str | None = None, rank: int | None = None,
            answer: str | None = None, ceiling: str | None = None, note: str | None = None,
            synonyms: list[str] | None = None, drop_evidence: bool = False) -> list[str]:
    """Change one row's fields in place and return the changes as `field: old -> new` lines. The JD fields (text,
    terms, heading) are the employer's words and never change here; a rebuild changes them."""
    rows = [r for r in matrix.get("rows") or [] if r.get("id") == row_id]
    if not rows:
        raise UsageError(f"no row {row_id!r} in the matrix")
    r, changes = rows[0], []
    if type_ is not None and type_ not in TYPES:
        raise UsageError(f"--type must be one of {', '.join(TYPES)}")
    if answer is not None and answer.strip() and not _ANSWER_OK.match(answer.strip()):
        raise UsageError("--answer must start with line:, Q<n> or GAP: <reason>")
    new = {"type": type_, "rank": rank, "answer": answer, "ceiling": ceiling, "note": note}
    if synonyms:
        new["synonyms"] = list(dict.fromkeys((r.get("synonyms") or []) + synonyms))
    if drop_evidence:
        new["candidate_evidence"] = []
    for k, v in new.items():
        if v is not None and r.get(k) != v:
            changes.append(f"{row_id}.{k}: {r.get(k)!r} -> {v!r}")
            r[k] = v
    return changes


# --------------------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    b = sub.add_parser("build", help="write the matrix skeleton from a JD")
    b.add_argument("--jd", type=Path, required=True)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--title", default=None, help="the posted title (default: the record's frontmatter or H1)")
    b.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    b.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    b.add_argument("--date", "--today", dest="date", default=None, help="the built: stamp (default: today)")
    b.add_argument("--json", action="store_true", help="print the matrix as JSON instead of a table")
    c = sub.add_parser("check", help="list unanswered R/Q/P rows")
    c.add_argument("--matrix", type=Path, required=True)
    c.add_argument("--json", action="store_true")
    st = sub.add_parser("set", help="change one row's type, rank, answer, ceiling, note or synonyms")
    st.add_argument("--matrix", type=Path, required=True)
    st.add_argument("--row", required=True)
    st.add_argument("--type", dest="type_", default=None)
    st.add_argument("--rank", type=int, default=None)
    st.add_argument("--answer", default=None)
    st.add_argument("--ceiling", default=None)
    st.add_argument("--note", default=None)
    st.add_argument("--synonym", action="append", default=[])
    st.add_argument("--drop-evidence", action="store_true", help="clear candidate_evidence (noise matches)")
    st.add_argument("--dry-run", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_usage(sys.stderr)
        return 2
    try:
        if args.cmd == "build":
            if not args.jd.is_file():
                raise UsageError(f"no such JD: {args.jd}")
            if args.date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
                raise UsageError(f"--date expects YYYY-MM-DD, got {args.date!r}")
            matrix = build_matrix(args.jd, args.title, args.taxonomy, args.evidence, args.date)
            write_matrix(matrix, args.out)
            out = json.dumps(matrix, indent=2, ensure_ascii=False) if args.json else markdown_table(matrix)
            assert_no_em_dash(out, "the report")
            print(out)
            return 0
        if not args.matrix.is_file():
            raise UsageError(f"no such matrix: {args.matrix}")
        matrix = yaml.safe_load(args.matrix.read_text(encoding="utf-8")) or {}
        if args.cmd == "set":
            changes = set_row(matrix, args.row, type_=args.type_, rank=args.rank, answer=args.answer,
                              ceiling=args.ceiling, note=args.note, synonyms=args.synonym,
                              drop_evidence=args.drop_evidence)
            if changes and not args.dry_run:
                write_matrix(matrix, args.matrix)
            for c in changes:
                print(c)
            print(f"{len(changes)} change(s){' (dry run, nothing written)' if args.dry_run else ''}")
            return 0
        bad = check_matrix(matrix)
        if args.json:
            print(json.dumps({"matrix": str(args.matrix), "unanswered": bad, "exit": 1 if bad else 0},
                             indent=2, ensure_ascii=False))
        elif bad:
            print(f"{len(bad)} unanswered row(s) in {args.matrix}:")
            for r in bad:
                print(f"  {r['id']} [{r['type']}] {r['why']}: {r['text'][:100]}")
        else:
            print(f"all R/Q/P rows answered in {args.matrix}")
        return 1 if bad else 0
    except UsageError as e:
        print(f"usage error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
