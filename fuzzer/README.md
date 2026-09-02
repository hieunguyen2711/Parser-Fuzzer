# fuzzer/ — Python: grammar strategies, runner glue, agentic loop.

| module | role |
|---|---|
| `runner.py` | Runs one input through the harness; classifies accept / reject / crash / harness-error. The only module that knows the harness contract. |
| `triage.py` | Sanitizer report → stable crash signature; saves reproducers to `crashes/`. Normalization choices are documented in the module docstring — they decide whether you report one bug or five. |
| `campaign.py` | One bounded run of a strategy (≤500 examples). Crash detection lives *inside* `@given` so Hypothesis shrinks reproducers. |
| `summary.py` | Compresses a run into the briefing the LLM sees between iterations. |
| `strategies/baseline.py` | Step 3's deliberately naive generator — the control condition. |
| `baseline_run.py` | CLI: `python -m fuzzer.baseline_run --harness build/harness-mock` |

Not yet written: `loop.py` (the agentic refinement loop) and the grammar-derived
strategies, both of which need the assigned library and its format's grammar.

Run the pipeline's own regression tests with `make mock && pytest tests/`.
