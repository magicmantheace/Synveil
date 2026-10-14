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
lines of at most 65,536 bytes including the newline. Each active/archive file remains capped
at 4 MiB; capacity, contention and incomplete-tail failures retain console
events and core status availability. Flush failures do not prove persistence;
power-loss durability still requires filesystem/guest failure-injection evidence.

This is a developer-grade persistent event log, not a complete durable-audit
subsystem. It lacks full archive retention policy, tamper evidence, integrity
chains and a history API. The durable-audit roadmap item remains open. Future
privileged actions also require typed cause, parameters, result, authorization,
and rollback evidence.

The native two-boot QEMU persistence gate verifies that the versioned audit
journal is present, root-owned, mode 0600 and singly linked. This gate and
target structural inspection passed at
[262d275](validation/native-262d275/README.md).

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

## Offline structural inspection

`veil-audit check` reads `/var/log/synveil/core-audit.jsonl` directly without
connecting to core IPC. `veil-audit check --path /absolute/journal` selects a
specific file for authorized offline diagnosis. The tool never creates,
repairs, truncates or appends to the journal. It returns a compact JSON
`synveil.audit-inspection/v1` report with byte and record counts and
`authenticated: false`; event contents and supplied paths are not printed.
Missing, inaccessible, busy or malformed journals produce a failure exit code.
An empty existing journal is structurally valid and reports zero records.

The checker opens read-only with Linux no-follow/nonblocking flags, refuses
final-component symlinks, hardlinks, non-regular files and modes other than 0600, and holds a
nonblocking shared advisory lock during inspection. Cooperative writers refuse
to append while inspection holds the lock. Input is capped at 4 MiB and each
record, including its mandatory newline, at 64 KiB. Invalid UTF-8/JSON, blank
records, raw carriage returns, unknown schemas, missing/extra top-level fields,
duplicate object keys at any nesting level, and invalid field types or bounds
are refused. Escaped key aliases are compared after JSON decoding. Fields use the v1 envelope;
process IDs and sequences must be positive and event/build labels bounded.

This is structural inspection, not proof of origin or completeness: it cannot
identify fabricated well-formed events, edits, removed records, reordered
events or lost console-only records. It does not impose global PID/sequence ordering across restarts.
An attacker with write access can still create a structurally valid file.
Cryptographic integrity, retention/rotation and power-loss testing remain open.

The native builder audits the new `veil-audit` ELF and link inputs alongside
core, CLI, enrollment and seat binaries. Rootfs packaging requires its hash-bound
artifact and installs it as `/usr/sbin/veil-audit`. Host inspection and packaging
checks precede an exact-revision native build/guest recovery check. The checker
passed that validation at [262d275](validation/native-262d275/README.md).

The two-independent-boot persistence smoke now requires the packaged checker
to accept the journal on both boots before emitting its success markers.
The two-boot inspector gate passed at
[262d275](validation/native-262d275/README.md); later rollover/failure gates
require their own exact-revision evidence.

Audit inspection uses a strict recursive JSON decoder with the existing pinned
Serde dependency (now explicitly declared by veil-core). It keeps the JSON
parser recursion limit and rejects trailing input. Duplicate-key errors do not
include key names; the CLI emits only the failure category. Regression tests
cover repeated envelope keys, Unicode-escaped aliases, nested objects inside
arrays, and the preservation of ambiguous input files.

## Owner-controlled snapshot export (native validation pending)

`veil-audit export --path JOURNAL --output /absolute/private-dir/SNAPSHOT`
validates and copies at most 4 MiB while holding the inspector's shared journal
and stable-directory locks. The destination must be a new mode-0600 file in
a separate mode-0700 directory owned by the effective caller. Existing files,
final symlinks, unsafe output directories and the source journal directory
are refused. Successful output and its parent are synchronized. Failed copies
remove only a destination still matching the created inode.
Creation and failed-copy cleanup are anchored to the opened private destination
directory using Linux descriptor-relative operations. A renamed or substituted
parent pathname cannot redirect those operations. Cleanup inspects the final
object without reading it or following symlinks and refuses unrelated inodes.
Host regressions substitute a directory path and preserve replacement files
and symlink targets. This extension awaits host and native validation.

The compact `synveil.audit-export/v1` stdout receipt includes the exact byte
count, record count, BLAKE2b-512 digest and `authenticated: false`; it contains
no supplied paths or event contents. Store the receipt separately from the
snapshot. `veil-audit verify --path SNAPSHOT --digest DIGEST` performs bounded
read-only structural inspection and requires an exact checksum match against
that independently retained lowercase hexadecimal digest. The snapshot itself
contains private audit data and must remain local unless explicitly exported
by its owner.

This detects changed snapshot bytes relative to a trusted retained digest.
It does not authenticate origin, prove completeness, prevent root from changing
both snapshot and receipt, or establish an integrity chain. Export neither
deletes archives nor releases retention capacity; authorized retention-slot
release remains a later dependency. No network or core IPC is involved.

The RustCrypto `blake2` crate is now a direct pinned dependency, reusing the
existing version 0.10.6 already locked for Argon2; no new package is introduced.
Host regressions cover exact private copies, a published digest vector,
well-formed tampering, busy-source refusal, unsafe/occupied outputs, invalid
input preservation and actual CLI argument/error privacy. The native rollover
gate now requires exporting its synthetic archive, verifying an exact private
copy, refusing replacement, and detecting an edited copy that remains valid
JSON/audit structure. Host shell fixtures refuse failed export, overwrite and
acceptance of the edited digest. Native export/verify execution and target
linkage validation remain pending.

## Bounded journal rollover

Before an append would exceed 4 MiB, the writer synchronizes the complete
active journal and preserves it as `core-audit.jsonl.1`. Linux atomic
`renameat2(RENAME_NOREPLACE)` refuses every existing archive destination,
including symlinks and directories. The archive rename is synchronized before
a new mode-0600 journal is created. The next event remains a normal v1 record.
A console notice reports the preserved archive; no event content is printed.

This is one archival slot, with at most 8 MiB of generated journal data. No
archive is overwritten, deleted or silently pruned. After both slots fill,
disk appends are refused with a retention-capacity error and console events
continue. Damaged/incomplete or oversized active journals are preserved and
refused, rather than rotated to conceal the problem. Automatic retention,
owner-authorized archival/export, disk-full policy and power-loss testing remain
open; this prototype does not complete durable audit.

Writers use a private, zero-length, singly linked mode-0600 `.audit-lock`
in the journal directory, held across open, append and rename. Inspectors
take a nonblocking shared lock on this stable inode before opening a journal
or archive. Per-file locks remain too, allowing read-only inspection of older
journals without a lock file and protecting the transition to the new writer.
Inspection never creates a lock file. Unsafe locks or lock contention refuse
access without changing event data. The stable lock is never renamed/deleted
by audit code.

Host regressions cover archive byte preservation and structural inspection,
continued appends/restart, an occupied archive, incomplete tails, writer/reader
lock contention, unsafe lock content and reopening after a rename with no
active file. The original guest rollover gate passed at
[f973531](validation/native-f973531/README.md). Crash/power-loss evidence remains pending.

The native workflow's `recovery-smoke --case audit-rotation` guest gate
uses a separate, exclusively created directory on the disposable QEMU state
volume. It generates exactly 512 valid 8192-byte fixture records (4 MiB), checks
them with the target inspector, and starts a bounded one-connection native core
using that test journal and a separate socket. A real status request triggers
the rollover. The gate requires unchanged archive SHA256, root/private/single-
link archive metadata, 512 archived records, a valid new journal and healthy
status from the independent main core. The existing journal and user state
are not overwritten. Host tests validate the exact normalized AWK/shell
command and reject command echo or missing boot readiness. Its original
rollover scenario passed QEMU at [f973531](validation/native-f973531/README.md).

The expanded gate then fills the synthetic active journal while keeping the
existing archive, runs another bounded native core, and requires healthy
status, console request/shutdown events, the explicit retention-capacity error,
and unchanged hashes for both files. Finally it replaces only the synthetic
active file with an incomplete JSON tail. Another core must retain console
events and status, report the incomplete-tail refusal, preserve both hashes,
and leave the damaged file rejected by the inspector. Host fixtures execute
the normalized shell and reject altered history, automatic tail repair,
missing failure notices, failed helper exits and an inspector that accepts
the incomplete tail. These expanded guest gates passed at
[3fb0158](validation/native-3fb0158/README.md).

## Isolated storage exhaustion

The `audit-enospc` guest scenario mounts an exclusively created 1 MiB tmpfs
under `/run`, seeds one valid page-aligned 4096-byte record and a private
zero-length lock, and fills only this synthetic filesystem. Page alignment
forces the next append to allocate a new page rather than consume spare bytes
in an existing page. The bounded native core must retain status and console
request/shutdown events, report ENOSPC, and preserve the seeded journal hash.
The offline checker must still accept exactly one record. After removing only
the synthetic filler, a fresh core must append successfully without sink
warnings, produce a larger structurally valid journal and retain main-core
status. Success also requires unmounting the temporary filesystem.

Host fixtures check shell syntax, page alignment, echo/readiness rejection and
control flow for false exhaustion, overwritten evidence, missing warnings,
failed resumed logging and failed unmount. They do not exercise the native
kernel/filesystem path. This bounded tmpfs scenario neither fills the main
state disk nor establishes ext4 disk-full or power-loss durability.
The native tmpfs exhaustion/resumption gate passed at
[ebc9f4e](validation/native-ebc9f4e/README.md).

The extended gate then remounts the same synthetic filesystem read-only.
Another bounded core must report EROFS, preserve the journal hash, and retain
IPC and console request/shutdown events; offline inspection must still work.
After an explicit read-write remount, a fresh core must append structurally
valid records without sink warnings. Host fixtures reject mutation while
read-only, missing warnings, failed remounts, and failed resumed writes.
The read-only extension passed in the
[partial ffd0b0a run](validation/native-ffd0b0a/README.md), whose unrelated
graphical settings gate failed. It does not
simulate an ext4 error-triggered remount or establish power-loss durability.
