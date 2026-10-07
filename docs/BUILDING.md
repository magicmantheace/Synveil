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

Synveil supplies its own `/init`. BusyBox exists only to provide bring-up utilities and a recovery shell.

Artifacts:

```text
out/synveil-initramfs.cpio
out/synveil-initramfs.cpio.zst
out/synveil-rootfs.tar.zst
```

### kernel

Builds the pinned upstream Linux source with a QEMU-focused configuration and embeds the normalized root-owned `newc` initramfs archive into the kernel.

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
