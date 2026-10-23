# Configuration

Nothing about a person is hard-coded. Scripts read three sections, `identity`, `style` and `policy`, through
`scripts/agency_config.py`. This guide explains where the values live, how they merge, and what the main keys do.
Every key below was read from `config/defaults.d/*.yaml`; the list is not exhaustive, because those 40 fragments hold
every key the code reads.

## Where values live

| Layer | Location | Who edits it |
|---|---|---|
| Neutral defaults | `config/defaults.d/*.yaml` in the repository | Contributors. Every key the code reads has a safe, non-personal value here. |
| Your values | `<data>/config/*.yaml` (the blank folder starts with `identity.yaml`, `style.yaml`, `policy.yaml`) | You |
| Test persona | `scripts/tests/persona.d/*.yaml` | Contributors. Used instead of your folder under pytest. |

Files in each directory merge in file-name order and mappings merge key by key, so later files win and you only write
the keys you change. Your file names do not matter; `identity.yaml`, `style.yaml` and `policy.yaml` are a
convention. Under pytest the data folder is ignored entirely.

A key left empty (`[]`, `""`, `null`, `0`) usually means the check it drives is off. The blank `config/policy.yaml`
that `init-data.py` writes lists every key with its default, commented out; remove the leading `# ` from a key and
its parents to set it.

In code, a script reads a value by dotted path with a default:

```python
import agency_config as cfg
cfg.get("style.no_em_dash", False)
cfg.section("identity")
```

Check that your files parse with `python3 scripts/doctor.py`; the `config-values` line reports how many files parsed.

## `identity`: who you are on paper

| Key | Type | Does |
|---|---|---|
| `identity.name` | string | Your name as the system prints it |
| `identity.emails` | list | Addresses that are yours |
| `identity.signature_names` | list | Names you sign with, for example `["Sam", "Sam Rivera"]`; used to recognise you in letters and in mail |
| `identity.linkedin_url` | string | Your profile URL |
| `identity.resume_file_pattern` | regex | Which resume file names are yours, for example `^SamRivera_Resume[\w.\-]*\.pdf$` |
| `identity.resume_pdf_name` | string | The file name a built resume variant carries (default `Resume.pdf`) |
| `identity.owner_label` | string | How section titles address you: "Needs you" or "Needs Sam" (default `you`) |
| `identity.timezone`, `identity.timezone_label` | IANA name, short label | Mail-pass times, calendar defaults, and the label printed after a time (defaults `UTC`) |
| `identity.degrees_held` | list | Degree kinds you hold (`bachelor`, `master`, `phd`); a posting with several degree paths is read at the easiest one you meet |
| `identity.account_emails` | list | Mailboxes the system reads; defaults to `identity.emails` |
| `identity.schools` | list | Schools to recognize in a contact's headline: `[{name, pattern}]` |
| `identity.signature_urls` | list | Links that belong in your signature block |

## `style`: how you write

The repository ships the mechanism and no rules of its own. An empty `style` section means no style checks.

| Key | Type | Does |
|---|---|---|
| `style.no_em_dash`, `style.no_en_dash` | bool | When true, a dash of that kind in a draft or an apply pack is a finding |
| `style.banned_phrases` | list of regexes | Phrases a message may not contain (not applied to comments or official letters) |
| `style.banned_words` | list | Single words that are findings |
| `style.ai_tell_words`, `style.ai_tell_density` | list, int | Words that read as machine-written; three or more in one paragraph is a finding (default density 3) |
| `style.greeting`, `style.signoffs` | string, list | The greeting form (for example `"Hi {first_name},"`) and accepted sign-offs |
| `style.send.plain_text` | bool | True blocks markdown, emoji, images and inline styles in a message |
| `style.letters.banned_phrases`, `style.letters.prose_only` | list, bool | Extra rules for cover letters and why-us answers |
| `style.resume_slop_words` | list | Words and phrases the resume rubric rejects on a page |
| `style.resume_section_order` | list or null | Heading order of a resume; null uses the conventional ATS order |

Your voice itself lives in `profile/VOICE.md`; the style keys are the mechanical checks beside it.

## `policy`: how you search

### Hard gates (`scripts/fit_gates.py`)

| Key | Does |
|---|---|
| `policy.fit_gates.engineer_months` | Months of professional engineering experience you hold, for the years comparison |
| `policy.fit_gates.cold_max_years` | A posting asking for up to this many years is applied to cold; more only through a person |
| `policy.fit_gates.reject_years` | A posting asking this many years or more fails (null: never) |
| `policy.fit_gates.degree_conferred` | `YYYY-MM` of your newest degree, used when `profile/facts.yaml` has none |
| `policy.location.home_cities`, `home_state_words`, `home_cities_need_state` | What counts as your home region |
| `policy.employer.staffing_names` | Regexes for staffing and recruiting firms, which fail the employer gate |
| `policy.employer.it_services_names` | Regexes for IT-services consultancies, which go to review |
| `policy.sponsorship.weight` | Fit points removed for a plain "no sponsorship" (0 ignores it; sponsorship is weighed by default, not a fail) |

`targets/fit-gates.yaml` in your data folder holds `allow_default`: kinds of role (`staff`, `principal`, `new-grad`,
`intern`, `manager`, `lead`, `contract`, `fellowship`, `years`) that you accept, so they become a note instead of a
fail.

### Tiers and strategy

| Key | Default | Does |
|---|---|---|
| `policy.tiers.dream_fit` | 4.5 | An unlisted application at or above this fit is tier dream |
| `policy.tiers.focused_fit` | 4.0 | At or above this (and below dream) is tier focused; below it, mass |
| `policy.strategy.no_reply_tries` | 15 | A bucket with this many applications in 30 days and no reply is flagged |
| `policy.strategy.dream_share_min`, `big_share_max` | 0.20, 0.60 | Flags when the dream-list share is too low or the big-company share too high |
| `policy.strategy.interview_by_day` | 21 | Flags when no interview has happened by this day of your job clock |
| `policy.strategy.pause_ruling` | empty | The id of a ruling that, while it stands, pauses mass applications |

### Outreach and sending

| Key | Default | Does |
|---|---|---|
| `policy.outreach.comments_per_day` | 10 | The most comments a day |
| `policy.outreach.linkedin_ask_chars`, `linkedin_max_chars` | 400, 600 | Length limits for LinkedIn messages |
| `policy.outreach.first_email_words` | [60, 120] | Word range for a first email |
| `policy.outreach.touch_cap` | 3 | The most touches to one person before it stops |
| `policy.outreach.job_ask_patterns` | empty | Patterns that make a comment or post "ask for a job", which is blocked |
| `policy.outreach.confidential_patterns` | empty | Patterns that must never appear in a public message |
| `policy.send.email_word_warn` | 250 | A mail longer than this gets a warning |
| `policy.send.own_addresses` | empty | Extra addresses that are yours; sending to one is blocked |
| `policy.nda.always_terms`, `policy.nda_terms` | empty | Terms you must never publish on a page or message |
| `policy.letters.caps` | cover 150 to 400 words; why-us and else up to 150 | Allowed word counts per letter file |

### Pacing (`scripts/pace.py`, `scripts/browser_pace.py`)

| Key | Default | Does |
|---|---|---|
| `policy.pace.caps.linkedin` | connect 15, message 30, comment 10, follow 20 | Hard daily caps on outward acts |
| `policy.pace.caps.x` | follow 10, message 5, comment 5 | The same on X |
| `policy.pace.read_caps` | linkedin 150, x 100 | Soft daily caps on page-heavy reads |
| `policy.pace.gap_seconds` | 120 | Minimum gap between two acts on one platform |
| `policy.browser.daily_profile_views` | 25 | Most distinct LinkedIn profiles read per day |
| `policy.browser.min_gap_seconds` | 20 | Human pace between two profile or job-page reads |
| `policy.browser.daily_job_views` | 20 | Most named LinkedIn job pages read per day |

### Discovery and ranking

| Key | Does |
|---|---|
| `policy.title_filter.include`, `exclude` | Regexes a posting title must or must not match when fetching ATS boards |
| `policy.hunt.*` | Word lists and score parameters for the hunt triage score (`ai_title`, `hard_off`, `soft_off`, `seniority`, `location_part`, ...) |
| `policy.prospects.*` | How the people CRM classifies and scores a sender (`bigtech_companies`, `ai_lab_companies`, `fit_keywords`, ...) |
| `policy.segments.known_names` | Company-size segments by name: `big-tech`, `ai-lab`, `enterprise`, `growth`, `startup` |
| `policy.fresh10.hunt_cut` | An unscored role needs a hunt score at or over this to enter the daily list (default 69) |
| `policy.mail.backlog_start` | Where a first Gmail harvest starts (empty: 90 days back) |
| `policy.apply.*` | Apply-pack rules: portal hosts, whether an employer that fails E-Verify blocks a pack, required answer wording |

## A worked example

The demo candidate's `config/` shows what a filled-in set looks like (fictional values):

```yaml
# config/policy.yaml
policy:
  fit_gates:
    engineer_months: 56
    cold_max_years: 4
    reject_years: 10
    degree_conferred: "2021-05"
  location:
    home_cities: ["austin", "round rock", "cedar park"]
    home_state_words: ["tx", "texas"]
  employer:
    staffing_names: ["^globex hiring partners\\b"]
  outreach:
    comments_per_day: 5
    touch_cap: 3
```

```yaml
# config/style.yaml
style:
  no_em_dash: true
  no_en_dash: true
  banned_phrases: [finds you well, excited to, thrilled, circle back]
  greeting: "Hi {first_name},"
  signoffs: ["Sam"]
```

With these, a posting that asks for ten or more years fails, one asking for five or six is reachable only through a
person, a remote posting with no stated country goes to review, and a draft containing an em-dash or "circle back"
fails its lint.

## Where a value is read

When a key does not seem to take effect, find who reads it:

```bash
grep -rn "policy.fit_gates" scripts/*.py | head
```

and confirm the file parsed with `python3 scripts/doctor.py`. If a key is missing from `config/defaults.d`, the
script that reads it is wrong, and that is a bug to report.
