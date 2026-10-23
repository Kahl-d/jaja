#!/usr/bin/env python3
"""model_policy.py: every agent runs on the model and effort its kind of work needs.

The policy is roles/references/model-policy.md (its yaml blocks: kinds, roles, lanes, exceptions). This script:
  check   every role card (roles/*.md) and lane card (lanes/<lane>/AGENT.md) carries the model and effort of its kind,
          or of its written exception; a card the policy does not name is a finding too;
  pick    prints `<model> <effort>` for a kind or a role, for whoever spawns an agent; with --tier, the model the
          spawner passes at that tier (`none`: do not spawn it at that tier) and the card's effort;
  apply   sets every card's model and effort from the policy (the only writer of those two fields);
  table   prints the resolved table (with the tier columns).
Usage: python3 scripts/model_policy.py check|table|apply [--json] | pick <kind-or-role> [--tier mass|focused|dream]
Exit 0 clean, 1 findings, 2 usage error. No network.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from agency_paths import CODE_ROOT as JS_ROOT  # noqa: E402  (the cards are code: agents/, lanes/)
POLICY = JS_ROOT / "role-references" / "model-policy.md"
FM = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def load(path: Path = POLICY) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for block in re.findall(r"```yaml\n(.*?)```", path.read_text(encoding="utf-8"), re.S):
        out.update(yaml.safe_load(block) or {})
    return out


def want(policy: dict[str, Any], name: str, lane: bool = False) -> dict[str, str] | None:
    kind = (policy.get("lanes" if lane else "roles") or {}).get(name)
    if kind is None:
        return None
    row = dict(policy["kinds"][kind])
    row.update({k: v for k, v in ((policy.get("exceptions") or {}).get(name) or {}).items() if k in ("model", "effort")})
    return {"kind": kind, "model": row["model"], "effort": row["effort"]}


def cards(root: Path) -> list[tuple[str, bool, Path]]:
    out = [(p.stem, False, p) for p in sorted((root / "agents").glob("*.md"))]
    out += [(p.parent.name, True, p) for p in sorted((root / "lanes").glob("*/AGENT.md"))]
    return out


def check(root: Path = JS_ROOT, policy_path: Path = POLICY) -> list[str]:
    policy = load(policy_path)
    findings = []
    for name, lane, path in cards(root):
        m = FM.match(path.read_text(encoding="utf-8"))
        meta = yaml.safe_load(m.group(1)) if m else {}
        w = want(policy, name, lane)
        rel = path.relative_to(root)
        if w is None:
            findings.append(f"{rel}: not in the policy (add it to a kind in {policy_path.name})")
            continue
        for key in ("model", "effort"):
            have = str(meta.get(key) or "").strip()
            if have != w[key]:
                findings.append(f"{rel}: {key} {have or '(none)'}, policy {w[key]} ({w['kind']})")
    return findings


def tier_pick(policy: dict[str, Any], role: str, tier: str) -> str:
    """`<model> <effort>` for a role at a tier, or `none`. A role the tier does not name runs on its card's row."""
    base = want(policy, role) or want(policy, role, lane=True)
    tiers = policy.get("tiers") or {}
    if tier not in tiers:
        raise KeyError(f"unknown tier {tier}")
    model = (tiers[tier] or {}).get(role)
    if model == "none":
        return "none"
    if base is None:
        raise KeyError(f"unknown role {role}")
    return f"{model or base['model']} {base['effort']}"


def apply(root: Path = JS_ROOT, policy_path: Path = POLICY) -> list[str]:
    """Rewrite the model: and effort: lines of every card to the policy; returns the cards changed."""
    policy = load(policy_path)
    changed = []
    for name, lane, path in cards(root):
        w = want(policy, name, lane)
        if w is None:
            continue
        text = path.read_text(encoding="utf-8")
        m = FM.match(text)
        if not m:
            continue
        fm = m.group(1)
        new = fm
        for key in ("model", "effort"):
            if re.search(rf"(?m)^{key}:", new):
                new = re.sub(rf"(?m)^{key}:.*$", f"{key}: {w[key]}", new, count=1)
            else:
                new += f"\n{key}: {w[key]}"
        if new != fm:
            path.write_text(text.replace(fm, new, 1), encoding="utf-8")
            changed.append(str(path.relative_to(root)))
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "table", "apply"):
        s = sub.add_parser(name)
        s.add_argument("--json", action="store_true")
        s.add_argument("--root", type=Path, default=JS_ROOT)
    pk = sub.add_parser("pick")
    pk.add_argument("what")
    pk.add_argument("--tier", choices=["mass", "focused", "dream"])
    a = ap.parse_args(argv)
    policy = load()
    if a.cmd == "apply":
        for c in apply(a.root):
            print(f"set {c}")
        return 0
    if a.cmd == "pick" and a.tier:
        try:
            print(tier_pick(policy, a.what, a.tier))
        except KeyError as e:
            print(str(e), file=sys.stderr)
            return 2
        return 0
    if a.cmd == "pick":
        row = policy["kinds"].get(a.what) or want(policy, a.what) or want(policy, a.what, lane=True)
        if not row:
            print(f"unknown kind or role: {a.what}", file=sys.stderr)
            return 2
        print(f"{row['model']} {row['effort']}")
        return 0
    if a.cmd == "table":
        rows = {n: want(policy, n, lane) for n, lane, _ in cards(a.root)}
        if a.json:
            print(json.dumps(rows, indent=2))
        else:
            tiers = list((policy.get("tiers") or {}).keys())
            print(f"{'card':<32} {'kind':<10} {'model':<7} {'effort':<7} " + " ".join(f"{t:<8}" for t in tiers))
            for n, r in rows.items():
                cols = []
                for t in tiers:
                    try:
                        cols.append(tier_pick(policy, n, t).split()[0])
                    except KeyError:
                        cols.append("?")
                print(f"{n:<32} {(r or {}).get('kind', '?'):<10} {(r or {}).get('model', '?'):<7} "
                      f"{(r or {}).get('effort', '?'):<7} " + " ".join(f"{c:<8}" for c in cols))
        return 0
    findings = check(a.root)
    if a.json:
        print(json.dumps(findings, indent=2))
    else:
        for f in findings:
            print(f"FINDING {f}")
        print(f"model policy: {len(cards(a.root))} cards, {len(findings)} findings")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
