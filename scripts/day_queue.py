#!/usr/bin/env python3
"""day_queue.py - the day's ranked work queue the lanes claim from (agency charter §4: lanes are personas; each reads
the charter, claims the next item of its type, and hands the result back to the Recruiter for review).

    python3 scripts/day_queue.py build [--date D] [--yes-quote "<their words>" --via chat]   # from cards-<date>: the yes cards
    python3 scripts/day_queue.py add --kind K --title T [--effort E] [--channel C] [--url U] [--job J] [--slug S]
    python3 scripts/day_queue.py claim --lane NAME --kind K [--channel C]    # the top open item of that kind (and channel)
    python3 scripts/day_queue.py done --id Q-... --result PATH [--lane NAME]
    python3 scripts/day_queue.py release --id Q-... [--blocked REASON]
    python3 scripts/day_queue.py show [--kind K] [--status S] [--channel C] [--json]
    common: [--root JS] [--out DIR] [--date YYYY-MM-DD] [--now ISO]

The queue is state/recruiter/queue-<date>.yaml (--out moves the folder): one item each with id (Q-<date>-NN), kind
(research | resume | apply | focused | posts | portal | mass | prep), effort (E1-E5), rank (1 = first), title, card and item_key (where
it came from), links (url, job, slug, company_slug), status (open | claimed | done | blocked), claimed_by, claimed_at,
result (a path the lane wrote), done_at, carried_from, note, channel (linkedin | x | t1 | t2 | null: the portal tier
the item belongs to; set on mass and apply items, null on the rest).

build   Reads cards-<date>.md (decisions: the top `decisions:` line, then each card's `decision:` line, cards.decisions)
        and cards-<date>.json (the cards), then every later batch of the day, cards-<date>-b2.md/.json, -b3 ... (a later
        sweep's cards, numbered on), each with its own decisions. Every deep card answered yes becomes an item: a posts-and-people card is kind
        posts; a card of a person in conversation or a focused company (dream, warm, personal) is kind focused; a
        tailored page is kind resume; research is kind research. Mass (charter §7, one yes is enough): a yes
        quick-apply-base card is one item of kind mass, and a yes on the "[mass batch]" card is one mass item per
        listing it holds; the apply lane claims them (`claim --kind mass`) and submits on that yes. With
        --yes-quote (the owner's exact words) the mass items added are recorded at once in ONE approval file through
        scripts/kernel/approvals.py decide (--via, --session; work/approvals/ under --os-root), and each item carries
        its `approval:` id; without it they carry approval null and the lane records the yes before any submit.
        --with-quick-apply is accepted and changes nothing (mass cards are always queued). Prep: a yes card of
        interview_invite, interview_scheduling or ats_assessment mail, or any card whose action is prep, is kind prep
        (quick or deep) for the prep lane (`claim --kind prep`). Unfinished items of the
        newest earlier queue are carried over first (open and blocked as they were, claimed back to open with a note),
        then today's in card order; ranks are renumbered. Re-running build keeps every item already in today's queue
        (status, claims, results) and adds only cards not in it yet.
claim   Under the queue's lock: the open item of that kind with the lowest rank becomes claimed by the lane, written, and
        printed as JSON. With --channel only that channel's items are considered (an item with no channel is never
        taken by a channel claim); without it every channel is, as before. Exit 1 when nothing of that kind is open. Two lanes claiming at once never get the same item.
done    The item becomes done with its result path (the lane's hand-back to the Recruiter for review); --lane refuses an
        item another lane holds.
release An item goes back to open (or to blocked with --blocked REASON); its claim is cleared.
Every change takes an exclusive lock (fcntl.flock on queue-<date>.yaml.lock, --lock-timeout seconds, default 10, exit 2
when busy) and writes atomically (a temp file, then os.replace), so several lanes can work one queue at once.
Nothing here sends or submits anything: an item is work for a lane; outward acts still wait for the owner's yes.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cards as cards_mod  # noqa: E402
from js_common import JS_ROOT, os_root_for, cli_date, dept_day, fm  # noqa: E402

KINDS = ("research", "resume", "apply", "focused", "posts", "portal", "mass", "prep", "hunt")  # hunt: the Recruiter adds with `add --kind hunt`
CHANNELS = ("linkedin", "x", "t1", "t2")
CHANNEL_KINDS = ("mass", "apply")            # the kinds an apply node claims; build sets a channel on these only
# Platform id (targets/platforms.yaml) to channel, the portal tiers of the lane tree. Every other
# platform and every ATS or career page (greenhouse, ashby, workday, company-ats, hn, himalayas ...) is t2.
LINKEDIN_PLATFORMS = ("linkedin",)
X_PLATFORMS = ("x", "x-jobs")
T1_PLATFORMS = ("indeed", "ziprecruiter", "glassdoor", "yc", "wellfound", "google-jobs-search", "hiringcafe", "builtin",
                "dice", "jobright", "indeed-connector", "dice-connector", "ziprecruiter-connector")
HOST_CHANNELS = (("linkedin.com", "linkedin"), ("x.com", "x"), ("twitter.com", "x"), ("indeed.com", "t1"),
                 ("ziprecruiter.com", "t1"), ("glassdoor.com", "t1"), ("workatastartup.com", "t1"),
                 ("wellfound.com", "t1"), ("hiring.cafe", "t1"), ("hiringcafe.com", "t1"), ("builtin.com", "t1"),
                 ("builtinsf.com", "t1"), ("dice.com", "t1"), ("jobright.ai", "t1"))
PREP_LABELS = ("interview_invite", "interview_scheduling", "ats_assessment")
STATUSES = ("open", "claimed", "done", "blocked")
EFFORTS = ("E1", "E2", "E3", "E4", "E5")
DEFAULT_OUT = Path("state") / "recruiter"


class QueueError(Exception):
    pass


class Busy(QueueError):
    pass


def stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def queue_path(out: Path, day: str) -> Path:
    return out / f"queue-{day}.yaml"


@contextlib.contextmanager
def locked(path: Path, timeout: float = 10.0) -> Iterator[None]:
    """An exclusive lock on <queue>.lock for the whole read-change-write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path.with_name(path.name + ".lock"), "a+")
    try:
        deadline = time.monotonic() + max(timeout, 0)
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Busy(f"queue busy: {path.name} is locked by another lane; try again") from None
                time.sleep(0.02)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or not isinstance(data.get("items", []), list):
        raise QueueError(f"{path} is not a day queue")
    return data


def save(path: Path, data: dict[str, Any], now: datetime) -> None:
    data["updated"] = stamp(now)
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-queue-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def mail_label(card: dict[str, Any]) -> str:
    """A mail card's label: its own `label`, else the one its title carries ("[mail interview_invite] ...")."""
    if card.get("label"):
        return str(card["label"])
    m = re.match(r"^\[mail (\S+)\]", str(card.get("title") or ""))
    return m.group(1) if m else ""


def kind_for(card: dict[str, Any]) -> str | None:
    if card.get("action") in ("quick-apply-base", cards_mod.MASS_BATCH):
        return "mass"
    if card.get("action") == "prep" or (card.get("kind") == "mail" and mail_label(card) in PREP_LABELS):
        return "prep"                                    # the prep lane claims these 
    if card.get("mode") != "deep":
        return None
    if card.get("tier") == 0:
        return "posts"
    if card.get("tier") in (1, 2):
        return "focused"
    return "resume" if card.get("action") == "tailored page" else "research"


def channel_for_platform(pid: Any) -> str | None:
    """linkedin / x / t1 for those portals' ids, t2 for any other id, None for no id. A source ending in -alert is
    that portal's alert mail ("ziprecruiter-alert" is ziprecruiter)."""
    pid = re.sub(r"-alert$", "", str(pid or "").strip().lower())
    if not pid or pid in ("email", "email-alert"):
        return None
    if pid in LINKEDIN_PLATFORMS:
        return "linkedin"
    if pid in X_PLATFORMS:
        return "x"
    return "t1" if pid in T1_PLATFORMS else "t2"


def channel_for_url(url: Any) -> str | None:
    """By host when there is no job record: a known board host is its channel, any other link is a career page (t2)."""
    if not url:
        return None
    host = (re.match(r"^\w+://([^/:?#]+)", str(url).strip()) or re.match(r"^([^/:?#]+)", str(url).strip()))
    host = host.group(1).lower() if host else ""
    for dom, ch in HOST_CHANNELS:
        if host == dom or host.endswith("." + dom):
            return ch
    return "t2"


def channel_for_job(meta: dict[str, Any]) -> str:
    """A job record's channel. Alert mail goes by the portal that sent it (the record's `platform`), not by the board
    the alert lists; every other source goes by its own id; a record that names neither is a career page (t2)."""
    src = str(meta.get("source") or "").strip().lower()
    if src in ("email-alert", "email"):
        return channel_for_platform(meta.get("platform")) or "t2"
    return channel_for_platform(src) or channel_for_platform(meta.get("platform")) or "t2"


def item_channel(root: Path | None, links: dict[str, Any], source: Any = None) -> str | None:
    """The channel of one queue item: its job record's, else the listing's own source, else its link's host."""
    job = links.get("job")
    if root is not None and job:
        path = root / "jobs" / f"{job}.md"
        if path.is_file():
            try:
                meta = fm.load(path)[0]
            except Exception:  # noqa: BLE001 - an unreadable record falls through to the link
                meta = None
            if isinstance(meta, dict) and (meta.get("source") or meta.get("platform")):
                return channel_for_job(meta)
    if source:
        return channel_for_job({"source": source})
    return channel_for_url(links.get("url"))


def check_channel(channel: str | None) -> str | None:
    if channel is not None and channel not in CHANNELS:
        raise QueueError(f"unknown channel {channel!r}: one of {', '.join(CHANNELS)}")
    return channel


def _item(day: str, n: int, kind: str, effort: str, title: str, **extra: Any) -> dict[str, Any]:
    return {"id": f"Q-{day}-{n:02d}", "kind": kind, "effort": effort, "channel": extra.get("channel"), "rank": 0,
            "title": title,
            "card": extra.get("card"), "item_key": extra.get("item_key"), "links": extra.get("links") or {},
            "status": "open", "claimed_by": None, "claimed_at": None, "result": None, "done_at": None,
            "carried_from": extra.get("carried_from"), "note": extra.get("note"), "why": extra.get("why")}


def _next_n(items: list[dict[str, Any]], day: str) -> int:
    nums = [int(m.group(1)) for i in items if (m := re.fullmatch(rf"Q-{re.escape(day)}-(\d+)", str(i.get("id"))))]
    return max(nums, default=0) + 1


def _rerank(items: list[dict[str, Any]]) -> None:
    for r, it in enumerate(items, 1):
        it["rank"] = r


def earlier_queue(out: Path, day: str) -> Path | None:
    olds = sorted(p for p in out.glob("queue-*.yaml") if re.fullmatch(r"queue-\d{4}-\d{2}-\d{2}\.yaml", p.name)
                  and p.name < f"queue-{day}.yaml")
    return olds[-1] if olds else None


def card_batches(out: Path, day: str) -> list[tuple[list[dict[str, Any]], dict[int, str]]]:
    """Every card batch of the day, in order: cards-<day>.md/.json, then the later sweeps' cards-<day>-b2, -b3 ...
    (cards.py numbers them on, so card numbers are unique across batches); each with the decisions of its own .md."""
    pairs = [(out / f"cards-{day}.md", out / f"cards-{day}.json")]
    later = []
    for js_path in out.glob(f"cards-{day}-b*.json"):
        m = re.fullmatch(rf"cards-{re.escape(day)}-b(\d+)\.json", js_path.name)
        if m:
            later.append((int(m.group(1)), js_path.with_suffix(".md"), js_path))
    pairs += [(md, js) for _n, md, js in sorted(later)]
    out_: list[tuple[list[dict[str, Any]], dict[int, str]]] = []
    for md, js in pairs:
        if md.is_file() and js.is_file():
            cards = json.loads(js.read_text(encoding="utf-8")).get("cards") or []
            out_.append((cards, cards_mod.decisions(md.read_text(encoding="utf-8"))))
    return out_


def mass_sources(card: dict[str, Any]) -> list[dict[str, Any]]:
    """The listings one yes covers: the batch card's `items`, else the quick-apply-base card itself."""
    return list(card.get("items") or []) if card.get("action") == cards_mod.MASS_BATCH else [card]


def mass_title(src: dict[str, Any]) -> str:
    """The card's own line for that listing ("[greenhouse] Acme · AI Engineer · Remote US"), marked mass."""
    return f"[mass] {src.get('title') or src.get('item_key')}"


def record_yes(mass: list[dict[str, Any]], quote: str, via: str, session: str | None, os_root: Path,
               day: str) -> str:
    """ONE approval for the mass items a yes covers (scripts/kernel/approvals.py decide): the owner's words, the session, the
    time and one Exact content line per item. Returns the approval id."""
    import approvals                                     # the kernel's (js_common puts system/scripts on sys.path)
    ids = [str((it.get("links") or {}).get("job") or it.get("item_key")) for it in mass]
    lines = [f"{i} · {it['title'][len('[mass] '):] if it['title'].startswith('[mass] ') else it['title']} · "
             f"{(it.get('links') or {}).get('url') or 'no link'} · base resume, employer site (charter §7)"
             for i, it in zip(ids, mass)]
    cards_ids = sorted({str(it.get("card")) for it in mass if it.get("card")})
    try:
        aid, _path, _line = approvals.decide(Path(os_root), ids, via, quote, "yes", action="submit-form",
                                             target=f"mass batch {day}: {len(ids)} applications "
                                                    f"({', '.join(cards_ids)})",
                                             lines=lines, session=session, project="job-search",
                                             today=datetime.fromisoformat(day).date())
    except ValueError as exc:                            # nothing is queued when the yes cannot be recorded
        raise QueueError(f"the yes was not recorded: {exc}") from exc
    return aid


def build(out: Path, day: str, now: datetime, with_quick_apply: bool = False, timeout: float = 10.0,
          yes_quote: str | None = None, via: str = "chat", session: str | None = None,
          os_root: Path | None = None, root: Path | None = None) -> dict[str, Any]:
    batches = card_batches(out, day)
    if not batches:
        raise QueueError(f"no cards for {day} in {out} (cards-{day}.md and .json): run scripts/cards.py --write first")
    answered = [(c, answers) for cards, answers in batches for c in cards]   # decisions read per batch file
    path = queue_path(out, day)
    with locked(path, timeout):
        data = load(path) or {"type": "day-queue", "date": day, "built_by": "scripts/day_queue.py", "items": []}
        items: list[dict[str, Any]] = data["items"]
        have_cards = {i.get("card") for i in items if i.get("card")}
        have_ids = {i.get("id") for i in items}
        added = carried = 0
        if not items:                                    # carry over once, when today's queue is first made
            old = earlier_queue(out, day)
            for it in (load(old).get("items") or []) if old else []:
                if it.get("status") == "done" or it.get("id") in have_ids:
                    continue
                it = dict(it)
                if it.get("status") == "claimed":
                    it["note"] = (f"carried: was claimed by {it.get('claimed_by')} at {it.get('claimed_at')}; "
                                  + (it.get("note") or "")).strip("; ")
                    it.update(status="open", claimed_by=None, claimed_at=None)
                it["carried_from"] = it.get("carried_from") or old.name
                items.append(it)
                carried += 1
        for it in items:                                 # a mass item made before channels existed: set it now
            if it.get("kind") in CHANNEL_KINDS and not it.get("channel"):
                it["channel"] = item_channel(root, it.get("links") or {})
        n = _next_n(items, day)
        new_mass: list[dict[str, Any]] = []
        for c, answers in answered:
            if answers.get(int(c.get("n") or 0)) != "yes" or c.get("id") in have_cards:
                continue
            kind = kind_for(c)
            if kind is None:
                continue
            for src in mass_sources(c) if kind == "mass" else [c]:
                it = _item(day, n, kind, src.get("effort") or c.get("effort") or "E2",
                           mass_title(src) if kind == "mass" else (c.get("title") or c.get("item_key")),
                           card=c.get("id"), item_key=src.get("item_key"), why="; ".join(c.get("why") or []),
                           links={k: src.get(k) for k in ("url", "job", "slug", "company_slug") if src.get(k)})
                if kind in CHANNEL_KINDS:
                    it["channel"] = item_channel(root, it["links"], src.get("source"))
                if kind == "mass":
                    it["approval"] = None
                    new_mass.append(it)
                items.append(it)
                n += 1
                added += 1
        if new_mass and yes_quote:
            aid = record_yes(new_mass, yes_quote, via, session, os_root or os_root_for(out.resolve().parents[2]), day)
            for it in new_mass:
                it["approval"] = aid
        _rerank(items)
        data["items"] = items
        save(path, data, now)
    return {"path": str(path), "added": added, "carried": carried, "items": len(items)}


def mutate(out: Path, day: str, now: datetime, fn, timeout: float = 10.0) -> Any:
    path = queue_path(out, day)
    with locked(path, timeout):
        data = load(path)
        if not data:
            raise QueueError(f"no queue for {day} at {path}: run `day_queue.py build` first")
        result = fn(data["items"])
        save(path, data, now)
    return result


def find(items: list[dict[str, Any]], qid: str) -> dict[str, Any]:
    for it in items:
        if it.get("id") == qid:
            return it
    raise QueueError(f"no item {qid}")


def claim(out: Path, day: str, now: datetime, lane: str, kind: str, timeout: float = 10.0,
          channel: str | None = None) -> dict[str, Any] | None:
    check_channel(channel)

    def fn(items):
        open_ = sorted((i for i in items if i.get("status") == "open" and i.get("kind") == kind
                        and (channel is None or i.get("channel") == channel)),
                       key=lambda i: int(i.get("rank") or 0))
        if not open_:
            return None
        it = open_[0]
        it.update(status="claimed", claimed_by=lane, claimed_at=stamp(now))
        return dict(it)
    return mutate(out, day, now, fn, timeout)


def done(out: Path, day: str, now: datetime, qid: str, result: str, lane: str | None = None,
         timeout: float = 10.0) -> dict[str, Any]:
    def fn(items):
        it = find(items, qid)
        if lane and it.get("claimed_by") not in (None, lane):
            raise QueueError(f"{qid} is claimed by {it.get('claimed_by')}, not {lane}")
        if it.get("status") == "done" and it.get("result") != result:
            raise QueueError(f"{qid} is already done with {it.get('result')}")
        it.update(status="done", result=result, done_at=stamp(now))
        if lane and not it.get("claimed_by"):
            it["claimed_by"] = lane
        return dict(it)
    return mutate(out, day, now, fn, timeout)


def release(out: Path, day: str, now: datetime, qid: str, blocked: str | None = None,
            timeout: float = 10.0) -> dict[str, Any]:
    def fn(items):
        it = find(items, qid)
        if it.get("status") == "done":
            raise QueueError(f"{qid} is done; a done item is not released")
        it.update(status="blocked" if blocked else "open", claimed_by=None, claimed_at=None)
        if blocked:
            it["note"] = f"blocked: {blocked}"
        return dict(it)
    return mutate(out, day, now, fn, timeout)


def add(out: Path, day: str, now: datetime, kind: str, title: str, effort: str = "E2", links: dict | None = None,
        note: str | None = None, timeout: float = 10.0, channel: str | None = None) -> dict[str, Any]:
    check_channel(channel)
    path = queue_path(out, day)
    with locked(path, timeout):
        data = load(path) or {"type": "day-queue", "date": day, "built_by": "scripts/day_queue.py", "items": []}
        it = _item(day, _next_n(data["items"], day), kind, effort, title, links=links, note=note, channel=channel)
        data["items"].append(it)
        _rerank(data["items"])
        save(path, data, now)
    return it


def render(data: dict[str, Any], kind: str | None = None, status: str | None = None, channel: str | None = None) -> str:
    items = [i for i in data.get("items") or [] if (not kind or i.get("kind") == kind)
             and (not status or i.get("status") == status) and (not channel or i.get("channel") == channel)]
    counts = {s: sum(i.get("status") == s for i in data.get("items") or []) for s in STATUSES}
    L = [f"Day queue {data.get('date')}: " + " · ".join(f"{s} {n}" for s, n in counts.items())]
    for i in items:
        L.append(f"{i['rank']:>3}. {i['id']} [{i['kind']} {i['effort']}{' ' + i['channel'] if i.get('channel') else ''}] {i['status']}"
                 + (f" by {i['claimed_by']}" if i.get("claimed_by") else "") + f" · {i.get('title')}"
                 + (f" · result {i['result']}" if i.get("result") else "") + (f" · {i['note']}" if i.get("note") else ""))
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--root", type=Path, default=JS_ROOT)
    p.add_argument("--out", type=Path, default=None, help="folder (default: <root>/state/recruiter)")
    p.add_argument("--date", type=cli_date, default=None, help="the queue's day (default: today in the owner's time zone)")
    p.add_argument("--now", default=None, help="ISO instant (default: now)")
    p.add_argument("--lock-timeout", type=float, default=10.0)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--with-quick-apply", action="store_true", help="accepted, no effect: mass cards are always queued")
    b.add_argument("--yes-quote", default=None, help="the owner's exact words: record the mass yes in one approval file")
    b.add_argument("--via", default="chat", choices=("chat", "desktop", "phone"))
    b.add_argument("--session", default=None)
    b.add_argument("--os-root", type=Path, default=None, help="the OS root for work/approvals (default: derived)")
    c = sub.add_parser("claim")
    c.add_argument("--lane", required=True)
    c.add_argument("--kind", required=True, choices=KINDS)
    c.add_argument("--channel", default=None, choices=CHANNELS, help="claim only this channel's items")
    d = sub.add_parser("done")
    d.add_argument("--id", required=True)
    d.add_argument("--result", required=True)
    d.add_argument("--lane", default=None)
    r = sub.add_parser("release")
    r.add_argument("--id", required=True)
    r.add_argument("--blocked", default=None, metavar="REASON")
    a_ = sub.add_parser("add")
    a_.add_argument("--kind", required=True, choices=KINDS)
    a_.add_argument("--title", required=True)
    a_.add_argument("--effort", default="E2", choices=EFFORTS)
    a_.add_argument("--channel", default=None, choices=CHANNELS)
    for k in ("url", "job", "slug", "company-slug", "note"):
        a_.add_argument(f"--{k}", default=None)
    s = sub.add_parser("show")
    s.add_argument("--kind", default=None, choices=KINDS)
    s.add_argument("--status", default=None, choices=STATUSES)
    s.add_argument("--channel", default=None, choices=CHANNELS)
    s.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    now = datetime.fromisoformat(a.now.replace("Z", "+00:00")) if a.now else datetime.now(timezone.utc)
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    out = a.out or a.root / DEFAULT_OUT
    day = (a.date or dept_day(now)).isoformat()
    t = a.lock_timeout
    try:
        if a.cmd == "build":
            res = build(out, day, now, a.with_quick_apply, t, a.yes_quote, a.via, a.session,
                        a.os_root or os_root_for(a.root), a.root)
            print(f"queue {res['path']}: {res['items']} items ({res['added']} added from cards, {res['carried']} carried)")
        elif a.cmd == "claim":
            it = claim(out, day, now, a.lane, a.kind, t, a.channel)
            if it is None:
                print(f"nothing open of kind {a.kind}{' on channel ' + a.channel if a.channel else ''} in queue-{day}", file=sys.stderr)
                return 1
            print(json.dumps(it, indent=1))
        elif a.cmd == "done":
            print(json.dumps(done(out, day, now, a.id, a.result, a.lane, t), indent=1))
        elif a.cmd == "release":
            print(json.dumps(release(out, day, now, a.id, a.blocked, t), indent=1))
        elif a.cmd == "add":
            links = {k: v for k, v in (("url", a.url), ("job", a.job), ("slug", a.slug), ("company_slug", a.company_slug)) if v}
            print(json.dumps(add(out, day, now, a.kind, a.title, a.effort, links, a.note, t, a.channel), indent=1))
        else:
            data = load(queue_path(out, day))
            if not data:
                print(f"no queue for {day} at {queue_path(out, day)}", file=sys.stderr)
                return 1
            print(json.dumps(data, indent=1, default=str) if a.json else render(data, a.kind, a.status, a.channel), end="" if not a.json else "\n")
    except Busy as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except QueueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
