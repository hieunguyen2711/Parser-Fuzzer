"""The seed prompt must actually carry what it claims to carry.

These are cheap structural checks, not judgements of prompt quality. They
exist because the prompt interpolates the .g4 files by path: renaming or
moving a grammar file would silently produce a prompt with an empty grammar
section, and the first sign of trouble would be a strategy that ignores the
grammar entirely -- an expensive way to find a typo, given the loop has five
iterations of budget.
"""

from __future__ import annotations

import pytest

from fuzzer.prompts import SYSTEM, seed_prompt


@pytest.fixture(scope="module")
def prompt() -> str:
    return seed_prompt()


def test_carries_both_grammars(prompt: str):
    assert "lexer grammar XMLmxmlLexer" in prompt
    assert "parser grammar XMLmxmlParser" in prompt
    # A rule from each, so an empty or truncated file is caught.
    assert "SPECIAL_OPEN" in prompt
    assert "csvFile" not in prompt, "CSV grammar leaked in from the superseded work"


def test_states_the_non_context_free_constraints(prompt: str):
    """The three rules no grammar can express, which the code must enforce."""
    for phrase in ("name ONCE and reuse it", "WITHOUT replacement", "five mxml supports"):
        assert phrase in prompt


def test_requires_real_recursion(prompt: str):
    assert "st.recursive" in prompt
    assert "composite" in prompt
    assert "Do NOT flatten" in prompt


def test_carries_verified_mxml_behavior(prompt: str):
    """The part general XML knowledge cannot supply."""
    for phrase in (
        "XML does not start with '<'",
        "illegal second root",
        "need not be quoted",
        "50,000 nested elements",
    ):
        assert phrase in prompt


def test_specifies_an_output_contract(prompt: str):
    assert "exactly one Python code block" in prompt
    assert "STRATEGY" in prompt
    assert "surrogatepass" in prompt


def test_forbids_nondeterminism(prompt: str):
    """Anything outside Hypothesis draws breaks shrinking."""
    assert "Do not\n   import or call `random`" in prompt
    assert "breaks shrinking" in prompt


def test_is_not_absurdly_large(prompt: str):
    """Budget guard: this prompt is resent, so its size is paid repeatedly."""
    assert len(prompt) + len(SYSTEM) < 40_000


if __name__ == "__main__":
    # So `python tests/this_file.py` runs the tests rather than importing them
    # and silently exiting. `pytest` and `python -m pytest` remain the normal
    # ways in; this is just for when you reach for the file directly.
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
