// SPDX-License-Identifier: MPL-2.0
#ifndef SYNVEIL_DESKTOP_PREFERENCES_H
#define SYNVEIL_DESKTOP_PREFERENCES_H

struct desktop_preferences {
    int high_contrast;
    unsigned cursor_scale;
};

/* Returns 1 for a valid saved record; 0 for missing/rejected records.
   Values in *out are always reset to safe defaults on failure. */
int preferences_load(const char *directory, struct desktop_preferences *out);

/* Atomic, directory-synchronized replacement; never follows a symlink. */
int preferences_save(const char *directory, const struct desktop_preferences *value);

#endif
