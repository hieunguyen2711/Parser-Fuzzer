"""Evidence for the gap table in README.md: parse the same inputs with both
grammars and show where they disagree.

Not part of the pytest suite -- it needs the ANTLR tool (a ~2MB Java jar) and
generates parsers, neither of which belongs in the fuzzing loop's dependencies.
Run it by hand when the gap analysis needs re-checking, e.g. after the pinned
libcsv commit is known.

    JAR=/path/to/antlr-4.13.2-complete.jar        # antlr.org/download
    OUT=$(mktemp -d)
    python3 -m venv "$OUT/venv" && "$OUT/venv/bin/pip" install antlr4-python3-runtime==4.13.2
    java -jar "$JAR" -o "$OUT/gen/upstream" -Dlanguage=Python3 grammar/CSV.g4
    java -jar "$JAR" -o "$OUT/gen/adapted"  -Dlanguage=Python3 grammar/CSVlibcsv.g4
    "$OUT/venv/bin/python" grammar/compare_grammars.py "$OUT"

A "gap" row is an input libcsv handles that the upstream grammar cannot derive.
A "REGRESSION" row would mean the adaptation lost something upstream accepted;
there should never be one.
"""
import sys
sys.path[:0] = [f"{sys.argv[1]}/gen/upstream/grammar", f"{sys.argv[1]}/gen/adapted/grammar"]
from antlr4 import InputStream, CommonTokenStream
from antlr4.error.ErrorListener import ErrorListener
from CSVLexer import CSVLexer
from CSVParser import CSVParser
from CSVlibcsvLexer import CSVlibcsvLexer
from CSVlibcsvParser import CSVlibcsvParser

class Boom(Exception): pass
class Strict(ErrorListener):
    def syntaxError(self, *a): raise Boom(a[4])

def parses(lexer_cls, parser_cls, rule, text):
    try:
        lx = lexer_cls(InputStream(text)); lx.removeErrorListeners(); lx.addErrorListener(Strict())
        ps = parser_cls(CommonTokenStream(lx)); ps.removeErrorListeners(); ps.addErrorListener(Strict())
        getattr(ps, rule)()
        return True
    except Exception:
        return False

CASES = [
    ("single row, no header",      "a,b\n"),
    ("no trailing newline",        "a,b"),
    ("empty input",                ""),
    ("lone CR terminator",         "a,b\rc,d\n"),
    ("stray quote in text",        'ab"cd\n'),
    ("text after closing quote",   '"abc"junk\n'),
    ("unterminated quote",         '"abc\n'),
    ("NUL byte in field",          "a\x00b,c\n"),
    ("--- both should accept ---", None),
    ("two rows, quoted",           '"h1","h2"\nv1,v2\n'),
    ("empty fields",               "a,,b\nc,,d\n"),
    ("quoted with comma",          '"a,b",c\nd,e\n'),
    ("escaped quote",              '"a""b",c\nd,e\n'),
    ("multiline quoted field",     '"a\nb",c\nd,e\n'),
]
print(f"{'case':30} {'upstream':>10} {'adapted':>9}")
print("-" * 51)
for name, text in CASES:
    if text is None:
        print(f"{name:30}"); continue
    up = parses(CSVLexer, CSVParser, "csvFile", text)
    ad = parses(CSVlibcsvLexer, CSVlibcsvParser, "csvFile", text)
    flag = "  <-- gap" if (ad and not up) else ("  <-- REGRESSION" if (up and not ad) else "")
    print(f"{name:30} {'accept' if up else 'REJECT':>10} {'accept' if ad else 'REJECT':>9}{flag}")
