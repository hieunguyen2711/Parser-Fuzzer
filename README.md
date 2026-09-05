# Parser Fuzzer — mxml

A grammar-based fuzzer for the **mxml** C library (an XML parser). An LLM writes
a Hypothesis strategy that makes XML documents. A C harness runs each document
with AddressSanitizer and UndefinedBehaviorSanitizer. A five-step loop then improves
the strategy from what each run shows. The full write-up is in
[`report/REPORT.md`](report/REPORT.md).

This README explains how to reproduce the results from a clean copy.

---

## What you need

- **macOS or Linux**
- **clang** (builds the harness with sanitizers)
- **Python 3.11+** (3.13 is used here)
- **Java** on your PATH — only for `make grammar-check` (it runs the ANTLR tool)
- **Google Cloud access** — only if you want to run the LLM steps. See
  [LLM setup](#5-set-up-the-llm-only-for-the-generation-steps) below. The build,
  the tests, and the grammar check need no cloud access and no money.

The target library **mxml v4.0.5** (commit `18d5c7dd`) is already in
`target/mxml`, pinned and not edited. Do not change it — a clean pinned copy is
what makes triage honest.

---

## Project layout

```
grammar/    the XML grammar, the changed copy for mxml, and the checker
harness/    the C harness (harness.c) and the mxml glue (fuzz_target.c)
target/     mxml v4.0.5, built from source into the harness
fuzzer/     the Python: prompts, LLM client, loop, reward, campaign runner
tests/      pytest suite (no cloud, no cost)
report/     the written report and the per-step logs
crashes/    saved crash reports (empty here — none were found)
Makefile    builds build/harness with ASan + UBSan
```

---

## Steps to reproduce

### 1. Make a Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .            # so `import fuzzer` works from anywhere
```

`requirements.txt` holds the exact pinned versions. `pip install -e .` installs
the local `fuzzer` package in editable mode; edits take effect with no reinstall.

### 2. Build the harness

```bash
make
```

This compiles mxml from source together with the harness, using
`-fsanitize=address,undefined`. The result is `build/harness`. (Note: mxml is
compiled *into* the harness, not linked as a ready-made library, so the
sanitizers can watch the library code.)

Quick check that it works:

```bash
printf '<a/>'  | ./build/harness ; echo "exit=$?"   # accepted   -> exit 0
printf 'oops'  | ./build/harness ; echo "exit=$?"   # rejected   -> exit 2
```

Exit codes: **0** accepted, **2** cleanly rejected, **1** harness error,
**134** (or another fatal signal) a sanitizer stop — that is a bug.

### 3. Run the tests (proves the pipeline works, no cost)

```bash
.venv/bin/python -m pytest -q
```

All 72 tests should pass. These cover the reward function, the loop control flow
(with a fake LLM, so no network), the prompt contracts, and the runner.

### 4. Check the grammar (optional, needs Java)

```bash
make grammar-check
```

This downloads the ANTLR tool into `build/`, regenerates the parsers, compiles
the acceptance probe against mxml, and compares three things: what the standard
grammar allows, what our changed grammar allows, and what mxml really accepts.
It exits non-zero if our changed grammar stops matching mxml.

### 5. Set up the LLM (only for the generation steps)

The LLM runs through **Vertex AI** with **Application Default Credentials
(ADC)** — there is no API key in this repo.

1. Create a `.env` file in the repo root with your project and region:

   ```
   GOOGLE_CLOUD_PROJECT=your-project-id
   GOOGLE_CLOUD_LOCATION=your-region       # e.g. us-central1
   ```

2. Log in once so ADC is on your machine:

   ```bash
   gcloud auth application-default login
   ```

   This writes `~/.config/gcloud/application_default_credentials.json`, which the
   client picks up on its own.

### 6. Run iteration 1 (the seed)

```bash
.venv/bin/python -m fuzzer.seed_run
```

This spends **one** LLM call. It builds the seed prompt, asks the model for a
Hypothesis strategy, writes `fuzzer/strategies/generated_iter1.py`, and checks
that it loads and passes the cheap gate. The full prompt and reply are saved to
`report/iterations/iter-01-seed-*.md`.

> The model is not perfect. A fresh seed may still contain a small API mistake
> that stops it loading. If that happens, `seed_run` exits 1 — just run it again,
> or reuse the known-good seed already in `fuzzer/strategies/generated_iter1.py`.

### 7. Run iterations 2–5 (the refine loop)

```bash
.venv/bin/python -m fuzzer.loop
```

This spends up to **four** LLM calls. It first runs the seed strategy once with
no cost to get a baseline, then refines it four times. Each step:

- builds a refine prompt from the last run's numbers,
- asks the model for a new strategy,
- checks it, and if it passes, runs a 500-example campaign,
- writes `report/iterations/iter-0N-refine-*.md` and updates the best strategy.

At the end it prints the budget and which step won. Expect the accept rate to
move into the **70–85%** band and the score to rise. In our run the winner was
step 4 (`generated_iter4.py`, score 0.87, 81% accepted).

---

## Where the outputs go

| Output | Location |
|---|---|
| Per-step logs (prompt, feedback, summary, score) | `report/iterations/iter-0N-*.md` |
| Generated strategies | `fuzzer/strategies/generated_iterN.py` |
| Crash reports (deduplicated, minimized) | `crashes/` (empty — none found) |
| Written report | `report/REPORT.md` |

---

## Notes and limits

- **Budget:** the assignment allows 5 LLM calls / $5. The seed uses 1 call and
  the loop uses 4 (1 + 4 = 5). The budget is counted per process, so it is a
  rule we follow across the two commands, not a hard shared cap.
- **No crashes are expected.** mxml has already been fuzzed with AFL, so its
  simple bugs are likely fixed. See `report/REPORT.md` for the full explanation
  and what to try next.
- **macOS + LeakSanitizer:** do **not** set `ASAN_OPTIONS=detect_leaks=1` on
  macOS — it makes ASan hard-fail on every input. The harness frees its memory
  anyway, so leaks are still caught on Linux.
- **Reproduce without spending money:** steps 1–4 (env, build, tests, grammar
  check) need no cloud access. They alone show the pipeline is correct.
- **mxml version:** v4.0.5 was chosen here, not confirmed against an assigned
  pin. If a different major version is required, the harness glue changes shape —
  see `grammar/PROVENANCE.txt`.
