// SPDX-License-Identifier: MPL-2.0
#define _POSIX_C_SOURCE 200809L
#include "preferences.h"
#include <assert.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

int main(void) {
    char template[] = "/tmp/synveil-prefs.XXXXXX";
    char *dir = mkdtemp(template);
    assert(dir);
    assert(chmod(dir, 0700) == 0);
    struct desktop_preferences prefs = {.high_contrast = 1, .cursor_scale = 1};
    struct desktop_preferences readback = {0};
    assert(preferences_load(dir, &readback) == 0);
    assert(!readback.high_contrast && !readback.cursor_scale);
    assert(preferences_save(dir, &prefs) == 0);
    assert(preferences_load(dir, &readback) == 1);
    assert(readback.high_contrast == 1 && readback.cursor_scale == 1);
    char path[256];
    assert(snprintf(path, sizeof(path), "%s/desktop-pref.conf", dir) > 0);
    struct stat info;
    assert(stat(path, &info) == 0);
    assert((info.st_mode & 0777) == 0600);
    // Corruption and an unexpected version fail closed to known defaults.
    int fd = open(path, O_WRONLY | O_TRUNC);
    assert(fd >= 0);
    assert(write(fd, "unrecognized\n", 13) == 13);
    assert(close(fd) == 0);
    assert(preferences_load(dir, &readback) == 0);
    assert(!readback.high_contrast && !readback.cursor_scale);
    assert(preferences_save(dir, &prefs) == 0);
    // Tampered symlinks are never followed on read.
    assert(unlink(path) == 0);
    assert(symlink("/etc/passwd", path) == 0);
    assert(preferences_load(dir, &readback) == 0);
    assert(!readback.high_contrast && !readback.cursor_scale);
    // An atomic replacement replaces the link, not its target.
    assert(preferences_save(dir, &prefs) == 0);
    assert(lstat(path, &info) == 0 && S_ISREG(info.st_mode));
    assert(preferences_load(dir, &readback) == 1);
    prefs.high_contrast = 2;
    assert(preferences_save(dir, &prefs) != 0);
    prefs.high_contrast = 1;
    prefs.cursor_scale = 42u;
    assert(preferences_save(dir, &prefs) != 0);
    // No user-editable directory permission to impersonate the store.
    assert(chmod(dir, 0777) == 0);
    assert(preferences_save(dir, &readback) != 0);
    assert(preferences_load(dir, &readback) == 0);
    assert(chmod(dir, 0700) == 0);
    assert(unlink(path) == 0);
    assert(rmdir(dir) == 0);
    return 0;
}
