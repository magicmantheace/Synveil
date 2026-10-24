// SPDX-License-Identifier: MPL-2.0
#ifndef SYNVEIL_FILES_H
#define SYNVEIL_FILES_H
#define SYNVEIL_FILES_ENTRIES 64u
#define SYNVEIL_FILES_DEPTH 6u
#define SYNVEIL_FILES_NAME 256u

// Read-only file list limited to the authenticated owner's private home.
struct synveil_file_entry {
    char name[SYNVEIL_FILES_NAME];
    int directory;
};
struct synveil_file_browser {
    int dirfds[SYNVEIL_FILES_DEPTH];
    unsigned depth, count, selected;
    int truncated, available;
    struct synveil_file_entry entries[SYNVEIL_FILES_ENTRIES];
};
int synveil_files_open(struct synveil_file_browser *view, const char *home);
void synveil_files_close(struct synveil_file_browser *view);
int synveil_files_refresh(struct synveil_file_browser *view);
int synveil_files_enter(struct synveil_file_browser *view);
int synveil_files_back(struct synveil_file_browser *view);
void synveil_files_select(struct synveil_file_browser *view, unsigned index);
#endif
