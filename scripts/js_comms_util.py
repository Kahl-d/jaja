"""Shared helpers for the job-search communications/CRM scripts.

Used by gmail_pull, mail_classify, linkedin_export, prospects, outbox and calendar_hold so that
addresses, names, company keys, timestamps, hashing, markdown sections, untrusted-text handling
and approval files behave identically everywhere. Stdlib + js_common only.

Approval files (work/approvals/A-YYYY-NNNN.md, schema schemas/approval.yaml):
  * `content_hash` = sha256 of the canonical "## Exact content" section: exactly what goes out.
  * Each item also carries its own `content_hash` over (channel, to, subject, body) so the draft
    in outbox/ can be re-checked right before anything is drafted in Gmail or pasted.
  * Per-item decisions are the checkboxes under "## Decisions" (tick = include); they sit
    outside the hashed section, so unticking an item is a decision, not a tamper.
  * Receipts are appended under "## Receipts", also outside the hash.
"""
from __future__ import annotations

import hashlib
import os
import re
from html.parser import HTMLParser
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

import yaml

import agency_config as acfg
import js_common as jc

DEPT_TZ = jc.DEPT_TZ                           # the department's timezone; business days are local to it


def _lower_set(values: Any) -> frozenset[str]:
    return frozenset(str(v).strip().lower() for v in (values or []) if str(v).strip())


# The owner's own mailboxes, profile URL and names (config `identity`): mail from these is never a contact.
ACCOUNT_EMAILS = _lower_set(acfg.get("identity.account_emails") or acfg.get("identity.emails"))
LINKEDIN_ME_URLS = frozenset(u for u in [str(acfg.get("identity.linkedin_url") or "").strip()] if u)
LINKEDIN_ME_NAMES = _lower_set([acfg.get("identity.name"), *(acfg.get("identity.name_variants") or [])])
_OWNER_WORDS = sorted({n.split()[0] for n in LINKEDIN_ME_NAMES if n.split()})

FREE_MAIL = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "outlook.com", "hotmail.com", "live.com",
    "msn.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "pm.me",
    "hey.com", "fastmail.com", "zoho.com", "gmx.com", "yandex.com", "mail.com",
})
# Sender domains that belong to a tool, not to the person's employer.
VENDOR_DOMAINS = frozenset({
    "greenhouse-mail.io", "greenhouse.io", "lever.co", "ashbyhq.com", "myworkday.com", "myworkdayjobs.com",
    "workday.com", "icims.com", "smartrecruiters.com", "jobvite.com", "workablemail.com", "workable.com",
    "bamboohr.com", "rippling.com", "taleo.net", "successfactors.com", "breezy.hr", "recruitee.com",
    "teamtailor.com", "gem.com", "hackerrank.com", "hackerrankforwork.com", "codesignal.com",
    "codility.com", "hackerearth.com", "coderbyte.com", "testgorilla.com", "karat.io", "karat.com",
    "goodtime.io", "modernloop.io", "calendly.com", "prelude.co", "docusign.net", "docusign.com",
    "linkedin.com", "indeed.com", "glassdoor.com", "wellfound.com", "angel.co", "google.com",
    "github.com", "substack.com", "medium.com", "meetup.com", "zoom.us", "builtin.com", "builtinsf.com",
})
_TWO_PART_TLDS = frozenset({"co.uk", "org.uk", "ac.uk", "com.au", "co.jp", "co.in", "com.br", "co.nz",
                            "com.sg", "co.il", "com.mx"})
_CREDENTIALS = frozenset({"phd", "ph.d", "mba", "phr", "sphr", "shrm-cp", "shrm-scp", "cpa", "md", "jr",
                          "sr", "ii", "iii", "iv", "pmp", "cissp", "msc", "ms", "ma", "bsc", "cfa", "esq"})
_HONORIFICS = frozenset({"dr", "mr", "mrs", "ms", "mx", "prof", "professor", "sir"})
_GENERIC_COMPANY_WORDS = ("inc", "llc", "ltd", "corp", "corporation", "co", "company", "gmbh", "plc", "pbc",
                          "ai", "labs", "lab", "technologies", "technology", "tech", "systems", "hq", "group",
                          "holdings", "software", "io", "app", "health", "the", "partners", "consulting")


# --------------------------------------------------------------------------- roots
@dataclass(frozen=True)
class Roots:
    """Where records live. Tests point both at tmp dirs; production uses the real tree."""
    js: Path = jc.JS_ROOT
    os: Path = jc.OS_ROOT

    @property
    def approvals(self) -> Path:
        return self.os / "work" / "approvals"

    @property
    def prospects(self) -> Path:
        return self.js / "prospects"

    @property
    def outbox(self) -> Path:
        return self.js / "outbox"

    @property
    def touches(self) -> Path:
        return self.js / "touches"

    @property
    def harvest(self) -> Path:
        return self.js / "state" / "harvest"


# --------------------------------------------------------------------------- addresses / names
def decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(str(value))))
    except Exception:
        return str(value)


def _clean_address(name: str, email: str) -> tuple[str, str]:
    return display_name(decode_header_value(name)), (email or "").strip().lower()


def parse_address(value: str | None) -> tuple[str, str]:
    """'Name <Email>' -> (clean display name, lower-case email). RFC 2047 names are decoded."""
    name, email = parseaddr(str(value or ""))
    if not email and "@" not in str(value or ""):
        return display_name(decode_header_value(value or "")), ""
    return _clean_address(name, email)


def parse_address_list(value: str | None) -> list[tuple[str, str]]:
    out = []
    for name, email in getaddresses([str(value or "")]):
        if email or name:
            out.append(_clean_address(name, email))
    return out


def email_domain(email: str) -> str:
    return str(email or "").rsplit("@", 1)[-1].strip().lower() if email else ""


def registrable_domain(domain: str) -> str:
    labels = [p for p in str(domain or "").lower().strip(".").split(".") if p]
    if len(labels) >= 3 and ".".join(labels[-2:]) in _TWO_PART_TLDS:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def is_vendor_domain(domain: str) -> bool:
    d = str(domain or "").lower()
    return d in VENDOR_DOMAINS or registrable_domain(d) in VENDOR_DOMAINS


def is_free_mail(domain: str) -> bool:
    return registrable_domain(domain) in FREE_MAIL or str(domain or "").lower() in FREE_MAIL


def company_from_domain(domain_or_email: str) -> str | None:
    """Employer name guessed from a sender domain; None for free mail, tools and relays."""
    domain = email_domain(domain_or_email) if "@" in str(domain_or_email or "") else str(domain_or_email or "").lower()
    if not domain or is_free_mail(domain) or is_vendor_domain(domain):
        return None
    reg = registrable_domain(domain)
    label = reg.split(".")[0]
    if not label:
        return None
    if reg.endswith(".edu") and len(label) <= 5:
        return label.upper()
    return " ".join(part.capitalize() for part in label.split("-") if part)


def _strip_name_noise(name: str) -> str:
    s = str(name or "")
    s = re.sub(r"\s+via\s+\S.*$", "", s, flags=re.I)             # "Jane Doe via LinkedIn"
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", s)                   # (she/her) [Recruiter]
    s = re.split(r"\s[|•·]\s", s)[0]                              # "Jane Doe | Acme"
    if s.count(",") >= 1:
        head, _, tail = s.partition(",")
        tail_tokens = [t.strip(". ").lower() for t in re.split(r"[\s,]+", tail) if t.strip(". ")]
        if tail_tokens and all(t in _CREDENTIALS for t in tail_tokens):
            s = head
        elif len(head.split()) <= 2 and 1 <= len(tail.split()) <= 2 and not tail_tokens[0] in _CREDENTIALS:
            s = f"{tail.strip()} {head.strip()}"                   # "Raman, Priya" -> "Priya Raman"
        else:
            s = head
    tokens = [t for t in s.split() if t.strip(".").lower() not in _HONORIFICS]
    return " ".join(tokens)


def display_name(name: str | None) -> str:
    """Human display form: noise stripped, emoji dropped, accents kept."""
    s = _strip_name_noise(name or "")
    s = re.sub(r"[^\w\s.'\-]", " ", s)
    s = re.sub(r"\d+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .-")
    if s and (s == s.lower() or s == s.upper()):
        s = " ".join(w[:1].upper() + w[1:].lower() for w in s.split())
    return s


def normalize_person_name(name: str | None) -> str:
    """Matching key: 'first last', ascii, lower, no initials/credentials/honorifics."""
    s = _strip_name_noise(name or "")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()
    s = s.replace("'", "")
    s = re.sub(r"[^a-z\s\-]", " ", s)
    tokens = [t.strip("-") for t in s.split() if len(t.strip("-")) > 1]
    tokens = [t for t in tokens if t not in _HONORIFICS and t not in _CREDENTIALS]
    if not tokens:
        return ""
    return tokens[0] if len(tokens) == 1 else f"{tokens[0]} {tokens[-1]}"


_LI_RE = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?(?:www\.)?linkedin\.com/(?:comm/)?in/([^/?#\s\"'<>]+)", re.I)


def normalize_linkedin_url(url: str | None) -> str:
    m = _LI_RE.search(str(url or ""))
    return f"https://www.linkedin.com/in/{m.group(1).lower().rstrip('/')}" if m else ""


def find_linkedin_urls(text: str | None) -> list[str]:
    seen: list[str] = []
    for m in _LI_RE.finditer(str(text or "")):
        u = f"https://www.linkedin.com/in/{m.group(1).lower()}"
        if u not in seen:
            seen.append(u)
    return seen


def company_keys(name: str | None) -> set[str]:
    tokens = jc.normalize_company(name or "").split()
    if not tokens:
        return set()
    keys = {"".join(tokens)}
    stripped = list(tokens)
    while stripped and stripped[0] == "the":
        stripped.pop(0)
    while len(stripped) > 1 and stripped[-1] in _GENERIC_COMPANY_WORDS:
        stripped.pop()
    if stripped:
        keys.add("".join(stripped))
    if len(tokens) == 1:
        for suffix in _GENERIC_COMPANY_WORDS:
            tok = tokens[0]
            if tok.endswith(suffix) and len(tok) - len(suffix) >= 4:
                keys.add(tok[: -len(suffix)])
    return {k for k in keys if k}


def companies_match(a: str | None, b: str | None) -> bool:
    return bool(a and b and company_keys(a) & company_keys(b))


# --------------------------------------------------------------------------- time
def parse_ts(value: Any) -> datetime | None:
    """Anything timestamp-like -> aware UTC datetime (naive input is taken as UTC)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    elif isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        num = float(value)
        dt = datetime.fromtimestamp(num / 1000 if num > 10_000_000_000 else num, tz=timezone.utc)
    else:
        text = str(value).strip()
        dt = None
        if re.search(r"[A-Za-z]{3},? \d{1,2} [A-Za-z]{3} \d{4}", text) or re.match(r"\d{1,2} [A-Za-z]{3} \d{4} \d", text):
            try:
                dt = parsedate_to_datetime(text)
            except (TypeError, ValueError):
                dt = None
        if dt is None:
            cleaned = re.sub(r"\s+UTC$", "+00:00", text).replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(cleaned)
            except ValueError:
                for fmt in ("%m/%d/%y, %I:%M %p", "%m/%d/%Y, %I:%M %p", "%d %b %Y", "%b %d, %Y"):
                    try:
                        dt = datetime.strptime(text, fmt)
                        break
                    except ValueError:
                        continue
        if dt is None:
            d = jc.parse_date(text)
            if d is None:
                return None
            dt = datetime(d.year, d.month, d.day)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_utc_iso(value: Any) -> str | None:
    dt = parse_ts(value)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def local_date(value: Any) -> date | None:
    dt = parse_ts(value)
    return dt.astimezone(DEPT_TZ).date() if dt else None


# --------------------------------------------------------------------------- text / hashing
def canonical_text(text: str | None) -> str:
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [ln.rstrip() for ln in lines]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def sha256_text(text: str | None) -> str:
    return "sha256:" + hashlib.sha256(canonical_text(text).encode("utf-8")).hexdigest()


def one_line(text: str | None, cap: int = 160) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    s = s.replace("[[", "[").replace("]]", "]").replace("`", "'")
    if len(s) > cap:
        s = s[: cap - 1].rstrip() + "…"
    return s


_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


def _section_spans(body: str) -> list[tuple[str, int, int, int]]:
    """[(heading, heading_start, content_start, end)] for level-1/2 headings outside fences."""
    spans: list[list[Any]] = []
    fence: str | None = None
    pos = 0
    for line in body.splitlines(keepends=True):
        m = _FENCE_RE.match(line)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not line.strip()[len(m.group(1)):].strip():
                fence = None
        elif m:
            fence = m.group(1)
        else:
            h = re.match(r"^(#{1,2})\s+(.*?)\s*$", line)
            if h:
                if spans:
                    spans[-1][3] = pos
                spans.append([h.group(2), pos, pos + len(line), len(body)])
        pos += len(line)
    return [tuple(s) for s in spans]  # type: ignore[misc]


def section_text(body: str, heading: str) -> str | None:
    for name, _, start, end in _section_spans(body):
        if name.strip().lower() == heading.strip().lower():
            return body[start:end]
    return None


def replace_section(body: str, heading: str, content: str) -> str:
    content = content.rstrip("\n") + "\n"
    for name, hstart, start, end in _section_spans(body):
        if name.strip().lower() == heading.strip().lower():
            tail = body[end:]
            return body[:start] + content + ("\n" if tail else "") + tail
    chunk = f"## {heading}\n{content}"
    for name, hstart, _, _ in _section_spans(body):
        if name.strip().lower() == "timeline":
            return body[:hstart] + chunk + "\n" + body[hstart:]
    return body.rstrip("\n") + "\n\n" + chunk


def create_numbered(folder: Path, prefix: str, year: int, render: Callable[[str], str],
                    floor: int = 0) -> tuple[str, Path]:
    """Create folder/PREFIX-YEAR-NNNN.md with the next free number above every file there and above
    `floor` (numbers already cited elsewhere); safe against concurrent writers (O_EXCL, never overwrites)."""
    folder.mkdir(parents=True, exist_ok=True)
    pat = re.compile(rf"^{re.escape(prefix)}-{year}-(\d{{4}})\.md$")
    n = max([floor, *(int(m.group(1)) for p in folder.iterdir() if (m := pat.match(p.name)))]) + 1
    while True:
        ident = f"{prefix}-{year}-{n:04d}"
        path = folder / f"{ident}.md"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            n += 1
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(render(ident))
        return ident, path


# --------------------------------------------------------------------------- records (alias-free YAML)
class _NoAliasDumper(yaml.SafeDumper):
    """Never emit &anchors/*aliases (Obsidian's property editor does not understand them)."""

    def ignore_aliases(self, data: Any) -> bool:  # noqa: D401 - PyYAML hook
        return True


def dump_record(meta: dict[str, Any], body: str) -> str:
    if not meta:
        return body
    raw = yaml.dump(meta, Dumper=_NoAliasDumper, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return f"---\n{raw}---\n{body}"


def write_record(path: Path, meta: dict[str, Any], body: str) -> None:
    """Write a record; records get their `type:` and `links:` graph edges (scripts/graph_links.py)."""
    from graph_links import stamp_path
    stamp_path(path, meta)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_record(meta, body), encoding="utf-8")


def read_record(path: Path) -> tuple[dict[str, Any], str]:
    return jc.fm.load(path)


# --------------------------------------------------------------------------- untrusted content
INJECTION_PATTERNS = [re.compile(p, re.I) for p in (
    r"\b(ignore|disregard|forget|override)\b[^.!?\n]{0,40}\b(instructions?|prompts?|rules|guidelines|system message)\b",
    r"^\W*(system|assistant|developer|user)\s*:",
    r"</?\s*untrusted_[a-z_]*\s*>",
    r"\byou are now\b",
    r"\b(new|updated|revised) instructions\b",
    r"\b(note|message|instructions?) (to|for) (the |any )?(ai|llm|assistant|agent|model|bot|language model)s?\b",
    r"\b(ai|llm|gpt|claude|language model)s?\s*(agents?|assistants?|models?|systems?)?\s+(reading|processing|parsing|summari[sz]ing) (this|these)\b",
    r"\b(classify|label|categori[sz]e|tag|mark|treat)\b[^.!?\n]{0,30}\b(this|the) (email|message|mail|thread)\b[^.!?\n]{0,20}\bas\b",
    r"\b(label|classify|categori[sz]e|mark|tag) (it|this|them)\b",
    r"\bwith confidence\b|\bconfidence\s*[=:]",
    r"\blabel\s*[=:]\s*\w+",
    r"\bforward (the|this|my|your|all) (inbox|emails?|messages?|mail)\b",
    r"\b(reveal|print|show|leak) (your|the) (system prompt|instructions|api key|token|secrets?)\b",
    r"\bdo not (tell|inform|alert) (the user|the owner|anyone" + "".join("|" + re.escape(w) for w in _OWNER_WORDS) + r")\b",
    r"\b(admin|developer|god) mode\b|\bjailbreak\b",
)]


def _sentences(text: str) -> list[str]:
    out: list[str] = []
    start, i, n = 0, 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\n":
            out.append(text[start:i + 1])
            start = i = i + 1
            continue
        if ch in ".!?":
            j = i
            while j + 1 < n and text[j + 1] in ".!?":
                j += 1
            if j + 1 == n or text[j + 1].isspace():
                k = j + 1
                while k < n and text[k] in " \t":
                    k += 1
                out.append(text[start:k])
                start = i = k
                continue
            i = j
        i += 1
    if start < n:
        out.append(text[start:])
    return out


def split_sentences(text: str | None) -> list[str]:
    """Sentences (and newline-separated lines) of `text`, verbatim, in order."""
    return _sentences(str(text or ""))


def strip_injections(text: str | None) -> tuple[str, list[str]]:
    """Drop sentences that address an AI/agent (prompt injection). Unchanged text when clean."""
    text = str(text or "")
    hits: list[str] = []
    kept: list[str] = []
    for sentence in _sentences(text):
        probe = sentence.strip()
        if probe and any(p.search(probe) for p in INJECTION_PATTERNS):
            hits.append(one_line(probe, 200))
            if sentence.endswith("\n"):
                kept.append("\n")
            continue
        kept.append(sentence)
    if not hits:
        return text, []
    return re.sub(r"[ \t]{2,}", " ", "".join(kept)), hits


_HIDDEN_STYLE = r"(?:display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?:px|pt|em)?\s*[;\"']|opacity\s*:\s*0(?:\.0+)?\s*[;\"']|max-height\s*:\s*0)"


_VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
                        "source", "track", "wbr"})
_DROP_TAGS = frozenset({"script", "style", "head", "template", "noscript"})
_HIDDEN_STYLE_RE = re.compile(_HIDDEN_STYLE, re.I)


class _HiddenStripper(HTMLParser):
    """Re-emits visible markup and drops every element a human reader cannot see, with its whole subtree.

    Nesting is tracked with a stack (an end tag closes the innermost open element of that name and
    every element opened after it, as browsers do), so a hidden element holding a child of the same
    tag cannot leak the text after that child. A hidden element that never closes hides the rest of
    the document: when in doubt this errs toward hiding, never toward showing. The one implied end
    honoured is HTML's own: `<body>` closes a `<head>` left open (end tag omitted, as the spec allows),
    and only when nothing else is open inside that head."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self.skip: list[str] = []          # open tags inside the hidden subtree, outermost first

    @staticmethod
    def _hidden(tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        if tag in _DROP_TAGS:
            return True
        for key, value in attrs:
            key = (key or "").lower()
            if key == "hidden":
                return True
            if key == "style" and value and _HIDDEN_STYLE_RE.search(value + ";"):
                return True
        return False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "body" and self.skip == ["head"]:
            self.skip.clear()               # HTML's implied </head>: a ZipRecruiter digest omits it (J22)
        if self.skip:
            if tag not in _VOID_TAGS:
                self.skip.append(tag)
            return
        if self._hidden(tag, attrs):
            if tag not in _VOID_TAGS:
                self.skip.append(tag)
            return
        self.out.append(self.get_starttag_text() or f"<{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skip or self._hidden(tag, attrs):
            return
        self.out.append(self.get_starttag_text() or f"<{tag}/>")

    def handle_endtag(self, tag: str) -> None:
        if self.skip:
            if tag in self.skip:            # close it and everything opened inside it (implied ends)
                while self.skip and self.skip.pop() != tag:
                    pass
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.out.append(data)

    def handle_entityref(self, name: str) -> None:
        if not self.skip:
            self.out.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self.skip:
            self.out.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:  # comments are never shown
        return

    def handle_decl(self, decl: str) -> None:
        return

    def handle_pi(self, data: str) -> None:
        return

    def unknown_decl(self, data: str) -> None:
        return


def strip_hidden_html(html: str | None) -> str:
    """Remove comments, scripts/styles and elements hidden from a human reader (injection vector),
    whole subtrees included, keeping visible markup for link and text extraction."""
    parser = _HiddenStripper()
    try:
        parser.feed(str(html or ""))
        parser.close()
    except Exception:  # noqa: BLE001 - a malformed document must never leak hidden text: show nothing
        return ""
    return "".join(parser.out)


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\w)")


def redact_pii(text: str | None) -> str:
    return _PHONE_RE.sub("[phone]", _EMAIL_RE.sub("[email]", str(text or "")))


def format_table(rows: Iterable[dict[str, Any]], columns: list[str]) -> str:
    rows = list(rows)
    cells = [[("" if r.get(c) is None else str(r.get(c))) for c in columns] for r in rows]
    widths = [max([len(c)] + [len(row[i]) for row in cells]) for i, c in enumerate(columns)]
    line = lambda vals: "  ".join(v.ljust(w) for v, w in zip(vals, widths)).rstrip()  # noqa: E731
    out = [line(columns), line(["-" * w for w in widths])]
    out.extend(line(row) for row in cells)
    return "\n".join(out)


# --------------------------------------------------------------------------- approval files
@dataclass
class ApprovalItem:
    id: str
    channel: str
    target: str
    body: str
    subject: str = ""
    label: str = ""
    prospect: str | None = None
    thread_id: str | None = None
    checks: list[str] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)      # "path sha256:<hex>" (attachment_digests)
    edit_field: str | None = None                             # profile edits: headline, about, experience:<id>
    old_text: str | None = None                               # profile edits: the current text (the "before")

    @property
    def canonical(self) -> str:
        return canonical_item(self.channel, self.target, self.subject, self.body, self.attachments,
                              field=self.edit_field, old_text=self.old_text)

    @property
    def content_hash(self) -> str:
        return sha256_text(self.canonical)


def canonical_item(channel: str, target: str, subject: str | None, body: str,
                   attachments: Iterable[str] = (), *, field: str | None = None,
                   old_text: str | None = None) -> str:
    """What an item's content hash covers. `field` and `old_text` (profile edits) join only when present, so
    every other item hashes exactly as before."""
    head = f"channel: {channel}\nto: {target}\nsubject: {subject or ''}"
    for att in attachments or ():
        head += f"\nattachment: {att}"
    if field:
        head += f"\nfield: {field}"
    tail = f"\n\nbefore:\n{canonical_text(old_text)}" if old_text is not None else ""
    return canonical_text(f"{head}\n\n{canonical_text(body)}{tail}")


def attachment_digests(root: Path, paths: Iterable[str] | None) -> list[str]:
    """["<path relative to root> sha256:<hex of the bytes>", ...]; a missing file raises."""
    out = []
    for rel in paths or ():
        p = Path(root) / str(rel)
        if not p.is_file():
            raise FileNotFoundError(f"attachment not found: {rel}")
        out.append(f"{rel} sha256:{hashlib.sha256(p.read_bytes()).hexdigest()}")
    return out


def draft_content_hash(root: Path, meta: dict[str, Any], body: str) -> str:
    """Hash of what would go out for an outbox draft: channel, recipient, subject, body, attachments."""
    try:
        atts = attachment_digests(root, meta.get("attachments"))
    except FileNotFoundError as exc:
        atts = [f"<missing: {exc}>"]
    old_text = meta.get("old_text")
    return sha256_text(canonical_item(meta.get("channel") or "", meta.get("to") or "", meta.get("subject"), body, atts,
                                      field=meta.get("field") or None,
                                      old_text=None if old_text is None else str(old_text)))


def _fence_for(body: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", body or "")), default=0)
    return "`" * max(4, longest + 1)


def render_exact_content(items: list[ApprovalItem]) -> str:
    parts = []
    for n, it in enumerate(items, 1):
        fence = _fence_for(it.body + "\n" + (it.old_text or ""))
        lines = [f"### {n}. {it.label or it.target} · {it.channel}", f"<!-- item:{it.id} -->", f"- To: {it.target}"]
        if it.edit_field:
            lines.append(f"- Field: {it.edit_field}")
        if it.subject:
            lines.append(f"- Subject: {it.subject}")
        if it.thread_id:
            lines.append(f"- Thread: {it.thread_id}")
        if it.attachments:
            lines.append(f"- Attachments: {'; '.join(it.attachments)}")
        lines.append(f"- Checks: {'; '.join(it.checks) if it.checks else 'none'}")
        if it.old_text is not None:
            lines += ["", "Before (the current text):", "", f"{fence}before", canonical_text(it.old_text), fence,
                      "", "After (the new text):"]
        lines += ["", f"{fence}text", canonical_text(it.body), fence]
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def write_approval(roots: Roots, *, action: str, items: list[ApprovalItem], requested_by: str, why: str,
                   effect: str, today: date, ticket: str | None = None, risk: str = "low",
                   project: str = "job-search", target: str | None = None,
                   draft_refs: list[str] | None = None) -> tuple[str, Path]:
    if not items:
        raise ValueError("an approval needs at least one item")
    exact = render_exact_content(items)
    channels = sorted({it.channel for it in items})
    target = target or f"{len(items)} item(s) via {', '.join(channels)}: " + ", ".join(
        (it.label or it.target) for it in items[:5]) + (" …" if len(items) > 5 else "")
    decisions = "\n".join(f"- [x] {it.id} · {it.label or it.target} · {it.channel}" for it in items)

    def render(aid: str) -> str:
        meta = {
            "type": "approval", "id": aid, "action": action, "requested_by": requested_by, "ticket": ticket, "target": target,
            "draft_ref": draft_refs or [], "diff": "inline: see Exact content", "risk": risk,
            "status": "pending", "content_hash": sha256_text(exact), "decided_at": None, "decided_via": None,
            "created": today, "expires": today + timedelta(days=7), "project": project,
            "items": [{"id": it.id, "prospect": it.prospect, "channel": it.channel, "target": it.target,
                       "subject": it.subject or None, "thread_id": it.thread_id,
                       "attachments": list(it.attachments) or None, "content_hash": it.content_hash} for it in items],
        }
        body = (
            f"# {action} → {target}\n\n"
            "<!-- Contract: system/templates/approval.md. content_hash = sha256 of the canonical\n"
            "     \"Exact content\" section. Only the owner changes status. -->\n\n"
            "## Decisions\n"
            "Ticked items are included. Untick any item to leave it out, then set `status: approved` (or\n"
            "`rejected`) in the frontmatter and fill `decided_at` (YYYY-MM-DD) and `decided_via`. Do not edit\n"
            "the Exact content: any edit voids the approval; ask the drafter for a new version instead.\n\n"
            f"{decisions}\n\n"
            "## Exact content\n\n"
            f"{exact}\n\n"
            "## Why this is being sent\n"
            f"{why}\n\n"
            "## What changes if approved\n"
            f"{effect}\n\n"
            "## Receipts\n"
        )
        return dump_record(meta, body)

    return create_numbered(roots.approvals, "A", today.year, render, floor=highest_approval_number(roots, today.year))


def highest_approval_number(roots: Roots, year: int) -> int:
    """The highest NNNN of any A-<year>-NNNN already in use: approval files (work/approvals and, at any
    depth, work/approvals/archive) and ids cited in outbox drafts and prospect records. A new approval
    id goes above all of them, so an id a timeline or draft already cites is never handed out again."""
    cite = re.compile(rf"\bA-{year}-(\d{{4}})\b")
    best = 0
    folders = [(roots.approvals, "*.md"), (roots.approvals / "archive", "**/*.md")]
    for folder, pattern in folders:
        if folder.is_dir():
            for path in folder.glob(pattern):
                for m in cite.finditer(path.name):
                    best = max(best, int(m.group(1)))
    for folder in (roots.outbox, roots.prospects):
        if folder.is_dir():
            for path in folder.glob("*.md"):
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                for m in cite.finditer(text):
                    best = max(best, int(m.group(1)))
    return best


@dataclass
class Approval:
    path: Path
    meta: dict[str, Any]
    body: str
    items: list[dict[str, Any]]
    blocks: dict[str, str]
    decisions: dict[str, bool]
    hash_ok: bool
    decision_ids: list[str] = field(default_factory=list)   # every checkbox line's id, in order (duplicates kept)

    @property
    def id(self) -> str:
        return str(self.meta.get("id") or self.path.stem)


def _parse_blocks(exact: str, items: list[dict[str, Any]]) -> dict[str, str]:
    channels = {str(i.get("id")): str(i.get("channel") or "") for i in items}
    blocks: dict[str, str] = {}
    chunks = re.split(r"<!-- item:(\S+?) -->", exact)
    for k in range(1, len(chunks) - 1, 2):
        item_id, chunk = chunks[k], chunks[k + 1]
        to = re.search(r"^- To: (.*)$", chunk, re.M)
        subject = re.search(r"^- Subject: (.*)$", chunk, re.M)
        atts = re.search(r"^- Attachments: (.*)$", chunk, re.M)
        fld = re.search(r"^- Field: (.*)$", chunk, re.M)
        before = re.search(r"^(`{4,})before\n(.*?)\n?\1\s*$", chunk, re.M | re.S)
        fence = re.search(r"^(`{4,})text\n(.*?)\n\1\s*$", chunk, re.M | re.S)
        if not (to and fence):
            continue
        blocks[item_id] = canonical_item(channels.get(item_id, ""), to.group(1).strip(),
                                         subject.group(1).strip() if subject else "", fence.group(2),
                                         [a.strip() for a in atts.group(1).split("; ")] if atts else (),
                                         field=fld.group(1).strip() if fld else None,
                                         old_text=before.group(2) if before else None)
    return blocks


def parse_approval(path: Path) -> Approval:
    meta, body = jc.fm.load(path)
    items = list(meta.get("items") or [])
    exact = section_text(body, "Exact content")
    hash_ok = exact is not None and sha256_text(exact) == str(meta.get("content_hash") or "")
    decisions: dict[str, bool] = {}
    decision_ids: list[str] = []
    for line in (section_text(body, "Decisions") or "").splitlines():
        m = re.match(r"^\s*[-*]\s*\[( |x|X)\]\s+(\S+)", line)
        if m:
            decisions[m.group(2)] = m.group(1).lower() == "x"
            decision_ids.append(m.group(2))
    return Approval(path=path, meta=meta, body=body, items=items,
                    blocks=_parse_blocks(exact or "", items), decisions=decisions, hash_ok=hash_ok,
                    decision_ids=decision_ids)


def approval_problems(appr: Approval, today: date) -> list[str]:
    """Everything that must stop execution. Empty list = safe to act on the ticked items."""
    meta = appr.meta
    problems: list[str] = []
    status = str(meta.get("status") or "")
    if status != "approved":
        problems.append(f"status is {status or 'missing'}, not approved")
    if not appr.hash_ok:
        problems.append("content hash mismatch: the Exact content changed after the request")
    created = jc.parse_date(meta.get("created"))
    expires = jc.parse_date(meta.get("expires")) or (created + timedelta(days=7) if created else None)
    if expires and today > expires:
        problems.append(f"expired on {expires}")
    if status == "approved":
        decided = jc.parse_date(meta.get("decided_at"))
        if not decided:
            problems.append("decided_at is empty: the owner records the decision date")
        elif (today - decided).days > 7:
            problems.append("the decision is older than 7 days")
    for item in appr.items:
        chunk = appr.blocks.get(str(item.get("id")))
        if chunk is None or sha256_text(chunk) != item.get("content_hash"):
            problems.append(f"item {item.get('id')}: shown content does not match its content hash")
    # The ticks decide what is acted on: each item needs exactly one readable Decisions line, and no line may name
    # anything else (a bolded or mistyped id used to read as "unticked" without a word; I-047 item 13).
    item_ids = [str(i.get("id")) for i in appr.items]
    for iid in item_ids:
        n = appr.decision_ids.count(iid)
        if n != 1:
            problems.append(f"item {iid}: {n} Decisions lines (exactly one `- [x] {iid}` or `- [ ] {iid}` is needed)")
    for did in dict.fromkeys(appr.decision_ids):
        if did not in item_ids:
            problems.append(f"a Decisions line names {did!r}, which is not an item of this approval")
    return problems


def set_frontmatter(path: Path, **fields: Any) -> dict[str, Any]:
    meta, body = jc.fm.load(path)
    meta.update(fields)
    write_record(path, meta, body)
    return meta


def append_receipt(path: Path, line: str) -> None:
    meta, body = jc.fm.load(path)
    if section_text(body, "Receipts") is None:
        body = body.rstrip("\n") + "\n\n## Receipts\n"
    body = body.rstrip("\n") + f"\n- {one_line(line, 400)}\n"
    write_record(path, meta, body)
