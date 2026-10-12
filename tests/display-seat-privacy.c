// SPDX-License-Identifier: MPL-2.0
#define _GNU_SOURCE
#include <sys/prctl.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#include "seat_privacy.h"

int main(void) {
    // Exercise the same helper in a child so host test discovery and
    // unrelated tests do not inherit altered dumpability.
    pid_t child = fork();
    if (child < 0) return 1;
    if (child == 0) {
        if (synveil_protect_authenticated_ui() != 0) _exit(2);
        if (prctl(PR_GET_DUMPABLE, 0, 0, 0, 0) != 0) _exit(3);
        _exit(0);
    }
    int status = 0;
    if (waitpid(child, &status, 0) != child) return 4;
    if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) return 5;
    return 0;
}
