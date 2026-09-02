"""Turn a sanitizer report into a stable crash signature, and save findings.

Deduplication is the step that decides whether you report one bug or five, so
the normalization choices here are a documented deliverable, not an
implementation detail. Two competing failure modes:

  Over-normalizing merges distinct bugs into one signature, and you under-report.
  Under-normalizing splits one bug across many signatures -- every distinct
  input address becomes its own "finding" -- and you drown in duplicates.

The choices made here, all reversible in one place:

  1. Hash the *crash* stack only, never the allocation stack. An ASan report
     for a heap overflow also prints "allocated by thread T0 here:" with a
     second stack. Two different overflow bugs can share an allocation site,
     and one bug can be reached from many allocation sites, so folding it in
     splits more than it joins.

  2. Keep function names, drop addresses, line numbers, and byte offsets.
     Addresses vary per run under ASLR. Line numbers shift with any edit to the
     library, which would silently reclassify every known crash as new.

  3. Drop sanitizer-runtime frames. A trace often begins inside an interceptor
     (`__asan_memcpy`, ASan's `malloc`), which is identical across unrelated
     bugs and would merge them if kept.

  4. Top 3 remaining frames. Deep enough to separate distinct call paths,
     shallow enough that a bug reached via two routes still dedups to one.

  5. Timeouts all collapse to a single signature. A killed process leaves no
     stack, so there is nothing to distinguish two different hangs by. This is
     a known limitation, flagged rather than hidden: if a run produces several
     timeouts, they need reading by hand before being called one bug.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from fuzzer.runner import RunResult

FRAME_COUNT = 3

# "    #0 0x000104... in fuzz_one_input mock_target.c:37"
# "    #2 0x000190... in start+0x17b8 (dyld:arm64e+0xfff...)"
_FRAME_RE = re.compile(r"^\s*#(\d+)\s+0x[0-9a-f]+\s+in\s+(.+)$")

# Everything from here on describes the allocation, not the crash (choice 1).
_STACK_END_MARKERS = (
    "allocated by thread",
    "freed by thread",
    "previously allocated by",
    "Shadow bytes around",
)

# Frames belonging to the sanitizer runtime or the dynamic loader (choice 3).
_NOISE_FRAME_RE = re.compile(
    r"libclang_rt|^__asan|^__ubsan|^__sanitizer|^wrap_|\(dyld|^start\+|^start$"
)

_ASAN_ERROR_RE = re.compile(r"ERROR:\s+AddressSanitizer:\s+(\S+)")
_UBSAN_SUMMARY_RE = re.compile(r"SUMMARY:\s+UndefinedBehaviorSanitizer:\s+(\S+)")
_RUNTIME_ERROR_RE = re.compile(r"runtime error:\s+(.+)")


@dataclass(frozen=True)
class CrashSignature:
    digest: str                       # short stable id, used as the dict key
    error_type: str                   # e.g. "heap-buffer-overflow"
    frames: tuple[str, ...]           # normalized top frames

    def describe(self) -> str:
        where = " <- ".join(self.frames) if self.frames else "(no frames)"
        return f"{self.digest}  {self.error_type}  [{where}]"


def _normalize_frame(raw: str) -> str | None:
    """`fuzz_one_input mock_target.c:37` -> `fuzz_one_input`, or None if noise."""
    if _NOISE_FRAME_RE.search(raw):
        return None
    # Split off the trailing "file.c:line" or "(module+0xoff)" (choice 2).
    func = raw.split(" ")[0]
    func = func.split("+0x")[0]        # strip byte offset
    return func or None


def _crash_stack(report: str) -> list[str]:
    frames: list[str] = []
    for line in report.splitlines():
        if any(marker in line for marker in _STACK_END_MARKERS):
            break                      # choice 1: stop at the allocation stack
        match = _FRAME_RE.match(line)
        if match:
            norm = _normalize_frame(match.group(2).strip())
            if norm:
                frames.append(norm)
    return frames


def _error_type(report: str) -> str:
    """Most specific description available, in that order.

    The ordering matters more than it looks. UBSan prints a precise
    "runtime error: signed integer overflow" line AND a generic
    "SUMMARY: UndefinedBehaviorSanitizer: undefined-behavior". Preferring the
    summary collapses every arithmetic, alignment, and shift bug in the library
    into one signature called "undefined-behavior" -- a dedup that reports five
    distinct bugs as one. Take the specific line; keep the summary as a
    fallback for reports that lack one.
    """
    if (m := _ASAN_ERROR_RE.search(report)):
        return m.group(1)
    if (m := _RUNTIME_ERROR_RE.search(report)):
        # Strip concrete values so "2147483647 + 1 cannot be represented" and
        # "5 + 3 cannot be represented" hash to the same bug.
        kind = m.group(1).split(":")[0]
        kind = re.sub(r"0x[0-9a-fA-F]+", "ADDR", kind)
        kind = re.sub(r"\b\d+\b", "N", kind)
        return " ".join(kind.split())[:80]
    if (m := _UBSAN_SUMMARY_RE.search(report)):
        return m.group(1)
    return "unknown"


def signature_for(result: RunResult) -> CrashSignature:
    """Compute the dedup key for a crashed run."""
    if result.timed_out:
        # choice 5: no stack exists, so every hang looks alike from here.
        return CrashSignature("timeout", "timeout", ())

    error_type = _error_type(result.stderr)
    frames = tuple(_crash_stack(result.stderr)[:FRAME_COUNT])
    key = "|".join((error_type,) + frames)
    digest = hashlib.sha1(key.encode()).hexdigest()[:12]
    return CrashSignature(digest, error_type, frames)


@dataclass
class Finding:
    """One unique crash: its signature, its smallest known input, its report."""
    signature: CrashSignature
    minimized_input: bytes
    report: str
    signal: int | None
    timed_out: bool
    first_seen_iteration: int = 0
    times_seen: int = 1


def save_finding(finding: Finding, crashes_dir: Path) -> Path:
    """Write the reproducer and its report side by side, per Step 5.2."""
    crashes_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{finding.signature.error_type}-{finding.signature.digest}"

    input_path = crashes_dir / f"{stem}.input"
    input_path.write_bytes(finding.minimized_input)

    detail = [
        f"signature : {finding.signature.digest}",
        f"error type: {finding.signature.error_type}",
        f"frames    : {' <- '.join(finding.signature.frames) or '(none)'}",
        f"signal    : {finding.signal if finding.signal is not None else '-'}",
        f"timed out : {finding.timed_out}",
        f"iteration : first seen at {finding.first_seen_iteration}",
        f"times seen: {finding.times_seen}",
        f"input len : {len(finding.minimized_input)} bytes",
        "",
        "--- input (repr) ---",
        repr(finding.minimized_input),
        "",
        "--- sanitizer report ---",
        finding.report or "(none captured -- timeout)",
    ]
    report_path = crashes_dir / f"{stem}.report.txt"
    report_path.write_text("\n".join(detail), encoding="utf-8")
    return input_path
