# Read-only local observer (developer v1)

`synctl --json observe` queries `veil-core` through its local read-only
Unix socket. This does not authorize process mutation, privileged actions,
AI recommendations or operating-system policy changes.

## Schema: `synveil.observe/v1`

- `cpu.logical_count`: logical CPUs from `/proc/stat`.
- `cpu.total_ticks`, `cpu.idle_ticks`: monotonically cumulative kernel
  counters; `idle_ticks` includes iowait. A single read is **not** an
  instantaneous CPU utilization percentage.
- `memory.total_bytes`, `memory.available_bytes`: Linux MemTotal and
  MemAvailable read from `/proc/meminfo`, converted from kB to bytes with
  overflow checks.
- `uptime_seconds`: kernel time since boot from `/proc/uptime`.
- `load.average_1m`, `load.average_5m`, `load.average_15m`:
  Linux scheduler load averages from `/proc/loadavg`. These are neither
  CPU percentages nor normalized by logical processor count.
- `load.runnable`, `load.total_threads`: scheduler entities from
  the runnable/total field in `/proc/loadavg`. Total refers to threads,
  not distinct processes, and includes the current procfs context.

The core validates required fields, finite nonnegative numeric values,
CPU and memory consistency and runnable/total bounds. Bad or unavailable
required procfs input returns a structured error; it never silently
substitutes fake zero values. The implementation reads kernel files
directly without starting shell commands.

Host tests cover valid parsing and malformed/inconsistent counters. The
native QEMU `observe` guest smoke was extended to require the new load
fields and is pending rerun.

## Deliberately not claimed

CPU utilization sampling requires a time delta; process inventory and
lifecycle tracking, PSI pressure, I/O, networking, resource history and
long-term aggregation are separate roadmap items. The observer does not
yet implement these metrics.

## Linux pressure stall information (PSI)

The `pressure.cpu`, `pressure.memory`, and `pressure.io` objects
report the kernel's `/proc/pressure/{cpu,memory,io}` values when present.
A ready record contains `some` and optional `full` groups with
`avg10_pct`, `avg60_pct`, `avg300_pct` (stall time percentages over
10/60/300 seconds), and `total_stall_us` (cumulative microseconds).

CPU's `full` record is optional; memory and I/O require both records
when PSI is available. Unsupported kernels return `status: unavailable`.
Malformed, oversized, duplicate, or out-of-range PSI records return
`status: invalid`, without inventing zero-pressure measurements or
breaking unrelated CPU/memory observation. No sampling daemon, historical
recording, process control, or inference authorization is introduced.
