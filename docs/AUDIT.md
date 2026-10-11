# Core audit record v1

Core startup, fatal errors, socket events, requests/rejections, connection
errors, shutdown, and supervisor lifecycle events share a compact JSON-line
format. The schema is `synveil.audit/v1`.

| Field | Meaning |
| --- | --- |
| schema | Audit schema identifier |
| component | `veil-core` |
| pid | Emitting process ID; supervisor and worker differ |
| sequence | Increasing counter within one emitting process, starting at 1 |
| unix_time_ms | Wall-clock milliseconds since Unix epoch, or null if unavailable/out of range |
| version | Committed Synveil version |
| git_revision | Build revision, or null when not supplied |
| event | Fixed event name supplied by core code |
| fields | Event-specific structured fields |

Sequence numbers restart in each new process. PID and wall-clock time are not
a globally unique record ID; time can move backwards. The sequence captures
record construction order within a process, not cross-process ordering or
concurrent sink-write order. JSON serialization escapes embedded newlines and
carriage returns so a field cannot create an additional console record.

The default sink is stderr. When the opt-in QEMU ext4 state disk is mounted
at `/var`, bootstrap enables `SYNVEIL_AUDIT_PATH`. The supervisor and worker
then also append each event to `/var/log/synveil/core-audit.jsonl`, creating it
mode 0600 in a root-owned mode 0700 directory and calling `sync_data()` after
each event. Sink failures are reported on the console; they do not disable
recovery. Sequence numbers remain process-local, not a cross-process ordering.

This is a developer-grade persistent event log, not a complete durable-audit
subsystem. It lacks rotation, tamper evidence, integrity checks, a history API,
and disk-full policy. The durable-audit roadmap item remains open. Future
privileged actions also require typed cause, parameters, result, authorization,
and rollback evidence.

Unit tests cover identity, sequence ordering, unavailable clocks, and escaped
fields. A host integration test starts the actual core with a temporary journal,
obtains status, and checks that the on-disk event stream matches console
records with owner-only file permissions. Guest reboot testing must separately
prove that the journal is written to persistent storage.
