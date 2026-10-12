// SPDX-License-Identifier: MPL-2.0
#ifndef SYNVEIL_CORE_HEALTH_H
#define SYNVEIL_CORE_HEALTH_H

// Read-only status probe. Returns one only for a valid ready response from
// the native core; unavailable, stale, malformed and timed-out peers return zero.
// Never issues commands or changes system policy.
int synveil_core_health(const char *socket_path);

#endif
