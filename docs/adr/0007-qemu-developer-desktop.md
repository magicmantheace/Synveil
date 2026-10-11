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
This is a corrected hypothesis until **actual QEMU framebuffer and menu
startup smoke tests** pass on the new commit.

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
interfaces still require explicit opt-in. User settings, on-disk profile
state, sessions, and display/input management are separate milestones.
