#!/usr/bin/env python3
"""reclassify_safe.py: re-run the mail classifier over stored harvest files without losing resolve answers.

 `mail_classify.py classify --in <src>` rewrites the whole
state/harvest/<name>.classified.jsonl from <name>.jsonl. Those files also hold the answers that
`mail_classify.py resolve --answers` wrote in (L3 decisions), so a plain re-classify wipes them, and the stored
labels drift from the classifier in HEAD (hundreds of messages on a large harvest). This script brings the stored labels up to
date and keeps every answer:

  1. For each classified file (--in one, or --all of <root>/state/harvest/*.classified.jsonl) the classifier is run
     on its source <name>.jsonl into a temporary folder, never in place, with the defaults of
     `mail_classify.py classify`: the CRM from the department's prospects/ (mc.default_prospects_dir, or
     --prospects) and the offline agent-queue L3 decider (mc.AgentQueueBackend; Jev is never asked, so no mail text
     leaves the Mac). The L3 queue that run writes lives in the temporary folder and is deleted with it; nothing is
     written to state/harvest except, with --write, the classified file itself. The report counts the L3 requests
     that were discarded.
  2. Rows are merged by message id. A row whose stored classification came from a `resolve` answer KEEPS it.
     mc.apply_answers_detailed marks such a row with `resolved_on` in its classification and with `backend: "agent"` (since the first version; no other path writes that backend value:
     the decider backends are named agent-queue, jev and review), so either marker keeps the row. Every other row
     takes the new classification dict whole. Nothing else in a row changes (not `portal`; portal drift is only
     counted).
  3. A row of the classified file that is missing from the source keeps its stored row. A passthrough row (type
     meta, person, conversation, connection) or a row without a classification or an id is kept as it is.
     A source row that is not in the classified file is reported and skipped, never appended: this tool changes
     labels, not the set of mail a file holds (prospects and job alerts read these files, and a row first enters
     them through a harvest pass).

A dry run (the default) writes nothing and prints every label change (file, id, sender, subject, old label and
level -> new), the counts by (old, new) label pair and the resolved rows kept. --write rewrites a changed file
atomically (a temporary file in the same folder, then os.replace), and only after asserting that the new file has
the same rows in the same order, byte for byte, except the `classification` of the changed rows, and that the
file did not change on disk since it was read. Unchanged lines are written back byte for byte; a changed row is
serialized the way classify and resolve write rows (sorted keys, ensure_ascii off).

Exit codes: 0 no changes; 1 changes found (dry run) or written; 2 usage or a precondition failure (a named file
without a source, a line that is not a JSON object, a duplicate message id, a failed write assertion). With --all,
a classified file with no source file is listed as skipped and the run goes on.

CLI:  python3 scripts/reclassify_safe.py (--in state/harvest/<name>.classified.jsonl | --all) [--root R]
                                          [--prospects DIR] [--write] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import js_common as jc
import js_comms_util as cu
import mail_classify as mc

EXIT_CLEAN, EXIT_CHANGES, EXIT_PRECONDITION = 0, 1, 2
CLASSIFIED_SUFFIX = ".classified.jsonl"
RESOLVED_FIELD = "resolved_on"          # written by mc.apply_answers_detailed (resolve --answers)
RESOLVED_BACKEND = "agent"              # written by the same function, and by nothing else


class PreconditionError(Exception):
    """The file cannot be reclassified safely; nothing is written."""


@dataclass
class Change:
    index: int
    id: str
    sender: str
    subject: str
    old_label: str
    old_level: str
    new_label: str
    new_level: str
    new_classification: dict[str, Any]

    def label_moved(self) -> bool:
        return self.old_label != self.new_label


@dataclass
class FilePlan:
    path: Path
    source: Path | None
    raw: bytes = b""
    lines: list[str] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    resolved_kept: list[str] = field(default_factory=list)
    missing_in_source: list[str] = field(default_factory=list)
    new_in_source: list[dict[str, str]] = field(default_factory=list)
    passthrough: int = 0
    rows: int = 0
    unchanged: int = 0
    portal_drift: int = 0
    l3_discarded: int = 0
    source_unparsed: int = 0
    skipped: str | None = None

    def summary(self) -> dict[str, Any]:
        moved = [c for c in self.changes if c.label_moved()]
        return {"file": str(self.path), "source": str(self.source) if self.source else None, "skipped": self.skipped,
                "rows": self.rows, "passthrough": self.passthrough, "unchanged": self.unchanged,
                "changed": len(self.changes), "label_changes": len(moved),
                "detail_only_changes": len(self.changes) - len(moved),
                "resolved_kept": list(self.resolved_kept), "missing_in_source_kept": len(self.missing_in_source),
                "new_in_source_skipped": list(self.new_in_source), "portal_drift_not_applied": self.portal_drift,
                "l3_requests_discarded": self.l3_discarded,
                "source_lines_unparsed": self.source_unparsed}


# --------------------------------------------------------------------------- pieces
def source_for(classified: Path) -> Path:
    """<name>.classified.jsonl -> <name>.jsonl in the same folder (the name classify_file derives the other way)."""
    classified = Path(classified)
    if not classified.name.endswith(CLASSIFIED_SUFFIX):
        raise PreconditionError(f"{classified}: not a *{CLASSIFIED_SUFFIX} file")
    return classified.with_name(classified.name[: -len(CLASSIFIED_SUFFIX)] + ".jsonl")


def is_resolved(classification: Any) -> bool:
    """True when the classification was written by `mail_classify.py resolve --answers`."""
    if not isinstance(classification, Mapping):
        return False
    return RESOLVED_FIELD in classification or classification.get("backend") == RESOLVED_BACKEND


def parse_lines(raw: bytes, path: Path) -> list[tuple[str, dict | None]]:
    """Every line kept with its exact text (newline included); blank lines parse to None. A line that is not a JSON
    object is a precondition failure, because jc.read_jsonl would drop it and a rewrite would lose it."""
    out: list[tuple[str, dict | None]] = []
    for n, line in enumerate(raw.decode("utf-8").splitlines(keepends=True), 1):
        if not line.strip():
            out.append((line, None))
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PreconditionError(f"{path}:{n}: not JSON ({exc.msg})") from exc
        if not isinstance(obj, dict):
            raise PreconditionError(f"{path}:{n}: not a JSON object")
        out.append((line, obj))
    return out


def classifiable(row: Mapping[str, Any] | None) -> bool:
    return bool(row) and row.get("type") not in mc.PASSTHROUGH_TYPES and isinstance(row.get("classification"), dict) \
        and row.get("id") not in (None, "")


def fresh_classifications(source: Path, prospects: Path | None = None,
                          portals: Mapping[str, str] | None = None) -> tuple[dict[str, dict], dict[str, Any], int]:
    """Run mc.classify_file on `source` into a temporary folder: ({id: classification}, {id: portal}, L3 requests
    discarded). The L3 decider is the offline agent queue, writing its queue inside the temporary folder."""
    crm = mc.CrmIndex.from_folder(Path(prospects) if prospects else mc.default_prospects_dir(source))
    with tempfile.TemporaryDirectory(prefix="reclassify-safe-") as tmp:
        queue = Path(tmp) / "l3-queue.jsonl"
        out = mc.classify_file(source, decider=mc.AgentQueueBackend(queue), out=Path(tmp) / "out.jsonl",
                               crm=crm, portals=portals)
        rows = jc.read_jsonl(out)
        discarded = len(jc.read_jsonl(queue))
    by_id: dict[str, dict] = {}
    portal: dict[str, Any] = {}
    for row in rows:
        if row.get("type") in mc.PASSTHROUGH_TYPES or not isinstance(row.get("classification"), dict):
            continue
        rid = str(row.get("id"))
        if rid in by_id and by_id[rid] != row["classification"]:
            raise PreconditionError(f"{source}: message id {rid} appears twice with different classifications")
        by_id[rid] = row["classification"]
        portal[rid] = row.get("portal")
    return by_id, portal, discarded


def unparsed_lines(path: Path) -> int:
    """Source lines that are not JSON: jc.read_jsonl (and so classify_file) skips them; counted, never repaired."""
    n = 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                json.loads(line)
            except json.JSONDecodeError:
                n += 1
    return n


def serialize(row: Mapping[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"


def plan_file(path: Path, source: Path | None = None, prospects: Path | None = None,
              portals: Mapping[str, str] | None = None) -> FilePlan:
    path = Path(path)
    source = Path(source) if source else source_for(path)
    plan = FilePlan(path=path, source=source)
    if not path.is_file():
        raise PreconditionError(f"{path}: no such file")
    if not source.is_file():
        raise PreconditionError(f"{path}: no source file {source.name}")
    plan.raw = path.read_bytes()
    parsed = parse_lines(plan.raw, path)
    plan.lines = [line for line, _ in parsed]
    seen: set[str] = set()
    for _, row in parsed:
        if classifiable(row):
            rid = str(row["id"])
            if rid in seen:
                raise PreconditionError(f"{path}: duplicate message id {rid}")
            seen.add(rid)
    fresh, portals_new, plan.l3_discarded = fresh_classifications(source, prospects, portals)
    for i, (_, row) in enumerate(parsed):
        if row is None:
            continue
        plan.rows += 1
        if not classifiable(row):
            plan.passthrough += 1
            continue
        rid, old = str(row["id"]), row["classification"]
        if is_resolved(old):
            plan.resolved_kept.append(rid)
            continue
        if rid not in fresh:
            plan.missing_in_source.append(rid)
            continue
        if (row.get("portal") or None) != (portals_new.get(rid) or None):
            plan.portal_drift += 1
        new = fresh[rid]
        if new == old:
            plan.unchanged += 1
            continue
        plan.changes.append(Change(i, rid, cu.one_line(str(row.get("from") or row.get("from_email") or ""), 90),
                                   cu.one_line(str(row.get("subject") or ""), 110),
                                   str(old.get("label")), str(old.get("level")),
                                   str(new.get("label")), str(new.get("level")), new))
    plan.source_unparsed = unparsed_lines(source)
    for row in jc.read_jsonl(source):
        rid = str(row.get("id"))
        if row.get("type") not in mc.PASSTHROUGH_TYPES and rid not in seen and rid in fresh:
            plan.new_in_source.append({"id": rid, "sender": cu.one_line(str(row.get("from") or ""), 90),
                                       "subject": cu.one_line(str(row.get("subject") or ""), 110)})
    return plan


def render(plan: FilePlan) -> list[str]:
    """The new file's lines: the stored lines, with only the classification of each changed row replaced."""
    lines = list(plan.lines)
    for ch in plan.changes:
        row = json.loads(lines[ch.index])
        row["classification"] = ch.new_classification
        lines[ch.index] = serialize(row)
    return lines


def check_rewrite(plan: FilePlan, new_lines: list[str]) -> None:
    """The write assertion: same rows, same order; an unchanged row is byte for byte the stored line; a changed row
    differs only in `classification`, which is the new classification; no resolved row moved."""
    if len(new_lines) != len(plan.lines):
        raise PreconditionError(f"{plan.path}: rewrite has {len(new_lines)} lines, stored {len(plan.lines)}")
    changed = {ch.index: ch for ch in plan.changes}
    for i, (old_line, new_line) in enumerate(zip(plan.lines, new_lines)):
        if i not in changed:
            if new_line != old_line:
                raise PreconditionError(f"{plan.path}:{i + 1}: an unchanged row was rewritten")
            continue
        old_row, new_row = json.loads(old_line), json.loads(new_line)
        if is_resolved(old_row.get("classification")):
            raise PreconditionError(f"{plan.path}:{i + 1}: a resolved row would change")
        if str(old_row.get("id")) != changed[i].id or str(new_row.get("id")) != changed[i].id:
            raise PreconditionError(f"{plan.path}:{i + 1}: row order moved (id {new_row.get('id')})")
        old_rest = {k: v for k, v in old_row.items() if k != "classification"}
        new_rest = {k: v for k, v in new_row.items() if k != "classification"}
        if old_rest != new_rest:
            raise PreconditionError(f"{plan.path}:{i + 1}: a field other than classification would change")
        if new_row.get("classification") != changed[i].new_classification:
            raise PreconditionError(f"{plan.path}:{i + 1}: classification is not the reclassified one")


def write_plan(plan: FilePlan) -> bool:
    """Rewrite the classified file atomically when it has changes. Returns True when written."""
    if not plan.changes:
        return False
    new_lines = render(plan)
    check_rewrite(plan, new_lines)
    data = "".join(new_lines).encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=plan.path.parent, prefix="." + plan.path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if plan.path.read_bytes() != plan.raw:
            raise PreconditionError(f"{plan.path}: changed on disk since it was read; run again")
        os.replace(tmp, plan.path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return True


# --------------------------------------------------------------------------- run and report
def targets(root: Path, one: Path | None, every: bool) -> list[Path]:
    if one is not None:
        return [Path(one)]
    return sorted((Path(root) / "state" / "harvest").glob("*" + CLASSIFIED_SUFFIX)) if every else []


def run(files: list[Path], *, all_mode: bool, write: bool = False, prospects: Path | None = None,
        portals: Mapping[str, str] | None = None) -> tuple[list[FilePlan], list[str]]:
    """Plan every file; with write, rewrite them only when every plan passed its preconditions."""
    portals = mc.portal_index() if portals is None else portals
    plans: list[FilePlan] = []
    for path in files:
        if all_mode and path.is_file() and not source_for(path).is_file():
            plans.append(FilePlan(path=path, source=None, skipped=f"no source file {source_for(path).name}"))
            continue
        plans.append(plan_file(path, prospects=prospects, portals=portals))
    written: list[str] = []
    if write:
        for plan in plans:
            if plan.skipped is None and write_plan(plan):
                written.append(str(plan.path))
    return plans, written


def report(plans: list[FilePlan], written: list[str], write: bool) -> dict[str, Any]:
    pairs: Counter[tuple[str, str]] = Counter()
    changes = []
    for plan in plans:
        for ch in plan.changes:
            if ch.label_moved():
                pairs[(ch.old_label, ch.new_label)] += 1
            changes.append({"file": plan.path.name, "id": ch.id, "sender": ch.sender, "subject": ch.subject,
                            "old": f"{ch.old_label}/{ch.old_level}", "new": f"{ch.new_label}/{ch.new_level}",
                            "label_moved": ch.label_moved()})
    totals = Counter()
    for plan in plans:
        s = plan.summary()
        for key in ("rows", "passthrough", "unchanged", "changed", "label_changes", "detail_only_changes",
                    "missing_in_source_kept", "portal_drift_not_applied", "l3_requests_discarded",
                    "source_lines_unparsed"):
            totals[key] += s[key]
        totals["resolved_kept"] += len(s["resolved_kept"])
        totals["new_in_source_skipped"] += len(s["new_in_source_skipped"])
        totals["files_skipped"] += 1 if plan.skipped else 0
    return {"mode": "write" if write else "dry-run", "files": [p.summary() for p in plans], "totals": dict(totals),
            "pairs": [{"old": o, "new": n, "n": k} for (o, n), k in sorted(pairs.items(), key=lambda x: (-x[1], x[0]))],
            "changes": changes, "written": written}


def print_text(rep: dict[str, Any]) -> None:
    for ch in rep["changes"]:
        if ch["label_moved"]:
            print(f"{ch['file']}  {ch['id']}  {ch['old']} -> {ch['new']}  from {json.dumps(ch['sender'])}  "
                  f"subject {json.dumps(ch['subject'])}")
    print("\npairs (old -> new label):")
    for p in rep["pairs"]:
        print(f"  {p['n']:5d}  {p['old']} -> {p['new']}")
    print("\nfiles:")
    for f in rep["files"]:
        if f["skipped"]:
            print(f"  {Path(f['file']).name}: skipped ({f['skipped']})")
            continue
        print(f"  {Path(f['file']).name}: rows {f['rows']}, label changes {f['label_changes']}, detail-only "
              f"{f['detail_only_changes']}, resolved kept {len(f['resolved_kept'])} {f['resolved_kept']}, "
              f"missing in source kept {f['missing_in_source_kept']}, new in source skipped "
              f"{len(f['new_in_source_skipped'])}, portal drift not applied {f['portal_drift_not_applied']}, "
              f"L3 requests discarded {f['l3_requests_discarded']}, source lines unparsed {f['source_lines_unparsed']}")
    mode = "written: " + ", ".join(Path(w).name for w in rep["written"]) if rep["mode"] == "write" \
        else "dry-run (nothing written)"
    print(f"\nreclassify_safe {mode}; totals: " + ", ".join(f"{k} {v}" for k, v in rep["totals"].items()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--in", dest="src", type=Path, help="one state/harvest/<name>.classified.jsonl")
    which.add_argument("--all", action="store_true", help="every <root>/state/harvest/*.classified.jsonl")
    ap.add_argument("--root", type=Path, default=jc.JS_ROOT, help="job-search root for --all")
    ap.add_argument("--prospects", type=Path, help="CRM folder (default: prospects/ of the department of each file)")
    ap.add_argument("--write", action="store_true", help="rewrite changed files atomically (default: dry run)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        plans, written = run(targets(args.root, args.src, args.all), all_mode=args.all, write=args.write,
                             prospects=args.prospects)
    except PreconditionError as exc:
        print(f"reclassify_safe: precondition failed, nothing written: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION
    rep = report(plans, written, args.write)
    if args.json:
        print(json.dumps(rep, indent=2, ensure_ascii=False))
    else:
        print_text(rep)
    return EXIT_CHANGES if any(p.changes for p in plans) else EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())
