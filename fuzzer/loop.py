"""Iterations 2-5: refine the seed strategy from what each run reveals.

    python -m fuzzer.loop                    # refine iterations 2..5
    python -m fuzzer.loop --stop 3           # just iteration 2 and 3

This is the refinement loop the seed shot (fuzzer.seed_run) leaves off at. It
is a LINEAR, GREEDY chain -- one refined prompt per iteration, kept if it passes
the cheap gate -- which is the budget-scaled special case of PromptAgent's MCTS
(Wang et al. 2023): width 1, no backtracking. The tree search the paper runs
evaluates 50-150 prompts; a five-iteration / $5 budget affords neither the
branching nor the second "optimizer" model call per step, so the critique the
paper generates with GPT-4 is instead computed for free in Python
(validate.brief() + summarize()) and folded into the one call each iteration
gets.

Framed as the paper's MDP: the STATE is the current strategy, the ACTION is the
feedback injected to steer the next one, and the REWARD is the band-targeted
score in fuzzer.score. The loop never chases 100% acceptance -- see that module
for why that would be a Goodhart failure.

Budget note: the seed used one of the five calls in a separate process, so this
loop runs iterations 2..5 (four calls) and lets Budget.check() stop it early if
a spend cap is ever set. Every iteration -- even one whose strategy fails to
load -- writes report/iterations/iter-0N-refine-*.md, which is the log of how
the strategy evolved.
"""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from fuzzer.campaign import MAX_EXAMPLES, CrashFound, run_campaign
from fuzzer.llm import LLM, BudgetExceeded
from fuzzer.prompts import SYSTEM, refine_prompt
from fuzzer.score import score_campaign
from fuzzer.strategy_io import ExtractionError, extract_code, load_strategy
from fuzzer.summary import summarize
from fuzzer.triage import Finding, save_finding
from fuzzer.validate import validate_strategy

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "report" / "iterations"
STRATEGY_DIR = ROOT / "fuzzer" / "strategies"
CRASHES_DIR = ROOT / "crashes"


@dataclass
class LoopState:
    """What carries between iterations. The md logs and crashes/ are the durable
    record; this is just the working set the next prompt is built from."""

    prev_code: str
    feedback: str
    trajectory: list = field(default_factory=list)  # (iteration, score, acceptance)
    known_digests: frozenset[str] = frozenset()
    findings: dict[str, Finding] = field(default_factory=dict)
    best_iteration: int | None = None
    best_score: float = -1.0
    best_path: Path | None = None
    best_code: str = ""  # code of the best-scoring iteration -- the revert target


def _build_finding(crash: CrashFound, iteration: int) -> Finding:
    """Same shape baseline_run.py builds, tagged with the iteration it appeared."""
    return Finding(
        signature=crash.signature,
        minimized_input=crash.payload,
        report=crash.result.stderr,
        signal=crash.result.signal,
        timed_out=crash.result.timed_out,
        first_seen_iteration=iteration,
    )


def _write_strategy_file(out_path: Path, code: str, iteration: int) -> None:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        f'"""Generated (refine iteration {iteration}) at {stamp}. '
        f'Do not edit by hand."""\n\n' + code + "\n",
        encoding="utf-8",
    )


def _apply_response(
    iteration: int,
    response: str,
    state: LoopState,
    harness: str | Path,
    max_examples: int,
) -> str:
    """Extract -> write -> validate -> (maybe) campaign. Mutate `state`, return
    the markdown Result section for the iteration log.

    Every exit updates state.feedback so the NEXT iteration has something to
    react to -- a failure names itself and becomes the next prompt's critique.
    No exit re-calls the model: a retry would spend another of the few calls.
    """
    try:
        code = extract_code(response)
    except ExtractionError as exc:
        # Unparseable reply: discard it and refine again from the last working
        # module (best_code), so one bad reply cannot cascade into the next.
        state.prev_code = state.best_code
        state.feedback = (
            f"Your previous reply could not be parsed into a module: {exc}. "
            "It was discarded; the module shown to you is the last one that "
            "worked. Return exactly ONE python code block that defines a "
            "module-level STRATEGY."
        )
        return f"## Result\n\nEXTRACTION FAILED: {exc}\n"

    out_path = STRATEGY_DIR / f"generated_iter{iteration}.py"
    _write_strategy_file(out_path, code, iteration)

    try:
        strategy = load_strategy(out_path)
    except ExtractionError as exc:
        # Broken module: revert to the last working one rather than refining
        # from code that does not even import.
        state.prev_code = state.best_code
        state.feedback = (
            f"The module you wrote does not import or run: {exc}. "
            "It was discarded; the module shown to you is the last one that "
            "loaded and passed the gate. Reapply your intended change to THAT "
            "module and fix the specific error above."
        )
        return f"## Result\n\nLOAD FAILED: {exc}\n"

    validation = validate_strategy(strategy, harness)
    if not validation.passed:
        # The gate refused it: do not spend a 500-example campaign, and do not
        # let a refused strategy become the base for the next one -- revert to
        # the last working module. brief() still names the failure as critique.
        state.prev_code = state.best_code
        state.feedback = (
            validation.brief()
            + "\n\n(This strategy was refused and discarded. The module shown "
            "to you is the last one that passed the gate -- revise THAT.)"
        )
        return (
            "## Result\n\nGATE REFUSED -- campaign skipped\n\n"
            f"```\n{validation.brief()}\n```\n"
        )

    campaign = run_campaign(
        strategy, harness, known_digests=state.known_digests, max_examples=max_examples
    )
    if campaign.crash is not None:
        finding = _build_finding(campaign.crash, iteration)
        state.findings[finding.signature.digest] = finding
        save_finding(finding, CRASHES_DIR)
        state.known_digests = state.known_digests | {finding.signature.digest}

    summary = summarize(campaign.stats, state.findings, iteration=iteration)
    score = score_campaign(campaign.stats)

    state.prev_code = code  # this one loaded and passed -- carry it forward
    state.feedback = validation.brief() + "\n\n" + summary
    state.trajectory.append((iteration, score.value, campaign.stats.acceptance_rate))
    if score.value > state.best_score:
        state.best_score = score.value
        state.best_iteration = iteration
        state.best_path = out_path
        state.best_code = code

    return f"## Result\n\n{score.brief()}\n\n```\n{summary}\n```\n"


def run_iteration(
    llm: LLM,
    iteration: int,
    state: LoopState,
    harness: str | Path,
    temperature: float,
    max_examples: int,
) -> Path:
    """One refinement step: build the prompt, spend one call, act on the reply,
    and write the iteration log. Returns the log path."""
    prompt = refine_prompt(state.prev_code, state.feedback, state.trajectory)
    response, usage = llm.generate(prompt, system=SYSTEM, temperature=temperature)
    llm.budget.iterations_used += 1  # the increment lives in the caller (see seed_run)

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    header = (
        f"# Iteration {iteration} (refine)\n\n"
        f"- when: {stamp}\n- model: {llm.model}\n"
        f"- temperature: {temperature}\n"
        f"- tokens: in {usage.input_tokens}, out {usage.output_tokens}\n\n"
        f"## System\n\n```\n{SYSTEM}\n```\n\n"
        f"## Feedback fed in\n\n```\n{state.feedback}\n```\n\n"
        f"## Prompt\n\n```\n{prompt}\n```\n\n"
        f"## Raw response\n\n{response}\n\n"
    )

    result_section = _apply_response(iteration, response, state, harness, max_examples)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"iter-{iteration:02d}-refine-{stamp}.md"
    log_path.write_text(header + result_section, encoding="utf-8")
    return log_path


def run_loop(
    llm: LLM,
    state: LoopState,
    harness: str | Path,
    *,
    start: int = 2,
    stop: int = 5,
    temperature: float = 1.0,
    max_examples: int = MAX_EXAMPLES,
) -> LoopState:
    """Drive iterations `start`..`stop`, stopping cleanly if the budget runs out."""
    for iteration in range(start, stop + 1):
        try:
            log_path = run_iteration(
                llm, iteration, state, harness, temperature, max_examples
            )
        except BudgetExceeded as exc:
            print(f"budget: {exc} -- stopping before iteration {iteration}")
            break
        rel = log_path.relative_to(ROOT) if log_path.is_relative_to(ROOT) else log_path
        # A failed iteration (extract/load/gate) appends no trajectory point, so
        # only report a score if THIS iteration produced one -- otherwise the
        # stale last point would misreport the failure as the prior success.
        note = state.trajectory[-1] if state.trajectory else None
        if note is not None and note[0] == iteration:
            detail = f"score {note[1]:.2f} @ {note[2]:.0%}"
        else:
            detail = "no campaign (see log)"
        print(f"iteration {iteration}: {detail}  ->  {rel}")
    return state


def _seed_state(harness: str | Path, iter1_path: Path, max_examples: int) -> LoopState:
    """Run the seed strategy once (no LLM cost) to give iteration 2 something to
    react to: its brief, its campaign summary, and the first trajectory point."""
    prev_code = iter1_path.read_text(encoding="utf-8")
    strategy = load_strategy(iter1_path)
    validation = validate_strategy(strategy, harness)

    state = LoopState(prev_code=prev_code, feedback=validation.brief())
    state.best_code = prev_code  # the seed is the base to revert to until beaten
    if not validation.passed:
        # Seed already failing the gate is fine -- iteration 2 gets the critique.
        return state

    campaign = run_campaign(strategy, harness, max_examples=max_examples)
    if campaign.crash is not None:
        finding = _build_finding(campaign.crash, 1)
        state.findings[finding.signature.digest] = finding
        save_finding(finding, CRASHES_DIR)
        state.known_digests = frozenset({finding.signature.digest})

    summary = summarize(campaign.stats, state.findings, iteration=1)
    score = score_campaign(campaign.stats)
    state.feedback = validation.brief() + "\n\n" + summary
    state.trajectory.append((1, score.value, campaign.stats.acceptance_rate))
    state.best_score = score.value
    state.best_iteration = 1
    state.best_path = iter1_path
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", default=str(ROOT / "build" / "harness"))
    parser.add_argument("--model", default=None)
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--start", type=int, default=2)
    parser.add_argument("--stop", type=int, default=5)
    parser.add_argument("-n", "--examples", type=int, default=MAX_EXAMPLES)
    parser.add_argument(
        "--iter1", default=str(STRATEGY_DIR / "generated_iter1.py"),
        help="the seed strategy to refine from",
    )
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")

    if not Path(args.harness).exists():
        raise SystemExit(f"no harness at {args.harness} -- run `make` first")
    iter1_path = Path(args.iter1)
    if not iter1_path.exists():
        raise SystemExit(
            f"no seed strategy at {iter1_path} -- run `python -m fuzzer.seed_run` first"
        )

    print("seeding from iteration 1 (no API call) ...")
    state = _seed_state(args.harness, iter1_path, args.examples)
    print(state.feedback.splitlines()[0] if state.feedback else "(no seed feedback)")

    llm = LLM(model=args.model) if args.model else LLM()
    print(f"\nmodel: {llm.model}   refining iterations {args.start}..{args.stop}\n")

    run_loop(
        llm, state, args.harness,
        start=args.start, stop=args.stop,
        temperature=args.temperature, max_examples=args.examples,
    )

    print("\n" + "-" * 70)
    print(llm.budget.report())
    print("-" * 70)
    if state.best_path is not None:
        print(
            f"\nbest: iteration {state.best_iteration} "
            f"(score {state.best_score:.2f}) -> "
            f"{state.best_path.relative_to(ROOT)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
