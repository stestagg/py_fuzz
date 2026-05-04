#!/bin/sh

set -e

CHECKOUT_ROOT=/pfm/cpython
INSTALL_ROOT=/pfm/py

CHECKOUT_REF=$1

if [ -z "$CHECKOUT_REF" ]; then
    echo "Usage: $0 <checkout-ref>"
    exit 1
fi

if [ -e "$CHECKOUT_ROOT"/.git ]; then
    # Check if we are already at the correct ref
    cd "$CHECKOUT_ROOT"

    if ! r=$(git rev-parse --verify "$CURRENT_REF" 2>/dev/null) || \
    [ "$r" != "$(git rev-parse HEAD)" ]; then
    rm -rf "$CHECKOUT_ROOT/*" "$CHECKOUT_ROOT/.[!.]*"
    fi
fi

if [ -e "$CHECKOUT_ROOT"/.git ]; then
    echo "Already at correct ref, skipping clone"
else
    git clone --depth 1 --branch "$CHECKOUT_REF" https://github.com/python/cpython.git "$CHECKOUT_ROOT"
fi

cd "$CHECKOUT_ROOT"


export LTOWRAP_ENABLE=0
if [ -e /tmp/ltowrap.json ]; then
    rm /tmp/ltowrap.json
fi

./configure --prefix="$INSTALL_ROOT" \
            --disable-shared \
            --without-ensurepip \
            --disable-test-modules \
            --without-doc-strings \
            --cache-file=/pfm/cache/config-cache

export LTOWRAP_ENABLE=1

sed -i 's|^\(\$(BUILDPYTHON):[[:space:]]*Programs/python\.o[[:space:]]\+\$(LINK_PYTHON_DEPS)\)|\1 $(SHAREDMODS)|' Makefile

make -j1 install

export AFL_LLVM_CMPLOG=1
export LTOWRAP_ENABLE=0
if [ -e /tmp/ltowrap.json ]; then
    rm /tmp/ltowrap.json
fi

# mkdir "$INSTALL_ROOT/cmplog"
# ./configure --prefix="$INSTALL_ROOT/cmplog" \
#             --disable-shared \
#             --without-ensurepip \
#             --disable-test-modules \
#             --without-doc-strings
# export LTOWRAP_ENABLE=1
# make -j4 install