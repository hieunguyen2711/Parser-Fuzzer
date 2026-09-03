"""Prompts for the agentic loop. This file currently holds only the seed.

The seed prompt (assignment Step 4.1) is the one shot that has no feedback to
work from: iteration 1 sees the grammar and nothing else. Everything after it
gets to react to real numbers, so the seed is the prompt that most needs to
front-load knowledge the model cannot otherwise have -- specifically the ways
mxml departs from the formal XML grammar, which were expensive to establish
and which no amount of general XML knowledge would supply.

The grammar is read from grammar/ rather than pasted here, so there is exactly
one copy of it. Editing the .g4 files changes the prompt.

    python -m fuzzer.prompts        # print the seed prompt and its size
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRAMMAR_DIR = ROOT / "grammar"

SYSTEM = """\
You write Hypothesis strategies for grammar-based fuzzing of C parsers.

You are precise about grammars: when asked to generate a language, you
reproduce its actual structure rather than approximating it with random text.
You know that a generator whose output the parser rejects at the first byte
tests nothing, and that a generator which emits the same document repeatedly
tests almost as little.

You return runnable code and no commentary around it."""


# Everything mxml does that the formal grammar does not describe. Each line was
# verified by compiling mxml v4.0.5 and running the input; see grammar/README.md.
# This is the part of the prompt that cannot be reconstructed from general XML
# knowledge, and it is why the seed is worth writing carefully.
BEHAVIOR_NOTES = """\
The target is mxml v4.0.5, a small C XML library. It is NOT a conformant XML
processor, and the differences decide whether your generated documents get
past its front door. All of the following were verified by running it:

REJECTED, though the formal grammar allows them:
  "  <a/>"            leading whitespace. The very first byte must be '<'.
                      Error: "XML does not start with '<'"
  "<!--c--><a/>"      a comment BEFORE the root element. mxml counts the
                      comment as the root node, so the element that follows
                      is an illegal second root.
  "<a/><!--c-->"      a comment AFTER the root element, same reason.
  "<a></b>"           open and close names must match.
  '<a x="1" x="2"/>'  attribute names must be unique within an element.
  "<a>&foo;</a>"      only these entities exist: &amp; &apos; &gt; &lt; &quot;
                      plus numeric refs (&#65; &#x41;). Anything else is an
                      error.
  "<a b/>"            an attribute must have a value.
  "<a <b/>"           a bare '<' inside a tag.
  "<!--x"             a truncated comment.
  ""                  empty input.

ACCEPTED, though the formal grammar forbids them:
  "<a b=c/>"          attribute values need not be quoted.
  "<a"                a truncated element, silently, with no error at all --
                      while a truncated comment is a hard error. The
                      truncation handling is inconsistent, which makes it
                      worth probing.

ACCEPTED with a diagnostic (a tree is still returned):
  "<a>\\x01</a>"       control characters draw "Bad control character" but the
                      document still parses.

Also true:
  - "<a/>  " is fine: trailing whitespace is allowed even though leading is not.
  - "<!DOCTYPE a><a/>" is fine: a declaration before the root does not count
    as a root node.
  - Quoted text, CDATA and comments may contain newlines.
  - Nesting depth is not a weak point: 50,000 nested elements parse without
    incident, so very deep nesting is a poor thing to spend generation on.
"""


HARD_REQUIREMENTS = """\
Requirements on the code you return:

1. RECURSION MUST BE REAL RECURSION. XML elements nest, so use
   `st.recursive(...)` or a `@st.composite` function that calls itself.
   Do NOT flatten the grammar into a fixed set of depth-1 and depth-2
   templates. A flattened generator cannot produce the depth distribution the
   grammar implies, and the whole point of starting from a grammar is lost.
   Bound the depth (roughly 1-6 is useful here) so generation stays fast.

2. ENFORCE IN CODE WHAT THE GRAMMAR CANNOT EXPRESS. Three of mxml's rules are
   not context-free, so the .g4 files below do not capture them and you must:
     - generate an element's name ONCE and reuse it for the closing tag;
     - draw attribute names WITHOUT replacement within one element;
     - draw entity references only from the five mxml supports, or emit
       numeric character references.
   This is the main advantage a programmatic generator has over deriving
   strings from a grammar. Use it.

3. MOST OUTPUT MUST BE WELL-FORMED, with a deliberate minority malformed.
   Aim for roughly 70-85% of documents that mxml accepts. Documents rejected
   at the first byte never reach the tree-building and accessor code where
   memory-safety bugs live. Make the malformed fraction a module-level
   constant so it can be tuned later.

4. COVER THESE EDGE CASES EXPLICITLY, each reachable with non-trivial
   probability:
     - empty elements: <a/> and <a></a>, and empty attribute values
     - deep-ish nesting, up to the bound in (1)
     - duplicate attribute names (a deliberate near-miss; mxml rejects these)
     - numeric character references at the extremes: &#0;, &#x10FFFF;,
       many-digit values, and malformed ones
     - unicode and escaping: non-ASCII text and element names, all five named
       entities, CDATA containing '<' and '&', text containing raw control
       characters
     - near-valid-but-malformed documents drawn from the REJECTED list above,
       and the two odd ACCEPTED cases
     - mixed content: elements containing text, child elements, comments,
       CDATA and processing instructions interleaved

5. DETERMINISM. Every random choice must come from Hypothesis draws. Do not
   import or call `random`, do not use the clock, do not read files or the
   network. Anything else breaks shrinking, and an unshrinkable crash is a
   much weaker bug report.
"""


OUTPUT_CONTRACT = """\
Return exactly one Python code block and nothing else -- no explanation before
or after it. The block must be a complete, self-contained module that:

  - imports only `hypothesis` and the Python standard library;
  - defines a module-level name `STRATEGY` bound to a strategy producing `str`;
  - defines nothing that runs at import time beyond building strategies (no
    prints, no I/O, no example generation);
  - carries brief comments naming which grammar production or which mxml
    behavior each part corresponds to.

The harness encodes your strings as UTF-8 with errors="surrogatepass", so a
lone surrogate is a legitimate way to emit deliberately invalid UTF-8.
"""


def _read(name: str) -> str:
    return (GRAMMAR_DIR / name).read_text(encoding="utf-8").strip()


def seed_prompt() -> str:
    """Assemble the iteration-1 prompt: grammar + adaptations + requirements."""
    return f"""\
Write a Hypothesis strategy that generates XML documents in the language of \
the grammar below, targeting the mxml C library.

The grammar is the ANTLR grammars-v4 XML grammar, adapted to match what mxml \
actually accepts. It is split into a lexer and a parser grammar because XML \
needs lexer modes.

===== LEXER GRAMMAR (XMLmxmlLexer.g4) =====
{_read("XMLmxmlLexer.g4")}

===== PARSER GRAMMAR (XMLmxmlParser.g4) =====
{_read("XMLmxmlParser.g4")}

===== HOW mxml DIFFERS FROM THE FORMAL GRAMMAR =====
{BEHAVIOR_NOTES}
===== REQUIREMENTS =====
{HARD_REQUIREMENTS}
===== OUTPUT =====
{OUTPUT_CONTRACT}"""


def main() -> int:
    prompt = seed_prompt()
    print(prompt)
    print("\n" + "=" * 70)
    chars = len(prompt) + len(SYSTEM)
    print(f"system + prompt: {chars} chars (~{chars // 4} tokens, rough)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
