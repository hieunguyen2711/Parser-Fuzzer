"""Recover the strategy code from a saved iteration log. No API call.

    python -m fuzzer.extract report/iterations/iter-01-seed-20260902-222938.md
    python -m fuzzer.extract <log> --out fuzzer/strategies/pristine.py
    python -m fuzzer.extract <log> --stdout

seed_run.py extracts inline, once, as part of the call. That is fine until you
want the code again: to recover the model's original output after the working
copy has been edited, to re-extract after fixing the fence-parsing rules, or to
pull code out of a reply whose extraction failed the first time. Re-running the
model to get a file back would cost an iteration and would not return the same
text anyway -- the log is the only copy, so there needs to be a way in.

By default this refuses to overwrite. The value of a log is that it holds the
one unedited copy of what the model said.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from fuzzer.strategy_io import ExtractionError, extract_code

RAW_HEADING = "## Raw response"


def raw_response(log_text: str) -> str:
    """Return just the model's reply from an iteration log."""
    if RAW_HEADING not in log_text:
        # Not one of our logs -- treat the whole file as the reply, so this
        # also works on a response pasted into a file by hand.
        return log_text
    return log_text.split(RAW_HEADING, 1)[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log", type=Path, help="an iteration log, or any file holding a reply")
    parser.add_argument("--out", type=Path, default=None,
                        help="where to write (default: alongside the log, .py)")
    parser.add_argument("--stdout", action="store_true", help="print instead of writing")
    parser.add_argument("--force", action="store_true", help="allow overwriting an existing file")
    args = parser.parse_args()

    if not args.log.exists():
        raise SystemExit(f"no such file: {args.log}")

    try:
        code = extract_code(raw_response(args.log.read_text(encoding="utf-8")))
    except ExtractionError as exc:
        raise SystemExit(f"extraction failed: {exc}")

    if args.stdout:
        print(code)
        return 0

    out = args.out or args.log.with_suffix(".py")
    if out.exists() and not args.force:
        raise SystemExit(f"{out} exists; pass --force to overwrite")

    out.write_text(
        f'"""Extracted from {args.log.name}. Unedited model output.\n\n'
        f'Recovered by `python -m fuzzer.extract`; see that log for the prompt\n'
        f'and the reply this came from.\n"""\n\n' + code + "\n",
        encoding="utf-8",
    )
    print(f"{len(code.splitlines())} lines -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
