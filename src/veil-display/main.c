// SPDX-License-Identifier: MPL-2.0
// A source-built framebuffer diagnostic. Not a desktop or a privileged agent.
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <dirent.h>
#include <errno.h>
#include <linux/input.h>
#include <net/if.h>
#include <netinet/in.h>
#include <poll.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <unistd.h>
#include "preferences.h"
#include "lifetime.h"

struct surface {
    unsigned char *mapped;
    size_t length;
    struct fb_var_screeninfo variable;
    struct fb_fix_screeninfo fixed;
};

static uint32_t channel(uint8_t input, struct fb_bitfield field) {
    uint64_t maximum = ((uint64_t)1u << field.length) - 1u;
    return (uint32_t)(((uint64_t)input * maximum + 127u) / 255u) << field.offset;
}

static uint32_t rgb(const struct surface *screen, uint8_t red, uint8_t green, uint8_t blue) {
    return channel(red, screen->variable.red) |
           channel(green, screen->variable.green) |
           channel(blue, screen->variable.blue);
}

static int valid_layout(const struct surface *screen) {
    const struct fb_var_screeninfo *v = &screen->variable;
    const struct fb_fix_screeninfo *f = &screen->fixed;
    if (v->bits_per_pixel != 32 || !v->xres || !v->yres || !f->smem_len ||
        (f->visual != FB_VISUAL_TRUECOLOR && f->visual != FB_VISUAL_DIRECTCOLOR)) {
        return 0;
    }
    const struct fb_bitfield channels[] = {v->red, v->green, v->blue};
    for (size_t i = 0; i < sizeof(channels) / sizeof(channels[0]); i++) {
        if (!channels[i].length || channels[i].length > 16 ||
            channels[i].offset >= 32 ||
            channels[i].length > 32 - channels[i].offset) {
            return 0;
        }
    }
    const uint64_t last_pixel = (uint64_t)(v->yoffset + (uint64_t)v->yres - 1u) * f->line_length +
                                ((uint64_t)v->xoffset + v->xres) * 4u;
    return last_pixel <= f->smem_len;
}

static void rectangle(const struct surface *screen, unsigned x, unsigned y,
                      unsigned width, unsigned height, uint32_t color) {
    const unsigned max_x = screen->variable.xres;
    const unsigned max_y = screen->variable.yres;
    if (x >= max_x || y >= max_y) return;
    if (width > max_x - x) width = max_x - x;
    if (height > max_y - y) height = max_y - y;
    for (unsigned row = 0; row < height; row++) {
        uint64_t start = (uint64_t)(screen->variable.yoffset + y + row) * screen->fixed.line_length +
                         (uint64_t)(screen->variable.xoffset + x) * 4u;
        for (unsigned col = 0; col < width; col++) {
            memcpy(screen->mapped + start + (uint64_t)col * 4u, &color, sizeof(color));
        }
    }
}

// Purposefully small glyph set for diagnostic headings. A real desktop will
// use a source-built font renderer and compositor instead.
static const uint8_t letters[26][7] = {
    {14,17,17,31,17,17,17}, {30,17,17,30,17,17,30},
    {14,17,16,16,16,17,14}, {30,17,17,17,17,17,30},
    {31,16,16,30,16,16,31}, {31,16,16,30,16,16,16},
    {14,17,16,23,17,17,14}, {17,17,17,31,17,17,17},
    {31,4,4,4,4,4,31}, {7,2,2,2,18,18,12},
    {17,18,20,24,20,18,17}, {16,16,16,16,16,16,31},
    {17,27,21,21,17,17,17}, {17,25,21,19,17,17,17},
    {14,17,17,17,17,17,14}, {30,17,17,30,16,16,16},
    {14,17,17,17,21,18,13}, {30,17,17,30,20,18,17},
    {15,16,16,14,1,1,30}, {31,4,4,4,4,4,4},
    {17,17,17,17,17,17,14}, {17,17,17,17,17,10,4},
    {17,17,17,21,21,21,10}, {17,17,10,4,10,17,17},
    {17,17,10,4,4,4,4}, {31,1,2,4,8,16,31}
};

static const uint8_t digits[10][7] = {
    {14,17,19,21,25,17,14}, {4,12,4,4,4,4,14},
    {14,17,1,2,4,8,31}, {30,1,1,14,1,1,30},
    {2,6,10,18,31,2,2}, {31,16,30,1,1,17,14},
    {6,8,16,30,17,17,14}, {31,1,2,4,8,8,8},
    {14,17,17,14,17,17,14}, {14,17,17,15,1,2,12}
};
static const uint8_t dot_glyph[7] = {0,0,0,0,0,6,6};
static const uint8_t colon_glyph[7] = {0,4,4,0,4,4,0};

static const uint8_t *glyph_for(unsigned char c) {
    if (c >= 'A' && c <= 'Z') return letters[c - 'A'];
    if (c >= '0' && c <= '9') return digits[c - '0'];
    if (c == '.') return dot_glyph;
    if (c == ':') return colon_glyph;
    return NULL;
}

static void heading(const struct surface *screen, const char *message,
                    unsigned x, unsigned y, unsigned scale, uint32_t color) {
    for (const unsigned char *cursor = (const unsigned char *)message; *cursor; cursor++) {
        const uint8_t *glyph = glyph_for(*cursor);
        if (glyph != NULL) {
            for (unsigned gy = 0; gy < 7; gy++) {
                for (unsigned gx = 0; gx < 5; gx++) {
                    if ((glyph[gy] & (1u << (4u - gx))) != 0u) {
                        rectangle(screen, x + gx * scale, y + gy * scale,
                                  scale, scale, color);
                    }
                }
            }
        }
        x += 6u * scale;
    }
}

static void paint(const struct surface *screen) {
    unsigned w = screen->variable.xres;
    unsigned h = screen->variable.yres;
    uint32_t base = rgb(screen, 13, 20, 36);
    rectangle(screen, 0, 0, w, h, base);
    rectangle(screen, 0, 0, w, h / 10u, rgb(screen, 19, 51, 79));
    rectangle(screen, 0, h / 10u, w, 3, rgb(screen, 80, 169, 215));
    unsigned scale = w >= 800u ? 5u : (w >= 480u ? 3u : 2u);
    heading(screen, "SYNVEIL", w / 16u, h / 5u, scale, rgb(screen, 220, 242, 250));
    heading(screen, "BOOTSTRAP", w / 16u, h / 5u + 11u * scale,
            2u, rgb(screen, 114, 194, 227));
    heading(screen, "GRAPHICS READY", w / 16u, h / 2u + 18u, 2u,
            rgb(screen, 190, 216, 224));
    rectangle(screen, w / 16u, h * 4u / 5u, w * 7u / 8u, 4u,
              rgb(screen, 74, 157, 196));
}

// Deliberately tiny read-only framebuffer launcher prototype. Terminal and
// privileged system actions are not exposed through this UI.
struct menu_state {
    unsigned selected;
    int page;       // -1: launcher; 0-2: read-only, 3: demo terminal
    int quit;
    int launch;
    int high_contrast;
    unsigned cursor_scale;  // 0: normal, 1: large
};

static void menu_key(struct menu_state *state, unsigned code) {
    if (code == KEY_ESC) {
        if (state->page >= 0) state->page = -1;
        else state->quit = 1;
    } else if (code == KEY_BACKSPACE) {
        state->page = -1;
    } else if (state->page == 3 && code == KEY_ENTER) {
        // Explicit second action: selection alone never launches a shell.
        state->launch = 1;
    } else if (state->page == 4) {
        if (code == KEY_LEFT || code == KEY_RIGHT ||
            code == KEY_A || code == KEY_D) {
            state->high_contrast = !state->high_contrast;
        } else if (code == KEY_UP) {
            state->cursor_scale = 1u;
        } else if (code == KEY_DOWN) {
            state->cursor_scale = 0u;
        }
    } else if (state->page < 0) {
        if (code == KEY_LEFT || code == KEY_A) state->selected = (state->selected + 4u) % 5u;
        else if (code == KEY_RIGHT || code == KEY_D) state->selected = (state->selected + 1u) % 5u;
        else if (code == KEY_ENTER) state->page = (int)state->selected;
    }
}

static int mounted_state(void) {
    FILE *mounts = fopen("/proc/mounts", "r");
    if (!mounts) return 0;
    char line[512];
    int found = 0;
    while (fgets(line, sizeof(line), mounts)) {
        if (strstr(line, " /var ext4 ") != NULL) {
            found = 1;
            break;
        }
    }
    fclose(mounts);
    return found;
}

static void show_runtime_details(const struct surface *screen, int page,
                                 unsigned y, uint32_t color) {
    char line[96];
    if (page == 0) {
        long cpus = sysconf(_SC_NPROCESSORS_ONLN);
        if (cpus > 0) {
            snprintf(line, sizeof(line), "CPU %ld THREADS", cpus);
            heading(screen, line, 32u, y, 2u, color);
        }
        FILE *memory = fopen("/proc/meminfo", "r");
        if (memory != NULL) {
            unsigned long long available = 0;
            char buffer[256];
            while (fgets(buffer, sizeof(buffer), memory)) {
                if (strncmp(buffer, "MemAvailable:", 13) == 0 &&
                    sscanf(buffer + 13, " %llu kB", &available) == 1) break;
            }
            fclose(memory);
            if (available > 0) {
                snprintf(line, sizeof(line), "RAM %llu MB AVAILABLE", available / 1024u);
                heading(screen, line, 32u, y + 34u, 2u, color);
            }
        }
        FILE *uptime = fopen("/proc/uptime", "r");
        if (uptime != NULL) {
            double seconds = 0.0;
            if (fscanf(uptime, "%lf", &seconds) == 1 &&
                seconds >= 0.0 && seconds < 1.0e12) {
                snprintf(line, sizeof(line), "UPTIME %llu MIN",
                         (unsigned long long)(seconds / 60.0));
                heading(screen, line, 32u, y + 68u, 2u, color);
            }
            fclose(uptime);
        }
    } else if (page == 1 && mounted_state()) {
        struct statvfs filesystem;
        if (statvfs("/var", &filesystem) == 0 && filesystem.f_frsize != 0) {
            unsigned long long free_mb =
                (unsigned long long)filesystem.f_bavail * filesystem.f_frsize / 1048576ull;
            snprintf(line, sizeof(line), "FREE %llu MB", free_mb);
            heading(screen, line, 32u, y, 2u, color);
        }
    } else if (page == 2) {
        int fd = socket(AF_INET, SOCK_DGRAM, 0);
        if (fd >= 0) {
            struct ifreq request = {0};
            snprintf(request.ifr_name, sizeof(request.ifr_name), "eth0");
            if (ioctl(fd, SIOCGIFADDR, &request) == 0) {
                const struct sockaddr_in *address =
                    (const struct sockaddr_in *)&request.ifr_addr;
                char ip[INET_ADDRSTRLEN];
                if (inet_ntop(AF_INET, &address->sin_addr, ip, sizeof(ip)) != NULL) {
                    snprintf(line, sizeof(line), "IP %s", ip);
                    heading(screen, line, 32u, y, 2u, color);
                }
            } else {
                heading(screen, "IP NOT ASSIGNED", 32u, y, 2u, color);
            }
            close(fd);
        }
    }
}

static void render_menu(const struct surface *screen, const struct menu_state *state) {
    unsigned w = screen->variable.xres;
    unsigned h = screen->variable.yres;
    uint32_t background = state->high_contrast ?
        rgb(screen, 245, 248, 250) : rgb(screen, 12, 21, 34);
    uint32_t title = state->high_contrast ?
        rgb(screen, 9, 22, 31) : rgb(screen, 219, 237, 244);
    uint32_t muted = state->high_contrast ?
        rgb(screen, 29, 53, 67) : rgb(screen, 134, 171, 190);
    uint32_t highlight = state->high_contrast ?
        rgb(screen, 232, 171, 28) : rgb(screen, 62, 148, 187);
    rectangle(screen, 0, 0, w, h, background);
    rectangle(screen, 0, 0, w, h / 11u, rgb(screen, 21, 51, 78));
    heading(screen, "SYNVEIL", 24u, 18u, 3u, title);
    if (state->page >= 0) {
        const char *pages[] = {"STATUS", "STORAGE", "NETWORK", "TERMINAL", "SETTINGS"};
        heading(screen, pages[state->page], 32u, h / 5u, 3u, title);
        if (state->page == 4) {
            heading(screen, state->high_contrast ? "BRIGHT COLORS" : "DARK COLORS",
                    32u, h / 3u, 2u, muted);
            heading(screen, "LEFT RIGHT TO CHANGE", 32u, h / 3u + 35u, 2u, muted);
            heading(screen, state->cursor_scale ? "LARGE POINTER" : "SMALL POINTER",
                    32u, h / 2u + 10u, 2u, muted);
            heading(screen, "UP DOWN TO CHANGE", 32u, h / 2u + 45u, 2u, muted);
            heading(screen, "BACK", w > 125u ? w - 105u : 0u, 18u, 2u, title);
            return;
        }
        if (state->page == 3) {
            heading(screen, "NON ROOT DEMO", 32u, h / 3u, 2u, muted);
            rectangle(screen, 32u, h / 2u - 10u, 250u, 50u, highlight);
            heading(screen, "OPEN TERMINAL", 40u, h / 2u + 5u, 2u, title);
            heading(screen, "ENTER TO OPEN", 32u, h * 3u / 5u, 2u, muted);
            heading(screen, "BACK", w > 125u ? w - 105u : 0u, 18u, 2u, title);
            return;
        }
        const char *label = "";
        if (state->page == 0) {
            label = access("/run/synveil/veil-core.sock", F_OK) == 0 ?
                "CORE SOCKET PRESENT" : "CORE SOCKET MISSING";
        } else if (state->page == 1) {
            label = mounted_state() ? "STATE DISK MOUNTED" : "STATE DISK MISSING";
        } else {
            label = access("/sys/class/net/eth0", F_OK) == 0 ?
                "VIRTUAL NIC PRESENT" : "VIRTUAL NIC MISSING";
        }
        heading(screen, label, 32u, h / 3u, 2u, muted);
        show_runtime_details(screen, state->page, h / 3u + 44u, muted);
        heading(screen, "BACK", w > 125u ? w - 105u : 0u, 18u, 2u, title);
        heading(screen, "ESC TO GO BACK", 32u, h * 4u / 5u, 2u, title);
        return;
    }
    heading(screen, "SYSTEM LAUNCHER", 32u, h / 5u, 3u, title);
    const char *pages[] = {"STATUS", "STORAGE", "NETWORK", "TERMINAL", "SETTINGS"};
    for (unsigned i = 0; i < 5u; ++i) {
        unsigned x = 32u + i * ((w > 140u ? w - 64u : w) / 5u);
        unsigned tile = w > 140u ? (w - 72u) / 5u : 20u;
        rectangle(screen, x, h / 3u, tile, h / 5u,
                  i == state->selected ? highlight : rgb(screen, 34, 61, 78));
        unsigned scale = tile >= 120u ? 2u : 1u;
        heading(screen, pages[i], x + 6u, h / 3u + 18u, scale, title);
    }
    heading(screen, "LEFT RIGHT ENTER", 32u, h * 4u / 5u, 2u, muted);
    heading(screen, "ESC TO EXIT", 32u, h * 4u / 5u + 24u, 2u, muted);
}

static int event_bit(const unsigned char *bits, size_t count, unsigned bit) {
    return bit / 8u < count && (bits[bit / 8u] & (1u << (bit % 8u))) != 0;
}

static int open_keyboard(void) {
    DIR *directory = opendir("/dev/input");
    if (directory == NULL) return -1;
    struct dirent *item;
    int chosen = -1;
    while ((item = readdir(directory)) != NULL) {
        if (strncmp(item->d_name, "event", 5) != 0) continue;
        const char *digit = item->d_name + 5;
        if (*digit == '\0') continue;
        int valid = 1;
        for (const char *p = digit; *p; p++)
            if (*p < '0' || *p > '9') valid = 0;
        if (!valid) continue;
        char path[256];
        int len = snprintf(path, sizeof(path), "/dev/input/%s", item->d_name);
        if (len < 0 || (size_t)len >= sizeof(path)) continue;
        int fd = open(path, O_RDONLY | O_CLOEXEC | O_NONBLOCK | O_NOFOLLOW);
        if (fd < 0) continue;
        struct stat info;
        unsigned char bits[(KEY_MAX + 8u) / 8u] = {0};
        if (fstat(fd, &info) == 0 && S_ISCHR(info.st_mode) &&
            ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(bits)), bits) >= 0 &&
            event_bit(bits, sizeof(bits), KEY_ENTER) &&
            event_bit(bits, sizeof(bits), KEY_LEFT) &&
            event_bit(bits, sizeof(bits), KEY_RIGHT)) {
            chosen = fd;
            break;
        }
        close(fd);
    }
    closedir(directory);
    return chosen;
}

struct pointer_device {
    int fd;
    struct input_absinfo x;
    struct input_absinfo y;
    unsigned screen_x;
    unsigned screen_y;
};

static int event_number(const char *name) {
    if (strncmp(name, "event", 5) != 0 || name[5] == '\0') return 0;
    for (const char *p = name + 5; *p; p++)
        if (*p < '0' || *p > '9') return 0;
    return 1;
}

static struct pointer_device open_pointer(void) {
    struct pointer_device pointer = {.fd = -1};
    DIR *directory = opendir("/dev/input");
    if (!directory) return pointer;
    struct dirent *item;
    while ((item = readdir(directory)) != NULL) {
        if (!event_number(item->d_name)) continue;
        char path[256];
        int len = snprintf(path, sizeof(path), "/dev/input/%s", item->d_name);
        if (len < 0 || (size_t)len >= sizeof(path)) continue;
        int fd = open(path, O_RDONLY | O_CLOEXEC | O_NONBLOCK | O_NOFOLLOW);
        if (fd < 0) continue;
        struct stat info;
        unsigned char events[(EV_MAX + 8u) / 8u] = {0};
        unsigned char keys[(KEY_MAX + 8u) / 8u] = {0};
        unsigned char axes[(ABS_MAX + 8u) / 8u] = {0};
        if (fstat(fd, &info) == 0 && S_ISCHR(info.st_mode) &&
            ioctl(fd, EVIOCGBIT(0, sizeof(events)), events) >= 0 &&
            event_bit(events, sizeof(events), EV_KEY) &&
            event_bit(events, sizeof(events), EV_ABS) &&
            ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(keys)), keys) >= 0 &&
            ioctl(fd, EVIOCGBIT(EV_ABS, sizeof(axes)), axes) >= 0 &&
            event_bit(keys, sizeof(keys), BTN_LEFT) &&
            event_bit(axes, sizeof(axes), ABS_X) &&
            event_bit(axes, sizeof(axes), ABS_Y) &&
            ioctl(fd, EVIOCGABS(ABS_X), &pointer.x) >= 0 &&
            ioctl(fd, EVIOCGABS(ABS_Y), &pointer.y) >= 0 &&
            pointer.x.maximum > pointer.x.minimum &&
            pointer.y.maximum > pointer.y.minimum) {
            pointer.fd = fd;
            break;
        }
        close(fd);
    }
    closedir(directory);
    return pointer;
}

static unsigned absolute_pixel(int value, int minimum, int maximum, unsigned extent) {
    if (maximum <= minimum || extent == 0u) return 0u;
    int64_t range = (int64_t)maximum - minimum;
    int64_t offset = (int64_t)value - minimum;
    if (offset <= 0) return 0u;
    if (offset >= range) return extent - 1u;
    return (unsigned)((uint64_t)offset * (extent - 1u) / (uint64_t)range);
}

static void menu_click(struct menu_state *state, unsigned x, unsigned y,
                       unsigned width, unsigned height) {
    if (state->page >= 0) {
        if (y <= height / 11u && x >= (width > 125u ? width - 125u : 0u)) {
            state->page = -1;
        } else if (state->page == 3 && x >= 32u && x < 282u &&
                   y >= height / 2u - 10u && y < height / 2u + 40u) {
            state->launch = 1;
        } else if (state->page == 4 && x >= 32u && x < width / 2u) {
            if (y >= height / 3u && y < height / 3u + 80u)
                state->high_contrast = !state->high_contrast;
            if (y >= height / 2u && y < height / 2u + 90u)
                state->cursor_scale = !state->cursor_scale;
        }
        return;
    }
    unsigned tile_spacing = (width > 140u ? width - 64u : width) / 5u;
    unsigned tile_width = width > 140u ? (width - 72u) / 5u : 20u;
    if (y < height / 3u || y >= height / 3u + height / 5u) return;
    for (unsigned i = 0; i < 5u; ++i) {
        unsigned left = 32u + i * tile_spacing;
        if (x >= left && x - left < tile_width) {
            state->selected = i;
            state->page = (int)i;
            break;
        }
    }
}

static void render_cursor(const struct surface *screen, const struct pointer_device *pointer,
                          const struct menu_state *state) {
    if (pointer->fd >= 0) {
        unsigned size = state->cursor_scale ? 16u : 9u;
        rectangle(screen, pointer->screen_x, pointer->screen_y, size, size,
                  state->high_contrast ? rgb(screen, 25, 75, 150) :
                                         rgb(screen, 251, 210, 102));
        rectangle(screen, pointer->screen_x + 2u, pointer->screen_y + 2u,
                  state->cursor_scale ? 9u : 3u, state->cursor_scale ? 9u : 3u,
                  state->high_contrast ? rgb(screen, 245, 248, 250) :
                                         rgb(screen, 31, 52, 73));
    }
}

static int launch_demo_terminal(void) {
    // Fixed binary and arguments, never a shell command or model-provided text.
    // A display crash must kill its terminal launcher, not orphan a shell.
    pid_t owner = getpid();
    pid_t process = fork();
    if (process < 0) return 1;
    if (process == 0) {
        if (synveil_parent_death_guard(owner) != 0) _exit(1);
        execl("/usr/bin/synveil-vt", "synveil-vt", "--demo-terminal", (char *)NULL);
        _exit(127);
    }
    int status = 0;
    while (waitpid(process, &status, 0) < 0) {
        if (errno == EINTR) continue;
        return 1;
    }
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}

static int run_menu(const struct surface *screen) {
    int keyboard = open_keyboard();
    if (keyboard < 0) {
        fputs("[synveil-display] evdev keyboard unavailable\n", stderr);
        return 1;
    }
    struct menu_state state = {.selected = 0u, .page = -1, .quit = 0};
    // Persist only explicit display choices. No authorization or AI policy
    // is read from this file. Without the opt-in ext4 state disk the UI works
    // exactly as before, with ephemeral settings.
    const char *const preferences_dir = "/var/lib/synveil";
    if (mounted_state()) {
        struct desktop_preferences loaded = {0};
        if (preferences_load(preferences_dir, &loaded)) {
            state.high_contrast = loaded.high_contrast;
            state.cursor_scale = loaded.cursor_scale;
            puts("\nSYNVEIL_SETTINGS_LOADED");
        }
    }
    struct pointer_device pointer = open_pointer();
    render_menu(screen, &state);
    render_cursor(screen, &pointer, &state);
    // The serial recovery shell may already have printed a prompt without
    // a newline. Begin on a new line so CI can require a standalone marker.
    puts("\nSYNVEIL_DESKTOP_READY");
    fflush(stdout);
    while (!state.quit) {
        int old_contrast = state.high_contrast;
        unsigned old_cursor_scale = state.cursor_scale;
        struct pollfd fds[2] = {{.fd = keyboard, .events = POLLIN},
                                {.fd = pointer.fd, .events = POLLIN}};
        nfds_t count = pointer.fd >= 0 ? 2u : 1u;
        int result = poll(fds, count, 1000);
        if (result < 0) {
            if (errno == EINTR) continue;
            break;
        }
        if ((fds[0].revents & (POLLERR | POLLHUP | POLLNVAL)) != 0) break;
        int redraw = result == 0 && state.page >= 0;
        if ((fds[0].revents & POLLIN) != 0) {
            struct input_event event;
            if (read(keyboard, &event, sizeof(event)) == (ssize_t)sizeof(event) &&
                event.type == EV_KEY && event.value == 1) {
                menu_key(&state, event.code);
                redraw = 1;
            }
        }
        if (pointer.fd >= 0 && (fds[1].revents & (POLLERR | POLLHUP | POLLNVAL)) != 0) {
            close(pointer.fd);
            pointer.fd = -1;
            redraw = 1;
        } else if (pointer.fd >= 0 && (fds[1].revents & POLLIN) != 0) {
            struct input_event event;
            if (read(pointer.fd, &event, sizeof(event)) == (ssize_t)sizeof(event)) {
                if (event.type == EV_ABS && event.code == ABS_X) {
                    pointer.screen_x = absolute_pixel(event.value, pointer.x.minimum,
                                                      pointer.x.maximum, screen->variable.xres);
                    redraw = 1;
                } else if (event.type == EV_ABS && event.code == ABS_Y) {
                    pointer.screen_y = absolute_pixel(event.value, pointer.y.minimum,
                                                      pointer.y.maximum, screen->variable.yres);
                    redraw = 1;
                } else if (event.type == EV_KEY && event.code == BTN_LEFT && event.value == 1) {
                    menu_click(&state, pointer.screen_x, pointer.screen_y,
                               screen->variable.xres, screen->variable.yres);
                    redraw = 1;
                }
            }
        }
        if (state.launch) {
            state.launch = 0;
            if (launch_demo_terminal() != 0)
                fputs("[synveil-display] demo terminal did not start or exited abnormally\n", stderr);
            state.page = -1;
            redraw = 1;
        }
        if (state.high_contrast != old_contrast ||
            state.cursor_scale != old_cursor_scale) {
            if (mounted_state()) {
                struct desktop_preferences current = {
                    .high_contrast = state.high_contrast,
                    .cursor_scale = state.cursor_scale
                };
                if (preferences_save(preferences_dir, &current) != 0) {
                    fputs("[synveil-display] could not persist display settings\n", stderr);
                } else {
                    puts("\nSYNVEIL_SETTINGS_SAVED");
                    fflush(stdout);
                }
            }
        }
        if (redraw && !state.quit) {
            render_menu(screen, &state);
            render_cursor(screen, &pointer, &state);
        }
    }
    if (pointer.fd >= 0) close(pointer.fd);
    close(keyboard);
    return state.quit ? 0 : 1;
}

// Experimental *developer-only* graphical login client for veil-seat.
// Never renders raw password bytes, grants root commands or authorizes a
// session itself. A production compositor/input isolation layer is pending.
#define AUTH_MAX_UI_PASSWORD 128u
#define AUTH_SEAT_SOCKET "/run/synveil/veil-seat.sock"

struct auth_input {
    unsigned char password[AUTH_MAX_UI_PASSWORD];
    unsigned length;
    int shift;
    int denied;
};

static void auth_clear(struct auth_input *state) {
    // Force wiping the UI-side secret even under compiler optimization.
    volatile unsigned char *bytes = state->password;
    for (unsigned i = 0; i < AUTH_MAX_UI_PASSWORD; i++) bytes[i] = 0u;
    state->length = 0u;
}

static unsigned char auth_key_char(unsigned code, int shift) {
    const struct {unsigned key; unsigned char value;} keys[] = {
        {KEY_A,'a'}, {KEY_B,'b'}, {KEY_C,'c'}, {KEY_D,'d'},
        {KEY_E,'e'}, {KEY_F,'f'}, {KEY_G,'g'}, {KEY_H,'h'},
        {KEY_I,'i'}, {KEY_J,'j'}, {KEY_K,'k'}, {KEY_L,'l'},
        {KEY_M,'m'}, {KEY_N,'n'}, {KEY_O,'o'}, {KEY_P,'p'},
        {KEY_Q,'q'}, {KEY_R,'r'}, {KEY_S,'s'}, {KEY_T,'t'},
        {KEY_U,'u'}, {KEY_V,'v'}, {KEY_W,'w'}, {KEY_X,'x'},
        {KEY_Y,'y'}, {KEY_Z,'z'}
    };
    for (unsigned i = 0; i < sizeof(keys)/sizeof(keys[0]); i++) {
        if (keys[i].key == code) {
            return (unsigned char)(shift ? keys[i].value - ('a'-'A') : keys[i].value);
        }
    }
    const struct {unsigned key; unsigned char normal; unsigned char shifted;} symbols[] = {
        {KEY_1,'1','!'}, {KEY_2,'2','@'}, {KEY_3,'3','#'},
        {KEY_4,'4','$'}, {KEY_5,'5','%'}, {KEY_6,'6','^'},
        {KEY_7,'7','&'}, {KEY_8,'8','*'}, {KEY_9,'9','('},
        {KEY_0,'0',')'}, {KEY_MINUS,'-','_'}, {KEY_EQUAL,'=','+'},
        {KEY_DOT,'.','>'}, {KEY_COMMA,',','<'}, {KEY_SLASH,'/','?'},
        {KEY_SPACE,' ',' '}, {KEY_SEMICOLON,';',':'},
        {KEY_APOSTROPHE,39u,34u}
    };
    for (unsigned i = 0; i < sizeof(symbols)/sizeof(symbols[0]); i++) {
        if (symbols[i].key == code)
            return shift ? symbols[i].shifted : symbols[i].normal;
    }
    return 0u;
}

static void auth_input_key(struct auth_input *state, unsigned code, int value) {
    if (code == KEY_LEFTSHIFT || code == KEY_RIGHTSHIFT) {
        state->shift = value != 0;
        return;
    }
    if (value != 1) return;
    if (code == KEY_BACKSPACE) {
        if (state->length) {
            state->length--;
            state->password[state->length] = 0;
        }
        return;
    }
    unsigned char letter = auth_key_char(code, state->shift);
    if (letter && state->length < AUTH_MAX_UI_PASSWORD) {
        state->password[state->length++] = letter;
        state->denied = 0;
    }
}

static void auth_render(const struct surface *screen, const struct auth_input *state) {
    unsigned w = screen->variable.xres;
    unsigned h = screen->variable.yres;
    uint32_t background = rgb(screen, 12, 21, 34);
    uint32_t text = rgb(screen, 219, 237, 244);
    uint32_t muted = rgb(screen, 134, 171, 190);
    rectangle(screen, 0u, 0u, w, h, background);
    rectangle(screen, 0u, 0u, w, h/11u, rgb(screen, 21, 51, 78));
    heading(screen, "SYNVEIL", 24u, 18u, 3u, text);
    heading(screen, "LOCAL LOGIN", 36u, h/4u, 3u, text);
    heading(screen, "PASSWORD", 36u, h/3u, 2u, muted);
    // Constant glyph per character, never a raw or encoded secret.
    unsigned visible = state->length < 26u ? state->length : 26u;
    for (unsigned i = 0; i < visible; i++) {
        rectangle(screen, 40u + i*15u, h/2u, 8u, 8u, text);
    }
    if (state->denied) heading(screen, "LOGIN DENIED", 36u, h*3u/5u, 2u, muted);
    heading(screen, "ENTER TO LOGIN", 36u, h*4u/5u, 2u, text);
    heading(screen, "ESC TO EXIT", 36u, h*4u/5u+32u, 2u, muted);
}

static int auth_send_all(int fd, const unsigned char *bytes, size_t len) {
    while (len) {
        ssize_t n = send(fd, bytes, len, MSG_NOSIGNAL);
        if (n <= 0) return -1;
        bytes += (size_t)n;
        len -= (size_t)n;
    }
    return 0;
}

static int auth_attempt(struct auth_input *state) {
    int connected = -1;
    if (state->length < 8u) goto denied;
    connected = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (connected < 0) goto denied;
    struct timeval limit = {.tv_sec = 4, .tv_usec = 0};
    if (setsockopt(connected, SOL_SOCKET, SO_SNDTIMEO, &limit, sizeof(limit)) != 0 ||
        setsockopt(connected, SOL_SOCKET, SO_RCVTIMEO, &limit, sizeof(limit)) != 0)
        goto denied;
    struct sockaddr_un addr = {0};
    addr.sun_family = AF_UNIX;
    const char *name = AUTH_SEAT_SOCKET;
    if (strlen(name) >= sizeof(addr.sun_path)) goto denied;
    memcpy(addr.sun_path, name, strlen(name)+1u);
    if (connect(connected, (struct sockaddr *)&addr, sizeof(addr)) != 0) goto denied;
    unsigned char header[] = {'S','V','L','1',0,(unsigned char)state->length};
    if (auth_send_all(connected, header, sizeof(header)) != 0 ||
        auth_send_all(connected, state->password, state->length) != 0)
        goto denied;
    auth_clear(state);
    // The broker sends only ACTIVE/DENIED/THROTTLED; never a token.
    unsigned char response[7] = {0};
    size_t have = 0;
    while (have < sizeof(response)) {
        ssize_t n = recv(connected, response+have, sizeof(response)-have, 0);
        if (n <= 0) goto denied;
        have += (size_t)n;
    }
    if (memcmp(response, "ACTIVE\n", sizeof(response)) != 0) goto denied;
    puts("\nSYNVEIL_GRAPHICAL_AUTH_ACTIVE");
    fflush(stdout);
    // This open descriptor is the lease. On a UI crash the OS closes the
    // socket and veil-seat immediately revokes the unprivileged VT session.
    limit.tv_sec = 1;
    if (setsockopt(connected, SOL_SOCKET, SO_RCVTIMEO, &limit, sizeof(limit)) != 0)
        goto denied;
    for (;;) {
        unsigned char discard[16];
        ssize_t n = recv(connected, discard, sizeof(discard), 0);
        if (n == 0) break;
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) continue;
        if (n < 0) break;
        if (n > 0) break;
    }
    close(connected);
    return 1;

denied:
    auth_clear(state);
    if (connected >= 0) close(connected);
    return 0;
}

static int run_auth_login(const struct surface *screen) {
    int keyboard = open_keyboard();
    if (keyboard < 0) return 1;
    struct auth_input state = {0};
    auth_render(screen, &state);
    puts("\nSYNVEIL_LOGIN_READY");
    fflush(stdout);
    int quit = 0;
    while (!quit) {
        struct pollfd fd = {.fd = keyboard, .events = POLLIN};
        int n = poll(&fd, 1u, 1000);
        if (n < 0) {
            if (errno == EINTR) continue;
            break;
        }
        if (fd.revents & (POLLERR | POLLHUP | POLLNVAL)) break;
        if ((fd.revents & POLLIN) == 0) continue;
        struct input_event event;
        if (read(keyboard, &event, sizeof(event)) != (ssize_t)sizeof(event) ||
            event.type != EV_KEY) continue;
        if (event.value == 1 && event.code == KEY_ESC) {
            quit = 1;
        } else if (event.value == 1 && event.code == KEY_ENTER) {
            state.denied = !auth_attempt(&state);
            if (state.denied) {
                puts("\nSYNVEIL_GRAPHICAL_AUTH_DENIED");
                fflush(stdout);
            }
        } else {
            auth_input_key(&state, event.code, event.value);
        }
        if (!quit) auth_render(screen, &state);
    }
    auth_clear(&state);
    close(keyboard);
    return quit ? 0 : 1;
}

static int self_test(void) {
    struct surface screen;
    memset(&screen, 0, sizeof(screen));
    screen.variable.bits_per_pixel = 32;
    screen.variable.xres = 640;
    screen.variable.yres = 480;
    screen.variable.red.offset = 16;
    screen.variable.red.length = 8;
    screen.variable.green.offset = 8;
    screen.variable.green.length = 8;
    screen.variable.blue.offset = 0;
    screen.variable.blue.length = 8;
    screen.fixed.visual = FB_VISUAL_TRUECOLOR;
    screen.fixed.line_length = 640 * 4;
    screen.fixed.smem_len = 640 * 480 * 4;
    if (!valid_layout(&screen) || rgb(&screen, 255, 0, 0) != 0x00ff0000u) return 1;
    screen.fixed.smem_len--;
    if (valid_layout(&screen)) return 1;
    struct menu_state state = {.selected = 0u, .page = -1, .quit = 0};
    menu_key(&state, KEY_RIGHT);
    if (state.selected != 1u) return 1;
    menu_key(&state, KEY_ENTER);
    if (state.page != 1) return 1;
    menu_key(&state, KEY_ESC);
    if (state.page != -1 || state.quit) return 1;
    menu_key(&state, KEY_LEFT);
    if (state.selected != 0u) return 1;
    menu_key(&state, KEY_ESC);
    if (!state.quit) return 1;
    struct menu_state click_state = {.selected = 0u, .page = -1, .quit = 0};
    menu_click(&click_state, 310u, 250u, 900u, 600u);
    if (click_state.page != 1 || click_state.selected != 1u) return 1;
    menu_click(&click_state, 850u, 20u, 900u, 600u);
    if (click_state.page != -1) return 1;
    struct menu_state demo = {.selected = 3u, .page = -1, .quit = 0};
    menu_key(&demo, KEY_ENTER);
    if (demo.page != 3 || demo.launch) return 1;
    menu_key(&demo, KEY_ENTER);
    if (!demo.launch) return 1;
    demo.launch = 0;
    menu_click(&demo, 60u, 310u, 900u, 600u);
    if (!demo.launch) return 1;
    struct menu_state settings = {.selected = 4u, .page = -1, .quit = 0};
    menu_key(&settings, KEY_ENTER);
    if (settings.page != 4 || settings.high_contrast || settings.cursor_scale) return 1;
    menu_key(&settings, KEY_RIGHT);
    menu_key(&settings, KEY_UP);
    if (!settings.high_contrast || settings.cursor_scale != 1u) return 1;
    menu_click(&settings, 50u, 220u, 900u, 600u);
    menu_click(&settings, 50u, 330u, 900u, 600u);
    if (settings.high_contrast || settings.cursor_scale) return 1;
    struct auth_input auth = {0};
    auth_input_key(&auth, KEY_A, 1);
    auth_input_key(&auth, KEY_1, 1);
    auth_input_key(&auth, KEY_MINUS, 1);
    if (auth.length != 3u || auth.password[0] != 'a' ||
        auth.password[1] != '1' || auth.password[2] != '-') return 1;
    auth_input_key(&auth, KEY_BACKSPACE, 1);
    auth_input_key(&auth, KEY_LEFTSHIFT, 1);
    auth_input_key(&auth, KEY_B, 1);
    auth_input_key(&auth, KEY_LEFTSHIFT, 0);
    if (auth.length != 3u || auth.password[2] != 'B') return 1;
    auth_clear(&auth);
    if (auth.length != 0u || auth.password[0] != 0u || auth.password[2] != 0u) return 1;
    if (glyph_for('4') == NULL || glyph_for('.') == NULL ||
        glyph_for('N') == NULL || glyph_for('@') != NULL) return 1;
    if (absolute_pixel(-20, 0, 100, 900u) != 0u ||
        absolute_pixel(200, 0, 100, 900u) != 899u ||
        absolute_pixel(50, 0, 100, 900u) != 449u) return 1;
    return 0;
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--self-test") == 0) return self_test();
    if (argc != 2 || (strcmp(argv[1], "--probe") != 0 &&
                      strcmp(argv[1], "--demo") != 0 &&
                      strcmp(argv[1], "--menu") != 0 &&
                      strcmp(argv[1], "--login") != 0)) {
        fputs("usage: synveil-display --probe|--demo|--menu|--login|--self-test\n", stderr);
        return 2;
    }
    int fd = open("/dev/fb0", O_RDWR | O_CLOEXEC);
    if (fd < 0) {
        fprintf(stderr, "[synveil-display] /dev/fb0 unavailable: %s\n", strerror(errno));
        return 1;
    }
    struct surface screen;
    memset(&screen, 0, sizeof(screen));
    if (ioctl(fd, FBIOGET_FSCREENINFO, &screen.fixed) == -1 ||
        ioctl(fd, FBIOGET_VSCREENINFO, &screen.variable) == -1 ||
        !valid_layout(&screen)) {
        fputs("[synveil-display] unsupported framebuffer layout\n", stderr);
        close(fd);
        return 1;
    }
    screen.length = screen.fixed.smem_len;
    screen.mapped = mmap(NULL, screen.length, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (screen.mapped == MAP_FAILED) {
        fprintf(stderr, "[synveil-display] mmap failed: %s\n", strerror(errno));
        return 1;
    }
    int result = 0;
    if (strcmp(argv[1], "--demo") == 0) paint(&screen);
    else if (strcmp(argv[1], "--menu") == 0) result = run_menu(&screen);
    else if (strcmp(argv[1], "--login") == 0) result = run_auth_login(&screen);
    else rectangle(&screen, 0, 0, 1, 1, rgb(&screen, 13, 20, 36));
    munmap(screen.mapped, screen.length);
    if (result == 0) puts("SYNVEIL_DISPLAY_READY");
    return result;
}
