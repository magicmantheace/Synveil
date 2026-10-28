# Test-media record — 2026-10-27 (full-stack owner package)

The owner test-media package was rebuilt from the current head
and the four points of the 2026-10-25 independent review were
re-proven **on this image** (nothing is inherited from earlier
images). This is the package's second rebuild of the day: the
first (source `9be2404`, image `283b1974…`) was superseded
before delivery by the owner's final decision on browser
connections (below), and nothing from it ships.

**Browser configuration of this image:** stock. By the
owner's explicit decision of 2026-10-27 ("I don't want you
blocking connections at all"), the shipped Chromium carries
**no connection blocking in any layer** — no `/etc/hosts`
null-routing, no managed policy, no blocking launcher flags
or seeded preferences. What remains is the integration that
is not connection control: Wayland/Ozone launch, the kernel
sandbox, TLS verification, the persistent profile, and a
profile seed pinning only the download directory. The
earlier same-day states (the three-layer privacy regime, the
IPv6 gate correction, then the accounts.google.com
exception) are recorded in `docs/BROWSER.md` and the addenda
to the browser-privacy record; that record describes a
superseded image.

1. **Writer safety** — `tests/write-test-media.py`: 15/15
   fixture refusal tests pass at the final head. The writer
   traces every mounted filesystem and swap device through
   sysfs backing-device relationships (device-mapper/LVM/LUKS,
   md, loop) to the whole disks behind them, fails closed when
   a chain cannot be determined, pins the image digest,
   requires an exact device-name confirmation, and verifies a
   full read-back digest. Fixtures only; no device was
   written.
2. **Graphics path** — the `vga` variant below: the complete
   image passed the full framebuffer acceptance with the
   display on QEMU **standard VGA** (no virtio GPU attached).
   Driver basis, stated exactly: the hardware kernel carries
   `CONFIG_DRM_SIMPLEDRM=y` and `CONFIG_DRM_BOCHS` is **not
   set** (verified from the gen-v1 build manifests), so with
   no other DRM driver able to bind the VGA adapter, the
   session ran through **simpledrm on the EFI framebuffer** —
   the same path the TG01's RTX 3060 will use. Physical RTX
   3060 behaviour remains explicitly unverified
   (`physical_hardware_tested: false` in both records).
3. **USB storage boot** — the `usb` variant below: the
   complete image booted from emulated USB storage
   (`qemu-xhci` + `usb-storage`) through the full sequence:
   graphical enrollment, mismatched confirmation rejected,
   cold reboot, wrong password denied, login, desktop **with
   the browser**, session-end relock, hard power cut returning
   to the same login screen with the credential intact, and a
   destroyed-STATE boot failing closed to the locked login —
   state on the image-provided STATE partition throughout.
   The browser's presence is not implied: the harness read
   the DATA partition back from the booted disk copies and
   found the sessions' browser logs with
   `SYNVEIL_BROWSER_STARTED` markers (2 in the VGA run, 3 in
   the USB run), the Chromium profile, and the Downloads
   directory (`chromium_launched_and_persisted_on_data: true`
   in both records).
4. **Claims discipline** — the package documents
   (`OWNER_FIRST_BOOT_TEST.md` in the package,
   `docs/PHYSICAL_BOOT.md`, package `PACKAGE.json`) contain
   no absolute "Windows cannot be affected" language; they
   enumerate the specific protections: the guest's writes
   confined to its own STATE/DATA partitions and (when
   staging) its inactive system slot, all located by fixed
   GPT identity; no formatter, partitioner or EFI-variable
   tool in the image; the boot/update design writes **no
   firmware variables** (ESP fallback + kexec chain-load);
   the one-time boot menu leaves the permanent boot order,
   the Windows ESP and Windows Boot Manager untouched; the
   media write is the only destructive step — guarded on
   Linux (digest pin, whole-removable-USB-only,
   backing-traced refusal failing closed, exact-name
   confirmation, read-back verify) and procedural-only on
   Windows, named there as the weaker path.

## Identities

- Build source commit:
  `9dbeda0d2be75c3f6e3267c949ec6db3656193e3`
  (`tools/native_ab.py --build --browser --boot-target
  hardware`). This source is the unblocking change itself:
  hosts null-routing, managed policy, blocking flags/seed,
  the receipt's privacy fields, and the gate's egress
  conditions all removed; `tests/smoke-gates.py` carries the
  positive fixture `test_vendor_egress_not_gated` (the IPv6
  extraction work remains, as receipt information only).
- Disk image `synveil-x86_64.img`: SHA-256
  `4be22adaaddb4c5e5abab87d8d636abc18cd4f4e67265b0a1fac188db1925553`,
  3,900,702,720 bytes. Layout (ADR 0011): ESP 768 MB,
  SYSTEM_A 768 MB (v1 installed), SYSTEM_B 768 MB (empty
  until an update stages), STATE 256 MB, DATA 384 MB,
  RECOVERY 768 MB.
- Release media `synveil-release-v2.img` (in the package):
  SHA-256
  `172888a85d490506ef8a4947ffbbfbe8ab1232d5af66de227b319234f57f6f3d`.
- Browser: Chromium `156.0.8078.47-1~deb13u1`,
  stock-configured, and **no proof material** — the build
  receipt records `test_ca: None`, `test_host: None`,
  `start_url: None`, and its browser record carries no
  privacy/null-routing fields at all (the fields no longer
  exist).
- Publisher: development key `synveil-dev-2026-01`
  (`development: true` in the build receipt).

## What the guest records do and do not claim

- The A/B desktop proof is a **peel**, not the joined image's
  drag/resize choreography: the five full-stack windows stack in
  varying order, so each window is closed by its own proven
  primitive (browser by focus click + Ctrl+Shift+W after its
  page settles, files and update GUI by their drawn close
  boxes, first window by raise + Escape), the terminal is
  focused and exited by the typed fixed `exit` command, and
  success is the login screen's return — the session's own end
  condition. `pointer_move_resize_keyboard` is recorded
  **false** for this image and
  `desktop_windows_closed_and_terminal_exited` true; no
  drag/resize is claimed for the A/B desktop.
- The broken-state boot **ran** on both variants of this image
  (corrected 2026-10-27 on independent review — an earlier text
  of this record carried over the previous package's skip note,
  which did not describe these runs): a copy whose STATE
  filesystem was destroyed host-side booted to the same locked
  login screen as a healthy boot, denied the test password (no
  credential store survives on a destroyed STATE), never
  reached a desktop in a 20-second watch, and printed nothing
  but firmware lines on serial. That is
  `broken_state_fails_closed_to_locked_login: true` in both
  JSONs, backed by `hardware-broken-state.log` in each variant's
  run evidence. The previous package's runs (source `9be2404`,
  image `283b1974…`) did skip this leg — their JSONs carry the
  skip string instead — because on the A/B layout a destroyed
  STATE was expected to take the recovery-diagnostics path
  rather than this harness's locked-login path; on this image
  the guest's observed endpoint was the locked login itself.
- The update/rollback mechanics themselves stand at the earlier
  records (`ab-qemu-proof-2026-10-26`,
  `ab-gui-qemu-proof-2026-10-26`,
  `browser-qemu-proof-2026-10-27`); this pass proves the
  packaged full-stack image boots and runs its whole desktop
  from both the framebuffer path and USB storage.

## Failures on the way (kept, with causes)

- First framebuffer attempt died copying the 3.9 GB image into a
  512 MB tmpfs (harness `TMPDIR` placement) — environment, not
  the image.
- At the harness's former default 1024 MB the hardware-kernel
  image **panics** in `do_populate_rootfs` (write error
  unpacking the ~560 MB browser rootfs): a real minimum-RAM
  fact for this stack, captured on the serial console. The
  acceptance runs at 3072 MB, as the browser acceptance does;
  the TG01's 32 GB is far above the failing size.
- The joined desktop proof failed on this healthy desktop
  (window occlusion by the files/GUI windows and the
  autostarted browser) — resolved by the peel proof above, not
  by weakening a gate: session end still requires both the
  first window and the terminal to exit.
- The first USB run's guest sequence passed but its readback
  reported no browser evidence (profile spuriously "present"):
  SIGTERM teardown leaves the DATA journal un-replayed and
  debugfs could not open the extracted copy at all. Journal
  replay on a copy recovered the complete evidence, and the
  readback now replays the journal and fails distinctly when a
  filesystem will not open.
- One USB re-run died at the power-cut-recovery boot with
  QEMU's "Failed to get write lock": qemu.sh backgrounds QEMU
  under bash, so the wrapper's exit did not prove the lock was
  released. Harness teardown now waits for the lock.
- The final rebuild (post-unblocking source `9dbeda0`) lost two
  acceptance attempts to environment service restarts — one
  framebuffer run and one USB run, each killed mid-sequence —
  and package churn stripped QEMU/OVMF, part of QEMU's library
  tree, sgdisk, and the merged-usr `/lib` symlink mid-pass;
  all were restored from the local .deb cache. The graded runs
  in this record are complete, uninterrupted passes on the
  final image.

## Files

- `guest-validation-vga.json` — framebuffer (standard VGA,
  simpledrm) variant, storage virtio-blk.
- `guest-validation-usb.json` — emulated USB storage variant,
  graphics virtio-vga.
- `build-receipt.json` — the image build receipt (source,
  digests, generations, publisher, browser payload).
