/*
 * probe_mxml.c -- does mxml accept this document? Evidence for README.md.
 *
 * NOT the fuzzing harness. This is a one-shot acceptance oracle used to
 * produce the gap table: it takes one document as argv[1] and prints whether
 * mxmlLoadString returned a tree, plus the first error the library reported.
 *
 * Build (from a configured mxml tree, so config.h exists):
 *   cd <mxml>; ./configure
 *   clang -std=c17 -I<mxml> -o probe grammar/probe_mxml.c <mxml>/mxml-*.c
 *   ./probe '<a>hi</a>'
 *
 * Watch the exit code, not just the word: mxml can report an error through
 * the callback AND still return a tree. See gap #9 in README.md.
 *   exit 0 = tree returned    exit 1 = NULL    exit 2 = bad usage
 */
#include <stdio.h>
#include <string.h>
#include "mxml.h"

static char errbuf[512];
static void on_error(void *cbdata, const char *message) {
    (void)cbdata;
    if (!errbuf[0]) snprintf(errbuf, sizeof errbuf, "%s", message);
}

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    errbuf[0] = '\0';
    mxml_options_t *opts = mxmlOptionsNew();
    mxmlOptionsSetErrorCallback(opts, on_error, NULL);
    mxmlOptionsSetTypeValue(opts, MXML_TYPE_TEXT);
    mxml_node_t *tree = mxmlLoadString(NULL, opts, argv[1]);
    printf("%-7s %s\n", tree ? "ACCEPT" : "REJECT", errbuf[0] ? errbuf : "");
    if (tree) mxmlDelete(tree);
    mxmlOptionsDelete(opts);
    return tree ? 0 : 1;
}
