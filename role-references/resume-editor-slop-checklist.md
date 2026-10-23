# resume-editor: Slop and voice checklist (S1 to S12d)

Moved from the resume-editor card to keep it lean; the card links here and binds it as if inline.

Voice and style: follow the user's style config (`<data>/config/style.yaml`, keys `style.*`) and voice guide
(`<data>/profile/VOICE.md`) if present; these are filled in by the user, not shipped. The banned-word list is
`style.resume_slop_words`, the dash rule is `style.no_em_dash`; `scripts/rubric_check.py` applies both.

| Id | Rule | Example to flag |
|---|---|---|
| S1 | Banned phrases: the user's list (`style.resume_slop_words`) plus generic filler that says nothing | "Leveraged cutting-edge LLMs" |
| S2 | Punctuation and character rules from the style config | a character the config forbids |
| S3 | Buzzword stacking: three or more modifiers or buzz nouns with no verb logic | "scalable robust agentic RAG pipelines" |
| S4 | Vague verbs: worked on, helped, involved in, responsible for, handled, participated in | "Responsible for evals" |
| S5 | Fake precision: numbers without a source and ceiling, rounder or more precise than the corpus, % without a base | "improved accuracy by 20%" |
| S6 | A distinctive motif more than twice (counter output 3x or more) | "schema-validated" three times |
| S6a | Motif scan runs over the whole rendered text; repeated headers, locations, school and company names are not motifs, but the review says so in one written line per repeated string | a city name x5 with no reason given |
| S7 | Sentence-shape monotony: more than 2 bullets opening with the same verb, or 3 in a row with one template | "Built... Built... Built..." |
| S8 | Every bullet answers what, for whom, and the result (qualitative when the corpus has no number) | a bullet naming only a tool |
| S9 | A framework named without its logic | "Used LangGraph" |
| S10 | Defensive framing | "not just prompts" |
| S11 | Each experience opens with a one-line context lead-in; Skills lists only what bullets show | a skill with no bullet behind it |
| S12a | Cover letter: follows the letters skill and the user's style config; every paragraph earns its place with one real experience as proof; not a list of experiences | "At X I built A, B and C. At Y I built D." |
| S12b | Why-us: answers from the reverse-engineered company (what they build and why); their copy is not quoted back; no flattery | "I love your mission to revolutionize ..." |
| S12c | Anything-else: plain, one belief and one real example; not a resume repeat | a pasted summary |
| S12d | All three: letters_check.py exits 0; no generic opener, one clear close; true to the page and the record | "I am excited to apply" |

## Review-flag words

Generic filler adjectives and adverbs (the kind that claim quality without evidence) are a finding unless literal and
evidenced; the user's own list is `style.resume_slop_words`.
