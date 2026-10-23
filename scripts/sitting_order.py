#!/usr/bin/env python3
"""sitting_order.py: the ONBOARDING sittings ranked by expected interviews per hour of the owner's time.

The agency's one goal is interviews, as many and as fast as possible. A portal sitting costs the owner minutes; this
ranks the sittings in state/portal-sittings/INDEX.md by the interviews a week each one is expected to add, per hour of
the owner's time. Gmail goes first whatever its score: every alert sitting reaches the agency through it (a gate).

Per platform (the sheet's file name is its id in targets/platforms.yaml), interviews a week =
    roles a week x queue share x the cold rate      (alerts and searches: roles we then apply to through the ATS)
  + people reaching in a week x the warm rate       (founders, recruiter firms, talent networks, LinkedIn recruiters)
  + cold applications made warm a week x (warm - cold)   (the LinkedIn export names a person at the company)
The rates are measured counts, from interviews.rates. Roles a week
are measured from platform_yield (new records in the last 30 days) once a platform has 10 or more, else a STATED
prior by kind. The queue share is shrunk the same way, (queued + 10 x 0.05) / (new + 10). People reaching in are a
STATED prior by kind (no platform records its intros yet). Every prior is a constant below, named in
the output.
A sitting's score is the sum over its sheets, divided by its minutes, times 60. "3 each" minutes (Workday) count each
dream or good Workday target in targets/companies.yaml.
Usage: python3 scripts/sitting_order.py [--write] [--json]
--write saves state/portal-sittings/ORDER.md. Read only otherwise. No network.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agency_config as cfg  # noqa: E402
from agency_paths import DATA_ROOT as JS_ROOT  # noqa: E402
LA = ZoneInfo(str(cfg.get("identity.timezone", "UTC") or "UTC"))   # the owner's clock
GATES = {"gmail": "every alert sitting reaches the agency through Gmail"}
MEASURED_MIN = 10            # new records in the window before a platform's own roles a week count
QUEUE_PRIOR = 0.05           # STATED: 1 in 20 roles from a targeted alert search reaches the queue
# STATED priors: roles a week a platform's alert or search surfaces, by kind
ROLES_PRIOR = {"job-board": 3.0, "search": 5.0, "aggregator": 3.0, "big-company": 1.0, "alumni-board": 1.0,
               "founder-matching": 2.0, "profile-network": 5.0, "network": 1.0}
# STATED priors: people who start a conversation a week once the profile is live, by kind
INBOUND_PRIOR = {"founder-matching": 0.5, "profile-network": 1.0, "recruiter-firm": 0.2, "talent-network": 0.2,
                 "ai-intro": 0.5, "employer-reach": 0.3, "network": 0.1}
# STATED: applications a week a sitting moves from cold to warm (the LinkedIn data export feeds network_map,
# so a role gets a named person); worth (warm - cold) each
WARM_UPLIFT = {"linkedin": 1.0}
# kinds for sheets whose platform entry carries no presence.kind
KIND = {"indeed-connector": "search", "ziprecruiter-connector": "search", "dice-connector": "search",
        "adzuna": "aggregator", "google-careers": "big-company", "google-jobs-search": "big-company",
        "meta-careers": "big-company", "amazon-jobs": "big-company", "workday-candidate-home": "big-company",
        "talent-communities": "big-company", "mygreenhouse": "job-board", "vc-portfolio": "job-board",
        "job-newsletters": "job-board", "hackajob": "employer-reach", "x": "network"}
KIND.update({str(k): str(v) for k, v in (cfg.get("policy.platforms.sitting_kinds", {}) or {}).items()})   # more sheet ids -> kind
DONE_STATES = {"alert set", "tracker linked", "no sitting", "token ok"}   # a sheet in one of these needs no sitting
ROW = re.compile(r"^\|\s*(\d+)\s*\|([^|]+)\|([^|]+)\|([^|]+)\|([^|]*)\|\s*$")


def _yaml(path: Path) -> Any:
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None


def _workday_targets(root: Path) -> int:
    data = _yaml(root / "targets" / "companies.yaml") or {}
    rows = data.get("companies") if isinstance(data, dict) else data
    return sum(1 for c in rows or [] if isinstance(c, dict) and c.get("ats_vendor") == "workday"
               and c.get("tier") in ("dream", "good"))


def sittings(root: Path) -> list[dict[str, Any]]:
    """The INDEX table's rows: number, name, sheet ids, minutes (with "N each" expanded), why."""
    out = []
    for line in (root / "state" / "portal-sittings" / "INDEX.md").read_text(encoding="utf-8").splitlines():
        m = ROW.match(line)
        if not m:
            continue
        sheets = re.findall(r"([a-z0-9-]+)\.md", m.group(3))
        mins = m.group(4).strip()
        n = int(re.match(r"\d+", mins).group()) if re.match(r"\d+", mins) else 5
        if "each" in mins:
            n *= max(1, _workday_targets(root))
        out.append({"index_no": int(m.group(1)), "name": m.group(2).strip(), "sheets": sheets, "minutes": n,
                    "why": m.group(5).strip()})
    return out


ALERTS_ARRIVING = {"tracker linked", "profile unverified", "export pending"}   # the alerts already reach us
FIRST_BLOCK_MIN = 75


def platform_value(pid: str, entry: dict[str, Any], y: dict[str, int], rates: dict[str, float],
                   days: int = 30, state: str = "") -> dict[str, Any]:
    """Expected interviews a week the sitting adds for one sheet. A manual-only sheet adds nothing (no setup step); a
    sheet whose alerts already arrive adds no roles (only its profile, people reaching in, is still to win)."""
    kind = (entry.get("presence") or {}).get("kind") if isinstance(entry.get("presence"), dict) else None
    kind = kind or entry.get("kind") or KIND.get(pid) or "job-board"
    new, queued = int(y.get("new") or 0), int(y.get("queued") or 0)
    measured = new >= MEASURED_MIN
    roles = round(new * 7 / days, 2) if measured else ROLES_PRIOR.get(kind, 0.0)
    share = (queued + 10 * QUEUE_PRIOR) / (new + 10)
    inbound = INBOUND_PRIOR.get(kind, 0.0)
    uplift = WARM_UPLIFT.get(pid, 0.0)
    if state == "manual only":
        roles = inbound = uplift = 0.0
    elif state in ALERTS_ARRIVING:
        roles = 0.0
    from_roles = roles * share * rates["cold"]
    value = from_roles + inbound * rates["warm"] + uplift * max(0.0, rates["warm"] - rates["cold"])
    # the measured part: roles a week from the platform's own records (its queue share and the rate stay estimates)
    prior = value - (from_roles if measured else 0.0)
    return {"id": pid, "kind": kind, "source": "measured" if measured else "prior", "roles_wk": roles,
            "queue_share": share, "inbound_wk": inbound, "warm_uplift_wk": uplift, "state": state,
            "interviews_wk": round(value, 4), "prior_wk": round(prior, 4)}


def read_status(root: Path) -> dict[str, str]:
    """Each sheet's state: portal_sitting.status (H2, computed from the records) for the platforms; Gmail from its read
    token (ok while 2+ days remain); LinkedIn stays open until the data export is parsed (the sitting requests it)."""
    import portal_sitting
    st = dict(portal_sitting.status(root))
    try:
        import gmail_auth
        tok = gmail_auth.token_status("read")
        ok = tok.get("exists") and not tok.get("needs_reauth") and (tok.get("days_left") or 0) >= 2
        st["gmail"] = "token ok" if ok else "to create"
    except Exception:  # noqa: BLE001  (no token store on this machine: the sitting stays open)
        st["gmail"] = "to create"
    import network_map
    if st.get("linkedin") in DONE_STATES and network_map.latest_export(root) is None:
        st["linkedin"] = "export pending"
    # a presence portal (founders, talent networks, LinkedIn) is worth its profile, which no alert proves: it stays
    # open until the sitting's closing command records `profile_filled_on` in state/accounts.yaml
    # (apply_pack.py account ... --profile-filled-on <date>)
    data = _yaml(root / "targets" / "platforms.yaml") or {}
    presence = {str(p.get("id")) for p in (data.get("platforms") if isinstance(data, dict) else data) or []
                if isinstance(p, dict) and p.get("presence")}
    if isinstance(data, dict):            # presence surfaces are profiles by definition (the writer accepts them too)
        presence |= {str(p.get("id")) for p in data.get("presence_surfaces") or [] if isinstance(p, dict)}
    acc = (_yaml(root / "state" / "accounts.yaml") or {}).get("accounts") or []
    recorded = {str(a.get("platform")) for a in acc if isinstance(a, dict) and a.get("profile_filled_on")}
    for pid in presence:
        if st.get(pid) in ("alert set", "tracker linked") and pid not in recorded:   # "no sitting" stays as it is
            st[pid] = "profile unverified"
    return st


def order(root: Path, rates: dict[str, float] | None = None, yields: dict[str, dict[str, int]] | None = None,
          days: int = 30, status: dict[str, str] | None = None) -> dict[str, Any]:
    """status: {sheet id: state} from the portal status computation; a sitting whose every sheet is done moves to
    the done block. None reads no status (every sitting is open)."""
    now = datetime.now(LA)
    if rates is None:
        import interviews
        rates = interviews.rates(root, now)
    if yields is None:
        import platform_yield
        yields = platform_yield.compute(root, now.date(), days)
    data = _yaml(root / "targets" / "platforms.yaml") or {}
    entries = {str(p.get("id")): p for p in (data.get("platforms") if isinstance(data, dict) else data) or []
               if isinstance(p, dict)}
    rows = []
    for s in sittings(root):
        parts = [platform_value(sh, entries.get(sh, {}), yields.get(sh, {}), rates, days, (status or {}).get(sh, ""))
                 for sh in s["sheets"] if sh not in GATES]
        wk = round(sum(p["interviews_wk"] for p in parts), 4)
        prior_share = sum(p["prior_wk"] for p in parts) / wk if wk else 1.0
        gate = next((GATES[sh] for sh in s["sheets"] if sh in GATES), None)
        states = {sh: (status or {}).get(sh, "") for sh in s["sheets"]}
        done = bool(s["sheets"]) and all(st in DONE_STATES for st in states.values())
        rows.append(s | {"parts": parts, "interviews_wk": wk, "gate": gate, "states": states, "done": done,
                         "prior_share": prior_share,
                         "per_hour": round(wk / s["minutes"] * 60, 4) if s["minutes"] else 0.0})
    rows.sort(key=lambda r: (r["gate"] is None, -r["per_hour"], r["index_no"]))
    for r in rows:     # every sheet manual only: the registry wants no setup unless the owner does; listed, not ranked
        r["optional"] = bool(r["sheets"]) and all(st == "manual only" for st in r["states"].values())
    open_rows = [r for r in rows if not r["done"] and not r["optional"]]
    return {"date": now.date().isoformat(), "rates": dict(rates), "days": days,
            "rows": open_rows, "done": [r for r in rows if r["done"]],
            "optional": [r for r in rows if r["optional"] and not r["done"]], "status_read": status is not None,
            "minutes": sum(r["minutes"] for r in open_rows)}


def _n(rates: dict[str, Any], side: str) -> str:
    k, n = rates.get(f"{side}_k"), rates.get(f"{side}_n")
    return f"{k} of {n}" if k is not None and n is not None else "not recorded"


def render(res: dict[str, Any]) -> str:
    r = res["rates"]
    out = [f"# Sitting order · {res['date']} (expected interviews per hour of your time)", "",
           f"Expected interview rates (interviews.py): warm {r['warm']:.0%}, cold {r['cold']:.0%} per application, from "
           f"warm {_n(r, 'warm')} and cold {_n(r, 'cold')} so far, pulled toward the overall rate. An interview is a "
           f"screen, OA, interview or final; a rejection does not count.",
           f"Roles a week are measured from the last {res['days']} days where a platform has {MEASURED_MIN}+ new "
           f"records, else a stated prior. Queue share is a platform's queued share of its own records, pulled "
           f"toward a stated prior of 1 in {round(1 / QUEUE_PRIOR)} with the weight of 10 records (1 in "
           f"{round(1 / QUEUE_PRIOR)} where a platform has none). People reaching in a week are a stated prior by kind of site.", "",
           "**How far to trust this order:** almost all of it is judgment, not measurement. The top rows rank on "
           "stated priors (how many people a week reach in, by kind of site) times a warm rate that rests on "
           f"{r.get('warm_n', 'few')} applications; the rest rank on stated priors of roles a week times a cold rate "
           f"that rests on {r.get('cold_k', 0)} interviews in {r.get('cold_n', 0)} applications. Read the top rows as \"the places where people write to you "
           "first\", the reasoning above, not a measured result. Lower down, the scores differ by less than the "
           "priors' error, so their order is mostly minutes.", "",
           "Key: [state] is the portal's status from the records (INDEX.md's key). [profile unverified] = alerts "
           "already arrive (tracker linked), but no sitting has recorded the profile (profile_filled_on in state/accounts.yaml): the sitting "
           "is for the profile, not the alerts. [export pending] = the LinkedIn data export is not parsed yet. \"was #\" is the sitting's number in INDEX.md. "
           f"Total {res['minutes']} minutes. Regenerate: `python3 scripts/sitting_order.py --write`.", "",
           "| # | sitting | minutes | expected interviews a week | per hour of sitting | basis | was # |",
           "|---|---|--:|--:|--:|---|--:|"]
    run = total = 0.0
    every = sum(row["interviews_wk"] for row in res["rows"])
    marked = False
    for i, row in enumerate(res["rows"], 1):
        basis = row["gate"] and f"gate: {row['gate']}" or "; ".join(
            f"{p['id']}" + (f" [{p['state']}]" if p["state"] else "")
            + (" not counted (manual only)" if p["state"] == "manual only" else
               f" {p['kind']} (" + ("alerts already arrive, not counted" if p["state"] in ALERTS_ARRIVING
                                    else f"measured {p['roles_wk']:g} roles/wk" if p["source"] == "measured"
                                    else f"stated prior {p['roles_wk']:g} roles/wk" if p["roles_wk"]
                                    else "no roles")
               + (f", stated prior {p['inbound_wk']:g} people/wk" if p["inbound_wk"] else "")
               + (f", stated prior {p['warm_uplift_wk']:g} cold application/wk made warm" if p["warm_uplift_wk"]
                  else "") + ")")
            for p in row["parts"])
        if not row["gate"] and row["parts"]:
            basis += ("; stated prior only" if row["prior_share"] >= 0.999 else
                      f"; mostly stated prior ({row['prior_share']:.0%} of the score)" if row["prior_share"] > 0.5
                      else "")
        out.append(f"| {i} | {row['name']} | {row['minutes']} | {row['interviews_wk']:.2g} | "
                   f"{row['per_hour']:.2g} | {basis} | {row['index_no']} |")
        run += row["minutes"]
        total += row["interviews_wk"]
        if not marked and run >= FIRST_BLOCK_MIN and i < len(res["rows"]):
            marked = True
            share = f" ({total / every:.0%})" if every else ""
            out.append(f"| | **First block ends here:** rows 1 to {i}, {run:g} minutes, {total:.2g} of {every:.2g} "
                       f"expected interviews a week{share} | | | | | |")
    if res.get("optional"):
        out += ["", "**Optional (every sheet is manual only in the registry; sit only if you want it):** "
                + "; ".join(f"{r['name']} ({r['minutes']} min)" for r in res["optional"])]
    if not res["status_read"]:
        out += ["", "Portal status not read: every sitting is listed as open."]
    elif res["done"]:
        out += ["", "**Done (no sitting needed):** " + "; ".join(
            f"{r['name']} ({', '.join(f'{k} {v}' for k, v in r['states'].items())})" for r in res["done"])]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=JS_ROOT)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = order(a.root, status=read_status(a.root))
    if a.json:
        print(json.dumps(res, indent=2, default=str))
        return 0
    text = render(res)
    if a.write:
        p = a.root / "state" / "portal-sittings" / "ORDER.md"
        p.write_text(text, encoding="utf-8")
        print(f"wrote {p.relative_to(a.root)}")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
