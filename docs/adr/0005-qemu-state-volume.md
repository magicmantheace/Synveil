# ADR 0005: Opt-in QEMU Ext4 State Volume for Bootstrap

- Status: Accepted (QEMU developer prototype only)
- Date: 2026-10-11
- Supersedes: none
- Superseded by: ADR 0017 (device identity only; the volume's purpose,
  host-side creation, and fail-soft recovery behavior stand)

## Context

The Phase 1 EFI-stub kernel embeds the entire immutable bootstrap rootfs.
A writable root and a physical installer are not yet available. Before desktop
work, guest services need a safe way to retain state across separate boots.

## Decision

1. Preserve the existing read-only UEFI/GPT boot image and embedded initramfs.
2. Allow an **explicitly opted-in** developer rootfs to mount a preformatted
   ext4 disk as `/var` at bootstrap.
3. Attach that volume only as a second QEMU virtio block disk, at `/dev/vdb`.
4. Create the disk exclusively on the *build host*; never reformat guest disks
   on boot. Refuse to overwrite any existing image or symlink.
5. Missing/broken storage never prevents recovery console access. Emit an
   explicit failure marker and do not claim persistence.
6. Demonstrate a write, a terminated first guest session, and a read after a
   separate second guest boot before claiming runtime validation.

This is intentionally a disposable test-environment device assumption, not a
safe general-purpose disk selection or an installer. The installed-system
filesystem layout, mount identities, encryption, and rollback remain open.

## Tradeoffs

It gives isolated durable state without entangling early system bring-up with
partitioning or an initramfs-to-root handoff. The image remains a bootstrap
console, not a full installed Linux distribution. Host-created ext4 is a
development test volume; user data and audit journals are not yet supported.

The mount uses `nosuid,nodev,noatime` and the initial persistent service
directories are root-owned. A proper production boot must identify volumes by
trusted identity, handle fsck/recovery, and specify update/rollback semantics.

## Validation

Static tests check exclusive creation, symlink refusal, permissions, bounds
and cleanup on formatting failure. A two-process QEMU smoke check must observe
`SYNVEIL_STATE_READY`, write and sync a probe under `/var/lib/synveil`, stop
the guest, then boot a new process and read the same bytes. CI evidence is
required before the milestone can be checked complete.
