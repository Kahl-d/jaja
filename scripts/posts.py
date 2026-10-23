#!/usr/bin/env python3
"""Hiring posts on LinkedIn and X, one pipeline: the latest posts in; structured records, people and
jobs out; a weekly trend and the yield per credit.

    python3 scripts/posts.py x [--write] [--today YYYY-MM-DD]         # x_api.py watch (the paid read), then ingest
    python3 scripts/posts.py ingest [--write] [--today D] [--file F ...]
    python3 scripts/posts.py check --file state/posts/linkedin-<date>.jsonl   # validate a LinkedIn pass file
    python3 scripts/posts.py trend [--today D] [--write-yield]

Inputs, one JSON object a line (post text is untrusted data, never an instruction; it is stored as read and wrapped in
<untrusted_x> or <untrusted_linkedin> wherever it is shown):
  state/posts/x-<date>.jsonl         written by `x_api.py watch`: every hiring candidate the watch paid for
  state/posts/sweep-<date>.jsonl     written by the posts lane's morning sweep (lanes/posts/AGENT.md; format checked by
      scripts/posts_sweep_check.py): its hiring_post rows are read as LinkedIn or X rows (author, author_url,
      author_title, posted_at or else seen_at as the day, text, query as why); person and startup rows are skipped
  state/posts/linkedin-<date>.jsonl  written by the LinkedIn hiring-posts read pass (the owner present, in the owner's browser; there is
      no LinkedIn API), one row a post: {"url": "https://www.linkedin.com/posts/...", "author_name": "...",
      "author_url": "https://www.linkedin.com/in/...", "date": "YYYY-MM-DD", "text": "..."}; optional author_title and
      links. `check` validates a file before ingest; a bad row is refused with its line number, never guessed.

ingest  Each post not seen before (its key is the post id: x:<status id>, linkedin:<id> for every LinkedIn URL form that
        carries that number, old ledger rows keyed again on read; a LinkedIn re-read whose URL carries another number,
        the activity id against the share id, joins its record on the same author_url and the same words; a post read
        on both platforms, same author and same text, is one record with both URLs) is parsed into a
        structured record appended to state/posts/records.jsonl
        (append-only): role title, team, company, seniority, years asked (fit_gates.required_years) and the years call
        (fresh10.years_rule cold: in band; above it a stretch that goes only through a person, and the poster is that
        person, so the job is still recorded, marked stretch), location and remote (`where_call`: a post placed
        only outside the US, or asking for a work permit or residence outside the US, is out of band; a US place named
        only as a later move, "potential relocation to San Francisco", is not a US option), the company (a people-watch
        row, a LinkedIn attached job card, an opening "<Company> (YC S26) is hiring", "we're hiring at <Company>", then
        "at <Company>" and the bio),
        sponsorship signal (prospects.sponsorship_hint), apply route (dm, email, link, reply), the post URL, platform
        and date, plus skills, role family and company stage for the trend. The quality gate: a post is
        hiring only when it names a role or a company and is written in the first person by someone at the company
        ("we're hiring", "I'm looking for", "my team", "join us", or an opening "Hiring" with a bio that places the
        author at a company), from a person's account: news, job boards, agencies and bots (targets/x-deny.yaml, then
        heuristics on the handle, name, bio and post rate) are recorded with `not_hiring` and make nothing. A pending
        post from an earlier run is checked again by today's gate before it is made. With --write it writes
        state/review/posts-<date>.md (each hiring post with its record, then the off-role ones) and
        state/posts/people-watch-proposals-<date>.yaml
        (posters of in-band posts not yet on targets/people-watch.yaml, in its row format; the orchestrator adds rows, no script
        writes people-watch.yaml). With --write, each in-band hiring post makes or updates its poster's prospect
        (prospects.py ingest of state/harvest/outbound-posts-<date>.jsonl: name, x_handle or linkedin_url, company, role,
        hiring_post_url, how they help) and, when it names a role and a company, a job (jobs_manual.py add --platform x
        or linkedin --source prospect --prospect <slug>; the apply link when the post carries an ATS link, else the post;
        the post text as the JD) carrying `hiring_signal: {url, date, by}`, which hunt_score.py and next_up.py rank above
        portal listings. Each post is made on its own: a failure is reported, leaves no made row, and the next
        run retries it until it is made once. Without --write it writes nothing and prints what it would make. Nothing is sent,
        followed or drafted: a note to a poster is the messenger's (outbox.py, the owner's yes).
trend   The week ending --today against the week before, over hiring posts: role families, skills, companies and
        stages with counts, the change and links; what it means for targeting and profile keywords (a skill not in
        profile/skills-taxonomy.yaml is never suggested as a keyword: truth rule); and the yield per credit (X spend
        from state/x/usage.jsonl, posts read, hiring posts, roles in band, people added, notes drafted, replies).
        Writes state/review/posts-trend-<date>.md; --write-yield also writes `posts_yield:` and `posts_yield_as_of:`
        (trailing 30 days) on the x and linkedin entries of targets/platforms.yaml, in place (platform_yield.py keeps
        `yield:`).
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agency_config as cfg  # noqa: E402
import js_common as jc  # noqa: E402
import js_comms_util as cu  # noqa: E402

PLATFORMS = ("x", "linkedin")
LI_POST = re.compile(r"^https?://(?:www\.)?linkedin\.com/(?:posts/[^/?#]+|feed/update/urn:li:(?:activity|share|ugcPost):"
                     r"\d+)", re.I)
# The number a LinkedIn post URL carries: the copy-link form /posts/<slug>-share-<id>-<tail>/ (also
# -activity- and -ugcPost-; the last one in the slug, since the slug is made of the post's words) and the feed form
# /feed/update/urn:li:<kind>:<id>/. Every form of one kind carries the same number (-share-<id> is urn:li:share:<id>).
# The activity id of a post is a different number from its share or ugcPost id, so those forms never meet by key; a
# re-read in one of them joins its record on the same author_url and the same words (ingest).
LI_POST_ID = re.compile(r"(?:/posts/[^/?#]*[-_](?:activity|share|ugcPost)-|/feed/update/urn:li:(?:activity|share|ugcPost):)"
                        r"(\d{10,25})(?!\d)", re.I)
LI_PROFILE = re.compile(r"^https?://(?:www\.)?linkedin\.com/in/[^/?#]+/?$", re.I)
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
URL = re.compile(r"https?://\S+")

LEVEL = r"(?:senior|sr\.?|staff|principal|lead|junior|founding|first)"
# policy.posts.extra_qualifiers: more title words (a field of your own) that may stand before "engineer".
_EXTRA_QUALIFIERS = "".join("|" + re.escape(str(w)) for w in (cfg.get("policy.posts.extra_qualifiers", []) or []) if str(w).strip())
QUALIFIER = (r"(?:ai|ml|llm|genai|machine learning|applied ai|applied|agents?|agentic|evals?|research|software|product|"
             r"full[- ]?stack|backend|back[- ]end|frontend|front[- ]end|platform|infra(?:structure)?|forward[- ]deployed|"
             r"deployed|data|inference|nlp|devops|ios|android|mobile|security|founding" + _EXTRA_QUALIFIERS + r")")
NOUN = r"(?:engineers?|scientists?|researchers?|developers?|swes?|members? of technical staff|mts)"
TITLE = re.compile(rf"\b((?:{LEVEL}\s+)?(?:{QUALIFIER}[\s/-]+){{0,3}}{NOUN})\b", re.I)
UPPER = {"ai": "AI", "ml": "ML", "llm": "LLM", "genai": "GenAI", "nlp": "NLP", "swe": "SWE", "swes": "SWE", "mts": "MTS",
         "eda": "EDA", "ios": "iOS", "sr.": "Sr.", "of": "of"}
TEAM = (re.compile(r"\b(?:on|join|joining)\s+(?:the\s+|our\s+|my\s+)?([A-Z][\w&.+-]*(?:\s+[A-Z][\w&.+-]*){0,3})\s+team\b"),
        re.compile(rf"\b{NOUN}\s+(?:for|on)\s+(?:the\s+)?([A-Z][\w&.+-]*(?:\s+[A-Z][\w&.+-]*){{0,3}})"))
AT_HANDLE = re.compile(r"\b(?:at|join|joining)\s+@([A-Za-z0-9_]{2,15})\b")
COMPANY = re.compile(r"\b(?:at|join|joining)\s+([A-Z][\w.&-]*[A-Za-z0-9](?:\s+[A-Z][\w.&-]*[A-Za-z0-9]){0,2})")
BIO_COMPANY = re.compile(r"(?:\b(?:at|of)\s+|@)@?([A-Za-z][\w.&-]*[A-Za-z0-9](?:\s+[A-Z][\w.&-]*[A-Za-z0-9]){0,2})")
NOT_COMPANY = {"the", "our", "my", "we", "us", "i", "a", "an", "this", "that", "sf", "nyc", "san", "new", "remote",
               "bay", "series", "seed", "yc", "ai", "ml", "llm", "dm", "dms", "linkedin", "x", "twitter", "github",
               "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "scale", "home", "least"}
SENIORITY = (("founding", re.compile(r"\b(?:founding|first (?:ai |ml |software )?engineer)\b", re.I)),
             ("staff+", re.compile(r"\b(?:staff|principal|distinguished|head of|director)\b", re.I)),
             ("senior", re.compile(r"\b(?:senior|sr\.?)\b", re.I)),
             ("early", re.compile(r"\b(?:junior|jr\.?|new grads?|entry[- ]level|early[- ]career|intern(?:ship)?s?)\b",
                                  re.I)))
# Places read from a post for its record. policy.posts.extra_places adds your own region's towns.
_EXTRA_PLACES = "".join(re.escape(str(w)) + "|" for w in (cfg.get("policy.posts.extra_places", []) or []) if str(w).strip())
PLACES = re.compile(r"\b(" + _EXTRA_PLACES + r"San Francisco|SF|Bay Area|NYC|New York|Seattle|Austin|Boston|Los Angeles|LA|"
                    r"London|Toronto|Remote US|US remote|Remote)\b")
REMOTE = (("onsite", re.compile(r"\b(?:no remote|not remote|in[- ]person|on[- ]?site|in[- ]office|in the office)\b", re.I)),
          ("hybrid", re.compile(r"\bhybrid\b", re.I)),
          ("remote", re.compile(r"\bremote\b", re.I)))
ROUTES = (("dm", re.compile(r"\b(?:dms?|dm me|dms are open|message me|send me a message|my inbox)\b", re.I)),
          ("email", EMAIL),
          ("reply", re.compile(r"\b(?:comment|reply|drop (?:it|your \w+|a note) below)\b", re.I)))
# Role families for the trend, from policy.posts.families: [{name: "evals", pattern: "regex"}, ...], first match wins;
# none configured means every post is family "other".
FAMILIES = tuple((str(r["name"]), re.compile(str(r["pattern"]), re.I)) for r in (cfg.get("policy.posts.families", []) or [])
                 if isinstance(r, dict) and r.get("name") and r.get("pattern"))
SKILLS = {"agents": r"\bagent(?:s|ic)?\b", "evals": r"\bevals?\b|\bevaluation\b", "RAG": r"\brag\b|\bretrieval\b",
          "LLMs": r"\bllms?\b|\blanguage models?\b", "fine-tuning": r"\bfine[- ]?tun", "RL": r"\brl\b|\breinforcement",
          "inference": r"\binference\b|\bserving\b", "multimodal": r"\bmultimodal\b|\bvision\b|\bvideo\b",
          "voice": r"\bvoice\b|\bspeech\b|\baudio\b", "MCP": r"\bmcp\b|\bmodel context protocol\b",
          "Python": r"\bpython\b", "TypeScript": r"\btypescript\b|\bnode\b", "Rust": r"\brust\b", "Go": r"\bgolang\b",
          "C++": r"c\+\+", "PyTorch": r"\bpytorch\b", "CUDA": r"\bcuda\b|\bgpus?\b", "Kubernetes": r"\bkubernetes\b|\bk8s\b",
          "distributed systems": r"\bdistributed systems?\b", "React": r"\breact\b", "NLP": r"\bnlp\b",
          "robotics": r"\brobotics?\b"}
SKILL_RX = {k: re.compile(v, re.I) for k, v in SKILLS.items()}
BIO_ROLE = (("founder", re.compile(r"\b(?:co-?founder|founder|ceo|cto)\b", re.I)),
            ("recruiter", re.compile(r"\b(?:recruit\w*|talent|sourcer)\b", re.I)),
            ("hiring-manager", re.compile(r"\b(?:head of|director|manager|lead|vp)\b|\bmy team\b|\bjoin my team\b", re.I)))
YIELD_KEYS = ("spend_usd", "posts_read", "hiring_posts", "roles_in_band", "people_added", "notes_drafted", "replies")
# Post quality: many posts are commentary or come from aggregator accounts. A post is a hiring
# post only when it names a role or a company AND is written in the first person by someone at the company: the text
# says "we're hiring", "I'm hiring", "my team", "join us" (and the like), or it opens with "Hiring" or "Looking for"
# while the author's bio places them at a company. Anything else is commentary: recorded, never made.
_AP = "(?:'|’)"                # an ASCII or a curly apostrophe ("We’re hiring")
_WHO = rf"(?:we{_AP}re|we are|we|i{_AP}m|i am|i)"
FIRST_PERSON = re.compile(
    rf"\b{_WHO}\s+(?:(?:are|am|now|actively|currently|also|still)\s+){{0,2}}"
    r"(?:hiring|looking for|recruiting|searching for|growing (?:the|our|my) team)\b"
    r"|\b(?:my|our) team\b|\bjoin (?:us|our|my)\b|\bour (?:first|next|founding)\b"
    r"|\bcome (?:build|work) with (?:us|me)\b", re.I)
OPENS_HIRING = re.compile(r"(?:^|[\n.!?]\s*)(?:(?:now\s+)?hiring|looking for)\b", re.I)
ACCOUNT_KINDS = ("news", "aggregator", "agency", "bot")
ACCOUNT_NAME = (("news", re.compile(r"news(?!om)|pulse|headlines|breaking", re.I)),    # not Newsom or Newsome
                ("aggregator", re.compile(r"jobs|careers|alerts|daily", re.I)))
ACCOUNT_BIO = (("agency", re.compile(r"\btalent partner\b|\btalent matchmak\w*|\bmatchmaker\b|\bconnecting (?:\S+ ){0,3}"
                                     r"(?:talent|engineers|candidates)\b|\brecruiting agency\b|\bstaffing\b|\bheadhunt",
                                     re.I)),
               ("news", re.compile(r"\bnews\b|\bheadlines\b|\bbreaking\b", re.I)),
               ("bot", re.compile(r"\bbot\b|\bautomated\b", re.I)),
               ("aggregator", re.compile(r"\bjobs\b|\bjob board\b|\bhiring board\b|\balerts?\b|\baggregat\w*|"
                                         r"\bdaily (?:jobs?|news|updates?|alerts?|posts?|digest)\b|\bfollow for more\b",
                                         re.I)))
ACCOUNT_TEXT = re.compile(r"\bfollow (?:us )?for more\b[^.\n]{0,40}\bjobs\b", re.I)   # a feed's sign-off, never a founder's
BOT_POSTS_PER_DAY = 50            # a person posts a few times a day; a feed posts all day
# Location: when policy.posts.us_only is on, the search is for US roles. A post placed only outside the US, or one
# asking for a work permit or residence outside the US, is out of band (recorded, never made).
US_ONLY = bool(cfg.get("policy.posts.us_only", False))
US_PLACE = re.compile(r"\b(?:" + _EXTRA_PLACES + r"San Francisco|Bay Area|New York|Seattle|Austin|Boston|Los Angeles|Chicago|Denver|Miami|Atlanta|"
                      r"Washington,? D\.?C\.?|San Diego|Remote US|US remote|United\s+States|America)\b", re.I)
US_CODE = re.compile(r"\b(?:SF|NYC|LA|US|USA|U\.S\.A?\.?)\b")
NON_US_PLACE = re.compile(r"\b(?:Paris|France|London|England|United Kingdom|Berlin|Munich|Germany|Amsterdam|Netherlands|"
                          r"Oxford|Manchester|Edinburgh|Scotland|"
                          r"Dublin|Ireland|Europe|European Union|EMEA|Lisbon|Portugal|Madrid|Barcelona|Spain|Warsaw|Poland|"
                          r"Zurich|Switzerland|Stockholm|Sweden|Copenhagen|Denmark|Helsinki|Finland|Oslo|Norway|Vienna|"
                          r"Austria|Prague|Milan|Italy|Toronto|Vancouver|Montreal|Ottawa|Canada|India|Bangalore|Bengaluru|"
                          r"Hyderabad|Pune|Mumbai|Delhi|Noida|Gurgaon|Gurugram|Chennai|Pakistan|Karachi|Lahore|Singapore|"
                          r"Dubai|UAE|Tel Aviv|Israel|Sydney|Melbourne|Australia|Tokyo|Japan|Seoul|Korea|Brazil|"
                          r"São Paulo|Mexico|LATAM|APAC|Lagos|Nigeria|Nairobi|Kenya|Philippines|Manila|Vietnam|Cyprus|"
                          r"Estonia|Romania|Ukraine)\b", re.I)
NON_US_CODE = re.compile(r"\b(?:UK|EU)\b|[€£₹]")
WORK_PERMIT = re.compile(r"\bwork permit\b[^.\n]{0,40}\brequired\b|\brequired?\b[^.\n]{0,20}\bwork permit\b|"
                         r"\b(?:EU|UK|European) work (?:permit|authori[sz]ation|visa)\b|\bright to work in the (?:UK|EU)\b",
                         re.I)
MUST_BE_BASED = re.compile(r"\bmust (?:be|live) (?:based|located|living|resident)? ?in ([^.\n,;]{2,40})", re.I)
# A US place named only as a later move is not a US option ("London, 6 days in person, with potential relocation to
# San Francisco" is not in band). "Relocation to SF provided" (the role is in SF) is
# not a later move: only a hedged move ("potential", "possible", "eventual", "later", "an option") is taken out.
_LATER = (r"(?:potential(?:ly)?|possible|possibly|possibility|eventual(?:ly)?|future|later|optional|option(?:al)?|"
          r"chance|opportunit(?:y|ies)|path)")
_MOVE_TO = r"relocat\w*\s+to\s+(?:the\s+)?"
LATER_MOVE = (re.compile(rf"\b{_LATER}\b[^.\n;!?]{{0,30}}?\b{_MOVE_TO}[^.\n;!?,]{{2,40}}", re.I),
              re.compile(rf"\b{_MOVE_TO}[^.\n;!?,]{{2,40}}?\s+(?:is\s+)?(?:possible|optional|an option|later|"
                         r"eventually|down the line|in the future|over time)\b", re.I))
# The company of a LinkedIn post: the LinkedIn pass writes an attached job card into the text as
# "[Attached job: <title>, <company>, <place> (Verified job)]"; the plain card "<title> / <company> / <place>" is read
# too (a whole line whose first piece names a role). An opening "<Company> (YC S26) is hiring" or "<Company> is
# hiring", and "we're hiring at <Company>", name the company before any other "at <Company>" in the text.
ATTACHED_JOB = re.compile(r"\[\s*Attached job:\s*([^\]\n]{3,240}?)\s*\]", re.I)
PLAIN_CARD = re.compile(r"^[ \t]*([^/\n]{3,80}?)[ \t]+/[ \t]+([^/\n]{2,60}?)[ \t]+/[ \t]+([^/\n]{2,60}?)[ \t]*$", re.M)
CARD_BADGE = re.compile(r"\s*\((?:verified job|promoted|easy apply|actively recruiting|be an early applicant)\)\s*$", re.I)
WORK_MODE = re.compile(r"\s*\((?:remote|hybrid|on-?site)\)\s*$", re.I)
US_STATES = ("Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Dela[w]are|Florida|Georgia|Hawaii|Idaho|"
             "Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|"
             "Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|"
             "North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|"
             "Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming|District of Columbia")
CARD_STATE = re.compile(rf"(?:[A-Z]{{2}}|{US_STATES})")
CARD_REGION = re.compile(r"(?:United\s+States|USA|US|U\.S\.A?\.?|Remote|Hybrid|On-?site|Worldwide)", re.I)
OPENS_COMPANY = re.compile(r"^\W*([A-Z][\w.&'’+-]*[A-Za-z0-9](?:\s+[A-Z0-9][\w.&'’+-]*[A-Za-z0-9]){0,3})\s*"
                           r"(?:\((?:YC|Y\s?Combinator)\b[^)]{0,12}\)\s*)?(?:is|are)\s+"
                           r"(?:(?:now|actively|currently|also|still|officially)\s+)?hiring\b")
# "Example Systems is building AI agents for X, and we're hiring a Founding Engineer": the company opens the post and the
# same sentence says we're hiring (otherwise the company would be read from a later "began my career at <past employer>").
OPENS_BUILDING = re.compile(r"^\W*([A-Z][\w.&'’+-]*[A-Za-z0-9](?:\s+[A-Z0-9][\w.&'’+-]*[A-Za-z0-9]){0,3})\s*"
                            r"(?:\((?:YC|Y\s?Combinator)\b[^)]{0,12}\)\s*)?(?:is|are)\s+"
                            r"(?:building|developing|making|creating|bringing|automating|reinventing|rebuilding)\b"
                            rf"[^.!?\n]{{0,200}}?\b(?:we{_AP}re|we are)\s+(?:(?:now|actively|currently|also)\s+)?hiring\b")
# A past employer is not the hiring company: "began my career at X", "worked at X", "before joining X".
# ("was" and "were" count only right before the "at": "he was building at Initech" is the company now.)
PAST_AT = re.compile(r"(?:\b(?:career|began|started|worked|spent|previously|formerly|before|after|left|ex)\b"
                     r"[^.!?\n]{0,30}|\b(?:was|were)\s+)$", re.I)
# A founder or leader's own company from the bio's lead: "CEO, Example Systems | ...", "Co-Founder @ Acme (YC S26)".
BIO_LEAD = re.compile(r"^\W*(?i:co-?founder|founder|ceo|cto|coo|cpo|president|founding\s+(?!partner)[a-z ]{1,30}?|"
                      r"head\s+of\s+[a-z ]{1,30}?)(?:\s*(?:&|and|/)\s*(?i:co-?founder|founder|ceo|cto|coo|cpo))?"
                      r"\s*(?:,|@|\bat\b)\s*@?([A-Z][\w.&'’+-]*[A-Za-z0-9](?:\s+[A-Z0-9][\w.&'’+-]*[A-Za-z0-9]){0,3})")
NOT_OPENER = {"everyone", "everybody", "nobody", "someone", "anyone", "who", "what", "which", "it", "he", "she", "they",
              "your", "their", "his", "her", "its", "company", "team", "startup", "everyone's", "nobody's"}
HIRING_AT = re.compile(rf"(?i:\b{_WHO}\s+(?:(?:are|am|now|actively|currently|also|still)\s+){{0,2}}hiring\b)"
                       r"(?:\s+(?!at\b)[\w-]+){0,4}\s+(?i:at)\s+@?"
                       r"([A-Z][\w.&-]*[A-Za-z0-9](?:\s+[A-Z][\w.&-]*[A-Za-z0-9]){0,2})")


class PostsError(ValueError):
    """A post row or a request that cannot be handled as it is; nothing was written for it."""


# --------------------------------------------------------------------------- paths
def posts_dir(root: Path) -> Path:
    return Path(root) / "state" / "posts"


def ledger_path(root: Path) -> Path:
    return posts_dir(root) / "records.jsonl"


# --------------------------------------------------------------------------- input rows
def post_key(platform: str, url: str) -> str:
    """The one key a post has on its platform: x:<status id>, linkedin:<id> (the number its URL carries, the same in
    every form of one kind: /posts/<slug>-share-<id>-<tail>/ and /feed/update/urn:li:share:<id>/ are one key), else
    the URL without its query."""
    if platform == "x":
        import x_api
        return f"x:{x_api.post_id(url)}"
    parts = urlsplit(url)
    m = LI_POST_ID.search(parts.path)
    if m:
        return f"linkedin:{m.group(1)}"
    return "linkedin:" + urlunsplit(("https", parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def normalize_row(row: dict[str, Any], platform: str) -> dict[str, Any]:
    """One input row, checked: (platform, key, url, author, date, text, links). PostsError names what is wrong."""
    url = str(row.get("url") or "").strip()
    if platform == "x":
        import x_api
        if not x_api.POST_URL.match(url):
            raise PostsError(f"not an X post URL: {url!r}")
        author = {"name": row.get("author_name") or None, "handle": str(row.get("author_handle") or "").lower() or None,
                  "url": None, "bio": row.get("author_bio") or None}
        rate = row.get("author_posts_per_day")                 # x_api.py watch, when X returned the author's counts
        if isinstance(rate, (int, float)) and not isinstance(rate, bool):
            author["posts_per_day"] = float(rate)
    elif platform == "linkedin":
        if not LI_POST.match(url):
            raise PostsError(f"not a LinkedIn post URL (linkedin.com/posts/... or /feed/update/urn:li:activity:...): {url!r}")
        profile = str(row.get("author_url") or "").strip()
        if profile and not LI_PROFILE.match(profile):
            raise PostsError(f"author_url must be one profile (linkedin.com/in/<name>): {profile!r}")
        author = {"name": row.get("author_name") or None, "handle": None,
                  "url": cu.normalize_linkedin_url(profile) if profile else None, "bio": row.get("author_title") or None}
    else:
        raise PostsError(f"unknown platform {platform!r}")
    if not author["name"] and not author["handle"]:
        raise PostsError("a post needs its author (author_name, or author_handle on X)")
    day = jc.parse_date(row.get("date"))
    if not day:
        raise PostsError(f"date must be YYYY-MM-DD: {row.get('date')!r}")
    text = str(row.get("text") or "").strip()
    if not text:
        raise PostsError("text is empty (copy the post's text as shown)")
    links = [str(u) for u in row.get("links") or [] if str(u).startswith("http")]
    return {"platform": platform, "key": post_key(platform, url), "url": url, "author": author, "date": day.isoformat(),
            "text": text, "links": links, "why": row.get("why")}


SWEEP = "sweep"                   # state/posts/sweep-<date>.jsonl: the posts lane's morning sweep (lanes/posts/AGENT.md)
X_PROFILE = re.compile(r"^https?://(?:www\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/?$", re.I)


def input_files(root: Path, files: Iterable[Path] | None = None) -> list[tuple[str, Path]]:
    if files:
        out = []
        for f in files:
            name = Path(f).name
            platform = next((p for p in (*PLATFORMS, SWEEP) if name.startswith(p + "-")), None)
            if not platform:
                raise PostsError(f"{name}: a posts file is named x-<date>.jsonl, linkedin-<date>.jsonl or "
                                 "sweep-<date>.jsonl")
            out.append((platform, Path(f)))
        return out
    folder = posts_dir(root)
    return [(p, f) for p in (*PLATFORMS, SWEEP) for f in sorted(folder.glob(f"{p}-*.jsonl"))] if folder.is_dir() else []


def from_sweep(row: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """A sweep row (scripts/posts_sweep_check.py format) as a pass-file row of its platform; None for a person or a
    startup row (those are the Recruiter's cards, not posts), and None for a platform this parser does not model (a
    `yc` row reaches the Recruiter through recruiter_sweep.py and the cards). The post's day is posted_at's, else the
    day it was seen."""
    if row.get("kind") != "hiring_post":
        return None
    platform = str(row.get("platform") or "")
    if platform not in PLATFORMS:
        return None
    day = str(row.get("posted_at") or row.get("seen_at") or "")[:10] or None
    out: dict[str, Any] = {"url": row.get("url"), "author_name": row.get("author"), "date": day, "text": row.get("text"),
                           "why": f"sweep query: {row['query']}" if row.get("query") else None}
    if platform == "x":
        m = X_PROFILE.match(str(row.get("author_url") or ""))
        out.update(author_handle=m.group(1) if m else None, author_bio=row.get("author_title"))
    else:
        out.update(author_url=row.get("author_url"), author_title=row.get("author_title"))
    return platform, out


def read_rows(platform: str, path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows, problems = [], []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row, plat = json.loads(line), platform
            if platform == SWEEP:
                converted = from_sweep(row)
                if converted is None:
                    continue
                plat, row = converted
            rows.append(normalize_row(row, plat))
        except (ValueError, PostsError) as exc:            # json.JSONDecodeError is a ValueError
            problems.append(f"{Path(path).name} line {n}: {exc}")
    return rows, problems


# --------------------------------------------------------------------------- the parser
def fingerprint(author: dict[str, Any], text: str) -> str:
    """Same person, same words: the cross-platform duplicate key (a post shared on both X and LinkedIn)."""
    words = re.findall(r"[a-z0-9]+", URL.sub(" ", text.lower()))[:40]
    who = cu.normalize_person_name(author.get("name")) or str(author.get("handle") or "")
    return jc.short_hash(who, " ".join(words), n=16)


def same_author(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Two LinkedIn reads with one fingerprint are one post unless both carry an author_url and the two differ (the
    fingerprint already holds the normalized name and the first 40 words of the text)."""
    return not (a.get("url") and b.get("url") and a["url"] != b["url"])


def _title_case(phrase: str) -> str:
    words = re.sub(r"\s+", " ", phrase.strip()).split(" ")
    out = []
    for w in words:
        low = w.lower()
        out.append(UPPER.get(low) or "-".join(p.capitalize() for p in w.split("-")))
    title = " ".join(out)
    return re.sub(r"(Engineer|Scientist|Researcher|Developer)s\b", r"\1", title)


def role_title(text: str) -> str | None:
    """The role the post hires for, as a title: the first engineer, scientist, researcher or developer phrase, preferring
    one with a qualifier (AI Engineer over a bare 'engineers'); None when the post names no role."""
    found = [m.group(1) for m in TITLE.finditer(text)]
    if not found:
        return None
    qualified = [f for f in found if len(f.split()) > 1]
    return _title_case((qualified or found)[0])


def _place_piece(piece: str) -> bool:
    """A card piece that is a place: a city or country named in the place lists, "<city> Bay Area", "Greater <city>
    Area", with an optional "(Remote)" or "(Hybrid)"."""
    piece = WORK_MODE.sub("", piece).strip()
    return any(re.fullmatch(rf"(?:Greater\s+)?(?:{rx.pattern})(?:\s+(?:Bay|Metropolitan|City))?(?:\s+Area)?", piece, re.I)
               for rx in (US_PLACE, NON_US_PLACE, PLACES))


def attached_job(text: str) -> dict[str, Any] | None:
    """The job card a LinkedIn post carries, as the LinkedIn pass writes it: {title, company, location}, or None.
    "[Attached job: Applied AI Engineer, Example Health, Palo Alto, CA (Verified job)]": the place is read from the
    end (a state or region code, a state, a country, Remote; the city before a state), the company is the piece before
    it and the title is the rest. "Title / Company / Place" (in the card or as a whole line naming a role) splits on
    the slashes."""
    text = text or ""
    m = ATTACHED_JOB.search(text)
    if m:
        body = CARD_BADGE.sub("", m.group(1)).strip()
        if " / " in body:
            parts = [p.strip() for p in body.split(" / ")]
            parts = [p for p in parts if p]
            if len(parts) < 2:
                return None
            return {"title": parts[0], "company": parts[1], "location": " / ".join(parts[2:]) or None}
        parts = [p.strip() for p in body.split(",") if p.strip()]
        if len(parts) < 2:
            return None
        i = len(parts)
        while i > 2 and (CARD_STATE.fullmatch(WORK_MODE.sub("", parts[i - 1]).strip()) or
                         CARD_REGION.fullmatch(WORK_MODE.sub("", parts[i - 1]).strip()) or
                         NON_US_PLACE.fullmatch(WORK_MODE.sub("", parts[i - 1]).strip())):
            i -= 1
        first = WORK_MODE.sub("", parts[i]).strip() if i < len(parts) else ""
        if i > 2 and ((CARD_STATE.fullmatch(first) and not US_PLACE.fullmatch(first)) or _place_piece(parts[i - 1])):
            i -= 1                                          # the city before its state ("New York, NY" has it), or a place
        return {"title": ", ".join(parts[:i - 1]), "company": parts[i - 1], "location": ", ".join(parts[i:]) or None}
    for m in PLAIN_CARD.finditer(text):
        if TITLE.search(m.group(1)):
            return {"title": m.group(1).strip(), "company": m.group(2).strip(),
                    "location": CARD_BADGE.sub("", m.group(3)).strip() or None}
    return None


def opening_company(text: str) -> str | None:
    """"Example Co (YC S26) is hiring!", "Acme is hiring" or "Acme is building X, and we're hiring": the company a post
    opens with, else None."""
    m = OPENS_COMPANY.match(text or "") or OPENS_BUILDING.match(text or "")
    if not m or m.group(1).split()[0].lower() in NOT_COMPANY | NOT_OPENER:
        return None
    return m.group(1).strip(" .-")


def _company(text: str, author: dict[str, Any], watched: dict[str, Any] | None) -> str | None:
    if watched and watched.get("company"):
        return str(watched["company"])
    card = attached_job(text)
    if card and card["company"] and card["company"].split()[0].lower() not in NOT_COMPANY:
        return card["company"]
    opener = opening_company(text)
    if opener:
        return opener
    bio = author.get("bio") or ""
    for rx, source in ((HIRING_AT, text), (AT_HANDLE, text), (COMPANY, text), (BIO_LEAD, bio), (BIO_COMPANY, bio)):
        for m in rx.finditer(source):
            name = m.group(1).strip(" .-")
            if rx is COMPANY and PAST_AT.search(source[:m.start()]):
                continue                                    # a past employer, not the hiring company
            if name.split()[0].lower() not in NOT_COMPANY:
                return name
    return None


def _first(pairs: Iterable[tuple[str, re.Pattern]], text: str) -> str | None:
    return next((name for name, rx in pairs if rx.search(text)), None)


# --------------------------------------------------------------------------- post quality
def deny_path(root: Path) -> Path:
    return Path(root) / "targets" / "x-deny.yaml"


def load_deny(root: Path) -> dict[str, Any]:
    """targets/x-deny.yaml: {accounts: {handle: {kind, why}}, allow: {handle}}; a missing file is an empty list. A row
    without a valid handle or kind is refused (PostsError), never guessed."""
    import yaml
    path = deny_path(root)
    if not path.is_file():
        return {"accounts": {}, "allow": set()}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    accounts: dict[str, dict[str, str]] = {}
    for n, row in enumerate(data.get("accounts") or [], 1):
        handle = str((row or {}).get("x_handle") or "").strip().lstrip("@").lower()
        kind = str((row or {}).get("kind") or "")
        if not re.fullmatch(r"[a-z0-9_]{1,15}", handle) or kind not in ACCOUNT_KINDS:
            raise PostsError(f"{path.name} account {n}: needs x_handle and kind ({', '.join(ACCOUNT_KINDS)}): {row!r}")
        accounts[handle] = {"kind": kind, "why": str(row.get("why") or "")}
    allow = {str(h).strip().lstrip("@").lower() for h in data.get("allow") or []}
    return {"accounts": accounts, "allow": allow}


def poster_kind(author: dict[str, Any], text: str, *, deny: dict[str, Any] | None = None,
                watched: dict[str, Any] | None = None) -> tuple[str, str | None]:
    """("person", None), or (news | aggregator | agency | bot, why): the deny list first, then a watched person or an
    allowed handle is a person, then the heuristics on the handle, the name, the bio, the text and the post rate."""
    deny = deny or {"accounts": {}, "allow": set()}
    handle = str(author.get("handle") or "").lower()
    if handle and handle in deny["accounts"]:
        row = deny["accounts"][handle]
        return row["kind"], f"targets/x-deny.yaml: {row['why'] or row['kind']}"
    if watched or (handle and handle in deny["allow"]):
        return "person", None
    for kind, rx in ACCOUNT_NAME:
        for field in ("handle", "name"):
            m = rx.search(str(author.get(field) or ""))
            if m:
                return kind, f"{field} says {m.group(0).lower()!r}"
    bio = str(author.get("bio") or "")
    for kind, rx in ACCOUNT_BIO:
        m = rx.search(bio)
        if m:
            return kind, f"bio says {m.group(0).lower()!r}"
    m = ACCOUNT_TEXT.search(text or "")
    if m:
        return "aggregator", f"post says {m.group(0).lower()!r}"
    rate = author.get("posts_per_day")
    if isinstance(rate, (int, float)) and rate > BOT_POSTS_PER_DAY:
        return "bot", f"posts about {rate:.0f} times a day"
    return "person", None


def bio_at_company(bio: str | None) -> bool:
    """The author's bio places them at a company: a founder, CEO or CTO line, `@company`, or a role "at" a company."""
    bio = str(bio or "")
    return bool(re.search(r"\b(?:co-?founder|founder|ceo|cto|founding)\b", bio, re.I)
                or re.search(r"(?:^|[\s(])@[A-Za-z0-9_]{2,15}\b", bio)
                or re.search(r"\bat\s+@?[A-Z][\w.&-]", bio))


def first_person(text: str, author: dict[str, Any]) -> bool:
    """Written by someone at the hiring company: the text speaks as the company ("we're hiring", "I'm looking for",
    "my team", "join us"), or it opens with "Hiring" or "Looking for" and the bio places the author at a company."""
    return bool(FIRST_PERSON.search(text or "")) or bool(OPENS_HIRING.search(text or "") and
                                                         bio_at_company(author.get("bio")))


def later_moves(text: str) -> tuple[str, list[str]]:
    """The text without its hedged moves to a place ("with potential relocation to San Francisco"), and the moves."""
    found: list[str] = []
    for rx in LATER_MOVE:
        found += [m.group(0) for m in rx.finditer(text)]
        text = rx.sub(" ", text)
    return text, found


def where_call(text: str) -> str:
    """"in band", or "out of band: <why>" for a work permit or residence outside the US, or a place only outside the US
    (a post naming a US place too, "SF or London", stays in band; a US place named only as a hedged later move,
    "London, in person, with potential relocation to San Francisco", is not a US option and the post is out of band)."""
    text = text or ""
    if not US_ONLY:
        return "in band"
    m = WORK_PERMIT.search(text)
    if m:
        return f"out of band: asks for a work permit outside the US ({cu.one_line(m.group(0), 60)!r})"
    m = MUST_BE_BASED.search(text)
    if m and (NON_US_PLACE.search(m.group(1)) or NON_US_CODE.search(m.group(1))) and not (
            US_PLACE.search(m.group(1)) or US_CODE.search(m.group(1))):
        return f"out of band: must be based outside the US ({cu.one_line(m.group(0), 60)!r})"
    abroad = NON_US_PLACE.search(text) or NON_US_CODE.search(text)
    if abroad:
        here, moves = later_moves(text)
        if not (US_PLACE.search(here) or US_CODE.search(here)):
            why = f"out of band: placed outside the US ({abroad.group(0)!r})"
            if moves and (US_PLACE.search(text) or US_CODE.search(text)):
                why += f"; the US only as a later move ({cu.one_line(moves[0].strip(), 60)!r})"
            return why
    return "in band"


def structure(post: dict[str, Any], *, watched: dict[str, Any] | None = None, companies: Any = None,
              big: set[str] | None = None, deny: dict[str, Any] | None = None) -> dict[str, Any]:
    """The structured record of one post (pure: no file, no network). `hiring` is the quality gate: a hiring phrase, a role or a company named, the first person from someone at the company, and a person's
    account (not news, a job board, an agency or a bot); otherwise `not_hiring` says why and nothing is made. `in_band`
    adds the role (on role) and the place (`where_call`: a US role, or one not placed)."""
    import fit_gates
    import fresh10
    import hunt_score
    import job_alerts as ja
    import prospects
    import x_api
    text = post["text"]
    card = attached_job(text)
    title = (card or {}).get("title") or role_title(text)     # an attached job card names the role exactly
    signal = x_api.hiring_signal(text)
    team = next((m.group(1) for rx in TEAM for m in [rx.search(text)] if m), None)
    company = _company(text, post["author"], watched)
    slug = (companies.resolve(company)[0] if companies is not None else jc.slugify(company)) if company else None
    years = fit_gates.required_years(text)
    years, implied = fresh10.implied_years(title or "", years, text)
    rule = fresh10.years_rule(years, warm=False)         # the cold call: in band, or a person is needed
    reject = fit_gates.REJECT_YEARS
    years_call = "in band" if rule is None else (     # above the cold band only through a person; a post has that person
        f"stretch: asks {years} years, only through a person, and the poster is that person"
        + (f"; {reject} or more is still the fit gate" if reject and years and years >= reject else ""))
    family = _first(FAMILIES, title or "") or _first(FAMILIES, text) or "other"
    links = post.get("links") or []
    apply_link = next((u for u in links + URL.findall(text) if ja.match_job_url(u.rstrip(").,"), None)), None)
    routes = [name for name, rx in ROUTES if rx.search(text)]
    if apply_link or any(not re.search(r"(?:x|twitter|linkedin)\.com", u) for u in links + URL.findall(text)):
        routes.append("link")
    stage = ("founding" if hunt_score.FOUNDING.search(text) else
             "new startup" if hunt_score.NEW_STARTUP.search(text) else
             "big company" if slug and slug in (big or set()) else "unknown")
    spons, evidence = prospects.sponsorship_hint(text)
    ai = bool(hunt_score.AI_TITLE.search(title or "")) or family != "other" or sum(
        bool(rx.search(text)) for rx in SKILL_RX.values()) >= 2
    on_role = bool(title) and ai and not hunt_score.HARD_OFF.search(title or "")
    kind, kind_why = poster_kind(post["author"], text, deny=deny, watched=watched)
    first = first_person(text, post["author"])
    not_hiring = ("no hiring phrase with a role word" if not signal else
                  f"{kind} account ({kind_why}): not someone at the hiring company" if kind != "person" else
                  "names no role and no company" if not (title or company) else
                  "commentary: not written in the first person by someone at the company" if not first else None)
    hiring = not_hiring is None
    where = where_call(text)
    return {"key": post["key"], "platform": post["platform"], "url": post["url"], "date": post["date"],
            "author": {k: v for k, v in post["author"].items() if k != "bio"}, "hiring": hiring,
            "not_hiring": not_hiring, "poster_kind": kind, "first_person": first, "where_call": where,
            "in_band": bool(hiring and on_role and where == "in band"), "role_title": title, "team": team,
            "company": company, "company_slug": slug, "seniority": _first(SENIORITY, title or "") or _first(SENIORITY, text) or "unknown",
            "years": years, "years_implied": implied, "years_call": years_call,
            "location": sorted({m.group(1) for m in PLACES.finditer(text)}) or None,
            "remote": _first(REMOTE, text) or "unknown", "sponsorship": spons, "sponsorship_evidence": evidence,
            "apply_route": routes or ["not stated"],
            "apply_link": apply_link.rstrip(").,") if apply_link else None,
            "emails": sorted(set(EMAIL.findall(text))) or None,
            "skills": sorted(k for k, rx in SKILL_RX.items() if rx.search(text)), "family": family, "stage": stage,
            "fingerprint": fingerprint(post["author"], text)}


# --------------------------------------------------------------------------- the ledger
def load_ledger(root: Path) -> dict[str, Any]:
    """{records: key -> record (with made rows folded in), alias: key -> the record key it duplicates, fp: fingerprint
    -> key}. The ledger is append-only: a record row, a `same_as` row, or a `made` row (prospect, job). Keys are read
    as today's post_key makes them (no migration): a row written under an older key (a LinkedIn URL) is keyed again
    from its URL, and the rows that point at the old key (made, same_as) follow it."""
    path = ledger_path(root)
    records: dict[str, dict] = {}
    alias: dict[str, str] = {}
    moved: dict[str, str] = {}                              # the key a row was written under -> its key today

    def join(rec: dict[str, Any], row: dict[str, Any]) -> None:
        if row.get("url") and row["url"] not in rec.setdefault("urls", [rec["url"]]):
            rec["urls"].append(row["url"])
        if row.get("platform") and row["platform"] not in rec.setdefault("platforms", [rec["platform"]]):
            rec["platforms"].append(row["platform"])

    for row in jc.read_jsonl(path) if path.is_file() else []:
        if row.get("same_as"):
            key, target = ledger_key(row), moved.get(row["same_as"], row["same_as"])
            moved[row["key"]] = key
            if key != target:
                alias[key] = target
            rec = records.get(target)
            if rec is not None:
                join(rec, row)
        elif row.get("made"):
            rec = records.get(moved.get(row["key"], row["key"]))
            if rec is not None:
                rec.update({k: v for k, v in row.items() if k in ("prospect", "job", "made_on")})
        elif row.get("key"):
            key = ledger_key(row)
            moved[row["key"]] = key
            if key in records:                              # two old URL keys of one post: the first is the record
                join(records[key], row)
            else:
                records[key] = dict(row, key=key)
    fp = {r["fingerprint"]: k for k, r in records.items() if r.get("fingerprint")}
    return {"records": records, "alias": alias, "fp": fp}


def ledger_key(row: dict[str, Any]) -> str:
    """A ledger row's key as post_key makes it today: a LinkedIn row is keyed again from its URL (older rows carry the
    URL as their key), any other row keeps the key it was written with."""
    url = str(row.get("url") or "")
    if row.get("platform") == "linkedin" and LI_POST.match(url):
        return post_key("linkedin", url)
    return str(row["key"])


def _people_watch(root: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    import people_watch
    try:
        people = people_watch.load(root)
    except people_watch.WatchError:
        people = []
    by_handle = {p["x_handle"]: p for p in people if p.get("x_handle")}
    by_url = {cu.normalize_linkedin_url(p["linkedin_url"]): p for p in people if p.get("linkedin_url")}
    return by_handle, by_url


def _watched(post: dict[str, Any], by_handle: dict, by_url: dict) -> dict[str, Any] | None:
    a = post["author"]
    return by_handle.get(a.get("handle") or "") or by_url.get(a.get("url") or "")


def _big(root: Path) -> set[str]:
    import fetch_ats
    import hunt_score
    return {c["slug"] for c in fetch_ats.load_companies(root) if c.get("segment") in hunt_score.BIG_SEGMENTS}


# --------------------------------------------------------------------------- ingest
def poster_role(rec: dict[str, Any], bio: str | None, text: str) -> str:
    return _first(BIO_ROLE, bio or "") or ("hiring-manager" if re.search(r"\bmy team\b", text, re.I) else "unknown")


def outbound_row(rec: dict[str, Any], post: dict[str, Any], now: str) -> dict[str, Any]:
    """The poster as a prospects.py outbound row."""
    a = post["author"]
    role = rec["role_title"] or "a role"
    at = f" at {rec['company']}" if rec.get("company") else ""
    row = {"name": a.get("name") or f"@{a['handle']}", "company": rec.get("company"),
           "title": a.get("bio") if post["platform"] == "linkedin" else None,      # an X bio is not a job title
           "role": poster_role(rec, a.get("bio"), post["text"]), "at": now,
           "found_via": f"hiring post on {'X' if post['platform'] == 'x' else 'LinkedIn'} {post['date']}: {post['url']}",
           "relation": f"hiring for {role}{at}; the post's person is the path in",
           "hiring_post_url": post["url"]}
    if a.get("handle"):
        row["x_handle"] = a["handle"]
    if a.get("url"):
        row["linkedin_url"] = a["url"]
    if row["title"]:
        row["title"] = cu.one_line(row["title"], 120)
    return row


def _prospect_slugs(root: Path, os_root: Path) -> dict[str, str]:
    """hiring_post_url, x handle and LinkedIn URL -> prospect slug, read back after the ingest."""
    import prospects
    out: dict[str, str] = {}
    for _, meta in prospects.iter_metas(cu.Roots(js=Path(root), os=Path(os_root))):
        slug = str(meta.get("id") or "")
        for k in ("hiring_post_url", "x_handle", "linkedin_url"):
            v = meta.get(k)
            if v:
                out.setdefault(f"{k}:{str(v).lower() if k == 'x_handle' else v}", slug)
        name = cu.normalize_person_name(meta.get("name"))
        if name and meta.get("company_slug"):
            out.setdefault(f"name:{name}|{meta['company_slug']}", slug)
    return out


def _slug_for(slugs: dict[str, str], post: dict[str, Any], rec: dict[str, Any] | None = None) -> str | None:
    """The poster's prospect: by this post, handle or profile URL, else (a LinkedIn row without author_url)
    by the normalized name at the post's company, the same pair prospects.py's matcher merges on."""
    a = post["author"]
    name = cu.normalize_person_name(a.get("name"))
    company = (rec or {}).get("company_slug")
    return (slugs.get(f"hiring_post_url:{post['url']}") or (slugs.get(f"x_handle:{a['handle']}") if a.get("handle") else None)
            or (slugs.get(f"linkedin_url:{a['url']}") if a.get("url") else None)
            or (slugs.get(f"name:{name}|{company}") if name and company else None))


def _sign(root: Path, job: dict[str, Any], post: dict[str, Any], slug: str | None) -> str:
    """The post on the job record: `hiring_signal` and the poster as its
    prospect, each only when empty (a record's links are never overwritten, as in jobs_manual.py)."""
    import fetch_ats
    changed = False
    if not isinstance(job.get("hiring_signal"), dict) or not job["hiring_signal"].get("url"):
        a = post["author"]
        job["hiring_signal"] = {"url": post["url"], "date": post["date"],
                                "by": f"@{a['handle']}" if a.get("handle") else a.get("name")}
        changed = True
    if slug and not job.get("prospect"):
        job["prospect"], changed = slug, True
    if changed:
        fetch_ats.save_job(root, job)
    return str(job["id"])


def make_job(root: Path, rec: dict[str, Any], post: dict[str, Any], slug: str | None, today: date) -> str | None:
    """The job for one in-band post that names a role and a company: a job already recorded at the post's apply link
    (an ATS board found it first) gets the post; otherwise jobs_manual.py add records it. Returns the job id."""
    import fetch_ats
    import jobs_manual
    url = rec.get("apply_link") or post["url"]
    try:
        canon = jobs_manual.canonical_url(url, post["platform"])[0]
    except jobs_manual.Refused:
        return None
    known = next((r for r in fetch_ats.load_jobs(root).values() if r.get("source_url") == canon), None) \
        if (Path(root) / "jobs").is_dir() else None
    if known is not None:
        return _sign(root, known, post, slug)
    title = rec["role_title"]
    if len(title.split()) == 1 and rec.get("team"):
        title = f"{title}, {rec['team']}"                  # "hiring engineers for Claude Platform": Engineer, Claude Platform
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(post["text"])
        jd_file = fh.name
    argv = ["add", "--root", str(root), "--url", url, "--platform", post["platform"], "--title", title,
            "--company", rec["company"], "--jd-file", jd_file, "--posted", post["date"], "--today", today.isoformat()]
    argv += ["--source", "prospect", "--prospect", slug] if slug else []
    if rec.get("remote") in ("remote", "hybrid", "onsite"):
        argv += ["--remote", rec["remote"]]
    if rec.get("location"):
        argv += ["--location", ", ".join(rec["location"])]
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            code = jobs_manual.main(argv)
    finally:
        Path(jd_file).unlink(missing_ok=True)
    if code != 0:
        return None
    job = next((r for r in fetch_ats.load_jobs(root).values() if r.get("source_url") == canon), None)
    return _sign(root, job, post, slug) if job is not None else None


def ingest(root: Path, *, today: date, files: Iterable[Path] | None = None, write: bool = False,
           os_root: Path | None = None) -> dict[str, Any]:
    root = Path(root)
    os_root = Path(os_root) if os_root else jc.os_root_for(root)
    from company_index import CompanyIndex
    companies = CompanyIndex.load(root)
    big = _big(root)
    by_handle, by_url = _people_watch(root)
    deny = load_deny(root)
    led = load_ledger(root)
    report: dict[str, Any] = {"read": 0, "new": 0, "duplicates": 0, "cross_platform": 0, "hiring": 0, "in_band": 0,
                              "prospects": 0, "jobs": 0, "proposals": 0, "problems": [], "write": write}
    fresh: list[tuple[dict, dict]] = []
    same_rows: list[dict[str, Any]] = []
    batch_fp: dict[str, str] = {}
    for platform, path in input_files(root, files):
        rows, problems = read_rows(platform, path)
        report["problems"] += problems
        for post in rows:
            platform = post["platform"]                      # a sweep file carries both platforms
            report["read"] += 1
            if post["key"] in led["records"] or post["key"] in led["alias"] or any(p["key"] == post["key"] for p, _ in fresh) \
                    or any(r["key"] == post["key"] for r in same_rows):
                report["duplicates"] += 1
                continue
            rec = structure(post, watched=_watched(post, by_handle, by_url), companies=companies, big=big, deny=deny)
            rec.update({"ingested": today.isoformat(), "source_file": str(Path(path).name)})
            same = led["fp"].get(rec["fingerprint"]) or batch_fp.get(rec["fingerprint"])
            if same and platform == "linkedin":
                other = led["records"].get(same) or next((r for _, r in fresh if r["key"] == same), None)
                if other is not None and other.get("platform") == "linkedin":
                    if same_author(other.get("author") or {}, post["author"]):
                        report["duplicates"] += 1           # the same post re-read in a URL form with another number
                        same_rows.append({"key": post["key"], "same_as": same, "url": post["url"], "platform": platform,
                                          "re_read": True, "ingested": today.isoformat()})
                        continue
                    same = None                              # the same name and words from another profile: its own post
            if same:
                report["cross_platform"] += 1
                same_rows.append({"key": post["key"], "same_as": same, "url": post["url"], "platform": platform,
                                  "ingested": today.isoformat()})
                continue
            batch_fp[rec["fingerprint"]] = rec["key"]
            fresh.append((post, rec))
    report["new"] = len(fresh)
    hiring = [(p, r) for p, r in fresh if r["hiring"]]
    report["hiring"], report["in_band"] = len(hiring), sum(r["in_band"] for _, r in hiring)
    if write:
        for row in [rec for _, rec in fresh] + same_rows:          # a record before the rows that point at it
            jc.append_jsonl(ledger_path(root), row)
    made: dict[str, dict[str, Any]] = {}
    band = [(p, r) for p, r in hiring if r["in_band"]]
    report["errors"], report["retried"] = [], 0
    if write:
        retry, dropped = [], []
        for post, old in pending(root, led, {r["key"] for _, r in band}):
            now = structure(post, watched=_watched(post, by_handle, by_url), companies=companies, big=big, deny=deny)
            if now["hiring"] and now["in_band"]:        # an earlier run's in-band call is checked again by today's gate
                retry.append((post, old))
            else:
                dropped.append({"key": old["key"], "why": now["not_hiring"] or now["where_call"]})
        report["retried"], report["retry_dropped"] = len(retry), dropped
        todo = band + retry
        if todo:
            made = make_all(root, os_root, todo, today, report)

    if write:
        report["proposals"] = write_proposals(root, band, by_handle, by_url, today) if band else 0
        report["review"] = str(write_review(root, fresh, made, report, today))
    else:                                                  # a dry run writes nothing: it says what it would make
        report["would_make"] = [{"post": r["url"], "role": r["role_title"], "company": r["company"],
                                 "years": r["years_call"], "prospect": True,
                                 "job": bool(r["role_title"] and r["company"])} for _, r in band]
        report["out_of_band"] = [{"post": r["url"], "role": r["role_title"], "years": r["years_call"],
                                  "where": r["where_call"]} for _, r in hiring if not r["in_band"]]
        report["not_hiring"] = [{"post": r["url"], "poster": r["poster_kind"], "why": r["not_hiring"]}
                                for _, r in fresh if not r["hiring"]]
    return report


def pending(root: Path, led: dict[str, Any], skip: set[str]) -> list[tuple[dict, dict]]:
    """In-band hiring records with no made row (a crash, a refusal or a SystemExit last time), with their
    posts read back from the input files, so the next run makes each one (exactly once: the made row ends it)."""
    want = {k: r for k, r in led["records"].items() if r.get("hiring") and r.get("in_band") and not r.get("made_on")
            and k not in skip}
    if not want:
        return []
    out = []
    for platform, path in input_files(root):
        for post in read_rows(platform, path)[0]:
            if post["key"] in want:
                out.append((post, want.pop(post["key"])))
    return out


def make_all(root: Path, os_root: Path, todo: list[tuple[dict, dict]], today: date,
             report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The poster's prospect and the job for each post, each post on its own: a failure (an exception, a SystemExit from
    jobs_manual's argparse, a job not made) is reported and leaves no made row, so the next run retries it. The made row
    is written only when the prospect exists and the job, when the post names a role and a company, exists too."""
    import prospects
    made: dict[str, dict[str, Any]] = {}
    out = root / "state" / "harvest" / f"outbound-posts-{today.isoformat()}.jsonl"
    stamp = jc.run_stamp(today)
    try:
        for post, rec in todo:
            jc.append_jsonl(out, outbound_row(rec, post, stamp))
        res = prospects.ingest(cu.Roots(js=root, os=os_root), outbound_files=[out], today=today)
        report["prospects"] = res.get("created", 0) + res.get("updated", 0)
        slugs = _prospect_slugs(root, os_root)
    except (SystemExit, Exception) as exc:  # noqa: BLE001 - reported; every post stays pending
        report["errors"].append({"key": "prospects", "error": f"{type(exc).__name__}: {exc}"})
        return made
    for post, rec in todo:
        try:
            slug = _slug_for(slugs, post, rec)
            if not slug:
                raise RuntimeError("no prospect found for the poster after the prospects ingest")
            wants_job = bool(rec["role_title"] and rec["company"])
            job = make_job(root, rec, post, slug, today) if wants_job else None
            if wants_job and not job:
                raise RuntimeError("the job was not made (jobs_manual.py refused it or found no record)")
        except (SystemExit, Exception) as exc:  # noqa: BLE001 - one post never stops the others
            report["errors"].append({"key": rec["key"], "error": f"{type(exc).__name__}: {exc}"})
            continue
        report["jobs"] += bool(job)
        made[rec["key"]] = {"prospect": slug, "job": job}
        jc.append_jsonl(ledger_path(root), {"key": rec["key"], "made": True, "prospect": slug, "job": job,
                                            "made_on": today.isoformat()})
    return made


def write_proposals(root: Path, band: list[tuple[dict, dict]], by_handle: dict, by_url: dict, today: date) -> int:
    """Posters worth watching, in people-watch.yaml's row format, for the orchestrator to add (never written there)."""
    import yaml
    seen: set[str] = set()
    for old in sorted(posts_dir(root).glob("people-watch-proposals-*.yaml")) if posts_dir(root).is_dir() else []:
        for row in (yaml.safe_load(old.read_text(encoding="utf-8")) or {}).get("proposals") or []:
            seen.add(str(row.get("x_handle") or row.get("linkedin_url") or row.get("name")).lower())
    rows = []
    for post, rec in band:
        a = post["author"]
        ident = str(a.get("handle") or a.get("url") or a.get("name")).lower()
        if _watched(post, by_handle, by_url) or ident in seen:
            continue
        seen.add(ident)
        row = {"name": a.get("name") or f"@{a['handle']}"}
        row.update({"x_handle": a["handle"]} if a.get("handle") else {})
        row.update({"linkedin_url": a["url"]} if a.get("url") else {})
        row.update({"company": rec.get("company_slug") or "unknown", "role": "hiring poster",
                    "why": cu.one_line(f"Hiring poster: posted for {rec['role_title'] or 'a role'}"
                                       f"{' at ' + rec['company'] if rec.get('company') else ''} on {post['date']} "
                                       f"({rec['years_call']}); a person to talk to", 280),
                    "added": today.isoformat(), "source": f"state/posts/records.jsonl {rec['key']}", "status": "watch"})
        rows.append(row)
    if rows:
        path = posts_dir(root) / f"people-watch-proposals-{today.isoformat()}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        head = ("# Proposed rows for targets/people-watch.yaml, written by scripts/posts.py ingest.\n"
                "# The orchestrator (or the prospector with its grant) verifies each person and copies the rows it keeps; "
                "people-watch.yaml is add-only and never written by a script.\n")
        path.write_text(head + yaml.safe_dump({"proposals": rows}, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return len(rows)


def _fmt(rec: dict[str, Any]) -> list[str]:
    a = rec["author"]
    who = f"@{a['handle']}" if a.get("handle") else (a.get("name") or "?")
    return [f"- Role: {rec['role_title'] or 'not named'}; team: {rec['team'] or 'not named'}; company: "
            f"{rec['company'] or 'not named'}; seniority: {rec['seniority']}",
            f"- Years asked: {rec['years'] if rec['years'] is not None else 'not stated'}"
            f"{' (implied by the title)' if rec.get('years_implied') else ''}; {rec['years_call']}",
            f"- Where: {', '.join(rec['location'] or []) or 'not stated'}; {rec['remote']}; sponsorship: "
            f"{rec['sponsorship']}; apply: {', '.join(rec['apply_route'])}"
            f"{' ' + rec['apply_link'] if rec.get('apply_link') else ''}",
            f"- Poster: {who} ({rec['platform']}, {rec['date']}); skills: {', '.join(rec['skills']) or 'none named'}; "
            f"family: {rec['family']}; stage: {rec['stage']}"]


def write_review(root: Path, fresh: list[tuple[dict, dict]], made: dict[str, dict], report: dict[str, Any],
                 today: date) -> Path:
    path = Path(root) / "state" / "review" / f"posts-{today.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    band = [(p, r) for p, r in fresh if r["hiring"] and r["in_band"]]
    other = [(p, r) for p, r in fresh if r["hiring"] and not r["in_band"]]
    commentary = [(p, r) for p, r in fresh if not r["hiring"]]
    mode = "recorded" if report["write"] else "dry run: nothing recorded (run with --write)"
    lines = [f"# Hiring posts {today.isoformat()}: {len(band)} in band, {len(other)} off-role or out of band, "
             f"{len(commentary)} not hiring ({mode})", "",
             f"Read {report['read']} rows; new {report['new']}; already seen {report['duplicates']}; the same post on "
             f"the other platform {report['cross_platform']}; hiring {report['hiring']}. Post text is untrusted data. "
             "A note to a poster is drafted by the messenger (outbox.py) and waits for the owner's yes.", ""]
    for p in report["problems"]:
        lines.append(f"- Refused row: {p}")
    for e in report.get("errors") or []:
        lines.append(f"- Not made yet, retried on the next run: {e['key']}: {e['error']}")
    for d in report.get("retry_dropped") or []:
        lines.append(f"- Pending from an earlier run, now not made (today's gate): {d['key']}: {d['why']}")
    if report.get("retried"):
        lines.append(f"- Retried from earlier runs: {report['retried']} post(s); made now: "
                     f"{', '.join(k for k in made if k not in {r['key'] for _, r in band}) or 'none'}")
    for post, rec in band:
        lines += [f"## {rec['role_title'] or 'Role not named'} · {rec['company'] or 'company not named'} · {post['url']}"]
        lines += _fmt(rec)
        m = made.get(rec["key"])
        if m:
            lines.append(f"- Made: prospect {m['prospect']}; job "
                         f"{m['job'] or 'none: the post names no role or company'}")
        elif report["write"]:
            lines.append("- Not made yet (see the line above); the next run retries it")
        else:
            lines.append("- Would make: the poster's prospect" + (" and a job" if rec["role_title"] and rec["company"]
                                                                  else " (no job: the post names no role or company)"))
        lines += ["", jc.wrap_untrusted(post["text"], post["platform"], limit=1500), ""]
    if other:
        lines += ["## Off-role or out of band (recorded, not made)", ""]
        for post, rec in other:
            lines.append(f"- {rec['role_title'] or 'role not named'} · {rec['company'] or '?'} · {rec['years_call']} · "
                         f"{rec.get('where_call') or 'in band'} · {post['url']}")
        lines.append("")
    if commentary:
        lines += ["## Not hiring: commentary, news, job boards, agencies, bots (recorded, nothing made)", ""]
        for post, rec in commentary:
            lines.append(f"- {post['url']} · {rec.get('not_hiring') or 'not a hiring post'}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- trend and yield
def _in(day: str | None, start: date, end: date) -> bool:
    d = jc.parse_date(day)
    return bool(d) and start <= d <= end


def _counts(recs: list[dict], dim: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in recs:
        values = r.get(dim)
        for v in (values if isinstance(values, list) else [values]):
            if v:
                out.setdefault(str(v), []).append(r["url"])
    return out


def _table(name: str, now: dict[str, list[str]], before: dict[str, list[str]]) -> list[str]:
    keys = sorted(set(now) | set(before), key=lambda k: (-len(now.get(k, [])), -(len(now.get(k, [])) - len(before.get(k, []))), k))
    if not keys:
        return [f"### {name}", "", "None this week or last.", ""]
    lines = [f"### {name}", "", f"| {name.lower()} | this week | last week | change | links |", "|---|---|---|---|---|"]
    for k in keys:
        n, b = len(now.get(k, [])), len(before.get(k, []))
        links = " ".join(f"[{i + 1}]({u})" for i, u in enumerate(now.get(k, [])[:3]))
        lines.append(f"| {k} | {n} | {b} | {n - b:+d} | {links} |")
    return lines + [""]


def _taxonomy_text(root: Path) -> str:
    path = Path(root) / "profile" / "skills-taxonomy.yaml"
    return path.read_text(encoding="utf-8").lower() if path.is_file() else ""


def yield_counts(root: Path, start: date, end: date) -> dict[str, dict[str, Any]]:
    """Per platform over [start, end]: spend, posts read, hiring posts, roles in band, people added, notes
    drafted, replies (missing is not empty: LinkedIn spend is the owner's time, shown as 0 USD)."""
    led = load_ledger(root)["records"].values()
    out: dict[str, dict[str, Any]] = {p: {k: 0 for k in YIELD_KEYS} for p in PLATFORMS}
    usage = Path(root) / "state" / "x" / "usage.jsonl"
    for row in jc.read_jsonl(usage) if usage.is_file() else []:
        if _in(row.get("day"), start, end):
            out["x"]["spend_usd"] += float(row.get("usd") or 0)
            if row.get("endpoint") == "search":
                out["x"]["posts_read"] += int(row.get("results") or 0)
    people: dict[str, set[str]] = {p: set() for p in PLATFORMS}
    for r in led:
        p = r.get("platform")
        if p not in out:
            continue
        if p == "linkedin" and _in(r.get("ingested"), start, end):
            out[p]["posts_read"] += 1                       # the LinkedIn pass reads by hand: every row is a post read
        if r.get("hiring") and _in(r.get("date"), start, end):
            out[p]["hiring_posts"] += 1
            out[p]["roles_in_band"] += bool(r.get("in_band") and r.get("role_title"))
        if r.get("prospect") and _in(r.get("made_on"), start, end):
            people[p].add(r["prospect"])
    for p in PLATFORMS:
        out[p]["people_added"] = len(people[p])
    posters = {s: p for p in PLATFORMS for s in people[p]} | {r["prospect"]: r["platform"] for r in led
                                                              if r.get("prospect") and r.get("platform") in out}
    outbox = Path(root) / "outbox"
    for f in sorted(outbox.glob("*.md")) if outbox.is_dir() else []:
        try:
            meta, _ = jc.read_record(f)
        except Exception:  # noqa: BLE001 - an unreadable draft never stops the count
            continue
        if meta.get("prospect") in posters and _in(str(meta.get("created") or ""), start, end):
            out[posters[meta["prospect"]]]["notes_drafted"] += 1
    for f in sorted((Path(root) / "touches").glob("*.jsonl")) if (Path(root) / "touches").is_dir() else []:
        for t in jc.read_jsonl(f):
            if t.get("direction") == "in" and t.get("prospect") in posters and _in(str(t.get("at") or "")[:10], start, end):
                out[posters[t["prospect"]]]["replies"] += 1
    out["x"]["spend_usd"] = round(out["x"]["spend_usd"], 4)
    return out


def trend(root: Path, *, today: date, write_yield: bool = False) -> dict[str, Any]:
    root = Path(root)
    recs = [r for r in load_ledger(root)["records"].values() if r.get("hiring")]
    w0, w1 = today - timedelta(days=6), today
    b0, b1 = today - timedelta(days=13), today - timedelta(days=7)
    now = [r for r in recs if _in(r.get("date"), w0, w1)]
    before = [r for r in recs if _in(r.get("date"), b0, b1)]
    lines = [f"# Hiring-post trend, week ending {today.isoformat()} ({w0.isoformat()} to {w1.isoformat()} against "
             f"{b0.isoformat()} to {b1.isoformat()})", "",
             f"Hiring posts: {len(now)} this week ({sum(r['platform'] == 'x' for r in now)} X, "
             f"{sum(r['platform'] == 'linkedin' for r in now)} LinkedIn), {len(before)} the week before. Counted from "
             "state/posts/records.jsonl (scripts/posts.py); one post read on both platforms counts once.", ""]
    dims = (("Role families", "family"), ("Skills", "skills"), ("Companies", "company"), ("Stages", "stage"))
    tables = {dim: (_counts(now, dim), _counts(before, dim)) for _, dim in dims}
    for name, dim in dims:
        lines += _table(name, *tables[dim])
    lines += ["## What it means", ""]
    tax = _taxonomy_text(root)
    meaning = []
    fam_now, fam_before = tables["family"]
    for k, urls in sorted(fam_now.items(), key=lambda kv: -len(kv[1])):
        if len(urls) > len(fam_before.get(k, [])) and len(urls) >= 2:
            meaning.append(f"- Targeting: more posts for {k} ({len(urls)} against {len(fam_before.get(k, []))}): keep "
                           "that search and lens near the top of the queue.")
    for k, urls in sorted(fam_before.items()):
        if len(fam_now.get(k, [])) < len(urls) and len(urls) >= 2:
            meaning.append(f"- Targeting: fewer posts for {k} ({len(fam_now.get(k, []))} against {len(urls)}).")
    sk_now, sk_before = tables["skills"]
    for k, urls in sorted(sk_now.items(), key=lambda kv: -len(kv[1])):
        if len(urls) > len(sk_before.get(k, [])) and len(urls) >= 2:
            backed = k.lower() in tax
            meaning.append(f"- Profile keywords: {k} rising ({len(urls)} against {len(sk_before.get(k, []))}): " +
                           ("in the skills taxonomy, so lead with it where the evidence holds." if backed else
                            "not in the skills taxonomy: never added without evidence (truth rule); a prep or learning "
                            "note only."))
    co_now, _ = tables["company"]
    for k, urls in sorted(co_now.items(), key=lambda kv: -len(kv[1])):
        if len(urls) >= 2:
            meaning.append(f"- People: {len(urls)} hiring posts from {k} this week: check people-watch and the pursuits.")
    lines += meaning or ["- Too few hiring posts this week to read a trend into; the yield below says what the reads "
                         "bought."]
    lines += ["", "## Yield per credit", "",
              "| platform | spend USD | posts read | hiring posts | roles in band | people added | notes drafted | "
              "replies | hiring posts per USD |", "|---|---|---|---|---|---|---|---|---|"]
    week = yield_counts(root, w0, w1)
    for p in PLATFORMS:
        y = week[p]
        per = (f"{y['hiring_posts'] / y['spend_usd']:.1f}" if y["spend_usd"] else
               ("free (the owner's time)" if p == "linkedin" else "no spend"))
        lines.append(f"| {p} | {y['spend_usd']:.2f} | {y['posts_read']} | {y['hiring_posts']} | {y['roles_in_band']} | "
                     f"{y['people_added']} | {y['notes_drafted']} | {y['replies']} | {per} |")
    path = root / "state" / "review" / f"posts-trend-{today.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = {"trend": str(path), "this_week": len(now), "last_week": len(before), "yield_week": week}
    if write_yield:
        registry = root / "targets" / "platforms.yaml"
        if registry.is_file():
            month = yield_counts(root, today - timedelta(days=29), today)
            out["platforms_written"] = write_posts_yield(registry, month, today)
        else:
            out["platforms_written"] = 0
            out["yield_note"] = f"no {registry.relative_to(root)}: posts_yield not written (the trend report is)"
    return out


def write_posts_yield(path: Path, counts: dict[str, dict[str, Any]], as_of: date) -> int:
    """`posts_yield:` and `posts_yield_as_of:` on the x and linkedin entries, in place (added after `yield_as_of:` the
    first time); every other line is left as it was."""
    lines = Path(path).read_text(encoding="utf-8").splitlines(keepends=True)
    out, current, touched, i = [], None, 0, 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^\s*-\s+id:\s*([\w.-]+)", line)
        if m:
            current = m.group(1)
        if current in counts and re.match(r"^\s+posts_yield(?:_as_of)?:", line):
            i += 1
            continue                                         # rewritten below, after yield_as_of
        out.append(line)
        y = re.match(r"^(\s+)yield_as_of:", line)
        if y and current in counts:
            c = counts[current]
            body = ", ".join(f"{k}: {c.get(k, 0)}" for k in YIELD_KEYS)
            out.append(f"{y.group(1)}posts_yield: {{{body}}}\n")
            out.append(f"{y.group(1)}posts_yield_as_of: '{as_of.isoformat()}'\n")
            touched += 1
        i += 1
    Path(path).write_text("".join(out), encoding="utf-8")
    return touched


# --------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", type=Path, default=jc.JS_ROOT)
    common.add_argument("--os-root", type=Path, default=None)
    common.add_argument("--today", type=jc.cli_date, default=None)
    xp = sub.add_parser("x", parents=[common], help="the X watch (paid), then ingest")
    xp.add_argument("--config", default="targets/x-watch.yaml")
    xp.add_argument("--write", action="store_true")
    ig = sub.add_parser("ingest", parents=[common])
    ig.add_argument("--file", action="append", type=Path, default=None)
    ig.add_argument("--write", action="store_true", help="append records, make prospects and jobs (default: dry run)")
    ck = sub.add_parser("check", parents=[common])
    ck.add_argument("--file", required=True, type=Path)
    tr = sub.add_parser("trend", parents=[common])
    tr.add_argument("--write-yield", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.root)
    os_root = Path(args.os_root) if args.os_root else jc.os_root_for(root)
    today = args.today or jc.today()

    def out(obj: Any) -> None:
        print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))

    try:
        if args.cmd == "check":
            platform = input_files(root, [args.file])[0][0]
            rows, problems = read_rows(platform, args.file)
            warnings = [f"{r['url']}: no author_url; the poster is matched by name and company only (add the profile "
                        "URL when the pass can see it)" for r in rows if platform == "linkedin" and not r["author"]["url"]]
            out({"rows": len(rows), "problems": problems, "warnings": warnings})
            return 1 if problems else 0
        if args.cmd == "trend":
            out(trend(root, today=today, write_yield=args.write_yield))
            return 0
        files = None
        if args.cmd == "x":
            import x_api
            cfg = Path(args.config) if Path(args.config).is_absolute() else root / args.config
            client = x_api.Client("read", now=datetime.now(timezone.utc), js=root, os_root=os_root)
            got = x_api.watch(client, cfg, today=today)
            print(json.dumps({"watch": {"queries": got["queries"], "candidates": len(got["candidates"])}}), file=sys.stderr)
            files = [posts_dir(root) / f"x-{today.isoformat()}.jsonl"]
            files = [f for f in files if f.is_file()]
            if not files:
                out({"note": "the watch found no new hiring post; nothing to ingest"})
                return 0
        else:
            files = args.file
        out(ingest(root, today=today, files=files, write=args.write, os_root=os_root))
        return 0
    except PostsError as exc:
        out({"error": str(exc)})
        return 2
    except Exception as exc:  # noqa: BLE001 - an X refusal (credits, budget) is reported, never a traceback
        if type(exc).__name__ == "XError":
            out({"error": str(exc)})
            return 2
        raise


if __name__ == "__main__":
    sys.exit(main())
