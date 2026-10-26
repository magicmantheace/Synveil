# Muse joined-image transfer review

Scope: owner-authorized synchronization and independent testing only. No
independent roadmap development, physical writes, merges, releases or changes
to shiny-dollop. Original roadmap remains paused after this review.

## Reviewed snapshot

Muse branch `synveil/first-boot-desktop-muse`, fixed snapshot
`a6ef5112619b38441543a09e38f3b22c46bb918b` (16 commits after the previously
copied `e17b9e7fc5d0dc4058b7320609f5a479c1344f75`). The original development
branch still starts at `318a5c90ed20d13b86dc95f21c8e341099699584`.

Reviewed and transferred the completed changes in:
`3a8d537`, `01b4838`, `89f7f43`, `8d7623f`, `a2b1528`, `4654c65`,
`3b27616`, `cf73400`, `117c88b`, `033dc83`, `48af350`, `2443f82`,
`f181807`, `dc1c9fd`, `124abbc`, `a6ef511`.

These provide GPT state identity, exact filesystem/partition sizing, bounded
USB enumeration waiting, opt-in persistent image packaging, authenticated
Weston session launch through the existing seat lease, VT handoff, devpts,
umask-independent rootfs permissions, a joined image builder and two-boot
acceptance, and floating-size restore. The earlier deferred Muse resize change
is superseded by `124abbc`; actual host compositor validation remains required.

All 35 project paths changed since e17 were transferred selectively, with the
monorepo prefix removed. Preserve our existing `-nodefaults` QEMU assertion.
No other original-repository files were replaced. New integration-only work
consists of regression tests, CI wiring and this evidence record.

## Independent verification

Local results on the integration checkout:

- QEMU command fixtures: 11 passed (fake emulator, not a boot).
- State-disk fixtures: 5 passed (mocked formatting commands).
- State-mount path safety: 5 passed.
- VM bundle fixtures: 12 passed.
- Display build and helper tests: 10 passed, including compiled C enrollment,
  VT self-test and framebuffer executable checks.
- Desktop supervisor + state-mount tests under umasks 0007, 0022 and 0077:
  33 test executions passed.
- First-window protocol probe fixtures: 7 passed.
- Native guest fixture tests: 8 passed.
- Native Weston and VM builder fixture tests: 4 + 4 passed.
- Added production-resolver tests: 5 passed (immediate, delayed, missing,
  wrong and duplicate partition identities). Only sysfs location and sleep
  are substituted; no mounts or credential operations are executed.
- Shell/Python syntax and git whitespace checks passed.
- Three real GPT/ext4 image checks cannot run locally because sgdisk/mtools
  are absent; CI installs these prerequisites. They verify the production
  image builder and state-disk creator using regular files, partition and
  filesystem lengths, e2fsck, and EFI payload preservation. The EFI payload
  is a fixture, so these checks cannot establish bootability.

The synchronization workflow also runs pinned Rust formatting, Clippy and
workspace tests under all three umasks, plus the complete existing host tier.
The first-window workflow runs the real host Weston creation, maximize/restore,
repaint and multiwindow checks. Results are pending at initial transfer; consult
PR #40 checks for exact-revision completion.

Prior integration head `4045263` complete host checks have now finished:
https://github.com/magicmantheace/Synveil/actions/runs/40222980381 (PASS)
https://github.com/magicmantheace/Synveil/actions/runs/40222976247 (PASS)
These are earlier-head results, not validation of this transfer.

The previous full native build at candidate `4466b64` failed at "Build separate
native Synveil compositor VM image":
https://github.com/magicmantheace/Synveil/actions/runs/40222757132
The failure annotation identifies `ValueError: private native dependency sysroot missing` in `tools/native_weston_vm.py`. Source-built component compilation completed, but packaging failed before QEMU execution. It did not prove a successful QEMU desktop boot. Do not reuse that run as a
passing native acceptance result.

## Attributed evidence and remaining limits

`docs/validation/joined-124abbc/README.md` is copied verbatim from Muse. Its
reported two-boot joined-image, USB-state and hardware-build results belong to
Muse's environment. Raw `out/` logs, receipts and image bytes are not committed
there and have not been independently reproduced or checksum-verified here.

The joined image builder retains Muse's stage-1 libgcc dependency and default
`~/workspace/synveil-tools` Rust locations. Reproduction elsewhere requires
explicit RUSTUP_HOME/CARGO_HOME and the prerequisite toolchain build tree;
a cached SDK alone may be insufficient. No independent build-system fix was
invented during transfer.

The former raw ext4 state fixture format is replaced by GPT-partitioned media.
Existing raw state images need preservation and a separately reviewed migration;
they must not be overwritten or assumed compatible. The fixed partition GUID
is a role identifier, not a cryptographic authenticity guarantee. Duplicate
partitions present at discovery are refused; attachment after the initial mount
is outside this test evidence.

No original-repository joined QEMU boot or physical hardware test is claimed.
No safe installer, independent owner recovery, bootable A/B update/rollback,
launcher, file manager or settings GUI is completed by this review. Physical
installation remains blocked by the documented gates. Muse remains primary.
