// SPDX-License-Identifier: MPL-2.0
// Local VM terminal on Linux fbcon. No model-to-shell execution path.
#define _GNU_SOURCE
#include <errno.h>
#include <dirent.h>
#include <fcntl.h>
#include <grp.h>
#include <linux/vt.h>
#include <linux/fb.h>
#include <linux/input.h>
#include <poll.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>
#include "lifetime.h"

#define DEMO_UID 1000
#define DEMO_GID 1000

static int is_character_device(const char *name) {
    struct stat info;
    return lstat(name, &info) == 0 && S_ISCHR(info.st_mode);
}

static int has_graphical_vt(void) {
    return is_character_device("/dev/fb0") &&
           is_character_device("/dev/tty0") &&
           is_character_device("/dev/tty1");
}

// A narrow optional pointer handle is delegated after authentication.
static int input_bit(const unsigned char *bits, size_t bytes, unsigned key) {
    return key / 8u < bytes && (bits[key / 8u] & (1u << (key % 8u))) != 0;
}

// Never transfer an evdev keyboard (including a composite keyboard/tablet)
 // as a pointer lease. The user process receives raw events on this FD,
 // so even ignored text keys would cross the login device boundary.
static int safe_pointer_keys(const unsigned char *keys, size_t count) {
    if (!input_bit(keys, count, BTN_LEFT)) return 0;
    // All Linux KEY_* text and function codes occupy the range below
    // BTN_MISC. No keyboard-capable composite may cross this raw FD lease.
    for (unsigned key = 0u; key < BTN_MISC; ++key) {
        if (input_bit(keys, count, key)) return 0;
    }
    return 1;
}

static int event_number(const char *name) {
    if (strncmp(name, "event", 5u) != 0 || name[5] == '\0') return 0;
    for (const char *p = name + 5u; *p; p++)
        if (*p < '0' || *p > '9') return 0;
    return 1;
}

static int open_session_pointer(void) {
    DIR *dir = opendir("/dev/input");
    if (!dir) return -1;
    int result = -1;
    struct dirent *entry;
    while ((entry = readdir(dir)) != NULL) {
        if (!event_number(entry->d_name)) continue;
        char name[256];
        int size = snprintf(name, sizeof(name), "/dev/input/%s", entry->d_name);
        if (size < 0 || (size_t)size >= sizeof(name)) continue;
        int fd = open(name, O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
        if (fd < 0) continue;
        struct stat info;
        unsigned char keys[(KEY_MAX + 8u) / 8u] = {0};
        unsigned char axes[(ABS_MAX + 8u) / 8u] = {0};
        struct input_absinfo x, y;
        if (fstat(fd, &info) == 0 && S_ISCHR(info.st_mode) &&
            ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(keys)), keys) >= 0 &&
            ioctl(fd, EVIOCGBIT(EV_ABS, sizeof(axes)), axes) >= 0 &&
            safe_pointer_keys(keys, sizeof(keys)) &&
            input_bit(axes, sizeof(axes), ABS_X) &&
            input_bit(axes, sizeof(axes), ABS_Y) &&
            ioctl(fd, EVIOCGABS(ABS_X), &x) >= 0 &&
            ioctl(fd, EVIOCGABS(ABS_Y), &y) >= 0 &&
            x.maximum > x.minimum && y.maximum > y.minimum) {
            result = fd;
            break;
        }
        close(fd);
    }
    closedir(dir);
    return result;
}

static int is_demo_mode(int argc, char **argv) {
    return argc == 2 && strcmp(argv[1], "--demo-terminal") == 0;
}

static int is_authenticated_mode(int argc, char **argv) {
    return argc == 2 && strcmp(argv[1], "--authenticated-terminal") == 0;
}

static int is_root_debug_mode(int argc, char **argv) {
    return argc == 3 &&
           strcmp(argv[1], "--developer-terminal") == 0 &&
           strcmp(argv[2], "--i-understand-root-shell") == 0;
}

// VT_WAITACTIVE can block forever when fbcon/VT activation breaks. Every
// launcher (including the authenticated one) has a finite wait budget.
#define VT_START_TIMEOUT_SECONDS 2
#define VT_START_POLL_MS 25

static int deadline_reached(const struct timespec *deadline,
                            const struct timespec *now) {
    return now->tv_sec > deadline->tv_sec ||
           (now->tv_sec == deadline->tv_sec && now->tv_nsec >= deadline->tv_nsec);
}

static int activate_terminal_vt(int fd, unsigned vt) {
    if (ioctl(fd, VT_ACTIVATE, vt) != 0) return -1;
    struct timespec deadline;
    if (clock_gettime(CLOCK_MONOTONIC, &deadline) != 0) return -1;
    deadline.tv_sec += VT_START_TIMEOUT_SECONDS;
    for (;;) {
        struct vt_stat current = {0};
        if (ioctl(fd, VT_GETSTATE, &current) != 0) return -1;
        if (current.v_active == vt) return 0;
        struct timespec now;
        if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return -1;
        if (deadline_reached(&deadline, &now)) {
            errno = ETIMEDOUT;
            return -1;
        }
        if (poll(NULL, 0u, VT_START_POLL_MS) < 0 && errno != EINTR) return -1;
    }
}

// A crashed UID1000 dashboard can leave tty1 in raw mode. Restore the
// canonical tty baseline *before every new authenticated launch* rather
// than treating the previous user's termios as trusted state.
static void restore_session_tty_mode(struct termios *mode) {
    mode->c_iflag &= ~(IGNCR | INLCR | IXOFF);
    mode->c_iflag |= ICRNL | IXON;
    mode->c_oflag |= OPOST | ONLCR;
    mode->c_lflag |= ISIG | ICANON | ECHO | ECHOE | ECHOK | IEXTEN;
    mode->c_cc[VINTR] = 3;
    mode->c_cc[VQUIT] = 28;
    mode->c_cc[VERASE] = 127;
    mode->c_cc[VKILL] = 21;
    mode->c_cc[VEOF] = 4;
    mode->c_cc[VSTART] = 17;
    mode->c_cc[VSTOP] = 19;
    mode->c_cc[VSUSP] = 26;
    mode->c_cc[VMIN] = 1;
    mode->c_cc[VTIME] = 0;
}

static int self_test(void) {
    char *demo[] = {"synveil-vt", "--demo-terminal", NULL};
    char *authenticated[] = {"synveil-vt", "--authenticated-terminal", NULL};
    char *unconfirmed[] = {"synveil-vt", "--developer-terminal", NULL};
    char *confirmed[] = {"synveil-vt", "--developer-terminal",
                         "--i-understand-root-shell", NULL};
    if (!event_number("event14") || event_number("event14-link") ||
        event_number("event")) return 1;
    unsigned char pointer_keys[(KEY_MAX + 8u) / 8u] = {0};
    pointer_keys[BTN_LEFT / 8u] |= (unsigned char)(1u << (BTN_LEFT % 8u));
    if (!safe_pointer_keys(pointer_keys, sizeof(pointer_keys))) return 1;
    // Every possible KEY_* code below BTN_MISC must invalidate the
    // delegated raw-event descriptor, not just the common ASCII keys.
    for (unsigned key = 0u; key < BTN_MISC; ++key) {
        pointer_keys[key / 8u] |= (unsigned char)(1u << (key % 8u));
        if (safe_pointer_keys(pointer_keys, sizeof(pointer_keys))) return 1;
        pointer_keys[key / 8u] &= (unsigned char)~(1u << (key % 8u));
    }
    if (!safe_pointer_keys(pointer_keys, sizeof(pointer_keys))) return 1;
    if (!is_demo_mode(2, demo) || !is_authenticated_mode(2, authenticated) ||
        is_authenticated_mode(2, demo) || is_root_debug_mode(2, unconfirmed) ||
        !is_root_debug_mode(3, confirmed) || is_root_debug_mode(2, demo)) {
        return 1;
    }
    struct termios restored = {0};
    restored.c_iflag = IGNCR | INLCR | IXOFF;
    restore_session_tty_mode(&restored);
    if (!(restored.c_iflag & ICRNL) || !(restored.c_iflag & IXON) ||
        (restored.c_iflag & (IGNCR | INLCR | IXOFF)) ||
        !(restored.c_oflag & OPOST) || !(restored.c_oflag & ONLCR) ||
        !(restored.c_lflag & ISIG) || !(restored.c_lflag & ICANON) ||
        !(restored.c_lflag & ECHO) ||
        restored.c_cc[VINTR] != 3 || restored.c_cc[VEOF] != 4 ||
        restored.c_cc[VMIN] != 1 || restored.c_cc[VTIME] != 0) return 1;
    struct timespec cutoff = {.tv_sec = 105, .tv_nsec = 420000000};
    struct timespec before = {.tv_sec = 105, .tv_nsec = 419999999};
    struct timespec exactly = {.tv_sec = 105, .tv_nsec = 420000000};
    struct timespec after = {.tv_sec = 106, .tv_nsec = 0};
    if (deadline_reached(&cutoff, &before) ||
        !deadline_reached(&cutoff, &exactly) ||
        !deadline_reached(&cutoff, &after)) return 1;
    return DEMO_UID != 1000 || DEMO_GID != 1000;
}

static void configure_demo_user(void) {
    // Restrict the development shell to a dedicated, unprivileged identity.
    // No inherited root groups, environment, or new privileged execs.
    if (chdir("/home/synveil") < 0 ||
        setgroups(0, NULL) < 0 ||
        setgid(DEMO_GID) < 0 ||
        setuid(DEMO_UID) < 0 ||
        prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) < 0 ||
        clearenv() != 0 ||
        setenv("HOME", "/home/synveil", 1) < 0 ||
        setenv("USER", "synveil", 1) < 0 ||
        setenv("LOGNAME", "synveil", 1) < 0 ||
        setenv("SHELL", "/bin/sh", 1) < 0 ||
        setenv("TERM", "linux", 1) < 0 ||
        setenv("PATH", "/bin:/usr/bin", 1) < 0) {
        _exit(1);
    }
    umask(0077);
}

static int launch_terminal(bool demo, bool authenticated) {
    if (geteuid() != 0) {
        fputs("[synveil-vt] VM terminal launcher requires root\n", stderr);
        return 1;
    }
    if (!has_graphical_vt()) {
        fputs("[synveil-vt] framebuffer/VT unavailable; use serial recovery\n", stderr);
        return 1;
    }
    int controller = open("/dev/tty0", O_RDWR | O_CLOEXEC | O_NOCTTY);
    if (controller < 0) {
        perror("[synveil-vt] open tty0");
        return 1;
    }
    int activated = activate_terminal_vt(controller, 1u);
    close(controller);
    if (activated != 0) {
        perror("[synveil-vt] activate tty1");
        return 1;
    }
    // Only the authenticated graphical session is given a framebuffer FD.
    // No device chmod, group privilege or general root helper is granted.
    int session_fb = -1;
    if (authenticated) {
        session_fb = open("/dev/fb0", O_RDWR | O_CLOEXEC | O_NOFOLLOW);
        struct stat info;
        if (session_fb < 0 || fstat(session_fb, &info) != 0 ||
            !S_ISCHR(info.st_mode)) {
            if (session_fb >= 0) close(session_fb);
            fputs("[synveil-vt] authenticated framebuffer unavailable\n", stderr);
            return 1;
        }
    }
    int pointer_fd = authenticated ? open_session_pointer() : -1;
    // Fixed destinations 3 (fbdev) and 5 (pointer) must not overwrite
    // source FDs; reserve high-numbered CLOEXEC handles before fork.
    if (authenticated) {
        int safe_fb = fcntl(session_fb, F_DUPFD_CLOEXEC, 10);
        if (safe_fb < 0) {
            close(session_fb);
            if (pointer_fd >= 0) close(pointer_fd);
            return 1;
        }
        close(session_fb);
        session_fb = safe_fb;
        if (pointer_fd >= 0) {
            int safe_pointer = fcntl(pointer_fd, F_DUPFD_CLOEXEC, 10);
            close(pointer_fd);
            pointer_fd = safe_pointer;
        }
    }
    pid_t launcher_pid = getpid();
    pid_t child = fork();
    if (child < 0) {
        if (session_fb >= 0) close(session_fb);
        if (pointer_fd >= 0) close(pointer_fd);
        perror("[synveil-vt] fork");
        return 1;
    }
    if (child != 0) {
        if (session_fb >= 0) close(session_fb);
        if (pointer_fd >= 0) close(pointer_fd);
        if (!demo) {
            fprintf(stderr, "[synveil-vt] root developer shell pid %ld\n", (long)child);
            return 0;
        }
        // Keep the framebuffer launcher suspended until this demo shell exits.
        int status = 0;
        while (waitpid(child, &status, 0) < 0) {
            if (errno == EINTR) continue;
            return 1;
        }
        return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
    }

    if (setsid() < 0) _exit(1);
    int terminal = open("/dev/tty1", O_RDWR | O_NOCTTY);
    if (terminal < 0 || ioctl(terminal, TIOCSCTTY, 0) < 0 ||
        dup2(terminal, STDIN_FILENO) < 0 ||
        dup2(terminal, STDOUT_FILENO) < 0 ||
        dup2(terminal, STDERR_FILENO) < 0) {
        _exit(1);
    }
    if (terminal > STDERR_FILENO) close(terminal);
    if (authenticated) {
        struct termios mode;
        if (tcgetattr(STDIN_FILENO, &mode) < 0) _exit(1);
        restore_session_tty_mode(&mode);
        // Drop raw-mode keystrokes queued by a crashed previous session.
        if (tcsetattr(STDIN_FILENO, TCSAFLUSH, &mode) < 0) _exit(1);
    }
    const char *banner = authenticated ?
        "\033[2J\033[H"
        "Synveil graphical user session (AUTHENTICATED UID 1000)\r\n"
        "VM evaluation only. ESC at the desktop logs out.\r\n\r\n" :
        demo ?
        "\033[2J\033[H"
        "Synveil demo console (NON-ROOT, NOT AUTHENTICATED)\r\n"
        "VM evaluation only. Type exit to return to the menu.\r\n\r\n" :
        "\033[2J\033[H"
        "Synveil developer console (UNAUTHENTICATED ROOT)\r\n"
        "Local test VM only. Type exit to close this shell.\r\n\r\n";
    size_t length = strlen(banner);
    if (write(STDOUT_FILENO, banner, length) != (ssize_t)length) _exit(1);
    if (demo) {
        configure_demo_user();
        // setuid clears Linux PDEATHSIG; rearm it only after UID/GID drop.
        // The helper exiting or being killed must terminate this session.
        if (synveil_parent_death_guard(launcher_pid) != 0) _exit(1);
        if (authenticated) {
            // Fixed, inherited framebuffer descriptor 3. dup2 with equal
            // descriptors preserves FD_CLOEXEC, so clear it explicitly.
            if (dup2(session_fb, 3) != 3) _exit(1);
            close(session_fb);
            if (pointer_fd >= 0) {
                if (dup2(pointer_fd, 5) != 5) _exit(1);
                close(pointer_fd);
            } else {
                close(5); // prevent inheritance of unrelated root FD
            }
            execl("/usr/bin/synveil-display", "synveil-display",
                  "--session", (char *)NULL);
            _exit(127);
        }
    }
    execl("/bin/sh", "sh", "-i", (char *)NULL);
    _exit(127);
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--self-test") == 0) return self_test();
    if (argc == 2 && strcmp(argv[1], "--probe") == 0) {
        if (has_graphical_vt()) {
            puts("SYNVEIL_VT_READY");
            return 0;
        }
        fputs("[synveil-vt] framebuffer or virtual terminal unavailable\n", stderr);
        return 1;
    }
    if (is_demo_mode(argc, argv)) return launch_terminal(true, false);
    if (is_authenticated_mode(argc, argv)) return launch_terminal(true, true);
    if (is_root_debug_mode(argc, argv)) return launch_terminal(false, false);
    fputs("usage: synveil-vt --probe | --self-test | --demo-terminal | "
          "--authenticated-terminal | --developer-terminal --i-understand-root-shell\n", stderr);
    return 2;
}
