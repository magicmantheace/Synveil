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
    // The mount-shape layer no longer pins a device name: any nonempty
    // source with the right target and filesystem matches here, and the
    // designated GPT identity is enforced separately below.
    if (!synveil_enroll_mount_matches("/dev/vdb1", "/var", "ext4")) return 7;
    if (!synveil_enroll_mount_matches("/dev/sda2", "/var", "ext4")) return 7;
    if (synveil_enroll_mount_matches("/dev/vdb1", "/var", "tmpfs") ||
        synveil_enroll_mount_matches("", "/var", "ext4") ||
        synveil_enroll_mount_matches("/dev/vdb1", "/tmp", "ext4") ||
        synveil_enroll_mount_matches(NULL, "/var", "ext4")) return 8;
    const char *state_uevent =
        "MAJOR=254\nMINOR=17\nDEVNAME=vdb1\nDEVTYPE=partition\nPARTN=1\n"
        "PARTNAME=Synveil State\n"
        "PARTUUID=7c9e2d41-8b3a-4f5e-a6d1-2e8c4b7f9053\n";
    if (!synveil_enroll_uevent_is_state(state_uevent)) return 10;
    if (synveil_enroll_uevent_is_state(NULL) ||
        synveil_enroll_uevent_is_state("") ||
        // A whole disk has no partition identity at all.
        synveil_enroll_uevent_is_state(
            "MAJOR=254\nMINOR=16\nDEVNAME=vdb\nDEVTYPE=disk\n") ||
        synveil_enroll_uevent_is_state(
            "DEVNAME=sda2\nDEVTYPE=partition\nPARTNAME=Synveil State\n"
            "PARTUUID=0c9e2d41-8b3a-4f5e-a6d1-2e8c4b7f9053\n") ||
        synveil_enroll_uevent_is_state(
            "DEVNAME=sda2\nDEVTYPE=partition\nPARTNAME=Other State\n"
            "PARTUUID=7c9e2d41-8b3a-4f5e-a6d1-2e8c4b7f9053\n") ||
        synveil_enroll_uevent_is_state(
            "DEVNAME=sda2\nDEVTYPE=partition\n"
            "PARTUUID=7c9e2d41-8b3a-4f5e-a6d1-2e8c4b7f9053\n") ||
        // A repeated identity key is ambiguous, even a faithful one.
        synveil_enroll_uevent_is_state(
            "DEVNAME=sda2\nDEVTYPE=partition\nPARTNAME=Synveil State\n"
            "PARTUUID=7c9e2d41-8b3a-4f5e-a6d1-2e8c4b7f9053\n"
            "PARTUUID=7c9e2d41-8b3a-4f5e-a6d1-2e8c4b7f9053\n"))
        return 11;
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
