// SPDX-License-Identifier: MPL-2.0
// Host-only Linux process fixture; no framebuffer or privileged user needed.
#define _GNU_SOURCE
#include "lifetime.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    if (strcmp(argv[1], "--wrong-parent") == 0)
        return synveil_parent_death_guard(getppid() + 1) == -1 ? 0 : 1;
    if (strcmp(argv[1], "--relay") == 0) {
        pid_t owner = getpid();
        pid_t leaf = fork();
        if (leaf < 0) return 1;
        if (leaf == 0) {
            if (synveil_parent_death_guard(owner) != 0) _exit(3);
            printf("LEAF %ld\n", (long)getpid());
            fflush(stdout);
            for (;;) pause();
        }
        for (;;) pause();
    }
    if (strcmp(argv[1], "--chain") == 0) {
        pid_t owner = getpid();
        pid_t middle = fork();
        if (middle < 0) return 1;
        if (middle == 0) {
            if (synveil_parent_death_guard(owner) != 0) _exit(4);
            execl(argv[0], argv[0], "--relay", (char *)NULL);
            _exit(5);
        }
        for (;;) pause();
    }
    return 2;
}
