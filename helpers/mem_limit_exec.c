#include <sys/resource.h>
#include <stdlib.h>
#include <stdio.h>
#include <unistd.h>

/*
 * Launcher used by analyze.py to replay crashes under AFL's recorded memory
 * limit.  Sets RLIMIT_AS from MEM_LIMIT_KB then exec's MEM_LIMIT_EXEC.
 * LLDB follows the exec and debugs the real harness binary.
 */
int main(int argc, char *argv[]) {
    (void)argc;
    const char *limit_str = getenv("MEM_LIMIT_KB");
    const char *exec_target = getenv("MEM_LIMIT_EXEC");

    if (!exec_target) {
        fprintf(stderr, "mem_limit_exec: MEM_LIMIT_EXEC not set\n");
        return 1;
    }

    if (limit_str) {
        rlim_t limit = (rlim_t)atol(limit_str) * 1024;
        struct rlimit rl = { limit, limit };
        setrlimit(RLIMIT_AS, &rl);
    }

    execv(exec_target, argv);
    perror("mem_limit_exec: execv");
    return 1;
}
