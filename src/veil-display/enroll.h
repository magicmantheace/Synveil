// SPDX-License-Identifier: MPL-2.0
#ifndef SYNVEIL_GRAPHICAL_ENROLL_H
#define SYNVEIL_GRAPHICAL_ENROLL_H
#include <stddef.h>
#define SYNVEIL_ENROLL_MAX_PASSWORD 1024u
void synveil_enroll_wipe(void *data, size_t length);
int synveil_enroll_matches(const unsigned char *first, size_t first_length,
                           const unsigned char *again, size_t again_length);
int synveil_enroll_can_start(void);
int synveil_enroll_mount_matches(const char *source, const char *target,
                                  const char *type);
int synveil_enroll_mount_options_writable(const char *options);
int synveil_enroll_password(const unsigned char *secret, size_t length);
#endif
