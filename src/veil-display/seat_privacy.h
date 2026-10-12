// SPDX-License-Identifier: MPL-2.0
#ifndef SYNVEIL_AUTHENTICATED_SEAT_PRIVACY_H
#define SYNVEIL_AUTHENTICATED_SEAT_PRIVACY_H

#include <sys/prctl.h>

// The fixed UID1000 terminal must not ptrace or read /proc/PID/mem of its
// same-UID graphical parent and recover fbdev mappings or input events.
// PR_SET_DUMPABLE is process-local: children cannot reenable it for parent.
static inline int synveil_protect_authenticated_ui(void) {
    return prctl(PR_SET_DUMPABLE, 0, 0, 0, 0);
}

#endif
