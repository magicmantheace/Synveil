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
