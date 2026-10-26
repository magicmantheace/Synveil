# Physical UEFI boot bring-up (developer-only)

**Status (2026-10-25): Not ready for owner hardware testing.**

Target: HP Pavilion TG01, Intel i5-10400F, RTX 3060 12 GB, 32 GB RAM,
USB HID keyboard/mouse, UEFI firmware and Windows 11 on the internal disk.

A single **joined image** now exists that boots (in QEMU) through
graphical enrollment and login into a multiwindow Weston desktop,
with state on a GPT state partition the image itself provides, and a
hardware-profile build of that same image has been produced
([validation record](validation/joined-124abbc/README.md)). None of
this has been booted on physical hardware: the EFI binaries are
unsigned, no removable-media preparation method has been reviewed,
and HP firmware behavior is unverified. The image is **not** yet a
safe standalone owner install.

## Exact separation of targets

- `SYNVEIL_BOOT_TARGET=qemu` (default): use QEMU virtio graphics, serial
  diagnostics and existing automated tests.
- `SYNVEIL_BOOT_TARGET=hardware` (opt in): request UEFI GOP/simpleDRM
  framebuffer with text console on `tty0`; compile USB HID, mass storage,
  UAS, AHCI and NVMe support into the kernel. This is a kernel **build**
  target only. It does not enable NVIDIA acceleration, create an installer,
  verify Secure Boot, or alter an installed disk.
- Kernel build writes `out/manifests/kernel-boot-target.txt`; subsequent
  `bash build.sh manifest` records `boot_target` from that file, not from
  the caller's current shell environment. Ordinary QEMU bundles must reject
  a hardware-labelled build.

## Safe, file-only developer build

In an isolated Linux development checkout with the pinned toolchain built:

```sh
# This section builds artifacts under out/. It does NOT write a USB.
# For this kernel/EFI validation only, omit native core and owner credentials.
SYNVEIL_GRAPHICS_ROOTFS=1 SYNVEIL_DESKTOP_AUTOSTART=1 \
SYNVEIL_NETWORK_ROOTFS=1 bash build.sh rootfs

SYNVEIL_BOOT_TARGET=hardware bash build.sh kernel
bash build.sh image
bash build.sh manifest
bash tests/inspect-image.sh out/synveil-x86_64.img
grep -E 'CONFIG_(DRM_SIMPLEDRM|SYSFB_SIMPLEFB|USB_STORAGE|BLK_DEV_NVME|SATA_AHCI)=y' \
    out/manifests/kernel.config
cat out/manifests/kernel-boot-target.txt
```

Keep the output under `out/` (a normal file). Do not run `dd`,
`mkfs`, `parted`, or an installer against any physical device for this
milestone. The existing `image.sh` creates its GPT partitions **inside a
regular image file**, without privileges or loop devices. Its success is not
a claim of boot success on the physical machine.

## Mandatory gates before owner-side USB boot

- [x] Source-built hardware kernel and FAT/EFI image verified in CI
      ([hardware build proof at 0cc3879](https://github.com/magicmantheace/Synveil/actions/runs/39505922998)).
      This is build-only evidence, not an actual hardware boot.
- [x] Experimental Debian/XFCE ISO built with BIOS and UEFI El Torito
      boot entries verified ([image build](https://github.com/magicmantheace/Synveil/actions/runs/39506908391),
      [fail-closed UEFI inspection](https://github.com/magicmantheace/Synveil/actions/runs/39522697228)).
      An entry in an ISO is not proof of a running desktop.
- [x] Experimental **Debian/XFCE** image booted in actual OVMF/QEMU UEFI
      and Xfwm4 moved/resized, maximized, minimized and restored its
      real Terminal and Thunar application windows. Both the serial session
      marker and framebuffer passed at exact SHA `c0242259f`
      ([completed CI evidence](https://github.com/magicmantheace/Synveil/actions/runs/39541711454),
      [validation record](validation/desktop-live-c024225/README.md)).
      The [subsequent 437020d guest run](validation/desktop-live-437020d/README.md)
      additionally passed **two fresh EFI cold boots**, a running panel and
      graphical Settings Manager. No state/data persisted between those boots.
      This is **not** a native Synveil compositor, protected owner login,
      installed-system boot, hardware GPU test, or production release.
- [ ] Correct `tty0` screen diagnostics and UEFI firmware framebuffer
      tested in a disposable QEMU UEFI graphical session.
- [ ] Real multiwindow desktop with graphical login and owner enrollment;
      keyboard, pointer, launcher, terminal, file manager and settings.
      **QEMU-proven subset (2026-10-25, joined image @ 124abbc):**
      graphical enrollment + login (wrong password denied after cold
      reboot), multiwindow Weston desktop, keyboard and pointer incl.
      window move/resize, weston-terminal, session-end relock, state
      persisted on the image-provided partition — see
      [validation record](validation/joined-124abbc/README.md).
      Still missing: launcher, file manager and settings applications;
      any physical-hardware boot.
- [x] Non-root session; no unauthenticated serial or local root-shell bypass.
      Proven for the joined image in QEMU (2026-10-25): the desktop
      session runs as UID 1000 behind the seat lease, the production
      profile carries no developer-serial-recovery marker, and the
      serial console stays locked through enroll/login/relock.
      Physical-hardware confirmation remains outstanding.
- [ ] Independent console/recovery path when the desktop/seat fails.
- [ ] Signed or otherwise owner-verified release/image provenance, with
      clear test-media identification and checksums.
- [ ] Dedicated removable-media preparation method reviewed for correct
      device identity and destructive-operation confirmation.
- [ ] Documented HP firmware compatibility, USB boot selection, network
      and RTX 3060 fallback limitations. Windows partitions must remain
      untouched, including firmware boot-order and ESP changes.
- [ ] Physical validation recorded **separately** from QEMU.

UEFI Secure Boot may reject unsigned EFI-stub binaries. Changing Secure Boot
could trigger Windows device-encryption/BitLocker recovery. Do not make
firmware security changes without appropriate recovery planning and explicit
owner authorization.

## Updateable external SSD (later milestone)

Before installation: confirm an A/B immutable system design (ESP, slots A/B,
persistent home/state, independent recovery), partition identification via
stable verified UUIDs, and signed release/update metadata. Prove startup
attempt accounting, boot-health confirmation, interrupted write handling,
confirmed-slot fallback, user-data preservation, and manual rollback on
**disposable** virtual disks. Never install over or modify Windows media.

See [ADR 0013](adr/0013-first-boot-desktop-priority.md),
[ADR 0011](adr/0011-dual-slot-installation.md), and
[the first-install contract](FIRST_INSTALL.md).
