# Building Synveil

Status: **Phase 1 bootstrap implementation**

Synveil does not currently install or borrow a root filesystem from a host distribution. The host provides build tools; target sources are downloaded only from the pinned source lock and are verified before extraction.

## Current target

- x86_64
- UEFI
- QEMU/OVMF
- Linux 6.18 LTS line
- glibc target userspace
- BusyBox bootstrap shell only

## Host requirements

A Linux host is currently required.

Typical Debian/Ubuntu package families needed for the bootstrap are:

```text
build-essential
bison flex texinfo
python3
xz-utils bzip2
cpio zstd
dosfstools mtools
qemu-system-x86 ovmf
libgmp-dev libmpfr-dev libmpc-dev
```

Depending on the host release, GCC may require additional ordinary build packages such as `libisl-dev`.

Fedora-family systems need the equivalent packages, including GCC/G++, make, binutils, bison, flex, texinfo, Python 3, xz/bzip2, cpio, zstd, dosfstools, mtools, QEMU x86, edk2-ovmf, and GMP/MPFR/MPC development headers.

Run:

```sh
bash build.sh doctor
```

before starting a full build.

## Source lock

Pinned inputs are in:

```text
build/manifests/sources.json
```

The fetcher refuses a cached or downloaded archive whose cryptographic digest does not match the lock. Each Phase 1 source also has a GitHub fallback pinned to an exact release-tag commit. This lets restricted build environments use Git transport without relaxing source versioning.

```sh
bash build.sh fetch
python3 tools/source_lock.py verify

# Force the pinned GitHub fallback transport:
SYNVEIL_SOURCE_TRANSPORT=git bash build.sh fetch
```

`SYNVEIL_SOURCE_TRANSPORT` accepts `auto` (default), `archive`, or `git`. In `auto`, Synveil prefers the official verified archive and falls back to the pinned Git revision only when the upstream transport is unavailable.

No build command resolves a floating "latest" version.

## Build commands

```sh
bash build.sh toolchain
bash build.sh rootfs
bash build.sh kernel
bash build.sh image
bash build.sh manifest
bash build.sh smoke
```

Or run the complete construction pipeline:

```sh
bash build.sh all
```

Equivalent convenience targets are available through `make`.

Build commands acquire a nonblocking checkout lock. A second build or cleanup
in the same checkout fails immediately rather than deleting source trees or
artifacts that the first command is using. Use separate checkouts for parallel
builds. Source extraction keeps ownership with the build user and aborts on
materialization errors.

### toolchain

Builds a cross/bootstrap environment rooted at:

```text
build/toolchain/out/
```

The initial target triplet is:

```text
x86_64-synveil-linux-gnu
```

The process builds binutils and a C-only bootstrap GCC, installs Linux UAPI headers into an isolated sysroot, then builds glibc for that sysroot.

This is intentionally a bootstrap compiler, not yet Synveil's final development toolchain.

The stage-1 GCC is C-only and deliberately does not build target libstdc++. During the glibc build, Synveil therefore invokes make with an empty `CXX` variable so glibc selects its supported C-only `links-dso-program-c` bootstrap path. A full C/C++ target compiler belongs after libc exists; Phase 1 does not require it to produce the bootable base image.

### rootfs

Builds a static BusyBox against the Synveil sysroot and installs it into a purpose-built root filesystem under `build/work/rootfs`.

The bootstrap disables BusyBox's `tc` applet, whose legacy CBQ definitions are
absent from the pinned Linux headers. Traffic-control tooling is not required
for the bootstrap console or recovery shell.

Synveil supplies its own `/init`. BusyBox exists only to provide bring-up utilities and a recovery shell.

Artifacts:

```text
out/synveil-initramfs.cpio
out/synveil-initramfs.cpio.zst
out/synveil-rootfs.tar.zst
```

### kernel

Builds the pinned upstream Linux source with a QEMU-focused configuration and embeds the normalized root-owned `newc` initramfs archive into the kernel.

Kernel compilation uses a separate materialized source directory from Linux UAPI
header installation, so generated header state cannot contaminate the kernel's
out-of-tree build.

The x86 Linux EFI stub means the resulting `bzImage` is also a UEFI-loadable executable.

Artifact:

```text
out/synveil-x86_64.efi
```

### image

Creates a GPT disk image containing a FAT32 EFI System Partition and copies the kernel to:

```text
EFI/BOOT/BOOTX64.EFI
```

Artifact:

```text
out/synveil-x86_64.img
```

No separate bootloader is used during this bootstrap phase.

### qemu

Boots the image with OVMF and a serial console.

If KVM is available it is used; otherwise the runner falls back to TCG.

Custom firmware paths can be supplied with:

```sh
SYNVEIL_OVMF_CODE=/path/to/OVMF_CODE.fd \
SYNVEIL_OVMF_VARS=/path/to/OVMF_VARS.fd \
bash build.sh qemu
```

### Optional QEMU graphical-device target

QEMU remains headless and network-isolated by default. To boot the same
bootstrap image with a virtio VGA device, a virtual USB keyboard/tablet and
an optional user-mode network adapter:

```sh
SYNVEIL_QEMU_DISPLAY=gtk SYNVEIL_QEMU_NETWORK=1 bash build.sh qemu
```

`SYNVEIL_QEMU_DISPLAY` accepts `none` (default), `gtk`, and `sdl`.
To build the opt-in framebuffer diagnostic into the bootstrap rootfs, set
`SYNVEIL_GRAPHICS_ROOTFS=1` while building the rootfs. Rebuild the kernel
and image afterward. For a headless CI framebuffer probe, set
`SYNVEIL_QEMU_VIDEO=1` and `SYNVEIL_SMOKE_REQUIRE_DISPLAY=1` when calling
`bash build.sh native-smoke`. This checks framebuffer creation and pixel writes. The same opt-in
graphics rootfs also includes `synveil-input --probe`, which checks that Linux
evdev has exposed at least one keyboard and pointer. With
`SYNVEIL_SMOKE_REQUIRE_INPUT=1`, the QEMU smoke test requires both input
devices as well as the framebuffer. Neither check provides a compositor or a finished desktop.
When booted with `SYNVEIL_QEMU_DISPLAY=gtk` and the graphics rootfs enabled,
you may manually start `synveil-display --menu` from the recovery shell for
a basic status/storage/network display. In the QEMU graphics configuration,
the USB tablet supports a visible pointer: move the pointer to a tile and
left-click to open it; click **BACK** in the top-right to return. Keyboard
navigation remains Left/Right, Enter and Escape. If no tablet is found, the
keyboard-only menu remains usable. The Status panel now reads local CPU thread
count, available memory and uptime; Storage reports ext4 free space, and
Network shows the actual assigned eth0 IPv4 address when available.
A fifth **Settings** tile controls a bright/high-contrast color scheme
(Left/Right) and small/large cursor (Up/Down). Pointer clicks can toggle
these options too. With the opt-in QEMU ext4 state volume mounted under `/var`,
the launcher now saves the contrast and pointer-size selections in a
private versioned file using atomic replace and `fsync`. The same opt-in
state disk houses `/home/synveil` as a bind mount backed by
`/var/lib/synveil/home/synveil`, with a root-private parent and an
owner-private UID 1000 directory. With no state disk, the demo home remains
ephemeral. A failed mount or remount cannot block the serial recovery shell. The startup
menu loads it on the next boot, rejecting malformed records. Without
the state disk, these preferences remain session-only. These UI settings
do not change display modes, networking, permissions or AI authorization.
An isolated host test covers read/write/corruption/symlink handling,
including recovery from an owner-private stale temporary file after
interrupted saves. Symlinked or unexpectedly permissive temporary files
are refused rather than trusted.
The `bash build.sh ui-preferences-smoke --timeout 60` QEMU test now
starts two separate VM processes on the same ext4 state disk. On boot one,
it uses a private *host-only QMP Unix socket* to send actual keyboard events
through the guest's Settings tile, toggling contrast and pointer size, and
requires the on-disk preferences to match. The serial recovery shell separately
writes a demo-user file. Boot two must report `SYNVEIL_SETTINGS_LOADED`
from the real graphical launcher and prove that the private home file retains
its contents and UID/GID 1000. The host QMP path must live in a mode-0700
owner-owned directory; it is never available to Synveil's guest AI/core and
is disabled for ordinary VM boots. The test still does not replace manual
validation of visible graphical input or a secure authenticated login.
The new guest test must pass before this behavior is marked validated.

To start the read-only framebuffer menu automatically on boot, enable
`SYNVEIL_DESKTOP_AUTOSTART=1` **and**
`SYNVEIL_GRAPHICS_ROOTFS=1` when building the rootfs. The menu runs under a bounded developer supervisor in the background;
unexpected UI failures are retried at most twice after the initial launch
(three total attempts), then `SYNVEIL_DESKTOP_RESTART_EXHAUSTED` is emitted.
A deliberate clean Escape exit stops without restart. The independent serial
recovery shell remains usable even when graphics repeatedly fail.
CI's graphics smoke requires `SYNVEIL_DESKTOP_READY` only after a drawable
framebuffer and usable keyboard have both been opened. This is still a
development desktop prototype, not a login-capable user session.

The temporary framebuffer launcher also has a **Terminal** tile. Opening it
presents a second confirmation before starting a disposable UID/GID 1000
interactive shell on VT1. The root launcher executes only the fixed
`synveil-vt --demo-terminal` binary, which drops all supplementary groups,
privileges and inherited environment, and sets `no_new_privs` before launching
BusyBox `sh`. The menu resumes when the shell exits. The demo home is ephemeral,
not encrypted or backed up, and the account is password-locked. This is an
**unattended/non-authenticated VM demo session**, not the final user login.

A separate source-built `synveil-vt` tool can launch an actual kernel-backed
interactive shell on framebuffer VT1. This is a **developer test VM only**
entrypoint with no login/authentication, and is **never started by init**.
Run it explicitly from the serial recovery shell:
```sh
synveil-vt --developer-terminal --i-understand-root-shell
```
The command activates tty1 and starts an interactive root shell there; the
serial recovery console remains independent. The kernel's fbcon renders the
terminal rather than an incomplete homegrown terminal emulator. Do not expose
this unauthenticated feature in a production or network-reachable image. This early menu is
read-only and still lacks a graphical terminal and general application launcher.

The chosen frontend must be present in the installed QEMU build; check
`qemu-system-x86_64 -display help`. Network access uses QEMU's user-mode
network backend and is disabled by default. This provides hardware support,
**not** a graphical session or terminal. DHCP is separately opt-in when
building with `SYNVEIL_NETWORK_ROOTFS=1`, which installs a bounded, QEMU-only
`eth0` DHCP helper and validated lease callback. Boot QEMU with
`SYNVEIL_QEMU_NETWORK=1` to attach the NIC. The helper uses BusyBox `udhcpc`
and reports `SYNVEIL_NETWORK_READY` only after IPv4 assignment.
`SYNVEIL_SMOKE_REQUIRE_NETWORK=1` enables explicit guest validation.
Normal serial CI remains offline; the runtime control protocol cannot
enable network access by itself.
The kernel config enables the matching DRM virtio GPU, input event and USB HID
drivers, but actual device readiness still requires QEMU guest validation.

### Private QEMU desktop bundle (developer-only)

After building a graphics-enabled, desktop-autostart rootfs (including the
non-root demo account), rebuilding its kernel/image and generating the
`out/manifests/build.json` for the **same committed revision**, you can
prepare a disposable virtual installation target:

```sh
bash build.sh vm-bundle --dest "$PWD/out/dev-vm" --size-mib 256
SYNVEIL_REPO_ROOT="$PWD" ./out/dev-vm/run-vm.sh
```

This tool verifies the EFI image hash against the committed build manifest,
requires the resolved framebuffer/input kernel options and packaged desktop
binaries, and creates the destination **exclusively** (never overwrites
an existing VM or state disk). The EFI image is read-only, while the ext4
state disk is a separate, writable developer volume. The launcher defaults to
a GTK display and **no network access**. It now checks immutable boot,
manifest and kernel checksums **again before every launch**, and refuses
symlinked artifacts, non-private state disks and bundles not owned by the
launching user with mode 0700. This is accidental-integrity detection, not
a signed/trusted release: a local attacker who can edit the bundle and its
checksums can replace both.

This is not a physical disk installer or a validated user release. A stale or
incomplete build is rejected. The demo shell is unprivileged but intentionally
unauthenticated; use this only for a disposable, isolated development VM. No
hardware disk selection, firmware support outside QEMU, encryption, secure
boot, or production user-login process exists yet.

### Optional QEMU persistent state

This is an isolated developer/VM feature, **not an installer or persistent system
root**. It leaves the EFI boot image read-only. Opt in when building the rootfs:

```sh
SYNVEIL_NATIVE_ROOTFS=1 SYNVEIL_PERSISTENCE_ROOTFS=1 bash build.sh rootfs
bash build.sh kernel
bash build.sh image
bash build.sh state-disk --size-mib 256
SYNVEIL_QEMU_STATE_DISK="$PWD/out/synveil-state.ext4" bash build.sh qemu
```

The new ext4 volume is attached as a second virtio block disk and mounted at
`/var` through `/dev/vdb` only in explicitly opted-in rootfs images. The
guest never formats a disk. The creator uses exclusive file creation and
**refuses to overwrite an existing image**. Retain the state image between
QEMU sessions to retain data. `bash build.sh clean` removes the generated
`out/` directory including this experimental state image; copy it elsewhere
first if you want to preserve it.

The state disk is only for controlled QEMU tests; do not use this device-path
assumption for a physical installation. In the absence of the disk, Synveil
prints `SYNVEIL_STATE_UNAVAILABLE` and provides a recovery shell instead of
pretending storage is persistent. A correctly mounted volume prints
`SYNVEIL_STATE_READY`.

To verify actual persistence across separate VM processes:

```sh
bash build.sh persistence-smoke --timeout 60
```

This is not yet the final root filesystem/storage or a production-grade
audit journal. With a mounted state volume, native `veil-core` writes
synchronized JSONL events under `/var/log/synveil/`; it does not yet provide
journal rotation, verified integrity, or historical query APIs.
First hardware installation and user-facing desktop testing remain blocked
until the separate installation contract is met.

### smoke

Runs the QEMU image under a timeout and requires the bootstrap userspace to emit:

```text
SYNVEIL_BOOT_OK
```

Serial output is retained in:

```text
out/logs/qemu-smoke.log
```

## Build manifest

```sh
bash build.sh manifest
```

writes:

```text
out/manifests/build.json
```

It records the Git revision, target, pinned sources, build identity, and cryptographic hashes of generated artifacts.

## Clean builds

```sh
bash build.sh clean
```

removes generated images and work trees while preserving downloaded source archives and the bootstrap toolchain.

```sh
bash build.sh distclean
```

also removes the source cache and generated toolchain.

## Reproducibility

The pipeline honors `SOURCE_DATE_EPOCH` where implemented and records it in the build manifest. Bit-for-bit reproducibility is a later Phase 14 target; Phase 1's immediate requirement is that every source input and command path be explicit and repeatable.

## Known bootstrap limitations

- The toolchain build currently uses host GMP/MPFR/MPC development libraries to build GCC itself. Target binaries do not link against those host libraries.
- BusyBox 1.38.0 is explicitly labelled "unstable" by upstream; it is pinned and used only as temporary bootstrap userspace. Replacing BusyBox with Synveil-native userspace is already part of the architecture.
- Secure Boot/signing is not implemented.
- The initramfs is embedded in the kernel, so rootfs changes rebuild the kernel.
- The image has no persistent writable root filesystem yet.


### Private authenticated QEMU preview bundle (experimental)

After the native framebuffer image builds, run the locked-serial two-boot
graphical-onboard guest fixture to produce a successful validation receipt.
For the *same Git commit*, the optional `auth-vm-bundle` command can
create an isolated authenticated desktop test VM:

```sh
bash build.sh guest-fixture --case graphical-onboard
bash build.sh auth-vm-bundle --dest "$PWD/out/auth-vm" --size-mib 256
SYNVEIL_REPO_ROOT="$PWD" ./out/auth-vm/run-vm.sh
```

The packager refuses a missing or failed two-boot validation receipt,
stale Git revision, altered EFI/image/initramfs artifacts, unexpected or
wrong-mode authenticated login markers, and any developer root-serial
override. It validates the *embedded* initramfs rather than relying on
a mutable rootfs staging directory. It creates a NEW ext4 volume and
never copies the synthetic credential-bearing CI guest state disk.

The launcher enforces private owner-only mode-0700 bundle storage,
checks immutable SHA256 hashes, disables QMP monitoring and networking
and runs QEMU with an interactive GTK framebuffer. Existing bundles
and state disks are never overwritten. The old unauthenticated developer
VM bundle remains separate. The proof/manifest and SHA256 checks are
not digital signatures or trusted boot. This is a developer-only
offline QEMU preview, NOT a physical installer, signed release,
general-purpose compositor or validated owner recovery mechanism.
