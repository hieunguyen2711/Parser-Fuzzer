"""Run one generated input through the C harness and classify what happened.

This is the only module that knows how to talk to the harness process. Every
other part of the fuzzer works in terms of the `Outcome` enum below, so if the
harness contract changes, it changes here and nowhere else.

The classification is the whole point. The assignment is explicit that a
parser rejecting malformed input is correct behavior, not a bug -- so the
expensive mistake is a runner that reports rejections as findings, or that
misses a real crash because it only looked at the exit code.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# Assignment constraint: 5 seconds per input, to bound hangs.
DEFAULT_TIMEOUT_S = 5.0

# If any of these appear in stderr, a sanitizer spoke, regardless of exit code.
# This is a backstop: the harness is built with -fno-sanitize-recover=undefined
# so UBSan aborts rather than printing and continuing. But if that flag is ever
# dropped, UBSan reports a real bug and still exits 0 -- and a runner watching
# only the exit code would score it as a clean parse. Cheap insurance.
SANITIZER_MARKERS = (
    "AddressSanitizer",
    "UndefinedBehaviorSanitizer",
    "LeakSanitizer",
    "runtime error:",
)


class Outcome(Enum):
    """What one run of the harness did.

    ACCEPTED and REJECTED are both *correct* parser behavior. They are kept
    apart because their ratio is the acceptance rate the refinement loop
    steers on -- a generator that is rejected 99% of the time never reaches
    the code worth testing, and collapsing these would hide that.
    """

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CRASHED = "crashed"
    HARNESS_ERROR = "harness_error"


@dataclass(frozen=True)
class RunResult:
    outcome: Outcome
    exit_code: int | None      # None if killed by a signal or timed out
    signal: int | None         # e.g. 6 for SIGABRT; None otherwise
    stderr: str
    duration_s: float
    timed_out: bool

    @property
    def crashed(self) -> bool:
        return self.outcome is Outcome.CRASHED


def sanitizer_env() -> dict[str, str]:
    """Environment that makes sanitizer behavior deterministic and legible.

    detect_leaks=0 is not a preference, it is required on macOS: Darwin has no
    LeakSanitizer, and setting detect_leaks=1 makes ASan hard-fail with SIGABRT
    on *every* input -- which this runner would faithfully report as a crash on
    all 500 examples. On Linux you would want this at 1.
    """
    env = dict(os.environ)
    env["ASAN_OPTIONS"] = ":".join([
        "abort_on_error=1",    # die on SIGABRT so the signal is unambiguous
        "halt_on_error=1",     # stop at the first fault, do not keep going
        "detect_leaks=0",      # see docstring -- do not set to 1 on macOS
        "symbolize=1",         # function names in traces; dedup depends on it
    ])
    env["UBSAN_OPTIONS"] = ":".join([
        "halt_on_error=1",
        "print_stacktrace=1",  # without this a UB report has no frames to hash
        "symbolize=1",
    ])
    return env


def _decode(raw: bytes | None) -> str:
    # Sanitizer output is ASCII, but the parser may echo fragments of a
    # deliberately malformed input, so never assume valid UTF-8.
    return raw.decode("utf-8", errors="replace") if raw else ""


def run_once(
    harness: str | Path,
    data: bytes,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> RunResult:
    """Feed `data` to the harness on stdin and classify the result."""
    started = time.perf_counter()

    try:
        proc = subprocess.run(
            [str(harness)],
            input=data,
            capture_output=True,
            timeout=timeout_s,
            env=sanitizer_env(),
        )
    except subprocess.TimeoutExpired as exc:
        # The assignment counts timeouts as crashes: a parser that hangs on an
        # input is a denial-of-service bug and goes through the same triage as
        # a sanitizer abort. Note stderr is usually None here -- Python does
        # not always hand back partial output from a killed child -- so a
        # timeout has no stack to dedup on. triage.py handles that.
        return RunResult(
            outcome=Outcome.CRASHED,
            exit_code=None,
            signal=None,
            stderr=_decode(exc.stderr),
            duration_s=time.perf_counter() - started,
            timed_out=True,
        )

    duration = time.perf_counter() - started
    stderr = _decode(proc.stderr)
    rc = proc.returncode

    # Order matters below: check for a crash before trusting any exit code.
    #
    # The subtlety worth internalizing: when a child dies from a signal,
    # Python reports returncode as NEGATIVE (-6 for SIGABRT). The 134 you see
    # in a shell is the shell's own 128+signal convention, and it never
    # reaches you here. Comparing `rc == 134` would silently miss every crash.
    if rc < 0:
        return RunResult(Outcome.CRASHED, None, -rc, stderr, duration, False)

    if any(marker in stderr for marker in SANITIZER_MARKERS):
        return RunResult(Outcome.CRASHED, rc, None, stderr, duration, False)

    if rc == 0:
        return RunResult(Outcome.ACCEPTED, rc, None, stderr, duration, False)

    if rc == 2:
        return RunResult(Outcome.REJECTED, rc, None, stderr, duration, False)

    # rc == 1 is the harness reporting its own failure (bad path, OOM). Any
    # other code is unexpected -- possibly the library calling exit() itself.
    # Neither is a fatal signal nor a sanitizer report, so neither is a crash
    # by the assignment's definition. Surface them rather than miscounting
    # them as findings; a run that produces many of these is misconfigured.
    return RunResult(Outcome.HARNESS_ERROR, rc, None, stderr, duration, False)
