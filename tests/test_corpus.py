"""Step 2's demonstration: the harness classifies known inputs correctly.

Unlike test_pipeline.py, which exercises the machinery against a mock, this
runs the real harness against the real mxml build. It is the check that the
seam is wired to the library and that the accept/reject boundary sits where
the grammar analysis says it does.

Every file in corpus/invalid is a *correct rejection*, not a bug. If any of
them ever produces a crash instead, that is a finding -- and this test will
say so, because it distinguishes "rejected" from "crashed" rather than just
asserting nonzero.

Build first:  make
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fuzzer.runner import Outcome, run_once

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "build" / "harness"
VALID = sorted((ROOT / "corpus" / "valid").glob("*.xml"))
INVALID = sorted((ROOT / "corpus" / "invalid").glob("*.xml"))

pytestmark = pytest.mark.skipif(not HARNESS.exists(), reason="run `make` first")


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name)
def test_valid_documents_are_accepted(path: Path):
    result = run_once(HARNESS, path.read_bytes())
    assert result.outcome is Outcome.ACCEPTED, (
        f"{path.name} should parse; got {result.outcome.value}: {result.stderr[:200]}"
    )


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.name)
def test_invalid_documents_are_rejected_not_crashed(path: Path):
    result = run_once(HARNESS, path.read_bytes())
    assert result.outcome is Outcome.REJECTED, (
        f"{path.name} should be cleanly rejected; got {result.outcome.value}: "
        f"{result.stderr[:200]}"
    )
    assert result.stderr.startswith("reject:"), (
        "a rejection should carry mxml's own message, so the loop can cluster "
        f"reasons; got: {result.stderr[:120]!r}"
    )


def test_corpus_is_not_empty():
    """Guards against the parametrized tests silently passing on zero files."""
    assert len(VALID) >= 5 and len(INVALID) >= 5


def test_accept_with_diagnostic_counts_as_accepted():
    """The three-outcome case, pinned so the judgment call cannot drift.

    mxml returns a tree AND reports an error for a control character. The
    harness counts that as accepted and logs the diagnostic separately; see
    the reasoning in harness/fuzz_target.c.
    """
    result = run_once(HARNESS, b"<a>\x01</a>")
    assert result.outcome is Outcome.ACCEPTED
    assert result.stderr.startswith("diag:")
    assert "control character" in result.stderr
