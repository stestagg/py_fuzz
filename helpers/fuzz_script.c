#define _GNU_SOURCE
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <stdint.h>
#include <sys/types.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>
#include <fcntl.h>

#define TEST_CRASH_INPUT     "fuzztestcrash"
#define TEST_CRASH_INPUT_LEN 13

__AFL_FUZZ_INIT();

/*
 * This harness differs from fuzz_python.c: instead of treating each AFL input
 * as Python *source*, it runs a FIXED script (loaded from the FUZZ_SCRIPT env
 * var, compiled once at startup) every iteration. The raw AFL input bytes are
 * exposed to that script as a `bytes` object named FUZZ_INPUT. This is for
 * fuzzing a specific API/codepath (e.g. a decompressor) rather than the
 * compiler/parser itself.
 *
 * Tuning knobs:
 *
 * - __AFL_LOOP() keeps persistent mode fast, but state will accumulate inside
 *   CPython over time. Lower it when chasing heisenbugs.
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

/*
 * Read the entire FUZZ_SCRIPT file into a freshly allocated, NUL-terminated
 * buffer. Returns NULL on any error (after printing to stderr). Caller frees.
 */
static char *read_script_file(const char *path) {
    FILE *fp = fopen(path, "rb");
    if (!fp) {
        fprintf(stderr, "FUZZ_SCRIPT: cannot open '%s'\n", path);
        return NULL;
    }

    if (fseek(fp, 0, SEEK_END) != 0) {
        fprintf(stderr, "FUZZ_SCRIPT: cannot seek '%s'\n", path);
        fclose(fp);
        return NULL;
    }
    long size = ftell(fp);
    if (size < 0) {
        fprintf(stderr, "FUZZ_SCRIPT: cannot tell size of '%s'\n", path);
        fclose(fp);
        return NULL;
    }
    rewind(fp);

    char *buf = (char *)malloc((size_t)size + 1);
    if (!buf) {
        fprintf(stderr, "FUZZ_SCRIPT: out of memory reading '%s'\n", path);
        fclose(fp);
        return NULL;
    }

    size_t got = fread(buf, 1, (size_t)size, fp);
    fclose(fp);
    if (got != (size_t)size) {
        fprintf(stderr, "FUZZ_SCRIPT: short read on '%s'\n", path);
        free(buf);
        return NULL;
    }
    buf[size] = '\0';
    return buf;
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

    /*
     * Load and compile the fixed fuzz script once, before the forkserver.
     * The compiled code object is reused for every iteration; only the
     * FUZZ_INPUT bytes change.
     */
    const char *script_path = getenv("FUZZ_SCRIPT");
    if (!script_path || !*script_path) {
        fprintf(stderr, "FUZZ_SCRIPT environment variable is required "
                        "(path to the Python script to run).\n");
        return 1;
    }

    char *script_src = read_script_file(script_path);
    if (!script_src) {
        return 1;
    }

    PyObject *code = Py_CompileString(script_src, script_path, Py_file_input);
    free(script_src);
    if (code == NULL) {
        fprintf(stderr, "FUZZ_SCRIPT: failed to compile '%s':\n", script_path);
        PyErr_Print();
        return 1;
    }

    /*
     * Warm run: execute the script once with an empty FUZZ_INPUT so that any
     * top-level imports become resident in the parent process before the
     * forkserver starts (see FUZZ_WARMUP_IMPORTS note above). A script that
     * legitimately fails on empty input must not abort startup, so errors are
     * cleared.
     */
    {
        PyObject *globals = make_fresh_globals();
        if (globals) {
            PyObject *empty = PyBytes_FromStringAndSize("", 0);
            if (empty) {
                PyDict_SetItemString(globals, "FUZZ_INPUT", empty);
                Py_DECREF(empty);
                PyObject *result = PyEval_EvalCode(code, globals, globals);
                Py_XDECREF(result);
            }
            Py_DECREF(globals);
        }
        PyErr_Clear();
    }

    /* Read once before the forkserver so all forked children inherit the value. */
    int test_crash_mode = (getenv("FUZZ_TEST_CRASH") != NULL);
    int crash_on_memerror = (getenv("FUZZ_CRASH_ON_MEMORY_ERROR") != NULL);
    const char *track_inputs_base = getenv("FUZZ_TRACK_INPUTS");
    int do_track_inputs = track_inputs_base != NULL;
    int log_fd = -1, idx_fd = -1;
    if (do_track_inputs) {
        /* Ensure the parent directory exists (track_inputs_base is e.g. /pfm/input_tracks/a01;
         * the parent /pfm/input_tracks/ must exist before we create the .log/.idx files). */
        char parent_dir[4096];
        snprintf(parent_dir, sizeof(parent_dir), "%s", track_inputs_base);
        char *last_slash = strrchr(parent_dir, '/');
        if (last_slash && last_slash != parent_dir) {
            *last_slash = '\0';
            mkdir(parent_dir, 0755); /* ignore EEXIST */
        }

        char log_path[4096], idx_path[4096];
        snprintf(log_path, sizeof(log_path), "%s.log", track_inputs_base);
        snprintf(idx_path, sizeof(idx_path), "%s.idx", track_inputs_base);

        log_fd = open(log_path, O_WRONLY | O_CREAT | O_APPEND, 0644);
        if (log_fd < 0) {
            printf("FUZZ_TRACK_INPUTS: failed to open log: %s\n", log_path);
            abort();
        }
        idx_fd = open(idx_path, O_WRONLY | O_CREAT | O_APPEND, 0644);
        if (idx_fd < 0) {
            printf("FUZZ_TRACK_INPUTS: failed to open idx: %s\n", idx_path);
            abort();
        }
        printf("FUZZ_TRACK_INPUTS: Tracking fuzz inputs in: %s\n", log_path);
    } else {
        printf("FUZZ_TRACK_INPUTS is not set, fuzz inputs will not be saved to disk.\n");
    }

    /*
     * Deferred forkserver starts here, after Python init and warm-up.
     */
#ifdef __AFL_HAVE_MANUAL_CONTROL
    __AFL_INIT();
#endif

    uint32_t track_pid = 0;
    if (do_track_inputs) {
        struct timeval tv;
        gettimeofday(&tv, NULL);
        uint64_t ts_us = (uint64_t)tv.tv_sec * 1000000ULL + (uint64_t)tv.tv_usec;
        track_pid = (uint32_t)getpid();
        uint64_t log_off = (uint64_t)lseek(log_fd, 0, SEEK_END);

        unsigned char idx_buf[20];
        memcpy(idx_buf + 0,  &track_pid, 4);
        memcpy(idx_buf + 4,  &ts_us,     8);
        memcpy(idx_buf + 12, &log_off,   8);
        write(idx_fd, idx_buf, 20);
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

        if (log_fd >= 0) {
            struct timeval tv;
            gettimeofday(&tv, NULL);
            uint64_t ts_us = (uint64_t)tv.tv_sec * 1000000ULL + (uint64_t)tv.tv_usec;
            uint16_t magic = 0xF00D;
            uint16_t ilen  = (uint16_t)len;
            size_t rec_len = 16 + (size_t)ilen;
            unsigned char *rec = (unsigned char *)malloc(rec_len);
            if (rec) {
                memcpy(rec + 0,  &magic,      2);
                memcpy(rec + 2,  &track_pid,  4);
                memcpy(rec + 6,  &ts_us,      8);
                memcpy(rec + 14, &ilen,        2);
                memcpy(rec + 16, buf,          ilen);
                write(log_fd, rec, rec_len);
                free(rec);
            }
        }

        /*
         * The AFL input is data, not source: expose the raw bytes (including
         * any embedded NULs) to the script as FUZZ_INPUT, then run the
         * pre-compiled script in a fresh namespace.
         */
        PyObject *input = PyBytes_FromStringAndSize((char *)buf, len);
        if (!input) {
            PyErr_Clear();
            continue;
        }

        PyObject *globals = make_fresh_globals();
        if (globals) {
            if (PyDict_SetItemString(globals, "FUZZ_INPUT", input) == 0) {
                PyObject *result = PyEval_EvalCode(code, globals, globals);
                Py_XDECREF(result);
            } else {
                PyErr_Clear();
            }
            Py_DECREF(globals);
        } else {
            PyErr_Clear();
        }
        Py_DECREF(input);

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
            /* Optionally surface MemoryError as a crash. Must be checked while
             * the error indicator is still set, before it is cleared. */
            if (crash_on_memerror && PyErr_ExceptionMatches(PyExc_MemoryError)) {
                fprintf(stderr, "FUZZ_CRASH_ON_MEMORY_ERROR: MemoryError raised, aborting.\n");
                fflush(stderr);
                abort();
            }
            PyErr_Clear();
        }

        if (MODULE_CLEANUP_EVERY > 0 && (iter % MODULE_CLEANUP_EVERY) == 0) {
            cleanup_sys_modules();
            PyErr_Clear();
        }

        maybe_collect_gc(iter);
        PyErr_Clear();
    }

    if (log_fd >= 0) {
        close(log_fd);
        close(idx_fd);
    }

    Py_DECREF(code);
    return 0;
}
