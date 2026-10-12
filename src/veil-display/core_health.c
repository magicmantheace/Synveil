// SPDX-License-Identifier: MPL-2.0
#define _POSIX_C_SOURCE 200809L
#define _GNU_SOURCE
#include "core_health.h"
#include <errno.h>
#include <poll.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#define CORE_HEALTH_DEADLINE_MS 350
#define CORE_HEALTH_REPLY_MAX 2048u

static int64_t monotonic_millis(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return -1;
    return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static int ready_fd(int fd, short events, int64_t deadline) {
    for (;;) {
        int64_t now = monotonic_millis();
        if (now < 0 || now >= deadline) return 0;
        int64_t left = deadline - now;
        int timeout = left > INT32_MAX ? INT32_MAX : (int)left;
        struct pollfd wait = {.fd = fd, .events = events};
        int result = poll(&wait, 1, timeout);
        if (result < 0 && errno == EINTR) continue;
        if (result <= 0 || (wait.revents & (POLLERR | POLLNVAL))) return 0;
        return (wait.revents & events) != 0;
    }
}

static int valid_ready_response(char *response, size_t length) {
    static const char prefix[] =
        "{\"schema\":\"synveil.core/v1\",\"id\":\"synveil-desktop\","
        "\"ok\":true,\"result\":{";
    if (length <= sizeof(prefix) ||
        memcmp(response, prefix, sizeof(prefix) - 1u) != 0 ||
        memchr(response, '\0', length) != NULL ||
        response[length - 1u] != '\n') return 0;
    response[length - 1u] = '\0';
    // The pinned Rust CoreStatus serializer may order result keys
    // differently. All three exact values must be present in the result.
    return strstr(response, "\"service\":\"veil-core\"") != NULL &&
           strstr(response, "\"protocol\":\"synveil.core/v1\"") != NULL &&
           strstr(response, "\"state\":\"ready\"") != NULL;
}

int synveil_core_health(const char *socket_path) {
    if (socket_path == NULL) return 0;
    size_t name_length = strlen(socket_path);
    struct sockaddr_un address = {.sun_family = AF_UNIX};
    if (name_length == 0 || name_length >= sizeof(address.sun_path)) return 0;
    memcpy(address.sun_path, socket_path, name_length + 1u);
    int64_t now = monotonic_millis();
    if (now < 0 || now > INT64_MAX - CORE_HEALTH_DEADLINE_MS) return 0;
    int64_t deadline = now + CORE_HEALTH_DEADLINE_MS;
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (fd < 0) return 0;
    int good = 0;
    int connected = connect(fd, (struct sockaddr *)&address, sizeof(address));
    if (connected < 0) {
        if (errno != EINPROGRESS || !ready_fd(fd, POLLOUT, deadline)) goto done;
        int error = 0;
        socklen_t error_length = sizeof(error);
        if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &error_length) != 0 ||
            error != 0 || error_length != sizeof(error)) goto done;
    }
    static const char request[] =
        "{\"schema\":\"synveil.core/v1\",\"id\":\"synveil-desktop\","
        "\"method\":\"status\",\"params\":{}}\n";
    size_t sent = 0;
    while (sent < sizeof(request) - 1u) {
        if (!ready_fd(fd, POLLOUT, deadline)) goto done;
        ssize_t n = send(fd, request + sent, sizeof(request) - 1u - sent,
                         MSG_DONTWAIT | MSG_NOSIGNAL);
        if (n < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK))
            continue;
        if (n <= 0) goto done;
        sent += (size_t)n;
    }
    char response[CORE_HEALTH_REPLY_MAX + 1u];
    size_t used = 0;
    while (used < CORE_HEALTH_REPLY_MAX) {
        if (!ready_fd(fd, POLLIN, deadline)) goto done;
        ssize_t n = recv(fd, response + used, CORE_HEALTH_REPLY_MAX - used, 0);
        if (n < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK))
            continue;
        if (n <= 0) goto done;
        used += (size_t)n;
        char *newline = memchr(response, '\n', used);
        if (newline != NULL) {
            if ((size_t)(newline - response) != used - 1u) goto done;
            good = valid_ready_response(response, used);
            goto done;
        }
    }
done:
    close(fd);
    return good;
}
