# Grammar-Based Fuzzing of the mxml XML Library — Report

**Target:** mxml v4.0.5 (`18d5c7dd`, tag *v4.0.5*), a small C library that parses
XML. It is built from source, so the sanitizers can look inside it.
**Idea:** an LLM writes a Hypothesis strategy that makes XML documents. A C
harness runs each document with AddressSanitizer and UndefinedBehaviorSanitizer.
A five-step loop then improves the strategy, using what each run shows.

---

## Design

### The grammar and how we changed it

We start from the ANTLR `grammars-v4` XML grammar. We copy it exactly, with no
edits, into `grammar/XMLLexer.g4` and `grammar/XMLParser.g4`. (XML uses two
grammar files, not one. The lexer needs *modes* — `DEFAULT`, `INSIDE`,
`PROC_INSTR` — to change its tokens when it enters a tag, and ANTLR only allows
modes in a pure lexer grammar.)

That grammar describes *correct* XML. But mxml does not follow the XML standard
exactly. So we made a changed copy (`XMLmxmlLexer.g4` and `XMLmxmlParser.g4`)
that matches what mxml really accepts. We did not guess the differences. We
**tested** them: `grammar/probe_mxml.c` asks mxml "do you accept this document?",
and `compare_grammars.py` checks three things side by side (what the standard
grammar allows, what our changed grammar allows, and what mxml really accepts).
It fails if our copy stops matching mxml. You can repeat this with
`make grammar-check`.

The main differences we found:

- **The first byte must be `<`.** mxml rejects a leading space. It also rejects a
  comment before *or* after the root element, because it treats the comment as
  the root node — so the real element looks like a second root, which is not
  allowed. This is stricter than the standard.
- **Attribute values do not need quotes.** `<a b=c/>` is accepted. The standard
  grammar cannot make this.
- **`<!...>` declarations become real nodes.** A cut-off declaration is a hard
  error, but a cut-off element (`<a`) is accepted with no error. This difference
  is worth testing.
- **Three rules that a context-free grammar cannot express.** The generator must
  handle these in code. (This is the main reason a program is better than a
  grammar for making test inputs.) **(N1)** the open and close tag names must be
  the same; **(N2)** attribute names must be unique inside one element;
  **(N3)** only five named entities exist — `amp apos gt lt quot` — plus numeric
  character references.

### The harness, the build, and how we decide accept / reject / crash

`harness.c` does not know the target library. It reads one input into a heap
buffer of the **exact** size (so ASan's guard bytes sit right after the real
input and can catch a read that goes one byte too far). It returns an exit code
that the Python runner reads: **0** = accepted, **2** = cleanly rejected,
**1** = harness error (not a bug), **134** or another fatal signal = a sanitizer
stopped the process (this is the bug we want). Timeouts are handled by the
runner, because a stuck process cannot report on itself.

`fuzz_target.c` is the only file that knows mxml. It sends the input through
`mxmlLoadIO` (a read callback), **not** `mxmlLoadString`. `mxmlLoadString` needs
a text string that ends in a `\0`, which would loosen the ASan guard and would
also cut off any document that contains a `\0` byte. The file sets
`MXML_TYPE_TEXT` so the accept rate measures *document shape*, not how often the
generator writes numbers. It also walks the whole tree, so the accessor code
runs too, not only the parser.

One important choice: mxml can return a tree **and** report an error at the same
time. For example, `<a>\x01</a>` gives a tree plus the message "Bad control
character". We count this third case as **accepted, with a note**, and we log the
message on its own. The sanitizers are the only judge of what is a real bug, and
mixing this case into "rejected" would spoil the one number the loop follows.

For the build (`Makefile`, `clang -fsanitize=address,undefined
-fno-sanitize-recover=undefined`), we **compile mxml from source into the
harness**. We do not link a ready-made library. Sanitizers only watch code that
is built with their flags, so linking a system `libmxml` would give a harness
that cannot see any bug inside the library.

### The loop and the signal it follows

**There is no coverage tool** in this build (no gcov, no
`-fsanitize-coverage`). So we cannot use branch coverage as a reward, the way the
PromptAgent paper does. The only signals we have from the outside are the
**accept rate, the grouped reject reasons, output variety, and the crash count.**

Our reward (`fuzzer/score.py`) aims for a **band, not a maximum**. A generator
that only prints `<a/>` gets a 100% accept rate but tests almost nothing. So we
aim for **70–85%** accepted, plus variety (a good spread of size and nesting
depth), with a small bonus for a crash. A 100% accept rate scores *lower* than a
varied generator inside the band. This stops the loop from cheating.

The loop (`fuzzer/loop.py`) is a **simple, greedy chain**: one new strategy per
step, kept only if it passes a cheap check. This is the smallest form of the
PromptAgent tree search (width one, no going back), which is all a 5-call / $5
budget allows. In the paper's terms: the **state** is the current strategy, the
**action** is the feedback we add to guide the next one, and the **reward** is
the band score. Each step uses **one** LLM call. The critique that the paper
makes with a second model, we compute for free in Python
(`validate.brief()` + `summarize()`) and add to that one call. A cheap 30-sample
check throws out a broken strategy *before* we pay for a 500-example run. If a
strategy fails to load or fails the check, the loop **goes back to the last
working strategy**, so one bad step cannot spoil the next.

---

## Findings

### No crashes found — and why this is the expected result

In every 500-example run (all five steps), we saw **zero sanitizer stops**. This
"none found" is on purpose and explained, not a miss. mxml ships an `afl-input/`
folder, which means **it has already been fuzzed with AFL**. So the easy
memory-safety bugs that a grammar-based generator finds first are most likely
already fixed. Our generator reached a healthy point — 80–82% accepted, with
varied and mostly correct output and a real minority of broken inputs (mismatched
tags, unterminated entities, cut-off comments) — and still found nothing. That
fits a parser whose simple bugs are already closed.

### How the strategy improved (second, hardened run)

| Step | Accept rate | Score | What changed |
|-----:|:-----------:|:-----:|:-------------|
| 1 (seed) | ~65% | 0.76 | first generator, built from the grammar |
| 2 | 49% | 0.49 | too many broken inputs → fell below the band |
| 3 | 93% | 0.65 | too high, above the band; output got shallow (nesting 0 ≈ 91%) |
| **4** | **81%** | **0.87** | **set broken fraction to 0.35, removed "broken" cases that mxml actually accepts (control chars, cut-off element), added unterminated-reference near-misses → landed in the band** |
| 5 | 82% | 0.87 | settled; only a small change, score stayed the same |

The winning generator is `fuzzer/strategies/generated_iter4.py`. The reward
worked as planned: step 3's 93% scored *lower* than step 4's 81%, because 93% is
above the band and less varied. So the loop was pulled toward the target, not
toward the highest accept rate. The key edit at step 4 was smart: the model read
its own summary ("92.6% was too high"), raised the broken fraction, and **took
out the cases that mxml accepts anyway** — which is why the "broken" 35% was not
lowering the real reject rate.

### Grammar parts still not well tested

- **Nesting depth.** 50,000 nested elements parse with no problem, so the parse
  path is not simply recursive. Depth is a poor place to look. Also, our depth
  measure counts bracket characters, not `<>` nesting, so the depth numbers are
  rough.
- **Encoding.** mxml checks UTF-8 but still accepts some invalid bytes silently
  (`<a>\xc3\x28</a>`). These gaps are only lightly touched.
- **Cut-off inputs** at each construct edge, and the **accept-with-note** case
  (control characters, odd declarations), are barely tested. These meaning-level
  edges — not raw volume — are where any remaining bug most likely hides.

---

## Challenges

- **No coverage feedback.** This was the main limit. It forced us to use an
  outside signal (accept rate + reject reasons + variety) and the band reward. We
  first tried a CSV target, but we dropped it, because a non-strict CSV library
  rejects *nothing* — the accept rate stays at 100% for every generator, so there
  is no gradient to follow. mxml has a real gradient (107 different failure
  points), which is why we chose it.
- **The three-way outcome.** "Accepted" is not the same as "no error". We had to
  decide that "tree plus a message" counts as accepted (and log the message
  separately) *before* writing the harness, because this choice changes the exit
  codes and every accept rate the loop later sees.
- **The LLM inventing Hypothesis API.** The first strategy needed several hand
  fixes just to load (no `+` on strategies, `max_leaves` not `max_depth`,
  `st.permutations` not `st.shuffled`, and `@st.composite` factories must be
  called). A first full run then invented *new* wrong API in the middle
  (`st.weighted_choices`, calling a strategy object). This broke two of four
  steps and spread: a broken module became the base for the next prompt, and the
  accept rate fell to about 9%. We fixed this two ways: **(1)** the refine prompt
  now lists every API mistake we have seen, and **(2)** a failed load or check
  goes back to the last working module. The second run then had no API failures
  and rose cleanly to 0.87. This weak spot of a width-one chain is part of the
  result, so we report it.
- **A real budget.** Five calls are far too few for the paper's search of 50–150
  prompts. So the honest choice was to build the smallest form of that search and
  describe it in the paper's MDP words, instead of pretending to run a search the
  budget cannot pay for. The budget is also counted per process, not shared
  across the seed run and the loop run — it is a rule we follow (1 + 4 = 5), not a
  hard stop.

**With more time or with coverage:** add `-fsanitize-coverage` and change the
reward from the accept band to real branch coverage; point the generator at the
meaning-level edges (UTF-8 gaps, cut-off inputs, the accept-with-note case)
instead of at volume; run longer with a saved corpus; and replace the single
chain with a small beam (width 2–3) once each try is no longer a paid API call.

---

## Artifacts (appendix — not counted in the two pages)

| Deliverable | Location |
|---|---|
| Grammar source + noted changes | `grammar/` (`XML*Lexer/Parser.g4`, `README.md`, `PROVENANCE.txt`); check with `make grammar-check` |
| Build script + harness source | `Makefile`, `harness/harness.c`, `harness/fuzz_target.c` |
| Baseline strategy + pipeline demo | `fuzzer/strategies/generated_iter1.py`; seed log `report/iterations/iter-01-seed-*.md` |
| Loop + reward + final generator | `fuzzer/loop.py`, `fuzzer/prompts.py`, `fuzzer/score.py`; **final winner** `fuzzer/strategies/generated_iter4.py` |
| Iteration log (how it changed) | `report/iterations/iter-0N-refine-*.md` (raw prompt, feedback, run summary, score per step) |
| Crash reports | **None found** — see Findings; `crashes/` holds only its README |
| Tests (pipeline is correct) | `tests/` — 72 passing (`fuzzer/*` + reward + loop + prompt checks) |
