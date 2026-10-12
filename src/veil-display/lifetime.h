// SPDX-License-Identifier: MPL-2.0
#ifndef SYNVEIL_LIFETIME_H
#define SYNVEIL_LIFETIME_H

#include <errno.h>
#include <signal.h>
#include <sys/prctl.h>
#include <sys/types.h>
#include <unistd.h>

// Keep disposable developer-terminal descendants tied to their parent.
// Install before exec and verify the expected parent again to close the
// fork/prctl race. After setuid/setgid, Linux clears PDEATHSIG: the final
// unprivileged shell must set the guard again after dropping privileges.
static int synveil_parent_death_guard(pid_t expected_parent) {
    if (expected_parent <= 1) {
        errno = ECHILD;
        return -1;
    }
    if (prctl(PR_SET_PDEATHSIG, SIGKILL, 0, 0, 0) != 0) return -1;
    if (getppid() != expected_parent) {
        errno = ECHILD;
        return -1;
    }
    return 0;
}

#endif
