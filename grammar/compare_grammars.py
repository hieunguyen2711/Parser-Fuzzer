"""Three-way evidence for the gap table in README.md.

For each sample document, report whether it is derivable from the upstream
grammars-v4 XML grammar, derivable from the mxml-adapted grammar, and accepted
by mxml itself. The third column is the ground truth; the first two are models
of it, and every disagreement is a documented gap.

Not part of the pytest suite: it needs the ANTLR tool (a Java jar), generated
parsers, and a compiled mxml probe. Run it by hand when the analysis needs
re-checking -- notably if the assignment pins an mxml version other than
v4.0.5.

    SP=$(mktemp -d)
    # 1. generated parsers (lexer first; the parser needs its .tokens)
    java -jar antlr.jar -o "$SP/up" -Dlanguage=Python3 grammar/XMLLexer.g4
    java -jar antlr.jar -o "$SP/up" -lib "$SP/up/grammar" -Dlanguage=Python3 grammar/XMLParser.g4
    java -jar antlr.jar -o "$SP/ad" -Dlanguage=Python3 grammar/XMLmxmlLexer.g4
    java -jar antlr.jar -o "$SP/ad" -lib "$SP/ad/grammar" -Dlanguage=Python3 grammar/XMLmxmlParser.g4
    # 2. the acceptance probe, from a configured mxml tree
    clang -std=c17 -Itarget/mxml -o "$SP/probe" grammar/probe_mxml.c target/mxml/mxml-*.c
    # 3. run
    python3 grammar/compare_grammars.py "$SP/up/grammar" "$SP/ad/grammar" "$SP/probe"
"""

import subprocess
import sys

sys.path[:0] = [sys.argv[1], sys.argv[2]]

from antlr4 import CommonTokenStream, InputStream
from antlr4.error.ErrorListener import ErrorListener

from XMLLexer import XMLLexer
from XMLmxmlLexer import XMLmxmlLexer
from XMLmxmlParser import XMLmxmlParser
from XMLParser import XMLParser

PROBE = sys.argv[3]


class Boom(Exception):
    pass


class Strict(ErrorListener):
    def syntaxError(self, *a):
        raise Boom(a[4])


def derivable(lexer_cls, parser_cls, text):
    try:
        lx = lexer_cls(InputStream(text))
        lx.removeErrorListeners()
        lx.addErrorListener(Strict())
        ps = parser_cls(CommonTokenStream(lx))
        ps.removeErrorListeners()
        ps.addErrorListener(Strict())
        ps.document()
        return True
    except Exception:
        return False


def mxml_accepts(text):
    """True if mxmlLoadString returned a tree. Note this is NOT the same as
    'no error reported' -- see gap #9."""
    return subprocess.run([PROBE, text], capture_output=True).returncode == 0


# (label, document, expected-divergence reason or None)
#
# A divergence is only a defect in the adaptation if it is unexplained. Three
# of mxml's rules cannot be written in ANY context-free grammar, so no
# adaptation can close them; they are enforced by the generator instead, and
# are listed as N1-N3 in XMLmxmlParser.g4. Labelling those "STILL DIVERGES"
# would imply a fixable modelling error, which they are not.
CASES = [
    ("plain element",          "<a>hi</a>", None),
    ("self-closing",           "<a/>", None),
    ("nested",                 "<a><b/></a>", None),
    ("xml declaration",        '<?xml version="1.0"?><a/>', None),
    ("CDATA",                  "<a><![CDATA[x<y]]></a>", None),
    ("trailing whitespace",    "<a/>  ", None),
    ("DTD then root",          "<!DOCTYPE a><a/>", None),
    ("leading whitespace",     "  <a/>", None),
    ("leading comment",        "<!--c--><a/>", None),
    ("trailing comment",       "<a/><!--c-->", None),
    ("mismatched tags",        "<a></b>", "N1 tag names must match"),
    ("duplicate attribute",    '<a x="1" x="2"/>', "N2 attribute names unique"),
    ("undefined entity",       "<a>&foo;</a>", "N3 entity set is closed"),
    ("unquoted attr value",    "<a b=c/>", None),
    ("truncated element",      "<a", "mxml accepts truncated input"),
    ("control char in text",   "<a>\x01</a>", None),
    ("empty input",            "", None),
    ("text before root",       "x<a/>", None),
]

def mark(b):
    return "yes" if b else "no"


print(f"{'case':24} {'upstr':>6} {'adapt':>6} {'mxml':>6}   note")
print("-" * 78)
unexplained = 0
for name, text, expected in CASES:
    up = derivable(XMLLexer, XMLParser, text)
    ad = derivable(XMLmxmlLexer, XMLmxmlParser, text)
    mx = mxml_accepts(text)

    if ad == mx:
        note = "gap closed" if up != mx else ""
    elif expected:
        note = f"diverges, expected: {expected}"
    else:
        note = "UNEXPLAINED -- adaptation is wrong"
        unexplained += 1

    print(f"{name:24} {mark(up):>6} {mark(ad):>6} {mark(mx):>6}   {note}")

print()
print(f"unexplained divergences: {unexplained}")
sys.exit(1 if unexplained else 0)
