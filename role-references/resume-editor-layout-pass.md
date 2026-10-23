# resume-editor: the layout pass

A page can pass every word-level gate and still look wrong. The card links here and binds this as if inline. Run it
in step 2, after the ATS re-run.

1. Measure: `python3 scripts/page_layout_check.py V/<pdf from V/meta.yaml> --out <scratch dir>/editor-<slug>-r<round>/`
   (a folder of your own, never the tailor's). Exit 0 PASS; exit 1 FAIL is a High finding quoting each failure line;
   exit 2 is UNCHECKABLE, so the page is not approved. The report gives bottom slack (empty band beyond the bottom
   margin, limit 3%), the header ratio (air above the tightest section header over the median bullet break, minimum
   1.5), the hierarchy warning (an entry with more air than a header), margins, rows per inch.
2. Look: Read the `page-1.png` it wrote (the Read tool shows the image). Judge it as the recruiter's first scan
   ("Page design" in the `page-review` skill): balance (the page full to its margin, no blank strip at
   the foot, no section crammed while another breathes), whitespace (headers clearly separated, bullets apart),
   clutter (can the eye find the name, current title, dates and every section without reading), readability at a
   glance (no orphan word, no line past the margin, dates on one rhythm).
3. Sign: one line in review.md's front matter, falsifiable, quoting the numbers:
   `layout: PASS (bottom slack 0.6%, header ratio 1.8; balanced, sections clear)` or
   `layout: FAIL <the region and what is wrong>`. A layout FAIL, the script's or yours, is High: the verdict stays
   `changes-requested` and is never `approved`. Verdict table row 5a LAID OUT carries the command and the line.
4. Fixes are not yours: a short page goes back to the tailor (fill with the strongest true work, never padding);
   crowded headers or margins go to the RESUME lane (renderer and templates).
