// SPDX-License-Identifier: MPL-2.0
#define _GNU_SOURCE
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/mman.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#include "mapping.h"

int main(void) {
    long page = sysconf(_SC_PAGESIZE);
    if (page < 4096) return 1;
    size_t length = (size_t)page;
    unsigned char *mapped = mmap(NULL, length, PROT_READ | PROT_WRITE,
                                 MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mapped == MAP_FAILED) return 2;
    mapped[0] = 0x5a;
    if (synveil_exclude_mapping_from_children(mapped, length) != 0) return 3;
    pid_t child = fork();
    if (child < 0) return 4;
    if (child == 0) {
        unsigned char present = 0;
        errno = 0;
        // mincore on an unmapped range fails with ENOMEM. This observes the
        // child's actual address space without deliberately dereferencing
        // an invalid pointer or installing a signal handler.
        int status = mincore(mapped, length, &present);
        _exit(status < 0 && errno == ENOMEM ? 0 : 5);
    }
    int status = 0;
    if (waitpid(child, &status, 0) != child) return 6;
    if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) return 7;
    if (mapped[0] != 0x5a) return 8;
    if (munmap(mapped, length) != 0) return 9;
    return 0;
}
