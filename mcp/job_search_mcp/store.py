"""The data layer behind the job-search MCP tools. Plain Python, no MCP imports, fully testable.

Reads the department records (jobs, prospects, applications, touches, profile, resumes) and
writes only records and drafts inside the workspace, through the single-writer scripts wherever
they exist: prospects.py set (status, priority, tags), tracker.py event, outbox.py draft and
batch, dashboard.py build() (read-only). Only fields a script has no flag for, and approval
files for non-message actions (js_comms_util.write_approval), are written here directly.
Nothing here sends, posts, submits or approves, and no input or output carries a credential.

Every result is compact JSON-able data under MAX_BYTES; lists page with (limit, cursor). Text
that came from outside (JDs, email and LinkedIn messages, event evidence quoted from mail) is
wrapped with js_common.wrap_untrusted so agents read it as data, never as instructions.

Roots: JOB_AGENCY_DATA / JOB_SEARCH_ROOT (data folder) and JOB_AGENCY_RUNTIME (repo) override the defaults for tests.
The scripts themselves (js_common, js_comms_util, tracker.py) always come from this checkout.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import yaml

PKG_DIR = Path(__file__).resolve().parent
CODE_ROOT = PKG_DIR.parents[1]                       # job_search_mcp -> mcp -> repo root
SCRIPTS = CODE_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import js_common as jc  # noqa: E402
import js_comms_util as cu  # noqa: E402
import profile_titles as pt  # noqa: E402

DEFAULT_JS_ROOT = jc.JS_ROOT                         # the data folder (JOB_AGENCY_DATA)
DEFAULT_OS_ROOT = jc.OS_ROOT
SCHEMAS = CODE_ROOT / "schemas"

MAX_BYTES = 8000            # hard cap on any single tool result (compact JSON, UTF-8)
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
TEXT_CHUNK = 3000           # characters of a long body per page
SUMMARY_CAP = 300           # characters of a touch summary or event evidence

TOUCH_CHANNELS = ("email", "linkedin-message", "linkedin-connect", "linkedin-comment", "call")
OUTBOX_CHANNELS = ("email", "linkedin-message", "linkedin-connect", "linkedin-comment", "linkedin-post",
                   "linkedin-profile-edit")
TOUCH_KINDS = ("intro", "reply", "follow_up", "thank_you", "scheduling", "coffee", "other")
EVENT_SOURCES = ("email", "manual", "calendar", "linkedin")
RISKS = ("low", "medium", "high")
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
CLOSED_PROSPECT = {"closed", "do_not_contact", "dormant"}

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
_EVENT_TYPE_RE = re.compile(r"^[a-z][a-z_]*(?::[a-z_]+)?$")
_EVENT_LINE_RE = re.compile(
    r"^- (?P<at>\S+) · (?P<type>.+?) · (?P<source>.+?) · conf=(?P<conf>[0-9.]+) ·(?: (?P<evidence>.*))?$")
_CREDENTIAL_PATTERNS = [re.compile(p) for p in (
    r"ya29\.[\w-]{10,}",                                   # Google access token
    r"\b1//[\w-]{20,}",                                    # Google refresh token
    r"\bsk-[A-Za-z0-9_-]{16,}",                            # Anthropic / OpenAI style keys
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"\"(?:refresh_token|client_secret|access_token)\"\s*:",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bgh[pousr]_[A-Za-z0-9]{30,}",
    r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    r"\bGOCSPX-[\w-]{10,}",                                # Google OAuth client secret
)]


class ToolError(ValueError):
    """A reason the tool refused, phrased so the calling agent can act on it."""


# ============================================================================ helpers
def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, default=_default)


def _default(v: Any) -> Any:
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, Path):
        return str(v)
    if isinstance(v, (set, tuple)):
        return list(v)
    return str(v)


def size_of(obj: Any) -> int:
    return len(dumps(obj).encode("utf-8"))


def _json_safe(obj: Any) -> Any:
    return json.loads(dumps(obj))


def _as_list(value: Any, field: str = "value") -> list[str] | None:
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    raise ToolError(f"{field} must be a string, a comma-separated string or a list")


def _check_id(value: str, what: str) -> str:
    value = str(value or "").strip()
    if not _ID_RE.match(value) or ".." in value:
        raise ToolError(f"invalid {what} {value!r}: use the record's slug (letters, digits, '-', '_', '.')")
    return value


def _date_arg(value: Any, field: str) -> date | None:
    if value in (None, ""):
        return None
    d = jc.parse_date(value)
    if d is None:
        raise ToolError(f"{field} must be an ISO date (YYYY-MM-DD), got {value!r}")
    return d


def _clamp_limit(limit: Any) -> int:
    try:
        n = int(limit if limit is not None else DEFAULT_LIMIT)
    except (TypeError, ValueError):
        raise ToolError("limit must be an integer") from None
    return max(1, min(MAX_LIMIT, n))


def _cursor(cursor: Any) -> int:
    if cursor in (None, ""):
        return 0
    try:
        n = int(str(cursor))
    except ValueError:
        raise ToolError(f"cursor must be the next_cursor value from a previous page, got {cursor!r}") from None
    return max(0, n)


def page(rows: list[dict[str, Any]], limit: Any = None, cursor: Any = None, **extra: Any) -> dict[str, Any]:
    """One page of rows that always fits MAX_BYTES: shrink the page before exceeding the cap."""
    start, n = _cursor(cursor), _clamp_limit(limit)
    items = [_json_safe(r) for r in rows[start:start + n]]
    out = {"total": len(rows), "items": items, "next_cursor": None, **extra}
    while True:
        end = start + len(out["items"])
        out["next_cursor"] = str(end) if end < len(rows) else None
        if size_of(out) <= MAX_BYTES or len(out["items"]) <= 1:
            break
        out["items"] = out["items"][:-1]
    if size_of(out) > MAX_BYTES and out["items"]:        # a single oversized row: clip its strings
        out["items"] = [_clip_strings(out["items"][0], 400)]
    return out


def _clip_strings(obj: Any, cap: int) -> Any:
    if isinstance(obj, str):
        return obj if len(obj) <= cap else obj[:cap] + "…"
    if isinstance(obj, list):
        return [_clip_strings(v, cap) for v in obj[:20]]
    if isinstance(obj, dict):
        return {k: _clip_strings(v, cap) for k, v in obj.items()}
    return obj


def guard_credentials(*texts: Any) -> None:
    for text in texts:
        if text is None:
            continue
        blob = text if isinstance(text, str) else dumps(text)
        for pat in _CREDENTIAL_PATTERNS:
            if pat.search(blob):
                raise ToolError("refused: the input looks like it contains a credential (token, key or secret). "
                                "Credentials never go into records, drafts or approvals; remove it and retry.")


def untrusted(text: Any, kind: str, limit: int = SUMMARY_CAP) -> str:
    return jc.wrap_untrusted(str(text or ""), kind, limit=limit)


@lru_cache(maxsize=16)
def _schema(kind: str) -> dict[str, Any]:
    path = SCHEMAS / f"{kind}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}


def norm(value: Any) -> str:
    """YAML 1.1 reads bare yes/no as booleans; the schemas mean the words."""
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "" if value is None else str(value)


def enum(kind: str, field: str) -> tuple[str, ...]:
    return tuple(norm(v) for v in (_schema(kind).get("enums") or {}).get(field, ()))


def _enum_view(meta: dict[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    """Row projection with yes/no booleans turned back into their words."""
    return {k: (norm(meta[k]) if isinstance(meta.get(k), bool) else meta.get(k))
            for k in keys if meta.get(k) not in (None, "", [])}


def _check_enum(values: list[str] | None, allowed: Iterable[str], field: str) -> None:
    allowed = tuple(allowed)
    if values and allowed:
        bad = [v for v in values if v not in allowed]
        if bad:
            raise ToolError(f"{field} {bad} not allowed; use one of {list(allowed)}")


@lru_cache(maxsize=8)
def _yaml_file(path: str, mtime: float) -> Any:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _load_yaml(path: Path) -> Any:
    if not path.is_file():
        raise ToolError(f"{path.name} is missing; it could not be read (missing is not empty)")
    return _yaml_file(str(path), path.stat().st_mtime)


def _is_internal(node: Any) -> bool:
    return isinstance(node, dict) and str(node.get("discretion") or "") == "internal-only"


def _strip_internal(node: Any) -> tuple[Any, int]:
    hidden = 0
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if _is_internal(v):
                hidden += 1
                continue
            out[k], h = _strip_internal(v)
            hidden += h
        return out, hidden
    if isinstance(node, list):
        out_l = []
        for v in node:
            if _is_internal(v):
                hidden += 1
                continue
            item, h = _strip_internal(v)
            out_l.append(item)
            hidden += h
        return out_l, hidden
    return node, 0


def _max_date(*values: Any) -> date | None:
    dates = [d for d in (jc.parse_date(v) for v in values) if d]
    return max(dates) if dates else None


# ============================================================================ the store
@dataclass
class Store:
    js: Path
    os_root: Path
    today_override: date | None = None

    @classmethod
    def from_env(cls) -> "Store":
        js = Path(os.environ.get("JOB_SEARCH_ROOT") or DEFAULT_JS_ROOT).expanduser().resolve()
        os_root = Path(os.environ.get("JOB_AGENCY_RUNTIME") or DEFAULT_OS_ROOT).expanduser().resolve()
        override = jc.parse_date(os.environ.get("JOB_SEARCH_TODAY")) if os.environ.get("JOB_SEARCH_TODAY") else None
        return cls(js=js, os_root=os_root, today_override=override)

    # ------------------------------------------------------------------ basics
    def today(self) -> date:
        return self.today_override or datetime.now(cu.DEPT_TZ).date()

    def now_iso(self) -> str:
        if self.today_override:
            return f"{self.today_override.isoformat()}T12:00:00"
        return datetime.now(cu.DEPT_TZ).replace(microsecond=0, tzinfo=None).isoformat()

    @property
    def roots(self) -> cu.Roots:
        return cu.Roots(js=self.js, os=self.os_root)

    def rel(self, path: Path) -> str:
        for base in (self.js, self.os_root):
            try:
                return str(Path(path).relative_to(base))
            except ValueError:
                continue
        return str(path)

    def _records(self, folder: str) -> list[tuple[Path, dict[str, Any], str]]:
        return list(jc.iter_records(self.js / folder))

    # ------------------------------------------------------------------ jobs
    JOB_ROW = ("id", "company", "title", "status", "location", "remote", "sponsorship", "fit_score",
               "fit_verdict", "lens", "posted_at", "last_seen", "source", "source_url")

    def jobs_search(self, query: str | None = None, status: Any = None, company: str | None = None,
                    source: Any = None, sponsorship: Any = None, remote: Any = None, lens: Any = None,
                    fit_verdict: Any = None, min_fit: float | None = None, posted_since: str | None = None,
                    sort: str = "recent", limit: Any = None, cursor: Any = None) -> dict[str, Any]:
        filters = {"status": _as_list(status, "status"), "source": _as_list(source, "source"),
                   "sponsorship": _as_list(sponsorship, "sponsorship"), "remote": _as_list(remote, "remote"),
                   "lens": _as_list(lens, "lens"), "fit_verdict": _as_list(fit_verdict, "fit_verdict")}
        for field, values in filters.items():
            _check_enum(values, enum("job", field), field)
        since = _date_arg(posted_since, "posted_since")
        q = (query or "").strip().lower()
        comp = jc.normalize_company(company) if company else ""
        rows = []
        for path, meta, _body in self._records("jobs"):
            meta.setdefault("id", path.stem)
            if any(vals and norm(meta.get(f)) not in vals for f, vals in filters.items()):
                continue
            if q and q not in " ".join(str(meta.get(k) or "") for k in ("id", "title", "company", "location",
                                                                          "tags")).lower():
                continue
            if comp and comp not in jc.normalize_company(meta.get("company") or "") \
                    and comp not in str(meta.get("company_slug") or ""):
                continue
            if min_fit is not None and not (isinstance(meta.get("fit_score"), (int, float))
                                            and meta["fit_score"] >= float(min_fit)):
                continue
            posted = jc.parse_date(meta.get("posted_at")) or jc.parse_date(meta.get("first_seen"))
            if since and (posted is None or posted < since):
                continue
            rows.append(_enum_view(meta, self.JOB_ROW))
        if sort == "fit":
            rows.sort(key=lambda r: (-(r.get("fit_score") or 0), r["id"]))
        elif sort == "recent":
            rows.sort(key=lambda r: (str(r.get("posted_at") or r.get("last_seen") or ""), r["id"]), reverse=True)
        else:
            raise ToolError("sort must be 'recent' or 'fit'")
        return page(rows, limit, cursor)

    def job_get(self, job_id: str, offset: int = 0) -> dict[str, Any]:
        job_id = _check_id(job_id, "job_id")
        path = self.js / "jobs" / f"{job_id}.md"
        if not path.is_file():
            raise ToolError(f"job {job_id!r} not found in jobs/; search with jobs_search")
        meta, body = jc.read_record(path)
        return self._text_page({"id": job_id, "meta": _json_safe(meta)}, body, "jd", offset)

    def _text_page(self, head: dict[str, Any], text: str, kind: str, offset: int) -> dict[str, Any]:
        offset = max(0, int(offset or 0))
        chunk = TEXT_CHUNK
        while True:
            piece = text[offset:offset + chunk]
            end = offset + len(piece)
            out = {**head, kind: untrusted(piece, kind, limit=chunk), "offset": offset, "chars": len(text),
                   "next_offset": end if end < len(text) else None}
            if size_of(out) <= MAX_BYTES or chunk <= 500:
                return out
            chunk -= 500

    # ------------------------------------------------------------------ portals
    PORTAL_ROW = ("id", "name", "control", "status", "agent_terms", "setup_priority", "account", "alerts", "last_alert")

    def _registry(self) -> dict[str, Any]:
        import portal_registry
        return portal_registry.load(self.js)

    def _last_alerts(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for row in jc.read_jsonl(self.js / "state" / "job-alerts.jsonl"):
            p, at = row.get("platform"), str(row.get("at") or "")
            if p and at > out.get(p, ""):
                out[p] = at
        return out

    def _portal_row(self, e: dict[str, Any], last: dict[str, str]) -> dict[str, Any]:
        pres = e.get("presence") or {}
        return {"id": e.get("id"), "name": e.get("name"), "control": e.get("control"), "status": e.get("status"),
                "agent_terms": e.get("agent_terms"), "setup_priority": e.get("setup_priority"),
                "account": (pres.get("account") or {}).get("state") or "unknown",
                "alerts": (pres.get("alerts") or {}).get("state") or ("on" if e.get("alerts_on") else "unknown"),
                "last_alert": last.get(str(e.get("id")))}

    def portals_list(self, control: Any = None, status: Any = None, limit: Any = None,
                     cursor: Any = None) -> dict[str, Any]:
        controls, statuses = _as_list(control, "control"), _as_list(status, "status")
        last = self._last_alerts()
        rows = [self._portal_row(e, last) for e in self._registry().get("platforms") or []]
        rows = [r for r in rows if (not controls or r["control"] in controls) and (not statuses or r["status"] in statuses)]
        return page(rows, limit, cursor, source="targets/platforms.yaml")

    def _job_portals(self, meta: dict[str, Any]) -> set[str]:
        import dedup
        return {dedup.portal_of(meta)} | {str(x.get("portal")) for x in meta.get("sightings") or [] if isinstance(x, dict)}

    def portal_get(self, portal_id: str, limit: Any = None, cursor: Any = None) -> dict[str, Any]:
        pid = _check_id(portal_id, "portal_id")
        entry = next((e for e in self._registry().get("platforms") or [] if str(e.get("id")) == pid), None)
        if entry is None:
            raise ToolError(f"no portal {pid!r} in targets/platforms.yaml; portals_list() gives the ids")
        jobs = []
        for _, meta, _ in self._records("jobs"):
            if pid in self._job_portals(meta):
                jobs.append({k: meta.get(k) for k in ("id", "company", "title", "status", "first_seen", "source_url")})
        jobs.sort(key=lambda j: (str(j.get("first_seen") or ""), str(j.get("id"))), reverse=True)
        follow: list[dict[str, Any]] = []
        if pid == "linkedin":
            import prospects
            follow = [{k: r.get(k) for k in ("id", "name", "company", "due_date", "nudge", "channel")}
                      for r in prospects.due(self.roots, self.today())["due"] if str(r.get("channel") or "").startswith("linkedin")]
        pres = entry.get("presence") or {}
        return {"portal": self._portal_row(entry, self._last_alerts()),
                "setup": str(entry.get("alert_setup") or "")[:2000],
                "terms": {k: (entry.get("tos") or {}).get(k) for k in ("quote", "url", "read_on")},
                "presence": {k: pres.get(k) for k in ("next_refresh", "next_step", "last_updated")},
                "jobs": page(jobs, limit, cursor), "follow_ups_due": follow[:20]}

    def job_sightings(self, job_id: str) -> dict[str, Any]:
        jid = _check_id(job_id, "job_id")
        metas = {str(m.get("id")): m for _, m, _ in self._records("jobs")}
        if jid not in metas:
            raise ToolError(f"no job {jid!r} in jobs/; jobs_search() finds ids")
        dup = metas[jid].get("duplicate_of")
        canon = metas.get(str(dup), metas[jid]) if dup else metas[jid]
        sightings = [x for x in canon.get("sightings") or [] if isinstance(x, dict)]
        if not sightings:
            import dedup
            sightings = [{"portal": dedup.portal_of(canon), "url": canon.get("source_url"),
                          "first_seen": canon.get("first_seen")}]
        similar = [{"id": i, "title": metas[i].get("title"), "status": metas[i].get("status"),
                    "first_seen": metas[i].get("first_seen")} for i in canon.get("similar") or [] if i in metas]
        return _json_safe({"job": jid, "canonical": canon.get("id"), "duplicate_of": dup, "sightings": sightings,
                           "similar": similar})

    # ------------------------------------------------------------------ reader model
    def reader_model_get(self, slug: str) -> dict[str, Any]:
        """research/targets/<slug>.md section 6, parsed with scripts/reader_model.py's own rules."""
        import reader_model as rm
        sl = _check_id(slug, "slug")
        path = self.js / "research" / "targets" / f"{sl}.md"
        if not path.is_file():
            raise ToolError(f"no package research/targets/{sl}.md; the role-package skill writes it")
        text = path.read_text(encoding="utf-8")
        secs = rm.sections(text)
        body = secs.get("6", "")

        def line_value(label: str) -> str:
            m = re.search(rf"\*\*{label}:?\*\*:?\s*(.+)", body, re.I)
            return m.group(1).strip() if m else ""

        wants = [m for m in (rm.WANT.match(l) for l in rm._block(body, "Wants").splitlines()) if m]
        answers = [m for m in (rm.WANT.match(l) for l in rm._block(body, "Answer table").splitlines()) if m]
        vocab = []
        for l in rm._block(body, "Their words").splitlines():
            cells = [c.strip() for c in l.strip().lstrip("-* ").split("|")]
            if len(cells) >= 3 and cells[0]:
                vocab.append({"their": cells[0], "ours": cells[1], "ev": cells[2]})
        answered = {m.group(1) for m in answers}
        questions = secs.get("8", "")
        gaps = [{"id": m.group(1), "asked": bool(re.search(rf"\b{m.group(1)}\b", questions))}
                for m in wants if m.group(1) not in answered]
        check = rm.check(path)
        return _json_safe({
            "slug": sl, "path": self.rel(path), "reader": line_value("Reader"),
            "wants": [{"id": m.group(1), "words": untrusted(m.group(2), "reader_words", 300),
                       "source": m.group(3)} for m in wants],
            "fears": line_value("Fears"), "proof": line_value("Proof they trust"), "their_words": vocab[:8],
            "answers": [{"id": m.group(1), "line": m.group(2)[:300],
                         "ev": (rm.EV.search(m.group(3)) or rm.EV.search(m.group(2)) or [None])[0]} for m in answers],
            "gaps": gaps,
            "questions": [l.strip("-* ").strip() for l in questions.splitlines() if l.strip().startswith(("-", "*"))][:5],
            "check": {"errors": check.get("errors", []), "warnings": check.get("warnings", [])}})

    # ------------------------------------------------------------------ focus profiles
    def focus_profile_get(self, query: str) -> dict[str, Any]:
        """A focus profile (profile/lenses/<family>.md) by family id or by job title (scripts/focus_profiles.py pick)."""
        import focus_profiles as fp
        q = str(query or "").strip()
        if not q:
            raise ToolError("query is a profile family (e.g. evaluation) or a job title")
        lenses = {l["family"]: l for l in fp.lenses(self.js)}
        others: list[dict[str, Any]] = []
        if q.lower() in lenses:
            lens, matched = lenses[q.lower()], "family"
        else:
            ranked = fp.pick(self.js, q)
            if not ranked:
                raise ToolError(f"no focus profile matches {q!r}; families: {', '.join(sorted(lenses))}")
            lens, matched = lenses[ranked[0]["family"]], f"title ({ranked[0]['why']}, score {ranked[0]['score']})"
            others = [{"family": r["family"], "score": r["score"]} for r in ranked[1:4]]
        s7 = re.sub(r"^## 7\.[^\n]*\n", "", lens["section7"]).strip()
        out = {"family": lens["family"], "path": lens["path"], "status": lens["status"], "matched_by": matched,
               "titles": lens["titles"], "best_foot_written": bool(s7), "best_foot": s7[:4000], "other_profiles": others}
        if not s7:
            out["note"] = "section 7 (best foot) is not written yet for this profile; sections 1 to 6 read the focus"
        return _json_safe(out)

    NETWORK_ROW = ("name", "company", "tier", "kind", "title", "school", "warmth", "last_message", "connected",
                   "hiring_post", "next_up", "prospect", "linkedin_url", "x_handle", "why")

    def network_for(self, query: str, limit: int = 50) -> dict[str, Any]:
        """: the people in the network at a company, or of one role kind, classified by scripts/network_map.py
        (built live from the parsed LinkedIn export, prospects, the Scout's hiring posters and X following)."""
        import network_map as nm
        q = str(query or "").strip()
        if not q:
            raise ToolError(f"query is a company name or a role kind ({', '.join(nm.KINDS)})")
        net = nm.build(self.js, self.today())
        rows = nm.for_query(net, q)
        n = max(1, min(int(limit or 50), 100))
        out = {"query": q, "matched_by": "role kind" if q.lower() in nm.KINDS else "company", "count": len(rows),
               "people": [{k: r.get(k) for k in self.NETWORK_ROW} for r in rows[:n]],
               "sources": {"linkedin": net["linkedin"]["note"], "x": net["x"]["note"]}}
        if not rows:
            out["note"] = f"nobody in the network matches {q!r}; network_plan and people_traces.py find new people"
        return _json_safe(out)

    # ------------------------------------------------------------------ prospects
    PROSPECT_ROW = ("id", "name", "company", "role", "status", "priority", "warmth", "fit_score", "sponsorship_hint",
                    "source", "label", "next_touch_at", "tags")

    def prospects_list(self, status: Any = None, priority: Any = None, company: str | None = None,
                       source: Any = None, tags: Any = None, role: Any = None, company_type: Any = None,
                       sponsorship: Any = None, last_touch_from: str | None = None,
                       last_touch_to: str | None = None, min_warmth: int | None = None,
                       due_by: str | None = None, query: str | None = None, label: Any = None,
                       channel: Any = None, sort: str = "priority",
                       limit: Any = None, cursor: Any = None) -> dict[str, Any]:
        sources = _as_list(source, "source")
        _check_enum(sources, enum("prospect", "source"), "source")
        labels, channels = _as_list(label, "label"), _as_list(channel, "channel")
        filters = {"status": _as_list(status, "status"), "priority": _as_list(priority, "priority"),
                   "role": _as_list(role, "role"),
                   "company_type": _as_list(company_type, "company_type"),
                   "sponsorship_hint": _as_list(sponsorship, "sponsorship")}
        for field, values in filters.items():
            _check_enum(values, enum("prospect", field), field if field != "sponsorship_hint" else "sponsorship")
        want_tags = _as_list(tags, "tags") or []
        lo, hi = _date_arg(last_touch_from, "last_touch_from"), _date_arg(last_touch_to, "last_touch_to")
        due = _date_arg(due_by, "due_by")
        comp = jc.normalize_company(company) if company else ""
        q = (query or "").strip().lower()
        rows = []
        for path, meta, _body in self._records("prospects"):
            meta.setdefault("id", path.stem)
            if any(vals and norm(meta.get(f)) not in vals for f, vals in filters.items()):
                continue
            have = {str(t) for t in (meta.get("tags") or [])}
            if want_tags and not set(want_tags) <= have:
                continue
            if sources and not set(sources) & ({str(meta.get("source"))} | {str(x) for x in meta.get("sources") or []}):
                continue
            if labels and str(meta.get("label") or "") not in labels:
                continue
            if channels and not set(channels) & {str(c) for c in meta.get("channels") or []}:
                continue
            if comp and comp not in jc.normalize_company(meta.get("company") or "") \
                    and comp not in str(meta.get("company_slug") or ""):
                continue
            last = _max_date(meta.get("last_inbound_at"), meta.get("last_outbound_at")) \
                or jc.parse_date(meta.get("first_contact_at"))
            if (lo or hi) and last is None:
                continue
            if lo and last < lo or hi and last > hi:
                continue
            if min_warmth is not None and int(meta.get("warmth") or 0) < int(min_warmth):
                continue
            if due:
                nxt = jc.parse_date(meta.get("next_touch_at"))
                if nxt is None or nxt > due or meta.get("status") in CLOSED_PROSPECT:
                    continue
            if q and q not in " ".join(str(meta.get(k) or "") for k in ("id", "name", "company", "headline",
                                                                          "role_hint")).lower():
                continue
            row = _enum_view(meta, self.PROSPECT_ROW)
            row["last_touch"] = last.isoformat() if last else None
            rows.append(row)
        if sort == "priority":
            rows.sort(key=lambda r: (PRIORITY_ORDER.get(str(r.get("priority")), 9), -int(r.get("warmth") or 0),
                                     "".join(chr(0x10FFFF - ord(c)) for c in (r.get("last_touch") or "")), r["id"]))
        elif sort == "last_touch":
            rows.sort(key=lambda r: (r.get("last_touch") or "", r["id"]), reverse=True)
        elif sort == "name":
            rows.sort(key=lambda r: (str(r.get("name") or "").lower(), r["id"]))
        else:
            raise ToolError("sort must be 'priority', 'last_touch' or 'name'")
        return page(rows, limit, cursor, via=self._via("prospects.py", "list"))

    def prospect_get(self, prospect_id: str, timeline_limit: int = 20, touches_limit: int = 10) -> dict[str, Any]:
        pid = _check_id(prospect_id, "prospect_id")
        path = self.js / "prospects" / f"{pid}.md"
        if not path.is_file():
            raise ToolError(f"prospect {pid!r} not found in prospects/; list with prospects_list")
        meta, body = jc.read_record(path)
        lines = [ln for ln in (cu.section_text(body, "Timeline") or "").splitlines() if ln.strip()]
        n_lines = max(0, int(timeline_limit or 0))
        touches = [t for t in jc.iter_touches(self.js) if t.get("prospect") == pid]
        touches.sort(key=lambda t: str(t.get("at") or ""), reverse=True)
        n_touch = max(0, int(touches_limit or 0))
        while True:
            shown = lines[-n_lines:] if n_lines else []
            out = {"id": pid, "meta": _json_safe(meta),
                   "timeline": untrusted("\n".join(shown), "timeline", limit=TEXT_CHUNK),
                   "timeline_lines": len(lines), "timeline_shown": len(shown),
                   "touches": [self._touch_row(t) for t in touches[:n_touch]], "touches_total": len(touches)}
            if size_of(out) <= MAX_BYTES or (n_lines <= 1 and n_touch <= 1):
                return out
            if n_touch > 1:
                n_touch -= 1
            else:
                n_lines -= 1

    # ------------------------------------------------------------------ applications
    APP_ROW = ("company", "title", "stage", "applied_at", "applied_via", "next_action", "next_action_at",
               "last_inbound_at", "updated")

    def _applications(self) -> list[tuple[str, dict[str, Any], str]]:
        base = self.js / "applications"
        out = []
        for folder in sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []:
            f = folder / "application.md"
            if f.is_file():
                meta, body = jc.read_record(f)
                out.append((folder.name, meta, body))
        return out

    def applications_list(self, stage: Any = None, company: str | None = None, applied_via: Any = None,
                          updated_since: str | None = None, next_action_due: str | None = None,
                          limit: Any = None, cursor: Any = None) -> dict[str, Any]:
        stages, via = _as_list(stage, "stage"), _as_list(applied_via, "applied_via")
        _check_enum(stages, enum("application", "stage"), "stage")
        _check_enum(via, enum("application", "applied_via"), "applied_via")
        since, due = _date_arg(updated_since, "updated_since"), _date_arg(next_action_due, "next_action_due")
        comp = jc.normalize_company(company) if company else ""
        rows = []
        for slug, meta, _ in self._applications():
            if stages and str(meta.get("stage")) not in stages or via and str(meta.get("applied_via")) not in via:
                continue
            if comp and comp not in jc.normalize_company(meta.get("company") or "") \
                    and comp not in str(meta.get("company_slug") or ""):
                continue
            upd = jc.parse_date(meta.get("updated"))
            if since and (upd is None or upd < since):
                continue
            nxt = jc.parse_date(meta.get("next_action_at"))
            if due and (nxt is None or nxt > due):
                continue
            rows.append({"id": slug, **{k: meta.get(k) for k in self.APP_ROW if meta.get(k) is not None}})
        rows.sort(key=lambda r: (str(r.get("updated") or ""), r["id"]), reverse=True)
        return page(rows, limit, cursor)

    def application_get(self, slug: str, events_limit: int = 30) -> dict[str, Any]:
        slug = _check_id(slug, "application slug")
        folder = self.js / "applications" / slug
        if not (folder / "application.md").is_file():
            raise ToolError(f"application {slug!r} not found in applications/; list with applications_list")
        meta, body = jc.read_record(folder / "application.md")
        events = self._parse_events(body)
        fit = {}
        if (folder / "fit.md").is_file():
            fmeta, _ = jc.read_record(folder / "fit.md")
            fit = {k: fmeta[k] for k in ("score", "fit_score", "verdict", "fit_verdict", "lens") if k in fmeta}
        files = {p.name: p.stat().st_size for p in sorted(folder.iterdir()) if p.is_file()}
        n = max(1, int(events_limit or 1))
        while True:
            out = {"id": slug, "meta": _json_safe(meta), "events": events[-n:], "events_total": len(events),
                   "fit": _json_safe(fit), "files": files}
            if size_of(out) <= MAX_BYTES or n <= 1:
                return out
            n -= 1

    def application_hub(self, slug: str, offset: int = 0) -> dict[str, Any]:
        """The per-role record hub, joined live by scripts/role_hub.py (the same text HUB.md holds when fresh)."""
        import role_hub
        slug = _check_id(slug, "application slug")
        path = self.js / "applications" / slug / "application.md"
        if not path.is_file():
            raise ToolError(f"application {slug!r} not found in applications/; list with applications_list")
        meta, body = jc.read_record(path)
        text = role_hub.render_hub(role_hub.Records.load(self.js), slug, meta, body)
        hub = path.parent / role_hub.HUB
        on_disk = "fresh" if hub.is_file() and hub.read_text(encoding="utf-8") == text else \
            ("stale" if hub.is_file() else "absent")
        return self._text_page({"id": slug, "path": self.rel(hub), "hub_on_disk": on_disk,
                                "via": "scripts/role_hub.py"}, text, "hub", offset)

    # ------------------------------------------------------------------ pursuits (REQ-9)
    PURSUIT_ROW = ("id", "title", "company_slug", "kind", "stage", "waiting_on", "next_action", "next_action_at",
                   "referrer", "updated")

    def pursuits_list(self, stage: Any = None, waiting_on: str | None = None, open_only: bool = False,
                      limit: Any = None, cursor: Any = None) -> dict[str, Any]:
        import pursuits
        stages = _as_list(stage, "stage")
        _check_enum(stages, pursuits.STAGES, "stage")
        rows = [r for r in pursuits.rows(self.js)
                if (not stages or r["stage"] in stages) and (not waiting_on or r["waiting_on"] == waiting_on)
                and not (open_only and r["stage"] in pursuits.CLOSED)]
        rows = [{k: r[k] for k in self.PURSUIT_ROW if r.get(k) is not None} for r in rows]
        rows.sort(key=lambda r: (str(r.get("next_action_at") or "9999"), r["id"]))
        return page(rows, limit, cursor)

    def pursuit_get(self, slug: str, offset: int = 0) -> dict[str, Any]:
        """The pursuit record, its last decisions, and its hub joined live by scripts/pursuit_hub.py."""
        import pursuit_hub
        import pursuits
        import role_hub
        slug = _check_id(slug, "pursuit slug")
        path = self.js / "pursuits" / slug / "pursuit.md"
        if not path.is_file():
            raise ToolError(f"pursuit {slug!r} not found in pursuits/; list with pursuits_list")
        meta, body = jc.read_record(path)
        others = {s: m for s, m, _ in pursuits.iter_pursuits(self.js)}
        text = pursuit_hub.render(role_hub.Records.load(self.js), slug, meta, body, os_root=self.os_root,
                                  others=others)
        hub = path.parent / pursuit_hub.HUB
        on_disk = "fresh" if hub.is_file() and hub.read_text(encoding="utf-8") == text else \
            ("stale" if hub.is_file() else "absent")
        decisions = [f"{d} · {t}" for d, t in pursuit_hub._section_lines(body, "Decisions")][-10:]
        meta = {k: v for k, v in meta.items() if k != "links"}
        return self._text_page({"id": slug, "path": self.rel(hub), "meta": _json_safe(meta), "decisions": decisions,
                                "hub_on_disk": on_disk, "via": "scripts/pursuit_hub.py"}, text, "hub", offset)

    def _parse_events(self, body: str) -> list[dict[str, Any]]:
        events = []
        for line in (cu.section_text(body, "Events") or "").splitlines():
            m = _EVENT_LINE_RE.match(line.strip())
            if m:
                events.append({"at": m["at"], "type": m["type"], "source": m["source"], "conf": float(m["conf"]),
                               "evidence": untrusted(m["evidence"] or "", "evidence")})
        return events

    # ------------------------------------------------------------------ touches
    def _touch_row(self, t: dict[str, Any]) -> dict[str, Any]:
        row = {k: t.get(k) for k in ("at", "prospect", "application", "channel", "direction", "kind", "status",
                                     "thread_id", "approval_id", "draft_path") if t.get(k) not in (None, "")}
        row["summary"] = untrusted(t.get("summary") or "", "message")
        return row

    def touches_recent(self, prospect: str | None = None, application: str | None = None, channel: Any = None,
                       direction: str | None = None, kind: Any = None, since: str | None = None,
                       until: str | None = None, limit: Any = None, cursor: Any = None) -> dict[str, Any]:
        channels, kinds = _as_list(channel, "channel"), _as_list(kind, "kind")
        _check_enum(channels, TOUCH_CHANNELS, "channel")
        _check_enum(kinds, TOUCH_KINDS, "kind")
        if direction not in (None, "", "in", "out"):
            raise ToolError("direction must be 'in' or 'out'")
        lo = _date_arg(since, "since") or (self.today() - timedelta(days=14))
        hi = _date_arg(until, "until")
        rows = []
        for t in jc.iter_touches(self.js):
            at = jc.parse_date(t.get("at"))
            if at is None or at < lo or hi and at > hi:
                continue
            if prospect and t.get("prospect") != prospect or application and t.get("application") != application:
                continue
            if channels and t.get("channel") not in channels or kinds and t.get("kind") not in kinds:
                continue
            if direction and t.get("direction") != direction:
                continue
            rows.append(t)
        rows.sort(key=lambda t: str(t.get("at") or ""), reverse=True)
        return page([self._touch_row(t) for t in rows], limit, cursor, since=lo.isoformat())

    # ------------------------------------------------------------------ profile
    def _facts(self) -> dict[str, Any]:
        data = _load_yaml(self.js / "profile" / "facts.yaml")
        return data if isinstance(data, dict) else {}

    def profile_facts(self, path: str | None = None, include_internal: bool = False, limit: Any = None,
                      cursor: Any = None) -> dict[str, Any]:
        facts = self._facts()
        if not path:
            sections = []
            for key, value in facts.items():
                shown = value if include_internal else _strip_internal(value)[0]
                sec = {"key": key, "type": type(value).__name__, "bytes": size_of(shown)}
                if isinstance(value, dict):
                    sec["keys"] = list(value)[:25]
                elif isinstance(value, list):
                    sec["items"] = len(value)
                else:
                    sec["value"] = _clip_strings(value, 200)
                sections.append(sec)
            return _json_safe({"sections": sections, "hint": "call again with path='<section>' or a dotted path "
                                                  "like 'identity.links.github'; list items by index ('experience.0')"})
        node: Any = facts
        parent: Any = None
        internal_ancestor = False
        for part in str(path).split("."):
            parent = node
            internal_ancestor = internal_ancestor or _is_internal(node)
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            else:
                raise ToolError(f"no facts at {path!r} (stopped at {part!r}); call profile_facts() for sections")
        if not include_internal and (_is_internal(node) or internal_ancestor):
            raise ToolError(f"{path!r} is internal-only (discretion rule); pass include_internal=true only for "
                            "private files, never for anything that leaves the workspace")
        value, hidden = (node, 0) if include_internal else _strip_internal(node)
        # `title` is the record (held variants, conflict notes); serve the render form instead.
        held = pt.load_held(self.js / "profile" / "PROFILE-HOLDS.md")
        if str(path).split(".")[-1] == "title" and isinstance(parent, dict):
            view = pt.render_view(parent, held)
            value = view["title"]
            extra = {k: view[k] for k in ("title_record", "title_flag") if k in view}
        else:
            value, extra = pt.render_view(value, held), {}
        out = {"path": path, "value": _json_safe(value), **extra, "hidden_internal": hidden}
        if size_of(out) <= MAX_BYTES:
            return out
        if isinstance(value, list):
            rows = [{"index": i, "value": v} for i, v in enumerate(value)]
            return page(rows, limit, cursor, path=path, hidden_internal=hidden)
        if isinstance(value, dict):
            keys = list(value)
            return {"path": path, "too_large": True, "keys": keys[:200], "hidden_internal": hidden,
                    "hint": f"narrow the path, e.g. '{path}.{keys[0]}'" if keys else "empty"}
        return {"path": path, "value": _clip_strings(value, 4000), "hidden_internal": hidden, "clipped": True}

    def evidence_lookup(self, ids: Any = None, query: str | None = None, limit: Any = None,
                        cursor: Any = None) -> dict[str, Any]:
        data = _load_yaml(self.js / "profile" / "evidence.yaml") or {}
        ev = data.get("evidence") or {}
        want = _as_list(ids, "ids")
        if not want and not query:
            raise ToolError("pass ids (e.g. ['ev-dfai-001']) or a query to search sources and notes")
        rows, missing = [], []
        if want:
            for eid in want:
                if eid in ev:
                    rows.append({"id": eid, **(ev[eid] or {})})
                else:
                    missing.append(eid)
        else:
            q = query.lower()
            for eid, entry in ev.items():
                entry = entry or {}
                if q in " ".join([eid] + [str(entry.get(k) or "") for k in ("source", "locator", "note")]).lower():
                    rows.append({"id": eid, **entry})
        return page(rows, limit, cursor, missing=missing)

    # ------------------------------------------------------------------ resumes
    def resume_variants_list(self, kind: str = "all", lens: Any = None, editorial_verdict: Any = None,
                             job: str | None = None, limit: Any = None, cursor: Any = None) -> dict[str, Any]:
        kinds = {"all": ("bases", "variants"), "bases": ("bases",), "base": ("bases",),
                 "variants": ("variants",), "variant": ("variants",)}
        if kind not in kinds:
            raise ToolError("kind must be 'all', 'bases' or 'variants'")
        lenses, verdicts = _as_list(lens, "lens"), _as_list(editorial_verdict, "editorial_verdict")
        rows = []
        for group in kinds[kind]:
            base = self.js / "resumes" / group
            for folder in sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []:
                meta_path = folder / "meta.yaml"
                meta = {}
                if meta_path.is_file():
                    loaded = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
                    meta = loaded if isinstance(loaded, dict) else {}
                review = {}
                if (folder / "review.md").is_file():
                    review, _ = jc.read_record(folder / "review.md")
                row = {"kind": group.rstrip("s"), "id": folder.name,
                       **{k: meta.get(k) for k in ("lens", "base_of", "job", "built_at", "pages", "editorial_verdict",
                                                   "hash") if meta.get(k) is not None},
                       "review_verdict": review.get("verdict"),
                       "has_pdf": any(folder.glob("*.pdf")), "ats_report": (folder / "ats-report.md").is_file(),
                       "meta_missing": not meta_path.is_file()}
                if lenses and str(row.get("lens")) not in lenses or verdicts and \
                        str(row.get("editorial_verdict")) not in verdicts or job and row.get("job") != job:
                    continue
                rows.append(row)
        return page(rows, limit, cursor)

    # ------------------------------------------------------------------ dashboard
    def _via(self, script: str, action: str) -> str:
        return (f"records (scripts/{script} is present; its {action} is the canonical view)"
                if (SCRIPTS / script).is_file() else f"records (scripts/{script} not present yet)")

    def _run_script(self, script: str, args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
        path = SCRIPTS / script
        if not path.is_file():
            raise ToolError(f"scripts/{script} is missing; this step cannot run until it exists")
        return subprocess.run([sys.executable, str(path), *args], capture_output=True, text=True, timeout=timeout,
                              cwd=str(self.js), stdin=subprocess.DEVNULL)

    def dashboard_metrics(self) -> dict[str, Any]:
        today = self.today()
        sources: dict[str, str] = {}
        try:
            proc = self._run_script("tracker.py", ["--root", str(self.js), "metrics", "--json"])
            if proc.returncode == 0:
                apps: Any = json.loads(proc.stdout or "{}")
                sources["applications"] = "scripts/tracker.py metrics --json"
            else:
                apps = {"error": (proc.stderr or proc.stdout).strip().splitlines()[-3:]}
                sources["applications"] = "scripts/tracker.py metrics failed"
        except (ToolError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
            apps = {"unavailable": str(exc)[:200]}
            sources["applications"] = "scripts/tracker.py unavailable"

        by_status: dict[str, int] = {}
        by_priority: dict[str, int] = {}
        due = 0
        prospects = self._records("prospects")
        for _p, meta, _b in prospects:
            by_status[str(meta.get("status"))] = by_status.get(str(meta.get("status")), 0) + 1
            by_priority[str(meta.get("priority"))] = by_priority.get(str(meta.get("priority")), 0) + 1
            nxt = jc.parse_date(meta.get("next_touch_at"))
            if nxt and nxt <= today and meta.get("status") not in CLOSED_PROSPECT:
                due += 1
        sources["prospects"] = self._via("prospects.py", "stats")

        jobs: dict[str, int] = {}
        for _p, meta, _b in self._records("jobs"):
            jobs[str(meta.get("status"))] = jobs.get(str(meta.get("status")), 0) + 1
        outbox: dict[str, int] = {}
        for _p, meta, _b in self._records("outbox"):
            outbox[str(meta.get("status"))] = outbox.get(str(meta.get("status")), 0) + 1
        pending = 0
        for _p, meta, _b in jc.iter_records(self.os_root / "work" / "approvals"):
            if meta.get("status") == "pending" and meta.get("project") in (None, "job-search"):
                pending += 1
        connectors: Any = "not checked yet: run python3 scripts/connectors_doctor.py"
        cpath = self.js / "state" / "connectors.json"
        if cpath.is_file():
            try:
                rep = json.loads(cpath.read_text(encoding="utf-8"))
                connectors = {"overall": rep.get("overall"), "generated_at": rep.get("generated_at"),
                              "states": {c.get("name"): c.get("state") for c in rep.get("connectors") or []}}
            except json.JSONDecodeError:
                connectors = "state/connectors.json unreadable: rerun scripts/connectors_doctor.py"
        dash, sources["dashboard"] = self._dashboard_build()
        out = {"today": today.isoformat(), "applications": apps, "dashboard": dash,
               "prospects": {"total": len(prospects), "by_status": by_status, "by_priority": by_priority,
                             "follow_ups_due": due},
               "jobs": {"by_status": jobs}, "outbox": {"by_status": outbox}, "approvals_pending": pending,
               "connectors": connectors, "sources": sources}
        if size_of(out) > MAX_BYTES and isinstance(apps, dict):   # keep the headline numbers, drop the detail
            out["applications"] = {k: apps[k] for k in ("applications", "funnel", "outcomes") if k in apps}
            out["applications"]["detail"] = "trimmed to fit; run scripts/tracker.py metrics for the full report"
        if size_of(out) > MAX_BYTES and isinstance(dash, dict):
            out["dashboard"] = {k: (v.get("count") if isinstance(v, dict) and "count" in v else v)
                                for k, v in dash.items() if k != "visa_timers"}
        if size_of(out) > MAX_BYTES:
            out = _clip_strings(out, 120)
        return _json_safe(out)

    def _dashboard_build(self) -> tuple[Any, str]:
        """The canonical dashboard numbers from scripts/dashboard.py build(), compacted (it writes nothing)."""
        if not (SCRIPTS / "dashboard.py").is_file():
            return None, "scripts/dashboard.py not present yet; the counts here come from records"
        try:
            import importlib
            dash = importlib.import_module("dashboard")
            now = (datetime(self.today_override.year, self.today_override.month, self.today_override.day, 19,
                            tzinfo=timezone.utc) if self.today_override else datetime.now(timezone.utc))
            data = dash.build(self.js, self.os_root, now)
        except Exception as exc:  # noqa: BLE001 - another lane owns dashboard.py; degrade, do not crash
            return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}, "scripts/dashboard.py build() failed"

        def head(v: Any, n: int = 3) -> Any:
            if isinstance(v, list):
                return {"count": len(v), "first": _clip_strings(v[:n], 120)}
            return v                                   # None = source missing ("no data yet")

        needs = data.get("needs_owner") or {}
        compact = {
            "needs_owner": {k: head(v) for k, v in needs.items()},
            # the roles' own work (fit-scorer's gate reviews), never presented as the owner's
            "agency_backlog": {k: head(v) for k, v in (data.get("agency_backlog") or {}).items()},
            "fresh_fits": head(data.get("fresh_fits")),
            "scoring_queue": data.get("scoring_queue"),
            "interviews_next_7_days": head(data.get("interviews_next_7_days")),
            "pipeline": data.get("pipeline"),
            "visa_timers": _clip_strings(data.get("visa_timers"), 160),
            "discovery": _clip_strings((data.get("metrics") or {}).get("discovery"), 120),
        }
        return _json_safe(compact), "scripts/dashboard.py build() (read-only; DASHBOARD.md is its rendered view)"

    # ------------------------------------------------------------------ writes: prospects
    PROSPECT_MANAGED = ("id", "created", "updated", "type", "links")   # type/links: graph edges, code-written only
    PROSPECT_LISTS = ("emails", "tags", "thread_ids", "linkedin_threads", "applications")
    PROSPECT_INTS = ("inbound_count", "outbound_count", "follow_ups_sent", "max_follow_ups")

    def _validate_prospect_fields(self, fields: dict[str, Any]) -> dict[str, Any]:
        schema = _schema("prospect")
        allowed = {str(f) for f in schema.get("fields") or []}
        enums = schema.get("enums") or {}
        date_fields = set(schema.get("dates") or [])
        clean: dict[str, Any] = {}
        for key, value in (fields or {}).items():
            if key in self.PROSPECT_MANAGED:
                raise ToolError(f"{key} is a managed field (set by the tool); leave it out")
            if key not in allowed:
                raise ToolError(f"unknown field {key!r}; prospect fields are {sorted(allowed)}")
            if value is None:
                clean[key] = None
                continue
            if key in enums:
                allowed_vals = [norm(v) for v in enums[key]]
                if norm(value) not in allowed_vals:
                    raise ToolError(f"{key} {value!r} not allowed; use one of {allowed_vals}")
                clean[key] = norm(value)
            elif key == "warmth":
                if not (isinstance(value, int) and 0 <= value <= 3):
                    raise ToolError("warmth must be an integer 0 (cold) to 3 (hot)")
                clean[key] = value
            elif key == "fit_score":
                try:
                    f = float(value)
                except (TypeError, ValueError):
                    raise ToolError("fit_score must be a number 1.0 to 5.0") from None
                if not 1.0 <= f <= 5.0:
                    raise ToolError("fit_score must be a number 1.0 to 5.0")
                clean[key] = round(f, 2)
            elif key in self.PROSPECT_INTS:
                if not (isinstance(value, int) and value >= 0):
                    raise ToolError(f"{key} must be a non-negative integer")
                clean[key] = value
            elif key in date_fields:
                d = _date_arg(value, key)
                clean[key] = d
            elif key in self.PROSPECT_LISTS:
                clean[key] = _as_list(value, key) or []
            else:
                clean[key] = str(value)
        return clean

    def prospect_set(self, prospect_id: str, fields: dict[str, Any] | None = None, timeline: str | None = None,
                     add_tags: Any = None, remove_tags: Any = None, create: bool = False) -> dict[str, Any]:
        pid = _check_id(prospect_id, "prospect_id")
        guard_credentials(fields, timeline, add_tags)
        clean = self._validate_prospect_fields(fields or {})
        try:        # tags are the controlled vocabulary (schemas/tags.yaml); checked before anything is written
            jc.check_tags([*(_as_list(add_tags, "add_tags") or []), *(clean.get("tags") or [])], jc.os_root_for(self.js))
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        path = self.js / "prospects" / f"{pid}.md"
        today = self.today()
        if create:
            if path.exists():
                raise ToolError(f"prospect {pid!r} already exists; update it without create=true")
            if not clean.get("name") or not clean.get("source"):
                raise ToolError("creating a prospect needs fields.name and fields.source")
            if (SCRIPTS / "prospects.py").is_file():
                # the one definition of a new prospect's fields (lists, counts, hints), so outbox.py mark-sent and
                # prospects.py never meet a record without sources, channels or counts (I-047 item 12)
                import prospects  # noqa: E402
                meta = prospects.new_meta(pid, str(clean["name"]), str(clean["source"]), today)
            else:
                meta = {"id": pid, "status": "new", "priority": "P3", "warmth": 0, "max_follow_ups": 3,
                        "created": today}
            body = f"# {clean['name']}\n\n## Timeline\n"
            old: dict[str, Any] = {}
            deferred = {k: clean.pop(k) for k in ("status", "priority", "warmth", "fit_score", "sponsorship_hint")
                        if k in clean} if (SCRIPTS / "prospects.py").is_file() else {}
            if deferred:
                # Write the bare record first so prospects.py set (the single writer of these fields) can lock,
                # date and mark the hand-set values; otherwise the next `score` run would silently overwrite them.
                bare = dict(meta)
                bare.update(clean)
                bare["updated"] = today
                jc.write_record(path, bare, body)
                meta, body = jc.read_record(path)
                clean.update(deferred)
        else:
            if not path.is_file():
                raise ToolError(f"prospect {pid!r} not found; pass create=true with name and source to add one")
            meta, body = jc.read_record(path)
            old = dict(meta)
        if old.get("status") == "do_not_contact" and "status" in clean and clean["status"] != "do_not_contact":
            raise ToolError("this prospect is do_not_contact; only the owner lifts that, by editing the record")
        if clean.get("status") == "do_not_contact" and not (clean.get("do_not_contact_reason")
                                                            or old.get("do_not_contact_reason")):
            raise ToolError("setting do_not_contact needs fields.do_not_contact_reason (the evidence)")
        add, drop = _as_list(add_tags, "add_tags") or [], _as_list(remove_tags, "remove_tags") or []
        via = "records (scripts/prospects.py not present)"
        base = old if not create else (dict(meta) if path.is_file() else {})  # what the script compares against
        if (not create or path.is_file()) and (SCRIPTS / "prospects.py").is_file():
            # prospects.py set is the single writer for status, priority, tags and the hand-set score fields
            # (it logs the status change on the Timeline; locks, dates and marks warmth, fit_score, priority and
            # sponsorship_hint in score_reasons); the tool writes only what the script has no field for.
            args = ["--root", str(self.js), "--today", today.isoformat(), "set", pid]
            delegated = []
            if clean.get("status") == "do_not_contact" and base.get("status") != "do_not_contact":
                args += ["--dnc", str(clean.get("do_not_contact_reason") or base.get("do_not_contact_reason"))]
                delegated += ["status", "do_not_contact_reason"]
            elif "status" in clean and clean["status"] != base.get("status"):
                args += ["--status", str(clean["status"])]
                delegated.append("status")
            if "priority" in clean and clean["priority"] != base.get("priority"):
                args += ["--priority", str(clean["priority"])]
                delegated.append("priority")
            for key in ("warmth", "fit_score", "sponsorship_hint"):
                if clean.get(key) is not None and clean[key] != base.get(key):
                    args.append(f"{key}={clean[key]}")
                    delegated.append(key)
            for t in add:
                args += ["--tag", t]
            for t in drop:
                args += ["--untag", t]
            if len(args) > 6:
                proc = self._run_script("prospects.py", args)
                if proc.returncode != 0:
                    tail = " | ".join((proc.stderr or proc.stdout).strip().splitlines()[-3:])
                    raise ToolError(f"prospects.py set refused: {tail[:500]}")
                meta, body = jc.read_record(path)
                for key in delegated:
                    clean.pop(key, None)
                add, drop = [], []
                via = ("scripts/prospects.py set (status, priority, tags, warmth, fit_score, sponsorship_hint) "
                       "+ records (other fields)")
            else:
                via = "records (fields scripts/prospects.py set has no flag for)"
        meta.update(clean)
        tags = [str(t) for t in (meta.get("tags") or [])]
        for t in add:
            if t not in tags:
                tags.append(t)
        tags = [t for t in tags if t not in set(drop)]
        if tags or "tags" in meta:
            meta["tags"] = tags
        bookkeeping = {"updated", "priority_locked", "locked_fields", "locked_on", "score_reasons", "type", "links"}
        changed = sorted(k for k in meta if k not in bookkeeping and dumps(meta.get(k)) != dumps(old.get(k)))
        appended = False
        if timeline and timeline.strip():
            body = jc.append_section_line(body, "Timeline", f"- {today.isoformat()} {cu.one_line(timeline, 400)}")
            appended = True
        if not (changed or appended):
            return {"id": pid, "created": False, "changed": [], "timeline_appended": False, "path": self.rel(path),
                    "note": "nothing to change", "via": via}
        meta["updated"] = today
        jc.write_record(path, meta, body)
        return {"id": pid, "created": bool(create), "changed": changed if not create else sorted(meta),
                "timeline_appended": appended, "path": self.rel(path), "via": via}

    # ------------------------------------------------------------------ writes: tracker
    def tracker_event(self, slug: str, type: str, source: str = "manual", conf: float = 1.0, evidence: str = "",
                      at: str | None = None, resume: str | None = None, reason: str | None = None) -> dict[str, Any]:
        slug = _check_id(slug, "application slug")
        if not (self.js / "applications" / slug / "application.md").is_file():
            raise ToolError(f"application {slug!r} not found; tracker.py new --job <id> creates it first")
        etype = str(type or "").strip().lower()
        if not _EVENT_TYPE_RE.match(etype):
            raise ToolError("type must look like 'applied', 'screen', 'rejected' or 'override:<stage>'")
        if source not in EVENT_SOURCES:
            raise ToolError(f"source must be one of {list(EVENT_SOURCES)}")
        if not (0.0 <= float(conf) <= 1.0):
            raise ToolError("conf must be between 0 and 1")
        if not str(evidence or "").strip():
            raise ToolError("evidence is required: a message id, a quoted line, or 'the owner said ... on <date>'")
        guard_credentials(evidence)
        args = ["--root", str(self.js), "event", slug, etype, "--source", source, "--conf", f"{float(conf):.2f}",
                "--evidence", cu.one_line(evidence, 240)]
        if at:
            if not cu.parse_ts(at):
                raise ToolError("at must be an ISO timestamp")
            args += ["--at", str(at)]
        if resume is not None:                     # post-mortem change 3: tracker.py requires it on applied events
            args += ["--resume", cu.one_line(str(resume), 300)]
        if reason is not None:
            guard_credentials(reason)
            args += ["--reason", cu.one_line(str(reason), 200)]
        proc = self._run_script("tracker.py", args)
        if proc.returncode != 0:
            tail = " | ".join((proc.stderr or proc.stdout).strip().splitlines()[-5:])
            raise ToolError(f"tracker.py refused the event: {tail[:600]}")
        m = re.search(r"stage (\S+)", proc.stdout or "")
        out = {"slug": slug, "type": etype, "stage": m.group(1) if m else None, "via": "scripts/tracker.py",
               "output": (proc.stdout or "").strip()[:300]}
        warnings = [ln.strip()[:300] for ln in (proc.stderr or "").splitlines() if ln.startswith("warning:")]
        if warnings:
            out["warnings"] = warnings[:5]
        return out

    # ------------------------------------------------------------------ writes: outbox
    def _outbox_cli(self, args: list[str]) -> Any:
        common = ["--root", str(self.js), "--os-root", str(self.os_root), "--today", self.today().isoformat()]
        proc = self._run_script("outbox.py", [args[0], *common, *args[1:]])
        try:
            data = json.loads(proc.stdout or "null")
        except json.JSONDecodeError:
            data = None
        if proc.returncode != 0 or data is None or (isinstance(data, dict) and data.get("error")):
            why = data.get("error") if isinstance(data, dict) and data.get("error") else \
                " | ".join((proc.stderr or proc.stdout or "no output").strip().splitlines()[-3:])
            raise ToolError(f"outbox.py {args[0]} refused: {str(why)[:700]}")
        return data

    def outbox_create(self, prospect: str | None, channel: str, body: str, subject: str | None = None,
                      kind: str = "reply", to: str | None = None) -> dict[str, Any]:
        if not (SCRIPTS / "outbox.py").is_file():
            raise ToolError("scripts/outbox.py is missing; drafts are written only through it (single writer)")
        if channel not in OUTBOX_CHANNELS:
            raise ToolError(f"channel must be one of {list(OUTBOX_CHANNELS)}")
        if kind not in TOUCH_KINDS:
            raise ToolError(f"kind must be one of {list(TOUCH_KINDS)}")
        if prospect:
            prospect = _check_id(prospect, "prospect")
        text = cu.canonical_text(body)
        if not text:
            raise ToolError("body is empty")
        guard_credentials(text, subject, to)
        with tempfile.TemporaryDirectory(prefix="js-mcp-") as tmp:
            body_file = Path(tmp) / "body.txt"
            body_file.write_text(text + "\n", encoding="utf-8")
            os.chmod(body_file, 0o600)
            args = ["draft", "--channel", channel, "--body-file", str(body_file), "--kind", kind]
            if prospect:
                args += ["--prospect", prospect]
            if subject:
                args += ["--subject", subject]
            if to:
                args += ["--to", to]
            data = self._outbox_cli(args)
        path = Path(str(data.get("path") or ""))
        return {"id": data.get("id"), "path": self.rel(path) if path.name else None, "status": "drafted",
                "checks": data.get("checks") or [], "via": "scripts/outbox.py draft",
                "next": "fix every check by filing a corrected draft, then approval_create(outbox_ids=[...])"}

    def approval_create(self, requested_by: str, why: str, outbox_ids: Any = None,
                        items: list[dict[str, Any]] | None = None, action: str | None = None,
                        effect: str | None = None, risk: str = "low", ticket: str | None = None) -> dict[str, Any]:
        requested_by = _check_id(requested_by, "requested_by")
        if not str(why or "").strip():
            raise ToolError("why is required: it is what the owner reads before deciding")
        ids = [_check_id(i, "outbox id") for i in _as_list(outbox_ids, "outbox_ids") or []]
        if bool(ids) == bool(items):
            raise ToolError("pass exactly one of outbox_ids (drafts filed with outbox_create) or items")
        guard_credentials(why, effect, items)
        actions = enum("approval", "action")
        if action is not None and action not in actions:
            raise ToolError(f"action {action!r} not allowed; use one of {list(actions)}")
        if ids:
            if action not in (None, "send-email", "post-linkedin", "edit-linkedin-profile"):
                raise ToolError("outbox drafts become send-email, post-linkedin or edit-linkedin-profile approvals "
                                "(outbox.py picks per channel)")
            args = ["batch", "--ids", ",".join(ids), "--why", str(why).strip()]
            if ticket:
                args += ["--ticket", str(ticket)]
            created = self._outbox_cli(args)
            approvals = [{"approval": c.get("approval"), "path": self.rel(Path(str(c.get("path")))),
                          "items": c.get("items")} for c in created or []]
            return {"status": "pending", "approvals": approvals, "via": "scripts/outbox.py batch",
                    "next": "The owner reviews each file, unticks anything rejected and sets status: approved; then "
                            "outbox.py apply-decisions. Nothing is drafted in Gmail or sent before that"}
        if action is None:
            raise ToolError(f"action is required for items; use one of {list(actions)}")
        if risk not in RISKS:
            raise ToolError(f"risk must be one of {list(RISKS)}")
        if not str(effect or "").strip():
            raise ToolError("effect is required for items: what changes in the world if the owner approves")
        approval_items: list[cu.ApprovalItem] = []
        for raw in items or []:
            missing = [k for k in ("id", "channel", "target", "body") if not str(raw.get(k) or "").strip()]
            if missing:
                raise ToolError(f"each item needs id, channel, target and body; missing {missing}")
            approval_items.append(cu.ApprovalItem(
                id=_check_id(raw["id"], "item id"), channel=str(raw["channel"]), target=str(raw["target"]),
                body=cu.canonical_text(raw["body"]), subject=str(raw.get("subject") or ""),
                label=str(raw.get("label") or ""), prospect=raw.get("prospect"), thread_id=raw.get("thread_id"),
                checks=[str(c) for c in raw.get("checks") or []]))
        aid, apath = cu.write_approval(self.roots, action=action, items=approval_items,
                                       requested_by=f"role:{requested_by}", why=str(why).strip(),
                                       effect=str(effect).strip(), today=self.today(), ticket=ticket, risk=risk)
        meta, _ = jc.read_record(apath)
        return {"status": "pending", "approvals": [{"approval": aid, "path": self.rel(apath),
                                                    "items": len(approval_items)}],
                "content_hash": meta.get("content_hash"), "expires": str(meta.get("expires")),
                "via": "js_comms_util.write_approval",
                "next": "The owner reviews the file and sets status: approved or rejected; only then may the sender "
                        "role or the owner act on it"}
