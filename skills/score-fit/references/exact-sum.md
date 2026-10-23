# score-fit: Procedure step 6 (sum exactly)

Detail for the skill's step 6; the skill links here.

6. **Sum exactly** (never by eye), with the weights of the card's current `weights_version`. The weights and scores in
   this command are placeholders (they only show the shape); replace them with the card's weights and the scores you
   gave:
   `python3 -c "from decimal import Decimal as Dec, ROUND_HALF_UP; w={'A':'0.125','B':'0.125','C':'0.125','D':'0.125','E':'0.125','F':'0.125','G':'0.125','H':'0.125'}; s={'A':4,'B':4,'C':4,'D':4,'E':4,'F':4,'G':4,'H':4}; t=sum(Dec(w[k])*s[k] for k in w); print(t, t.quantize(Dec('0.1'), rounding=ROUND_HALF_UP))"`
   Apply the card's caps, then its verdict bands.

## Every cited id resolves (the skill's Gate)
`for id in $(grep -o "ev-[a-z0-9-]*" applications/<slug>/fit.md | sort -u); do grep -q "^  $id:" profile/evidence.yaml || echo "missing $id"; done`
prints nothing.
