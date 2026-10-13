// SPDX-License-Identifier: MPL-2.0
#define _POSIX_C_SOURCE 200809L
#include "pressure_diag.h"
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int read_percentage(const char **cursor, unsigned *tenths) {
    const char *p = *cursor;
    unsigned whole = 0, fraction = 0, digits = 0;
    while (*p >= '0' && *p <= '9' && digits < 3u) {
        whole = whole * 10u + (unsigned)(*p++ - '0');
        digits++;
    }
    if (!digits || *p != '.' || p[1] < '0' || p[1] > '9' ||
        p[2] < '0' || p[2] > '9') return 0;
    p++;
    fraction = 10u * (unsigned)(*p++ - '0');
    fraction += (unsigned)(*p++ - '0');
    if (whole > 100u || (whole == 100u && fraction != 0u)) return 0;
    *tenths = whole * 10u + (fraction + 5u) / 10u;
    *cursor = p;
    return 1;
}

static int percentage_field(const char **p, const char *prefix,
                            unsigned *tenths) {
    const size_t length = strlen(prefix);
    if (strncmp(*p, prefix, length) != 0) return 0;
    *p += length;
    return read_percentage(p, tenths);
}

int synveil_pressure_parse_some(const char *line, unsigned *tenths) {
    if (!line || !tenths || strncmp(line, "some ", 5u) != 0) return 0;
    const char *p = line + 5u;
    unsigned avg10, ignored;
    if (!percentage_field(&p, "avg10=", &avg10) || *p != ' ') return 0;
    p++;
    if (!percentage_field(&p, "avg60=", &ignored) || *p != ' ') return 0;
    p++;
    if (!percentage_field(&p, "avg300=", &ignored) || *p != ' ') return 0;
    p++;
    if (strncmp(p, "total=", 6u) != 0) return 0;
    p += 6u;
    if (*p < '0' || *p > '9') return 0;
    uint64_t total = 0;
    do {
        unsigned digit = (unsigned)(*p++ - '0');
        if (total > (UINT64_MAX - digit) / 10u) return 0;
        total = total * 10u + digit;
    } while (*p >= '0' && *p <= '9');
    if (*p != '\n' || p[1] != '\0') return 0;
    *tenths = avg10;
    return 1;
}

int synveil_pressure_some_avg10(const char *path, unsigned *tenths) {
    if (!path || !tenths) return 0;
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NONBLOCK | O_NOFOLLOW);
    if (fd < 0) return 0;
    struct stat info;
    if (fstat(fd, &info) != 0 || !S_ISREG(info.st_mode)) {
        close(fd);
        return 0;
    }
    FILE *source = fdopen(fd, "r");
    if (!source) {
        close(fd);
        return 0;
    }
    char line[192];
    int valid = fgets(line, sizeof(line), source) != NULL &&
                strchr(line, '\n') != NULL &&
                synveil_pressure_parse_some(line, tenths);
    fclose(source);
    return valid;
}
