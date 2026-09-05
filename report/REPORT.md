# Grammar-Based Fuzzing of the mxml XML Library — Report

**Target:** mxml v4.0.5 (`18d5c7dd`, tag *v4.0.5*), a small C XML parser, built
from source so the sanitizers can see inside it.
**Approach:** an LLM writes a Hypothesis strategy that generates XML; a C harness
runs each document under AddressSanitizer/UndefinedBehaviorSanitizer; a
five-iteration loop refines the strategy from what each run reveals.

---

## Design

### The grammar and its adaptations

The starting point is the ANTLR `grammars-v4` XML grammar, vendored byte-for-byte
as `grammar/XMLLexer.g4` + `grammar/XMLParser.g4` (XML ships as a split
lexer/parser grammar because the lexer needs *modes* — `DEFAULT`, `INSIDE`,
`PROC_INSTR` — to switch its token set on entering a tag). That grammar describes
*conformant* XML, and mxml is not a conformant processor. The adaptation
(`XMLmxmlLexer.g4` + `XMLmxmlParser.g4`) closes the gap. Each divergence was
**observed**, not inferred: `grammar/probe_mxml.c` is an acceptance oracle that
asks "does mxml return a tree for this document?", and `compare_grammars.py`
runs a three-way comparison (upstream-derivable vs. adaptation-derivable vs.
mxml-accepted), failing if the model stops matching the library. Reproduce with
`make grammar-check`.

The material adaptations:

- **The first byte must be `<`.** mxml rejects leading whitespace, and rejects a
  leading *or* trailing comment — it counts the comment as the root node, so the
  real element becomes an illegal "second root." Stricter than the spec.
- **Unquoted attribute values are accepted** (`<a b=c/>`) — an entire syntax the
  upstream grammar cannot derive.
- **`<!...>` declarations parse into real nodes**; a *truncated* declaration is a
  hard error, but a truncated *element* (`<a`) is accepted silently — an
  inconsistency worth probing.
- **Three constraints no context-free grammar can express**, which the generator
  must enforce in code (this is exactly where a programmatic strategy beats
  deriving strings from a grammar): **(N1)** open/close tag names must match;
  **(N2)** attribute names are unique within an element; **(N3)** the entity set
  is closed — only `amp apos gt lt quot` plus numeric character references.

### Harness, build, and the accept/reject/crash decision

`harness.c` is library-agnostic: it reads one input into an **exact-sized** heap
buffer (so ASan's redzone sits immediately past the real input and catches
off-by-one overreads) and returns an exit code the Python runner reads —
**0** accepted, **2** cleanly rejected, **1** harness error (not a finding),
**134/other fatal signal** a sanitizer abort (the finding). Timeouts are the
runner's job, since a hung process cannot report on itself.

`fuzz_target.c` is the only mxml-aware file. It feeds input through `mxmlLoadIO`
(a read callback), *not* `mxmlLoadString`, to avoid NUL-termination — which would
both loosen the redzone and silently truncate any document containing a NUL. It
sets `MXML_TYPE_TEXT` so acceptance measures *document structure* rather than how
often the generator emits numbers, and it walks the whole tree so the accessor
code (not just the parser) runs. A key judgment call: mxml can return a tree
**and** report an error (`<a>\x01</a>` yields a document plus "Bad control
character"). That third outcome is counted as **accepted-with-diagnostic** and
logged separately — the sanitizers remain the only authority on what is a bug,
and folding it into "rejected" would corrupt the one number the loop steers on.

The build (`Makefile`, `clang -fsanitize=address,undefined
-fno-sanitize-recover=undefined`) **compiles mxml from source into the harness**
rather than linking a prebuilt library — sanitizers only instrument code built
with their flags, so linking a system `libmxml` would produce a harness blind to
every bug inside the library.

### The agentic loop and its steering signal

**There is no coverage instrumentation** (the assignment's build has no gcov/
`-fsanitize-coverage`), so PromptAgent's branch-coverage reward is unavailable.
The only blackbox signals are **acceptance rate, grouped rejection reasons,
output diversity, and crash count.** The reward (`fuzzer/score.py`) is
deliberately **band-targeted and anti-Goodhart**: a generator emitting `<a/>`
forever scores 100% acceptance and tests nothing, so the target is **70–85%**
acceptance *plus* structural variety (spread in size and nesting depth), with a
small crash bonus. 100% acceptance scores *below* an in-band, varied generator.

The loop (`fuzzer/loop.py`) is a **linear greedy chain** — the degenerate
(width-1, no-backtrack) case of PromptAgent's MCTS, which is all a 5-call / $5
budget affords. Framed as the paper's MDP: **state** = current strategy,
**action** = the feedback injected to steer the next one, **reward** = the band
score. Each iteration spends **one** LLM call; the critique the paper generates
with a second model is instead computed for free in Python (`validate.brief()` +
`summarize()`) and folded into that one call. Iteration 1 is the plain seed (no
optimization); iterations 2–5 refine. A cheap 30-sample gate rejects a broken
strategy *before* spending a 500-example campaign, and a failed load/gate
**reverts to the last working module** so one bad call cannot cascade.

---

## Findings

### No crashes found — and why that is the expected result

Across every 500-example campaign (5 iterations), **zero sanitizer aborts** were
observed. This is a *documented* none-found, not an oversight. mxml ships an
`afl-input/` corpus: **it has already been fuzzed with AFL**, so the shallow
memory-safety bugs a grammar-driven generator finds first are long since fixed.
The generator reached a healthy operating point — 80–82% acceptance with varied,
well-formed output and a meaningful malformed minority (mismatched tags,
unterminated entities, early-EOF comments) — and still tripped nothing, which is
consistent with a parser whose easy faults are already closed.

### How the strategy evolved (second, hardened run)

| Iter | Acceptance | Score | What drove the change |
|-----:|:----------:|:-----:|:----------------------|
| 1 (seed) | ~65% | 0.76 | baseline generator from the grammar |
| 2 | 49% | 0.49 | over-corrected toward malformed → dropped below band |
| 3 | 93% | 0.65 | overshot *above* the band; output went shallow (nesting 0 ≈ 91%) |
| **4** | **81%** | **0.87** | **tuned malformed fraction to 0.35, pruned "malformed" cases mxml actually accepts (control chars, truncated element), added unterminated-reference near-misses → landed in band** |
| 5 | 82% | 0.87 | converged; only cosmetic change, score held |

The winning generator is `fuzzer/strategies/generated_iter4.py`. The reward did
its job: iteration 3's 93% scored *lower* than iteration 4's 81%, precisely
because 93% is outside the band and less varied — the loop was pulled toward the
target rather than toward maximal acceptance. The decisive edit at iteration 4
was the model reading its own summary ("92.6% was too high") and both raising the
malformed fraction and **removing the accept-anyway cases from the malformed
bucket** — a genuinely apt diagnosis of why the "malformed" 35% was not lowering
the true rejection rate.

### Grammar regions still under-tested

- **Nesting depth.** 50,000 nested elements parse without incident, so the parse
  path is not naively recursive; depth is a poor place to hunt. The depth proxy
  also under-measures (it counts bracket characters, not `<>` nesting), so the
  reported depth distribution is coarse.
- **Encoding.** mxml validates UTF-8 yet accepts some invalid sequences silently
  (`<a>\xc3\x28</a>`). The validation has mapped gaps that the current generator
  only grazes.
- **Truncation** at construct boundaries and the **accept-with-diagnostic seam**
  (control characters, odd declarations) are lightly exercised — the semantic
  edges, rather than volume, are where remaining bugs most plausibly live.

---

## Challenges

- **No coverage feedback.** The central design constraint. It forced the
  blackbox proxy signal (acceptance + rejection clustering + diversity) and the
  anti-Goodhart band reward. An earlier CSV target was abandoned outright because
  non-strict libcsv rejects *nothing* — pinning acceptance at 100% for every
  generator and leaving no gradient to steer on. mxml has a real gradient (107
  distinct failure sites), which is why it was chosen.
- **The three-outcome oracle.** "Accepted" ≠ "no error reported." Deciding that
  tree-plus-diagnostic counts as accepted (and logging the message separately)
  had to happen *before* the harness seam was written, because it changes the
  exit-code contract and therefore every acceptance rate the loop ever sees.
- **The LLM hallucinating the Hypothesis API.** The seed strategy needed several
  hand patches to even load (no `+` on strategies, `max_leaves` not `max_depth`,
  `st.permutations` not `st.shuffled`, calling `@st.composite` factories). A
  first full loop run then produced *new* hallucinations mid-flight
  (`st.weighted_choices`, calling a strategy object) that broke two of four
  iterations and cascaded — a bad module became the base for the next prompt,
  collapsing acceptance to ~9%. Two fixes followed: **(1)** the refine prompt now
  names every observed API pitfall explicitly, and **(2)** a failed load/gate
  reverts to the last working module. The second run then had zero API failures
  and climbed cleanly to 0.87. This is the width-1 chain's core fragility, and
  documenting it is part of the result.
- **Budget realism.** Five calls forbid MCTS's 50–150 prompt evaluations, so the
  faithful move was to implement the *degenerate* case honestly and frame it in
  the paper's MDP vocabulary, rather than pretend to a search the budget cannot
  fund. The budget is also per-process, not enforced across the seed and loop
  runs — a convention (1 + 4 = 5), not a hard cap.

**With more time or coverage:** wire in `-fsanitize-coverage` and switch the
reward from acceptance-band to real branch coverage; aim the generator at the
mapped semantic edges (UTF-8 validation gaps, truncation, the accept-with-error
seam) rather than at volume; run longer campaigns with a persistent corpus; and
replace the greedy chain with a small beam (width 2–3) once each evaluation is no
longer a paid API call.

---

## Artifacts (appendix — does not count against the two pages)

| Deliverable | Location |
|---|---|
| Grammar source + noted adaptations | `grammar/` (`XML*Lexer/Parser.g4`, `README.md`, `PROVENANCE.txt`); verify with `make grammar-check` |
| Build script + harness source | `Makefile`, `harness/harness.c`, `harness/fuzz_target.c` |
| Baseline strategy + pipeline demonstration | `fuzzer/strategies/generated_iter1.py`; seed log `report/iterations/iter-01-seed-*.md` |
| Agentic loop + reward + final generator | `fuzzer/loop.py`, `fuzzer/prompts.py`, `fuzzer/score.py`; **final winner** `fuzzer/strategies/generated_iter4.py` |
| Iteration log (how the strategy evolved) | `report/iterations/iter-0N-refine-*.md` (raw prompt, feedback, campaign summary, score per iteration) |
| Crash reports | **None found** — see Findings; `crashes/` holds only its README |
| Tests (pipeline correctness) | `tests/` — 72 passing (`fuzzer/*` + reward + loop + prompt contracts) |
