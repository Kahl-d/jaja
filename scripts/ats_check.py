#!/usr/bin/env python3
"""ATS check for a resume PDF: what an applicant tracking system will read (design section 4).

    ats_check.py check --pdf <file> [--jd <jd.md>]
                       [--vendor greenhouse|lever|ashby|workday|icims|taleo|successfactors|unknown]
                       [--contact-email you@example.com] [--taxonomy profile/skills-taxonomy.yaml]
                       [--source <resume.yaml>] [--expect-sections Education,Skills,...]
                       [--max-pages 1] [--out <dir>] [--no-pdfkit] [--date YYYY-MM-DD]

Extracts the text three ways (poppler pdftotext, pypdf, and macOS PDFKit, the engine behind
Preview's copy/paste, through JavaScript for Automation or a compiled Swift helper) and runs:
  hard checks (exit 1 on failure): text_layer, reading_order (extractors agree), contact_email
  (literal in every extractor), headings (recognized, in order, never letter-spaced), dates
  (parseable, one format), glyphs (no unmapped ligatures, private-use or cid glyphs), em_dashes,
  split_words (no word broken at a line-end hyphen, no glued words), layout (no columns or
  tables), page_count, probes (every claim of --source is found verbatim in every extractor);
  advisory: keyword coverage vs a JD (must-have / nice-to-have, with the skills taxonomy:
  covered, missing-but-evidenced, missing-and-not-evidenced = must NOT add), widows, and vendor
  profile rules as warnings. Writes ats-report.json and ats-report.md to --out (default: the
  PDF's folder). Exit codes: 0 pass (warnings allowed), 1 a hard check failed, 2 usage error.
The JD is untrusted data: it is only scanned for terms, never followed. Terms come only from the
text inside <untrusted_jd> tags (never the jd.md header, source URL or sponsorship notes), must-haves
from requirements/qualifications sections, multi-word terms kept (golden datasets, LLM-as-judge), each
in the form the JD writes it: "ML" stays "ML"; coverage matches either form.
"""
from __future__ import annotations

import argparse
import difflib
import functools
import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as cfg  # noqa: E402
import render_resume as _rr  # noqa: E402  (same folder; its wall clock and --date stamp)
from render_resume import (  # noqa: E402  (same folder)
    CANONICAL_SECTIONS, CV_SECTIONS, JS_ROOT, SECTION_ALIASES, cli_date, iter_claims, parse_evidence, plain_text)

VENDORS = ("greenhouse", "lever", "ashby", "workday", "icims", "taleo", "successfactors", "unknown")
_EMAILS = [str(e) for e in (cfg.get("identity.emails", []) or []) if "@" in str(e)]
FALLBACK_CONTACT_EMAIL = _EMAILS[0] if _EMAILS else ""   # the first address in identity.emails
FACTS = JS_ROOT / "profile" / "facts.yaml"
DEFAULT_TAXONOMY = JS_ROOT / "profile" / "skills-taxonomy.yaml"
READING_ORDER_THRESHOLD = 0.90     # good renders measure 0.976-1.0; two-column layouts 0.70-0.76
REQUIRED_HEADINGS = ("Experience", "Education")
EM_DASHES = "\u2014\u2015\u2e3a\u2e3b"
LIGATURES = "\ufb00\ufb01\ufb02\ufb03\ufb04\ufb05\ufb06"

logging.getLogger("pdfminer").setLevel(logging.ERROR)


# --------------------------------------------------------------------------- data model
@dataclass
class Check:
    name: str
    status: str                  # pass | fail | warn | skip
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Extraction:
    name: str
    text: str = ""
    available: bool = True
    backend: str = ""
    error: str | None = None


@dataclass
class Heading:
    canonical: str
    raw: str
    line: int
    is_standard: bool
    letter_spaced: bool = False


# --------------------------------------------------------------------------- extraction
_JXA = ('ObjC.import("Quartz"); function run(argv) { var d = $.PDFDocument.alloc.initWithURL('
        '$.NSURL.fileURLWithPath(argv[0])); if (!d || d.isNil()) { throw new Error("cannot open '
        'PDF"); } var out = []; for (var i = 0; i < d.pageCount; i++) { var s = d.pageAtIndex(i)'
        '.string; out.push(s && !s.isNil() ? ObjC.unwrap(s) : ""); } return out.join("\\f"); }')
_SWIFT = """import Foundation
import PDFKit
guard CommandLine.arguments.count > 1,
      let doc = PDFDocument(url: URL(fileURLWithPath: CommandLine.arguments[1])) else { exit(2) }
var pages: [String] = []
for i in 0..<doc.pageCount { pages.append(doc.page(at: i)?.string ?? "") }
print(pages.joined(separator: "\\u{0C}"), terminator: "")
"""


def extract_pdftotext(pdf: Path, layout: bool = False) -> Extraction:
    name = "pdftotext_layout" if layout else "pdftotext"
    exe = shutil.which("pdftotext")
    if not exe:
        return Extraction(name, available=False, error="pdftotext not installed (poppler)")
    args = [exe, "-enc", "UTF-8"] + (["-layout"] if layout else []) + [str(pdf), "-"]
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        return Extraction(name, available=False, error=proc.stderr.strip()[:300])
    return Extraction(name, proc.stdout, backend="poppler")


def extract_pypdf(pdf: Path) -> Extraction:
    try:
        import pypdf
        reader = pypdf.PdfReader(str(pdf))
        return Extraction("pypdf", "\n".join(p.extract_text() or "" for p in reader.pages),
                          backend=f"pypdf {pypdf.__version__}")
    except Exception as exc:          # a broken PDF must not crash the report
        return Extraction("pypdf", available=False, error=str(exc)[:300])


def _swift_helper() -> Path | None:
    swiftc = shutil.which("swiftc")
    if not swiftc:
        return None
    digest = hashlib.sha1(_SWIFT.encode()).hexdigest()[:12]
    folder = Path(tempfile.gettempdir()) / f"js-ats-pdfkit-{digest}"
    binary = folder / "pdfkit_text"
    if binary.is_file():
        return binary
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pdfkit_text.swift").write_text(_SWIFT, encoding="utf-8")
    proc = subprocess.run([swiftc, "-O", "-o", str(binary), str(folder / "pdfkit_text.swift")],
                          capture_output=True, text=True)
    return binary if proc.returncode == 0 and binary.is_file() else None


def extract_pdfkit(pdf: Path, enabled: bool = True) -> Extraction:
    """macOS PDFKit page text (what Preview copies). JXA first, then a compiled Swift helper."""
    if not enabled:
        return Extraction("pdfkit", available=False, error="disabled (--no-pdfkit)")
    if sys.platform != "darwin":
        return Extraction("pdfkit", available=False, error="PDFKit needs macOS")
    errors = []
    osascript = shutil.which("osascript")
    if osascript:
        proc = subprocess.run([osascript, "-l", "JavaScript", "-e", _JXA, str(pdf.resolve())],
                              capture_output=True, text=True)
        if proc.returncode == 0:
            return Extraction("pdfkit", proc.stdout.removesuffix("\n"), backend="PDFKit via JXA")
        errors.append(f"jxa: {proc.stderr.strip()[:200]}")
    helper = _swift_helper()
    if helper:
        proc = subprocess.run([str(helper), str(pdf.resolve())], capture_output=True, text=True)
        if proc.returncode == 0:
            return Extraction("pdfkit", proc.stdout, backend="PDFKit via Swift")
        errors.append(f"swift: exit {proc.returncode}")
    return Extraction("pdfkit", available=False, error="; ".join(errors) or "no osascript or swiftc")


# --------------------------------------------------------------------------- text helpers
def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", text).lower())


def similarity(a: list[str], b: list[str]) -> float:
    if not a and not b:
        return 1.0
    return round(difflib.SequenceMatcher(None, a, b, autojunk=False).ratio(), 4)


def _lines(text: str) -> list[str]:
    return [ln.replace("\f", "").strip() for ln in text.splitlines()]


def _context(text: str, index: int, width: int = 30) -> str:
    snippet = text[max(0, index - width): index + width].replace("\n", " ")
    return re.sub(r"\s+", " ", snippet).strip()


def _texts(extractions: dict[str, Extraction]) -> dict[str, str]:
    """name -> text for the reading-order extractors that ran."""
    return {n: e.text for n, e in extractions.items()
            if e.available and n in ("pdftotext", "pypdf", "pdfkit")}


# --------------------------------------------------------------------------- hard checks
def check_text_layer(texts: dict[str, str]) -> Check:
    counts = {n: sum(ch.isalnum() for ch in t) for n, t in texts.items()}
    if not counts or max(counts.values()) < 50:
        return Check("text_layer", "fail", "no text layer: the PDF is an image (scanned or "
                     "flattened), so an ATS reads nothing", {"alnum_chars": counts})
    return Check("text_layer", "pass", "text layer present", {"alnum_chars": counts})


def check_reading_order(texts: dict[str, str], threshold: float = READING_ORDER_THRESHOLD) -> Check:
    ref_name = "pdftotext" if "pdftotext" in texts else next(iter(texts), None)
    if ref_name is None or len(texts) < 2:
        return Check("reading_order", "skip", "fewer than two extractors ran")
    ref = tokens(texts[ref_name])
    scores = {n: similarity(ref, tokens(t)) for n, t in texts.items() if n != ref_name}
    low = {n: s for n, s in scores.items() if s < threshold}
    data = {"reference": ref_name, "similarity": scores, "threshold": threshold}
    if low:
        worst = ", ".join(f"{n} {s:.2f}" for n, s in low.items())
        return Check("reading_order", "fail", f"reading order: extractors disagree with {ref_name} "
                     f"({worst} < {threshold}); columns, tables or floating text", data)
    return Check("reading_order", "pass", "extractors agree: " +
                 ", ".join(f"{n} {s:.3f}" for n, s in scores.items()), data)


def check_contact(texts: dict[str, str], email: str) -> Check:
    want = email.strip().lower()
    missing = [n for n, t in texts.items() if want not in t.lower()]
    if missing:
        return Check("contact_email", "fail", f"contact email {email} not found verbatim in: "
                     f"{', '.join(missing)}", {"missing_in": missing})
    ref = next(iter(texts.values()), "")
    head = "\n".join([ln for ln in _lines(ref) if ln][:8]).lower()
    if want not in head:
        return Check("contact_email", "warn", f"{email} is present but not in the header lines")
    return Check("contact_email", "pass", f"{email} present in every extractor")


def collapse_letter_spacing(line: str) -> str | None:
    """'E D U C AT I O N' -> 'EDUCATION' when a line is mostly single letters; else None."""
    parts = line.split()
    if len(parts) < 4:
        return None
    if sum(len(p) <= 2 for p in parts) / len(parts) < 0.7:
        return None
    joined = "".join(parts)
    return joined if joined.isalpha() and len(joined) >= 4 else None


def _heading_index() -> tuple[dict[str, str], dict[str, str]]:
    exact = {c.lower(): c for c in CV_SECTIONS}       # the resume's eight plus the CV headings
    exact.update({a: c for a, c in SECTION_ALIASES.items()})
    squashed = {re.sub(r"[^a-z]", "", k): v for k, v in exact.items()}
    return exact, squashed


def find_headings(text: str) -> list[Heading]:
    exact, squashed = _heading_index()
    canon_lower = {c.lower() for c in CV_SECTIONS}
    found: list[Heading] = []
    for i, line in enumerate(_lines(text)):
        if not line or len(line) > 60:
            continue
        collapsed = collapse_letter_spacing(line)
        if collapsed:
            key = collapsed.lower()
            if key in squashed:
                std = key in {re.sub(r"[^a-z]", "", c) for c in canon_lower}
                found.append(Heading(squashed[key], line, i, std, letter_spaced=True))
            continue
        key = re.sub(r"\s+", " ", line.rstrip(":").strip()).lower()
        if key in exact:
            found.append(Heading(exact[key], line, i, key in canon_lower))
    return found


def check_headings(texts: dict[str, str], expected: list[str] | None = None) -> Check:
    problems: list[str] = []
    orders: dict[str, list[str]] = {}
    spaced: list[str] = []
    nonstandard: set[str] = set()
    for name, text in texts.items():
        heads = find_headings(text)
        orders[name] = [h.canonical for h in heads]
        spaced += [f"{name}: '{h.raw}'" for h in heads if h.letter_spaced]
        nonstandard |= {h.raw for h in heads if not h.is_standard and not h.letter_spaced}
        missing = [r for r in REQUIRED_HEADINGS if r not in orders[name]]
        if missing:
            problems.append(f"missing required headings in {name}: {', '.join(missing)}")
    if spaced:
        problems.append("letter-spaced headings (an ATS reads separate letters and cannot find "
                        "the section): " + "; ".join(spaced))
    distinct = {tuple(o) for o in orders.values()}
    if len(distinct) > 1:
        problems.append("heading order differs between extractors: "
                        + "; ".join(f"{n} {o}" for n, o in orders.items()))
    ref = orders.get("pdftotext") or next(iter(orders.values()), [])
    if expected is not None and [h for h in ref if h in expected] != list(expected):
        problems.append(f"heading order {ref} does not match the resume's sections {expected}")
    data = {"order": ref, "by_extractor": orders, "nonstandard": sorted(nonstandard),
            "letter_spaced": spaced}
    if problems:
        return Check("headings", "fail", "; ".join(problems), data)
    dupes = [h for h, n in Counter(ref).items() if n > 1]
    if dupes:
        return Check("headings", "warn", f"repeated headings: {', '.join(dupes)}", data)
    return Check("headings", "pass", "headings in order: " + ", ".join(ref), data)


MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
          "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_MONTH = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
          r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?")
_YEAR = r"(?:19|20)\d{2}"
_MDATE = rf"(?:{_MONTH}\s+{_YEAR}|(?:0?[1-9]|1[0-2])/{_YEAR}|{_YEAR}-(?:0[1-9]|1[0-2])\b)"
_DATE = rf"(?:{_MDATE}|{_YEAR})"
DATE_RE = re.compile(rf"(?<![\w/]){_MDATE}(?![\w/])")          # month precision only
RANGE_RE = re.compile(rf"(?<![\w/])(?P<start>{_DATE})\s*(?P<sep>\u2013|\u2014|-|to)\s*"
                      rf"(?P<end>{_DATE}|Present|Current|Now|present|current)(?![\w/])")
SEASON_RE = re.compile(rf"\b(?:Spring|Summer|Fall|Autumn|Winter|Q[1-4])\s+{_YEAR}\b")
ABBR3 = {"Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"}
FULL = {"January", "February", "March", "April", "May", "June", "July", "August", "September",
        "October", "November", "December"}


def _date_format(token: str) -> str | None:
    """Format family of one date mention; None when ambiguous ('May 2024')."""
    token = token.strip()
    if re.fullmatch(rf"(?:0?[1-9]|1[0-2])/{_YEAR}", token):
        return "MM/YYYY"
    if re.fullmatch(rf"{_YEAR}-\d{{2}}", token):
        return "YYYY-MM"
    if re.fullmatch(_YEAR, token):
        return "YYYY"
    word = token.split()[0]
    if word == "May":
        return None
    if word.endswith("."):
        return "Mon. YYYY"
    if word in ABBR3:
        return "Mon YYYY"
    if word in FULL:
        return "Month YYYY"
    return f"irregular ({word})"


def _date_value(token: str) -> tuple[int, int]:
    token = token.strip()
    if token.lower() in ("present", "current", "now"):
        return (9999, 12)
    m = re.fullmatch(rf"(0?[1-9]|1[0-2])/({_YEAR})", token)
    if m:
        return (int(m.group(2)), int(m.group(1)))
    m = re.fullmatch(rf"({_YEAR})-(\d{{2}})", token)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    if re.fullmatch(_YEAR, token):
        return (int(token), 0)
    word, year = token.split()
    return (int(year), MONTHS[word.rstrip(".")[:3].lower()])


def check_dates(text: str) -> Check:
    flat = re.sub(r"[ \t]+", " ", text)
    problems: list[str] = []
    seasons = SEASON_RE.findall(flat)
    if seasons:
        problems.append(f"unparseable dates: {', '.join(sorted(set(seasons)))} (use Mon YYYY)")
    ranges = list(RANGE_RE.finditer(flat))
    mentions = [m.group(0) for m in DATE_RE.finditer(flat)]
    # a bare year only counts as a date format when it ends a range ("2019 - 2021")
    mentions += [m.group(k) for m in ranges for k in ("start", "end")
                 if re.fullmatch(_YEAR, m.group(k))]
    if not mentions:
        return Check("dates", "fail", "no dates found: every role needs Mon YYYY dates")
    formats = Counter(f for f in (_date_format(t) for t in mentions) if f)
    seps = Counter(m.group("sep") for m in ranges)
    for m in ranges:                      # a year-only range is fine; a range mixing precisions is not
        ends = [m.group("start"), m.group("end")]
        years = [bool(re.fullmatch(_YEAR, e)) for e in ends]
        if years[0] != years[1] and not re.fullmatch(r"(?i)present|current|now", ends[1]):
            problems.append(f"range mixes year and month precision: '{m.group(0)}' (use YYYY - YYYY "
                            "or Mon YYYY - Mon YYYY)")
        elif years[0] and not years[1]:
            problems.append(f"year-only start with an open end: '{m.group(0)}' (use Mon YYYY - Present)")
    if set(formats) - {"YYYY"}:           # next to month dates, year-only ranges are not a rival format
        formats.pop("YYYY", None)
    if len(formats) > 1:
        examples: dict[str, str] = {}
        for t in mentions:
            f = _date_format(t)
            if f and f not in examples:
                examples[f] = t
        problems.append("mixed date formats: " + ", ".join(f"'{t}' ({f})" for f, t in examples.items()))
    if len(seps) > 1:
        problems.append("mixed range separators: " + ", ".join(repr(s) for s in seps))
    for m in ranges:
        if _date_value(m.group("start")) > _date_value(m.group("end")):
            problems.append(f"range ends before it starts: '{m.group(0)}'")
    fmt = formats.most_common(1)[0][0] if formats else "Mon YYYY"
    sep = seps.most_common(1)[0][0] if seps else None
    data = {"format": fmt, "range_separator": sep, "mentions": len(mentions), "ranges": len(ranges)}
    if problems:
        return Check("dates", "fail", "; ".join(problems), data)
    if fmt != "Mon YYYY":
        return Check("dates", "warn", f"dates are consistent but '{fmt}', not 'Mon YYYY'", data)
    return Check("dates", "pass", f"{len(mentions)} dates, all '{fmt}'"
                 + (f", ranges with '{sep}'" if sep else ""), data)


def check_glyphs(texts: dict[str, str]) -> Check:
    found: list[str] = []
    for name, text in texts.items():
        bad = []
        for ch in sorted(set(text)):
            cp = ord(ch)
            if ch in LIGATURES:
                bad.append(f"unmapped ligature U+{cp:04X}")
            elif 0xE000 <= cp <= 0xF8FF or cp >= 0xF0000:
                bad.append(f"private-use glyph U+{cp:04X} (icon font?)")
            elif cp == 0xFFFD:
                bad.append("replacement character U+FFFD")
            elif unicodedata.category(ch) == "Cc" and ch not in "\n\r\t\f":
                bad.append(f"control character U+{cp:04X}")
        if re.search(r"\(cid:\d+\)", text):
            bad.append("(cid:N) glyphs with no Unicode mapping")
        if bad:
            found.append(f"{name}: {', '.join(bad)}")
    if found:
        return Check("glyphs", "fail", "glyph garbage: " + "; ".join(found))
    return Check("glyphs", "pass", "every glyph maps to real text")


def check_em_dashes(texts: dict[str, str]) -> Check:
    """Fails on any em-dash when the owner's style forbids them (style.no_em_dash); passes unchecked when it does not."""
    if not cfg.get("style.no_em_dash", False):
        return Check("em_dashes", "pass", "em-dashes not restricted (style.no_em_dash is off)")
    for name, text in texts.items():
        hits = [m.start() for m in re.finditer(f"[{EM_DASHES}]", text)]
        if hits:
            ctx = "; ".join(f"'{_context(text, i)}'" for i in hits[:3])
            return Check("em_dashes", "fail", f"{len(hits)} em-dash(es) in {name}: {ctx}",
                         {"count": len(hits)})
    return Check("em_dashes", "pass", "no em-dashes")


def check_split_words(texts: dict[str, str]) -> Check:
    problems: list[str] = []
    for name, text in texts.items():
        lines = [ln for ln in _lines(text) if ln]
        for a, b in zip(lines, lines[1:]):
            if re.search(r"[A-Za-z0-9](-|\u00ad)$", a) and re.match(r"[a-z]", b):
                problems.append(f"{name}: '{a.split()[-1]}' | '{b.split()[0]}' (word broken at "
                                "a line-end hyphen)")
    ref_name = "pdftotext" if "pdftotext" in texts else None
    if ref_name:
        ref = tokens(texts[ref_name])
        ref_set = set(ref)
        glued = {a + b: f"{a} {b}" for a, b in zip(ref, ref[1:])}
        for name, text in texts.items():
            if name == ref_name:
                continue
            hits = sorted({t for t in tokens(text) if t not in ref_set and t in glued})
            if hits:
                problems.append(f"{name}: glued words (missing interword spaces): "
                                + ", ".join(f"'{h}' for '{glued[h]}'" for h in hits[:5]))
    if problems:
        return Check("split_words", "fail", "; ".join(problems[:8]), {"count": len(problems)})
    return Check("split_words", "pass", "no words split across lines or glued together")


_LOC = re.compile(r"^(?:[A-Z][A-Za-z.'\- ]+,\s*(?:[A-Z]{2}|[A-Z][a-z]+(?: [A-Z][a-z]+)*)|Remote|"
                  r"Hybrid|On-?site)$")
_TABLE_ROW = re.compile(r"^\s*(?:[\u2022\-*\u00b7]\s*)?\|.*\|.*\|\s*$")


def _benign_segment(seg: str) -> bool:
    seg = seg.strip()
    if re.fullmatch(r"[|\u2022\u00b7\u22c4\u25e6-]", seg) or re.fullmatch(r"\d{1,2}", seg):
        return True
    if _LOC.match(seg):
        return True
    rest = DATE_RE.sub("", RANGE_RE.sub("", seg))
    rest = re.sub(rf"\b{_YEAR}\b", "", rest)
    return not re.sub(r"[\s,|\-\u2013()]", "", rest)


def detect_columns(layout_text: str) -> list[str]:
    """Lines of `pdftotext -layout` text that show side-by-side blocks (columns or tables).
    A right-aligned date or location next to an entry is allowed."""
    suspicious: list[str] = []
    for line in layout_text.splitlines():
        body = line.replace("\f", "").rstrip()
        if not body.strip():
            continue
        if _TABLE_ROW.match(body):
            suspicious.append(body.strip())
            continue
        segments = [s for s in re.split(r"\s{3,}", body.strip()) if s]
        if len(segments) >= 2 and not all(_benign_segment(s) for s in segments[1:]):
            suspicious.append(body.strip())
    return suspicious


def check_layout(layout_text: str | None) -> Check:
    if layout_text is None:
        return Check("layout", "skip", "pdftotext -layout unavailable")
    suspicious = detect_columns(layout_text)
    table_rows = [s for s in suspicious if _TABLE_ROW.match(s)]
    if table_rows or len(suspicious) >= 2:
        kind = "table rows" if table_rows else "multi-column or table layout"
        examples = "; ".join(f"'{s[:70]}'" for s in suspicious[:3])
        return Check("layout", "fail", f"{kind}: {len(suspicious)} line(s) with side-by-side text "
                     f"blocks (columns or a table): {examples}", {"lines": suspicious[:20]})
    if suspicious:
        return Check("layout", "warn", f"one line with side-by-side blocks: '{suspicious[0][:70]}'",
                     {"lines": suspicious})
    return Check("layout", "pass", "single column, no tables")


def check_pages(pages: int, max_pages: int) -> Check:
    if pages > max_pages:
        return Check("page_count", "fail", f"{pages} pages > max {max_pages} pages",
                     {"pages": pages, "max": max_pages})
    return Check("page_count", "pass", f"{pages} page(s)", {"pages": pages, "max": max_pages})


def _probe_norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'}))
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([.,;:!?)\]])", r"\1", text)
    return re.sub(r"([(\[])\s+", r"\1", text).strip().lower()


def load_probes(source: Path) -> list[str]:
    data = yaml.safe_load(Path(source).read_text(encoding="utf-8"))
    cv = data.get("cv", {}) if isinstance(data, dict) else {}
    return [plain_text(parse_evidence(c.text)[0]) for c in iter_claims(cv)]


def check_probes(texts: dict[str, str], probes: list[str] | None) -> Check:
    if not probes:
        return Check("probes", "skip", "no --source resume given (and no meta.yaml source)")
    missing: dict[str, list[str]] = {}
    for name, text in texts.items():
        norm = _probe_norm(text)
        gone = [p for p in probes if _probe_norm(p) not in norm]
        if gone:
            missing[name] = gone
    data = {"probes": len(probes), "missing": missing}
    if missing:
        worst = "; ".join(f"{n}: {len(probes) - len(g)}/{len(probes)} found, e.g. '{g[0][:60]}'"
                          for n, g in missing.items())
        return Check("probes", "fail", f"claims not found verbatim: {worst}", data)
    return Check("probes", "pass", f"{len(probes)}/{len(probes)} claims found verbatim in every "
                 "extractor", data)


def check_widows(layout_text: str | None) -> Check:
    if not layout_text:
        return Check("widows", "skip", "")
    lines = [ln.rstrip() for ln in layout_text.splitlines()]
    widows = []
    for prev, cur in zip(lines, lines[1:]):
        words = cur.split()
        if (len(words) == 1 and len(prev.strip()) > 60 and cur.startswith(" ")
                and not DATE_RE.fullmatch(words[0])):
            widows.append(words[0])
        if re.search(r"\b\w+/$", prev) and cur.strip():               # "A/" then "B": a token split by the wrap
            widows.append(prev.split()[-1] + " " + words[0] if words else prev.split()[-1])
    if widows:
        return Check("widows", "warn", "one-word last lines or split tokens: " + ", ".join(widows[:6]),
                     {"words": widows})
    return Check("widows", "pass", "no one-word last lines")


# --------------------------------------------------------------------------- fonts
SERIF_HINTS = ("times", "termes", "newtx", "charter", "georgia", "garamond", "cambria", "palatino",
               "pagella", "bookman", "baskerville", "minion", "libertinus", "computermodern",
               "cmr", "lmroman", "latinmodern", "nimbusrom", "gentium", "caslon", "didot",
               "bodoni", "century", "schoolbook", "crimson", "merriweather", "lora",
               "sourceserif", "notoserif", "fontin", "serif", "roman")
SANS_HINTS = ("sans", "arial", "helvetica", "calibri", "verdana", "tahoma", "segoe", "lato",
              "roboto", "inter", "fira", "dejavusans", "ubuntu", "mukta", "poppins", "raleway",
              "avenir", "futura", "gill", "franklin", "montserrat", "nunito", "karla", "carlito",
              "liberationsans", "opensauce")


def font_is_serif(name: str) -> bool | None:
    key = re.sub(r"[^a-z]", "", name.split("+", 1)[-1].lower())
    if any(h in key for h in SANS_HINTS):
        return False
    if any(h in key for h in SERIF_HINTS):
        return True
    return None


def pdf_fonts(pdf: Path) -> dict[str, Any]:
    """Body font = the (font, size) carrying the most characters (pdfminer)."""
    try:
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LTChar, LTTextContainer
    except ImportError:
        return {"body": None, "all": []}
    census: Counter = Counter()
    try:
        for page in extract_pages(str(pdf)):
            for element in page:
                if not isinstance(element, LTTextContainer):
                    continue
                for line in element:
                    for ch in getattr(line, "_objs", []):
                        if isinstance(ch, LTChar) and ch.get_text().strip():
                            font = ch.fontname.split("+", 1)[-1]
                            if not font.endswith("-Space"):
                                census[(font, round(ch.size, 1))] += 1
    except Exception:
        return {"body": None, "all": []}
    if not census:
        return {"body": None, "all": []}
    (name, size), _ = census.most_common(1)[0]
    return {"body": {"name": name, "size": size, "serif": font_is_serif(name)},
            "all": [{"name": n, "size": s, "chars": c} for (n, s), c in census.most_common(6)]}


# --------------------------------------------------------------------------- keywords
# (name, aliases, case_sensitive): terms a JD may ask for, whether or not the owner has them.
LEXICON: list[tuple[str, tuple[str, ...], bool]] = [
    ("Python", (), False), ("Java", (), False), ("Golang", (), False), ("Rust", (), True),
    ("C++", ("cpp",), False), ("C#", (), False), ("TypeScript", ("TS",), False),
    ("JavaScript", ("JS",), False), ("SQL", (), False), ("Scala", (), False), ("Kotlin", (), False),
    ("Bash", ("shell scripting",), False), ("PyTorch", (), False), ("TensorFlow", (), False),
    ("JAX", (), False), ("Keras", (), False), ("scikit-learn", ("sklearn",), False),
    ("XGBoost", (), False), ("LightGBM", (), False), ("Hugging Face", ("HuggingFace", "Transformers library"), False),
    ("LangChain", (), False), ("LangGraph", (), False), ("LlamaIndex", (), False), ("DSPy", (), False),
    ("vLLM", (), False), ("TensorRT", (), False), ("Triton", (), True), ("ONNX", (), False),
    ("CUDA", (), False), ("DeepSpeed", (), False), ("Ray", (), True), ("Spark", ("PySpark",), True),
    ("Dask", (), False), ("Airflow", (), False), ("Dagster", (), False), ("Kubeflow", (), False),
    ("MLflow", (), False), ("Weights & Biases", ("wandb", "W&B"), False), ("Kafka", (), False),
    ("Flink", (), False), ("Docker", (), False), ("Kubernetes", ("k8s",), False),
    ("Terraform", (), False), ("AWS", ("Amazon Web Services",), False),
    ("GCP", ("Google Cloud", "Google Cloud Platform"), False), ("Azure", (), False),
    ("SageMaker", (), False), ("Bedrock", (), True), ("Vertex AI", (), False),
    ("PostgreSQL", ("Postgres",), False), ("MySQL", (), False), ("MongoDB", ("Mongo",), False),
    ("Redis", (), False), ("Elasticsearch", (), False), ("pgvector", (), False),
    ("Pinecone", (), False), ("Weaviate", (), False), ("FAISS", (), False), ("Milvus", (), False),
    ("Qdrant", (), False), ("Snowflake", (), True), ("BigQuery", (), False), ("Databricks", (), False),
    ("dbt", (), True), ("FastAPI", (), False), ("Flask", (), True), ("Django", (), False),
    ("React", (), True), ("Node.js", ("NodeJS",), False), ("GraphQL", (), False), ("gRPC", (), False),
    ("LLM", ("large language model", "large language models"), False),
    ("retrieval-augmented generation", ("RAG",), False), ("fine-tuning", ("finetuning",), False),
    ("RLHF", (), False), ("LoRA", ("QLoRA",), False), ("prompt engineering", (), False),
    ("embeddings", (), False), ("vector database", ("vector databases", "vector search"), False),
    ("semantic search", (), False), ("information retrieval", (), False),
    ("NLP", ("natural language processing",), False), ("computer vision", (), False),
    ("reinforcement learning", (), False), ("deep learning", (), False),
    ("machine learning", ("ML",), False), ("MLOps", (), False), ("distributed systems", (), False),
    ("distributed training", (), False), ("model serving", (), False),
    ("inference optimization", (), False), ("quantization", (), False),
    ("LLM evaluation", ("LLM evals",), False), ("AI agents", ("agentic",), False),
    ("golden datasets", ("golden dataset", "golden sets", "golden set"), False),
    ("LLM-as-judge", ("LLM-as-a-judge", "LLM as a judge", "LLM as judge", "LLM judges", "LLM judge"), False),
    ("regression testing", ("regression tests", "regression test"), False),
    ("evaluation harness", ("eval harness", "evals harness"), False),
    ("multi-agent", ("multi-agent systems",), False), ("tool calling", ("function calling",), False),
    ("MCP", ("Model Context Protocol",), False), ("A/B testing", (), False),
    ("recommender systems", ("recommendation systems",), False), ("time series", (), False),
    ("speech recognition", ("ASR",), False), ("text-to-speech", ("TTS",), False),
    ("WebRTC", (), False), ("observability", (), False), ("CI/CD", (), False), ("Git", (), True),
    ("Linux", (), False), ("OpenTelemetry", (), False), ("LangSmith", (), False),
    ("Langfuse", (), False), ("GPU", ("GPUs",), False),
]
COMMON_WORDS = {"ray", "rust", "go", "spark", "swift", "julia", "dash", "flask", "react",
                "triton", "bedrock", "snowflake", "git", "dbt", "express", "spring", "echo"}
ABBREVIATIONS = {
    "RAG": "retrieval-augmented generation", "LLM": "large language model",
    "NLP": "natural language processing", "ML": "machine learning", "AI": "artificial intelligence",
    "RL": "reinforcement learning", "RLHF": "reinforcement learning from human feedback",
    "CV": "computer vision", "ASR": "automatic speech recognition", "TTS": "text-to-speech",
    "STT": "speech-to-text", "VAD": "voice activity detection", "MCP": "Model Context Protocol",
    "SDK": "software development kit", "CI": "continuous integration",
    "CD": "continuous delivery", "ETL": "extract, transform, load",
    "AWS": "Amazon Web Services", "GCP": "Google Cloud Platform", "GPU": "graphics processing unit",
    "LoRA": "low-rank adaptation", "PEFT": "parameter-efficient fine-tuning",
    "DPO": "direct preference optimization", "KV": "key-value", "OCR": "optical character recognition",
    "HL7": "Health Level Seven", "FHIR": "Fast Healthcare Interoperability Resources",
    "BI": "business intelligence", "SLA": "service-level agreement",
}
SAFE_ACRONYMS = {"GPA", "USA", "US", "UK", "EU", "CEO", "CTO", "CFO", "COO", "VP", "MBA", "BS",
                 "BA", "MS", "MA", "II", "III", "IV", "ID", "OK", "TV", "PDF", "URL", "HTML",
                 "CSS", "JSON", "YAML", "XML", "HTTP", "HTTPS", "SQL", "API", "APIs", "IT", "HR",
                 "AM", "PM", "UTC", "IDE", "OS", "UI", "UX", "CPU", "RAM", "USB", "PR", "PRs"}
US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE "
                "NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split())
NONSTANDARD_SKILLS = {"k8s": "Kubernetes", "postgres": "PostgreSQL", "js": "JavaScript",
                      "ts": "TypeScript", "mongo": "MongoDB", "tf": "TensorFlow",
                      "sklearn": "scikit-learn", "golang": "Go", "gcp": "Google Cloud Platform (GCP)",
                      "nodejs": "Node.js", "react.js": "React", "pytorch lightning": "PyTorch Lightning"}
_STOP = {"a", "an", "the", "and", "or", "of", "in", "with", "for", "to", "on", "at", "our", "your",
         "we", "you", "strong", "solid", "deep", "modern", "hands-on", "production", "proven",
         "working", "good", "excellent", "any", "similar", "other", "related", "relevant", "etc"}
_GENERIC = {"tools", "systems", "technologies", "frameworks", "platforms", "environment",
            "environments", "teams", "code", "software", "experience", "skills", "knowledge",
            "stack", "products", "services", "data", "problems"}


@dataclass
class Term:
    key: str
    name: str
    aliases: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    source: str = "lexicon"
    case_sensitive: bool = False

    def surfaces(self) -> list[str]:
        return [self.name] + self.aliases

    def regexes(self) -> list[re.Pattern[str]]:
        return [_surface_regex(s, self.case_sensitive) for s in self.surfaces()]

    def find(self, text: str) -> str | None:
        for rx in self.regexes():
            m = rx.search(text)
            if m:
                return m.group(0)
        return None


@functools.lru_cache(maxsize=4096)
def _surface_regex(surface: str, case_sensitive: bool) -> re.Pattern[str]:
    parts = [re.escape(p) for p in re.split(r"[\s\-]+", surface.strip()) if p]
    body = r"[\s\-]*".join(parts)
    tail = r"(?:s|es)?" if surface[-1:].isalpha() else ""
    return re.compile(rf"(?<![A-Za-z0-9+#]){body}{tail}(?![A-Za-z0-9+#])",
                      0 if case_sensitive else re.I)


class Taxonomy:
    """Skills from profile/skills-taxonomy.yaml (with evidence) merged over the built-in lexicon."""

    def __init__(self, terms: Iterable[Term]):
        self.by_key: dict[str, Term] = {}
        self._alias: dict[str, str] = {}
        for term in terms:
            self.add(term)

    def add(self, term: Term) -> Term:
        existing = self.lookup(term.name) or next(
            (self.lookup(a) for a in term.aliases if self.lookup(a)), None)
        if existing:
            if term.source == "taxonomy" and existing.source != "taxonomy":
                existing.source = "taxonomy"
            existing.evidence = sorted(set(existing.evidence) | set(term.evidence))
            existing.aliases += [a for a in term.aliases if a.lower() not in
                                 {s.lower() for s in existing.surfaces()}]
            for s in existing.surfaces():
                self._alias[s.lower()] = existing.key
            return existing
        self.by_key[term.key] = term
        for s in term.surfaces():
            self._alias.setdefault(s.lower(), term.key)
        return term

    def lookup(self, text: str) -> Term | None:
        key = self._alias.get(str(text).strip().lower())
        return self.by_key.get(key) if key else None

    def get(self, key: str) -> Term | None:
        return self.by_key.get(key) or self.lookup(key)

    def terms(self) -> list[Term]:
        return list(self.by_key.values())


def _lexicon_terms() -> list[Term]:
    return [Term(n.lower(), n, list(a), [], "lexicon", cs) for n, a, cs in LEXICON]


def _taxonomy_terms(node: Any) -> Iterable[Term]:
    meta = {"schema_version", "updated", "created", "built_by", "status_legend", "notes",
            "legend", "version", "title", "description"}
    skill_keys = {"evidence", "evidence_ids", "aliases", "synonyms", "level", "years", "lenses"}

    def make(name: str, spec: dict[str, Any]) -> Term:
        aliases = spec.get("aliases") or spec.get("synonyms") or []
        ev = spec.get("evidence") or spec.get("evidence_ids") or []
        return Term(name.lower(), name, [str(a) for a in (aliases if isinstance(aliases, list) else [aliases])],
                    [str(e) for e in (ev if isinstance(ev, list) else [ev])], "taxonomy",
                    name.lower() in COMMON_WORDS)

    if isinstance(node, list):
        for item in node:
            if isinstance(item, str):
                yield Term(item.lower(), item, [], [], "taxonomy", item.lower() in COMMON_WORDS)
            else:
                yield from _taxonomy_terms(item)
    elif isinstance(node, dict):
        name = node.get("name") or node.get("skill") or node.get("term")
        if isinstance(name, str):
            yield make(name, node)
            return
        for key, value in node.items():
            if key in meta:
                continue
            if isinstance(value, dict) and (not value or (set(value) & skill_keys and "name" not in value)):
                yield make(str(key), value)
            elif isinstance(value, (list, dict)):
                yield from _taxonomy_terms(value)


def load_taxonomy(path: Path | None) -> Taxonomy:
    tax = Taxonomy([])
    if path and Path(path).is_file():
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        for term in _taxonomy_terms(data):
            tax.add(term)
    for term in _lexicon_terms():
        tax.add(term)
    return tax


_HEADING_LINE = re.compile(r"^\s*(?:#{1,6}\s+(?P<md>.+?)\s*#*|\*\*(?P<bold>[^*]+)\*\*:?|(?P<colon>[A-Z][^.!?]{2,50}):)\s*$")
_MUST = re.compile(r"requirement|qualification|must|what you('|\u2019)?ll need|what you need|"
                   r"looking for|you have|you bring|who you are|about you|minimum|basic|required",
                   re.I)
_NICE = re.compile(r"nice|prefer|bonus|plus|desired|ideal|extra credit|good to have|great if|"
                   r"not required|optional", re.I)
_CUE = re.compile(r"\b(?:experience|expertise|proficiency|fluency|familiarity|knowledge|"
                  r"background|skills?|understanding)\s+(?:with|in|of|using|building|designing|"
                  r"developing|working with)\s+([^.;:\n()]+)", re.I)
_CUT = re.compile(r"\b(?:for|to|that|which|who|where|when|including|such as|at|on|across|"
                  r"within|like|from|by)\b", re.I)
_TECH = re.compile(r"(?<![\w.+#-])(?:[A-Z][a-z]+[A-Z][A-Za-z]*|[a-z]+[A-Z][A-Za-z]+|"
                   r"[A-Z]{2,6}s?|[A-Za-z]+\+\+|[A-Za-z]+#|[A-Za-z]+\.js|[A-Za-z]+\d+[A-Za-z]*)"
                   r"(?![\w+#])")
_JD_STOP_ACRONYMS = {"BS", "MS", "BA", "MA", "PHD", "PhD", "CS", "US", "USA", "EEO", "OK", "AND",
                     "OR", "WE", "YOU", "TBD", "HR", "PTO", "IT", "DEI", "EOE", "USD", "OTE",
                     "NYC", "SF", "LA", "CA", "WA", "NY", "TX",
                     "STEM", "OPT", "CPT", "EAD", "H1B", "H1", "GC", "USC", "W2", "C2C", "LCA", "DSO", "AI"}
_VERSION = re.compile(r"^v\d+(?:\.\d+)*$", re.I)
_HEAD_NOUN = re.compile(r"(?<![\w-])([A-Za-z][A-Za-z0-9+#.]{2,})\s+(testing|tests|datasets?|evaluations?|evals|"
                        r"harness(?:es)?|pipelines?|monitoring|tracing|orchestration|labeling|annotation)\b", re.I)
_COMPOUND = re.compile(r"(?<![\w-])[A-Za-z0-9]+(?:-[A-Za-z0-9]+){2,}(?![\w-])")
_NOT_TERMS = {"end-to-end", "state-of-the-art", "day-to-day", "up-to-date", "one-on-one", "face-to-face",
              "best-in-class", "first-of-its-kind", "out-of-the-box", "back-and-forth"}
_VERBS = {"own", "owns", "build", "builds", "built", "building", "design", "designs", "run", "runs", "lead",
          "leads", "drive", "drives", "improve", "improves", "write", "writes", "ship", "ships", "create",
          "creates", "develop", "develops", "maintain", "maintains", "scale", "scales", "define", "defines",
          "set", "use", "uses", "using", "new", "our", "their", "all", "every", "automated", "automate"}


def jd_scope(jd_text: str) -> str:
    """The part of a jd.md that is the posting: the text inside <untrusted_jd> tags when present (the header,
    source URL and our own sponsorship notes sit outside them), else the file minus its frontmatter."""
    text = re.sub(r"\A---\n.*?\n---\n", "", jd_text, flags=re.S)
    inside = re.findall(r"<untrusted_jd>(.*?)</untrusted_jd(?:_)?>", text, flags=re.S)
    return "\n".join(inside) if inside else text


def split_jd_regions(jd_text: str) -> dict[str, str]:
    text = re.sub(r"</?untrusted_[a-z_]*>", "", jd_scope(jd_text))
    regions = {"must": [], "nice": [], "other": []}
    current = "other"
    for line in text.splitlines():
        m = _HEADING_LINE.match(line)
        if m:
            head = m.group("md") or m.group("bold") or m.group("colon") or ""
            current = "nice" if _NICE.search(head) else "must" if _MUST.search(head) else "other"
            if current == "other":
                regions["other"].append(head)
            continue
        regions[current].append(line)
    return {k: "\n".join(v) for k, v in regions.items()}


def _phrases(text: str) -> list[str]:
    out = []
    for m in _CUE.finditer(text):
        chunk = _CUT.split(m.group(1))[0]
        for part in re.split(r",|/|\band\b|\bor\b", chunk):
            words = [w for w in re.findall(r"[A-Za-z0-9+#.\-]+", part) if w.lower() not in _STOP]
            if 1 <= len(words) <= 4 and not (len(words) == 1 and words[0].lower() in _GENERIC):
                out.append(" ".join(words).strip(".-"))
    return [p for p in out if p]


def _jd_surface(term: Term, text: str) -> str:
    """The form the JD itself uses (ML stays ML, never "machine learning"); the name when several appear."""
    return next((s for s in term.surfaces() if _surface_regex(s, term.case_sensitive).search(text)), term.name)


def _multiword(text: str) -> list[str]:
    """Multi-word skills the lexicon may not know: "<modifier> testing|datasets|evaluation|harness|..."
    and hyphenated compounds such as LLM-as-judge or human-in-the-loop."""
    out = [m.group(0) for m in _COMPOUND.finditer(text) if m.group(0).lower() not in _NOT_TERMS]
    for m in _HEAD_NOUN.finditer(text):
        mod = m.group(1)
        if mod.lower() in _STOP or mod.lower() in _VERBS or mod.lower() in _GENERIC:
            continue
        out.append(f"{mod} {m.group(2)}")
    return out


def _terms_in(text: str, tax: Taxonomy) -> list[str]:
    """JD terms as the JD writes them (lowercased); one entry per underlying skill."""
    found: dict[str, str] = {}                      # term key -> the JD's surface form

    def note(term: Term, surface: str) -> None:
        found.setdefault(term.key, surface.lower())

    for term in tax.terms():
        if term.find(text):
            note(term, _jd_surface(term, text))
    candidates = [(p, False) for p in _phrases(text) + _multiword(text)]
    candidates += [(t, True) for t in _TECH.findall(text)]
    for cand, is_token in candidates:
        if is_token and (cand in _JD_STOP_ACRONYMS or cand.upper() in _JD_STOP_ACRONYMS or _VERSION.match(cand)):
            continue
        known = tax.lookup(cand)
        if known:
            note(known, cand)
        elif not any(t.find(cand) for t in tax.terms()):
            note(tax.add(Term(cand.lower(), cand, [], [], "phrase", is_token)), cand)
    return list(found.values())


def extract_jd_terms(jd_text: str, taxonomy: Taxonomy) -> dict[str, list[str]]:
    """Must-have and nice-to-have terms from a JD (untrusted: scanned, never followed).
    Terms outside must/nice sections count as nice-to-have; with no sections at all, as must."""
    regions = split_jd_regions(jd_text)
    must = _terms_in(regions["must"], taxonomy)
    nice = _terms_in(regions["nice"], taxonomy) + _terms_in(regions["other"], taxonomy)
    if not must and not regions["nice"].strip():
        must, nice = nice, []

    def ident(k: str) -> str:
        term = taxonomy.get(k)
        return term.key if term else k

    taken = {ident(k) for k in must}
    out_nice: list[str] = []
    for k in nice:
        if ident(k) not in taken:
            taken.add(ident(k))
            out_nice.append(k)
    return {"must": must, "nice": out_nice}


def _held_terms() -> set[str]:
    """The held strings (profile/PROFILE-HOLDS.md): a held term is never offered as "you could add"."""
    try:
        import profile_titles as pt  # noqa: PLC0415  (same folder)
        return {h.text.lower() for h in pt.load_holds()}
    except Exception:  # noqa: BLE001  (no holds list: nothing is filtered, and check_holds still runs on the page)
        return set()


def keyword_coverage(must: list[str], nice: list[str], resume_text: str,
                     taxonomy: Taxonomy, held: set[str] | None = None) -> dict[str, Any]:
    covered, evidenced, unevidenced = [], [], []
    held = _held_terms() if held is None else held
    tiers = [("must", k) for k in must] + [("nice", k) for k in nice if k not in must]
    counts = {"must": [0, 0], "nice": [0, 0]}
    for tier, key in tiers:
        term = taxonomy.get(key) or Term(key, key, [], [], "phrase")
        counts[tier][0] += 1
        hit = term.find(resume_text)
        if hit:
            counts[tier][1] += 1
            covered.append({"term": key, "tier": tier, "matched": hit})
        elif {key.lower(), str(getattr(term, "name", key)).lower()} & held:
            unevidenced.append({"term": key, "tier": tier, "held": True})
        elif term.evidence:
            evidenced.append({"term": key, "tier": tier, "evidence": term.evidence})
        else:
            unevidenced.append({"term": key, "tier": tier})

    def stat(tier: str) -> dict[str, Any]:
        total, hit = counts[tier]
        return {"total": total, "covered": hit, "coverage": round(hit / total, 3) if total else 1.0}

    denom = 2 * counts["must"][0] + counts["nice"][0]
    weighted = (2 * counts["must"][1] + counts["nice"][1]) / denom if denom else 1.0
    return {"must": stat("must"), "nice": stat("nice"), "weighted_coverage": round(weighted, 3),
            "covered": covered, "missing_evidenced": evidenced, "missing_unevidenced": unevidenced}


# --------------------------------------------------------------------------- vendor rules
TITLE_WORDS = re.compile(r"\b(?:Engineer|Scientist|Developer|Researcher|Analyst|Manager|Lead|"
                         r"Architect|Intern|Consultant|Assistant|Director|Designer|Specialist|"
                         r"Founder|Associate|Fellow|Instructor|Programmer|Administrator)\b")


@dataclass
class VendorContext:
    text: str
    headings: list[Heading]
    jd_text: str | None = None
    has_docx: bool = False
    body_font: dict[str, Any] | None = None
    taxonomy: Taxonomy | None = None


def _experience_block(text: str) -> list[str]:
    lines, inside, out = _lines(text), False, []
    exact, _ = _heading_index()
    for ln in lines:
        key = ln.rstrip(":").lower()
        if key in exact:
            inside = exact[key] == "Experience"
            continue
        if inside and ln:
            out.append(ln)
    return out


def _acronyms_not_spelled_out(text: str) -> list[str]:
    body = "\n".join(ln for ln in _lines(text) if ln and not ln.isupper())
    low = body.lower()
    found: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"(?<![\w])(?:[A-Z]{2,6}|[A-Z][A-Z0-9]{1,5})s?(?![\w])", body):
        base = token[:-1] if token.endswith("s") and token[:-1].isupper() else token
        if base in seen or base in SAFE_ACRONYMS or base in US_STATES:
            continue
        seen.add(base)
        expansion = ABBREVIATIONS.get(base)
        if expansion and expansion.lower() in low:
            continue
        if re.search(rf"\([^)]*\b{re.escape(base)}\b[^)]*\)", body) and expansion is None:
            continue
        found.append(f"{base} ({expansion})" if expansion else base)
    return found


def vendor_warnings(vendor: str, ctx: VendorContext) -> list[str]:
    tax = ctx.taxonomy or load_taxonomy(None)
    rules = {"unknown": ("ashby", "lever", "icims", "taleo", "successfactors")}.get(vendor, (vendor,))
    out: list[str] = []
    if "workday" in rules:
        titled = [ln for ln in _experience_block(ctx.text)
                  if RANGE_RE.search(ln) and TITLE_WORDS.search(RANGE_RE.split(ln)[0])]
        if not ctx.has_docx:
            out.append("Workday: submit the DOCX (render_resume.py --docx); its company line sits "
                       "above the title, which Workday parses best")
        if titled:
            out.append(f"Workday: {len(titled)} experience header(s) lead with the title; Workday "
                       "wants the company line above the title (the DOCX does this)")
    if "greenhouse" in rules:
        out.append("Greenhouse: no auto-score or auto-reject, a recruiter reads it; lead each role "
                   "with its strongest evidenced bullet")
        fmt = check_dates(ctx.text).data.get("format")
        if fmt and fmt != "Mon YYYY":
            out.append(f"Greenhouse: dates read '{fmt}'; use 'Mon YYYY - Mon YYYY'")
    if "lever" in rules:
        acronyms = _acronyms_not_spelled_out(ctx.text)
        if acronyms:
            out.append("Lever: spell out each abbreviation once (Lever does not map them): "
                       + ", ".join(acronyms[:10]))
    if "ashby" in rules:
        bad = [h.raw for h in ctx.headings if not h.is_standard or h.letter_spaced]
        if bad:
            out.append("Ashby: non-canonical headings orphan their content: "
                       + ", ".join(f"'{b}'" for b in dict.fromkeys(bad))
                       + " (use Education, Skills, Experience, Projects, Publications, Awards)")
    if "icims" in rules:
        font = ctx.body_font or {}
        if font.get("serif") and (font.get("size") or 0) < 11:
            out.append(f"iCIMS: serif body font {font.get('name')} at {font.get('size')}pt; iCIMS "
                       "misparses serif under 11pt, use the sans-serif RenderCV template")
    if "taleo" in rules:
        if ctx.jd_text:
            regions = split_jd_regions(ctx.jd_text)
            jd = "\n".join(regions.values())
            gaps = []
            for term in tax.terms():
                if term.source == "phrase":
                    continue
                surface = term.find(jd)
                if not surface:
                    continue
                if not _surface_regex(surface, False).search(ctx.text):
                    alias = term.find(ctx.text)
                    if alias:
                        gaps.append(f"'{surface}' (resume says '{alias}')")
            if gaps:
                out.append("Taleo: matches keywords literally, use the JD's wording where the work "
                           "is real: " + ", ".join(gaps[:10]))
        else:
            out.append("Taleo: matches keywords literally; pass --jd to list exact-wording gaps")
    if "successfactors" in rules:
        odd = [f"'{m.group(0)}' ({NONSTANDARD_SKILLS[m.group(0).lower()]})"
               for m in re.finditer(r"(?<![\w.])(?:k8s|postgres|mongo|sklearn|golang|nodejs|react\.js)(?![\w.])",
                                    ctx.text, re.I)]
        if odd:
            out.append("SuccessFactors: use standard skill names for its taxonomy: "
                       + ", ".join(dict.fromkeys(odd)))
        if not any(h.canonical == "Skills" for h in ctx.headings):
            out.append("SuccessFactors: add a Skills section; its parser maps skills from there")
    if vendor == "unknown":       # no vendor named: every strict rule applies, and the reader should know why
        out = [f"vendor unknown, strictest rules apply: {w}" for w in out]
    return out


# --------------------------------------------------------------------------- orchestration
def default_contact_email() -> str:
    try:
        facts = yaml.safe_load(FACTS.read_text(encoding="utf-8"))
        email = (facts or {}).get("identity", {}).get("email")
        if isinstance(email, str) and "@" in email:
            return email
    except (OSError, yaml.YAMLError, AttributeError):
        pass
    return FALLBACK_CONTACT_EMAIL


def run_check(pdf: Path, *, jd: Path | None = None, vendor: str = "unknown",
              contact_email: str | None = None, taxonomy_path: Path | None = None,
              source: Path | None = None, expect_sections: list[str] | None = None,
              max_pages: int = 1, use_pdfkit: bool = True, check_date: Any = None) -> dict[str, Any]:
    import pypdf

    pdf = Path(pdf)
    email = contact_email or default_contact_email()
    meta_path = pdf.parent / "meta.yaml"
    meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else None
    meta = meta if isinstance(meta, dict) else {}
    if expect_sections is None and isinstance(meta.get("sections"), list):
        expect_sections = [s for s in meta["sections"] if s in CV_SECTIONS]
    if source is None and meta.get("source"):
        candidate = Path(meta["source"])
        candidate = candidate if candidate.is_absolute() else JS_ROOT / candidate
        source = candidate if candidate.is_file() else None

    extractions = {"pdftotext": extract_pdftotext(pdf), "pypdf": extract_pypdf(pdf),
                   "pdfkit": extract_pdfkit(pdf, use_pdfkit),
                   "pdftotext_layout": extract_pdftotext(pdf, layout=True)}
    texts = _texts(extractions)
    try:
        pages = len(pypdf.PdfReader(str(pdf)).pages)
    except Exception:
        pages = 0
    reference = texts.get("pdftotext") or next(iter(texts.values()), "")
    layout = extractions["pdftotext_layout"].text if extractions["pdftotext_layout"].available else None

    checks = [check_text_layer(texts)]
    if checks[0].status == "fail":
        names = ("reading_order", "contact_email", "headings", "dates", "glyphs", "em_dashes",
                 "split_words", "layout", "probes")
        checks += [Check(n, "skip", "no text layer") for n in names]
        checks.append(check_pages(pages, max_pages))
    else:
        probes = load_probes(source) if source else None
        split_texts = dict(texts)
        if layout is not None:
            split_texts["pdftotext_layout"] = layout
        checks += [check_reading_order(texts), check_contact(texts, email),
                   check_headings(texts, expect_sections), check_dates(reference),
                   check_glyphs(texts), check_em_dashes(texts), check_split_words(split_texts),
                   check_layout(layout), check_pages(pages, max_pages),
                   check_probes(texts, probes), check_widows(layout)]

    taxonomy = load_taxonomy(taxonomy_path)
    jd_text = Path(jd).read_text(encoding="utf-8") if jd else None
    keywords = None
    if jd_text and reference:
        terms = extract_jd_terms(jd_text, taxonomy)
        keywords = keyword_coverage(terms["must"], terms["nice"], reference, taxonomy)
        low = keywords["must"]["coverage"] < 0.6
        checks.append(Check("keywords", "warn" if low else "pass",
                            f"must-have coverage {keywords['must']['coverage']:.0%}, nice-to-have "
                            f"{keywords['nice']['coverage']:.0%}"))
    fonts = pdf_fonts(pdf)
    headings = find_headings(reference)
    has_docx = any(pdf.parent.glob(f"{pdf.stem}.docx")) or bool(meta.get("docx"))
    vwarn = vendor_warnings(vendor, VendorContext(reference, headings, jd_text, has_docx,
                                                  fonts.get("body"), taxonomy)) if reference else []

    failures = [c.detail for c in checks if c.status == "fail"]
    warnings = [f"{c.name}: {c.detail}" for c in checks if c.status == "warn"]
    return {
        "pdf": str(pdf),
        "checked_at": _rr.stamp(check_date),
        "passed": not failures,
        "vendor": vendor,
        "contact_email": email,
        "pages": pages,
        "max_pages": max_pages,
        "source": str(source) if source else None,
        "jd": str(jd) if jd else None,
        "extractors": {n: {"available": e.available, "backend": e.backend, "chars": len(e.text),
                           "error": e.error} for n, e in extractions.items()},
        "fonts": fonts,
        "checks": [asdict(c) for c in checks],
        "failures": failures,
        "warnings": warnings,
        "keywords": keywords,
        "vendor_warnings": vwarn,
    }


def report_markdown(rep: dict[str, Any]) -> str:
    status = "PASS" if rep["passed"] else "FAIL"
    lines = [f"# ATS report: {status}", "",
             f"- PDF: `{rep['pdf']}` ({rep['pages']} page(s), max {rep['max_pages']})",
             f"- Vendor: {rep['vendor']} \u00b7 contact: `{rep['contact_email']}` \u00b7 checked "
             f"{rep['checked_at']}"]
    ex = rep["extractors"]
    lines.append("- Extractors: " + ", ".join(
        f"{n} {'ok (' + v['backend'] + ')' if v['available'] else 'unavailable (' + str(v['error']) + ')'}"
        for n, v in ex.items() if n != "pdftotext_layout"))
    body = (rep.get("fonts") or {}).get("body")
    if body:
        kind = {True: "serif", False: "sans-serif", None: "unknown style"}[body["serif"]]
        lines.append(f"- Body font: {body['name']} {body['size']}pt ({kind})")
    lines += ["", "## Checks", "", "| Check | Status | Detail |", "|---|---|---|"]
    for c in rep["checks"]:
        detail = c["detail"].replace("|", "/").replace("\n", " ")
        lines.append(f"| {c['name']} | {c['status'].upper()} | {detail} |")
    if rep["failures"]:
        lines += ["", "## Failures (fix before use)", ""] + [f"- {f}" for f in rep["failures"]]
    kw = rep.get("keywords")
    if kw:
        lines += ["", "## Keyword coverage", "",
                  f"Must-have {kw['must']['covered']}/{kw['must']['total']} "
                  f"({kw['must']['coverage']:.0%}), nice-to-have {kw['nice']['covered']}/"
                  f"{kw['nice']['total']} ({kw['nice']['coverage']:.0%}), weighted "
                  f"{kw['weighted_coverage']:.0%}.", "",
                  "- Covered: " + (", ".join(f"{c['term']} ({c['tier']})" for c in kw["covered"]) or "none"),
                  "- Missing but evidenced (you could add, through an evidenced bullet): "
                  + (", ".join(f"{c['term']} ({c['tier']}; {', '.join(c['evidence'])})"
                               for c in kw["missing_evidenced"]) or "none"),
                  "- Missing and not evidenced (must NOT add): "
                  + (", ".join(f"{c['term']} ({c['tier']}{'; held' if c.get('held') else ''})"
                               for c in kw["missing_unevidenced"]) or "none")]
    if rep["vendor_warnings"] or rep["warnings"]:
        lines += ["", "## Warnings", ""] + [f"- {w}" for w in rep["vendor_warnings"] + rep["warnings"]]
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="check one PDF and write ats-report.json + ats-report.md")
    c.add_argument("--pdf", required=True, type=Path)
    c.add_argument("--jd", type=Path)
    c.add_argument("--vendor", choices=VENDORS, default="unknown")
    c.add_argument("--contact-email", help="expected contact email (default: profile/facts.yaml "
                   "identity.email)")
    c.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    c.add_argument("--source", type=Path, help="the resume YAML, for verbatim claim probes")
    c.add_argument("--expect-sections", help="comma-separated heading order (default: meta.yaml)")
    c.add_argument("--max-pages", type=int, default=1)
    c.add_argument("--out", type=Path, help="report folder (default: the PDF's folder)")
    c.add_argument("--no-pdfkit", action="store_true", help="skip the macOS PDFKit extractor")
    c.add_argument("--date", type=cli_date, default=None,
                   help="check date YYYY-MM-DD for checked_at in both reports (default: today)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.pdf.is_file():
        print(f"error: PDF not found: {args.pdf}", file=sys.stderr)
        return 2
    for label, path in (("--jd", args.jd), ("--source", args.source)):
        if path is not None and not path.is_file():
            print(f"error: {label} file not found: {path}", file=sys.stderr)
            return 2
    expect = [s.strip() for s in args.expect_sections.split(",")] if args.expect_sections else None
    rep = run_check(args.pdf, jd=args.jd, vendor=args.vendor, contact_email=args.contact_email,
                    taxonomy_path=args.taxonomy, source=args.source, expect_sections=expect,
                    max_pages=args.max_pages, use_pdfkit=not args.no_pdfkit, check_date=args.date)
    out = args.out or args.pdf.parent
    out.mkdir(parents=True, exist_ok=True)
    (out / "ats-report.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False) + "\n",
                                         encoding="utf-8")
    (out / "ats-report.md").write_text(report_markdown(rep), encoding="utf-8")
    print(("PASS" if rep["passed"] else "FAIL") + f" {args.pdf} -> {out / 'ats-report.md'}")
    for f in rep["failures"]:
        print(f"  FAIL {f}")
    for w in rep["vendor_warnings"]:
        print(f"  WARN {w}")
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
