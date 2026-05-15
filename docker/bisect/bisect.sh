#!/bin/sh
git reset --hard
git clean -fd

OUT=${BISECT_OUT:-/dev/null}

if [ -n "$USE_CCACHE" ]; then
    export CCACHE_DIR=/tmp/ccache
    export PATH="/usr/lib/ccache:$PATH"
fi

echo Configuring
./configure --with-ensurepip=no $CONFIGURE_ARGS >>$OUT 2>&1
echo Configure Complete, making

make -j4 >>$OUT 2>&1 || exit 125
echo Make Complete, running

./python "/pfm/bisect_script/$SCRIPT_NAME.py" >>$OUT 2>&1
rc=$?

echo Run finished, resetting repo
git reset --hard
git clean -fd

if [ "$rc" -ge 134 ] && [ "$rc" -le 143 ]; then
    exit 1
else
    exit 0
fi