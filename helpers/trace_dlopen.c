#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>

void *dlopen(const char *filename, int flags) {
    static void *(*real)(const char *, int);
    if (!real) real = dlsym(RTLD_NEXT, "dlopen");

    if (filename) {
        const char *log = getenv("DLOPEN_LOG");
        if (log) {
            FILE *f = fopen(log, "a");
            if (f) { fprintf(f, "%s\n", filename); fclose(f); }
        }
    }
    return real(filename, flags);
}

void *dlmopen(long lmid, const char *filename, int flags) {
    static void *(*real)(long, const char *, int);
    if (!real) real = dlsym(RTLD_NEXT, "dlmopen");

    if (filename) {
        const char *log = getenv("DLOPEN_LOG");
        if (log) {
            FILE *f = fopen(log, "a");
            if (f) { fprintf(f, "%s\n", filename); fclose(f); }
        }
    }
    return real(lmid, filename, flags);
}
