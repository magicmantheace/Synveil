// SPDX-License-Identifier: MPL-2.0
#define _GNU_SOURCE
#include "files.h"
#include <assert.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static void touch(const char *name) {
    int fd = open(name, O_CREAT | O_EXCL | O_WRONLY | O_CLOEXEC, 0600);
    assert(fd >= 0);
    assert(write(fd, "x", 1) == 1);
    assert(close(fd) == 0);
}
static int lookup(const struct synveil_file_browser *view, const char *name) {
    for (unsigned i = 0; i < view->count; ++i)
        if (!strcmp(view->entries[i].name, name)) return (int)i;
    return -1;
}
int main(void) {
    char home[] = "/tmp/synveil-files-XXXXXX";
    char outside[] = "/tmp/synveil-outside-XXXXXX";
    assert(mkdtemp(home) && mkdtemp(outside));
    int cwd = open(".", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    assert(cwd >= 0 && chdir(home) == 0);
    assert(mkdir("DIRECTORY", 0700) == 0);
    touch("notes.txt");
    assert(symlink(outside, "outside-link") == 0);
    assert(symlink("DIRECTORY", "directory-link") == 0);
    struct synveil_file_browser view;
    assert(synveil_files_open(&view, home) == 0);
    assert(view.available && view.depth == 0u && view.count == 2u);
    assert(lookup(&view, "DIRECTORY") == 0);
    assert(lookup(&view, "notes.txt") == 1);
    assert(lookup(&view, "outside-link") == -1);
    assert(lookup(&view, "directory-link") == -1);
    assert(lookup(&view, "..") == -1);
    synveil_files_select(&view, 1);
    assert(synveil_files_enter(&view) != 0);
    assert(view.depth == 0u);
    synveil_files_select(&view, 999u);
    assert(view.selected == 1u);
    synveil_files_select(&view, 0u);
    assert(synveil_files_enter(&view) == 0);
    assert(view.depth == 1u && view.count == 0u);
    touch("new-in-home");
    assert(synveil_files_refresh(&view) == 0 && view.count == 0u);
    assert(synveil_files_back(&view) == 0);
    assert(view.depth == 0u && view.count == 3u);
    assert(synveil_files_back(&view) != 0);
    synveil_files_close(&view);
    assert(!view.available);
    assert(unlink("directory-link") == 0);
    assert(unlink("outside-link") == 0);
    assert(unlink("notes.txt") == 0);
    assert(unlink("new-in-home") == 0);
    assert(rmdir("DIRECTORY") == 0);
    assert(fchdir(cwd) == 0 && close(cwd) == 0);
    assert(rmdir(home) == 0 && rmdir(outside) == 0);
    assert(synveil_files_open(&view, "/does/not/exist") != 0);
    puts("private-home read-only browser: pass");
    return 0;
}
