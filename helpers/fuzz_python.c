#define _GNU_SOURCE
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <stdint.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#define TEST_CRASH_INPUT     "fuzztestcrash"
#define TEST_CRASH_INPUT_LEN 13

__AFL_FUZZ_INIT();

/*
 * Tuning knobs:
 *
 * - __AFL_LOOP(10000) keeps persistent mode fast, but state will accumulate
 *   inside CPython over time. Lower it when chasing heisenbugs.
 *
 * - MODULE_CLEANUP_EVERY removes newly imported modules from sys.modules every
 *   N iterations. This reduces cross-iteration contamination without doing a
 *   full interpreter restart.
 *
 * - GC_COLLECT_EVERY runs gc.collect() every N iterations to clean up cyclic
 *   garbage that refcounting alone will not reclaim immediately.
 */
#ifndef MODULE_CLEANUP_EVERY
#define MODULE_CLEANUP_EVERY 16
#endif

#ifndef GC_COLLECT_EVERY
#define GC_COLLECT_EVERY 64
#endif

static PyObject *g_baseline_builtins = NULL; /* dict */
static PyObject *g_baseline_modules  = NULL; /* set of module names */

/*
 * Snapshot the current sys.modules keys into a set.
 * Used after startup so we can later remove modules imported by fuzzed code.
 */
static PyObject *snapshot_sys_modules_keys(void) {
    PyObject *sys_modules = PyImport_GetModuleDict(); /* borrowed */
    if (!sys_modules) {
        return NULL;
    }

    PyObject *keys = PyMapping_Keys(sys_modules);
    if (!keys) {
        return NULL;
    }

    PyObject *set = PySet_New(NULL);
    if (!set) {
        Py_DECREF(keys);
        return NULL;
    }

    Py_ssize_t n = PyList_GET_SIZE(keys);
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *key = PyList_GET_ITEM(keys, i); /* borrowed */
        if (PyUnicode_Check(key)) {
            if (PySet_Add(set, key) < 0) {
                Py_DECREF(set);
                Py_DECREF(keys);
                return NULL;
            }
        }
    }

    Py_DECREF(keys);
    return set;
}

/*
 * Remove any modules from sys.modules that were not present in the startup
 * snapshot. This is not a full reset, but it helps prevent imports during one
 * iteration from changing later iterations too much.
 */
static void cleanup_sys_modules(void) {
    if (!g_baseline_modules) {
        return;
    }

    PyObject *sys_modules = PyImport_GetModuleDict(); /* borrowed */
    if (!sys_modules) {
        PyErr_Clear();
        return;
    }

    PyObject *keys = PyMapping_Keys(sys_modules);
    if (!keys) {
        PyErr_Clear();
        return;
    }

    Py_ssize_t n = PyList_GET_SIZE(keys);
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *key = PyList_GET_ITEM(keys, i); /* borrowed */
        if (!PyUnicode_Check(key)) {
            continue;
        }

        int in_baseline = PySet_Contains(g_baseline_modules, key);
        if (in_baseline == 0) {
            if (PyDict_DelItem(sys_modules, key) < 0) {
                PyErr_Clear();
            }
        } else if (in_baseline < 0) {
            PyErr_Clear();
        }
    }

    Py_DECREF(keys);
}

/*
 * Run gc.collect() occasionally to reclaim cyclic garbage created by fuzzed
 * code. Doing this every iteration is usually too expensive.
 */
static void maybe_collect_gc(unsigned long iter) {
    if (GC_COLLECT_EVERY <= 0 || (iter % GC_COLLECT_EVERY) != 0) {
        return;
    }

    PyObject *gc_mod = PyImport_ImportModule("gc");
    if (!gc_mod) {
        PyErr_Clear();
        return;
    }

    PyObject *result = PyObject_CallMethod(gc_mod, "collect", NULL);
    Py_XDECREF(result);
    Py_DECREF(gc_mod);
    PyErr_Clear();
}

/*
 * Build a fresh globals dict for each iteration.
 *
 * Important: we do NOT expose the live interpreter builtins dict directly.
 * Instead we copy the baseline builtins snapshot taken after startup/warmup.
 * That prevents fuzzed code from permanently mutating builtins across runs.
 */
static PyObject *make_fresh_globals(void) {
    PyObject *globals = PyDict_New();
    if (!globals) {
        return NULL;
    }

    PyObject *builtins_copy = PyDict_Copy(g_baseline_builtins);
    if (!builtins_copy) {
        Py_DECREF(globals);
        return NULL;
    }

    if (PyDict_SetItemString(globals, "__builtins__", builtins_copy) < 0) {
        Py_DECREF(builtins_copy);
        Py_DECREF(globals);
        return NULL;
    }

    Py_DECREF(builtins_copy);
    return globals;
}

int main(int argc, char **argv) {
    (void)argc;
    (void)argv;

    /*
     * One-time Python initialization, done before AFL's deferred forkserver
     * starts. Child processes inherit this already-initialized interpreter.
     */
    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    config.install_signal_handlers = 0;
    config.write_bytecode = 0;
    #ifdef Py_DEBUG_PARSER
    config.parser_debug = 1;
    #endif

    /* PyConfig_InitIsolatedConfig sets use_environment=0, so PYTHONHOME is
     * silently ignored.  Read it manually so the harness is relocatable
     * (e.g. when dist/ is mounted at a different path inside Docker). */
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

    {
        PyObject *code = Py_CompileString("x = 1\n", "<warmup>", Py_file_input);
        if (code) {
            PyObject *globals = PyDict_New();
            if (globals) {
                PyObject *builtins = PyEval_GetBuiltins(); /* borrowed */
                if (builtins) {
                    PyObject *builtins_copy = PyDict_Copy(builtins);
                    if (builtins_copy) {
                        PyDict_SetItemString(globals, "__builtins__", builtins_copy);
                        Py_DECREF(builtins_copy);
                    }
                }

                PyObject *result = PyEval_EvalCode(code, globals, globals);
                Py_XDECREF(result);
                Py_DECREF(globals);
            }
            Py_DECREF(code);
        }
        PyErr_Clear();
    }

    PyRun_SimpleString(
        // "import bz2, csv, ctypes, heapq, lzma, "
        // "struct, binascii, fcntl, math, "
        // "pyexpat, select, termios, unicodedata, zlib"
        // "from xml.parsers import expat"
        "import binascii, unicodedata, faulthandler"
    );
    PyErr_Clear();

    /*
     * Pre-import modules listed in FUZZ_WARMUP_IMPORTS (comma-separated) so
     * their .so files are resident in the process before the AFL forkserver
     * starts.  Without this, afl-clang-fast-instrumented extension modules
     * loaded via dlopen() inside __AFL_LOOP trigger the fatal
     * "forkserver is already up, but an instrumented dlopen() library loaded
     * afterwards" error even when ASAN is disabled.
     */
    {
        const char *warmup = getenv("FUZZ_WARMUP_IMPORTS");
        if (warmup && *warmup) {
            char *buf = strdup(warmup);
            if (buf) {
                char *saveptr = NULL;
                char *tok = strtok_r(buf, ",", &saveptr);
                while (tok) {
                    while (*tok == ' ' || *tok == '\t') tok++;
                    if (*tok) {
                        PyObject *mod = PyImport_ImportModule(tok);
                        if (mod) {
                            Py_DECREF(mod);
                        } else {
                            PyErr_Clear();
                        }
                    }
                    tok = strtok_r(NULL, ",", &saveptr);
                }
                free(buf);
            }
        }
    }

    /*
     * Capture baseline builtins and sys.modules after warm-up.
     * These are used to reduce cross-iteration contamination.
     */
    {
        PyObject *builtins = PyEval_GetBuiltins(); /* borrowed */
        if (!builtins || !PyDict_Check(builtins)) {
            PyErr_Clear();
            return 1;
        }

        g_baseline_builtins = PyDict_Copy(builtins);
        if (!g_baseline_builtins) {
            PyErr_Clear();
            return 1;
        }

        g_baseline_modules = snapshot_sys_modules_keys();
        if (!g_baseline_modules) {
            Py_DECREF(g_baseline_builtins);
            g_baseline_builtins = NULL;
            PyErr_Clear();
            return 1;
        }
    }

    /* Read once before the forkserver so all forked children inherit the value. */
    int test_crash_mode = (getenv("FUZZ_TEST_CRASH") != NULL);
    const char *track_inputs_base = getenv("FUZZ_TRACK_INPUTS");
    int do_track_inputs = track_inputs_base != NULL;
    if (do_track_inputs) {
        printf("FUZZ_TRACK_INPUTS is set, fuzz inputs will be saved to subdirectories of: %s\n", track_inputs_base);
        int mkdir_result = mkdir(track_inputs_base, 0755);
        if (mkdir_result != 0 && errno != EEXIST) {
            printf("FUZZ_TRACK_INPUTS is set but failed to create directory: %s\n", track_inputs_base);
            abort();
        }
    } else {
        printf("FUZZ_TRACK_INPUTS is not set, fuzz inputs will not be saved to disk.\n");
    }

    /*
     * Deferred forkserver starts here, after Python init and warm-up.
     */
#ifdef __AFL_HAVE_MANUAL_CONTROL
    __AFL_INIT();
#endif

    FILE *track_fp = NULL;
    if (do_track_inputs) {
        char track_path[4096];
        pid_t pid = getpid();
        snprintf(track_path, sizeof(track_path),
                 "%s/%d.inputs", track_inputs_base, (int)pid);
        track_fp = fopen(track_path, "wb");
        if (!track_fp) {
            printf("FUZZ_TRACK_INPUTS: failed to open: %s\n", track_path);
            abort();
        }
        printf("FUZZ_TRACK_INPUTS: Tracking fuzz inputs in: %s\n", track_path);
    }

    unsigned char *buf = __AFL_FUZZ_TESTCASE_BUF;
    unsigned long iter = 0;

    while (__AFL_LOOP(1000)) {
        iter++;

        int len = __AFL_FUZZ_TESTCASE_LEN;
        if (len > 65535) {
            len = 65535;
        }

        /* Intentional crash for end-to-end verification of crash detection. */
        if (test_crash_mode &&
                memmem(buf, (size_t)len, TEST_CRASH_INPUT, TEST_CRASH_INPUT_LEN)) {
            abort();
        }

        if (track_fp) {
            uint32_t input_len = (uint32_t)len;
            static const char null_pad[6] = {0};
            fwrite(&input_len, sizeof(uint32_t), 1, track_fp);
            fwrite("\n", 1, 1, track_fp);
            fwrite(buf, 1, (size_t)len, track_fp);
            fwrite("\n", 1, 1, track_fp);
            fwrite(null_pad, 1, 6, track_fp);
            fwrite("\n", 1, 1, track_fp);
            fflush(track_fp);
        }

        /* Py_CompileString is NUL-terminated; inputs with embedded NULs would
         * be silently truncated, hurting reproducibility. Skip them instead. */
        if (memchr(buf, '\0', (size_t)len) != NULL) {
            continue;
        }

        char *src = (char *)malloc((size_t)len + 1);
        if (!src) {
            PyErr_Clear();
            continue;
        }

        memcpy(src, buf, (size_t)len);
        src[len] = '\0';

        /*
         * Compile first, then eval if compilation succeeded.
         * Eval is intentionally included in the fuzz cycle.
         */
        PyObject *code = Py_CompileString(src, "<fuzz>", Py_file_input);
        if (code != NULL) {
            PyObject *globals = make_fresh_globals();
            if (globals) {
                PyObject *result = PyEval_EvalCode(code, globals, globals);
                Py_XDECREF(result);
                Py_DECREF(globals);
            } else {
                PyErr_Clear();
            }
            Py_DECREF(code);
        }

        /*
         * Best-effort cleanup:
         *
         * - clear any pending exception
         * - periodically drop newly imported modules
         * - periodically run gc.collect()
         *
         * This does not make iterations fully isolated, but it cuts down on
         * some common persistent-mode contamination at moderate cost.
         */
        if (PyErr_Occurred()) {
            PyErr_Clear();
        }

        if (MODULE_CLEANUP_EVERY > 0 && (iter % MODULE_CLEANUP_EVERY) == 0) {
            cleanup_sys_modules();
            PyErr_Clear();
        }

        maybe_collect_gc(iter);
        PyErr_Clear();

        free(src);
    }

    if (track_fp) {
        fclose(track_fp);
    }

    return 0;
}