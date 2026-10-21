// SPDX-License-Identifier: MPL-2.0
// Fixed, one-time graphical first-user enrollment helper. No shell involved.
#define _GNU_SOURCE
#include "enroll.h"
#include <errno.h>
#include <fcntl.h>
#include <mntent.h>
#include <stdio.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define STATE_DIR "/var/lib/synveil"
#define CREDENTIAL "/var/lib/synveil/auth/credential.phc"
#define AUTH_DIR "/var/lib/synveil/auth"
#define ENROLL_PROGRAM "/usr/sbin/veil-enroll"
#define ENROLL_TIMEOUT_SECONDS 20

void synveil_enroll_wipe(void *data, size_t length) {
    volatile unsigned char *bytes = data;
    while (length) bytes[--length] = 0;
}

// Compare two *fixed-size* in-memory slots without timing differences
// dependent on a matching prefix or first differing character.
int synveil_enroll_matches(const unsigned char *first, size_t first_length,
                           const unsigned char *again, size_t again_length) {
    if (!first || !again) return 0;
    unsigned difference = (unsigned)(first_length ^ again_length);
    for (size_t i = 0; i < SYNVEIL_ENROLL_MAX_PASSWORD; ++i)
        difference |= (unsigned)(first[i] ^ again[i]);
    return first_length >= 8u && first_length <= SYNVEIL_ENROLL_MAX_PASSWORD &&
           again_length >= 8u && again_length <= SYNVEIL_ENROLL_MAX_PASSWORD &&
           difference == 0u;
}

// The graphical setup must agree with the Rust verifier about mounted,
// durable credential storage. Merely seeing a different st_dev would accept
// ephemeral tmpfs and then create a password no subsequent boot can use.
int synveil_enroll_mount_matches(const char *source, const char *target,
                                  const char *type) {
    return source && target && type && strcmp(source, "/dev/vdb") == 0 &&
           strcmp(target, "/var") == 0 && strcmp(type, "ext4") == 0;
}

int synveil_enroll_mount_options_writable(const char *options) {
    if (!options) return 0;
    struct mntent entry = {0};
    entry.mnt_opts = (char *)options;
    return hasmntopt(&entry, "rw") != NULL &&
           hasmntopt(&entry, "ro") == NULL;
}

static int mounted_expected_state(void) {
    FILE *mounts = fopen("/proc/mounts", "re");
    if (!mounts) return 0;
    int matched = 0;
    int count = 0;
    struct mntent *entry;
    while ((entry = getmntent(mounts)) != NULL) {
        if (strcmp(entry->mnt_dir, "/var") != 0) continue;
        count++;
        if (!synveil_enroll_mount_matches(entry->mnt_fsname,
                                          entry->mnt_dir,
                                          entry->mnt_type) ||
            !synveil_enroll_mount_options_writable(entry->mnt_opts)) {
            matched = 0;
            break;
        }
        matched = 1;
    }
    fclose(mounts);
    // Ignore ambiguous mount stacks, even if one entry is the expected
    // writable /dev/vdb. Match the native Argon2id verifier's contract.
    return matched && count == 1;
}

static int private_directory(const char *path) {
    struct stat info;
    return lstat(path, &info) == 0 && S_ISDIR(info.st_mode) &&
           info.st_uid == 0 && info.st_gid == 0 &&
           (info.st_mode & 0777) == 0700;
}

// Only the root graphical login of an explicit authenticated image may
// enroll. The developer-serial fixture must keep its established workflow.
// Never make credentials in an ephemeral initramfs or overwrite an existing
// record, including symlinks.
int synveil_enroll_can_start(void) {
    if (getuid() != 0 || geteuid() != 0) return 0;
    struct stat root, state, record, marker;
    if (lstat("/etc/synveil/auth-desktop", &marker) != 0 ||
        !S_ISREG(marker.st_mode) || marker.st_uid != 0 ||
        (marker.st_mode & 0777) != 0644) return 0;
    if (lstat("/etc/synveil/developer-serial-recovery", &marker) == 0 ||
        errno != ENOENT) return 0;
    if (stat("/", &root) != 0 || lstat("/var", &state) != 0 ||
        !S_ISDIR(state.st_mode) || state.st_uid != 0 || state.st_gid != 0 ||
        (state.st_mode & 0022) != 0 || root.st_dev == state.st_dev ||
        !mounted_expected_state() || !private_directory(STATE_DIR)) return 0;
    if (lstat(CREDENTIAL, &record) == 0 || errno != ENOENT) return 0;
    if (lstat(AUTH_DIR, &record) == 0)
        return S_ISDIR(record.st_mode) && record.st_uid == 0 &&
               record.st_gid == 0 && (record.st_mode & 0777) == 0700;
    return errno == ENOENT;
}

static int wait_for_enroller(pid_t child) {
    struct timespec started, now;
    if (clock_gettime(CLOCK_MONOTONIC, &started) != 0) return -1;
    for (;;) {
        int status = 0;
        pid_t got = waitpid(child, &status, WNOHANG);
        if (got == child)
            return WIFEXITED(status) && WEXITSTATUS(status) == 0 ? 0 : -1;
        if (got < 0 && errno != EINTR) return -1;
        if (clock_gettime(CLOCK_MONOTONIC, &now) != 0 ||
            now.tv_sec - started.tv_sec >= ENROLL_TIMEOUT_SECONDS) return -1;
        (void)poll(NULL, 0, 50);
    }
}

int synveil_enroll_password(const unsigned char *secret, size_t length) {
    if (!secret || length < 8u || length > SYNVEIL_ENROLL_MAX_PASSWORD ||
        !synveil_enroll_can_start()) return -1;
    int channel[2];
    if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, channel) != 0)
        return -1;
    pid_t child = fork();
    if (child < 0) {
        close(channel[0]);
        close(channel[1]);
        return -1;
    }
    if (child == 0) {
        close(channel[0]);
        // Do not inherit stdin from tty2 or expose enroller output over
        // serial. The only execution target is the pinned Rust credential
        // helper with a fixed option; never a shell or arbitrary program.
        int sink = open("/dev/null", O_WRONLY | O_CLOEXEC);
        if (sink < 0 || dup2(channel[1], STDIN_FILENO) < 0 ||
            dup2(sink, STDOUT_FILENO) < 0 ||
            dup2(sink, STDERR_FILENO) < 0) _exit(126);
        close(channel[1]);
        close(sink);
        execl(ENROLL_PROGRAM, "veil-enroll", "--password-stdin", (char *)NULL);
        _exit(127);
    }
    close(channel[1]);
    size_t sent = 0;
    int valid = 1;
    while (sent < length) {
        ssize_t n = send(channel[0], secret + sent, length - sent, MSG_NOSIGNAL);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) {
            valid = 0;
            break;
        }
        sent += (size_t)n;
    }
    (void)shutdown(channel[0], SHUT_WR);
    close(channel[0]);
    int success = wait_for_enroller(child);
    if (!valid || success != 0) {
        (void)kill(child, SIGKILL);
        while (waitpid(child, NULL, 0) < 0 && errno == EINTR) {}
        return -1;
    }
    return 0;
}
