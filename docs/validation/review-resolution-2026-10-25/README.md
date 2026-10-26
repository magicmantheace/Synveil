# Review resolution — the four points on 1962a7d / 4622519 (2026-10-25/26)

An independent review of the hardware-profile test-media pass found
its "owner test-ready" conclusion premature and raised four points.
This record is the resolution pass: each point is closed below with
evidence produced in this environment, or explicitly left open. The
status line in `docs/PHYSICAL_BOOT.md` was restored to "Not ready"
first (commit `d20a5d9`) and is updated again only on the strength of
what is recorded here.

**Identities.** Shipped image under test (unchanged throughout):
SHA-256 `6b10b482239ca3e771bd20d85a5f4e0dc414a00fc34334408eb134b150bacfc3`,
built from source `124abbc`. Variant acceptance runs executed at
source commit `7f6bf2b3bce0923475baf607bb4364d7f978b639` (harness-only
changes after the image build; the image bytes are the pinned digest
above, re-verified after every run). Commits in this pass: `d20a5d9`
(status hold), `834dba5` (writer), `406945e` (harness variants),
`e1ec08c` + revert `14e7899` (diagnostic twin, below), `7f6bf2b`
(xhci ordering fix).

## Point 1 — writer backing-device tracing: CLOSED

`tools/write-test-media.sh` no longer infers "in use" from device
names. It resolves every mounted filesystem source and every swap
device through sysfs to the set of whole disks behind it:
partition → parent disk; device-mapper → its `slaves/` (LUKS, LVM —
walked transitively); md → component devices; loop → the backing
file's filesystem, resolved by longest-prefix mountpoint match. Any
`/dev` source whose backing chain cannot be fully determined is a
refusal ("cannot determine the backing devices of X — refusing (fail
closed)"). Disk size is read from sysfs; `blockdev` is no longer
used. The confirmation prompt and the `dd` write are structurally
unreachable in fixture mode (`SYNVEIL_TESTMEDIA_FIXTURE=1` with
fixture sysfs/proc roots), and the fixture variables are ignored in
real mode. **No physical write was performed anywhere in this pass;
every writer run was fixture mode.**

Tests: `tests/write-test-media.py`, **15/15 pass**
(`out/pass-logs/writer-tests.log`): clean removable target passes;
directly mounted partition refused; LUKS volume on the target
refused; LVM-over-LUKS refused; swap on a mapper backed by the target
refused; swap directly on a target partition refused; md array with a
target component refused; loop device backed by a file on the target
refused; unresolvable mapper source fails closed; mapper with no
slaves fails closed; system on another disk passes; non-removable,
digest mismatch, partition-as-target, and undersized target refused.
A fixture mirrored from this host's *live* sysfs/proc topology (where
`/dev/mapper/rv` is backed by a partition of another disk) also
passes a clean synthetic target with no false uncertainty
(`out/pass-logs/writer-live-topology.log`). The suite is registered
in the offline quick tier (`tools/offline_checks.py`), which passes
in full at `7f6bf2b` (receipt
`out/local-validation/20261026T033242Z-7f6bf2b3-8450/results.json`).

## Point 2 — graphics: driver identified; simpledrm path exercised: CLOSED (QEMU scope)

**(a) Driver used by the existing acceptance: `virtio_gpu`.** The
shipped hardware kernel pins `console=tty0` and emits nothing on
serial, so identification used a *diagnostic twin*: the same build
with one added serial console (`console=ttyS0,115200n8`, `loglevel=7`;
tty0 kept first so display behavior is unchanged). The twin's kernel
config differs from the shipped image's in exactly that one
`CONFIG_CMDLINE` line (diff taken over
`out/native-joined-hardware*/manifests/kernel.config`), and its
app/rootfs payload digests match the shipped validation record. The
twin was committed as DIAGNOSTIC ONLY (`e1ec08c`) to satisfy the
packager's clean-tree guard and reverted immediately after the build
(`14e7899`); the shipped image bytes were never touched (digest
re-verified after restoring the directory). Booting the twin with
virtio-vga (the acceptance configuration) shows, verbatim in
`drm-driver-evidence.txt`: `virtio_gpu` initialized on **DRM minor
0**, `fbcon: virtio_gpu (fb0) is primary device`; simpledrm appears
only as the secondary EFI-framebuffer placeholder on minor 1. The
original acceptance therefore proved the desktop on **virtio_gpu,
not simpledrm** — the review's premise was correct.

**(b) simpledrm path exercised end to end.** With standard VGA (for
which the hardware kernel has no native DRM driver), the twin's boot
log shows `Initialized simpledrm 1.0.0 for simple-framebuffer.0 on
minor 0` and `fb0: simpledrmdrmfb`. The full hardware acceptance was
then run against the **shipped** image in that configuration
(`tests/smoke-native-joined-hardware.py --graphics vga`): **PASS**,
marker `SYNVEIL_NATIVE_JOINED_HARDWARE_QEMU_ENROLL_LOGIN_WESTON_PASS`
— enrollment (mismatch rejected) → login → Weston desktop proof →
session-end relock → cold reboot (wrong password denied) → login →
power-cut recovery → destroyed-state fail-closed. Receipt:
`guest-validation-vga.json` in this directory (log:
`out/pass-logs/smoke-vga2.log`). **Physical RTX 3060 compatibility
remains explicitly unverified**: QEMU's VGA GOP framebuffer is the
same simpledrm mechanism the TG01 firmware framebuffer would use,
but it is not the TG01's firmware, resolution, or outputs.

## Point 3 — complete image booted from emulated USB storage: CLOSED

`tests/smoke-native-joined-hardware.py --image-usb` attaches the
whole shipped image via `usb-storage` on qemu-xhci: **PASS**, same
marker, full sequence — enrollment, desktop interaction,
logout/relock, cold-reboot login, wrong-password rejection, plus
power-cut recovery and destroyed-state fail-closed (receipt:
`guest-validation-usb.json`; log: `out/pass-logs/smoke-usb2.log`;
OVMF serial captures show the boot entry loaded from
"UEFI QEMU QEMU USB HARDDRIVE"). Two harness defects were found and
fixed on the way, neither in the guest: `qemu.sh` emitted the
usb-storage device before the xhci controller existed (QEMU: "No
'usb-bus' bus found") — fixed in `7f6bf2b` by emitting the boot-disk
device after the graphics/input block, qemu-options suite still
14/14; and this VM's /tmp is a 512 MB tmpfs, too small for the
smoke's private image copies (runs here used an on-disk TMPDIR).

## Point 4 — recovery and Windows-safety wording: CLOSED

- `docs/OWNER_FIRST_BOOT_TEST.md`: the absolute sentence "Nothing on
  the Windows disk can be affected…" is replaced by the enumerated
  protections actually implemented and tested (guest payload has no
  disk-writing tool and mounts only its fixed-identity state
  partition; one-time boot menu leaves boot order/ESP/Windows Boot
  Manager untouched; the media write is the only destructive step and
  is guarded on Linux by digest pin + backing-traced refusal +
  exact-name confirmation + read-back verify, and on Windows is
  procedural only — certUtil digest, Disk Management identification,
  Etcher validation — and is named as the weaker path; disabling
  Secure Boot is an owner-made firmware change with the BitLocker
  caution). The guide now also states explicitly that rewriting this
  disposable media is **not** independent installed-system recovery
  and ADR 0010 recovery remains unimplemented.
- The earlier record (`docs/validation/joined-hardware-124abbc/`)
  carried a correction note: its "Honest limits" implied the
  simpledrm path had been exercised; it had not, at that time.
- `docs/FIRST_INSTALL.md` already kept independent recovery as an
  open blocker throughout and made no absolute Windows claim; its
  hardware section now points here.

## Failed attempts kept on the record

Two acceptance attempts in this pass failed for environment reasons
before the passing runs: the VGA run first died copying the image
into the 512 MB tmpfs /tmp, and the USB run first died at receipt
time when the non-root runner could not read the repository through
git. Both are visible in `out/pass-logs/smoke-vga.log` /
`smoke-usb.log`; the passing runs are `smoke-vga2.log` /
`smoke-usb2.log` at source `7f6bf2b3`, receipts as copied above. (An
earlier background-delivery summary of these runs reported success
prematurely; the receipts on disk are the authority, and they date
from the `*2` runs only.)

## What remains unproven after this pass

Physical hardware, entirely: RTX 3060 firmware framebuffer behavior,
HP firmware boot-menu/ Secure Boot behavior, USB SSD enumeration on
the TG01, HID behavior. The image is unsigned. ADR 0010 interactive
recovery is unimplemented. This record closes the four *review*
points; it is QEMU evidence, and the owner boot test remains a test
that can fail.
