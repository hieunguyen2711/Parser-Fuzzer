"""The loop's control flow, tested without a network or a harness.

Three properties matter and none of them need a real model or a real parser:

  1. A failed gate does NOT spend a 500-example campaign -- it turns the failure
     into the next iteration's feedback instead.
  2. Crash signatures ACCUMULATE across iterations, so a later campaign hunts a
     new bug rather than rediscovering the first one.
  3. The loop stops when the budget is exhausted, cleanly, rather than throwing.

These are enforced by faking the model (canned fenced modules) and stubbing the
harness-touching functions in the loop's namespace.
"""

from __future__ import annotations

import pytest

import fuzzer.loop as loop
from fuzzer.campaign import CampaignResult, CampaignStats, CrashFound
from fuzzer.llm import Budget, BudgetExceeded, Usage
from fuzzer.loop import LoopState, _apply_response, run_loop
from fuzzer.runner import Outcome, RunResult
from fuzzer.triage import CrashSignature
from fuzzer.validate import Validation


class FakeLLM:
    """Stands in for LLM: same generate() contract, no Vertex call."""

    def __init__(self, responses, budget):
        self.responses = list(responses)
        self.budget = budget
        self.model = "fake"
        self.calls = 0

    def generate(self, prompt, system=None, temperature=1.0, max_output_tokens=None):
        self.budget.check()  # raises BudgetExceeded exactly like the real one
        text = self.responses[self.calls % len(self.responses)]
        self.calls += 1
        usage = Usage(model="fake", input_tokens=1, output_tokens=1, seconds=0.0)
        self.budget.record(usage)
        return text, usage


def _fresh_state() -> LoopState:
    return LoopState(prev_code="STRATEGY = None", feedback="seed feedback")


def _passing() -> Validation:
    return Validation(passed=True, accepted=22, rejected=8, sampled=30,
                      requested=30, distinct=30, mean_bytes=20.0)


def _failing() -> Validation:
    return Validation(passed=False, reasons=["parser refuses nearly everything"],
                      accepted=1, rejected=29, sampled=30, requested=30,
                      distinct=30, mean_bytes=8.0)


def _campaign_with_crash(digest: str) -> CampaignResult:
    stats = CampaignStats(accepted=78, rejected=22, crashed=1,
                          input_lengths=list(range(100)),
                          nesting_depths=[i % 9 for i in range(100)])
    sig = CrashSignature(digest, "heap-buffer-overflow", ("parse",))
    run = RunResult(outcome=Outcome.CRASHED, exit_code=None, signal=6,
                    stderr="ERROR: AddressSanitizer: heap-buffer-overflow",
                    duration_s=0.0, timed_out=False)
    crash = CrashFound(sig, b"<a/>", run)
    return CampaignResult(stats=stats, crash=crash)


def test_failed_gate_skips_campaign(monkeypatch):
    monkeypatch.setattr(loop, "extract_code", lambda r: "code")
    monkeypatch.setattr(loop, "_write_strategy_file", lambda p, c, n: None)
    monkeypatch.setattr(loop, "load_strategy", lambda p: object())
    monkeypatch.setattr(loop, "validate_strategy", lambda s, h: _failing())

    def _must_not_run(*a, **k):
        raise AssertionError("campaign ran despite a failed gate")

    monkeypatch.setattr(loop, "run_campaign", _must_not_run)

    state = _fresh_state()
    section = _apply_response(2, "```python\ncode\n```", state, "harness", 500)

    assert "GATE REFUSED" in section
    assert "refuses nearly everything" in state.feedback
    assert state.trajectory == []  # no campaign -> no score point


def test_gate_failure_reverts_to_best_code(monkeypatch):
    """A refused strategy must not become the base for the next iteration:
    prev_code reverts to the last working module so the failure cannot cascade."""
    monkeypatch.setattr(loop, "extract_code", lambda r: "REFUSED CODE")
    monkeypatch.setattr(loop, "_write_strategy_file", lambda p, c, n: None)
    monkeypatch.setattr(loop, "load_strategy", lambda p: object())
    monkeypatch.setattr(loop, "validate_strategy", lambda s, h: _failing())
    monkeypatch.setattr(loop, "run_campaign",
                        lambda *a, **k: pytest.fail("campaign ran on a failed gate"))

    state = _fresh_state()
    state.best_code = "STRATEGY = WORKING"
    _apply_response(2, "```python\nREFUSED CODE\n```", state, "harness", 500)

    assert state.prev_code == "STRATEGY = WORKING"  # reverted, not the refused code
    assert "revise THAT" in state.feedback


def test_load_failure_reverts_to_best_code(monkeypatch):
    """A module that will not import reverts to the last working one too."""
    from fuzzer.strategy_io import ExtractionError

    monkeypatch.setattr(loop, "extract_code", lambda r: "boom")
    monkeypatch.setattr(loop, "_write_strategy_file", lambda p, c, n: None)

    def _boom(p):
        raise ExtractionError("NameError: undefined name 'st_thing'")

    monkeypatch.setattr(loop, "load_strategy", _boom)

    state = _fresh_state()
    state.best_code = "STRATEGY = WORKING"
    _apply_response(2, "resp", state, "harness", 500)

    assert state.prev_code == "STRATEGY = WORKING"
    assert "does not import or run" in state.feedback


def test_passing_iteration_updates_best_code(monkeypatch):
    """A passing iteration that beats the best becomes the new revert target."""
    monkeypatch.setattr(loop, "extract_code", lambda r: "NEW BEST CODE")
    monkeypatch.setattr(loop, "_write_strategy_file", lambda p, c, n: None)
    monkeypatch.setattr(loop, "load_strategy", lambda p: object())
    monkeypatch.setattr(loop, "validate_strategy", lambda s, h: _passing())
    monkeypatch.setattr(loop, "save_finding", lambda f, d: None)
    monkeypatch.setattr(loop, "summarize", lambda stats, findings, iteration: "SUMMARY")
    monkeypatch.setattr(loop, "run_campaign", lambda *a, **k: _campaign_with_crash("aaa"))

    state = _fresh_state()
    state.best_code = "STRATEGY = OLD"
    _apply_response(2, "resp", state, "harness", 500)

    assert state.prev_code == "NEW BEST CODE"
    assert state.best_code == "NEW BEST CODE"
    assert state.best_iteration == 2


def test_known_digests_accumulate(monkeypatch):
    monkeypatch.setattr(loop, "extract_code", lambda r: "code")
    monkeypatch.setattr(loop, "_write_strategy_file", lambda p, c, n: None)
    monkeypatch.setattr(loop, "load_strategy", lambda p: object())
    monkeypatch.setattr(loop, "validate_strategy", lambda s, h: _passing())
    monkeypatch.setattr(loop, "save_finding", lambda f, d: None)
    monkeypatch.setattr(loop, "summarize", lambda stats, findings, iteration: "SUMMARY")

    results = iter([_campaign_with_crash("aaa"), _campaign_with_crash("bbb")])
    monkeypatch.setattr(loop, "run_campaign", lambda *a, **k: next(results))

    state = _fresh_state()
    _apply_response(2, "resp", state, "harness", 500)
    _apply_response(3, "resp", state, "harness", 500)

    assert state.known_digests == frozenset({"aaa", "bbb"})
    assert set(state.findings) == {"aaa", "bbb"}
    assert [t[0] for t in state.trajectory] == [2, 3]


def test_loop_stops_at_budget(monkeypatch, tmp_path):
    # Keep logs out of the repo, and make each iteration a no-op past the call.
    monkeypatch.setattr(loop, "LOG_DIR", tmp_path)
    monkeypatch.setattr(loop, "_apply_response", lambda *a, **k: "## Result\n\n(stub)\n")

    llm = FakeLLM(responses=["```python\ncode\n```"], budget=Budget(max_iterations=2))
    state = _fresh_state()
    run_loop(llm, state, "harness", start=2, stop=10)

    # Fresh budget of 2 -> exactly two calls, then BudgetExceeded stops it.
    assert llm.calls == 2
    assert llm.budget.iterations_used == 2


def test_budget_raises_are_caught(monkeypatch, tmp_path):
    monkeypatch.setattr(loop, "LOG_DIR", tmp_path)
    monkeypatch.setattr(loop, "_apply_response", lambda *a, **k: "")

    # A budget already spent: the very first iteration must not throw.
    llm = FakeLLM(responses=["x"], budget=Budget(max_iterations=0))
    run_loop(llm, _fresh_state(), "harness", start=2, stop=5)
    assert llm.calls == 0


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
