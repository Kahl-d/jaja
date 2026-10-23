#!/usr/bin/env python3
"""card_audit.py - a read-only audit of the department's specialists: each role, lane and skill card should be
valid, correct and free of bloat.

Files: roles/*.md (archived cards skipped), lanes/*/AGENT.md, .agents/skills/*/SKILL.md. Per file:
  bloat       a card (role or lane) over CARD_KB, a skill over SKILL_KB; lines and KB are always reported
  path        a backticked repo path (with an extension, a trailing slash, or a first folder that exists) or any
              scripts/<name>.py that resolves under neither the department root, the OS root nor the file's folder;
              placeholders (<slug>, *, {x}, $VAR, SLUG, V/, COMPANY), slash commands and URLs are skipped; a missing
              non-script path whose folder exists is listed as an output not yet created, not counted
  ruling      a cited ruling id (shape: policy.ruling_id_pattern) missing from hub/rulings.yaml, or one whose status is superseded or history (the
              superseding id printed when the record names one)
  skill       a name in frontmatter `skills:` with no .agents/skills/<name>/ here and no system/plugin/skills/<name>/
  handoff     frontmatter reports_to / handoff(s) naming no known role or lane; any mention of an archived role
              (a retired role)
  eval        role cards only: no evals/<role>/cases.yaml, or under 3 cases; the date of the last
              results/<date>-*.json is reported (never run is shown, not counted)
  stale-name  hub/APPLY-QUEUE.md (now hub/NEXT-UP.md), TRACKER.md, progress.md

Output: a table with a FINDINGS count per file, then the findings; --json for the same data. Exit 0 clean, 1 findings.

CLI:  python3 scripts/card_audit.py [--root JS] [--os-root OS] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from js_common import JS_ROOT, os_root_for  # noqa: E402
from agency_paths import CODE_ROOT, DATA_ROOT  # noqa: E402
import agency_config as agency_cfg  # noqa: E402

CARD_KB, SKILL_KB = 12.0, 6.0
EXTS = (".md", ".py", ".yaml", ".yml", ".json", ".jsonl", ".txt", ".csv", ".toml", ".sh", ".html", ".pdf", ".base")
STALE = {"hub/APPLY-QUEUE.md": "now hub/NEXT-UP.md", "TRACKER.md": "archived", "progress.md": "archived"}
TICK = re.compile(r"`([^`\n]+)`")
SCRIPT = re.compile(r"(?<![\w/.-])(scripts/[\w-]+\.py)\b")
KID = re.compile(r"(?<![\w-])(" + str(agency_cfg.get("policy.ruling_id_pattern", r"[A-Z]{1,4}-\d+")) + r"[a-z]?)\b")
PLACEHOLDER = re.compile(r"[<>*{}$]|\.\.\.|^https?:|://")
PLACE_SEG = {"SLUG", "COMPANY", "ROLE", "ID", "NAME", "DATE", "V", "JS", "X", "N", "YYYY-MM-DD", "OS"}


def _front(text: str) -> dict[str, Any]:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    try:
        return yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}


def _list(v: Any) -> list[str]:
    if v is None:
        return []
    return [str(x) for x in (v if isinstance(v, list) else [v]) if str(x).strip()]


def files(root: Path) -> list[tuple[str, Path]]:
    out = [("role", p) for p in sorted((root / "agents").glob("*.md"))
           if str(_front(p.read_text(encoding="utf-8")).get("status") or "") != "archived"]
    out += [("lane", p) for p in sorted((root / "lanes").glob("*/AGENT.md"))]
    out += [("skill", p) for p in sorted((root / "skills").glob("*/SKILL.md"))]
    return out


def rulings(root: Path) -> dict[str, dict[str, Any]]:
    p = (root if (root / "hub").is_dir() else DATA_ROOT) / "hub" / "rulings.yaml"
    data = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.is_file() else {}
    return {str(r.get("id")): r for r in data.get("rulings") or []}


def known_roles(root: Path, os_root: Path) -> tuple[set[str], set[str]]:
    live, archived = set(), set()
    for p in (root / "agents").glob("*.md"):
        (archived if str(_front(p.read_text(encoding="utf-8")).get("status") or "") == "archived" else live).add(p.stem)
    for d in (root / "lanes").glob("*/AGENT.md"):
        live |= {d.parent.name, f"lane-{d.parent.name}", str(_front(d.read_text(encoding="utf-8")).get("name") or "")}
    return live - {""}, archived


def _path_candidates(text: str) -> set[str]:
    out = set(SCRIPT.findall(text))
    for span in TICK.findall(text):
        toks = span.split()
        if not toks:
            continue
        cand = toks[1] if toks[0] in ("python3", "python", "bash", "sh", "cat", "open") and len(toks) > 1 else toks[0]
        cand = cand.rstrip(".,;:)")
        if PLACEHOLDER.search(cand) or "/" not in cand.rstrip("/"):
            continue
        if not re.fullmatch(r"[~\w.@-]*(/[\w.@-]+)+/?", cand):
            continue
        segs = [x for x in cand.strip("/").split("/") if x]
        if cand.startswith("/") and len(segs) == 1:
            continue                                            # a slash command (/role-package) or a URL piece
        if any(x.split(".")[0] in PLACE_SEG or re.fullmatch(r"[A-Z]-[A-Z]", x.split(".")[0]) for x in segs):
            continue                                            # applications/SLUG/jd.md, V/cover.md, D-X.md
        out.add(cand)
    return out


def _resolves(cand: str, roots: list[Path]) -> bool:
    if cand.startswith(("/", "~")):
        return Path(cand).expanduser().exists()
    return any((r / cand).exists() for r in roots)


def _looks_like_path(cand: str, roots: list[Path]) -> bool:
    if cand.startswith(("/", "~")) or cand.endswith("/") or cand.lower().endswith(EXTS):
        return True
    first = cand.split("/", 1)[0]
    return any((r / first).is_dir() for r in roots)


def audit_file(kind: str, path: Path, root: Path, os_root: Path, rules: dict[str, dict[str, Any]],
               roles: set[str], archived: set[str]) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    fm = _front(text)
    size = len(text.encode("utf-8"))
    kb = round(size / 1024, 1)
    findings: list[dict[str, str]] = []

    def add(k: str, msg: str) -> None:
        findings.append({"kind": k, "msg": msg})

    cap = SKILL_KB if kind == "skill" else CARD_KB
    if size / 1024 > cap:
        add("bloat", f"{kb} KB, over the {cap:g} KB {'skill' if kind == 'skill' else 'card'} cap")
    roots = [root, *([DATA_ROOT] if root.resolve() == CODE_ROOT.resolve() else []), os_root, path.parent]
    outputs: list[str] = []
    for cand in sorted(_path_candidates(text)):
        if _looks_like_path(cand, roots) and not _resolves(cand, roots):
            parent = cand.rstrip("/").rsplit("/", 1)[0]
            if not cand.endswith(".py") and _resolves(parent + "/", roots):
                outputs.append(cand)                            # its folder exists: an output the role writes
            else:
                add("path", f"{cand} does not exist (department root, OS root or the file's folder)")
    for kid in sorted(set(KID.findall(text)), key=lambda k: (int(re.sub(r"\D", "", k) or 0), k)):
        r = rules.get(kid)
        if r is None:
            add("ruling", f"{kid} is not in hub/rulings.yaml")
        elif str(r.get("status")) in ("superseded", "history"):
            by = r.get("superseded_by")
            add("ruling", f"{kid} is {r.get('status')}" + (f", superseded by {by}" if by else ""))
    for s in _list(fm.get("skills")):
        if not ((root / "skills" / s).is_dir()):
            add("skill", f"skill {s} does not exist")
    for key in ("reports_to", "handoff", "handoffs", "hands_off_to"):
        for name in _list(fm.get(key)):
            if name not in roles:
                add("handoff", f"{key}: {name} names no known role or lane" + (" (archived)" if name in archived else ""))
    body = text[text.find("\n---\n", 4) + 5:] if text.startswith("---\n") else text
    for name in sorted(archived):
        if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", body):
            add("handoff", f"names the archived role {name}")
    eval_cases = eval_last = None
    if kind == "role":
        ev = root / "evals" / path.stem
        cases_file = ev / "cases.yaml"
        if not (root / "evals").is_dir():
            pass                                            # evals are opt-in: no evals/ folder, no eval finding
        elif not cases_file.is_file():
            add("eval", f"no eval set (evals/{path.stem}/cases.yaml)")
        else:
            data = yaml.safe_load(cases_file.read_text(encoding="utf-8")) or {}
            eval_cases = len(data.get("cases") or [])
            if eval_cases < 3:
                add("eval", f"eval set has {eval_cases} case(s), under 3")
            dates = sorted(m.group(0) for p in (ev / "results").glob("*.json")
                           if (m := re.match(r"\d{4}-\d{2}-\d{2}", p.name)))
            eval_last = dates[-1] if dates else "never run"
    for name, note in STALE.items():
        pat = rf"(?<![\w/-]){re.escape(name)}" if "/" in name else rf"(?<![\w/.-]){re.escape(name)}"
        if re.search(pat, text):
            add("stale-name", f"names {name} ({note})")
    return {"file": path.relative_to(root).as_posix(), "kind": kind, "lines": text.count("\n") + (not text.endswith("\n")),
            "kb": kb, "eval_cases": eval_cases, "eval_last_run": eval_last, "findings": findings,
            "outputs_not_yet_created": outputs}


def audit(root: Path, os_root: Path) -> list[dict[str, Any]]:
    rules = rulings(root)
    roles, archived = known_roles(root, os_root)
    return [audit_file(k, p, root, os_root, rules, roles, archived) for k, p in files(root)]


def render(rows: list[dict[str, Any]]) -> str:
    total = sum(len(r["findings"]) for r in rows)
    kinds: dict[str, int] = {}
    for r in rows:
        for f in r["findings"]:
            kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
    L = [f"# Card audit · {len(rows)} files · FINDINGS {total}"
         + (" (" + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())) + ")" if kinds else ""), "",
         "| file | kind | lines | KB | eval (cases · last run) | FINDINGS |", "|---|---|---|---|---|---|"]
    for r in rows:
        ev = f"{r['eval_cases']} · {r['eval_last_run']}" if r["eval_cases"] is not None else ("none" if r["kind"] == "role" else "-")
        L.append(f"| {r['file']} | {r['kind']} | {r['lines']} | {r['kb']} | {ev} | {len(r['findings'])} |")
    L += ["", "## Findings"]
    for r in rows:
        if r["findings"]:
            L.append(f"- {r['file']}")
            L += [f"  - {f['kind']}: {f['msg']}" for f in r["findings"]]
    outs = [(r["file"], o) for r in rows for o in r.get("outputs_not_yet_created") or []]
    if outs:
        L += ["", f"## Named outputs not yet created ({len(outs)}; their folder exists; listed, not counted)"]
        L += [f"- {f}: {o}" for f, o in outs]
    return "\n".join(L).replace("\u2014", "-") + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=CODE_ROOT, help="the code root (agents/, lanes/, skills/)")
    ap.add_argument("--os-root", type=Path, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    os_root = args.os_root or os_root_for(DATA_ROOT)
    rows = audit(args.root, os_root)
    total = sum(len(r["findings"]) for r in rows)
    if args.json:
        print(json.dumps({"files": rows, "findings": total}, indent=2))
    else:
        print(render(rows), end="")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
