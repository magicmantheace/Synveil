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

The versioned core status reports whether persistent audit is configured, the
latest attempted write's `healthy` or `degraded` result, and a process-local
failed-write count. A successful later append returns the state to `healthy`
without erasing the count. This exposes loss of persistent evidence through the
existing read-only control path while keeping status and console recovery alive.
It does not prove that a successful sync survived device-cache loss, and the
counter resets when the emitting process restarts.
The rollover-capacity, incomplete-tail, tmpfs ENOSPC/read-only and ext4 ENOSPC
guest gates require matching `healthy` or `degraded` status and zero/nonzero
failure counts from the real helper core. Their host control-flow fixtures
reject incorrect state, configuration and counts. All three native guest gates
passed at [141e20e](validation/native-141e20e/README.md), with the downloaded
evidence digest and unique success markers verified.

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

Host fault-injection tests exercise write, file-sync, archive-rename and
parent-directory-sync error returns through a private statically dispatched
storage adapter. The production adapter directly performs the existing
filesystem operations; there are no runtime fault flags or environment hooks.
The tests require prior bytes and archive identity to survive failures, verify
safe recovery from an archive-only or empty-active layout, and preserve/refuse
further append after an injected partial write. A sync error can leave complete
but unacknowledged bytes, so an error does not mean nothing was written or grant
exactly-once retry semantics. Injected errors establish control-flow behavior,
not real device EIO, interrupted filesystem operations or power-loss durability.
All four fault-injection tests passed host CI at `6e7d7ed`; full native
revalidation of the production storage adapter passed at
[4390a8c](validation/native-4390a8c/README.md).

An additional host fixture substitutes a symlink, hardlink, directory or FIFO
for the next active journal after the archive rename and directory sync. It
requires the writer to refuse the substituted object before attempting a record
write, preserve the archive's bytes and inode, leave unrelated target bytes
untouched, and retain the substituted object. Only the fixture removes its own
injection before checking recovery. This is deterministic host path-boundary
coverage, not a guest crash, device fault or proof against a malicious root.
The four cases passed host CI at `d70c745` (113 Rust tests, formatting/clippy).

A further test-only subprocess matrix sends itself SIGKILL immediately after
archive rename, after archive-directory sync, before writing the new active
journal, or after writing a 17-byte partial record. The parent requires an
actual signal-9 exit, exact archived bytes/inode, released locks, and safe
restart; an incomplete active tail must remain unchanged and block appends.
A five-second watchdog fails the test if a boundary is not reached. Host CI
passed at `f75de2b` (115 Rust tests, formatting and clippy;
[run 38907418988](https://github.com/magicmantheace/Synveil/actions/runs/38907418988)).
The kernel and filesystem cache survive these process
cuts: this does not interrupt a kernel syscall, reboot a guest or model power loss.

An extension covers complete but unacknowledged writes before file sync, after
file sync and after final directory sync. It also exercises first creation and
ordinary append at five cuts each, for 17 cases including rotation. A restart
must preserve full records before a new event, retain inode identity and valid
record counts, and refuse partial tails without repair. The extension passed
host CI at `72df789` (117 Rust tests, formatting and clippy;
[run 38914789468](https://github.com/magicmantheace/Synveil/actions/runs/38914789468)).
Successful host fixture appends use bounded WouldBlock-only
retries for descriptors briefly inherited by parallel fork/exec tests; production
append and explicit contention/error assertions remain nonblocking. A held-lock
regression checks a single append without duplicate records or tail repair.
Process death after sync is still not evidence that a
volatile device cache survives power loss.


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

## Owner-controlled snapshot export

`veil-audit export --path JOURNAL --output /absolute/private-dir/SNAPSHOT`
validates and copies at most 4 MiB while holding the inspector's shared journal
and stable-directory locks. The destination must be a new mode-0600 file in
a separate mode-0700 directory owned by the effective caller. Existing files,
final symlinks, unsafe output directories and the source journal directory
are refused. Basic export, digest verification, replacement refusal and
well-formed edit detection passed at
[612bd80](validation/native-612bd80/README.md).

A later hardening change creates an unnamed Linux `O_TMPFILE` inode relative
to the opened private destination directory. The copy is written, synchronized
and read back before `linkat` atomically publishes its destination name without
replacement. Closing an unpublished inode discards it without pathname-based
cleanup; another process's replacement entry cannot be deleted. Publication
uses `/proc/self/fd` so it requires no additional capability. Filesystems lacking
`O_TMPFILE` support or unavailable procfs fail closed; no named-copy fallback
exists. If directory synchronization or identity checking fails after
publication, a complete copy may remain and no success receipt is returned.
The tool never deletes that name because it may have been replaced.
Host regressions exercise directory substitution, competing files/symlinks and
unpublished partial-copy disposal. Host run 38665047365 passed formatting,
clippy and all 107 Rust tests at 594c1cb. Native validation of this later
implementation passed the complete native run at
[9f722d0](validation/native-9f722d0/README.md).

The isolated exhaustion/read-only guest gate now also attempts snapshot
exports into a separate private directory on its synthetic filesystem. It
requires the specific storage-full/read-only failure category, no remaining
partial copy and an unchanged source hash. After recovering write access,
export and verification must succeed and copy/source hashes must match.
Host fixtures reject false success, leftover partial files, wrong failure
categories, failed recovered export and altered copy bytes. Native evidence
for these export-failure gates passed at
[9f722d0](validation/native-9f722d0/README.md).

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
graphical settings gate failed. The complete native run, including settings,
subsequently passed at [612bd80](validation/native-612bd80/README.md). It does not
simulate an ext4 error-triggered remount or establish power-loss durability.

## Disposable ext4 exhaustion gate

The `audit-ext4` scenario always creates a fresh 64 MiB host-side ext4 image
and overrides any caller-supplied state path for its own QEMU process. It
requires state readiness and `/dev/vdb` mounted as ext4 at `/var`. The test
keeps helper sockets and logs on `/run`, seeds a private page-aligned journal
on `/var`, and uses a bounded 64 MiB filler to force actual ENOSPC. Both the
filler and bounded native core must report exhaustion. The seeded journal
hash and one-record structural report must remain unchanged, failed export
must report StorageFull with no partial destination, and main-core status
must remain available while storage is full.

After deleting only the synthetic filler and synchronizing, another helper
must append valid records without sink warnings. Export, digest verification,
copy/source byte equality and independent main-core status must succeed.
QEMU is terminated before the private disposable image is removed, including
on guest failure or timeout. The caller's state image is never passed to this
scenario. Host fixtures reject missing state readiness, wrong mount identity,
false exhaustion, changed history, incorrect export errors, partial copies,
failed recovery and loss of main-core IPC. The guest shell is syntax checked.
These harness checks and the native ext4 gate passed in the complete
[fcba067 run](validation/native-fcba067/README.md).

This test targets clean ext4 ENOSPC and resumed writes. It does not simulate
power loss, journal corruption, device I/O failure or an error-triggered
read-only remount, and does not establish an installed-system disk-full policy.

## Acknowledged writes after abrupt emulator stop

`bash build.sh audit-crash-smoke` creates a new private 64 MiB ext4 state image
for two QEMU boots, ignoring caller-supplied state images. A bounded helper
core writes an isolated journal, completes a status request and shuts down.
The first boot refuses sink errors, structurally checks the nonempty journal,
exports a synchronized snapshot, and verifies both files against the export
digest. Its digest and acknowledgement are retained in the host serial log.
The guest test does not run a global `sync` or clean shutdown before the cut.

The host identifies the wrapper's single emulator child using parent identity,
process group and executable name, opens a Linux pidfd, sends SIGKILL to that
exact process, and requires the wrapper to reap it with status 137. A missing
or ambiguous child, unsupported process inspection/pidfd, failed signal or
unexpected exit status fails the test. Normal cleanup remains available after
any failure. The second boot uses the exact same disk and the single strictly
parsed host-retained digest to verify both original journal and exported copy,
then requires healthy main-core IPC. The disposable image is removed only
after both wrappers have been waited on.

Host control-flow fixtures reject missing state readiness, command echo,
missing/malformed/duplicate digest evidence, failed second-boot verification,
wrong emulator identity and unexpected exit status. Real signal/process identity
fixtures run on CI; local process proxies cannot provide that evidence.
This basic gate passed in the complete
[00476e5 run](validation/native-00476e5/README.md).

This tests already acknowledged writes after an emulator process crash. It
never interrupts an in-flight append or rotation and does not model a host
power failure, volatile drive cache loss, arbitrary block loss or physical
hardware durability. Those scenarios and authenticated integrity remain open.

The later `--rollover` extension seeds exactly 4 MiB of valid synthetic audit
records, then lets the native helper complete rotation and append to the new
active file. Before acknowledging, the gate requires the archived seed hash,
private single-link metadata, 512 archived records, and verified exports of
both active and archived files. It retains distinct active/archive digests in
the host log. After SIGKILL and independent reboot, both originals and both
snapshots must match their respective host-retained digests. Missing or duplicate
archive digest evidence, archive corruption, wrong counts, failed archive
export and changed copy bytes are rejected by host fixtures, including execution
of the generated shell. This gate passed in the
[partial c8a759b run](validation/native-c8a759b/README.md). Full revalidation,
including both previously failing graphical scenarios, passed at
[1edc09e](validation/native-1edc09e/README.md). This tests completed rollover;
it does not cut power between rename, directory sync and creation of the next
active file.

The crash harness now rejects a valid active/archive digest receipt accompanied
by any malformed receipt with the same prefix. Counting only regex-valid lines
could otherwise hide truncated or conflicting evidence. This changes host-side
test gating, not the audit runtime or its on-disk format. Candidate-count and
control-flow regressions passed host CI, and the strict parser passed the full
[1edc09e native run](validation/native-1edc09e/README.md).

The later `--archive-only` gate constructs a valid 4 MiB retained archive with
no active journal. Preparation runs `sync` before the native helper starts,
establishing the seed as a disk baseline; no global sync follows native recovery
appends or exports. Failed preparation sync prevents helper startup and success
evidence. The helper must create a valid active journal without changing the
archive. Independent exports and host-retained digests then cover both originals
and both snapshots across verified emulator SIGKILL and reboot. This gate is
validated in the full [c97f554 native run](validation/native-c97f554/README.md).
A constructed post-rename layout is not a timed interruption of a live rotation.

## Explicit offline archive release (candidate)

The explicit filesystem-owner operation is:

```sh
veil-audit release-archive --path /private/journal \
  --snapshot /copies/snapshot --authorization /copies/release.json \
  --digest "$RETAINED_DIGEST"
```

The paths must be absolute without parent
traversal; both directories must be owned by the caller and mode 0700, and the
snapshot/authorization directory must differ from the journal directory. The
command derives only `<active>.1`; it never unlinks the supplied active path.
It refuses an active basename ending in `.1`, reserved lock names, aliases,
unsafe files, busy locks, malformed/truncated history and digest/copy mismatch.

The archive and independent singly linked snapshot must be private mode 0600,
owned by the caller, structurally valid and byte-for-byte identical to the
separately retained digest. While holding the stable exclusive journal lock,
archive lock and snapshot inspection locks, the command syncs the copy and its
directory, then publishes a new mode-0600 authorization receipt using an unnamed
inode and no-overwrite link. It syncs that receipt and its directory before
unlinking the archive through the opened journal-directory descriptor and
syncing that directory. All names and parent identities are checked again
before unlink. Existing receipts are never replaced or automatically cleaned up.

The persisted `synveil.audit-retention-authorization/v1` receipt records
`authorized-before-unlink`, digest, size, count, caller UID and archive/copy inode
identities. It does not claim release completed. After unlink and journal-directory
sync, a second JSON line records `synveil.audit-retention-release/v1` with
`released:true`; that line is synchronized before the same report is returned.
The authorization file is therefore a two-record JSONL receipt on success.
A failure after receipt publication can leave the intent; a failure after unlink can leave the
archive absent or an incomplete completion line. Preserve the first intent and
inspect both locations before retrying with a fresh receipt.
The active journal must exist as a caller-owned private singly linked regular
file, opened without following symlinks and exclusively locked. Its bounded
contents must be structurally valid (an empty active file is allowed). Its inode
must differ from the archive and snapshot, and its identity is checked again
before unlink. Missing, malformed, incomplete or unsafe active journals refuse
release before authorization; active history and the retained copy remain untouched.

There is no automatic deletion, planner request or IPC endpoint. Filesystem
ownership is the authorization boundary of this offline administrative tool;
future model-driven retention requires typed policy and stronger owner approval.
A copied digest does not authenticate origin, guarantee an independently backed
up copy, defeat a malicious owner/root process, or prove device-cache durability.
The owner must preserve the copy, authorization and independently retained digest.
The initial [e81d6d5 run](validation/native-e81d6d5/README.md) passed all 44
native steps, including release, before review identified the unsafe-active case.
The corrected active validation and private storage-error adapter passed host
CI at `da43fc1` (126 Rust tests, formatting/clippy and bootstrap lint;
[run 39038885162](https://github.com/magicmantheace/Synveil/actions/runs/39038885162)).
Eleven EIO boundaries and partial intent/completion writes preserve active/copy
history and truthful residual receipts; these are simulated errors, not real
device or crash durability proof. Corrected source `da43fc1` passed all 44 native
steps ([evidence](validation/native-da43fc1/README.md)); merged source `8d8dfed`
passed all 44 steps in [run 39052189785](https://github.com/magicmantheace/Synveil/actions/runs/39052189785).
The durable-audit roadmap item remains open.

## Release process-loss matrix

Test-only subprocess adapters terminate themselves with SIGKILL at nine release
boundaries: partial anonymous intent, intent file sync, publication,
authorization-directory sync, unlink, journal-directory sync, partial completion,
complete completion write, and completion file sync. Each worker must emit its
fixed phase checkpoint before the parent accepts a signal-9 exit. A watchdog
kill fails rather than counting as checkpoint evidence.

The seed is synchronized before the worker starts. Before any post-cut sync or
recovery, inspect exact active/copy bytes and inodes, archive presence/identity,
receipt phases and incomplete completion tails, anonymous-inode cleanup and
release of stable/active/archive/copy locks. A fresh authorization can finish a
pre-unlink release without changing the old intent. An already removed archive
must refuse retry without altering the old receipt or retained copy.

All nine cuts passed host CI at `4f4c0a2` with 129 Rust tests, formatting and
clippy ([run 39046865707](https://github.com/magicmantheace/Synveil/actions/runs/39046865707)).
The nine-cut matrix runs in a separate exact-filtered test executable to keep
its forks away from parallel tests' open audit descriptors. All crash controls
are compiled only into tests. These are userspace process failures while the
host kernel/cache survives, not interrupted kernel operations, guest reboot at
release boundaries, real device faults or physical power-loss durability.

## Read-only release receipt inspection

After a refused or interrupted release, preserve its receipt and retained copy.
The offline inspector accepts the same four explicit inputs:

```sh
veil-audit inspect-release --path /private/journal \
  --snapshot /private-copies/archive \
  --authorization /private-copies/release.json --digest "$retained_digest"
```

It reports `synveil.audit-release-inspection/v1` with one of
`authorization-only`, `incomplete-completion` or `completion-recorded`.
It verifies the private snapshot's structure, checksum, size, count and recorded
inode/device against a bounded, strictly decoded receipt. Both receipt records
must match the release schemas; duplicate/unknown keys, extra lines and mismatched
completion claims refuse. An incomplete completion must be a prefix of the
actual writer's completion line. A partial first intent is invalid.

The existing stable journal lock and snapshot/receipt shared locks exclude
cooperating release and file writers. The journal scope and copies directories
must remain private and distinct; receipt and snapshot must be caller-owned,
mode 0600 and singly linked, opened without following symlinks. Inspection
creates no lock, repairs no receipt, syncs no file and removes no archive.
A missing active file does not prevent reading an otherwise valid receipt.
A missing stable scope lock refuses without recreating it.

Every successful report has `archive_state:"not-inspected"` and
`authenticated:false`. A completion line can survive an unacknowledged write;
reading it does not prove its final sync completed or establish current archive
presence, identity or durable storage. The archive slot may already have been
reused. Preserve the independently retained digest and inspect current storage
separately before deciding on a fresh explicit release. This tool grants no
additional policy authority and does not automatically resume release.

## Live release guest-interruption fixture

`bash build.sh guest-fixture --case audit-release-crash` stages an isolated
x86_64 syscall tracer beside the unchanged native `veil-audit` binary. The
tracer is compiled only into this disposable test image, never the normal
rootfs or packaged preview. It holds the real release process at a syscall stop
and emits a fixed checkpoint only after observing the required successful calls.
The host must see that exact phase, SIGKILL QEMU, confirm wrapper reap, retain
both journal digests independently and reboot the same disposable state disk.
Each phase starts with a fresh disk. The initial four phases required eight
QEMU boots and passed at [d846b81](validation/native-d846b81/README.md).
The six-cut matrix passed across twelve boots at
[cbf272c](validation/native-cbf272c/README.md).

| Cut | Held boundary | Required recovery observation |
| --- | --- | --- |
| `before-unlink` | Before archive unlink, after receipt publication and copy-directory sync | Archive intact; authorization-only receipt |
| `after-unlink` | Unlink returned zero, before journal-directory sync | Archive present or absent after replay; authorization-only receipt |
| `unlink-synced` | Journal-directory fsync returned zero, before completion write | Archive absent; authorization-only receipt |
| `partial-completion` | Exactly one completion byte written after journal-directory sync | Archive absent; authorization-only or incomplete-completion receipt |
| `completion-written` | Full completion line's final newline write returned, before file sync | Archive absent; authorization-only, incomplete-completion or completion-recorded receipt |
| `completion-synced` | Completion-file fsync returned, before caller receives its report | Archive absent; completion-recorded receipt |

The tracer identifies publication, archive unlink and journal sync descriptors;
it requires intent file sync before publication and authorization-directory sync
before unlink. Unknown signals, failed syscalls, missing checkpoints and watchdog
expiry fail the fixture. After checkpoint it never resumes the tracee. The only
syscall modification shortens the first completion write to one byte; it does
not change the production binary or add a production fault flag.

After reboot, the read-only inspector and retained digests check the surviving
receipt/history before recovery. A surviving archive can be released only with
a fresh receipt; an absent archive must refuse retry without creating that
receipt. Original receipt bytes and active/snapshot histories must remain intact.
Host source `d3a0360` passed real tracing and refusal checks against a controlled
filesystem worker, but the first native candidate failed because that worker and
tracer assumed a publication syscall different from production. The corrected
tracer recognizes the production procfs descriptor source; host CI also traces
the actual built CLI at every cut. This correction passed at `18f43f1`, including
all four actual CLI stops and read-only receipt inspection afterward. Corrected
native source `d846b81` passed all 45 steps and the initial four guest cuts;
native source `cbf272c` passed all 45 steps and the two completion-boundary
extensions across twelve independent boots. The tracer
recognizes the complete line by its successful final-newline write, then tracks
the following sync of that same receipt descriptor. Neither stop resumes the
command to return its report. Reading a complete receipt still does not, by
itself, establish that its file sync returned. The guest fixture
targets kernel loss and filesystem replay while the host page cache survives; it does not
simulate real device errors, volatile-device-cache loss or physical power failure.
