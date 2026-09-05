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
   The base case must be a REAL minimal element with a freshly drawn name,
   e.g. `<{name}/>`, never a fixed placeholder like `st.just('<base/>')`. A
   literal placeholder leaks the same dummy tag into output over and over and
   wastes the run.

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

   Keep the WELL-FORMED core clean so it actually parses: bias element and
   attribute names toward ASCII (`[A-Za-z_][A-Za-z0-9_.-]*`), and keep text
   and attribute values free of raw control characters. The risky material --
   raw control characters, lone surrogates, exotic or out-of-range codepoints,
   unusual Unicode name characters -- belongs to the deliberate malformed/edge
   minority in requirement 4, NOT sprinkled through every document. Spreading
   it everywhere is what drags acceptance below the target band.

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


# The Hypothesis API mistakes below were each made by a prior generation of this
# same prompt and had to be hand-corrected before the module would even load.
# Naming them explicitly is far cheaper than paying an iteration to discover
# them again. Every item is a real fix, not a hypothetical.
HYPOTHESIS_API = """\
HYPOTHESIS API -- avoid these specific mistakes. Every one of them has been
made before and stopped the module from loading or running:

  - Strategies do NOT support `+`. To join two generated strings, use
    `st.tuples(a, b).map("".join)`. `a | b` means "either a or b", not
    "a followed by b"; there is no operator for concatenation.
  - `@st.composite` turns a function into a strategy FACTORY, not a strategy.
    You must CALL it: write `st_thing()` inside `st.one_of(...)` or a `|`
    chain, never a bare `st_thing`.
  - `st.recursive(base, extend, max_leaves=N)` -- the size bound is
    `max_leaves`. There is NO `max_depth` argument.
  - `st.booleans()` takes NO weighting argument -- no `average_value`, no `p`.
    For a biased coin write a helper, e.g.
    `st.floats(0, 1).map(lambda x: x < p)` or
    `st.integers(0, 999).map(lambda n: n < round(p * 1000))`.
  - `st.integers(min_value=..., max_value=...)` -- there is no `max_size` on
    integers; `max_size` belongs to `st.text`/`st.lists`.
  - `.filter(pred)` returns a NEW strategy; it does not filter an
    already-drawn value. Write `draw(s.filter(pred))`, never
    `draw(s).filter(pred)`.
  - There is no `st.shuffled`. To reorder a list use
    `draw(st.permutations(items))` and USE the returned list.
  - Inside an `@st.composite` function, every generated value must come
    through `draw(...)`. A bare strategy object used as if it were its value
    is always a bug.
  - There is no `st.weighted_choices`, no `st.weighted`, and no `weights=`
    argument on `st.sampled_from`. To choose WITH weights, draw and branch
    yourself: pick `items[draw(st.integers(0, len(items) - 1))]` for a uniform
    choice, or map a `draw(st.integers(0, 999))` onto cumulative weight
    thresholds for a biased one.
  - A built strategy is NOT callable. `st.recursive(...)`, `st.text()`,
    `st.one_of(...)` and the like each evaluate to a strategy OBJECT; get a
    value from it with `draw(s)`, never `s()`. Only a function you decorated
    with `@st.composite` is meant to be called. The error
    `'...Strategy' object is not callable` always means you wrote `s()`.
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
===== HYPOTHESIS API PITFALLS =====
{HYPOTHESIS_API}
===== OUTPUT =====
{OUTPUT_CONTRACT}"""


# Iterations 2-5 (assignment Step 4.4). Unlike the seed, the refine prompt has
# real numbers to react to, so it front-loads the LAST run's behavior and the
# previous module rather than the grammar -- the grammar's structure is already
# embedded in that module, and re-sending both .g4 files every iteration would
# waste the token budget the loop is trying to preserve. The mxml behavior notes
# and the Hypothesis API pitfalls DO carry over: they are the two things the
# model still cannot derive from its own previous output.
REFINE_INTRO = """\
You already wrote a Hypothesis strategy that generates XML documents for the
mxml C library. Below is how your PREVIOUS version actually behaved when its
output was fed to mxml, then the previous module itself. Revise it.

The goal is NOT 100% acceptance. Aim for 70-85% of documents accepted: a
generator that emits only `<a/>` scores 100% and tests nothing, so a deliberate
malformed minority is wanted. What matters just as much is that the ACCEPTED
documents VARY -- in nesting depth, number of attributes, and the mix of text,
child elements, CDATA, comments, and references -- because variety, not raw
acceptance, is what drives the parser into new code.

Do NOT regress. The score history below shows the best you have reached; the
module shown to you is the last one that WORKED. Change it incrementally. If a
previous edit lowered acceptance or score, that edit was wrong -- undo it and
build on the higher-scoring version rather than layering new changes on a
regression. A large rewrite that collapses acceptance is worse than a small,
safe adjustment."""


def _format_trajectory(trajectory: list) -> str:
    """Render the score history so the model can see whether it is improving."""
    if not trajectory:
        return ""
    rows = ["", "===== SCORE HISTORY SO FAR =====",
            "  iter   score   acceptance"]
    for iteration, score, acceptance in trajectory:
        rows.append(f"  {iteration:<5d}  {score:>5.2f}   {acceptance:>7.0%}")
    rows.append(
        "(higher score is better: it rewards landing in the 70-85% acceptance "
        "band AND producing structurally varied documents, and penalises both "
        "over-rejection and monotonous output.)")
    return "\n".join(rows)


def refine_prompt(prev_code: str, feedback: str, trajectory: list) -> str:
    """Assemble an iteration 2-5 prompt from the last run's behavior.

    `feedback` is the Python-computed briefing (validate.brief() + summarize()),
    so no separate LLM "optimizer" call is spent producing the critique. The two
    PromptAgent framings -- reflect on the error, then transition to a new prompt
    -- are folded into this single message to stay within the call budget.
    """
    return f"""\
{REFINE_INTRO}

===== HOW YOUR LAST STRATEGY BEHAVED =====
{feedback}
{_format_trajectory(trajectory)}

===== HOW mxml DIFFERS FROM THE FORMAL GRAMMAR (unchanged) =====
{BEHAVIOR_NOTES}
===== YOUR PREVIOUS STRATEGY (revise this whole module) =====
```python
{prev_code}
```

===== WHAT TO DO =====
Diagnose, from the numbers above, WHY documents were rejected or why output was
repetitive -- then rewrite the module to fix precisely that, keeping the parts
that already worked. Return the complete revised module, not a diff.

===== HYPOTHESIS API PITFALLS =====
{HYPOTHESIS_API}
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
