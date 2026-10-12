// SPDX-License-Identifier: MPL-2.0
#define _GNU_SOURCE
#include "network_diag.h"
#include <arpa/inet.h>
#include <net/if.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <unistd.h>

static void address_and_link(const char *name, struct synveil_network_diag *out) {
    int fd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (fd < 0) return;
    struct ifreq request = {0};
    size_t length = strlen(name);
    if (length == 0 || length >= IFNAMSIZ) goto done;
    memcpy(request.ifr_name, name, length + 1u);
    if (ioctl(fd, SIOCGIFFLAGS, &request) != 0) goto done;
    out->present = 1;
    out->link_up = (request.ifr_flags & (IFF_UP | IFF_RUNNING)) ==
                   (IFF_UP | IFF_RUNNING);
    memset(&request, 0, sizeof(request));
    memcpy(request.ifr_name, name, length + 1u);
    if (ioctl(fd, SIOCGIFADDR, &request) == 0) {
        const struct sockaddr_in *address =
            (const struct sockaddr_in *)&request.ifr_addr;
        if (address->sin_family == AF_INET)
            (void)inet_ntop(AF_INET, &address->sin_addr,
                            out->address, sizeof(out->address));
    }
done:
    close(fd);
}

static void default_gateway(const char *name, const char *routes,
                            char result[INET_ADDRSTRLEN]) {
    FILE *stream = fopen(routes, "r");
    if (!stream) return;
    char line[256];
    // The kernel exposes one line per route; cap work on malformed procfs.
    for (unsigned i = 0; i < 128u && fgets(line, sizeof(line), stream); ++i) {
        char device[IFNAMSIZ];
        unsigned long destination = 1, gateway = 0, flags = 0;
        if (sscanf(line, "%15s %lx %lx %lx", device, &destination,
                   &gateway, &flags) != 4 || strcmp(device, name) != 0 ||
            destination != 0ul || (flags & 3ul) != 3ul ||
            gateway == 0ul || gateway > UINT32_MAX) continue;
        // /proc/net/route encodes IPv4 gateways as little-endian hex.
        unsigned char bytes[4] = {
            (unsigned char)(gateway & 255ul),
            (unsigned char)((gateway >> 8u) & 255ul),
            (unsigned char)((gateway >> 16u) & 255ul),
            (unsigned char)((gateway >> 24u) & 255ul)
        };
        (void)snprintf(result, INET_ADDRSTRLEN, "%u.%u.%u.%u",
                       bytes[0], bytes[1], bytes[2], bytes[3]);
        break;
    }
    fclose(stream);
}

static void first_dns(const char *resolv, char result[INET_ADDRSTRLEN]) {
    FILE *stream = fopen(resolv, "r");
    if (!stream) return;
    char line[256];
    for (unsigned i = 0; i < 128u && fgets(line, sizeof(line), stream); ++i) {
        char keyword[32], value[48];
        if (sscanf(line, "%31s %47s", keyword, value) != 2 ||
            strcmp(keyword, "nameserver") != 0) continue;
        struct in_addr parsed;
        if (inet_pton(AF_INET, value, &parsed) == 1) {
            (void)inet_ntop(AF_INET, &parsed, result, INET_ADDRSTRLEN);
            break;
        }
    }
    fclose(stream);
}

void synveil_network_snapshot(const char *interface, const char *routes,
                              const char *resolv, struct synveil_network_diag *out) {
    if (!out) return;
    memset(out, 0, sizeof(*out));
    if (!interface || !routes || !resolv || strlen(interface) >= IFNAMSIZ)
        return;
    address_and_link(interface, out);
    if (!out->present) return;
    default_gateway(interface, routes, out->gateway);
    first_dns(resolv, out->dns);
}
