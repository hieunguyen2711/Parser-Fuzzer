"""Look at XML documents and what mxml makes of them.

    # the hand-written corpus, with mxml's verdict on each
    python -m fuzzer.inspect corpus

    # what a strategy actually generates, and how mxml judges it
    python -m fuzzer.inspect strategy fuzzer.strategies.baseline:BASELINE -n 12

    # one document straight from the command line
    python -m fuzzer.inspect one '<a x="1"><b/></a>'

Written for reading with your own eyes. The campaign reports aggregates --
acceptance rate, grouped rejection reasons -- which tell you *that* a
generator is being refused but never show you what it emitted. When a
generated strategy is doing something strange, this is where you find out
what.
"""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path

from fuzzer.examples import draw_examples
from fuzzer.runner import Outcome, run_once

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HARNESS = ROOT / "build" / "harness"

VERDICT = {
    Outcome.ACCEPTED: "ACCEPT",
    Outcome.REJECTED: "reject",
    Outcome.CRASHED: "CRASH!",
    Outcome.HARNESS_ERROR: "harness-error",
}


def show(label: str, data: bytes, harness, width: int) -> Outcome:
    """Print one document, what it looks like, and mxml's verdict."""
    result = run_once(harness, data)

    # Escape non-printables so a control character or a lone byte cannot
    # scramble the terminal -- and so you can see that it is there at all.
    body = data.decode("utf-8", errors="replace")
    printable = "".join(
        c if (c.isprintable() or c == " ") else f"\\x{ord(c):02x}" for c in body
    )
    if len(printable) > width:
        printable = printable[: width - 1] + "…"

    note = ""
    if result.outcome is Outcome.REJECTED:
        note = result.stderr.strip().removeprefix("reject:").strip()
    elif result.stderr.startswith("diag:"):
        note = "accepted, but: " + result.stderr.strip().removeprefix("diag:").strip()
    elif result.outcome is Outcome.CRASHED:
        note = "*** see crashes/ ***"

    print(f"  {label:<22} {VERDICT[result.outcome]:>6}  {printable}")
    if note:
        print(f"  {'':22} {'':6}  ↳ {note[:width]}")
    return result.outcome


def load_strategy(spec: str):
    """Load `package.module:ATTRIBUTE`."""
    if ":" not in spec:
        raise SystemExit(f"strategy must look like 'module:NAME', got {spec!r}")
    module_name, attr = spec.split(":", 1)
    return getattr(importlib.import_module(module_name), attr)


def summarize(outcomes: list[Outcome]) -> None:
    total = len(outcomes)
    if not total:
        return
    accepted = sum(o is Outcome.ACCEPTED for o in outcomes)
    rejected = sum(o is Outcome.REJECTED for o in outcomes)
    crashed = sum(o is Outcome.CRASHED for o in outcomes)
    print(
        f"\n  {total} documents: {accepted} accepted, {rejected} rejected, "
        f"{crashed} crashed  ({accepted / total:.0%} acceptance)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("mode", choices=["corpus", "strategy", "one"])
    parser.add_argument("value", nargs="?", help="strategy spec, or the document")
    parser.add_argument("-n", "--count", type=int, default=10)
    parser.add_argument("--harness", default=str(DEFAULT_HARNESS))
    parser.add_argument("--width", type=int, default=68)
    parser.add_argument("--seed", type=int, default=0, help="0 for reproducible draws")
    args = parser.parse_args()

    if not Path(args.harness).exists():
        raise SystemExit(f"no harness at {args.harness} -- run `make` first")

    outcomes: list[Outcome] = []

    if args.mode == "corpus":
        for group in ("valid", "invalid"):
            files = sorted((ROOT / "corpus" / group).glob("*.xml"))
            print(f"\ncorpus/{group}/  ({len(files)} documents)")
            for path in files:
                outcomes.append(
                    show(path.name, path.read_bytes(), args.harness, args.width)
                )

    elif args.mode == "strategy":
        if not args.value:
            raise SystemExit("give a strategy, e.g. fuzzer.strategies.baseline:BASELINE")
        strategy = load_strategy(args.value)
        print(f"\n{args.value}  ({args.count} draws, seed={args.seed})")
        for i, example in enumerate(draw_examples(strategy, args.count, args.seed)):
            data = example if isinstance(example, bytes) else str(example).encode(
                "utf-8", errors="surrogatepass"
            )
            outcomes.append(show(f"#{i:<3} ({len(data)}B)", data, args.harness, args.width))

    else:  # one
        if args.value is None:
            raise SystemExit("give a document to check")
        print()
        outcomes.append(
            show("input", args.value.encode("utf-8", "surrogatepass"), args.harness, args.width)
        )

    summarize(outcomes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
