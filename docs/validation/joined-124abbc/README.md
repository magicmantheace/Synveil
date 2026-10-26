# Joined authenticated Weston image, GPT state policy, USB-late-enumeration — @ 124abbc (2026-10-25)

Third development pass on branch `synveil/first-boot-desktop-muse`
(PR #17), following `docs/validation/qemu-desktop-auth-f48771c/`.
Everything below was **actually run in this environment during this
pass**; nothing is carried over from earlier records.

## Result summary

- **One joined image** now carries the whole first-boot flow: the
  production rootfs profile (NATIVE+GRAPHICS+PERSISTENCE+AUTOSTART+
  AUTH_DESKTOP) boots to graphical enrollment on VT2, then graphical
  login, then — in the same boot, same image — a multiwindow Weston
  desktop session on tty1 behind the authenticated seat. The QEMU
  acceptance (`tests/smoke-native-joined.py`) passes end to end,
  twice (two independent QEMU boots): enroll (with a mismatched
  confirmation rejected first) → login → Weston desktop with pointer
  move/resize and keyboard input proven from scanout pixels → session
  end relocks → cold reboot → wrong password denied → login → desktop
  again. State persists on the image's own state partition; no
  separate state disk is attached (`separate_state_disk_used: false`).
- **Physical storage policy replaces the `/dev/vdb` pin.** The state
  volume is a GPT partition identified by PARTUUID
  `7c9e2d41-8b3a-4f5e-a6d1-2e8c4b7f9053` / PARTNAME `Synveil State`
  (ADR 0017). Both the initramfs mount helper and the credential
  layer resolve exactly that identity and fail closed on duplicates.
  The image builder can stamp the partition into the install image
  itself (`SYNVEIL_STATE_SIZE_MB`), so enrollment and verification
  run against storage the image provides.
- **Late-enumerating USB state media works** (ADR 0019): the mount
  helper rescans for a bounded ~10 s while no partition matches. In
  the USB proof below the wait genuinely engaged (the serial log
  contains the waiting notice) and enrollment + cold-reboot login
  completed with the state disk attached as `usb-storage` — the
  arrangement that matches the owner's external-USB-SSD install
  target.
- **Window maximize/restore regression fixed**: a window restored
  from maximized now returns to its floating size instead of staying
  at the maximized size (interaction with the 28888da zero-configure
  fix; see commit `124abbc`).
- The hardware-profile build (`SYNVEIL_BOOT_TARGET=hardware`) of the
  joined image was produced; see the artifact table. It is a build
  artifact only — **no physical hardware was tested this pass.**

## Production bugs found by in-guest forensics and fixed

1. `3b27616` — the state-disk helper created the partition 33 sectors
   smaller than the filesystem it wrote (end-relative sgdisk
   arithmetic), so the guest kernel refused the mount in the
   separate-disk arrangement. Partition and filesystem sizes are now
   exact; a size regression test is pinned in `tests/state-disk.py`.
2. `cf73400` (+ ADR 0018) — the login greeter held VT2 in KD_GRAPHICS;
   the kernel silently refuses VT switches away from a KD_GRAPHICS
   console in VT_AUTO mode, so the session's `VT_ACTIVATE(1)` timed
   out and every graphical login died into relock. The greeter now
   drops VT2 to KD_TEXT (verified) immediately before the password
   attempt and restores K_OFF+KD_GRAPHICS on relock.
3. `117c88b`, `033dc83` — under this VM's umask 0007, `rootfs.sh`
   produced a tree the UID-1000 session could not traverse (0770
   directories, 0660 /etc files, 0770 display binaries). The script
   now pins `umask 022`; the credential store is unaffected (it lives
   on the state volume, not in the rootfs).
4. `48af350` — the production init never mounted devpts, so
   weston-terminal died ~3 s into the desktop ("failed to fork and
   create pty"). The Weston fixture's private init had masked this.
   Production init now mounts devpts like the fixture does.
5. `2443f82` — test-only: the joined smoke screenshotted before
   Weston's first frame; blank frames are now polled as "not ready".

## Storage policy (ADR 0017 + 0019)

- Identity: GPT PARTUUID `7c9e2d41-8b3a-4f5e-a6d1-2e8c4b7f9053`,
  PARTNAME `Synveil State`, ext4 label `SYNVEIL_STATE`, type 8300.
- `build/rootfs/mount-state` resolves the partition through sysfs
  uevent data; exactly one match may exist or boot continues without
  persistence (fail closed on duplicates — a cloned disk cannot
  silently split the credential store).
- While zero partitions match, the resolver rescans every 0.25 s up
  to a ~10 s bound (late USB enumeration); finding the partition
  later within the bound is normal, not an error.
- `tools/state_disk.py` creates fixture disks with the same identity;
  `tools/native_joined.py` stamps ESP (256 MiB) + state (128 MiB)
  into one 384 MiB GPT image.

## Validation actually run at the final head (124abbca)

| Check | Result |
|---|---|
| Joined QEMU acceptance (`tests/smoke-native-joined.py`, two boots) | **PASS** — `SYNVEIL_NATIVE_JOINED_QEMU_ENROLL_LOGIN_WESTON_PASS`; `out/native-joined/guest-validation.json`: enrollment graphical, mismatch rejected, wrong password denied after cold reboot, Weston after login, pointer move/resize/keyboard, relock, `state_on_image_provided_partition: true`, `separate_state_disk_used: false`, `physical_hardware_tested: false` |
| USB state proof (auth image + `SYNVEIL_QEMU_STATE_USB=1`, two boots) | **PASS** — enroll → login → relock; cold reboot: wrong password denied → login → relock; serial contains the late-enumeration waiting notice, so the ADR 0019 wait was exercised, not bypassed. Log: `out/pass-logs/usb-proof-124abbc.log` |
| Graphical-login fixture (`bash build.sh guest-fixture --case graphical-login`) | **PASS** — "isolated QEMU graphical-login passed" at 124abbca |
| Graphical-onboard fixture (`--case graphical-onboard`) | **PASS** — `validation.json`: `result: passed`, `first_user_enrollment: true`, `persistent_authentication: true`, `wrong_password_denied_after_cold_reboot: true` at 124abbca |
| Offline quick tier | **PASS**, exit 0 — receipt `out/local-validation/20261025T222114Z-124abbca-97259/results.json` |
| Offline host tier | **PASS**, exit 0 — receipt `out/local-validation/20261025T223933Z-124abbca-6611/results.json`. (A first invocation without the Rust toolchain env reported rustfmt/rust-tests/clippy as *blocked*; rerun with the documented env passes outright.) |
| First-window probe (`tools/first_window_probe.py --exercise-resize`) | **PASS** — `SYNVEIL_HOST_WESTON_RESIZE_REPAINT_VERIFIED`; sizes 400x240 → 1024x768 (maximized) → 400x240 (restored). Pre-fix binary under the same compositor ends at 1024x768. Host compositor is Weston 13.0.0 (apt); the sealed target compositor is 15.0.1, and the configure(0,0)-on-restore behavior reproduced identically. |
| Weston smoke (`tests/smoke-native-weston.py`, fixture image rebuilt at this head) | **PASS** — `SYNVEIL_NATIVE_QEMU_WESTON_RGB_WINDOWS_INPUT_PASS`; image SHA-256 `2e2d3b5c126791d42e181e6c8a5185d6c2f5bc4af0a5905b04e52f4eb9cc92a7` |
| Hardware-profile joined build (`tools/native_joined.py --build --boot-target hardware`) | **BUILT**, exit 0 — `validation.json` records `boot_target: hardware`, `kernel-boot-target.txt` = `hardware`, `physical_boot_validated: false`; artifact in the table below |

## Image artifacts produced this pass

| Artifact | Size | SHA-256 | Status |
|---|---|---|---|
| `out/native-joined/synveil-x86_64.img` (qemu target) | 402,653,184 B | `2a9afb30c645c2a8330ef3552759cb636e6fe891d368b0aabf95e59319c4785d` | Joined authenticated Weston image; QEMU acceptance PASS (above) |
| `out/native-joined-hardware/synveil-x86_64.img` (hardware target) | 402,653,184 B | `6b10b482239ca3e771bd20d85a5f4e0dc414a00fc34334408eb134b150bacfc3` | Build artifact only; NOT boot-validated anywhere; physical hardware NOT tested |
| `out/synveil-x86_64.img` (auth-desktop profile) | 134,217,728 B | `51a51b2df2514708e055b775aba8cec963ede17baae49c5cc98f3da854bbb88b` | Framebuffer auth image used for the USB state proof |

## Environment notes

- The VM's system layer was reset repeatedly (apt packages, /tmp);
  QEMU 8.2.2 + OVMF and the build tools were reinstalled each time
  (`out/pass-logs/restore-env.sh`). One kernel build survived a reset
  as an orphaned `make` and completed; artifact mtimes/hashes — not
  process exit claims — were used to confirm completion.
- QEMU ran under TCG (no /dev/kvm); guest time runs behind wall time.
- Host umask remains 0007; the rootfs umask fix above is the durable
  guard.

## Honest limits

- **No physical hardware was tested.** `docs/PHYSICAL_BOOT.md` still
  reads "Not ready for owner hardware testing": the images are
  unsigned (Secure Boot may reject them), no removable-media
  preparation method has been reviewed, and HP firmware/USB-boot
  behavior is unverified.
- The A/B installer/updater and graphical update flow remain
  unexercised.
- The desktop session offers the first-window app and weston-terminal;
  there is no launcher, file manager, or settings application yet.
- The joined acceptance ran with the QEMU boot target; the
  hardware-target artifact shares the rootfs/userspace but its kernel
  differs and it has not been booted anywhere.
