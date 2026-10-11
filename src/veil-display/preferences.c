// SPDX-License-Identifier: MPL-2.0
#define _POSIX_C_SOURCE 200809L
#include "preferences.h"
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#define RECORD "synveil.desktop/v1\n"
#define PREFERENCES_FILE "desktop-pref.conf"
#define PREFERENCES_TMP ".desktop-pref.tmp"

static int safe_directory(const char *directory) {
    int fd = open(directory, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return -1;
    struct stat info;
    if (fstat(fd, &info) != 0 || !S_ISDIR(info.st_mode) ||
        info.st_uid != geteuid() || (info.st_mode & 0022) != 0) {
        close(fd);
        errno = EACCES;
        return -1;
    }
    return fd;
}

int preferences_load(const char *directory, struct desktop_preferences *out) {
    if (!out) { errno = EINVAL; return 0; }
    *out = (struct desktop_preferences){0, 0u};
    if (!directory) { errno = EINVAL; return 0; }
    int dir = safe_directory(directory);
    if (dir < 0) return 0;
    int file = openat(dir, PREFERENCES_FILE, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
    close(dir);
    if (file < 0) return 0;
    struct stat info;
    if (fstat(file, &info) != 0 || !S_ISREG(info.st_mode) ||
        info.st_uid != geteuid() || (info.st_mode & 0077) != 0 ||
        info.st_size <= 0 || info.st_size >= 96) {
        close(file);
        return 0;
    }
    char buffer[96];
    ssize_t read_bytes = read(file, buffer, sizeof(buffer));
    close(file);
    if (read_bytes <= 0 || read_bytes != info.st_size) return 0;
    buffer[read_bytes] = '\0';
    // Fixed-length and byte-exact protocol, no unbounded JSON or shell input.
    char expected[96];
    for (int contrast = 0; contrast <= 1; ++contrast) {
        for (unsigned size = 0; size <= 1; ++size) {
            int count = snprintf(expected, sizeof(expected), RECORD
                                 "contrast=%d\ncursor_scale=%u\n", contrast, size);
            if (count == read_bytes && memcmp(buffer, expected, (size_t)count) == 0) {
                out->high_contrast = contrast;
                out->cursor_scale = size;
                return 1;
            }
        }
    }
    return 0;
}

/* An interrupted save can leave the fixed temporary name behind. Since
   only the trusted root-owned 0700 directory is writable, a bounded,
   owner-only regular temp can be discarded before retrying. Never unlink
   symlinks, hardlinks, directories, or unexpectedly permissive files. */
static int open_staging_file(int dir) {
    int flags = O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC;
    int file = openat(dir, PREFERENCES_TMP, flags, 0600);
    if (file >= 0 || errno != EEXIST) return file;

    struct stat old;
    if (fstatat(dir, PREFERENCES_TMP, &old, AT_SYMLINK_NOFOLLOW) != 0)
        return -1;
    if (!S_ISREG(old.st_mode) || old.st_uid != geteuid() ||
        (old.st_mode & 0777) != 0600 || old.st_nlink != 1 ||
        old.st_size < 0 || old.st_size > 96) {
        errno = EEXIST;
        return -1;
    }
    if (unlinkat(dir, PREFERENCES_TMP, 0) < 0) return -1;
    return openat(dir, PREFERENCES_TMP, flags, 0600);
}

static int write_all(int file, const char *buffer, size_t length) {
    size_t position = 0;
    while (position < length) {
        ssize_t count = write(file, buffer + position, length - position);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return -1;
        position += (size_t)count;
    }
    return 0;
}

int preferences_save(const char *directory, const struct desktop_preferences *value) {
    if (!directory || !value ||
        (value->high_contrast != 0 && value->high_contrast != 1) ||
        value->cursor_scale > 1u) {
        errno = EINVAL;
        return -1;
    }
    int dir = safe_directory(directory);
    if (dir < 0) return -1;
    char buffer[96];
    int length = snprintf(buffer, sizeof(buffer), RECORD
                          "contrast=%d\ncursor_scale=%u\n",
                          value->high_contrast, value->cursor_scale);
    if (length <= 0 || (size_t)length >= sizeof(buffer)) {
        close(dir);
        errno = EINVAL;
        return -1;
    }
    int file = open_staging_file(dir);
    if (file < 0) {
        close(dir);
        return -1;
    }
    int result = write_all(file, buffer, (size_t)length);
    if (result == 0) result = fsync(file);
    int saved = errno;
    if (close(file) != 0 && result == 0) {
        result = -1;
        saved = errno;
    }
    if (result == 0) {
        result = renameat(dir, PREFERENCES_TMP, dir, PREFERENCES_FILE);
        saved = errno;
    }
    if (result == 0) {
        result = fsync(dir);
        saved = errno;
    }
    if (result != 0) {
        (void)unlinkat(dir, PREFERENCES_TMP, 0);
    }
    close(dir);
    if (result != 0) errno = saved;
    return result;
}
