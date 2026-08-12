#!/bin/sh
#
# Build numpy from source, AFL-instrumented, into the fuzzed interpreter.
# Source is checked out host-side; invoked by build_packages.sh with LTOWRAP_PROFILE set.

set -xe

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/_common.sh"

SRC=$(pf_package_source numpy)

# The build VM has no guaranteed system BLAS; allow numpy's reference fallback so
# the build does not require OpenBLAS/MKL to be present.
pf_pip_build "$SRC" --config-settings=setup-args=-Dallow-noblas=true
