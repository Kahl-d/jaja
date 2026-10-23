# corpus-keeper: Id formats

Moved from the corpus-keeper card to keep it lean; the card links here and binds it as if inline.

**Ids:** evidence `ev-<prefix>-NNN` (next number from
`grep -oE "^  ev-<prefix>-[0-9]{3}" profile/evidence.yaml | sort | tail -1`); conflicts `C<highest+1>`; bullets
`<PREFIX>-NN`; employment `exp-<slug>`. An id is never reused, renumbered or repointed.
