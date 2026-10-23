"""Shared helpers for the job-search department scripts.

Every script in this folder imports from here so paths, slugs, record I/O and the touch log
behave identically. Pure stdlib plus the OS kernel's frontmatter module.

Conventions:
  - One markdown file per record with YAML frontmatter; bodies may carry an append-only log
    section ("## Timeline" for prospects, "## Events" for applications).
  - Touches are JSON lines in touches/YYYY-MM.jsonl, append-only.
  - All dates are absolute ISO (YYYY-MM-DD, or YYYY-MM-DDTHH:MM:SS for timestamps).
  - Scripts never send anything. Outbound content is written as drafts; the OS sender role
    executes only approved items.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from zoneinfo import ZoneInfo

import agency_paths as _ap  # noqa: E402  (code root / data root / runtime root; see agency_paths.py)
import agency_config as _cfg  # noqa: E402  (identity / style / policy values; see agency_config.py)

JS_ROOT = _ap.DATA_ROOT                                # your data folder
CODE_ROOT = _ap.CODE_ROOT
OS_ROOT = _ap.RUNTIME_ROOT                             # approvals (work/approvals), run receipts (logs/runs), vault/
_KERNEL = _ap.KERNEL_DIR
if _ap.EVAL_MARK.is_file():
    sys.dont_write_bytecode = True                       # keep eval sandboxes free of __pycache__
sys.path.insert(0, str(_KERNEL))
import frontmatter as fm  # noqa: E402  (OS kernel module)

def os_root_for(js_root: Path) -> Path:
    """The runtime root (approvals, run receipts, vault) for a data folder given on the command line (--root):
    the root itself when it is a marked eval sandbox, RUNTIME_ROOT for the configured data folder, `<root>/runtime`
    for a data folder that has one, else (legacy layout) three folders up
    (<root>/domains/career/projects/<project> -> <root>)."""
    js_root = Path(js_root).resolve()
    if (js_root / ".eval-sandbox").is_file():
        return js_root
    if js_root.resolve() == JS_ROOT.resolve():
        return OS_ROOT
    if (js_root / "runtime").is_dir():
        return js_root / "runtime"
    try:
        return js_root.parents[3]
    except IndexError:
        return OS_ROOT


DIRS = {
    "jobs": JS_ROOT / "jobs",
    "applications": JS_ROOT / "applications",
    "prospects": JS_ROOT / "prospects",
    "companies": JS_ROOT / "companies",
    "touches": JS_ROOT / "touches",
    "outbox": JS_ROOT / "outbox",
    "profile": JS_ROOT / "profile",
    "resumes": JS_ROOT / "resumes",
    "targets": JS_ROOT / "targets",
    "legal": JS_ROOT / "legal",
    "cache": JS_ROOT / ".cache",           # gitignored-by-convention scratch (API responses)
    "state": JS_ROOT / "state",            # ledgers: seen.jsonl, harvest state, etc.
}


# --------------------------------------------------------------------------- dates
def _zone() -> ZoneInfo:
    """The department's time zone: identity.timezone from the config (default UTC); the department's day is local."""
    try:
        return ZoneInfo(str(_cfg.get("identity.timezone", "UTC") or "UTC"))
    except Exception:          # an unknown zone name: fall back rather than break every script
        return ZoneInfo("UTC")


DEPT_TZ = _zone()


def today() -> date:
    return date.today()


def dept_day(instant: datetime) -> date:
    """The department's calendar day at an instant (America/Los_Angeles); a naive instant is read as UTC."""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(DEPT_TZ).date()


def cli_date(text: str) -> date:
    """argparse `type=` for --today and other day flags: strict YYYY-MM-DD."""
    import argparse
    value = str(text).strip()
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError(value)
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {text!r}") from None


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def run_stamp(day: date | None = None, *, z: bool = False) -> str:
    """UTC timestamp for record content (timelines, receipts, sent_at, harvested_at, generated_at): the wall
    clock when `day` is None or today, else `day` at 12:00:00 UTC, so a run dated with --today stamps that
    day. z=True writes the trailing "Z" form."""
    stamp = utc_now_iso() if day is None or day == today() else f"{day.isoformat()}T12:00:00+00:00"
    return stamp.replace("+00:00", "Z") if z else stamp


def parse_date(value: Any) -> date | None:
    """Accept date, datetime, 'YYYY-MM-DD', ISO timestamps, or epoch ms/seconds; else None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        secs = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(secs, tz=timezone.utc).date()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M:%S %Z", "%m/%d/%y, %I:%M %p", "%m/%d/%Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def business_days_between(start: date, end: date) -> int:
    """Count Mon-Fri days after start up to and including end (0 if end <= start)."""
    if end <= start:
        return 0
    days = 0
    cur = start.toordinal() + 1
    while cur <= end.toordinal():
        if date.fromordinal(cur).weekday() < 5:
            days += 1
        cur += 1
    return days


def add_business_days(start: date, n: int) -> date:
    cur = start
    added = 0
    while added < n:
        cur = date.fromordinal(cur.toordinal() + 1)
        if cur.weekday() < 5:
            added += 1
    return cur


# --------------------------------------------------------------------------- text
def slugify(text: str, max_len: int = 60) -> str:
    norm = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    norm = re.sub(r"[^a-zA-Z0-9]+", "-", norm).strip("-").lower()
    return norm[:max_len].strip("-") or "unknown"


_COMPANY_SUFFIXES = re.compile(
    r"\b(inc|inc\.|llc|l\.l\.c\.|ltd|ltd\.|corp|corporation|co|company|gmbh|plc|pbc|ai labs?)\b\.?", re.I)


def normalize_company(name: str) -> str:
    """Stable company key: lowercase, no legal suffixes, no punctuation."""
    s = _COMPANY_SUFFIXES.sub("", str(name or ""))
    s = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    return re.sub(r"\s+", " ", s)


_TITLE_NOISE = re.compile(r"\((?:[^)]*)\)|\b(remote|hybrid|onsite|on-site|us|usa|new grad|contract)\b", re.I)


def normalize_title(title: str) -> str:
    s = _TITLE_NOISE.sub(" ", str(title or "").lower())
    s = s.replace("sr.", "senior").replace("sr ", "senior ").replace("jr.", "junior")
    s = re.sub(r"[^a-z0-9+#]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def short_hash(*parts: str, n: int = 10) -> str:
    h = hashlib.sha1("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return h[:n]


def text_hash(text: str) -> str:
    collapsed = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    return hashlib.sha1(collapsed.encode("utf-8")).hexdigest()


def html_to_text(html: str) -> str:
    """Minimal HTML → text for job descriptions (no external deps)."""
    import html as _html
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", str(html or ""))
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h[1-6]>|</div>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "\n- ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(_html.unescape(s))
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s)
    return s.strip()


# --------------------------------------------------------------------------- records
def read_record(path: Path) -> tuple[dict[str, Any], str]:
    return fm.load(path)


def write_record(path: Path, meta: dict[str, Any], body: str) -> None:
    """Write a record. A job, prospect, application or outbox record is stamped on the way out with its
    `type:` and its `links:` graph edges (scripts/graph_links.py), so every owning script keeps them current."""
    from graph_links import stamp_path   # sibling module; imported here to keep js_common's import light
    stamp_path(path, meta)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fm.dump(meta, body), encoding="utf-8")


def iter_records(folder: Path, pattern: str = "*.md") -> Iterator[tuple[Path, dict[str, Any], str]]:
    if not folder.is_dir():
        return
    for p in sorted(folder.glob(pattern)):
        if p.is_file() and not p.name.startswith(("_", ".")) and p.name != "README.md":
            meta, body = fm.load(p)
            yield p, meta, body


def append_section_line(body: str, heading: str, line: str) -> str:
    """Append `line` under `## heading` (created at the end if missing). Append-only logs."""
    marker = f"## {heading}"
    if marker not in body:
        body = body.rstrip("\n") + f"\n\n{marker}\n"
    head, _, tail = body.partition(marker)
    # insert at the end of that section (before the next "## " or EOF)
    m = re.search(r"\n## ", tail)
    if m:
        section, rest = tail[: m.start()], tail[m.start():]
    else:
        section, rest = tail, ""
    section = section.rstrip("\n") + f"\n{line}\n"
    return head + marker + section + rest


# --------------------------------------------------------------------------- jsonl
def append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def log_touch(touch: dict[str, Any], root: Path | None = None) -> Path:
    """Append a touch (design §5.4) to touches/YYYY-MM.jsonl. Requires at, prospect, channel,
    direction, kind, status."""
    required = ("at", "prospect", "channel", "direction", "kind", "status")
    missing = [k for k in required if not touch.get(k)]
    if missing:
        raise ValueError(f"touch missing {missing}")
    at = parse_date(touch["at"]) or today()
    folder = (root or JS_ROOT) / "touches"
    path = folder / f"{at:%Y-%m}.jsonl"
    append_jsonl(path, touch)
    return path


def iter_touches(root: Path | None = None) -> Iterable[dict[str, Any]]:
    folder = (root or JS_ROOT) / "touches"
    if not folder.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for p in sorted(folder.glob("*.jsonl")):
        rows.extend(read_jsonl(p))
    return rows


# --------------------------------------------------------------------------- untrusted content
def wrap_untrusted(text: str, kind: str = "external", limit: int = 4000) -> str:
    """Wrap external content (emails, JDs, messages) so prompts treat it as data, never
    instructions. Truncates to `limit` characters."""
    clipped = str(text or "")[:limit]
    clipped = clipped.replace(f"</untrusted_{kind}>", f"</untrusted_{kind}_>")
    return f"<untrusted_{kind}>\n{clipped}\n</untrusted_{kind}>"


# --------------------------------------------------------------------------- tag vocabulary
# A record's `tags:` take only what schemas/tags.yaml allows: domain/<slug>, project/<existing slug>, the
# topics and the record tags. One reader: the kernel's lint.load_tag_vocabulary (what the nightly lint checks).
def _vocab_root(os_root: Path | str | None = None) -> Path:
    """`os_root` (default OS_ROOT), or the kernel's root when it has no schemas/tags.yaml (an eval sandbox)."""
    import lint  # noqa: E402  (OS kernel module, on sys.path beside frontmatter)
    root = Path(os_root) if os_root is not None else OS_ROOT
    return root if (root / lint.TAGS_FILE).is_file() else CODE_ROOT


def tag_vocabulary(os_root: Path | str | None = None) -> dict[str, Any]:
    """lint.load_tag_vocabulary for the vocabulary root (_vocab_root), plus `records`: the record tags (the
    `records:` keys, the machine flags scripts and roles write on jobs and prospects)."""
    import lint  # noqa: E402
    root = _vocab_root(os_root)
    vocab = lint.load_tag_vocabulary(root)
    path = root / lint.TAGS_FILE
    data = fm.parse_yaml(path.read_text(encoding="utf-8")) if path.is_file() else {}
    vocab["records"] = sorted(str(t) for t in ((data or {}).get("records") or {}))
    return vocab


def unknown_tags(tags: Iterable[Any], vocab: dict[str, Any] | None = None) -> list[str]:
    """The tags in `tags` the vocabulary does not allow, each once, in order."""
    import lint  # noqa: E402
    vocab = tag_vocabulary() if vocab is None else vocab
    return [t for t in dict.fromkeys(str(x) for x in tags) if not lint.tag_known(t, vocab)]


def check_tags(tags: Iterable[Any], os_root: Path | str | None = None) -> None:
    """Raise ValueError unless every tag is allowed by schemas/tags.yaml; the message lists the allowed
    record tags. A missing or empty vocabulary refuses every tag (missing is not empty)."""
    tags = [str(t) for t in tags]
    if not tags:
        return
    vocab = tag_vocabulary(os_root)
    if vocab.get("enforcement") == "off" or not vocab.get("allowed"):
        raise ValueError("no tag vocabulary (schemas/tags.yaml missing or empty): no tag can be checked, "
                         "so none is written")
    bad = unknown_tags(tags, vocab)
    if bad:
        raise ValueError(
            f"unknown tag{'s' if len(bad) != 1 else ''} {', '.join(repr(t) for t in bad)}: not in "
            f"schemas/tags.yaml. Record tags allowed: {', '.join(vocab['records'])}. Also allowed: the topic "
            f"tags there, domain/<slug> and project/<slug> for an existing project. A new record tag goes into "
            f"tags.yaml `records:` first; it is never written ad hoc.")


# --------------------------------------------------------------------------- the learning gate and the goals
# Charter §1: a change driven by outcome rates needs about 15 tries in that segment, or one clear cause. One
# constant for every reader: strategy_review's MIN_N (a multiplier between the paths) and lessons.py (an experiment
# is ready to decide).
MIN_TRIES = 15

# targets/goals.yaml (as many interviews as possible, no ceiling): a daily and a weekly FLOOR per metric.
# It replaces state/recruiter/target.yaml, read only when goals.yaml is missing.
GOALS_FILE = Path("targets") / "goals.yaml"
LEGACY_TARGET_FILE = Path("state") / "recruiter" / "target.yaml"
POST_CHANNELS = ("linkedin", "x", "yc")         # the channels the posts lane reads; a floor per channel
GOAL_METRICS = ("interviews_booked", "interviews_held", "applications_mass", "applications_focused", "first_touches",
                *(f"posts_read_{c}" for c in POST_CHANNELS))
GOAL_KEYS = ("daily", "weekly", "why")


def _floor(metric: str, key: str, value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"targets/goals.yaml: {metric}.{key} must be a whole number 0 or more, or null (got {value!r})")
    return value


def load_goals(root: Path | None = None) -> dict[str, Any]:
    """The goals: {"source", "status", "floors": {metric: {"daily": int|None, "weekly": int|None, "why": str}}}.
    targets/goals.yaml first; when it is missing, state/recruiter/target.yaml's `weekly_interviews` as the weekly
    interviews-booked floor (source names the file); neither: source None and no floors (missing is not empty).
    A floor has no ceiling: any key other than daily, weekly and why is refused (ValueError), as is an unknown metric."""
    root = Path(root or JS_ROOT)
    path = root / GOALS_FILE
    if path.is_file():
        data = fm.parse_yaml(path.read_text(encoding="utf-8")) or {}
        raw = data.get("floors")
        if not isinstance(raw, dict) or not raw:
            raise ValueError("targets/goals.yaml: no `floors:` mapping")
        floors: dict[str, dict[str, Any]] = {}
        for metric, spec in raw.items():
            if metric not in GOAL_METRICS:
                raise ValueError(f"targets/goals.yaml: unknown metric {metric!r} (known: {', '.join(GOAL_METRICS)})")
            spec = spec if isinstance(spec, dict) else {}
            extra = sorted(set(spec) - set(GOAL_KEYS))
            if extra:
                raise ValueError(f"targets/goals.yaml: {metric} has {', '.join(extra)}; a goal is a floor with no "
                                 "ceiling: only daily, weekly and why")
            floors[metric] = {"daily": _floor(metric, "daily", spec.get("daily")),
                              "weekly": _floor(metric, "weekly", spec.get("weekly")), "why": str(spec.get("why") or "")}
        return {"source": GOALS_FILE.as_posix(), "status": str(data.get("status") or "set"),
                "set_by": data.get("set_by"), "floors": floors}
    legacy = root / LEGACY_TARGET_FILE
    if legacy.is_file():
        v = (fm.parse_yaml(legacy.read_text(encoding="utf-8")) or {}).get("weekly_interviews")
        weekly = int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None
        return {"source": LEGACY_TARGET_FILE.as_posix(), "status": "legacy", "set_by": None,
                "floors": {"interviews_booked": {"daily": None, "weekly": weekly, "why": "weekly target"}}
                if weekly else {}}
    return {"source": None, "status": "absent", "set_by": None, "floors": {}}


def weekly_interview_floor(root: Path | None = None) -> int | None:
    """The weekly interviews-booked floor (load_goals), or None when no goal file sets one or the file is unreadable."""
    try:
        return load_goals(root)["floors"].get("interviews_booked", {}).get("weekly") or None
    except (OSError, ValueError, fm.FrontmatterError):
        return None
