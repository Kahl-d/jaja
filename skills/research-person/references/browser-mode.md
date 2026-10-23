# research-person: Browser mode

Detail for the skill's browser mode; the skill links here.

## Browser mode (LinkedIn and pages that need the owner's session)
Department `AGENTS.md` (LinkedIn rule): LinkedIn may be read in the owner's browser, in a session the owner runs (never a
subagent or a routine), for people someone named. Nothing is ever sent, clicked or signalled.
1. **Take the browser** (one holder; see the workflow document's browser section): announce in your file that you take
   it and why; release it when done. Load the Claude in Chrome tools (`tabs_context_mcp`, `navigate`, `get_page_text`,
   `find`, `computer`); `python3 scripts/browser_research.py status` shows today's room.
2. **Find the exact person** from a URL the owner gave, the company's page, or a web search for the full name plus the
   company; a LinkedIn search for that one name is allowed and only the matching result is opened. Never a company
   "people" list; never record the other results.
3. **Before each profile:** `python3 scripts/browser_research.py view --person "<Name>" --url <profile URL>
   --named-by "<who named them>" --lane <lane>` (exit 3: stop). **Before each click:**
   `python3 scripts/browser_research.py click-check "<the control's label>"` (exit 2: do not click). Reading controls only.
4. **Read** with `get_page_text`: About, Experience, Education, Featured, the hiring badge, recent posts. Paraphrase;
   no bulk copying. Source line: "LinkedIn profile, read in the owner's browser <date>".
5. **Cross-reference on the web** (papers, talks, repos, company pages), each dated and cited; then step 1's `verify`,
   release the browser and note what you read.
Stop at once, hand the browser back and note `linkedin-warning` on a login, CAPTCHA or security check, a view-limit or
"unusual activity" notice, or any page asking for an action.
