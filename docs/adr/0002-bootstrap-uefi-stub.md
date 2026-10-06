# ADR 0002: Boot Phase 1 Directly Through the Linux EFI Stub

- Status: Accepted
- Date: 2026-10-06
- Supersedes: none
- Superseded by: none

## Context

Phase 1 needs a bootable x86_64 UEFI image in QEMU. Introducing GRUB, systemd-boot, Limine, or another boot manager at this stage would add another privileged dependency before Synveil has proved its own userspace bootstrap.

The Linux x86 kernel can be built with EFI-stub support and presented directly to UEFI firmware as a PE/COFF executable. An initramfs can be embedded into that kernel.

## Decision

For the Phase 1 QEMU bootstrap, Synveil will:

1. build Linux with `CONFIG_EFI_STUB=y`;
2. embed the generated bootstrap root filesystem as the kernel initramfs;
3. place the resulting kernel at the UEFI removable-media fallback path `EFI/BOOT/BOOTX64.EFI` on a FAT ESP image;
4. boot that image using OVMF in QEMU.

This is a bootstrap decision only. The final physical-install boot manager remains an open architectural question.

## Rationale

This path:

- minimizes the number of components required for the first boot;
- keeps the first boot chain close to upstream Linux and UEFI;
- avoids allowing boot-manager design to block work on Synveil's defining control plane;
- still exercises the UEFI path that physical x86_64 hardware will eventually use.

## Alternatives considered

### GRUB

Mature and flexible, but adds a substantial source/build/configuration dependency before boot menus are needed.

### systemd-boot

Simple for UEFI systems, but would introduce systemd build dependencies while Synveil has not chosen systemd as its userspace/service architecture.

### Limine

Modern and capable, but still an unnecessary additional component for the first QEMU shell boot.

### Legacy BIOS/direct QEMU kernel loading

Simpler for testing, but would avoid the UEFI boot path explicitly selected for Synveil's first platform.

## Consequences

### Positive

- No separate Phase 1 bootloader source dependency.
- Smaller bootstrap attack and build surface.
- The generated disk image has a straightforward ESP layout.

### Negative / tradeoffs

- No boot menu or generation selection.
- Embedded initramfs means a rootfs change requires rebuilding the kernel for this stage.
- Recovery entries and multi-kernel management are deferred.

## Security implications

Reducing early boot components reduces bootstrap complexity. This does not provide verified boot by itself; signing and Secure Boot remain future work.

## Reversibility

High. A later boot manager can load the same or successor kernels without changing Synveil's userspace architecture.

## Validation

The QEMU smoke test must reach the bootstrap `/init` through OVMF and observe the serial marker `SYNVEIL_BOOT_OK`.
