#!/bin/sh
#
# Build pyarrow (Apache Arrow Python bindings) from source, AFL-instrumented, with
# Parquet support, into the fuzzed interpreter. Two stages:
#   1. the Arrow C++ core (libarrow.so, libparquet.so) via CMake, and
#   2. the pyarrow bindings (setuptools + Cython + CMake) linked against it.
# numpy is built first (declared dep). Source and the bundled C++ deps (Thrift for
# Parquet + snappy/zlib/zstd codecs) are prefetched host-side (the VM is offline);
# this recipe only builds. Invoked by build_packages.sh with LTOWRAP_PROFILE=cmake.

set -xe

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/_common.sh"

SRC=$(pf_package_source pyarrow)

# Offline bundled-dep URLs, prefetched host-side by ensure_arrow_thirdparty and
# rewritten to VM paths. Exports ARROW_DEPENDENCY_SOURCE=BUNDLED + ARROW_<DEP>_URL,
# so the in-VM CMake configure resolves every dependency from the local cache.
. "$SRC/.arrow_thirdparty/env.sh"

# --- Stage 1: Arrow C++ core -> /pfm/py (libarrow.so.*, libparquet.so.*) ----------
pf_cmake_build "$SRC/cpp" "$SRC/cpp/build" \
    -DARROW_BUILD_SHARED=ON \
    -DARROW_BUILD_STATIC=OFF \
    -DARROW_BUILD_TESTS=OFF \
    -DARROW_BUILD_BENCHMARKS=OFF \
    -DARROW_PARQUET=ON \
    -DARROW_WITH_SNAPPY=ON \
    -DARROW_WITH_ZLIB=ON \
    -DARROW_WITH_ZSTD=ON

# --- Stage 2: pyarrow bindings, linked against the just-installed Arrow C++ --------
# ARROW_HOME points the bindings' CMake at the Stage 1 install; BUNDLE_ARROW_CPP
# copies libarrow.so.* / libparquet.so.* into site-packages/pyarrow/ so the loader
# resolves them (and so_files/AFL_PRELOAD picks them up as versioned sonames).
export ARROW_HOME="$PF_PY_ROOT"
export PARQUET_HOME="$PF_PY_ROOT"
export PYARROW_WITH_PARQUET=1
export PYARROW_BUNDLE_ARROW_CPP=1
export PYARROW_BUILD_TYPE=debug
export PYARROW_PARALLEL=1   # serial, so the shared STARTID counter stays deterministic
pf_pip_build_src "$SRC/python"
