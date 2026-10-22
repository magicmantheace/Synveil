# Weston native desktop integration prerequisite

Status: Experimental HEADLESS PROTOCOL PREFLIGHT ONLY. 2026-10-22.

Synveil currently boots a native authenticated framebuffer launcher but not
a real multiwindow compositor. The separate Debian Xfce live image has
multiwindow runtime proof but is not the native system.

The test-host-only Weston/Pixman proof can be run without root privileges:

    python3 tests/weston-headless.py
    python3 tools/weston_headless_probe.py --timeout 45

This starts Weston with the headless backend and desktop shell, then
requires two different live Wayland application processes (weston-terminal
and weston-simple-shm) to each negotiate configured xdg_toplevel windows.
It rejects an empty socket, short-lived app or unconfigured window, isolates
the server socket in a private 0700 directory, and cleans all children.

The CI workflow installs Weston on the disposable runner only and records
the package version in its logs. Neither these Debian/Ubuntu packages nor
a Debian root filesystem are added to the native Synveil image.

A passed test proves *protocol-level* multiwindow support by the proposed
compositor. It does NOT prove RGB pixels, native UEFI boot, KMS/DRM,
keyboard/mouse input, graphical authentication, session revoke, or
physical compatibility.

Required next: source-pin and cross-build Weston and all dependency
libraries for the native Synveil sysroot; adapt the single Argon2id
veil-seat authority to revocable, non-root DRM/input access without
relaxing its VT and crash cleanup; then test an actual visible multiwindow
native UEFI/QEMU session with terminal, file manager and settings.
Separate signed A/B installation and physical owner tests follow.
See docs/adr/0013-first-boot-desktop-priority.md. Do not mark M1 complete.
