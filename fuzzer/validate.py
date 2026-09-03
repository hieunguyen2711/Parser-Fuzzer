"""Step 4.2: check a generated strategy before spending a run on it.

The assignment is blunt about the failure mode: "A generator that's rejected
99% of the time by the parser's front door isn't testing anything
interesting." Running 500 examples to discover that wastes an iteration of a
five-iteration budget, so this samples a few dozen first and refuses cheaply.

It checks three different ways of being useless, because acceptance rate alone
catches only one:

  TOO REJECTED   -- the parser refuses nearly everything, so only the error
                    path is under test. Against mxml, whose first check is
                    "does this start with '<'", a generator emitting free text
                    scores ~0% and never reaches the tree builder at all.

  TOO REPETITIVE -- the strategy emits the same handful of documents. This is
                    what over-optimizing for acceptance produces: a generator
                    that has learned to emit `<a/>` forever scores 100%
                    acceptance and tests nothing. Acceptance rate cannot see
                    it; counting distinct outputs can.

  DEGENERATE     -- everything is empty or near-empty. Cheap to produce,
                    passes some checks, exercises nothing.

  EXHAUSTED      -- the strategy has so few possible outputs that Hypothesis
                    runs out before the sample is full. This one hides from
                    the distinctness check, because that check is a fraction
                    of the sample and the sample itself shrank: a generator
                    with exactly three possible documents scores 3 distinct
                    out of 3 and looks perfect. Comparing what was drawn
                    against what was asked for is what catches it.

A crash during validation is NOT a failure. It is the point of the exercise,
and it is reported upward rather than swallowed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from hypothesis import strategies as st

from fuzzer.examples import draw_examples
from fuzzer.runner import Outcome, run_once

SAMPLE_SIZE = 30
MIN_ACCEPTANCE = 0.05
MIN_DISTINCT_FRACTION = 0.5
MIN_MEAN_BYTES = 3.0


@dataclass
class Validation:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    accepted: int = 0
    rejected: int = 0
    crashed: int = 0
    harness_errors: int = 0
    distinct: int = 0
    sampled: int = 0
    requested: int = 0
    mean_bytes: float = 0.0
    sample_rejections: list[str] = field(default_factory=list)
    crashing_input: bytes | None = None

    @property
    def acceptance_rate(self) -> float:
        judged = self.accepted + self.rejected
        return self.accepted / judged if judged else 0.0

    def brief(self) -> str:
        """Compact enough to paste into a refine prompt."""
        lines = [
            f"GENERATOR CHECK: {'PASS' if self.passed else 'FAIL'}",
            f"sampled {self.sampled}"
            + (f" (of {self.requested} asked for -- space exhausted)"
               if self.requested and self.sampled < self.requested else "")
            + f": {self.accepted} accepted, "
            f"{self.rejected} rejected, {self.crashed} crashed",
            f"acceptance {self.acceptance_rate:.0%}, "
            f"{self.distinct} distinct outputs, mean {self.mean_bytes:.0f} bytes",
        ]
        for reason in self.reasons:
            lines.append(f"  problem: {reason}")
        if self.sample_rejections:
            lines.append("  parser said:")
            lines.extend(f"    {m}" for m in self.sample_rejections[:3])
        return "\n".join(lines)


def validate_strategy(
    strategy: st.SearchStrategy,
    harness: str | Path,
    sample_size: int = SAMPLE_SIZE,
    min_acceptance: float = MIN_ACCEPTANCE,
    seed: int | None = 0,
) -> Validation:
    """Sample the strategy and judge whether it is worth a full campaign."""
    try:
        examples = draw_examples(strategy, sample_size, seed)
    except Exception as exc:  # a strategy that cannot even draw
        return Validation(
            passed=False,
            reasons=[f"strategy raised while generating: {type(exc).__name__}: {exc}"],
        )

    payloads = [
        e if isinstance(e, bytes) else str(e).encode("utf-8", errors="surrogatepass")
        for e in examples
    ]
    if not payloads:
        return Validation(passed=False, reasons=["strategy produced no examples"])

    result = Validation(passed=True, sampled=len(payloads))
    result.requested = sample_size
    result.distinct = len(set(payloads))
    result.mean_bytes = sum(len(p) for p in payloads) / len(payloads)

    seen_messages: list[str] = []
    for payload in payloads:
        run = run_once(harness, payload)
        if run.outcome is Outcome.ACCEPTED:
            result.accepted += 1
        elif run.outcome is Outcome.REJECTED:
            result.rejected += 1
            first = run.stderr.strip().splitlines()[:1]
            if first and first[0] not in seen_messages:
                seen_messages.append(first[0])
        elif run.outcome is Outcome.CRASHED:
            result.crashed += 1
            if result.crashing_input is None:
                result.crashing_input = payload
        else:
            result.harness_errors += 1

    result.sample_rejections = seen_messages[:5]

    # A crash is a finding, not a validation failure -- do not fail the gate
    # on it, or the loop would discard the very generator that just worked.
    if result.acceptance_rate < min_acceptance and result.crashed == 0:
        result.reasons.append(
            f"only {result.acceptance_rate:.0%} of documents were accepted "
            f"(need {min_acceptance:.0%}); the parser is refusing them at the "
            f"front door, so the generator is testing the error path only"
        )
    if result.sampled < sample_size:
        result.reasons.append(
            f"Hypothesis exhausted the strategy after {result.sampled} of "
            f"{sample_size} examples; the generator can only produce a handful "
            f"of distinct documents, so it covers almost none of the grammar"
        )
    if result.distinct < max(2, int(len(payloads) * MIN_DISTINCT_FRACTION)):
        result.reasons.append(
            f"only {result.distinct} distinct documents out of {len(payloads)}; "
            f"the generator is repeating itself and covers little of the grammar"
        )
    if result.mean_bytes < MIN_MEAN_BYTES:
        result.reasons.append(
            f"documents average {result.mean_bytes:.1f} bytes; near-empty output "
            f"exercises almost nothing"
        )
    if result.harness_errors:
        result.reasons.append(
            f"{result.harness_errors} harness errors -- the build or wiring is "
            f"broken, not the strategy"
        )

    result.passed = not result.reasons
    return result
