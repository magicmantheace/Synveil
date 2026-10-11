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
and Network views; Enter opens a view, Escape goes back or exits.
It intentionally does not run a graphical terminal or perform privileged
changes. The serial recovery shell stays independent.

This is a **graphics/input bring-up and menu prototype**, not a graphical desktop or window
manager. Automated QEMU validation is required before declaring the graphics
device path working.

The development boot launcher can explicitly opt in to a virtio VGA device,
USB keyboard/tablet and user-mode networking. The kernel configuration now
requests the matching DRM/input drivers. A host-side test verifies option
selection and ensures that the normal serial CI path stays headless and offline.
This is only a VM hardware configuration: graphical session, user settings,
and real driver readiness remain unvalidated. A separate opt-in
`SYNVEIL_NETWORK_ROOTFS=1` installs a bounded BusyBox DHCP helper and strict
IPv4 lease callback for QEMU `eth0`. Live QEMU DHCP validation remains
required; this is not a production network manager.

## Controlled QEMU persistence prototype

`SYNVEIL_PERSISTENCE_ROOTFS=1` optionally includes a mount helper for a
separately preformatted ext4 state image. The bootstrap OS stays in its
embedded initramfs, while `/var` can survive separate QEMU boots. A guest
must prove write -> shutdown -> new QEMU process -> read before we mark
persistence validated. This mechanism deliberately does not pick or format
hardware disks, and it is not an installation path.

## Decisions still open

- Minimal compositor/session stack and its source-build dependencies.
- First supported test target (VM and/or specific physical hardware).
- Persistent filesystem layout and installation mechanism.

These decisions should follow implementation evidence and the target's needs.
No installer, desktop, hardware compatibility, or persistent rootfs is currently
validated by the native-image status smoke test.
