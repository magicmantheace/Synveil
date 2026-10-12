// SPDX-License-Identifier: MPL-2.0
#ifndef SYNVEIL_NETWORK_DIAG_H
#define SYNVEIL_NETWORK_DIAG_H

#include <netinet/in.h>
#include <stddef.h>

// Read-only IPv4 diagnostic for the fixed developer VM network interface.
struct synveil_network_diag {
    int present;
    int link_up;
    char address[INET_ADDRSTRLEN];
    char gateway[INET_ADDRSTRLEN];
    char dns[INET_ADDRSTRLEN];
    char interface[32];
};

int synveil_network_route_interface(const char *routes, char *out, size_t capacity);
void synveil_network_snapshot_auto(const char *routes, const char *resolv,
                                   struct synveil_network_diag *out);

void synveil_network_snapshot(const char *interface, const char *routes,
                              const char *resolv, struct synveil_network_diag *out);

#endif
