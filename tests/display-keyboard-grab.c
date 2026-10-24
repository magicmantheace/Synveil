// SPDX-License-Identifier: MPL-2.0
// Deterministic input lifecycle fixture; no real input device or secret data.
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <linux/input.h>
#include <linux/kd.h>
#include <poll.h>
#include <stdarg.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

static int held, forever, grabbed, grabs, releases, polls, reads, queries;
static int race, query_fail, grab_fail, read_fail, partial, flood, poll_fail;
static int quiet_fail, wrong_mode, quiet;
static int graphics_fail, wrong_graphics, graphics;

static int fixture_ioctl(int fd, unsigned long request, ...) {
    assert(fd == 7);
    va_list args;
    va_start(args, request);
    int result = 0;
    if (request == KDSKBMODE) {
        assert(va_arg(args, int) == K_OFF);
        if (quiet_fail) { errno = EPERM; result = -1; }
        else quiet = 1;
    } else if (request == KDGKBMODE) {
        int *mode = va_arg(args, int *);
        *mode = wrong_mode ? K_UNICODE : K_OFF;
    } else if (request == KDSETMODE) {
        assert(va_arg(args, int) == KD_GRAPHICS);
        if (graphics_fail) { errno = EPERM; result = -1; }
        else graphics = 1;
    } else if (request == KDGETMODE) {
        int *mode = va_arg(args, int *);
        *mode = wrong_graphics ? KD_TEXT : KD_GRAPHICS;
    } else if (request == EVIOCGRAB) {
        int acquire = va_arg(args, int);
        if (acquire) {
            assert(!held); // console must receive the held chord's release
            if (grab_fail) { errno = EBUSY; result = -1; }
            else { grabbed = 1; ++grabs; }
        } else {
            assert(grabbed);
            grabbed = 0;
            ++releases;
        }
    } else {
        assert(request == EVIOCGKEY((KEY_MAX + 8u) / 8u));
        unsigned char *keys = va_arg(args, unsigned char *);
        ++queries;
        if (queries == query_fail) { errno = EIO; result = -1; }
        else {
            memset(keys, 0, (KEY_MAX + 8u) / 8u);
            if (held || (race && grabbed && grabs == 1))
                keys[KEY_LEFTCTRL / 8u] |= 1u << (KEY_LEFTCTRL % 8u);
        }
    }
    va_end(args);
    return result;
}

static ssize_t fixture_read(int fd, void *buffer, size_t size) {
    assert(fd == 7 && grabbed && size == sizeof(struct input_event));
    ++reads;
    if (read_fail) { errno = ENODEV; return -1; }
    if (partial) return 1;
    if (flood || reads == 1) {
        memset(buffer, 0, size);
        return (ssize_t)size;
    }
    errno = EAGAIN;
    return -1;
}

static int fixture_poll(struct pollfd *fds, nfds_t count, int timeout) {
    assert(fds == NULL && count == 0 && timeout == 25 && !grabbed);
    ++polls;
    if (poll_fail) { errno = EIO; return -1; }
    if (!forever) held = 0;
    return 0;
}

#define ioctl fixture_ioctl
#define read fixture_read
#define poll fixture_poll
#include "login_keyboard.h"
#undef ioctl
#undef read
#undef poll

static void reset(void) {
    held = forever = grabbed = grabs = releases = polls = reads = queries = 0;
    race = query_fail = grab_fail = read_fail = partial = flood = poll_fail = 0;
    quiet_fail = wrong_mode = quiet = 0;
    graphics_fail = wrong_graphics = graphics = 0;
}

int main(void) {
    reset();
    assert(synveil_login_vt_quiet(7) == 0 && quiet && graphics);
    reset(); quiet_fail = 1;
    assert(synveil_login_vt_quiet(7) == -1 && errno == EPERM && !graphics);
    reset(); wrong_mode = 1;
    assert(synveil_login_vt_quiet(7) == -1 && errno == EIO && !graphics);
    reset(); graphics_fail = 1;
    assert(synveil_login_vt_quiet(7) == -1 && errno == EPERM && !graphics);
    reset(); wrong_graphics = 1;
    assert(synveil_login_vt_quiet(7) == -1 && errno == EIO);

    reset();
    assert(synveil_login_keyboard_grab(7) == 0);
    assert(grabbed && grabs == 1 && releases == 0 && reads == 2);

    reset(); held = 1;
    assert(synveil_login_keyboard_grab(7) == 0);
    assert(grabbed && polls == 1 && grabs == 1);

    reset(); race = 1;
    assert(synveil_login_keyboard_grab(7) == 0);
    assert(grabbed && grabs == 2 && releases == 1 && polls == 1);

    reset(); held = forever = 1;
    assert(synveil_login_keyboard_grab(7) == -1 && errno == ETIMEDOUT);
    assert(!grabbed && grabs == 0 && polls == 80);

    reset(); query_fail = 1;
    assert(synveil_login_keyboard_grab(7) == -1 && errno == EIO && !grabbed);
    reset(); query_fail = 2;
    assert(synveil_login_keyboard_grab(7) == -1 && errno == EIO);
    assert(!grabbed && releases == 1);
    reset(); grab_fail = 1;
    assert(synveil_login_keyboard_grab(7) == -1 && errno == EBUSY && !grabbed);
    reset(); read_fail = 1;
    assert(synveil_login_keyboard_grab(7) == -1 && errno == ENODEV);
    assert(!grabbed && releases == 1);
    reset(); partial = 1;
    assert(synveil_login_keyboard_grab(7) == -1 && errno == EIO && !grabbed);
    reset(); flood = 1;
    assert(synveil_login_keyboard_grab(7) == -1 && errno == EOVERFLOW);
    assert(!grabbed && reads == 256);
    reset(); held = poll_fail = 1;
    assert(synveil_login_keyboard_grab(7) == -1 && errno == EIO && !grabbed);
    return 0;
}
