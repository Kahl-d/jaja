# Apply Assist: the answer rules

Moved from the apply-assist card's Gate section to keep the card lean; the card binds it as if inline.

**Answers.** Work-authorization questions: the legal-visa role's scripted answers verbatim (the yes/no form answers
and the written sentence, both in the user's policy config); a rewording of the same question takes the same answer;
a question splitting now from the future, or asking visa type, OPT dates or E-Verify details: the script only if it
answers exactly that, else ask the owner and legal-visa adds a script. Salary: the pack's row, never a number from
memory. Years: the posting's own count, never a total on the page; the bank's spans are upper bounds, round down; an
unsupported number is asked. GitHub activity numbers only as the profile allows. Rows that say "ask the owner" are
asked. Dropdowns: the option saying the same thing as the row, else ask. Free text: `cover.md` or `why-us.md`
unedited only when the form asks and the file exists; you write no new prose: an essay field goes back to the lane,
whose `letters` skill writes it (a company whose AI policy binds gets the owner's own draft). A resume-parse prefill
is corrected to the pack.
Good: "Workday, My Information: legal name from the Contact block, phone split (+1; national number), how did you hear
= Company careers page (jobs/<id>.md source greenhouse). Owner: the street address is yours to type." Bad: "5 years"
of Python when the span is 3 years 6 months; No on sponsorship "to move faster"; ticking "I certify the above is true".
