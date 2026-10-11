# Observer v1 — Read-only Linux snapshot

Status: **implemented in the Rust host workspace; guest runtime validation pending**

The first observer endpoint is `synctl observe` (or `synctl --json observe`)
over the existing private Unix socket. Request method: `observe`, with an
empty JSON parameter object. Unknown parameters receive `invalid_parameters`.

The result is `synveil.observe/v1`. It is sampled from three kernel interfaces
without spawning shell commands or performing privileged mutations:

- `/proc/meminfo`: `MemTotal` and `MemAvailable`, returned as unsigned bytes.
- `/proc/stat`: cumulative CPU tick totals and idle+iowait ticks, as unsigned
  integers; number of logical `cpuN` lines.
- `/proc/uptime`: seconds since boot.

Example result (illustrative values, not actual hardware measurements):

```json
{
  "schema": "synveil.observe/v1",
  "memory": {
    "total_bytes": 33554432,
    "available_bytes": 12582912
  },
  "cpu": {
    "logical_count": 2,
    "total_ticks": 1145,
    "idle_ticks": 1010
  },
  "uptime_seconds": 12.5
}
```

CPU counters are cumulative; they are **not instantaneous CPU utilization**.
A consumer may compute deltas between snapshots but must also handle counter
reset/reboot and CPU hotplug. Absent, malformed, inconsistent or overflowing
kernel values cause a typed `internal_failure` response rather than invented
measurements.

The kernel remains the source of truth; the service does not persist metrics,
infer user intent, call an AI model, or change policy. Additional metrics,
normalized event streams, historical aggregation, privacy boundaries, and
guest smoke evidence remain Phase 3 work.
