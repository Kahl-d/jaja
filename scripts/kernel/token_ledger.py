#!/usr/bin/env python3
"""token_ledger.py - measured token use per session, subagent type and model, read from Claude Code transcripts.

Why: without a measurement every token-efficiency claim is a guess. Claude Code already writes the
usage of every model call into the session transcript (~/.claude/projects/<dir>/<session>.jsonl, and each subagent's
calls into <session>/subagents/agent-*.jsonl with an agent-*.meta.json naming its agentType). This script sums them.
Deterministic, read-only, no network, no model.

Columns (tokens): in = uncached input, cw = cache writes, cr = cache reads, out = output (thinking included), and
ieq = an input-equivalent index (in + 1.25 x 5-minute writes + 2 x 1-hour writes + 0.1 x reads + 5 x out), the shape of
API list pricing. ieq is for comparing runs of the same model; it is not a dollar figure and not the plan's limit unit.

Usage:
  python3 scripts/kernel/token_ledger.py                          # every session of the data folder's (else this repo's) project dir
  python3 scripts/kernel/token_ledger.py --session <id>           # one session, split by subagent type
  python3 scripts/kernel/token_ledger.py --json
  python3 scripts/kernel/token_ledger.py --receipt <run-dir>      # also write tokens.json into a routine receipt dir
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COLS = ("calls", "in", "cw", "cr", "out", "ieq")


def project_dir(root: Path | None = None) -> Path:
    """Claude Code names the project dir after the session's cwd with "/" and "." replaced by "-". Lanes run in the
    data folder, so that one is tried first, then this repo."""
    def of(p: Path) -> Path:
        return Path.home() / ".claude/projects" / str(p).replace("/", "-").replace(".", "-")
    if root is not None:
        return of(root)
    sys.path.insert(0, str(ROOT / "scripts"))
    import agency_paths
    return next((d for d in (of(agency_paths.DATA_ROOT), of(ROOT)) if d.is_dir()), of(ROOT))


def usage_rows(path: Path):
    """(model, usage) once per API request. Streamed content blocks repeat the requestId; the last one carries the
    final output count, so the largest output_tokens per request wins."""
    best: dict = {}
    with path.open(errors="replace") as fh:
        for line in fh:
            if '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            m = d.get("message") or {}
            u = m.get("usage")
            if not u:
                continue
            rid = d.get("requestId") or d.get("uuid")
            if rid not in best or (u.get("output_tokens") or 0) > (best[rid][1].get("output_tokens") or 0):
                best[rid] = (m.get("model") or "?", u)
    yield from best.values()


def add(acc: dict, u: dict) -> None:
    cc = u.get("cache_creation") or {}
    w1h = cc.get("ephemeral_1h_input_tokens", 0) or 0
    w5m = cc.get("ephemeral_5m_input_tokens", 0) or 0
    cw = u.get("cache_creation_input_tokens", 0) or (w1h + w5m)
    if not (w1h or w5m):
        w5m = cw
    acc["calls"] += 1
    acc["in"] += u.get("input_tokens", 0) or 0
    acc["cw"] += cw
    acc["cr"] += u.get("cache_read_input_tokens", 0) or 0
    acc["out"] += u.get("output_tokens", 0) or 0
    acc["ieq"] += round((u.get("input_tokens", 0) or 0) + 1.25 * w5m + 2 * w1h
                        + 0.1 * (u.get("cache_read_input_tokens", 0) or 0) + 5 * (u.get("output_tokens", 0) or 0))


def ledger(pdir: Path, session: str | None = None) -> dict:
    """{(session, agent_type, model): totals}"""
    out: dict = defaultdict(lambda: dict.fromkeys(COLS, 0))
    for t in sorted(pdir.glob("*.jsonl")):
        sid = t.stem
        if session and sid != session:
            continue
        for model, u in usage_rows(t):
            add(out[(sid, "main", model)], u)
        for s in sorted((pdir / sid / "subagents").glob("agent-*.jsonl")):
            meta = s.with_suffix(".meta.json")
            try:
                kind = json.loads(meta.read_text()).get("agentType", "subagent")
            except (OSError, json.JSONDecodeError):
                kind = "subagent"
            for model, u in usage_rows(s):
                add(out[(sid, kind, model)], u)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--project-dir", type=Path, default=None)
    ap.add_argument("--session")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--receipt", type=Path, help="write tokens.json (totals by model) into this receipt dir")
    a = ap.parse_args(argv)
    pdir = a.project_dir or project_dir()
    if not pdir.is_dir():
        print(f"no transcripts at {pdir}", file=sys.stderr)
        return 1
    rows = ledger(pdir, a.session)
    total = dict.fromkeys(COLS, 0)
    for v in rows.values():
        for c in COLS:
            total[c] += v[c]
    if a.receipt:
        by_model: dict = defaultdict(lambda: dict.fromkeys(COLS, 0))
        for (_, _, model), v in rows.items():
            for c in COLS:
                by_model[model][c] += v[c]
        (a.receipt / "tokens.json").write_text(json.dumps({"by_model": by_model, "total": total}, indent=1) + "\n")
    if a.json:
        print(json.dumps([{"session": s, "agent": k, "model": m, **v} for (s, k, m), v in sorted(rows.items())]
                         + [{"session": "TOTAL", **total}], indent=1))
        return 0
    print(f"{'session':10} {'agent':26} {'model':20} " + " ".join(f"{c:>11}" for c in COLS))
    for (s, k, m), v in sorted(rows.items(), key=lambda kv: -kv[1]["ieq"]):
        print(f"{s[:8]:10} {k[:26]:26} {m[:20]:20} " + " ".join(f"{v[c]:>11,}" for c in COLS))
    print(f"{'TOTAL':58} " + " ".join(f"{total[c]:>11,}" for c in COLS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
