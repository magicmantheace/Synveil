# Toolchain, QEMU graphical desktop, and authenticated persistence — @ f48771c (2026-10-25)

Second development pass on branch `synveil/first-boot-desktop-muse`
(PR #17), following the monorepo baseline record
(`docs/validation/monorepo-d3c7822/`). Everything below was **actually
run in this environment during this pass**; nothing is carried over from
upstream/historical evidence.

## Result summary

- The full source toolchain (binutils/GCC/glibc, C++ runtime, libatomic)
  was built from the pinned sources.
- The native Weston graphical desktop image boots in QEMU and passes the
  project's own graphical acceptance test end to end, including
  pointer-driven window move and resize.
- The two-boot `graphical-onboard` guest fixture passes: first-user
  enrollment on boot 1, persistent authentication after a cold reboot on
  boot 2, wrong password denied, serial locked on both boots.
- The authenticated-preview image (`SYNVEIL_AUTH_DESKTOP_ROOTFS=1`)
  builds and boots standalone in QEMU to its locked login state.

## Environment notes (read before reproducing)

- 2 CPUs; builds are slow (a graphical-profile kernel build is ~55 min).
- The VM's root filesystem was reset repeatedly during the pass
  (apt-installed packages and `/opt` vanish; `~/workspace` persists).
  QEMU 8.2.2 + OVMF were reinstalled after each reset.
- Shell umask is **0007**. This single fact caused most of the guest
  failures fixed in this pass: builders that create files/directories
  without pinning modes produce 0660/0770 artifacts that unprivileged
  guest users cannot read or traverse.
- Stages that refuse to run as root were run inside a user namespace
  (`unshare --user --map-user=1000 --map-group=1000`); QEMU therefore
  ran under TCG (no KVM), so guest time runs behind wall time.
- Rust 1.99.0 + pinned nightly-2026-10-01 live under
  `~/workspace/synveil-tools/` (persistent), not `/opt`.

## Fixes committed this pass (d208b7c..f48771c)

1. `d208b7c` — **Codex P2**: `tools/offline_checks.py` applied the
   image-provenance gate to every check named `qemu-*`, permanently
   blocking the host fixtures `qemu-lock`/`qemu-options`. The gate now
   covers only genuine GUEST entries; real guest-image verification is
   unchanged. Regression test added in `tests/offline-checks.py`
   (fixture suite: 8 tests OK). This corrects the baseline record's
   "2 blocked, expected" framing: the tiers now pass outright.
2. `57245a9` — `tools/native_weston.py` read the sysroot libc at
   `usr/lib64/libc.so.6`; this project's glibc installs it under
   `lib64/`. Provenance byte-comparison itself unchanged.
3. `e72e00d` — `tools/native_weston_assets.py` allowlist omitted the
   eudev daemon paths (`sbin/udevd` etc.) that its own seal requires.
4. `9abe888` — the Weston stage never built `util-linux`, so eudev's
   `udevadm` lacked `libblkid.so.1` in the guest. The stage now builds
   `util-linux` alongside `weston`/`dejavu`.
5. `e1d62ec` — same class: `xkeyboard-config` is a runtime-only data
   dependency absent from `WESTON_DEPENDENCIES`; the stage now builds
   it, so `/usr/share/X11/xkb` reaches the guest (289 entries sealed).
6. `bbf1e6b`, `2342c45`, `ddaf4a2` — `tools/native_weston_vm.py` packed
   rootfs directories at 0770 and several builder-written files
   (weston config, fixture marker, passwd/group) at 0660 under the host
   umask; the unprivileged guest session could not traverse or read
   them. The builder now normalizes directories to 0755 and regular
   files to 0755/0644 before packing (pinned `/etc/shadow` 0600 kept),
   matching the asset sealer's own policy.
7. `5d93747` — the Weston fixture `init` created runtime parent
   directories (`/run/user`, `/home`, `/var/cache`) as 0700 root-owned
   under its own umask 077, so the session user could not reach its
   leaf directories. Parents are now chmod 0755; leaves stay 0700.
8. `1ccbbb0` — the fixture `init` searched for `udevd` only under
   `/lib/udev` and `/usr/lib/udev`; the sealed eudev installs it at
   `/sbin/udevd`. Without the daemon, the udev database stayed empty
   and Weston found no input devices.
9. `0c5069b` — `build/scripts/qemu.sh` let QEMU add its default devices
   (including a DVD-ROM, `/sys/block/sr0`), which the guests' storage
   gates correctly reject as undeclared hardware. The launcher now
   passes `-nodefaults`; the emulated set is exactly the declared set.
   Base-image smoke re-verified after the change (below).
10. `28888da` — `src/desktop-wayland/first-window.c`: on resize-grab
    release, Weston 15 sends `set_size(0, 0)` ("client decides"); the
    client mapped zero to its initial 400x240, snapping resized
    windows back. Zero now keeps the current size (initial size when
    none is set yet).
11. `f48771c` — `tests/build-guest-fixture.py` created the
    `auth-desktop` marker with `Path.touch()`, inheriting the host
    umask (0660 here); the guest marker policy requires exactly 0644.
    Both fixture markers are now pinned to 0644. (The desktop-marker
    policy suite itself already pinned modes; the builder did not.)

## Toolchain (built this pass, in order)

- Stage 1 (`toolchain.sh`): binutils 2.46.1, GCC 16.2.0, Linux UAPI
  headers 6.18.55, glibc 2.44; dynamic+static target link sanity
  checks pass. Marker `build/toolchain/out/SYNVEIL-TOOLCHAIN`.
  Tar extractions require `TAR_OPTIONS=--no-same-owner` in this
  sandbox (chown to arbitrary uid/gid is denied even for root).
- Stage 2 (`toolchain-cxx.sh` steps): libgcc + libstdc++ built and
  installed; `tools/native_cxx_runtime.py --stage` →
  `SYNVEIL_NATIVE_CXX_RUNTIME_STAGED_AND_LINKED_NOT_EXECUTED`;
  marker gained `cxx=1`.
- Stage 3 (`toolchain-atomic.sh`): libatomic staged;
  `SYNVEIL_NATIVE_ATOMIC_RUNTIME_STAGED_AND_LINKED_NOT_EXECUTED` and
  `SYNVEIL_NATIVE_ATOMIC_COMPARE_EXCHANGE_EXECUTED_TARGET_GLIBC`
  (a target binary executed under the target glibc).
- Native Rust binaries (`tools/native_build.py`): veil-core, synctl,
  veil-enroll, veil-seat, veil-audit audited into `out/native/`.
  Note: `native_preflight.py` assumes the stage-1 libgcc layout
  (unwind objects in `libgcc.a`); after stage 2 they live in
  `libgcc_eh.a`. The build was run against a stage-1-layout copy of
  the toolchain (real toolchain untouched), mirroring upstream's
  `build.sh all` ordering (native before cxx). The bundle must be
  rebuilt at the current HEAD before an auth/native rootfs build —
  `package_native.py` verifies the bundle's recorded revision.

## Validation actually run at the final head (f48771c)

| Check | Result |
|---|---|
| Offline quick tier (`bash build.sh offline-check --level quick`) | **PASS**, exit 0 (27/27) — receipts `out/local-validation/20261025T144042Z-28888dad-49547/` (at 28888da) and rerun at f48771c, exit 0 |
| Offline host tier (`--level host`) | **PASS**, exit 0 (30/30) at 28888da |
| Offline fixture suite (`tests/offline-checks.py`) | 8 tests OK (includes the new provenance-gate regression test) |
| Base pipeline (rootfs/kernel/image/manifest) | exit 0; `out/synveil-x86_64.img` 134,217,728 B |
| Base QEMU smoke (`bash build.sh smoke`) | **PASS** — "QEMU smoke boot passed"; re-run after the `qemu.sh -nodefaults` change: PASS |
| Weston stage (`tools/native_weston.py --build`) | exit 0; receipt `SYNVEIL_NATIVE_WESTON_COMPONENTS_SOURCE_BUILT_NOT_BOOTED`, Buildroot 2026.08.1 @ `7fdeadb`, Weston 15.0.1 |
| Weston VM image (`tools/native_weston_vm.py --build`) | exit 0; image below |
| **QEMU graphical acceptance** (`tests/smoke-native-weston.py --timeout 300`) | **PASS** — prints `SYNVEIL_NATIVE_QEMU_WESTON_RGB_WINDOWS_INPUT_PASS`: two windows, real RGB scanout, QMP pointer move + resize (persists after release), Escape closes a window, terminal `exit` traversal |
| Auth-desktop rootfs + kernel + image + manifest | exit 0 (profile: NATIVE+GRAPHICS+PERSISTENCE+AUTOSTART+AUTH_DESKTOP) |
| Auth-desktop image standalone QEMU boot (manual, same device set as `qemu.sh`) | `SYNVEIL_CORE_READY`, `SYNVEIL_SEAT_READY`, `SYNVEIL_DISPLAY_READY`, `SYNVEIL_BOOT_OK`, `SYNVEIL_SERIAL_RECOVERY_LOCKED` — boots to the locked login state (no enrolled user on a bare image, as documented) |
| **Graphical-onboard fixture** (`bash build.sh guest-fixture --case graphical-onboard`) | **PASS**, exit 0 — `out/guest-fixtures/graphical-onboard/manifests/validation.json`: `result: passed`, `independent_qemu_boots: 2`, `locked_serial: true`, `first_user_enrollment: true`, `persistent_authentication: true`, `wrong_password_denied_after_cold_reboot: true`, `nonblank_rgb_after_login_both_boots: true`, `graphical_scanout_after_cold_reboot: true`; builder line: "locked-serial graphical enrollment survived independent QEMU reboot" |

QEMU invocation for the graphical acceptance (constructed by the test
via `build/scripts/qemu.sh`): OVMF pflash, `SYNVEIL_QEMU_VIDEO=1`,
`SYNVEIL_QEMU_DISPLAY=none`, network off, 768 MiB, QMP socket for
screendump/input; launcher adds `-nodefaults`, virtio disk (read-only),
virtio-vga, xhci keyboard+tablet.

## Image artifacts produced this pass

| Artifact | Size | SHA-256 | Status |
|---|---|---|---|
| `out/native-weston-vm/synveil-x86_64.img` | 134,217,728 B | `b4fae203b9eb1dbdf781d1817ab27e5b77034406a5214b79dac52cc7edd22462` | Weston desktop fixture (unauthenticated, non-persistent by design); QEMU graphical acceptance PASS |
| `out/pass-artifacts/auth-desktop/synveil-x86_64.img` | 134,217,728 B | `f43f6f6aa0fb5956cce4caf5c7726f0d237fbb2a46c5dab49f3b715d81bb572d` | Authenticated preview; boots to locked login in QEMU (see above); enrollment/login proven via the graphical-onboard fixture on the same rootfs profile |
| `out/synveil-x86_64.img` | 134,217,728 B | `bda07194f63f53a9ee4c5d1415657336a87277b904cc56f2ffcc1eafb5043c3f` | Developer graphics image (native+graphics+persistence+autostart, unauthenticated menu); base for the guest fixtures. An earlier serial-profile build of this path also passed `bash build.sh smoke` |

All images: GPT + EFI System Partition + `BOOTX64.EFI`, structure
verified by the builders.

## Honest limits

- No physical hardware was tested. Per `docs/PHYSICAL_BOOT.md`, the
  hardware-target kernel profile (`SYNVEIL_BOOT_TARGET=hardware`) is a
  separate build, and the unsigned EFI may be rejected by Secure Boot.
- The A/B installer/updater and graphical update flow were not
  exercised; `docs/FIRST_INSTALL.md` remains the owner procedure and
  its own caveats apply (controlled prototype, not a production
  installer).
- QEMU ran under TCG in a user namespace; timing-sensitive behavior
  on real hardware is unproven.
- The base (non-Weston) rootfs still packs directories at the host
  umask's 0770 in the serial profile; harmless there (everything runs
  as root) but worth normalizing in `rootfs.sh` like the Weston VM
  builder now does.
