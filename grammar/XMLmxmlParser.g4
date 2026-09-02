/*
 * XMLmxmlParser.g4 -- the grammars-v4 XML parser grammar, adapted to
 * mxml v4.0.5. Derived from XMLParser.g4 (BSD); see PROVENANCE.txt.
 *
 * READ THIS BEFORE GENERATING FROM IT. Roughly half of what makes a document
 * acceptable to mxml is NOT context-free and therefore cannot appear below.
 * Those constraints are listed at the bottom of this file and must be enforced
 * by the generator in code. A generator that satisfies only this grammar will
 * be rejected by mxml most of the time -- and the acceptance rate will say so.
 */

parser grammar XMLmxmlParser;

options {
    tokenVocab = XMLmxmlLexer;
}

// CHANGE P1: no misc before or after the root element. Upstream is
//   `prolog? misc* element misc* EOF`, which permits leading and trailing
//   comments, PIs and whitespace. mxml rejects all of them:
//     "  <a/>"        -> XML does not start with '<' (saw ' ')
//     "<!--c--><a/>"  -> <a> cannot be a second root node after <c>
//     "<a/><!--c-->"  -> <!--c--> cannot be a second root node after <a>
//   all [PROBE]. mxml treats a leading comment as the first root node, so the
//   real element that follows becomes an illegal second root. This is
//   stricter than the XML specification, and it is the single largest
//   difference between the formal grammar and this library.
// CHANGE P2: a declaration may precede the root -- "<!DOCTYPE a><a/>" is
//   accepted [PROBE] -- because declarations do not count as a root node.
// CHANGE P3: trailing whitespace is allowed ("<a/>  " accepted [PROBE]),
//   an asymmetry with leading whitespace that is worth generating both sides of.
document
    : prolog? DECLARATION* element SEA_WS? EOF
    ;

prolog
    : XMLDeclOpen attribute* SPECIAL_CLOSE
    ;

content
    : chardata? ((element | reference | CDATA | PI | COMMENT | DECLARATION) chardata?)*
    ;

element
    : OPEN Name attribute* CLOSE content OPEN SLASH Name CLOSE
    | OPEN Name attribute* SLASH_CLOSE
    ;

reference
    : EntityRef
    | CharRef
    ;

// CHANGE P4: the value may be unquoted -- "<a b=c/>" is accepted [PROBE].
//   Name appears as an alternative because an identifier-shaped unquoted
//   value lexes as Name, not UNQUOTED_VALUE; see the ordering note in the
//   lexer grammar.
attribute
    : Name EQUALS (STRING | UNQUOTED_VALUE | Name)
    ;

chardata
    : TEXT
    | SEA_WS
    ;

/*
 * ---------------------------------------------------------------------------
 * NON-CONTEXT-FREE CONSTRAINTS -- the generator must enforce these itself
 * ---------------------------------------------------------------------------
 *
 * Each is a rule mxml enforces that no context-free grammar can express. All
 * are verified [PROBE] against mxml v4.0.5. A Hypothesis strategy can satisfy
 * every one of them by construction, which is precisely the advantage a
 * programmatic generator has over deriving strings from a grammar.
 *
 *  N1. OPEN AND CLOSE NAMES MUST MATCH.
 *      `element` above has two independent `Name` tokens, so `<a></b>` is
 *      derivable -- and upstream has the same hole. mxml rejects it:
 *      "Mismatched close tag </b> under parent <a>". A strategy should
 *      generate the name once and reuse it, then emit mismatches only as
 *      deliberate near-misses.
 *
 *  N2. ATTRIBUTE NAMES MUST BE UNIQUE WITHIN AN ELEMENT.
 *      `<a x="1" x="2"/>` is derivable from `attribute*`; mxml rejects it
 *      with "Duplicate attribute 'x'".
 *
 *  N3. ENTITY NAMES ARE A CLOSED SET.
 *      `EntityRef : '&' Name ';'` admits any name. mxml supports exactly
 *      amp, apos, gt, lt, quot, plus numeric character references, plus
 *      whatever a user callback adds [SRC mxml-options.c]. `&foo;` is
 *      rejected: "Entity '&foo;' not supported".
 *
 *  N4. EXACTLY ONE ROOT NODE, COUNTING COMMENTS, CDATA AND PIs.
 *      See CHANGE P1. Partly expressed above, but the rule mxml actually
 *      applies -- "the first top-level node of any kind claims root, and
 *      any later one is an error" -- is a stateful check, not a shape.
 *
 * ---------------------------------------------------------------------------
 * NEAR-MISS PATTERNS worth generating deliberately
 * ---------------------------------------------------------------------------
 *
 *   <a></b>                mismatched close        rejected
 *   <a x="1" x="2"/>       duplicate attribute     rejected
 *   &foo;                  unknown entity          rejected
 *   <!--c--><a/>           leading comment         rejected
 *   <a/><!--c-->           trailing comment        rejected
 *   "  <a/>"               leading whitespace      rejected
 *   <a <b/>                bare < in element       rejected
 *   <a b/>                 attribute with no value rejected
 *   <!--x                  truncated comment       rejected
 *   <a                     truncated element       ACCEPTED, silently [PROBE]
 *   <a>\x01</a>            control character       ACCEPTED, with an error
 *                                                  reported -- see gap #9
 *   <a>\xc3\x28</a>        invalid UTF-8           ACCEPTED, silently [PROBE]
 *
 * The last three are the interesting ones: mxml's idea of "accepted" does not
 * line up with "no error reported", and the generator should be producing
 * inputs on both sides of that line.
 */
