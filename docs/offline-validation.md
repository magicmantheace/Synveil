# Running Synveil checks without GitHub Actions

GitHub Actions is optional for local validation. On a Linux development machine
with Python 3, the pinned Rust toolchain (see `rust-toolchain.toml`) and Cargo:

```sh
sh tools/check-host.sh
```

This runs the same Python host fixtures, formatting, Rust workspace tests and
strict Clippy checks as `.github/workflows/native-core-host.yml`. It stops
on the first failure and does **not** build a bootable image or prove guest
runtime behavior. For Python-only diagnostics when Rust is unavailable:

```sh
SYNVEIL_SKIP_RUST_CHECKS=1 sh tools/check-host.sh
```

**Do not report a passing native target build or QEMU graphical-login smoke
test from this host-only script.** Those require their own native build and
QEMU runs. Never use a production machine for the experimental rootfs.
