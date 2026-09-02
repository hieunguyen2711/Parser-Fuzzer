# fuzzer/ — Python: grammar strategies, runner glue, agentic loop.

| module | role |
|---|---|
| `runner.py` | Runs one input through the harness; classifies accept / reject / crash / harness-error. The only module that knows the harness contract. |
| `triage.py` | Sanitizer report → stable crash signature; saves reproducers to `crashes/`. Normalization choices are documented in the module docstring — they decide whether you report one bug or five. |
| `campaign.py` | One bounded run of a strategy (≤500 examples). Crash detection lives *inside* `@given` so Hypothesis shrinks reproducers. |
| `summary.py` | Compresses a run into the briefing the LLM sees between iterations. |
| `strategies/baseline.py` | Step 3's deliberately naive generator — the control condition. |
| `baseline_run.py` | CLI: `python -m fuzzer.baseline_run --harness build/harness` |
| `examples.py` | Draws concrete examples out of a strategy, with a fixed seed so runs are comparable. |
| `inspect.py` | CLI for looking at documents and mxml's verdict on them, by eye. |
| `validate.py` | Step 4.2 gate: sample a generated strategy cheaply and refuse it before it burns a 500-example run. |
| `llm.py` | Vertex AI client (ADC auth) plus token/cost accounting against the 5-iteration / $5 cap. |

Not yet written: `loop.py` (the agentic refinement loop) and the
grammar-derived strategies it generates.

## Looking at what a generator produces

    python -m fuzzer.inspect corpus
    python -m fuzzer.inspect one '<a x="1"><b/></a>'
    python -m fuzzer.inspect strategy fuzzer.strategies.baseline:BASELINE -n 12

Campaign reports give aggregates, which say *that* a generator is being
refused but never show what it emitted. This shows the documents themselves,
with control characters escaped so they cannot scramble the terminal.

Run the pipeline's own regression tests with `make mock && pytest tests/`.
