#!/usr/bin/env python3
"""check_records.py - every department record parses; otherwise the file, line and error are named.

    python3 scripts/check_records.py PATH... [--json]          (a record file, or a folder of records)
    python3 scripts/check_records.py --all [--root JS] [--json]

A hand-written record can be invalid YAML: an agent wrote `equity_type: stock options (ISO or NSO: to
confirm)` unquoted in offer.md, and a strict loader then reads none of its fields. Run this after writing a
record by hand. Markdown records (.md) have their frontmatter checked, YAML records (.yaml, .yml) the whole
file. The frontmatter is split and parsed by the OS kernel module (scripts/kernel/frontmatter.py, what the
sweep, the lint and every script read) and, when PyYAML is present, by a strict loader that also reports
  - a key that appears twice in one mapping (YAML keeps the last value silently);
  - a flow-mapping value cut by unquoted commas: `{skill: SQL (Postgres, SQLite, DuckDB), level: strong}` reads
    as skill 'SQL (Postgres' plus keys 'SQLite' and 'DuckDB)' with no value (a skills taxonomy file);
    the report gives the quoted form to write instead.
Without PyYAML only the kernel parser runs and the report says the strict checks were skipped.
Jobs (jobs/*.md) and applications (applications/*/application.md) are also checked for an unquoted YAML
boolean (yes, no, true, false, on, off in any case) in a field that holds a string: `sponsorship: yes`
parses as True, not the enum value "yes". String fields are the schema's fields (schemas/job.yaml,
application.yaml) minus the numeric, list and boolean ones below; the report gives the quoted form.

--all checks every Markdown and YAML file under the job-search root except code and caches (scripts/, mcp/,
state/harvest/, dot folders such as .cache/ and .agents/) and hub prose (hub/archive, briefs, plans, review). Exit 0 clean, 1 on any problem, 2 on a usage error.
Deterministic, read-only, no network.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))
from js_common import JS_ROOT, fm  # noqa: E402  (fm: the OS kernel frontmatter module)

try:
    import yaml
except ImportError:  # pragma: no cover - exercised by monkeypatching in the tests
    yaml = None

MARKDOWN = (".md", ".markdown")
YAML_SUFFIXES = (".yaml", ".yml")
SKIP_PARTS = {"scripts", "mcp", "node_modules", "__pycache__", "venv"}
SKIP_PATHS = (("state", "harvest"),
              # hub prose with a header, never parsed as records; hub/NEXT-UP.md and hub/lanes stay
              # checked (crm.lane_claims reads the lane files' claims and scope)
              ("hub", "archive"), ("hub", "briefs"), ("hub", "plans"), ("hub", "review"))


# Unquoted YAML 1.1 booleans (PyYAML's resolver): these parse as True or False.
_BOOL_TOKENS = {t: t.lower() in ("yes", "true", "on") for w in ("yes", "no", "true", "false", "on", "off")
                for t in (w, w.capitalize(), w.upper())}
_BOOL_LINE = re.compile(r"^(?P<key>[A-Za-z_][\w-]*):[ \t]+(?P<value>[A-Za-z]+)[ \t]*(?:#.*)?$")
# Schema fields whose values are not strings (numbers, lists, booleans); every other schema field is a string.
NOT_STRING = {
    "job": {"comp_min", "comp_max", "h1b_approvals_3y", "median_lca_wage", "ghost_score", "freshness_hours",
            "fit_score", "tags", "seen_on", "links", "hiring_signal"},
    "application": {"round", "prospects", "thread_ids", "ghost_after_days", "sponsorship_confirmed", "links"},
}
SCHEMAS_DIR = __import__("agency_paths").SCHEMAS_DIR      # the repo's schemas/


def record_type(path: Path) -> str | None:
    """'job' for jobs/*.md, 'application' for applications/<slug>/application.md, else None."""
    path = Path(path)
    if path.suffix == ".md" and path.parent.name == "jobs":
        return "job"
    if path.name == "application.md" and path.parent.parent.name == "applications":
        return "application"
    return None


_SCHEMAS: dict[str, dict[str, Any]] = {}


def _schema(kind: str) -> dict[str, Any]:
    if kind not in _SCHEMAS:
        path = SCHEMAS_DIR / f"{kind}.yaml"
        _SCHEMAS[kind] = (fm.parse_yaml(path.read_text(encoding="utf-8")) or {}) if path.is_file() else {}
    return _SCHEMAS[kind]


def string_fields(kind: str) -> set[str]:
    schema = _schema(kind)
    fields = {str(f) for f in schema.get("fields") or []} | set((schema.get("enums") or {}).keys())
    return fields - NOT_STRING.get(kind, set())


def _quoted_fix(kind: str, key: str, value: str) -> str:
    """The value to write: the enum's own yes/no when the field has one, else the token quoted."""
    enum = [str(v) for v in (_schema(kind).get("enums") or {}).get(key) or []]
    if enum:
        want = "yes" if _BOOL_TOKENS[value] else "no"
        if want in enum:
            return f'{key}: "{want}"'
        return f"{key}: one of {', '.join(enum)} (quote yes or no)"
    return f'{key}: "{value}"'


def bool_problems(raw: str, path: str, kind: str) -> list[Problem]:
    """Unquoted booleans in top-level string fields of a job or application frontmatter (raw = the block)."""
    fields = string_fields(kind)
    out = []
    for n, line in enumerate(raw.splitlines(), start=2):             # line 1 is the opening fence
        m = _BOOL_LINE.match(line)
        if not m or m.group("key") not in fields or m.group("value") not in _BOOL_TOKENS:
            continue
        key, value = m.group("key"), m.group("value")
        out.append(Problem(path, n, len(key) + 3, "unquoted-bool",
                           f"{key}: {value} is an unquoted YAML boolean (it parses as {_BOOL_TOKENS[value]}); "
                           f"{key} holds a string: write {_quoted_fix(kind, key, value)}"))
    return out


@dataclass(frozen=True)
class Problem:
    path: str
    line: int
    column: int
    kind: str         # yaml | frontmatter | duplicate-key | flow-split
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}:{self.column}: {self.kind}: {self.message}"


if yaml is not None:
    class StrictLoader(yaml.SafeLoader):
        """SafeLoader that records what PyYAML accepts silently: a key twice in one mapping. `offset` is the
        number of file lines before the parsed text (1 for frontmatter: the opening fence)."""

        def __init__(self, stream: str, offset: int = 0) -> None:
            super().__init__(stream)
            self.offset = offset
            self.findings: list[tuple[int, int, str, str]] = []

        def construct_mapping(self, node: Any, deep: bool = False) -> Any:
            if isinstance(node, yaml.MappingNode):
                self._inspect(node)
            return super().construct_mapping(node, deep=deep)

        def _line(self, mark: Any) -> int:
            return self.offset + mark.line + 1

        def _inspect(self, node: Any) -> None:
            if node.flow_style:
                self._flow_splits(node)
            first: dict[str, int] = {}
            for key_node, _ in node.value:
                if not isinstance(key_node, yaml.ScalarNode) or key_node.tag == "tag:yaml.org,2002:merge":
                    continue
                key = key_node.value
                if key in first:
                    self.findings.append((self._line(key_node.start_mark), key_node.start_mark.column + 1,
                                          "duplicate-key", f"duplicate key {key!r} (first on line {first[key]}): "
                                          "YAML keeps the last value silently"))
                else:
                    first[key] = self._line(key_node.start_mark)

        def _flow_splits(self, node: Any) -> None:
            """Entries of a flow mapping written without ': ' (a key with an implicit empty value): in practice the
            pieces of a value that unquoted commas cut. Each run is reported once, with the quoted fix."""
            buf = node.start_mark.buffer
            if not buf:
                return
            prev: tuple[Any, Any] | None = None
            run: list[Any] = []
            for key_node, value_node in node.value:
                if isinstance(key_node, yaml.ScalarNode) and self._colonless(buf, key_node, value_node):
                    run.append(key_node)
                    continue
                self._report_split(buf, prev, run)
                prev, run = (key_node, value_node), []
            self._report_split(buf, prev, run)

        @staticmethod
        def _colonless(buf: str, key_node: Any, value_node: Any) -> bool:
            if not (isinstance(value_node, yaml.ScalarNode) and value_node.tag == "tag:yaml.org,2002:null"
                    and value_node.value == ""):
                return False
            i = key_node.end_mark.index
            while i < len(buf) and buf[i] in " \t\r\n":
                i += 1
            return not (i < len(buf) and buf[i] == ":")

        def _report_split(self, buf: str, prev: tuple[Any, Any] | None, run: list[Any]) -> None:
            if not run:
                return
            pieces = ", ".join(repr(k.value) for k in run)
            plain = prev is not None and all(isinstance(n, yaml.ScalarNode) and not n.style for n in prev)
            if not plain:
                self.findings.append((self._line(run[0].start_mark), run[0].start_mark.column + 1, "flow-split",
                                      f"flow mapping key {pieces} has no value: an unquoted comma cut a value, or "
                                      "': ' is missing; quote the value that holds the comma"))
                return
            key, value = prev
            whole = buf[value.start_mark.index:run[-1].end_mark.index]
            self.findings.append((self._line(value.start_mark), value.start_mark.column + 1, "flow-split",
                                  f"{key.value}: {value.value!r} is cut by unquoted commas; YAML reads {pieces} as "
                                  f"keys with no value. Quote it: {key.value}: {json.dumps(whole, ensure_ascii=False)}"))


def strict_problems(text: str, path: str, offset: int = 0, mapping: bool = False) -> list[Problem]:
    """Parse `text` with the strict loader; problems carry file lines (`offset` lines precede `text`)."""
    loader = StrictLoader(text, offset)
    try:
        data = loader.get_single_data()
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark or exc.context_mark
        line, col = (offset + mark.line + 1, mark.column + 1) if mark else (offset + 1, 1)
        why = exc.problem or "cannot parse"
        msg = f"invalid YAML: {why}" + (f" ({exc.context})" if exc.context else "")
        if "mapping values are not allowed" in why:
            msg += "; a ': ' inside an unquoted value ends it: quote the whole value"
        return [Problem(path, line, col, "yaml", msg)]
    except yaml.YAMLError as exc:
        return [Problem(path, offset + 1, 1, "yaml", f"invalid YAML: {exc}")]
    finally:
        loader.dispose()
    out = [Problem(path, line, col, kind, msg) for line, col, kind, msg in loader.findings]
    if mapping and data is not None and not isinstance(data, dict):
        out.insert(0, Problem(path, offset + 1, 1, "frontmatter", "frontmatter must be a mapping (key: value lines)"))
    return out


def check_text(text: str, path: str, *, markdown: bool, kind: str | None = None) -> list[Problem]:
    """Problems in one record's text: the frontmatter of a Markdown record, or a whole YAML record."""
    if not markdown:
        return strict_problems(text, path) if yaml is not None else []
    try:
        raw, _ = fm.split(text)
    except fm.FrontmatterError as exc:
        return [Problem(path, 1, 1, "frontmatter", str(exc))]
    if raw is None:
        return []
    problems = strict_problems(raw, path, offset=1, mapping=True) if yaml is not None else []
    try:
        fm.parse_yaml(raw)                                  # the kernel's own reading of the same block
    except fm.FrontmatterError as exc:
        if not any(p.kind in ("yaml", "frontmatter") for p in problems):
            problems.insert(0, Problem(path, 2, 1, "frontmatter", str(exc)))
    if kind:
        problems += bool_problems(raw, path, kind)
    return problems


def check_file(path: Path | str, shown: str | None = None) -> list[Problem]:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return [Problem(shown or str(path), 1, 1, "frontmatter", f"not UTF-8 text: {exc}")]
    return check_text(text, shown or str(path), markdown=path.suffix.lower() not in YAML_SUFFIXES,
                      kind=record_type(path))


def iter_records(folder: Path) -> Iterator[Path]:
    """Every Markdown and YAML file under `folder`, sorted, without code, caches or dot folders."""
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in MARKDOWN + YAML_SUFFIXES:
            continue
        parts = path.relative_to(folder).parts
        if any(p.startswith(".") or p in SKIP_PARTS for p in parts[:-1]):
            continue
        if any(parts[i:i + len(s)] == s for s in SKIP_PATHS for i in range(len(parts))):
            continue
        yield path


def _shown(path: Path, root: Path) -> str:
    for base in (root, Path.cwd()):
        try:
            return str(path.resolve().relative_to(base.resolve()))
        except ValueError:
            continue
    return str(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*", type=Path, help="record files or folders")
    ap.add_argument("--all", action="store_true", help="every record under --root")
    ap.add_argument("--root", type=Path, default=JS_ROOT, help="the job-search root (default: this department)")
    ap.add_argument("--json", action="store_true", help="print one JSON report")
    args = ap.parse_args(argv)
    if not args.paths and not args.all:
        print("error: give record paths or --all", file=sys.stderr)
        return 2
    missing = [str(p) for p in args.paths if not p.exists()]
    if missing:
        print(f"error: no such file or folder: {', '.join(missing)}", file=sys.stderr)
        return 2
    files: list[Path] = list(iter_records(args.root)) if args.all else []
    for p in args.paths:
        files += list(iter_records(p)) if p.is_dir() else [p]
    files = list(dict.fromkeys(files))
    problems = [prob for f in files for prob in check_file(f, _shown(f, args.root))]
    bad = len({p.path for p in problems})
    strict = yaml is not None
    if args.json:
        print(json.dumps({"root": str(args.root), "files": len(files), "strict": strict, "ok": not problems,
                          "problems": [asdict(p) for p in problems]}, indent=2))
        return 1 if problems else 0
    for prob in problems:
        print(prob)
    if not strict:
        print("note: PyYAML is not installed: strict checks skipped (the kernel parser ran; duplicate keys and "
              "split flow values were not checked)")
    n = len(files)
    if problems:
        print(f"FAIL check_records: {len(problems)} problem{'s' if len(problems) != 1 else ''} in {bad} of {n} "
              f"file{'s' if n != 1 else ''}")
        return 1
    print(f"OK check_records: {n} file{'s' if n != 1 else ''}, no problems")
    return 0


if __name__ == "__main__":
    sys.exit(main())
