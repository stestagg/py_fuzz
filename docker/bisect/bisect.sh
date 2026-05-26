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

if [ -n "$MEM_LIMIT" ]; then
    (ulimit -v $((MEM_LIMIT * 1024)); ./python "/pfm/scratch/bisect/$SCRIPT_NAME.py") >>$OUT 2>&1
else
    ./python "/pfm/scratch/bisect/$SCRIPT_NAME.py" >>$OUT 2>&1
fi
rc=$?

echo Run finished, resetting repo
git reset --hard
git clean -fd

if [ "$rc" -ge 134 ] && [ "$rc" -le 143 ]; then
    exit 1
else
    exit 0
fi