/*
 * XMLmxmlLexer.g4 -- the grammars-v4 XML lexer, adapted to mxml v4.0.5.
 * Derived from XMLLexer.g4 (BSD); see PROVENANCE.txt. README.md explains
 * every change and shows the evidence.
 *
 * Kept as a separate lexer grammar because of the modes, which ANTLR allows
 * only outside a combined grammar.
 */

lexer grammar XMLmxmlLexer;

COMMENT : '<!--' .*? '-->';
CDATA   : '<![CDATA[' .*? ']]>';

// CHANGE L1: emit the declaration instead of discarding it. Upstream has
//   `DTD : '<!' .*? '>' -> skip`, which throws DTDs away wholesale. mxml
//   parses `<!...>` into a real MXML_TYPE_DECLARATION node, and a truncated
//   one is a hard error ("Early EOF in declaration node") [SRC mxml-file.c].
//   A generator that cannot emit declarations never reaches that code.
DECLARATION : '<!' .*? '>';

EntityRef : '&' Name ';';
CharRef   : '&#' DIGIT+ ';' | '&#x' HEXDIGIT+ ';';
SEA_WS    : (' ' | '\t' | '\r'? '\n')+;

OPEN         : '<'       -> pushMode(INSIDE);
XMLDeclOpen  : '<?xml' S -> pushMode(INSIDE);
SPECIAL_OPEN : '<?' Name -> more, pushMode(PROC_INSTR);

TEXT: ~[<&]+;

mode INSIDE;

CLOSE         : '>'  -> popMode;
SPECIAL_CLOSE : '?>' -> popMode;
SLASH_CLOSE   : '/>' -> popMode;
SLASH         : '/';
EQUALS        : '=';

STRING: '"' ~[<"]* '"' | '\'' ~[<']* '\'';

Name : NameStartChar NameChar*;

// CHANGE L2: mxml accepts unquoted attribute values -- `<a b=c/>` parses
//   [PROBE]. It reads until whitespace, '=', '/' or '>' [SRC mxml-file.c
//   "Read unquoted value..."]. Upstream's STRING requires quotes, so this
//   syntax was underivable.
//
//   ORDER IS LOad-BEARING and it cost me a broken grammar to learn it: this
//   rule must come AFTER Name. ANTLR breaks equal-length matches by
//   declaration order, and `a` in `<a/>` is matched at length 1 by both. With
//   this rule first, every element name lexes as an unquoted value and the
//   grammar rejects even `<a/>`. Because Name now wins those ties, an
//   identifier-shaped unquoted value (`b=c`) arrives as a Name token, which
//   is why the parser's `attribute` rule accepts Name here too.
UNQUOTED_VALUE: ~[ \t\r\n=/><"']+;

S : [ \t\r\n] -> skip;

fragment HEXDIGIT: [a-fA-F0-9];

fragment DIGIT: [0-9];

fragment NameChar:
    NameStartChar
    | '-'
    | '.'
    | DIGIT
    | '·'
    | '̀' ..'ͯ'
    | '‿' ..'⁀'
;

fragment NameStartChar:
    [_:a-zA-Z]
    | '⁰' ..'↏'
    | 'Ⰰ' ..'⿯'
    | '、' ..'퟿'
    | '豈' ..'﷏'
    | 'ﷰ' ..'�'
;

mode PROC_INSTR;

PI          : '?>' -> popMode;
IGNORE_CHAR : . -> more;
