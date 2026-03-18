/*
 * coredump_writer — kernel core dump pipe handler.
 *
 * Set via core_pattern:
 *   |/path/coredump_writer <cores_dir> %p %e %t
 *
 * The kernel streams core data to stdin; we write it to a file.
 * A compiled binary avoids the PATH-less environment the kernel provides
 * to pipe handlers, which causes shell scripts to fail when they invoke
 * external commands like cat.
 */
#include <fcntl.h>
#include <stdio.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc < 5) return 1;

    /* argv[1]=cores_dir  argv[2]=%p(pid)  argv[3]=%e(exe)  argv[4]=%t(time) */
    char path[1024];
    int n = snprintf(path, sizeof(path),
                     "%s/core.%s.%s.%s", argv[1], argv[3], argv[2], argv[4]);
    if (n < 0 || (size_t)n >= sizeof(path)) return 1;

    int fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (fd < 0) return 1;

    char buf[65536];
    ssize_t r;
    while ((r = read(STDIN_FILENO, buf, sizeof(buf))) > 0) {
        const char *p = buf;
        while (r > 0) {
            ssize_t w = write(fd, p, (size_t)r);
            if (w < 0) { close(fd); return 1; }
            p += w;
            r -= w;
        }
    }

    close(fd);
    return 0;
}
