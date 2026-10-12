# Offline validation while GitHub Actions are unavailable

Synveil development does **not** require a GitHub Actions runner. Run the
following from the repository checkout on a Linux host:

```bash
python3 tools/validate_offline.py
```

The runner executes existing Python, C, shell and Rust host fixtures using
their committed inputs. Rust commands use `--offline --locked` and
`CARGO_NET_OFFLINE=true` to prevent unexpected registry downloads.
The correct Rust toolchain and any Rust crate dependencies therefore need
to be installed or cached **before** running the check.

Evidence goes to `out/offline-validation/<commit>/report.json` with
one log and its SHA-256 per command. `failed` means a check failed;
`blocked` means a missing executable, timeout, or the requested
partial mode. A blocked or failed run never reports overall success.

If Rust is unavailable, use `--skip-rust` to run partial checks,
but the result remains **blocked** even when all selected checks pass.

```bash
python3 tools/validate_offline.py --skip-rust
python3 tests/offline-validation-fixture.py
```

## Optional isolated QEMU checks

Use `--guest` **only** after building the developer-QEMU EFI
image and prerequisites using the pinned toolchain. This is a deliberate
opt-in, and never launches on an unbuilt machine.

```bash
python3 tools/validate_offline.py --guest
```

This runs native boot/status, locked-seat denial and a separately staged
graphical-login guest fixture. The graphical fixture creates its own state
disk and never formats or selects a physical disk. A passing host-only report
does **not** validate native target linking, QEMU behavior or a physical
installer.

### Known last build issue

The archived QEMU native run at commit `9d441279a2ff` failed when
compiling `src/veil-core/src/seat.rs` because the `FileTypeExt`
trait was not imported. The next development commit added that import and
corrected Rust formatting, but its native x86_64 target link remains to be
validated locally. Do not infer passing native evidence from the fix alone.

Do not test-install on a physical computer yet. The current bootstrap
developer console is unauthenticated and the graphical login is a gated
preview, not first-use account enrollment or a production display manager.
