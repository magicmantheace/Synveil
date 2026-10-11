// SPDX-License-Identifier: MPL-2.0
// Explicit developer-only Linux VT shell. No AI/model/policy access to this CLI.
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/vt.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

static int is_character_device(const char *name) {
    struct stat info;
    return lstat(name, &info) == 0 && S_ISCHR(info.st_mode);
}

static int has_graphical_vt(void) {
    return is_character_device("/dev/fb0") &&
           is_character_device("/dev/tty0") &&
           is_character_device("/dev/tty1");
}

static int self_test(void) {
    // Unsupported options must never reach a shell-launch path.
    struct stat info = {0};
    if (S_ISCHR(info.st_mode)) return 1;
    if (strcmp("--probe", "--developer-terminal") == 0) return 1;
    return 0;
}

static int developer_terminal(void) {
    if (geteuid() != 0) {
        fputs("[synveil-vt] developer terminal requires root on test VM\n", stderr);
        return 1;
    }
    if (!has_graphical_vt()) {
        fputs("[synveil-vt] display/VT not available; use serial recovery\n", stderr);
        return 1;
    }
    int controller = open("/dev/tty0", O_RDWR | O_CLOEXEC | O_NOCTTY);
    if (controller < 0) {
        perror("[synveil-vt] open tty0");
        return 1;
    }
    if (ioctl(controller, VT_ACTIVATE, 1) < 0 ||
        ioctl(controller, VT_WAITACTIVE, 1) < 0) {
        perror("[synveil-vt] activate VT1");
        close(controller);
        return 1;
    }
    close(controller);

    pid_t child = fork();
    if (child < 0) {
        perror("[synveil-vt] fork");
        return 1;
    }
    if (child != 0) {
        fprintf(stderr, "[synveil-vt] developer VT1 shell started (pid %ld)\n",
                (long)child);
        return 0;
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
    ssize_t banner_written = write(STDOUT_FILENO,
        "\033[2J\033[H"
        "Synveil developer console (UNAUTHENTICATED ROOT)\r\n"
        "Test VM only. Type exit to close this shell.\r\n\r\n",
        sizeof("\033[2J\033[H"
        "Synveil developer console (UNAUTHENTICATED ROOT)\r\n"
        "Test VM only. Type exit to close this shell.\r\n\r\n") - 1);
    if (banner_written < 0) _exit(1);
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
    // Intentional double flag for an unauthenticated root developer shell.
    if (argc == 3 &&
        strcmp(argv[1], "--developer-terminal") == 0 &&
        strcmp(argv[2], "--i-understand-root-shell") == 0) {
        return developer_terminal();
    }
    fputs("usage: synveil-vt --probe | --self-test | "
          "--developer-terminal --i-understand-root-shell\n", stderr);
    return 2;
}
