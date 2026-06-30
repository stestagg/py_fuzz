#!/bin/sh

if [ -n "$BISECT_LOG" ]; then
    OUT=/pfm/scratch/bisect-logs/$(git rev-parse --short HEAD).txt
else
    OUT=${BISECT_OUT:-/dev/null}
fi

git reset --hard >>$OUT 2>&1
git clean -ffdx >>$OUT 2>&1


if [ -n "$USE_CCACHE" ]; then
    export CCACHE_DIR=/tmp/ccache
    export PATH="/usr/lib/ccache:$PATH"
fi

echo Configuring
./configure --with-ensurepip=no $CONFIGURE_ARGS >>$OUT 2>&1
echo Configure Complete, making

if ! make -j4 >>$OUT 2>&1; then
    echo '\e[43;37;1m\e[2K\r MAKE fail \e[0m'
    exit 125
fi
echo Make Complete, running

if [ -n "$MEM_LIMIT" ]; then
    (ulimit -v $((MEM_LIMIT * 1024)); ./python "/pfm/scratch/bisect/$SCRIPT_NAME.py") >>$OUT 2>&1
else
    ./python "/pfm/scratch/bisect/$SCRIPT_NAME.py" >>$OUT 2>&1
fi
rc=$?

echo Run finished, resetting repo
git reset --hard
git clean -ffdx

if [ "$rc" -ge 134 ] && [ "$rc" -le 143 ]; then
    echo '\e[41;37;1m\e[2K\r  FAILED  \e[0m'
    exit 1
else
    echo '\e[42;37;1m\e[2K\r  PASSED  \e[0m'
    exit 0
fi