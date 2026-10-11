# Full native QEMU graphical development-image validation

- **Source revision:** `d875da39dbe4d57ebfe055f3d03ac1469d4e59c2`
- **Date:** 2026-10-11
- **Workflow:** [Native target build #38171346213](https://github.com/magicmantheace/Synveil/actions/runs/38171346213)
- **Conclusion:** SUCCESS; all job steps green
- **Scope:** pinned source/toolchain build and controlled x86_64 OVMF QEMU image
- **Companion:** [VM bundle host checks #38171346048](https://github.com/magicmantheace/Synveil/actions/runs/38171346048), SUCCESS at the same SHA

The same full workflow built native services, rootfs, EFI kernel, boot image and
manifest and then passed the following *independent runtime steps*:

1. Native core status and serial boot readiness
2. Structured read-only `synctl observe` in the guest
3. Core-crash shell recovery
4. Supervision restart and bounded restart exhaustion with recovery
5. Rejection of malformed IPC without breaking a healthy core
6. Missing core binary with working recovery shell
7. Virtio framebuffer, keyboard/tablet, VT and desktop startup markers
8. Non-root demo VT1 terminal with UID/GID 1000 and `NoNewPrivs`
9. QEMU opt-in DHCP networking
10. Ext4 `/var` state survival across two separate QEMU processes
11. Retention of build and smoke evidence artifacts

The companion VM bundle workflow checked image identity, integrity,
private permissions, exclusive destination creation and rollback/cleanup.

## Explicit limitations

**No user-facing install or production release is validated.** This run proves
a QEMU developer framebuffer **menu**, not an authenticated multiuser
display manager, compositor, package updater or physical disk installer.
The non-root demo shell remains an *unauthenticated disposable evaluation
interface*. The persistent QEMU state disk is not an installed rootfs.

Persistent display settings and demo-home bind mounts were introduced in
subsequent revisions. Their own host and two-boot QEMU checks are needed;
this older full-green run is not evidence for those new behaviors.
