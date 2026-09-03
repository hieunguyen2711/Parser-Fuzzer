"""The Step 4.2 gate must catch each way a generator can be useless.

The strategies here are TEST FIXTURES, not candidate generators. They are
hand-written caricatures of specific failure modes, deliberately crude. The
real grammar-derived strategy is the LLM's output in Step 4; writing one by
hand would hollow out the part of the assignment being graded.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import strategies as st

from fuzzer.validate import validate_strategy

HARNESS = Path(__file__).resolve().parents[1] / "build" / "harness"

pytestmark = pytest.mark.skipif(not HARNESS.exists(), reason="run `make` first")

# Varied, well-formed XML: distinct names, attributes, nesting, self-closing.
NAME = st.sampled_from(["a", "b", "item", "root", "x1"])
WELL_FORMED = st.builds(
    lambda tag, attr, body: (
        f"<{tag}{attr}/>" if body is None else f"<{tag}{attr}>{body}</{tag}>"
    ),
    tag=NAME,
    attr=st.sampled_from(["", ' x="1"', ' y="two"', ' a="1" b="2"']),
    body=st.one_of(st.none(), st.sampled_from(["text", "", "<c/>", "<c>d</c>"])),
)

REPETITIVE = st.just("<a/>")          # 100% accepted, tests nothing
DEGENERATE = st.just("")              # near-empty output

# A whole "grammar" of three documents. Looks healthy on every ratio -- 100%
# accepted, 3 distinct out of 3 sampled -- because Hypothesis exhausts the
# space and the sample shrinks with it.
TINY_SPACE = st.builds(lambda t: f"<{t}/>", t=st.sampled_from(["a", "b", "item"]))


def test_passes_a_varied_well_formed_generator():
    result = validate_strategy(WELL_FORMED, HARNESS)
    assert result.passed, result.brief()
    assert result.acceptance_rate > 0.9


def test_catches_a_generator_the_parser_refuses():
    from fuzzer.strategies.baseline import BASELINE

    result = validate_strategy(BASELINE, HARNESS)
    assert not result.passed
    assert any("accepted" in r for r in result.reasons)


def test_catches_a_repetitive_generator_despite_full_acceptance():
    """The failure acceptance rate cannot see.

    `<a/>` is accepted 100% of the time, so an acceptance-only gate waves it
    through -- and the loop then spends its remaining budget fuzzing one
    four-byte document.
    """
    result = validate_strategy(REPETITIVE, HARNESS)
    assert result.acceptance_rate == 1.0, "premise: this is fully accepted"
    assert not result.passed
    assert any("distinct" in r for r in result.reasons)


def test_catches_a_generator_whose_space_is_exhausted():
    """The failure that hides behind good-looking ratios.

    Every proportion this generator reports is perfect. Only comparing the
    sample it produced against the sample that was asked for reveals that its
    entire language is three documents.
    """
    result = validate_strategy(TINY_SPACE, HARNESS)
    assert result.acceptance_rate == 1.0
    assert result.distinct == result.sampled, "premise: no repeats within the sample"
    assert result.sampled < result.requested, "premise: Hypothesis ran out"
    assert not result.passed
    assert any("exhausted" in r for r in result.reasons)


def test_catches_a_degenerate_generator():
    result = validate_strategy(DEGENERATE, HARNESS)
    assert not result.passed
    assert any("bytes" in r or "distinct" in r for r in result.reasons)


def test_a_crash_does_not_fail_the_gate():
    """A generator that crashes the parser has succeeded, not failed.

    Verified against the mock, which crashes on a known trigger; failing the
    gate here would make the loop discard the one generator that worked.
    """
    mock = HARNESS.parent / "harness-mock"
    if not mock.exists():
        pytest.skip("run `make mock` first")

    crashing = st.just("CRASH")
    result = validate_strategy(crashing, mock)
    assert result.crashed > 0
    assert result.crashing_input == b"CRASH"
    assert not any("accepted" in r for r in result.reasons), (
        "a crashing generator must not be failed for low acceptance"
    )


if __name__ == "__main__":
    # So `python tests/this_file.py` runs the tests rather than importing them
    # and silently exiting. `pytest` and `python -m pytest` remain the normal
    # ways in; this is just for when you reach for the file directly.
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
