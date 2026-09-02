#ifndef FUZZ_TARGET_H
#define FUZZ_TARGET_H

#include <stddef.h>
#include <stdint.h>

/*
 * Set to 1 only if the library's entry point takes a NUL-terminated C string
 * instead of a (pointer, length) pair. It costs one byte of detection range:
 * the driver must allocate size+1, so a parser reading exactly one byte past
 * the input can no longer be told apart from a legitimate read of the
 * terminator. Leave it at 0 whenever the API accepts an explicit length.
 */
#define FUZZ_TARGET_WANTS_NUL_TERMINATION 0

/*
 * Outcome of handing one input to the library. Neither value is a bug: a
 * parser refusing malformed input is behaving correctly. Findings come from
 * the sanitizers aborting the process, never from this return value.
 *
 * The distinction still matters, because the agentic loop steers on parser
 * acceptance rate -- a generator the library rejects 99% of the time is not
 * reaching the code worth testing. Collapsing both outcomes into one exit
 * code would hide exactly that signal.
 */
#define FUZZ_ACCEPTED 0
#define FUZZ_REJECTED 1

/*
 * Hand one input to the library under test. Returns FUZZ_ACCEPTED or
 * FUZZ_REJECTED. On rejection the seam may print one short line to stderr
 * describing why, which the runner can cluster to see what is being refused.
 */
int fuzz_one_input(const uint8_t *data, size_t size);

#endif /* FUZZ_TARGET_H */
