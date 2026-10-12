# ADR 0007: Minimal QEMU Direct-Framebuffer Developer Desktop

- Status: Accepted (first desktop development target only)
- Date: 2026-10-11
- Supersedes: none
- Superseded by: none

## Context

The owner requires a basic graphical desktop before their first hands-on
installation. The current source-built Linux bootstrap has no Wayland/X11
stack, installed users, or authentication. Its initial QEMU desktop can be
tested incrementally without committing to a production compositor.

## Decision

For the **developer QEMU target only**, retain a small source-built C
framebuffer application as a temporary single-app menu. The chosen guest
virtual hardware is QEMU virtio VGA plus USB HID keyboard and tablet. Build
and boot options remain explicit and off by default.

- The menu maps a validated 32-bit `/dev/fb0` surface and reads evdev input
  device capabilities. Keyboard and optional tablet mouse events navigate
  read-only Status, Storage and Network information.
- `SYNVEIL_DESKTOP_AUTOSTART=1` includes an immutable autostart marker
  in the image. The session runs in the background, independently of the
  recovery shell. Startup marker emission requires a framebuffer and keyboard.
- Disposable UID 1000 demo-terminal descendants now carry Linux parent-death
  guards across the graphical launcher and terminal helper. The guard is
  rearmed after dropping UID/GID (which clears the kernel setting); a
  crashing display cannot intentionally leave its demo shell behind.
  Host process-tree tests cover a killed launcher; QEMU retesting remains
  required for VT behavior.
- A **separate** explicitly confirmed `synveil-vt` command can activate a
  real Linux framebuffer virtual console and launch a developer root shell.
  It does not run automatically, does not enter the AI action plane, and is
  **not appropriate for user installations**.
- Early graphical debugging uses the kernel fbcon console for real terminal
  rendering rather than implementing a partial, unsafe PTY terminal emulator.
- A future authenticated, non-root graphical login and compositor must replace
  this developer-grade session before user-facing installation. No arbitrary
  command execution, privilege delegation, or shell control is exposed by the
  read-only menu.

The existing EFI stub, embedded initramfs, deterministic core and serial
recovery continue to function if the display or input stack fails.

## Requirements and validation

Linux 6.18's `CONFIG_DRM_FBDEV_EMULATION=y` does not by itself create a
`/dev/fb*` device: `CONFIG_FB_DEVICE=y` is also required. A prior QEMU
guest run showed `/dev/fb0` absent, and its archived kernel config confirmed
the missing option. The kernel builder now asserts the resolved Kconfig state.
The [810f66d native guest run](../validation/native-810f66d/README.md)
confirmed framebuffer, virtual input and VT readiness markers; it also
reported the desktop readiness marker, but not on a standalone line.
Consequently the desktop smoke gate failed. The marker formatting is fixed
in a later revision but requires a repeat native-image validation.

Static/host tests cover framebuffer bounds, keyboard capability discovery,
tile-click mapping, keyboard navigation, virtual terminal CLI gating,
graphical boot markers and recovery independence. Guest smoke must exercise
DRM framebuffer creation, keyboard and pointer discovery, virtual terminal
availability, desktop startup, and independent core readiness.

## Deliberate non-goals

This is not a Wayland compositor, mouse-driven window manager, graphical
application launcher, authenticated terminal, secure display manager,
production installer, or certified hardware driver configuration. The root
debug shell remains a trusted local **developer VM** capability only. Network
interfaces still require explicit opt-in. The VM demo home can now be bind-mounted from a UID 1000-owned private
directory on the same explicit ext4 state disk. Missing or invalid mounts
must leave the independently running recovery shell usable. This mechanism
is not intended as a physical-system home filesystem layout.

The two simple display choices now use an opt-in versioned state record
only on the existing trusted QEMU `/var` ext4 volume. Writes replace an
owner-private file atomically, synchronize it, and reject invalid records;
a missing volume means ephemeral defaults, not a boot failure. No
authorization is inferred from saved display preferences. Host tests
validate corrupt records, refused symlinks and file permissions; on-device
cross-reboot tests remain outstanding. Broader profiles, authenticated user
sessions, and display/input management are separate milestones.
