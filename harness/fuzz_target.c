/*
 * fuzz_target.c -- the only file in harness/ that knows the target library.
 *
 * Target: mxml v4.0.5 (18d5c7d), built from source at target/mxml so the
 * sanitizers instrument it. harness.c is library-agnostic and needs no edits.
 *
 * Rules that keep the results honest:
 *
 *  1. Free everything the library allocates. LeakSanitizer does not run on
 *     macOS, so leaks are not reported here -- and do not set
 *     ASAN_OPTIONS=detect_leaks=1 to try: on Darwin that makes ASan hard-fail
 *     with exit 134 on every input. Free anyway; it keeps long runs from
 *     exhausting memory and the leaks do get caught if this runs on Linux.
 *
 *  2. Consume the parse result. mxml builds the whole tree eagerly, but the
 *     accessors (mxmlGetText, mxmlElementGetAttrByIndex, ...) have their own
 *     pointer arithmetic, and an input that is only parsed and freed never
 *     touches it. walk_tree() below exists for this reason alone.
 *
 *  3. Print nothing on the clean-accept path. One short line on rejection or
 *     diagnostic is wanted -- the loop clusters those to see what mxml objects
 *     to -- but per-input output at fuzzing rates otherwise dominates runtime.
 *
 *  4. Report the verdict honestly. Malformed input is the point; mxml
 *     refusing it is correct behavior, not a finding. Only the sanitizers
 *     decide what is a fault.
 */

#include <stdio.h>
#include <string.h>

#include "fuzz_target.h"
#include "mxml.h"

/*
 * Feeding mxml the input.
 *
 * mxmlLoadString would be the obvious call, but it takes a NUL-terminated C
 * string, which would cost two things worth keeping. First, the driver would
 * have to allocate size+1 bytes, moving ASan's redzone one byte past the end
 * of the real input and blinding it to exactly the off-by-one overreads this
 * project exists to find. Second, a generated document containing a NUL byte
 * would be silently truncated at that byte, so an entire class of input could
 * never be tested.
 *
 * mxmlLoadIO takes a read callback instead, so the exact (pointer, length)
 * buffer is handed over with no copy, no terminator, and no truncation.
 * FUZZ_TARGET_WANTS_NUL_TERMINATION therefore stays 0 in fuzz_target.h.
 */
typedef struct {
    const uint8_t *data;
    size_t size;
    size_t pos;
} input_cursor;

static size_t read_input(void *cbdata, void *buffer, size_t bytes) {
    input_cursor *cursor = (input_cursor *)cbdata;
    size_t remaining = cursor->size - cursor->pos;
    size_t take = bytes < remaining ? bytes : remaining;

    if (take > 0) {
        memcpy(buffer, cursor->data + cursor->pos, take);
        cursor->pos += take;
    }
    return take;
}

/*
 * Capturing what mxml complains about.
 *
 * mxml reports problems through a callback rather than a return value, and --
 * this is the part that shapes the exit-code contract -- it can report a
 * problem AND still return a tree. `<a>\x01</a>` yields a document plus "Bad
 * control character 0x01 not allowed by XML standard". So there are three
 * outcomes, not two:
 *
 *   tree, silent    -> accepted            exit 0
 *   NULL, message   -> cleanly rejected    exit 2
 *   tree, message   -> accepted, with a diagnostic logged   exit 0
 *
 * The third is a judgment call, documented rather than hidden. It counts as
 * accepted because mxml did produce a document, and because folding it into
 * "rejected" would mix "mxml is fussy about control characters" together with
 * "the generator emits garbage" in the one number the refinement loop steers
 * on -- and send the loop off fixing a generator that was working. The
 * diagnostic is still logged, so the loop can see it separately.
 */
static char first_diagnostic[256];

static void on_error(void *cbdata, const char *message) {
    (void)cbdata;
    if (first_diagnostic[0] == '\0') {
        snprintf(first_diagnostic, sizeof first_diagnostic, "%s", message);
    }
}

/* Rule 2: touch every node so the accessors run, not just the parser. */
static void walk_tree(mxml_node_t *tree) {
    mxml_node_t *node = tree;

    while (node != NULL) {
        switch (mxmlGetType(node)) {
            case MXML_TYPE_ELEMENT: {
                (void)mxmlGetElement(node);
                size_t count = mxmlElementGetAttrCount(node);
                for (size_t i = 0; i < count; i++) {
                    const char *name = NULL;
                    (void)mxmlElementGetAttrByIndex(node, i, &name);
                }
                break;
            }
            case MXML_TYPE_TEXT: {
                bool whitespace = false;
                (void)mxmlGetText(node, &whitespace);
                break;
            }
            case MXML_TYPE_OPAQUE:
                (void)mxmlGetOpaque(node);
                break;
            case MXML_TYPE_CDATA:
                (void)mxmlGetCDATA(node);
                break;
            case MXML_TYPE_COMMENT:
                (void)mxmlGetComment(node);
                break;
            default:
                break;
        }
        node = mxmlWalkNext(node, tree, MXML_DESCEND_ALL);
    }
}

int fuzz_one_input(const uint8_t *data, size_t size) {
    first_diagnostic[0] = '\0';

    mxml_options_t *options = mxmlOptionsNew();
    if (options == NULL) {
        /* Allocation failure in the harness, not a finding in the library.
         * Say so loudly rather than scoring it as a clean parse. */
        fprintf(stderr, "harness: mxmlOptionsNew failed\n");
        return FUZZ_REJECTED;
    }

    mxmlOptionsSetErrorCallback(options, on_error, NULL);

    /* MXML_TYPE_TEXT is chosen, not inherited. The type option changes the
     * accepted language: under MXML_TYPE_INTEGER or MXML_TYPE_REAL, mxml runs
     * every text node through a numeric conversion and rejects what will not
     * parse ("Bad integer value ..."), which would make acceptance rate a
     * measure of how often the generator emits numbers. TEXT keeps the
     * acceptance signal about document structure, which is what the grammar
     * describes and what the loop is trying to steer. */
    mxmlOptionsSetTypeValue(options, MXML_TYPE_TEXT);

    input_cursor cursor = {data, size, 0};
    mxml_node_t *tree = mxmlLoadIO(NULL, options, read_input, &cursor);

    int verdict;
    if (tree == NULL) {
        fprintf(stderr, "reject: %s\n",
                first_diagnostic[0] ? first_diagnostic : "(no message)");
        verdict = FUZZ_REJECTED;
    } else {
        walk_tree(tree);
        if (first_diagnostic[0] != '\0') {
            fprintf(stderr, "diag: %s\n", first_diagnostic);
        }
        mxmlDelete(tree);           /* rule 1 */
        verdict = FUZZ_ACCEPTED;
    }

    mxmlOptionsDelete(options);     /* rule 1 */
    return verdict;
}
