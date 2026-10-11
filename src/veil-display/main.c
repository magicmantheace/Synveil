// SPDX-License-Identifier: MPL-2.0
// A source-built framebuffer diagnostic. Not a desktop or a privileged agent.
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

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

static void heading(const struct surface *screen, const char *message,
                    unsigned x, unsigned y, unsigned scale, uint32_t color) {
    for (const unsigned char *cursor = (const unsigned char *)message; *cursor; cursor++) {
        if (*cursor >= 'A' && *cursor <= 'Z') {
            const uint8_t *glyph = letters[*cursor - 'A'];
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
    return 0;
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--self-test") == 0) return self_test();
    if (argc != 2 || (strcmp(argv[1], "--probe") != 0 &&
                      strcmp(argv[1], "--demo") != 0)) {
        fputs("usage: synveil-display --probe|--demo|--self-test\n", stderr);
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
    if (strcmp(argv[1], "--demo") == 0) paint(&screen);
    else rectangle(&screen, 0, 0, 1, 1, rgb(&screen, 13, 20, 36));
    munmap(screen.mapped, screen.length);
    puts("SYNVEIL_DISPLAY_READY");
    return 0;
}
