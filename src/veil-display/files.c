// SPDX-License-Identifier: MPL-2.0
#define _GNU_SOURCE
#include "files.h"
#include <dirent.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

// Never use a user-supplied pathname as an absolute path. Symlinks and
// anything other than a regular file or directory are excluded.
static int compare_entries(const void *left, const void *right) {
    const struct synveil_file_entry *a = left, *b = right;
    if (a->directory != b->directory) return b->directory - a->directory;
    return strcmp(a->name, b->name);
}

void synveil_files_close(struct synveil_file_browser *view) {
    if (!view) return;
    for (unsigned i = 0; i < SYNVEIL_FILES_DEPTH; ++i) {
        if (view->dirfds[i] >= 0) close(view->dirfds[i]);
        view->dirfds[i] = -1;
    }
    view->depth = view->count = view->selected = 0u;
    view->truncated = view->available = 0;
}

int synveil_files_refresh(struct synveil_file_browser *view) {
    if (!view || !view->available || view->depth >= SYNVEIL_FILES_DEPTH)
        return -1;
    // Independent descriptor avoids sharing readdir offsets with the
    // persistent directory handle. No symlink is followed.
    int fd = openat(view->dirfds[view->depth], ".",
                    O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return -1;
    DIR *directory = fdopendir(fd);
    if (!directory) { close(fd); return -1; }
    view->count = view->selected = 0u;
    view->truncated = 0;
    struct dirent *item;
    while ((item = readdir(directory)) != NULL) {
        if (!strcmp(item->d_name, ".") || !strcmp(item->d_name, ".."))
            continue;
        size_t length = strnlen(item->d_name, SYNVEIL_FILES_NAME);
        if (length == SYNVEIL_FILES_NAME) continue;
        struct stat info;
        if (fstatat(dirfd(directory), item->d_name, &info,
                    AT_SYMLINK_NOFOLLOW) != 0) continue;
        if (!S_ISDIR(info.st_mode) && !S_ISREG(info.st_mode)) continue;
        if (view->count == SYNVEIL_FILES_ENTRIES) {
            view->truncated = 1;
            continue;
        }
        struct synveil_file_entry *entry = &view->entries[view->count++];
        memcpy(entry->name, item->d_name, length + 1u);
        entry->directory = S_ISDIR(info.st_mode);
    }
    if (closedir(directory) != 0) { view->count = 0u; return -1; }
    qsort(view->entries, view->count, sizeof(view->entries[0]), compare_entries);
    return 0;
}

int synveil_files_open(struct synveil_file_browser *view, const char *home) {
    if (!view || !home) return -1;
    memset(view, 0, sizeof(*view));
    for (unsigned i = 0; i < SYNVEIL_FILES_DEPTH; ++i) view->dirfds[i] = -1;
    int fd = open(home, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return -1;
    struct stat info;
    if (fstat(fd, &info) != 0 || !S_ISDIR(info.st_mode) ||
        info.st_uid != geteuid()) { close(fd); return -1; }
    view->dirfds[0] = fd;
    view->available = 1;
    if (synveil_files_refresh(view) == 0) return 0;
    synveil_files_close(view);
    return -1;
}

void synveil_files_select(struct synveil_file_browser *view, unsigned index) {
    if (view && view->available && index < view->count)
        view->selected = index;
}

int synveil_files_enter(struct synveil_file_browser *view) {
    if (!view || !view->available || view->selected >= view->count ||
        !view->entries[view->selected].directory ||
        view->depth + 1u >= SYNVEIL_FILES_DEPTH) return -1;
    int fd = openat(view->dirfds[view->depth],
                    view->entries[view->selected].name,
                    O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return -1;
    struct stat info;
    if (fstat(fd, &info) != 0 || !S_ISDIR(info.st_mode)) {
        close(fd);
        return -1;
    }
    view->dirfds[++view->depth] = fd;
    if (synveil_files_refresh(view) == 0) return 0;
    close(view->dirfds[view->depth]);
    view->dirfds[view->depth--] = -1;
    (void)synveil_files_refresh(view);
    return -1;
}

int synveil_files_back(struct synveil_file_browser *view) {
    if (!view || !view->available || view->depth == 0u) return -1;
    close(view->dirfds[view->depth]);
    view->dirfds[view->depth--] = -1;
    return synveil_files_refresh(view);
}
