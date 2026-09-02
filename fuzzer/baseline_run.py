"""Step 3: run the naive strategy end to end and print what came back.

    python -m fuzzer.baseline_run [--harness build/harness-mock] [-n 500]

Purpose is plumbing validation, not bug-finding. A successful run here means:
inputs were generated, serialized, executed, classified, and summarized, and
the numbers are consistent with what the target under test should do.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from fuzzer.campaign import run_campaign
from fuzzer.strategies.baseline import BASELINE
from fuzzer.summary import summarize
from fuzzer.triage import Finding, save_finding


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", default="build/harness")
    parser.add_argument("-n", "--examples", type=int, default=500)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--crashes-dir", default="crashes")
    args = parser.parse_args()

    result = run_campaign(
        BASELINE,
        args.harness,
        max_examples=args.examples,
        timeout_s=args.timeout,
    )

    findings: dict[str, Finding] = {}
    if result.crash is not None:
        crash = result.crash
        finding = Finding(
            signature=crash.signature,
            minimized_input=crash.payload,
            report=crash.result.stderr,
            signal=crash.result.signal,
            timed_out=crash.result.timed_out,
            first_seen_iteration=0,
        )
        findings[crash.signature.digest] = finding
        saved = save_finding(finding, Path(args.crashes_dir))
        print(f"saved reproducer -> {saved}\n")

    print(summarize(result.stats, findings, iteration=0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
