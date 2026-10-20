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
