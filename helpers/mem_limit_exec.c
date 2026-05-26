#include <sys/resource.h>
#include <stdlib.h>
#include <stdio.h>
#include <unistd.h>
#include <fcntl.h>

/*
 * Launcher used by analyze.py to replay crashes under AFL's recorded memory
 * limit.  MEM_LIMIT_MB is required.  If MEM_LIMIT_EXEC is set, exec's that
 * target with the original argv; otherwise treats argv[1] as the target
 * (i.e. mem_limit_exec /usr/bin/python foo.py).
 * LLDB follows the exec and debugs the real harness binary.
 */
int main(int argc, char *argv[]) {
    const char *limit_str = getenv("MEM_LIMIT_MB");
    const char *exec_target = getenv("MEM_LIMIT_EXEC");
    char **exec_argv;

    if (!limit_str) {
        fprintf(stderr, "mem_limit_exec: MEM_LIMIT_MB not set\n");
        return 1;
    }

    if (exec_target) {
        exec_argv = argv;
    } else {
        if (argc < 2) {
            fprintf(stderr, "mem_limit_exec: MEM_LIMIT_EXEC not set and no argv[1]\n");
            return 1;
        }
        exec_target = argv[1];
        exec_argv = argv + 1;
    }

    rlim_t limit = (rlim_t)atol(limit_str) * 1024 * 1024;
    struct rlimit rl = { limit, limit };
    setrlimit(RLIMIT_AS, &rl);

    execv(exec_target, exec_argv);
    perror("mem_limit_exec: execv");
    return 1;
}
