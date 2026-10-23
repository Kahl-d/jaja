#!/usr/bin/env python3
"""Employer sponsorship enrichment from cached USCIS H-1B and DOL LCA data (design §4, research §8).

Caches live in legal/evidence/h1b/ (refreshed monthly by `fetch`, alias `download`; never hand-edited; the data
files are gitignored and README.md, rewritten by each fetch, records source URLs and fetch times):
  uscis_h1b_fy<FY>.csv        employer-level approvals per fiscal year, pivoted from the USCIS
                              H-1B Employer Data Hub Tableau export (FY2024+):
                              https://bigdataanalyticspub-sb.uscis.dhs.gov/views/H1BEmployerDataHub-Final/H1BPublic.csv?Fiscal%20Year%20%20%20=<FY>
                              (hub page: https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub)
  h1b_datahubexport-<FY>.csv  archived hub files FY2009-FY2023, as published:
                              https://www.uscis.gov/sites/default/files/document/data/h1b_datahubexport-<FY>.csv
  lca_<name>.csv              DOL OFLC LCA disclosure subset (H-1B, SOC 15-* by default) converted
                              from the quarterly xlsx: https://www.dol.gov/agencies/eta/foreign-labor/performance
                              (LCA_Disclosure_Data_FY<FY>_Q<Q>.xlsx). dol.gov refuses scripted
                              downloads (HTTP 403 seen): save the file in a browser, then
                              `fetch --lca-xlsx <file>`.
  _manifest.json              source URL, fetch time and row count per cache file.
  naics_codes.csv             optional NAICS code table (columns code,title, e.g. converted from the
                              Census NAICS files at https://www.census.gov/naics/). Without it a
                              NAICS industry is a code only: the USCIS data carries no names.

Lookup (by employer name, plus optional aliases, e.g. companies.yaml `h1b_names`):
  names match on normalized keys (legal suffixes, "L L C", d/b/a split) exactly, or as a prefix
  followed only by corporate words (Acme -> ACME OPCO LLC; Acme -> ACME PLATFORMS INC, never
  ACME IT SYSTEMS LLC). Matching rows are then split into ENTITIES by tax id (by state when a row
  has no tax id; city and NAICS reported): "Quillstone Systems, Inc." (San Jose, CA) and "Quillstone Systems LLC"
  (Houston, TX) share a name key but are two employers. Each entity is a row in `entities`
  (legal_name, names, city, state, naics, tax_id, h1b_by_fy, h1b_approvals_3y, lca_count, match).
  Only `match: primary` rows are counted in the totals; every other row is
  `match: possible_other_entity`. Primary = the entities whose legal name (suffix kept) equals the
  query or an --alias; else the only entity left after --state/--city; else none: the result is
  `ambiguous: true`, h1b_approvals_3y and lca_count are null and sponsorship stays "unknown"
  (never a guess). Rows without tax id or place (older caches) cannot be told apart and stay one
  entity. --merge restores the old behavior: every name-key match summed, no entity split.
  Fields: h1b_approvals_3y (approvals in the last 3 fiscal years, FY starting Oct 1),
  sponsorship ("yes" when that is > 0, else "unknown"; never "no" from absence), lca_count
  (certified H-1B LCAs), median_lca_wage (annualized offered wage), wage_level_est (most common
  PW_WAGE_LEVEL; rows with a similar job title win when any exist), lca_titles (the certified H-1B
  LCAs behind lca_count, counted per job title: [{title, count}], most first, then by title),
  denials ({fiscal year: denials} for the years whose cached USCIS rows all carry a denials figure;
  a year with no row, a missing column or a blank cell is left out, and the key is omitted when no
  year has one: absent is never 0), ambiguous, note. Entity rows carry `naics` as the code only;
  `naics_name` and `naics_source` appear only when naics_codes.csv in the cache names that code.
  Ambiguous: lca_titles null, no denials. Quote these counts; never count titles or denials by eye.
  Missing is not zero: a fiscal year no cached USCIS file covers prints as "unknown" in h1b_by_fy and makes
  h1b_approvals_3y "unknown"; no cached LCA file makes lca_count "unknown" (wage fields null, lca_titles null).
  `missing` lists each absent source with its reason, and `lookup` then exits 3 unless --allow-missing.

CLI:
  python h1b_lookup.py [--root R] lookup "Quillstone Systems, Inc." [--alias NAME] [--title T]
                         [--state ST] [--city C] [--merge] [--today D] [--allow-missing]
  python h1b_lookup.py [--root R] enrich [--dry-run] [--merge] [--today D]
  python h1b_lookup.py [--root R] fetch [--fy 2024 2025 2026] [--archive-fy 2023]
                         [--lca-xlsx FILE] [--lca-quarter 2026Q3] [--soc-prefix 15-] [--dry-run]
"""
from __future__ import annotations

import argparse
import functools
import csv
import json
import re
import statistics
import sys
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_ats import Http, iso_utc, load_companies, load_jobs, save_job  # noqa: E402
from js_common import JS_ROOT, normalize_company, normalize_title  # noqa: E402

HUB_CSV = "https://bigdataanalyticspub-sb.uscis.dhs.gov/views/H1BEmployerDataHub-Final/H1BPublic.csv"
HUB_PAGE = "https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub"
ARCHIVE_CSV = "https://www.uscis.gov/sites/default/files/document/data/h1b_datahubexport-{fy}.csv"
DOL_PAGE = "https://www.dol.gov/agencies/eta/foreign-labor/performance"
DOL_LCA = "https://www.dol.gov/sites/dolgov/files/ETA/oflc/pdfs/LCA_Disclosure_Data_FY{fy}_Q{q}.xlsx"
FETCH_USER_AGENT = ("jaja h1b_lookup.py/1.1 (personal job search; public USCIS H-1B Employer Data Hub "
                    "and DOL LCA files; python-requests)")

COMPACT_COLUMNS = ["fiscal_year", "employer", "tax_id", "city", "state", "zip", "naics", "approvals",
                   "denials", "initial_approvals", "continuing_approvals"]
LCA_COLUMNS = ["CASE_NUMBER", "CASE_STATUS", "RECEIVED_DATE", "DECISION_DATE", "VISA_CLASS", "JOB_TITLE",
               "SOC_CODE", "SOC_TITLE", "FULL_TIME_POSITION", "EMPLOYER_NAME", "EMPLOYER_CITY",
               "EMPLOYER_STATE", "WORKSITE_CITY", "WORKSITE_STATE", "WAGE_RATE_OF_PAY_FROM",
               "WAGE_RATE_OF_PAY_TO", "WAGE_UNIT_OF_PAY", "PREVAILING_WAGE", "PW_UNIT_OF_PAY", "PW_WAGE_LEVEL"]
INITIAL_MEASURES = {"New Employment Approval", "Change of Employer Approval"}
WAGE_UNITS = {"year": 1, "month": 12, "bi-weekly": 26, "biweekly": 26, "week": 52, "hour": 2080}
LEVELS = ("I", "II", "III", "IV")
NAICS_TABLE = "naics_codes.csv"
UNKNOWN = "unknown"                 # a count the cache cannot answer (its source is absent): never 0
STRUCTURAL_WORDS = {"opco", "holdings", "holding", "group", "global", "technologies", "technology", "tech",
                    "platforms", "platform", "labs", "lab", "usa", "us", "america", "services", "service",
                    "international", "operations", "com", "software", "systems", "research", "north"}


def cache_dir(root: Path) -> Path:
    return Path(root) / "legal" / "evidence" / "h1b"


def hub_url(fy: int) -> str:
    return f"{HUB_CSV}?Fiscal%20Year%20%20%20={fy}"     # the Tableau field name has 3 trailing spaces


def lca_url(fy: int, quarter: int) -> str:
    return DOL_LCA.format(fy=fy, q=quarter)


def fiscal_years(today: date) -> list[int]:
    fy = today.year + (1 if today.month >= 10 else 0)
    return [fy - 2, fy - 1, fy]


def published_years(index: "H1bIndex", today: date) -> list[int]:
    """The 3-year window lookups read: the last three fiscal years, ending at the current one only once a USCIS
    file covers it. When a new fiscal year begins with nothing published, every lookup would exit 3 and enrich would write
    null counts on every job. A year USCIS has published but we have not cached
    still shows as missing: the shift is only for the new year, never a silent gap."""
    fys = fiscal_years(today)
    if fys[-1] not in index.uscis_fys and fys[-2] in index.uscis_fys:
        return [fy - 1 for fy in fys]
    return fys


# ------------------------------------------------------------------------------ names
_EXTRA_SUFFIX = re.compile(r"\b(?:l l c|l l p|llp|lp|limited|incorporated)\b")


def employer_keys(name: str) -> list[str]:
    base = _EXTRA_SUFFIX.sub(" ", normalize_company(name))
    base = re.sub(r"\s+", " ", base).strip()
    out: list[str] = []
    for part in re.split(r"\s+(?:d b a|dba|aka|f k a|fka)\s+", base):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out


def _key_matches(target: str, key: str) -> bool:
    if key == target:
        return True
    if key.startswith(target + " "):
        return all(tok in STRUCTURAL_WORDS for tok in key[len(target) + 1:].split())
    return False


# ------------------------------------------------------------------------------ parsing helpers
def _num(value: Any) -> float | None:
    s = re.sub(r"[,$\s]", "", str(value or ""))
    try:
        return float(s) if s else None
    except ValueError:
        return None


def _int(value: Any) -> int:
    v = _num(value)
    return int(round(v)) if v is not None else 0


def _opt_int(value: Any) -> int | None:
    """A count, or None when the cell is blank: a missing figure is never 0."""
    v = _num(value)
    return int(round(v)) if v is not None else None


def annualize(amount: Any, unit: Any) -> float | None:
    v = _num(amount)
    mult = WAGE_UNITS.get(str(unit or "").strip().lower())
    return v * mult if v is not None and mult else None


def _level(value: Any) -> str | None:
    s = str(value or "").strip().upper().replace("LEVEL", "").strip()
    return s if s in LEVELS else None


def legal_key(name: str) -> str:
    """The legal name with its suffix kept, spelling-normalized: "Quillstone Systems, Inc." and
    "QUILLSTONE SYSTEMS INC" agree; "QUILLSTONE SYSTEMS LLC" does not."""
    s = re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()
    for pattern, repl in ((r"\bl l c\b", "llc"), (r"\bl l p\b", "llp"), (r"\bincorporated\b", "inc"),
                          (r"\bcorporation\b", "corp"), (r"\blimited\b", "ltd"), (r"\bcompany\b", "co")):
        s = re.sub(pattern, repl, s)
    return re.sub(r"\s+", " ", s).strip()


def _place(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().upper()


@dataclass
class H1bIndex:
    approvals: dict[str, dict[int, dict[str, Any]]] = field(default_factory=lambda: defaultdict(dict))
    lca: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    files: list[str] = field(default_factory=list)
    naics: dict[str, str] = field(default_factory=dict)          # code -> title, only from NAICS_TABLE
    uscis_fys: set[int] = field(default_factory=set)             # fiscal years some USCIS file covers
    lca_files: list[str] = field(default_factory=list)           # DOL LCA disclosure files read

    def add_approvals(self, employer: str, fy: int, approvals: int, denials: int | None, initial: int,
                      tax_id: str = "", city: str = "", state: str = "", naics: str = "") -> None:
        if fy:
            self.uscis_fys.add(fy)
        row = {"name": employer.strip(), "fy": fy, "approvals": approvals, "denials": denials, "initial": initial,
               "tax_id": str(tax_id or "").strip(), "city": _place(city), "state": _place(state),
               "naics": str(naics or "").strip()}
        for key in employer_keys(employer):
            slot = self.approvals[key].setdefault(fy, {"approvals": 0, "denials": 0, "initial": 0, "names": set()})
            slot["approvals"] += approvals
            slot["denials"] += denials or 0
            slot["initial"] += initial
            slot["names"].add(employer.strip())
            self.rows[key].append(row)

    @property
    def empty(self) -> bool:
        return not self.approvals and not self.lca

    def missing(self, fys: Iterable[int]) -> list[dict[str, Any]]:
        """The sources this index cannot answer for `fys`: USCIS fiscal years with no cached hub file, and the
        DOL LCA disclosure data when no LCA file is cached. Absent data is reported, never read as zero."""
        out: list[dict[str, Any]] = []
        gone = [fy for fy in fys if fy not in self.uscis_fys]
        if gone:
            years = ", ".join(f"FY{fy}" for fy in gone)
            out.append({"source": "uscis_h1b_hub", "fiscal_years": gone,
                        "reason": f"no USCIS Data Hub cache for {years}; run h1b_lookup.py fetch"})
        if not self.lca_files:
            out.append({"source": "dol_lca", "fiscal_years": None,
                        "reason": f"no DOL LCA disclosure cache; save the quarterly xlsx from {DOL_PAGE} in a "
                                  "browser and run h1b_lookup.py fetch --lca-xlsx <file>"})
        return out


def _cell(row: list[str], col: dict[str, int], name: str) -> str:
    i = col.get(name)
    return row[i] if i is not None and i < len(row) else ""


def _read_rows(path: Path) -> tuple[list[str], Iterator[list[str]]]:
    fh = path.open(newline="", encoding="utf-8", errors="replace")
    reader = csv.reader(fh)
    header = [h.strip() for h in next(reader, [])]
    return header, reader


def load_index(folder: Path) -> H1bIndex:
    """Read every recognizable CSV in the cache folder (compact, archive, hub long/wide, LCA)."""
    idx = H1bIndex()
    folder = Path(folder)
    if not folder.is_dir():
        return idx
    idx.naics = load_naics(folder / NAICS_TABLE)
    for path in sorted(folder.glob("*.csv")):
        if path.name == NAICS_TABLE:
            continue
        header, rows = _read_rows(path)
        col = {h: i for i, h in enumerate(header)}

        def get(row: list[str], name: str, _col: dict[str, int] = col) -> str:
            return _cell(row, _col, name)

        if {"fiscal_year", "employer", "approvals"} <= col.keys():
            for row in rows:
                idx.add_approvals(get(row, "employer"), _int(get(row, "fiscal_year")), _int(get(row, "approvals")),
                                  _opt_int(get(row, "denials")), _int(get(row, "initial_approvals")),
                                  get(row, "tax_id"), get(row, "city"), get(row, "state"), get(row, "naics"))
        elif "Employer" in col and "Initial Approval" in col:
            for row in rows:
                ia, ca = _int(get(row, "Initial Approval")), _int(get(row, "Continuing Approval"))
                denied = [d for d in (_opt_int(get(row, "Initial Denial")), _opt_int(get(row, "Continuing Denial")))
                          if d is not None]
                idx.add_approvals(get(row, "Employer"), _int(get(row, "Fiscal Year")), ia + ca,
                                  sum(denied) if denied else None, ia,
                                  get(row, "Tax ID"), get(row, "City"), get(row, "State"), get(row, "NAICS"))
        elif "Measure Names" in col:
            rows = list(rows)
            has_denials = any(get(row, "Measure Names").endswith("Denial") for row in rows)
            for row in rows:
                m, v = get(row, "Measure Names"), _int(get(row, "Measure Values"))
                idx.add_approvals(get(row, "Employer (Petitioner) Name"), _int(get(row, "Fiscal Year")),
                                  v if m.endswith("Approval") else 0,
                                  (v if m.endswith("Denial") else 0) if has_denials else None,
                                  v if m in INITIAL_MEASURES else 0, get(row, "Tax ID"), get(row, "Petitioner City"),
                                  get(row, "Petitioner State"), get(row, "Industry (NAICS) Code"))
        elif "Employer (Petitioner) Name" in col:
            approval_cols = [h for h in header if h.endswith("Approval")]
            denial_cols = [h for h in header if h.endswith("Denial")]
            for row in rows:
                idx.add_approvals(get(row, "Employer (Petitioner) Name"), _int(get(row, "Fiscal Year")),
                                  sum(_int(get(row, h)) for h in approval_cols),
                                  sum(_int(get(row, h)) for h in denial_cols) if denial_cols else None,
                                  sum(_int(get(row, h)) for h in approval_cols if h in INITIAL_MEASURES),
                                  get(row, "Tax ID"), get(row, "Petitioner City"), get(row, "Petitioner State"),
                                  get(row, "Industry (NAICS) Code"))
        elif {"EMPLOYER_NAME", "CASE_STATUS"} <= col.keys():
            for row in rows:
                rec = {"status": get(row, "CASE_STATUS").strip(), "visa": get(row, "VISA_CLASS").strip(),
                       "title": get(row, "JOB_TITLE").strip(), "soc": get(row, "SOC_CODE").strip(),
                       "wage": annualize(get(row, "WAGE_RATE_OF_PAY_FROM"), get(row, "WAGE_UNIT_OF_PAY")),
                       "level": _level(get(row, "PW_WAGE_LEVEL")), "employer": get(row, "EMPLOYER_NAME").strip(),
                       "city": _place(get(row, "EMPLOYER_CITY")), "state": _place(get(row, "EMPLOYER_STATE"))}
                for key in employer_keys(get(row, "EMPLOYER_NAME")):
                    idx.lca[key].append(rec)
            idx.lca_files.append(path.name)
        else:
            continue
        idx.files.append(path.name)
    return idx


def load_naics(path: Path) -> dict[str, str]:
    """NAICS code -> title from the optional local table (columns code,title); {} when it is absent."""
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh)
        return {str(r.get("code") or "").strip(): str(r.get("title") or "").strip() for r in reader
                if str(r.get("code") or "").strip() and str(r.get("title") or "").strip()}


# ------------------------------------------------------------------------------ lookup
def _title_similarity(a: set[str], title: str) -> float:
    b = set(normalize_title(title).split())
    return len(a & b) / len(a | b) if a and b else 0.0


@dataclass
class Entity:
    """One employer behind a name: rows sharing a tax id, or, without one, a state."""
    tax_id: str = ""
    states: Counter = field(default_factory=Counter)
    rows: list[dict[str, Any]] = field(default_factory=list)
    lca: list[dict[str, Any]] = field(default_factory=list)

    @property
    def state(self) -> str:
        return self.states.most_common(1)[0][0] if self.states else ""

    @property
    def names(self) -> set[str]:
        return {r["name"] for r in self.rows} | {r["employer"] for r in self.lca if r.get("employer")}

    @property
    def legal_keys(self) -> set[str]:
        return {legal_key(n) for n in self.names}

    @property
    def cities(self) -> set[str]:
        return {r["city"] for r in [*self.rows, *self.lca] if r.get("city")}

    def compatible(self, tax_id: str, state: str) -> bool:
        if tax_id and self.tax_id:
            return tax_id == self.tax_id                 # the petitioner's tax id outranks its address
        return not state or not self.states or state in self.states

    def add(self, item: dict[str, Any], kind: str) -> None:
        (self.rows if kind == "uscis" else self.lca).append(item)
        self.tax_id = self.tax_id or item.get("tax_id") or ""
        if item.get("state"):
            self.states[item["state"]] += max(int(item.get("approvals") or 0), 1)


def split_entities(index: H1bIndex, keys: Iterable[str]) -> list[Entity]:
    """Cluster every USCIS row and LCA filing under the matched name keys into entities. Most specific
    rows first (tax id, then state only); a row joins the one entity it agrees with (same tax id; without
    a tax id on either side, a state the entity files from),
    prefers an entity with its exact legal name when several agree, else starts a new one. Rows with
    neither tax id nor state join the only located entity if there is exactly one, else share one
    unlocated entity."""
    seen: set[int] = set()
    items: list[tuple[str, dict[str, Any]]] = []
    for key in sorted(keys):
        for kind, pool in (("uscis", index.rows.get(key, [])), ("lca", index.lca.get(key, []))):
            for item in pool:
                if id(item) not in seen:
                    seen.add(id(item))
                    items.append((kind, item))

    def tier(item: dict[str, Any]) -> int:
        tax, state = item.get("tax_id"), item.get("state")
        return 0 if tax and state else 1 if tax else 2 if state else 3

    entities: list[Entity] = []
    unlocated: Entity | None = None
    for kind, item in sorted(items, key=lambda ki: tier(ki[1])):
        tax, state = item.get("tax_id") or "", item.get("state") or ""
        name = legal_key(item.get("name") or item.get("employer") or "")
        if not tax and not state:
            located = [e for e in entities if e is not unlocated]
            if len(located) == 1 and unlocated is None:
                located[0].add(item, kind)
                continue
            if unlocated is None:
                unlocated = Entity()
                entities.append(unlocated)
            unlocated.add(item, kind)
            continue
        fits = [e for e in entities if e is not unlocated and e.compatible(tax, state)]
        if len(fits) > 1:
            fits = [e for e in fits if name in e.legal_keys] or [e for e in fits if not e.tax_id and not tax]
        if len(fits) == 1:
            fits[0].add(item, kind)
        else:
            fresh = Entity()
            fresh.add(item, kind)
            entities.append(fresh)
    return entities


def _certified(recs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in recs if r["status"].lower().startswith("certified") and r["visa"].upper() == "H-1B"]


def _lca_titles(certified: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Certified H-1B LCAs per job title (whitespace collapsed, spelling kept), most first, then by title."""
    counts = Counter(re.sub(r"\s+", " ", r["title"]).strip() for r in certified)
    return [{"title": t, "count": n} for t, n in sorted(counts.items(), key=lambda tn: (-tn[1], tn[0]))]


def _denials(rows: Iterable[dict[str, Any]], fys: list[int]) -> dict[int, int]:
    """{fy: denials} for the years whose USCIS rows all carry a figure; other years are left out."""
    out: dict[int, int] = {}
    rows = list(rows)
    for fy in fys:
        year = [r["denials"] for r in rows if r["fy"] == fy]
        if year and all(d is not None for d in year):
            out[fy] = sum(year)
    return out


def _lca_stats(certified: list[dict[str, Any]], title: str | None) -> dict[str, Any]:
    pool, basis = certified, "employer"
    if title:
        tokens = set(normalize_title(title).split())
        similar = [r for r in certified if _title_similarity(tokens, r["title"]) >= 0.5]
        if similar:
            pool, basis = similar, "title"
    wages = [r["wage"] for r in pool if r["wage"]]
    counts = Counter(r["level"] for r in pool if r["level"])
    level = "unknown"
    if counts:
        best = max(counts.values())
        level = next(lv for lv in LEVELS if counts.get(lv) == best)       # ties -> the lower level
    return {"lca_count": len(certified), "median_lca_wage": int(round(statistics.median(wages))) if wages else None,
            "wage_level_est": level, "lca_basis": basis if pool else None, "lca_titles": _lca_titles(certified)}


def _entity_row(e: Entity, fys: list[int], match: str, naics_names: dict[str, str] | None = None) -> dict[str, Any]:
    by_fy = {fy: sum(r["approvals"] for r in e.rows if r["fy"] == fy) for fy in fys}
    weight = Counter()
    for r in e.rows:
        weight[r["name"]] += r["approvals"] or 1
    city_votes = Counter(r["city"] for r in e.rows if r["city"]) or Counter(r["city"] for r in e.lca if r.get("city"))
    naics = Counter(r["naics"] for r in e.rows if r["naics"])
    code = naics.most_common(1)[0][0] if naics else None
    row = {"legal_name": max(sorted(weight), key=lambda n: weight[n]) if weight else sorted(e.names)[0],
           "names": sorted(e.names), "city": city_votes.most_common(1)[0][0] if city_votes else None,
           "state": e.state or None, "naics": code,
           "tax_id": e.tax_id or None, "h1b_by_fy": by_fy, "h1b_approvals_3y": sum(by_fy.values()),
           "lca_count": len(_certified(e.lca)), "match": match}
    if code and (naics_names or {}).get(code):                  # a name only from the local table, with its source
        row.update(naics_name=naics_names[code], naics_source=NAICS_TABLE)
    return row


def lookup(index: H1bIndex, company: str, fys: list[int] | None = None, aliases: Iterable[str] = (),
           title: str | None = None, today: date | None = None, *, state: str | None = None,
           city: str | None = None, merge: bool = False) -> dict[str, Any]:
    """Per-entity enrichment for one employer (see the module docstring). merge=True: the old name-key sum."""
    fys = list(fys) if fys else published_years(index, today or date.today())
    aliases = [a for a in aliases if a]
    if merge:
        res = lookup_merged(index, company, fys, aliases, title)
        res.update(entities=[], ambiguous=False, note="--merge: every name-key match summed, entities not split")
        return _mark_missing(res, index, fys)
    targets = [k for name in [company, *aliases] if name for k in employer_keys(name)]
    keys_all = set(index.approvals) | set(index.lca)
    matched = [k for k in keys_all if any(_key_matches(t, k) for t in targets)]
    entities = split_entities(index, matched)
    want_state, want_city = _place(state), _place(city)
    candidates = [e for e in entities if (not want_state or not e.states or want_state in e.states)
                  and (not want_city or not e.cities or want_city in e.cities)]
    named = {legal_key(n) for n in [company, *aliases]}
    primary = [e for e in candidates if e.legal_keys & named]
    ambiguous = False
    if not primary:
        if len(candidates) == 1:
            primary = candidates
        elif len(candidates) > 1:
            ambiguous = True
    rows = [_entity_row(e, fys, "primary" if e in primary else "possible_other_entity", index.naics)
            for e in entities]
    rows.sort(key=lambda r: (r["match"] != "primary", -r["h1b_approvals_3y"], r["legal_name"]))
    others = sum(1 for r in rows if r["match"] != "primary")
    by_fy = {fy: sum(r["approvals"] for e in primary for r in e.rows if r["fy"] == fy) for fy in fys}
    total = sum(by_fy.values())
    names = sorted({n for e in primary for n in e.names})
    stats = _lca_stats(_certified(r for e in primary for r in e.lca), title)
    denials = _denials((r for e in primary for r in e.rows), fys)
    note, evidence = None, None
    if ambiguous:
        places = "; ".join(f"{r['legal_name']}, {r['city'] or '?'} {r['state'] or '?'}: {r['h1b_approvals_3y']}"
                           for r in rows)
        note = (f"{len(rows)} entities share this name ({places}). Pass the legal name with its suffix, --alias, "
                f"--state or --city to pick one, or --merge for the old summed total.")
        evidence = (f"USCIS H-1B hub: {len(rows)} entities named like {company!r} ({places}); not attributed: "
                    f"set h1b_names in targets/companies.yaml")
        by_fy = {fy: None for fy in fys}
        stats = {"lca_count": None, "median_lca_wage": None, "wage_level_est": "unknown", "lca_basis": None,
                 "lca_titles": None}
        denials = {}
    elif others:
        note = f"{others} possible_other_entity row(s) share the name and are not counted"
    if total and not ambiguous:
        where = sorted({f"{e_row['city']}, {e_row['state']}" for e_row in rows
                        if e_row["match"] == "primary" and e_row["state"] and e_row["city"]})
        detail = "; ".join(names) + (f" at {' / '.join(where)}" if where else "")
        if others:
            detail += f"; {others} look-alike entit{'y' if others == 1 else 'ies'} not counted"
        evidence = f"USCIS H-1B hub: {total:,} approvals FY{fys[0]}-FY{fys[-1]} ({detail})"
    return _mark_missing({
        "employer_matches": names,
        "h1b_approvals_3y": None if ambiguous else total,
        "h1b_by_fy": by_fy,
        **({"denials": denials} if denials else {}),
        **stats,
        "sponsorship": "yes" if total > 0 and not ambiguous else "unknown",
        "sponsorship_evidence": evidence,
        "fiscal_years": fys,
        "ambiguous": ambiguous,
        "note": note,
        "entities": rows,
    }, index, fys)


def _mark_missing(res: dict[str, Any], index: H1bIndex, fys: list[int]) -> dict[str, Any]:
    """Absent data is "unknown", never 0 (an empty cache must never print h1b_approvals_3y 0 for a dossier to repeat). A fiscal year no cached USCIS file covers is "unknown" in h1b_by_fy and makes the 3-year total
    "unknown"; no cached LCA file makes lca_count "unknown" and the wage fields empty. Approvals found in the
    covered years still make sponsorship "yes" (positive evidence), with the uncovered years named. `missing`
    lists each absent source with the reason; it is [] when the cache answers every field."""
    missing = index.missing(fys)
    res["missing"] = missing
    for gap in missing:
        if gap["source"] == "uscis_h1b_hub":
            gone = gap["fiscal_years"]
            if res.get("h1b_by_fy") is not None:
                res["h1b_by_fy"] = {fy: ("unknown" if fy in gone else v) for fy, v in res["h1b_by_fy"].items()}
            if res.get("h1b_approvals_3y") is not None:
                res["h1b_approvals_3y"] = UNKNOWN
            if res.get("sponsorship_evidence") and res.get("sponsorship") == "yes":
                res["sponsorship_evidence"] += f"; not cached: {', '.join(f'FY{fy}' for fy in gone)}"
            for e in res.get("entities") or []:
                e["h1b_by_fy"] = {fy: ("unknown" if fy in gone else v) for fy, v in e["h1b_by_fy"].items()}
                e["h1b_approvals_3y"] = UNKNOWN
        elif gap["source"] == "dol_lca":
            if res.get("lca_count") is not None:
                res["lca_count"] = UNKNOWN
            res.update(median_lca_wage=None, wage_level_est="unknown", lca_basis=None, lca_titles=None)
            for e in res.get("entities") or []:
                e["lca_count"] = UNKNOWN
    return res


def lookup_merged(index: H1bIndex, company: str, fys: list[int] | None = None, aliases: Iterable[str] = (),
                  title: str | None = None, today: date | None = None) -> dict[str, Any]:
    """The older lookup: every name-key match summed as one employer (lookup --merge)."""
    fys = list(fys) if fys else published_years(index, today or date.today())
    targets = [k for name in [company, *aliases] if name for k in employer_keys(name)]
    keys_all = set(index.approvals) | set(index.lca)
    matched = sorted(k for k in keys_all if any(_key_matches(t, k) for t in targets))
    by_fy = {fy: 0 for fy in fys}
    names: set[str] = set()
    for k in matched:
        for fy in fys:
            slot = index.approvals.get(k, {}).get(fy)
            if slot:
                by_fy[fy] += slot["approvals"]
                names |= slot["names"]
    total = sum(by_fy.values())
    certified = [r for k in matched for r in index.lca.get(k, [])
                 if r["status"].lower().startswith("certified") and r["visa"].upper() == "H-1B"]
    pool, basis = certified, "employer"
    if title:
        tokens = set(normalize_title(title).split())
        similar = [r for r in certified if _title_similarity(tokens, r["title"]) >= 0.5]
        if similar:
            pool, basis = similar, "title"
    wages = [r["wage"] for r in pool if r["wage"]]
    counts = Counter(r["level"] for r in pool if r["level"])
    level = "unknown"
    if counts:
        best = max(counts.values())
        level = next(lv for lv in LEVELS if counts.get(lv) == best)       # ties -> the lower level
    evidence = None
    if total:
        evidence = f"USCIS H-1B hub: {total:,} approvals FY{fys[0]}-FY{fys[-1]} ({'; '.join(sorted(names))})"
    seen: dict[int, dict[str, Any]] = {id(r): r for k in matched for r in index.rows.get(k, [])}
    denials = _denials(seen.values(), fys)
    return {
        "employer_matches": sorted(names),
        "h1b_approvals_3y": total,
        "h1b_by_fy": by_fy,
        **({"denials": denials} if denials else {}),
        "lca_count": len(certified),
        "lca_titles": _lca_titles(certified),
        "median_lca_wage": int(round(statistics.median(wages))) if wages else None,
        "wage_level_est": level,
        "lca_basis": basis if pool else None,
        "sponsorship": "yes" if total > 0 else "unknown",
        "sponsorship_evidence": evidence,
        "fiscal_years": fys,
    }


US_STATES = {"alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
             "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
             "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
             "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
             "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
             "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
             "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
             "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
             "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
             "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC"}
STATE_CODES = set(US_STATES.values())


def posting_state(location: Any) -> str | None:
    """The one US state a posting names ("San Francisco, CA" -> CA; "Boston, Massachusetts" -> MA), else None (remote,
    unnamed, or more than one state)."""
    text = str(location or "")
    if ";" in text or " | " in text:              # a multi-location posting: no single state to check
        return None
    found = {m.group(1) for m in re.finditer(r",\s*([A-Z]{2})\b", text) if m.group(1) in STATE_CODES}
    low = text.lower()
    found |= {code for name, code in US_STATES.items() if re.search(rf"\b{name}\b", low)
              and not (name == "washington" and "washington, dc" in low)}
    return found.pop() if len(found) == 1 else None


TRUSTED_SECTORS = {"51", "52", "54", "55"}     # information, finance, professional and technical services, holding companies


def _sector(naics: Any) -> str:
    digits = re.match(r"\s*(\d{2})", str(naics or ""))
    return digits.group(1) if digits else ""


def _name_doubt(company: str, primary_rows: list[dict[str, Any]]) -> bool:
    """ L-03e51971: a short name (one word of six letters or fewer) or a weak match (the legal name does not begin
    with the company's name, e.g. a d/b/a) can be another employer."""
    key = legal_key(company)
    short = len(key.split()) == 1 and len(key) <= 6
    weak = not any(legal_key(r.get("legal_name") or "").startswith(key) for r in primary_rows)
    return short or weak


DICT_PATH = Path("/usr/share/dict/words")
SMALL_FILER = 20     # approvals in 3 years under which a short or d/b/a-only match must be located (a short name can match many unrelated filers)


@functools.lru_cache(maxsize=1)
def _dictionary() -> frozenset[str]:
    """Lower-case dictionary words (proper nouns excluded), or empty when the system list is absent."""
    try:
        return frozenset(w.strip() for w in DICT_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
                         if w.strip().islower() and len(w.strip()) >= 3)
    except OSError:
        return frozenset()


def _short_name(company: str) -> bool:
    """One word of four letters or fewer, or one dictionary word ("Sim", "Ema", "Scale")."""
    words = legal_key(company).split()
    letters = re.sub(r"[^a-z]", "", "".join(words))
    return len(words) == 1 and bool(letters) and (len(letters) <= 4 or letters in _dictionary())


def _dba_only(company: str, primary_rows: list[dict[str, Any]]) -> bool:
    """Every primary entity matches the company only through a "D B A <name>" alias, never its legal name."""
    targets = employer_keys(company)
    if not primary_rows or not targets:
        return False
    for r in primary_rows:
        for name in r.get("names") or [r.get("legal_name") or ""]:
            parts = employer_keys(name)
            if parts and any(_key_matches(t, parts[0]) for t in targets):
                return False
    return True


def posting_states(location: Any) -> set[str]:
    """Every US state a posting names, multi-location postings included ("Boston, MA | Foster City, CA" -> MA, CA)."""
    text = str(location or "")
    found = {m.group(1) for m in re.finditer(r"(?:,|\b)\s*([A-Z]{2})\b", text) if m.group(1) in STATE_CODES}
    low = text.lower()
    found |= {code for name, code in US_STATES.items() if re.search(rf"\b{name}\b", low)
              and not (name == "washington" and "washington, dc" in low)}
    return found


def _located(rec: dict[str, Any], primary_rows: list[dict[str, Any]], company: dict[str, Any]) -> bool:
    """A primary entity files from a state or city the posting names (any of them, for a multi-location posting).
    (SETUP's rule also allowed the company's domain; the hub files carry no domain, so a domain cannot confirm an
    entity: ema.co would "confirm" EMA, INC. of St. Paul. The override for a real employer filing elsewhere is
    h1b_names on the watchlist.)"""
    loc = str(rec.get("location") or "")
    states, text = posting_states(loc), " " + re.sub(r"[^A-Z0-9]+", " ", _place(loc)) + " "
    for r in primary_rows:
        if str(r.get("state") or "").upper() in states:
            return True
        city = re.sub(r"[^A-Z0-9]+", " ", _place(r.get("city"))).strip()
        if city and f" {city} " in text:
            return True
    return False


def _short_name_guard(rec: dict[str, Any], res: dict[str, Any], company: dict[str, Any]) -> str | None:
    """Evidence for an unattributed hub match (rules 1 and 2), else None. Callers skip it
    when the watchlist sets h1b_names (rule 3)."""
    name = str(rec.get("company") or rec.get("company_slug") or "")
    primary = [r for r in res.get("entities") or [] if r.get("match") == "primary"]
    filed = sum(v for r in primary for v in (r.get("h1b_by_fy") or {}).values() if isinstance(v, int))
    if filed >= SMALL_FILER:                # counted from the years on file (a year with no cache is not zero, skipped)
        return None                     # a large filer under the name is the known employer (a well-known employer)
    where = "; ".join(f"{r['legal_name']}, {r.get('city') or '?'} {r.get('state') or '?'}" for r in primary)
    if _dba_only(name, primary):
        return (f"USCIS H-1B hub: the name matches only a d/b/a alias ({where}); not attributed: set h1b_names in "
                f"targets/companies.yaml if it is the same employer")
    if _short_name(name) and not _located(rec, primary, company):
        return (f"USCIS H-1B hub: short-name match outside the posting's location ({where}; the posting is in "
                f"{rec.get('location') or 'an unnamed place'}); not attributed: set h1b_names in targets/companies.yaml "
                f"if it is the same employer")
    return None


def _jd_level(rec: dict[str, Any]) -> bool:
    ev = str(rec.get("sponsorship_evidence") or "")
    return rec.get("sponsorship") in ("yes", "no") and bool(ev) and not ev.startswith(("USCIS", "targets/"))


def enrich(records: list[dict[str, Any]], index: H1bIndex, companies: list[dict[str, Any]] | None = None,
           today: date | None = None, merge: bool = False) -> list[dict[str, Any]]:
    """Set sponsorship + H-1B/LCA fields on job records. JD-level evidence (a "no sponsorship" line,
    an HN visa flag) is never overwritten by employer history. Only the primary entity counts (see
    lookup); an ambiguous name leaves sponsorship "unknown" with evidence asking for h1b_names. A source the
    cache lacks leaves h1b_approvals_3y / lca_count None (lookup says "unknown"), never 0."""
    fys = published_years(index, today or date.today())
    by_slug = {c.get("slug"): c for c in companies or []}
    for rec in records:
        c = by_slug.get(rec.get("company_slug"), {})
        aliases = c.get("h1b_names") or c.get("h1b_aliases") or []
        res = lookup(index, rec.get("company") or rec.get("company_slug") or "", fys=fys,
                     aliases=aliases if isinstance(aliases, list) else [aliases], title=rec.get("title"), merge=merge)
        # a job record's counts are numbers or empty: an uncached source leaves them None, never 0
        rec["h1b_approvals_3y"] = None if res["h1b_approvals_3y"] == UNKNOWN else res["h1b_approvals_3y"]
        rec["lca_count"] = None if res["lca_count"] == UNKNOWN else res["lca_count"]
        rec["median_lca_wage"] = res["median_lca_wage"]
        rec["wage_level_est"] = res["wage_level_est"]
        if _jd_level(rec):
            continue
        stance = str(c.get("sponsorship_stance") or "").lower().replace("_", "-")
        recent = c.get("h1b_approvals_recent")
        if stance == "cap-exempt":
            rec["sponsorship"], rec["sponsorship_evidence"] = "cap_exempt", "targets/companies.yaml: cap-exempt employer"
        elif stance == "does-not-sponsor":
            rec["sponsorship"], rec["sponsorship_evidence"] = "no", "targets/companies.yaml: sponsorship_stance does-not-sponsor"
        elif res["sponsorship"] == "yes" and not aliases and (guard := _short_name_guard(rec, res, c)):
            rec["sponsorship"], rec["sponsorship_evidence"] = "unknown", guard
        elif res["sponsorship"] == "yes" and _out_of_state(rec, res, c, aliases):
            primary = [r for r in res["entities"] if r["match"] == "primary"]
            where = "; ".join(f"{r['legal_name']}, {r['city'] or '?'} {r['state'] or '?'}" for r in primary)
            rec["sponsorship"] = "unknown"
            rec["sponsorship_evidence"] = (f"USCIS H-1B hub: name match outside the posting's state ({where}; the posting is in "
                                           f"{posting_state(rec.get('location'))}); not attributed: set h1b_names in "
                                           f"targets/companies.yaml if it is the same employer")
        elif res["sponsorship"] == "yes":
            rec["sponsorship"], rec["sponsorship_evidence"] = "yes", res["sponsorship_evidence"]
        elif isinstance(recent, (int, float)) and recent > 0:
            rec["sponsorship"] = "yes"
            rec["sponsorship_evidence"] = f"targets/companies.yaml: h1b_approvals_recent {int(recent)}"
        else:
            rec["sponsorship"] = "unknown"
            rec["sponsorship_evidence"] = res["sponsorship_evidence"] if res.get("ambiguous") else None
    return records


def _out_of_state(rec: dict[str, Any], res: dict[str, Any], company: dict[str, Any], aliases: Any) -> bool:
    """True when a hub match should not count as this posting's employer: the company is not on the watchlist (no
    curated entry, no h1b_names), its name is short or weakly matched, the posting names one US state, no primary
    entity files from it, and none files in a TRUSTED_SECTORS industry (big tech and professional
    employers often file from their home state, e.g. a firm's head office in one state for a seat in another)."""
    if company or aliases:
        return False
    state = posting_state(rec.get("location"))
    primary = [r for r in res.get("entities") or [] if r.get("match") == "primary"]
    if not state or not primary or any(str(r.get("state") or "").upper() == state for r in primary):
        return False
    if any(_sector(r.get("naics")) in TRUSTED_SECTORS for r in primary):
        return False                              # a tech, finance or professional employer filing from its home state
    return _name_doubt(str(rec.get("company") or rec.get("company_slug") or ""), primary)


# ------------------------------------------------------------------------------ downloads
def pivot_hub_long(src: Path, dest: Path) -> int:
    """Tableau long export (one row per measure) -> compact employer rows. Returns rows written."""
    groups: dict[tuple, dict[str, int]] = {}
    has_denials = False                         # no Denial measure in the export: write blanks, never 0
    header, rows = _read_rows(src)
    col = {h: i for i, h in enumerate(header)}

    def get(row: list[str], name: str) -> str:
        return _cell(row, col, name).strip()

    for row in rows:
        key = (get(row, "Fiscal Year"), get(row, "Employer (Petitioner) Name"), get(row, "Tax ID"),
               get(row, "Petitioner City"), get(row, "Petitioner State"), get(row, "Petitioner Zip Code"),
               get(row, "Industry (NAICS) Code"), get(row, "Line by line"))
        m, v = get(row, "Measure Names"), _int(get(row, "Measure Values"))
        g = groups.setdefault(key, {"approvals": 0, "denials": 0, "initial": 0, "continuing": 0})
        if m.endswith("Approval"):
            g["approvals"] += v
            g["initial" if m in INITIAL_MEASURES else "continuing"] += v
        elif m.endswith("Denial"):
            g["denials"] += v
            has_denials = True
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".csv.part")
    n = 0
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(COMPACT_COLUMNS)
        for key in sorted(groups, key=lambda k: (k[1], k[2], k[3], k[7])):
            g = groups[key]
            if not key[1] or not (g["approvals"] or g["denials"]):
                continue
            w.writerow([key[0], key[1], key[2], key[3], key[4], key[5], key[6], g["approvals"],
                        g["denials"] if has_denials else "",
                        g["initial"], g["continuing"]])
            n += 1
    tmp.replace(dest)
    return n


_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _first_sheet(z: zipfile.ZipFile) -> str:
    try:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rid = wb.find(f"{_NS}sheets/{_NS}sheet").get(f"{_REL_NS}id")
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        for rel in rels:
            if rel.get("Id") == rid:
                target = rel.get("Target").lstrip("/")
                return target if target.startswith("xl/") else f"xl/{target}"
    except (KeyError, AttributeError, ET.ParseError):
        pass
    return "xl/worksheets/sheet1.xml"


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref or "")
    n = 0
    for ch in (letters.group(0) if letters else ""):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def iter_xlsx_rows(path: Path) -> Iterator[list[str]]:
    """Stream rows of the first worksheet (stdlib only; shared strings, inline strings, numbers)."""
    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            with z.open("xl/sharedStrings.xml") as fh:
                for _ev, el in ET.iterparse(fh):
                    if el.tag == f"{_NS}si":
                        shared.append("".join(t.text or "" for t in el.iter(f"{_NS}t")))
                        el.clear()
        with z.open(_first_sheet(z)) as fh:
            for _ev, el in ET.iterparse(fh):
                if el.tag != f"{_NS}row":
                    continue
                cells: dict[int, str] = {}
                for c in el.findall(f"{_NS}c"):
                    idx = _col_index(c.get("r") or "") if c.get("r") else len(cells)
                    kind, v = c.get("t"), c.find(f"{_NS}v")
                    if kind == "s":
                        val = shared[int(v.text)] if v is not None and v.text else ""
                    elif kind == "inlineStr":
                        val = "".join(t.text or "" for t in c.iter(f"{_NS}t"))
                    elif kind == "b":
                        val = "TRUE" if v is not None and v.text == "1" else "FALSE"
                    else:
                        val = v.text if v is not None and v.text is not None else ""
                    cells[idx] = val
                if cells:
                    yield [cells.get(i, "") for i in range(max(cells) + 1)]
                el.clear()


def _excel_date(value: str) -> str:
    v = _num(value)
    if v is None or not re.fullmatch(r"\d+(?:\.\d+)?", str(value).strip()):
        return value
    return (date(1899, 12, 30) + timedelta(days=int(v))).isoformat()


def lca_xlsx_to_csv(src: Path, dest: Path, soc_prefixes: Iterable[str] = ("15-",), visa: str = "H-1B") -> int:
    rows = iter_xlsx_rows(src)
    header = [h.strip().upper() for h in next(rows, [])]
    col = {h: i for i, h in enumerate(header)}
    keep = [c for c in LCA_COLUMNS if c in col]
    prefixes = tuple(soc_prefixes)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".csv.part")
    n = 0
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(keep)
        for row in rows:
            if "VISA_CLASS" in col and _cell(row, col, "VISA_CLASS").strip().upper() != visa:
                continue
            if prefixes and "SOC_CODE" in col and not _cell(row, col, "SOC_CODE").strip().startswith(prefixes):
                continue
            w.writerow([_excel_date(_cell(row, col, c)) if c.endswith("_DATE") else _cell(row, col, c) for c in keep])
            n += 1
    tmp.replace(dest)
    return n


def _manifest(cache: Path) -> dict[str, Any]:
    p = cache / "_manifest.json"
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"files": {}}


def download(root: Path, http: Any, fys: Iterable[int] | None = None, archive_fys: Iterable[int] = (),
             lca_xlsx: Iterable[Path] = (), lca_quarters: Iterable[tuple[int, int]] = (),
             soc_prefixes: Iterable[str] = ("15-",), now: datetime | None = None,
             dry_run: bool = False) -> dict[str, list[str]]:
    now = now or datetime.now(timezone.utc)
    cache = cache_dir(root)
    fys = list(fiscal_years(now.date()) if fys is None else fys)
    report: dict[str, list[str]] = {"written": [], "errors": []}
    manifest = _manifest(cache)

    def record(name: str, source: str, rows: int) -> None:
        manifest["files"][name] = {"source": source, "fetched_at": iso_utc(now), "rows": rows}
        report["written"].append(name)

    for fy in fys:
        url, name = hub_url(fy), f"uscis_h1b_fy{fy}.csv"
        if dry_run:
            print(f"would fetch {url} -> {name}")
            continue
        raw = cache / f"_raw_hub_fy{fy}.csv"
        try:
            http.download_to(url, raw)
            record(name, url, pivot_hub_long(raw, cache / name))
        except Exception as exc:
            report["errors"].append(f"{name}: {exc}")
            print(f"USCIS hub FY{fy} failed: {exc} (page: {HUB_PAGE})")
        finally:
            raw.unlink(missing_ok=True)
    for fy in archive_fys:
        url, name = ARCHIVE_CSV.format(fy=fy), f"h1b_datahubexport-{fy}.csv"
        if dry_run:
            print(f"would fetch {url} -> {name}")
            continue
        try:
            http.download_to(url, cache / name)
            record(name, url, sum(1 for _ in (cache / name).open(encoding="utf-8", errors="replace")) - 1)
        except Exception as exc:
            report["errors"].append(f"{name}: {exc}")
    for src in lca_xlsx:
        src = Path(src)
        name = f"lca_{src.stem}.csv"
        if dry_run:
            print(f"would convert {src} -> {name}")
            continue
        record(name, f"file:{src.name} (from {DOL_PAGE})", lca_xlsx_to_csv(src, cache / name, soc_prefixes))
    for fy, q in lca_quarters:
        url, name = lca_url(fy, q), f"lca_LCA_Disclosure_Data_FY{fy}_Q{q}.csv"
        if dry_run:
            print(f"would fetch {url} -> {name}")
            continue
        raw = cache / f"_raw_lca_fy{fy}_q{q}.xlsx"
        try:
            http.download_to(url, raw)
            record(name, url, lca_xlsx_to_csv(raw, cache / name, soc_prefixes))
        except Exception as exc:
            report["errors"].append(f"{name}: {exc}")
            print(f"dol.gov refused {url} ({exc}). Open {DOL_PAGE} in a browser, save "
                  f"LCA_Disclosure_Data_FY{fy}_Q{q}.xlsx, then run: "
                  f"python h1b_lookup.py fetch --fy --lca-xlsx <saved file>")
        finally:
            raw.unlink(missing_ok=True)
    if report["written"] and not dry_run:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (cache / "README.md").write_text(cache_readme(manifest), encoding="utf-8")
    return report


def cache_readme(manifest: dict[str, Any]) -> str:
    """legal/evidence/h1b/README.md: where every cached file came from and when (rewritten by each fetch)."""
    files = manifest.get("files") or {}
    lines = [
        "# H-1B and LCA cache (written by `scripts/h1b_lookup.py fetch`; do not hand-edit)",
        "",
        "Public U.S. government data, fetched one request per file with the User-Agent",
        f"`{FETCH_USER_AGENT}`. The data files here are gitignored (large, rebuildable); this README is the",
        "tracked record of what was fetched. `h1b_lookup.py lookup` reports a count as `unknown` (and exits 3",
        "unless `--allow-missing`) for any source or fiscal year not cached here: absent data is never 0.",
        "",
        "## Sources",
        "",
        f"- USCIS H-1B Employer Data Hub (hub page: {HUB_PAGE}). FY2024 and later: the hub's Tableau CSV export,",
        f"  one file per fiscal year, `{HUB_CSV}?Fiscal%20Year%20%20%20=<FY>`, pivoted to one row per",
        "  employer (`uscis_h1b_fy<FY>.csv`). FY2009-FY2023: the archived files",
        f"  `{ARCHIVE_CSV.format(fy='<FY>')}` (`fetch --archive-fy <FY>`).",
        "  Counts are petitions approved or denied per fiscal year (October 1 to September 30); the current",
        "  fiscal year is partial until USCIS publishes its last quarter.",
        f"- DOL OFLC LCA disclosure data ({DOL_PAGE}): quarterly xlsx files, one row per LCA case for every",
        "  visa class and occupation. dol.gov refuses scripted requests (HTTP 403 on a GET and on a HEAD",
        "  request, so the file size could not be checked by script); `fetch` tries them only with",
        "  `--lca-quarter`. Nothing else fetches them: the step below is yours, in your browser.",
        "",
        "## Manual step: the DOL LCA file (you, in a browser)",
        "",
        f"1. Open {DOL_PAGE} and find the disclosure data files for the LCA programs (H-1B, H-1B1, E-3).",
        "   The page's headings were not readable by script (the 403 above); the file name below is what matters.",
        "2. Save exactly one file: the newest `LCA_Disclosure_Data_FY<FY>_Q<Q>.xlsx` listed (the highest fiscal",
        "   year, then the highest quarter), keeping DOL's file name: the cache file is named after it",
        "   (`lca_LCA_Disclosure_Data_FY<FY>_Q<Q>.csv`) and `_manifest.json` records it. Not the record layout",
        "   PDF, and not the PERM, H-2A, H-2B or prevailing-wage files: only the LCA xlsx is read. Save it",
        "   outside the repo (for example `~/Downloads/`); it is not needed after the conversion.",
        "3. From your data folder, run",
        "   `python3 scripts/h1b_lookup.py fetch --fy --lca-xlsx ~/Downloads/LCA_Disclosure_Data_FY<FY>_Q<Q>.xlsx`",
        "   (`--fy` with no years skips the USCIS refetch; the conversion keeps H-1B rows with SOC 15-*).",
        "4. Check: this README's Files table gains the `lca_...csv` row, and",
        "   `python3 scripts/h1b_lookup.py lookup \"<employer>\"` no longer lists `dol_lca` under `missing`.",
        "",
        "`lookup` counts every row of every cached `lca_*.csv`. Before adding a second quarter of the same fiscal",
        "year, read the page's note on whether the quarterly files are cumulative; if they are, keep only the",
        "newest (delete the older `lca_*.csv` here), or the counts double.",
        "",
        "## Files (from `_manifest.json`)",
        "",
        "| File | Rows | Fetched (UTC) | Source |",
        "|---|---|---|---|",
    ]
    for name in sorted(files):
        f = files[name]
        lines.append(f"| `{name}` | {f.get('rows')} | {f.get('fetched_at')} | {f.get('source')} |")
    if not files:
        lines.append("| (none) | | | |")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------ CLI
def _today(value: str | None) -> date:
    return date.fromisoformat(value) if value else date.today()


def _vintage_lines(cache: Path) -> list[str]:
    try:
        files = json.loads((cache / "_manifest.json").read_text(encoding="utf-8")).get("files") or {}
    except (OSError, ValueError):
        return ["- no _manifest.json in legal/evidence/h1b/: the cache vintage is unknown"]
    return [f"- {name}: fetched {f.get('fetched_at') or 'unknown'}, {f.get('rows') if f.get('rows') is not None else '?'} "
            f"rows, {f.get('source') or 'source unknown'}" for name, f in sorted(files.items())] or ["- (manifest lists no files)"]


def save_evidence(root: Path, name: str, argv: list[str], res: dict[str, Any], code: int, day: date) -> Path:
    """F12: research/targets/evidence/<company>-h1b-<date>.md, a citable file holding the command, the cache vintage,
    the exit status and the full output. A same-day file with other content is never overwritten (-2, -3 ...)."""
    import os
    import shlex
    import tempfile
    from company_index import canonical
    shown, skip = [], False
    for a in argv:                                       # the command as a reader reruns it: no --root, no flag
        if skip:
            skip = False
            continue
        if a == "--root":
            skip = True
            continue
        if a.startswith("--root=") or a == "--save-evidence":
            continue
        shown.append(a)
    gaps = [f"- {g['source']} unknown: {g['reason']}" for g in res.get("missing") or []] or ["- none"]
    text = "\n".join([
        f"# H-1B lookup, read only, {day.isoformat()}: {name}",
        f"# Command: python3 scripts/h1b_lookup.py {shlex.join(shown)}",
        f"# Result: exit {code}",
        "# Cache vintage (legal/evidence/h1b/_manifest.json):", *[f"# {ln}" for ln in _vintage_lines(cache_dir(root))],
        "# Sources the cache cannot answer:", *[f"# {ln}" for ln in gaps],
        "# Public USCIS and DOL data, reduced by scripts/h1b_lookup.py; the full output follows.",
        "", "```json", json.dumps(res, indent=2, default=str), "```", ""])
    folder = root / "research" / "targets" / "evidence"
    folder.mkdir(parents=True, exist_ok=True)
    stem, n = f"{canonical(name, root) or 'unknown'}-h1b-{day.isoformat()}", 1
    path = folder / f"{stem}.md"
    while path.exists() and path.read_text(encoding="utf-8") != text:
        n += 1
        path = folder / f"{stem}-{n}.md"
    if not path.exists():
        fd, tmp = tempfile.mkstemp(dir=folder)
        os.write(fd, text.encode("utf-8"))
        os.close(fd)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    return path


def main(argv: list[str] | None = None, http: Any = None, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    lk = sub.add_parser("lookup", help="print the enrichment for one employer as JSON",
                        description="Per-entity H-1B/LCA enrichment. Rows sharing a name key are split into "
                                    "entities by tax id and state; only match=primary entities are counted, "
                                    "look-alikes are listed as match=possible_other_entity. An unresolved "
                                    "name is ambiguous (totals null, sponsorship unknown).")
    lk.add_argument("name", help="employer; the legal name with its suffix (\"Quillstone Systems, Inc.\") picks "
                                 "that entity exactly")
    lk.add_argument("--alias", action="append", default=[], help="another legal name of the same employer to count")
    lk.add_argument("--title", default=None)
    lk.add_argument("--state", default=None, help="keep entities in this state (2 letters), e.g. CA")
    lk.add_argument("--city", default=None, help="keep entities with a filing in this city")
    lk.add_argument("--merge", action="store_true",
                    help="old behavior: sum every name-key match as one employer (no entity split)")
    lk.add_argument("--today", default=None)
    lk.add_argument("--save-evidence", action="store_true",
                    help="also write research/targets/evidence/<company>-h1b-<date>.md (command, cache vintage, "
                         "exit status, full output) for a dossier to cite (F12)")
    lk.add_argument("--allow-missing", action="store_true",
                    help="exit 0 even when a source is not cached (its counts print as \"unknown\"); "
                         "without it such a lookup exits 3")
    en = sub.add_parser("enrich", help="write sponsorship fields onto jobs/*.md")
    en.add_argument("--today", default=None)
    en.add_argument("--merge", action="store_true", help="old behavior: sum look-alike entities")
    en.add_argument("--dry-run", action="store_true")
    dl = sub.add_parser("fetch", aliases=["download"],
                        help="fetch/refresh the caches in legal/evidence/h1b/ (public USCIS hub files, last 3 FY)",
                        description="Download the public USCIS H-1B Employer Data Hub files (one request per file, "
                                    "no retries) into legal/evidence/h1b/ and write its README.md and "
                                    "_manifest.json. DOL LCA files are converted from a browser-saved xlsx "
                                    "(--lca-xlsx): dol.gov refuses scripted downloads.")
    dl.add_argument("--fy", type=int, nargs="*", default=None,
                    help="hub fiscal years (default: last 3; `--fy` with no years fetches none)")
    dl.add_argument("--archive-fy", type=int, nargs="*", default=[], help="archived FY2009-2023 files")
    dl.add_argument("--lca-xlsx", type=Path, action="append", default=[], help="a saved DOL LCA xlsx")
    dl.add_argument("--lca-quarter", action="append", default=[], help="try dol.gov directly, e.g. 2026Q3")
    dl.add_argument("--soc-prefix", action="append", default=None, help="SOC prefixes to keep (default 15-)")
    dl.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    cache = cache_dir(args.root)
    if args.cmd == "lookup":
        res = lookup(load_index(cache), args.name, aliases=args.alias, title=args.title, today=_today(args.today),
                     state=args.state, city=args.city, merge=args.merge)
        print(json.dumps(res, indent=2, default=str))
        if args.save_evidence:
            code = 3 if res["missing"] and not args.allow_missing else 0
            saved = save_evidence(args.root, args.name, list(sys.argv[1:] if argv is None else argv), res, code,
                                  _today(args.today))
            try:
                shown = saved.relative_to(args.root)
            except ValueError:
                shown = saved
            print(f"h1b lookup: evidence saved to {shown}", file=sys.stderr)
        if res["missing"]:
            for gap in res["missing"]:
                print(f"h1b lookup: {gap['source']} unknown: {gap['reason']}", file=sys.stderr)
            if not args.allow_missing:
                print("h1b lookup: exit 3 (a source is not cached; pass --allow-missing to accept unknown)",
                      file=sys.stderr)
                return 3
        return 0
    if args.cmd == "enrich":
        index = load_index(cache)
        jobs = list(load_jobs(args.root).values())
        fields = ("sponsorship", "sponsorship_evidence", "h1b_approvals_3y", "lca_count", "median_lca_wage",
                  "wage_level_est")
        before = {j["id"]: {f: j.get(f) for f in fields} for j in jobs}
        enrich(jobs, index, load_companies(args.root), today=_today(args.today), merge=args.merge)
        changed = [j for j in jobs if {f: j.get(f) for f in fields} != before[j["id"]]]
        if not args.dry_run:
            for j in changed:
                save_job(args.root, j)
        note = " (cache empty: run `fetch`)" if index.empty else ""
        print(f"h1b enrich: {len(jobs)} jobs, {len(changed)} updated{' (dry-run)' if args.dry_run else ''}{note}")
        return 0
    quarters = []
    for q in args.lca_quarter:
        m = re.fullmatch(r"(\d{4})Q([1-4])", q.strip().upper())
        if not m:
            ap.error(f"--lca-quarter expects YYYYQn, got {q!r}")
        quarters.append((int(m.group(1)), int(m.group(2))))
    if http is None and not args.dry_run:
        http = Http(timeout=300, retries=0, min_interval=1.0, user_agent=FETCH_USER_AGENT)   # one request per file
        http.session.headers["Accept"] = "text/csv, application/octet-stream, */*"
    report = download(args.root, http, fys=args.fy,
                      archive_fys=args.archive_fy, lca_xlsx=args.lca_xlsx, lca_quarters=quarters,
                      soc_prefixes=args.soc_prefix or ("15-",), now=now, dry_run=args.dry_run)
    print(f"h1b fetch: wrote {report['written'] or 'nothing'}; errors: {report['errors'] or 'none'}")
    return 1 if report["errors"] and not report["written"] else 0


if __name__ == "__main__":
    sys.exit(main())
