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
