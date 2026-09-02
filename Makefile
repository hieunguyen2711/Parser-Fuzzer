# Build the fuzzing harness with sanitizers.
#
#   make            build build/harness
#   make clean      remove build outputs
#

CC = clang

# -O1 keeps traces readable while staying fast enough to fuzz.
# -fno-omit-frame-pointer gives ASan complete stacks.
# -fno-sanitize-recover=undefined makes UBSan abort instead of printing and
#   continuing; without it a UB report scrolls past and the process still
#   exits 0, so the runner never sees the finding.
# On -fno-sanitize-recover=undefined: UBSan otherwise prints a report and
#   keeps going, exiting 0, so a runner watching exit codes would miss the
#   bug entirely. Aborting makes every finding an unambiguous signal.
#
#   Known interaction, documented rather than worked around: when the compiler
#   can statically infer an allocation's size, UBSan's type-mismatch check
#   fires before ASan and reports a terse "insufficient space for an object of
#   type" instead of ASan's heap-buffer-overflow-with-allocation-stack. It
#   cannot be disabled via -fno-sanitize=object-size (tried; no effect). It
#   rarely matters on a real parser, where buffer sizes are runtime values and
#   ASan wins; triage.py handles either report shape.
SAN ?= address,undefined
CFLAGS ?= -std=c11 -g -O1 -fno-omit-frame-pointer \
          -fsanitize=$(SAN) -fno-sanitize-recover=undefined \
          -Wall -Wextra
LDFLAGS ?= -fsanitize=$(SAN)

BUILD := build

# The target library: mxml v4.0.5, pinned at target/mxml.
#
# Compiled from source into the harness rather than linked as a prebuilt .a,
# because sanitizers only instrument code compiled with their flags. Linking a
# system libmxml would produce a harness that cannot see a single bug inside
# the library -- the exact failure that looks like a clean fuzzing run.
#
# mxml's own test file is excluded; it has a main().
TARGET_DIR := target/mxml
TARGET_SRC ?= $(filter-out %/testmxml.c,$(wildcard $(TARGET_DIR)/mxml-*.c))
TARGET_INC ?= -I$(TARGET_DIR) -I$(MXML_CONF)

# mxml needs a config.h from its configure script. It is generated OUT OF TREE
# so target/mxml stays pristine at its pinned commit -- a dirty pinned checkout
# makes it impossible to tell library code from local edits when triaging.
MXML_CONF := $(BUILD)/mxmlconf
BIN := $(BUILD)/harness
SRC := harness/harness.c harness/fuzz_target.c

# A fake parser that produces every outcome on demand (accept, reject, ASan
# abort, UBSan abort, hang). Used only to test the Python runner; never part
# of a result.
MOCK_BIN := $(BUILD)/harness-mock
MOCK_SRC := harness/harness.c tests/mock_target.c

.PHONY: all mock clean distclean

all: $(BIN)

mock: $(MOCK_BIN)

$(MOCK_BIN): $(MOCK_SRC) | $(BUILD)
	$(CC) $(CFLAGS) -Iharness -o $@ $(MOCK_SRC) $(LDFLAGS)

$(BIN): $(SRC) $(TARGET_SRC) $(MXML_CONF)/config.h | $(BUILD)
	$(CC) $(CFLAGS) $(TARGET_INC) -o $@ $(SRC) $(TARGET_SRC) $(LDFLAGS)

$(MXML_CONF)/config.h: | $(BUILD)
	mkdir -p $(MXML_CONF)
	cd $(MXML_CONF) && $(CURDIR)/$(TARGET_DIR)/configure --quiet >/dev/null

$(BUILD):
	mkdir -p $(BUILD)

clean:
	rm -rf $(BUILD)/harness $(BUILD)/harness.dSYM \
	  $(BUILD)/harness-mock $(BUILD)/harness-mock.dSYM

# Also drops the generated mxml config; use when changing pinned versions.
distclean: clean
	rm -rf $(MXML_CONF)
