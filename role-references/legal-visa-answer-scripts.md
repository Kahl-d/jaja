# legal-visa: Procedure step 4 (answer scripts: the format apply_pack reads)

Moved from the legal-visa card to keep it lean; the card links here and binds it as if inline.

4. **Answer scripts:** `## Answer scripts` in `legal/timers.md` holds the form pairs and the Written and Spoken
   scripts, built from the owner's own words (quoted there, never paraphrased here). `apply_pack.py` copies them
   verbatim into every pack: pairs as `"<question>" → **<answer>**` (plain double quotes, `→` or `->`, bold
   answer), one question matching `authori[sz]` and one matching `sponsor`, whose answers start as required by
   `policy.apply.authorization_answer_starts_with` and `policy.apply.sponsorship_answer_starts_with`; scripts as
   `- Written (3 sentences): "<text>"` and `- Spoken ...: "<text>"` bullets (or `### Written` sub-headings), one
   script per bullet. Check after every edit: `python3 scripts/apply_pack.py answers --today <date>` shows both
   answers and both scripts sourced `legal/timers.md ## Answer scripts`, no "legal-visa answer missing".
