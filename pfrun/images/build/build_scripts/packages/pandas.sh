#!/bin/sh
#
# Build pandas from source, AFL-instrumented, into the fuzzed interpreter.
# Depends on numpy having been built first (build_packages.sh orders deps first);
# pandas' extensions build against the already-installed instrumented numpy.
# Source is checked out host-side; invoked by build_packages.sh with LTOWRAP_PROFILE set.

set -xe

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/_common.sh"

SRC=$(pf_package_source pandas)

pf_pip_build "$SRC"

# Pure-Python runtime dependencies; safe as uninstrumented wheels and imported
# during warmup, before the forkserver comes up.
pf_pip_install_wheels python-dateutil pytz tzdata
