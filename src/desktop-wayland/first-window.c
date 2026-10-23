// SPDX-License-Identifier: MPL-2.0
// Real xdg_toplevel shm client; host compositor proof, not native boot.
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <wayland-client.h>
#include "xdg-shell-client.h"

enum { W = 400, H = 240 };
struct app {
    struct wl_display *display;
    struct wl_registry *registry;
    struct wl_compositor *compositor;
    struct wl_shm *shm;
    struct xdg_wm_base *wm;
    struct wl_surface *surface;
    struct xdg_surface *xdg;
    struct xdg_toplevel *top;
    struct wl_buffer *buffer;
    void *pixels;
    int configured;
    int closed;
};

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
    (void)ctx; (void)top; (void)w; (void)h; (void)states;
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
    if (!a->configured) {
        wl_surface_attach(a->surface, a->buffer, 0, 0);
        wl_surface_damage(a->surface, 0, 0, W, H);
        wl_surface_commit(a->surface);
        a->configured = 1;
    }
}
static const struct xdg_surface_listener surface_handlers = {
    .configure = configure_surface
};

static int make_buffer(struct app *a) {
    const size_t size = (size_t)W * H * 4;
    int fd = memfd_create("synveil-first-window", MFD_CLOEXEC);
    if (fd < 0) return -1;
    if (ftruncate(fd, (off_t)size) < 0) { close(fd); return -1; }
    void *data = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (data == MAP_FAILED) { close(fd); return -1; }
    uint32_t *px = data;
    for (int y = 0; y < H; y++)
        for (int x = 0; x < W; x++)
            px[(size_t)y * W + x] = 0xff000000u |
                ((uint32_t)(30 + (45 * x / W)) << 16) |
                ((uint32_t)(45 + (35 * y / H)) << 8) | 0x74;
    struct wl_shm_pool *pool = wl_shm_create_pool(a->shm, fd, (int)size);
    close(fd);
    if (!pool) { munmap(data, size); return -1; }
    a->buffer = wl_shm_pool_create_buffer(pool, 0, W, H, W * 4,
                                          WL_SHM_FORMAT_ARGB8888);
    wl_shm_pool_destroy(pool);
    if (!a->buffer) { munmap(data, size); return -1; }
    a->pixels = data;
    return 0;
}

static void cleanup(struct app *a) {
    if (a->top) xdg_toplevel_destroy(a->top);
    if (a->xdg) xdg_surface_destroy(a->xdg);
    if (a->surface) wl_surface_destroy(a->surface);
    if (a->buffer) wl_buffer_destroy(a->buffer);
    if (a->wm) xdg_wm_base_destroy(a->wm);
    if (a->shm) wl_shm_destroy(a->shm);
    if (a->compositor) wl_compositor_destroy(a->compositor);
    if (a->registry) wl_registry_destroy(a->registry);
    if (a->display) wl_display_disconnect(a->display);
    if (a->pixels) munmap(a->pixels, (size_t)W * H * 4);
}

int main(void) {
    struct app a = {0};
    int success = 0;
    if (geteuid() == 0) { fputs("refusing root window client\n", stderr); return 1; }
    a.display = wl_display_connect(NULL);
    if (!a.display) goto out;
    a.registry = wl_display_get_registry(a.display);
    if (!a.registry) goto out;
    wl_registry_add_listener(a.registry, &registry_handlers, &a);
    if (wl_display_roundtrip(a.display) < 0 ||
        !a.compositor || !a.shm || !a.wm) goto out;
    xdg_wm_base_add_listener(a.wm, &wm_handlers, &a);
    if (make_buffer(&a) < 0) goto out;
    a.surface = wl_compositor_create_surface(a.compositor);
    if (!a.surface) goto out;
    a.xdg = xdg_wm_base_get_xdg_surface(a.wm, a.surface);
    if (!a.xdg) goto out;
    xdg_surface_add_listener(a.xdg, &surface_handlers, &a);
    a.top = xdg_surface_get_toplevel(a.xdg);
    if (!a.top) goto out;
    xdg_toplevel_add_listener(a.top, &top_handlers, &a);
    xdg_toplevel_set_title(a.top, "Synveil First Wayland Window");
    xdg_toplevel_set_app_id(a.top, "org.synveil.first-window");
    wl_surface_commit(a.surface);
    for (int i = 0; i < 20 && !a.configured && !a.closed; i++)
        if (wl_display_dispatch(a.display) < 0) goto out;
    if (!a.configured || a.closed || wl_display_flush(a.display) < 0) goto out;
    puts("SYNVEIL_XDG_TOPLEVEL_SHM_SUBMITTED");
    fflush(stdout);
    usleep(750000);
    success = 1;
out:
    cleanup(&a);
    return success ? 0 : 1;
}
