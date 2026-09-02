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

.PHONY: all mock clean distclean grammar-check

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

# ---------------------------------------------------------------------------
# grammar-check -- regenerate the ANTLR parsers and re-run the three-way
# comparison in grammar/README.md (upstream grammar vs adaptation vs mxml).
#
# Everything it needs is downloaded or built into $(BUILD), so the check is
# reproducible from a clean clone rather than depending on whatever happened
# to be in a scratch directory. Needs network on first run (for the jar) and
# java on PATH. Exits nonzero if the adaptation stops matching mxml.
# ---------------------------------------------------------------------------
ANTLR_VERSION := 4.13.2
ANTLR_JAR := $(BUILD)/antlr-$(ANTLR_VERSION)-complete.jar
ANTLR_URL := https://www.antlr.org/download/antlr-$(ANTLR_VERSION)-complete.jar
GEN := $(BUILD)/antlr
PROBE := $(BUILD)/probe-mxml
PYTHON ?= .venv/bin/python

grammar-check: $(ANTLR_JAR) $(PROBE)
	@rm -rf $(GEN)
	@mkdir -p $(GEN)/up $(GEN)/ad
	# The parser grammar needs the lexer's .tokens file, so the lexer must be
	# generated first and its output directory passed via -lib. Generating both
	# in one invocation fails -- upstream's own grammar included.
	java -jar $(ANTLR_JAR) -o $(GEN)/up -Dlanguage=Python3 grammar/XMLLexer.g4
	java -jar $(ANTLR_JAR) -o $(GEN)/up -lib $(GEN)/up/grammar -Dlanguage=Python3 grammar/XMLParser.g4
	java -jar $(ANTLR_JAR) -o $(GEN)/ad -Dlanguage=Python3 grammar/XMLmxmlLexer.g4
	java -jar $(ANTLR_JAR) -o $(GEN)/ad -lib $(GEN)/ad/grammar -Dlanguage=Python3 grammar/XMLmxmlParser.g4
	$(PYTHON) grammar/compare_grammars.py $(GEN)/up/grammar $(GEN)/ad/grammar $(PROBE)

$(ANTLR_JAR): | $(BUILD)
	curl -fsSL --max-time 120 -o $@ $(ANTLR_URL)

# The acceptance oracle. Built without sanitizers on purpose: it answers
# "does mxml accept this document", and a sanitizer abort would be reported
# as a rejection, quietly corrupting the comparison table.
$(PROBE): grammar/probe_mxml.c $(TARGET_SRC) $(MXML_CONF)/config.h | $(BUILD)
	$(CC) -std=c17 -g -O1 $(TARGET_INC) -o $@ grammar/probe_mxml.c $(TARGET_SRC)

# Also drops the generated mxml config; use when changing pinned versions.
distclean: clean
	rm -rf $(MXML_CONF) $(GEN) $(PROBE) $(ANTLR_JAR)
