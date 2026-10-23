#!/usr/bin/env python3
"""graph_links.py - the knowledge-graph edges on job-search records, so a notes vault (Obsidian) can draw them as a
knowledge graph with a meaningful index and tags.

Every record carries a node type and a frontmatter `links:` list of wikilinks (Obsidian draws wikilinks found
in frontmatter properties). Only code writes them, never a hand or an agent edit:

  kind         type:         links: (each only when the target record exists on disk)
  job          job           its company dossier
  prospect     prospect      its company dossier
  application  application   its job, its company dossier, its prospects
  outbox item  outbox        its prospect, its application
  pursuit      pursuit       its company dossier, its people (referrer, introducer, contacts), its applications,
                             its case and intake files (pursuits/<slug>/pursuit.md, written by pursuits.py)
  (touches are JSONL rows, not graph nodes: skipped)

Company dossiers (companies/<slug>.md) are the hub nodes. A job or prospect whose company has no dossier links
nothing until company-researcher writes one; the next write of the record, or `--write` here, adds the edge.
Existing scalar fields (`company`, `company_slug`, `job`, `prospect`, `prospects`, ...) are only read, never
changed: the scripts depend on them. An existing `type:` is kept; a missing one is set to the kind.

Link form: the vault-root path without `.md`, aliased with the record id, e.g.
  [[<vault prefix>/companies/acme|acme]]
  [[<vault prefix>/applications/acme-ml-engineer-202609/application|acme-ml-engineer-202609]]
where <vault prefix> is identity.vault_prefix (the job-search root seen from the vault root; empty when the vault root is the job-search root).
Every record link is path-qualified, not only the ones to non-unique basenames (application.md, HUB.md, fit.md,
jd.md, a person's profile.md): a job id or person slug may collide with a brain page tomorrow. Obsidian resolves
a vault-root path; scripts/kernel/lint.py resolves the same form strictly (the path must exist).

Where it runs: js_common.write_record and js_comms_util.write_record call `stamp_path` on every write, so every
owning script (fetch_ats.save_job and the fetchers, dedup, fit_gates, job_alerts, scout_review, jobs_manual,
tracker.py, prospects.py, outbox.py, the MCP's prospect_set) keeps the edges current; outbox.py and tracker.py
stamp the records they create in one piece. This CLI is the backfill for records written before the edges existed.

  python3 scripts/graph_links.py [--root JS] [--check | --write] [--kind job|prospect|application|outbox] [--json]
    --check (default) is a dry run: counts per kind and exit 1 when any record would change.
    --write stamps the records that differ (idempotent: a second run changes nothing).
Deterministic, no network, no clock.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import agency_config as cfg

VAULT_PREFIX = str(cfg.get("identity.vault_prefix", "") or "").strip("/")   # the job-search root, from the vault root
KINDS = ("job", "prospect", "application", "outbox", "pursuit")
FOLDER = {"job": "jobs", "prospect": "prospects", "outbox": "outbox"}   # application: applications/<slug>/
_LINK_RE = re.compile(r"^\s*\[\[([^\]|#]+)")
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def wikilink(rel: str, label: str) -> str:
    """[[<vault prefix>/<rel without .md>|<label>]] (no prefix and no slash when identity.vault_prefix is empty)."""
    rel = rel[:-3] if rel.endswith(".md") else rel
    return f"[[{VAULT_PREFIX + '/' if VAULT_PREFIX else ''}{rel}|{label}]]"


def _target(value: Any) -> str | None:
    """The id a scalar names: "[[x]]", "[[x|y]]" or a bare "x"; None when empty or not a plain id."""
    if value in (None, "", [], {}):
        return None
    text = str(value).strip()
    m = _LINK_RE.match(text)
    text = (m.group(1) if m else text).strip().rsplit("/", 1)[-1]
    return text if _SLUG_RE.match(text) else None


def _targets(value: Any) -> list[str]:
    items = value if isinstance(value, (list, tuple)) else [value]
    return [t for t in (_target(v) for v in items) if t]


def _company(js: Path, meta: dict[str, Any]) -> list[str]:
    slug = _target(meta.get("company_slug"))
    return [wikilink(f"companies/{slug}", slug)] if slug and (js / "companies" / f"{slug}.md").is_file() else []


def _job(js: Path, job_id: str | None) -> list[str]:
    return [wikilink(f"jobs/{job_id}", job_id)] if job_id and (js / "jobs" / f"{job_id}.md").is_file() else []


def _prospects(js: Path, ids: Iterable[str]) -> list[str]:
    return [wikilink(f"prospects/{p}", p) for p in ids if (js / "prospects" / f"{p}.md").is_file()]


def _application(js: Path, slug: str | None) -> list[str]:
    if slug and (js / "applications" / slug / "application.md").is_file():
        return [wikilink(f"applications/{slug}/application", slug)]
    return []


def _file(js: Path, rel: Any) -> list[str]:
    rel = str(rel or "").strip()
    if not rel or rel.startswith(("/", "..")) or not (js / rel).is_file():
        return []
    return [wikilink(rel, Path(rel).stem)]


def links_for(kind: str, js: Path, meta: dict[str, Any]) -> list[str]:
    """The `links:` list a record of this kind should carry, in a fixed order, without duplicates."""
    js = Path(js)
    if kind == "job":
        out = _company(js, meta)
    elif kind == "prospect":
        out = _company(js, meta)
    elif kind == "application":
        out = _job(js, _target(meta.get("job"))) + _company(js, meta) + _prospects(js, _targets(meta.get("prospects")))
    elif kind == "outbox":
        out = _prospects(js, _targets(meta.get("prospect"))) + _application(js, _target(meta.get("application")))
    elif kind == "pursuit":
        people = _targets([meta.get("referrer"), meta.get("introducer"), *(meta.get("contacts") or [])])
        out = _company(js, meta) + _prospects(js, people) \
            + [x for a in _targets(meta.get("applications") or []) for x in _application(js, a)] \
            + _file(js, meta.get("case")) + _file(js, meta.get("intake"))
    else:
        raise ValueError(f"unknown record kind {kind!r}; expected one of {KINDS}")
    return list(dict.fromkeys(out))


def stamp(kind: str, js: Path, meta: dict[str, Any]) -> bool:
    """Set `type:` (when missing; always the first key) and `links:` on `meta` in place. Returns True when
    anything changed. An empty links list is left out (and a stale one removed): most jobs have no dossier yet."""
    before = dict(meta)
    kind_value = meta.get("type") if meta.get("type") not in (None, "") else kind
    if kind_value != meta.get("type") or next(iter(meta), None) != "type":
        # type: leads the frontmatter whatever order a writer builds (save_job rebuilds JOB_FIELDS order)
        rest = {k: v for k, v in meta.items() if k != "type"}
        meta.clear()
        meta.update({"type": kind_value, **rest})
    links = links_for(kind, js, meta)
    if links:
        meta["links"] = links
    else:
        meta.pop("links", None)
    return meta != before or list(meta) != list(before)


def kind_of(path: Path) -> tuple[str, Path] | None:
    """(kind, job-search root) for a record path, or None for any other file."""
    path = Path(path)
    if path.suffix != ".md" or path.name.startswith(("_", ".")) or path.name.upper() == "README.MD":
        return None
    if path.name == "application.md" and path.parent.parent.name == "applications":
        return "application", path.parents[2]
    if path.name == "pursuit.md" and path.parent.parent.name == "pursuits":
        return "pursuit", path.parents[2]
    for kind, folder in FOLDER.items():
        if path.parent.name == folder:
            return kind, path.parents[1]
    return None


def stamp_path(path: Path, meta: dict[str, Any]) -> bool:
    """Stamp `meta` when `path` is a record; no-op (False) for every other file."""
    found = kind_of(path)
    return stamp(found[0], found[1], meta) if found else False


# ------------------------------------------------------------------------------ backfill
def record_paths(js: Path, kind: str) -> list[Path]:
    js = Path(js)
    pattern = {"application": "applications/*/application.md", "pursuit": "pursuits/*/pursuit.md"}.get(
        kind, f"{FOLDER.get(kind)}/*.md")
    return [p for p in sorted(js.glob(pattern)) if p.is_file() and kind_of(p)]


def backfill(js: Path, kinds: Iterable[str] = KINDS, write: bool = False) -> dict[str, dict[str, Any]]:
    """Per kind: records, with_type/with_links before, to_change, and after (what the tree holds once written)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import js_common as jc   # noqa: E402  (lazy: js_common imports this module)
    import js_comms_util as cu  # noqa: E402
    report: dict[str, dict[str, Any]] = {}
    for kind in kinds:
        row = {"records": 0, "with_type_before": 0, "with_links_before": 0, "links_before": 0,
               "to_change": 0, "with_type_after": 0, "with_links_after": 0, "links_after": 0, "changed": []}
        for path in record_paths(js, kind):
            meta, body = jc.fm.load(path)
            row["records"] += 1
            row["with_type_before"] += bool(meta.get("type"))
            row["with_links_before"] += bool(meta.get("links"))
            row["links_before"] += len(meta.get("links") or [])
            if stamp(kind, Path(js), meta):
                row["to_change"] += 1
                row["changed"].append(path.relative_to(js).as_posix())
                if write:
                    # the same writer the owning script uses (the kernel dumper for jobs and applications,
                    # the no-alias dumper for prospects and outbox), so a record changes only by these lines
                    (cu.write_record if kind in ("prospect", "outbox") else jc.write_record)(path, meta, body)
            row["with_type_after"] += bool(meta.get("type"))
            row["with_links_after"] += bool(meta.get("links"))
            row["links_after"] += len(meta.get("links") or [])
        report[kind] = row
    return report


def main(argv: list[str] | None = None) -> int:
    from agency_paths import DATA_ROOT as here
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=here, help="the job-search root (default: this department)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="dry run (default): report, exit 1 when records differ")
    mode.add_argument("--write", action="store_true", help="stamp the records that differ")
    ap.add_argument("--kind", action="append", choices=KINDS, help="limit to a kind (repeatable)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = backfill(args.root, args.kind or KINDS, write=args.write)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        verb = "changed" if args.write else "would change"
        for kind, r in report.items():
            print(f"{kind}: {r['records']} record(s) · type {r['with_type_before']} -> {r['with_type_after']} · "
                  f"with links {r['with_links_before']} -> {r['with_links_after']} "
                  f"({r['links_before']} -> {r['links_after']} link(s)) · {verb} {r['to_change']}")
    pending = sum(r["to_change"] for r in report.values())
    if args.write:
        print(f"graph_links: wrote {pending} record(s)")
        return 0
    print("graph_links: in step" if not pending else f"graph_links: {pending} record(s) to stamp; run --write")
    return 1 if pending else 0


if __name__ == "__main__":
    sys.exit(main())
