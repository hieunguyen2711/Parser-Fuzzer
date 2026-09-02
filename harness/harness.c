/*
 * harness.c -- blackbox fuzzing driver for the target parsing library.
 *
 * Reads exactly one input, from a file named on the command line or from
 * stdin, and hands it to fuzz_one_input(). One input per process: the
 * sanitizers abort on the first fault, so the process is the unit of work.
 *
 * Usage:
 *   build/harness path/to/input      read the named file
 *   build/harness                    read stdin
 *   build/harness -                  read stdin (explicit)
 *
 * Exit codes, as the Python runner should read them:
 *   0    the library accepted the input.
 *   2    the library cleanly rejected the input. Correct behavior, not a bug,
 *        but tracked separately: the ratio of 0 to 2 is the acceptance rate
 *        the refinement loop steers on. A generator sitting at ~all-2 is
 *        being turned away at the front door and testing nothing.
 *   1    harness-level error (bad usage, unreadable file, OOM). Not a finding;
 *        the run is invalid and should be reported as such, not counted.
 *   134  killed by a sanitizer (SIGABRT). This is the finding. The report is
 *        on stderr.
 *
 * A run killed by any fatal signal is likewise a finding. So is a run the
 * parent kills at its own timeout -- the harness cannot report that about
 * itself, so enforcing the per-input timeout is the runner's job.
 *
 * Keeping these apart matters: a runner that lumps all nonzero exits together
 * will report an unreadable path, or a perfectly correct rejection, as a
 * parser bug.
 */

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fuzz_target.h"

#define EXIT_ACCEPTED 0
#define HARNESS_ERR 1
#define EXIT_REJECTED 2
#define INITIAL_CAP (64 * 1024)

static void die(const char *msg) {
    fprintf(stderr, "harness: %s\n", msg);
    exit(HARNESS_ERR);
}

/*
 * Read a stream to EOF into a heap buffer sized to the input exactly.
 *
 * The exact sizing is the point. ASan places its redzone immediately after
 * the allocation, so a parser that reads one byte past the end of the input
 * is caught. Handing the library a buffer with slack -- a fixed 64 KiB array,
 * or this buffer left at its grown capacity -- would silently absorb exactly
 * the off-by-one overreads a parser fuzzer exists to find.
 */
static uint8_t *read_all(FILE *f, size_t *out_size) {
    size_t cap = INITIAL_CAP;
    size_t size = 0;
    uint8_t *buf = malloc(cap);
    if (buf == NULL) die("out of memory");

    for (;;) {
        if (size == cap) {
            if (cap > SIZE_MAX / 2) {
                free(buf);
                die("input too large");
            }
            cap *= 2;
            uint8_t *grown = realloc(buf, cap);
            if (grown == NULL) {
                free(buf);
                die("out of memory");
            }
            buf = grown;
        }

        size_t n = fread(buf + size, 1, cap - size, f);
        size += n;

        if (n == 0) {
            if (ferror(f)) {
                free(buf);
                die("read error");
            }
            break; /* EOF */
        }
    }

    /* Shrink to fit. ASan's realloc hands back a fresh exact-sized region
     * rather than trimming in place, which is what re-tightens the redzone. */
    size_t alloc = size + (FUZZ_TARGET_WANTS_NUL_TERMINATION ? 1 : 0);
    if (alloc == 0) {
        alloc = 1; /* malloc(0) may return NULL; keep the pointer meaningful */
    }
    uint8_t *exact = realloc(buf, alloc);
    if (exact == NULL) {
        free(buf);
        die("out of memory");
    }
    if (FUZZ_TARGET_WANTS_NUL_TERMINATION) {
        exact[size] = '\0';
    }

    *out_size = size;
    return exact;
}

int main(int argc, char **argv) {
    FILE *in = stdin;

    if (argc > 2) {
        fprintf(stderr, "usage: %s [input-file]   (reads stdin if omitted)\n",
                argv[0]);
        return HARNESS_ERR;
    }

    if (argc == 2 && strcmp(argv[1], "-") != 0) {
        in = fopen(argv[1], "rb");
        if (in == NULL) {
            fprintf(stderr, "harness: cannot open %s: %s\n", argv[1],
                    strerror(errno));
            return HARNESS_ERR;
        }
    }

    size_t size = 0;
    uint8_t *data = read_all(in, &size);
    if (in != stdin) {
        fclose(in);
    }

    /* If the library faults, a sanitizer ends the process inside this call and
     * main never returns. A rejection is not a fault -- it just exits 2. */
    int verdict = fuzz_one_input(data, size);

    free(data);
    return (verdict == FUZZ_ACCEPTED) ? EXIT_ACCEPTED : EXIT_REJECTED;
}
