PYTHON_VERSION ?= 3.14
PYTHON_TAG     ?= v$(PYTHON_VERSION).0a1
PYTHON_SRC      = python
DIST_DIR       ?= dist/main
PREFIX          = $(CURDIR)/$(DIST_DIR)/install
TESTCASES_DIR   = testcases
OUTPUT_DIR      = output
DICT_FILE       = dicts/python.dict
NPROC          ?= $(shell nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)

# Prefer afl-clang-lto > afl-clang-fast > afl-gcc-fast
AFL_CC := $(shell \
  command -v afl-clang-lto   2>/dev/null || \
  command -v afl-clang-fast  2>/dev/null || \
  command -v afl-gcc-fast    2>/dev/null || \
  echo "afl-clang-fast")

PYTHON_CONFIG  = $(PREFIX)/bin/python3-config
PYTHON_CFLAGS  = $(shell $(PYTHON_CONFIG) --includes 2>/dev/null)
PYTHON_LDFLAGS = $(shell $(PYTHON_CONFIG) --ldflags --embed 2>/dev/null)

HARNESS        = $(DIST_DIR)/fuzz_python
HARNESS_CMPLOG = $(DIST_DIR)/fuzz_python_cmplog

.PHONY: all build-python harness harness-cmplog \
        fuzz fuzz-multi status tmin clean distclean

all: harness

# --- Source ---
python:
	git clone --depth=1 https://github.com/python/cpython.git $@
	# To pin a tag: cd $@ && git fetch --depth=1 origin $(PYTHON_TAG) && git checkout FETCH_HEAD

# --- Build Python ---
$(PREFIX)/lib/python$(PYTHON_VERSION): | python
	mkdir -p $(DIST_DIR)
	cd python && \
	  CC=$(AFL_CC) CFLAGS="-O2 -g" \
	  ax_cv_c_float_words_bigendian=no \
	  ./configure \
	    --prefix=$(PREFIX) \
	    --disable-shared \
	    --without-pymalloc \
	    2>&1 | tee $(CURDIR)/$(DIST_DIR)/configure.log
	$(MAKE) -C python -j$(NPROC) 2>&1 | tee $(DIST_DIR)/build.log
	$(MAKE) -C python install     2>&1 | tee $(DIST_DIR)/install.log

build-python: $(PREFIX)/lib/python$(PYTHON_VERSION)

# --- Harness ---
$(HARNESS): harness/fuzz_python.c build-python
	mkdir -p $(DIST_DIR)
	$(AFL_CC) -O2 -g \
	  $(PYTHON_CFLAGS) \
	  harness/fuzz_python.c \
	  $(PYTHON_LDFLAGS) \
	  -o $@

$(HARNESS_CMPLOG): harness/fuzz_python.c build-python
	mkdir -p $(DIST_DIR)
	AFL_LLVM_CMPLOG=1 $(AFL_CC) -O2 -g \
	  $(PYTHON_CFLAGS) \
	  harness/fuzz_python.c \
	  $(PYTHON_LDFLAGS) \
	  -o $@

harness: $(HARNESS)
harness-cmplog: $(HARNESS_CMPLOG)

# --- Fuzzing ---
fuzz: harness | $(OUTPUT_DIR)
	afl-fuzz \
	  -i $(TESTCASES_DIR) \
	  -o $(OUTPUT_DIR) \
	  -t 10000 -m 512 \
	  -x $(DICT_FILE) \
	  -- $(HARNESS)

# Multi-instance: master (with cmplog) + 1 slave via tmux
fuzz-multi: harness harness-cmplog | $(OUTPUT_DIR)
	@command -v tmux >/dev/null 2>&1 || { echo "tmux required for fuzz-multi"; exit 1; }
	tmux new-session -d -s fuzzing \
	  "afl-fuzz -i $(TESTCASES_DIR) -o $(OUTPUT_DIR) -M main \
	     -t 10000 -m 512 -x $(DICT_FILE) \
	     -c $(HARNESS_CMPLOG) \
	     -- $(HARNESS); read"
	tmux split-window -t fuzzing \
	  "afl-fuzz -i $(TESTCASES_DIR) -o $(OUTPUT_DIR) -S slave1 \
	     -t 10000 -m 512 \
	     -- $(HARNESS); read"
	tmux attach -t fuzzing

$(OUTPUT_DIR):
	mkdir -p $@

status:
	afl-whatsup $(OUTPUT_DIR)

tmin: | $(OUTPUT_DIR)/tmin
	for f in $(TESTCASES_DIR)/*.py; do \
	  afl-tmin -i $$f -o $(OUTPUT_DIR)/tmin/$$(basename $$f) \
	    -t 10000 -- $(HARNESS); \
	done

$(OUTPUT_DIR)/tmin:
	mkdir -p $@

clean:
	rm -rf dist/main

distclean: clean
	rm -rf dist python output
