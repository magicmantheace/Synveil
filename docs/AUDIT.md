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

The sink is stderr. No on-disk append journal, fsync guarantee, rotation,
tamper evidence, or reboot persistence exists yet. The durable-audit roadmap
item remains open pending writable storage and sink semantics. These are
service/status records; future privileged actions require their own typed
cause, parameters, result, authorization, and rollback evidence.

Unit tests cover identity, sequence ordering, unavailable clocks, and escaped
fields. A host integration test starts the actual core, obtains a status
response, and validates its startup/socket/request/shutdown records. CI must
pass these tests before the new format is treated as validated.
