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

    /* Read once before the forkserver so all forked children inherit the value. */
    int test_crash_mode = (getenv("FUZZ_TEST_CRASH") != NULL);
    int crash_on_memerror = (getenv("FUZZ_CRASH_ON_MEMORY_ERROR") != NULL);
    const char *track_inputs_base = getenv("FUZZ_TRACK_INPUTS");
    int do_track_inputs = track_inputs_base != NULL;
    int log_fd = -1, idx_fd = -1;

    /* Output capture: redirect the fuzzed code's stdout/stderr into our own
     * per-worker files so they survive AFL (which otherwise swallows them) and
     * can be sliced per-pid during analysis. Always on when the base path is
     * set. <base>-stdout.log / <base>-stderr.log hold the raw streams;
     * <base>.idx records, per forked child, where that child's region begins. */
    const char *capture_output_base = getenv("FUZZ_CAPTURE_OUTPUT");
    int do_capture_output = capture_output_base != NULL;
    int cap_out_fd = -1, cap_err_fd = -1, cap_idx_fd = -1, saved_stderr_fd = -1;
    if (do_capture_output) {
        char parent_dir[4096];
        snprintf(parent_dir, sizeof(parent_dir), "%s", capture_output_base);
        char *last_slash = strrchr(parent_dir, '/');
        if (last_slash && last_slash != parent_dir) {
            *last_slash = '\0';
            mkdir(parent_dir, 0755); /* ignore EEXIST */
        }

        char out_path[4096], err_path[4096], cidx_path[4096];
        snprintf(out_path, sizeof(out_path), "%s-stdout.log", capture_output_base);
        snprintf(err_path, sizeof(err_path), "%s-stderr.log", capture_output_base);
        snprintf(cidx_path, sizeof(cidx_path), "%s.idx", capture_output_base);

        cap_out_fd = open(out_path, O_WRONLY | O_CREAT | O_APPEND, 0644);
        cap_err_fd = open(err_path, O_WRONLY | O_CREAT | O_APPEND, 0644);
        cap_idx_fd = open(cidx_path, O_WRONLY | O_CREAT | O_APPEND, 0644);
        if (cap_out_fd < 0 || cap_err_fd < 0 || cap_idx_fd < 0) {
            printf("FUZZ_CAPTURE_OUTPUT: failed to open capture files under: %s\n",
                   capture_output_base);
            abort();
        }
        printf("FUZZ_CAPTURE_OUTPUT: Capturing harness stdout/stderr to: %s-{stdout,stderr}.log\n",
               capture_output_base);
    } else {
        printf("FUZZ_CAPTURE_OUTPUT is not set, harness stdout/stderr will not be captured.\n");
    }
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

    if (do_capture_output) {
        struct timeval tv;
        gettimeofday(&tv, NULL);
        uint64_t ts_us = (uint64_t)tv.tv_sec * 1000000ULL + (uint64_t)tv.tv_usec;
        uint32_t cap_pid = (uint32_t)getpid();
        uint64_t out_off = (uint64_t)lseek(cap_out_fd, 0, SEEK_END);
        uint64_t err_off = (uint64_t)lseek(cap_err_fd, 0, SEEK_END);

        uint16_t magic = 0xF00D;
        unsigned char cidx_buf[30];
        memcpy(cidx_buf + 0,  &magic,   2);
        memcpy(cidx_buf + 2,  &cap_pid, 4);
        memcpy(cidx_buf + 6,  &ts_us,   8);
        memcpy(cidx_buf + 14, &out_off, 8);
        memcpy(cidx_buf + 22, &err_off, 8);
        write(cap_idx_fd, cidx_buf, 30);

        /* Flush any buffered C stdio first, so earlier diagnostics go to the
         * real stdout/stderr rather than leaking into the capture files after
         * the redirect. */
        fflush(stdout);
        fflush(stderr);

        /* Keep a handle on the original stderr for harness diagnostics, then
         * point fd 1/2 at the capture files so all fuzzed-code output lands
         * there. Buffering is disabled via PYTHONUNBUFFERED so writes survive a
         * crash. */
        saved_stderr_fd = dup(2);
        dup2(cap_out_fd, 1);
        dup2(cap_err_fd, 2);
    }

    unsigned char *buf = __AFL_FUZZ_TESTCASE_BUF;

    while (__AFL_LOOP(10000)) {
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

        /* Py_CompileStringExFlags is NUL-terminated; inputs with embedded NULs
         * would be silently truncated, hurting reproducibility. Skip them so a
         * tracked input parses the same bytes the harness saw. */
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

        PyCompilerFlags flags = {PyCF_ONLY_AST, 0};
        PyObject *ast = Py_CompileStringExFlags(src, "<fuzz>", Py_file_input, &flags, 0);
        Py_XDECREF(ast);

        if (PyErr_Occurred()) {
            /* Optionally surface MemoryError as a crash. Must be checked while
             * the error indicator is still set, before it is cleared. */
            if (crash_on_memerror && PyErr_ExceptionMatches(PyExc_MemoryError)) {
                /* Route to the original stderr (fd 2 may now be a capture file). */
                const char *msg = "FUZZ_CRASH_ON_MEMORY_ERROR: MemoryError raised, aborting.\n";
                dprintf(saved_stderr_fd >= 0 ? saved_stderr_fd : 2, "%s", msg);
                abort();
            }
            PyErr_Clear();
        }

        free(src);
    }

    if (log_fd >= 0) {
        close(log_fd);
        close(idx_fd);
    }

    if (do_capture_output) {
        close(cap_out_fd);
        close(cap_err_fd);
        close(cap_idx_fd);
        if (saved_stderr_fd >= 0)
            close(saved_stderr_fd);
    }

    return 0;
}
