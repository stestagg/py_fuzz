#!/bin/sh

set -xe

CHECKOUT_ROOT=/pfm/cpython
INSTALL_ROOT=/pfm/py

mkdir -p $INSTALL_ROOT/afl_dicts

if [ -e "$CHECKOUT_ROOT"/.git ]; then
    echo "Already have a checkout, skipping clone"
else
    if [ -n "$CLONE_BRANCH" ]; then
        git clone --depth 1 --branch "$CLONE_BRANCH" https://github.com/python/cpython.git "$CHECKOUT_ROOT"
    elif [ -n "$CLONE_COMMIT" ]; then
        git clone --no-checkout --filter=blob:none https://github.com/python/cpython.git "$CHECKOUT_ROOT"
        (
            cd "$CHECKOUT_ROOT" &&
            git fetch --depth 1 origin "$CLONE_COMMIT" &&
            git checkout FETCH_HEAD
        )
    fi
fi

cd "$CHECKOUT_ROOT"

if [ -d /pfm/tactical-patches ]; then
    for _patch in /pfm/tactical-patches/*.diff /pfm/tactical-patches/*.patch; do
        [ -f "$_patch" ] || continue
        _basename=$(basename "$_patch")
        case ":${PY_FUZZ_SKIP_PATCHES}:" in
            *":${_basename}:"*)
                echo "Skipping tactical patch: $_patch"
                continue
                ;;
        esac
        if git apply --check "$_patch"; then
            git apply "$_patch"
            echo "Applied tactical patch: $_patch"
        elif git apply --reverse --check "$_patch"; then
            # The checkout is mounted read-write, so a failed build may leave
            # tactical changes behind.  Treat an already-applied patch as a
            # no-op so retries can continue from that checkout.
            echo "Tactical patch already applied: $_patch"
        else
            echo "Could not apply tactical patch: $_patch" >&2
            exit 1
        fi
    done
fi

export LTOWRAP_ENABLE=0
if [ -e /tmp/ltowrap.json ]; then
    rm /tmp/ltowrap.json
fi

if [ "$PY_DEBUG" = "1" ]; then
    DEBUG_FLAG="--with-pydebug"
fi

./configure --prefix="$INSTALL_ROOT" \
            --disable-shared \
            --without-ensurepip \
            --disable-test-modules \
            --without-doc-strings \
            --cache-file=/pfm/cache/config-cache \
            $DEBUG_FLAG \
            ${PY_CONFIGURE_EXTRA_ARGS}

export LTOWRAP_ENABLE=1
if [ -e /pfm/py/lto-wrap-state.json ]; then
    rm /pfm/py/lto-wrap-state.json
fi

sed -i 's|^\(\$(BUILDPYTHON):[[:space:]]*Programs/python\.o[[:space:]]\+\$(LINK_PYTHON_DEPS)\)|\1 $(SHAREDMODS)|' Makefile

make -j1 install

# Use the freshly-built interpreter to introspect its own standard library and
# emit mutation and AFL dictionary candidates: builtin names, stdlib module
# names, and their members (e.g. ``fileinput`` -> ``fileinput``, ``nextfile``).
# This runs entirely offline; the previous Sphinx-inventory approach needed
# network access to create the documentation venv.
"$INSTALL_ROOT/bin/python3" /pfm/build_scripts/generate_pymutate_names.py \
    "$INSTALL_ROOT/pymutate_names.txt" \
    "$INSTALL_ROOT/combined.dict"

if [ $PY_FUZZ_CMPLOG = "1" ]; then

    export AFL_LLVM_CMPLOG=1
    export LTOWRAP_ENABLE=0

    mkdir "$INSTALL_ROOT/cmplog"
    touch Programs/_freeze_module.o
    ./configure --prefix="$INSTALL_ROOT/cmplog" \
                --disable-shared \
                --without-ensurepip \
                --disable-test-modules \
                --without-doc-strings
    export LTOWRAP_ENABLE=1
    if [ -e /tmp/ltowrap.json ]; then
        rm /tmp/ltowrap.json
    fi
    make -j4 -o Programs/_freeze_module -o _bootstrap_python -o Programs/_testembed install
fi

# Runtime images do not necessarily contain git or the CPython checkout.
# Preserve the exact source revision (including a hash of tactical changes)
# next to the installed interpreter for helpers/verinfo.
/pfm/helpers/verinfo --write-git-info "$INSTALL_ROOT/.git-version-info" "$CHECKOUT_ROOT"
