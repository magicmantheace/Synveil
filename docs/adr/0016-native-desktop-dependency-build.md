# ADR 0016: Source-built desktop dependencies with the native toolchain

- Status: Accepted for isolated development builds; runtime integration incomplete
- Date: 2026-10-23
- Related: ADR 0001, 0013, 0015; `docs/WESTON_NATIVE_PORT.md`

## Blocking dependency and decision

Weston 15.0.1 declares both C and C++ compilers in its upstream Meson
project. Synveil's existing bootstrap GCC is deliberately C-only and built
before glibc. It cannot configure that compositor unchanged. Add an explicit
post-glibc `bash build.sh toolchain-cxx` stage using the same pinned GCC
source and the existing Synveil target/glibc sysroot. Keep the default
bootstrap C/glibc path unchanged. Native libstdc++ and libgcc shared runtimes
are staged into that sysroot with hashes and a real C++17 link covering
strings, vectors, threads and exceptions. ELF/link success is not execution.

For the compositor's complete dependency graph, use Buildroot 2026.08.1 at
reviewed commit `7fdeadb3b9f12f78e5fccca914d7fef47090ab9a` as a **build-time
package recipe executor only**. It is not a replacement operating system,
a target root filesystem, a kernel, an account authority or a runtime
control-plane dependency. This reuses established source recipes for the
input/keymap/DRM/Cairo/font dependencies rather than adding many bespoke
one-library build pipelines. Weston remains the established compositor;
Synveil remains responsible for its native image, authentication and sessions.

## Boundaries

- Require the existing `x86_64-synveil-linux-gnu` source-built compiler and
  glibc, with the explicit native C++ stage and matching runtime hashes.
- Pin the recipe executor's Git commit and Weston's release SHA-256.
  Require recipe-provided hashes for dependency downloads.
- Override the recipe set's older Wayland/Pixman sources with fresh,
  checksum-verified, safely extracted copies of Synveil's existing pins:
  Wayland 1.26.0 (host scanner and target), protocols 1.49, Pixman 0.46.4,
  libffi 3.5.2. Do not silently replace those versions.
- Configure software DRM and headless backends, desktop shell, terminal,
  seatd, input libraries, font rendering and fonts. Disable X11, Xwayland,
  Mesa, remoting and unrelated shells.
- Validate the resolved Kconfig before compilation. Reject an internal or
  foreign compiler, alternate libc, systemd, bootloader, kernel, BusyBox,
  image-generation target or missing source-hash enforcement.
- Invoke only package compilation (`weston` and fonts), never a complete
  system/image build. Its private staging tree may contain package support
  files; **never copy that tree wholesale into Synveil**. In particular its
  generated init, accounts, /etc/shadow and base-system skeleton are not
  Synveil components and may not be imported.
- No privileged build, physical-device path, installer, firmware operation,
  release key, owner credential or published executable/image is added.

## Evidence and remaining integration

The actual pinned recipe set's Kconfig generated a valid constrained
configuration locally. Offline refusal tests reject substituted compilers,
rootfs/boot/init generation, absent seat support and disabled source hashes.
These checks do not establish successful C++ or compositor compilation.
The opt-in `native-weston.yml` workflow must complete its real builds before
those results can be claimed. Its output is component provenance, not an
owner installation image.

Next, import an explicit reviewed set of dependency assets into the existing
native Synveil VM fixture, preserve native glibc/loader identity, and run
Weston with software rendering and two independently managed windows under
the Synveil kernel. Real RGB scanout, input, window lifecycle, authenticated
session revocation, installed persistence, A/B boot/recovery and physical
hardware remain independent unfinished acceptance gates. The earlier target
client's host-runtime failure also remains unresolved until its diagnostic
has been obtained and repaired; this build decision does not bypass that gate.
