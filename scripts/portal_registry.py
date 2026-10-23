#!/usr/bin/env python3
"""portal_registry.py - targets/platforms.yaml is the one source per portal.

Every portal's terms, channel (`control`), alert senders, inbox senders and presence (account, profile, resume,
inbox, refresh dates) live in its targets/platforms.yaml entry; surfaces that are not job platforms (the owner's site,
GitHub, research pages, portals not yet vetted) sit in the file's top-level `presence_surfaces:`. This module is how
other scripts read them:

  tracker(root)                 the presence tracker presence_check.py and outbox.py read: {source, base_resume,
                                surfaces}; each platform entry with a `presence:` block becomes one surface (id, name,
                                url from presence or the entry, its inbox email senders = alert_senders + inbox_senders
                                + any listed in presence.inbox), then presence_surfaces. Only when the registry carries
                                no presence data at all is the legacy state/presence/portals.yaml read (test roots and
                                the transition); its stub after the move has no surfaces.
  surface_ids(root)             the ids above
  portal_for_sender(root, addr) the portal id whose alert_senders or inbox_senders name the address (bare or a From
                                value) or its registrable
                                domain (a domain covers subdomains), else None
  board_domains(root)           {id: senders} for job boards only: entries whose control is connector or api, the
                                ATS and portfolio entries in NOT_BOARDS, and recruiter firms and talent networks
                                (presence.kind in FIRM_KINDS) are left out unless boards_only=False (an ATS domain such
                                as an ATS vendor's mail domain, or a recruiter writing from a firm's own domain, must
                                not read as a job board)

Writers: add_platform(root, entry), add_candidate(root, entry),
add_blocked(root, entry). Each refuses an id already anywhere in the file, checks the fields platform_yield.py requires
(platforms) or the candidate fields, inserts a YAML block at the end of its section as text (comments survive) and
writes atomically.

Reads are stdlib plus PyYAML and the department's js_comms_util.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as cfg  # noqa: E402
import js_comms_util as cu  # noqa: E402

REGISTRY = Path("targets") / "platforms.yaml"
LEGACY = Path("state") / "presence" / "portals.yaml"
NOT_BOARDS = frozenset({"company-ats", "ats-public-feeds", "vc-portfolio",
                        *[str(x) for x in (cfg.get("policy.platforms.not_boards_extra", []) or [])]})   # more ids: policy.platforms.not_boards_extra
# +: recruiter firms and talent networks are channels (portal tags, setup, messages) but people write from
# their domains, so they are never job boards (presence.kind)
FIRM_KINDS = frozenset({"recruiter-firm", "talent-network"})


def load(root: Path) -> dict[str, Any]:
    path = Path(root) / REGISTRY
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.is_file() else {}


def _senders(entry: dict[str, Any]) -> list[str]:
    out = []
    for s in list(entry.get("alert_senders") or []) + list(entry.get("inbox_senders") or []):
        s = str(s).strip().lower()
        if s and s not in out:
            out.append(s)
    return out


def tracker(root: Path) -> dict[str, Any]:
    root = Path(root)
    reg = load(root)
    surfaces: list[dict[str, Any]] = []
    for entry in reg.get("platforms") or []:
        presence = entry.get("presence")
        if not isinstance(presence, dict):
            continue
        s = {"id": entry.get("id"), "name": entry.get("name"), **presence}
        s["url"] = presence.get("url") or entry.get("url")
        inbox = dict(presence.get("inbox") or {})
        listed = [x for x in inbox.get("email_senders") or [] if isinstance(x, dict)]
        known = {str(x.get("sender") or "").lower() for x in listed}
        inbox["email_senders"] = listed + [{"sender": a} for a in _senders(entry) if a not in known]
        s["inbox"] = inbox
        surfaces.append(s)
    surfaces += [dict(s) for s in reg.get("presence_surfaces") or [] if isinstance(s, dict)]
    if surfaces or reg.get("presence_base_resume"):
        return {"source": str(REGISTRY), "base_resume": reg.get("presence_base_resume"), "surfaces": surfaces}
    legacy = root / LEGACY
    if legacy.is_file():
        data = yaml.safe_load(legacy.read_text(encoding="utf-8")) or {}
        return {"source": str(LEGACY), "base_resume": data.get("base_resume"), "surfaces": data.get("surfaces") or []}
    if (root / REGISTRY).is_file():
        return {"source": str(REGISTRY), "base_resume": None, "surfaces": []}
    raise FileNotFoundError(f"no {REGISTRY} or {LEGACY} under {root}")


def surface_ids(root: Path) -> set[str]:
    return {str(s.get("id")) for s in tracker(root)["surfaces"] if s.get("id")}


def _matches(addr: str, sender: str) -> bool:
    if "@" in sender:
        return addr == sender
    domain = cu.email_domain(addr)
    return domain == sender or domain.endswith("." + sender) or cu.registrable_domain(domain) == sender


def portal_for_sender(root: Path, address: str) -> str | None:
    raw = str(address or "").strip()
    addr = (cu.parse_address(raw)[1] or raw).strip().lower()     # a bare address or a From value "Name <a@b>"
    if not addr:
        return None
    for entry in load(root).get("platforms") or []:
        if any(_matches(addr, s) for s in _senders(entry)):
            return str(entry.get("id"))
    return None


def board_domains(root: Path, boards_only: bool = True) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for entry in load(root).get("platforms") or []:
        pid = str(entry.get("id"))
        if boards_only and (pid in NOT_BOARDS or entry.get("control") in ("connector", "api")
                            or (entry.get("presence") or {}).get("kind") in FIRM_KINDS):
            continue
        senders = set(_senders(entry))
        if senders:
            out[pid] = senders
    return out


# --------------------------------------------------------------------------- writers
PLATFORM_FIELDS = ("id", "name", "url", "access", "adapter", "alert_senders", "tos", "agent_terms", "sponsorship_filter",
                   "account_needed", "alert_setup", "status", "yield", "last_reviewed")
CANDIDATE_FIELDS = ("id", "name", "url", "why", "access_guess", "checked_on", "next_step")
BLOCKED_FIELDS = ("id", "name", "agent_terms", "reason")


def _all_ids(data: dict[str, Any]) -> set[str]:
    out = set()
    for key in ("platforms", "candidates", "blocked_tools", "presence_surfaces"):
        out |= {str(e.get("id")) for e in data.get(key) or [] if isinstance(e, dict)}
    return out


class _NoAliasDumper(yaml.SafeDumper):
    """One value used twice in an entry (a date in tos.read_on and last_reviewed) must be written twice, never as a YAML
    anchor: two entries with the same anchor name break the file."""

    def ignore_aliases(self, data: Any) -> bool:
        return True


def _insert(root: Path, section: str, entry: dict[str, Any], required: tuple[str, ...], flow: bool = False) -> None:
    path = Path(root) / REGISTRY
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    missing = [f for f in required if f not in entry]
    if missing:
        raise ValueError(f"{entry.get('id')}: missing {', '.join(missing)}")
    if str(entry["id"]) in _all_ids(data):
        raise ValueError(f"{entry['id']}: already in {REGISTRY}")
    if flow:
        block = "  - " + yaml.dump(entry, Dumper=_NoAliasDumper, default_flow_style=True, sort_keys=False,
                                   width=10000, allow_unicode=True).strip() + "\n"
    else:
        dumped = yaml.dump([entry], Dumper=_NoAliasDumper, default_flow_style=False, sort_keys=False, width=1000,
                           allow_unicode=True)
        block = "\n" + "".join("  " + ln + "\n" for ln in dumped.splitlines())
    lines = text.splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines) if ln.rstrip() == f"{section}:"), None)
    if start is None:
        lines += ["\n", f"{section}:\n"]
        start = len(lines) - 1
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^(#|[A-Za-z_]+:)", lines[i]):
            end = i
            break
    last = end
    while last > start + 1 and not lines[last - 1].strip():
        last -= 1
    if lines[last - 1] and not lines[last - 1].endswith("\n"):
        lines[last - 1] += "\n"
    new = "".join(lines[:last]) + block + "".join(lines[last:])
    yaml.safe_load(new)                                            # the result must still parse
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, path)


def add_platform(root: Path, entry: dict[str, Any]) -> None:
    _insert(root, "platforms", entry, PLATFORM_FIELDS)


def add_candidate(root: Path, entry: dict[str, Any]) -> None:
    _insert(root, "candidates", entry, CANDIDATE_FIELDS)


def add_blocked(root: Path, entry: dict[str, Any]) -> None:
    _insert(root, "blocked_tools", entry, BLOCKED_FIELDS, flow=True)
