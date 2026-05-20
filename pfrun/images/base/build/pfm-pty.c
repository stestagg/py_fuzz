#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pty.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <termios.h>
#include <unistd.h>

static struct termios saved_termios;
static int saved_termios_valid = 0;
static int tty_fd = -1;
static int master_fd = -1;
static pid_t child_pid = -1;

static void die(const char *msg) {
    perror(msg);
    exit(127);
}

static void restore_terminal(void) {
    if (saved_termios_valid && tty_fd >= 0) {
        tcsetattr(tty_fd, TCSANOW, &saved_termios);
    }
}

static void set_rawish(int fd) {
    struct termios t;

    if (tcgetattr(fd, &saved_termios) == 0) {
        saved_termios_valid = 1;
        t = saved_termios;
        cfmakeraw(&t);
        t.c_iflag |= ICRNL;
        t.c_oflag |= OPOST;
        tcsetattr(fd, TCSANOW, &t);
    }
}

static void copy_winsize(int from_fd, int to_fd) {
    struct winsize ws;

    if (ioctl(from_fd, TIOCGWINSZ, &ws) == 0) {
        ioctl(to_fd, TIOCSWINSZ, &ws);
    }
}

static void on_signal(int sig) {
    if (sig == SIGWINCH) {
        if (tty_fd >= 0 && master_fd >= 0)
            copy_winsize(tty_fd, master_fd);
        if (child_pid > 0)
            kill(child_pid, SIGWINCH);
        return;
    }

    if (child_pid > 0)
        kill(child_pid, sig);
}

static int write_all(int fd, const void *buf, size_t len) {
    const char *p = buf;

    while (len > 0) {
        ssize_t n = write(fd, p, len);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            return -1;
        }
        if (n == 0)
            return -1;
        p += n;
        len -= (size_t)n;
    }

    return 0;
}

static void usage(const char *argv0) {
    fprintf(stderr, "usage: %s /dev/hvc1 -- command [args...]\n", argv0);
    exit(2);
}

int main(int argc, char **argv) {
    int slave_fd;
    int sep = -1;
    struct winsize ws;
    char buf[8192];

    if (argc < 4)
        usage(argv[0]);

    for (int i = 2; i < argc; i++) {
        if (strcmp(argv[i], "--") == 0) {
            sep = i;
            break;
        }
    }

    if (sep < 0 || sep + 1 >= argc)
        usage(argv[0]);

    tty_fd = open(argv[1], O_RDWR | O_NOCTTY);
    if (tty_fd < 0)
        die("open tty");

    memset(&ws, 0, sizeof(ws));
    ioctl(tty_fd, TIOCGWINSZ, &ws);

    if (openpty(&master_fd, &slave_fd, NULL, NULL, &ws) < 0)
        die("openpty");

    child_pid = fork();
    if (child_pid < 0)
        die("fork");

    if (child_pid == 0) {
        close(master_fd);

        if (setsid() < 0)
            die("setsid");

        if (ioctl(slave_fd, TIOCSCTTY, 0) < 0)
            die("TIOCSCTTY");

        if (dup2(slave_fd, STDIN_FILENO) < 0)
            die("dup2 stdin");
        if (dup2(slave_fd, STDOUT_FILENO) < 0)
            die("dup2 stdout");
        if (dup2(slave_fd, STDERR_FILENO) < 0)
            die("dup2 stderr");

        if (slave_fd > STDERR_FILENO)
            close(slave_fd);

        execvp(argv[sep + 1], &argv[sep + 1]);
        die("execvp");
    }

    close(slave_fd);

    atexit(restore_terminal);
    set_rawish(tty_fd);
    copy_winsize(tty_fd, master_fd);

    signal(SIGWINCH, on_signal);
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    signal(SIGHUP, on_signal);

    for (;;) {
        struct pollfd fds[2];

        fds[0].fd = tty_fd;
        fds[0].events = POLLIN;

        fds[1].fd = master_fd;
        fds[1].events = POLLIN;

        int r = poll(fds, 2, -1);
        if (r < 0) {
            if (errno == EINTR)
                continue;
            break;
        }

        if (fds[0].revents & POLLIN) {
            ssize_t n = read(tty_fd, buf, sizeof(buf));
            if (n <= 0)
                break;
            if (write_all(master_fd, buf, (size_t)n) < 0)
                break;
        }

        if (fds[1].revents & POLLIN) {
            ssize_t n = read(master_fd, buf, sizeof(buf));
            if (n <= 0)
                break;
            if (write_all(tty_fd, buf, (size_t)n) < 0)
                break;
        }

        if ((fds[0].revents | fds[1].revents) & (POLLHUP | POLLERR | POLLNVAL))
            break;

        int status;
        pid_t p = waitpid(child_pid, &status, WNOHANG);
        if (p == child_pid) {
            restore_terminal();
            if (WIFEXITED(status))
                return WEXITSTATUS(status);
            if (WIFSIGNALED(status))
                return 128 + WTERMSIG(status);
            return 1;
        }
    }

    if (child_pid > 0)
        kill(child_pid, SIGHUP);

    int status;
    waitpid(child_pid, &status, 0);
    restore_terminal();

    if (WIFEXITED(status))
        return WEXITSTATUS(status);
    if (WIFSIGNALED(status))
        return 128 + WTERMSIG(status);

    return 1;
}