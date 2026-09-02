# grammar/ — the CSV grammar, and where it diverges from libcsv

| file | what it is |
|---|---|
| `CSV.g4` | The ANTLR `grammars-v4` CSV grammar, vendored byte-for-byte. Unmodified. |
| `CSVlibcsv.g4` | The same grammar adapted to what libcsv actually recognizes. |
| `PROVENANCE.txt` | Source URLs, upstream commit, license, retrieval date. |
| `compare_grammars.py` | Parses the same inputs under both grammars; produces the verification table below. |

Claims tagged `[SRC]` were checked against libcsv's parser source, not inferred
from its documentation.

---

## The upstream grammar

```antlr
csvFile : hdr row+ EOF ;
hdr     : row ;
row     : field (',' field)* '\r'? '\n' ;
field   : TEXT | STRING | ;
TEXT    : ~[,\n\r"]+ ;
STRING  : '"' ('""' | ~'"')* '"' ;
```

Six lines, and four of them are wrong about libcsv.

---

## Gaps that change what is derivable

These matter most: each is a document libcsv handles that the upstream grammar
cannot produce, so a generator built from it verbatim would never emit one.

| # | Upstream says | libcsv does | Why it matters for fuzzing |
|---|---|---|---|
| 1 | `hdr row+` — at least **two** rows | No header concept; every row goes to the same callback. One row is fine. | A generator inheriting this never emits a single-row file, the most common CSV shape there is. |
| 2 | Every row ends with `'\n'`, including the last | `csv_fini()` flushes the pending field and row, so `a,b` with no trailing newline is complete `[SRC]` | The unterminated-final-row path is separate code, reached only at EOF, and never exercised without this. |
| 3 | Terminator is `'\r'? '\n'` — LF required | CR and LF each terminate independently. `\r\n` terminates once, because the LF then lands in `ROW_NOT_BEGUN` where empty rows are suppressed unless `CSV_REPALL_NL` `[SRC]` | Lone-CR files (classic Mac) are underivable upstream but perfectly ordinary to libcsv. |
| 4 | `TEXT : ~[,\n\r"]+` — quotes **excluded** from unquoted fields | Non-strict mode submits a stray quote as a literal character `[SRC FIELD_BEGUN]` | The single most valuable gap. `ab"cd` is underivable upstream, yet quote handling in unquoted fields is exactly where a hand-rolled state machine goes wrong. |
| 5 | Empty input violates `hdr row+` | Accepted; zero rows | Empty and near-empty inputs are cheap and disproportionately good at finding initialization bugs. |
| 6 | No notion of NUL or non-text bytes | Parses a `(pointer, length)` buffer, so `0x00` is ordinary field data | An ANTLR-derived generator will produce text. libcsv's interface is binary. |

## Verification

Both grammars were compiled with ANTLR 4.13.2 (upstream clean; the adaptation
clean and warning-free) and run against the same inputs. Every gap claimed
above is demonstrated rather than asserted, and nothing upstream accepted was
lost:

```
case                             upstream   adapted
---------------------------------------------------
single row, no header              REJECT    accept  <-- gap
no trailing newline                REJECT    accept  <-- gap
empty input                        REJECT    accept  <-- gap
lone CR terminator                 REJECT    accept  <-- gap
stray quote in text                REJECT    accept  <-- gap
text after closing quote           REJECT    accept  <-- gap
unterminated quote                 REJECT    accept  <-- gap
NUL byte in field                  REJECT    accept  <-- gap
--- both should accept ---
two rows, quoted                   accept    accept
empty fields                       accept    accept
quoted with comma                  accept    accept
escaped quote                      accept    accept
multiline quoted field             accept    accept
```

Reproduce with `compare_grammars.py`; its docstring lists the steps. The eight
gap rows are eight document shapes a generator built from the upstream grammar
verbatim would never emit, three of which (`no trailing newline`,
`single row`, `stray quote`) are entirely ordinary CSV in the wild.

## Gaps in interpretation, not acceptance

Both accept these; they disagree on what the document *means*. They won't
change the accept/reject signal, but they will mislead anyone reading
generated examples and assuming ANTLR semantics.

- **Whitespace is trimmed.** libcsv skips leading spaces/tabs before a field
  begins, and strips trailing ones at submission via `entry_pos -= spaces`
  `[SRC SUBMIT_FIELD]`. Upstream's `TEXT` keeps them as ordinary characters.
- **Blank lines vanish.** A blank line is a valid one-empty-field row
  upstream. libcsv suppresses empty rows by default `[SRC ROW_NOT_BEGUN]`.
- **A trailing terminator yields a phantom row.** `csvFile : row (TERM row)*`
  derives `a,b\n` as two rows, the second empty, because `row` can derive
  empty. libcsv reports one. Writing the rule any other way gives ANTLR two
  derivations of the empty string and an ambiguity warning; the phantom row is
  the cheaper of the two problems, and it costs nothing as long as a generator
  never treats derivation count as row count.
- **Delimiter and quote are runtime-configurable** (`csv_set_delim`,
  `csv_set_quote`). Both grammars hardcode `,` and `"`; the harness must pin
  them to match, or every generated document is misparsed.

---

## The finding that affects the loop design

**In its default non-strict mode, libcsv rejects nothing.** Walking the state
machine, there is no input for which `csv_parse` returns a parse error unless
`CSV_STRICT` is set — every malformed construct has a lenient branch that
consumes it and moves on `[SRC]`.

That breaks the primary steering signal. The refinement loop in
`fuzzer/summary.py` is built around acceptance rate as the proxy for "is the
generator reaching interesting code?", on the reasoning that a generator
rejected 99% of the time is only testing the error path. Against non-strict
libcsv that number is pinned at 100% for every generator ever written,
including one emitting uniform random bytes. A constant is not a signal.

Two ways out, and I'd take both:

1. **Run the primary campaign with `CSV_STRICT | CSV_STRICT_FINI`.** Strict
   mode has four distinct `CSV_EPARSE` sites `[SRC]`, so rejection becomes
   meaningful and acceptance rate starts discriminating between generators
   again. This is the configuration the loop should optimize against.

2. **Run a secondary non-strict campaign,** because the lenient branches are
   real code that strict mode never reaches — and skipping them would cede
   exactly the code paths gap #4 says are most suspect. Steer this one by
   structural yield (fields and rows emitted per byte of input) rather than
   acceptance rate.

Deciding this before writing the seam matters, because the mode is chosen in
`csv_init()` and it determines whether the loop has a usable gradient at all.

---

## Where the generator should be pushed

From reading the state machine, the constructs most likely to repay effort:

- **Very long fields.** `entry_buf` grows in `MEM_BLK_SIZE` (128 byte) blocks
  via realloc. Field lengths straddling multiples of 128 exercise the growth
  path at its boundaries.
- **`CSV_APPEND_NULL`.** It writes `entry_buf[entry_pos] = '\0'` at submission
  `[SRC SUBMIT_FIELD]` — a write at exactly the end of the used region, which
  is the classic shape of an off-by-one.
- **Spaces before a closing quote.** `FIELD_MIGHT_HAVE_ENDED` does
  `entry_pos -= spaces + 1` `[SRC]`. `entry_pos` is a `size_t`, so if
  `spaces + 1` could ever exceed it, that subtraction wraps to an enormous
  value. I have **not** shown that state is reachable — but inputs of the form
  `"a"` followed by runs of spaces and quotes are cheap to generate and this
  is where I would aim first.
- **The four near-miss productions** in `CSVlibcsv.g4`, which exist for
  exactly this purpose.

---

## Caveat

libcsv was read at **`b1d5212831842ee5869d99bc208a21837e4037d5`** — upstream
master HEAD, version **3.0.3** per the macros in `csv.h`. The repository
carries no git tags, so those macros are the only version identifier it
offers. Both files read were verified byte-identical to that commit's blobs.

That commit is **my choice, not the assigned one**, which has not been
provided. Master has not moved since August 2021, so drift is unlikely to be
the problem here — but if the coursework pins something else, every `[SRC]`
claim needs re-checking against that tree. The structural gaps against the
ANTLR grammar will not move; the line-level observations could.
