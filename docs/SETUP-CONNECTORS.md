# Connector setup for the job search

One page, for the owner of the agency. Commands run from the repo root; the data folder is the owner's own. After
any step, check with:

```bash
python3 scripts/connectors_doctor.py          # add --skip-mcp for a faster run
```

It prints one row per connector and writes `state/connectors.json` and `state/connectors.md` (the
dashboard shows that block). **red** blocks work, **amber** means optional or due soon, **green**
means working, **verify-in-session** means a claude.ai connector the doctor cannot see. It exits 1
while anything is red and never prints a token or key.

This page is the one setup guide for Gmail, Calendar, LinkedIn, Adzuna and the job platforms' alerts
(section 8), which browser does what (section 9) and Firecrawl (section 10); `scripts/gmail_auth.py` points here.
Section 11 is the connector map (which path does what, and its doctor check); section 12 is the Claude Code
permission line for approved executes; section 13 says why no desktop mail or calendar app is needed; section 14 is
X (Twitter); section 15 is how to add your own claude.ai connector ids and use two Claude accounts.

**Where secrets live.** Never in the repo and never in the data folder's git history. Tokens are `chmod 600` files in
`~/.config/jaja/` (override the folder with the `JAJA_CONFIG_DIR` environment variable), API keys are in
the OS keychain (macOS Keychain, or your platform's secret store) or in environment variables, and routine secrets
for cloud runs are set in the scheduler. The scripts refuse a credential path inside the repo, and the doctor goes
red when a token file is not `chmod 600`. No script ever prints a token or key.

## 1. Google Cloud project and OAuth client (once, about 10 minutes)

1. At console.cloud.google.com, signed in as the Google account the job search uses, create a project (for
   example `jaja`).
2. APIs and Services, Library: enable the **Gmail API** and the **Google Calendar API**.
3. OAuth consent screen (Google Auth Platform): user type **External**, publishing status
   **Testing**, and add your own address as a test user. In Testing, Google expires refresh
   tokens 7 days after consent, so you log in again once a week. Stay in Testing: going to
   Production needs Google's security review for Gmail read scopes.
4. Data access: add `gmail.readonly`, `gmail.labels`, `gmail.compose`, `gmail.send`, `calendar.events`.
5. Clients, Create client, type **Desktop app**. Download the JSON and store it outside the repo:

```bash
mkdir -p ~/.config/jaja && chmod 700 ~/.config/jaja
mv ~/Downloads/client_secret_*.json ~/.config/jaja/gmail_client_secret.json
chmod 600 ~/.config/jaja/gmail_client_secret.json
pip3 install google-auth google-auth-oauthlib google-api-python-client
```

## 2. Google tokens (split on purpose)

| File in `~/.config/jaja/` (chmod 600) | Scopes | Create with | Used by | Keep it? |
|---|---|---|---|---|
| `gmail_token_read.json` | gmail.readonly, gmail.labels | `python3 scripts/gmail_auth.py login` | harvest, mail-triage, routines | yes; log in again weekly |
| `gmail_token_compose.json` | gmail.compose | `python3 scripts/gmail_auth.py login --compose` | only `gmail_pull.py draft --approval <file>` on an approved file | no: create just before, delete right after |
| `gmail_token_send.json` | gmail.send (nothing else) | `python3 scripts/gmail_auth.py login --send` | only `gmail_send.py execute --approval <file>` on an approved file (send mode) | yes; log in again weekly |
| `calendar_token.json` | calendar.events | `python3 scripts/gmail_auth.py login --calendar` | `calendar_events.py` (agenda, conflicts, free time; invites with guests and a Meet link, changes and cancels after approval) and `calendar_hold.py` holds | yes; log in again weekly |

`calendar.events` is the narrowest scope that lets the scripts read the primary calendar and create, change and
delete events (with guests and Meet links). Free/busy is computed from the primary calendar's own events, because
Google's freeBusy.query does not accept `calendar.events` and no script needs other people's calendars. The doctor
goes amber when the calendar token carries a broader scope. Invites, and changes or cancels that email guests, run
only through an approved file: `calendar_events.py invite ... --request`, then `calendar_events.py execute --approval
<A-id>` after the owner says yes.

`gmail.compose` can send mail, which is why it never sits in the token routines load. The doctor
goes amber when the compose token is on disk with no approved email approval waiting, and red
when any token file is not chmod 600. `python3 scripts/gmail_auth.py status` shows each token's
re-login date. If an old single-token file `gmail_token.json` is still in the config folder, delete it. The send
token holds `gmail.send` only: it cannot read, list or draft, and the doctor turns red if it ever carries another
scope. The scripts use these chmod 600 files, refused inside the repo.

## 3. claude.ai connectors (Gmail, Google Calendar)

Connect both in claude.ai, Settings, Connectors. Cloud routines use them for read-only triage.
The doctor reports them as "configured in claude.ai, verify in session": to verify, ask Claude in
a session to search one recent email and to list today's events. Section 15 covers the tool names.

## 4. LinkedIn data export (monthly)

1. LinkedIn, Me, Settings and Privacy, Data privacy, **Get a copy of your data**. Pick Messages,
   Invitations, Connections and Profile, then Request archive. LinkedIn emails a download link,
   within 48 hours for Messages and Connections; the link lasts 72 hours.
2. Save the zip into `profile/_inbox/linkedin-export/` (gitignored; raw messages never enter git).
   Put the date in the name, for example `Basic_LinkedIn_Data_YYYY-MM-DD.zip`.
3. Parse and ingest:

```bash
python3 scripts/linkedin_export.py parse profile/_inbox/linkedin-export/Basic_LinkedIn_Data_YYYY-MM-DD.zip
python3 scripts/prospects.py ingest
```

The doctor goes amber when the newest export is unparsed or older than 30 days.

## 5. Adzuna keys (optional job source)

Register at developer.adzuna.com for a free app id and key (the scouts stay under 25 requests a
minute and 250 a day). Add to `~/.zshrc`, never to the repo, then open a new terminal:

```bash
export ADZUNA_APP_ID="..."
export ADZUNA_APP_KEY="..."
```

For cloud routines, add the same two names as routine secrets. The doctor checks only that the
names are set.

## 6. The job-search MCP server

The repo ships a small MCP server (`mcp/`) that exposes the agency's records to a session. Register it in your
engine's MCP config (for Claude Code, a project `.mcp.json` entry that runs `uv run job-search-mcp` from `mcp/`).
It needs `uv`. First run and check:

```bash
cd mcp && uv sync && uv run pytest -q && uv run job-search-mcp selftest
```

Restart Claude Code or Codex afterwards. If the entry points at `${CLAUDE_PROJECT_DIR}`, start the session at the
repo root; if the server shows as failed, that variable is the first suspect.

## 7. What to rotate, and when

| What | When | How |
|---|---|---|
| X tokens | only when X revokes them (they refresh themselves every 2 hours) | `python3 scripts/x_api.py login` and `login --write` |
| Read, send and calendar tokens | every 7 days (Testing mode) | `python3 scripts/gmail_auth.py login` (and `--send`, `--calendar`) |
| Compose token | after every draft run | `rm ~/.config/jaja/gmail_token_compose.json`; recreate for the next approved batch |
| OAuth client secret | on any suspicion, otherwise yearly | Cloud console, the client, reset secret, download, replace the file, log in again for every token |
| Everything Google | if the machine or a token may be exposed | remove the app at myaccount.google.com/permissions, delete `~/.config/jaja/*token*.json`, redo step 2 |
| Adzuna key | on any suspicion | regenerate in the Adzuna dashboard, update `~/.zshrc` and routine secrets |
| Firecrawl key | on any suspicion | regenerate in the Firecrawl dashboard, then step 2 of section 10 again (`-U` replaces the old one) |
| LinkedIn export | monthly | step 4; delete older zips once the new one is parsed |
| Job alerts | monthly (platform watch, the 3rd) | platform-scout checks each platform's alerts still arrive; delete alerts on platforms it retires |

## 8. Job platforms and alerts

Most job boards forbid bots and offer you no API, so their own alert emails carry their postings in:
platform alert → Gmail → mail-triage (harvest, label `job_alert`) → pipeline stage `alerts`
(`scripts/job_alerts.py`) → `jobs/` (tag `jd-missing`: held at review until job-scout reads the JD).
Every platform, its terms and the full steps: `targets/platforms.yaml` (`alert_setup`) and
`research/job-platforms-2026.md`. Alerts need Gmail connected (steps 1 to 3); mail that waits longer
than 14 days is logged as stale, not parsed. Use one search set everywhere, built from your own targets
(`targets/roles.md`): the role titles you want, your locations and remote, full-time, your seniority band, the
work-authorization or sponsorship filter wherever a platform has one, and daily email.

**Set up first (about 45 minutes, in this order)**

| # | Platform | Your steps |
|---|---|---|
| 1 | LinkedIn | linkedin.com/jobs: for each search in your set, pick the experience level and job type, turn on "Job alert", then Manage job alerts: Daily, Email. One alert per title and location group. LinkedIn has no sponsorship filter. |
| 2 | HiringCafe | hiringcafe.com, create the account; run your title search with your location and seniority filters; Save search, alert on (daily). |
| 3 | Wellfound | Edit Profile, set your work authorization; run your title searches with your locations; pencil icon, name the search, "Get Job alerts for this search", daily. |
| 4 | YC Work at a Startup | workatastartup.com with your login; one profile for your role family and locations, your work-authorization need. Turn on email notifications if offered. Founders who write to you land in mail-triage as outreach. |
| 5 | Indeed + Glassdoor | One Indeed account signs you into both. Indeed: run your title search, bottom of page 1, enter the email, Activate, Daily; repeat per title. Glassdoor: the same searches, turn the alert on, Daily. |
| 6 | Welcome to the Jungle | welcometothejungle.com, create the account; run your searches; yellow "Create an alert" for each. Use a sponsorship filter if the panel shows one. |

Then tell Claude which ones are done ("alerts set up on LinkedIn, HiringCafe, ..."): platform-scout
records the date (`alerts_on`) and, the next morning, checks the flow with
`python3 scripts/job_alerts.py --dry-run` (one line per platform whose alerts arrived).

**Second wave, this week:** ZipRecruiter (bell icon on your searches; daily), city or sector job boards that
fit your targets, Jobright (titles as above, its sponsorship filter if you need it, keep the daily email; never
turn on its auto-apply agent), your school's job board (Handshake or similar) with saved searches and alerts on,
and Adzuna keys (step 5).

**Later:** Dice, TrueUp and Levels.fyi alerts if they offer them, and a weekly 10-minute browse of venture firms'
portfolio job boards (send companies worth watching to job-scout).

**Never set up:** auto-apply tools (any Easy Apply bot, any "apply for me" agent), browser extensions on
LinkedIn, paid "we apply for you" services, scrapers. Himalayas, Hacker News and the company boards need nothing
from you: scripts poll their public APIs.

Check after any change: `python3 scripts/platform_yield.py --check` (the registry is consistent) and,
once alerts flow, `python3 scripts/platform_yield.py` (30-day yield per platform).

## 9. Browsers (which one does what)

Two browsers are available in a Claude Code session; a third is optional.

| Browser | What it is | Signed in to | Use it for |
|---|---|---|---|
| **Claude in Chrome** (the extension in your Chrome) | your own Chrome profile, driven through the extension | whatever you sign in to there | **every portal of yours**: reading your inboxes and profiles, recording postings, filling a form with you present, uploading an approved resume (`file_upload`), downloading your own data (a click on the site's own download button, each one on your yes) |
| **Built-in browser** (the pane in the Claude app) | an isolated browser inside the app, the app's default | nothing (a clean profile, separate from Chrome) | public pages: company sites, careers pages, docs, previews of dev servers. Never your accounts |
| **Playwright** (optional, not installed as a tool) | a scripted browser with its own saved profile | nothing | a scripted job on a public page that a plain fetch cannot read (a JavaScript-only careers page), or a repeatable download of a public file. Add it only when such a job exists |

**Your part, once (about 15 minutes):** in the Chrome where the Claude extension runs, sign in to the portals you
use (Wellfound, GitHub, Indeed, which also signs you into Glassdoor, YC Work at a Startup, Jobright, your school's job
board, ZipRecruiter, Welcome to the Jungle ...). Tick "keep me signed in". Claude never types a password or solves a
CAPTCHA; when a site asks, it stops and hands the tab back to you. Then say "portals signed in" and the next check confirms
each one.

**The rules do not change with the browser.** Reading your own pages is free. Anything that changes a page
or leaves the house (a message, a profile edit, an upload, an application, a connection request) is a
draft with an approval first and your yes for each action. LinkedIn stays at human pace
and only in a session you run; no routine or subagent drives a browser on your accounts.

**Not recommended: stealth browsers.** Tools whose selling point is masking the automation flag to get past bot
detection are the wrong fit: they keep no signed-in profile, so they cannot reach your portals, and the portals' terms
forbid evading bot detection, which puts your LinkedIn and job accounts at risk (and which Claude will not do). For
public pages, a plain fetch and the built-in browser already work. Cloud browser agents sign your accounts in on
someone else's machine; the Claude in Chrome extension gives the same signed-in access with a narrower permission.

## 10. Firecrawl (public pages to dated evidence; about 3 minutes, once)

Firecrawl (firecrawl.dev) opens a public page in a real browser and returns its text, so JavaScript careers
sites can be saved as evidence. The agency uses it through one script, `scripts/web_snapshot.py`, which checks
robots.txt and its crawl-delay itself, refuses LinkedIn, Glassdoor, Indeed and every job platform in
`targets/platforms.yaml`, always asks Firecrawl for its plain proxy (never its anti-bot bypass, which Firecrawl
otherwise turns on by itself when a site blocks it), and stops at the credit budget in the budgets config (set it
below your plan's monthly credits; one page is one credit). Prefer this script over a general Firecrawl connector:
a connector hands every tool to any agent without those guards. (If you do add the connector, section 15, keep it
out of the allowlists of lanes that do not need it.)

1. Sign up at https://www.firecrawl.dev (the free plan is enough) and copy the API key from its dashboard.
2. Put the key in the OS keychain. This prompts for it, so it never lands in your shell history or a file (macOS
   shown; use your platform's secret store elsewhere, or the `FIRECRAWL_API_KEY` environment variable):

```bash
security add-generic-password -U -a "$USER" -s jaja-firecrawl -w
```

3. Check (prints where the key is and the credits used, never the key):

```bash
python3 scripts/web_snapshot.py status
python3 scripts/connectors_doctor.py --skip-mcp      # the firecrawl row turns green
```

For a cloud routine (no Keychain there), add `FIRECRAWL_API_KEY` as a routine secret instead; the script reads
the environment first. Every call appends one line to `state/firecrawl/usage.jsonl` (when, which site, credits;
never page text), which is what the budget counts.

## 11. The connector map (which path does what)

Every write below waits for the owner: the script paths refuse to act without an approved, ticked, unexpired
approval file whose content still hashes to what the owner approved, and the owner sees the full item first.

| Need | Use | Why this path | Doctor check |
|---|---|---|---|
| Search or read mail; a plain-text reply | claude.ai Gmail connector, in a session | quickest for reading; a reply is sent only on the owner's yes in that chat | `claude-ai-gmail` (verify in session) |
| Harvest and classify mail (routines, lanes) | `gmail_pull.py harvest` | the read token cannot write; receipts; resumable | `gmail-read-token` |
| A Gmail draft of an approved outbox reply | `gmail_pull.py draft --approval <A-id>` | outbox drafts, voice gate, compose token only for that run | `gmail-compose-token` |
| Send a reply or new mail WITH local attachments (PDF resumes) | `gmail_send.py compose ... --request`, then `show` and `execute --approval <A-id>` | the connector takes attachments only as inline base64; this path hashes recipients, body and each file's sha256, sends once, logs the touch | `gmail-send-token` |
| A Gmail draft with attachments for the owner to send | `gmail_send.py compose ... --mode draft --request`, then `execute` | nothing sends; the draft waits in the owner's Drafts | `gmail-compose-token` |
| Read the calendar: events, clashes, free time | `calendar_events.py agenda` or `check` | read only | `calendar-token` |
| Invite guests (with a Meet link), change or cancel an event the agency created | `calendar_events.py` `invite`, `change` or `cancel` with `--request`, then `show` and `execute` | fixed event id (no double invite), etag guard, guests see only the title and description | `calendar-token` |
| Accept, decline or tentatively accept an invite someone else sent | `calendar_events.py rsvp --event-id <id> --response ... --request`, then `execute` | the organizer is told, so it is always gated | `calendar-token` |
| A private tentative hold (nobody invited) | `calendar_hold.py build --request`, then `create` | no guest is ever emailed | `calendar-token` |
| X: read a post, a profile, search, the hiring watch list | `x_api.py post`, `user`, `search`, `watch` (READ token) | official API, budgeted, text wrapped untrusted; never x.com pages | `x-client`, `x-read-token` |
| X: follow, DM, reply, post | `x_api.py follow`, `dm`, `reply` or `post-new` with `--request`, then `show` and `execute` | one approval per action, daily caps, a refusal (closed DMs) is final | `x-write-token` |
| Quick calendar reads in a chat | claude.ai Google Calendar connector (if connected) | reads only; writes go through the scripts | `claude-ai-calendar` (verify in session) |

Run `python3 scripts/connectors_doctor.py --skip-mcp` for the whole table; red blocks work, amber means act soon.

## 12. The Claude Code permission line for approved executes

Claude Code's auto mode sends shell commands to a classifier, which may block `calendar_events.py execute`. An
**ask** rule is evaluated before the classifier and always shows the owner a permission prompt with the exact
command, even in auto mode, so the owner stays the final click on anything that emails someone; an **allow** rule
would run it with no prompt (narrow Bash allow rules are resolved before the classifier). Sources:
code.claude.com/docs/en/auto-mode-config ("Content-scoped ask rules ... are evaluated before the classifier and
always force a permission prompt, even in auto mode"; "narrow Bash and PowerShell allow rules such as `Bash(npm
test)` stay in effect in auto mode. Claude Code resolves them before the classifier runs") and
code.claude.com/docs/en/permissions.

Recommended: the owner adds these lines to the project's `.claude/settings.local.json` (gitignored). Scripts never
edit settings.

```json
{
  "permissions": {
    "ask": [
      "Bash(python3 scripts/calendar_events.py execute *)",
      "Bash(python3 /path/to/jaja/scripts/calendar_events.py execute *)",
      "Bash(python3 scripts/gmail_send.py execute *)",
      "Bash(python3 /path/to/jaja/scripts/gmail_send.py execute *)"
    ]
  }
}
```

The rule matches the command as written: run `execute` directly from the repo root (first form) or by absolute path
(second form). A command wrapped as `cd ... && python3 ...` is split and each part must match. If you prefer no
prompt at all, the same four lines under `"allow"` instead of `"ask"` let the command run unprompted; the script
still refuses anything without an approved file, but nobody sees the command before it emails a guest, so `ask` is
the recommendation. `/permissions` shows the rules in effect.

## 13. Desktop mail and calendar apps

None is needed. Gmail and Google Calendar are fully reachable through their APIs (the scripts above) and the
claude.ai connectors, attachments are read from local files, and the browser choices are in section 9. An app
(Mail, Calendar, a menu-bar client) would add a second writer to the same accounts without receipts or approvals, so
it would weaken the gate rather than fill a gap. Revisit only if a need appears that no API covers.

## 14. X (Twitter), through the official API (about 10 minutes, once)

X is a channel like LinkedIn: posts and job calls, people, DMs, replies. Everything goes through the official
X API (`scripts/x_api.py`, skill `x-channel`). Nothing reads x.com pages or uses cookies: X's terms ban scraping "in
any form" without written consent, and its automation rules want the API; unofficial cookie-based clients warn of
account suspension. The API is pay-per-use (see docs.x.com for current pricing; there is no free tier for reads), so
set a daily and monthly cap under `connectors: x` in the budgets config; `x_api.py status` shows what is used.

You, signed in to your own X account (no agent types a password or a token):
1. console.x.com: accept the developer terms, create a Project and an App.
2. The App, User authentication settings: **OAuth 2.0**; type **Native App** (a public client: no secret);
   permissions **Read and write and Direct message**; callback URL exactly `http://127.0.0.1:8765/callback`;
   website URL any page of your own.
3. Copy the **Client ID** into `~/.config/jaja/x_client.json` as `{"client_id": "<the id>"}`, then
   `chmod 600 ~/.config/jaja/x_client.json`.
4. Buy a small credit amount in the Developer Console: reads spend credits.
5. From the repo root: `python3 scripts/x_api.py login` (the READ token: tweet.read, users.read, offline.access) and
   `python3 scripts/x_api.py login --write` (the WRITE token: adds tweet.write, follows.write, dm.read, dm.write).
   Each opens X's consent page in your browser and saves a chmod 600 token in `~/.config/jaja/`.
6. Check: `python3 scripts/connectors_doctor.py --skip-mcp` shows `x-client`, `x-read-token` and `x-write-token` green;
   `python3 scripts/x_api.py post <any post URL>` prints the post and its reply count.

Daily caps for follow, DM, reply and post are `CAPS` in the script (override in config). Every write is one approval
shown in full first (`x_api.py show`); a reply or post is public, so the confidential-terms check in
`scripts/outbox.py` applies to every word. A person whose DMs are closed answers 403: the refusal is recorded, never
worked around.

The official MCP server (`xmcp`, hosted at `https://api.x.com/mcp` through the `xurl` bridge, docs.x.com/tools/mcp)
is optional. If you want it in chat sessions, enable it with `X_API_TOOL_ALLOWLIST` set to read tools only; writes
stay on the approval path above, never on an MCP tool.

## 15. Adding your own connector ids, and more than one Claude account

claude.ai connectors (Gmail, Google Calendar, Firecrawl, and others) appear in a session as tools named
`mcp__claude_ai_<Connector>__<tool>` (for example `mcp__claude_ai_Gmail__search_threads`). A connector you add from a
custom or per-account connector may instead appear as `mcp__<connector-id>__<tool>`, where the id is specific to
your account. Those ids are yours: this repo ships none.

To use a connector id in a lane:
1. Connect it in claude.ai, Settings, Connectors, and start a new session (a session started before the change does
   not see it).
2. Find the exact tool names in the session (`/mcp`, or ask Claude to list its MCP tools).
3. Add the names to the lane's `tools_allow` in `lanes/<lane>/AGENT.md`, then regenerate the agents with the repo's
   sync script. Allow only the read and draft tools a lane needs. A connector that also exposes send, reply, forward
   or delete tools keeps those out of every allowlist: outward actions go through the approval-file scripts of
   section 11, never through a connector tool.
4. Verify with `python3 scripts/connectors_doctor.py`: claude.ai connectors show as "verify in session", so ask
   Claude to run one read call (search one recent email, list today's events) and confirm it answers.

If you use two Claude accounts on one machine, the local parts are shared (tokens in `~/.config/jaja/`, the
keychain entry, the MCP servers registered in the project) and the claude.ai connectors and the Claude in Chrome
pairing are per account. Carry both accounts' tool-name sets in the lane allowlists, or keep one account's
connectors authoritative and use the scripts (section 11) from the other.
