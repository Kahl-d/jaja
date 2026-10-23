#!/usr/bin/env python3
"""Render a resume YAML to an ATS-safe one-page PDF (and a DOCX for Workday), with a strict
pre-render lint and a `meta.yaml` build record (design sections 4, 5.5, 6.3).

    render_resume.py render  --input <resume.yaml> --out <dir> [--docx] [--engine rendercv|latex]
                             [--max-pages 1] [--evidence profile/evidence.yaml] [--force]
                             [--keep-build] [--basename NAME] [--date YYYY-MM-DD]
                             [--facts profile/facts.yaml] [--rulings resumes/templates/rulings.yaml]
                             [--template house|classic|inline|<file>] [--density house|one-page]
    render_resume.py lint    --input <resume.yaml> [--evidence ...] [--facts ...] [--rulings ...]
                             [--template ...] [--density ...]
    render_resume.py measure --input <resume.yaml> [--template ...] [--density ...] [--record]

--template (default house: resumes/templates/rendercv-ats.yaml, dates inline after a bar) picks the design.
`classic` and `inline` are the general base's own files, resumes/bases/general/template-classic.yaml (dates
right-aligned on the title line) and template-inline.yaml (each date inline after its title), read in place so
the frozen base stays the one source of its design (a base folder without them falls back to the repo's
templates/resume copies; so do the house template, line-widths.yaml, rulings.yaml and latex-ats/ when the data
folder's resumes/templates has none; `measure --record` writes into the data folder); a path renders with a
variant's copy of them. Every template
must keep the ATS invariants (template_problems: one column, black text, left alignment, no icons, the sans
body font, three-letter months, dash-free templates) or the run is a usage error. The general base renders
text-identical to its frozen PDFs with `--template classic --density one-page` and `--template inline
--density one-page` (scripts/tests/test_render_general_base.py). meta.yaml records the template and its sha256.

--density (default house: the template's design, unchanged) picks an opt-in overlay from the template's
`densities:` block. `one-page` sets 10pt body text, 0.4em line spacing and 0.4in margins (measured: 45
rendered lines on page one become 52). An overlay may change only the page margins, the line spacing and
the body font size (10pt or more); every other key, and so every ATS rule, stays as in the house design.
RenderCV engine only (the LaTeX fallback has no measured density). meta.yaml records the density.

--date (default: today) dates the build: meta.yaml `built_at` is that day at 12:00 (the wall clock
when it is today).
PDF title (metadata): "<name>, Resume" from --facts identity.name_on_resumes.canonical (else
identity.full_name, else cv.name), never a dash variant; set in the RenderCV settings and the LaTeX hyperref
setup, read back from the PDF (a mismatch fails the build) and kept in meta.yaml. The lens never shows for a
base (base_of null) or a variant of the general base (base_of: general): it is an internal emphasis label
(title_lens). Only a variant of another designed base would carry "(<Lens>)".

Input: RenderCV 2.8 YAML (`cv:`) plus department sidecars that never render:
  sidecar:            top-level build metadata, copied into meta.yaml: lens (the variant's emphasis label,
                      a kebab slug or null), base_of (null for a base; else the base it copies, a folder
                      resumes/bases/<name>/ with a resume.yaml: `general` today), job (null or jobs/<id>.md).
                      Anything else is a `bad-sidecar` error; a base_of that names no base is `unknown-base`.
  evidence: [ids]     optional, on an entry: evidence for its header facts (title, dates, ...)
  <!-- ev:ID[,ID] --> required, at the END of every rendered claim: each highlight, text or
                      bullet entry, one-line `details` (skills), `summary`, and `cv.headline`.
Dates: YYYY-MM or YYYY-MM-DD (render "Mon YYYY"), present, or YYYY when the whole entry is year-only
(start and end both YYYY, rendered "2017 - 2021"; or a single YYYY date), the conservative form while a
month is unconfirmed. A year mixed with a month or with present in one entry is a date-precision error.
The design, locale and settings always come from the template (--template); a resume cannot override them.

Line estimate: `bullet-too-long` counts rendered lines with a greedy word wrap at the template's measured
characters per line (resumes/templates/line-widths.yaml, one entry per layout: page size, side margins, body
size and font after the density; `measure` renders a resume, counts each claim's lines in `pdftotext -layout`
and fits the width; `--record` writes the entry). A layout with no measurement falls back to the font formula
(estimate_chars_per_line) and the run prints a note saying so.

Rulings (the owner's, recorded, never an agent's own call) bend two lint rules. They are `rules:` entries in --facts
(profile/facts.yaml, the corpus-keeper's record) or in --rulings (resumes/templates/rulings.yaml, the owner's rulings
on how resumes render, each naming its `source:`), both read the same way. Every entry carries
`ruled: {by: <owner token>, "on": YYYY-MM-DD, basis}` and one or both lists (the owner token is
identity.owner_token in the config):
  long_bullets: a claim estimated past MAX_CLAIM_LINES (2) is `bullet-too-long` unless an item names it by its
    exact evidence-id set (the ids of its trailing <!-- ev: --> comment, in any order) with its own
    `max_lines` (3 or 4) and an optional `label`:
      - {rule: "...", ruled: {by: <owner token>, "on": 2030-01-15, basis: "..."},
         long_bullets: [{evidence: [ev-a, ev-b], max_lines: 3, label: XX-01}]}
    It covers highlight, bullet and plain-text entries (a Publications paragraph), never a summary. A matching
    claim within its max_lines prints a NOTE `long-bullet-allowed` naming the ruling; past it, still an error.
  labeled_links: a Markdown link whose text is not its URL is `hidden-link` unless an item names that URL:
      labeled_links: [{url: https://example.com/award, label: "what it is, for people"}]
    A matching link prints a NOTE `labeled-link-allowed` naming the ruling. facts.yaml is the record: a URL listed
    in both files is allowed by its facts.yaml entry and the rulings.yaml copy warns `link-rule-duplicate`.
A malformed entry (no ruling block by the owner, a rulings.yaml entry with no source, max_lines outside 3..4, a bad or
repeated evidence set, a URL that is not http(s), an unknown key) is a `long-bullet-rule` or `link-rule` error and
allows nothing.

Exit codes: 0 ok, 1 content failure (lint or page budget), 2 usage or
input error, 3 toolchain failure (renderer missing or crashed).
"""
from __future__ import annotations

import argparse
import copy
import functools
import hashlib
import importlib.metadata
import importlib.util
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as cfg  # noqa: E402
import profile_titles as pt  # noqa: E402

from agency_paths import DATA_ROOT as JS_ROOT  # noqa: E402
TEMPLATES_DIR = JS_ROOT / "resumes" / "templates"       # the data folder's own copy (wins when present)
SYSTEM_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates" / "resume"   # shipped with the repo


def _system_file(rel: str, data_dir: Path = TEMPLATES_DIR) -> Path:
    """`rel` under the data folder's resumes/templates when it has one, else the repo's templates/resume copy."""
    own = data_dir / rel
    return own if own.exists() else SYSTEM_TEMPLATES_DIR / rel


RENDERCV_TEMPLATE = _system_file("rendercv-ats.yaml")
LATEX_TEMPLATE_DIR = _system_file("latex-ats")
BASES_DIR = JS_ROOT / "resumes" / "bases"
GENERAL_BASE = "general"          # the one base every variant starts from
LINE_WIDTHS = TEMPLATES_DIR / "line-widths.yaml"        # --record writes here (the data folder), never the repo
LINE_WIDTHS_READ = _system_file("line-widths.yaml")
DEFAULT_RULINGS = _system_file("rulings.yaml")
DEFAULT_EVIDENCE = JS_ROOT / "profile" / "evidence.yaml"
DEFAULT_FACTS = JS_ROOT / "profile" / "facts.yaml"
HOUSE_TEMPLATE = "house"


def _base_template(name: str) -> Path:
    """The general base's own design file, or the repo's shipped copy when the base folder has none."""
    own = BASES_DIR / GENERAL_BASE / name
    return own if own.is_file() else SYSTEM_TEMPLATES_DIR / name


# Named designs. classic and inline are read in place from the frozen general base (one source of the design).
NAMED_TEMPLATES = {
    HOUSE_TEMPLATE: RENDERCV_TEMPLATE,
    "classic": _base_template("template-classic.yaml"),
    "inline": _base_template("template-inline.yaml"),
}

# Canonical headings, in the house order. The order is a style choice: style.resume_section_order (a list of
# the eight standard headings) overrides this conventional default.
HOUSE_ORDER = tuple(cfg.get("style.resume_section_order", None) or (
    "Summary", "Skills", "Experience", "Projects", "Education", "Publications", "Awards", "Certifications"))
# The token the owner signs a ruling with (`ruled: {by: <token>}`).
OWNER_TOKEN = str(cfg.get("identity.owner_token", "owner")).strip().lower()
CANONICAL_SECTIONS = frozenset(HOUSE_ORDER)
# A CV may also carry these standard academic CV headings,
# in this order after Experience. Resumes keep the eight above.
CV_ORDER = ("Summary", "Education", "Skills", "Experience", "Research Experience", "Teaching Experience", "Projects",
            "Publications", "Presentations", "Awards", "Student Leadership", "Certifications")
CV_SECTIONS = frozenset(CV_ORDER)
# Common non-canonical headings -> the canonical one (Ashby orphans non-standard headings).
SECTION_ALIASES = {
    "work experience": "Experience", "professional experience": "Experience",
    "work history": "Experience", "employment": "Experience", "employment history": "Experience",
    "relevant experience": "Experience", "industry experience": "Experience",
    "experiences": "Experience",
    "technical skills": "Skills", "skills and tools": "Skills", "skills & tools": "Skills",
    "core competencies": "Skills", "technologies": "Skills", "tools": "Skills",
    "technical skills and tools": "Skills",
    "selected projects": "Projects", "personal projects": "Projects",
    "academic projects": "Projects", "side projects": "Projects", "project": "Projects",
    "papers": "Publications", "selected publications": "Publications", "research": "Publications",
    "papers and awards": "Publications", "publication": "Publications",
    "honors": "Awards", "honors and awards": "Awards", "honors & awards": "Awards",
    "achievements": "Awards", "award": "Awards",
    "certificates": "Certifications", "licenses and certifications": "Certifications",
    "licenses & certifications": "Certifications",
    "professional summary": "Summary", "profile": "Summary", "about": "Summary",
    "about me": "Summary", "objective": "Summary",
    "academic background": "Education", "education and training": "Education",
}

EM_DASHES = "\u2014\u2015\u2e3a\u2e3b"            # em-dash, horizontal bar, two/three-em dash
EN_DASH = "\u2013"
EV_TRAILING_RE = re.compile(r"\s*<!--\s*ev:\s*(?P<ids>.*?)\s*-->\s*$", re.S)
EV_ANY_RE = re.compile(r"<!--\s*ev:", re.S)
COMMENT_RE = re.compile(r"\s*<!--.*?-->", re.S)
EV_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
RAW_TYPST_RE = re.compile(r"#[A-Za-z]")
URLISH_RE = re.compile(
    r"(?:https?://|www\.)[^\s)\]]+|[\w.+-]+@[\w-]+(?:\.[\w-]+)+|"
    r"\b[\w-]+(?:\.[\w-]+)*\.(?:com|org|net|io|ai|dev|app|edu|gov|co|us|me|info|xyz)(?:/[^\s)\]]*)?",
    re.I)
COMPOUND_RE = re.compile(r"(?<![\w/#@.\[-])([A-Za-z0-9]+(?:-[A-Za-z0-9]+)+)(?![\w/@\]-])")

# Fields that never carry prose (no char-level lint, no Typst boxing).
NON_PROSE_KEYS = {"email", "phone", "website", "url", "doi", "photo", "date", "start_date",
                  "end_date", "username", "network", "fontawesome_icon", "placeholder"}
# Entry fields that render prose we box against line breaks at hyphens.
BOX_KEYS = {"highlights", "summary", "details", "label", "bullet", "number", "reversed_number",
            "position", "company", "institution", "area", "degree", "title", "journal", "name"}
PAGE_WIDTH_IN = {"us-letter": 8.5, "a4": 8.27, "a5": 5.83, "us-executive": 7.25}
AVG_CHAR_EM = 0.44        # Source Sans 3 average advance, calibrated on a rendered 10.5pt line
BULLET_INDENT_PT = 9.0    # bullet glyph + gap before the text
MAX_CLAIM_LINES = 2
MAX_RULED_LINES = 4       # the most a bullet the owner ruled long may run (facts.yaml rules: long_bullets)
LONG_BULLET_KINDS = frozenset({"highlight", "bullet", "text"})
LONG_BULLET_KEYS = frozenset({"evidence", "max_lines", "label"})
LINK_RULE_KEYS = frozenset({"url", "label"})
RULED_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SIDECAR_KEYS = ("lens", "base_of", "job", "doc")  # doc: cv opens the CV headings
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
JOB_REF_RE = re.compile(r"^jobs/[^/\s]+\.md$")
FIT_KINDS = frozenset({"highlight", "bullet", "text"})    # claims `measure` fits the width on (upright body text)


class UsageError(Exception):
    """Bad input or arguments (exit 2)."""


class ToolchainError(Exception):
    """Renderer missing or crashed (exit 3)."""


# --------------------------------------------------------------------------- lint model
@dataclass
class Issue:
    code: str
    path: str
    message: str
    severity: str = "error"

    def __str__(self) -> str:
        return f"{self.severity.upper()} {self.code} {self.path}: {self.message}"


@dataclass
class LintReport:
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    notes: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, code: str, path: str, message: str) -> None:
        self.errors.append(Issue(code, path, message, "error"))

    def warn(self, code: str, path: str, message: str) -> None:
        self.warnings.append(Issue(code, path, message, "warning"))

    def note(self, code: str, path: str, message: str) -> None:
        self.notes.append(Issue(code, path, message, "note"))

    def summary(self) -> str:
        notes = f", {len(self.notes)} note(s)" if self.notes else ""
        return (("OK" if self.ok else "FAIL") + f" lint: {len(self.errors)} error(s), "
                f"{len(self.warnings)} warning(s){notes}")


@dataclass
class Claim:
    path: str
    text: str
    kind: str      # highlight | text | bullet | details | summary | headline


@dataclass(frozen=True)
class LongBullet:
    """One bullet the owner ruled may run past MAX_CLAIM_LINES (facts.yaml rules: long_bullets)."""
    evidence: frozenset[str]
    max_lines: int
    ruling: str      # names the ruling, for the note and the error


@dataclass(frozen=True)
class RuleSource:
    """A parsed file whose `rules:` may carry rulings, and how issues name it."""
    name: str                    # "facts.yaml" or "rulings.yaml"
    data: Any
    needs_source: bool = False   # rulings.yaml entries must name where the owner ruled


# --------------------------------------------------------------------------- yaml helpers
class _NoAliasDumper(yaml.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:        # RenderCV rejects YAML aliases
        return True


def dump_yaml(data: Any) -> str:
    return yaml.dump(data, Dumper=_NoAliasDumper, sort_keys=False, allow_unicode=True, width=1000)


def load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise UsageError(f"file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise UsageError(f"not valid YAML: {path}: {exc}") from exc


def load_template(path: Path = RENDERCV_TEMPLATE) -> dict[str, Any]:
    data = load_yaml(path)
    if not isinstance(data, dict) or not {"design", "locale", "settings"} <= set(data):
        raise UsageError(f"template must define design, locale and settings: {path}")
    return data


def resolve_template(spec: str | Path | None) -> Path:
    """--template: a name in NAMED_TEMPLATES or a template file. None is the house template (read at call time,
    so a caller that points RENDERCV_TEMPLATE elsewhere, like the general base's frozen render.py, still works)."""
    if spec is None or str(spec) == HOUSE_TEMPLATE:
        return RENDERCV_TEMPLATE
    if str(spec) in NAMED_TEMPLATES:
        return NAMED_TEMPLATES[str(spec)]
    path = Path(spec)
    if not path.is_file():
        raise UsageError(f"unknown template {str(spec)!r}: use {', '.join(NAMED_TEMPLATES)} or a template file")
    return path


def template_label(path: Path) -> str:
    """The name of a named template, else its path (department-relative when inside it)."""
    for name, named in NAMED_TEMPLATES.items():
        if Path(path).resolve() == named.resolve():
            return name
    return _rel(Path(path))


def _get(node: Any, *keys: str) -> Any:
    for key in keys:
        node = node.get(key) if isinstance(node, dict) else None
    return node


SANS_FONTS = frozenset({"Source Sans 3"})       # bundled with RenderCV, embedded in the PDF


def template_problems(template: dict[str, Any]) -> list[str]:
    """The ATS invariants every template keeps, whatever its layout (the house inline dates or the general base's
    right-aligned classic): one column, black text, left alignment with no hyphenation, no icons, footer or top
    note, no time spans, the sans body font at 10pt or more, three-letter months and dash-free template text."""
    d = template.get("design") or {}
    out: list[str] = []
    if _get(d, "page", "size") != "us-letter":
        out.append(f"page.size is {_get(d, 'page', 'size')!r}: US Letter only")
    for key in ("show_footer", "show_top_note"):
        if _get(d, "page", key) is not False:
            out.append(f"page.{key} must be false")
    colors = _get(d, "colors") or {}
    off = sorted(k for k, v in colors.items() if str(v).replace(" ", "") != "rgb(0,0,0)")
    if off:
        out.append(f"colors {', '.join(off)} are not black")
    if _get(d, "typography", "alignment") != "left":
        out.append("typography.alignment must be left (no justification, no hyphenation)")
    if _get(d, "typography", "font_family") not in SANS_FONTS:
        out.append(f"typography.font_family {_get(d, 'typography', 'font_family')!r} is not the sans body font")
    body = _get(d, "typography", "font_size", "body")
    try:
        if body is None or _to_points(body) < MIN_BODY_PT:
            out.append(f"typography.font_size.body {body!r} is under {MIN_BODY_PT:g}pt")
    except UsageError:
        out.append(f"typography.font_size.body {body!r} is not a length")
    if any(v for v in (_get(d, "typography", "small_caps") or {}).values()):
        out.append("typography.small_caps must all be false")
    if _get(d, "header", "connections", "show_icons") is not False:
        out.append("header.connections.show_icons must be false")
    if str(_get(d, "entries", "date_and_location_width")).replace(" ", "") not in ("0cm", "0in", "0pt", "0"):
        out.append("entries.date_and_location_width must be 0 (one column)")
    if _get(d, "sections", "show_time_spans_in"):
        out.append("sections.show_time_spans_in must be empty")
    for path, value in _leaves(_get(d, "templates") or {}, ("templates",)):
        if isinstance(value, str) and ("--" in value or any(c in value for c in EM_DASHES + EN_DASH)):
            out.append(f"design.{'.'.join(path)} carries a dash Typst renders as an en- or em-dash")
    months = _get(template, "locale", "month_abbreviations") or []
    if len(months) != 12 or any(not (isinstance(m, str) and len(m) == 3) for m in months):
        out.append("locale.month_abbreviations must be twelve three-letter months")
    return out


def load_checked_template(spec: str | Path | None, density: str | None) -> tuple[Path, dict[str, Any]]:
    """The template file for --template with --density applied, refused when it breaks an ATS invariant."""
    path = resolve_template(spec)
    template = apply_density(load_template(path), density)
    problems = template_problems(template)
    if problems:
        raise UsageError(f"template {path} breaks the ATS rules: " + "; ".join(problems))
    return path, template


HOUSE_DENSITY = "house"
BASE_DENSITY = "one-page"          # what the general base's PDFs are rendered at (resumes/bases/general/README.md)
# The only design keys a density may set (margins, line spacing, body size): never an ATS-bearing key.
DENSITY_KEYS = frozenset({("page", "top_margin"), ("page", "bottom_margin"), ("page", "left_margin"),
                          ("page", "right_margin"), ("typography", "line_spacing"),
                          ("typography", "font_size", "body")})
MIN_BODY_PT = 10.0


def density_names(template: dict[str, Any] | None = None) -> list[str]:
    template = template if template is not None else load_template()
    return [HOUSE_DENSITY] + sorted((template.get("densities") or {}).keys())


def _leaves(node: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _leaves(v, path + (str(k),))
    else:
        yield path, node


def apply_density(template: dict[str, Any], density: str | None) -> dict[str, Any]:
    """The template with the named density overlaid on its design (house or None: the template as is)."""
    if density in (None, HOUSE_DENSITY):
        return template
    overlays = template.get("densities") or {}
    if density not in overlays:
        raise UsageError(f"unknown density {density!r}; choose from {', '.join(density_names(template))}")
    out = copy.deepcopy(template)
    for path, value in _leaves(overlays[density]):
        if path not in DENSITY_KEYS:
            raise UsageError(f"density {density!r} sets design.{'.'.join(path)}: a density may change only the "
                             "page margins, typography.line_spacing and typography.font_size.body")
        if path == ("typography", "font_size", "body") and _to_points(value) < MIN_BODY_PT:
            raise UsageError(f"density {density!r} sets the body to {value}; the floor is {MIN_BODY_PT:g}pt")
        node = out["design"]
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = value
    return out


# --------------------------------------------------------------------------- evidence
def parse_evidence(text: str) -> tuple[str, list[str]]:
    """Split a claim into (text without its trailing evidence comment, evidence ids)."""
    m = EV_TRAILING_RE.search(text)
    if not m:
        return text.strip(), []
    ids = [i for i in re.split(r"[,\s]+", m.group("ids")) if i]
    return text[: m.start()].rstrip(), ids


def load_evidence_ids(path: Path) -> set[str]:
    """Evidence ids from profile/evidence.yaml. Accepts `{evidence: {id: {...}}}`, a plain
    mapping of ids, or a list of `{id: ...}` items (or bare id strings)."""
    data = load_yaml(path)
    if isinstance(data, dict) and isinstance(data.get("evidence"), (dict, list)):
        data = data["evidence"]
    ids: set[str] = set()
    if isinstance(data, dict):
        meta_keys = {"schema_version", "updated", "created", "version", "title", "notes"}
        ids = {str(k) for k in data if k not in meta_keys}
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("id"):
                ids.add(str(item["id"]))
            elif isinstance(item, str):
                ids.add(item)
    return ids


def iter_claims(cv: dict[str, Any]) -> Iterator[Claim]:
    """Every rendered claim that must carry evidence, with its YAML path."""
    if isinstance(cv.get("headline"), str):
        yield Claim("cv.headline", cv["headline"], "headline")
    sections = cv.get("sections") or {}
    if not isinstance(sections, dict):
        return
    for title, entries in sections.items():
        if not isinstance(entries, list):
            continue
        for i, entry in enumerate(entries):
            base = f"cv.sections.{title}[{i}]"
            if isinstance(entry, str):
                yield Claim(base, entry, "text")
                continue
            if not isinstance(entry, dict):
                continue
            for key in ("bullet", "number", "reversed_number"):
                if isinstance(entry.get(key), str):
                    yield Claim(f"{base}.{key}", entry[key], "bullet")
            if isinstance(entry.get("details"), str):
                yield Claim(f"{base}.details", entry["details"], "details")
            if isinstance(entry.get("summary"), str):
                yield Claim(f"{base}.summary", entry["summary"], "summary")
            highlights = entry.get("highlights")
            if isinstance(highlights, list):
                for j, item in enumerate(highlights):
                    if isinstance(item, str):
                        yield Claim(f"{base}.highlights[{j}]", item, "highlight")


def _ruled_block_problems(block: Any) -> list[str]:
    """the owner's ruling block on a long-bullet rule: by owner, a quoted "on" date, a basis."""
    if not isinstance(block, dict):
        return [f'needs a `ruled: {{by: {OWNER_TOKEN}, "on": YYYY-MM-DD, basis}}` block (only the owner rules; '
                "the corpus-keeper records)"]
    out: list[str] = []
    if any(isinstance(k, bool) for k in block):
        out.append('ruled has a boolean key: quote "on" (bare on is true in YAML 1.1)')
    if str(block.get("by") or "").strip().lower() != OWNER_TOKEN:
        out.append(f"ruled.by is {block.get('by')!r}: only the owner rules")
    on = block.get("on")
    if not (isinstance(on, date) or RULED_DATE_RE.match(str(on or ""))):
        out.append(f"ruled.on {on!r} is not a date (YYYY-MM-DD)")
    if not str(block.get("basis") or "").strip():
        out.append("ruled.basis is empty (where the owner ruled: session, chat, intake note)")
    return out


def _long_bullet_item_problems(item: Any) -> list[str]:
    if not isinstance(item, dict):
        return ["must be a mapping {evidence: [ids], max_lines: 3 or 4, label}"]
    out: list[str] = []
    unknown = sorted(str(k) for k in item if k not in LONG_BULLET_KEYS)
    if unknown:
        out.append(f"unknown key(s) {', '.join(unknown)} (allowed: {', '.join(sorted(LONG_BULLET_KEYS))})")
    ev = item.get("evidence")
    if not isinstance(ev, list) or not ev:
        out.append("evidence must be a non-empty list of the bullet's evidence ids")
    elif not all(isinstance(i, str) and EV_ID_RE.match(i) for i in ev):
        out.append(f"evidence {ev!r} holds a malformed id")
    elif len(set(ev)) != len(ev):
        out.append(f"evidence {ev!r} repeats an id")
    lines = item.get("max_lines")
    if isinstance(lines, bool) or not isinstance(lines, int) or not MAX_CLAIM_LINES < lines <= MAX_RULED_LINES:
        out.append(f"max_lines {lines!r} must be a whole number from {MAX_CLAIM_LINES + 1} to {MAX_RULED_LINES}")
    if "label" in item and not isinstance(item["label"], str):
        out.append(f"label {item['label']!r} must be text")
    return out


def _rule_sources(facts: Any, rulings: Any = None) -> list[RuleSource]:
    return [RuleSource("facts.yaml", facts), RuleSource("rulings.yaml", rulings, needs_source=True)]


def _ruled_entries(sources: list[RuleSource], key: str, code: str, shape: str
                   ) -> Iterator[tuple[str, dict[str, Any], list[Any], list[Issue]]]:
    """(where, entry, items, problems) for every `rules:` entry carrying `key`, in every source. The problems
    cover the owner's ruling block, the named source (rulings.yaml) and the list itself; items are checked by
    the caller."""
    for src in sources:
        rules = src.data.get("rules") if isinstance(src.data, dict) else None
        for i, rule in enumerate(rules if isinstance(rules, list) else []):
            if not isinstance(rule, dict) or key not in rule:
                continue
            where = f"{src.name} rules[{i}]"
            found = [Issue(code, where, p) for p in _ruled_block_problems(rule.get("ruled"))]
            if src.needs_source and not str(rule.get("source") or "").strip():
                found.append(Issue(code, where, "source is empty: name the record that holds the owner's words"))
            items = rule[key]
            if not isinstance(items, list) or not items:
                found.append(Issue(code, f"{where}.{key}", f"{key} must be a non-empty list of {shape} items"))
                items = []
            yield where, rule, items, found


def long_bullet_rules(facts: Any, rulings: Any = None) -> tuple[dict[frozenset[str], LongBullet], list[Issue]]:
    """The long-bullet allowances in a parsed facts.yaml and rulings.yaml (`rules:` entries carrying
    `long_bullets:`), keyed by evidence-id set, and the problems as `long-bullet-rule` errors. A malformed entry
    allows nothing."""
    allowed: dict[frozenset[str], LongBullet] = {}
    listed: list[tuple[str, LongBullet]] = []
    problems: list[Issue] = []
    for where, rule, items, found in _ruled_entries(_rule_sources(facts, rulings), "long_bullets",
                                                    "long-bullet-rule", "{evidence, max_lines}"):
        for j, item in enumerate(items):
            found += [Issue("long-bullet-rule", f"{where}.long_bullets[{j}]", p)
                      for p in _long_bullet_item_problems(item)]
        if found:
            problems += found
            continue
        on = rule["ruled"]["on"]
        for j, item in enumerate(items):
            path = f"{where}.long_bullets[{j}]"
            label = f", {item['label']}" if item.get("label") else ""
            listed.append((path, LongBullet(frozenset(item["evidence"]), item["max_lines"],
                                            f"the owner's ruling of {on} ({path}{label})")))
    for path, entry in listed:
        twins = [other for p, other in listed if other.evidence == entry.evidence and p != path]
        if twins:                     # ambiguous: no listing of this set applies
            problems.append(Issue("long-bullet-rule", path, f"evidence set {sorted(entry.evidence)} is listed "
                                  f"twice (also {twins[0].ruling}); keep one entry"))
        else:
            allowed[entry.evidence] = entry
    return allowed, problems


@dataclass(frozen=True)
class LinkRuling:
    """One link the owner ruled may show a short label instead of its URL (rules: labeled_links)."""
    url: str
    ruling: str


def _link_item_problems(item: Any) -> list[str]:
    if not isinstance(item, dict):
        return ["must be a mapping {url, label}"]
    out: list[str] = []
    unknown = sorted(str(k) for k in item if k not in LINK_RULE_KEYS)
    if unknown:
        out.append(f"unknown key(s) {', '.join(unknown)} (allowed: {', '.join(sorted(LINK_RULE_KEYS))})")
    url = item.get("url")
    if not (isinstance(url, str) and re.fullmatch(r"https?://\S+", url.strip())):
        out.append(f"url {url!r} must be one http(s) address")
    if "label" in item and not isinstance(item["label"], str):
        out.append(f"label {item['label']!r} must be text")
    return out


def link_rules(facts: Any, rulings: Any = None) -> tuple[dict[str, LinkRuling], list[Issue]]:
    """The labeled-link allowances (`rules:` entries carrying `labeled_links:`) keyed by normalized URL, and the
    problems as `link-rule` errors. A malformed entry allows nothing. facts.yaml is the record: a URL it lists
    is allowed by its entry, and the same URL in rulings.yaml is a `link-rule-duplicate` warning (delete the
    rulings.yaml entry; the ruling keeps one record) that allows nothing more."""
    allowed: dict[str, LinkRuling] = {}
    recorded: set[str] = set()                      # URLs whose ruling facts.yaml (the record) holds
    problems: list[Issue] = []
    for where, rule, items, found in _ruled_entries(_rule_sources(facts, rulings), "labeled_links", "link-rule",
                                                    "{url, label}"):
        for j, item in enumerate(items):
            found += [Issue("link-rule", f"{where}.labeled_links[{j}]", p) for p in _link_item_problems(item)]
        if found:
            problems += found
            continue
        for j, item in enumerate(items):
            label = f", {item['label']}" if item.get("label") else ""
            key = _norm_url(item["url"])
            if key in recorded and not where.startswith("facts.yaml"):
                problems.append(Issue("link-rule-duplicate", f"{where}.labeled_links[{j}]",
                                      f"{item['url']} is also recorded in profile/facts.yaml ({allowed[key].ruling}); "
                                      "facts.yaml is the record: delete this rulings.yaml entry", "warning"))
                continue
            if where.startswith("facts.yaml"):
                recorded.add(key)
            allowed[key] = LinkRuling(
                item["url"], f"the owner's ruling of {rule['ruled']['on']} ({where}.labeled_links[{j}]{label})")
    return allowed, problems


def load_facts(path: Path | str | None) -> Any:
    """Parsed facts.yaml for the long-bullet rulings, or None when there is no such file."""
    return load_yaml(Path(path)) if path and Path(path).is_file() else None


def load_rulings(path: Path | str | None) -> Any:
    """Parsed resumes/templates/rulings.yaml (the owner's rulings on how resumes render), or None when absent."""
    return load_yaml(Path(path)) if path and Path(path).is_file() else None


# --------------------------------------------------------------------------- text helpers
def strip_comments(text: str) -> str:
    return COMMENT_RE.sub("", text).rstrip()


def plain_text(text: str) -> str:
    """Visible text of a markdown claim (links -> their text, emphasis markers dropped)."""
    text = MD_LINK_RE.sub(lambda m: m.group(1), strip_comments(text))
    return re.sub(r"\*\*|\*|`", "", text).strip()


def _to_points(value: Any) -> float:
    s = str(value).strip()
    m = re.fullmatch(r"([\d.]+)\s*(pt|in|cm|mm|em)?", s)
    if not m:
        raise UsageError(f"cannot read a length from {value!r}")
    num, unit = float(m.group(1)), m.group(2) or "pt"
    return num * {"pt": 1.0, "in": 72.0, "cm": 72 / 2.54, "mm": 72 / 25.4, "em": 10.0}[unit]


def formula_chars_per_line(template: dict[str, Any]) -> int:
    """Characters that fit on one bullet line of the template's body text, from the page width and an average
    glyph advance: the fallback for a layout nobody has measured yet."""
    d = template["design"]
    width = PAGE_WIDTH_IN.get(d["page"]["size"], 8.5) * 72
    width -= _to_points(d["page"]["left_margin"]) + _to_points(d["page"]["right_margin"])
    width -= BULLET_INDENT_PT
    body = _to_points(d["typography"]["font_size"]["body"])
    return max(40, int(width / (body * AVG_CHAR_EM)))


def layout_key(template: dict[str, Any]) -> dict[str, Any]:
    """What sets a line's width: page size, side margins (pt), body size (pt) and font, after the density."""
    d = template["design"]
    return {"page": d["page"]["size"],
            "left_margin_pt": round(_to_points(d["page"]["left_margin"]), 2),
            "right_margin_pt": round(_to_points(d["page"]["right_margin"]), 2),
            "body_pt": round(_to_points(d["typography"]["font_size"]["body"]), 2),
            "font": d["typography"]["font_family"]}


def load_line_widths(path: Path | None = None) -> list[dict[str, Any]]:
    source = path or (LINE_WIDTHS if LINE_WIDTHS.is_file() else LINE_WIDTHS_READ)
    data = load_yaml(source) if Path(source).is_file() else None
    widths = data.get("widths") if isinstance(data, dict) else None
    return [w for w in widths if isinstance(w, dict)] if isinstance(widths, list) else []


def measured_chars_per_line(template: dict[str, Any], path: Path | None = None) -> int | None:
    """The measured characters per line for this template's layout (line-widths.yaml), or None."""
    key = layout_key(template)
    for entry in load_line_widths(path):
        if entry.get("layout") == key and isinstance(entry.get("chars_per_line"), int):
            return entry["chars_per_line"]
    return None


def estimate_chars_per_line(template: dict[str, Any]) -> int:
    """Characters one line holds in this template and density: measured when line-widths.yaml has the layout,
    else the font formula."""
    return measured_chars_per_line(template) or formula_chars_per_line(template)


GLYPH_FIT = 1.01          # line width in em = (page width - side margins - bullet indent) / body size x this, fitted
                          # on 150 rendered bullets (classic, one-page): exact on all 150 and on a 369-char case
                          # from 1.01 to 1.015; 1.02 misses that case


@functools.lru_cache(maxsize=4)
def glyph_widths(family: str) -> tuple[dict[str, float], dict[str, float]] | None:
    """(regular, bold) advance widths in em per character for a font RenderCV bundles (rendercv_fonts), read with
    fontTools; None when either is missing (the character estimate then runs alone)."""
    try:
        import rendercv_fonts
        from fontTools.ttLib import TTFont
    except ImportError:
        return None
    stem = family.replace(" ", "")
    root = Path(rendercv_fonts.__file__).parent
    out = []
    for style in ("Regular", "Bold"):
        hit = next(root.rglob(f"{stem}-{style}.ttf"), None)
        if hit is None:
            return None
        font = TTFont(str(hit), lazy=True)
        cmap, metrics, upm = font.getBestCmap(), font["hmtx"].metrics, font["head"].unitsPerEm
        out.append({chr(cp): metrics[g][0] / upm for cp, g in cmap.items()})
    return out[0], out[1]


def glyph_lines(text: str, widths: tuple[dict[str, float], dict[str, float]], line_em: float) -> int:
    """Greedy word wrap by the font's advance widths: **bold** runs measured in bold, an unknown glyph as 0.6 em."""
    regular, bold = widths
    words: list[float] = []
    on = False
    for part in re.split(r"(\*\*)", MD_LINK_RE.sub(lambda m: m.group(1), strip_comments(text))):   # links first
        if part == "**":
            on = not on
            continue
        table = bold if on else regular
        words += [sum(table.get(ch, 0.6) for ch in w) for w in plain_text(part).split()]
    space, lines, cur = regular.get(" ", 0.2), 1, 0.0
    for w in words:
        need = w if cur == 0 else cur + space + w
        if need > line_em and cur > 0:
            lines, cur = lines + 1, w
        else:
            cur = need
    return lines


def line_estimate(template: dict[str, Any]):
    """The line count lint uses: the larger of the character estimate (line-widths.yaml) and the glyph-width
    estimate, so a line of wide glyphs is never under-counted. Falls back to the character estimate."""
    cpl = estimate_chars_per_line(template)
    d = template["design"]
    widths = glyph_widths(str(d["typography"]["font_family"]))
    width = PAGE_WIDTH_IN.get(d["page"]["size"], 8.5) * 72
    width -= _to_points(d["page"]["left_margin"]) + _to_points(d["page"]["right_margin"]) + BULLET_INDENT_PT
    line_em = width / _to_points(d["typography"]["font_size"]["body"]) * GLYPH_FIT
    if widths is None:
        return lambda text: estimate_lines(text, cpl)
    return lambda text: max(estimate_lines(text, cpl), glyph_lines(text, widths, line_em))


def estimate_lines(text: str, chars_per_line: int) -> int:
    """Greedy word-wrap estimate of rendered lines."""
    lines, cur = 1, 0
    for word in plain_text(text).split():
        need = len(word) if cur == 0 else cur + 1 + len(word)
        if need > chars_per_line and cur > 0:
            lines, cur = lines + 1, len(word)
        else:
            cur = need
    return lines


def _norm_url(url: str) -> str:
    u = re.sub(r"^(https?://)?(www\.)?", "", url.strip().lower())
    return u.rstrip("/")


# --------------------------------------------------------------------------- lint
def _is_year_only(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, int) or bool(re.fullmatch(r"\d{4}", str(value).strip()))


def _check_date(value: Any, path: str, report: LintReport, year_only_ok: bool = False) -> None:
    """YYYY-MM, YYYY-MM-DD or present; YYYY only when the whole entry is year-only (year_only_ok):
    a closed range such as 2017 - 2021 (the conservative form while a month is unconfirmed) or a single
    date. A year mixed with a month or with present in one entry is a date-precision error."""
    if value is None:
        return
    if isinstance(value, (date, datetime)):
        return
    s = str(value).strip()
    if _is_year_only(value):
        if not year_only_ok:
            report.error("date-precision", path, f"year-only date {s} next to a month date or present: "
                         "use YYYY-MM on both ends, or YYYY on both ends of a closed range")
        return
    if s.lower() == "present" or re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", s):
        return
    report.error("bad-date", path, f"unparseable date {s!r}: use YYYY-MM, YYYY-MM-DD, YYYY or present")


def _check_chars(text: str, path: str, report: LintReport, links: dict[str, LinkRuling] | None = None) -> None:
    """Character-level rules for every rendered string. `links`: URLs the owner ruled may carry a short label."""
    bad = [c for c in EM_DASHES if c in text]
    if cfg.get("style.no_em_dash", False) and (bad or "---" in text):
        report.error("em-dash", path, "em-dash found (no em-dashes anywhere; use a comma, colon "
                     "or parentheses)")
    elif "--" in text:
        report.warn("en-dash-in-prose", path, "'--' renders as an en-dash; en-dashes belong in "
                    "date ranges only")
    if EN_DASH in text:
        report.warn("en-dash-in-prose", path, "en-dash in prose; the house style is a hyphen "
                    "(dates) or a comma")
    if "\t" in text or "|" in text:
        report.error("table-content", path, "tabs or '|' pipes read as table columns to ATS "
                     "parsers; write a sentence or a comma list")
    if "  " in text:
        report.error("double-space", path, "double space")
    if RAW_TYPST_RE.search(text):
        report.error("raw-typst", path, "raw Typst command ('#name') in content; layout belongs "
                     "to the template (a '#' URL fragment also breaks the render)")
    for m in MD_LINK_RE.finditer(text):
        if _norm_url(m.group(1)) != _norm_url(m.group(2)):
            allow = (links or {}).get(_norm_url(m.group(2)))
            if allow:
                report.note("labeled-link-allowed", path, f"link text '{m.group(1)}' labels {m.group(2)}, "
                            f"allowed by {allow.ruling}")
            else:
                report.error("hidden-link", path, f"link text '{m.group(1)}' hides its URL; ATS read "
                             "visible text only, so show the URL itself (or have the corpus-keeper record the owner's "
                             "ruling as labeled_links in profile/facts.yaml rules)")


def _walk_strings(node: Any, path: str) -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in NON_PROSE_KEYS or k == "evidence":
                continue
            yield from _walk_strings(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_strings(v, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def _walk_rendered(node: Any, path: str) -> Iterator[tuple[str, str]]:
    """Every string the renderer can print (contacts and URLs included; `evidence` sidecars excluded)."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k != "evidence":
                yield from _walk_rendered(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_rendered(v, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def base_names(bases_dir: Path | None = None) -> set[str]:
    """The bases a variant may copy: folders under resumes/bases/ that hold a resume.yaml."""
    root = bases_dir or BASES_DIR
    return {p.name for p in root.iterdir() if (p / "resume.yaml").is_file()} if root.is_dir() else set()


def check_sidecar(sidecar: Any, report: LintReport, bases: set[str] | None = None) -> None:
    """The sidecar contract: lens (a kebab slug or null), base_of (null for a base, else a base under
    resumes/bases/, `general` in the one-base model), job (null or jobs/<id>.md), doc. Nothing else."""
    if sidecar is None:
        return
    if not isinstance(sidecar, dict):
        report.error("bad-sidecar", "sidecar", "sidecar must be a mapping {lens, base_of, job}")
        return
    unknown = sorted(str(k) for k in sidecar if k not in SIDECAR_KEYS)
    if unknown:
        report.error("bad-sidecar", "sidecar", f"unknown key(s) {', '.join(unknown)} (allowed: "
                     f"{', '.join(SIDECAR_KEYS)})")
    doc = sidecar.get("doc")
    if doc is not None and doc != "cv":      #: the one document kind besides a resume
        report.error("bad-sidecar", "sidecar.doc", f"doc {doc!r} must be 'cv' or absent (a resume)")
    for key in ("lens", "base_of"):
        value = sidecar.get(key)
        if value is not None and not (isinstance(value, str) and SLUG_RE.match(value)):
            report.error("bad-sidecar", f"sidecar.{key}", f"{key} {value!r} must be a kebab-case slug or null")
    base_of = sidecar.get("base_of")
    if isinstance(base_of, str) and SLUG_RE.match(base_of):
        known = base_names() if bases is None else bases
        if base_of not in known:
            report.error("unknown-base", "sidecar.base_of", f"base_of {base_of!r} names no base under "
                         f"resumes/bases/ (bases: {', '.join(sorted(known)) or 'none'}); every variant starts "
                         f"from the general base: base_of: {GENERAL_BASE}")
    job = sidecar.get("job")
    if job is not None and not (isinstance(job, str) and JOB_REF_RE.match(job)):
        report.error("bad-sidecar", "sidecar.job", f"job {job!r} must be jobs/<id>.md or null")


def lint_resume(data: Any, *, known_evidence: set[str] | None = None,
                chars_per_line: int | None = None, held: list[str] | None = None,
                facts: Any = None, rulings: Any = None, line_model: Any = None) -> LintReport:
    """Pre-render lint. Errors block the render (unless --force); warnings and notes do not.
    `facts` (profile/facts.yaml) and `rulings` (resumes/templates/rulings.yaml) are parsed files whose `rules:`
    may carry the owner's long-bullet and labeled-link rulings (None: no ruling, every claim keeps the
    MAX_CLAIM_LINES limit and every link must show its URL).
    `held` defaults to the held strings in profile/PROFILE-HOLDS.md. Every rendered string (titles, bullets,
    skills, name, contacts, URLs) is checked with profile_titles.held_hits, so Markdown emphasis, spacing or
    dash style cannot hide a held string (held-title for positions and titles, held-text elsewhere)."""
    report = LintReport()
    long_ok, rule_problems = long_bullet_rules(facts, rulings)
    links_ok, link_problems = link_rules(facts, rulings)
    report.errors += rule_problems + [i for i in link_problems if i.severity == "error"]
    report.warnings += [i for i in link_problems if i.severity != "error"]
    if held is None:
        held = pt.load_held()
        if not held:
            report.warn("no-holds", "profile/PROFILE-HOLDS.md", "no held strings loaded; the held-title "
                        "check did not run (missing is not empty)")
    if not isinstance(data, dict) or not isinstance(data.get("cv"), dict):
        report.error("no-cv", "cv", "the file needs a top-level `cv:` mapping")
        return report
    for key in data:
        if key in ("design", "locale", "settings", "rendercv_settings"):
            report.error("design-override", key, "design, locale and settings come from the template "
                         "(--template); remove this block")
        elif key not in ("cv", "sidecar"):
            report.error("unknown-key", key, "only `cv` and `sidecar` are allowed at top level")
    check_sidecar(data.get("sidecar"), report)
    if chars_per_line is None:
        chars_per_line = estimate_chars_per_line(load_template())
    cv = data["cv"]
    if not cv.get("name"):
        report.error("missing-name", "cv.name", "the resume has no name")
    if not cv.get("email"):
        report.error("missing-email", "cv.email", "no contact email; every resume must carry "
                     "the application email")
    if cv.get("photo"):
        report.error("photo", "cv.photo", "no photos on US resumes (ATS and bias)")
    sections = cv.get("sections")
    if not isinstance(sections, dict) or not sections:
        report.error("no-sections", "cv.sections", "no sections")
        return report

    titles = list(sections)
    sidecar = data.get("sidecar") if isinstance(data.get("sidecar"), dict) else {}   # a malformed one is check_sidecar's error
    is_cv = str(sidecar.get("doc") or "").lower() == "cv"
    allowed, order = (CV_SECTIONS, CV_ORDER) if is_cv else (CANONICAL_SECTIONS, HOUSE_ORDER)
    for title in titles:
        if title not in allowed:
            hint = SECTION_ALIASES.get(str(title).strip().lower())
            if not hint:
                low = str(title).strip().lower()
                hint = next((c for c in CANONICAL_SECTIONS if c.lower() == low), None)
            report.error("noncanonical-heading", f"cv.sections.{title}",
                         f"heading '{title}' is not a standard ATS heading"
                         + (f"; use '{hint}'" if hint else f"; use one of {', '.join(HOUSE_ORDER)}"))
    ranks = [order.index(t) for t in titles if t in allowed]
    # A years- or seniority-sensitive variant may move Education after Experience; the rest keeps the house
    # order (the base is unchanged).
    rest = [order.index(t) for t in titles if t in allowed and t != "Education"]
    education_late = ("Education" in titles and "Experience" in titles and rest == sorted(rest)
            and titles.index("Education") > titles.index("Experience"))
    if ranks != sorted(ranks) and not education_late:
        report.warn("section-order", "cv.sections", "sections leave the house order ("
                    + ", ".join(order) + ")")

    for path, text in _walk_strings(cv, "cv"):
        _check_chars(strip_comments(text), path, report, links_ok)
    titles_checked = {f"cv.sections.{t}[{i}].{k}" for t, entries in sections.items() if isinstance(entries, list)
                      for i, e in enumerate(entries) if isinstance(e, dict)
                      for k in ("position", "title") if isinstance(e.get(k), str)}
    for path, text in _walk_rendered(cv, "cv"):
        found = [] if path in titles_checked else pt.held_found(strip_comments(text), held)
        if found:
            notes = ", ".join(pt.held_note(h, how) for h, how in found)
            report.error("held-text", path, f"held text {notes} ({pt.held_sources(h for h, _ in found)}): use the "
                         "conservative form in PROFILE.md or drop it (plain: emphasis or spacing split it; "
                         "loose: case, spacing or dash style differ)")

    for title, entries in sections.items():
        base = f"cv.sections.{title}"
        if not isinstance(entries, list):
            report.error("bad-section", base, "a section must be a list of entries")
            continue
        for i, entry in enumerate(entries):
            epath = f"{base}[{i}]"
            if not isinstance(entry, (str, dict)):
                report.error("bad-entry", epath, "an entry must be text or a mapping")
                continue
            if not isinstance(entry, dict):
                continue
            highlights = entry.get("highlights")
            if highlights is not None and not isinstance(highlights, list):
                report.error("bad-entry", f"{epath}.highlights", "highlights must be a list")
            for j, item in enumerate(highlights or []):
                if not isinstance(item, str):
                    report.error("not-a-string", f"{epath}.highlights[{j}]",
                                 "this bullet parsed as a mapping, not text: an unquoted "
                                 "'ev: id' (colon + space) splits it; quote the bullet or write "
                                 "'ev:id'")
            if isinstance(entry.get("evidence"), (str, list)):
                ids = entry["evidence"] if isinstance(entry["evidence"], list) else [entry["evidence"]]
                _check_ids([str(x) for x in ids], f"{epath}.evidence", report, known_evidence)
            kind = ("experience" if {"company", "position"} <= set(entry)
                    else "education" if {"institution", "area"} <= set(entry) else "")
            if kind:
                if not entry.get("location"):
                    report.error("missing-location", f"{epath}.location",
                                 f"{kind} entries need a location (use 'Remote' if remote)")
                if not (entry.get("start_date") or entry.get("date")):
                    report.error("missing-date", epath, f"{kind} entries need dates")
            for key in ("position", "title"):
                if isinstance(entry.get(key), str):
                    _check_title(entry[key], f"{epath}.{key}", report, held, markers=key == "position")
            dated = [entry[k] for k in ("start_date", "end_date", "date") if entry.get(k) is not None]
            closed = entry.get("start_date") is None or entry.get("end_date") is not None
            year_only_ok = bool(dated) and closed and all(_is_year_only(v) for v in dated)
            for key in ("start_date", "end_date", "date"):
                if key in entry:
                    _check_date(entry[key], f"{epath}.{key}", report, year_only_ok)

    seen: dict[str, str] = {}
    for claim in iter_claims(cv):
        body, ids = parse_evidence(claim.text)
        if EV_ANY_RE.search(body):
            report.error("evidence-not-trailing", claim.path, "the <!-- ev:... --> comment "
                         "must be the last thing in the bullet")
        elif not ids:
            report.error("missing-evidence", claim.path, "no trailing <!-- ev:ID --> comment; "
                         "every claim cites profile/evidence.yaml")
        _check_ids(ids, claim.path, report, known_evidence)
        other = COMMENT_RE.findall(body)
        if other and not EV_ANY_RE.search(body):
            report.warn("stray-comment", claim.path, "HTML comment in a claim (stripped)")
        if claim.kind == "highlight" and " - " in body:
            report.error("nested-bullet", claim.path, "' - ' inside a bullet becomes a nested "
                         "sub-bullet in RenderCV; use a comma, colon or parentheses")
        lines = max(estimate_lines(body, chars_per_line), line_model(body) if line_model else 0)
        if lines > MAX_CLAIM_LINES:
            allow = long_ok.get(frozenset(ids)) if claim.kind in LONG_BULLET_KINDS else None
            if allow and lines <= allow.max_lines:
                report.note("long-bullet-allowed", claim.path, f"about {lines} lines "
                            f"({len(plain_text(body))} chars), allowed up to {allow.max_lines} by {allow.ruling}")
            else:
                limit = (f"limit {allow.max_lines} lines by {allow.ruling}" if allow
                         else f"limit {MAX_CLAIM_LINES} lines")
                report.error("bullet-too-long", claim.path, f"about {lines} lines "
                             f"({len(plain_text(body))} chars; {limit} of ~{chars_per_line} chars)")
        key = plain_text(body).lower()
        if claim.kind == "highlight" and key in seen:
            report.warn("duplicate-bullet", claim.path, f"same text as {seen[key]}")
        seen.setdefault(key, claim.path)
    report.evidence_ids = sorted({i for c in iter_claims(cv) for i in parse_evidence(c.text)[1]}
                                 | _entry_evidence(cv))
    return report


def _check_title(text: str, path: str, report: LintReport, held: list[str], markers: bool) -> None:
    """A position comes from facts.yaml `title_render`, never the `title` record: no conflict
    note, no held variant. Publication titles get the held check only (their words are free text)."""
    plain = strip_comments(text)
    if markers:
        found = [m.group(0) for m in pt.MARKER_RE.finditer(plain)]
        if found:
            report.error("title-conflict-marker", path, f"conflict note {found} in a title: use the facts.yaml "
                         "`title_render` (the PROFILE.md wording), never the `title` record")
    hits = pt.held_found(plain, held)
    if hits:
        notes = ", ".join(pt.held_note(h, how) for h, how in hits)
        report.error("held-title", path, f"held text {notes} ({pt.held_sources(h for h, _ in hits)}) in a title: use "
                     "the conservative form in PROFILE.md")


def _check_ids(ids: list[str], path: str, report: LintReport, known: set[str] | None) -> None:
    for ev in ids:
        if not EV_ID_RE.match(ev):
            report.error("bad-evidence-id", path, f"malformed evidence id {ev!r}")
        elif known is not None and ev not in known:
            report.error("unknown-evidence", path, f"evidence id {ev!r} is not in the evidence file")


def _entry_evidence(cv: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for entries in (cv.get("sections") or {}).values():
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and entry.get("evidence"):
                ev = entry["evidence"]
                out |= {str(x) for x in (ev if isinstance(ev, list) else [ev])}
    return out


# --------------------------------------------------------------------------- preparation
def strip_for_render(data: dict[str, Any]) -> dict[str, Any]:
    """A copy with every sidecar gone: no `sidecar`, no entry `evidence`, no HTML comments,
    and no design/locale/settings overrides (the template supplies them)."""
    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: clean(v) for k, v in node.items()}
        if isinstance(node, list):
            return [clean(v) for v in node]
        if isinstance(node, str):
            return strip_comments(node)
        return node

    cv = clean(copy.deepcopy(data.get("cv") or {}))
    for entries in (cv.get("sections") or {}).values():
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict):
                entry.pop("evidence", None)
    return {"cv": cv}


def box_hyphenated(text: str) -> str:
    """Wrap hyphenated compounds in a Typst box so a line never breaks at their hyphen
    (pdftotext rejoins 'retrieval-' + 'augmented' as 'retrievalaugmented'). URLs, emails and
    domains are left untouched."""
    out, last = [], 0
    for m in URLISH_RE.finditer(text):
        out.append(COMPOUND_RE.sub(r"#box[\1]", text[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(COMPOUND_RE.sub(r"#box[\1]", text[last:]))
    return "".join(out)


def _box_entries(cv: dict[str, Any]) -> dict[str, Any]:
    cv = copy.deepcopy(cv)
    if isinstance(cv.get("headline"), str):
        cv["headline"] = box_hyphenated(cv["headline"])
    for entries in (cv.get("sections") or {}).values():
        if not isinstance(entries, list):
            continue
        for i, entry in enumerate(entries):
            if isinstance(entry, str):
                entries[i] = box_hyphenated(entry)
            elif isinstance(entry, dict):
                for key, value in entry.items():
                    if key not in BOX_KEYS:
                        continue
                    if isinstance(value, str):
                        entry[key] = box_hyphenated(value)
                    elif key == "highlights" and isinstance(value, list):
                        entry[key] = [box_hyphenated(v) if isinstance(v, str) else v for v in value]
    return cv


# A template that puts a field inside a Typst command (`#text(..., [POSITION])`, the classic and inline designs) hands
# RenderCV text it never escapes, and RenderCV's command match `#name(...)` stops at the first ')' (a position such
# as "(part-time)" gave "unclosed delimiter"; "demo_app" opened an emphasis). Those fields are made
# Typst-safe here: markup characters escaped, parentheses written as Typst symbols (the PDF text keeps "(" and ")").
TEMPLATE_FIELDS = {"POSITION": ("position",), "NAME": ("name",), "DEGREE_WITH_AREA": ("degree", "area"),
                   "TITLE": ("title",), "COMPANY": ("company",), "INSTITUTION": ("institution",)}
TYPST_MARKUP_ESCAPES = {"\\": "\\\\", "_": "\\_", "*": "\\*", "#": "\\#", "$": "\\$", "@": "\\@", "<": "\\<",
                        ">": "\\>", "[": "\\[", "]": "\\]", "`": "\\`", "~": "\\~"}


def fields_in_typst_commands(template: dict[str, Any]) -> set[str]:
    """Entry keys whose placeholder sits inside a Typst command's parentheses in the template's main columns."""
    keys: set[str] = set()
    templates = ((template.get("design") or {}).get("templates") or {})
    for spec in templates.values() if isinstance(templates, dict) else []:
        layout = spec.get("main_column") if isinstance(spec, dict) else None
        for call in re.findall(r"#[A-Za-z][\w.]*\(([^)]*)\)", str(layout or "")):
            for placeholder, fields in TEMPLATE_FIELDS.items():
                if re.search(rf"\b{placeholder}\b", call):
                    keys.update(fields)
    return keys


def typst_command_safe(text: str) -> str:
    """Text for a placeholder inside a Typst command: markup escaped, ( and ) as #sym.paren.l; and #sym.paren.r;."""
    out = "".join(TYPST_MARKUP_ESCAPES.get(ch, ch) for ch in text)
    return out.replace("(", "#sym.paren.l;").replace(")", "#sym.paren.r;")


def _command_safe_entries(cv: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    if not keys:
        return cv
    cv = copy.deepcopy(cv)
    for entries in (cv.get("sections") or {}).values():
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict):
                for key in keys & set(entry):
                    if isinstance(entry[key], str):
                        entry[key] = typst_command_safe(entry[key])
    return cv


def build_rendercv_input(clean: dict[str, Any], template: dict[str, Any],
                         title: str | None = None) -> dict[str, Any]:
    settings = copy.deepcopy(template["settings"])
    if title:
        settings["pdf_title"] = title
    cv = _command_safe_entries(clean["cv"], fields_in_typst_commands(template))
    return {"cv": _box_entries(cv), "design": copy.deepcopy(template["design"]),
            "locale": copy.deepcopy(template["locale"]), "settings": settings}


# --------------------------------------------------------------------------- PDF title
TITLE_DASHES_RE = re.compile("[\u2010-\u2015\u2212\u2e3a\u2e3b\ufe58\ufe63\uff0d]")
TITLE_UNSAFE_RE = re.compile(r'["\\{}#%$&~^_<>|\x00-\x1f\x7f]')     # breaks a Typst string or LaTeX
LENS_ACRONYMS = {"ai", "ml", "nlp", "llm", "llms", "rag", "mcp", "ats", "api", "hci", "ui", "ux", "cv"}


def _title_text(text: Any) -> str:
    s = TITLE_DASHES_RE.sub("-", str(text or ""))
    s = TITLE_UNSAFE_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip(" ,-")


def lens_label(lens: Any) -> str:
    """'agentic-systems' -> 'Agentic Systems', 'applied-ml' -> 'Applied ML'."""
    words = [w for w in re.split(r"[-\s]+", _title_text(lens)) if w]
    return " ".join(w.upper() if w.lower() in LENS_ACRONYMS else w[:1].upper() + w[1:] for w in words)


def title_lens(sidecar: Any) -> str | None:
    """The lens the PDF title carries: none for a base (base_of null) or a variant of the general base, where the
    lens is an internal emphasis label nobody outside should see in a viewer's title bar."""
    if not isinstance(sidecar, dict) or sidecar.get("base_of") in (None, "", GENERAL_BASE):
        return None
    return sidecar.get("lens")


def pdf_title(name: Any, lens: Any = None) -> str:
    """The PDF metadata title: '<name>, Resume' or '<name>, Resume (<Lens>)'. Never a dash variant (an
    em-dash once slipped into a title) and never a character that breaks a Typst string or LaTeX."""
    who = _title_text(name)
    title = f"{who}, Resume" if who else "Resume"
    label = lens_label(lens)
    return f"{title} ({label})" if label else title


def facts_name(path: Path | str | None = DEFAULT_FACTS) -> str | None:
    """The name resumes carry: profile/facts.yaml identity.name_on_resumes.canonical, else identity.full_name;
    None when the file is missing, unreadable or has neither."""
    if not path or not Path(path).is_file():
        return None
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    ident = data.get("identity") if isinstance(data, dict) else None
    if not isinstance(ident, dict):
        return None
    on_resumes = ident.get("name_on_resumes")
    name = on_resumes.get("canonical") if isinstance(on_resumes, dict) else on_resumes
    name = name if isinstance(name, str) and name.strip() else ident.get("full_name")
    return name.strip() if isinstance(name, str) and name.strip() else None


def pdf_metadata_title(pdf: Path) -> str | None:
    import pypdf
    meta = pypdf.PdfReader(str(pdf)).metadata
    return str(meta.title) if meta and meta.title is not None else None      # a plain str, not pypdf's type


# --------------------------------------------------------------------------- dates, contacts
def format_single_date(value: Any, locale: dict[str, Any]) -> str:
    if isinstance(value, str) and value.strip().lower() == "present":
        return locale.get("present", "Present")
    if isinstance(value, (date, datetime)):
        year, month = value.year, value.month
    else:
        m = re.match(r"^(\d{4})(?:-(\d{2}))?", str(value).strip())
        if not m:
            return str(value)
        if not m.group(2):
            return m.group(1)
        year, month = int(m.group(1)), int(m.group(2))
    return f"{locale['month_abbreviations'][month - 1]} {year}"


def format_entry_date(entry: dict[str, Any], template: dict[str, Any]) -> str:
    locale = template["locale"]
    rng = template["design"]["templates"]["date_range"]
    start, end, single = entry.get("start_date"), entry.get("end_date"), entry.get("date")
    if start:
        return (rng.replace("START_DATE", format_single_date(start, locale))
                .replace("END_DATE", format_single_date(end or "present", locale)))
    if single:
        return format_single_date(single, locale)
    return ""


SOCIAL_URLS = {
    "linkedin": "https://linkedin.com/in/{}", "github": "https://github.com/{}",
    "gitlab": "https://gitlab.com/{}", "x": "https://x.com/{}", "twitter": "https://x.com/{}",
    "orcid": "https://orcid.org/{}", "researchgate": "https://researchgate.net/profile/{}",
    "google scholar": "https://scholar.google.com/citations?user={}",
    "stackoverflow": "https://stackoverflow.com/users/{}", "youtube": "https://youtube.com/@{}",
}


def display_url(url: str) -> str:
    return re.sub(r"^(https?://)?(www\.)?", "", str(url)).rstrip("/")


def format_phone(phone: str) -> str:
    try:
        import phonenumbers
        num = phonenumbers.parse(str(phone), "US")
        return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
    except Exception:           # phonenumbers missing or unparseable: show it as written
        return str(phone)


def connections(cv: dict[str, Any]) -> list[tuple[str, str | None]]:
    """(visible text, link) for the header, in the YAML key order RenderCV also uses. `custom_connections` show
    their placeholder as written (a Markdown link shows its text): a base may carry the phone this way,
    in a form such as "+1 (000) 000-0000" that RenderCV's `phone` formatting cannot produce."""
    out: list[tuple[str, str | None]] = []
    for key in cv:
        value = cv[key]
        values = value if isinstance(value, list) else [value]
        if key == "location" and value:
            out.append((str(value), None))
        elif key == "custom_connections":
            for item in values or []:
                placeholder = str(item.get("placeholder") or "") if isinstance(item, dict) else ""
                m = MD_LINK_RE.fullmatch(placeholder.strip())
                if m:
                    out.append((m.group(1), m.group(2)))
                elif placeholder.strip():
                    url = item.get("url")
                    out.append((placeholder.strip(), str(url) if url else None))
        elif key == "email":
            out += [(str(v), f"mailto:{v}") for v in values if v]
        elif key == "phone":
            out += [(format_phone(v), None) for v in values if v]
        elif key == "website":
            out += [(display_url(v), str(v)) for v in values if v]
        elif key == "social_networks":
            for sn in values or []:
                if not isinstance(sn, dict):
                    continue
                net, user = str(sn.get("network", "")), str(sn.get("username", ""))
                pattern = SOCIAL_URLS.get(net.lower())
                if pattern:
                    url = pattern.format(user)
                    out.append((display_url(url), url))
                else:
                    out.append((f"{net}: {user}", None))
    return out


# --------------------------------------------------------------------------- markdown (DOCX)
def _md_entry(entry: Any, template: dict[str, Any]) -> list[str]:
    if isinstance(entry, str):
        return [entry, ""]
    if not isinstance(entry, dict):
        return []
    when = format_entry_date(entry, template)
    tail = f" | {when}" if when else ""
    lines: list[str] = []
    if {"company", "position"} <= set(entry):           # Workday: company line above title
        loc = f", {entry['location']}" if entry.get("location") else ""
        lines += [f"**{entry['company']}**{loc}{tail}", "", f"*{entry['position']}*", ""]
    elif {"institution", "area"} <= set(entry):
        degree = f"{entry['degree']} in {entry['area']}" if entry.get("degree") else entry["area"]
        loc = f", {entry['location']}" if entry.get("location") else ""
        lines += [f"**{degree}**, {entry['institution']}{loc}{tail}", ""]
    elif "title" in entry and "authors" in entry:
        lines += [f"**{entry['title']}**{tail}", "", ", ".join(map(str, entry["authors"])), ""]
        link = entry.get("url") or (f"https://doi.org/{entry['doi']}" if entry.get("doi") else "")
        venue = f" ({entry['journal']})" if entry.get("journal") else ""
        if link or venue:
            lines += [f"{display_url(link) if link else ''}{venue}".strip(), ""]
    elif "label" in entry and "details" in entry:
        return [f"**{entry['label']}:** {entry['details']}", ""]
    elif "name" in entry:
        lines += [f"**{entry['name']}**{tail}", ""]
    for key in ("bullet", "number", "reversed_number"):
        if isinstance(entry.get(key), str):
            return [f"- {entry[key]}", ""]
    if entry.get("summary"):
        lines += [str(entry["summary"]), ""]
    for h in entry.get("highlights") or []:
        lines.append(f"- {h}")
    if entry.get("highlights"):
        lines.append("")
    return lines


def resume_markdown(clean: dict[str, Any], template: dict[str, Any] | None = None) -> str:
    """A plain markdown rendering for the Workday DOCX: canonical `##` headings, the company
    line above the title, visible URLs, the same date format as the PDF."""
    template = template or load_template()
    cv = clean["cv"]
    lines = [f"# {cv.get('name', '')}", ""]
    if cv.get("headline"):
        lines += [str(cv["headline"]), ""]
    lines += [" | ".join(text for text, _ in connections(cv)), ""]
    for title, entries in (cv.get("sections") or {}).items():
        lines += [f"## {title}", ""]
        for entry in entries or []:
            lines += _md_entry(entry, template)
    return "\n".join(lines).rstrip() + "\n"


def markdown_to_docx(markdown: str, docx_path: Path) -> None:
    pandoc = shutil.which("pandoc")
    if not pandoc:
        raise ToolchainError("pandoc not found (needed for --docx)")
    fmt = ("markdown-smart-pipe_tables-simple_tables-multiline_tables-grid_tables"
           "-table_captions-raw_html-auto_identifiers")
    proc = subprocess.run([pandoc, "-f", fmt, "-t", "docx", "-o", str(docx_path)],
                          input=markdown, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ToolchainError(f"pandoc failed: {proc.stderr.strip()}")
    _style_docx(docx_path)


def _style_docx(docx_path: Path) -> None:
    """Letter page, narrow margins, one sans font, black headings (python-docx if present)."""
    try:
        import docx
        from docx.shared import Inches, Pt, RGBColor
    except ImportError:
        return
    doc = docx.Document(str(docx_path))
    for sec in doc.sections:
        sec.page_width, sec.page_height = Inches(8.5), Inches(11)
        sec.left_margin = sec.right_margin = Inches(0.6)
        sec.top_margin = sec.bottom_margin = Inches(0.5)
    sizes = {"Heading 1": 18, "Heading 2": 12, "Title": 18}
    for style in doc.styles:
        font = getattr(style, "font", None)
        if font is None:
            continue
        name = style.name or ""
        if name in ("Normal", "Body Text", "First Paragraph", "Compact") or name.startswith("Heading"):
            font.name = "Calibri"
            font.size = Pt(sizes.get(name, 10.5))
            font.color.rgb = RGBColor(0, 0, 0)
            if name.startswith("Heading"):
                font.bold = True
    doc.save(str(docx_path))


# --------------------------------------------------------------------------- LaTeX engine
LATEX_ESCAPES = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
                 "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
                 "^": r"\textasciicircum{}", "|": r"\textbar{}", "<": r"\textless{}",
                 ">": r"\textgreater{}"}
_BARE_URL_RE = re.compile(r"https?://[^\s)\]]+")


def _latex_escape(text: str) -> str:
    return "".join(LATEX_ESCAPES.get(c, c) for c in text)


def _latex_href(url: str, visible: str) -> str:
    target = url.replace("\\", "/").replace("%", r"\%").replace("#", r"\#")
    return f"\\href{{{target}}}{{{_latex_escape(visible)}}}"


def _latex_plain(text: str) -> str:
    out = _latex_escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", out)
    return re.sub(r"\*(.+?)\*", r"\\emph{\1}", out)


def latex_inline(text: str) -> str:
    """Markdown-ish claim text -> LaTeX: escapes specials, **bold**, *italic*, visible links."""
    text = strip_comments(str(text))
    pieces, last = [], 0
    pattern = re.compile(MD_LINK_RE.pattern + "|" + _BARE_URL_RE.pattern)
    for m in pattern.finditer(text):
        pieces.append(_latex_plain(text[last:m.start()]))
        if m.group(1) is not None:
            pieces.append(_latex_href(m.group(2), m.group(1)))
        else:
            url = m.group(0).rstrip(".,;:")
            pieces.append(_latex_href(url, url) + _latex_escape(m.group(0)[len(url):]))
        last = m.end()
    pieces.append(_latex_plain(text[last:]))
    return "".join(pieces)


def find_pdflatex() -> str | None:
    found = shutil.which("pdflatex")
    if found:
        return found
    mac = Path("/Library/TeX/texbin/pdflatex")
    return str(mac) if mac.exists() else None


def _latex_blocks(entries: list[Any], template: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for entry in entries or []:
        if isinstance(entry, str):
            blocks.append({"type": "para", "text": latex_inline(entry)})
            continue
        if not isinstance(entry, dict):
            continue
        item = next((entry[k] for k in ("bullet", "number", "reversed_number")
                     if isinstance(entry.get(k), str)), None)
        if item is not None:
            if blocks and blocks[-1]["type"] == "list":
                blocks[-1]["items"].append(latex_inline(item))
            else:
                blocks.append({"type": "list", "items": [latex_inline(item)]})
            continue
        if "label" in entry and "details" in entry:
            blocks.append({"type": "para", "text": f"\\textbf{{{latex_inline(entry['label'])}:}} "
                                                   f"{latex_inline(entry['details'])}"})
            continue
        when = format_entry_date(entry, template)
        tail = f" \\textbar{{}} \\mbox{{{_latex_escape(when)}}}" if when else ""
        lines: list[str] = []
        if {"company", "position"} <= set(entry):
            head = f"\\textbf{{{latex_inline(entry['position'])}}}{tail}"
            loc = f", {latex_inline(entry['location'])}" if entry.get("location") else ""
            lines.append(f"{latex_inline(entry['company'])}{loc}")
        elif {"institution", "area"} <= set(entry):
            degree = (f"{entry['degree']} in {entry['area']}" if entry.get("degree")
                      else str(entry["area"]))
            loc = f", {latex_inline(entry['location'])}" if entry.get("location") else ""
            head = f"\\textbf{{{latex_inline(degree)}}}, {latex_inline(entry['institution'])}{loc}{tail}"
        elif "title" in entry and "authors" in entry:
            head = f"\\textbf{{{latex_inline(entry['title'])}}}{tail}"
            lines.append(latex_inline(", ".join(map(str, entry["authors"]))))
            link = entry.get("url") or (f"https://doi.org/{entry['doi']}" if entry.get("doi") else "")
            venue = f" ({latex_inline(entry['journal'])})" if entry.get("journal") else ""
            if link or venue:
                lines.append((_latex_href(str(link), display_url(link)) if link else "") + venue)
        else:
            head = f"\\textbf{{{latex_inline(entry.get('name', ''))}}}{tail}"
        if entry.get("summary"):
            lines.append(latex_inline(entry["summary"]))
        bullets = [latex_inline(h) for h in entry.get("highlights") or [] if isinstance(h, str)]
        blocks.append({"type": "entry", "head": head, "lines": lines, "bullets": bullets})
    return blocks


def render_latex(clean: dict[str, Any], template: dict[str, Any], build_dir: Path,
                 pdf_path: Path, title: str | None = None) -> Path:
    import jinja2

    pdflatex = find_pdflatex()
    if not pdflatex:
        raise ToolchainError("pdflatex not found (needed for --engine latex)")
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(LATEX_TEMPLATE_DIR)), autoescape=False,
        block_start_string="<%", block_end_string="%>", variable_start_string="<<",
        variable_end_string=">>", comment_start_string="<#", comment_end_string="#>",
        trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True,
        undefined=jinja2.StrictUndefined)
    cv = clean["cv"]
    conn = []
    for text, link in connections(cv):
        conn.append(_latex_href(link, text) if link else _latex_escape(text))
    ctx = {
        "preamble": (LATEX_TEMPLATE_DIR / "ats-preamble.tex").read_text(encoding="utf-8"),
        "name": latex_inline(cv.get("name", "")),
        "headline": latex_inline(cv["headline"]) if cv.get("headline") else "",
        "connections": conn,
        "sections": [{"title": _latex_escape(heading), "blocks": _latex_blocks(entries, template)}
                     for heading, entries in (cv.get("sections") or {}).items()],
        "pdf_title": _latex_escape(title or pdf_title(cv.get("name"))),
        "pdf_author": _latex_escape(_title_text(cv.get("name"))),
    }
    tex = env.get_template("resume.tex.j2").render(**ctx)
    tex_path = build_dir / f"{pdf_path.stem}.tex"
    tex_path.write_text(tex, encoding="utf-8")
    proc = subprocess.run([pdflatex, "-interaction=nonstopmode", "-halt-on-error",
                           tex_path.name], cwd=str(build_dir), capture_output=True, text=True)
    built = build_dir / f"{pdf_path.stem}.pdf"
    if proc.returncode != 0 or not built.is_file():
        log = (proc.stdout or "")[-2000:]
        raise ToolchainError(f"pdflatex failed on {tex_path}:\n{log}")
    shutil.copyfile(built, pdf_path)
    return pdf_path


# --------------------------------------------------------------------------- RenderCV engine
def rendercv_available() -> bool:
    return importlib.util.find_spec("rendercv") is not None


def rendercv_version() -> str | None:
    try:
        return importlib.metadata.version("rendercv")
    except importlib.metadata.PackageNotFoundError:
        return None


def render_rendercv(clean: dict[str, Any], template: dict[str, Any], build_dir: Path,
                    pdf_path: Path, title: str | None = None) -> Path:
    if not rendercv_available():
        raise ToolchainError("RenderCV is not installed (pip install rendercv)")
    src = build_dir / f"{pdf_path.stem}.rendercv.yaml"
    src.write_text(dump_yaml(build_rendercv_input(clean, template, title)), encoding="utf-8")
    built = build_dir / f"{pdf_path.stem}.pdf"
    cmd = [sys.executable, "-m", "rendercv", "render", src.name,
           "--output-folder", ".", "--pdf-path", built.name, "--typst-path", f"{pdf_path.stem}.typ",
           "--dont-generate-markdown", "--dont-generate-html", "--dont-generate-png", "--quiet"]
    proc = subprocess.run(cmd, cwd=str(build_dir), capture_output=True, text=True)
    if proc.returncode != 0 or not built.is_file():
        out = re.sub(r"\x1b\[[0-9;]*m", "", (proc.stdout or "") + (proc.stderr or ""))
        raise ToolchainError(f"RenderCV failed on {src}:\n{out.strip()[-3000:]}")
    shutil.copyfile(built, pdf_path)
    return pdf_path


# --------------------------------------------------------------------------- PDF helpers
def pdf_page_count(pdf: Path) -> int:
    import pypdf
    return len(pypdf.PdfReader(str(pdf)).pages)


def pdf_text(pdf: Path) -> str:
    exe = shutil.which("pdftotext")
    if exe:
        proc = subprocess.run([exe, "-enc", "UTF-8", str(pdf), "-"], capture_output=True, text=True)
        if proc.returncode == 0:
            return proc.stdout
    import pypdf
    return "\n".join(p.extract_text() or "" for p in pypdf.PdfReader(str(pdf)).pages)


# --------------------------------------------------------------------------- fill to page
# A one-page resume must not end with an empty band under a packed body: the leftover height is spread over the
# vertical spacing only. Widths, margins and the body size never change, so no line rewraps and the text is the same.
FILL_KEYS: tuple[tuple[str, ...], ...] = (
    ("design", "section_titles", "space_above"),
    ("design", "sections", "space_between_regular_entries"),
    ("design", "sections", "space_between_text_based_entries"),
    ("design", "entries", "highlights", "space_between_items"),
    ("design", "typography", "line_spacing"),
)
FILL_MAX: dict[tuple[str, ...], str] = {        # sane upper bounds: a filled page still reads as one document;
    ("design", "section_titles", "space_above"): "0.80cm",      # section headers keep the widest breaks (the
    ("design", "sections", "space_between_regular_entries"): "0.25cm",  # page_layout_check hierarchy rule): roles
    ("design", "sections", "space_between_text_based_entries"): "0.12cm",  # and rows open less than headers
    ("design", "entries", "highlights", "space_between_items"): "0.12cm",
    ("design", "typography", "line_spacing"): "0.60em",
}
FILL_T_MAX = 1.0            # t = 1: every key at its FILL_MAX
FILL_TARGET = 0.015         # aim: at most 1.5% empty under the last line (bottom margin excluded); the gate allows 3%
FILL_STEPS = 8


def fill_overlay(template: dict[str, Any], t: float) -> dict[str, Any]:
    """The template with each FILL_KEYS value moved from its own value toward FILL_MAX by t (0 to FILL_T_MAX)."""
    out = copy.deepcopy(template)
    frac = max(0.0, min(t, FILL_T_MAX)) / FILL_T_MAX
    for path in FILL_KEYS:
        node = out
        for key in path[:-1]:
            node = node.setdefault(key, {})
        start = _to_points(node.get(path[-1], FILL_MAX[path]))
        top = max(start, _to_points(FILL_MAX[path]))
        value = start + (top - start) * frac
        node[path[-1]] = f"{value / 10:.4f}em" if path[-1] == "line_spacing" else f"{value * 2.54 / 72:.4f}cm"
    return out


def bottom_gap_fraction(pdf: Path, bottom_margin: str = "0.42in") -> float:
    """The page height empty under the last text line of page one, bottom margin excluded (pdftotext -bbox)."""
    exe = shutil.which("pdftotext")
    if not exe:
        raise ToolchainError("poppler pdftotext is needed to measure the page (brew install poppler)")
    out = subprocess.run([exe, "-bbox", "-f", "1", "-l", "1", str(pdf), "-"], capture_output=True, text=True).stdout
    page = re.search(r'<page width="([\d.]+)" height="([\d.]+)"', out)
    ys = [float(y) for y in re.findall(r'yMax="([\d.]+)"', out)]
    if not page or not ys:
        raise ToolchainError(f"no text found on page one of {pdf}")
    height = float(page.group(2))
    return (height - max(ys) - _to_points(bottom_margin)) / height


def fill_search(measure, target: float = FILL_TARGET, t_max: float = FILL_T_MAX,
                steps: int = FILL_STEPS) -> tuple[float, tuple[int, float]]:
    """The largest t whose render stays one page, stopping once the gap is at most target. measure(t) -> (pages, gap)."""
    best = (0.0, measure(0.0))
    if best[1][0] != 1 or best[1][1] <= target:
        return best
    top = measure(t_max)
    if top[0] == 1:          # even fully opened the page stays one page: take the bound (the content is short)
        return (t_max, top)
    lo, hi = 0.0, t_max
    for _ in range(steps):
        mid = (lo + hi) / 2
        res = measure(mid)
        if res[0] == 1:
            best, lo = (mid, res), mid
            if res[1] <= target:
                break
        else:
            hi = mid
    return best


def section_line_counts(text: str, titles: list[str]) -> list[tuple[str, int]]:
    """Rendered (non-empty) lines per section, measured from the PDF text."""
    lines = [ln.strip() for ln in text.splitlines()]
    marks: list[tuple[int, str]] = []
    start = 0
    for title in titles:
        for i in range(start, len(lines)):
            if lines[i].lower() == title.lower():
                marks.append((i, title))
                start = i + 1
                break
    counts = []
    for n, (i, title) in enumerate(marks):
        end = marks[n + 1][0] if n + 1 < len(marks) else len(lines)
        counts.append((title, sum(1 for ln in lines[i + 1:end] if ln and ln != "\f")))
    return sorted(counts, key=lambda kv: kv[1], reverse=True)


# --------------------------------------------------------------------------- build record
def content_hash(clean: dict[str, Any], template: dict[str, Any], engine: str) -> str:
    tmpl = {k: template[k] for k in ("design", "locale")}
    tmpl["settings"] = {k: v for k, v in template["settings"].items() if k != "current_date"}
    blob = json.dumps({"cv": clean["cv"], "template": tmpl, "engine": engine},
                      sort_keys=True, default=str, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(JS_ROOT))
    except ValueError:
        return str(path.resolve())


def default_basename(cv: dict[str, Any]) -> str:
    name = re.sub(r"[^A-Za-z0-9]+", "_", str(cv.get("name") or "resume")).strip("_")
    return f"{name}_Resume"


@dataclass
class RenderResult:
    ok: bool
    lint: LintReport
    pdf: Path | None = None
    docx: Path | None = None
    meta_path: Path | None = None
    pages: int | None = None
    failures: list[str] = field(default_factory=list)
    draft_dir: Path | None = None          # a failed build's PDF and meta.yaml, outside V
    fill_t: float = 0.0                    #: how far the vertical spacing was opened to fill the page


def width_note(template: dict[str, Any]) -> str | None:
    """A note when the line estimate is not measured for this layout (missing is not empty)."""
    if measured_chars_per_line(template) is not None:
        return None
    return (f"note: no measured line width for layout {layout_key(template)} in {_rel(LINE_WIDTHS)}; the line "
            f"estimate uses the font formula (~{formula_chars_per_line(template)} chars): run render_resume.py "
            "measure --record")


def render(input_path: Path, out_dir: Path, *, engine: str = "rendercv", docx: bool = False,
           max_pages: int = 1, evidence_path: Path | None = None, force: bool = False,
           keep_build: bool = False, basename: str | None = None, build_date: date | None = None,
           facts_path: Path | None = DEFAULT_FACTS, density: str | None = None, log=print,
           template_spec: str | Path | None = None, rulings_path: Path | None = DEFAULT_RULINGS,
           fill: bool = True) -> RenderResult:
    data = load_yaml(input_path)
    density = density or HOUSE_DENSITY
    if density != HOUSE_DENSITY and engine == "latex":
        raise UsageError(f"--density {density} is measured for the RenderCV engine only; the LaTeX fallback "
                         "renders the house density")
    template_path, template = load_checked_template(template_spec, density)
    known = load_evidence_ids(evidence_path) if evidence_path and Path(evidence_path).is_file() else None
    facts = load_facts(facts_path)
    rulings = load_rulings(rulings_path)
    report = lint_resume(data, known_evidence=known,
                         chars_per_line=estimate_chars_per_line(template), facts=facts, rulings=rulings,
                         line_model=line_estimate(template))
    for issue in report.errors + report.warnings + report.notes:
        log(str(issue))
    if known is None:
        log(f"note: no evidence file checked (looked for {evidence_path})")
    if facts is None:
        log(f"note: no long-bullet rulings loaded (no facts file at {facts_path}); every bullet keeps the "
            f"{MAX_CLAIM_LINES}-line limit")
    if rulings is None:
        log(f"note: no rulings file at {rulings_path}; no labeled link or rulings.yaml allowance applies")
    note = width_note(template)
    if note:
        log(note)
    result = RenderResult(ok=False, lint=report)
    if report.errors and not force:
        result.failures = [f"lint: {len(report.errors)} error(s); fix them or pass --force to "
                           "build a draft for inspection"]
        log(f"FAIL {result.failures[0]}")
        return result
    if report.errors:
        result.failures.append(f"lint: {len(report.errors)} error(s) (built anyway with --force)")

    clean = strip_for_render(data)
    sidecar = data.get("sidecar") or {}
    name = facts_name(facts_path)
    if not name:
        log(f"note: no facts name in {facts_path}; the PDF title uses cv.name")
    elif str(clean["cv"].get("name") or "").strip() != name:
        log(f"note: cv.name {clean['cv'].get('name')!r} differs from the facts name {name!r}; the PDF title uses "
            "the facts name, so the header and the title disagree until cv.name matches profile/facts.yaml")
    title = pdf_title(name or clean["cv"].get("name"), title_lens(sidecar))
    base = basename or default_basename(clean["cv"])
    # Build into a staging folder; V's PDF, docx and meta.yaml are replaced only when every check passes
    stage = Path(tempfile.mkdtemp(prefix="resume-render-"))
    pdf_path = stage / f"{base}.pdf"
    if keep_build:
        out_dir.mkdir(parents=True, exist_ok=True)
    build_dir = out_dir / "build" if keep_build else Path(tempfile.mkdtemp(prefix="resume-build-"))
    build_dir.mkdir(parents=True, exist_ok=True)
    try:
        if engine == "latex":
            render_latex(clean, template, build_dir, pdf_path, title)
        else:
            render_rendercv(clean, template, build_dir, pdf_path, title)
    finally:
        if not keep_build:
            shutil.rmtree(build_dir, ignore_errors=True)
    result.pdf = pdf_path
    written = pdf_metadata_title(pdf_path)
    if written != title:
        result.failures.append(f"pdf title: meant {title!r}, the PDF says {written!r}")
        log(f"FAIL {result.failures[-1]}")
    result.pages = pdf_page_count(pdf_path)
    if fill and engine == "rendercv" and max_pages == 1 and result.pages == 1 and shutil.which("pdftotext"):
        margin = str(_get(template, "design", "page", "bottom_margin") or "0.42in")

        def _measure(t: float) -> tuple[int, float]:
            trial_dir = Path(tempfile.mkdtemp(prefix="resume-fill-"))
            try:
                (trial_dir / "out").mkdir()
                trial = render_rendercv(clean, fill_overlay(template, t), trial_dir, trial_dir / "out" / pdf_path.name,
                                        title)
                return pdf_page_count(trial), bottom_gap_fraction(trial, margin)
            finally:
                shutil.rmtree(trial_dir, ignore_errors=True)

        gap0 = bottom_gap_fraction(pdf_path, margin)
        if gap0 > FILL_TARGET:
            t_best, (pages_best, gap_best) = fill_search(lambda t: (1, gap0) if t == 0 else _measure(t))
            if t_best > 0 and pages_best == 1:
                template = fill_overlay(template, t_best)
                fill_dir = Path(tempfile.mkdtemp(prefix="resume-build-"))
                try:
                    render_rendercv(clean, template, fill_dir, pdf_path, title)
                finally:
                    shutil.rmtree(fill_dir, ignore_errors=True)
                result.fill_t = t_best
                result.pages = pdf_page_count(pdf_path)
                gap_end = bottom_gap_fraction(pdf_path, margin)
                log(f"NOTE fill: bottom gap {gap0:.1%} -> {gap_end:.1%}; vertical "
                    f"spacing opened to t={t_best:.3f} of FILL_MAX")
                if gap_end > FILL_TARGET:
                    log(f"WARN fill: {gap_end:.1%} still empty at the spacing bound: the page is short of content; "
                        "add the strongest true content the role wants rather than stretching")
    titles = list((clean["cv"].get("sections") or {}).keys())
    if result.pages > max_pages:
        counts = section_line_counts(pdf_text(pdf_path), titles)
        longest = ", ".join(f"{t} ({n} line{'s' if n != 1 else ''})" for t, n in counts)
        claims = sorted(iter_claims(data["cv"]), key=lambda c: len(plain_text(c.text)), reverse=True)
        bullets = ", ".join(f"{c.path} ({len(plain_text(c.text))} chars)" for c in claims[:3])
        msg = (f"page budget: {result.pages} pages > --max-pages {max_pages}. "
               f"Longest sections: {longest} (cut from the front of this list first). "
               f"Longest bullets: {bullets}.")
        result.failures.append(msg)
        log(f"FAIL {msg}")
    if docx:
        result.docx = stage / f"{base}.docx"
        markdown_to_docx(resume_markdown(clean, template), result.docx)

    digest = content_hash(clean, template, engine)
    meta_path = out_dir / "meta.yaml"
    previous = load_yaml(meta_path) if meta_path.is_file() else {}
    same = isinstance(previous, dict) and previous.get("hash") == digest
    meta = {
        "lens": sidecar.get("lens"),
        "base_of": sidecar.get("base_of"),
        "job": sidecar.get("job"),
        "built_at": stamp(build_date),
        "engine": engine,
        "template": template_label(template_path),
        "template_sha256": _file_sha256(template_path),
        "density": density,
        "fill_t": round(result.fill_t, 4),
        "rendercv_version": rendercv_version() if engine == "rendercv" else None,
        "pages": result.pages,
        "max_pages": max_pages,
        "status": "failed" if result.failures else "ok",
        "failures": result.failures,
        "hash": digest,
        "source": _rel(Path(input_path)),
        "pdf": pdf_path.name,
        "pdf_title": written,
        "pdf_sha256": _file_sha256(pdf_path),
        "docx": result.docx.name if result.docx else None,
        "sections": titles,
        "evidence_ids": report.evidence_ids,
        "ats_report": previous.get("ats_report") if same else None,
        "editorial_verdict": previous.get("editorial_verdict") if same else None,
    }
    result.ok = not result.failures
    if result.ok:
        out_dir.mkdir(parents=True, exist_ok=True)
        for staged in (pdf_path, result.docx):
            if staged is not None:
                shutil.move(str(staged), str(out_dir / staged.name))
        pdf_path = out_dir / pdf_path.name
        result.pdf = pdf_path
        result.docx = out_dir / result.docx.name if result.docx else None
        fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=".meta-")
        os.write(fd, dump_yaml(meta).encode("utf-8"))
        os.close(fd)
        os.chmod(tmp, 0o644)
        os.replace(tmp, meta_path)
        result.meta_path = meta_path
        shutil.rmtree(stage, ignore_errors=True)
    else:
        (stage / "meta.yaml").write_text(dump_yaml(meta), encoding="utf-8")
        result.meta_path, result.draft_dir = stage / "meta.yaml", stage
        log(f"FAIL {out_dir} left as it was (its PDF and meta.yaml were not replaced); failed draft kept at {stage}")
    log(("OK" if result.ok else "FAIL") + f" {pdf_path} ({result.pages} page"
        f"{'s' if result.pages != 1 else ''}, {engine}"
        + (f", template {template_label(template_path)}" if template_label(template_path) != HOUSE_TEMPLATE else "")
        + (f", density {density}" if density != HOUSE_DENSITY else "") + ")" + (f" + {result.docx.name}" if result.docx else ""))
    return result


# --------------------------------------------------------------------------- measure (line widths)
def _norm_line(text: str) -> str:
    text = text.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'}))
    return re.sub(r"\s+", " ", text).strip()


def claim_line_counts(layout_text: str, cv: dict[str, Any]) -> list[tuple[Claim, str, int | None]]:
    """For each claim, its visible text and the lines it takes in `pdftotext -layout` output (None when it could
    not be found whole: a page break inside it, text the PDF changed, or a Skills line whose label shares its
    line). Lines are matched in page order; a wrapped claim is the run of lines whose joined text equals the
    claim's."""
    lines = []
    for raw in layout_text.splitlines():
        line = _norm_line(raw)
        if line.startswith("•"):
            line = line[1:].strip()
        if line:
            lines.append(line)
    out: list[tuple[Claim, str, int | None]] = []
    pos = 0
    for claim in iter_claims(cv):
        body, _ = parse_evidence(claim.text)
        want = _norm_line(plain_text(body))
        found = None
        for i in range(pos, len(lines)):
            if len(lines[i]) < 8 or not want.startswith(lines[i]):
                continue
            acc, j = lines[i], i
            while acc != want and j + 1 < len(lines) and want.startswith(f"{acc} {lines[j + 1]}"):
                j += 1
                acc = f"{acc} {lines[j]}"
            if acc == want:
                found, pos = j - i + 1, j + 1
                break
        out.append((claim, want, found))
    return out


def fit_chars_per_line(samples: list[tuple[str, int]], low: int = 40, high: int = 250) -> dict[str, Any]:
    """The characters per line whose greedy-wrap estimate agrees with the most rendered line counts: the middle
    of the widest run of best-agreeing widths (its ends are reported as low and high)."""
    scores = {cpl: sum(estimate_lines(text, cpl) == n for text, n in samples) for cpl in range(low, high + 1)}
    best = max(scores.values()) if scores else 0
    runs: list[list[int]] = []
    for cpl in range(low, high + 1):
        if scores[cpl] == best:
            if runs and runs[-1][-1] == cpl - 1:
                runs[-1].append(cpl)
            else:
                runs.append([cpl])
    run = max(runs, key=len) if runs else [low]
    return {"chars_per_line": run[len(run) // 2] if len(run) % 2 else run[len(run) // 2 - 1],
            "low": run[0], "high": run[-1], "agree": best, "samples": len(samples),
            "multi_line": sum(1 for _, n in samples if n > 1)}


def measure(input_path: Path, *, template_spec: str | Path | None = None, density: str | None = None,
            record: bool = False, widths_path: Path | None = None, build_date: date | None = None,
            log=print) -> dict[str, Any]:
    """Render the input (lint not applied: it may be a calibration text), count each claim's rendered lines and
    fit the line width for the layout. --record writes it to line-widths.yaml, replacing that layout's entry."""
    density = density or HOUSE_DENSITY
    template_path, template = load_checked_template(template_spec, density)
    data = load_yaml(input_path)
    if not isinstance(data, dict) or not isinstance(data.get("cv"), dict):
        raise UsageError(f"{input_path}: the file needs a top-level `cv:` mapping")
    exe = shutil.which("pdftotext")
    if not exe:
        raise ToolchainError("pdftotext not found (poppler; needed to count rendered lines)")
    clean = strip_for_render(data)
    build = Path(tempfile.mkdtemp(prefix="resume-measure-"))
    try:
        (build / "work").mkdir()
        pdf = render_rendercv(clean, template, build / "work", build / "measure.pdf")
        proc = subprocess.run([exe, "-layout", "-enc", "UTF-8", str(pdf), "-"], capture_output=True, text=True)
    finally:
        shutil.rmtree(build, ignore_errors=True)
    counts = claim_line_counts(proc.stdout, data["cv"])
    samples = [(parse_evidence(c.text)[0], n) for c, _, n in counts if n and c.kind in FIT_KINDS]
    if not samples:
        raise UsageError(f"{input_path}: no highlight, bullet or text claim was found whole in the render")
    fit = fit_chars_per_line(samples)
    missing = sum(1 for c, _, n in counts if n is None and c.kind in FIT_KINDS)
    key = layout_key(template)
    log(f"layout {key}: template {template_label(template_path)}, density {density}")
    log(f"measured {fit['chars_per_line']} chars per line (agreeing widths {fit['low']} to {fit['high']}; "
        f"{fit['agree']} of {fit['samples']} claims agree, {fit['multi_line']} of them wrap"
        + (f"; {missing} claim(s) not found whole, left out" if missing else "") + ")")
    measured = measured_chars_per_line(template, widths_path)
    if measured is not None:
        agree = sum(estimate_lines(t, measured) == n for t, n in samples)
        log(f"recorded width {measured}: {agree} of {len(samples)} claims agree")
    entry = {"layout": key, "chars_per_line": fit["chars_per_line"],
             "fit": {"low": fit["low"], "high": fit["high"], "agree": fit["agree"], "samples": fit["samples"],
                     "multi_line": fit["multi_line"]},
             "measured": {"on": (build_date or wall_clock().date()).isoformat(),
                          "template": template_label(template_path), "density": density,
                          "input": _rel(Path(input_path)), "rendercv": rendercv_version()}}
    if record:
        path = widths_path or LINE_WIDTHS
        seed = path if Path(path).is_file() else LINE_WIDTHS_READ      # first --record seeds from the shipped file
        doc = load_yaml(seed) if Path(seed).is_file() else {}
        doc = doc if isinstance(doc, dict) else {}
        widths = [w for w in (doc.get("widths") or []) if isinstance(w, dict) and w.get("layout") != key]
        doc["widths"] = widths + [entry]
        header = "".join(line + "\n" for line in Path(seed).read_text(encoding="utf-8").splitlines()
                         if line.startswith("#")) if Path(seed).is_file() else ""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(header + dump_yaml({k: v for k, v in doc.items()}), encoding="utf-8")
        log(f"recorded in {_rel(Path(path))}")
    return entry


# --------------------------------------------------------------------------- dates
def wall_clock() -> datetime:
    return datetime.now()


def stamp(day: date | None = None) -> str:
    """Local timestamp for meta.yaml and reports: the wall clock, or `day` at 12:00 when a run is dated
    to another day (--date)."""
    now = wall_clock()
    if day is None or day == now.date():
        return now.replace(microsecond=0).isoformat()
    return f"{day.isoformat()}T12:00:00"


def cli_date(text: str) -> date:
    """argparse type for --date: strict YYYY-MM-DD."""
    value = str(text).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {text!r}")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {text!r}") from None


# --------------------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("render", help="lint, render the PDF (and DOCX), write meta.yaml")
    r.add_argument("--input", required=True, type=Path)
    r.add_argument("--out", required=True, type=Path)
    r.add_argument("--docx", action="store_true", help="also write a DOCX (Workday)")
    r.add_argument("--engine", choices=("rendercv", "latex"), default="rendercv")
    r.add_argument("--max-pages", type=int, default=1)
    r.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    r.add_argument("--force", action="store_true",
                   help="render despite lint errors (the build is still marked failed)")
    r.add_argument("--keep-build", action="store_true", help="keep intermediates in <out>/build")
    r.add_argument("--basename", help="output file stem (default: <Name>_Resume)")
    r.add_argument("--date", type=cli_date, default=None,
                   help="build date YYYY-MM-DD for meta.yaml built_at (default: today)")
    r.add_argument("--facts", type=Path, default=DEFAULT_FACTS,
                   help="facts.yaml whose identity name titles the PDF (default: profile/facts.yaml)")
    r.add_argument("--density", default=HOUSE_DENSITY,
                   help="opt-in layout density from the template's densities: block (default: house, "
                        "unchanged; one-page: 10pt body, 0.4em line spacing, tighter margins)")
    r.add_argument("--no-fill", dest="fill", action="store_false",
                   help="do not fill a one-page resume to the bottom (default fills: the vertical spacing "
                        "opens until at most 3%% of the page is empty under the last line); use to reproduce a "
                        "frozen PDF")
    lint = sub.add_parser("lint", help="pre-render lint only")
    lint.add_argument("--input", required=True, type=Path)
    lint.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    lint.add_argument("--density", default=None,
                      help=f"the density the line estimate assumes (default: {BASE_DENSITY}, the density the general "
                           f"base ships with, when the template has it; {HOUSE_DENSITY} measures the template's own design)")
    lint.add_argument("--facts", type=Path, default=DEFAULT_FACTS,
                      help="facts.yaml whose rules: may carry the owner's long-bullet ruling (default: profile/facts.yaml)")
    m = sub.add_parser("measure", help="render a resume and measure the characters per line of its layout")
    m.add_argument("--input", required=True, type=Path, help="a resume or the calibration fixture")
    m.add_argument("--density", default=HOUSE_DENSITY)
    m.add_argument("--record", action="store_true", help="write the width to resumes/templates/line-widths.yaml")
    m.add_argument("--widths", type=Path, default=None, help="line-widths file (default: the templates one)")
    m.add_argument("--date", type=cli_date, default=None, help="measurement date YYYY-MM-DD (default: today)")
    for sp in (r, lint, m):
        sp.add_argument("--template", default=HOUSE_TEMPLATE,
                        help=f"design: {', '.join(NAMED_TEMPLATES)} (the general base's classic and inline "
                             "files) or a template file (default: house)")
    for sp in (r, lint):
        sp.add_argument("--rulings", type=Path, default=DEFAULT_RULINGS,
                        help="the owner's rulings on how resumes render (default: resumes/templates/rulings.yaml)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        template_spec = None if args.template == HOUSE_TEMPLATE else args.template
        if args.command == "measure":
            measure(args.input, template_spec=template_spec, density=args.density, record=args.record,
                    widths_path=args.widths, build_date=args.date)
            return 0
        if args.command == "lint":
            known = load_evidence_ids(args.evidence) if args.evidence and args.evidence.is_file() else None
            density = args.density
            if density is None:                  # the density the base ships with, when this template has it
                has = BASE_DENSITY in density_names(load_template(resolve_template(template_spec)))
                density = BASE_DENSITY if has else HOUSE_DENSITY
            _, template = load_checked_template(template_spec, density)
            facts = load_facts(args.facts)
            rulings = load_rulings(args.rulings)
            report = lint_resume(load_yaml(args.input), known_evidence=known,
                                 chars_per_line=estimate_chars_per_line(template), facts=facts, rulings=rulings,
                                 line_model=line_estimate(template))
            for issue in report.errors + report.warnings + report.notes:
                print(issue)
            if facts is None:
                print(f"note: no long-bullet rulings loaded (no facts file at {args.facts}); every bullet keeps "
                      f"the {MAX_CLAIM_LINES}-line limit")
            if rulings is None:
                print(f"note: no rulings file at {args.rulings}; no labeled link or rulings.yaml allowance applies")
            note = width_note(template)
            if note:
                print(note)
            print(f"line estimate: ~{estimate_chars_per_line(template)} chars per line "
                  f"({'measured' if measured_chars_per_line(template) else 'font formula'}, template "
                  f"{args.template}, density {density})")
            print(report.summary())
            return 0 if report.ok else 1
        result = render(args.input, args.out, engine=args.engine, docx=args.docx,
                        max_pages=args.max_pages, evidence_path=args.evidence, force=args.force,
                        keep_build=args.keep_build, basename=args.basename, build_date=args.date,
                        facts_path=args.facts, density=args.density, template_spec=template_spec,
                        rulings_path=args.rulings, fill=args.fill)
        return 0 if result.ok else 1
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ToolchainError as exc:
        print(f"toolchain error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
