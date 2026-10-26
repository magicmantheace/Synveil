# Hardware-profile joined image: QEMU boot validation + owner test package — @ 124abbc code (2026-10-25)

Fourth development pass on branch `synveil/first-boot-desktop-muse`
(PR #17), following `docs/validation/joined-124abbc/`. That pass built
the hardware-profile image but never booted it. This pass boot-
validates it as far as QEMU allows, closes the PHYSICAL_BOOT.md gates
that are satisfiable without physical hardware (each with evidence),
and assembles the owner test-media package. Everything below was
**actually run in this environment during this pass**.

## Result summary

- **The hardware-profile joined image passes the full joined
  acceptance in QEMU/OVMF**: graphical enrollment (mismatched
  confirmation visibly rejected) → login → Weston desktop with
  pointer move/resize and keyboard input → session-end relock → cold
  reboot with wrong password denied → login → desktop again. Marker:
  `SYNVEIL_NATIVE_JOINED_HARDWARE_QEMU_ENROLL_LOGIN_WESTON_PASS`
  (`out/pass-logs/joined-hw-smoke4.log`, exit 0).
- The hardware kernel pins `console=tty0`, so the guest emits nothing
  on serial and the serial-marker driver of the qemu-target
  acceptance cannot observe it. A new driver,
  `tests/smoke-native-joined-hardware.py`, synchronizes on the
  framebuffer scanout instead: the auth screens are identified
  relationally from their deterministic layout (dark background, top
  band, title/subtitle/footer bands at fixed fractional positions in
  `auth_render`), never by pinned hashes, so the test is independent
  of GOP resolution. The measured band digests reproduced the
  exploratory run's values exactly.
- Two extra failure-mode boots passed: a **hard power cut during an
  active desktop session** (emulator killed; QEMU runs `-no-reboot`,
  so an in-guest reset cannot be used) returns to the same login
  screen with the credential intact, and a boot with the **state
  partition's filesystem destroyed host-side** fails closed to a
  locked login screen — no enrollment offered, the test password
  denied, no desktop ever appears.
- **Serial evidence**: across all four QEMU runs the serial capture
  contains only OVMF `BdsDxe` firmware lines and QEMU's own
  termination notice — zero guest markers, no shell banner, no
  developer-recovery marker. The hardware image's initramfs contains
  the `auth-desktop` marker and **no** `developer-serial-recovery`
  marker (inventory taken this pass).
- **tty0/UEFI-framebuffer gate closed**: the acceptance recorded 3
  distinct live text-console frames on the UEFI (OVMF) framebuffer
  before the greeter took over, and the full auth UI + Weston desktop
  rendered through that framebuffer path at 1280×800.
- **Owner test-media package assembled** at `out/test-media/`
  (not in git): image copy (digest re-verified after copying), the
  owner guide, the guarded Linux writer, `PACKAGE.json`,
  `SHA256SUMS` — `sha256sum --check` passes on all four entries.

## Validation actually run this pass

| Check | Result |
|---|---|
| Hardware joined acceptance (`tests/smoke-native-joined-hardware.py`, boots 1–2 + power-cut boot + broken-state boot) | **PASS** — marker above; `out/native-joined-hardware/guest-validation.json`: framebuffer 1280×800, `tty0_console_frames_before_greeter: 3`, mismatch rejected, wrong password denied after cold reboot, `hard_power_cut_returns_to_login_and_credential_survives: true`, `broken_state_fails_closed_to_locked_login: true`, `serial_contains_no_guest_console_or_shell: true`, `physical_hardware_tested: false` |
| Serial captures (4 runs, `out/native-joined-hardware/smoke-evidence/`) | **PASS** — firmware lines only; no `SYNVEIL_` guest markers, no shell banner |
| Hardware initramfs inventory | auth-desktop marker present; developer-serial-recovery marker **absent**; veil-core, veil-seat, synveil-display, mount-state, recovery-status present |
| Writer refusal paths (`tools/write-test-media.sh`) | **PASS** — refuses on digest mismatch, partition device name, missing image, non-removable device class. The removable/USB check itself is code-reviewed (no USB block device exists in this VM to exercise it); the positive write path was **not** executed against a real device here — its read-back digest verification is the same dd+sha256sum pattern, and the owner's Windows path uses Etcher's automatic validation |
| Package verification | **PASS** — `sha256sum --check SHA256SUMS` all OK; image digest `6b10b482…fc3` matches the build record |
| Offline quick tier | **PASS**, exit 0 — receipt `out/local-validation/20261026T003347Z-a6ef5112-6137/results.json` |
| Offline host tier | **PASS**, exit 0 — receipt `out/local-validation/20261026T003654Z-a6ef5112-11715/results.json` |

Provenance note: the acceptance ran with the new driver still
uncommitted at tree `a6ef511` (the `source_commit` inside
`guest-validation.json`); the driver and writer are committed in this
pass with byte-identical content, and the docs commits follow, per
the established record pattern.

Driver fixes during the run (test-only, no production code changed):
the QMP socket path exceeded qemu.sh's 100-char cap when nested
inside the image work dir (temp dir now sits directly under TMPDIR),
and the reset-recovery step was redesigned as a hard power cut after
QEMU's `-no-reboot` turned `system_reset` into an emulator exit.
The environment also lost its apt layer mid-pass (QEMU vanished);
`out/pass-logs/restore-env.sh` restored it, as in earlier passes.

## Gate dispositions (detail in PHYSICAL_BOOT.md)

- **Closed**: tty0 diagnostics + UEFI framebuffer in a QEMU UEFI
  graphical session; owner-verified provenance + test-media
  identification + checksums (SHA-256 pinned here and in the package;
  the image remains **unsigned** — "owner-verified" is the checksum
  route, with Secure Boot handling documented for the owner);
  removable-media preparation method (guarded writer + reviewed
  Windows steps in the owner guide).
- **Extended evidence, already closed**: non-root session / no
  serial or local root-shell bypass — now also demonstrated on the
  hardware-profile image (serial silent, fail-closed boots).
- **Still open**: launcher/file-manager/settings applications (the
  desktop gate's remaining content); the independent console/recovery
  gate — bounded supervisor restarts, fail-closed relock, power-cut
  recovery and broken-state degradation are proven, and the read-only
  `recovery-status` mode exists in the initramfs but is unreachable
  under this image's fixed kernel command line; the interactive
  owner recovery of ADR 0010 remains a separate, owner-approved
  program. For this disposable test media the documented recovery
  procedure is a media rewrite; HP firmware compatibility and
  physical validation — only the owner's TG01 test can close them.

## Honest limits

> **Correction (2026-10-25, review-resolution pass):** the original
> text of this section said the simpledrm path was "exercised here".
> It was not: this pass ran on virtio-vga, and a diagnostic twin
> later proved the driver in use was **virtio_gpu** (DRM minor 0,
> fbcon primary), with simpledrm present only as a secondary
> placeholder. The simpledrm path and the full-image USB boot have
> since been exercised for real — see
> [`../review-resolution-2026-10-25/README.md`](../review-resolution-2026-10-25/README.md).
> The sentence below is corrected accordingly.

- **No physical hardware was tested.** QEMU's GOP framebuffer via
  virtio-vga stands in for the RTX 3060's firmware GOP. The hardware
  kernel carries **no nouveau and no NVIDIA driver**
  (`CONFIG_DRM_NOUVEAU` unset): on the TG01 the display will run on
  the firmware framebuffer through simpledrm with software
  rendering, at the firmware-chosen resolution. That path was **not**
  exercised by this record (the acceptance ran on virtio_gpu); it
  has since been exercised in the review-resolution record above.
  HP firmware behavior, the 3060's GOP mode and USB HID/storage
  timing on the real machine are all unproven.
- Storage here was virtio-blk. The late-enumeration USB state proof
  (ADR 0019) was run on the authenticated image family sharing this
  rootfs and init; the joined hardware image itself has not been
  booted from USB storage anywhere.
- The desktop offers the first-window app and weston-terminal only.

## Image artifacts

| Artifact | Size | SHA-256 | Status |
|---|---|---|---|
| `out/native-joined-hardware/synveil-x86_64.img` | 402,653,184 B | `6b10b482239ca3e771bd20d85a5f4e0dc414a00fc34334408eb134b150bacfc3` | Hardware-profile joined image; **QEMU boot-validated this pass** (record above); physical hardware NOT tested |
| `out/test-media/` package | — | image entry identical to above | Owner first-boot test package: image + `PACKAGE.json` + `SHA256SUMS` + owner guide + guarded writer; not in git |
