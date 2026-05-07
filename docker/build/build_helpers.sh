#!/bin/sh

set -e

export LTOWRAP_ENABLE=1

PY_ROOT=/pfm/py

if [ "$PY_DEBUG" = "1" ]; then
    # for the debug config, we need to match the exact version,
    # but we only build one version, so we can find the matching config with a glob: python3*d-config
    PY_CONFIG=$(ls $PY_ROOT/bin/python3*d-config)
    PY_CMP_CONFIG=$(ls $PY_ROOT/cmplog/bin/python3*d-config)
    DEBUG_FLAG="-DPy_DEBUG"
else
    PY_CONFIG=/pfm/py/bin/python3-config
    PY_CMP_CONFIG=/pfm/py/cmplog/bin/python3-config
    DEBUG_FLAG=""
fi

PY_INCLUDE=$($PY_CONFIG --embed --includes)
PY_LIBS=$($PY_CONFIG --embed --ldflags)
CFLAGS="-O2 -g -fno-omit-frame-pointer -mno-omit-leaf-frame-pointer"


PY_CMP_INCLUDE=$($PY_CMP_CONFIG --embed --includes)
PY_CMP_LIBS=$($PY_CMP_CONFIG --embed --ldflags)


COMPILE_ARGS="$CFLAGS $PY_INCLUDE $PY_LIBS $EMBED_LINK_FLAGS -Wl,-export-dynamic"
COMPILE_ARGS_CMPLOG="$CFLAGS $PY_CMP_INCLUDE $PY_CMP_LIBS $EMBED_LINK_FLAGS -Wl,-export-dynamic"

if [ $PF_BUILD_HELPER = "fuzz_python" ]; then
    if [ ! -e /pfm/tools/fuzz_python ]; then
        $CC -o /pfm/tools/fuzz_python $COMPILE_ARGS /pfm/helpers/fuzz_python.c $DEBUG_FLAG
    fi
    if [ ! -e /pfm/tools/fuzz_python.cmplog ]; then
        env AFL_LLVM_CMPLOG=1 $CC -o /pfm/tools/fuzz_python.cmplog $COMPILE_ARGS_CMPLOG /pfm/helpers/fuzz_python.c $DEBUG_FLAG
    fi
fi

if [ $PF_BUILD_HELPER = "fuzz_peg" ]; then
    if [ ! -e /pfm/tools/fuzz_peg ]; then
        $CC -o /pfm/tools/fuzz_peg $COMPILE_ARGS /pfm/helpers/fuzz_peg.c $DEBUG_FLAG
    fi
    if [ ! -e /pfm/tools/fuzz_peg.cmplog ]; then
        env AFL_LLVM_CMPLOG=1 $CC -o /pfm/tools/fuzz_peg.cmplog $COMPILE_ARGS_CMPLOG /pfm/helpers/fuzz_peg.c $DEBUG_FLAG
    fi
fi
