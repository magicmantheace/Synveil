# First test install contract

The project owner prefers to wait for a basic desktop before their first
hands-on test install. Automated QEMU validation continues during development.

## Dependency order

1. Finish deterministic core recovery and define its service lifecycle.
2. Add persistent writable storage, mount/boot behavior, and a documented
   installation path. Validate file survival across reboot before user testing.
3. Enable the display/input devices and firmware required by the chosen test
   target, plus a usable networking configuration path.
4. Select a small graphical stack and build a basic desktop session with a
   terminal, launcher, and basic display/input/network settings. Record that
   choice in an ADR before making it an architectural dependency.
5. Validate the desktop in a VM, console fallback when graphics fail, reboot
   persistence, and access to core status. Publish a checksummed image with
   installation and test instructions for the supported target.

The initial desktop does not depend on a local model or cloud inference. Full
optimization, personalization, package generations, and the polished desktop
remain later work. Essential filesystem and boot recovery behavior must exist
before an installation path is presented for user testing.

## QEMU graphical hardware foundation

A small source-built C framebuffer diagnostic can be included with
`SYNVEIL_GRAPHICS_ROOTFS=1`. The first QEMU graphics-enabled guest run exposed a missing `/dev/fb0` despite
having DRM and virtio GPU enabled. Inspection of its archived kernel config
showed `CONFIG_FB_DEVICE` disabled; Synveil now explicitly enables it and
checks the resolved config before compilation. Guest revalidation is still
required.

When `/dev/fb0` is available and writable, it
draws a diagnostic splash and emits `SYNVEIL_DISPLAY_READY` on serial;
otherwise it reports `SYNVEIL_DISPLAY_UNAVAILABLE` while preserving recovery.
The same opt-in image includes a source-built `synveil-input --probe`
helper. It scans only evdev character devices and verifies keyboard and pointer
capabilities without reading private user input events. The QEMU graphics smoke
now requires input discovery too.

The read-only framebuffer launcher now includes a Terminal tile with
explicit confirmation, opening a disposable UID 1000 demo shell through a
fixed executable. The graphical launcher cannot request a root shell.
The separate root-debug mode is only reachable with deliberate privileged
serial-console flags. The demo account has no password login and must not
be mistaken for secure multi-user authentication. Its home is ephemeral
without the optional state disk. With the explicit QEMU ext4 `/var` volume,
the mount helper bind-mounts a UID 1000-owned private home from
`/var/lib/synveil/home/synveil` to `/home/synveil`, preserving its files
across reboot. The mount helper now rejects symlinked home/state paths
and requires a root-owned parent before changing UID ownership. A failed
bind, suspect directory, or restrictive remount leaves serial recovery
available and cannot silently claim durable home storage. Directory
hardening passed [full native guest validation at 8c4290f](validation/native-8c4290f/README.md).
A dedicated guest test now requires a VT1 shell with UID/GID 1000,
`NoNewPrivs=1` and `/dev/tty1` stdin before claiming the demo terminal
works. That exact UID 1000/no-new-privileges/tty1 guest check passed in the [d875da39 full native QEMU run](validation/native-d875da39/README.md). This is not a secure authenticated production login.

A source-built `synveil-vt` tool now implements an actual kernel VT1
terminal for the VM framebuffer. It is deliberately **not boot-launched**:
its unauthenticated root shell requires the local developer to pass two
explicit flags from the already privileged serial recovery shell. It is
unsuitable for a user install until there is login/authentication and a
non-root graphical session. A fast host self-test checks the CLI boundary;
the guest must expose real framebuffer and VT device nodes.

The graphical menu now optionally autostarts from a fixed build-time
`/etc/synveil/desktop-autostart` marker. Its startup marker is produced only
after framebuffer mapping and keyboard discovery. It runs under a three-attempt supervisor alongside the serial
console. Crash restarts have a bounded budget; a deliberate menu exit
stops cleanly and a missing or failed display cannot remove recovery. The build
option is off by default, and the first user's installation still requires
more than this provisional session.

A manually started `synveil-display --menu` session now provides a
read-only framebuffer launcher prototype. Arrow keys select Status, Storage
and Network views; Enter opens a view, Escape goes back or exits. An attached
QEMU USB tablet also supplies absolute pointer motion and left-click
navigation; missing mouse input does not block keyboard recovery.
The menu now has Status, Storage, Network, Terminal and Settings. Its
read-only panels report actual local CPU/memory/uptime, state-volume free
space and network IPv4. Display contrast and pointer-size selections now
save in a private, versioned record under `/var/lib/synveil/` **only when**
the opt-in ext4 state disk is mounted. The file is atomically replaced and
synchronized, and invalid records revert to safe defaults. Without a state
volume, settings remain session-only. This is limited to two display choices:
it never stores authorization or AI policy. A two-process QEMU guest test now drives the real graphical Settings
screen through a private host QMP keyboard connection, verifies the launcher
saved its two bounded preferences, then requires the launcher to load those
preferences in a second QEMU process. The test also checks that a UID
1000-owned demo-home file survived. Those two-boot checks passed at
[f642d6f](validation/native-f642d6f/README.md); newer state-directory
hardening passed full QEMU validation at 8c4290f. Display modes, authenticated
login and a production network UI remain open.
No AI can request or authorize actions from this menu. The serial recovery
shell stays independent.

This is a **graphics/input bring-up and menu prototype**, not a graphical desktop or window
manager. The [810f66d guest run](validation/native-810f66d/README.md) did validate
`SYNVEIL_DISPLAY_READY`, `SYNVEIL_INPUT_READY` and `SYNVEIL_VT_READY`.
The desktop menu also emitted its readiness marker, but the marker followed
the serial prompt without a newline and failed the strict smoke assertion.
The [d875da39 complete native-image run](validation/native-d875da39/README.md)
subsequently passed desktop readiness, framebuffer/input/VT, non-root
demo-terminal, DHCP and two-boot QEMU state checks. Changes committed
after d875da39 still require their own validation.

The development boot launcher can explicitly opt in to a virtio VGA device,
USB keyboard/tablet and user-mode networking. The kernel configuration now
requests the matching DRM/input drivers. A host-side test verifies option
selection and ensures that the normal serial CI path stays headless and offline.
The controlled QEMU virtual graphics/input hardware and developer menu
passed full guest smoke checks at d875da39; hardware outside QEMU
and the cross-reboot settings/home checks passed at f642d6f. Newer
state-hardening and the desktop crash-restart guest test remain pending.
A separate opt-in
`SYNVEIL_NETWORK_ROOTFS=1` installs a bounded BusyBox DHCP helper and strict
IPv4 lease callback for QEMU `eth0`. QEMU guest DHCP passed at
[f642d6f](validation/native-f642d6f/README.md); this is not a production
network manager.

## Private VM packaging prerequisite

`bash build.sh vm-bundle` now packages an already-built QEMU EFI image with
a separate preformatted ext4 state disk, read-only boot image, checksums,
build identity, and a launcher. It refuses incomplete desktop builds, stale
or dirty build manifests, invalid kernels, or existing output destinations.
The launcher revalidates immutable checksums and private state-disk
permissions before every QEMU process starts. These are not signatures.
This enables a controlled, **developer-only VM setup** once the full guest
image passes validation; it is not a physical installer, user release, or
authenticated graphical login. Packaging now explicitly rejects both
`auth-desktop` and `developer-serial-recovery` rootfs markers, including
symlinked markers, so an authenticated preview or deliberate root-serial
override cannot be mistaken for the ordinary demo VM bundle. The bundle
checker also checks the build manifest's SHA-256 for the **embedded newc
initramfs** and inspects its archive entries: the kernel input must contain
the developer console/menu components but neither forbidden marker. A clean
staging tree alone is insufficient.

## Controlled QEMU persistence prototype

`SYNVEIL_PERSISTENCE_ROOTFS=1` optionally includes a mount helper for a
separately preformatted ext4 state image. The bootstrap OS stays in its
embedded initramfs, while `/var` can survive separate QEMU boots. The [2e1e54c QEMU persistence test](validation/persistence-2e1e54c/README.md)
passed write -> stop first QEMU -> start new QEMU -> read. This validates
the experimental VM /var state volume, not installed-system persistence. This mechanism deliberately does not pick or format
hardware disks, and it is not an installation path.

## Authentication preview serial recovery boundary

An authenticated-preview initramfs now **refuses to open an unauthenticated
serial root shell** by default. The check runs both after normal startup and
when mandatory early procfs/sysfs mounts fail. A failed GUI or seat broker
does not change that decision; a locked serial port remains visible for
diagnostics but never reads shell commands.

Developer-only QEMU enrollment/guest tests deliberately stage a second,
rootfs-owned `/etc/synveil/developer-serial-recovery` marker alongside
`auth-desktop`. This allows the synthetic one-time test password to be
enrolled through a trusted fixture shell. In a production-oriented preview,
the override is **not installed** and there is no serial command shell.

`SYNVEIL_DEV_SERIAL_ROOTFS=1` is an explicitly unsafe, developer-only
build option requiring `SYNVEIL_AUTH_DESKTOP_ROOTFS=1`.
Do not enable it in a physical installer or user-facing image. Disabling
developer serial recovery before implementing graphical first-user
enrollment means a fresh image still has **no usable onboarding path**;
Synveil is not ready for a physical installation.

## Experimental seat-backed graphical login preview

The framebuffer binary now has a separate, explicitly invoked `--login`
screen that displays masked password-length indicators, never plaintext.
Its password input currently uses a **US keyboard-only** evdev mapping
with separate Shift keys, Caps Lock indication, common punctuation and
numeric-keypad characters. The fixed private buffer accepts up to **1,024
bytes**, matching the pinned Argon2id verifier's password bound, and the
graphical client encodes the length as a two-byte network-order field. It sends a fixed password frame to the root-owned
`veil-seat` socket, then keeps that connection alive during the fixed
non-root VT session. A closed/crashed graphical client must end the lease
and relock the seat; neither a status tile nor an AI planner grants login.

To experiment on a developer-only build, `SYNVEIL_AUTH_DESKTOP_ROOTFS=1`
selects `--login` rather than the unauthenticated developer menu. This
requires all of `SYNVEIL_NATIVE_ROOTFS=1`,
`SYNVEIL_GRAPHICS_ROOTFS=1`, `SYNVEIL_PERSISTENCE_ROOTFS=1` and
`SYNVEIL_DESKTOP_AUTOSTART=1`, and installs a rootfs-owned selection
marker. It does **not** enroll a password. Without an operator-enrolled,
protected state volume the screen remains locked. The default developer image
continues to exercise the diagnostic menu. A rootfs-selected authenticated
preview locks its ordinary root serial shell unless the deliberately unsafe
developer-recovery marker is installed. No complete native QEMU run has yet
validated the latest graphical-login and serial-lock changes.

This is a controlled prototype, **not a secure physical installer or
production display manager**: the graphical input/display code still runs
as root; the developer build still exposes an unauthenticated serial root
recovery console, while the authenticated preview intentionally disables it
and consequently lacks a safe first-user enrollment or recovery process.
Comprehensive international keyboard/IME, clipboard, multiwindow compositor,
encrypted disk, signed boot and account onboarding are not implemented.

## Decisions still open

- Minimal compositor/session stack and its source-build dependencies.
- First supported test target (VM and/or specific physical hardware).
- Persistent filesystem layout and installation mechanism.

These decisions should follow implementation evidence and the target's needs.
No installer, desktop, hardware compatibility, or persistent rootfs is currently
validated by the native-image status smoke test.

## Live core status in the developer launcher

The Status tile now sends a fixed, read-only `synveil.core/v1` status
request over the root-owned core Unix socket. A leftover socket pathname,
failed connect, malformed/wrong-version response or hung service reports
`CORE UNAVAILABLE` rather than claiming the service is online. The
nonblocking probe has a single 350 ms monotonic deadline, is not a shell or
AI command path, and does not grant authentication authority. The host
fixture (`tests/core-health.py`) simulates partial, invalid and stalled
peers. Native guest validation of this updated framebuffer image remains
outstanding; do not confuse the diagnostic menu with a desktop compositor.

## Read-only network diagnostics in the developer launcher

The Network page now reads the fixed QEMU `eth0` interface state,
assigned IPv4 address, kernel default route and first IPv4 DNS resolver.
Missing or invalid data is displayed as unavailable/not assigned rather
than inferred from the presence of a device node. The snapshot is
read-only, bounded when reading procfs/resolver records, and does not
manage DHCP or change routes. The offline host fixture
`tests/network-diag.py` covers gateway endianness, bad route flags,
missing interface and invalid DNS input. A guest rebuild and QEMU check
are still needed to validate the new framebuffer display in its VM.

## Offline release-gate state isolation

The opt-in combined native QEMU guest runner now creates private disposable
ext4 images for seat-crash authentication and independent two-boot state/home
checks. It ignores any operator-provided `SYNVEIL_QEMU_STATE_DISK` path,
so this developer test harness cannot silently reuse an owner's credential
disk. A clean image-manifest match and every guest scenario passing remain
mandatory before promotion. This does not replace a supported installer or
account-enrollment process; no owner test is requested yet.

## Wired network bring-up in the developer target

The opt-in DHCP helper now selects a present, safely named non-loopback
network interface from sysfs, preferring one with carrier and otherwise
falling back to an installed adapter. The callback applies address,
default route and DNS to that bounded interface, rather than assuming
that Linux named it `eth0`. Host fixtures cover another conventional
wired name and reject unsafe names before mutation. This is limited
developer DHCP plumbing, not an interactive production network manager:
Wi-Fi, static IP, link selection UI, multi-homed route policy and
physical-hardware driver support remain open.

## Authenticated graphical user dashboard

The opt-in authentication preview now starts a fixed, non-root UID1000
framebuffer dashboard through the root seat's one-time inherited framebuffer
fd. Its terminal is also non-root, and Esc on the top-level menu ends the
session and returns to graphical login. Its view and settings reuse the
existing diagnostic renderer but do not carry privileged mutation powers.
The prototype initially launched keyboard-only; optional QEMU absolute
tablet input was added subsequently. General pointing-device support,
app windows, secure recovery/onboarding, installed storage, and actual QEMU
validation still block first installation.

## Authenticated keyboard and tablet session

The experimental authenticated user desktop supports keyboard navigation
through tty1 and, when available, an absolute Linux evdev pointer delegated
by the root VT helper. It reuses the existing five-page framebuffer
launcher, with non-root terminal execution and user-private preferences.
The root-owned login UI is separate and remains responsible for password
capture. Device nodes are not chmodded or exposed to the user account,
and no generic privileged command channel is added. Host C compilation
and QEMU login/pointer/logout integration checks remain to be executed.

## Masked local first-user setup prototype (October 2026)

A clean authenticated boot image can now offer a two-stage masked Create
Password / Retype Password screen on VT2, **only** when its separate /var
filesystem is mounted, the root-private state directory is safe and no
credential exists. The immutable auth-desktop marker is required and the
unsafe developer-serial-recovery marker must be absent. A direct invocation
outside those conditions must remain locked. Passwords are 8–1024 bytes,
compared over fixed-length slots, and wiped before returning to ordinary
login. The privileged operation is a bounded, fixed execution of
/usr/sbin/veil-enroll --password-stdin using a private socketpair, never
a shell or an arbitrary user-supplied executable. The Rust utility still
rejects existing records, symlinks and nonprivate state and writes the
Argon2id record once.

The new isolated 'graphical-onboard' QEMU fixture has **no developer serial
shell**. It drives both masked entry prompts and graphical authentication,
then boots an entirely new QEMU process using the same disposable state disk
and requires ordinary login (not another enrollment). It also insists on
locked serial on both boots. Host-only fake-QMP fixtures reject early exit,
missing confirmation, re-enrollment on the second boot, bad reboot login and
serial-root bypass. Passing those host tests does not constitute system
validation; actual full native QEMU results are required.

This removes one developer-shell dependency for first-user creation, but
**does not authorize a physical installation**. Missing pieces include an
independent secure recovery mechanism, persistent installed rootfs,
hardware support and a production-quality input/compositor design.


## Isolated authenticated QEMU preview packaging

The `auth-vm-bundle` build target accepts only the locked-serial
`graphical-onboard` scenario with a matching successful two-boot QEMU
receipt. It verifies the build revision, all three guest artifact hashes,
the root-owned immutable authentication markers and absence of any
developer serial-recovery marker inside the actual initramfs.
The resulting bundle has a new private ext4 state disk and an offline
GTK launch script; synthetic CI passwords and CI state are not shipped.

This is a reproducible, guarded virtual demonstration path.
It is not a physical installation, recovery entry, signed system
or full multiwindow desktop. First physical installation remains blocked
by persistent root filesystem layout, hardware driver breadth,
owner recovery, secure disk identity and installer validation.

## Kernel stall information on the provisional Status page

The QEMU framebuffer Status page now shows CPU, memory and I/O
10-second Linux PSI `some` stall percentages in tenths, read from
`/proc/pressure/{cpu,memory,io}`. These are wait/stall signals, **not**
utilization or throughput. A bounded non-root-compatible C reader rejects
bad kernel records, overlong lines and symlinks and reports unavailable
instead of inventing zero values. Host parser/build tests precede guest
display validation; this does not turn the framebuffer menu into a full
compositor or authorize AI-driven resource management.
