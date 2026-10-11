# ADR 0006: Opt-in QEMU Display/Input Hardware for First Desktop

- Status: Accepted (VM bootstrap only)
- Date: 2026-10-11
- Supersedes: none
- Superseded by: none

## Context

The first test install requires a basic graphical desktop. The current VM
image tests only a serial recovery console. The VM must expose a deterministic
virtual GPU, keyboard and pointer before a source-built compositor can be
validated.

## Decision

- Retain the serial-only, offline QEMU default used by CI.
- Allow explicit `SYNVEIL_QEMU_DISPLAY=gtk|sdl` opt-in.
- Add a QEMU 2D virtio-vga device and a USB keyboard/tablet on an xHCI
  controller only when a GUI display is requested.
- Allow optional QEMU user-mode virtio networking through
  `SYNVEIL_QEMU_NETWORK=1`; do not enable it by default.
- Compile the requisite Linux DRM, virtio GPU, console, evdev and USB HID
  support into the bootable bootstrap kernel. Serial recovery remains.
- Defer compositor, user session, authentication, network setup and install
  target design. This ADR does not select a desktop toolkit.

## Security and constraints

Opt-in networking changes the VM's connectivity; it is not silently enabled.
No GPU acceleration or guest service is required for the deterministic core.
The supported first GUI target is a QEMU VM, not a promise of physical
GPU support.

## Validation

Host CLI tests verify default isolation, correct optional device selection,
and rejection of invalid mode values. Guest validation must separately prove
DRM/input device readiness, compositor startup and console fallback before
checking the desktop milestone complete.
