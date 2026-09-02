/*
 * CSVlibcsv.g4 -- the ANTLR grammars-v4 CSV grammar, adapted to the language
 * libcsv actually recognizes. Derived from CSV.g4 (BSD, Terence Parr); see
 * PROVENANCE.txt. README.md explains the reasoning behind every change.
 *
 * This is a GENERATOR SPEC. The distinction matters more than usual here:
 * libcsv in its default non-strict mode rejects nothing at all -- its accepted
 * language is every byte string, so an acceptor grammar for it would be the
 * useless production `.*`. What is worth describing is the structure libcsv
 * *recognizes*: how it divides bytes into fields and rows. That is what a
 * generator must reproduce, and what the near-miss patterns at the bottom
 * deliberately violate.
 *
 * Assumes the harness leaves the delimiter at ',' and the quote at '"'. Both
 * are runtime-configurable in libcsv (csv_set_delim / csv_set_quote), and if
 * the harness changes them, this grammar is wrong.
 */

grammar CSVlibcsv;

// ---------------------------------------------------------------------------
// Parser
// ---------------------------------------------------------------------------

// CHANGE 1: no header/body split. libcsv has no header concept -- every row is
//   delivered through the same callback. Upstream's `hdr row+` also imposed a
//   two-row minimum that libcsv does not have.
// CHANGE 2: empty input is valid, yielding zero rows.
// CHANGE 3: the trailing terminator is optional. csv_fini() flushes a pending
//   field and row, so `a,b` with no newline is a complete document. Upstream
//   required '\n' on every row including the last.
// No optional wrapper here, deliberately. `row` can already derive empty (a
// row of one empty field), so wrapping it in `(...)?` gives two derivations of
// the empty string and ANTLR warns. Written this way, a trailing terminator
// yields a final empty row -- which libcsv suppresses rather than reports.
// That is an interpretation gap, not an acceptance gap; see README.md.
csvFile
    : row (TERM row)* EOF
    ;

row
    : field (DELIM field)*
    ;

field
    : STRING
    | TEXT
    |            // empty field
    ;

// ---------------------------------------------------------------------------
// Lexer
//
// STRING is declared before TEXT deliberately. ANTLR resolves overlaps by
// longest match, then by declaration order, and since CHANGE 4 lets TEXT
// contain quotes, `"a"` is matched at equal length by both. Declaration order
// is what makes it lex as a quoted field rather than as literal text.
// ---------------------------------------------------------------------------

// Unchanged from upstream. `~'"'` admits CR and LF, so a quoted field may span
// lines -- true of libcsv as well.
STRING
    : '"' ('""' | ~'"')* '"'
    ;

// CHANGE 4: quotes are ordinary characters in an unquoted field. Upstream's
//   `~[,\n\r"]+` excluded them, making `ab"cd` underivable; libcsv non-strict
//   submits the quote as a literal. This is the highest-value gap of the six.
// CHANGE 6: no restriction to printable text. libcsv parses a
//   (pointer, length) buffer, so NUL and arbitrary bytes are ordinary field
//   data -- something an ANTLR-derived generator would otherwise never emit.
TEXT
    : ~[,\r\n]+
    ;

// CHANGE 5: CR alone, LF alone, and CRLF all terminate a row. libcsv tests
//   `c == CSV_CR || c == CSV_LF` byte by byte; CRLF terminates once because
//   the LF then lands in ROW_NOT_BEGUN, where empty rows are suppressed
//   unless CSV_REPALL_NL is set. Upstream's `'\r'? '\n'` required the LF.
TERM
    : '\r\n'
    | '\r'
    | '\n'
    ;

DELIM
    : ','
    ;

/*
 * ---------------------------------------------------------------------------
 * NEAR-MISS PATTERNS -- deliberately not grammar rules
 * ---------------------------------------------------------------------------
 *
 * These are structurally malformed, consumed anyway by non-strict libcsv, and
 * rejected with CSV_EPARSE under CSV_STRICT. They are the generator's most
 * valuable output: they drive the state machine through transitions that
 * exist only to cope with bad input, which is where a hand-written C parser
 * is most likely to be wrong.
 *
 * They are given as byte patterns rather than parser rules on purpose. With
 * the lexer above, most of them do not survive tokenization as anything
 * distinct -- `"a"b` simply lexes as one TEXT token by longest match -- so a
 * rule pretending otherwise would be fiction. A generator should emit these
 * directly, at a controlled rate, rather than deriving them.
 *
 *   unterminated quote      "abc            EOF inside a quoted field;
 *                                           csv_fini submits it anyway unless
 *                                           CSV_STRICT|CSV_STRICT_FINI
 *   text after close        "abc"junk       non-strict reopens the field
 *   space then quote        "a" "           a distinct strict-mode error site
 *   stray quote in text     ab"cd           quote literal in an unquoted field
 *   lone CR terminator      a,b\rc,d        row break upstream cannot express
 *   quote-heavy runs        """""""""       stresses FIELD_MIGHT_HAVE_ENDED
 *   long field at a         <128*k bytes>   entry_buf grows in 128-byte
 *   realloc boundary                        blocks; boundaries are where
 *                                           growth logic breaks
 *   spaces before close     "a<sp*n>"       targets `entry_pos -= spaces + 1`,
 *                                           a size_t subtraction
 */
