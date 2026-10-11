// SPDX-License-Identifier: MPL-2.0
// Non-invasive Linux evdev discovery for Synveil's opt-in VM display.
#define _POSIX_C_SOURCE 200809L
#include <dirent.h>
#include <fcntl.h>
#include <linux/input.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define BIT_BYTES(max_bit) (((max_bit) + 8u) / 8u)

struct devices {
    int keyboard;
    int pointer;
};

static int has_bit(const unsigned char *bits, size_t bytes, unsigned code) {
    return code / 8u < bytes && (bits[code / 8u] & (1u << (code % 8u))) != 0;
}

static void mark_bit(unsigned char *bits, unsigned code) {
    bits[code / 8u] |= (unsigned char)(1u << (code % 8u));
}

static struct devices classify(const unsigned char *events,
                               const unsigned char *keys,
                               const unsigned char *relative,
                               const unsigned char *absolute) {
    struct devices found = {0, 0};
    if (has_bit(events, BIT_BYTES(EV_MAX), EV_KEY)) {
        found.keyboard = has_bit(keys, BIT_BYTES(KEY_MAX), KEY_A) &&
                         has_bit(keys, BIT_BYTES(KEY_MAX), KEY_ENTER);
        int button = has_bit(keys, BIT_BYTES(KEY_MAX), BTN_LEFT) ||
                     has_bit(keys, BIT_BYTES(KEY_MAX), BTN_TOUCH);
        int rel_axes = has_bit(events, BIT_BYTES(EV_MAX), EV_REL) &&
                       has_bit(relative, BIT_BYTES(REL_MAX), REL_X) &&
                       has_bit(relative, BIT_BYTES(REL_MAX), REL_Y);
        int abs_axes = has_bit(events, BIT_BYTES(EV_MAX), EV_ABS) &&
                       has_bit(absolute, BIT_BYTES(ABS_MAX), ABS_X) &&
                       has_bit(absolute, BIT_BYTES(ABS_MAX), ABS_Y);
        found.pointer = button && (rel_axes || abs_axes);
    }
    return found;
}

static struct devices probe_fd(int fd) {
    unsigned char events[BIT_BYTES(EV_MAX)] = {0};
    unsigned char keys[BIT_BYTES(KEY_MAX)] = {0};
    unsigned char relative[BIT_BYTES(REL_MAX)] = {0};
    unsigned char absolute[BIT_BYTES(ABS_MAX)] = {0};
    struct devices missing = {0, 0};
    if (ioctl(fd, EVIOCGBIT(0, sizeof(events)), events) < 0) return missing;
    if (has_bit(events, sizeof(events), EV_KEY) &&
        ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(keys)), keys) < 0) return missing;
    if (has_bit(events, sizeof(events), EV_REL) &&
        ioctl(fd, EVIOCGBIT(EV_REL, sizeof(relative)), relative) < 0) return missing;
    if (has_bit(events, sizeof(events), EV_ABS) &&
        ioctl(fd, EVIOCGBIT(EV_ABS, sizeof(absolute)), absolute) < 0) return missing;
    return classify(events, keys, relative, absolute);
}

static int event_filename(const char *name) {
    if (strncmp(name, "event", 5) != 0 || name[5] == '\0') return 0;
    for (const char *p = name + 5; *p; p++) {
        if (*p < '0' || *p > '9') return 0;
    }
    return 1;
}

static struct devices scan_devices(void) {
    struct devices all = {0, 0};
    DIR *input = opendir("/dev/input");
    if (input == NULL) return all;
    struct dirent *entry;
    while ((entry = readdir(input)) != NULL) {
        if (!event_filename(entry->d_name)) continue;
        char path[256];
        int used = snprintf(path, sizeof(path), "/dev/input/%s", entry->d_name);
        if (used < 0 || (size_t)used >= sizeof(path)) continue;
        int fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
        if (fd < 0) continue;
        struct stat info;
        if (fstat(fd, &info) == 0 && S_ISCHR(info.st_mode)) {
            struct devices found = probe_fd(fd);
            all.keyboard |= found.keyboard;
            all.pointer |= found.pointer;
        }
        close(fd);
        if (all.keyboard && all.pointer) break;
    }
    closedir(input);
    return all;
}

static int self_test(void) {
    unsigned char events[BIT_BYTES(EV_MAX)] = {0};
    unsigned char keys[BIT_BYTES(KEY_MAX)] = {0};
    unsigned char relative[BIT_BYTES(REL_MAX)] = {0};
    unsigned char absolute[BIT_BYTES(ABS_MAX)] = {0};
    if (has_bit(events, sizeof(events), EV_KEY)) return 1;
    if (event_filename("event") || event_filename("event12-link") ||
        !event_filename("event12")) return 1;
    mark_bit(events, EV_KEY);
    mark_bit(keys, KEY_A);
    mark_bit(keys, KEY_ENTER);
    if (!classify(events, keys, relative, absolute).keyboard ||
        classify(events, keys, relative, absolute).pointer) return 1;
    mark_bit(events, EV_ABS);
    mark_bit(keys, BTN_LEFT);
    mark_bit(absolute, ABS_X);
    mark_bit(absolute, ABS_Y);
    struct devices found = classify(events, keys, relative, absolute);
    if (!found.keyboard || !found.pointer) return 1;
    if (has_bit(events, 0, EV_KEY) || has_bit(events, sizeof(events), 9999)) return 1;
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fputs("usage: synveil-input --probe|--self-test\n", stderr);
        return 2;
    }
    if (strcmp(argv[1], "--self-test") == 0) return self_test();
    if (strcmp(argv[1], "--probe") != 0) {
        fputs("usage: synveil-input --probe|--self-test\n", stderr);
        return 2;
    }
    // USB enumeration may complete shortly after init; cap startup delay.
    for (int attempt = 0; attempt < 16; attempt++) {
        struct devices found = scan_devices();
        if (found.keyboard && found.pointer) {
            puts("SYNVEIL_INPUT_READY");
            return 0;
        }
        struct timespec interval = {.tv_sec = 0, .tv_nsec = 50000000L};
        nanosleep(&interval, NULL);
    }
    fputs("[synveil-input] keyboard and pointer were not both detected\n", stderr);
    return 1;
}
