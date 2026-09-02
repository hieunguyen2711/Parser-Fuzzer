# grammar/ — the XML grammar, and where it diverges from mxml

Target: **mxml v4.0.5** (`18d5c7d`), cloned at `target/mxml`.

| file | what it is |
|---|---|
| `XMLLexer.g4`, `XMLParser.g4` | The ANTLR `grammars-v4` XML grammar, vendored byte-for-byte. Unmodified. |
| `XMLmxmlLexer.g4`, `XMLmxmlParser.g4` | The same grammar adapted to what mxml actually accepts. |
| `probe_mxml.c` | One-shot acceptance oracle: does `mxmlLoadString` return a tree for this document? |
| `compare_grammars.py` | Three-way comparison producing the table below. Exits nonzero on any unexplained divergence. |
| `PROVENANCE.txt` | Sources, commits, licenses, and what was verified how. |
| `superseded-csv/` | An earlier, complete Step 1 for libcsv/CSV, before the target was confirmed. Not part of the submission. |

Claims tagged `[PROBE]` were produced by compiling mxml and running the input;
they are observed. Claims tagged `[SRC]` were read from `mxml-file.c`.

---

## Why XML is split into two grammar files

Unlike most formats in `grammars-v4`, XML ships as a separate lexer grammar and
parser grammar. The split is forced: the lexer needs **modes** (`DEFAULT`,
`INSIDE`, `PROC_INSTR`) to switch its token set on entering a tag, and ANTLR
allows modes only in a pure lexer grammar. The adaptation keeps the split.

This has a practical consequence — the lexer must be generated *before* the
parser, so the parser can find its `.tokens` file:

```
antlr4 -o OUT XMLmxmlLexer.g4
antlr4 -o OUT -lib OUT/grammar XMLmxmlParser.g4
```

Generating them together fails with `cannot find tokens file`, and upstream's
own grammar fails identically, so it is a build-order issue rather than a
grammar defect. `make grammar-check` handles the ordering for you.

---

## Verification

Reproduce with **`make grammar-check`** — it downloads the ANTLR tool into
`build/`, regenerates both parsers, compiles the acceptance probe against the
pinned mxml, runs the comparison, and exits nonzero if the adaptation stops
matching the library.

All three columns run against the same documents: derivable from upstream,
derivable from the adaptation, accepted by mxml itself. The third column is
ground truth; the first two are models of it.

```
case                      upstr  adapt   mxml   note
------------------------------------------------------------------------------
plain element               yes    yes    yes
self-closing                yes    yes    yes
nested                      yes    yes    yes
xml declaration             yes    yes    yes
CDATA                       yes    yes    yes
trailing whitespace         yes    yes    yes
DTD then root               yes    yes    yes
leading whitespace          yes     no     no   gap closed
leading comment             yes     no     no   gap closed
trailing comment            yes     no     no   gap closed
mismatched tags             yes    yes     no   diverges, expected: N1
duplicate attribute         yes    yes     no   diverges, expected: N2
undefined entity            yes    yes     no   diverges, expected: N3
unquoted attr value          no    yes    yes   gap closed
truncated element            no     no    yes   diverges, expected
control char in text        yes    yes    yes
empty input                  no     no     no
text before root             no     no     no

unexplained divergences: 0
```

---

## Gaps that change what is derivable

| # | Upstream says | mxml does | Consequence |
|---|---|---|---|
| 1 | `document : prolog? misc* element misc* EOF` — comments, PIs and whitespace allowed on both sides of the root | **Rejects all of them.** `  <a/>` → *"XML does not start with '<'"*. `<!--c--><a/>` → *"`<a>` cannot be a second root node after `<c>`"* `[PROBE]` | The largest divergence, and stricter than the XML spec. mxml counts a leading comment as the root node, so the real element becomes an illegal second root. |
| 2 | `STRING` requires quotes | **Accepts unquoted attribute values.** `<a b=c/>` parses `[PROBE]`; it reads until whitespace, `=`, `/` or `>` `[SRC]` | An entire attribute syntax upstream cannot derive. |
| 3 | `DTD : '<!' .*? '>' -> skip` — discarded by the lexer | Parses `<!...>` into a real `MXML_TYPE_DECLARATION` node; a truncated one is a hard error `[SRC]` | A generator that cannot emit declarations never reaches that code. |
| 4 | Trailing whitespace covered by `misc*` | Accepted `[PROBE]` — but *leading* whitespace is not | An asymmetry worth generating both sides of. |

## Constraints no context-free grammar can express

These are why the adaptation still diverges on three rows above, and why that
is not a defect. **The generator must enforce them in code** — which is exactly
where a programmatic Hypothesis strategy beats deriving strings from a grammar.

- **N1 — open and close names must match.** `element` has two independent
  `Name` tokens, so `<a></b>` is derivable; upstream has the same hole. mxml:
  *"Mismatched close tag `</b>` under parent `<a>`"*. A strategy should generate
  the name once and reuse it, emitting mismatches only as deliberate near-misses.
- **N2 — attribute names unique per element.** `attribute*` permits duplicates;
  mxml rejects with *"Duplicate attribute 'x'"*.
- **N3 — the entity set is closed.** `EntityRef : '&' Name ';'` admits any name.
  mxml supports exactly `amp`, `apos`, `gt`, `lt`, `quot`, plus numeric
  character references, plus a user callback `[SRC mxml-options.c]`. `&foo;` is
  rejected.

---

## Two findings that shape the harness

**The acceptance signal is healthy here.** `mxml-file.c` has 107 failure
sites with specific messages, and the probe shows a wide spread of accept and
reject across ordinary inputs. This matters because the refinement loop steers
on acceptance rate: the earlier CSV target had to be abandoned as a signal
source because non-strict libcsv rejects *nothing*, pinning the rate at 100%
for every generator. mxml has a real gradient, and its error strings cluster
well for the "why is it rejecting?" half of the summary.

**"Accepted" and "no error reported" are not the same thing, and the harness
must choose.** `<a>\x01</a>` returns a tree *and* reports *"Bad control
character 0x01 not allowed by XML standard"* through the error callback
`[PROBE]`. So there are three outcomes, not two:

| `mxmlLoadString` | error callback | how to count it |
|---|---|---|
| tree | silent | accepted |
| `NULL` | message | cleanly rejected |
| **tree** | **message** | **a decision** |

I would count the third as *accepted with a diagnostic* and log the message
separately, on the grounds that the parser did produce a document and the
sanitizers remain the only authority on what is a bug. But it needs deciding
before the seam is written, since it changes the exit-code contract and
therefore every acceptance rate the loop ever sees.

---

## Where to push the generator

- **The three N-constraints, from both sides.** Well-formed by construction
  most of the time, deliberately violated at a controlled rate.
- **Truncation.** `<a` is accepted *silently* `[PROBE]` while `<!--x` is
  rejected with *"Early EOF in comment node"*. Truncation at every construct
  boundary is cheap to generate and the handling is visibly inconsistent.
- **Encoding.** mxml validates UTF-8 and rejects bad control characters
  `[SRC]`, yet `<a>\xc3\x28</a>` — an invalid two-byte sequence — is accepted
  silently `[PROBE]`. The validation has gaps worth mapping.
- **Not depth.** 50,000 nested elements parse without incident `[PROBE]`, so
  the parse path is not naively recursive. Deep nesting is a cheap thing to
  generate and a poor thing to bet on here.

## What tempers expectations

mxml ships an `afl-input/` directory: **it has been fuzzed with AFL already.**
The shallow memory-safety bugs a grammar-driven generator finds first are
likely long since fixed. That is worth saying now rather than discovering it at
report time, and it makes the assignment's "documented none found, and why" a
genuinely plausible outcome. It also argues for aiming at the semantic edges
above — truncation, encoding, the accept-with-error seam — rather than at
volume.

---

## Caveat

v4.0.5 is the latest release tag and **my choice, not a confirmed assignment
pin**. mxml 4.x is an API rewrite of 3.x — `mxmlLoadString` gained an
`mxml_options_t` parameter — so if the assignment pins a 3.x version, the
harness seam changes shape and this analysis needs redoing rather than
re-checking. Re-run `compare_grammars.py` after any version change; it exits
nonzero if the adaptation stops matching the library.
