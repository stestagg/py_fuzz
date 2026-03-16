#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdlib.h>
#include <string.h>

__AFL_FUZZ_INIT();

int main(int argc, char **argv) {
    /* --- One-time Python initialization (runs in the parent, before fork) --- */
    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    config.install_signal_handlers = 0;
    config.write_bytecode = 0;

    PyStatus status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) {
        Py_ExitStatusException(status);
    }

    /*
     * Warm-up: run a trivial compile+eval so that CPython's lazy dlopen()
     * calls (extension modules like _json, math, etc.) all fire here in the
     * parent process, before the forkserver starts.  Without this, AFL++
     * warns that dlopen'd libraries won't have instrumented coverage because
     * they were loaded after __AFL_INIT().
     */
    {
        PyObject *dummy = Py_CompileString("pass", "<warmup>", Py_file_input);
        if (dummy) {
            PyObject *globals = PyDict_New();
            if (globals) {
                PyObject *builtins = PyEval_GetBuiltins();
                PyDict_SetItemString(globals, "__builtins__", builtins);
                PyObject *result = PyEval_EvalCode(dummy, globals, globals);
                Py_XDECREF(result);
                Py_DECREF(globals);
            }
            Py_DECREF(dummy);
        }
        PyErr_Clear();
    }

    /*
     * Deferred forkserver: the fork point is HERE, after Python init.
     * Child processes each get a pre-warmed Python runtime for free.
     */
#ifdef __AFL_HAVE_MANUAL_CONTROL
    __AFL_INIT();
#endif

    unsigned char *buf = __AFL_FUZZ_TESTCASE_BUF;

    while (__AFL_LOOP(10000)) {
        int len = __AFL_FUZZ_TESTCASE_LEN;

        /* Null-terminate into a local buffer; cap at 64 KB */
        if (len > 65535) len = 65535;
        char *src = (char *)malloc(len + 1);
        if (!src) continue;
        memcpy(src, buf, len);
        src[len] = '\0';

        /*
         * Primary target: compile only (parser + compiler).
         * This is fast and safe for persistent mode — no global state mutation.
         */
        PyObject *code = Py_CompileString(src, "<fuzz>", Py_file_input);

#ifndef FUZZ_PARSE_ONLY
        /*
         * Secondary target: also evaluate compiled code.
         * Uses a fresh dict per iteration to avoid cross-iteration state.
         */
        if (code != NULL) {
            PyObject *globals = PyDict_New();
            if (globals) {
                PyObject *builtins = PyEval_GetBuiltins();
                PyDict_SetItemString(globals, "__builtins__", builtins);
                PyObject *result = PyEval_EvalCode(code, globals, globals);
                Py_XDECREF(result);
                Py_DECREF(globals);
            }
            Py_DECREF(code);
        }
#else
        Py_XDECREF(code);
#endif

        PyErr_Clear();
        free(src);
    }

    return 0;
}
