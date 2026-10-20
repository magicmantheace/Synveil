# Native Core Bring-Up Contract

Status: **native boot and status validated on `phase2/native-core-bootstrap`; core-crash recovery validated; absent-core validation pending**

## Target build preflight

`bash build.sh native-check` checks the isolated Synveil GCC/sysroot and the
nightly Rust compiler and source bytes pinned in `build/manifests/rust-bootstrap.json`.
It requires Python 3.11+, rustup, the existing Phase 1 toolchain, and the pinned
nightly with its `rust-src` component. Install Rust prerequisites explicitly:

```sh
rustup toolchain install nightly-2026-10-01 --profile minimal --component rust-src
mkdir -p .cache/sources/archives
curl --fail --location \
  https://static.rust-lang.org/dist/2026-10-01/rust-src-nightly.tar.xz \
  --output .cache/sources/archives/rust-src-nightly-2026-10-01.tar.xz
bash build.sh native-check
```

The existing `SYNVEIL_TOOLCHAIN_DIR`, `SYNVEIL_SYSROOT`, and `SYNVEIL_TARGET`
settings apply. The checker does not install dependencies, compile binaries,
modify the rootfs, or change startup. The stable host-test toolchain stays pinned
separately in `rust-toolchain.toml`.

Passing preflight verifies compiler identity, installed source metadata, target
glibc files, and that GCC selects Synveil's sysroot/libgcc. It checks the archive
SHA-256 against the pin, then compares the complete installed Rust library file
set and every file's bytes with the archive without extracting it. Changed,
missing, extra, or symlinked source files fail validation. The output records the
archive identity and verified file count. Use `--rust-source-archive /path/to/archive`
to supply the same pinned archive from another location.

The native builder must still rebuild std, audit actual target link inputs, and
record output identity before packaging. ADR 0004 remains Proposed. This source
check has automated fixture coverage and passed with the real installed
toolchain in the [first native target validation](validation/native-577b5e7/README.md).

## Native compilation command

After preflight prerequisites are installed, run:

```sh
bash build.sh native
# An archive outside the default cache can be supplied explicitly:
bash build.sh native --rust-source-archive /path/to/rust-src-nightly.tar.xz
```

This command acquires the checkout build lock and creates a fresh Cargo output
directory under `build/work/native/` for every invocation. It rebuilds target
`std` from the verified source using the pinned nightly and committed Cargo
lock. Target links use Synveil GCC with static glibc and aborting panics; host
build scripts and procedural macros link through host `cc`. The stage-1 GCC
unwind archive is exposed as `libgcc_eh.a` through a symlink to Synveil's own
`libgcc.a`, without borrowing host `libgcc_s`.

Before publishing binaries, it inspects x86_64 ELF headers, rejects shared
runtime dependencies, and checks linker-map inputs against the Synveil
toolchain, sysroot, and fresh native build directory. The builder controls Rust
flags and clears inherited compiler/library search overrides. Build work paths
must not contain whitespace or commas.

Successful audited outputs are `out/native/veil-core`, `out/native/synctl`,
`out/native/veil-enroll`, `out/native/veil-seat`, `out/native/veil-audit`, and
`out/native/build.json`. The manifest records source verification, Git identity,
Cargo lock and libgcc hashes, binary hashes, and link-map identities and inputs.
Cargo logs and link maps remain under the fresh work directory. A failed build
does not imply that older outputs in `out/native/` are valid for the current
revision; callers must require a successful build exit status.

The command does not package the rootfs or change boot startup, and is not yet
part of `build.sh all`. Its orchestration and audit boundaries have automated
test coverage and a successful real target build. Integrated-image QEMU
validation remains required before ADR acceptance.

The `Native target build` workflow on `phase2/native-core-bootstrap` exercises
this command on a fresh Ubuntu runner. It explicitly installs and authenticates
the pinned Rust source inputs before rebuilding Synveil's C toolchain, then
compiles and audits both binaries. It retains binaries, identities, build logs,
and link maps as `native-target-<commit>` evidence. This is target-build
validation was first recorded separately. The workflow now also builds an
opt-in native rootfs, kernel, and GPT image, records the image manifest, and
runs the native status smoke test. Logs and image identities are retained even
when a later step fails; a queued workflow is not boot validation.

`python3 tools/prepare_native_rust.py` is the explicit dependency-install step
used by that workflow. Unlike `native-check`, it installs the pinned rustup
toolchain and downloads the pinned archive when it is absent from the cache.

## Opt-in rootfs and startup integration

On a clean checkout, build native binaries and then opt into packaging:

```sh
bash build.sh native
SYNVEIL_NATIVE_ROOTFS=1 bash build.sh rootfs
bash build.sh kernel
bash build.sh image
bash build.sh native-smoke
```

The rootfs builder validates the bundle before building BusyBox and again
before installation. It requires all five binaries to match their audited hashes,
the current clean Git revision, the current Cargo lock, and the pinned Rust
source verification. It installs `veil-core` in `/usr/sbin`, `synctl` in
`/usr/bin`, and the native build identity at `/usr/share/synveil/native-build.json`.
The default rootfs remains the Phase 1 bootstrap without native packaging.

Native packaging also installs `/usr/libexec/synveil/start-core`. After mounting
the virtual filesystems, `/init` runs that helper, which starts `veil-core` and
attempts up to five status checks, each capped at one second with forced
termination. A successful status request emits `SYNVEIL_CORE_READY`. Missing or
failed components emit `SYNVEIL_CORE_UNAVAILABLE`; unsuccessful startup is
terminated and `/init` continues to its recovery shell. A later core crash does
not terminate the independent recovery shell. Bootstrap now starts
`veil-core --supervise`. Its Rust parent launches a
worker with a fixed command and socket path, waits/reaps it, and allows at most
three restarts after abnormal exits, with 250 ms between attempts. Normal exit
stops supervision. Spawn failures and budget exhaustion stop the parent;
recovery remains independent. Linux parent-death signalling kills the worker
if its supervisor dies, including the fork/exec race. This is one bounded core
supervisor, not a general service manager or production PID 1. Guest validation
of the new lifecycle is required before completing that roadmap item.

Packaging-integrity tests and startup failure tests use isolated fixtures. The
`native-smoke` command requires standalone `SYNVEIL_CORE_READY` and
`SYNVEIL_BOOT_OK` console lines and rejects `SYNVEIL_CORE_UNAVAILABLE`.
The ready marker is emitted only after `synctl --json status` succeeds while
its core process is alive. The command holds the checkout build lock, boots
the existing image, and saves console evidence to `out/logs/qemu-native-smoke.log`.
The default smoke timeout is 30 seconds (`SYNVEIL_SMOKE_TIMEOUT` overrides it);
CI allows 60 seconds. Baseline `smoke` still requires only the boot marker.
Console fixture tests exercise the verdict without claiming a QEMU boot.
Integrated-image core status passed in the
[native image validation](validation/native-image-79a41c2/README.md).

`bash build.sh recovery-smoke --timeout 60` boots the existing native image,
waits for boot and ready markers, then sends a fixed test command through the
recovery console. It kills `veil-core`, requires the process to be gone and
`synctl status` to fail, then writes and reads a file through the still-working
shell. Only the exact standalone `SYNVEIL_RECOVERY_OK` line passes; terminal
command echo cannot satisfy it. Console output is retained at
`out/logs/qemu-recovery-smoke.log`; timeouts terminate QEMU and its wrapper.
CI runs this as a separate guest boot after the status smoke. Its console
fixtures validate harness behavior, not a real guest crash. Actual guest crash
recovery passed in the [core-crash record](validation/core-crash-248775a/README.md).
Absent-core boot evidence remains pending; Phase 2 stays open.

`bash build.sh guest-fixture --case absent` copies the built native rootfs to
an isolated staging tree, removes only the copied core binary, repacks its
initramfs, and builds a separate kernel/GPT image. Its guest must report boot
success and core unavailability, have no core binary or running core, and
execute shell file operations successfully. Normal rootfs and image outputs
are preserved.

`bash build.sh guest-fixture --case protocol` uses another isolated rootfs copy
and compiles the fixed C protocol probe with Synveil GCC/static glibc. The probe
is installed only into that test copy at `/usr/libexec/synveil-test/protocol-probe`.
It requires framed, versioned error responses with the expected correlation
IDs for malformed JSON, an unsupported schema, and an unregistered method.
The guest must then obtain healthy status through `synctl`. The probe is test
traffic, not a new runtime control API, and adds no production dependencies.

Both commands hold the build lock. Variant images, console logs, and SHA-256
identity manifests are under `out/guest-fixtures/<case>/`. CI retains the logs
and manifests. Fresh guest validation is required before treating either new
case as passed; host fixtures do not close the guest requirements.

Target-build CI now queues newer validation behind the active build so pushing
packaging/startup work does not discard an in-progress compiler build. Its path
filter includes compilation, packaging, kernel, image, and smoke-test inputs.

This document defines the smallest useful native Synveil control service and CLI boundary.

It deliberately does not define actions, AI planning, long-term memory, or the final supervisor. Those belong to later roadmap phases.

## Components

The initial Rust workspace is expected to contain three responsibilities:

```text
veil-protocol
  shared wire types and schema validation helpers

veil-core
  deterministic local control service

synctl
  human/developer command-line client
```

Exact crate paths may change during implementation, but these boundaries should remain clear.

## First capability

The first supported operation is read-only status.

```sh
synctl status
```

It must prove that:

1. native Synveil code is present in the generated rootfs;
2. `veil-core` starts independently of the AI stack;
3. a local client can reach it through a structured IPC endpoint;
4. request and response versions are explicit;
5. malformed input fails safely;
6. failure of `veil-core` does not prevent recovery access to the machine.

No privileged mutation is introduced in Phase 2's first slice.

## IPC endpoint

Initial endpoint:

```text
/run/synveil/veil-core.sock
```

The runtime directory is ephemeral and recreated on boot.

The initial transport is a local Unix domain stream socket.

JSON is acceptable for bring-up because it is inspectable, testable, and keeps the protocol independent of Rust type layout. A later encoding change must preserve explicit schema/version semantics.

## Framing

Each protocol message is one UTF-8 JSON object followed by a newline.

Constraints for the first implementation:

- one request per line;
- one response per request;
- maximum message size enforced before parsing;
- invalid UTF-8 or invalid JSON is rejected;
- connections are local only;
- no request may cause generic shell execution.
- correlation IDs contain 1 to 128 bytes; method names contain 1 to 64 bytes;
- outgoing frames obey the same 64 KiB limit as incoming frames.

The first implementation should close a connection that exceeds the message limit rather than buffering without bound.

The host prototype requires newline termination even when the peer closes the
connection. Clean EOF between frames is allowed; an incomplete frame is rejected.
Core connections have a five-second total read deadline and at most sixteen
requests. Both peers use bounded reads and write timeouts. `synctl` verifies
response correlation and returns failure for an error response even in JSON mode.

New runtime directories are private (0700) and the socket is 0600. A second
core refuses to replace an active listener. Only a refused connection to an
existing socket permits stale-socket removal; files and symlinks are preserved.

## Request envelope

Conceptual request:

```json
{
  "schema": "synveil.core/v1",
  "id": "01J...",
  "method": "status",
  "params": {}
}
```

Required fields:

- `schema` — protocol schema identifier;
- `id` — caller-provided correlation ID;
- `method` — registered operation name;
- `params` — method-specific object.

Unknown methods must return a structured error.

Unknown required schema versions must be rejected rather than guessed.

## Successful response envelope

Conceptual response:

```json
{
  "schema": "synveil.core/v1",
  "id": "01J...",
  "ok": true,
  "result": {
    "service": "veil-core",
    "version": "0.1.0-dev",
    "protocol": "synveil.core/v1",
    "state": "ready",
    "persistent_audit": {
      "configured": true,
      "state": "healthy",
      "failed_writes": 0
    }
  }
}
```

The status result contains deterministic service/build information and the
current process's directly observed persistent-audit sink state. Audit state is
`disabled` when no path is configured, `starting` before the first configured
write, `healthy` after the latest write succeeds, and `degraded` after the
latest write fails. `failed_writes` is a monotonic process-local counter; it is
not retained across core restarts. The service remains `ready` while the audit
sink is degraded so console recovery and status inspection remain available.

## Read-only observation

`synctl --json observe` reads Linux kernel interfaces directly. Aggregate CPU
utilization is derived from two validated `/proc/stat` samples separated by a
minimum 100 ms sleep. `cpu.utilization.status` is `ready` with
`busy_basis_points` in the inclusive range 0 through 10000 and the actual
monotonic `sample_window_ms`. Basis points make the counter-derived precision
explicit: 2500 means 25.00 percent busy across all logical CPUs.

If the logical CPU topology changes between samples, no counter advances or
iowait decreases, utilization is `unavailable` and no value is invented. Linux
permits iowait to decrease, so that case does not fail the observation. A
malformed or backward non-guest counter other than iowait does fail it. Guest
and guest_nice are excluded from totals because Linux already includes them in
user and nice. The cumulative `total_ticks` and `idle_ticks` come from the later
sample. The same interval now includes bounded, numerically sorted per-logical-
CPU busy basis points. A stable CPU with zero delta or decreasing iowait remains
present with explicit `unavailable` status. This is a live window, not
per-process attribution or historical storage.
The native client rejects a successful observe response unless the utilization
schema, sorted logical CPU IDs, record statuses and basis-point bounds validate.

Exact revision `23f323345b073008357a4ca283b9ae86d1e3ec2f` passed the full
native workflow, including the per-logical-CPU gate through the real native core
and structurally validating client. The digest-verified artifact and clean-
revision manifests are recorded at
[native-23f3233](validation/native-23f3233/README.md).

Exact revision `255101d0c5329bba12546466480ec748ec1e3097` passed the full
native workflow, including the QEMU observer gate against the real native core
and client. The downloaded evidence and clean-revision manifests are recorded
at [native-255101d](validation/native-255101d/README.md).

`cpu.frequency` reports bounded, numerically sorted per-logical-CPU current
frequency in kHz from Linux cpufreq sysfs. This is the kernel-reported value,
not an independent hardware measurement. `scaling_cur_freq` is preferred;
`cpuinfo_cur_freq` is used when the scaling value is absent. Missing drivers,
offline CPUs and temporarily busy policies are explicit through `unavailable`
or `partial` status. Malformed kernel values make only the optional frequency
object `invalid`; they do not discard otherwise valid memory, load, pressure or
cumulative CPU observations.

Exact revision `376e2b43fe70a3550009a6d02e267a98e5fe1d34` passed the full
native workflow. The QEMU observer gate required the frequency object from the
real native core and client, though that virtual machine exposed no cpufreq
driver. The digest-verified artifact and clean-revision manifests are recorded
at [native-376e2b4](validation/native-376e2b4/README.md).

The optional top-level `thermal` object reads `/sys/class/thermal/thermal_zone*`
without changing governors, trip points or cooling state. Up to 256 zones are
sorted by numeric kernel ID and report the kernel type plus signed temperature
in millidegrees Celsius. Missing support and wholly unreadable zones are
`unavailable`; an unreadable subset is `partial`. Malformed zone data makes only
this object `invalid`. This is a current kernel sensor snapshot, not a cooling
recommendation, hardware alarm or policy action.

Exact revision `b5f5cae2d0971b200ce7e7ea76fd7c2965a221a1` passed the full
native workflow. The QEMU observer gate required the thermal object from the
real native core and client, though that virtual machine exposed no thermal
zones. The digest-verified artifact and clean-revision manifests are recorded at
[native-b5f5cae](validation/native-b5f5cae/README.md).

The optional top-level `processes` object reads a bounded point-in-time view of
numeric `/proc` entries. Records are sorted by PID and combine PID with kernel
`start_time_ticks` so later consumers can distinguish PID reuse. They expose
parent/process-group/session IDs, state, cumulative user/system ticks, virtual
bytes, resident pages, thread count and nice value. The scan reads at most 16384
directory entries and returns at most 128 process records. Process exit races
are counted as unavailable records. Command lines, environments and executable
paths are never opened. The command name embedded in `stat` is used only to
locate the following fields and is not returned, so arbitrary non-UTF-8 names
remain observable. Before returning, the core removes highest-PID records and
increments the omitted count until the entire response, including the caller's
ID and all other metrics, fits the 64 KiB protocol frame. This is a current
snapshot, not lifecycle history or interval utilization. The native client
validates the bounded schema before accepting a successful observation.

Exact revision `316f74518335fe4df32ba0bef225547b1c8c6d01` passed the full
native workflow after composing the energy extension and correcting the client
to reject successful responses above the producer's 128-record cap. The QEMU
observer gate required the process object and `start_time_ticks` through the
real native core and client. The digest-verified artifact and clean-revision
manifests are recorded at
[native-316f745](validation/native-316f745/README.md).

The optional top-level `energy` object reads bounded Linux powercap zones. It
walks control-type and nested zone directories with a bounded, deduplicated
scan. Each record contains the stable sysfs zone ID, bounded kernel name,
cumulative microjoules and the counter's maximum wrap range. Unavailable zones
are counted; malformed values invalidate only this object. These raw counters
are not watts, do not provide history, and do not authorize power-limit changes.

Exact revision `bca81e5866c6797efcd029796f506737222e0a01` passed the full
native workflow. The QEMU observer gate required the energy object from the
real native core and client, though that virtual machine exposed no powercap
zones. The digest-verified artifact and clean-revision manifests are recorded at
[native-bca81e5](validation/native-bca81e5/README.md).

## Error response envelope

Conceptual response:

```json
{
  "schema": "synveil.core/v1",
  "id": "01J...",
  "ok": false,
  "error": {
    "code": "unknown_method",
    "message": "requested method is not registered"
  }
}
```

Stable machines should consume `code`, not parse the human-readable `message`.

Initial error classes should distinguish at least:

- malformed message,
- unsupported schema,
- unknown method,
- invalid parameters,
- internal service failure.

## Lifecycle

For the first Phase 2 image:

1. bootstrap init mounts required virtual filesystems;
2. init creates `/run/synveil`;
3. init launches `veil-core`;
4. core creates its socket;
5. init may report whether native core reached ready state;
6. the recovery/bootstrap shell remains available even if core exits.

A core crash must not create an init crash loop that prevents console access.

The bounded core supervisor implements the first restart policy. General
service management and a production PID 1 remain separate work.

## Privilege boundary

During first bring-up, `veil-core` may need to run as root because later phases will introduce narrowly scoped privileged capabilities.

That does **not** grant the protocol arbitrary root authority.

Phase 2 exposes only registered methods. There is no `exec`, `shell`, `run_command`, or equivalent generic privileged method.

The socket should initially be root-owned with restrictive permissions. Multi-user authorization and a dedicated control group can be introduced when required by the action/policy phase.

## Protocol library rule

Wire compatibility must not exist only as Rust structs.

The protocol identifier and envelope semantics in this document are part of the contract. Rust types implement that contract; they do not define it implicitly through serialization accidents.

Persistent or externally consumed schema changes require an explicit version decision.

## Logging

Phase 2 should emit structured service events for:

- service start,
- socket ready,
- accepted request method,
- rejected request category,
- clean shutdown,
- fatal internal error.

Do not log full arbitrary request payloads by default; future methods may contain sensitive data.

Early logs may be newline-delimited JSON written to the console or a dedicated file, provided the format is explicit and testable.

## Build identity

`veil-core` and `synctl` should expose:

- Synveil version from `VERSION`;
- source Git revision when available at build time;
- protocol version;
- component name.

The build system, not an LLM, supplies these values.

The host prototype derives its version from `VERSION` and its revision from
Git at build time, with an explicit `SYNVEIL_GIT_SHA` override for build systems.
Both binaries support `--version` without connecting to a service.

### Host validation

```sh
cargo fmt --all --check
cargo test --workspace --all-targets --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
```

Run the complete suite on a host permitting local Unix sockets. A restricted
workspace may run `cargo test --workspace --all-targets --locked -- --skip
unix_socket` for pure protocol and filesystem checks, but this is not socket
integration evidence. Host artifacts do not satisfy the source-built image or
QEMU requirements below.

## Recovery behavior

The following failure modes must leave a usable recovery shell:

- binary missing;
- binary not executable;
- core exits immediately;
- socket cannot be created;
- malformed client request;
- client disconnects mid-request.

Recovery cannot depend on `veil-core` answering successfully.

## Initial tests

Before the first Phase 2 roadmap items are marked complete, tests should cover:

- protocol serialization/deserialization;
- valid status request;
- unsupported schema rejection;
- unknown method rejection;
- malformed JSON rejection;
- oversized-message rejection;
- multiple sequential client requests;
- core clean shutdown;
- init continuing when core startup fails;
- `synctl status` against the core inside QEMU.

## Deliberately deferred

Do not add these merely because the protocol could support them:

- system mutation methods;
- AI/model endpoints;
- arbitrary command execution;
- package operations;
- telemetry history;
- persistent memory;
- remote TCP listeners;
- plugin execution.

The first native core is intentionally boring. Its job is to establish a trustworthy deterministic boundary on which the interesting parts of Synveil can later depend.

## Supervision guest validation

`bash build.sh recovery-smoke --case supervision --timeout 60` boots the native
image, identifies the supervisor's worker through `/proc`, kills only that
worker, and requires a different worker PID plus healthy `synctl status`.
It then kills the remaining workers until the three-restart budget is exhausted,
requires the parent to exit and status to fail, and verifies shell file operations.
Only `SYNVEIL_SUPERVISION_OK` completes the test; console logs are saved as
`out/logs/qemu-supervision-smoke.log`. The guest checks are pending CI.

The [a136fb5 failure record](validation/native-a136fb5/README.md) explains why
absent-core and malformed-IPC guest validation remain pending. Smoke/QEMU
children do not inherit the checkout build-lock descriptor, and failed
interactive smokes print their console tail for diagnosis.

## Versioned audit events

All core and supervisor events now use the [audit v1 format](AUDIT.md), with
schema, emitting PID, process-local sequence, wall-clock availability, build
identity, event, and fields. Startup/fatal messages use the same emitter as
service and supervisor events. The current sink remains console plus an opt-in private persistent journal; this does not complete the
durable-audit roadmap item. The standalone `veil-audit check` performs bounded
read-only structural inspection without requiring core IPC. See AUDIT.md for
its schema, failure behavior and integrity limitations.

The [fc78195 guest result](validation/supervision-fc78195/README.md) records
passing crash recovery but a supervision timeout. Supervision tests now treat
zombie parents as exited. Independent protocol and absent-core guest checks
continue after other guest-test failures when the image was built successfully.
