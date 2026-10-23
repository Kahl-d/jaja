# resume-tailor: Cover letter, why-us and anything-else

Moved from the resume-tailor card to keep it lean; the card links here and binds it as if inline.

**Cover letter, why-us and anything-else** (only when an application asks; the method is the `letters` skill).
Letters present the owner's ideas and use real experience as proof; they are not a list of experiences. Voice and
style: follow the user's style config (`<data>/config/style.yaml`, keys `style.*`) and voice guide
(`<data>/profile/VOICE.md`) if present; these are filled in by the user, not shipped. `cover.md`: paragraphs that
each carry one idea with one real experience as its proof (ev ids in a trailing comment per paragraph), within the
length the letters skill sets. `why-us.md`: the company reverse-engineered first (NOTES `## Why-us research`: what
they build, why, the founders' and the technology's thinking), answered through the owner's own view, never their
copy quoted back (in any of the three). `else.md`: plain, one real example. All three: no lists, no tool runs, no
generic opener, no sponsorship or salary, nothing that signals a search or names a confidential employer's
internals; `python3 scripts/letters_check.py V/` exits 0 before the editor.
