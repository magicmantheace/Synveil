# ADR 0013: First-boot desktop and updateable installation priority

- Status: Accepted (owner directive; implementation incomplete)
- Date: 2026-10-20
- Related: ADR 0005, 0006, 0007, 0008, 0010, 0011, 0012

## Context and priority change

The owner requires the first hands-on Synveil experience to be a direct UEFI
boot into a usable graphical desktop on an HP Pavilion TG01 (i5-10400F,
NVIDIA RTX 3060, USB keyboard/mouse, UEFI), **without starting Windows**.
Subsequent Synveil releases must install without erasing persistent user data.

This directive supersedes the old ordering that deferred the desktop until
nonessential observer/AI milestones. Existing native core, security boundaries,
QEMU test tooling and validated work are retained. An existing framebuffer
menu or login dashboard is **not** a multiwindow desktop.

## Decisions

1. **Do not touch the Windows disk.** Initial work produces image *files*
   and disposable QEMU disks only. A user-owned removable USB or preferably
   dedicated external SSD is the eventual physical target. No implicit disk
   discovery or writing is permitted.
2. Preserve the existing QEMU kernel as the default. An explicit
   `SYNVEIL_BOOT_TARGET=hardware` selects a separate bring-up kernel profile
   for firmware-provided GOP/simpleDRM scanout, local virtual console,
   USB HID, USB storage, AHCI and NVMe. This is a *developer boot profile*,
   not evidence of RTX 3060 or HP Pavilion compatibility. An accelerated
   NVIDIA driver is not a first-boot requirement.
3. **Reuse an established compositor.** Prefer evaluating Weston with its
   existing desktop shell and software-rendering fallback for initial
   multiwindow/resize testing. Confirm Wayland desktop-shell, seat/auth,
   launcher, terminal, file manager, settings, panel and persistence before
   locking the exact stack. Do not advertise the existing raw framebuffer
   renderer as a compositor.
4. Plan the installed layout **before** any permanent installation:
   EFI System Partition; immutable Synveil system slot A; immutable system
   slot B; independently mounted persistent state/home; independent recovery.
   Bind boot selection to verified PARTUUIDs, boot image digests, and trusted
   release metadata. Merely computing or describing hashes is insufficient.
5. Whole-system updates will be downloaded, signature-verified, and written
   only to the inactive slot. Activation needs durable boot-attempt accounting,
   bounded health checks and automatic rollback, while preserving the confirmed
   slot. Manual rollback and upgrades require owner authorization.
6. A functioning, AI-independent recovery path is required. The experimental
   serial recovery override, unauthenticated developer menu, and default
   demo account must never be published as a consumer installer.
7. First physical boot testing and VM validation must be reported separately.
   Physical hardware validation requires the owner's direct participation.

## Delivery order and evidence

- **Stage 1:** UEFI build profile, EFI/GPT image, QEMU boot tests, documented
  firmware/graphics limitations, safely staged removable-media prototype.
- **Stage 2:** Real multiwindow compositor, graphical sign-in, unprivileged
  session, desktop applications and data persistence.
- **Stage 3:** Safe external-drive install, verified A/B layout, writable
  persistent home and independent recovery.
- **Stage 4:** Signed whole-system downloads, inactive-slot updates, boot health,
  interrupted-write recovery, downgrade policy, automatic/manual rollback.
- **Stage 5:** Graphical updates UI (status, progress, history, restart, rollback).
- **Stage 6:** Real HP Pavilion hardware compatibility work.

No Stage is complete based only on this ADR or on host unit tests.

## Compatibility/safety notes

EFI-stub images are currently unsigned. UEFI Secure Boot may reject them.
Do not change firmware Secure Boot settings or modify Windows boot settings
without first accounting for Windows recovery/BitLocker implications and
obtaining the owner's explicit authorization.

The current initramfs-based developer image is not yet a persistent installed
root filesystem. ADR 0011 remains a **read-only prototype**; it does not
authenticate disk contents, install updates, or implement a safe rollback.
The existing native source-build architecture and security invariants remain.
