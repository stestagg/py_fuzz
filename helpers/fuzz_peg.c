#define _GNU_SOURCE
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>

#define TEST_CRASH_INPUT     "fuzztestcrash"
#define TEST_CRASH_INPUT_LEN 13

__AFL_FUZZ_INIT();

static int
activate_perf_trampoline_if_requested(void)
{
    const char *enable = getenv("FUZZ_PERF_TRAMPOLINE");
    if (!enable || strcmp(enable, "1") != 0) {
        return 0;
    }

    PyObject *sys = PyImport_ImportModule("sys");
    if (sys == NULL) {
        PyErr_Print();
        return -1;
    }

    PyObject *result = PyObject_CallMethod(sys, "activate_stack_trampoline", "s", "perf");
    if (result == NULL) {
        PyErr_Print();
        Py_DECREF(sys);
        return -1;
    }

    Py_DECREF(result);
    Py_DECREF(sys);
    return 0;
}

int main(int argc, char **argv) {
    (void)argc;
    (void)argv;

    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    config.install_signal_handlers = 0;
    config.write_bytecode = 0;

    const char *python_home = getenv("PYTHONHOME");
    if (python_home) {
        PyStatus s = PyConfig_SetBytesString(&config, &config.home, python_home);
        if (PyStatus_Exception(s)) {
            PyConfig_Clear(&config);
            Py_ExitStatusException(s);
        }
    }

    PyStatus status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) {
        Py_ExitStatusException(status);
    }

    /* PyConfig_InitIsolatedConfig leaves install_signal_handlers=0, so CPython
     * does NOT ignore SIGPIPE/SIGXFSZ the way a normal interpreter does. Replicate
     * just those SIG_IGN calls (mirroring signal_install_handlers() in CPython's
     * signalmodule.c) so fuzzed code hitting a broken pipe / file-size limit raises
     * BrokenPipeError/OSError instead of killing the process and registering as a
     * false-positive crash. We intentionally do NOT enable install_signal_handlers,
     * which would also install Python's SIGINT handler and import _signal. */
#ifdef SIGPIPE
    PyOS_setsig(SIGPIPE, SIG_IGN);
#endif
#ifdef SIGXFZ
    PyOS_setsig(SIGXFZ, SIG_IGN);
#endif
#ifdef SIGXFSZ
    PyOS_setsig(SIGXFSZ, SIG_IGN);
#endif

    /* Warm up the PEG parser before the forkserver starts. */
    {
        PyCompilerFlags flags = {PyCF_ONLY_AST, 0};
        PyObject *ast = Py_CompileStringExFlags("x = 1\n", "<warmup>", Py_file_input, &flags, 0);
        Py_XDECREF(ast);
        PyErr_Clear();
    }

    if (activate_perf_trampoline_if_requested() < 0) {
        return 1;
    }

    int test_crash_mode = (getenv("FUZZ_TEST_CRASH") != NULL);

#ifdef __AFL_HAVE_MANUAL_CONTROL
    __AFL_INIT();
#endif

    unsigned char *buf = __AFL_FUZZ_TESTCASE_BUF;

    while (__AFL_LOOP(10000)) {
        int len = __AFL_FUZZ_TESTCASE_LEN;
        if (len > 65535) {
            len = 65535;
        }

        if (test_crash_mode &&
                memmem(buf, (size_t)len, TEST_CRASH_INPUT, TEST_CRASH_INPUT_LEN)) {
            abort();
        }

        char *src = (char *)malloc((size_t)len + 1);
        if (!src) {
            continue;
        }
        memcpy(src, buf, (size_t)len);
        src[len] = '\0';

        PyCompilerFlags flags = {PyCF_ONLY_AST, 0};
        PyObject *ast = Py_CompileStringExFlags(src, "<fuzz>", Py_file_input, &flags, 0);
        Py_XDECREF(ast);
        PyErr_Clear();

        free(src);
    }

    return 0;
}
