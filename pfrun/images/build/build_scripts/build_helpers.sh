#!/bin/sh

set -e

export LTOWRAP_ENABLE=1

PY_ROOT=/pfm/py

if [ "$PY_DEBUG" = "1" ]; then
    # for the debug config, we need to match the exact version,
    # but we only build one version, so we can find the matching config with a glob: python3*d-config
    PY_CONFIG=$(ls $PY_ROOT/bin/python3*d-config)
    if [ $PY_FUZZ_CMPLOG = "1" ]; then
        PY_CMP_CONFIG=$(ls $PY_ROOT/cmplog/bin/python3*d-config)
    fi
    DEBUG_FLAG="-DPy_DEBUG"
else
    PY_CONFIG=/pfm/py/bin/python3-config
    if [ $PY_FUZZ_CMPLOG = "1" ]; then
    PY_CMP_CONFIG=/pfm/py/cmplog/bin/python3-config
    fi
    DEBUG_FLAG=""
fi

PY_INCLUDE=$($PY_CONFIG --embed --includes)
PY_LIBS=$($PY_CONFIG --embed --ldflags)
CFLAGS="-O2 -g -fno-omit-frame-pointer -mno-omit-leaf-frame-pointer"

if [ $PY_FUZZ_CMPLOG = "1" ]; then
    PY_CMP_INCLUDE=$($PY_CMP_CONFIG --embed --includes)
    PY_CMP_LIBS=$($PY_CMP_CONFIG --embed --ldflags)
    COMPILE_ARGS_CMPLOG="$CFLAGS $PY_CMP_INCLUDE $PY_CMP_LIBS $EMBED_LINK_FLAGS -Wl,-export-dynamic"
fi

COMPILE_ARGS="$CFLAGS $PY_INCLUDE $PY_LIBS $EMBED_LINK_FLAGS -Wl,-export-dynamic"


if [ ! -e /pfm/tools/mem_limit_exec ]; then
    cc -O2 -o /pfm/tools/mem_limit_exec /pfm/helpers/mem_limit_exec.c
fi

if [ $PF_BUILD_HELPER = "fuzz_python" ]; then
    if [ ! -e /pfm/tools/fuzz_python ]; then
        $CC -o /pfm/tools/fuzz_python $COMPILE_ARGS /pfm/helpers/fuzz_python.c $DEBUG_FLAG
    fi
    if [ $PY_FUZZ_CMPLOG = "1" ]; then
        if [ ! -e /pfm/tools/fuzz_python.cmplog ]; then
            env AFL_LLVM_CMPLOG=1 $CC -o /pfm/tools/fuzz_python.cmplog $COMPILE_ARGS_CMPLOG /pfm/helpers/fuzz_python.c $DEBUG_FLAG
        fi
    fi
fi

if [ $PF_BUILD_HELPER = "fuzz_peg" ]; then
    if [ ! -e /pfm/tools/fuzz_peg ]; then
        $CC -o /pfm/tools/fuzz_peg $COMPILE_ARGS /pfm/helpers/fuzz_peg.c $DEBUG_FLAG
    fi
    if [ $PY_FUZZ_CMPLOG = "1" ]; then
        if [ ! -e /pfm/tools/fuzz_peg.cmplog ]; then
            env AFL_LLVM_CMPLOG=1 $CC -o /pfm/tools/fuzz_peg.cmplog $COMPILE_ARGS_CMPLOG /pfm/helpers/fuzz_peg.c $DEBUG_FLAG
        fi
    fi
fi

if [ $PF_BUILD_HELPER = "fuzz_script" ]; then
    if [ ! -e /pfm/tools/fuzz_script ]; then
        $CC -o /pfm/tools/fuzz_script $COMPILE_ARGS /pfm/helpers/fuzz_script.c $DEBUG_FLAG
    fi
    if [ $PY_FUZZ_CMPLOG = "1" ]; then
        if [ ! -e /pfm/tools/fuzz_script.cmplog ]; then
            env AFL_LLVM_CMPLOG=1 $CC -o /pfm/tools/fuzz_script.cmplog $COMPILE_ARGS_CMPLOG /pfm/helpers/fuzz_script.c $DEBUG_FLAG
        fi
    fi
fi
