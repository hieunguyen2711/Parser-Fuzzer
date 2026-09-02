"""Compress a run into the briefing the LLM sees between iterations.

This module is the feedback loop's bandwidth limit, and the reason it needs
care: the assignment caps total spend at ~$5 across 5 iterations, and the
strategy code plus the summary must fit in every refine prompt. Dumping 500
raw results would blow the budget and, worse, bury the three numbers that
actually should drive a revision.

What gets included, and why each earns its tokens:

  ACCEPTANCE RATE -- the primary steering signal. With no coverage
    instrumentation available, this is the closest available proxy for "is the
    generator reaching the parser's interesting code at all?". A generator at
    2% acceptance is testing the error path 49 times out of 50.

  REJECTION REASONS, grouped -- tells the LLM *why* it is being turned away,
    which is far more actionable than the bare rate. "expected ':' at line N"
    appearing 300 times says the generator is emitting malformed objects, and
    points at the exact production to fix.

  STRUCTURAL DIVERSITY -- nesting depth and input size distributions. Guards
    against the failure mode where acceptance rate is optimized into a
    generator that emits `{}` forever: 100% accepted, nothing tested. The two
    signals have to be read together, which is why they travel together.

  KNOWN CRASHES -- so the LLM stops re-deriving inputs for bugs already found
    and spends its remaining budget elsewhere.
"""

from __future__ import annotations

from collections import Counter

from fuzzer.campaign import CampaignStats
from fuzzer.triage import Finding

TOP_REJECTIONS = 6
DEPTH_BUCKETS = ((0, 0), (1, 1), (2, 3), (4, 7), (8, 15), (16, 10**9))


def _percentiles(values: list[int]) -> str:
    if not values:
        return "n/a"
    ordered = sorted(values)

    def at(fraction: float) -> int:
        index = min(len(ordered) - 1, int(fraction * len(ordered)))
        return ordered[index]

    return f"min={ordered[0]} p50={at(0.5)} p90={at(0.9)} max={ordered[-1]}"


def _depth_histogram(depths: list[int]) -> str:
    if not depths:
        return "n/a"
    counts: Counter[str] = Counter()
    for depth in depths:
        for low, high in DEPTH_BUCKETS:
            if low <= depth <= high:
                label = f"{low}" if low == high else f"{low}-{high if high < 10**9 else '+'}"
                counts[label] += 1
                break
    total = len(depths)
    parts = [
        f"{label}:{count * 100 // total}%"
        for label, count in sorted(counts.items(), key=lambda kv: kv[0])
    ]
    return " ".join(parts)


def summarize(
    stats: CampaignStats,
    findings: dict[str, Finding],
    iteration: int,
) -> str:
    """Render the compact briefing. Kept well under ~400 tokens on purpose."""
    lines = [
        f"ITERATION {iteration} RESULTS",
        f"examples run      : {stats.examples_run}"
        + (" (stopped early: wall-clock cap)" if stats.stopped_early else ""),
        f"accepted          : {stats.accepted}",
        f"rejected          : {stats.rejected}",
        f"crashed           : {stats.crashed}",
        f"harness errors    : {stats.harness_errors}",
        f"ACCEPTANCE RATE   : {stats.acceptance_rate:.1%}",
        f"wall clock        : {stats.wall_clock_s:.1f}s",
        "",
        f"input size (bytes): {_percentiles(stats.input_lengths)}",
        f"nesting depth     : {_depth_histogram(stats.nesting_depths)}",
    ]

    if stats.harness_errors:
        lines += [
            "",
            "WARNING: harness errors occurred. These are not parser bugs -- the "
            "harness itself failed. Results this iteration may be unreliable.",
        ]

    if stats.rejection_messages:
        lines += ["", "TOP REJECTION REASONS (grouped, digits normalized to N):"]
        for message, count in stats.rejection_messages.most_common(TOP_REJECTIONS):
            share = count * 100 // max(1, stats.rejected)
            lines.append(f"  {count:5d} ({share:2d}%)  {message}")
        distinct = len(stats.rejection_messages)
        if distinct > TOP_REJECTIONS:
            lines.append(f"  ... and {distinct - TOP_REJECTIONS} other distinct reasons")

    lines += ["", f"UNIQUE CRASHES SO FAR: {len(findings)}"]
    for finding in findings.values():
        where = " <- ".join(finding.signature.frames) or "no frames"
        lines.append(
            f"  [{finding.signature.digest}] {finding.signature.error_type} "
            f"in {where}; first seen iter {finding.first_seen_iteration}; "
            f"minimal input ({len(finding.minimized_input)}B): "
            f"{finding.minimized_input[:60]!r}"
        )
    if not findings:
        lines.append("  (none yet)")

    return "\n".join(lines)
