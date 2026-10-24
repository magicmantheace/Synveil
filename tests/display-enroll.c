// SPDX-License-Identifier: MPL-2.0
// Runs unprivileged; this test never executes the root credential utility.
#include "enroll.h"
#include <string.h>
int main(void) {
    unsigned char first[SYNVEIL_ENROLL_MAX_PASSWORD] = {0};
    unsigned char again[SYNVEIL_ENROLL_MAX_PASSWORD] = {0};
    memcpy(first, "first-password-123", 18);
    memcpy(again, "first-password-123", 18);
    if (!synveil_enroll_matches(first, 18u, again, 18u)) return 1;
    if (synveil_enroll_matches(first, 17u, again, 18u)) return 2;
    again[8] = 'X';
    if (synveil_enroll_matches(first, 18u, again, 18u)) return 3;
    again[8] = first[8];
    if (synveil_enroll_matches(first, 7u, again, 7u)) return 4;
    if (synveil_enroll_matches(first, 1025u, again, 1025u)) return 5;
    synveil_enroll_wipe(first, sizeof(first));
    synveil_enroll_wipe(again, sizeof(again));
    for (unsigned i = 0; i < sizeof(first); ++i)
        if (first[i] || again[i]) return 6;
    if (!synveil_enroll_mount_matches("/dev/vdb", "/var", "ext4")) return 7;
    if (synveil_enroll_mount_matches("/dev/vdb", "/var", "tmpfs") ||
        synveil_enroll_mount_matches("/dev/vda", "/var", "ext4") ||
        synveil_enroll_mount_matches("/dev/vdb", "/tmp", "ext4") ||
        synveil_enroll_mount_matches(NULL, "/var", "ext4")) return 8;
    if (!synveil_enroll_mount_options_writable("rw,nosuid,nodev,noatime") ||
        synveil_enroll_mount_options_writable("ro,nosuid,nodev") ||
        synveil_enroll_mount_options_writable("defaults") ||
        synveil_enroll_mount_options_writable("xrw,nosuid") ||
        synveil_enroll_mount_options_writable("rw,ro") ||
        synveil_enroll_mount_options_writable(NULL)) return 9;
    // A direct host invocation is neither the installed authenticated image
    // nor the expected isolated state volume, even if running as root.
    if (synveil_enroll_can_start()) return 7;
    return 0;
}
