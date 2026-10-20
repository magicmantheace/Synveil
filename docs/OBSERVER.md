# Read-only local observer (developer v1)

`synctl --json observe` queries `veil-core` through its local read-only
Unix socket. This does not authorize process mutation, privileged actions,
AI recommendations or operating-system policy changes.

## Schema: `synveil.observe/v1`

- `cpu.logical_count`: logical CPUs from `/proc/stat`.
- `cpu.total_ticks`, `cpu.idle_ticks`: cumulative kernel counters from the
  later sample. `total_ticks` excludes `guest` and `guest_nice`, which Linux
  already includes in `user` and `nice`; `idle_ticks` includes iowait.
- `cpu.utilization`: aggregate and per-logical-CPU busy time derived from two `/proc/stat`
  samples separated by at least 100 ms. A ready result contains integer
  `busy_basis_points` from 0 through 10000 and the measured monotonic
  `sample_window_ms`; 2500 means 25.00 percent busy across all logical CPUs.
  Its numerically sorted `logical_cpus` records report the same fixed-point
  measurement for each stable CPU ID and are bounded to 4096 entries. A CPU
  with no counter delta or decreasing iowait is explicitly `unavailable`.
  Zero total delta, a topology change, or an iowait decrease returns
  `{"status":"unavailable"}` without inventing a percentage. A backward
  non-guest counter other than iowait is invalid.
  `synctl` validates the versioned utilization object, strictly increasing CPU
  IDs, per-CPU status and basis-point bounds before accepting a successful
  observe response.
- `cpu.frequency`: kernel-reported current per-logical-CPU frequency from Linux
  cpufreq sysfs, in integer kHz. It is not an independent hardware measurement.
  `scaling_cur_freq` is preferred and `cpuinfo_cur_freq` is the fallback.
  Systems without a cpufreq driver report `status: unavailable`;
  offline, temporarily busy or unsupported logical CPUs make an otherwise
  populated result `partial`. Output is numerically sorted and bounded to 4096
  logical CPUs.
- `memory.total_bytes`, `memory.available_bytes`: Linux MemTotal and
  MemAvailable read from `/proc/meminfo`, converted from kB to bytes with
  overflow checks.
- `thermal`: bounded Linux thermal-zone snapshots. A ready or truncated result
  contains numerically sorted zones with kernel `type` labels and signed
  `temperature_millicelsius` values. Systems without thermal zones report
  `status: unavailable`; unreadable zones are counted and make a populated
  result `partial`, while malformed zone data reports `invalid` without
  discarding the other observation fields.
- `energy`: bounded Linux powercap zones with stable sysfs IDs, bounded names,
  cumulative `energy_uj` and `max_energy_range_uj`. Counters may wrap at the
  reported range and do not represent instantaneous power. Missing or unreadable
  zones are explicit; malformed counters invalidate only this optional object.
- `uptime_seconds`: kernel time since boot from `/proc/uptime`.
- `load.average_1m`, `load.average_5m`, `load.average_15m`:
  Linux scheduler load averages from `/proc/loadavg`. These are neither
  CPU percentages nor normalized by logical processor count.
- `load.runnable`, `load.total_threads`: scheduler entities from
  the runnable/total field in `/proc/loadavg`. Total refers to threads,
  not distinct processes, and includes the current procfs context.
- `processes`: a point-in-time, PID-sorted `/proc/<pid>/stat` snapshot. Up to
  128 records report PID, parent/process-group/session IDs, kernel state,
  cumulative user/system ticks, start time in boot ticks, virtual bytes,
  resident pages, thread count and nice value. PID plus `start_time_ticks`
  distinguishes PID reuse. Directory scanning is capped at 16384 entries;
  disappearing or unreadable processes are counted instead of failing the
  required CPU/memory snapshot. `synctl` validates status/count relationships,
  strict PID ordering, identifiers, states and counter types before accepting a
  successful response. The observer never opens command lines, environments or
  executable paths. The command name embedded in `stat` is used only to locate
  the following fields and is not returned; it need not be valid UTF-8. If the
  full response would exceed the 64 KiB protocol frame, the core removes
  highest-PID records and increments `omitted_processes` until it fits.

The core validates required fields, finite nonnegative numeric values,
CPU and memory consistency and runnable/total bounds. Bad or unavailable
required procfs input returns a structured error; it never silently
substitutes fake zero values. The implementation reads kernel files
directly without starting shell commands.

Host tests cover valid parsing and malformed/inconsistent counters. The native
QEMU `observe` guest gate passed at exact revision `255101d`, requiring the
versioned schema and live utilization fields from the real command response.
See the [retained evidence](validation/native-255101d/README.md).
The frequency extension passed host and native QEMU validation at exact revision
`376e2b4`; QEMU required the frequency object but exposed no cpufreq driver, so
positive and partial readings remain host-fixture evidence. See the
[frequency evidence](validation/native-376e2b4/README.md).
Per-logical-CPU utilization and the native client's structural checks passed at
exact revision `23f3233`. See the
[per-CPU evidence](validation/native-23f3233/README.md).
The thermal extension passed host and native QEMU validation at exact revision
`b5f5cae`; QEMU required the thermal object but exposed no zones, so positive
and partial readings remain host-fixture evidence. See the
[thermal evidence](validation/native-b5f5cae/README.md).
The bounded process snapshot and the native client's structural checks,
including rejection above the 128-record cap, passed host and native QEMU
validation after composition with energy at exact revision `316f745`. See the
[process evidence](validation/native-316f745/README.md).
The powercap energy extension passed host and native QEMU validation at exact
revision `bca81e5`; QEMU required the energy object but exposed no zones, so
positive hierarchical readings remain host-fixture evidence. See the
[energy evidence](validation/native-bca81e5/README.md).

## Deliberately not claimed

Per-process interval utilization, lifecycle events, instantaneous power,
resource history and long-term aggregation are separate roadmap items. The
process snapshot does not retain state between requests or infer process
purpose, and the energy snapshot does not derive power from counter changes.

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
