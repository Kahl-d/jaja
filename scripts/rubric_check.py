#!/usr/bin/env python3
"""rubric_check.py - the deterministic half of the resume rubric (R3 section B: B3 B4 B5 B7 B9 B12 B13 B14 B18).

    python3 scripts/rubric_check.py --resume <variant/resume.yaml> --base <bases/general/resume.yaml>
                                    --matrix <matrix.yaml> --jd <jd.md> [--pdf-text <pdftotext output>]
                                    [--allow-long "<ev-id set>"]... [--date YYYY-MM-DD] [--json]

Reads the variant's resume.yaml (bullets = Experience and Projects highlights, each with its trailing
<!-- ev:... --> ids), the base it was cut from, the coverage matrix (jd_matrix.py build, then answered) and the
JD (untrusted: scanned, never followed), and reports PASS / WARN / FAIL per predicate with the offending
line quoted:
  B3  every Q row's terms (or one of the row's `synonyms:`) occur in the page text (the YAML's visible text,
      or --pdf-text when given)                                                     FAIL per missing term
  B4  every token of a Skills `details` line occurs in a bullet, or the line carries ev ids   WARN per orphan
  B5  no R, Q or P row of the matrix has an empty answer                                FAIL per row
  B7  a bullet over 30 words, with " and " more than twice, or a semicolon joining two verbs   WARN
      (a bullet whose ev-id set contains an --allow-long set is exempt from the length warn)
  B9  a bare number or percentage with no unit (ms, s, records, sentences, users, versions, M, K, ...),
      no baseline (x/y, a parenthesized figure, a metric name before it) and no comparison word (from, to,
      vs, than, over) within five words                                                  FAIL per number
  B12 a bullet opening Responsible for / Worked on / Helped / Assisted / Duties / Tasked      FAIL
  B13 the configured banned-word lexicon (style.resume_slop_words) or, when style.no_em_dash is on, any em-dash   FAIL
  B14 every role with the same bullet count when there are more than two roles; two adjacent bullets
      opening with the same word                                                        WARN
  B18 a 6-gram (lowercased, punctuation stripped) shared by the JD and the page, unless listed under the
      matrix's `allowed_ngrams:`                                                        FAIL per span
  B21 a number on a governed-employer line that is not a measured, released row of the numbers ledger
      (numbers_ledger.py): a target, estimate or held row FAILs, and so does a number the ledger lacks; never spared
  B22 a base bullet whose work is gone from the page: no page bullet carries its text or any of its ev ids
      (alignment rewords, reorders and adds; it never drops base work)                         FAIL per bullet
  B23 focused and dream tiers: no Experience or Projects bullet differs from the base (a Skills-only or empty
      diff); a WARN when the matrix's `skills_only_ok:` carries the owner's own words and date      FAIL
  B24 focused and dream tiers: NOTES.md next to the resume has `## Lead and order` with a line `applied: ...`
 or `not applied: <why>`  FAIL
and `changed_bullets`: every bullet that differs from the base (plus the base bullets that were dropped), for
the base-comparer (B16). The report never carries an em-dash (quoted ones print as [em-dash]).
Line-level findings (B9 B12 B13 B18) on a line the base carries verbatim are warnings only: the base is the owner's
approved page; the gate stops regressions. Exit 0 when no predicate fails (warnings allowed), 1 on any FAIL, 2 on a usage error. Deterministic, no network.
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
import agency_config as cfg  # noqa: E402

try:
    import jd_matrix as _jm  # noqa: E402  (same folder: jd_scope, surface regexes)
except Exception:  # pragma: no cover
    _jm = None

EM_DASHES = "".join(chr(c) for c in (0x2014, 0x2015, 0x2E3A, 0x2E3B))   # em-dash and kin
PREDICATES = ("B3", "B4", "B5", "B7", "B9", "B12", "B13", "B14", "B18")
NAMES = {"B3": "explicit requirements visible", "B4": "skill terms live in bullets",
         "B5": "preferred and required rows answered", "B7": "one result per bullet",
         "B9": "numbers have baselines", "B12": "no duty statements", "B13": "no AI-slop lexicon",
         "B14": "no shape sameness", "B18": "mirrors, never pastes",
    "B19": "no NDA term", "B20": "reader model clean",
    "B21": "governed-employer numbers from the ledger", "B22": "no base bullet dropped",
    "B23": "a focused page aligns a bullet, not only Skills", "B24": "the package's lead and order applied or why not"}
BULLET_SECTIONS = ("Experience", "Projects")
EV_RE = re.compile(r"<!--\s*ev:\s*(?P<ids>[^>]*?)\s*-->")
LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
DUTY = re.compile(r"^\s*(?:responsible for|worked on|helped|assisted|duties|tasked)\b", re.I)
def slop_words() -> list[str]:
    """The banned-word lexicon for B13: style.resume_slop_words (your own list; empty = no word check)."""
    return [str(w) for w in cfg.get("style.resume_slop_words", []) or [] if str(w).strip()]


def slop_re() -> "re.Pattern[str] | None":
    words = slop_words()
    if not words:
        return None
    return re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(w).replace(r"\ ", r"\s+") for w in words) + r")(?![\w-])",
                      re.I)


UNITS = {"ms", "s", "sec", "secs", "seconds", "second", "min", "mins", "minutes", "minute", "hours", "hour", "h",
         "days", "day", "weeks", "months", "years", "fps", "hz", "khz", "ghz", "px", "gb", "mb", "kb", "tb",
         "records", "record", "sentences", "sentence", "essays", "essay", "users", "user", "versions", "version",
         "releases", "release", "people", "engineers", "tokens", "requests", "rows", "files", "pages", "labels",
         "themes", "classes", "tests", "runs", "prompts", "tasks", "roles", "members", "bugs", "lines", "items",
         "documents", "docs", "samples", "examples", "entries", "points", "pts", "x", "k", "m", "b", "percent",
         "defects", "criteria", "gates", "agents", "servers", "ports", "reviewers", "commits", "prs", "cases"}
METRIC_BEFORE = {"macro-f1", "pr-auc", "f1", "auc", "accuracy", "precision", "recall", "gpa", "p50", "p95", "p99",
                 "score", "of", "top", "rank", "version", "v", "team", "ios", "python"}
COMPARE = {"from", "to", "vs", "vs.", "versus", "than", "over", "against", "baseline", "range", "within", "under",
           "below", "above"}
NUM_RE = re.compile(r"(?<![\w.])[+-]?\d[\d,]*(?:\.\d+)?(?P<suffix>%|[KMB]\+?|x)?(?![\w])")
WORD_RE = re.compile(r"[A-Za-z0-9][\w'./+%-]*")
VERB_AFTER_SEMI = re.compile(r";\s*(?:and\s+|then\s+)?(?:[a-z]+ed|built|ran|led|made|cut|drove|gave|wrote|shipped|"
                             r"grew|took|kept|set|chose|became|won|rebuilt|split|put|sent|held|ran)\b", re.I)


class UsageError(Exception):
    pass


# --------------------------------------------------------------------------- text helpers
def safe(text: str) -> str:
    for d in EM_DASHES:
        text = text.replace(d, "[em-dash]")
    return text


def split_claim(text: str) -> tuple[str, list[str]]:
    """(visible text, ev ids) of one YAML claim: the comment stripped, links to their text, emphasis dropped."""
    m = EV_RE.search(text)
    ids = [i for i in re.split(r"[,\s]+", m.group("ids")) if i] if m else []
    body = EV_RE.sub("", text)
    body = LINK_RE.sub(lambda mm: mm.group(1), body)
    body = re.sub(r"\*\*|\*|`", "", body)
    return re.sub(r"\s+", " ", body).strip(), ids


def surface_rx(surface: str) -> re.Pattern[str]:
    if _jm is not None:
        return _jm._surface_rx(surface)
    parts = [re.escape(p) for p in re.split(r"[\s\-]+", surface.strip()) if p]
    tail = r"(?:s|es)?" if surface[-1:].isalpha() else ""
    return re.compile(r"(?<![A-Za-z0-9+#])" + r"[\s\-]*".join(parts) + tail + r"(?![A-Za-z0-9+#])", re.I)


def term_surfaces(term: str) -> list[str]:
    """A term and the forms a page may use for it: the head of a parenthetical name and its items."""
    out = [term]
    if _jm is not None:
        out += _jm._name_surfaces(term)
    else:
        m = re.fullmatch(r"\s*(.+?)\s*\((.+)\)\s*", term)
        if m:
            out += [m.group(1)] + [i.strip() for i in m.group(2).split(",") if i.strip()]
    return [s for s in out if s.strip()]


def singular(phrase: str) -> str:
    """The singular of a phrase's last word (harnesses -> harness, gates -> gate, sets -> set)."""
    w = phrase.rstrip()
    if re.search(r"(?:ss|sh|ch|x)es$", w):
        return w[:-2]
    if re.search(r"[^aeiou]ies$", w):
        return w[:-3] + "y"
    if re.search(r"[^s]s$", w):
        return w[:-1]
    return w


def present(term: str, text: str) -> bool:
    """The term, a form of its name, or its singular appears in the text (plurals match either way)."""
    surfaces = term_surfaces(term)
    surfaces += [singular(s) for s in surfaces if singular(s) != s]
    return any(surface_rx(s).search(text) for s in surfaces)


def normalize_words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).split()


def ngrams(words: list[str], n: int = 6) -> list[tuple[str, ...]]:
    return [tuple(words[i:i + n]) for i in range(len(words) - n + 1)]


def jd_text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    body = _jm.jd_scope(raw) if _jm is not None else re.sub(r"\A---\n.*?\n---\n", "", raw, flags=re.S)
    body = re.sub(r"</?untrusted_[a-z_]*>", "", body)
    return html.unescape(body).replace("\xa0", " ")


# --------------------------------------------------------------------------- the page
def load_page(path: Path) -> dict[str, Any]:
    """bullets (Experience/Projects highlights), roles, skills lines, and every visible line of the resume."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cv = data.get("cv") if isinstance(data, dict) else None
    if not isinstance(cv, dict):
        raise UsageError(f"{path}: no cv: mapping")
    sections = cv.get("sections") or {}
    bullets, roles, skills, lines = [], [], [], []

    def role_name(entry: dict[str, Any]) -> str:
        head = entry.get("company") or entry.get("name") or entry.get("institution") or "?"
        pos = entry.get("position") or entry.get("degree")
        return f"{head} / {pos}" if pos else str(head)

    for title, entries in sections.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, str):
                text, ids = split_claim(entry)
                lines.append({"section": title, "text": text, "ids": ids})
                continue
            if not isinstance(entry, dict):
                continue
            name = role_name(entry)
            if title == "Skills":
                text, ids = split_claim(str(entry.get("details") or ""))
                skills.append({"label": str(entry.get("label") or ""), "text": text, "ids": ids})
                lines.append({"section": title, "text": text, "ids": ids})
                continue
            if entry.get("summary"):
                text, ids = split_claim(str(entry["summary"]))
                lines.append({"section": title, "text": text, "ids": ids, "role": name})
            hl = [h for h in (entry.get("highlights") or []) if isinstance(h, str)]
            role_bullets = []
            for i, h in enumerate(hl, 1):
                text, ids = split_claim(h)
                item = {"section": title, "role": name, "index": i, "text": text, "ids": ids, "raw": h}
                lines.append(item)
                if title in BULLET_SECTIONS:
                    bullets.append(item)
                    role_bullets.append(item)
            if title == "Experience":
                roles.append({"role": name, "bullets": role_bullets})
    header = []
    for title, entries in sections.items():
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict):
                header += [str(entry.get(k)) for k in ("label", "position", "degree", "area", "name") if entry.get(k)]
    return {"bullets": bullets, "roles": roles, "skills": skills, "lines": lines,
            "text": "\n".join(x["text"] for x in lines), "header": "\n".join(header),
            "raw": path.read_text(encoding="utf-8")}


# NDA terms (B19): terms the owner is bound not to publish never appear on a page. They come from policy.nda_terms in
# the config; profile/private-holds.txt may add more under a line "# nda" (one per line; never echoed in reports).
def nda_builtin() -> tuple[str, ...]:
    return tuple(str(t).lower() for t in cfg.get("policy.nda_terms", []) or [] if str(t).strip())



PRIVATE_HOLDS = Path(__file__).resolve().parents[1] / "profile" / "private-holds.txt"
TAXONOMY = Path(__file__).resolve().parents[1] / "profile" / "skills-taxonomy.yaml"


def taxonomy_evidence() -> dict[str, set[str]]:
    """skill name (lowercased, and its head before a parenthesis) -> its evidence ids, from profile/skills-taxonomy.yaml."""
    out: dict[str, set[str]] = {}
    try:
        data = yaml.safe_load(TAXONOMY.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return out
    for entries in (data.get("groups") or {}).values():
        for e in entries or []:
            if isinstance(e, dict) and e.get("skill"):
                ids = {str(i) for i in (e.get("evidence") or [])}
                name = str(e["skill"]).strip().lower()
                out[name] = ids
                out.setdefault(name.split("(")[0].strip(), ids)
    return out


def nda_terms() -> list[str]:
    terms = list(nda_builtin())
    try:
        block = False
        for line in PRIVATE_HOLDS.read_text(encoding="utf-8").splitlines():
            t = line.strip()
            if t.lower().startswith("# nda"):
                block = True
                continue
            if t.startswith("#"):
                block = False
                continue
            if block and t:
                terms.append(t.lower())
    except OSError:
        pass
    return terms


def b19(page: dict[str, Any]) -> dict[str, Any]:
    findings = []
    for x in page["lines"]:
        low = x["text"].lower()
        for i, term in enumerate(nda_terms()):
            if re.search(r"(?<![\w])" + re.escape(term) + r"(?![\w])", low):
                label = term if i < len(nda_builtin()) else "a private NDA term"
                findings.append({"line": x["text"], "detail": f"{x.get('role', x['section'])}: NDA term on the page ({label})"})
                break
    return _pred("B19", findings, fail=True)


# --------------------------------------------------------------------------- predicates
def _pred(pid: str, findings: list[dict[str, str]], fail: bool, notes: list[str] | None = None) -> dict[str, Any]:
    status = "PASS" if not findings else ("FAIL" if fail else "WARN")
    return {"id": pid, "name": NAMES[pid], "status": status,
            "findings": [{"line": safe(f.get("line", "")), "detail": safe(f["detail"])} for f in findings],
            "notes": [safe(n) for n in (notes or [])]}


OR_LIST = re.compile(r"\b(or|such as|e\.g\.|including|one of|any of|similar)\b", re.I)
GENERIC_TAIL = re.compile(r"\s+(engineering|design|development|systems?|practice|work)$", re.I)


def _b3_surfaces(term: str) -> list[str]:
    """The term's own forms plus, for a long taxonomy name, its head without a generic tail word
    ("Agent harness engineering (modes, ...)" also matches "agent harness")."""
    out = term_surfaces(term)
    for s in list(out):
        short = GENERIC_TAIL.sub("", s.strip())
        if short and short != s.strip() and len(short.split()) >= 2:
            out.append(short)
    return out


def _soft(term: str) -> bool:
    """A lowercase hyphenated phrase ("fast-paced", "well-defined", "user-facing") is a trait, not a requirement a
    page can show literally; it never fails B3 (the editor and readers judge fit)."""
    t = term.strip()
    return t == t.lower() and "-" in t


def b3(matrix: dict[str, Any], page_text: str, page_ids: set[str] | None = None) -> dict[str, Any]:
    """A Q row is met when a term (or a synonym) is on the page, one alternative of an "or" list is, or the page
    carries the evidence of the taxonomy skill the term names (the work is shown, in the page's own words)."""
    findings, notes = [], []
    page_ids = page_ids or set()
    tax_ev = taxonomy_evidence() if page_ids else {}
    for row in matrix.get("rows") or []:
        if row.get("type") != "Q":
            continue
        syn = row.get("synonyms") or []
        syn = syn if isinstance(syn, list) else [syn]
        if any(present(str(s), page_text) for s in syn):
            continue
        terms = [str(t) for t in (row.get("terms") or [])]
        hard = [t for t in terms if not _soft(t)]
        notes += [f"{row.get('id')}: '{t}' is a trait, not checked literally" for t in terms if _soft(t)]
        hits = [t for t in hard if any(present(x, page_text) for x in _b3_surfaces(t))
                or (page_ids and tax_ev.get(t.strip().lower(), set()) & page_ids)]
        if hits and OR_LIST.search(str(row.get("text", ""))):
            continue            # "Python, Go, Scala, or Java": one of the alternatives is enough
        for term in hard:
            if term not in hits:
                findings.append({"line": row.get("text", ""), "detail": f"{row.get('id')}: '{term}' not on the page"})
    return _pred("B3", findings, fail=True, notes=notes)


def b4(page: dict[str, Any]) -> dict[str, Any]:
    findings, covered = [], {}
    bullet_text = "\n".join(b["text"] for b in page["bullets"])
    for sk in page["skills"]:
        for token in re.split(r"[,;()]", sk["text"]):
            token = token.strip().strip(".")
            if len(token) < 2:
                continue
            if present(token, bullet_text):
                continue
            if sk["ids"]:
                covered.setdefault(sk["label"], []).append(token)
            else:
                findings.append({"line": sk["text"], "detail": f"'{token}' (Skills: {sk['label']}) is in no bullet and the line has no ev ids"})
    notes = [f"in no bullet, covered by the line's ev ids (Skills: {label}): " + ", ".join(toks)
             for label, toks in covered.items()]
    return _pred("B4", findings, fail=False, notes=notes)


def b5(matrix: dict[str, Any]) -> dict[str, Any]:
    findings = [{"line": "", "detail": f"{r.get('id')} [{r.get('type')}] empty answer: {str(r.get('text', ''))[:40]}"}
                for r in (matrix.get("rows") or []) if r.get("type") in ("R", "Q", "P") and not str(r.get("answer") or "").strip()]
    return _pred("B5", findings, fail=True)


def b7(page: dict[str, Any], allow_long: list[set[str]]) -> dict[str, Any]:
    findings = []
    for b in page["bullets"]:
        reasons = []
        words = len(WORD_RE.findall(b["text"]))
        exempt = any(s and s <= set(b["ids"]) for s in allow_long)
        if words > 30 and not exempt:
            reasons.append(f"{words} words")
        ands = len(re.findall(r"\band\b", b["text"], flags=re.I))
        if ands > 2:
            reasons.append(f"'and' {ands} times")
        if VERB_AFTER_SEMI.search(b["text"]):
            reasons.append("a semicolon joins two verbs")
        if reasons:
            findings.append({"line": b["text"], "detail": f"{b['role']} #{b['index']}: " + "; ".join(reasons)})
    return _pred("B7", findings, fail=False)


def _is_year(tok: str) -> bool:
    return bool(re.fullmatch(r"(?:19|20)\d\d", tok))


def bare_numbers(text: str) -> list[str]:
    """Numbers in a bullet that carry no unit, baseline or nearby comparison word."""
    out = []
    toks = WORD_RE.findall(text)
    low = [t.lower().strip(".,;:") for t in toks]
    for m in NUM_RE.finditer(text):
        num = m.group(0)
        if _is_year(num) or m.group("suffix") in ("K", "K+", "M", "M+", "B", "B+", "x"):
            continue
        before, after = text[:m.start()], text[m.end():]
        if re.search(r"/\s*$", before) or re.match(r"\s*/", after) or re.search(r"\(\s*$", before):
            continue                                      # x/y ratio or a parenthesized figure
        if re.match(r"\s*of\s+\d", after, flags=re.I) or re.search(r"\d\s+of\s*$", before, flags=re.I):
            continue                                      # "55 of 56": the figure is its own baseline
        if re.match(r"\s*(?:" + "|".join(re.escape(u) for u in UNITS) + r")(?![\w])", after, flags=re.I):
            continue
        if re.match(r"\s*\w[\w-]*\s+(?:" + "|".join(re.escape(u) for u in UNITS) + r")(?![\w])", after, flags=re.I):
            continue                                      # "17,622 essay sentences"
        prev = re.findall(r"[\w.+-]+", before)
        if prev and prev[-1].lower().strip(".,;:") in METRIC_BEFORE:
            continue
        pos = len(WORD_RE.findall(before))
        window = low[max(0, pos - 5):pos] + low[pos + 1:pos + 6]
        if any(w in COMPARE for w in window):
            continue
        out.append(num)
    return out


def b9(page: dict[str, Any]) -> dict[str, Any]:
    findings = []
    for b in page["bullets"]:
        for num in bare_numbers(b["text"]):
            findings.append({"line": b["text"], "detail": f"{b['role']} #{b['index']}: '{num}' has no unit, baseline or comparison"})
    return _pred("B9", findings, fail=True)


def b12(page: dict[str, Any]) -> dict[str, Any]:
    findings = [{"line": x["text"], "detail": f"{x.get('role', x['section'])}: opens with a duty statement"}
                for x in page["lines"] if DUTY.match(x["text"])]
    return _pred("B12", findings, fail=True)


def b13(page: dict[str, Any]) -> dict[str, Any]:
    findings = []
    pat = slop_re()
    no_dash = bool(cfg.get("style.no_em_dash", False))
    for x in page["lines"]:
        hits = sorted({h.lower() for h in (pat.findall(x["text"]) if pat else [])})
        if hits:
            findings.append({"line": x["text"], "detail": f"{x.get('role', x['section'])}: slop: " + ", ".join(hits)})
        if no_dash and any(d in x["text"] for d in EM_DASHES):
            findings.append({"line": x["text"], "detail": f"{x.get('role', x['section'])}: em-dash (U+2014)"})
    if no_dash and any(d in page["raw"] for d in EM_DASHES) and not any("em-dash" in f["detail"] for f in findings):
        findings.append({"line": "", "detail": "em-dash (U+2014) in the resume file outside its visible lines"})
    return _pred("B13", findings, fail=True)


def b14(page: dict[str, Any]) -> dict[str, Any]:
    findings = []
    roles = page["roles"]
    counts = [len(r["bullets"]) for r in roles]
    if len(roles) > 2 and len(set(counts)) == 1:
        findings.append({"line": "", "detail": f"same bullet count ({counts[0]}) in all {len(roles)} roles"})
    for r in roles:
        for a, b in zip(r["bullets"], r["bullets"][1:]):
            fa = (WORD_RE.findall(a["text"]) or [""])[0]
            fb = (WORD_RE.findall(b["text"]) or [""])[0]
            if fa and fa.lower() == fb.lower():
                findings.append({"line": b["text"], "detail": f"{r['role']} #{a['index']} and #{b['index']} both open with '{fa}'"})
    return _pred("B14", findings, fail=False)


def b18(page: dict[str, Any], jd_body: str, allowed: list[str]) -> dict[str, Any]:
    allowed_set = {tuple(normalize_words(str(a))) for a in allowed}
    jd_grams = set()
    for line in jd_body.splitlines():
        jd_grams.update(ngrams(normalize_words(line)))
    findings = []
    for x in page["lines"]:
        words = normalize_words(x["text"])
        shared = [i for i, g in enumerate(ngrams(words)) if g in jd_grams and g not in allowed_set]
        spans, start = [], None
        for i in shared:
            if start is None or i > prev + 1:
                if start is not None:
                    spans.append((start, prev + 6))
                start = i
            prev = i
        if start is not None:
            spans.append((start, prev + 6))
        for s, e in spans:
            findings.append({"line": x["text"], "detail": f"{x.get('role', x['section'])}: shares with the JD: \"{' '.join(words[s:e])}\""})
    return _pred("B18", findings, fail=True)


# --------------------------------------------------------------------------- base diff
def diff_bullets(page: dict[str, Any], base: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    def by_role(p: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for b in p["bullets"]:
            out.setdefault(b["role"], []).append(b)
        return out
    v, b = by_role(page), by_role(base)
    changed = [{"role": role, "index": x["index"], "text": x["text"], "ids": x["ids"]}
               for role, items in v.items() for x in items
               if x["text"] not in {y["text"] for y in b.get(role, [])}]
    removed = [{"role": role, "index": x["index"], "text": x["text"], "ids": x["ids"]}
               for role, items in b.items() for x in items
               if x["text"] not in {y["text"] for y in v.get(role, [])}]
    return changed, removed


def b22(removed: list[dict[str, Any]], page: dict[str, Any]) -> dict[str, Any]:
    """A base bullet the variant rewrote still carries its ev ids; one whose text and ids are both gone from
    every page bullet is dropped base work."""
    texts = {x["text"] for x in page["bullets"]}
    ids = {i for x in page["bullets"] for i in x["ids"]}
    dropped = [x for x in removed if x["text"] not in texts and not set(x["ids"]) & ids]
    return _pred("B22", [{"line": x["text"], "detail": f"{x['role']} #{x['index']}: base bullet dropped"}
                         for x in dropped], fail=True)


def b23(changed: list[dict[str, Any]], removed: list[dict[str, Any]], mat: dict[str, Any]) -> dict[str, Any]:
    """A focused page that only touches Skills is not aligned; the recruiter fails it."""
    if changed or removed:
        return _pred("B23", [], fail=True)
    ok = str(mat.get("skills_only_ok") or "").strip()
    finding = [{"detail": "no Experience or Projects bullet differs from the base: a Skills-only diff on a "
                          "focused page (align the lead bullet to the role's top need)"}]
    if ok:
        return _pred("B23", finding, fail=False, notes=[f"skills_only_ok: {ok}"])
    return _pred("B23", finding, fail=True)


LEAD_HEAD = re.compile(r"^##\s+Lead and order\s*$", re.M | re.I)
LEAD_LINE = re.compile(r"^\s*-?\s*(applied|not applied)\s*:\s*(\S.*)$", re.M | re.I)


def b24(resume: Path) -> dict[str, Any]:
    """Charter section 13 [5]: the tailor applies the package's lead and order (section 7) or writes why not, in
    NOTES.md `## Lead and order`."""
    notes = resume.with_name("NOTES.md")
    if not notes.is_file():
        return _pred("B24", [{"detail": f"no NOTES.md next to {resume.name}: write `## Lead and order`"}], fail=True)
    text = notes.read_text(encoding="utf-8")
    head = LEAD_HEAD.search(text)
    if not head:
        return _pred("B24", [{"detail": "NOTES.md has no `## Lead and order` section"}], fail=True)
    nxt = re.search(r"^## ", text[head.end():], re.M)
    body = text[head.end(): head.end() + nxt.start()] if nxt else text[head.end():]
    if not LEAD_LINE.search(body):
        return _pred("B24", [{"detail": "`## Lead and order` needs a line `applied: <the lead bullet and order>` "
                                        "or `not applied: <why>`"}], fail=True)
    return _pred("B24", [], fail=True)


# --------------------------------------------------------------------------- base lines
LINE_PREDICATES = ("B9", "B12", "B13", "B18")   # B19 (NDA) is never spared, even on a base line


def _spare_base_lines(pred: dict[str, Any], base_page: dict[str, Any]) -> dict[str, Any]:
    """A line-level finding on a line the base already carries verbatim is a warning, never a FAIL: the
    base is the owner's approved page. This gate stops regressions a variant introduces."""
    if pred["id"] not in LINE_PREDICATES or pred["status"] != "FAIL":
        return pred
    base_lines = {safe(x["text"]) for x in base_page["lines"]}
    kept = [f for f in pred["findings"] if not f["line"] or f["line"] not in base_lines]
    spared = [f for f in pred["findings"] if f["line"] and f["line"] in base_lines]
    if not spared:
        return pred
    notes = pred["notes"] + [f"on the approved base, warning only: {f['detail']}" for f in spared]
    status = "FAIL" if kept else "WARN"
    return {**pred, "status": status, "findings": kept if kept else spared, "notes": notes}


# --------------------------------------------------------------------------- run and report
def run(resume: Path, base: Path, matrix: Path, jd: Path, pdf_text: Path | None = None,
        allow_long: list[str] | None = None, today: str | None = None, tier: str | None = None) -> dict[str, Any]:
    for p in (resume, base, matrix, jd) + ((pdf_text,) if pdf_text else ()):
        if not Path(p).is_file():
            raise UsageError(f"no such file: {p}")
    page = load_page(Path(resume))
    base_page = load_page(Path(base))
    mat = yaml.safe_load(Path(matrix).read_text(encoding="utf-8")) or {}
    if not isinstance(mat, dict):
        raise UsageError(f"{matrix}: not a matrix mapping")
    body = jd_text(Path(jd))
    page_text = Path(pdf_text).read_text(encoding="utf-8") if pdf_text else page["text"] + "\n" + page["header"]
    allow = [set(i for i in re.split(r"[,\s]+", s) if i) for s in (allow_long or [])]
    page_ids = {i for x in page["lines"] for i in x.get("ids", [])}
    preds = [b3(mat, page_text, page_ids), b4(page), b5(mat), b7(page, allow), b9(page), b12(page), b13(page), b14(page),
             b18(page, body, mat.get("allowed_ngrams") or []), b19(page), b21(Path(resume))]
    preds = [_spare_base_lines(p, base_page) for p in preds]
    if tier == "mass":   # base 72 unchanged: the matrix is not filled by hand; B3 still checks the must-haves are visible
        preds = [{**p, "status": "WARN", "notes": p["notes"] + ["mass tier: unanswered rows are a warning"]}
                 if p["id"] == "B5" and p["status"] == "FAIL" else p for p in preds]
    changed, removed = diff_bullets(page, base_page)
    preds.append(b22(removed, page))
    if tier in ("focused", "dream"):     #: the page answers a reader the package has modelled
        preds += [b20(Path(matrix), tier), b23(changed, removed, mat), b24(Path(resume))]
    failed = [p["id"] for p in preds if p["status"] == "FAIL"]
    return {"resume": str(resume), "base": str(base), "matrix": str(matrix), "jd": str(jd),
            "pdf_text": str(pdf_text) if pdf_text else None, "date": today or date.today().isoformat(), "tier": tier,
            "predicates": preds,
            "changed_bullets": [{**c, "text": safe(c["text"])} for c in changed],
            "removed_bullets": [{**c, "text": safe(c["text"])} for c in removed],
            "failed": failed, "exit": 1 if failed else 0}


def b20(matrix: Path, tier: str = "focused") -> dict[str, Any]:
    """: the Role Package next to the matrix (<slug>.matrix.yaml -> <slug>.md): a section 6 that fails
    reader_model.py check FAILs; no section 6 WARNs; no package WARNs for focused and
    FAILs for dream."""
    import reader_model
    name = matrix.name
    package = matrix.with_name(name[: -len(".matrix.yaml")] + ".md") if name.endswith(".matrix.yaml") else None
    if tier == "dream" and (package is None or not package.is_file()):
        slug = name[: -len(".matrix.yaml")] if name.endswith(".matrix.yaml") else "<slug>"
        return _pred("B20", [{"detail": f"no role package: run /role-package {slug}"}], fail=True)
    if package is None or not package.is_file():         # a focused page whose package predates: warn
        where = package.name if package is not None else f"<slug>.md (the matrix {name} is not <slug>.matrix.yaml)"
        return _pred("B20", [{"detail": f"no Role Package {where} next to the matrix"}], fail=False)
    res = reader_model.check(package)
    if res["errors"][:1] and res["errors"][0].startswith("no `## 6 Reader model`"):
        return _pred("B20", [{"detail": "reader model: none (written before reader models existed; the role-analyst adds it on the next pass)"}],
                     fail=False)
    findings = [{"detail": f"reader model: {e}"} for e in res["errors"][:1]]
    notes = [f"{len(res['errors'])} error(s); run reader_model.py check {package.name}"] if res["errors"] else []
    return _pred("B20", findings, fail=True, notes=notes)


def b21(resume: Path, ledger: Path | None = None) -> dict[str, Any]:
    """A number on a governed-employer line (policy.numbers_ledger.employer_pattern) renders only from a measured,
    released row of the numbers ledger (numbers_ledger.py). A target, estimate or held row FAILs, and so does a number
    the ledger does not carry: internal numbers never render. The corpus-keeper records and releases a number before it reaches a page."""
    import numbers_ledger
    res = numbers_ledger.check(resume, ledger or numbers_ledger.LEDGER)
    blocked = [f for f in res["numbers"] if f["verdict"] == "blocked"]
    unknown = [f for f in res["numbers"] if f["verdict"] == "unknown"]
    pick = blocked or unknown
    findings = [{"line": split_claim(f["raw"])[0], "detail": f"{f['where']}: {f['number']}: {f['why']}"} for f in pick]
    notes = [f"{len(unknown)} more not in the ledger" for _ in [0] if blocked and unknown]
    return _pred("B21", findings, fail=bool(blocked or unknown), notes=notes)


def clip(text: str, width: int = 100) -> str:
    return text if len(text) <= width else text[:width - 3].rstrip() + "..."


def render_text(rep: dict[str, Any]) -> str:
    out = [f"rubric_check {rep['date']}  resume: {rep['resume']}  base: {rep['base']}",
           f"  matrix: {rep['matrix']}  jd: {rep['jd']}" + (f"  pdf-text: {rep['pdf_text']}" if rep["pdf_text"] else ""), ""]
    for p in rep["predicates"]:
        n = len(p["findings"])
        out.append(f"{p['id']:<4}{p['status']:<5} {p['name']}" + (f" ({n})" if n else ""))
        for f in p["findings"]:
            out.append(f"      - {f['detail']}")
            if f["line"]:
                out.append(f"        | {clip(f['line'])}")
        for n_ in p["notes"]:
            out.append(f"      . {n_}")
    out.append("")
    out.append(f"changed bullets vs base ({len(rep['changed_bullets'])}):")
    for c in rep["changed_bullets"]:
        out.append(f"      - {c['role']} #{c['index']}: {clip(c['text'])}")
    out.append(f"removed from base ({len(rep['removed_bullets'])}):")
    for c in rep["removed_bullets"]:
        out.append(f"      - {c['role']} #{c['index']}: {clip(c['text'])}")
    out.append("")
    out.append(f"exit {rep['exit']}" + (" (FAIL: " + ", ".join(rep["failed"]) + ")" if rep["failed"] else " (no FAIL)"))
    return safe("\n".join(out))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--resume", type=Path, required=True)
    ap.add_argument("--base", type=Path, required=True)
    ap.add_argument("--matrix", type=Path, required=True)
    ap.add_argument("--jd", type=Path, required=True)
    ap.add_argument("--pdf-text", type=Path, default=None, help="pdftotext output to use as the page text")
    ap.add_argument("--allow-long", action="append", default=[], metavar="EV-ID-SET",
                    help="comma-separated ev ids; a bullet carrying them all is exempt from the length warn")
    ap.add_argument("--date", "--today", dest="date", default=None, help="the report date (default: today)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--tier", choices=["dream", "focused", "mass"], default=None,
                    help="mass: base 72 unchanged, so unanswered matrix rows (B5) are a warning; every other check holds")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
            raise UsageError(f"--date expects YYYY-MM-DD, got {args.date!r}")
        rep = run(args.resume, args.base, args.matrix, args.jd, args.pdf_text, args.allow_long, args.date, args.tier)
    except (UsageError, yaml.YAMLError) as e:
        print(f"usage error: {safe(str(e))}", file=sys.stderr)
        return 2
    print(safe(json.dumps(rep, indent=2, ensure_ascii=False)) if args.json else render_text(rep))
    return rep["exit"]


if __name__ == "__main__":
    sys.exit(main())
