#!/usr/bin/env python3
"""blind_pairs.py: make the base comparison blind, then reveal it.

The base-comparer must judge a variant against the general base without knowing which text is the base (the base ships
unless the variant is a clear win). A comparer that can see which version is the base, from the diff or the notes, is
not blind. This script removes that:

  make    compares a variant resume.yaml with the base, per Experience role:
          - every changed bullet becomes a LINE pair, beside the base bullet it replaces: matched by shared evidence
            ids, then text similarity, never by position alone; a line only one page has is a
            ONE-SIDED pair (the line on a random side beside "(no line here)"; added or removed is in the key only);
          - a role whose bullets come in a different order becomes an ORDER pair (both full orders), also when some
            bullets were reworded as well (then the LINE pairs are judged too);
          - a role's position and summary lines: a changed one is a POSITION or SUMMARY pair, one only one page has
            is ONE-SIDED (a changed summary must get a verdict too);
          - every other section (Education, Projects, Publications, Awards ...): a changed entry pairs blind, a dropped or
            added one is ONE-SIDED, and a section only one page has is one "ONE-SIDED SECTION <name>" pair;
            one-sided pairs count with letters, apart from LINE numbers;
          - per Skills row (matched by label): a changed row becomes a SKILLS pair ("Label: details", both sides); a
            renamed row pairs by position with the base row it replaces ("SKILLS renamed n"); a new or dropped row is
            ONE-SIDED; the
            same rows in a different order become one ORDER Skills pair (both label orders);
          and writes `pairs.md` with Option A / Option B in a seeded random order, evidence comments stripped, the words
          "base" and "variant" never used. The key (which option is which) goes to a separate JSON file the judge
          must not read until its verdicts are written.
  reveal  reads the key and the judge's verdicts (`<pair id>: {winner: A|B|tie, why: ...}`) and prints one row per
          pair: winner base|variant, verdict KEEP (the variant won) or REVERT (the base won, or a tie: the base keeps).

Usage:
  python3 scripts/blind_pairs.py make --resume V/resume.yaml --base resumes/bases/general/resume.yaml \\
      --out V/compare/pairs.md --key V/compare/key.json [--seed N]
  python3 scripts/blind_pairs.py reveal --key V/compare/key.json --verdicts V/compare/verdicts.yaml [--json]
Exit 0 ok, 2 usage error. Deterministic for a given seed; no network.
"""
from __future__ import annotations

import argparse
import difflib
import json
import random
import re
import sys
from pathlib import Path
from typing import Any

import yaml

COMMENT = re.compile(r"\s*<!--.*?-->\s*", re.S)
EV_ID = re.compile(r"\bev-[\w.-]*\w")
SIMILAR = 0.6                  # a reworded line with no shared evidence id still pairs at this text similarity
EMPTY = "(no line here)"


def _clean(text: str) -> str:
    return COMMENT.sub(" ", str(text)).strip().strip("'").strip()


def _roles(path: Path) -> dict[str, list[str]]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    exp = (((data.get("cv") or {}).get("sections") or {}).get("Experience")) or []
    out: dict[str, list[str]] = {}
    for entry in exp:
        if isinstance(entry, dict):
            out[str(entry.get("company") or entry.get("position") or "role")] = [
                _clean(h) for h in (entry.get("highlights") or [])]
    return out


def _heads(path: Path) -> dict[str, dict[str, str]]:
    """Per role, its position and summary lines, cleaned like bullets."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    exp = (((data.get("cv") or {}).get("sections") or {}).get("Experience")) or []
    return {str(e.get("company") or e.get("position") or "role"):
            {k: _clean(e[k]) for k in ("position", "summary") if e.get(k) not in (None, "")}
            for e in exp if isinstance(e, dict)}


def _evidence(path: Path) -> dict[str, list[set[str]]]:
    """Per role, the evidence ids each bullet's comments carry (aligned with _roles)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    exp = (((data.get("cv") or {}).get("sections") or {}).get("Experience")) or []
    return {str(e.get("company") or e.get("position") or "role"):
            [set(EV_ID.findall(" ".join(re.findall(r"<!--(.*?)-->", str(h), re.S)))) for h in (e.get("highlights") or [])]
            for e in exp if isinstance(e, dict)}


def match_lines(b_only: list[str], v_only: list[str], b_ev: dict[str, set[str]], v_ev: dict[str, set[str]],
                b_pos: dict[str, int], v_pos: dict[str, int]) -> list[tuple[str, str]]:
    """Pair each reworded variant line with the base line it replaces: shared evidence ids first, then text
    similarity (SIMILAR or more), each greedy and one to one (ties: the nearer position). Position alone never pairs
    two lines: what is left is a real addition or removal (a rewritten bullet that moved down one place must not be
    paired with a different new bullet)."""
    pairs: list[tuple[str, str]] = []
    free_b, free_v = list(b_only), list(v_only)

    def ratio(bt: str, vt: str) -> float:
        return difflib.SequenceMatcher(None, bt.lower(), vt.lower()).ratio()

    for stage in ("evidence", "text"):
        cands = []
        for bt in free_b:
            for vt in free_v:
                shared = len(b_ev.get(bt, set()) & v_ev.get(vt, set()))
                r = ratio(bt, vt)
                if (stage == "evidence" and shared) or (stage == "text" and r >= SIMILAR):
                    cands.append((-shared, -r, abs(b_pos[bt] - v_pos[vt]), b_pos[bt], v_pos[vt], bt, vt))
        for *_, bt, vt in sorted(cands):
            if bt in free_b and vt in free_v:
                pairs.append((bt, vt))
                free_b.remove(bt)
                free_v.remove(vt)
    return sorted(pairs, key=lambda p: b_pos[p[0]])


def _skills(path: Path) -> list[tuple[str, str]]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    rows = (((data.get("cv") or {}).get("sections") or {}).get("Skills")) or []
    return [(_clean(r.get("label", "")), _clean(r.get("details", ""))) for r in rows if isinstance(r, dict)]


def _letter(n: int) -> str:
    """1 -> a, 26 -> z, 27 -> aa: one-sided pairs count apart from LINE numbers so a label hints at no position."""
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(97 + r) + out
    return out


def _entry_text(entry: Any) -> str:
    """One entry of an other section as the page shows it (comments stripped)."""
    if not isinstance(entry, dict):
        return _clean(entry)
    head = next((entry[k] for k in ("name", "institution", "title", "label", "company") if entry.get(k)), "")
    parts = [str(head), ", ".join(str(entry[k]) for k in ("degree", "area") if entry.get(k)),
             _clean(entry.get("summary") or entry.get("details") or "")]
    parts += [_clean(h) for h in entry.get("highlights") or []]
    return " · ".join(x for x in parts if x)


def _other_sections(path: Path) -> dict[str, list[tuple[str, set[str]]]]:
    """Every section but Experience and Skills: its entries as (visible text, evidence ids)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    secs = ((data.get("cv") or {}).get("sections")) or {}
    out = {}
    for name, entries in secs.items():
        if name in ("Experience", "Skills") or not isinstance(entries, list):
            continue
        out[str(name)] = [(_entry_text(e), set(EV_ID.findall(" ".join(re.findall(r"<!--(.*?)-->", str(e), re.S)))))
                          for e in entries]
    return out


def _section_pairs(v_secs: dict, b_secs: dict, pairs: dict, texts: dict, kinds: dict, rng: random.Random) -> None:
    for name in list(b_secs) + [n for n in v_secs if n not in b_secs]:
        b, v = b_secs.get(name), v_secs.get(name)
        if b is None or v is None:                     # a whole section only one page has
            entries = b if v is None else v
            _one_sided(pairs, texts, kinds, f"ONE-SIDED SECTION {name}", "\n".join(t for t, _ in entries),
                       "base" if v is None else "variant", rng, empty="(no section here)")
            continue
        b_lines, v_lines = [t for t, _ in b], [t for t, _ in v]
        if b_lines == v_lines:
            continue
        b_only = [t for t in b_lines if t not in v_lines]
        v_only = [t for t in v_lines if t not in b_lines]
        matched = match_lines(b_only, v_only, dict(b), dict(v), {t: b_lines.index(t) for t in b_only},
                              {t: v_lines.index(t) for t in v_only})
        for n, (bt, vt) in enumerate(matched, 1):
            labels = ["A", "B"]
            rng.shuffle(labels)
            pairs[f"{name} {n}"] = {labels[0]: "base", labels[1]: "variant"}
            texts[f"{name} {n}"] = {labels[0]: bt, labels[1]: vt}
        pb, pv = {bt for bt, _ in matched}, {vt for _, vt in matched}
        solo = [(t, "variant") for t in v_only if t not in pv] + [(t, "base") for t in b_only if t not in pb]
        rng.shuffle(solo)
        for n, (line, owner) in enumerate(solo, 1):
            _one_sided(pairs, texts, kinds, f"ONE-SIDED {name} {_letter(n)}", line, owner, rng)


def _one_sided(pairs: dict, texts: dict, kinds: dict, pid: str, line: str, owner: str, rng: random.Random,
               empty: str = EMPTY) -> None:
    """A line only one page has, on a random side beside EMPTY, under a neutral id: the judge decides "with or
    without it" and never learns which page holds it. The kind (added or removed) is in the key only."""
    labels = ["A", "B"]
    rng.shuffle(labels)
    pairs[pid] = {labels[0]: owner, labels[1]: "base" if owner == "variant" else "variant"}
    texts[pid] = {labels[0]: line, labels[1]: empty}
    kinds[pid] = "added" if owner == "variant" else "removed"


def _skill_pairs(v_rows: list[tuple[str, str]], b_rows: list[tuple[str, str]], rng: random.Random,
                 kinds: dict[str, str] | None = None) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    pairs: dict[str, dict[str, str]] = {}
    texts: dict[str, dict[str, str]] = {}
    kinds = {} if kinds is None else kinds
    if v_rows == b_rows:
        return pairs, texts

    def show(row: tuple[str, str]) -> str:
        return f"{row[0]}: {row[1]}"

    def blind(pid: str, b_text: str, v_text: str) -> None:
        labels = ["A", "B"]
        rng.shuffle(labels)
        pairs[pid] = {labels[0]: "base", labels[1]: "variant"}
        texts[pid] = {labels[0]: b_text, labels[1]: v_text}

    v_map, b_map = dict(v_rows), dict(b_rows)
    for label, b_details in b_rows:
        if label in v_map and v_map[label] != b_details:
            blind(f"SKILLS {label}", show((label, b_details)), show((label, v_map[label])))
    b_only = [r for r in b_rows if r[0] not in v_map]
    v_only = [r for r in v_rows if r[0] not in b_map]
    for n, (br, vr) in enumerate(zip(b_only, v_only), 1):  # a renamed row stands in the base row's place; the id
        blind(f"SKILLS renamed {n}", show(br), show(vr))   # names neither label
    solo = [(show(vr), "variant") for vr in v_only[len(b_only):]] + [(show(br), "base") for br in b_only[len(v_only):]]
    rng.shuffle(solo)
    for n, (line, owner) in enumerate(solo, 1):
        _one_sided(pairs, texts, kinds, f"ONE-SIDED Skills {_letter(n)}", line, owner, rng)
    common_b = [r[0] for r in b_rows if r[0] in v_map]
    common_v = [r[0] for r in v_rows if r[0] in b_map]
    if common_b != common_v:
        blind("ORDER Skills", "\n".join(f"{i}. {lbl}" for i, lbl in enumerate(common_b, 1)),
              "\n".join(f"{i}. {lbl}" for i, lbl in enumerate(common_v, 1)))
    return pairs, texts


def make(resume: Path, base: Path, out: Path, key: Path, seed: int | None = None) -> dict[str, Any]:
    rng = random.Random(seed)
    v_roles, b_roles = _roles(resume), _roles(base)
    v_evs, b_evs = _evidence(resume), _evidence(base)
    v_heads, b_heads = _heads(resume), _heads(base)
    pairs: dict[str, dict[str, str]] = {}
    texts: dict[str, dict[str, str]] = {}
    kinds: dict[str, str] = {}
    for role, b_lines in b_roles.items():
        v_lines = v_roles.get(role, [])
        if role in v_roles:                                # the position and summary lines are judged too
            bh, vh = b_heads.get(role, {}), v_heads.get(role, {})
            for field in ("position", "summary"):
                bt, vt = bh.get(field), vh.get(field)
                if bt == vt:
                    continue
                if bt and vt:
                    labels = ["A", "B"]
                    rng.shuffle(labels)
                    pid = f"{field.upper()} {role}"
                    pairs[pid] = {labels[0]: "base", labels[1]: "variant"}
                    texts[pid] = {labels[0]: bt, labels[1]: vt}
                else:
                    _one_sided(pairs, texts, kinds, f"ONE-SIDED {role} {field}", bt or vt,
                               "base" if bt else "variant", rng)
        if v_lines == b_lines:
            continue
        if sorted(v_lines) == sorted(b_lines):
            pid = f"ORDER {role}"
            content = {"base": "\n".join(f"{i}. {t}" for i, t in enumerate(b_lines, 1)),
                       "variant": "\n".join(f"{i}. {t}" for i, t in enumerate(v_lines, 1))}
            labels = ["A", "B"]
            rng.shuffle(labels)
            pairs[pid] = {labels[0]: "base", labels[1]: "variant"}
            texts[pid] = {labels[0]: content["base"], labels[1]: content["variant"]}
            continue
        b_only = [t for t in b_lines if t not in v_lines]
        v_only = [t for t in v_lines if t not in b_lines]
        # a reorder plus a reworded line: map each variant line to the base slot it holds (kept lines by text, a
        # reworded line by the base line it replaces); slots out of order mean the order changed too
        b_ev = {t: e for t, e in zip(b_lines, b_evs.get(role, []))}
        v_ev = {t: e for t, e in zip(v_lines, v_evs.get(role, []))}
        matched = match_lines(b_only, v_only, b_ev, v_ev, {t: b_lines.index(t) for t in b_only},
                              {t: v_lines.index(t) for t in v_only})
        slot = {t: b_lines.index(t) for t in v_lines if t in b_lines}
        slot.update({vt: b_lines.index(bt) for bt, vt in matched})
        seq = [slot[t] for t in v_lines if t in slot]
        if seq != sorted(seq):
            pid = f"ORDER {role}"
            labels = ["A", "B"]
            rng.shuffle(labels)
            pairs[pid] = {labels[0]: "base", labels[1]: "variant"}
            texts[pid] = {labels[0]: "\n".join(f"{i}. {t}" for i, t in enumerate(b_lines, 1)),
                          labels[1]: "\n".join(f"{i}. {t}" for i, t in enumerate(v_lines, 1))}
        for n, (bt, vt) in enumerate(matched, 1):
            pid = f"LINE {role} {n}"
            labels = ["A", "B"]
            rng.shuffle(labels)
            pairs[pid] = {labels[0]: "base", labels[1]: "variant"}
            texts[pid] = {labels[0]: bt, labels[1]: vt}
        paired_b, paired_v = {bt for bt, _ in matched}, {vt for _, vt in matched}
        solo = [(t, "variant") for t in v_only if t not in paired_v] + [(t, "base") for t in b_only if t not in paired_b]
        rng.shuffle(solo)                                          # added and removed lines, mixed and unlabelled
        for n, (line, owner) in enumerate(solo, 1):
            _one_sided(pairs, texts, kinds, f"ONE-SIDED {role} {_letter(n)}", line, owner, rng)
    s_pairs, s_texts = _skill_pairs(_skills(resume), _skills(base), rng, kinds)
    pairs.update(s_pairs)
    _section_pairs(_other_sections(resume), _other_sections(base), pairs, texts, kinds, rng)
    texts.update(s_texts)
    lines = ["# Pairs to judge", "",
             "For each pair, decide which option better serves this job's reader, reads on its own, carries one clear",
             "result and stays true to its record. Write verdicts as YAML: `<pair id>: {winner: A|B|tie, why: ...}`.",
             "Do not open the key file before your verdicts are written.", ""]
    if not pairs:
        lines.append("No differences: the two pages carry the same bullets and Skills rows in the same order.")
    for pid in pairs:
        lines += [f"## {pid}", "", "**Option A**", "", texts[pid]["A"], "", "**Option B**", "", texts[pid]["B"], ""]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text(json.dumps({"resume": str(resume), "base": str(base), "seed": seed, "pairs": pairs, "kinds": kinds},
                              indent=2) + "\n", encoding="utf-8")
    return {"pairs": list(pairs)}


def reveal(key: Path, verdicts: Path) -> list[dict[str, Any]]:
    k = json.loads(Path(key).read_text(encoding="utf-8"))
    v = yaml.safe_load(Path(verdicts).read_text(encoding="utf-8")) or {}
    rows = []
    for pid, labels in k["pairs"].items():
        verdict = v.get(pid) or {}
        w = str(verdict.get("winner", "")).strip()
        who = labels.get(w, "tie" if w.lower() == "tie" else "missing")
        rows.append({"pair": pid, "kind": (k.get("kinds") or {}).get(pid), "winner": who, "why": verdict.get("why", ""),
                     "verdict": "KEEP" if who == "variant" else ("MISSING" if who == "missing" else "REVERT")})
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("--resume", required=True, type=Path)
    m.add_argument("--base", required=True, type=Path)
    m.add_argument("--out", required=True, type=Path)
    m.add_argument("--key", required=True, type=Path)
    m.add_argument("--seed", type=int, default=None)
    r = sub.add_parser("reveal")
    r.add_argument("--key", required=True, type=Path)
    r.add_argument("--verdicts", required=True, type=Path)
    r.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "make":
            for p in (a.resume, a.base):
                if not p.is_file():
                    print(f"no such file: {p}", file=sys.stderr)
                    return 2
            res = make(a.resume, a.base, a.out, a.key, a.seed)
            print(f"{len(res['pairs'])} pair(s) -> {a.out} (key: {a.key})")
            return 0
        rows = reveal(a.key, a.verdicts)
        if a.json:
            print(json.dumps(rows, indent=2))
        else:
            for row in rows:
                print(f"{row['verdict']:7} {row['pair']}: winner {row['winner']}. {row['why']}")
            keep = sum(r["verdict"] == "KEEP" for r in rows)
            print(f"KEEP {keep} · REVERT {len(rows) - keep} · base unchanged: {'yes' if keep == 0 else 'no'}")
        return 0
    except (OSError, ValueError, KeyError, yaml.YAMLError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
