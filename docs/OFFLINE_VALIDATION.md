# Offline validation during GitHub Actions unavailability

The **single canonical command** is `bash build.sh offline-check`,
implemented by `tools/offline_checks.py`. It never calls the
GitHub Actions API or starts a GitHub-hosted runner.

```bash
# Python, C, shell, and local build-fixture checks; no Rust or QEMU
bash build.sh offline-check --level quick

# All host checks including Rust formatting, tests and Clippy
bash build.sh offline-check --level host

# See the exact planned commands without running them
bash build.sh offline-check --level guest --plan
```

The pinned Rust toolchain and all required crates must already be available
locally. Host Rust commands use Cargo `--locked --offline`.
Missing prerequisites are reported as blocked; a partial quick-tier pass
**is not** a Rust/native-build or QEMU pass.

Each invocation saves
`out/local-validation/<UTC-time>-<commit>-<pid>/results.json`
with per-check logs, result types and checkout identity. The runner also
checks whether the tracked working tree is clean.

After building the native developer VM image with the pinned source/toolchain,
the QEMU gates may be explicitly enabled:

```bash
bash build.sh offline-check --level guest
```

The runner refuses guest validation of an image whose build-manifest commit
or SHA-256 digest doesn't match the current clean Git revision. Guest tests
include the separately staged, disposable graphical-login image/state disk.
They must never target a physical device or be interpreted as installer proof.

The canonical runner's tests are in `tests/offline-checks.py`.
The developer VM bundle now scans the manifest-matched *embedded newc
initramfs*, not just editable rootfs staging. The
`tests/initramfs-profile.py` fixture covers forbidden markers,
truncated records, duplicate paths and missing boot components.
No additional parallel/competing offline runner should be introduced.

## Current native validation blocker

The previously archived native build at commit `9d441279a2ff`
failed compilation because `src/veil-core/src/seat.rs` lacked
the Unix `FileTypeExt` import needed for `is_socket`.
That source issue was corrected on the development branch, but **a fresh
native target build has not yet verified the correction**. Without a
complete native build and QEMU pass, the authenticated framebuffer preview
remains experimental. The default developer boot still has an
unauthenticated serial recovery shell and is not safe as an installer.
