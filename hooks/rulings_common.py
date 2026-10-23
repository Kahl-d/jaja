#!/usr/bin/env python3
"""
rulings_common.py - shared reader for the rulings registries.

A ruling is the owner's own words, recorded once in `<hub>/rulings.yaml` (one entry per ruling id:
id, date, quote (verbatim), source, reading, topic, scope{lanes, roles, paths}, status,
enforced_by). The hooks `lane_rules.py` (SessionStart) and `role_rules.py` (SubagentStart)
read that file and print the standing rulings whose scope matches the session's lane type or
the subagent's role. Nothing else copies a ruling: cards, briefs and memory link its id;
this file is the one place the words live.

Why: rulings copied by hand into many files go stale (a superseded answer keeps being used) and
a paraphrase hardens into a rule that works against the owner's actual words. Linking by id and
printing from one registry avoids both.

Fail-open: every helper returns an empty result on any error. Hooks never stop a session from starting.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

try:
    import yaml  # PyYAML is used by the department scripts
except Exception:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

sys.path.insert(0, str(Path(__file__).resolve().parent))          # importable from the hooks, the kernel and the tests
from lane_common import KERNEL_DIR as _KDIR, WORKSPACE, find_hubs, strip_namespace  # noqa: E402
import agency_config as cfg  # noqa: E402  (lane_common puts scripts/ on sys.path)

LANE_PREFIX = "lane-"


def registries() -> list[Path]:
    """The rulings registries: `<data folder>/hub/rulings.yaml`, then any other `<hub>/rulings.yaml` next to a
    programme.json. A registry that does not exist is simply absent (an empty template is templates/data/hub/rulings.yaml)."""
    try:
        found = [WORKSPACE / "hub" / "rulings.yaml"]
        found += [p.parent / "rulings.yaml" for p in find_hubs()]
        out: list[Path] = []
        for p in found:
            if p.is_file() and p not in out:
                out.append(p)
        return out
    except Exception:
        return []


def load_rulings(path: Path) -> list[dict[str, Any]]:
    if yaml is None:
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    rows = data.get("rulings") if isinstance(data, dict) else data
    return [r for r in (rows or []) if isinstance(r, dict) and r.get("id")]


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v if x is not None]


def lane_aliases() -> dict[str, tuple[str, ...]]:
    """A lane inherits the rulings of the lanes it absorbed: {lane: (absorbed lane, ...)} from the
    `policy.lane_aliases` config section (empty by default)."""
    raw = cfg.get("policy.lane_aliases", {}) or {}
    return {str(k).lower(): tuple(str(x).lower() for x in (v or ())) for k, v in raw.items()} if isinstance(raw, dict) else {}


def lane_keys(lane: str) -> set[str]:
    k = lane.lower()
    return {k, *lane_aliases().get(k, ())}


def standing_for(rows: list[dict[str, Any]], *, lane: str | None = None, role: str | None = None) -> list[dict[str, Any]]:
    """Standing rulings whose scope names the lane type or the role. A scope of ["all"] or an
    empty scope list matches nothing (a ruling binds only what it names), except the literal "all"."""
    out: list[dict[str, Any]] = []
    for r in rows:
        if str(r.get("status", "")).lower() != "standing":
            continue
        scope = r.get("scope") or {}
        lanes = [s.lower() for s in _as_list(scope.get("lanes"))]
        roles = [s.lower() for s in _as_list(scope.get("roles"))]
        if lane and (lane_keys(lane) & set(lanes) or "all" in lanes):
            out.append(r)
        elif role and (role.lower() in roles or "all" in roles):
            out.append(r)
    return out


BUDGET = 8500          # characters: SessionStart and SubagentStart output is capped; past the cap it is cut silently
QUOTE_MAX = 240        # a longer quote is clipped with an explicit [...] and its full text stays in rulings.yaml


def _key(r: dict[str, Any]) -> tuple[int, str]:
    """Newest first: a later ruling refines an earlier one."""
    m = str(r.get("id", "0")).split("-")[-1]
    return (-int(m) if m.isdigit() else 0, str(r.get("id")))


def render(rows: list[dict[str, Any]], heading: str, registry: str = "hub/rulings.yaml") -> str:
    """One line per ruling, newest first, inside BUDGET characters: id, date, the owner's words (clipped with [...]
    when long), the source. Readings are marked. Rulings past the budget are listed by id, never dropped silently."""
    if not rows:
        return ""
    lines = [heading, f"Full text of every ruling: {registry} (read it before acting on a clipped one)."]
    footer = ("These are the owner's words; a reading is a reading. A new ruling is recorded in rulings.yaml by the "
              "Recruiter or the orchestrator with the owner's exact words, never relayed as a paraphrase.")
    shown: list[tuple[str, str]] = []                    # (id, line), newest first
    left: list[str] = []
    for r in sorted(rows, key=_key):
        quote = r.get("quote")
        if quote:
            q = " ".join(str(quote).split())
            if len(q) > QUOTE_MAX:
                q = q[:QUOTE_MAX].rsplit(" ", 1)[0] + " [...]"
            line = f'- {r["id"]} ({r.get("date", "")}): "{q}"'
        else:
            reading = " ".join(str(r.get("reading", "")).split())
            if len(reading) > QUOTE_MAX:
                reading = reading[:QUOTE_MAX].rsplit(" ", 1)[0] + " [...]"
            line = f"- {r['id']} ({r.get('date', '')}): (reading, not the owner's words) {reading}"
        shown.append((str(r["id"]), line))

    def text() -> str:
        also = [f"Also standing (read in {registry}): " + ", ".join(left)] if left else []
        return "\n".join(lines + [line for _, line in shown] + also + [footer])

    while len(text()) > BUDGET and shown:                # the "Also standing" line and the footer count too
        left.insert(0, shown.pop()[0])
    return text()


# ---- the compiled index and the per-lane digest ----
# rulings.yaml stays the one place the words live. The index (<hub>/rulings.index.json, gitignored, rebuilt when
# rulings.yaml or rulings-pins.yaml is newer) holds one line per standing ruling, so a start loads ~0.002 s of JSON
# instead of ~0.8 s of YAML, and the digest prints pinned principles first, then the lane's recent rulings, then a
# count with the command that shows the rest. Old clipping put yesterday's day orders in full and as a bare id.

LINE_MAX = 170          # one-line reading per ruling in the digest
DIGEST_CAP = 3500       # characters per digest (~900 tokens; the old print was 8,500)
DIGEST_DAYS = 14        # non-pinned rulings older than this are counted, not printed
UNIVERSAL_KINDS = ("principle",)


def one_line(text: Any) -> str:
    import re
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    t = re.sub(r"^Reading:\s*", "", t)
    first = re.split(r"(?<=[.;])\s", t, maxsplit=1)[0]
    return first if len(first) <= LINE_MAX else first[: LINE_MAX - 1].rstrip() + "\u2026"


def load_pins(hub: Path) -> set[str] | None:
    """Ids in <hub>/rulings-pins.yaml: the curated principles every lane and role sees first."""
    p = hub / "rulings-pins.yaml"
    if not p.is_file() or yaml is None:
        return None
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    return {str(x) for x in (data.get("pins") or [])}


def _id_num(r: dict[str, Any]) -> int:
    m = str(r.get("id", "0")).split("-")[-1]
    return int(m) if m.isdigit() else 0


def build_index(reg: Path) -> list[dict[str, Any]]:
    """Compile <hub>/rulings.index.json from rulings.yaml. Returns the standing rows, newest first."""
    import json
    hub = reg.parent
    pins = load_pins(hub)
    rows = []
    for r in load_rulings(reg):
        if str(r.get("status", "")).lower() != "standing":
            continue
        scope = r.get("scope") or {}
        lanes = [s.lower() for s in _as_list(scope.get("lanes"))]
        roles = [s.lower() for s in _as_list(scope.get("roles"))]
        kind = str(r.get("kind") or "").lower() or None
        pinned = (str(r["id"]) in pins) if pins is not None else (
            kind in UNIVERSAL_KINDS or (lanes == ["all"] and roles == ["all"]
                                        and str(r.get("enforced_by") or "none").lower() != "none"))
        rows.append({"id": str(r["id"]), "date": str(r.get("date") or ""), "topic": r.get("topic"),
                     "lanes": lanes, "roles": roles, "kind": kind, "expires": str(r.get("expires") or ""),
                     "line": one_line(r.get("reading") or r.get("quote")), "pinned": pinned,
                     "enforced_by": r.get("enforced_by")})
    rows.sort(key=lambda x: (x["date"], _id_num(x)), reverse=True)
    try:
        (hub / "rulings.index.json").write_text(json.dumps({"source": "rulings.yaml", "standing": rows},
                                                           ensure_ascii=False, indent=0), encoding="utf-8")
    except Exception:
        pass
    return rows


def index_rows(reg: Path) -> list[dict[str, Any]]:
    """The standing rows from the compiled index, rebuilt when rulings.yaml or the pins file is newer."""
    import json
    hub = reg.parent
    idx, pins = hub / "rulings.index.json", hub / "rulings-pins.yaml"
    try:
        newest = max(reg.stat().st_mtime, pins.stat().st_mtime if pins.is_file() else 0)
        if idx.is_file() and idx.stat().st_mtime >= newest:
            return json.loads(idx.read_text(encoding="utf-8"))["standing"]
    except Exception:
        pass
    return build_index(reg)


def digest(rows: list[dict[str, Any]], *, lane: str | None = None, role: str | None = None,
           cap: int = DIGEST_CAP, days: int = DIGEST_DAYS, today: str | None = None,
           registry: str = "hub/rulings.yaml") -> str:
    """Pinned first, then the scope's standing rulings from the last `days` (lane- or role-specific before "all",
    newest first; expired orders dropped), each one line, to `cap` characters; then one line counting the rest."""
    import datetime as _dt
    key, field = ((lane or "").lower(), "lanes") if lane else ((role or "").lower(), "roles")
    day = _dt.date.fromisoformat(today) if today else _dt.date.today()
    cutoff = str(day - _dt.timedelta(days=days))
    pinned = [r for r in rows if r.get("pinned")]
    keys = lane_keys(key) if lane else {key}
    scoped = [r for r in rows if not r.get("pinned") and (keys & set(r[field]) or "all" in r[field])
              and not (r.get("expires") and r["expires"] < str(day))]
    recent = [r for r in scoped if r["date"] >= cutoff]
    recent.sort(key=lambda r: not (keys & set(r[field])))                        # stable: specific first, newest first
    out = [f"[rulings] {len(pinned)} pinned + {len(scoped)} standing for {field[:-1]} {key}. "
           f"Words: python3 {_KDIR}/rulings_digest.py show <id> (source {registry})."]
    out += [f"- {r['id']} (pinned): {r['line']}" for r in pinned]
    used, shown = sum(len(x) + 1 for x in out), 0
    for r in recent:
        line = f"- {r['id']} ({r['date'][5:]}): {r['line']}"
        if used + len(line) + 1 > cap:
            break
        out.append(line)
        used += len(line) + 1
        shown += 1
    rest = len(scoped) - shown
    if rest:
        out.append(f"- {rest} more standing for {key}: `python3 {_KDIR}/rulings_digest.py digest "
                   f"--{field[:-1]} {key} --all`; act on a ruling only after reading its words with `show`.")
    return "\n".join(out)


def first_time(kind: str, key: str | None) -> bool:
    """True the first time this (hook kind, session or agent id) is seen. The plugin and a project's settings may
    both register a rulings hook; the second run prints nothing. Without an id there is nothing to dedupe on."""
    if not key:
        return True
    import hashlib
    import tempfile
    from pathlib import Path as _P
    marker = _P(tempfile.gettempdir()) / "agency-rulings-seen" / f"{kind}-{hashlib.sha256(key.encode()).hexdigest()[:20]}"
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch(exist_ok=False)
        return True
    except FileExistsError:
        return False
    except Exception:
        return True


def lane_type_from_agent(agent_type: str | None) -> str | None:
    """`lane-resume` (or the plugin-scoped `jaja:lane-resume`) -> `resume`; anything else -> None."""
    if not agent_type:
        return None
    a = strip_namespace(agent_type).lower()
    if a.startswith(LANE_PREFIX):
        return a[len(LANE_PREFIX):] or None
    return None
