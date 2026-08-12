#!/bin/sh
#
# Shared helpers for package build recipes. Meant to be sourced, not executed.
#
# The build backends (pip/meson/Cython/ninja) are installed into an isolated
# directory kept OFF the fuzzed interpreter's default sys.path, so they never
# land in site-packages or on AFL_PRELOAD; they are exposed only via PYTHONPATH
# while a package is being built. Only the target packages themselves (and their
# pure-Python runtime deps) are installed into /pfm/py.

# Per-project, mounted from projects/<name>/packages (see Env.mounts). Source
# checkouts and build tooling live here; built packages install into /pfm/py.
PF_PY_ROOT=/pfm/py
PF_PKG_ROOT=/pfm/packages
PF_BUILDENV=/pfm/packages/.buildenv

pf_python() {
    if [ -x "$PF_PY_ROOT/bin/python3" ]; then
        echo "$PF_PY_ROOT/bin/python3"
    else
        ls "$PF_PY_ROOT"/bin/python3* | grep -v -- -config | head -n1
    fi
}
PY=$(pf_python)

PF_WHEELS="$PF_PKG_ROOT/.wheels"

# The VM root filesystem is read-only; HOME is unset in the guest, so pip and the
# compilers try to write scratch to /.cache and fail. Redirect HOME, the XDG/pip
# caches, and TMPDIR to the writable, disk-backed project mount.
export HOME="$PF_PKG_ROOT/.home"
export XDG_CACHE_HOME="$PF_PKG_ROOT/.cache"
export TMPDIR="$PF_PKG_ROOT/.tmp"
mkdir -p "$HOME" "$XDG_CACHE_HOME" "$TMPDIR" "$PF_BUILDENV"

# The toolchain is installed with `pip --target`, so its console scripts and the
# bundled ninja binary land in $PF_BUILDENV/bin; put it on PATH so meson-python
# and meson can find ninja/meson. (Populated by pf_bootstrap_buildenv.)
export PATH="$PF_BUILDENV/bin:$PATH"

pf_bootstrap_buildenv() {
    mkdir -p "$PF_BUILDENV"
    if [ ! -d "$PF_WHEELS" ] || [ -z "$(ls "$PF_WHEELS"/*.whl 2>/dev/null)" ]; then
        echo "No wheelhouse at $PF_WHEELS (host tooling fetch did not run?)" >&2
        exit 1
    fi
    if [ -d "$PF_BUILDENV/pip" ]; then
        return
    fi
    # Bootstrap fully offline: run pip straight from its own wheel (put on
    # PYTHONPATH) to install the toolchain into the buildenv from the wheelhouse.
    # LTOWRAP_ENABLE=0 -> the wrapper execs plain clang, so any sdist that compiles
    # (Cython) is uninstrumented and does NOT consume a STARTID range.
    _pip_whl=$(ls "$PF_WHEELS"/pip-*.whl | head -n1)
    LTOWRAP_ENABLE=0 PYTHONPATH="$_pip_whl" "$PY" -m pip install --no-cache-dir \
        --no-index --find-links="$PF_WHEELS" --target="$PF_BUILDENV" \
        pip setuptools wheel Cython meson-python meson ninja cmake build versioneer
}

# pf_package_source <name>  ->  echoes the host-checked-out source dir
# The source was cloned host-side (ensure_package_checkout) into /pfm/packages/<name>;
# the offline VM only reads it.
pf_package_source() {
    _name=$1
    _dir="$PF_PKG_ROOT/$_name"
    if [ ! -d "$_dir/.git" ]; then
        echo "Package source not checked out on host: $_dir" >&2
        exit 1
    fi
    echo "$_dir"
}

# pf_pip_build <src_dir> [extra -C/--config-settings args...]
# Builds and installs a package from source through the AFL wrapper (CC/CXX and
# LTOWRAP_PROFILE are inherited from the environment). Single-threaded so the
# shared STARTID counter is advanced serially, exactly like CPython's make -j1.
#
# -Db_lto=true forces meson to compile with -flto so the objects are LLVM bitcode;
# afl-clang-lto instruments at link time and needs bitcode inputs (otherwise it
# reports "No instrumentation targets found" and fails).
pf_pip_build() {
    _src=$1; shift
    PYTHONPATH="$PF_BUILDENV" "$PY" -m pip install --no-cache-dir \
        --no-index --find-links="$PF_WHEELS" \
        --no-build-isolation --no-deps \
        --config-settings=setup-args=-Db_lto=true \
        --config-settings=compile-args=-j1 \
        "$@" "$_src"
}

# pf_pip_build_src <src_dir> [extra pip args...]
# Like pf_pip_build but WITHOUT the meson-specific config settings, for source trees
# whose backend is setuptools/CMake (e.g. pyarrow). Objects still compile through the
# AFL wrapper (CC/CXX from the environment), so afl-clang-lto instruments at link.
pf_pip_build_src() {
    _src=$1; shift
    PYTHONPATH="$PF_BUILDENV" "$PY" -m pip install --no-cache-dir \
        --no-index --find-links="$PF_WHEELS" \
        --no-build-isolation --no-deps \
        "$@" "$_src"
}

# pf_cmake_build <src_dir> <build_dir> [extra -D args...]
# Configure + build + install a CMake project through the AFL wrapper. Serial build
# (-j1) so the shared STARTID counter advances deterministically, exactly like the
# CPython make -j1 and pf_pip_build. Ninja generator; CC/CXX/AR/RANLIB inherited from
# the environment. cmake/ninja come from the buildenv on PATH (pf_bootstrap_buildenv).
# Objects compile as bitcode via afl-clang-lto's LTO mode (same path as CPython);
# libarrow/libparquet shared links are instrumented by the `cmake` wrapper profile.
pf_cmake_build() {
    _src=$1; _bld=$2; shift 2
    # Always configure from a clean build tree. A failed configure caches the
    # compiler identity (CMAKE_<LANG>_COMPILER_ID) in CMakeCache.txt; a later
    # re-run then trusts that stale/empty value and never re-detects, so a fix to
    # the compiler wrapper can't take effect until the cache is gone. Wiping also
    # keeps the shared STARTID sequence deterministic (full relink each run);
    # ccache still shortcuts the recompiles.
    rm -rf "$_bld"
    # The cmake/ninja wheels install Python launchers (bin/cmake -> `from cmake
    # import cmake`), so the buildenv must be importable, exactly like pf_pip_build.
    PYTHONPATH="$PF_BUILDENV" cmake -S "$_src" -B "$_bld" -G Ninja \
        -DCMAKE_BUILD_TYPE=Debug \
        -DCMAKE_INSTALL_PREFIX="$PF_PY_ROOT" \
        "$@"
    PYTHONPATH="$PF_BUILDENV" cmake --build "$_bld" --target install -j1
}

# pf_pip_install_wheels <req>...  installs pure-Python runtime deps offline from
# the wheelhouse (imported during warmup before the forkserver; safe uninstrumented).
pf_pip_install_wheels() {
    PYTHONPATH="$PF_BUILDENV" "$PY" -m pip install --no-cache-dir \
        --no-index --find-links="$PF_WHEELS" "$@"
}
