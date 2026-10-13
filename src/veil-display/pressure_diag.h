// SPDX-License-Identifier: MPL-2.0
#ifndef SYNVEIL_PRESSURE_DIAG_H
#define SYNVEIL_PRESSURE_DIAG_H

// Read-only Linux PSI "some" 10-second stall percent, rounded to tenths.
// No user paths, IPC, mutation, inference, or elevated authorization.
int synveil_pressure_parse_some(const char *line, unsigned *tenths);
int synveil_pressure_some_avg10(const char *path, unsigned *tenths);

#endif
