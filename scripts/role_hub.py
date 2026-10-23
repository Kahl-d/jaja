#!/usr/bin/env python3
"""The per-role record hub: one generated applications/<slug>/HUB.md per application, tracking a role's company,
messages, people, trails, reach-outs, application, opening and research in one place.

A hub is a deterministic JOIN, never a record. Every line links the file that holds the fact; the
records stay the single sources (root AGENTS.md rule 1). Nothing here writes a record: the only outputs
are the generated hubs and the index.

What one hub joins, for applications/<slug>/application.md (links are relative to the hub, except files inside
an application folder, whose basenames repeat per application: those are linked by vault path,
[[<vault prefix>/applications/<slug>/<file>|<file>.md]], the graph_links.py form):
  Opening       jobs/<id>.md (the application's `job:`): title, posting and apply URLs, status; `closed` when
                the job says so, else "not marked closed" with last_seen and live_checked_at (never "live"
                without a liveness check on record); jd.md snapshot date.
  Application   the stage and next action on file (written by tracker.py project), applied_at, applied_via,
                and the event log (tracker.py parse_events) in time order: at, type, source. Evidence text
                stays in application.md (it can quote mail); unparseable event lines are reported.
  Company       companies/<company_slug>.md: tier, e_verify, sponsorship_stance, last_researched; the pursuits
                (pursuits/<slug>/pursuit.md) that picked this role, with stage and what each waits on.
  People        prospects/*.md linked to this application (the application's `prospects:` or the prospect's
                `applications:`), then everyone else at the company (`company_slug`), marked as such. A person
                linked to another application at the same company is listed as "linked to <that slug>", and
                their trail stays in that application's hub.
  Trail         outbox/*.md items and touches/*.jsonl rows for those people, oldest first, with their status
                (drafted, pending, approved, sent, replied, delivered, ...), except people linked to a sibling
                application. An item or touch that names an
                `application:` belongs to that application only; one naming none belongs to every
                application at the company (a company-wide reach-out). A touch logged from an outbox item
                (`draft_path`) is folded into that item's line. Message text is never copied: it is data from
                outside, and it lives in the records.
  Resume        the application's recorded `resume_variant:` (a folder path or a [[wikilink]]; never a folder
                guessed from the slug): meta.yaml base_of and editorial_verdict,
                NOTES.md, review.md verdict, cover.md, why-us.md; `resume_pdf` and `resume_sha256` (the page that
                went); `resume_none_reason` when no PDF went.
  Fit           applications/<slug>/fit.md: fit_score (or score), fit_verdict (or verdict).
  Research      the dossier above plus research/**/*.md that name the company or application: a frontmatter
                `company`, `company_slug`, `application` or `links` value, or a [[<company_slug>]] or [[<slug>]]
                wikilink in the text.
  Prep          prep/packs/<slug>/*.md, prep/debriefs/<slug>-*.md, prep/mocks/*-<slug>.md.
  Legal / visa  legal/checks/<company_slug>.md (verdict, e_verify, checked_at), legal/offers/<slug>.md.
  Offer, outcome, apply pack   offer.md, outcome.md, apply-pack.md in the application folder.
Anything absent gets a `missing:` line naming the file and the role that makes it (missing is not empty);
stage-gated items say from which stage they are expected.

A company with people, a trail or a dossier but no application gets NO hub: a hub needs an application,
and applications/<slug>/ is created only by `tracker.py new` (fit-scorer). Inventing the folder would
put a phantom application in the tracker, the dashboard and the MCP. Its trail is listed instead in the
index, state/role-hubs.md, under "Companies with a trail and no application"; when the application is
created, its hub picks up the company-wide trail on the next refresh.

Deterministic: a hub depends only on the records (no clock), so a refresh with nothing changed writes
nothing. A HUB.md left in a folder whose application.md is gone is removed (only when it carries this
script's header).

Refreshed by scripts/dashboard.py (every DASHBOARD.md regeneration writes the hubs too; tracker-ops runs
it) and on demand:
  python3 scripts/role_hub.py [--root R] [<slug> ...] [--dry-run] [--check]
    no slug: every application, the index, and pruning; with slugs: only those hubs.
    --dry-run prints what would change; --check exits 1 when any hub or the index is stale (writes nothing).
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import graph_links  # noqa: E402
import tracker  # noqa: E402
from company_index import CompanyIndex  # noqa: E402
from fetch_ats import parse_ts  # noqa: E402
from js_common import JS_ROOT, fm, iter_records, iter_touches  # noqa: E402

SELF = "scripts/role_hub.py"
HUB = "HUB.md"
INDEX = Path("state") / "role-hubs.md"


def header(source: str) -> str:
    """The repo's GENERATED header (the PreToolUse guard refuses hand edits to files carrying it)."""
    return f"<!-- GENERATED by {SELF} from {source} — do not edit -->"


# stage from which an absent item counts as expected (tracker.PROGRESS order); None = always expected
EXPECTED_FROM = {"company": None, "legal_check": "fit_checked", "variant": "fit_checked", "prep": "screen",
                 "offer": "offer", "apply_pack": "tailored", "people": None}


# ------------------------------------------------------------------------------ records
@dataclass
class Records:
    root: Path
    prospects: list[dict[str, Any]] = field(default_factory=list)
    outbox: list[dict[str, Any]] = field(default_factory=list)
    touches: list[dict[str, Any]] = field(default_factory=list)
    apps: list[tuple[str, dict[str, Any], str]] = field(default_factory=list)
    research: list[tuple[str, dict[str, Any], str]] = field(default_factory=list)
    pursuits: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    @classmethod
    def load(cls, root: Path) -> "Records":
        root = Path(root)
        rec = cls(root)
        companies = CompanyIndex.load(root)            # one slug per company, the dossier's first

        def canon(meta: dict[str, Any]) -> dict[str, Any]:
            named = meta.get("company_slug") or meta.get("company")
            return {**meta, "company_slug": companies.resolve(named)[0]} if isinstance(named, str) and named \
                else dict(meta)

        for path, meta, _ in iter_records(root / "prospects"):
            rec.prospects.append({**canon(meta), "id": str(meta.get("id") or path.stem), "_file": path.name})
        for path, meta, _ in iter_records(root / "outbox"):
            rec.outbox.append({**meta, "id": str(meta.get("id") or path.stem), "_file": path.name})
        for row in iter_touches(root):
            rec.touches.append(dict(row))
        rec.apps = [(slug, canon(meta), body) for slug, meta, body in tracker.iter_applications(root) or []]
        for path in sorted((root / "pursuits").glob("*/pursuit.md")) if (root / "pursuits").is_dir() else []:
            rec.pursuits.append((path.parent.name, fm.load(path)[0]))
        folder = root / "research"
        if folder.is_dir():
            for path in sorted(folder.rglob("*.md")):
                if path.is_file() and not path.name.startswith((".", "_")):
                    try:
                        meta, body = fm.load(path)
                    except Exception:                      # an unreadable note is skipped, never fatal
                        continue
                    rec.research.append((path.relative_to(root).as_posix(), canon(meta) if "company" in meta
                                         or "company_slug" in meta else meta, body))
        return rec


def _link(value: Any) -> str | None:
    return tracker._link_target(value)


def _list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    return [t for t in (_link(v) for v in items) if t]


def _day(value: Any) -> str:
    ts = parse_ts(value)
    return ts.strftime("%Y-%m-%d") if ts else (str(value) if value not in (None, "") else "?")


def _stamp(value: Any) -> str:
    ts = parse_ts(value)
    if not ts:
        return str(value) if value not in (None, "") else "?"
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ") if (ts.hour, ts.minute, ts.second) != (0, 0, 0) else ts.strftime("%Y-%m-%d")


def _sort_key(value: Any) -> str:
    ts = parse_ts(value)
    return ts.strftime("%Y-%m-%dT%H:%M:%S") if ts else "9999"


def _rank(stage: Any) -> int:
    return tracker.PROGRESS.index(stage) if stage in tracker.PROGRESS else -1


def _expected(key: str, stage: Any) -> bool:
    start = EXPECTED_FROM.get(key)
    if start is None:
        return True
    if stage in ("rejected", "ghosted", "withdrawn", "on_hold"):
        return False
    return _rank(stage) >= _rank(start)


def variant_path(root: Path, value: Any) -> str | None:
    """application.md resume_variant as a folder: a path (resumes/variants/<name>) as written, or a [[wikilink]]
    resolved under resumes/variants/ then resumes/bases/; None when nothing is recorded. Never guessed from the slug."""
    text = str(value or "").strip()
    if not text:
        return None
    m = re.fullmatch(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]", text)
    name = (m.group(1) if m else text).strip().strip("/")
    if not name or ".." in Path(name).parts or Path(name).is_absolute():
        return None
    if "/" in name:                                    # a path; a file in it (the PDF) means its folder
        parts = name.split("/")
        if Path(name).suffix:
            parts = parts[:-1]
        if len(parts) > 3 and parts[0] == "resumes" and parts[1] in ("variants", "bases"):
            parts = parts[:3]
        return "/".join(parts) or None
    for base in ("resumes/variants", "resumes/bases"):  # a bare folder name, as a [[wikilink]] or plain
        if (Path(root) / base / name).is_dir():
            return f"{base}/{name}"
    return f"resumes/variants/{name}"


def _missing(what: str, key: str | None, stage: Any) -> str:
    """A `missing:` line; `what` names the file and its maker. Stage-gated items say when they are due."""
    if key is None or _expected(key, stage):
        return f"- missing: {what}"
    return f"- missing: {what}; expected from stage {EXPECTED_FROM[key]}, stage now {stage}"


def _meta(path: Path) -> dict[str, Any]:
    try:
        return fm.load(path)[0] if path.is_file() else {}
    except Exception:
        return {}


def _yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = fm.parse_yaml(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


# ------------------------------------------------------------------------------ joins
def company_apps(rec: Records, company_slug: str | None) -> list[str]:
    return sorted(slug for slug, meta, _ in rec.apps if company_slug and meta.get("company_slug") == company_slug)


def people_for(rec: Records, slug: str, meta: dict[str, Any]) -> list[dict[str, Any]]:
    """Prospects for this application: linked ones first, then the rest of the company, each with its basis."""
    cs = meta.get("company_slug")
    linked_ids = set(_list(meta.get("prospects")))
    siblings = set(company_apps(rec, cs)) - {slug}
    other_app_links = {s: set(_list(m.get("prospects"))) for s, m, _ in rec.apps if s in siblings}
    out = []
    for p in rec.prospects:
        apps = set(_list(p.get("applications")))
        if p["id"] in linked_ids or slug in apps:
            out.append({**p, "_basis": "linked to this application"})
        elif cs and p.get("company_slug") == cs:
            elsewhere = sorted((apps & siblings) | {s for s, ids in other_app_links.items() if p["id"] in ids})
            if elsewhere:                                  # a sibling application's person: listed, trail stays there
                out.append({**p, "_basis": "at the company, linked to " + ", ".join(elsewhere), "_elsewhere": True})
            else:
                out.append({**p, "_basis": "at the company"})
    out.sort(key=lambda p: (p["_basis"] != "linked to this application", bool(p.get("_elsewhere")),
                            str(p.get("name") or p["id"]).lower()))
    return out


def trail_for(rec: Records, slug: str | None, people: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Outbox items and touches for these people (and any naming this application), oldest first."""
    ids = {p["id"] for p in people if not p.get("_elsewhere")}
    names = {p["id"]: str(p.get("name") or p["id"]) for p in people}

    def mine(item: dict[str, Any]) -> bool:
        app = _link(item.get("application"))
        if app:
            return app == slug
        return str(item.get("prospect") or "") in ids

    rows: list[dict[str, Any]] = []
    folded: dict[str, dict[str, Any]] = {}
    for o in rec.outbox:
        if not mine(o):
            continue
        at = o.get("sent_at") or o.get("approved_at") or o.get("created")
        row = {"at": at, "sort": _sort_key(at), "direction": "out", "channel": o.get("channel") or "?",
               "kind": o.get("kind") or "?", "status": o.get("status") or "drafted",
               "prospect": o.get("prospect"), "link": f"outbox/{o['_file']}", "label": o["id"],
               "approval": o.get("approval"), "touch": None}
        folded[f"outbox/{o['_file']}"] = row
        rows.append(row)
    for t in rec.touches:
        if not mine(t):
            continue
        draft = str(t.get("draft_path") or "")
        if draft in folded:
            folded[draft]["touch"] = _stamp(t.get("at"))
            continue
        month = _day(t.get("at"))[:7]
        rows.append({"at": t.get("at"), "sort": _sort_key(t.get("at")), "direction": t.get("direction") or "?",
                     "channel": t.get("channel") or "?", "kind": t.get("kind") or "?",
                     "status": t.get("status") or "?", "prospect": t.get("prospect"),
                     "link": f"touches/{month}.jsonl", "label": f"touch {t.get('key') or month}",
                     "approval": t.get("approval_id"), "touch": None})
    for r in rows:
        r["name"] = names.get(str(r.get("prospect") or ""), r.get("prospect") or "no prospect")
    rows.sort(key=lambda r: (r["sort"], r["label"]))
    return rows


def research_for(rec: Records, company_slug: str | None, slug: str | None) -> list[tuple[str, dict[str, Any]]]:
    keys = {k for k in (company_slug, slug) if k}
    if not keys:
        return []
    out = []
    for rel, meta, body in rec.research:
        named = set()
        for f in ("company", "company_slug", "application", "links"):
            named |= set(_list(meta.get(f)))
        wiki = set(re.findall(r"\[\[([^\]|#]+)", body or ""))
        if keys & (named | wiki):
            out.append((rel, meta))
    return out


# ------------------------------------------------------------------------------ render
def _time_source(event: Any) -> str:
    """For an applied event: whether its time was stated (--at) or is the run clock (tracker.py's marker)."""
    if event.evidence.startswith(tracker.APPLIED_TIME_STATED):
        return " · time stated"
    if event.evidence.startswith(tracker.APPLIED_TIME_CLOCK.split(" (", 1)[0]):
        return " · time from the run clock, not the stated submission time"
    return ""


def _rel(path: str) -> str:
    """A department-relative path as seen from applications/<slug>/HUB.md."""
    return f"../../{path}"


def _app_file(slug: str, name: str, label: str | None = None) -> str:
    """A file inside applications/<slug>/ (application.md, HUB.md, fit.md, jd.md, outcome.md, offer.md,
    apply-pack.md): these basenames repeat once per application, so a bare or relative link is ambiguous in the
    vault. Linked by vault path instead, the form graph_links.py writes: [[<vault path>|label]]."""
    return graph_links.wikilink(f"applications/{slug}/{name}", label or name)


def _trail_lines(rows: list[dict[str, Any]], rel) -> list[str]:
    out = []
    for r in rows:
        extra = []
        if r.get("approval"):
            extra.append(f"approval {r['approval']}")
        if r.get("touch"):
            extra.append(f"touch logged {r['touch']}")
        out.append(f"- {_stamp(r['at'])} · {r['direction']} · {r['channel']} · {r['kind']} · {r['status']} · "
                   f"{r['name']} · [{r['label']}]({rel(r['link'])})" + (f" · {', '.join(extra)}" if extra else ""))
    return out


def _people_lines(people: list[dict[str, Any]], rel) -> list[str]:
    return [f"- [{p.get('name') or p['id']}]({rel('prospects/' + p['_file'])}) · {p.get('role') or 'role unknown'} · "
            f"{p.get('status') or 'status unknown'} · {p.get('priority') or 'no priority'} · last in "
            f"{_day(p.get('last_inbound_at')) if p.get('last_inbound_at') else 'never'}, last out "
            f"{_day(p.get('last_outbound_at')) if p.get('last_outbound_at') else 'never'} · {p['_basis']}"
            for p in people]


def render_hub(rec: Records, slug: str, meta: dict[str, Any], body: str) -> str:
    root = rec.root
    folder = root / "applications" / slug
    stage = meta.get("stage")
    cs = meta.get("company_slug")
    title, company = meta.get("title") or "?", meta.get("company") or cs or "?"
    L = [header(f"applications/{slug}/ and the records it links"),
         f"# {title} at {company}", "",
         f"The record for this role: every line links the file that holds the fact. Refreshed by "
         f"`scripts/role_hub.py` (and every `scripts/dashboard.py` run); never hand-edited.", ""]

    # ---- opening
    L += ["## Opening", ""]
    job_id = _link(meta.get("job"))
    job_path = root / "jobs" / f"{job_id}.md" if job_id else None
    if job_path and job_path.is_file():
        job = _meta(job_path)
        status = job.get("status") or "unknown"
        if status == "closed":
            live = f"closed (job status closed; last seen {_day(job.get('last_seen'))})"
        else:
            check = _day(job.get("live_checked_at")) if job.get("live_checked_at") else "never"
            live = (f"not marked closed · last seen {_day(job.get('last_seen'))} · liveness check {check}")
        L.append(f"- [{job_id}]({_rel('jobs/' + job_id + '.md')}) · status {status} · {live}")
        L.append(f"- posting: {job.get('source_url') or 'missing (no source_url on the job)'}"
                 + (f" · apply: {job['apply_url']}" if job.get("apply_url") else "")
                 + (f" · posted {_day(job['posted_at'])}" if job.get("posted_at") else ""))
    elif job_id:
        L.append(f"- missing: jobs/{job_id}.md (the application names it; job-scout)")
    else:
        L.append("- missing: the application names no job (tracker.py new links it)")
    jd = _meta(folder / "jd.md")
    L.append(f"- {_app_file(slug, 'jd.md')} · snapshot {_day(jd.get('snapshot_at'))}" if (folder / "jd.md").is_file()
             else "- missing: jd.md (the JD snapshot; tracker.py new or fit-scorer)")
    L.append("")

    # ---- application
    L += ["## Application", ""]
    events = tracker.parse_events(body)
    if meta.get("applied_at"):
        applied = f"applied {_day(meta['applied_at'])}"
    elif any(tracker.EVENT_STAGE.get(e.type.strip().lower()) == "applied" for e in events):
        applied = "applied_at not on file though an applied event is logged (tracker.py project sets it)"
    else:
        applied = "not applied"
    L.append(f"- {_app_file(slug, 'application.md')} · stage {stage or 'unknown'} (on file, projected by "
             f"`tracker.py project`) · {applied} · via {meta.get('applied_via') or 'not recorded'}")
    L.append(f"- next: {meta.get('next_action') or 'none'}"
             + (f" · due {_day(meta.get('next_action_at'))}" if meta.get("next_action_at") else ""))
    ordered = [e for _, e in sorted(enumerate(events), key=lambda ie: (_sort_key(ie[1].at), ie[0]))]
    L.append(f"- events ({len(events)}, evidence in application.md):")
    L += [f"  - {_stamp(e.at)} · {e.type} · {e.source}{_time_source(e)}" for e in ordered] or ["  - none"]
    bad = tracker.unparsed_event_lines(body)
    if bad:
        L.append(f"- warning: {len(bad)} unparseable event line(s) in application.md, not counted (tracker-ops fixes)")
    L.append("")

    # ---- company
    L += ["## Company", ""]
    dossier = root / "companies" / f"{cs}.md" if cs else None
    if dossier and dossier.is_file():
        d = _meta(dossier)
        L.append(f"- [companies/{cs}.md]({_rel(f'companies/{cs}.md')}) · tier {d.get('tier') or '?'} · "
                 f"E-Verify {d.get('e_verify') or 'unknown'} · sponsorship {d.get('sponsorship_stance') or 'unknown'}"
                 f" · researched {_day(d.get('last_researched'))}")
    else:
        L.append(_missing(f"companies/{cs or '<company_slug>'}.md (the dossier; company-researcher)", "company", stage))
    siblings = [s for s in company_apps(rec, cs) if s != slug]
    if siblings:
        L.append("- other applications here: " + ", ".join(_app_file(s, HUB, s) for s in siblings))
    for pid, pm in rec.pursuits:                   # the directions this role is picked for
        if slug in (pm.get("applications") or []):
            L.append(f"- pursuit: {graph_links.wikilink(f'pursuits/{pid}/HUB', pid)} · stage {pm.get('stage')} · "
                     f"waiting on {pm.get('waiting_on')}")
    L.append("")

    # ---- people
    people = people_for(rec, slug, meta)
    L += ["## People", ""]
    L += _people_lines(people, _rel) or [_missing(f"no prospect linked to this application or at {company} "
                                                  "(prospector finds a path)", "people", stage)]
    L.append("")

    # ---- trail
    trail = trail_for(rec, slug, people)
    L += ["## Trail (reach-outs and messages, oldest first)", ""]
    L += _trail_lines(trail, _rel) or ["- missing: no outbox item or touch for these people (messenger, prospector)"]
    L.append("")

    # ---- resume
    L += ["## Resume variant", ""]
    vrel = variant_path(root, meta.get("resume_variant"))          # recorded only, never guessed
    vdir = root / vrel if vrel else None
    if vdir is not None and vdir.is_dir():
        vmeta = _yaml(vdir / "meta.yaml")
        review = _meta(vdir / "review.md")
        L.append(f"- [{vrel}/]({_rel(vrel + '/')}) · base_of {vmeta.get('base_of') or 'not recorded'} · "
                 f"editorial_verdict {vmeta.get('editorial_verdict') or 'not recorded'}")
        for name, owner in (("NOTES.md", "resume-tailor"), ("review.md", "resume-editor"),
                            ("cover.md", "resume-tailor"), ("why-us.md", "resume-tailor")):
            if (vdir / name).is_file():
                extra = f" · verdict {review.get('verdict')}" if name == "review.md" and review.get("verdict") else ""
                L.append(f"- [{name}]({_rel(f'{vrel}/{name}')}){extra}")
            else:
                L.append(f"- missing: {vrel}/{name} ({owner})")
    elif vrel:
        L.append(_missing(f"{vrel}/ (recorded in resume_variant; resume-tailor, from resumes/bases/general)", "variant",
                          stage))
    elif meta.get("resume_none_reason"):
        L.append(f"- no PDF went: {meta['resume_none_reason']} (resume_none_reason)")
    else:
        L.append(_missing(f"resume_variant not recorded: `python3 scripts/tracker.py event {slug} applied --resume "
                          "<PDF>` names the page that went (resume-tailor makes a variant from resumes/bases/general)",
                          "variant", stage))
    if meta.get("resume_pdf"):
        pdf = str(meta["resume_pdf"])
        sha = str(meta.get("resume_sha256") or "")
        L.append(f"- sent: [{pdf.rsplit('/', 1)[-1]}]({_rel(pdf)})" + (f" · sha256 {sha[:12]}" if sha else ""))
    L.append("")

    # ---- fit
    L += ["## Fit", ""]
    if (folder / "fit.md").is_file():
        f = _meta(folder / "fit.md")
        score = f.get("fit_score") if f.get("fit_score") is not None else f.get("score")   # fit-scorer writes either
        L.append(f"- {_app_file(slug, 'fit.md')} · score {score if score is not None else 'unscored'}"
                 f" · verdict {f.get('fit_verdict') or f.get('verdict') or 'unscored'}")
    else:
        L.append("- missing: fit.md (fit-scorer)")
    L.append("")

    # ---- research and prep
    L += ["## Research and prep", ""]
    notes = research_for(rec, cs, slug)
    L += [f"- [{rel}]({_rel(rel)})" + (f" · updated {_day(m.get('updated'))}" if m.get("updated") else "")
          for rel, m in notes] or ["- research notes naming this company or role: none"]
    packs = sorted((root / "prep" / "packs" / slug).glob("*.md")) if (root / "prep" / "packs" / slug).is_dir() else []
    debriefs = sorted((root / "prep" / "debriefs").glob(f"{slug}-*.md")) if (root / "prep" / "debriefs").is_dir() else []
    mocks = sorted((root / "prep" / "mocks").glob(f"*-{slug}.md")) if (root / "prep" / "mocks").is_dir() else []
    L += [f"- prep pack [{p.name}]({_rel(p.relative_to(root).as_posix())})" for p in packs] \
        or [_missing(f"prep/packs/{slug}/<round>.md (prep-coach)", "prep", stage)]
    L += [f"- debrief [{p.name}]({_rel(p.relative_to(root).as_posix())})" for p in debriefs]
    L += [f"- mock [{p.name}]({_rel(p.relative_to(root).as_posix())})" for p in mocks]
    L.append("")

    # ---- legal
    L += ["## Legal and visa", ""]
    check = root / "legal" / "checks" / f"{cs}.md" if cs else None
    if check and check.is_file():
        c = _meta(check)
        L.append(f"- [legal/checks/{cs}.md]({_rel(f'legal/checks/{cs}.md')}) · verdict {c.get('verdict') or '?'} · "
                 f"E-Verify {c.get('e_verify') or 'unknown'} · checked {_day(c.get('checked_at'))}")
    else:
        L.append(_missing(f"legal/checks/{cs or '<company_slug>'}.md (the visa gate; legal-visa)", "legal_check", stage))
    offer_review = root / "legal" / "offers" / f"{slug}.md"
    if offer_review.is_file():
        L.append(f"- [legal/offers/{slug}.md]({_rel(f'legal/offers/{slug}.md')})")
    elif _expected("offer", stage):
        L.append(f"- missing: legal/offers/{slug}.md (legal-visa, offer visa review)")
    L.append("")

    # ---- offer, outcome, apply pack
    L += ["## Apply pack, offer and outcome", ""]
    L.append(f"- {_app_file(slug, 'apply-pack.md')} (local only, gitignored)" if (folder / "apply-pack.md").is_file()
             else _missing("apply-pack.md (apply-assist, scripts/apply_pack.py build)", "apply_pack", stage))
    if (folder / "offer.md").is_file():
        o = _meta(folder / "offer.md")
        L.append(f"- {_app_file(slug, 'offer.md')}" + (f" · deadline {_day(o.get('deadline'))}" if o.get("deadline") else ""))
    elif _expected("offer", stage):
        L.append("- missing: offer.md (negotiator)")
    if (folder / "outcome.md").is_file():
        L.append(f"- {_app_file(slug, 'outcome.md')} · outcome {_meta(folder / 'outcome.md').get('outcome') or 'pending'}")
    else:
        L.append("- missing: outcome.md (tracker.py new)")
    L.append("")
    return "\n".join(L)


def companies_without_application(rec: Records) -> list[str]:
    with_app = {m.get("company_slug") for _, m, _ in rec.apps if m.get("company_slug")}
    seen = {p.get("company_slug") for p in rec.prospects if p.get("company_slug")}
    folder = rec.root / "companies"
    if folder.is_dir():
        seen |= {p.stem for p in folder.glob("*.md") if not p.name.startswith(("_", "."))}
    return sorted(s for s in seen - with_app if s)


def render_index(rec: Records) -> str:
    L = [header("applications/*/application.md, prospects/, outbox/, touches/, companies/"),
         "# Role records", "",
         "One hub per application (`applications/<slug>/HUB.md`, generated by `scripts/role_hub.py`). A company "
         "with a trail but no application has no hub: its record is below until fit-scorer creates the "
         "application (`tracker.py new`).", "", "## Hubs", ""]
    if not (rec.root / "applications").is_dir():
        L.append("- no data yet (applications/ is absent)")
    elif not rec.apps:
        L.append("- none (applications/ holds no application yet)")
    for slug, meta, _ in rec.apps:
        L.append(f"- {_app_file(slug, HUB, slug)} · {meta.get('company') or '?'}: {meta.get('title') or '?'}"
                 f" · stage {meta.get('stage') or 'unknown'}")
    L += ["", "## Companies with a trail and no application", ""]
    orphans = companies_without_application(rec)
    if not orphans:
        L.append("- none")
    for cs in orphans:
        dossier = rec.root / "companies" / f"{cs}.md"
        people = [{**p, "_basis": "at the company"} for p in rec.prospects if p.get("company_slug") == cs]
        people.sort(key=lambda p: str(p.get("name") or p["id"]).lower())
        L += [f"### {cs}", "",
              f"- no hub: no application at {cs} yet"]
        L.append(f"- dossier: [companies/{cs}.md](../companies/{cs}.md)" if dossier.is_file()
                 else f"- missing: companies/{cs}.md (company-researcher)")
        rel = lambda p: f"../{p}"  # noqa: E731
        L.append("- people:")
        L += ["  " + ln for ln in _people_lines(people, rel)] or ["  - missing: no prospect at this company"]
        L.append("- trail (oldest first):")
        L += ["  " + ln for ln in _trail_lines(trail_for(rec, None, people), rel)] or ["  - none"]
        L.append("")
    if L[-1] != "":
        L.append("")
    return "\n".join(L)


# ------------------------------------------------------------------------------ write
def plan(root: Path, slugs: list[str] | None = None) -> dict[Path, str | None]:
    """{path: new text, or None to remove}: only the files that would change."""
    root = Path(root)
    rec = Records.load(root)
    known = {slug: (meta, body) for slug, meta, body in rec.apps}
    wanted = slugs if slugs else sorted(known)
    changes: dict[Path, str | None] = {}
    for slug in wanted:
        if slug not in known:
            raise KeyError(f"no application {slug}: applications/{slug}/application.md not found")
        path = root / "applications" / slug / HUB
        text = render_hub(rec, slug, *known[slug])
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            changes[path] = text
    if not slugs:
        folder = root / "applications"
        for hub in sorted(folder.glob(f"*/{HUB}")) if folder.is_dir() else []:
            if hub.parent.name not in known and SELF in hub.read_text(encoding="utf-8").split("\n", 1)[0]:
                changes[hub] = None
        index = root / INDEX
        text = render_index(rec)
        if not index.is_file() or index.read_text(encoding="utf-8") != text:
            changes[index] = text
    return changes


def apply(changes: dict[Path, str | None]) -> None:
    for path, text in changes.items():
        if text is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")


def refresh(root: Path, slugs: list[str] | None = None) -> dict[Path, str | None]:
    """Write every stale hub (and, without slugs, the index); return what changed."""
    changes = plan(root, slugs)
    apply(changes)
    return changes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("slugs", nargs="*", help="application slugs (default: every application, the index, pruning)")
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print what would change; write nothing")
    mode.add_argument("--check", action="store_true", help="exit 1 when any hub or the index is stale; write nothing")
    args = ap.parse_args(argv)
    try:
        changes = plan(args.root, args.slugs or None)
    except KeyError as exc:
        print(str(exc).strip("'\""), file=sys.stderr)
        return 2
    for path, text in changes.items():
        rel = path.relative_to(args.root).as_posix() if path.is_relative_to(args.root) else str(path)
        verb = "remove" if text is None else ("update" if path.is_file() else "create")
        print(f"{'would ' if args.dry_run or args.check else ''}{verb} {rel}")
    if args.check:
        print(f"stale: {len(changes)}")
        return 1 if changes else 0
    if not args.dry_run:
        apply(changes)
    print(f"role hubs: {len(changes)} changed" + (" (dry-run, nothing written)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
