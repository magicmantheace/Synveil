# ADR 0015: First owner enrollment must use one audited credential authority

- Status: Proposed (integration unimplemented; test infrastructure only)
- Date: 2026-10-21
- Scope: First-boot usable graphical Synveil with persistent, authenticated owner state

## Current verified facts

- The disposable Debian/XFCE live profile can UEFI-boot under QEMU, open
  real Terminal, Thunar and Settings windows, and retain a harmless user
  `/home` file across a separate QEMU boot (see `docs/validation/`).
- A **different** disposable LightDM profile reached a locked greeter with
  no usable password, and pressed Return without opening a session.
- A third test-only LightDM profile passed wrong-then-correct PAM login,
  using a new random test secret injected through **unsafe-for-production**
  QEMU `fw_cfg`. It does **not** enroll a user or preserve a password
  across reboot.
- Native components already include `src/veil-core/src/credential.rs`
  (pinned Argon2id parameters), `src/veil-core/src/bin/veil-enroll.rs`,
  `src/veil-display/enroll.c` and the privilege-separated `veil-seat`
  broker, with independent test coverage. They are **not** integrated with
  Debian LightDM/PAM and must not be described as having been proven by
  the Debian graphical smoke.

## Disposable PAM logout/relock proof (under CI validation)

The current opt-in PAM test profile also contains a **one-shot,
user-owned** XDG autostart logout helper. It waits for the actual
Xfwm4 multiwindow proof to report successful window operations;
only then does the non-root XFCE session call its own standard
`xfce4-session-logout --logout --fast`. It records a test-only
marker in the disposable home so a second login in the same QEMU
process does not trigger another automatic logout.

A separate root-owned observer checks that the original XFCE
session and panel have exited and LightDM has resumed displaying
its greeter before reporting a fixed `AUTH_RELOCKED` marker.
The host runner must capture that locked greeter, submit a **new
independently wrong** ephemeral test password, require that no
relogin marker was emitted, submit the original correct test
password, and confirm a second non-root XFCE session and a real
RGB framebuffer.

The full job is `.github/workflows/desktop-pam-test.yml`. These
tests prove **logout/relock and same-boot relogin only if the
corresponding exact-source QEMU run finishes successfully**.
They do not prove credential persistence across power cycles,
first-user enrollment, owner recovery or physical installed-system
reboot. Only an isolated developer CI image may run the automated
one-shot logout; the default XFCE integration image must not
include it.

## Design rule

There must be **one authoritative** owner credential path in the eventual
bootable Synveil image. Do not silently create both a LightDM
`/etc/shadow` password and a native Argon2id credential as competing
sources of authentication. The actual integration choice—an audited PAM
bridge to the native authority versus a native seat-managed desktop login—
requires a separate, security-reviewed implementation decision.

The native `veil-enroll` flow already enforces a protected input pipe,
one-time root-private Argon2id storage on an actual ext4 state mount,
strict record permissions, and refusal to replace an existing account.
The first-owner GUI must use an explicitly authorized, narrowly scoped
privileged enrollment service. It must never call root shell commands,
accept arbitrary paths/accounts, or store a password in argv, environment,
CI firmware, browser data, shell history, logs, general telemetry, or
AI memory. Credential entry and confirmation must remain local and
non-echoing and use the owner's chosen secret, not a shared default.

## Native state mount provenance hardening (host-validated)

The **pre-install QEMU-only native** credential path now rejects an
arbitrary ext4 mount at `/var`. Both the Rust Argon2id
enroll/verify check and the C graphical first-user enrollment gate
require the explicitly pinned developer state device
`/dev/vdb`, a **writable** ext4 `/var` mount, and exactly one
unambiguous `/var` entry. Duplicate mount records, wrong devices,
read-only mounts, missing flags and filesystems other than ext4
must fail closed. The GUI must not claim first-user enrollment
is possible while the verifier would reject the state origin.

This is verified by real Rust and C host security tests at
[0bad15c](../validation/native-credential-mount-0bad15c/README.md):
7 Rust credential unit tests, 3 protected CLI integration tests,
and the graphical C enrollment gate all passed. It is **not**
a QEMU guest login or installed-system persistence test.

`/dev/vdb` is intentionally a **temporary QEMU fixture identity**.
A real physical installer will require an entirely separate,
owner-authorized stable PARTUUID/device-provenance design rather than
guessing that a block-device name is trustworthy on physical hardware.
Do not relax the source check to accept all ext4 mounts as a shortcut.
  
## Stronger cold-reboot negative-login gate (development)

The source-built **native** framebuffer first-user harness already supports
a one-time local, masked password creation and confirmation on an isolated
QEMU ext4 state file, followed by a second independent QEMU process using
the same state file. A prior harness checked that the saved password
opened a session after restart but did **not** challenge the verifier
with a deliberately wrong password first.

The revised `tests/smoke-graphical-onboard.py` now requires the
**second cold boot** to show its locked login screen, denies a
different 128-bit test password without launching an authenticated
session, emits `SYNVEIL_GRAPHICAL_AUTH_DENIED` only after the
secure graphical input has been reacquired, then accepts the original
credential and proves a non-root graphical session. Attempts that
wrongly authenticate, fail to report denial, re-enter enrollment,
accept a wrong confirmation at first boot or deny the genuine password
are all test failures. Host fixtures model the important negative
cases without privileged devices.

The dedicated
[`native-owner-enrollment.yml`](../../.github/workflows/native-owner-enrollment.yml)
builds source-native boot files only in CI and exercises the actual
QEMU guest twice when explicitly opted into an expensive build.
The guest proof receipt now records
`wrong_password_denied_after_cold_reboot: true` **only after** the
real guest smoke passes; `tools/auth_vm_bundle.py` refuses an
authenticated VM preview whose receipt lacks that flag. An old
two-boot receipt can no longer silently bypass the new denial
requirement.

This is a stronger **developer VM admission test**, not an owner-ready
desktop. Native framebuffer login is a separate component from the
Debian/XFCE live desktop; a passing native test cannot be represented
as an XFCE Argon2id integration. Real installation, durable enrollment
recovery, unattended A/B updates and physical hardware checks remain
open. The native full-image CI run must complete successfully at the
actual tested revision before its outcome is called verified.

## Acceptance gates before a hands-on installation

1. **Pre-enrollment:** a new image boots to a protected local setup
   screen, has no default user password, cannot start an ordinary
   non-root desktop, and cannot access an unauthenticated developer root
   shell. Missing or corrupt state must fail closed while preserving
   a separately authenticated recovery route.
2. **Enrollment:** require locally entered and confirmed matching
   password, enforce bounded length, protect intermediate buffers,
   refuse double enrollment, and durably commit a single root-private
   credential record only after validated state-media ownership and
   mounting. The GUI must not gain permanent root capability.
3. **Login:** refuse empty/incorrect passwords; correct credential
   opens exactly one non-root desktop session through the verified
   single authority. No credential may leak through logs or virtual
   firmware test channels.
4. **Logout/revocation:** normal logout, GUI crash and seat-broker
   death terminate the user's session and return the display to locked
   state. No background terminal remains usable. Repeated wrong
   passwords cannot bypass account rate controls.
5. **Cold reboot:** credentials and the user's home survive at least
   two separate QEMU boots, with no automatic session restoration or
   second enrollment after reboot. Corrupt/auth-missing state fails
   closed.
6. **Recovery and updates:** an independent authenticated recovery path,
   bootable A/B system slots and signed inactive-slot update/rollback
   must pass file-only media tests before any physical-device path is
   exposed.
7. **Physical release:** validate the full actual owner machine boot
   path—including UEFI, storage, networking, display/input, NVIDIA
   fallback and ability to boot back to existing Windows—only after
   the owner explicitly authorizes external test media. No unattended
   tool is permitted to repartition or install onto Windows.

## What does *not* satisfy these gates

A LightDM screenshot, a host source test, a preconfigured Debian live
user, a file-backed ext4 home alone, a `fw_cfg` test-only password, a
native enrollment library compiled in isolation, or an unformatted
A/B partition layout. Those are development evidence, not a secure,
persistent first-user installed operating system.

Keep implementation on `development/first-boot-desktop` and require
completed QEMU runtime evidence tied to exact commits. Do not publish
or flash the experimental test ISO as an owner-ready Synveil image.
