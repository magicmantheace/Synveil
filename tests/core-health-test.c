// SPDX-License-Identifier: MPL-2.0
#include "core_health.h"
#include <stdlib.h>

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    return synveil_core_health(argv[1]) == atoi(argv[2]) ? 0 : 1;
}
