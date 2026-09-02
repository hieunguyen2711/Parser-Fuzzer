"""Run one strategy against the harness for a bounded number of examples.

This is Step 3's pipeline and Step 4.3's "Run" stage: generate -> serialize ->
execute -> classify -> log. Three design constraints shape it, and each one is
easy to get wrong in a way that quietly ruins the results.

1. THE CRASH CHECK LIVES INSIDE @given.
   Step 5.4 is explicit that reproducers must be shrunk by Hypothesis rather
   than reported as the first crashing input seen. Hypothesis only shrinks what
   makes the test *fail*, so the check has to raise from inside the test body.
   Wrapping a loop around a passing test and inspecting results afterwards --
   the obvious design -- gets you a 200-byte reproducer where a 3-byte one
   exists, and no way to recover it.

2. THE SET OF KNOWN CRASHES IS FROZEN FOR THE DURATION OF A RUN.
   Hypothesis replays the failing example while shrinking, and raises `Flaky`
   if a test fails once and passes on replay. Adding a signature to the "known"
   set the moment it is seen would do exactly that: the first run raises, the
   replay finds it already known and passes. So the set is captured at entry
   and only updated by the caller, between runs.

3. STATISTICS STOP AT THE FIRST CRASH.
   Shrinking re-executes the harness many times, and those runs are not samples
   from the strategy's distribution. Counting them would corrupt the acceptance
   rate -- the very signal the refinement loop steers on -- by flooding it with
   near-duplicates of one crashing input.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import errors as hyp_errors
from hypothesis import reject
from hypothesis import strategies as st

from fuzzer.runner import DEFAULT_TIMEOUT_S, Outcome, RunResult, run_once
from fuzzer.triage import CrashSignature, signature_for

# Assignment constraints.
MAX_EXAMPLES = 500
WALL_CLOCK_CAP_S = 600.0


def to_bytes(payload: str | bytes) -> bytes:
    """Serialize a generated example for the harness.

    surrogatepass rather than plain utf-8: Hypothesis's text strategies can
    produce lone surrogates, which real encoders refuse. Dropping or replacing
    them would silently mutate the input, so the reproducer on disk would no
    longer be what the generator produced. Passing them through as WTF-8 keeps
    the bytes faithful -- and a parser's handling of malformed UTF-8 is exactly
    the kind of thing worth fuzzing.
    """
    if isinstance(payload, bytes):
        return payload
    return payload.encode("utf-8", errors="surrogatepass")


class CrashFound(Exception):
    """Raised inside the test so Hypothesis shrinks the input that caused it."""

    def __init__(self, signature: CrashSignature, payload: bytes, result: RunResult):
        super().__init__(f"crash {signature.digest}: {signature.error_type}")
        self.signature = signature
        self.payload = payload
        self.result = result


@dataclass
class CampaignStats:
    """Per-run counts. Everything the refinement loop reasons about starts here."""

    accepted: int = 0
    rejected: int = 0
    crashed: int = 0
    harness_errors: int = 0
    rejection_messages: Counter = field(default_factory=Counter)
    input_lengths: list[int] = field(default_factory=list)
    nesting_depths: list[int] = field(default_factory=list)
    wall_clock_s: float = 0.0
    stopped_early: bool = False

    @property
    def examples_run(self) -> int:
        return self.accepted + self.rejected + self.crashed + self.harness_errors

    @property
    def acceptance_rate(self) -> float:
        """Share of inputs the parser accepted.

        The headline number. Near 0 means the generator is being turned away at
        the front door and is testing the error path only; near 1 means it may
        have drifted so conservative that it never probes edges. Neither
        extreme is where bugs live.
        """
        judged = self.accepted + self.rejected
        return self.accepted / judged if judged else 0.0

    def record(self, result: RunResult, payload: bytes) -> None:
        if result.outcome is Outcome.ACCEPTED:
            self.accepted += 1
        elif result.outcome is Outcome.REJECTED:
            self.rejected += 1
            first_line = result.stderr.strip().splitlines()[:1]
            if first_line:
                self.rejection_messages[_normalize_message(first_line[0])] += 1
        elif result.outcome is Outcome.CRASHED:
            self.crashed += 1
        else:
            self.harness_errors += 1

        self.input_lengths.append(len(payload))
        self.nesting_depths.append(_bracket_depth(payload))


def _normalize_message(line: str) -> str:
    """Collapse a parser error to its shape, so the counter groups by cause.

    Without this the summary handed to the LLM is hundreds of unique strings,
    which says nothing about *what* is being rejected. Measured against real
    mxml output, the naive version (digits and hex only) left one cause --
    "XML does not start with '<'" -- split across 60+ rows, because the
    offending character is quoted in the message and every input has a
    different one. The dominant rejection reason looked like noise.

    So quoted runs and angle-bracketed names are collapsed too. This
    deliberately merges "Mismatched close tag </b>" with "</z>", and every
    unknown entity into one row, which is what the loop wants: it needs to
    know *which rule* it keeps violating, not which identifier it used.
    """
    import re

    line = re.sub(r"0x[0-9a-fA-F]+", "ADDR", line)   # before digit rule
    line = re.sub(r"'[^']*'", "'S'", line)           # 'x', '<', '&foo;'
    line = re.sub(r"<[^<>]*>", "<T>", line)          # </b>, <a>
    line = re.sub(r"\b\d+\b", "N", line)
    return " ".join(line.split())[:120]


def _bracket_depth(payload: bytes) -> int:
    """Max nesting depth by bracket balance -- a format-agnostic proxy.

    A stand-in for real grammar-production coverage, which needs the grammar
    and therefore the assigned library. It is crude: it ignores brackets inside
    string literals, so a quoted "[[[" inflates it. Good enough to answer the
    question the loop actually asks -- "is this generator producing flat things
    or nested things?" -- and cheap enough to run on every input.
    """
    depth = max_depth = 0
    for byte in payload:
        if byte in b"[{(":
            depth += 1
            max_depth = max(max_depth, depth)
        elif byte in b"]})":
            depth = max(0, depth - 1)
    return max_depth


@dataclass
class CampaignResult:
    stats: CampaignStats
    crash: CrashFound | None  # the shrunk crash, if a new one was found


def run_campaign(
    strategy: st.SearchStrategy,
    harness: str | Path,
    known_digests: frozenset[str] = frozenset(),
    max_examples: int = MAX_EXAMPLES,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    wall_clock_cap_s: float = WALL_CLOCK_CAP_S,
) -> CampaignResult:
    """Execute `strategy` against `harness`, stopping at the first NEW crash.

    Returns the shrunk crash if one was found. Crashes whose signature is
    already in `known_digests` are counted but do not stop the run -- that is
    what lets a second campaign find a second bug instead of rediscovering the
    first one forever.
    """
    stats = CampaignStats()
    started = time.perf_counter()
    state: dict[str, object] = {"crash": None, "recording": True}

    @given(payload=strategy)
    @settings(
        max_examples=max_examples,
        # Each example spawns a process under ASan; per-example deadlines are
        # meaningless here and would fail runs for being slow rather than wrong.
        # The real bound is the per-input timeout in run_once.
        deadline=None,
        suppress_health_check=[
            HealthCheck.too_slow,
            HealthCheck.data_too_large,
            HealthCheck.filter_too_much,
        ],
    )
    def check(payload: str | bytes) -> None:
        if time.perf_counter() - started > wall_clock_cap_s:
            # Constraint 3 in the assignment: a backstop for a strategy that
            # has gone pathological. reject() marks the example invalid rather
            # than failed, so it cannot be mistaken for a finding.
            stats.stopped_early = True
            reject()

        data = to_bytes(payload)
        result = run_once(harness, data, timeout_s=timeout_s)

        if state["recording"]:
            stats.record(result, data)

        if result.crashed:
            signature = signature_for(result)
            if signature.digest not in known_digests:
                state["recording"] = False  # see docstring note 3
                raise CrashFound(signature, data, result)

    try:
        check()
    except CrashFound as exc:
        # Hypothesis re-raises the exception from the SHRUNK example, so
        # exc.payload is the minimal input it could still reproduce with.
        state["crash"] = exc
    except hyp_errors.Unsatisfiable:
        # Every example was rejected -- in practice, the wall-clock backstop.
        stats.stopped_early = True
    except hyp_errors.Flaky as exc:
        # A crash that did not reproduce on replay. Worth surfacing loudly
        # rather than swallowing: it means the harness or library is
        # nondeterministic, which undermines every reproducer in the report.
        raise RuntimeError(
            "Harness behaved nondeterministically during shrinking; "
            "reproducers cannot be trusted until this is understood."
        ) from exc

    stats.wall_clock_s = time.perf_counter() - started
    return CampaignResult(stats=stats, crash=state["crash"])
