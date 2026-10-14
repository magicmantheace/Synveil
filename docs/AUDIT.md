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
mode 0600 in a root-owned mode 0700 directory. Each successful append calls
`sync_all()` on the file and then its parent directory to persist both the
record and the journal's directory entry. Sink failures are reported on the console; they do not disable
recovery. Sequence numbers remain process-local, not a cross-process ordering.

The append path rejects symlinks, non-regular files, hardlinks, files with
unexpected owners or modes, and writable journal directories. Linux
`O_NOFOLLOW | O_NONBLOCK` prevents following substituted final symlinks
or blocking on a FIFO. After opening, the file descriptor is checked before
writing; the state mount helper separately checks privileged /var parents.
Console audit output continues if the disk journal is refused. This does not
make logs tamper-proof against root access or an offline modified image.

Appends take a nonblocking advisory exclusive lock before reading the journal
size or final byte. An existing nonempty journal must end with a newline;
an incomplete tail is preserved verbatim and further disk appends are refused.
There is no automatic truncation or repair. Events must be nonempty single
lines of at most 65,536 bytes including the newline. The journal remains capped
at 4 MiB; capacity, contention and incomplete-tail failures retain console
events and core status availability. Flush failures do not prove persistence;
power-loss durability still requires filesystem/guest failure-injection evidence.

This is a developer-grade persistent event log, not a complete durable-audit
subsystem. It lacks rotation, tamper evidence, integrity checks, a history API,
and disk-full policy. The durable-audit roadmap item remains open. Future
privileged actions also require typed cause, parameters, result, authorization,
and rollback evidence.

The native two-boot QEMU persistence gate now also verifies that the
versioned audit journal is present, root-owned, mode 0600 and singly linked.
That stronger guest gate is pending on the latest native-image workflow.

Unit tests cover identity, sequence, unavailable clocks, escaped fields,
symlink/hardlink rejection, owner-only mode and unsafe-parent refusal. A host integration test starts the actual core with a temporary journal,
obtains status, and checks that the on-disk event stream matches console
records with owner-only file permissions. Guest reboot testing must separately
prove that the journal is written to persistent storage.

Additional host regressions cover record bounds, newline injection, preserved
incomplete tails, lock contention and recovery after lock release. A real-core
integration test requires healthy status and console lifecycle events with a
preexisting incomplete journal, while checking that the damaged bytes remain
unchanged. These checks do not close the durable-audit roadmap item.
