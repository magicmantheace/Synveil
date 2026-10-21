# ADR 0014: Isolated real-desktop proof using upstream Xfce

- Status: Experimental (not an approved final Synveil base image)
- Date: 2026-10-20
- Related: ADR 0007, 0008, 0011, 0013

## Reason and scope

Synveil's pinned native bootstrap currently proves a Linux EFI-stub boot,
deterministic core, framebuffer login preview and recovery behavior in QEMU.
It contains **no X11/Wayland rendering libraries**, window manager, terminal
GUI toolkit, file manager, seat-aware display manager or package plumbing.
Implementing all of these source packages at once would delay exercising the
owner's critical desktop interaction requirements.

Build a **separate and explicitly experimental**, Debian Trixie live image
with the established Xfce/X11 desktop stack. This is a temporary functional
desktop proof, not a replacement for the Synveil native-core architecture.
The normal build.sh kernel, source locks, native binaries and QEMU tests
remain untouched.

Xfce's Xfwm4 manages multiple simultaneous movable/resizable windows;
Xfce Panel supplies a taskbar/launcher; Thunar supplies file management;
xfce4-terminal and xfce4-settings supply two required applications.
LightDM is the provisional display manager. Standard Debian kernel,
X.Org, Mesa/firmware and NetworkManager components make initial USB-device
and GPU-fallback testing feasible with proven upstream technologies.

ADR 0013's Wayland/Weston preference remains open: this X11 compositor
equivalent is intended to prove *desktop behavior*, not lock in a permanent
X11 stack. Licensing and reproducibility review are still required before
a distributable Synveil release.

## Real QEMU desktop acceptance harness (in development)

The developer ISO now includes a CI-only Xfce session autostart probe and
a bounded serial status bridge, plus the `smoke-desktop-live.py` host runner.
The guest probe must observe Xfwm4, launch **two different genuine app
windows** (xfce4-terminal and Thunar), move and resize, maximize, minimize
and restore a window, and then report a fixed success marker. The host boots
the ISO in QEMU through UEFI OVMF with an isolated private variable store,
reads the serial marker, captures actual RGB video through QMP, and requires
a nonblank screenshot. The runner uses a temporary guest only, networking
disabled, and no writable host device.

These are acceptance checks, not evidence of a passed desktop until a
**completed** GitHub Actions run exercises the exact committed ISO and
reports success. If the probe or renderer fails, the test remains failed;
source existence and ISO construction alone never satisfy it. The proof's
autologin/demo session and serial reporting must not be shipped in a
trusted physical installation; production auth and recovery are distinct.

## VM-only home persistence acceptance experiment (not owner auth)

The first two graphical QEMU cold boots at [437020d](../validation/desktop-live-437020d/README.md)
proved repeatable XFCE startup but intentionally attached no writable disk.
The next test retains the same read-only developer ISO and adds one
**newly created, fixed-name, 256 MiB regular file** from
`tools/create_desktop_home_image.py --create`. The unprivileged helper
allows only `out/desktop-live/home-persistence.img`, refuses existing
and symlinked paths, and uses file-only `mkfs.ext4 -d` to seed the
small root `persistence.conf` specifying **only `/home`**.

The kernel's VM preview opts into Debian live-boot's narrowly named
`persistence-label=synvmhome persistence-storage=filesystem`. The exact
label is intentionally distinct from generic `persistence` media, and
`-net none` plus the two file-only VM drives prevent host device
discovery. When the guest actually bind-mounts a separate `/home`,
its non-root XFCE session creates a fixed benign 0600 test file and
synchronizes it; a later independent QEMU process must read those same
bytes from the unchanged file-backed virtual drive. The serial reporter
emits separate allowlisted `HOME_WRITTEN` and `HOME_RECOVERED` markers.
The host must require both **in their respective boot**, in addition to
the actual window-management/graphical-framebuffer proof. The home image
is never uploaded with the CI evidence.

The first real four-guest validation **passed** at
[0579de0](../validation/desktop-home-0579de/README.md): the third
UEFI boot wrote the file to the specifically labeled home filesystem and
a fourth fresh QEMU process recovered it, alongside graphical interactions.
That exact evidence supports this isolated developer-VM milestone only.

This remains a developer-only **unauthenticated live account** and
unencrypted state. It is neither trusted user provisioning nor an
installed root filesystem. The prototype does not protect private data,
provide independent recovery or enable A/B update transactions.
Successful file retention would **not** satisfy the user-ready test
installer criterion. Any missing/incorrect guest marker must be
recorded as failed, not inferred from image construction.

The [Debian live manual](https://live-team.pages.debian.net/live-manual/html/live-manual/customizing-run-time-behaviours.en.html)
documents `persistence.conf` with `/home`; the
[live-boot parameters](https://sources.debian.org/src/live-boot/1%3A20250815/manpages/en/live-boot.7/)
describe the restrictive persistence label and filesystem-only scan.

## Separate locked LightDM sign-in experiment (VM-only)

The first developer XFCE live image booted successfully and retained a
separate non-root `/home` file across two QEMU processes. That image still
uses the disposable Debian live session and **does not authenticate**.

A second **opt-in** image configuration now uses
`SYNVEIL_DESKTOP_LOGIN_GATE=1 bash tools/desktop_live.sh configure`
in a fresh, disposable checkout. The locked-only profile uses Debian
`live-config.noautologin` on both standard and fallback boot entries.
It adds a pre-start gate to LightDM that requires the disposable `synveil`
account to exist and be **locked by system shadow tooling** before the
greeter service starts. The separate LightDM configuration disables guest
and automatic login. A bounded VM-only systemd observer reports when the
graphical greeter is running and no XFCE session or panel is active.

This is intentionally a **negative authentication test**. The profile has
no default password, no accepted owner password and no credential enrollment
path. Its QEMU host runner advances only the recognized Debian boot
menu, requires the bounded locked-greeter marker, sends a single Return
without entering a password, verifies a nonblank graphical framebuffer
that is no longer the Debian boot menu, and refuses an unexpected XFCE
session. Code or configuration presence alone does not validate the gate:
the full isolated `desktop-login-gate.yml` QEMU job must pass first.

Why separate images: adding a locked account to the normal integration
ISO would break the already-proven unattended graphical regression
tests, and adding a shared known test password to an ISO would create
an insecure default account. The negative profile keeps that tradeoff
explicit and does not weaken the existing native `veil-seat` /
Argon2id credential boundary from ADR 0008.

**Required before any owner-ready graphical sign-in claim:**
secure one-time user enrollment, no default or shared credentials,
password verification through PAM or audited native broker, login
denial then successful login with a fresh secret, locked state after
logout/reboot, session/process revocation, non-root privileges and
independent owner recovery. Prefer testing this in separate disposable
QEMU media before integrating it into the native Synveil boot path.
No automated image is released or written to physical storage.

See Debian's
[documented `live-config.noautologin` shortcut](https://manpages.debian.org/trixie/live-config-doc/live-config.7.en.html).

## Disposable, fresh-password PAM graphical integration test

The **locked greeter** pass at
[exact SHA 2fd381f](https://github.com/magicmantheace/Synveil/actions/runs/39614332033)
proves a real LightDM login screen with no usable password; it does not
prove a successful authenticated session.

The next isolated developer-only profile uses
`SYNVEIL_DESKTOP_LOGIN_GATE=2` in a **fresh image-build output tree**.
Both boot entries disable live-config autologin and LightDM guest sessions.
The ISO contains **no known, fixed, default or owner password**. Only the
opt-in QEMU runner generates a 128-bit random, lowercase hexadecimal
test secret. It injects the test value through QEMU's virtual `fw_cfg`
device on the process command line. A root-only systemd LightDM
`ExecStartPre` in the disposable guest validates its length/hex format,
uses Debian's `chpasswd` to set the temporary account password through
normal system credential tools, and never prints or archives it.

The test host must see the genuine LightDM/GTK greeter and no user session
before any password is typed. It submits a **different** random password
using the existing allowlisted QMP keyboard, verifies no authenticated
session started, then submits the host's original ephemeral password.
A separate guest observer must confirm both `xfce4-session` and the
XFCE panel are owned by the **non-root temporary user**, and the host
must independently capture a nonblank graphical scanout. Failure to
read the test-only secret, start LightDM, deny the wrong password or
establish a non-root session fails the run.

See the isolated
[desktop-pam-test workflow](../../.github/workflows/desktop-pam-test.yml).
Merely committing this code is **not runtime evidence**: a completed
QEMU success at the tested source revision is required before claiming
an actual working password-authenticated VM desktop.

**Security boundary:** `fw_cfg` is a test-only virtual channel,
**not a secure credential vault** and never appropriate for real user
secrets or a shipping installer. The password and account shadow state
are ephemeral; no installed credential, secure first-user wizard,
password reset, logout/relock, encrypted data, native seat-broker
integration or reboot proof follows from this test. Production
enrollment must use a separate secure path with recovery and human
authorization, never ship a test credential or enable developer
virtual hardware on physical computers.

## Threat model, boundaries and first-user gate

- A real desktop **does not equal** a trusted Synveil installation.
- Debian live-config may automatically enter the temporary 'synveil' user
  without authentication. The prototype disables ordinary live sudo/polkit
  elevation using `live-config.noroot`, but it is **not** an authenticated
  first-user onboarding system, and has no enrolled owner credentials.
- The experiment is VM/developer-only. Do not advertise, distribute, flash,
  or recommend it as the owner's first hands-on release.
- The live image deliberately has **no included installer**. Running the
  build command never selects, partitions, formats or overwrites a host disk.
- A successful ISO build does not establish functioning desktop login,
  graphics on RTX 3060, safe recovery, persistent user files, signed boot
  or immutable A/B updates.
- The existing Synveil security, owner enrollment and independent recovery
  requirements still apply to the eventual bootable desktop product.
- No root-level installer, permanent Linux system, repo credentials, or
  owner information is injected into the image.

## Implementation and validation path

The `experiments/desktop-live` inputs and `tools/desktop_live.sh` configure
a Debian live-build tree strictly beneath `out/desktop-live/`; they refuse
existing/symlinked output trees. Only a separate explicit privileged `build`
invocation can assemble an ISO. The image uses Debian stable packages and is
**not independently reproducible yet** without an immutable package snapshot.

Host tests use a fake `lb` process to verify image configuration, package
set, boot arguments, no-installer declaration, explicit-build separation,
symlink/overwrite refusals and non-root build refusal.

Next evidence gates:
1. Build an ISO under CI and verify its UEFI boot and content provenance.
2. Boot the ISO in QEMU, capture/display evidence, exercise more than one
   simultaneous graphical app, move/resize and restart the session.
3. Add trusted first-user enrollment and login before physical user testing.
4. Decide whether to integrate this stack into native Synveil or source-build
   a smaller retained compositor after measuring the evidence.
5. Design physical persistence/A-B installation independently before any
   external SSD install is offered.

The immutable A/B updater, Synveil native core integration and physical
installation remain separate incomplete milestones.
