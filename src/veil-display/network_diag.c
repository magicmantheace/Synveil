// SPDX-License-Identifier: MPL-2.0
#define _GNU_SOURCE
#include "network_diag.h"
#include <arpa/inet.h>
#include <ctype.h>
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

// Parse a bounded procfs route table for a safe non-loopback default route.
// Hardware presence is checked by the caller, not inferred from procfs alone.
int synveil_network_route_interface(const char *routes, char *out, size_t capacity) {
    if (!out || capacity == 0u) return 0;
    out[0] = '\0';
    if (!routes || capacity < 2u) return 0;
    FILE *stream = fopen(routes, "r");
    if (!stream) return 0;
    char line[256];
    int found = 0;
    for (unsigned i = 0; i < 128u && fgets(line, sizeof(line), stream); ++i) {
        char name[IFNAMSIZ] = {0};
        unsigned long destination = 1ul, gateway = 0ul, flags = 0ul;
        if (sscanf(line, "%15s %lx %lx %lx", name, &destination,
                   &gateway, &flags) != 4 ||
            destination != 0ul || gateway == 0ul || gateway > UINT32_MAX ||
            (flags & 3ul) != 3ul || strcmp(name, "lo") == 0) continue;
        size_t len = strlen(name);
        if (!len || len >= IFNAMSIZ || len >= capacity) continue;
        int safe = 1;
        for (size_t j = 0; j < len; j++) {
            unsigned char x = (unsigned char)name[j];
            if (!isalnum(x) && x != '_' && x != '-' && x != '.') {
                safe = 0;
                break;
            }
        }
        if (!safe) continue;
        memcpy(out, name, len + 1u);
        found = 1;
        break;
    }
    fclose(stream);
    return found;
}

// Diagnose a real network interface rather than assuming QEMU's eth0.
// Prefer a present default-route device, then a running non-loopback link,
// then any installed non-loopback interface (including a disconnected NIC).
void synveil_network_snapshot_auto(const char *routes, const char *resolv,
                                   struct synveil_network_diag *out) {
    if (!out) return;
    memset(out, 0, sizeof(*out));
    if (!routes || !resolv) return;
    char selected[IFNAMSIZ] = {0};
    if (synveil_network_route_interface(routes, selected, sizeof(selected)) &&
        if_nametoindex(selected) != 0u) {
        synveil_network_snapshot(selected, routes, resolv, out);
        return;
    }
    struct if_nameindex *names = if_nameindex();
    if (!names) return;
    char fallback[IFNAMSIZ] = {0};
    for (unsigned i = 0; i < 256u &&
         names[i].if_index != 0u && names[i].if_name != NULL; ++i) {
        const char *name = names[i].if_name;
        size_t len = strlen(name);
        if (!len || len >= sizeof(selected) || strcmp(name, "lo") == 0) continue;
        struct synveil_network_diag candidate = {0};
        address_and_link(name, &candidate);
        if (!candidate.present) continue;
        if (!fallback[0]) memcpy(fallback, name, len + 1u);
        if (candidate.link_up) {
            memcpy(selected, name, len + 1u);
            break;
        }
    }
    if_freenameindex(names);
    if (!selected[0] && fallback[0]) memcpy(selected, fallback, sizeof(selected));
    if (selected[0]) synveil_network_snapshot(selected, routes, resolv, out);
}

void synveil_network_snapshot(const char *interface, const char *routes,
                              const char *resolv, struct synveil_network_diag *out) {
    if (!out) return;
    memset(out, 0, sizeof(*out));
    if (!interface || !routes || !resolv || strlen(interface) >= IFNAMSIZ)
        return;
    address_and_link(interface, out);
    if (!out->present) return;
    (void)snprintf(out->interface, sizeof(out->interface), "%s", interface);
    default_gateway(interface, routes, out->gateway);
    first_dns(resolv, out->dns);
}
