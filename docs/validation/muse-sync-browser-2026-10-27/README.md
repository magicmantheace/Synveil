# Muse browser and owner-test package synchronization, 2026-10-27

Fixed Muse snapshot: `677421cfc81bf3a193b2bd00d3d26619f7f3979a`,
`magicmantheace/shiny-dollop`, branch `synveil/first-boot-desktop-muse`,
prefix `projects/synveil/`. Reviewed 46 commits following the previously
transferred `dcaf954c63c2004fbd6bebf3b0ce8c6c0ff3fe31`.
Original development branch remains at `318a5c90ed20d13b86dc95f21c8e341099699584`.
Integration is on `development/muse-verified-sync-2026-10-24`, PR #40 only.

## Accepted implementation and evidence

- Pinned Chromium package-closure stage, asset seal, native-glibc overlay,
  authenticated non-root Wayland launch and sandbox evidence probe.
- Persistent browser profile and Downloads directory; read-only graphical
  home/Downloads listing with Refresh (not a full file manager).
- Release-media sizing from payload length and completed payload readback
  verification, replacing the fixed-size media that truncated large UKIs.
- Hardware-target A/B assembly, journal-aware readback, disk-lock wait,
  full-stack framebuffer/USB acceptance harness and fixed QMP chords/typing.
- Current stock browser configuration: no vendor hosts null-routing, no
  managed privacy policy, no connection-blocking launcher flags. Sandbox
  and TLS checks remain. IPv6 net-log records are informational.
- Historical browser receipts/logs are
  copied byte-for-byte and attributed to Muse, not relabeled as independent
  original-repository guest tests. Earlier privacy claims describe superseded
  images; the current package is stock-configured.

All 23 transferred files match the fixed Muse snapshot byte-for-byte.
Only integration-specific verification and this record are additional work.
No independent runtime functionality was added.

## Deferred or rejected

- Unfinished NetSurf fallback: `build/manifests/native-browser.json`,
  `tools/native_browser.py`, `tools/native_browser_assets.py`. Not needed
  for the verified Chromium route and not proven built/sealed. References
  in attributed upstream documentation describe Muse's fallback, not a
  transferred or verified original-repository implementation.
- Current test-media record and all three JSON receipts are deferred.
  The README truncation at d3a7d1d is fixed at 677421c, but the restored
  text says the broken-state boot was skipped while both JSONs still
  claim `broken_state_fails_closed_to_locked_login: true`, with no skip
  field. No source/image identity changed in this one-file repair.
- OWNER_FIRST_BOOT_TEST.md and PHYSICAL_BOOT.md readiness updates are
  deferred with that package record. Original physical-test restrictions
  remain in place; no incomplete receipt is promoted to verified status.
- Intermediate restrictive browser configurations and ROADMAP completion
  claims are not applied.

## Independent local results in the original repository

- `python3 tests/smoke-gates.py`: 30/30 PASS, including IPv6 and non-gated
  vendor-egress fixtures. Printed simulated QEMU PASS markers are unit
  fixture output, not guest boots.
- `python3 tests/write-test-media.py`: 15/15 PASS.
- `python3 tests/ab-update.py`: 18/18 PASS. The earlier review environment's
  OpenSSL timeout did not recur.
- `python3 tests/qemu-options.py`: 14/14 PASS.
- `python3 tests/ab-release-crosscheck.py`: nine interoperability/refusal
  tests PASS; fresh real-image construction errors because `sgdisk` is
  absent. This is not reported as 10/10 locally.
- `python3 tests/qmp-input.py`: 16 tests ran, 12 error reports/subtests
  from blocked AF_UNIX creation (EPERM). Ordinary Ubuntu CI reruns the
  unchanged socket fixtures; this local failure is not suppressed.
- `python3 tests/muse-browser-transfer.py`: stored browser-evidence check
  PASS, one real large-image packaging test skipped locally for missing
  GPT tooling. It runs without skips in CI with those tools installed.
- Shell syntax, Python compilation, code whitespace checks PASS.

CI checks the actual integration revision: Rust formatting/Clippy and
workspace tests under umasks 0007, 0022, 0077; real GPT/ext4 and release
construction; all transferred gates; a 192 MiB payload packaged and read
back from the finished GPT media; source-pinned protocol generation and
warning-clean update/files window compilation; complete existing host tier.
Initial CI status PENDING; PR #40 records exact-revision results.

## Attributed QEMU evidence and practical limits

The deferred owner-package build and both VGA/USB receipts identify source
`9dbeda0d2be75c3f6e3267c949ec6db3656193e3` and disk SHA-256
`4be22adaaddb4c5e5abab87d8d636abc18cd4f4e67265b0a1fac188db1925553`.
The current package carries no proof CA, fixture host or startup URL.
Both transferred historical browser proof receipts pass the current semantic validator.
The current package broken-state contradiction is reproduced, not resolved.
See MUSE_HANDOFF.md in this directory.
The owner-package receipts explicitly record no physical-hardware validation
and no drag/resize acceptance for this A/B package. Their browser evidence
is launch markers and persistent profile/Downloads directories, not a new
full HTTPS/download/update acceptance on this exact stock image. Historical
browser acceptance and A/B updater evidence apply to their named images.

This worker has no source-built SDK, Chromium stage payload or disk-image
bytes. No independent native QEMU boot, artifact checksum recomputation,
physical TG01 compatibility or public-Internet browsing is claimed. Full
browser-window runtime compilation/boot remains dependent on those inputs.
The publisher is development-only; trusted production releases and installed
system repair remain incomplete. DATA is only 384 MiB in this preview.

No physical writes, firmware/Windows changes, merge, release, force-push or
shiny-dollop modifications. FIRST_INSTALL.md restrictions and the original
roadmap are unchanged. Muse remains primary. Independent development stays
paused after this bounded synchronization.

## Transferred files

- `build/manifests/distro-chromium.json`
- `build/rootfs/synveil-browser`
- `build/rootfs/weston-session`
- `docs/BROWSER.md`
- `docs/validation/browser-privacy-proof-2026-10-27/README.md`
- `docs/validation/browser-privacy-proof-2026-10-27/boot1-serial.log`
- `docs/validation/browser-privacy-proof-2026-10-27/boot2-serial.log`
- `docs/validation/browser-privacy-proof-2026-10-27/boot3-serial.log`
- `docs/validation/browser-privacy-proof-2026-10-27/browser-receipt.json`
- `docs/validation/browser-privacy-proof-2026-10-27/build-receipt.json`
- `docs/validation/browser-qemu-proof-2026-10-27/README.md`
- `docs/validation/browser-qemu-proof-2026-10-27/browser-receipt.json`
- `docs/validation/browser-qemu-proof-2026-10-27/build-receipt.json`
- `src/desktop-wayland/files-window.c`
- `tests/smoke-gates.py`
- `tests/smoke-native-browser.py`
- `tests/smoke-native-joined-hardware.py`
- `tools/ab_release.py`
- `tools/distro_chromium.py`
- `tools/distro_chromium_assets.py`
- `tools/native_ab.py`
- `tools/native_joined.py`
- `tools/qmp_input.py`
