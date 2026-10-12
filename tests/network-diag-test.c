// SPDX-License-Identifier: MPL-2.0
#include "network_diag.h"
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc == 3 && strcmp(argv[1], "--route-interface") == 0) {
        char name[16] = {0};
        int found = synveil_network_route_interface(argv[2], name, sizeof(name));
        printf("found=%d\ninterface=%s\n", found, name);
        return 0;
    }
    if (argc != 4) return 2;
    struct synveil_network_diag snapshot;
    if (strcmp(argv[1], "auto") == 0)
        synveil_network_snapshot_auto(argv[2], argv[3], &snapshot);
    else
        synveil_network_snapshot(argv[1], argv[2], argv[3], &snapshot);
    printf("present=%d\nlink=%d\nip=%s\ngateway=%s\ndns=%s\n",
           snapshot.present, snapshot.link_up, snapshot.address,
           snapshot.gateway, snapshot.dns);
    if (strcmp(argv[1], "auto") == 0)
        printf("interface=%s\n", snapshot.interface);
    return 0;
}
