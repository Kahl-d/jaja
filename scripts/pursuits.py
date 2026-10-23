#!/usr/bin/env python3
"""pursuits.py - the pursuit record: one direction into one company, and the only writer of pursuits/*/pursuit.md.

A referral used to have no record of its own: the referrer, the introducer, the owner's picks, the stage and the
next action lived only in the case file's prose and the intake. A pursuit holds exactly that
(schemas/pursuit.yaml) and nothing another record already holds; scripts/pursuit_hub.py joins the rest
into pursuits/<slug>/HUB.md. Owner: the recruiter role.

One pursuit per DIRECTION: a referral through one person, a direct application, a recruiter's inbound. Several
may share a company. The id names the direction: <company_slug>-via-<referrer>-<YYYYMM>, or
<company_slug>-<kind>-<YYYYMM> without a referrer (--slug overrides).

  new       --company NAME|SLUG --kind referral|direct|recruiter-inbound [--referrer P] [--introducer P]
            [--contact P ...] [--case PATH] [--intake PATH] [--application SLUG ...] [--master-variant V]
            [--stage S] --waiting-on W [--next-action TEXT] [--next-action-at YYYY-MM-DD] [--title T] [--slug S]
  set       <id> [--stage S [--reason R]] [--waiting-on W] [--next-action TEXT] [--next-action-at D]
            [--add-contact P] [--remove-contact P] [--add-application A] [--remove-application A]
            [--referrer P] [--introducer P] [--case PATH] [--intake PATH] [--master-variant V] [--title T]
  decision  <id> --text TEXT (--ruling ID | --quote "words" [--speaker P])  append-only, dated
  correct   <id> --of TEXT [--speaker P] [--text NEW] --reason R (--ruling ID | --quote Q)   append-only
  strategy  <id> --quote "the owner's words" --source WHERE                   append-only, dated
  reopen    <id> --stage S --reason R (--ruling ID | --quote Q) [--waiting-on W]   from paused or closed-*
  list      [--open] [--person P] [--json]

Rules: the company resolves through company_index.py (the dossier's slug first). Every application, the case,
the intake and the master variant must exist, or nothing is written. A person without a prospects/ record is
kept and printed as `missing:` (the hub shows it too). waiting_on is owner, nobody, contact:<a person on this
pursuit>, lane:<name> or role:<role slug>. A closed stage needs a reason and clears the next action; a paused
or closed pursuit moves only through `reopen`. Every change is a dated line under `## Log`; decisions cite a
ruling id (shape: policy.ruling_id_pattern) or the owner's quoted words. A quote names its
speaker: anyone else must be on the pursuit, and their words never stand in for
the owner's, so the decision also cites a ruling. `correct` never edits a line: it appends a cited correction of the
one decision --of matches (its original line or its latest restatement), restating it with a new speaker, new text
or both; a later correction builds on the latest one. An entry is one line with " · " between its parts, so a line
break, " · " inside a quote or reason, a quote-like ': "' in a text, or a speaker name that would misread is refused.
Exit 0 written or listed, 2 refused (nothing written).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as cfg  # noqa: E402
from company_index import CompanyIndex  # noqa: E402
from js_common import JS_ROOT, append_section_line, cli_date, dept_day, fm, iter_records, slugify, write_record  # noqa: E402

KINDS = ("referral", "direct", "recruiter-inbound")
STAGES = ("intake", "researching", "case-ready", "packages", "resumes", "review", "drafted", "awaiting-approval",
          "sent", "in-conversation", "interviewing", "offer", "paused", "closed-won", "closed-lost")
CLOSED = ("closed-won", "closed-lost")
HELD = ("paused", *CLOSED)
FIELDS = ("type", "id", "title", "company", "company_slug", "kind", "referrer", "introducer", "contacts", "case",
          "intake", "applications", "master_variant", "stage", "waiting_on", "next_action", "next_action_at",
          "closed_reason", "created", "updated")
# Ruling ids, the owner's own token in waiting_on and the owner's name as a quote speaker come from config.
RULING_ID = cfg.get("policy.ruling_id_pattern", r"[A-Z]{1,4}-\d+")
OWNER_TOKEN = str(cfg.get("identity.owner_token", "owner")).lower()
OWNER_SPEAKER = str(cfg.get("identity.owner_speaker", "Owner"))
RULING = re.compile(rf"^{RULING_ID}$")
SEP = " · "
LINE_BREAKS = re.compile("[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")    # everything str.splitlines() splits on
_DAY = r"\d{4}-\d{2}-\d{2}"
_BODY = rf'(?:(?P<ruling>{RULING_ID}) · )?(?:(?P<speaker>[^:"·]+): "(?P<quote>.*?)" · )?(?P<text>.*)'
DECISION = re.compile(rf"^- (?P<day>{_DAY}) · {_BODY}$")
CORRECTION = re.compile(rf"^- {_DAY} · correction of decision (?P<n>\d+) \((?P<day>{_DAY})\) · "
                        rf".*? · now reads: {_BODY}$")
WAITING = re.compile(rf"^({re.escape(OWNER_TOKEN)}|nobody|(contact|lane|role):[a-z0-9][a-z0-9-]*)$")


class Refused(Exception):
    pass


def folder(root: Path) -> Path:
    return Path(root) / "pursuits"


def record_path(root: Path, slug: str) -> Path:
    return folder(root) / slug / "pursuit.md"


def iter_pursuits(root: Path):
    base = folder(root)
    for path in sorted(base.glob("*/pursuit.md")) if base.is_dir() else ():
        meta, body = fm.load(path)
        yield path.parent.name, meta, body


def person_name(root: Path, slug: str | None) -> str | None:
    if not slug:
        return None
    path = Path(root) / "prospects" / f"{slug}.md"
    return str(fm.load(path)[0].get("name") or slug) if path.is_file() else slug


def people(meta: dict[str, Any]) -> list[str]:
    return [p for p in [meta.get("referrer"), meta.get("introducer"), *(meta.get("contacts") or [])] if p]


def day_value(text: str | None, field: str) -> date | None:
    if text in (None, ""):
        return None
    try:
        return date.fromisoformat(str(text))
    except ValueError:
        raise Refused(f"{field} {text!r} is not a YYYY-MM-DD date") from None


def check(root: Path, meta: dict[str, Any]) -> list[str]:
    """Refuse what cannot be verified; return the `missing:` lines for people without a record."""
    root = Path(root)
    if meta.get("kind") not in KINDS:
        raise Refused(f"kind {meta.get('kind')!r} is not one of {', '.join(KINDS)}")
    if meta.get("stage") not in STAGES:
        raise Refused(f"stage {meta.get('stage')!r} is not one of {', '.join(STAGES)}")
    waiting = str(meta.get("waiting_on") or "")
    if not WAITING.match(waiting):
        raise Refused(f"waiting_on {waiting!r} is not {OWNER_TOKEN}, nobody, contact:<person>, lane:<name> or role:<role>")
    if waiting.startswith("contact:") and waiting.split(":", 1)[1] not in people(meta):
        raise Refused(f"waiting_on {waiting} names nobody on this pursuit (referrer, introducer, contacts)")
    for a in meta.get("applications") or []:
        if not (root / "applications" / a / "application.md").is_file():
            raise Refused(f"applications/{a}/application.md does not exist")
    for key in ("case", "intake"):
        rel = meta.get(key)
        if rel and (str(rel).startswith(("/", "..")) or not (root / rel).is_file()):
            raise Refused(f"{key} {rel} does not exist under the job-search root")
    v = meta.get("master_variant")
    if v and not (root / "resumes" / "variants" / v).is_dir():
        raise Refused(f"master_variant resumes/variants/{v} does not exist")
    if meta.get("stage") in CLOSED and not meta.get("closed_reason"):
        raise Refused(f"stage {meta['stage']} needs a closed_reason (--reason)")
    return [f"missing: prospects/{p}.md (a person on this pursuit has no record; prospector)"
            for p in people(meta) if not (root / "prospects" / f"{p}.md").is_file()]


def ordered(meta: dict[str, Any]) -> dict[str, Any]:
    out = {k: meta.get(k) for k in FIELDS}
    out.update({k: v for k, v in meta.items() if k not in out})
    return out


def one_line(flag: str, value: str | None, *, sep: bool = False, text: bool = False) -> str | None:
    """A part of a one-line entry: no line break; no " · " where the reader splits on it; a text never reads as
    a quote (': "') and is never empty."""
    if value is None:
        return None
    if len(value.splitlines()) > 1 or LINE_BREAKS.search(value):
        raise Refused(f"{flag} holds a line break; every entry is one line")
    v = value.strip()
    if sep and SEP in v:
        raise Refused(f"{flag} holds {SEP.strip()!r}, the entry separator")
    if text and ': "' in v:
        raise Refused(f"{flag} reads as a quote (': \"'); quote words with --quote and --speaker")
    if text and not v:
        raise Refused(f"{flag} is empty")
    return v


def speaker_name(root: Path, meta: dict[str, Any], speaker: str | None) -> str:
    """the owner (the default) or a person on this pursuit, by the name on their prospects/ record."""
    if speaker in (None, "", OWNER_TOKEN):
        return OWNER_SPEAKER
    if speaker not in people(meta) or not (Path(root) / "prospects" / f"{speaker}.md").is_file():
        raise Refused(f"--speaker {speaker} is not on this pursuit with a prospects/ record "
                      "(referrer, introducer, contacts)")
    name = str(person_name(root, speaker)).strip()
    if any(c in name for c in ':"·'):
        raise Refused(f"--speaker {speaker}: the name {name!r} cannot be quoted in a one-line entry (: \" ·)")
    if name.lower() == OWNER_SPEAKER.lower():
        raise Refused(f"--speaker {speaker}: the name {name!r} reads as the owner; record the person's full name")
    return name


def cite(args: argparse.Namespace, speaker: str = OWNER_SPEAKER) -> str:
    ruling, quote = getattr(args, "ruling", None), one_line("--quote", getattr(args, "quote", None), sep=True)
    if quote == "":
        raise Refused("--quote is empty; quote the words or leave --quote out")
    if not ruling and not quote:
        raise Refused("an entry cites a ruling id (--ruling) or the owner's own words (--quote)")
    if ruling and not RULING.match(ruling):
        raise Refused(f"--ruling {ruling!r} is not a ruling id (shape {RULING_ID})")
    if speaker != OWNER_SPEAKER and not ruling:
        raise Refused(f"{speaker}'s words are not the owner's: the decision also cites the ruling id (--ruling) "
                      "that made it the owner's")
    return render(ruling, speaker if quote else None, quote, None)


def render(ruling: str | None, speaker: str | None, quote: str | None, text: str | None) -> str:
    return SEP.join([*([ruling] if ruling else []), *([f'{speaker}: "{quote}"'] if quote is not None else []),
                     *([text] if text is not None else [])])


def decisions(body: str) -> list[tuple[str, dict[str, Any]]]:
    """Each decision as (its original line, its current state): the original, then each correction of it in order."""
    section = body.split("## Decisions", 1)[1].split("\n## ", 1)[0] if "## Decisions" in body else ""
    out: list[tuple[str, dict[str, Any]]] = []
    for ln in section.splitlines():
        c = CORRECTION.match(ln)
        if c:
            n = int(c["n"])
            if 1 <= n <= len(out):
                out[n - 1] = (out[n - 1][0], c.groupdict())
            continue
        m = DECISION.match(ln)
        if m:
            out.append((ln, m.groupdict()))
    return out


def body_for(title: str, day: date, stage: str) -> str:
    return (f"# {title}\n\nThe record for one direction into a company, written only by `scripts/pursuits.py`; "
            f"the join over every record it names is `HUB.md` beside it.\n\n## Strategy\n\n## Decisions\n\n"
            f"## Log\n- {day} · opened at stage {stage}\n")


# ------------------------------------------------------------------------------------------------ commands
def cmd_new(args: argparse.Namespace, day: date) -> int:
    root = Path(args.root)
    idx = CompanyIndex.load(root)
    cs, source = idx.resolve(args.company)
    entry = next((e for e in idx.entries if e["slug"] == cs), None) if source != "derived" else None
    company = entry["names"][0] if entry and entry["names"] else args.company
    slug = slugify(args.slug) if args.slug else (
        f"{cs}-via-{args.referrer}-{day:%Y%m}" if args.referrer else f"{cs}-{args.kind}-{day:%Y%m}")
    if record_path(root, slug).exists():
        raise Refused(f"pursuit {slug} exists; change it with `pursuits.py set {slug}`")
    title = args.title or (
        f"{company} via {person_name(root, args.referrer)}"
        + (f", introduced by {person_name(root, args.introducer)}" if args.introducer else "")
        if args.referrer else f"{company}, {args.kind}")
    meta = ordered({"type": "pursuit", "id": slug, "title": title, "company": company, "company_slug": cs,
                    "kind": args.kind, "referrer": args.referrer, "introducer": args.introducer,
                    "contacts": list(dict.fromkeys(args.contact or [])), "case": args.case, "intake": args.intake,
                    "applications": list(dict.fromkeys(args.application or [])),
                    "master_variant": args.master_variant, "stage": args.stage, "waiting_on": args.waiting_on,
                    "next_action": args.next_action,
                    "next_action_at": day_value(args.next_action_at, "next_action_at"),
                    "closed_reason": args.reason, "created": day, "updated": day})
    missing = check(root, meta)
    write_record(record_path(root, slug), meta, body_for(title, day, args.stage))
    print(f"opened pursuits/{slug}/pursuit.md · {title} · stage {args.stage} · waiting on {args.waiting_on}")
    for line in missing:
        print(line)
    return 0


def load(root: Path, slug: str) -> tuple[dict[str, Any], str]:
    path = record_path(root, slug)
    if not path.is_file():
        raise Refused(f"no pursuit {slug} (pursuits/{slug}/pursuit.md)")
    return fm.load(path)


def save(root: Path, slug: str, meta: dict[str, Any], body: str, day: date) -> list[str]:
    meta["updated"] = day
    missing = check(root, meta)
    write_record(record_path(root, slug), ordered(meta), body)
    return missing


def cmd_set(args: argparse.Namespace, day: date) -> int:
    root = Path(args.root)
    meta, body = load(root, args.id)
    before = dict(meta)
    if args.stage and args.stage != meta.get("stage"):
        if meta.get("stage") in HELD:
            raise Refused(f"{args.id} is {meta['stage']}; it moves only through `pursuits.py reopen {args.id}`")
        meta["stage"] = args.stage
        if args.stage in CLOSED:
            meta["closed_reason"] = args.reason or meta.get("closed_reason")
            meta["waiting_on"], meta["next_action"], meta["next_action_at"] = "nobody", None, None
    for key in ("waiting_on", "next_action", "referrer", "introducer", "case", "intake", "master_variant", "title"):
        value = getattr(args, key)
        if value is not None:
            meta[key] = value
    if args.next_action_at is not None:
        meta["next_action_at"] = day_value(args.next_action_at, "next_action_at")
    for key, add, remove in (("contacts", args.add_contact, args.remove_contact),
                             ("applications", args.add_application, args.remove_application)):
        items = list(meta.get(key) or [])
        items += [x for x in add or [] if x not in items]
        meta[key] = [x for x in items if x not in (remove or [])]
    for key in FIELDS:
        if key in ("updated", "type") or meta.get(key) == before.get(key):
            continue
        old, new = before.get(key), meta.get(key)
        if isinstance(new, list) or isinstance(old, list):
            added = [x for x in new or [] if x not in (old or [])]
            gone = [x for x in old or [] if x not in (new or [])]
            line = f"{key}" + "".join(f" + {x}" for x in added) + "".join(f" - {x}" for x in gone)
        else:
            line = f"{key} {old if old not in (None, '') else '(none)'} -> {new if new not in (None, '') else '(none)'}"
        body = append_section_line(body, "Log", f"- {day} · {line}")
    for line in save(root, args.id, meta, body, day):
        print(line)
    print(f"updated pursuits/{args.id}/pursuit.md · stage {meta['stage']} · waiting on {meta['waiting_on']}")
    return 0


def cmd_decision(args: argparse.Namespace, day: date) -> int:
    root = Path(args.root)
    meta, body = load(root, args.id)
    if args.speaker and not args.quote:
        raise Refused("--speaker names who said a --quote; this decision quotes nobody")
    text = one_line("--text", args.text, text=True)
    line = f"- {day} · {cite(args, speaker_name(root, meta, args.speaker))}{SEP}{text}"
    save(root, args.id, meta, append_section_line(body, "Decisions", line), day)
    print(f"decision recorded in pursuits/{args.id}/pursuit.md: {line[2:]}")
    return 0


def cmd_correct(args: argparse.Namespace, day: date) -> int:
    root = Path(args.root)
    meta, body = load(root, args.id)
    citation = cite(args)                                   # the correction's own authority: a ruling or the owner's yes
    reason = one_line("--reason", args.reason, sep=True)
    if not reason:
        raise Refused("a correction needs a --reason")
    of = one_line("--of", args.of)
    new_text = one_line("--text", args.text, text=True)
    found = decisions(body)
    hits = [i for i, (orig, cur) in enumerate(found) if of in orig or of in render(**{k: cur[k] for k in
            ("ruling", "speaker", "quote", "text")})]
    if len(hits) != 1:
        raise Refused(f"--of {of!r} matches {len(hits) or 'no'} decision(s); it must match exactly one")
    n = hits[0] + 1
    cur = found[hits[0]][1]
    if args.speaker and cur["quote"] is None:
        raise Refused("--speaker corrects who said a quote; the matched decision quotes nobody")
    speaker = speaker_name(root, meta, args.speaker) if args.speaker else cur["speaker"]
    text = new_text if new_text is not None else cur["text"]
    if speaker == cur["speaker"] and text == cur["text"]:
        raise Refused("this correction changes nothing (same speaker, same text as the decision reads now)")
    ruling = cur["ruling"]
    if cur["quote"] is not None and speaker != OWNER_SPEAKER and not ruling:
        if not args.ruling:
            raise Refused(f"{speaker}'s words are not the owner's: cite the ruling id (--ruling) that made the "
                          "decision the owner's; the corrected decision keeps it")
        ruling = args.ruling
    restated = render(ruling, speaker, cur["quote"], text)
    line = f"- {day} · correction of decision {n} ({cur['day']}){SEP}{citation}{SEP}{reason} · now reads: {restated}"
    save(root, args.id, meta, append_section_line(body, "Decisions", line), day)
    print(f"correction recorded in pursuits/{args.id}/pursuit.md: {line[2:]}")
    return 0


def cmd_strategy(args: argparse.Namespace, day: date) -> int:
    root = Path(args.root)
    meta, body = load(root, args.id)
    line = f'- {day} · "{args.quote.strip()}" ({OWNER_SPEAKER}, {args.source.strip()})'
    save(root, args.id, meta, append_section_line(body, "Strategy", line), day)
    print(f"strategy recorded in pursuits/{args.id}/pursuit.md")
    return 0


def cmd_reopen(args: argparse.Namespace, day: date) -> int:
    root = Path(args.root)
    meta, body = load(root, args.id)
    was = meta.get("stage")
    if was not in HELD:
        raise Refused(f"{args.id} is {was}, not paused or closed; move it with `pursuits.py set`")
    if args.stage in HELD:
        raise Refused(f"reopen moves to an open stage, not {args.stage}")
    if not args.reason:
        raise Refused("reopen needs a --reason")
    citation = cite(args)
    meta.update({"stage": args.stage, "closed_reason": None})
    if args.waiting_on:
        meta["waiting_on"] = args.waiting_on
    body = append_section_line(body, "Log", f"- {day} · reopened from {was} to {args.stage}: {args.reason} · {citation}")
    for line in save(root, args.id, meta, body, day):
        print(line)
    print(f"reopened pursuits/{args.id}/pursuit.md at stage {args.stage}")
    return 0


def rows(root: Path) -> list[dict[str, Any]]:
    out = []
    for slug, meta, _ in iter_pursuits(root):
        out.append({"id": slug, "title": meta.get("title"), "company_slug": meta.get("company_slug"),
                    "kind": meta.get("kind"), "stage": meta.get("stage"), "waiting_on": meta.get("waiting_on"),
                    "next_action": meta.get("next_action"),
                    "next_action_at": str(meta["next_action_at"]) if meta.get("next_action_at") else None,
                    "referrer": meta.get("referrer"), "people": people(meta), "updated": str(meta.get("updated") or "")})
    return out


def cmd_list(args: argparse.Namespace, day: date) -> int:
    data = [r for r in rows(Path(args.root)) if not (args.open and r["stage"] in CLOSED)
            and (not args.person or args.person in r["people"])]
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    if not data:
        print("no pursuits" + (" open" if args.open else "") + f" under {folder(Path(args.root))}")
    for r in data:
        nxt = f"next: {r['next_action']} ({r['next_action_at'] or 'no date'})" if r["next_action"] else "no next action"
        print(f"{r['id']} · {r['stage']} · waiting on {r['waiting_on']} · {nxt}")
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", type=Path, default=JS_ROOT)
    common.add_argument("--today", type=cli_date, default=None, help="the department day (default: now, Los Angeles)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", parents=[common])
    n.add_argument("--company", required=True)
    n.add_argument("--kind", required=True, choices=KINDS)
    n.add_argument("--slug")
    n.add_argument("--stage", default="intake")
    n.add_argument("--reason")
    n.add_argument("--contact", action="append")
    n.add_argument("--application", action="append")
    n.add_argument("--waiting-on", required=True)

    s = sub.add_parser("set", parents=[common])
    s.add_argument("id")
    s.add_argument("--stage", choices=STAGES)
    s.add_argument("--reason")
    s.add_argument("--waiting-on")
    for flag in ("--add-contact", "--remove-contact", "--add-application", "--remove-application"):
        s.add_argument(flag, action="append")
    for p in (n, s):
        for flag in ("--referrer", "--introducer", "--case", "--intake", "--master-variant", "--title",
                     "--next-action", "--next-action-at"):
            p.add_argument(flag)

    d = sub.add_parser("decision", parents=[common])
    d.add_argument("id")
    d.add_argument("--text", required=True)
    d.add_argument("--ruling")
    d.add_argument("--quote")
    d.add_argument("--speaker", help="who said --quote: the owner (default) or a person on this pursuit")

    c = sub.add_parser("correct", parents=[common])
    c.add_argument("id")
    c.add_argument("--of", required=True,
                   help="text in exactly one decision: its original line or its latest restatement")
    c.add_argument("--speaker", help="the quote's true speaker: the owner or a person on this pursuit")
    c.add_argument("--text", help="the decision's corrected text")
    c.add_argument("--reason", help="why the line was wrong (required)")
    c.add_argument("--ruling", help="the ruling id behind the correction; required when the quote is not the owner's")
    c.add_argument("--quote", help="the owner's words authorizing the correction")

    st = sub.add_parser("strategy", parents=[common])
    st.add_argument("id")
    st.add_argument("--quote", required=True)
    st.add_argument("--source", required=True, help="where the owner said it: chat, intake, a file")

    r = sub.add_parser("reopen", parents=[common])
    r.add_argument("id")
    r.add_argument("--stage", required=True, choices=STAGES)
    r.add_argument("--reason")
    r.add_argument("--ruling")
    r.add_argument("--quote")
    r.add_argument("--waiting-on")

    li = sub.add_parser("list", parents=[common])
    li.add_argument("--open", action="store_true", help="leave out closed-won and closed-lost")
    li.add_argument("--person", help="only pursuits this prospect slug is on (referrer, introducer or contact)")
    li.add_argument("--json", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    day = args.today or dept_day(datetime.now(timezone.utc))
    try:
        return {"new": cmd_new, "set": cmd_set, "decision": cmd_decision, "correct": cmd_correct,
                "strategy": cmd_strategy, "reopen": cmd_reopen, "list": cmd_list}[args.cmd](args, day)
    except Refused as exc:
        print(f"refused: {exc}; nothing written", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
