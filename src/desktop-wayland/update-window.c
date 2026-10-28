// SPDX-License-Identifier: MPL-2.0
// Graphical update controls for the A/B updater (owner Priority 2,
// docs/AB_UPDATER.md). An ordinary unprivileged Wayland client in
// the authenticated session: it renders the update state reported
// by the veil-ab agent and sends ONLY the agent's typed actions
// (status / stage / confirm / rollback / reboot, schema
// synveil.ab-agent/v1) over the agent's group-writable socket.
// It holds no privilege of its own, runs no command, opens no shell,
// and cannot ask the agent for anything that is not one of those
// five actions: the request line is assembled from a fixed action
// word and nothing the user (or a pixel) can type exists here —
// the window takes pointer input on five fixed buttons only.
//
// All state shown comes from the agent's status reply, which the
// agent derives from the on-disk control record at request time;
// this client caches nothing across refreshes except the last
// action's outcome, and says so on its face (the message line).
// Errors from the agent are displayed verbatim, never dressed up:
// a refused stage stays a refusal on screen.
//
// Text is rendered with the image's own FreeType and the DejaVu
// faces the sealed image already carries for Weston; rendering is
// into wl_shm buffers exactly like synveil-first-window.
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/input-event-codes.h>
#include <math.h>
#include <poll.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>
#include <wayland-client.h>
#include <ft2build.h>
#include FT_FREETYPE_H
#include "xdg-shell-client.h"

#define AGENT_SOCKET "/run/synveil-ab/agent.sock"
#define AGENT_SCHEMA "synveil.ab-agent/v1"
#define FONT_REGULAR "/usr/share/fonts/dejavu/DejaVuSans.ttf"
#define FONT_BOLD "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"

enum { WIN_W = 720, WIN_H = 520, WIN_MIN_W = 640, WIN_MIN_H = 480,
       TITLE_H = 30, BANNER_H = 52, MAX_W = 4096, MAX_H = 2160,
       MAX_BUFFERS = 3 };

/* ------------------------------------------------------------------ */
/* Minimal strict JSON reader. The agent's replies are the only     */
/* input; bounds are hard so a corrupt reply fails the parse instead */
/* of the client. Objects, arrays, strings, numbers, literals.      */

enum { J_NULL, J_BOOL, J_NUM, J_STR, J_ARR, J_OBJ };
struct jval {
    int type;
    double num;
    char *str;
    struct jval **items;
    char **keys;
    size_t len, cap;
};

struct jparser { const char *p, *end; int depth, nodes; };

static void jfree(struct jval *v) {
    if (!v) return;
    for (size_t i = 0; i < v->len; i++) { jfree(v->items[i]); free(v->keys ? v->keys[i] : NULL); }
    free(v->items); free(v->keys); free(v->str); free(v);
}

static struct jval *jnew(struct jparser *jp, int type) {
    if (++jp->nodes > 4096) return NULL;
    struct jval *v = calloc(1, sizeof(*v));
    if (v) v->type = type;
    return v;
}

static int jpush(struct jval *v, char *key, struct jval *item) {
    if (v->len == v->cap) {
        size_t cap = v->cap ? v->cap * 2 : 8;
        struct jval **items = realloc(v->items, cap * sizeof(*items));
        if (!items) return -1;
        v->items = items;
        if (v->type == J_OBJ) {
            char **keys = realloc(v->keys, cap * sizeof(*keys));
            if (!keys) return -1;
            v->keys = keys;
        }
        v->cap = cap;
    }
    if (v->type == J_OBJ) v->keys[v->len] = key;
    v->items[v->len++] = item;
    return 0;
}

static void jskip(struct jparser *jp) {
    while (jp->p < jp->end && (*jp->p == ' ' || *jp->p == '\t' ||
                              *jp->p == '\n' || *jp->p == '\r')) jp->p++;
}

static struct jval *jparse_value(struct jparser *jp);

static char *jparse_string_raw(struct jparser *jp) {
    if (jp->p >= jp->end || *jp->p != '"') return NULL;
    jp->p++;
    size_t cap = 32, len = 0;
    char *out = malloc(cap);
    if (!out) return NULL;
    while (jp->p < jp->end && *jp->p != '"') {
        unsigned char c = (unsigned char)*jp->p++;
        if (c == '\\') {
            if (jp->p >= jp->end) { free(out); return NULL; }
            char esc = *jp->p++;
            switch (esc) {
            case 'n': c = '\n'; break;
            case 't': c = '\t'; break;
            case 'r': c = '\r'; break;
            case 'b': c = '\b'; break;
            case 'f': c = '\f'; break;
            case '/': c = '/'; break;
            case '\\': c = '\\'; break;
            case '"': c = '"'; break;
            case 'u': {
                /* Only ASCII \u00XX is materialised; anything else
                 * becomes '?' — agent strings are identifiers and
                 * bounded error texts, never user content. */
                unsigned code = 0;
                for (int i = 0; i < 4; i++) {
                    if (jp->p >= jp->end) { free(out); return NULL; }
                    char h = *jp->p++;
                    code <<= 4;
                    if (h >= '0' && h <= '9') code |= (unsigned)(h - '0');
                    else if (h >= 'a' && h <= 'f') code |= (unsigned)(h - 'a' + 10);
                    else if (h >= 'A' && h <= 'F') code |= (unsigned)(h - 'A' + 10);
                    else { free(out); return NULL; }
                }
                c = code < 0x80 ? (unsigned char)code : (unsigned char)'?';
                break;
            }
            default: free(out); return NULL;
            }
        }
        if (len + 1 >= cap) {
            cap *= 2;
            char *grown = realloc(out, cap);
            if (!grown) { free(out); return NULL; }
            out = grown;
        }
        out[len++] = (char)c;
    }
    if (jp->p >= jp->end) { free(out); return NULL; }
    jp->p++;
    out[len] = 0;
    return out;
}

static struct jval *jparse_value(struct jparser *jp) {
    if (++jp->depth > 12) return NULL;
    jskip(jp);
    struct jval *v = NULL;
    if (jp->p >= jp->end) goto out;
    if (*jp->p == '{') {
        jp->p++;
        v = jnew(jp, J_OBJ);
        if (!v) goto out;
        jskip(jp);
        if (jp->p < jp->end && *jp->p == '}') { jp->p++; goto done; }
        for (;;) {
            jskip(jp);
            char *key = jparse_string_raw(jp);
            if (!key) { jfree(v); v = NULL; goto out; }
            jskip(jp);
            if (jp->p >= jp->end || *jp->p != ':') { free(key); jfree(v); v = NULL; goto out; }
            jp->p++;
            struct jval *item = jparse_value(jp);
            if (!item || jpush(v, key, item) < 0) {
                free(key); jfree(item); jfree(v); v = NULL; goto out;
            }
            jskip(jp);
            if (jp->p < jp->end && *jp->p == ',') { jp->p++; continue; }
            if (jp->p < jp->end && *jp->p == '}') { jp->p++; break; }
            jfree(v); v = NULL; goto out;
        }
    } else if (*jp->p == '[') {
        jp->p++;
        v = jnew(jp, J_ARR);
        if (!v) goto out;
        jskip(jp);
        if (jp->p < jp->end && *jp->p == ']') { jp->p++; goto done; }
        for (;;) {
            struct jval *item = jparse_value(jp);
            if (!item || jpush(v, NULL, item) < 0) {
                jfree(item); jfree(v); v = NULL; goto out;
            }
            jskip(jp);
            if (jp->p < jp->end && *jp->p == ',') { jp->p++; continue; }
            if (jp->p < jp->end && *jp->p == ']') { jp->p++; break; }
            jfree(v); v = NULL; goto out;
        }
    } else if (*jp->p == '"') {
        char *text = jparse_string_raw(jp);
        if (!text) goto out;
        v = jnew(jp, J_STR);
        if (!v) { free(text); goto out; }
        v->str = text;
    } else if (jp->p + 4 <= jp->end && !strncmp(jp->p, "true", 4)) {
        jp->p += 4; v = jnew(jp, J_BOOL); if (v) v->num = 1;
    } else if (jp->p + 5 <= jp->end && !strncmp(jp->p, "false", 5)) {
        jp->p += 5; v = jnew(jp, J_BOOL);
    } else if (jp->p + 4 <= jp->end && !strncmp(jp->p, "null", 4)) {
        jp->p += 4; v = jnew(jp, J_NULL);
    } else {
        char *stop = NULL;
        double number = strtod(jp->p, &stop);
        if (stop == jp->p) goto out;
        jp->p = stop;
        v = jnew(jp, J_NUM);
        if (v) v->num = number;
    }
done:
out:
    jp->depth--;
    return v;
}

static struct jval *jparse(const char *text, size_t len) {
    struct jparser jp = { text, text + len, 0, 0 };
    struct jval *v = jparse_value(&jp);
    if (!v) return NULL;
    jskip(&jp);
    if (jp.p != jp.end) { jfree(v); return NULL; }
    return v;
}

static const struct jval *jget(const struct jval *v, const char *key) {
    if (!v || v->type != J_OBJ) return NULL;
    for (size_t i = 0; i < v->len; i++)
        if (!strcmp(v->keys[i], key)) return v->items[i];
    return NULL;
}

static const char *jstr(const struct jval *v, const char *key) {
    const struct jval *item = jget(v, key);
    return item && item->type == J_STR ? item->str : NULL;
}

static long long jnum(const struct jval *v, const char *key, long long fallback) {
    const struct jval *item = jget(v, key);
    return item && item->type == J_NUM ? (long long)item->num : fallback;
}

static int jbool(const struct jval *v, const char *key, int fallback) {
    const struct jval *item = jget(v, key);
    return item && item->type == J_BOOL ? item->num != 0 : fallback;
}

/* ------------------------------------------------------------------ */
/* Status snapshot: everything the window shows, filled only from an */
/* agent status reply.                                               */

struct snapshot {
    int have;              /* a status reply was applied */
    int agent_down;        /* last request could not reach the agent */
    int enabled;
    int have_running;
    long long running_version;
    char running_gen[65];
    int have_confirmed;
    char confirmed_slot[4];
    long long confirmed_version;
    char confirmed_gen[65];
    int have_trial;
    char trial_slot[4];
    long long trial_version;
    char trial_gen[65];
    long long trial_attempts;
    int trial_awaiting;
    char booted_slot[16];
    int have_retained;     /* the non-confirmed slot holds a generation */
    char health_phase[12];
    char health_check[32];
    int avail_attached;
    int avail_valid;
    long long avail_version;
    char avail_gen[65];
    char avail_error[192];
    int publisher_dev;
    char publisher_key[48];
    int hist_count;
    char hist[6][160];
};

static void copy_str(char *dst, size_t cap, const char *src) {
    if (!src) src = "";
    size_t len = strlen(src);
    if (len >= cap) len = cap - 1;
    memcpy(dst, src, len);
    dst[len] = 0;
}

static void fill_snapshot(const struct jval *result, struct snapshot *s) {
    memset(s, 0, sizeof(*s));
    s->have = 1;
    s->enabled = jbool(result, "enabled", 0);
    const struct jval *running = jget(result, "running");
    if (running && running->type == J_OBJ) {
        s->have_running = 1;
        s->running_version = jnum(running, "version", 0);
        copy_str(s->running_gen, sizeof(s->running_gen), jstr(running, "generation_id"));
    }
    const struct jval *confirmed = jget(result, "confirmed");
    if (confirmed && confirmed->type == J_OBJ) {
        s->have_confirmed = 1;
        copy_str(s->confirmed_slot, sizeof(s->confirmed_slot), jstr(confirmed, "slot"));
        s->confirmed_version = jnum(confirmed, "version", 0);
        copy_str(s->confirmed_gen, sizeof(s->confirmed_gen), jstr(confirmed, "generation_id"));
    }
    const struct jval *trial = jget(result, "trial");
    if (trial && trial->type == J_OBJ) {
        s->have_trial = 1;
        copy_str(s->trial_slot, sizeof(s->trial_slot), jstr(trial, "slot"));
        s->trial_version = jnum(trial, "version", 0);
        copy_str(s->trial_gen, sizeof(s->trial_gen), jstr(trial, "generation_id"));
        s->trial_attempts = jnum(trial, "attempts", 0);
        s->trial_awaiting = jbool(trial, "awaiting_health", 0);
    }
    copy_str(s->booted_slot, sizeof(s->booted_slot), jstr(result, "booted_slot"));
    const struct jval *slots = jget(result, "slots");
    if (slots && slots->type == J_OBJ && s->have_confirmed) {
        const char *other = !strcmp(s->confirmed_slot, "A") ? "B" : "A";
        const struct jval *retained = jget(slots, other);
        s->have_retained = retained && retained->type == J_OBJ;
    }
    const struct jval *health = jget(result, "health");
    if (health && health->type == J_OBJ) {
        copy_str(s->health_phase, sizeof(s->health_phase), jstr(health, "phase"));
        copy_str(s->health_check, sizeof(s->health_check), jstr(health, "check"));
    } else {
        copy_str(s->health_phase, sizeof(s->health_phase), "none");
    }
    const struct jval *avail = jget(result, "available");
    if (avail && avail->type == J_OBJ) {
        s->avail_attached = jbool(avail, "attached", 0);
        const char *error = jstr(avail, "error");
        if (error) {
            copy_str(s->avail_error, sizeof(s->avail_error), error);
        } else if (s->avail_attached) {
            s->avail_valid = 1;
            s->avail_version = jnum(avail, "version", 0);
            copy_str(s->avail_gen, sizeof(s->avail_gen), jstr(avail, "generation_id"));
        }
    }
    const struct jval *publisher = jget(result, "publisher");
    if (publisher && publisher->type == J_OBJ) {
        s->publisher_dev = jbool(publisher, "development", 0);
        copy_str(s->publisher_key, sizeof(s->publisher_key), jstr(publisher, "key_id"));
    }
    const struct jval *history = jget(result, "history");
    if (history && history->type == J_ARR) {
        size_t start = history->len > 6 ? history->len - 6 : 0;
        for (size_t i = start; i < history->len; i++) {
            const struct jval *entry = history->items[i];
            long long at = jnum(entry, "at", 0);
            time_t when = (time_t)at;
            struct tm tmv;
            char clock[16] = "--:--:--";
            if (gmtime_r(&when, &tmv))
                strftime(clock, sizeof(clock), "%H:%M:%S", &tmv);
            snprintf(s->hist[s->hist_count], sizeof(s->hist[0]),
                     "%s  %s  v%lld  slot %s  %s", clock,
                     jstr(entry, "action") ?: "?",
                     jnum(entry, "version", 0),
                     jstr(entry, "slot") ?: "?",
                     jstr(entry, "result") ?: "?");
            s->hist_count++;
        }
    }
}

/* ------------------------------------------------------------------ */
/* Agent requests run on one worker thread so a slow stage (the      */
/* agent hashes and copies a whole generation) never stalls the      */
/* Wayland event loop. One request is ever in flight; the window is  */
/* honest about that (buttons disable and the banner says Working).  */

struct job {
    int used;
    char action[12];          /* status|stage|confirm|rollback|reboot */
    int ok;
    int agent_down;
    char error[256];
    char result_reason[48];   /* the agent's result.reason, when any */
    struct snapshot snap;     /* filled for a successful status */
    int have_snap;
};

struct worker {
    pthread_t thread;
    pthread_mutex_t lock;
    pthread_cond_t cond;
    struct job pending;       /* main -> worker */
    int has_pending;
    struct job done;          /* worker -> main */
    int has_done;
    int wake_fd;              /* pipe write end */
};

static int agent_request(const char *action, struct job *job) {
    job->ok = 0;
    job->agent_down = 0;
    job->have_snap = 0;
    job->error[0] = 0;
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) { job->agent_down = 1; return -1; }
    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    copy_str(addr.sun_path, sizeof(addr.sun_path), AGENT_SOCKET);
    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        job->agent_down = 1;
        close(fd);
        return -1;
    }
    struct timeval tv = { 600, 0 };
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    char request[160];
    int n = snprintf(request, sizeof(request),
                     "{\"schema\":\"" AGENT_SCHEMA "\",\"id\":\"update-gui\","
                     "\"action\":\"%s\"}\n", action);
    if (n < 0 || write(fd, request, (size_t)n) != n) {
        job->agent_down = 1;
        close(fd);
        return -1;
    }
    /* One newline-delimited reply frame, hard-capped. */
    static char reply[128 * 1024];
    size_t len = 0;
    int complete = 0;
    while (len < sizeof(reply) - 1) {
        ssize_t got = read(fd, reply + len, sizeof(reply) - 1 - len);
        if (got < 0) {
            if (errno == EINTR) continue;
            break;
        }
        if (got == 0) break;
        len += (size_t)got;
        if (memchr(reply, '\n', len)) { complete = 1; break; }
    }
    close(fd);
    if (!complete) {
        copy_str(job->error, sizeof(job->error),
                 "the update agent did not answer");
        return -1;
    }
    reply[len] = 0;
    struct jval *root = jparse(reply, len);
    if (!root) {
        copy_str(job->error, sizeof(job->error),
                 "the update agent's reply did not parse");
        return -1;
    }
    const char *schema = jstr(root, "schema");
    if (!schema || strcmp(schema, AGENT_SCHEMA)) {
        copy_str(job->error, sizeof(job->error),
                 "the update agent spoke an unknown protocol");
        jfree(root);
        return -1;
    }
    if (jbool(root, "ok", 0)) {
        job->ok = 1;
        const struct jval *result_any = jget(root, "result");
        if (result_any && result_any->type == J_OBJ)
            copy_str(job->result_reason, sizeof(job->result_reason),
                     jstr(result_any, "reason"));
        if (!strcmp(action, "status")) {
            const struct jval *result = jget(root, "result");
            if (result && result->type == J_OBJ) {
                fill_snapshot(result, &job->snap);
                job->have_snap = 1;
            } else {
                job->ok = 0;
                copy_str(job->error, sizeof(job->error),
                         "the update agent's status was empty");
            }
        }
    } else {
        copy_str(job->error, sizeof(job->error),
                 jstr(root, "error") ?: "the update agent refused");
    }
    jfree(root);
    return job->ok ? 0 : -1;
}

static void *worker_main(void *ctx) {
    struct worker *w = ctx;
    for (;;) {
        pthread_mutex_lock(&w->lock);
        while (!w->has_pending) pthread_cond_wait(&w->cond, &w->lock);
        struct job job = w->pending;
        w->has_pending = 0;
        pthread_mutex_unlock(&w->lock);
        agent_request(job.action, &job);
        pthread_mutex_lock(&w->lock);
        w->done = job;
        w->has_done = 1;
        pthread_mutex_unlock(&w->lock);
        char byte = 'x';
        ssize_t unused = write(w->wake_fd, &byte, 1);
        (void)unused;
    }
    return NULL;
}

static void worker_submit(struct worker *w, const char *action) {
    pthread_mutex_lock(&w->lock);
    if (w->has_pending && !strcmp(action, "status") &&
        strcmp(w->pending.action, "status") != 0) {
        /* An owner action is already queued; a background refresh
         * never displaces it. */
        pthread_mutex_unlock(&w->lock);
        return;
    }
    memset(&w->pending, 0, sizeof(w->pending));
    w->pending.used = 1;
    copy_str(w->pending.action, sizeof(w->pending.action), action);
    w->has_pending = 1;
    pthread_cond_signal(&w->cond);
    pthread_mutex_unlock(&w->lock);
}

/* ------------------------------------------------------------------ */
/* FreeType text: the image's DejaVu faces, ASCII glyphs pre-rendered */
/* per face/size at startup (the agent's strings are identifiers,    */
/* bounded error texts and history lines — plain ASCII throughout).  */

struct glyph {
    int w, h, left, top, advance;
    unsigned char *px;        /* 8-bit coverage, w*h */
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

/* ------------------------------------------------------------------ */
/* The window itself. Buffers follow synveil-first-window's pool: a  */
/* busy buffer belongs to the compositor until wl_buffer.release.    */

struct app;
struct buffer {
    struct app *app;
    struct buffer *next;
    struct wl_buffer *object;
    uint32_t *pixels;
    size_t size;
    int w, h;
};

enum banner_kind { B_NONE, B_NORMAL, B_TRIAL, B_ERROR, B_DISABLED, B_DOWN };
enum { BTN_STAGE, BTN_RESTART, BTN_CONFIRM, BTN_ROLLBACK, BTN_REFRESH, BTN_COUNT };

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
    struct font font_body, font_bold, font_small, font_banner;
    struct worker worker;
    int worker_wake_fd;
    struct snapshot snap;
    int busy;
    char busy_action[12];
    int ready_printed;
    char message[256];
    int message_error;
    long long last_refresh_ms;
    int btn_enabled[BTN_COUNT];
    int btn_pressed;
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

static uint32_t blend(uint32_t bg, uint32_t fg, unsigned cover) {
    unsigned r = (((fg >> 16) & 0xff) * cover + ((bg >> 16) & 0xff) * (255 - cover)) / 255;
    unsigned g = (((fg >> 8) & 0xff) * cover + ((bg >> 8) & 0xff) * (255 - cover)) / 255;
    unsigned b = ((fg & 0xff) * cover + (bg & 0xff) * (255 - cover)) / 255;
    return 0xff000000u | (r << 16) | (g << 8) | b;
}

static void fill_rect(struct app *a, uint32_t *px, int x, int y, int w, int h,
                      uint32_t color) {
    for (int row = y; row < y + h; row++) {
        if (row < 0 || row >= a->height) continue;
        for (int col = x; col < x + w; col++) {
            if (col < 0 || col >= a->width) continue;
            px[(size_t)row * (size_t)a->width + (size_t)col] = color;
        }
    }
}

static void draw_text(struct app *a, uint32_t *px, const struct font *f,
                      int x, int baseline, const char *text, uint32_t color) {
    int cx = x;
    for (const unsigned char *p = (const unsigned char *)text; *p; p++) {
        if (*p < 32 || *p >= 127) { cx += f->glyphs[0].advance; continue; }
        const struct glyph *g = &f->glyphs[*p - 32];
        for (int gy = 0; gy < g->h; gy++) {
            int row = baseline - g->top + gy;
            if (row < 0 || row >= a->height) continue;
            for (int gx = 0; gx < g->w; gx++) {
                int col = cx + g->left + gx;
                if (col < 0 || col >= a->width) continue;
                unsigned cover = g->px[(size_t)gy * (size_t)g->w + (size_t)gx];
                if (cover) {
                    size_t at = (size_t)row * (size_t)a->width + (size_t)col;
                    px[at] = blend(px[at], color, cover);
                }
            }
        }
        cx += g->advance;
    }
}

/* Draw text truncated with ".." to fit max_px. */
static void draw_text_fit(struct app *a, uint32_t *px, const struct font *f,
                          int x, int baseline, const char *text, int max_px,
                          uint32_t color) {
    if (text_width(f, text) <= max_px) {
        draw_text(a, px, f, x, baseline, text, color);
        return;
    }
    char buf[512];
    copy_str(buf, sizeof(buf), text);
    while (buf[0] && text_width(f, buf) + text_width(f, "..") > max_px)
        buf[strlen(buf) - 1] = 0;
    strncat(buf, "..", sizeof(buf) - strlen(buf) - 1);
    draw_text(a, px, f, x, baseline, buf, color);
}

/* ------------------------------------------------------------------ */
/* Display state derived from the snapshot — and only from it.       */

static const char *const BTN_LABELS[BTN_COUNT] = {
    "Stage update", "Restart", "Confirm update", "Roll back", "Refresh"
};
static const char *const BTN_ACTIONS[BTN_COUNT] = {
    "stage", "reboot", "confirm", "rollback", "status"
};

static int running_is_trial(const struct snapshot *s) {
    return s->have_trial && s->have_running &&
           !strcmp(s->running_gen, s->trial_gen);
}

static enum banner_kind banner_of(const struct app *a) {
    const struct snapshot *s = &a->snap;
    if (!s->have) return B_DOWN;
    if (s->agent_down) return B_ERROR;
    if (!s->enabled) return B_DISABLED;
    if (a->message_error) return B_ERROR;
    if (s->have_trial) return B_TRIAL;
    return B_NORMAL;
}

static const char *banner_name(enum banner_kind kind) {
    switch (kind) {
    case B_NORMAL: return "normal";
    case B_TRIAL: return "trial";
    case B_ERROR: return "error";
    case B_DISABLED: return "disabled";
    case B_DOWN: return "down";
    default: return "none";
    }
}

static uint32_t banner_color(enum banner_kind kind) {
    switch (kind) {
    case B_NORMAL: return 0xff2e7d4fu;
    case B_TRIAL: return 0xffb07a1eu;
    case B_ERROR: return 0xffa82a2au;
    case B_DISABLED: return 0xff6e6e6eu;
    case B_DOWN: return 0xff4a4a55u;
    default: return 0xff333333u;
    }
}

static void compute_buttons(struct app *a) {
    const struct snapshot *s = &a->snap;
    int base = s->have && s->enabled && !a->busy;
    a->btn_enabled[BTN_STAGE] = base && !s->have_trial && s->avail_attached;
    a->btn_enabled[BTN_RESTART] = base && s->have_trial && !running_is_trial(s);
    a->btn_enabled[BTN_CONFIRM] = base && s->have_trial && s->trial_awaiting &&
        running_is_trial(s) && !strcmp(s->health_phase, "passed");
    a->btn_enabled[BTN_ROLLBACK] = base && (s->have_trial || s->have_retained);
    a->btn_enabled[BTN_REFRESH] = !a->busy && s->have;
}

static void button_rect(const struct app *a, int i, int *x, int *y, int *w, int *h) {
    *x = 16 + i * 138;
    *y = a->height - 92;
    *w = 128;
    *h = 36;
}

static void banner_text(const struct app *a, char *head, size_t head_cap,
                        char *sub, size_t sub_cap) {
    const struct snapshot *s = &a->snap;
    if (a->busy) {
        snprintf(head, head_cap, "Working: %s", a->busy_action);
        copy_str(sub, sub_cap, "The agent is carrying out the action; staging can take a while.");
        return;
    }
    switch (banner_of(a)) {
    case B_DOWN:
        copy_str(head, head_cap, "Waiting for the update agent");
        copy_str(sub, sub_cap, "No state has been loaded yet.");
        break;
    case B_DISABLED:
        copy_str(head, head_cap, "System updates are not enabled");
        copy_str(sub, sub_cap, "This system has no A/B update layout.");
        break;
    case B_ERROR:
        if (s->agent_down) {
            copy_str(head, head_cap, "Update agent stopped answering");
            copy_str(sub, sub_cap, "Showing the last state the agent reported.");
        } else {
            copy_str(head, head_cap, "The last update action was refused");
            copy_str(sub, sub_cap, "The agent's reason is in the message line below.");
        }
        break;
    case B_TRIAL:
        if (running_is_trial(s)) {
            if (!strcmp(s->health_phase, "passed")) {
                copy_str(head, head_cap, "Update is healthy — your decision");
                snprintf(sub, sub_cap, "Confirm to keep v%lld, or roll back to v%lld.",
                         s->trial_version, s->confirmed_version);
            } else if (!strcmp(s->health_phase, "failed")) {
                copy_str(head, head_cap, "Update failed its health checks");
                copy_str(sub, sub_cap, "It cannot be confirmed; the system falls back on its own.");
            } else {
                copy_str(head, head_cap, "Trying the update");
                copy_str(sub, sub_cap, "Health checks are running for this trial.");
            }
        } else {
            copy_str(head, head_cap, "Update staged — restart to try it");
            snprintf(sub, sub_cap, "v%lld waits in slot %s with %lld attempts.",
                     s->trial_version, s->trial_slot, s->trial_attempts);
        }
        break;
    case B_NORMAL:
    default:
        copy_str(head, head_cap, "System is on the confirmed generation");
        if (s->avail_valid)
            snprintf(sub, sub_cap, "An update to v%lld is on the attached media.",
                     s->avail_version);
        else
            copy_str(sub, sub_cap, "No update is staged.");
        break;
    }
}

static int render(struct app *a) {
    if (a->buffer_count == MAX_BUFFERS) return 0;
    const size_t size = (size_t)a->width * (size_t)a->height * 4;
    int fd = memfd_create("synveil-update-gui", MFD_CLOEXEC);
    if (fd < 0) return -1;
    if (ftruncate(fd, (off_t)size) < 0) { close(fd); return -1; }
    void *data = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (data == MAP_FAILED) { close(fd); return -1; }
    struct buffer *b = calloc(1, sizeof(*b));
    if (!b) { munmap(data, size); close(fd); return -1; }
    uint32_t *px = data;
    const uint32_t bg = 0xfff2f0ebu, dark = 0xff1b1b1bu, dim = 0xff5a564eu;
    for (size_t i = 0; i < (size_t)a->width * (size_t)a->height; i++) px[i] = bg;

    /* Title bar (the window's pixel identity: this purple). */
    fill_rect(a, px, 0, 0, a->width, TITLE_H, 0xff5b2e6eu);
    fill_rect(a, px, a->width - 28, 0, 28, TITLE_H, 0xffa84455u);
    draw_text(a, px, &a->font_bold, 12, 21, "Synveil System Updates", 0xffffffffu);

    /* State banner. */
    enum banner_kind kind = banner_of(a);
    fill_rect(a, px, 0, TITLE_H, a->width, BANNER_H, banner_color(kind));
    char head[96], sub[128];
    banner_text(a, head, sizeof(head), sub, sizeof(sub));
    draw_text(a, px, &a->font_banner, 16, TITLE_H + 24, head, 0xffffffffu);
    draw_text(a, px, &a->font_small, 16, TITLE_H + 44, sub, 0xffffffffu);

    const struct snapshot *s = &a->snap;
    char line[256];
    int row = TITLE_H + BANNER_H + 30;
    const int label_x = 16, value_x = 176;

    draw_text(a, px, &a->font_bold, label_x, row, "Running", dark);
    if (s->have_running)
        snprintf(line, sizeof(line), "v%lld  —  slot %s  —  %s",
                 s->running_version, s->booted_slot, s->running_gen);
    else copy_str(line, sizeof(line), s->have ? "—" : "waiting for the agent");
    draw_text_fit(a, px, &a->font_body, value_x, row, line, a->width - value_x - 12, dark);
    row += 24;

    draw_text(a, px, &a->font_bold, label_x, row, "Confirmed", dark);
    if (s->have_confirmed)
        snprintf(line, sizeof(line), "v%lld  —  slot %s  —  %s",
                 s->confirmed_version, s->confirmed_slot, s->confirmed_gen);
    else copy_str(line, sizeof(line), "—");
    draw_text_fit(a, px, &a->font_body, value_x, row, line, a->width - value_x - 12, dark);
    row += 24;

    draw_text(a, px, &a->font_bold, label_x, row, "Trial", dark);
    if (s->have_trial) {
        if (running_is_trial(s))
            snprintf(line, sizeof(line), "v%lld  —  slot %s  —  running now, %lld attempts left",
                     s->trial_version, s->trial_slot, s->trial_attempts);
        else
            snprintf(line, sizeof(line), "v%lld  —  slot %s  —  staged, %lld attempts",
                     s->trial_version, s->trial_slot, s->trial_attempts);
    } else copy_str(line, sizeof(line), "none");
    draw_text_fit(a, px, &a->font_body, value_x, row, line, a->width - value_x - 12, dark);
    row += 24;

    draw_text(a, px, &a->font_bold, label_x, row, "Health", dark);
    if (!s->have_trial || !s->trial_awaiting) copy_str(line, sizeof(line), "—");
    else if (!strcmp(s->health_phase, "passed"))
        copy_str(line, sizeof(line), "passed — ready for your confirmation");
    else if (!strcmp(s->health_phase, "checking"))
        copy_str(line, sizeof(line), "checks running");
    else if (!strcmp(s->health_phase, "failed"))
        snprintf(line, sizeof(line), "FAILED (check: %s)",
                 s->health_check[0] ? s->health_check : "unknown");
    else copy_str(line, sizeof(line), "not assessed yet");
    draw_text_fit(a, px, &a->font_body, value_x, row, line, a->width - value_x - 12, dark);
    row += 24;

    draw_text(a, px, &a->font_bold, label_x, row, "Update media", dark);
    uint32_t media_color = dark;
    if (!s->avail_attached) copy_str(line, sizeof(line), "no release media attached");
    else if (s->avail_valid)
        snprintf(line, sizeof(line), "v%lld available (%s) — signature valid",
                 s->avail_version, s->avail_gen);
    else {
        snprintf(line, sizeof(line), "media attached — %s", s->avail_error);
        media_color = 0xffa82a2au;
    }
    draw_text_fit(a, px, &a->font_body, value_x, row, line, a->width - value_x - 12, media_color);
    row += 24;

    draw_text(a, px, &a->font_bold, label_x, row, "Publisher", dark);
    if (s->publisher_key[0])
        snprintf(line, sizeof(line), "%s%s", s->publisher_key,
                 s->publisher_dev ? "  —  DEVELOPMENT KEY" : "");
    else copy_str(line, sizeof(line), "—");
    draw_text_fit(a, px, &a->font_bold, value_x, row, line, a->width - value_x - 12,
                  s->publisher_dev ? 0xffa82a2au : dark);
    row += 34;

    draw_text(a, px, &a->font_bold, label_x, row, "Update history", dark);
    row += 20;
    if (!s->hist_count)
        draw_text(a, px, &a->font_small, label_x, row, "no update actions recorded yet", dim);
    for (int i = 0; i < s->hist_count; i++) {
        draw_text_fit(a, px, &a->font_small, label_x, row, s->hist[i],
                      a->width - label_x - 12, dim);
        row += 18;
    }

    /* Buttons. */
    compute_buttons(a);
    for (int i = 0; i < BTN_COUNT; i++) {
        int x, y, w, h;
        button_rect(a, i, &x, &y, &w, &h);
        fill_rect(a, px, x, y, w, h, a->btn_enabled[i] ? 0xff33566eu : 0xffc9c4bbu);
        const char *label = BTN_LABELS[i];
        int tw = text_width(&a->font_body, label);
        draw_text(a, px, &a->font_body, x + (w - tw) / 2, y + 24, label,
                  a->btn_enabled[i] ? 0xffffffffu : 0xff7a766eu);
    }

    /* Message line: the last action's outcome, the agent's words on
     * a refusal, never a paraphrase that softens it. */
    draw_text_fit(a, px, &a->font_small, 16, a->height - 16, a->message,
                  a->width - 32, a->message_error ? 0xffa82a2au : dim);

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
    fflush(stdout);
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

static void activate_button(struct app *a, int i);

static void pointer_button(void *ctx, struct wl_pointer *pointer, uint32_t serial,
                           uint32_t time, uint32_t button, uint32_t state) {
    (void)pointer; (void)time;
    struct app *a = ctx;
    if (!a->configured || button != BTN_LEFT ||
        state != WL_POINTER_BUTTON_STATE_PRESSED) return;
    if (a->pointer_y < TITLE_H) {
        if (a->pointer_x >= a->width - 28) a->closed = 1;
        else xdg_toplevel_move(a->top, a->seat, serial);
        return;
    }
    if (a->pointer_x >= a->width - 20 && a->pointer_y >= a->height - 20) {
        xdg_toplevel_resize(a->top, a->seat, serial,
                            XDG_TOPLEVEL_RESIZE_EDGE_BOTTOM_RIGHT);
        return;
    }
    compute_buttons(a);
    for (int i = 0; i < BTN_COUNT; i++) {
        int x, y, w, h;
        button_rect(a, i, &x, &y, &w, &h);
        if (a->pointer_x >= x && a->pointer_x < x + w &&
            a->pointer_y >= y && a->pointer_y < y + h) {
            if (a->btn_enabled[i]) activate_button(a, i);
            return;
        }
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
    /* Same zero-dimension rule as synveil-first-window: a (0,0)
     * configure wants the last committed floating size. */
    a->pending_w = w ? w : (!a->maximized && a->floating_w ? a->floating_w :
                            (a->width ? a->width : WIN_W));
    a->pending_h = h ? h : (!a->maximized && a->floating_h ? a->floating_h :
                            (a->height ? a->height : WIN_H));
    if (a->pending_w < WIN_MIN_W) a->pending_w = WIN_MIN_W;
    if (a->pending_h < WIN_MIN_H) a->pending_h = WIN_MIN_H;
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

/* ------------------------------------------------------------------ */
/* Button activation and worker-result handling. A failed action     */
/* leaves its message on screen until a later successful action; a  */
/* status refresh never overwrites an action's message.              */

static long long now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

/* Marker lines are the session log's evidence; make them durable
 * promptly, not whenever the page cache happens to flush — the
 * session can end by power loss (and the acceptance quits QEMU). */
static void emit_durable(void) {
    fflush(stdout);
    fsync(fileno(stdout));
}

static void print_status_marker(const struct app *a) {
    const struct snapshot *s = &a->snap;
    printf("SYNVEIL_UPDATE_GUI_STATUS running=%lld confirmed=%lld trial=%lld "
           "health=%s banner=%s\n",
           s->have_running ? s->running_version : -1,
           s->have_confirmed ? s->confirmed_version : -1,
           s->have_trial ? s->trial_version : -1,
           s->health_phase[0] ? s->health_phase : "none",
           banner_name(banner_of(a)));
    emit_durable();
}

static void activate_button(struct app *a, int i) {
    const char *action = BTN_ACTIONS[i];
    printf("SYNVEIL_UPDATE_GUI_CLICK action=%s\n", action);
    emit_durable();
    a->busy = 1;
    copy_str(a->busy_action, sizeof(a->busy_action), action);
    a->redraw = 1;
    worker_submit(&a->worker, action);
}

static void apply_job(struct app *a, const struct job *job) {
    const char *action = job->action;
    char one_line[256];
    copy_str(one_line, sizeof(one_line), job->error);
    for (char *p = one_line; *p; p++) if (*p == '\n') *p = ' ';
    printf("SYNVEIL_UPDATE_GUI_ACTION action=%s ok=%d%s%s\n", action,
           job->ok ? 1 : 0, job->error[0] ? " error=" : "",
           job->error[0] ? one_line : "");
    emit_durable();
    if (!strcmp(action, "status")) {
        /* A refresh must not clear the busy state of an action the
         * owner started; jobs complete in submission order, so a
         * status finishing while an action is queued or running
         * belongs to an earlier refresh. */
        if (a->busy && !strcmp(a->busy_action, "status")) a->busy = 0;
        a->last_refresh_ms = now_ms();
        if (job->ok && job->have_snap) {
            a->snap = job->snap;
            a->snap.agent_down = 0;
            if (!a->ready_printed) {
                a->ready_printed = 1;
                puts("SYNVEIL_UPDATE_GUI_READY");
                emit_durable();
            }
            print_status_marker(a);
        } else {
            if (job->agent_down) a->snap.agent_down = 1;
            copy_str(a->message, sizeof(a->message),
                     job->agent_down
                         ? "The update agent is not answering."
                         : job->error);
            a->message_error = 1;
        }
        a->redraw = 1;
        return;
    }
    a->busy = 0;
    if (job->ok) {
        if (!strcmp(action, "stage"))
            copy_str(a->message, sizeof(a->message),
                     "Update staged. Restart to try it.");
        else if (!strcmp(action, "confirm"))
            copy_str(a->message, sizeof(a->message),
                     "Update confirmed: this generation is now the confirmed system.");
        else if (!strcmp(action, "rollback")) {
            if (!strcmp(job->result_reason, "pending-trial-discarded"))
                copy_str(a->message, sizeof(a->message),
                         "Pending update discarded; the confirmed generation stays in charge.");
            else if (!strcmp(job->result_reason, "owner-rollback-trial"))
                copy_str(a->message, sizeof(a->message),
                         "Rollback armed. Restart to run the retained generation.");
            else
                copy_str(a->message, sizeof(a->message), "Rollback recorded.");
        } else if (!strcmp(action, "reboot"))
            copy_str(a->message, sizeof(a->message), "Restarting…");
        a->message_error = 0;
    } else {
        copy_str(a->message, sizeof(a->message), job->error);
        a->message_error = 1;
    }
    a->redraw = 1;
    if (strcmp(action, "reboot") == 0) {
        /* The guest is about to reboot; nothing to refresh. */
        return;
    }
    /* Every action's aftermath is read back from the agent, so the
     * window shows the record, not the hope. The refresh runs
     * without the busy state: the state rows will update under the
     * action's own message. */
    worker_submit(&a->worker, "status");
}

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

int main(int argc, char **argv) {
    (void)argc; (void)argv;
    struct app a = { .pending_w = WIN_W, .pending_h = WIN_H };
    int success = 0;
    int pipefd[2] = { -1, -1 };
    if (geteuid() == 0) { fputs("refusing root window client\n", stderr); return 1; }
    FT_Library ft;
    if (FT_Init_FreeType(&ft)) { fputs("freetype unavailable\n", stderr); return 1; }
    if (font_load(ft, FONT_REGULAR, 13, &a.font_body) < 0 ||
        font_load(ft, FONT_BOLD, 13, &a.font_bold) < 0 ||
        font_load(ft, FONT_REGULAR, 11, &a.font_small) < 0 ||
        font_load(ft, FONT_BOLD, 15, &a.font_banner) < 0) {
        puts("SYNVEIL_UPDATE_GUI_FONT_FAILED");
        fflush(stdout);
        return 1;
    }
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
    xdg_toplevel_set_title(a.top, "Synveil System Updates");
    xdg_toplevel_set_app_id(a.top, "org.synveil.update-gui");
    xdg_toplevel_set_min_size(a.top, WIN_MIN_W, WIN_MIN_H);
    xdg_toplevel_set_max_size(a.top, WIN_W, WIN_H);
    wl_surface_commit(a.surface);

    if (pipe(pipefd) < 0) goto out;
    int flags = fcntl(pipefd[0], F_GETFL);
    fcntl(pipefd[0], F_SETFL, flags | O_NONBLOCK);
    a.worker_wake_fd = pipefd[0];
    pthread_mutex_init(&a.worker.lock, NULL);
    pthread_cond_init(&a.worker.cond, NULL);
    a.worker.wake_fd = pipefd[1];
    if (pthread_create(&a.worker.thread, NULL, worker_main, &a.worker) != 0) goto out;

    copy_str(a.message, sizeof(a.message), "Contacting the update agent…");
    a.busy = 1;
    copy_str(a.busy_action, sizeof(a.busy_action), "status");
    worker_submit(&a.worker, "status");
    a.last_refresh_ms = now_ms();

    int wl_fd = wl_display_get_fd(a.display);
    while (!a.closed && !a.failed) {
        while (wl_display_prepare_read(a.display) != 0) {
            if (wl_display_dispatch_pending(a.display) < 0) goto out;
        }
        wl_display_flush(a.display);
        struct pollfd fds[2] = {
            { wl_fd, POLLIN, 0 },
            { a.worker_wake_fd, POLLIN, 0 },
        };
        int ready = poll(fds, 2, 200);
        if (ready > 0 && (fds[0].revents & POLLIN)) {
            if (wl_display_read_events(a.display) < 0) goto out;
        } else {
            wl_display_cancel_read(a.display);
        }
        if (wl_display_dispatch_pending(a.display) < 0) goto out;
        if (ready > 0 && (fds[1].revents & POLLIN)) {
            char drain[64];
            while (read(a.worker_wake_fd, drain, sizeof(drain)) > 0) {}
            pthread_mutex_lock(&a.worker.lock);
            int has = a.worker.has_done;
            struct job job = a.worker.done;
            a.worker.has_done = 0;
            pthread_mutex_unlock(&a.worker.lock);
            if (has) apply_job(&a, &job);
        }
        if (!a.busy && now_ms() - a.last_refresh_ms >= 3000) {
            /* Background refresh: submitted without the busy state,
             * so it never disables the buttons under the pointer. */
            worker_submit(&a.worker, "status");
            a.last_refresh_ms = now_ms();
        }
        if (a.redraw && render(&a) < 0) { a.failed = 1; break; }
    }
    if (!a.failed && a.configured) success = 1;
out:
    cleanup(&a);
    return success ? 0 : 1;
}
