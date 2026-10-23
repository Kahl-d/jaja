# resume-editor: The verdict table

Moved from the resume-editor card to keep it lean; the card links here and binds it as if inline.

| # | claim | signed? | evidence |
|:--:|---|:--:|---|
| 1 | COMPLETE: every finding has an outcome | ☐ | N = X FIXED + Y NO-FIX + Z REFUTED |
| 2 | TRUE: every evidence id resolves and matches the corpus | ☐ | ids checked, lint exit 0 |
| 3 | CONSISTENT: facts.yaml values; no open conflict touched | ☐ | conflict ids checked |
| 4 | CLEAN: 0 style-config violations (banned phrases, punctuation), motifs at most 2 | ☐ | scan outputs |
| 5 | PARSES: ats_check exit 0 on this PDF | ☐ | command, result line |
| 5a | LAID OUT: page_layout_check.py exit 0 on this PDF and page-1.png read; `layout: PASS` in the front matter | ☐ | command, result line, the layout line |
| 6 | BELIEVABLE: senior engineer pass done | ☐ | notes |
| 6a | ALIGNED: the page answers the title and the whole JD; preferred qualifications answered or reasoned | ☐ | `## Alignment` checked against the JD and the page |
| 7 | RE-REVIEWED WHOLE: two consecutive passes, nothing new | ☐ | pass ids |
| 8 | NOT COVERED | n/a | for example "PDFKit copy test not run" |
