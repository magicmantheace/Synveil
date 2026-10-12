# Running Synveil checks without GitHub Actions

The authoritative local runner is `tools/offline_checks.py`. It records
each check's status, command, exit code and log beneath
`out/local-validation/<UTC timestamp>-<commit>-<PID>/`, with the repository
commit, clean/dirty state and failure classification in `results.json`.

On a Linux checkout with Python 3:

```sh
sh tools/check-host.sh
```

This uses offline Cargo and the pinned Rust toolchain, and runs the full Python
host suite, `cargo fmt --check`, workspace tests and strict Clippy. It does
not call GitHub Actions or permit Rustup/Cargo to download toolchains or
packages. A missing tool or dependency is a **blocked** check, never a pass.
The runner collects results from all checks rather than stopping at the first
failure, to make local failure diagnosis possible.

For Python-only diagnostics when Rust is unavailable:

```sh
SYNVEIL_SKIP_RUST_CHECKS=1 sh tools/check-host.sh
```

The underlying runner also exposes a command-only plan and an opt-in guest
suite for a correctly manifest-matched image:

```sh
python3 tools/offline_checks.py --level guest --plan
python3 tools/offline_checks.py --level guest
```

**Host checks do not prove a bootable authenticated desktop.** The guest
suite refuses to certify a stale image, a modified image digest or an image
without the exact clean Git revision declared in its build manifest. QEMU,
source-built Linux and isolated state disks are required for true guest
evidence. Never run the experimental rootfs on a production machine.

## Expanded opt-in guest evidence

The `--level guest` plan now reuses the existing native QEMU harnesses for
core crash/restart exhaustion, observer output, desktop restart, the demo
terminal lifetime, absent/corrupt state devices, seat crash and locked seat,
missing core, protocol rejection, locked serial console and authenticated
graphical-login fixtures. The boot smoke additionally requires the actual
network, framebuffer, keyboard, VT and desktop readiness markers and enables
QEMU's user-mode network adapter.

This is deliberately a more demanding gate than the earlier four-case guest
plan. Its success would still apply only to the exact clean commit and native
developer QEMU image, **not** physical hardware or a release installer.
The guest runner now strips any operator-supplied `SYNVEIL_QEMU_STATE_DISK`
before running tests. Seat-crash, two-boot raw persistence, and two-boot
UI preferences/home each receive their **own newly formatted, mode-0600,
128 MiB ext4 state image** inside that run's `out/local-validation/`
receipt. A missing `mkfs.ext4` or failed disk creation records a **blocked**
test, not a pass. Those images are disposable test artifacts; never distribute
them as account or installation media.

The dedicated persistence and UI two-boot smoke scripts are now part of the
combined guest receipt. A successful run still cannot establish physical
installer readiness, secure onboarding, or a non-root graphical compositor.
