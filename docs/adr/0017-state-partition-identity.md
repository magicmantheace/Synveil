# ADR 0017: State volume identity by GPT partition, not device name

- Status: Accepted
- Date: 2026-10-25
- Supersedes: the `/dev/vdb` device pin in ADR 0005 (decision 3), ADR 0008
  (onboarding mount contract), and ADR 0015 (enrollment state identity)
- Superseded by: none

## Context

ADR 0005 attached the developer state volume as the second QEMU virtio
disk and pinned every consumer to the device name `/dev/vdb`: the
initramfs mount helper, the Rust credential authority, and the graphical
enrollment helper. ADR 0015 already recorded that this was "intentionally
a temporary QEMU fixture identity", and ADR 0005 required that "a proper
production boot must identify volumes by trusted identity".

The pin makes physical installation impossible: writing the boot image
to an SSD does not create a `/dev/vdb`, and device names are not stable
across hardware anyway (the same disk can be `sda`, `vda`, or `nvme0n1`
depending on the machine and the bus). The joined authenticated Weston
image (one image that enrolls, logs in, and only then starts the
multiwindow desktop) needs state on storage the image itself provides.

## Decision

1. The state volume is a GPT partition with a fixed project identity:
   partition name `Synveil State` and PARTUUID
   `7c9e2d41-8b3a-4f5e-a6d1-2e8c4b7f9053`. It holds the same ext4
   filesystem (label `SYNVEIL_STATE`) as before.
2. Every consumer resolves the state device by that identity and fails
   closed otherwise; no consumer trusts a device name, and there is no
   fallback device:
   - `build/rootfs/mount-state` scans `/sys/class/block/*/uevent` and
     requires exactly one partition with the designated PARTUUID and
     partition name before mounting it at `/var`.
   - `veil-core`'s credential authority (`credential.rs`) requires a
     unique writable ext4 `/var` mount whose device resolves through
     `/sys/dev/block/<major>:<minor>/uevent` to the same identity.
   - The graphical enrollment helper (`enroll.c`) applies the same two
     checks before enrollment may start.
3. The identity works unchanged for the state partition inside the
   install image and for a separately attached partitioned state disk,
   under QEMU and on physical hardware. `tools/state_disk.py` now
   creates GPT-partitioned state images; `build/scripts/image.sh` can
   add the state partition to the boot image itself
   (`SYNVEIL_STATE_SIZE_MB`).
4. Guests still never format or partition anything at boot: a missing,
   ambiguous, or unformatted state partition disables persistence and
   credential operations, and recovery remains available.

## Tradeoffs

The GUID is a role label, not a secret and not a signature: whoever can
attach or rewrite the disk already controls the machine, the same trust
class as the device pin it replaces. Authenticity of install media
remains the job of the signed A/B installer/update milestone, which is
unaffected by this decision.

Identifying a partition from sysfs uevent files needs no udev daemon,
blkid, or extra tooling in the initramfs, and the kernel emits each
identity key at most once; duplicate or conflicting identity keys are
treated as ambiguous and rejected.

One residual constraint at the time of this ADR: the state partition
had to be on a device the kernel probed before the initramfs mount
step. ADR 0019 supersedes that constraint with a bounded wait for
late-enumerating (USB) media.
