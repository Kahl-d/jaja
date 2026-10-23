# Resume templates: how bases and variants become ATS-safe PDFs

These are the system files `scripts/render_resume.py` reads. `tools/init-data.py` copies them into a new data
folder (`resumes/templates/`, and the two base designs into `resumes/bases/general/`); a file the data folder does
not have falls back to this repo copy, and `measure --record` always writes into the data folder.

| File | What it is |
|---|---|
| `rendercv-ats.yaml` | The house template (`--template house`, the default): RenderCV 2.8 `design` + `locale` + `settings`, theme `engineeringresumes`, dates inline after a bar (reasons in its header). A resume cannot override a template. |
| `template-classic.yaml`, `template-inline.yaml` | The general base's own designs (`--template classic`: dates right-aligned on the title line; `--template inline`: each date inline after its title). Copied next to the base at `resumes/bases/general/`. |
| `line-widths.yaml` | Measured characters per line for each layout (template and density), for the `bullet-too-long` estimate. Written by `render_resume.py measure --record`. |
| `rulings.yaml` | The owner's rulings on how resumes render that bend a lint rule (`labeled_links`, `long_bullets`), each naming the record of the words (`source:`). Ships empty (`rules: []`); the entry format is in its header. |
| `latex-ats/ats-preamble.tex`, `latex-ats/resume.tex.j2` | LaTeX fallback (`--engine latex`): a hardened preamble plus a Jinja2 layout with the same text structure as the RenderCV design. |
| `latex-ats/faangpath/` | A FAANGPath-style class and skeleton, hardened, for hand-tuned resumes only. |

## The flow

1. **The base**: `resumes/bases/general/resume.yaml`. Variants copy it and its two design files to
   `resumes/variants/<slug>/`, set `sidecar: {lens, base_of: general, job}`, then reorder, select and reword for the JD.
2. **Lint**: `python3 scripts/render_resume.py lint --input V/resume.yaml --template V/template-classic.yaml --density one-page`
   until it exits 0. It fails on held text (`profile/PROFILE-HOLDS.md`) in any rendered field, even when bold,
   italics or spacing split it; `python3 scripts/check_holds.py V/resume.yaml` lists each hit by line.
3. **Render**: `python3 scripts/render_resume.py render --input V/resume.yaml --out V --template V/template-classic.yaml --density one-page`
   (`--docx` for Workday; `--engine latex` only if RenderCV fails; `--max-pages` defaults to 1). Writes the PDF,
   an optional `.docx` and `V/meta.yaml` (template and its sha256, density, hash).
4. **ATS check**: `python3 scripts/ats_check.py check --pdf V/<pdf> --jd applications/<slug>/jd.md --vendor <vendor> --contact-email <your email> --taxonomy profile/skills-taxonomy.yaml --source V/resume.yaml --out V`
   writes `V/ats-report.md` and `V/ats-report.json`; exit 0 is required.
5. **Editor** signs, then the owner approves. A resume is done only when both the ATS check and the editor pass.

Exit codes, both scripts: 0 ok, 1 content failure, 2 usage error, 3 toolchain failure (render only).

## The resume YAML contract

```yaml
sidecar:                       # never rendered: build metadata copied into meta.yaml
  lens: agentic-systems        # variants: the emphasis label (kebab slug); the base: null
  base_of: general             # variants: general (a folder under resumes/bases/); the base: null
  job: jobs/acme-12345.md      # variants: the job record; the base: null
cv:
  name: ...
  email: ...
  social_networks: [{network: LinkedIn, username: ...}]
  website: https://...
  sections:
    Experience:
      - company: ...
        position: ...
        location: Austin, TX                 # required on Experience and Education
        start_date: 2026-02                  # YYYY-MM (or YYYY-MM-DD); end_date: present
        evidence: [ev-001]                   # optional: evidence for the header facts
        highlights:
          - "Built X for Y, cutting Z <!-- ev:ev-012,ev-013 -->"
```

**Evidence convention.** Every rendered claim ends with a trailing HTML comment `<!-- ev:ID[,ID...] -->` (each id a
key of `profile/evidence.yaml`). Claims are each `highlights` item, each plain-text or `bullet` entry, each one-line
`details` (Skills lines), each `summary`, and `cv.headline`. Quote any bullet that contains `: `. The renderer strips
`sidecar`, entry `evidence` lists and every HTML comment before RenderCV sees the file; `meta.yaml` lists all cited ids.

**Lint codes** (errors block the render; `--force` builds a draft marked `status: failed`): `missing-evidence`,
`unknown-evidence`, `bad-evidence-id`, `evidence-not-trailing`, `not-a-string`, `em-dash`, `table-content`,
`double-space`, `nested-bullet`, `hidden-link`, `raw-typst`, `bullet-too-long` (over 2 estimated lines),
`noncanonical-heading`, `design-override`, `photo`, `missing-email`, `missing-name`, `missing-location`,
`missing-date`, `date-precision`, `bad-date`, `bad-sidecar`, `unknown-base`, `long-bullet-rule`, `link-rule`.
Warnings: `section-order`, `en-dash-in-prose`, `duplicate-bullet`, `stray-comment`. Notes: `long-bullet-allowed`,
`labeled-link-allowed` (each names the ruling that allowed it).

**Line estimate.** `bullet-too-long` wraps each claim greedily at the layout's measured width (`line-widths.yaml`).
Re-measure after a layout change:
`python3 scripts/render_resume.py measure --input scripts/tests/fixtures/resume/calibration.yaml --template <t> --density <d> --record`.
A layout with no entry falls back to the font formula and the run says so.

**Rulings.** A claim may run past two lines only when the owner ruled it (`long_bullets`, keyed by the claim's exact
evidence set, 3 or 4 lines), and a link may show a short label only when the owner ruled that URL (`labeled_links`).
Rulings live in `profile/facts.yaml` `rules:` and in `rulings.yaml` here.

## What every template guarantees

US Letter, one column (no date column), black text, left-aligned with no hyphenation, no icons, footer, top note or
time spans, the sans body font (Source Sans 3) at 10pt or more, three-letter months, no dash in template text.
`render_resume.py` refuses a template that breaks these (`template_problems`).

- Canonical headings, exactly: Education, Skills, Experience, Projects, Publications, Awards (also Summary,
  Certifications), never letter-spaced or split.
- Experience: `**Title** | Mon YYYY - Mon YYYY`, then `Company, Location`, then bullets.
  Education: `**Degree in Area**, Institution, Location | Mon YYYY - Mon YYYY`.
- URLs are shown, never hidden. Page budget: `--max-pages 1`; over budget fails listing the longest sections.

Measured: a right-aligned date column makes some parsers (macOS PDFKit) move dates away from their entries;
inline dates extract identically in pdftotext, pypdf and PDFKit. pdftotext rejoins a compound broken at its hyphen,
so the renderer boxes hyphenated compounds.

## Vendor rules

Secondary-source design constraints, reported by `ats_check.py` as warnings (never failures): Workday handles DOCX
best (`--docx`); Greenhouse is read by a human (dates `Mon YYYY - Mon YYYY`); Lever is blind to abbreviations (spell
each out once); Ashby orphans content under non-standard headings; iCIMS misparses serif fonts under 11pt (the LaTeX
fallback is serif 10pt); Taleo matches keywords literally; SuccessFactors maps to a skills taxonomy (standard skill
names, a Skills section).
