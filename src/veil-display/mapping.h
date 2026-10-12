// SPDX-License-Identifier: MPL-2.0
#ifndef SYNVEIL_FRAMEBUFFER_MAPPING_H
#define SYNVEIL_FRAMEBUFFER_MAPPING_H

#include <stddef.h>
#include <sys/mman.h>

// fbdev mmap() survives close(fd). Forking the unprivileged dashboard's
// terminal would copy that mapping unless the kernel explicitly excludes it.
// Refuse to launch an authenticated dashboard if exclusion is unsupported.
static inline int synveil_exclude_mapping_from_children(void *mapped, size_t length) {
    return madvise(mapped, length, MADV_DONTFORK);
}

#endif
