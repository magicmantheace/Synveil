# Completed Muse changes copied for independent testing — 2026-10-25

Owner authorization: copy completed Muse work and test it while Muse continues
as primary developer. No independent feature implementation, hardware writes,
firmware changes, automatic merging or roadmap resumption is authorized here.

## Fixed snapshot and transfer

- Source: `magicmantheace/shiny-dollop`, `projects/synveil/`, branch
  `synveil/first-boot-desktop-muse`.
- Snapshot fetched once at task start:
  `e17b9e7fc5d0dc4058b7320609f5a479c1344f75`.
- Reviewed all 14 project commits since the previously integrated
  `81440eac4b4fd0ec29e8b377a74df9d4dd534eab`.
- Destination: `development/muse-verified-sync-2026-10-24`, existing PR #40,
  based on original `development/first-boot-desktop`. The original development
  branch and Muse repository remain unchanged.

| Muse commits | Transferred work |
|---|---|
| `d208b7c` | Gate image provenance only for real guest checks; retain full verification and run both fake-emulator host fixtures. Includes Muse's regression test. |
| `57245a9` | Correct the native libc provenance source path to `lib64/libc.so.6`, preserving exact byte comparison. |
| `e72e00d`, `9abe888`, `e1d62ec` | Explicit eudev executable assets, build util-linux runtime libraries and xkeyboard-config data for the guest. |
| `bbf1e6b`, `2342c45`, `ddaf4a2` | Normalize disposable Weston rootfs directory/file modes, preserve symlinks and private shadow mode, pin fixture config/account modes. |
| `5d93747`, `1ccbbb0` | Traversable runtime parent directories, private user leaves, and the actual `/sbin/udevd` daemon path. |
| `0c5069b` | Explicit `-nodefaults` QEMU devices, preserving existing disk/graphics/input selection. |
| `28888da` | Keep current client dimensions when compositor configuration leaves them unspecified. |
| `f48771c` | Pin test-only authentication/recovery marker modes to 0644. |
| `e17b9e7` | Attributed QEMU/toolchain validation report and corrected earlier host-tier evidence framing. |

All changed implementation files matched the preceding Muse snapshot before
application. They were transferred selectively, with no source conflicts or
new production implementation. No source change was rejected at code review;
independent runtime acceptance remains subject to the tests below.

The copied evidence is verbatim at
[QEMU desktop/authentication report](../qemu-desktop-auth-f48771c/README.md)
and [corrected Muse baseline](../monorepo-d3c7822/README.md).
Those are **Muse-reported runs**, not executions by this integration worker.
They describe distinct VM profiles, not a physical integrated owner desktop.
The original historical baseline copy under `muse-sync-2026-10-24/` remains
unchanged as evidence of the earlier snapshot.

## Independent checks and scope

Focused local checks passed after transfer:

- `tests/offline-checks.py`: 8 tests.
- `tests/qemu-options.py`: 9 tests, including an integration-added assertion
  that the real launcher supplies `-nodefaults` exactly once.
- `tests/native-weston.py`: 4 tests.
- `tests/native-weston-vm.py`: 4 tests.
- `tests/first-window-probe.py`: 7 tests.
- `tests/native-guest-fixture.py`: 8 tests.
- Python compilation, shell syntax, workflow YAML parsing and Git whitespace.

CI is enabled on the integration branch for the existing host window test and
source-native Weston build/image/UEFI guest acceptance. The focused integration
workflow now runs the existing complete host tier, including both fake-QEMU
host checks fixed by Muse, along with pinned Rust fmt/Clippy and three-umask
Rust/fixture checks. No test is replaced by a weaker marker-only substitute.

At this commit, CI execution is **pending**. Exact run IDs/results will be
recorded in PR #40. The full native build may take significantly longer than
host checks. No new native boot or independent authentication cold-reboot pass
is claimed until its corresponding actual scenario succeeds. In particular,
the enabled Weston guest test is not an authenticated onboarding guest test.
No separate cold toolchain/native-Rust enrollment build is started by this pass.

## Hardware limitations retained

The Weston fixture remains explicitly QEMU-only, unauthenticated and
non-persistent. The separate authenticated framebuffer preview still depends
on the fixed QEMU `/dev/vdb` state device. Neither profile is converted into a
physical installer or portable enrolled-owner image by these transfers.
No image is published or recommended for flashing, and no firmware security
change is requested. Installed persistence, recovery, trusted A/B boot/update
and owner hardware compatibility remain unproven.

After reporting this fixed-snapshot synchronization and its test results,
stop. Further failures are reported for Muse/owner review; do not independently
complete unfinished features or repeatedly poll Muse for new commits.
