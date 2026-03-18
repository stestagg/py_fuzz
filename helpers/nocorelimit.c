/* nocorelimit.c — LD_PRELOAD shim that prevents AFL++ from zeroing
 * RLIMIT_CORE on child processes, so coredumps are actually written.
 *
 * Intercepts setrlimit, prlimit, and prlimit64 since AFL++ and glibc may
 * use any of these to reset the core limit.
 *
 * Build:
 *   gcc -shared -fPIC -o helpers/nocorelimit.so helpers/nocorelimit.c -ldl
 */
#define _GNU_SOURCE
#include <sys/resource.h>
#include <sys/types.h>
#include <dlfcn.h>

int setrlimit(__rlimit_resource_t resource, const struct rlimit *rlim) {
    static int (*real)(__rlimit_resource_t, const struct rlimit *);
    if (!real)
        real = dlsym(RTLD_NEXT, "setrlimit");
    if (resource == RLIMIT_CORE && rlim->rlim_cur == 0)
        return 0;
    return real(resource, rlim);
}

int prlimit(pid_t pid, __rlimit_resource_t resource,
            const struct rlimit *new_limit, struct rlimit *old_limit) {
    static int (*real)(pid_t, __rlimit_resource_t, const struct rlimit *, struct rlimit *);
    if (!real)
        real = dlsym(RTLD_NEXT, "prlimit");
    if (resource == RLIMIT_CORE && new_limit && new_limit->rlim_cur == 0)
        return 0;
    return real(pid, resource, new_limit, old_limit);
}
