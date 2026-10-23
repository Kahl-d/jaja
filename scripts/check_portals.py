#!/usr/bin/env python3
"""check_portals.py - the agency's portal setup is done only when this exits 0.

    python3 scripts/check_portals.py [--root JS] [--today YYYY-MM-DD] [--max-age-days 30] [--json]

Reads state/recruiter/portal-inventory.yaml (writer: the recruiter) and state/recruiter/<owner token>-setup-list.md (the
one list of what only the owner can do; the owner token is identity.owner_token in the config, `owner` by default). A row is a portal, firm or network. Missing is not empty, so every check below
names the row and the field:
  - the file parses strictly: a key twice in one mapping is an error (YAML keeps the last value silently);
  - every row has portal, priority, kind, url, waits_on, next_step and a last_checked date within --max-age-days;
  - every row states all nine setup fields (account, profile, resume, skills, alerts, settings, messaging,
    applications, tools), each one of done | partial | todo | his-hand | n/a;
  - an n/a field gives its reason in the row's `na` mapping ({field: reason});
  - a row with any field still open (partial, todo, his-hand) names who it waits on (owner, recruiter or time)
    and never "nobody"; a row with nothing open waits on nobody (the owner is named by the owner token);
  - resume: done carries resume_file {path, sha256}; the file exists under the root and its sha256 matches, so a
    portal is proven to hold the file we think it holds (and drifts the day the base changes);
  - every row that waits on the owner is named in the owner's setup list;
  - every row names `sign_in` (google-sso | email | linkedin | indeed-login | unknown | none: the method, never a
    credential) and `registry` (its targets/platforms.yaml platform or presence_surfaces id, or none with na.registry);
  - one presence record: this inventory is the source of truth for setup state and the registry's
    `presence:` block is checked against it, so the job-search MCP and portal_report never serve a stale value:
      account  inventory done <=> registry presence.account.state exists (exists-inferred or unknown is a disagreement);
      alerts   inventory done <=> registry presence.alerts.state on or arriving;
      resume   inventory done => registry presence.resume.sha256 equals resume_file.sha256, and any sha prefix (8+ hex)
               written in presence.resume.uploaded is a prefix of it; inventory not done => no presence.resume.sha256;
               and a "base <n>" there names presence_base_resume's iteration when the file is that base;
      sign_in  registry presence.account.sign_in, when written, equals the inventory's;
    and every registry presence whose account is exists or exists-inferred (a portal, not a website, code or research
    surface) is named by an inventory row's `registry`.
Exit 0 clean, 1 on any problem, 2 on a usage error. Deterministic, read-only, no network.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as cfg  # noqa: E402
from js_common import JS_ROOT  # noqa: E402

import yaml  # noqa: E402

INVENTORY = Path("state/recruiter/portal-inventory.yaml")
REGISTRY = Path("targets/platforms.yaml")
# The owner's token in waits_on, and the name of the owner's setup list.
OWNER = str(cfg.get("identity.owner_token", "owner")).strip().lower()
SETUP_LIST = Path(f"state/recruiter/{OWNER}-setup-list.md")
FIELDS = ("account", "profile", "resume", "skills", "alerts", "settings", "messaging", "applications", "tools")
VALUES = {"done", "partial", "todo", "his-hand", "n/a", "unknown"}
OPEN = {"partial", "todo", "his-hand", "unknown"}            # unknown: not proven yet, so still open
SIGN_IN = {"google-sso", "email", "linkedin", "indeed-login", "unknown", "none"}   # the method only, never a credential
REG_ACCOUNT_EXISTS = {"exists", "exists-inferred"}
REG_ALERTS_ON = {"on", "arriving"}
NOT_PORTAL_KINDS = {"website", "code", "research"}           # presence surfaces that are not portals (portal_report)
HEX = re.compile(r"\b[0-9a-f]{8,64}\b")
WAITS = {OWNER, "recruiter", "time", "nobody"}
REQUIRED = ("portal", "priority", "kind", "url", "waits_on", "next_step", "last_checked", "sign_in", "registry")
SHA = re.compile(r"^[0-9a-f]{64}$")


class StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses a key twice in one mapping."""


def _mapping(loader: StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    seen: dict[Any, int] = {}
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r} (first on line {seen[key] + 1})", key_node.start_mark)
        seen[key] = key_node.start_mark.line
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def registry_presence(root: Path) -> tuple[dict[str, dict[str, Any]], set[str], str | None]:
    """({id: presence block} for every registry platform with one and every presence surface, every id in the file,
    a load error or None). The file's presence_base_resume rides along under the key "-base"."""
    path = root / REGISTRY
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}, set(), f"{REGISTRY} is missing"
    except yaml.YAMLError as exc:
        return {}, set(), f"{REGISTRY} does not parse: {exc}"
    presence: dict[str, dict[str, Any]] = {}
    ids: set[str] = set()
    for key in ("platforms", "presence_surfaces", "candidates", "blocked_tools"):
        for e in data.get(key) or []:
            if isinstance(e, dict) and e.get("id"):
                ids.add(str(e["id"]))
    for e in data.get("platforms") or []:
        if isinstance(e, dict) and isinstance(e.get("presence"), dict):
            presence[str(e.get("id"))] = e["presence"]
    for e in data.get("presence_surfaces") or []:
        if isinstance(e, dict) and e.get("id"):
            presence[str(e["id"])] = e
    if isinstance(data.get("presence_base_resume"), dict):
        presence["-base"] = data["presence_base_resume"]
    return presence, ids, None


def presence_problems(row: dict[str, Any], pres: dict[str, Any], base: dict[str, Any] | None = None
                      ) -> list[tuple[str, str]]:
    """Every disagreement between one inventory row and its registry presence block, as (field, problem). `base` is
    the registry's presence_base_resume {iteration, pdf_sha256}: a portal holding that file says "base <iteration>"."""
    out: list[tuple[str, str]] = []
    reg = str(row.get("registry"))
    acc_inv = str(row.get("account") or "")
    acc_reg = str((pres.get("account") or {}).get("state") or "unknown")
    if (acc_inv == "done") != (acc_reg == "exists"):
        if acc_inv == "done" or acc_reg in REG_ACCOUNT_EXISTS:
            out.append(("account", f"inventory says {acc_inv}, {REGISTRY} {reg} presence.account.state says {acc_reg}"))
    al_inv = str(row.get("alerts") or "")
    al_reg = str((pres.get("alerts") or {}).get("state") or "unknown")
    if (al_inv == "done") != (al_reg in REG_ALERTS_ON):
        out.append(("alerts", f"inventory says {al_inv}, {REGISTRY} {reg} presence.alerts.state says {al_reg}"))
    resume = pres.get("resume") if isinstance(pres.get("resume"), dict) else {}
    reg_sha = str(resume.get("sha256") or "").lower()
    rf = row.get("resume_file") if isinstance(row.get("resume_file"), dict) else {}
    inv_sha = str(rf.get("sha256") or "").lower()
    if row.get("resume") == "done":
        if reg_sha != inv_sha:
            out.append(("resume", f"inventory holds {inv_sha[:8] or 'no sha'}, {REGISTRY} {reg} presence.resume.sha256 "
                                  f"says {reg_sha[:8] or 'nothing'}"))
        for token in HEX.findall(str(resume.get("uploaded") or "").lower()):
            if inv_sha and not inv_sha.startswith(token):
                out.append(("resume", f"{REGISTRY} {reg} presence.resume.uploaded names sha {token}, the inventory "
                                      f"holds {inv_sha[:8]}"))
        base = base or {}
        if inv_sha and inv_sha == str(base.get("pdf_sha256") or "").lower() and base.get("iteration") is not None:
            for said in re.findall(r"\bbase (\d+)\b", str(resume.get("uploaded") or "").lower()):
                if said != str(base.get("iteration")):
                    out.append(("resume", f"{REGISTRY} {reg} presence.resume.uploaded says base {said}; the file is "
                                          f"base {base.get('iteration')} (presence_base_resume)"))
    elif reg_sha:
        out.append(("resume", f"inventory says {row.get('resume')}, {REGISTRY} {reg} presence.resume.sha256 claims "
                              f"{reg_sha[:8]}"))
    sign_reg = (pres.get("account") or {}).get("sign_in")
    if sign_reg not in (None, "") and str(sign_reg) != str(row.get("sign_in")):
        out.append(("sign_in", f"inventory says {row.get('sign_in')}, {REGISTRY} {reg} presence.account.sign_in says "
                               f"{sign_reg}"))
    return out


def check(root: Path, today: date, max_age_days: int = 30) -> dict[str, Any]:
    """Every problem as {portal, field, problem}; plus counts."""
    problems: list[dict[str, str]] = []

    def bad(portal: str, field: str, problem: str) -> None:
        problems.append({"portal": portal, "field": field, "problem": problem})

    inv_path = root / INVENTORY
    try:
        data = yaml.load(inv_path.read_text(encoding="utf-8"), Loader=StrictLoader)
    except FileNotFoundError:
        return {"problems": [{"portal": "-", "field": "-", "problem": f"{INVENTORY} is missing"}], "rows": 0}
    except yaml.YAMLError as exc:
        return {"problems": [{"portal": "-", "field": "-", "problem": f"{INVENTORY} does not parse: {exc}"}],
                "rows": 0}
    rows = (data or {}).get("portals") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        return {"problems": [{"portal": "-", "field": "portals", "problem": "no portals list"}], "rows": 0}

    setup_path = root / SETUP_LIST
    setup_text = setup_path.read_text(encoding="utf-8").lower() if setup_path.exists() else None
    if setup_text is None:
        bad("-", "-", f"{SETUP_LIST} is missing: the owner's items must be listed in one place")

    presence, reg_ids, reg_error = registry_presence(root)
    if reg_error:
        bad("-", "registry", reg_error)
    named: set[str] = set()
    names: set[str] = set()
    for row in rows:
        portal = str(row.get("portal") or "?") if isinstance(row, dict) else "?"
        if not isinstance(row, dict):
            bad(portal, "-", "row is not a mapping")
            continue
        if portal in names:
            bad(portal, "portal", "named twice")
        names.add(portal)
        for key in REQUIRED:
            if row.get(key) in (None, ""):
                bad(portal, key, "missing")
        checked = _date(row.get("last_checked"))
        if row.get("last_checked") not in (None, "") and checked is None:
            bad(portal, "last_checked", f"not a date: {row.get('last_checked')!r}")
        elif checked and (today - checked) > timedelta(days=max_age_days):
            bad(portal, "last_checked", f"{checked} is older than {max_age_days} days: read the portal again")
        na = row.get("na") if isinstance(row.get("na"), dict) else {}
        states: dict[str, str] = {}
        for field in FIELDS:
            value = row.get(field)
            if value in (None, ""):
                bad(portal, field, "missing: state it (n/a with a reason when it does not apply)")
                continue
            value = str(value)
            if value not in VALUES:
                bad(portal, field, f"{value!r} is not one of {sorted(VALUES)}")
                continue
            states[field] = value
            if value == "n/a" and not str(na.get(field) or "").strip():
                bad(portal, field, "n/a without a reason in `na`")
        waits = str(row.get("waits_on") or "")
        if waits and waits not in WAITS:
            bad(portal, "waits_on", f"{waits!r} is not one of {sorted(WAITS)}")
        open_fields = sorted(f for f, v in states.items() if v in OPEN)
        if open_fields and waits == "nobody":
            bad(portal, "waits_on", f"nobody, but {', '.join(open_fields)} still open")
        if not open_fields and waits in {OWNER, "recruiter", "time"} and len(states) == len(FIELDS):
            bad(portal, "waits_on", f"{waits}, but nothing is open")
        if states.get("resume") == "done":
            rf = row.get("resume_file") if isinstance(row.get("resume_file"), dict) else {}
            path, sha = str(rf.get("path") or ""), str(rf.get("sha256") or "").lower()
            if not path or not SHA.match(sha):
                bad(portal, "resume_file", "resume done needs resume_file {path, sha256}")
            else:
                file = root / path
                if not file.is_file():
                    bad(portal, "resume_file", f"{path} does not exist")
                elif hashlib.sha256(file.read_bytes()).hexdigest() != sha:
                    bad(portal, "resume_file", f"{path} no longer matches the recorded sha256 (the file changed)")
        if waits == OWNER and setup_text is not None and portal.lower() not in setup_text:
            bad(portal, "waits_on", f"waits on {OWNER} but is not named in {SETUP_LIST}")
        sign = row.get("sign_in")
        if sign not in (None, "") and str(sign) not in SIGN_IN:
            bad(portal, "sign_in", f"{sign!r} is not one of {sorted(SIGN_IN)} (the method only, never a credential)")
        reg = str(row.get("registry") or "")
        if reg == "none":
            if not str(na.get("registry") or "").strip():
                bad(portal, "registry", "none without a reason in `na`")
        elif reg and not reg_error:
            if reg not in reg_ids:
                bad(portal, "registry", f"{reg!r} is not an id in {REGISTRY}")
            named.add(reg)
            if reg in presence:
                for field, problem in presence_problems(row, presence[reg], presence.get("-base")):
                    bad(portal, field, f"disagrees: {problem} (fix the stale side; the inventory is the source)")
    for pid, pres in sorted(presence.items()):
        if pid == "-base":
            continue
        kind = str(pres.get("kind") or "")
        state = str((pres.get("account") or {}).get("state") or "")
        if state in REG_ACCOUNT_EXISTS and kind not in NOT_PORTAL_KINDS and pid not in named:
            bad(pid, "registry", f"{REGISTRY} presence says account {state} but no inventory row names registry {pid}")
    return {"rows": len(rows), "problems": problems}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--today", default=None, help="YYYY-MM-DD (default: the system date)")
    ap.add_argument("--max-age-days", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        today = date.fromisoformat(args.today) if args.today else date.today()
    except ValueError:
        print(f"--today: not a date: {args.today!r}", file=sys.stderr)
        return 2
    result = check(args.root, today, args.max_age_days)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for p in result["problems"]:
            print(f"{p['portal']}: {p['field']}: {p['problem']}")
        portals = len({p["portal"] for p in result["problems"]})
        print(f"{result['rows']} rows, {len(result['problems'])} problems in {portals} rows")
    return 1 if result["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
