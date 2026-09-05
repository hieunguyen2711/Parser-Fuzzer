"""The refine prompt must carry the last run's behavior and the guardrails.

Iterations 2-5 send this prompt every time, so like the seed it is a structural
contract: it has to embed the previous module (so the model revises rather than
starts over), the computed feedback (so it has numbers to react to), and the
carried-over guardrails -- the Hypothesis API pitfalls and the output contract.
Dropping any of those silently is the failure this pins, and each such failure
would cost one of only a few paid iterations to notice.
"""

from __future__ import annotations

import pytest

from fuzzer.prompts import SYSTEM, refine_prompt

PREV_CODE = "from hypothesis import strategies as st\nSTRATEGY = st.just('<a/>')\n"
FEEDBACK = (
    "GENERATOR CHECK: PASS\n"
    "sampled 30: 19 accepted, 11 rejected, 0 crashed\n"
    "acceptance 63%, 28 distinct outputs, mean 41 bytes\n"
    "  parser said:\n    reject: Bad control character"
)
TRAJECTORY = [(1, 0.48, 0.63)]


@pytest.fixture(scope="module")
def prompt() -> str:
    return refine_prompt(PREV_CODE, FEEDBACK, TRAJECTORY)


def test_embeds_the_previous_module(prompt: str):
    assert "STRATEGY = st.just('<a/>')" in prompt
    assert "revise this whole module" in prompt


def test_embeds_the_feedback(prompt: str):
    assert "acceptance 63%" in prompt
    assert "Bad control character" in prompt


def test_renders_the_trajectory(prompt: str):
    assert "SCORE HISTORY SO FAR" in prompt
    assert "0.48" in prompt


def test_states_the_band_not_maximisation(prompt: str):
    assert "70-85%" in prompt
    assert "NOT 100%" in prompt


def test_carries_the_api_pitfalls(prompt: str):
    """The guardrails the model cannot re-derive from its own last output."""
    assert "max_leaves" in prompt
    assert "st.permutations" in prompt
    assert "st.booleans()` takes NO weighting" in prompt


def test_carries_the_output_contract(prompt: str):
    assert "exactly one Python code block" in prompt
    assert "STRATEGY" in prompt
    assert "surrogatepass" in prompt


def test_carries_mxml_behavior(prompt: str):
    assert "XML does not start with '<'" in prompt


def test_omits_the_grammar_to_save_tokens(prompt: str):
    """The .g4 dump is intentionally not resent -- prev_code embeds its shape."""
    assert "SPECIAL_OPEN" not in prompt
    assert "lexer grammar XMLmxmlLexer" not in prompt


def test_is_not_absurdly_large(prompt: str):
    """Resent every iteration; prev_code dominates, so keep a ceiling on it."""
    assert len(prompt) + len(SYSTEM) < 60_000


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
