#!/usr/bin/env python3
"""page_layout_check.py: the rendered page, measured. Every other gate reads words; this one reads the page.

    page_layout_check.py <pdf> [--json] [--out DIR] [--max-pages N] [--date YYYY-MM-DD]

Renders page 1 to a PNG (poppler pdftoppm, greyscale, DPI below) and reads every text line's box (poppler
`pdftotext -bbox-layout`), then checks:
  page_count         pages <= the budget: --max-pages, else `max_pages` in a meta.yaml beside the PDF or one folder
                     up (a variant's send/ or inline/ copy), else 1. FAIL above it.
  bottom_whitespace  the empty band below the last ink on page 1, beyond the bottom margin. The bottom margin is taken
                     equal to the white band above the first ink (the templates use one margin top and bottom), so
                     bottom_slack_pct = (bottom band - top band) / page height. FAIL above BOTTOM_SLACK_FAIL_PCT: the
                     page stops short and leaves a blank strip a recruiter sees at once. bottom_whitespace_pct (the raw
                     band, margin included) is reported beside it.
  header_gaps        the white space above each section header against the white space above an ordinary bullet
                     (ink gaps measured on the image). header_gap_ratio = the tightest header gap / the median bullet
                     gap. FAIL below HEADER_GAP_MIN_RATIO: headers with no more air above them than a bullet read as
                     one undivided block (the "cluttered" signal).
  right_margin       FAIL when any line or ink runs past the right margin (taken equal to the left margin).
  margins            WARN when left and right, or top and side, differ by more than MARGIN_SKEW_PT; FAIL when a
                     margin is under MARGIN_MIN_PT (text against the paper's edge).
  hierarchy          WARN when a break inside a section (above an entry) has more air than the tightest header.
  density            text rows per inch of the inked height. WARN above DENSITY_WARN_LPI (calibrated on base 75).
Writes page-1.png and layout-report.json (bound to the PDF's sha256) beside the PDF, or into --out. Reviewers Read
page-1.png and judge the page by eye; apply_pack.py refuses a page whose layout-report.json is not a PASS for the
exact PDF it ships.
Exit 0 PASS (warnings allowed), 1 FAIL, 2 usage or toolchain error (UNCHECKABLE: the page was not measured).
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import shutil
import statistics
import struct
import subprocess
import sys
import tempfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

DPI = 150
INK = 160                         # a grey value below this is ink (anti-aliased edges are lighter)
BOTTOM_SLACK_FAIL_PCT = 3.0       # brief: more than 3% of the page empty beyond the bottom margin fails
HEADER_GAP_MIN_RATIO = 1.5        # a header needs half again the air of a bullet break above it
DENSITY_WARN_LPI = 6.0            # rows per inch; the densest accepted base page measures about 5.8
MARGIN_SKEW_PT = 18.0             # 0.25in
MARGIN_MIN_PT = 18.0              # 0.25in
RIGHT_TOLERANCE_PT = 1.5
MIN_GAP_PT = 0.25                 # floor for a bullet gap in the ratio (two lines whose ink touches)
REPORT_NAME = "layout-report.json"
PNG_NAME = "page-1.png"
BULLETS = ("•", "·", "▪", "◦", "‣", "⁃", "-", "*", "–")

# The section headings render_resume.py accepts; kept here so this gate never imports the renderer (another lane
# edits it, and a gate must not break when the renderer does).
HEADINGS = frozenset(h.lower() for h in (
    "Summary", "Experience", "Education", "Skills", "Projects", "Publications", "Awards", "Certifications",
    "Research Experience", "Teaching Experience", "Presentations", "Student Leadership",
    "Work Experience", "Professional Experience", "Work History", "Employment", "Employment History",
    "Relevant Experience", "Industry Experience", "Technical Skills", "Skills and Tools", "Skills & Tools",
    "Core Competencies", "Selected Projects", "Personal Projects", "Selected Publications", "Honors and Awards",
    "Honors", "Leadership", "Patents", "Talks"))


# --------------------------------------------------------------------------- data
@dataclass
class Line:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class Page:
    width: float
    height: float
    lines: list[Line] = field(default_factory=list)


@dataclass
class Ink:
    """A greyscale raster of page 1: `rows[r]` is one row of grey bytes, 0 black to 255 white."""
    width: int
    height: int
    rows: list[bytes]

    def row_inked(self, r: int, c0: int = 0, c1: int | None = None) -> bool:
        seg = self.rows[r][c0:c1]
        return bool(seg) and min(seg) < INK

    def inked_rows(self) -> list[int]:
        return [r for r in range(self.height) if self.row_inked(r)]

    def column_extent(self) -> tuple[int, int] | None:
        table = bytes(1 if v < INK else 0 for v in range(256))
        left, right = None, None
        for row in self.rows:
            marks = row.translate(table)
            a = marks.find(1)
            if a < 0:
                continue
            b = marks.rfind(1)
            left = a if left is None else min(left, a)
            right = b if right is None else max(right, b)
        return None if left is None else (left, right)


class Uncheckable(Exception):
    """The page could not be measured (exit 2)."""


# --------------------------------------------------------------------------- helpers
def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pt(px: float) -> float:
    return px * 72.0 / DPI


def px(points: float) -> int:
    return int(round(points * DPI / 72.0))


def is_header(text: str) -> bool:
    t = re.sub(r"\s+", " ", text).strip().rstrip(":").lower()
    return t in HEADINGS


def is_bullet(text: str) -> bool:
    return text.lstrip().startswith(BULLETS)


def gap_ratio(header_gaps: list[float], bullet_gaps: list[float]) -> float | None:
    """The tightest header gap over the median bullet gap; None when either side is unmeasured."""
    if not header_gaps or not bullet_gaps:
        return None
    return min(header_gaps) / max(statistics.median(bullet_gaps), MIN_GAP_PT)


def png_grey(width: int, height: int, pixels: bytes) -> bytes:
    """An 8-bit greyscale PNG (filter 0 on every row): no imaging library needed."""
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + pixels[r * width:(r + 1) * width] for r in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def max_pages_for(pdf: Path) -> int:
    """`max_pages` from a meta.yaml beside the PDF or one folder up (send/, inline/); else 1."""
    for folder in (pdf.parent, pdf.parent.parent):
        meta = folder / "meta.yaml"
        if not meta.is_file():
            continue
        try:
            data = yaml.safe_load(meta.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError, UnicodeDecodeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("max_pages"), int) and data["max_pages"] > 0:
            return data["max_pages"]
    return 1


# --------------------------------------------------------------------------- poppler
LINE_RE = re.compile(r'<line xMin="([-\d.]+)" yMin="([-\d.]+)" xMax="([-\d.]+)" yMax="([-\d.]+)">(.*?)</line>', re.S)
WORD_RE = re.compile(r"<word[^>]*>(.*?)</word>", re.S)
PAGE_RE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', re.S)


def read_pages(pdf: Path) -> list[Page]:
    exe = shutil.which("pdftotext")
    if not exe:
        raise Uncheckable("pdftotext not installed (poppler)")
    proc = subprocess.run([exe, "-bbox-layout", str(pdf), "-"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise Uncheckable(f"pdftotext failed: {proc.stderr.strip()[:200]}")
    pages = []
    for m in PAGE_RE.finditer(proc.stdout):
        page = Page(float(m.group(1)), float(m.group(2)))
        for lm in LINE_RE.finditer(m.group(3)):
            words = [html.unescape(w) for w in WORD_RE.findall(lm.group(5))]
            text = " ".join(w for w in words if w.strip())
            if text:
                page.lines.append(Line(*(float(v) for v in lm.groups()[:4]), text))
        pages.append(page)
    if not pages:
        raise Uncheckable("pdftotext found no page")
    return pages


def render_page1(pdf: Path) -> Ink:
    exe = shutil.which("pdftoppm")
    if not exe:
        raise Uncheckable("pdftoppm not installed (poppler)")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "p"
        proc = subprocess.run([exe, "-gray", "-r", str(DPI), "-f", "1", "-l", "1", "-singlefile", str(pdf), str(base)],
                              capture_output=True, text=True)
        out = base.with_suffix(".pgm")
        if proc.returncode != 0 or not out.is_file():
            raise Uncheckable(f"pdftoppm failed: {proc.stderr.strip()[:200]}")
        data = out.read_bytes()
    return parse_pgm(data)


def parse_pgm(data: bytes) -> Ink:
    tokens, pos = [], 0
    while len(tokens) < 4:
        while data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b"#":
            pos = data.index(b"\n", pos) + 1
            continue
        start = pos
        while not data[pos:pos + 1].isspace():
            pos += 1
        tokens.append(data[start:pos])
    if tokens[0] != b"P5" or int(tokens[3]) > 255:
        raise Uncheckable("pdftoppm wrote an unexpected image format")
    width, height = int(tokens[1]), int(tokens[2])
    pixels = data[pos + 1:pos + 1 + width * height]
    if len(pixels) != width * height:
        raise Uncheckable("pdftoppm wrote a truncated image")
    return Ink(width, height, [pixels[r * width:(r + 1) * width] for r in range(height)])


# --------------------------------------------------------------------------- measuring
def visual_rows(lines: list[Line]) -> list[Line]:
    """Lines that share a visual row (a date set apart on the right) merged, top to bottom."""
    rows: list[Line] = []
    for ln in sorted(lines, key=lambda l: (l.cy, l.x0)):
        if rows and abs(ln.cy - rows[-1].cy) < 0.35 * max(ln.y1 - ln.y0, 1.0):
            r = rows[-1]
            parts = sorted([(r.x0, r.text), (ln.x0, ln.text)])
            rows[-1] = Line(min(r.x0, ln.x0), min(r.y0, ln.y0), max(r.x1, ln.x1), max(r.y1, ln.y1),
                            " ".join(t for _, t in parts))
        else:
            rows.append(ln)
    return rows


def gap_above(prev: Line, cur: Line, ink: Ink | None) -> float:
    """The white band between two rows, in points: the longest run of ink-free image rows between their centres
    (the box gap when there is no image)."""
    if ink is None:
        return cur.y0 - prev.y1
    a, b = max(px(prev.cy), 0), min(px(cur.cy), ink.height)
    best = run = 0
    for r in range(a, b):
        run = 0 if ink.row_inked(r) else run + 1
        best = max(best, run)
    return pt(best)


def analyze(pages: list[Page], ink: Ink | None, max_pages: int = 1) -> dict[str, Any]:
    page = pages[0]
    W, H = page.width, page.height
    checks: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    n = len(pages)
    add("page_count", "pass" if n <= max_pages else "fail",
        f"{n} page{'s' if n != 1 else ''} (budget {max_pages})" + ("" if n <= max_pages else
                                                                     ": the page must fit its budget"))
    rows = visual_rows(page.lines)
    if not rows:
        raise Uncheckable("page 1 has no text layer")

    # margins and the bottom band
    if ink is not None:
        inked = ink.inked_rows()
        extent = ink.column_extent()
        if not inked or extent is None:
            raise Uncheckable("page 1 renders blank")
        top, bottom = pt(inked[0]), pt(ink.height - 1 - inked[-1])
        left, right = pt(extent[0]), pt(ink.width - 1 - extent[1])
        ink_h = pt(inked[-1] - inked[0])
    else:
        top, bottom = min(r.y0 for r in rows), H - max(r.y1 for r in rows)
        left, right = min(r.x0 for r in rows), W - max(r.x1 for r in rows)
        ink_h = max(r.y1 for r in rows) - min(r.y0 for r in rows)
    slack = (bottom - top) / H * 100
    metrics.update(top_margin_pt=round(top, 1), bottom_margin_pt=round(bottom, 1), left_margin_pt=round(left, 1),
                   right_margin_pt=round(right, 1), bottom_whitespace_pct=round(bottom / H * 100, 2),
                   bottom_slack_pct=round(slack, 2))
    if slack > BOTTOM_SLACK_FAIL_PCT:
        add("bottom_whitespace", "fail",
            f"bottom is {slack:.1f}% of the page emptier than the top ({bottom / H * 100:.1f}% below the last line vs "
            f"{top / H * 100:.1f}% above the first; limit {BOTTOM_SLACK_FAIL_PCT}%): the page stops short; fill it "
            "with the strongest true work or open the spacing")
    else:
        add("bottom_whitespace", "pass", f"{slack:+.1f}% beyond the top margin (limit {BOTTOM_SLACK_FAIL_PCT}%)")

    # header gaps against bullet gaps
    header_gaps: list[tuple[float, str]] = []
    bullet_gaps: list[float] = []
    ordinary: list[float] = []
    for prev, cur in zip(rows, rows[1:]):
        g = gap_above(prev, cur, ink)
        if is_header(cur.text):
            header_gaps.append((g, cur.text))
        elif not is_header(prev.text):
            ordinary.append(g)
            if is_bullet(cur.text):
                bullet_gaps.append(g)
    base = bullet_gaps or ordinary
    ratio = gap_ratio([g for g, _ in header_gaps], base)
    metrics.update(header_gap_ratio=None if ratio is None else round(ratio, 2),
                   header_gap_min_pt=round(min(g for g, _ in header_gaps), 2) if header_gaps else None,
                   bullet_gap_median_pt=round(statistics.median(base), 2) if base else None,
                   headers=len(header_gaps))
    if ratio is None:
        add("header_gaps", "warn", "no section header or no bullet to compare: judge the spacing by eye")
    else:
        tight_g, tight = min(header_gaps)
        detail = (f"tightest header '{tight}' has {tight_g:.1f}pt above it vs {statistics.median(base):.1f}pt above a "
                  f"bullet: ratio {ratio:.2f} (minimum {HEADER_GAP_MIN_RATIO})")
        add("header_gaps", "pass" if ratio >= HEADER_GAP_MIN_RATIO else "fail",
            detail if ratio >= HEADER_GAP_MIN_RATIO else detail + ": headers do not separate the sections (cluttered)")
    # hierarchy: a section break should carry at least the air of the widest break inside a section (an entry)
    inner = [g for g in ordinary if not math.isclose(g, 0.0)]
    metrics["entry_gap_max_pt"] = round(max(inner), 2) if inner else None
    if header_gaps and inner and min(g for g, _ in header_gaps) < max(inner):
        add("hierarchy", "warn", f"a break inside a section ({max(inner):.1f}pt) has more air than the tightest "
                                 f"section header ({min(g for g, _ in header_gaps):.1f}pt): the eye cannot find the sections")
    else:
        add("hierarchy", "pass", "section headers carry the widest breaks on the page")

    # right margin: a line or ink past the left margin's mirror
    limit = W - left + RIGHT_TOLERANCE_PT
    over = [r for r in rows if r.x1 > limit or r.x0 < 0]
    ink_over = ink is not None and right < left - RIGHT_TOLERANCE_PT
    metrics["rightmost_x_pt"] = round(max(r.x1 for r in rows), 1)
    if over or ink_over:
        what = "; ".join(f"'{r.text[:50]}' ends at {r.x1:.1f}pt" for r in over[:3]) or \
            f"ink reaches {W - right:.1f}pt"
        add("right_margin", "fail", f"text runs past the right margin ({W - left:.1f}pt): {what}")
    else:
        add("right_margin", "pass", f"every line ends inside {W - left:.1f}pt")

    # margins
    thin = [n for n, v in (("left", left), ("right", right), ("top", top), ("bottom", bottom)) if v < MARGIN_MIN_PT]
    skew = []
    if abs(left - right) > MARGIN_SKEW_PT:
        skew.append(f"left {left:.0f}pt vs right {right:.0f}pt")
    if abs(top - left) > MARGIN_SKEW_PT:
        skew.append(f"top {top:.0f}pt vs side {left:.0f}pt")
    if thin:
        add("margins", "fail", f"margin under {MARGIN_MIN_PT:.0f}pt: {', '.join(thin)}")
    elif skew:
        add("margins", "warn", "margins differ: " + "; ".join(skew))
    else:
        add("margins", "pass", f"top {top:.0f}pt, left {left:.0f}pt, right {right:.0f}pt")

    # density
    lpi = len(rows) / (ink_h / 72.0) if ink_h > 0 else 0.0
    metrics.update(lines=len(rows), lines_per_inch=round(lpi, 2))
    add("density", "warn" if lpi > DENSITY_WARN_LPI else "pass",
        f"{len(rows)} rows, {lpi:.2f} per inch (warn above {DENSITY_WARN_LPI})")

    failures = [f"{c['name']}: {c['detail']}" for c in checks if c["status"] == "fail"]
    warnings = [f"{c['name']}: {c['detail']}" for c in checks if c["status"] == "warn"]
    return {"verdict": "FAIL" if failures else "PASS", "passed": not failures, "pages": n, "max_pages": max_pages,
            "metrics": metrics, "checks": checks, "failures": failures, "warnings": warnings,
            "thresholds": {"bottom_slack_fail_pct": BOTTOM_SLACK_FAIL_PCT, "header_gap_min_ratio": HEADER_GAP_MIN_RATIO,
                           "density_warn_lpi": DENSITY_WARN_LPI, "margin_skew_pt": MARGIN_SKEW_PT,
                           "margin_min_pt": MARGIN_MIN_PT, "dpi": DPI}}


# --------------------------------------------------------------------------- the run
def check_pdf(pdf: Path, out_dir: Path | None = None, max_pages: int | None = None,
              checked_at: str | None = None) -> dict[str, Any]:
    """Measure the PDF, write page-1.png and layout-report.json into out_dir (default: beside the PDF), return the
    report. Raises Uncheckable when the page cannot be measured."""
    if not pdf.is_file():
        raise FileNotFoundError(pdf)
    pages = read_pages(pdf)
    ink = render_page1(pdf)
    budget = max_pages if max_pages else max_pages_for(pdf)
    report = analyze(pages, ink, budget)
    out = out_dir or pdf.parent
    out.mkdir(parents=True, exist_ok=True)
    png = out / PNG_NAME
    png.write_bytes(png_grey(ink.width, ink.height, b"".join(ink.rows)))
    report = {"checker": "page_layout_check.py", "version": sha256_file(Path(__file__))[:12], "pdf": pdf.name,
              "pdf_path": str(pdf), "pdf_sha256": sha256_file(pdf),
              "checked_at": checked_at or datetime.now().astimezone().isoformat(timespec="seconds"),
              "png": str(png), **report}
    (out / REPORT_NAME).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def human(report: dict[str, Any]) -> str:
    n, m = report["pages"], report["max_pages"]
    lines = [f"{report['verdict']} page layout: {report['pdf']} ({n} page{'s' if n != 1 else ''}, budget {m})"]
    for c in report["checks"]:
        lines.append(f"  {c['status'].upper():4} {c['name']}: {c['detail']}")
    lines.append(f"  png: {report['png']} (Read it: every reviewer judges the page by eye)")
    lines.append(f"  report: {Path(report['png']).with_name(REPORT_NAME)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("pdf", type=Path)
    p.add_argument("--json", action="store_true", help="print the report as JSON")
    p.add_argument("--out", type=Path, default=None, help="folder for page-1.png and layout-report.json")
    p.add_argument("--max-pages", type=int, default=None, help="page budget (default: meta.yaml max_pages, else 1)")
    p.add_argument("--date", default=None, help="stamp checked_at with this date (YYYY-MM-DD) for a replayable run")
    args = p.parse_args(argv)
    if not args.pdf.is_file():
        print(f"UNCHECKABLE: {args.pdf} not found", file=sys.stderr)
        return 2
    if args.date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
        print(f"--date {args.date!r} is not YYYY-MM-DD", file=sys.stderr)
        return 2
    try:
        report = check_pdf(args.pdf, args.out, args.max_pages, args.date)
    except Uncheckable as exc:
        print(f"UNCHECKABLE: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2) if args.json else human(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
