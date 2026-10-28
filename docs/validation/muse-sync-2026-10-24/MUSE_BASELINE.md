# Monorepo baseline re-validation — shiny-dollop @ d3c7822 (2026-10-24)

First development pass after the import of the Synveil first-boot snapshot
(pinned upstream `318a5c90`) into `projects/synveil/` of
`magicmantheace/shiny-dollop`, branch `synveil/first-boot-desktop-muse`.
This record separates what was **actually rerun in this repository and
environment** from historical upstream evidence, which remains labeled
historical and is not repeated here.

## Environment

- Linux VM, 2 CPUs, 7 GiB RAM, x86_64; shell umask **0007** (this matters —
  see "Root cause" below); processes run as root except where a tool
  refuses root and is run as the unprivileged `nobody` account.
- QEMU: `qemu-system-x86_64` 8.2.2 (Ubuntu package), OVMF at
  `/usr/share/OVMF/OVMF_CODE_4M.fd` + `OVMF_VARS_4M.fd`. Installed via apt
  during this pass; QEMU was **not** present at the start of the pass.
- Weston 13.0.0 (Ubuntu package) for host protocol probes only.
- Rust: rustup toolchain **1.99.0** (matches `rust-toolchain.toml`),
  installed to `/opt/cargo`; crates fetched once, then `--offline` used.
- `bash build.sh doctor`: all host prerequisites usable (after installing
  the documented Debian/Ubuntu build dependencies).

## Fixes committed this pass (all test-fixture scope; no production
## safety check was weakened)

- `c0a45aa` — `tests/desktop-supervisor.py`,
  `tests/state-mount-safety.py`: pin the auth-marker mode (exactly 0644)
  and the fixture umask (0022) instead of inheriting the ambient umask.
- `943e211` — `src/veil-core/src/audit.rs` (`test_dir`),
  `src/veil-core/tests/audit.rs`: pin journal-parent fixture directories
  to 0700.
- `d3c7822` — `src/veil-core/src/credential.rs`: formatting only
  (`cargo fmt` with the pinned toolchain); the imported file failed
  `cargo fmt --check` in four hunks.

### Root cause

The production checks are strict and correct: the desktop supervisor
requires the rootfs auth marker to be mode 0644, the state-mount helper
rejects group/world-writable privileged parents, and the audit writer
refuses a journal parent with any group/world write bit. Several test
fixtures created files/directories with the ambient umask. At umask 0022
(upstream CI) everything passed; at this VM's umask 0007 the fixtures
produced 0660/0770 modes and tripped the checks. Baseline failures before
the fix: quick tier `bootstrap` (1 test) and `state-mount` (3 tests);
Rust audit unit tests 17 of 125; both audit integration tests reported a
degraded sink. All green after the fixture fixes, verified under umask
0007, 0022, and 0077 for the two shell fixtures.

## Results actually rerun at d3c7822

| Check | Command | Result |
|---|---|---|
| Offline quick tier | `bash build.sh offline-check --level quick` | **PASS** — 25 pass, 2 blocked (`qemu-lock`, `qemu-options`: require a built native image, not QEMU presence); receipt `out/local-validation/20261024T011317Z-d3c78224-14804/results.json` |
| Offline host tier | `bash build.sh offline-check --level host` | **PASS** — quick tier + `cargo fmt` clean + `cargo test --offline` + `cargo clippy --offline -- -D warnings` clean; receipt `out/local-validation/20261024T014018Z-d3c78224-30582/results.json` |
| Rust tests (direct) | `cargo test --offline` (umask 0007) | **155 passed, 0 failed** (4 synctl + 125 veil-core lib + 2 audit integration + 24 across remaining suites) |
| Weston headless probe (fixtures) | `python3 tests/weston-headless.py` | 5 tests OK |
| Weston headless probe (real) | `python3 tools/weston_headless_probe.py --timeout 45` as `nobody` | **passed** — `{"backend":"headless","compositor":"weston","renderer":"pixman","windows":2,"native_synveil":false,"rgb_scanout":false}`. Protocol-level multiwindow only, per `docs/WESTON_NATIVE_PORT.md`. |
| First-window fixtures | `python3 tests/first-window-probe.py` | 7 tests OK |
| xdg-shell generation | `python3 tools/native_xdg_shell.py --generate` as `nobody` | pinned wayland-protocols 1.49 + Wayland 1.26.0 scanner, SHA-512 verified (`WAYLAND_SOURCE_SHA512_VERIFIED`) |
| First-window host run | `gcc` host build of `src/desktop-wayland/first-window.c`, then `tools/first_window_probe.py` as `nobody` | **passed** all three modes: `SYNVEIL_HOST_WESTON_SHM_XDG_WINDOW_VERIFIED`; `--exercise-resize` → `SYNVEIL_HOST_WESTON_RESIZE_REPAINT_VERIFIED`; `--exercise-resize --two-windows` → passed |
| Source lock (desktop prereqs) | `tools/native_pixman.py --check-lock/--verify-source`, `tools/native_libffi.py --verify-source` (as `nobody`) | `NATIVE_PIXMAN_PIN_OK`, `NATIVE_PIXMAN_SHA512_VERIFIED`, `LIBFFI_UPSTREAM_SHA256_VERIFIED` |
| Source lock (bootstrap) | `python3 tools/source_lock.py verify` | **all 5 archives verified**: linux 6.18.55, binutils 2.46.1, gcc 16.2.0, glibc 2.44, busybox 1.38.0 |

### Source-fetch transport note (no lock change)

`tools/source_lock.py fetch` downloads via Python urllib in a single
request with no resume. Through this VM's HTTP proxy, large urllib
transfers were repeatedly truncated mid-stream (binutils arrived at
23.2 MB and 24.6 MB of 28.6 MB in two attempts, each with a different
digest). The lock **correctly failed closed** on the digest mismatch
both times. The cache under `.cache/sources/archives/` was therefore
seeded with `curl -C -` (resumable) downloads of the exact manifest URLs,
and `source_lock.py verify`/`fetch` then verified every archive against
its pinned SHA-512 before acceptance (binutils manual check:
`a5c65e56…` matches the lock exactly). The lock, digests and verification
path are unchanged; only the transport used to fill the local cache
differed. A future improvement is resumable/retrying fetch inside
`source_lock.py`; it is not required for provenance, which the digest
check already guarantees.

## Not proven by this pass

- **No native Synveil image was built or booted.** The Phase 1 toolchain
  build (`bash build.sh toolchain`, from the verified source cache) was
  started after these checks; its result belongs to a later record.
  `bash build.sh native-check` currently reports the target GCC missing,
  as expected before the toolchain exists.
- **No QEMU guest boot of any Synveil image** was performed. The
  `qemu-lock`/`qemu-options` offline checks remain blocked on a built
  image. Nothing here is native multiwindow, authentication, persistence,
  installer or updater evidence.
- No roadmap checkbox changes are justified by this record.
- Physical hardware (HP Pavilion TG01) remains entirely untested.

## Next steps (in order)

1. Complete `bash build.sh toolchain`, then `toolchain-cxx` and the
   libatomic stage (`build/scripts/toolchain-atomic.sh`).
2. `python3 tools/native_weston.py --build` (full source-built compositor
   component set) — the Cairo pthread blocker was root-caused upstream to
   the missing native libatomic; confirm the repair at this revision.
3. `tools/native_weston_vm.py --build` + `tests/smoke-native-weston.py`
   under the now-installed QEMU/OVMF for the first native-image
   multiwindow guest evidence.
