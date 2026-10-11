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
be mistaken for secure multi-user authentication. Its home is ephemeral.
A dedicated guest test now requires a VT1 shell with UID/GID 1000,
`NoNewPrivs=1` and `/dev/tty1` stdin before claiming the demo terminal
works. That test is implemented but not yet validated in QEMU.

A source-built `synveil-vt` tool now implements an actual kernel VT1
terminal for the VM framebuffer. It is deliberately **not boot-launched**:
its unauthenticated root shell requires the local developer to pass two
explicit flags from the already privileged serial recovery shell. It is
unsuitable for a user install until there is login/authentication and a
non-root graphical session. A fast host self-test checks the CLI boundary;
the guest must expose real framebuffer and VT device nodes.

The graphical menu now optionally autostarts from a fixed build-time
`/etc/synveil/desktop-autostart` marker. Its startup marker is produced only
after framebuffer mapping and keyboard discovery. It runs alongside the serial
console, so a missing or failed display cannot remove recovery. The build
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
it never stores authorization or AI policy. Guest reboot validation for
these preferences is pending. Display modes, authenticated login and a
production network UI remain open.
No AI can request or authorize actions from this menu. The serial recovery
shell stays independent.

This is a **graphics/input bring-up and menu prototype**, not a graphical desktop or window
manager. The [810f66d guest run](validation/native-810f66d/README.md) did validate
`SYNVEIL_DISPLAY_READY`, `SYNVEIL_INPUT_READY` and `SYNVEIL_VT_READY`.
The desktop menu also emitted its readiness marker, but the marker followed
the serial prompt without a newline and failed the strict smoke assertion.
The source has since added the missing newline; a new native-image run must
still validate that fix and the demo-terminal launch.

The development boot launcher can explicitly opt in to a virtio VGA device,
USB keyboard/tablet and user-mode networking. The kernel configuration now
requests the matching DRM/input drivers. A host-side test verifies option
selection and ensures that the normal serial CI path stays headless and offline.
This is only a VM hardware configuration: graphical session, user settings,
and real driver readiness remain unvalidated. A separate opt-in
`SYNVEIL_NETWORK_ROOTFS=1` installs a bounded BusyBox DHCP helper and strict
IPv4 lease callback for QEMU `eth0`. Live QEMU DHCP validation remains
required; this is not a production network manager.

## Private VM packaging prerequisite

`bash build.sh vm-bundle` now packages an already-built QEMU EFI image with
a separate preformatted ext4 state disk, read-only boot image, checksums,
build identity, and a launcher. It refuses incomplete desktop builds, stale
or dirty build manifests, invalid kernels, or existing output destinations.
This enables a controlled, **developer-only VM setup** once the full guest
image passes validation; it is not a physical installer, user release, or
authenticated graphical login.

## Controlled QEMU persistence prototype

`SYNVEIL_PERSISTENCE_ROOTFS=1` optionally includes a mount helper for a
separately preformatted ext4 state image. The bootstrap OS stays in its
embedded initramfs, while `/var` can survive separate QEMU boots. The [2e1e54c QEMU persistence test](validation/persistence-2e1e54c/README.md)
passed write -> stop first QEMU -> start new QEMU -> read. This validates
the experimental VM /var state volume, not installed-system persistence. This mechanism deliberately does not pick or format
hardware disks, and it is not an installation path.

## Decisions still open

- Minimal compositor/session stack and its source-build dependencies.
- First supported test target (VM and/or specific physical hardware).
- Persistent filesystem layout and installation mechanism.

These decisions should follow implementation evidence and the target's needs.
No installer, desktop, hardware compatibility, or persistent rootfs is currently
validated by the native-image status smoke test.
