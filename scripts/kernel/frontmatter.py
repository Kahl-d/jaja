"""YAML frontmatter for the agency kernel.

Public interface (used by sync.py, sweep.py, lint.py, approvals.py and the tests):

    load(path)                 -> (meta: dict, body: str)
    loads(text)                -> (meta: dict, body: str)
    dump(meta, body)           -> str
    iter_docs(root, glob)      -> Iterable[(path, meta, body)]

Frontmatter is the block between a leading ``---`` line and the next ``---``
line.  A file without that block loads as ``({}, whole_text)``.  Parsing uses
pyyaml when it is importable and otherwise a minimal ``key: value`` parser
that understands scalars, ISO dates, inline lists/dicts, block lists and one
level of nested mappings (enough for every frontmatter contract in the spec).

Python 3.12 stdlib only, plus optional pyyaml.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Iterable, Iterator

try:  # optional dependency
    import yaml as _yaml
except Exception:  # pragma: no cover - exercised only when pyyaml is missing
    _yaml = None

HAVE_YAML = _yaml is not None
# libyaml's C loader when this Python has it: identical results to safe_load on every frontmatter in a large
# data folder, about seven times faster, and a job search reads thousands of job files several times a run.
_LOADER = getattr(_yaml, "CSafeLoader", None) if HAVE_YAML else None

FENCE = "---"


class FrontmatterError(ValueError):
    """Raised when a frontmatter block cannot be parsed."""


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def split(text: str) -> tuple[str | None, str]:
    """Split text into (raw_frontmatter or None, body)."""
    if not text.startswith(FENCE):
        return None, text
    first_nl = text.find("\n")
    if first_nl == -1:
        return None, text
    first_line = text[:first_nl].rstrip("\r")
    if first_line != FENCE:
        return None, text
    rest = text[first_nl + 1 :]
    # closing fence: a line that is exactly '---' (or end of file)
    m = re.search(r"^---[ \t]*\r?$", rest, flags=re.MULTILINE)
    if m is None:
        raise FrontmatterError("frontmatter opened with '---' but never closed")
    raw = rest[: m.start()]
    body = rest[m.end() :]
    if body.startswith("\r\n"):
        body = body[2:]
    elif body.startswith("\n"):
        body = body[1:]
    return raw, body


def parse_yaml(raw: str) -> dict[str, Any]:
    """Parse a YAML mapping; empty input gives {}."""
    if not raw.strip():
        return {}
    if HAVE_YAML:
        try:
            data = _yaml.load(raw, Loader=_LOADER) if _LOADER is not None else _yaml.safe_load(raw)
        except Exception as exc:  # yaml.YAMLError and friends
            raise FrontmatterError(f"invalid YAML frontmatter: {exc}") from exc
    else:
        data = _parse_yaml_fallback(raw)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise FrontmatterError("frontmatter must be a mapping")
    return data


def loads(text: str) -> tuple[dict[str, Any], str]:
    raw, body = split(text)
    if raw is None:
        return {}, body
    return parse_yaml(raw), body


def load(path: str | Path) -> tuple[dict[str, Any], str]:
    return loads(Path(path).read_text(encoding="utf-8"))


def iter_docs(root: str | Path, glob: str) -> Iterator[tuple[Path, dict[str, Any], str]]:
    """Yield (path, meta, body) for every file matching ``glob`` under root.

    Files are yielded in sorted order.  A file whose frontmatter cannot be
    parsed is yielded with ``meta == {"_error": message}`` and its raw text
    as body so callers (lint) can report it instead of crashing.
    """
    rootp = Path(root)
    if not rootp.exists():
        return
    for path in sorted(p for p in rootp.glob(glob) if p.is_file()):
        try:
            meta, body = load(path)
        except FrontmatterError as exc:
            yield path, {"_error": str(exc)}, path.read_text(encoding="utf-8", errors="replace")
            continue
        except UnicodeDecodeError as exc:
            yield path, {"_error": f"not utf-8: {exc}"}, ""
            continue
        yield path, meta, body


# --------------------------------------------------------------------------- #
# Dumping
# --------------------------------------------------------------------------- #
if HAVE_YAML:
    class _NoAliasDumper(_yaml.SafeDumper):
        def ignore_aliases(self, data: Any) -> bool:  # noqa: D401 (PyYAML hook)
            return True


def dump(meta: dict[str, Any], body: str) -> str:
    """Serialize meta + body back into a frontmatter document."""
    if not meta:
        return body
    if HAVE_YAML:
        # No anchors/aliases (&id001/*id001): the fallback parser and Obsidian cannot read them, and a
        # record must stay readable when one object fills two fields.
        raw = _yaml.dump(meta, Dumper=_NoAliasDumper, sort_keys=False, allow_unicode=True, default_flow_style=False)
    else:
        raw = _dump_yaml_fallback(meta)
    if not raw.endswith("\n"):
        raw += "\n"
    return f"{FENCE}\n{raw}{FENCE}\n{body}"


# --------------------------------------------------------------------------- #
# Minimal YAML fallback (only used when pyyaml is not importable)
# --------------------------------------------------------------------------- #
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?")
_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?\d+\.\d+$")


def _strip_comment(line: str) -> str:
    """Remove a trailing ' # comment' outside of quotes."""
    out, quote = [], None
    for i, ch in enumerate(line):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _scalar(token: str) -> Any:
    t = token.strip()
    if t == "" or t in ("null", "~", "Null", "NULL"):
        return None
    if len(t) >= 2 and t[0] == t[-1] and t[0] in ("'", '"'):
        if t[0] == '"':
            try:
                return json.loads(t)
            except json.JSONDecodeError:
                return t[1:-1]
        return t[1:-1].replace("''", "'")
    low = t.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if _INT_RE.match(t):
        return int(t)
    if _FLOAT_RE.match(t):
        return float(t)
    if _DATE_RE.match(t):
        try:
            return dt.date.fromisoformat(t)
        except ValueError:
            return t
    if _DATETIME_RE.match(t):
        try:
            return dt.datetime.fromisoformat(t.replace("Z", "+00:00"))
        except ValueError:
            return t
    if t.startswith("[") and t.endswith("]"):
        return [_scalar(x) for x in _split_inline(t[1:-1])]
    if t.startswith("{") and t.endswith("}"):
        result: dict[str, Any] = {}
        for item in _split_inline(t[1:-1]):
            if ":" in item:
                k, v = item.split(":", 1)
                result[_scalar(k) if k.strip()[:1] in "'\"" else k.strip()] = _scalar(v)
        return result
    return t


def _split_inline(s: str) -> list[str]:
    """Split 'a, b, [c, d], "e, f"' on top-level commas."""
    parts, buf, depth, quote = [], [], 0, None
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf)
    if tail.strip() or parts:
        parts.append(tail)
    return [p for p in parts if p.strip()]


def _parse_yaml_fallback(raw: str) -> dict[str, Any]:
    lines = [_strip_comment(l.rstrip("\n")) for l in raw.splitlines()]
    lines = [l for l in lines if l.strip()]
    result, i = _parse_block(lines, 0, 0)
    return result


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _parse_block(lines: list[str], i: int, indent: int) -> tuple[Any, int]:
    """Parse lines[i:] at the given indent; returns (value, next_index)."""
    if i >= len(lines):
        return {}, i
    if lines[i].lstrip().startswith("- "):
        return _parse_list(lines, i, _indent(lines[i]))
    mapping: dict[str, Any] = {}
    while i < len(lines):
        line = lines[i]
        ind = _indent(line)
        if ind < indent:
            break
        if ind > indent:
            raise FrontmatterError(f"unexpected indentation: {line!r}")
        stripped = line.strip()
        if ":" not in stripped:
            raise FrontmatterError(f"expected 'key: value': {line!r}")
        key, _, value = stripped.partition(":")
        key = key.strip()
        if key[:1] in ("'", '"'):
            key = key[1:-1]
        value = value.strip()
        i += 1
        if value == "":
            # nested block or empty
            if i < len(lines) and _indent(lines[i]) > indent:
                child, i = _parse_block(lines, i, _indent(lines[i]))
                mapping[key] = child
            elif i < len(lines) and lines[i].lstrip().startswith("- ") and _indent(lines[i]) == indent:
                child, i = _parse_list(lines, i, indent)
                mapping[key] = child
            else:
                mapping[key] = None
        elif value in ("|", ">", "|-", ">-"):
            buf = []
            block_indent = None
            while i < len(lines) and _indent(lines[i]) > indent:
                if block_indent is None:
                    block_indent = _indent(lines[i])
                buf.append(lines[i][block_indent:])
                i += 1
            joiner = "\n" if value.startswith("|") else " "
            mapping[key] = joiner.join(buf) + ("" if value.endswith("-") else "\n")
        else:
            mapping[key] = _scalar(value)
    return mapping, i


def _parse_list(lines: list[str], i: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    while i < len(lines):
        line = lines[i]
        ind = _indent(line)
        if ind < indent or not line.lstrip().startswith("- "):
            break
        if ind > indent:
            raise FrontmatterError(f"unexpected indentation: {line!r}")
        content = line.strip()[2:].strip()
        i += 1
        if ":" in content and not content.startswith(("[", "{", "'", '"')):
            # inline mapping item: "- key: value" possibly followed by more keys
            sub_lines = [" " * (indent + 2) + content]
            while i < len(lines) and _indent(lines[i]) > indent and not lines[i].lstrip().startswith("- "):
                sub_lines.append(lines[i])
                i += 1
            child, _ = _parse_block(sub_lines, 0, indent + 2)
            items.append(child)
        else:
            items.append(_scalar(content))
    return items, i


def _dump_scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    s = str(v)
    needs_quote = (
        s == ""
        or s.strip() != s
        or any(ch in s for ch in ":#[]{}\n\"'")
        or s.lower() in ("true", "false", "null", "yes", "no", "~")
        or _INT_RE.match(s)
        or _FLOAT_RE.match(s)
        or _DATE_RE.match(s)
    )
    return json.dumps(s, ensure_ascii=False) if needs_quote else s


def _dump_yaml_fallback(data: Any, indent: int = 0) -> str:
    pad = " " * indent
    out: list[str] = []
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict) and v:
                out.append(f"{pad}{k}:")
                out.append(_dump_yaml_fallback(v, indent + 2))
            elif isinstance(v, list) and v and any(isinstance(x, (dict, list)) for x in v):
                out.append(f"{pad}{k}:")
                out.append(_dump_yaml_fallback(v, indent))
            elif isinstance(v, list):
                out.append(f"{pad}{k}: [{', '.join(_dump_scalar(x) for x in v)}]")
            elif isinstance(v, dict):
                out.append(f"{pad}{k}: {{}}")
            else:
                out.append(f"{pad}{k}: {_dump_scalar(v)}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                inner = _dump_yaml_fallback(item, indent + 2).splitlines()
                if inner:
                    out.append(f"{pad}- {inner[0].strip()}")
                    out.extend(inner[1:])
                else:
                    out.append(f"{pad}- {{}}")
            else:
                out.append(f"{pad}- {_dump_scalar(item)}")
    else:
        out.append(f"{pad}{_dump_scalar(data)}")
    return "\n".join(x for x in out if x != "") + "\n"


__all__ = [
    "FrontmatterError",
    "HAVE_YAML",
    "dump",
    "iter_docs",
    "load",
    "loads",
    "parse_yaml",
    "split",
]
