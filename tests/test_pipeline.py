"""Regression tests for the fuzzing pipeline, run against tests/mock_target.c.

These do not test the target library -- they test the machinery that will
judge it. The mock produces every outcome on demand, so each classification
branch can be checked against a known-correct answer. Without this, a bug in
the runner looks exactly like a clean fuzzing run: zero crashes found.

Build the mock first:  make mock
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import strategies as st

from fuzzer.campaign import run_campaign
from fuzzer.runner import Outcome, run_once
from fuzzer.triage import signature_for

MOCK = Path(__file__).resolve().parents[1] / "build" / "harness-mock"

pytestmark = pytest.mark.skipif(
    not MOCK.exists(), reason="run `make mock` first"
)


def test_accepts_valid_input():
    result = run_once(MOCK, b"{valid")
    assert result.outcome is Outcome.ACCEPTED
    assert result.exit_code == 0


def test_rejects_invalid_input_without_calling_it_a_crash():
    """The expensive mistake: scoring a correct rejection as a finding."""
    result = run_once(MOCK, b"not a document")
    assert result.outcome is Outcome.REJECTED
    assert result.exit_code == 2
    assert not result.crashed
    assert "reject:" in result.stderr


def test_detects_asan_abort():
    result = run_once(MOCK, b"CRASH")
    assert result.outcome is Outcome.CRASHED
    assert result.signal == 6              # SIGABRT
    assert result.exit_code is None        # killed, so no exit code
    assert "AddressSanitizer" in result.stderr


def test_detects_ubsan_abort():
    result = run_once(MOCK, b"UB")
    assert result.outcome is Outcome.CRASHED
    assert "runtime error:" in result.stderr


def test_timeout_counts_as_crash():
    """Per the assignment: a hang is a denial-of-service bug, not a skip."""
    result = run_once(MOCK, b"HANG", timeout_s=1.0)
    assert result.outcome is Outcome.CRASHED
    assert result.timed_out


def test_signature_is_stable_across_inputs_hitting_one_bug():
    a = signature_for(run_once(MOCK, b"CRASH"))
    b = signature_for(run_once(MOCK, b"zzzCRASHzzz"))
    assert a.digest == b.digest
    assert a.error_type == "heap-buffer-overflow"


def test_signature_separates_distinct_bugs():
    asan = signature_for(run_once(MOCK, b"CRASH"))
    ubsan = signature_for(run_once(MOCK, b"UB"))
    assert asan.digest != ubsan.digest


# A tiny alphabet that can assemble the mock's triggers, so the campaign finds
# a crash within a few dozen examples instead of by luck.
TRIGGERING = st.lists(
    st.sampled_from(["{", "a", "}", "CRASH"]), min_size=1, max_size=6
).map("".join)


def test_campaign_finds_and_shrinks_a_crash():
    result = run_campaign(TRIGGERING, MOCK, max_examples=200, timeout_s=2.0)
    assert result.crash is not None, "campaign failed to find the planted bug"

    # The point of routing detection through @given: Hypothesis shrinks. The
    # minimal input that still reproduces is exactly the trigger, nothing else.
    assert result.crash.payload == b"CRASH", (
        f"expected a shrunk 5-byte reproducer, got {result.crash.payload!r}"
    )


def test_campaign_ignores_already_known_crashes():
    """What lets a later run find a second bug instead of the first one again."""
    first = run_campaign(TRIGGERING, MOCK, max_examples=200, timeout_s=2.0)
    assert first.crash is not None

    known = frozenset({first.crash.signature.digest})
    second = run_campaign(
        TRIGGERING, MOCK, known_digests=known, max_examples=100, timeout_s=2.0
    )
    assert second.crash is None
    assert second.stats.crashed > 0, "known crashes should still be counted"


def test_stats_track_acceptance_rate():
    accepting = st.just("{ok")
    result = run_campaign(accepting, MOCK, max_examples=20, timeout_s=2.0)
    assert result.crash is None
    assert result.stats.acceptance_rate == 1.0

    rejecting = st.just("nope")
    result = run_campaign(rejecting, MOCK, max_examples=20, timeout_s=2.0)
    assert result.stats.acceptance_rate == 0.0
    assert result.stats.rejection_messages, "rejection reasons should be logged"
