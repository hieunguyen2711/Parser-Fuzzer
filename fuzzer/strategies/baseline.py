"""Step 3's deliberately naive strategy: unstructured text.

This exists to prove the pipeline works end to end -- generate, serialize, run,
classify, log -- not to find bugs. It is the control condition. Whatever the
grammar-derived generator achieves in later iterations is only meaningful
measured against this.

Expect it to perform badly, and expect that to be visible in one number: the
acceptance rate. Random text is essentially never a valid document in any of
the candidate formats, so a real parser should reject ~100% of it. Seeing that
near-zero acceptance rate is itself the first validation that the harness is
wired up correctly and that the acceptance-rate signal has the sensitivity the
refinement loop will need.
"""

from hypothesis import strategies as st

# Printable-ish text plus the punctuation that matters to structured formats,
# so the baseline at least occasionally stumbles into a token the parser knows.
BASELINE = st.text(
    alphabet=st.characters(min_codepoint=1, max_codepoint=0x2FFF),
    min_size=0,
    max_size=200,
)

# A bytes variant, for parsers taking raw bytes rather than text. Kept separate
# because encoding choice is itself a variable worth controlling.
BASELINE_BYTES = st.binary(min_size=0, max_size=200)
