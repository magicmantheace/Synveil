/* SPDX-License-Identifier: MPL-2.0 */
/* Fixed guest test traffic only; never installed in the normal rootfs. */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/un.h>
#include <unistd.h>

static int probe(const char *path, const char *request, const char *id,
                 const char *code) {
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    struct sockaddr_un address = { .sun_family = AF_UNIX };
    struct timeval timeout = { .tv_sec = 2 };
    char response[65537];
    size_t used = 0, sent = 0, length = strlen(request);
    if (fd < 0) return -1;
    if (strlen(path) >= sizeof(address.sun_path)) goto fail;
    strcpy(address.sun_path, path);
    if (setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) ||
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout)) ||
        connect(fd, (struct sockaddr *)&address, sizeof(address))) goto fail;
    while (sent < length) {
        ssize_t n = send(fd, request + sent, length - sent, MSG_NOSIGNAL);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) goto fail;
        sent += (size_t)n;
    }
    while (used < sizeof(response) - 1) {
        ssize_t n = recv(fd, response + used, sizeof(response) - 1 - used, 0);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) goto fail;
        used += (size_t)n;
        response[used] = '\0';
        if (memchr(response, '\n', used)) break;
    }
    close(fd);
    /* The Rust encoder emits compact JSON. Match exact fixed field values. */
    if (!memchr(response, '\n', used) ||
        !strstr(response, "\"schema\":\"synveil.core/v1\"") ||
        !strstr(response, id) || !strstr(response, code) ||
        !strstr(response, "\"ok\":false")) return -1;
    return 0;
fail:
    close(fd);
    return -1;
}

int main(int argc, char **argv) {
    const char *path = argc == 2 ? argv[1] : "/run/synveil/veil-core.sock";
    if (argc > 2 ||
        probe(path, "not-json\n", "\"id\":\"\"",
              "\"code\":\"malformed_message\"") ||
        probe(path, "{\"schema\":\"unsupported/v0\",\"id\":\"schema-test\","
                    "\"method\":\"status\",\"params\":{}}\n", "\"id\":\"\"",
              "\"code\":\"unsupported_schema\"") ||
        probe(path, "{\"schema\":\"synveil.core/v1\",\"id\":\"method-test\","
                    "\"method\":\"unregistered_test\",\"params\":{}}\n",
              "\"id\":\"method-test\"", "\"code\":\"unknown_method\"")) {
        fputs("[protocol-probe] expected rejection was not received\n", stderr);
        return EXIT_FAILURE;
    }
    puts("[protocol-probe] malformed/schema/method requests rejected");
    return EXIT_SUCCESS;
}
