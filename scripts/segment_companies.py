#!/usr/bin/env python3
"""segment_companies.py - targets/companies.yaml `segment` (startup, growth, big-tech, ai-lab, enterprise) from
evidence. growth is a private company at Series D or later, or 500 to 10,000 people.

The strategist's review and the dashboard read a company's segment. Evidence, first hit wins:
  1. the segment files' own company lists: each targets/segments/<segment>.md heading's parenthetical names
     ("big tech (Bigsearch, Globex, ...)") and the Company column of its tables
  2. the dream list (research/dream/DREAM-COMPANIES.md) group: "frontier lab" is ai-lab, "big tech" is big-tech;
     its other groups (AI infra, autonomy, consumer ...) say nothing about size and are not used
  3. companies.yaml itself: an entry yc_boards.py added (notes say YC) or a VC-board careers_url (Getro, Consider)
     is a startup
  4. the company dossier companies/<slug>.md: a stage of pre-seed, seed or Series A to C, or a headcount under 500,
     is startup; public or over 10,000 people is big-tech or enterprise by sector (the dream group, else the
     dossier's words); public with no sector evidence stays unknown; otherwise Series D or later, a late or pre-IPO
     stage, or 500 to 10,000 people is growth
  5. KNOWN, a short table of the obvious names (exact)
  then word-prefix matches of 1, 2 and 5, and the job records (a "Founding ..." title, or a JD naming a seed to
  Series C round or an incorporation date). segment_for(root, name, slug) answers for companies off the watchlist.
Unknown stays unknown: no line is written. Exact names are tried in all four sources first; only then does a name
match a list entry when one is the other plus more words ("Initech" matches "Initech Capital"), so "Bigsearch DeepLab"
takes the dream list's frontier lab, not big tech through "Bigsearch". Writes `segment` and `segment_source` into each matched entry as text (comments
and order kept), re-parses the file and refuses a count change; if another writer changed the file during the run it
is re-read once (the Scout writes it too). --dry-run prints what it would set.

The owner's own pick goes through `set`: it is written with the owner source tag and never
overwritten by a later inference run; `none` removes it.

CLI:  python3 scripts/segment_companies.py [--root R] [--dry-run]
      python3 scripts/segment_companies.py [--root R] set <company slug or name> <segment|none> --source <OWNER_SOURCE>
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from js_common import JS_ROOT, iter_records, normalize_company  # noqa: E402
import agency_config as agency_cfg  # noqa: E402

# The segment_source tag a hand pick carries (a data-file vocabulary value shared with the dashboard).
OWNER_SOURCE = "owner"
SEGMENTS = ("startup", "growth", "big-tech", "ai-lab", "enterprise")
DREAM_GROUP = {"frontier lab": "ai-lab", "big tech": "big-tech"}
# A short table of exact company-name keys (see _key) whose size is a public fact (a public company, a public company's
# subsidiary, or a well-known large private one), each with its reason; everything else stays "not set". It comes from
# policy.segments.known_names in the config: {key: {segment, reason}} (empty by default).
KNOWN_REASONS = {str(k): (str(v.get("segment")), str(v.get("reason") or "")) for k, v in
                 (agency_cfg.get("policy.segments.known_names", {}) or {}).items() if isinstance(v, dict)}
KNOWN = {name: seg for name, (seg, _) in KNOWN_REASONS.items()}
KNOWN_SOURCE = "known-names table (segment_companies.py)"
STARTUP_STAGE = re.compile(r"\b(pre-?seed|seed|series [abc])\b", re.I)
GROWTH_STAGE = re.compile(r"\bseries [d-k]\b|\blate[- ]stage\b|\bpre-?ipo\b", re.I)
PRE_IPO = re.compile(r"\bpre-?ipo\b|\b(?:plans?|planning|aims?) to go public\b", re.I)
PUBLIC_LISTED = re.compile(r"\bNYSE\b|\bNASDAQ\b|\bpublic company\b|\bpublicly traded\b|\blisted\b", re.I)
SIZE = re.compile(r"\babout (\d[\d,]*) people\b", re.I)


def _key(name: Any) -> str:
    return normalize_company(re.sub(r"\([^)]*\)", "", str(name or ""))).strip()


def _matches(company: str, listed: str, exact: bool = False) -> bool:
    a, b = _key(company), _key(listed)
    if not (a and b):
        return False
    return a == b if exact else (a == b or a.startswith(b + " ") or b.startswith(a + " "))


def segment_lists(root: Path) -> list[tuple[str, str, str]]:
    """(segment, listed name, source file) from each segment file's heading and table Company columns."""
    out = []
    for seg in SEGMENTS:
        path = root / "targets" / "segments" / f"{seg}.md"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        src = f"targets/segments/{seg}.md"
        head = re.search(r"^# Segment:[^(\n]*\(([^)\n]*)\)", text, re.M)
        if head:
            names = head.group(1).split(":", 1)[-1]
            for item in names.split(","):
                item = item.replace("...", "").strip()
                if item and item[0].isupper() and len(item.split()) <= 4 and not re.search(r"firm|compan", item, re.I):
                    out.append((seg, item, src))
        in_table = False
        for ln in text.splitlines():
            cells = [c.strip() for c in ln.strip().strip("|").split("|")] if ln.startswith("|") else []
            if cells and cells[0].lower() == "company":
                in_table = True
                continue
            if not cells:
                in_table = False
                continue
            if in_table and cells[0] and not set(cells[0]) <= set("-: "):
                out.append((seg, re.sub(r"[*`]", "", cells[0]), src))
    return out


def dream_groups(root: Path) -> list[tuple[str, str]]:
    path = root / "research" / "dream" / "DREAM-COMPANIES.md"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")] if ln.startswith("|") else []
        if len(cells) >= 2 and cells[1].lower() in DREAM_GROUP:
            out.append((cells[0], DREAM_GROUP[cells[1].lower()]))
    return out


BIG_GROUPS = {"big tech", "consumer", "platform", "ai app", "ai infra", "generative media"}
ENT_GROUPS = {"eda", "defense", "autonomy", "robotics", "ai science"}
ENT_WORDS = ("instrument", "semiconductor", "eda", "bank", "insurance", "automotive", "manufactur", "consulting",
             "defense", "aerospace", "pharma", "biotech", "retail", "telecom")
TECH_WORDS = ("software", "app", "platform", "cloud", "internet", "payments", "fintech", "social", "search",
              "e-commerce")
PUBLIC = re.compile(r"\bpublic\b|\bNYSE\b|\bNASDAQ\b", re.I)
FOUNDING_SEAT = re.compile(r"^founding (?:(?:ai|ml|software|full[- ]?stack|product|backend|frontend|applied ai) )?"
                           r"engineer\b", re.I)      # "Founding Forward Deployed Engineer" names a team at a big firm
JD_STAGE = re.compile(r"incorporated (?:in )?\w+ 20\d\d|\b(?:pre-?seed|seed|series [abc])(?:[- ]stage| round| funding)",
                      re.I)
VC_BOARD = re.compile(r"getro\.com|consider\.com|portfoliojobs|jobs\.a16z|jobs\.sequoiacap", re.I)


def _headcount(size: str) -> int | None:
    """The headcount a dossier states; a range ("201-500 employees") takes its lower bound."""
    m = (re.search(r"(\d[\d,]*)\s*(?:-|to)\s*\d[\d,]*\s*(?:people|employees)", size, re.I)
         or re.search(r"(\d[\d,]*)\s*(?:people|employees)", size, re.I) or re.match(r"\s*(\d[\d,]*)\b", size))
    return int(m.group(1).replace(",", "")) if m else None


def _all_groups(root: Path) -> list[tuple[str, str]]:
    path = root / "research" / "dream" / "DREAM-COMPANIES.md"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")] if ln.startswith("|") else []
        if len(cells) >= 2 and cells[0] and cells[0].lower() != "company" and not set(cells[0]) <= set("-: "):
            out.append((cells[0], cells[1].lower()))
    return out


def dossier_segment(root: Path, groups: list[tuple[str, str]] | None = None) -> dict[str, tuple[str, str]]:
    """slug -> (segment, source) from companies/<slug>.md: stage seed to Series C or a headcount under 500 is startup;
    public or over 10,000 people is big-tech or enterprise by sector (the dream list's group, else the dossier's own
    words); public with no sector evidence stays unknown; a private Series D or later (or late, pre-IPO) stage, or 500
    to 10,000 people, is growth."""
    groups = groups if groups is not None else _all_groups(root)
    out = {}
    for path, meta, body in iter_records(root / "companies"):
        slug, name = str(meta.get("slug") or path.stem), str(meta.get("name") or "")
        stage, size = str(meta.get("stage") or ""), str(meta.get("size") or "")
        n = _headcount(size)
        private = bool(PRE_IPO.search(stage))                     # "pre-IPO, plans to go public" is not public
        if GROWTH_STAGE.search(stage) and not PUBLIC_LISTED.search(stage):    # the latest round decides: D beats C
            out[slug] = ("growth", f"companies/{path.name} (stage {stage})")
        elif STARTUP_STAGE.search(stage):
            out[slug] = ("startup", f"companies/{path.name} (stage {stage})")
        elif n is not None and n < 500:
            out[slug] = ("startup", f"companies/{path.name} (size {size})")
        elif (PUBLIC.search(stage) and not private) or (n is not None and n > 10000):
            group = next((g for listed, g in groups if _matches(name, listed)), None)
            text = " ".join([stage, size, body or ""]).lower()
            ent = next((w for w in ENT_WORDS if re.search(rf"\b{re.escape(w)}", text)), None)
            tech = next((w for w in TECH_WORDS if re.search(rf"\b{re.escape(w)}\b", text)), None)
            if group in BIG_GROUPS:
                out[slug] = ("big-tech", f"companies/{path.name} (public; dream group {group})")
            elif group in ENT_GROUPS:
                out[slug] = ("enterprise", f"companies/{path.name} (public; dream group {group})")
            elif ent:
                out[slug] = ("enterprise", f"companies/{path.name} (public; sector word '{ent}')")
            elif tech:
                out[slug] = ("big-tech", f"companies/{path.name} (public; sector word '{tech}')")
        elif n is not None and 500 <= n <= 10000:
            out[slug] = ("growth", f"companies/{path.name} (size {size})")
    return out


def job_evidence(root: Path, records: Any = None) -> dict[str, tuple[str, str]]:
    """normalized company -> (startup, source) from a job record: a founding engineer seat ("Founding Engineer",
    "Founding AI Engineer" ...), or the JD naming a seed to Series C round or an incorporation date."""
    out: dict[str, tuple[str, str]] = {}
    for path, meta, body in records if records is not None else iter_records(root / "jobs"):
        key = _key(meta.get("company"))
        if not key or key in out:
            continue
        title = str(meta.get("title") or "")
        if FOUNDING_SEAT.match(title.strip()):
            out[key] = ("startup", f"jobs/{path.name} (title {title})")
            continue
        m = JD_STAGE.search(body or "")
        if m:
            out[key] = ("startup", f"jobs/{path.name} (JD: '{m.group(0)}')")
    return out


class Evidence:
    """Every source loaded once."""

    def __init__(self, root: Path, jobs: dict[str, dict[str, Any]] | None = None):
        self.root = Path(root)
        self._loaded = jobs                            # a caller's already-loaded jobs/ (the dashboard's), else read
        self.lists = segment_lists(self.root)
        self.groups = _all_groups(self.root)
        self.dream = dream_groups(self.root)
        self.dossiers = dossier_segment(self.root, self.groups)
        self.dossier_names = {_key(meta.get("name")): str(meta.get("slug") or path.stem)
                              for path, meta, _ in iter_records(self.root / "companies") if meta.get("name")}
        self._jobs: dict[str, tuple[str, str]] | None = None

    @property
    def jobs(self) -> dict[str, tuple[str, str]]:
        if self._jobs is None:
            recs = None if self._loaded is None else [(Path(r.get("_path") or f"{k}.md"), r, r.get("_body") or "")
                                                      for k, r in sorted(self._loaded.items())]
            self._jobs = job_evidence(self.root, recs)
        return self._jobs

    def decide(self, name: str, slug: str, entry: dict[str, Any] | None = None) -> tuple[str | None, str | None]:
        hit = next(((s, f) for s, listed, f in self.lists if _matches(name, listed, True)), None)
        if hit:
            return hit
        d = next((s for listed, s in self.dream if _matches(name, listed, True)), None)
        if d:
            return d, "research/dream/DREAM-COMPANIES.md (group)"
        entry = entry or {}
        if re.search(r"yc_boards\.py|\bYC\b", str(entry.get("notes") or "")):
            return "startup", "companies.yaml notes (YC)"
        if VC_BOARD.search(str(entry.get("careers_url") or "")):
            return "startup", "companies.yaml careers_url (VC board)"
        dslug = slug if slug in self.dossiers else self.dossier_names.get(_key(name))
        if dslug in self.dossiers:                     # by slug, else by the dossier's company name
            return self.dossiers[dslug]
        k = next((n for n in KNOWN if _matches(name, n, True)), None)
        if k:
            return KNOWN[k], f"{KNOWN_SOURCE}: {KNOWN_REASONS[k][1]}"
        hit = next(((s, f) for s, listed, f in self.lists if _matches(name, listed)), None)
        if hit:
            return hit
        d = next((s for listed, s in self.dream if _matches(name, listed)), None)
        if d:
            return d, "research/dream/DREAM-COMPANIES.md (group)"
        if _key(name) in self.jobs:
            return self.jobs[_key(name)]
        k = next((n for n in KNOWN if _matches(name, n)), None)
        return (KNOWN[k], f"{KNOWN_SOURCE}: {KNOWN_REASONS[k][1]}") if k else (None, None)


def _watch_entry(root: Path, name: str, slug: str) -> dict[str, Any] | None:
    try:
        data = yaml.safe_load((Path(root) / "targets" / "companies.yaml").read_text(encoding="utf-8")) or {}
    except OSError:
        return None
    for c in data.get("companies") or []:
        if isinstance(c, dict) and ((slug and str(c.get("slug") or "") == slug) or _key(c.get("name")) == _key(name)):
            return c
    return None


def segment_for(root: Path, name: str, slug: str, ev: Evidence | None = None) -> tuple[str | None, str | None]:
    """The segment of any company, on the watchlist or not (strategy_review's fallback), with its evidence. A
    watchlist entry's own `segment` answers first (the one source hunt_score reads); else the evidence, with
    the entry's notes and careers_url when it has one."""
    entry = _watch_entry(root, name, slug)
    if entry and entry.get("segment"):
        return str(entry["segment"]), str(entry.get("segment_source") or "targets/companies.yaml")
    return (ev or Evidence(root)).decide(name, slug, entry)


def classify(root: Path) -> list[dict[str, Any]]:
    root = Path(root)
    data = yaml.safe_load((root / "targets" / "companies.yaml").read_text(encoding="utf-8")) or {}
    ev = Evidence(root)
    rows = []
    for c in data.get("companies") or []:
        if not isinstance(c, dict):
            continue
        name, slug = str(c.get("name") or ""), str(c.get("slug") or "")
        seg, src = ev.decide(name, slug, c)
        rows.append({"slug": slug, "name": name, "segment": seg, "source": src})
    return rows


def apply(text: str, found: dict[str, tuple[str, str]], remove: set[str] | None = None) -> tuple[str, int]:
    """Set `segment` and `segment_source` in each entry whose slug is in `found`, and drop both from the slugs in
    `remove`; block-style entries are edited as text, a flow-style entry line ("- {name: ..}") is re-dumped in flow
    style. Returns (text, entries changed)."""
    remove = remove or set()
    lines = text.splitlines(keepends=True)
    out, changed, i = [], 0, 0
    while i < len(lines):
        ln = lines[i]
        flow = re.match(r"^  - (\{.*\})\s*$", ln)
        if flow:
            try:
                entry = yaml.safe_load(flow.group(1))
            except yaml.YAMLError:
                entry = None
            slug = str((entry or {}).get("slug") or "")
            if isinstance(entry, dict) and (slug in found or slug in remove):
                entry.pop("segment", None)
                entry.pop("segment_source", None)
                if slug in found:
                    entry["segment"], entry["segment_source"] = found[slug]
                new = "  - " + yaml.safe_dump(entry, default_flow_style=True, sort_keys=False, width=10000).strip() + "\n"
                changed += int(new != ln)
                ln = new
            out.append(ln)
            i += 1
            continue
        if not re.match(r"^  - name:", ln):
            out.append(ln)
            i += 1
            continue
        j = i + 1
        while j < len(lines) and not re.match(r"^  - |^\S", lines[j]):
            j += 1
        block = lines[i:j]
        while block and block[-1].lstrip().startswith("#"):    # the next entry's comment line belongs to it
            block.pop()
            j -= 1
        slug_line = next((b for b in block if re.match(r"^    slug:", b)), "")
        slug = slug_line.split(":", 1)[1].strip().strip("\"'") if slug_line else ""
        if slug in found or slug in remove:
            new = [b for b in block if not re.match(r"^    segment(_source)?:", b)]
            if slug in found:
                seg, src = found[slug]
                at = next((k + 1 for k, b in enumerate(new) if re.match(r"^    tier:", b)), 2)
                new[at:at] = [f"    segment: {seg}\n", f"    segment_source: \"{src}\"\n"]
            changed += int(new != block)
            block = new
        out += block
        i = j
    return "".join(out), changed


def _write(root: Path, found: dict[str, tuple[str, str]], remove: set[str] | None = None) -> int:
    path = root / "targets" / "companies.yaml"
    for _ in range(2):
        mtime = path.stat().st_mtime_ns
        old = path.read_text(encoding="utf-8")
        new, changed = apply(old, found, remove)
        if len((yaml.safe_load(new) or {}).get("companies") or []) != len((yaml.safe_load(old) or {}).get("companies") or []):
            raise RuntimeError(f"{path}: the entry count changed; nothing written")
        if path.stat().st_mtime_ns != mtime:              # another writer meanwhile: re-read once
            continue
        if new != old:
            tmp = path.with_name("." + path.name + ".tmp")
            tmp.write_text(new, encoding="utf-8")
            os.replace(tmp, path)
        return changed
    raise RuntimeError(f"{path} kept changing under another writer; nothing written, run again")


def set_segment(root: Path, company: str, segment: str, source: str) -> str:
    """The owner's pick (the dashboard's segment control): validated, written with the owner source tag and never
    overwritten by inference; "none" removes the pick (inference may then set one)."""
    if source != OWNER_SOURCE:
        raise ValueError(f"--source must be {OWNER_SOURCE} (a hand pick; inference writes through the run, not set)")
    if segment not in (*SEGMENTS, "none"):
        raise ValueError(f"segment must be one of {', '.join(SEGMENTS)} or none")
    data = yaml.safe_load((root / "targets" / "companies.yaml").read_text(encoding="utf-8")) or {}
    entries = [c for c in data.get("companies") or [] if isinstance(c, dict)]
    hit = next((c for c in entries if str(c.get("slug")) == company), None) or \
        next((c for c in entries if _key(c.get("name")) == _key(company)), None)
    if not hit:
        raise ValueError(f"{company!r} is not on targets/companies.yaml (add the company there first)")
    slug = str(hit.get("slug"))
    if segment == "none":
        _write(root, {}, {slug})
    else:
        _write(root, {slug: (segment, OWNER_SOURCE)})
    return slug


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--source", default=None, help=f"set only: {OWNER_SOURCE}")
    ap.add_argument("args", nargs="*", help="set <company slug or name> <segment|none>")
    args = ap.parse_args(argv)
    root = Path(args.root)
    if args.args:
        if args.args[0] != "set" or len(args.args) != 3:
            print(f"usage: segment_companies.py [--root R] set <company> <segment|none> --source {OWNER_SOURCE}", file=sys.stderr)
            return 2
        try:
            slug = set_segment(root, args.args[1], args.args[2], str(args.source or ""))
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        print(f"segment {slug}: {args.args[2]} (source {OWNER_SOURCE})")
        return 0
    rows = classify(root)
    data = yaml.safe_load((root / "targets" / "companies.yaml").read_text(encoding="utf-8")) or {}
    picked = {str(c.get("slug")) for c in data.get("companies") or [] if isinstance(c, dict)
              and str(c.get("segment_source") or "") == OWNER_SOURCE}
    found = {r["slug"]: (r["segment"], r["source"]) for r in rows if r["segment"] and r["slug"] not in picked}
    by = {s: sum(1 for r in rows if r["segment"] == s) for s in SEGMENTS}
    summary = ", ".join(f"{s} {n}" for s, n in by.items()) + f", unknown {sum(1 for r in rows if not r['segment'])}"
    kept = f"; {len(picked)} hand picks kept" if picked else ""
    if args.dry_run:
        print(f"segment_companies dry run: would set {len(found)} of {len(rows)} ({summary}){kept}; nothing written")
        return 0
    changed = _write(root, found)
    print(f"segment_companies: set {len(found)} of {len(rows)} ({summary}); {changed} entries changed{kept}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
