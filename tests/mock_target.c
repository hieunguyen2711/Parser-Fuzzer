/*
 * mock_target.c -- a fake parser, for testing the pipeline and nothing else.
 *
 * This is NOT the target library and never ships as part of a result. It is a
 * stand-in that produces every outcome the runner must classify, on demand, so
 * the Python side can be validated before the real library is in target/.
 * Once fuzz_target.c is wired to the real parser, this file's only remaining
 * job is regression-testing the runner.
 *
 * Behavior, keyed off the input bytes:
 *   contains "CRASH"     -- heap-buffer-overflow, ASan aborts (SIGABRT).
 *                           The buffer is sized from the input and allocated
 *                           in another function on purpose: a constant-size
 *                           malloc next to the fault lets UBSan's
 *                           type-mismatch check preempt ASan and produce a
 *                           much less useful report. Real parsers size
 *                           buffers at runtime, so this mirrors them.
 *   contains "HANG"      -- spins forever, so the runner's timeout must fire
 *   contains "UB"        -- signed overflow, UBSan aborts
 *   starts with '{'      -- "accepted"
 *   anything else        -- "rejected", with a reason on stderr
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fuzz_target.h"

/* Deliberately opaque to the optimizer, so the allocation size is not a
 * compile-time constant and ASan -- not UBSan -- reports the overflow. */
static uint8_t *make_buffer(size_t n) {
    uint8_t *p = malloc(n);
    if (p != NULL) memset(p, 0, n);
    return p;
}

static int contains(const uint8_t *hay, size_t n, const char *needle) {
    size_t m = strlen(needle);
    if (m > n) return 0;
    for (size_t i = 0; i + m <= n; i++) {
        if (memcmp(hay + i, needle, m) == 0) return 1;
    }
    return 0;
}

int fuzz_one_input(const uint8_t *data, size_t size) {
    if (contains(data, size, "CRASH")) {
        size_t n = (size % 8) + 1;             /* runtime-determined size */
        uint8_t *small = make_buffer(n);
        if (small == NULL) return FUZZ_REJECTED;
        volatile uint8_t sink = small[n + 3];  /* deliberate overflow */
        (void)sink;
        free(small);
        return FUZZ_ACCEPTED;
    }

    if (contains(data, size, "HANG")) {
        volatile unsigned long spin = 0;
        for (;;) spin++;
    }

    if (contains(data, size, "UB")) {
        volatile int big = 2147483647;
        volatile int boom = big + 1; /* signed overflow */
        (void)boom;
        return FUZZ_ACCEPTED;
    }

    if (size > 0 && data[0] == '{') {
        return FUZZ_ACCEPTED;
    }

    fprintf(stderr, "reject: expected '{' at offset 0\n");
    return FUZZ_REJECTED;
}
