// SPDX-License-Identifier: MPL-2.0
// Graphical file access for the authenticated session (owner
// Priority 4): a read-only Wayland window listing the session
// user's Downloads directory and home directory — names and sizes,
// nothing else. It opens no file, executes nothing, deletes nothing;
// its only inputs are the title-bar gestures (move / close) and one
// Refresh button that re-scans the directories. Downloads the
// browser saves into ~/Downloads become visible here without a
// terminal, which is the whole point: find the file graphically,
// then do whatever the owner wants with it elsewhere.
//
// The window writes a line per listed entry to its stdout (the
// session captures it into $HOME/files-window.log). That log is the
// machine-readable evidence of what the window displayed; the
// entries printed are exactly the entries rendered, from the same
// scan, never a separate accounting.
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/input-event-codes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>
#include <wayland-client.h>
#include <ft2build.h>
#include FT_FREETYPE_H
#include "xdg-shell-client.h"

#define FONT_REGULAR "/usr/share/fonts/dejavu/DejaVuSans.ttf"
#define FONT_BOLD "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"

enum { WIN_W = 720, WIN_H = 520, WIN_MIN_W = 560, WIN_MIN_H = 380,
       TITLE_H = 30, MAX_W = 4096, MAX_H = 2160, MAX_BUFFERS = 3,
       MAX_ENTRIES = 200, ROW_H = 22, LIST_TOP = 96, REFRESH_W = 128,
       REFRESH_H = 36 };

/* The window's pixel identity (the QEMU harness locates it by
 * these): a deep teal title bar, the shared paper body, the shared
 * close-box red. Distinct from the update GUI's purple title and
 * from every generation background. */
#define COL_TITLE 0xff1d5c57u
#define COL_CLOSE 0xffa84455u
#define COL_PAPER 0xfff2f0ebu
#define COL_INK   0xff1b2a38u
#define COL_FAINT 0xff5a6a76u
#define COL_RULE  0xffc9c4b8u
#define COL_BTN   0xff2e7d4fu

struct glyph {
    int w, h, left, top, advance;
    unsigned char *px;
};

struct font {
    FT_Face face;
    struct glyph glyphs[95];
    int ascent;
};

static int font_load(FT_Library lib, const char *path, int px, struct font *f) {
    memset(f, 0, sizeof(*f));
    if (FT_New_Face(lib, path, 0, &f->face)) return -1;
    if (FT_Set_Pixel_Sizes(f->face, 0, (FT_UInt)px)) return -1;
    f->ascent = (int)(f->face->size->metrics.ascender >> 6);
    for (int c = 32; c < 127; c++) {
        struct glyph *g = &f->glyphs[c - 32];
        if (FT_Load_Char(f->face, (FT_ULong)c, FT_LOAD_RENDER)) continue;
        FT_GlyphSlot slot = f->face->glyph;
        g->w = (int)slot->bitmap.width;
        g->h = (int)slot->bitmap.rows;
        g->left = slot->bitmap_left;
        g->top = slot->bitmap_top;
        g->advance = (int)(slot->advance.x >> 6);
        if (g->w > 0 && g->h > 0) {
            g->px = malloc((size_t)g->w * (size_t)g->h);
            if (!g->px) return -1;
            for (int y = 0; y < g->h; y++)
                memcpy(g->px + (size_t)y * (size_t)g->w,
                       slot->bitmap.buffer + (size_t)y * (size_t)slot->bitmap.pitch,
                       (size_t)g->w);
        }
    }
    return 0;
}

static int text_width(const struct font *f, const char *text) {
    int width = 0;
    for (const unsigned char *p = (const unsigned char *)text; *p; p++)
        if (*p >= 32 && *p < 127) width += f->glyphs[*p - 32].advance;
    return width;
}

struct entry {
    char name[256];
    long long size;
    int is_dir;
    int scope; /* 0 = Downloads, 1 = home */
};

struct app;
struct buffer {
    struct app *app;
    struct buffer *next;
    struct wl_buffer *object;
    uint32_t *pixels;
    size_t size;
    int w, h;
};

struct app {
    struct wl_display *display;
    struct wl_registry *registry;
    struct wl_compositor *compositor;
    struct wl_shm *shm;
    struct wl_seat *seat;
    struct wl_pointer *pointer;
    struct wl_keyboard *keyboard;
    struct xdg_wm_base *wm;
    struct wl_surface *surface;
    struct xdg_surface *xdg;
    struct xdg_toplevel *top;
    struct buffer *buffers;
    unsigned buffer_count;
    int width, height, pending_w, pending_h, floating_w, floating_h;
    int pointer_x, pointer_y;
    int configured, closed, failed, redraw, maximized;
    struct font font_body, font_bold, font_small;
    struct entry entries[MAX_ENTRIES];
    int entry_count;
    char downloads_path[512];
    int ready_printed;
};

static int render(struct app *a);

static void discard_buffer(struct buffer *b) {
    struct app *a = b->app;
    struct buffer **link = &a->buffers;
    while (*link && *link != b) link = &(*link)->next;
    if (*link) { *link = b->next; a->buffer_count--; }
    wl_buffer_destroy(b->object);
    munmap(b->pixels, b->size);
    free(b);
}

static void released(void *ctx, struct wl_buffer *object) {
    (void)object;
    struct buffer *b = ctx;
    struct app *a = b->app;
    discard_buffer(b);
    if (a->redraw && render(a) < 0) a->failed = 1;
}

static const struct wl_buffer_listener buffer_handlers = { .release = released };

static void log_name_escaped(const char *name) {
    for (const unsigned char *p = (const unsigned char *)name; *p; p++) {
        if (*p >= 33 && *p < 127 && *p != '\\') putchar(*p);
        else printf("\\x%02x", *p);
    }
}

static int scan_dir(struct app *a, const char *path, int scope) {
    DIR *dir = opendir(path);
    if (!dir) return 0;
    int added = 0;
    struct dirent *de;
    while ((de = readdir(dir)) != NULL) {
        if (de->d_name[0] == '.') continue;
        if (a->entry_count >= MAX_ENTRIES) break;
        char full[768];
        if (snprintf(full, sizeof(full), "%s/%s", path, de->d_name)
                >= (int)sizeof(full)) continue;
        struct stat st;
        if (stat(full, &st) != 0) continue;
        struct entry *e = &a->entries[a->entry_count];
        size_t len = strlen(de->d_name);
        if (len >= sizeof(e->name)) len = sizeof(e->name) - 1;
        memcpy(e->name, de->d_name, len);
        e->name[len] = '\0';
        e->size = (long long)st.st_size;
        e->is_dir = S_ISDIR(st.st_mode) ? 1 : 0;
        e->scope = scope;
        a->entry_count++;
        added++;
    }
    closedir(dir);
    return added;
}

static int entry_cmp(const void *lhs, const void *rhs) {
    const struct entry *a = lhs, *b = rhs;
    if (a->scope != b->scope) return a->scope - b->scope;
    if (a->is_dir != b->is_dir) return b->is_dir - a->is_dir;
    return strcmp(a->name, b->name);
}

static void scan(struct app *a, int refresh) {
    a->entry_count = 0;
    const char *home = getenv("HOME");
    if (!home) home = "/home/synveil";
    snprintf(a->downloads_path, sizeof(a->downloads_path), "%s/Downloads", home);
    scan_dir(a, a->downloads_path, 0);
    scan_dir(a, home, 1);
    qsort(a->entries, (size_t)a->entry_count, sizeof(struct entry), entry_cmp);
    printf("SYNVEIL_FILES_%s dir=%s entries=%d\n",
           refresh ? "REFRESHED" : "READY", a->downloads_path, a->entry_count);
    for (int i = 0; i < a->entry_count; i++) {
        struct entry *e = &a->entries[i];
        printf("SYNVEIL_FILES_ENTRY scope=%s name=",
               e->scope == 0 ? "downloads" : "home");
        log_name_escaped(e->name);
        printf(" size=%lld dir=%d\n", e->size, e->is_dir);
    }
    fflush(stdout);
}

static void fill_rect(struct app *a, uint32_t *px, int x0, int y0, int x1, int y1,
                      uint32_t color) {
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > a->width) x1 = a->width;
    if (y1 > a->height) y1 = a->height;
    for (int y = y0; y < y1; y++)
        for (int x = x0; x < x1; x++)
            px[(size_t)y * (size_t)a->width + (size_t)x] = color;
}

static uint32_t blend(uint32_t bg, uint32_t fg, unsigned cover) {
    unsigned r = (((fg >> 16) & 0xff) * cover + ((bg >> 16) & 0xff) * (255 - cover)) / 255;
    unsigned g = (((fg >> 8) & 0xff) * cover + ((bg >> 8) & 0xff) * (255 - cover)) / 255;
    unsigned b = ((fg & 0xff) * cover + (bg & 0xff) * (255 - cover)) / 255;
    return 0xff000000u | (r << 16) | (g << 8) | b;
}

static void draw_text(struct app *a, uint32_t *px, const struct font *f,
                      int x, int baseline, const char *text, uint32_t color,
                      int max_x) {
    int cx = x;
    for (const unsigned char *p = (const unsigned char *)text; *p; p++) {
        unsigned char c = (*p >= 32 && *p < 127) ? *p : (unsigned char)'?';
        const struct glyph *g = &f->glyphs[c - 32];
        int gx = cx + g->left;
        int gy = baseline - g->top;
        for (int y = 0; y < g->h; y++) {
            int yy = gy + y;
            if (yy < 0 || yy >= a->height) continue;
            for (int xx = 0; xx < g->w; xx++) {
                int sx = gx + xx;
                if (sx < x || sx >= max_x || sx >= a->width) continue;
                unsigned cover = g->px[(size_t)y * (size_t)g->w + (size_t)xx];
                if (!cover) continue;
                uint32_t *at = &px[(size_t)yy * (size_t)a->width + (size_t)sx];
                *at = blend(*at, color, cover);
            }
        }
        cx += g->advance;
        if (cx > max_x) break;
    }
}

static void format_size(long long size, int is_dir, char *out, size_t cap) {
    if (is_dir) snprintf(out, cap, "<dir>");
    else if (size < 1024) snprintf(out, cap, "%lld B", size);
    else if (size < 1024 * 1024) snprintf(out, cap, "%.1f KiB", size / 1024.0);
    else snprintf(out, cap, "%.1f MiB", size / (1024.0 * 1024.0));
}

static int render(struct app *a) {
    if (a->buffer_count >= MAX_BUFFERS) return 0;
    const size_t size = (size_t)a->width * (size_t)a->height * 4;
    int fd = memfd_create("synveil-files-window", MFD_CLOEXEC);
    if (fd < 0) return -1;
    if (ftruncate(fd, (off_t)size) < 0) { close(fd); return -1; }
    void *data = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (data == MAP_FAILED) { close(fd); return -1; }
    struct buffer *b = calloc(1, sizeof(*b));
    if (!b) { munmap(data, size); close(fd); return -1; }
    uint32_t *px = data;
    fill_rect(a, px, 0, 0, a->width, a->height, COL_PAPER);
    fill_rect(a, px, 0, 0, a->width, TITLE_H, COL_TITLE);
    fill_rect(a, px, a->width - 28, 0, a->width, TITLE_H, COL_CLOSE);
    draw_text(a, px, &a->font_bold, 12, TITLE_H - 9, "Synveil Files", 0xffffffffu,
              a->width - 40);
    draw_text(a, px, &a->font_small, 12, TITLE_H + 26,
              "Downloads and home — read-only listing. Nothing here opens, "
              "runs or deletes a file.", COL_FAINT, a->width - 12);
    char header[600];
    snprintf(header, sizeof(header), "Downloads  (%s)", a->downloads_path);
    draw_text(a, px, &a->font_bold, 12, TITLE_H + 52, header, COL_INK, a->width - 12);
    fill_rect(a, px, 12, TITLE_H + 60, a->width - 12, TITLE_H + 61, COL_RULE);

    int rows = (a->height - LIST_TOP - 64) / ROW_H;
    if (rows < 1) rows = 1;
    int y = LIST_TOP;
    int shown_scope = 0;
    int row = 0;
    for (int i = 0; i < a->entry_count && row < rows; i++) {
        struct entry *e = &a->entries[i];
        if (e->scope != shown_scope) {
            shown_scope = e->scope;
            if (row > 0) y += 6;
            draw_text(a, px, &a->font_bold, 12, y + 14, "Home", COL_INK,
                      a->width - 12);
            y += ROW_H;
            row++;
            if (row >= rows) break;
        }
        char line[320];
        char sz[32];
        format_size(e->size, e->is_dir, sz, sizeof(sz));
        snprintf(line, sizeof(line), "%s%s", e->name, e->is_dir ? "/" : "");
        draw_text(a, px, &a->font_body, 20, y + 15, line, COL_INK,
                  a->width - 120);
        int sw = text_width(&a->font_body, sz);
        draw_text(a, px, &a->font_body, a->width - 20 - sw, y + 15, sz, COL_FAINT,
                  a->width - 12);
        y += ROW_H;
        row++;
    }
    if (a->entry_count == 0)
        draw_text(a, px, &a->font_body, 20, LIST_TOP + 15,
                  "(no files yet — downloads will appear here)", COL_FAINT,
                  a->width - 12);

    int by = a->height - 92 + 18;
    fill_rect(a, px, 16, a->height - 92, 16 + REFRESH_W, a->height - 92 + REFRESH_H,
              COL_BTN);
    int tw = text_width(&a->font_bold, "Refresh");
    draw_text(a, px, &a->font_bold, 16 + (REFRESH_W - tw) / 2, by + 24, "Refresh",
              0xffffffffu, 16 + REFRESH_W);

    struct wl_shm_pool *pool = wl_shm_create_pool(a->shm, fd, (int)size);
    close(fd);
    if (!pool) { munmap(data, size); free(b); return -1; }
    b->object = wl_shm_pool_create_buffer(pool, 0, a->width, a->height,
                                          a->width * 4, WL_SHM_FORMAT_ARGB8888);
    wl_shm_pool_destroy(pool);
    if (!b->object) { munmap(data, size); free(b); return -1; }
    b->app = a; b->pixels = data; b->size = size; b->w = a->width; b->h = a->height;
    b->next = a->buffers; a->buffers = b; a->buffer_count++;
    wl_buffer_add_listener(b->object, &buffer_handlers, b);
    wl_surface_attach(a->surface, b->object, 0, 0);
    wl_surface_damage(a->surface, 0, 0, a->width, a->height);
    wl_surface_commit(a->surface);
    a->redraw = 0;
    a->configured++;
    if (!a->ready_printed) {
        a->ready_printed = 1;
        puts("SYNVEIL_FILES_WINDOW_READY");
        fflush(stdout);
    }
    return 0;
}

static void pointer_enter(void *ctx, struct wl_pointer *pointer, uint32_t serial,
                          struct wl_surface *surface, wl_fixed_t x, wl_fixed_t y) {
    (void)pointer; (void)serial; (void)surface;
    struct app *a = ctx;
    a->pointer_x = wl_fixed_to_int(x); a->pointer_y = wl_fixed_to_int(y);
}
static void pointer_leave(void *ctx, struct wl_pointer *pointer, uint32_t serial,
                          struct wl_surface *surface) {
    (void)ctx; (void)pointer; (void)serial; (void)surface;
}
static void pointer_motion(void *ctx, struct wl_pointer *pointer, uint32_t time,
                           wl_fixed_t x, wl_fixed_t y) {
    (void)pointer; (void)time;
    struct app *a = ctx;
    a->pointer_x = wl_fixed_to_int(x); a->pointer_y = wl_fixed_to_int(y);
}
static void pointer_button(void *ctx, struct wl_pointer *pointer, uint32_t serial,
                           uint32_t time, uint32_t button, uint32_t state) {
    (void)pointer; (void)time;
    struct app *a = ctx;
    if (!a->configured || button != BTN_LEFT ||
        state != WL_POINTER_BUTTON_STATE_PRESSED) return;
    if (a->pointer_y < TITLE_H) {
        if (a->pointer_x >= a->width - 28) a->closed = 1;
        else xdg_toplevel_move(a->top, a->seat, serial);
    } else if (a->pointer_x >= 16 && a->pointer_x < 16 + REFRESH_W &&
               a->pointer_y >= a->height - 92 &&
               a->pointer_y < a->height - 92 + REFRESH_H) {
        scan(a, 1);
        a->redraw = 1;
        if (render(a) < 0) a->failed = 1;
    }
}
static void pointer_axis(void *ctx, struct wl_pointer *pointer, uint32_t time,
                         uint32_t axis, wl_fixed_t value) {
    (void)ctx; (void)pointer; (void)time; (void)axis; (void)value;
}
static const struct wl_pointer_listener pointer_handlers = {
    .enter = pointer_enter, .leave = pointer_leave, .motion = pointer_motion,
    .button = pointer_button, .axis = pointer_axis
};
static void keyboard_keymap(void *ctx, struct wl_keyboard *keyboard,
                            uint32_t format, int fd, uint32_t size) {
    (void)ctx; (void)keyboard; (void)format; (void)size;
    if (fd >= 0) close(fd);
}
static void keyboard_enter(void *ctx, struct wl_keyboard *keyboard, uint32_t serial,
                           struct wl_surface *surface, struct wl_array *keys) {
    (void)ctx; (void)keyboard; (void)serial; (void)surface; (void)keys;
}
static void keyboard_leave(void *ctx, struct wl_keyboard *keyboard, uint32_t serial,
                           struct wl_surface *surface) {
    (void)ctx; (void)keyboard; (void)serial; (void)surface;
}
static void keyboard_key(void *ctx, struct wl_keyboard *keyboard, uint32_t serial,
                         uint32_t time, uint32_t key, uint32_t state) {
    (void)keyboard; (void)serial; (void)time;
    struct app *a = ctx;
    if (key == KEY_ESC && state == WL_KEYBOARD_KEY_STATE_PRESSED) a->closed = 1;
}
static void keyboard_modifiers(void *ctx, struct wl_keyboard *keyboard, uint32_t serial,
                               uint32_t depressed, uint32_t latched, uint32_t locked,
                               uint32_t group) {
    (void)ctx; (void)keyboard; (void)serial; (void)depressed;
    (void)latched; (void)locked; (void)group;
}
static const struct wl_keyboard_listener keyboard_handlers = {
    .keymap = keyboard_keymap, .enter = keyboard_enter, .leave = keyboard_leave,
    .key = keyboard_key, .modifiers = keyboard_modifiers
};
static void capabilities(void *ctx, struct wl_seat *seat, uint32_t caps) {
    struct app *a = ctx;
    if ((caps & WL_SEAT_CAPABILITY_KEYBOARD) && !a->keyboard) {
        a->keyboard = wl_seat_get_keyboard(seat);
        wl_keyboard_add_listener(a->keyboard, &keyboard_handlers, a);
    } else if (!(caps & WL_SEAT_CAPABILITY_KEYBOARD) && a->keyboard) {
        wl_keyboard_destroy(a->keyboard); a->keyboard = NULL;
    }
    if ((caps & WL_SEAT_CAPABILITY_POINTER) && !a->pointer) {
        a->pointer = wl_seat_get_pointer(seat);
        wl_pointer_add_listener(a->pointer, &pointer_handlers, a);
    } else if (!(caps & WL_SEAT_CAPABILITY_POINTER) && a->pointer) {
        wl_pointer_destroy(a->pointer); a->pointer = NULL;
    }
}
static const struct wl_seat_listener seat_handlers = { .capabilities = capabilities };
static void global(void *ctx, struct wl_registry *registry,
                   uint32_t name, const char *interface, uint32_t version) {
    struct app *a = ctx;
    if (!strcmp(interface, wl_compositor_interface.name))
        a->compositor = wl_registry_bind(registry, name, &wl_compositor_interface,
                                          version < 4 ? version : 4);
    else if (!strcmp(interface, wl_shm_interface.name))
        a->shm = wl_registry_bind(registry, name, &wl_shm_interface, 1);
    else if (!strcmp(interface, xdg_wm_base_interface.name))
        a->wm = wl_registry_bind(registry, name, &xdg_wm_base_interface,
                                  version < 3 ? version : 3);
    else if (!strcmp(interface, wl_seat_interface.name) && !a->seat) {
        a->seat = wl_registry_bind(registry, name, &wl_seat_interface, 1);
        wl_seat_add_listener(a->seat, &seat_handlers, a);
    }
}
static void global_removed(void *ctx, struct wl_registry *registry, uint32_t name) {
    (void)ctx; (void)registry; (void)name;
}
static const struct wl_registry_listener registry_handlers = {
    .global = global, .global_remove = global_removed
};
static void ping(void *ctx, struct xdg_wm_base *wm, uint32_t serial) {
    (void)ctx; xdg_wm_base_pong(wm, serial);
}
static const struct xdg_wm_base_listener wm_handlers = { .ping = ping };
static void configure_top(void *ctx, struct xdg_toplevel *top, int32_t w,
                          int32_t h, struct wl_array *states) {
    (void)top;
    struct app *a = ctx;
    a->maximized = 0;
    uint32_t *state;
    wl_array_for_each(state, states)
        if (*state == XDG_TOPLEVEL_STATE_MAXIMIZED) a->maximized = 1;
    if (w < 0 || h < 0 || w > MAX_W || h > MAX_H) { a->failed = 1; return; }
    a->pending_w = w ? w : (!a->maximized && a->floating_w ? a->floating_w :
                            (a->width ? a->width : WIN_W));
    a->pending_h = h ? h : (!a->maximized && a->floating_h ? a->floating_h :
                            (a->height ? a->height : WIN_H));
}
static void close_top(void *ctx, struct xdg_toplevel *top) {
    (void)top; ((struct app *)ctx)->closed = 1;
}
static const struct xdg_toplevel_listener top_handlers = {
    .configure = configure_top, .close = close_top
};
static void configure_surface(void *ctx, struct xdg_surface *xdg, uint32_t serial) {
    struct app *a = ctx;
    xdg_surface_ack_configure(xdg, serial);
    if (a->failed) return;
    a->width = a->pending_w; a->height = a->pending_h;
    if (!a->maximized) { a->floating_w = a->width; a->floating_h = a->height; }
    a->redraw = 1;
    if (render(a) < 0) a->failed = 1;
}
static const struct xdg_surface_listener surface_handlers = {
    .configure = configure_surface
};

static void cleanup(struct app *a) {
    if (a->keyboard) wl_keyboard_destroy(a->keyboard);
    if (a->pointer) wl_pointer_destroy(a->pointer);
    if (a->seat) wl_seat_destroy(a->seat);
    if (a->top) xdg_toplevel_destroy(a->top);
    if (a->xdg) xdg_surface_destroy(a->xdg);
    if (a->surface) wl_surface_destroy(a->surface);
    while (a->buffers) discard_buffer(a->buffers);
    if (a->wm) xdg_wm_base_destroy(a->wm);
    if (a->shm) wl_shm_destroy(a->shm);
    if (a->compositor) wl_compositor_destroy(a->compositor);
    if (a->registry) wl_registry_destroy(a->registry);
    if (a->display) wl_display_disconnect(a->display);
}

int main(void) {
    struct app a;
    memset(&a, 0, sizeof(a));
    a.pending_w = WIN_W;
    a.pending_h = WIN_H;
    int success = 0;
    if (geteuid() == 0) { fputs("refusing root window client\n", stderr); return 1; }
    FT_Library lib;
    if (FT_Init_FreeType(&lib)) { fputs("freetype init failed\n", stderr); return 1; }
    if (font_load(lib, FONT_REGULAR, 15, &a.font_body) ||
        font_load(lib, FONT_BOLD, 15, &a.font_bold) ||
        font_load(lib, FONT_REGULAR, 12, &a.font_small)) {
        fputs("font load failed\n", stderr);
        return 1;
    }
    scan(&a, 0);
    a.display = wl_display_connect(NULL);
    if (!a.display) goto out;
    a.registry = wl_display_get_registry(a.display);
    if (!a.registry) goto out;
    wl_registry_add_listener(a.registry, &registry_handlers, &a);
    if (wl_display_roundtrip(a.display) < 0 ||
        !a.compositor || !a.shm || !a.wm) goto out;
    xdg_wm_base_add_listener(a.wm, &wm_handlers, &a);
    a.surface = wl_compositor_create_surface(a.compositor);
    if (!a.surface) goto out;
    a.xdg = xdg_wm_base_get_xdg_surface(a.wm, a.surface);
    if (!a.xdg) goto out;
    xdg_surface_add_listener(a.xdg, &surface_handlers, &a);
    a.top = xdg_surface_get_toplevel(a.xdg);
    if (!a.top) goto out;
    xdg_toplevel_add_listener(a.top, &top_handlers, &a);
    xdg_toplevel_set_title(a.top, "Synveil Files");
    xdg_toplevel_set_app_id(a.top, "org.synveil.files");
    xdg_toplevel_set_min_size(a.top, WIN_MIN_W, WIN_MIN_H);
    xdg_toplevel_set_max_size(a.top, MAX_W, MAX_H);
    wl_surface_commit(a.surface);
    while (!a.closed && !a.failed) {
        if (wl_display_dispatch(a.display) < 0) goto out;
    }
    if (a.failed || !a.configured) goto out;
    success = 1;
out:
    cleanup(&a);
    return success ? 0 : 1;
}
