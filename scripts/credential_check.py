#!/usr/bin/env python3
"""credential_check.py - one detector for credentials in free text.

Used by scripts/apply_pack.py wherever it writes or checks free text: `account` (--notes, --company), the
state/accounts.yaml check that build and lint run, every record value a pack or the answer bank copies, and
lint's scan of a pack's record tables. Standard library only; no network.

    find_credential(text) -> str | None

returns why the text looks like it holds a credential, or None. The reason never repeats the secret, so it is
safe to print. What counts:

  keyword forms  a credential word and a value, in any case. After an explicit separator (a colon, "=",
                 "is" / "was" / "are", a spaced dash) any value counts: "password: X", "PASSWORD=X",
                 "my password is X", "pin - 1234", "login: X", "security answer is X"; so does "X is my
                 password". After a bare space, the password words (password, passcode, passphrase, pwd, pw,
                 p/w) take any value ("pw X", "password X"); the other words (pin, secret, token, otp, the
                 code and key phrases, login, sign-in, user name, user id) take a value that looks secret:
                 a digit or a symbol such as ! @ # $ in it ("pin 1234", "login X!2026", "otp 123456").
                 Never values: words that describe the credential instead of giving it ("password reset",
                 "login via Google", "PIN not required", "the password is set by the owner"), paths, URLs,
                 placeholders (<site>), possessives (the owner's), an aside in brackets after a bare space, and
                 after a login word an email address (the account's email, which the registry keeps).
  key prefixes   sk-, ghp_ (gho_, ghu_, ghs_, ghr_, github_pat_), xox?-, AKIA / ASIA, ya29., GOCSPX-, 1//,
                 a JSON web token (eyJ...), a PEM private key header.
  tokens         20 or more characters without spaces, with letters and digits, holding a random-looking
                 piece: 16 or more characters between separators (- _ . /) that mix upper and lower case, or
                 switch between letters and digits four times or more (hex keys). Slugs, branch names and dates
                 have short pieces. URLs, email addresses, host names, absolute paths and file names
                 (name.ext) are set aside first.
"""
from __future__ import annotations

import re
from typing import Any

import agency_config as cfg

# Longest phrases first: a regex takes the first branch that matches, so "pin code 1234" must meet
# "pin code" before "pin" (whose next word, "code", describes the credential instead of giving it).
_PASSWORD = r"pass\s?(?:word|code|phrase)s?|passwd|pasword|pwd|pw|p/w|pass(?=\s*[:=])"
_OTHER = (r"pin\s+(?:codes?|numbers?)|client\s+secrets?|secret\s+keys?|private\s+keys?"
          r"|(?:secret|security)\s+(?:questions?\s+)?answers?|(?:secret|security)\s+questions?"
          r"|one[-\s]?time\s+(?:pass)?(?:codes?|words?)|verification\s+codes?|security\s+codes?"
          r"|access\s+codes?|auth(?:entication)?\s+codes?|backup\s+codes?|recovery\s+(?:codes?|keys?|phrases?)"
          r"|(?:2fa|mfa|two[-\s]factor)\s+codes?|login\s+codes?|api[-_\s]?keys?|access[-_\s]?keys?"
          r"|(?:access|auth|bearer|api|refresh|session)[-_\s]?tokens?|pins?|secrets?|otps?|tokens?")
_LOGIN = r"log[-\s]?in|logon|sign[-\s]?in|user\s?names?|user\s?ids?"
# Between the word and its value: an explicit separator (a colon or "=", "is" / "was", a spaced dash) or just a
# space. Never nothing: "pin-drift", "token-based" and "{token}" are words, not a keyword and a value.
_EXPLICIT = r"(?:\s*[:=]|\s+(?:is|was|are|were|equals)\b|\s+[-–—](?=\s))+\s*"
KEYWORD_RE = re.compile(rf"(?<![\w/])(?:(?P<other>{_OTHER})|(?P<password>{_PASSWORD})|(?P<login>{_LOGIN}))\b"
                        rf"(?:(?P<explicit>{_EXPLICIT})|\s+)(?P<value>[^\s,;]+)", re.I)
def _owner_possessives() -> str:
    """The owner's first name as a possessive ("Alex's", "Alexs") for the "X is <owner>'s password" form; empty
    when no name is configured."""
    first = str(cfg.get("identity.name", "") or "").split()[:1]
    return "".join(f"|{re.escape(w.lower())}'?s" for w in first)


REVERSED_RE = re.compile(rf"(?P<value>[^\s,;:]+)\s+(?:is|was)\s+(?:my|the|our|his|her{_owner_possessives()})\s+"
                         rf"(?:{_OTHER}|{_PASSWORD}|{_LOGIN})\b", re.I)

PREFIXES = (
    ("an API key (sk- prefix)", re.compile(r"(?<![\w-])sk-(?:[A-Za-z0-9]+-)*[A-Za-z0-9_]{16,}")),
    ("a GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("a Slack token (xox prefix)", re.compile(r"\bxox[abeoprs]-[A-Za-z0-9-]{10,}")),
    ("an AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("a Google OAuth token (ya29.)", re.compile(r"\bya29\.[\w-]{10,}")),
    ("a Google OAuth client secret", re.compile(r"\bGOCSPX-[\w-]{10,}")),
    ("a Google refresh token", re.compile(r"(?<![\w/])1//[\w-]{20,}")),
    ("a JSON web token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*")),
    ("a private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)

_URL = re.compile(r"(?:https?|ftp)://\S+|\bwww\.\S+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_HOST = re.compile(r"\b(?:[a-z0-9-]+\.)+(?:com|org|net|io|co|ai|app|dev|jobs|example|edu|gov|us|uk|cafe|careers)\b"
                   r"(?:/\S*)?")
_TOKEN = re.compile(r"[^\s,;:'\"()<>\[\]{}|`]{20,}")
_PATHLIKE = re.compile(r"^(?:~|\.{1,2})?/|\.[A-Za-z]{1,4}\d?$")     # /abs, ~/x, ./x, or name.ext (md, docx, mp4)
_SECRETISH = re.compile(r"\d|[!@#$%^&*+=?~|\\]")
_WRAP = "\"'`()[]{}.,;:!?*"

# Words that follow a credential word without being its value.
BENIGN = frozenset("""
a an the and or but if then than not no none n/a na nil never ever only just also any each every either
neither both all same own other another new old first second next last
is are was were be been being will would can could may might must should shall do does did has have had get
gets got
to of in on at by for from with without via per into onto over under after before during until when while
through since about against like as
this that these those it its it's which who what where how why
he she they we you i me him her them us his hers their our your my mine himself herself themselves yourself
myself
set reset resets resetting change changed changes changing update updated expire expires expired expiry
expiring rotate rotated required require requires needed need needs optional mandatory necessary
rule rules policy policies requirement requirements length strength complexity format hint hints prompt prompts
page pages screen field fields box boxes form step link links button manager managers vault keychain
email emails emailed mail mailed sms text texted texts phone call app authenticator
sent send sends sending received recover recovery forgot forgotten lost pasted copied leaked revoked found
printed logged written stop
protected protection login logins log sign signin sign-in signon auth authentication verification verify verified
2fa mfa otp sso saml oauth
reuse reused shared share sharing stored store stores saved save saves kept keep typed type types entered enter
enters used use uses asked asks ask shown show hidden
here there later now today again once twice yet already separately separate
code codes number numbers question questions answer answers key keys token tokens credential credentials
account accounts user username
google linkedin microsoft apple okta github facebook workday icims taleo greenhouse lever ashby indeed glassdoor
""".split())


def _is_value(raw: str, login: bool, strict: bool) -> bool:
    """Is the word after a credential word its value? strict (after an explicit separator, and after the
    password words): any word that does not describe the credential; else it must look secret (a digit or a
    symbol)."""
    word = raw.strip(_WRAP)
    lower = word.lower()
    if not word or lower in BENIGN or raw.lstrip("`").startswith("<") or lower.endswith("'s"):
        return False
    if _URL.match(word) or _PATHLIKE.search(word) or (login and _EMAIL.fullmatch(word)):
        return False
    if strict:
        return True
    return not raw.startswith(("(", "[")) and bool(_SECRETISH.search(word))     # "(ev-x-001)" is an aside


def _token_shaped(token: str) -> bool:
    """A random-looking run: a piece of 16 or more characters (between - _ . /) that mixes upper and lower case
    in a token that has a digit, or that switches between letters and digits four times or more (a hex key).
    Slugs, branch names and dates have short pieces; paths and file names are set aside."""
    if _PATHLIKE.search(token) or not (re.search(r"[A-Za-z]", token) and re.search(r"\d", token)):
        return False
    for piece in re.split(r"[-_./]", token):
        if len(piece) < 16:
            continue
        if re.search(r"[a-z]", piece) and re.search(r"[A-Z]", piece):
            return True
        if len(re.findall(r"[A-Za-z]+|\d+", piece)) >= 5:
            return True
    return False


def find_credential(text: Any) -> str | None:
    """Why `text` looks like it holds a credential, or None. Never repeats the secret."""
    if not isinstance(text, str) or not text.strip():
        return None
    for reason, rx in PREFIXES:
        if rx.search(text):
            return reason
    for m in KEYWORD_RE.finditer(text):
        strict = m.group("explicit") is not None or m.group("password") is not None
        if _is_value(m.group("value"), m.group("login") is not None, strict):
            word = re.sub(r"\s+", " ", m.group("other") or m.group("password") or m.group("login")).lower()
            return f"the word '{word}' followed by a value"
    for m in REVERSED_RE.finditer(text):
        if _is_value(m.group("value"), False, True):
            return "a value followed by 'is my' and a credential word"
    rest = _HOST.sub(" ", _EMAIL.sub(" ", _URL.sub(" ", text)))
    for m in _TOKEN.finditer(rest):
        if _token_shaped(m.group(0)):
            return f"a {len(m.group(0))}-character token"
    return None
