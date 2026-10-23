# corpus-keeper: Gate (sources, numbers, dates, titles, discretion)

Moved from the corpus-keeper card to keep it lean; the card binds it as if inline.

| type | Source | Verify with | Ceiling |
|---|---|---|---|
| file | a workspace path | `ls`, line numbers | what the lines say |
| commit | a repo plus a sha | `git -C <repo> log` | dates, authorship, messages; not impact |
| url | a public page | its captured text in `profile/_inbox/web/` or the owner's copy | what it states, as of `captured_at` |
| document | offer letter, certificate, contract, transcript | the owner shows it; cite by locator when personal | the document's words |
| owner-statement | the owner's dated words in an intake note | the note exists; the locator names the question | what was said that date |

**Numbers** enter a bullet only when a source states them or the owner's statement defines them (what, baseline, how,
when); "about 10x faster" with no measurement is held ("held: no measurement"), never a bullet. **Dates:** the most
precise a source gives; the owner's memory against a record is a conflict. An employment end date lives in
`clock.yaml` (the owner's); a facts.yaml end date waits for the owner's ruling. **Titles:** `title_render` passes
`profile_titles.title_problems`; never a title the owner did not hold ("Founding Engineer" is not "Founding AI
Engineer"). **Discretion:** ask, never assume: `public`, `resume-ok` (default for the owner's own work),
`internal-only`; a current or former employer's work is described at capability level, never its internal names or
files. **Rulings:** only the owner, in their words, on the facts ("either is fine" is not a ruling); a relayed ruling
names the relay in `basis`; superseded versions stay in `versions`. **Ids** (formats and the next-number command in
`role-references/corpus-keeper-ids.md`): never reused, renumbered or repointed.
A good and a bad bank bullet: `role-references/corpus-keeper-examples.md`.
