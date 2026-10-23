"""portals_inv.py: the Portals page's data. Read only. Reads the recruiter lane's files and never
writes them:

  state/recruiter/portal-inventory.yaml            one row per portal in priority order (writer: the recruiter lane)
  state/recruiter/<owner>-clicks-<date>.md        what only the owner clicks, per portal (the newest file)
  state/recruiter/portal-content/readback-findings-<date>.md   findings and outcomes per portal (the newest file)

Missing is not empty: a part the inventory does not record is None and the page prints "not recorded"; a portal the
clicks or findings never name has an empty list, which the page says in words.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

PARTS = ("account", "profile", "resume", "skills", "alerts", "settings", "messaging", "applications", "tools")
VALUES = {"done", "partial", "todo", "his-hand", "n/a"}
TOTAL_PARTS = len(PARTS)


def _newest(folder: Path, pattern: str) -> Path | None:
    files = sorted(folder.glob(pattern)) if folder.is_dir() else []
    return files[-1] if files else None


def _rows(md: str) -> list[list[str]]:
    """Every table body row of a Markdown file as its cells (header and separator rows left out)."""
    out = []
    for line in md.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        out.append(cells)
    return out


def _mentions(text: str, portal: str) -> bool:
    """Does a clicks or findings line name this portal? The whole name, or its first word when that word is distinctive
    (so "Greenhouse" matches "MyGreenhouse" and "YC" matches "YC Work at a Startup"; "AI" alone never matches)."""
    t = text.lower()
    name = portal.lower()
    if name in t:
        return True
    first = re.split(r"[\s(]", name)[0]
    return len(first) >= 3 and first not in {"the", "and", "job"} and re.search(rf"\b{re.escape(first)}\b", t) is not None


def clicks_for(md: str, names: list[str]) -> dict[str, list[dict[str, str]]]:
    """the owner's clicks per portal: the rows of every table whose first text cell names the portal."""
    out: dict[str, list[dict[str, str]]] = {n: [] for n in names}
    section = ""
    for line in md.splitlines():
        if line.startswith("# "):
            section = line[2:].strip()
    for cells in _rows(md):
        if len(cells) < 3:
            continue
        where = cells[1]
        for n in names:
            if _mentions(where, n):
                out[n].append({"where": where, "clicks": cells[2], "then": cells[3] if len(cells) > 3 else "", "section": section})
    return out


def findings_for(md: str, names: list[str]) -> dict[str, dict[str, Any]]:
    """Per portal: the finding rows under its "# <Portal> (n)" heading, and the Outcomes rows that name it."""
    out: dict[str, dict[str, Any]] = {n: {"rows": [], "outcomes": []} for n in names}
    heading, body = None, []
    sections: list[tuple[str, str]] = []
    for line in md.splitlines():
        if line.startswith("# "):
            if heading is not None:
                sections.append((heading, "\n".join(body)))
            heading, body = line[2:].strip(), []
        else:
            body.append(line)
    if heading is not None:
        sections.append((heading, "\n".join(body)))
    for head, text in sections:
        lower = head.lower()
        if lower.startswith("outcomes"):
            for cells in _rows(text):
                for n in names:
                    if any(_mentions(c, n) for c in cells[:2]):
                        out[n]["outcomes"].append({"cells": cells})
            continue
        for n in names:
            if _mentions(head, n):
                for cells in _rows(text):
                    if len(cells) >= 4 and cells[0] and not cells[0].lower().startswith("#"):
                        out[n]["rows"].append({"id": cells[0], "type": cells[1], "live": cells[2], "fix": cells[3],
                                               "needs": cells[4] if len(cells) > 4 else ""})
                break
    return out


def load(js: Path) -> dict[str, Any]:
    js = Path(js)
    inv = js / "state" / "recruiter" / "portal-inventory.yaml"
    if not inv.is_file():
        return {"read": False, "error": "state/recruiter/portal-inventory.yaml not found", "portals": [], "counts": None}
    data = yaml.safe_load(inv.read_text(encoding="utf-8")) or {}
    raw = data.get("portals") if isinstance(data, dict) else data
    raw = raw or []
    names = [str(p.get("portal") or "") for p in raw]
    clicks_file = _newest(js / "state" / "recruiter", "*-clicks-*.md")
    find_file = _newest(js / "state" / "recruiter" / "portal-content", "readback-findings-*.md")
    clicks = clicks_for(clicks_file.read_text(encoding="utf-8"), names) if clicks_file else {n: [] for n in names}
    finds = findings_for(find_file.read_text(encoding="utf-8"), names) if find_file else {n: {"rows": [], "outcomes": []} for n in names}
    portals = []
    for p in sorted(raw, key=lambda r: (int(r.get("priority") or 999), str(r.get("portal") or ""))):
        name = str(p.get("portal") or "")
        parts = {}
        for k in PARTS:
            v = p.get(k)
            parts[k] = str(v) if v is not None else None               # None: not recorded (never "todo")
        recorded = [v for v in parts.values() if v is not None]
        done = sum(1 for v in recorded if v in ("done", "n/a"))
        complete = bool(recorded) and all(v in ("done", "n/a") for v in parts.values())   # every part recorded and done
        updates = sorted((dict(u) for u in p.get("updates") or [] if isinstance(u, dict)),
                         key=lambda u: str(u.get("at") or ""), reverse=True)
        rf = p.get("resume_file") if isinstance(p.get("resume_file"), dict) else None
        links = []                                                   # §3 quick links: {label, url}; https or a repo path
        for l in p.get("links") or []:
            if not isinstance(l, dict) or not str(l.get("label") or "").strip() or not str(l.get("url") or "").strip():
                continue
            url = str(l["url"]).strip()
            links.append({"label": str(l["label"]).strip(), "url": url, "kind": "web" if url.startswith(("http://", "https://")) else "file"})
        portals.append({"portal": name, "priority": p.get("priority"), "kind": p.get("kind"), "url": p.get("url"), "links": links,
                        "parts": parts, "recorded": len(recorded), "done_parts": done, "complete": complete,
                        "waits_on": p.get("waits_on"), "next_step": p.get("next_step"),
                        "last_checked": str(p.get("last_checked")) if p.get("last_checked") else None,
                        "resume_file": rf, "updates": updates,
                        "clicks": clicks.get(name, []), "findings": finds.get(name, {}).get("rows", []),
                        "outcomes": finds.get(name, {}).get("outcomes", [])})
    in_progress = next((x["portal"] for x in portals if not x["complete"]), None)
    return {"read": True, "updated": str(data.get("updated")) if isinstance(data, dict) and data.get("updated") else None,
            "source": "state/recruiter/portal-inventory.yaml",
            "clicks_file": f"state/recruiter/{clicks_file.name}" if clicks_file else None,
            "findings_file": f"state/recruiter/portal-content/{find_file.name}" if find_file else None,
            "portals": portals,
            "counts": {"total": len(portals), "complete": sum(1 for x in portals if x["complete"]),
                       "waits_owner": sum(1 for x in portals if x["waits_on"] == "owner"),
                       "waits_recruiter": sum(1 for x in portals if x["waits_on"] == "recruiter"),
                       "in_progress": in_progress}}
