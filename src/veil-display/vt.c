// SPDX-License-Identifier: MPL-2.0
// Local VM terminal on Linux fbcon. No model-to-shell execution path.
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <linux/vt.h>
#include <poll.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include "lifetime.h"

#define DEMO_UID 1000
#define DEMO_GID 1000

static int is_character_device(const char *name) {
    struct stat info;
    return lstat(name, &info) == 0 && S_ISCHR(info.st_mode);
}

static int has_graphical_vt(void) {
    return is_character_device("/dev/fb0") &&
           is_character_device("/dev/tty0") &&
           is_character_device("/dev/tty1");
}

static int is_demo_mode(int argc, char **argv) {
    return argc == 2 && strcmp(argv[1], "--demo-terminal") == 0;
}

static int is_authenticated_mode(int argc, char **argv) {
    return argc == 2 && strcmp(argv[1], "--authenticated-terminal") == 0;
}

static int is_root_debug_mode(int argc, char **argv) {
    return argc == 3 &&
           strcmp(argv[1], "--developer-terminal") == 0 &&
           strcmp(argv[2], "--i-understand-root-shell") == 0;
}

// VT_WAITACTIVE can block forever when fbcon/VT activation breaks. Every
// launcher (including the authenticated one) has a finite wait budget.
#define VT_START_TIMEOUT_SECONDS 2
#define VT_START_POLL_MS 25

static int deadline_reached(const struct timespec *deadline,
                            const struct timespec *now) {
    return now->tv_sec > deadline->tv_sec ||
           (now->tv_sec == deadline->tv_sec && now->tv_nsec >= deadline->tv_nsec);
}

static int activate_terminal_vt(int fd, unsigned vt) {
    if (ioctl(fd, VT_ACTIVATE, vt) != 0) return -1;
    struct timespec deadline;
    if (clock_gettime(CLOCK_MONOTONIC, &deadline) != 0) return -1;
    deadline.tv_sec += VT_START_TIMEOUT_SECONDS;
    for (;;) {
        struct vt_stat current = {0};
        if (ioctl(fd, VT_GETSTATE, &current) != 0) return -1;
        if (current.v_active == vt) return 0;
        struct timespec now;
        if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return -1;
        if (deadline_reached(&deadline, &now)) {
            errno = ETIMEDOUT;
            return -1;
        }
        if (poll(NULL, 0u, VT_START_POLL_MS) < 0 && errno != EINTR) return -1;
    }
}

static int self_test(void) {
    char *demo[] = {"synveil-vt", "--demo-terminal", NULL};
    char *authenticated[] = {"synveil-vt", "--authenticated-terminal", NULL};
    char *unconfirmed[] = {"synveil-vt", "--developer-terminal", NULL};
    char *confirmed[] = {"synveil-vt", "--developer-terminal",
                         "--i-understand-root-shell", NULL};
    if (!is_demo_mode(2, demo) || !is_authenticated_mode(2, authenticated) ||
        is_authenticated_mode(2, demo) || is_root_debug_mode(2, unconfirmed) ||
        !is_root_debug_mode(3, confirmed) || is_root_debug_mode(2, demo)) {
        return 1;
    }
    struct timespec cutoff = {.tv_sec = 105, .tv_nsec = 420000000};
    struct timespec before = {.tv_sec = 105, .tv_nsec = 419999999};
    struct timespec exactly = {.tv_sec = 105, .tv_nsec = 420000000};
    struct timespec after = {.tv_sec = 106, .tv_nsec = 0};
    if (deadline_reached(&cutoff, &before) ||
        !deadline_reached(&cutoff, &exactly) ||
        !deadline_reached(&cutoff, &after)) return 1;
    return DEMO_UID != 1000 || DEMO_GID != 1000;
}

static void configure_demo_user(void) {
    // Restrict the development shell to a dedicated, unprivileged identity.
    // No inherited root groups, environment, or new privileged execs.
    if (chdir("/home/synveil") < 0 ||
        setgroups(0, NULL) < 0 ||
        setgid(DEMO_GID) < 0 ||
        setuid(DEMO_UID) < 0 ||
        prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) < 0 ||
        clearenv() != 0 ||
        setenv("HOME", "/home/synveil", 1) < 0 ||
        setenv("USER", "synveil", 1) < 0 ||
        setenv("LOGNAME", "synveil", 1) < 0 ||
        setenv("SHELL", "/bin/sh", 1) < 0 ||
        setenv("TERM", "linux", 1) < 0 ||
        setenv("PATH", "/bin:/usr/bin", 1) < 0) {
        _exit(1);
    }
    umask(0077);
}

static int launch_terminal(bool demo, bool authenticated) {
    if (geteuid() != 0) {
        fputs("[synveil-vt] VM terminal launcher requires root\n", stderr);
        return 1;
    }
    if (!has_graphical_vt()) {
        fputs("[synveil-vt] framebuffer/VT unavailable; use serial recovery\n", stderr);
        return 1;
    }
    int controller = open("/dev/tty0", O_RDWR | O_CLOEXEC | O_NOCTTY);
    if (controller < 0) {
        perror("[synveil-vt] open tty0");
        return 1;
    }
    int activated = activate_terminal_vt(controller, 1u);
    close(controller);
    if (activated != 0) {
        perror("[synveil-vt] activate tty1");
        return 1;
    }
    pid_t launcher_pid = getpid();
    pid_t child = fork();
    if (child < 0) {
        perror("[synveil-vt] fork");
        return 1;
    }
    if (child != 0) {
        if (!demo) {
            fprintf(stderr, "[synveil-vt] root developer shell pid %ld\n", (long)child);
            return 0;
        }
        // Keep the framebuffer launcher suspended until this demo shell exits.
        int status = 0;
        while (waitpid(child, &status, 0) < 0) {
            if (errno == EINTR) continue;
            return 1;
        }
        return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
    }

    if (setsid() < 0) _exit(1);
    int terminal = open("/dev/tty1", O_RDWR | O_NOCTTY);
    if (terminal < 0 || ioctl(terminal, TIOCSCTTY, 0) < 0 ||
        dup2(terminal, STDIN_FILENO) < 0 ||
        dup2(terminal, STDOUT_FILENO) < 0 ||
        dup2(terminal, STDERR_FILENO) < 0) {
        _exit(1);
    }
    if (terminal > STDERR_FILENO) close(terminal);
    const char *banner = authenticated ?
        "\033[2J\033[H"
        "Synveil local console (AUTHENTICATED NON-ROOT)\r\n"
        "VM evaluation only. Type exit to lock the graphical seat.\r\n\r\n" :
        demo ?
        "\033[2J\033[H"
        "Synveil demo console (NON-ROOT, NOT AUTHENTICATED)\r\n"
        "VM evaluation only. Type exit to return to the menu.\r\n\r\n" :
        "\033[2J\033[H"
        "Synveil developer console (UNAUTHENTICATED ROOT)\r\n"
        "Local test VM only. Type exit to close this shell.\r\n\r\n";
    size_t length = strlen(banner);
    if (write(STDOUT_FILENO, banner, length) != (ssize_t)length) _exit(1);
    if (demo) {
        configure_demo_user();
        // setuid clears Linux PDEATHSIG; rearm it only after UID/GID drop.
        // The helper exiting or being killed must terminate this shell.
        if (synveil_parent_death_guard(launcher_pid) != 0) _exit(1);
    }
    execl("/bin/sh", "sh", "-i", (char *)NULL);
    _exit(127);
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--self-test") == 0) return self_test();
    if (argc == 2 && strcmp(argv[1], "--probe") == 0) {
        if (has_graphical_vt()) {
            puts("SYNVEIL_VT_READY");
            return 0;
        }
        fputs("[synveil-vt] framebuffer or virtual terminal unavailable\n", stderr);
        return 1;
    }
    if (is_demo_mode(argc, argv)) return launch_terminal(true, false);
    if (is_authenticated_mode(argc, argv)) return launch_terminal(true, true);
    if (is_root_debug_mode(argc, argv)) return launch_terminal(false, false);
    fputs("usage: synveil-vt --probe | --self-test | --demo-terminal | "
          "--authenticated-terminal | --developer-terminal --i-understand-root-shell\n", stderr);
    return 2;
}
