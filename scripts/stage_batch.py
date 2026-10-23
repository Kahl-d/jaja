#!/usr/bin/env python3
"""Stage a LinkedIn approval batch from approved texts, and write the apply list from the same drafts.

    python3 scripts/stage_batch.py --batch L-YYYY-MM-DD-P<n>   (from the data root), then
    python3 scripts/outbox.py create --batch <same> ...

Every draft body comes from the approved text files (state/linkedin/approved/*.txt) or from literal text in the batch
config, so a batch cannot differ from the approved profile record. `old_text` names the live line or the conflict id,
never held text.

The list of items is configuration (policy.linkedin_batch), not code:

  live_ref        text substituted for `{LIVE}` in an item's `old` (where the live profile was captured)
  common_checks   checks appended to every draft
  apply_list      {title, owner, approval, approval_suffix, preamble: [lines], after: [lines]} for the generated apply list
  items           one mapping per profile edit: slug, label, field, where (the instruction), old (the live text),
                  evidence (ids), checks, and the body as one of
                    body: "<literal text>"
                    body_file: "<name in approved/>"  with body_mode "full" (default), "description" (the part after
                    the header paragraph) and an optional body_prefix
                  or `kind: skills`: the skills edit built from a REMOVE/KEEP file (see build_skills_item)
"""
from __future__ import annotations
import argparse, hashlib, sys
from pathlib import Path
import yaml

import agency_config as cfg
from agency_paths import DATA_ROOT as JS  # noqa: E402

A = JS / "state/linkedin/approved"


def t(n):
    return (A / n).read_text().strip()


def body_of(n):  # the description part of an approved experience file (after the blank line that ends its header)
    s = t(n)
    return s.split("\n\n", 1)[1].split("\n\n[")[0].strip() if "\n\n" in s else s


def head_of(n):
    return t(n).split("\n\n", 1)[0].strip()


def skill_name(item: str) -> str:
    """'Python (Programming Language) (duplicate of Python)' -> 'Python (Programming Language)': drop only the trailing
    reason group (it may nest: 'Computer Science (generic (the degree says it))'); a name's own parentheses stay."""
    s = item.strip()
    if not s.endswith(")"):
        return s
    depth = 0
    for i in range(len(s) - 1, -1, -1):
        depth += {")": 1, "(": -1}.get(s[i], 0)
        if depth == 0:
            return s[:i].rstrip()
    return s


def remove_list(line: str) -> list:
    return [skill_name(x) for x in line.split("REMOVE: ", 1)[1].split("\n", 1)[0].split("; ")]


def _skills_file() -> str:
    for item in cfg.get("policy.linkedin_batch.items") or []:
        if isinstance(item, dict) and item.get("kind") == "skills":
            return str(item.get("skills_file") or "skills.txt")
    return "skills.txt"


def _safe_read(name: str) -> str:
    try:
        return t(name)
    except OSError:
        return ""


_SK = _safe_read(_skills_file())
REMOVE = remove_list(_SK) if "REMOVE: " in _SK else []
KEEP = _SK.split("KEEP: ", 1)[1].split("\n", 1)[0].split("; ") if "KEEP: " in _SK else []


def _skills_catalogue() -> list:
    try:
        from skills_map import SKILLS  # noqa: E402
    except ImportError:
        return []
    return list(SKILLS)


def build_skills_item(spec: dict, live: str) -> tuple:
    """The skills edit: delete every REMOVE name first (the list is at its cap), then add every ADD name; a name the
    typeahead may not offer carries a bracketed fallback."""
    catalogue = _skills_catalogue()
    protected = set(spec.get("protected") or [])
    fallbacks = dict(spec.get("fallbacks") or {})
    add = [s[0] for s in catalogue if s[0] not in KEEP and s[0] not in protected]
    keep_n = len(KEEP) + int(spec.get("keep_extra", 0))
    body = ("REMOVE: " + "; ".join(REMOVE) + "\nADD: " + "; ".join(n + (" [" + fallbacks[n] + "]" if n in fallbacks else "") for n in add)
            + "\n" + str(spec.get("keep_note", "KEEP: the other {n}")).format(n=keep_n))
    if spec.get("after"):
        body += "\n" + str(spec["after"])
    label = str(spec.get("label", "Skills: remove {remove}, then add {add}")).format(remove=len(REMOVE), add=len(add))
    return (spec.get("slug", "skills-list"), label, spec.get("field", "skills (full list)"), spec.get("where", ""),
            str(spec.get("old", "")).replace("{LIVE}", live), body, list(spec.get("evidence") or []), list(spec.get("checks") or []))


def _body(spec: dict) -> str:
    if "body" in spec:
        return str(spec["body"])
    name = str(spec.get("body_file") or "")
    if not name:
        return ""
    text = {"description": body_of, "head": head_of}.get(str(spec.get("body_mode") or "full"), t)(name)
    return str(spec.get("body_prefix") or "") + text


def build_items() -> list:
    """[(slug, label, field, where, old_text, body, evidence_ids, checks)] from policy.linkedin_batch.items."""
    live = str(cfg.get("policy.linkedin_batch.live_ref", ""))
    out = []
    for spec in cfg.get("policy.linkedin_batch.items") or []:
        if spec.get("kind") == "skills":
            out.append(build_skills_item(spec, live))
            continue
        out.append((spec["slug"], spec["label"], spec["field"], spec.get("where", ""),
                    str(spec.get("old", "")).replace("{LIVE}", live), _body(spec),
                    list(spec.get("evidence") or []), list(spec.get("checks") or [])))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True)
    ap.add_argument("--approval", default=str(cfg.get("policy.linkedin_batch.apply_list.approval", "pending")))
    a = ap.parse_args()
    items = build_items()
    if not items:
        print("no items: set policy.linkedin_batch.items in your config", file=sys.stderr)
        return 1
    out = JS / "state/drafts" / a.batch
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.md"):
        old.unlink()
    common = [str(x) for x in cfg.get("policy.linkedin_batch.common_checks") or []]
    rows = []
    for slug, label, field, where, old, body, ev, checks in items:
        meta = {"batch": a.batch, "label": label, "channel": "linkedin-profile-edit", "kind": "other", "to": field, "field": field,
                "old_text": old, "subject": None, "thread_id": None, "evidence_ids": ev, "conflicts_touched": [], "attachments": [],
                "checks": checks + ["source: state/linkedin/LINKEDIN.md (generated from state/linkedin/approved/*.txt)"] + common}
        p = out / f"{slug}.md"
        p.write_text("---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=200) + "---\n" + body.strip() + "\n")
        rows.append((label, where, body.strip(), p.name, hashlib.sha256(p.read_bytes()).hexdigest()))
    al = cfg.get("policy.linkedin_batch.apply_list") or {}
    L = ["---", f"title: {al.get('title', 'LinkedIn apply list (the owner applies; nothing is applied by any agent)')}",
         f"owner: {al.get('owner', 'presence lane')}",
         f"batch: state/drafts/{a.batch} ({len(rows)} drafts, generated from state/linkedin/LINKEDIN.md by scripts/stage_batch.py)",
         f"approval: {a.approval}{al.get('approval_suffix', '')}", "---", "",
         "# LinkedIn apply list", "",
         "## Step 0: before anything else", ""]
    L += [str(x) for x in al.get("preamble") or ["Apply every change below in order, in one sitting."]] + [""]
    for i, (label, where, body, name, h) in enumerate(rows, 1):
        L += [f"## Step {i}. {label}", "", where, "", "```text", body, "```", "", f"<sub>draft `{name}`, sha256 `{h[:16]}...`</sub>", ""]
    L += ["## After you finish", ""] + [str(x) for x in al.get("after") or [
        "- Tell the orchestrator, or paste the profile back, and every field is checked against LINKEDIN.md.",
        "- Each item is recorded with `outbox.py mark-sent`."]] + [""]
    (JS / "state/linkedin/apply-list.md").write_text("\n".join(L))
    print(len(rows), "drafts in", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
