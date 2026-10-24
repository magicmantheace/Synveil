// SPDX-License-Identifier: MPL-2.0
#ifndef SYNVEIL_LOGIN_KEYBOARD_H
#define SYNVEIL_LOGIN_KEYBOARD_H

#include <errno.h>
#include <linux/input.h>
#include <linux/kd.h>
#include <poll.h>
#include <stddef.h>
#include <sys/ioctl.h>
#include <unistd.h>

// The login VT has no console consumer. Suppress typed characters while
// waiting ungrabbed for old console modifiers to receive their key-up events.
// K_OFF still lets Linux process modifier releases, unlike EVIOCGRAB.
// VT2 must also own graphical scanout: a keyboard-quiet *text* VT can still
// obscure the framebuffer greeter after a fresh boot or VT handoff.
static inline int synveil_login_vt_quiet(int fd) {
    int keyboard_mode = -1;
    int display_mode = -1;
    if (ioctl(fd, KDSKBMODE, K_OFF) < 0 ||
        ioctl(fd, KDGKBMODE, &keyboard_mode) < 0) return -1;
    if (keyboard_mode != K_OFF) {
        errno = EIO;
        return -1;
    }
    if (ioctl(fd, KDSETMODE, KD_GRAPHICS) < 0 ||
        ioctl(fd, KDGETMODE, &display_mode) < 0) return -1;
    if (display_mode != KD_GRAPHICS) {
        errno = EIO;
        return -1;
    }
    return 0;
}

static inline int synveil_keyboard_idle(int fd) {
    unsigned char keys[(KEY_MAX + 8u) / 8u] = {0};
    if (ioctl(fd, EVIOCGKEY(sizeof(keys)), keys) < 0) return -1;
    for (size_t i = 0; i < sizeof(keys); ++i)
        if (keys[i]) return 0;
    return 1;
}

// Never steal a console key release by grabbing a held VT-switch chord.
// Recheck after grabbing to close the query/grab race; drain pre-login events
// without interpreting or logging them. Both waiting and draining are bounded.
static inline int synveil_login_keyboard_grab(int fd) {
    for (unsigned attempt = 0; attempt < 80u; ++attempt) {
        int idle = synveil_keyboard_idle(fd);
        if (idle < 0) return -1;
        if (idle) {
            if (ioctl(fd, EVIOCGRAB, 1) < 0) return -1;
            int drained = 0;
            for (unsigned n = 0; n < 256u; ++n) {
                struct input_event event;
                ssize_t got = read(fd, &event, sizeof(event));
                if (got < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                    drained = 1;
                    break;
                }
                if (got < 0 && errno == EINTR) continue;
                if (got != (ssize_t)sizeof(event)) {
                    int error = got < 0 ? errno : EIO;
                    (void)ioctl(fd, EVIOCGRAB, 0);
                    errno = error;
                    return -1;
                }
            }
            idle = drained ? synveil_keyboard_idle(fd) : -1;
            if (drained && idle == 1) return 0;
            int error = drained ? errno : EOVERFLOW;
            if (ioctl(fd, EVIOCGRAB, 0) < 0) return -1;
            if (idle < 0) {
                errno = error;
                return -1;
            }
        }
        if (poll(NULL, 0u, 25) < 0 && errno != EINTR) return -1;
    }
    errno = ETIMEDOUT;
    return -1;
}

#endif
