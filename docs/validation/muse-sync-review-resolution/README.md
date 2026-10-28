# Selective Muse hardware-review synchronization

Owner-authorized review, transfer and testing only. Muse remains primary;
independent Synveil development stays paused. No physical writes, firmware
changes, merges, releases or shiny-dollop modifications were performed.

## Fixed source and classifications

Reviewed Muse tip `66c50b649c4fe580de81dd9d24825f26391a3936`, ten new commits
since the previous copied snapshot `a6ef5112619b38441543a09e38f3b22c46bb918b`.
Original development remains `318a5c90ed20d13b86dc95f21c8e341099699584`.

- `1962a7d`: transfer hardware-profile acceptance driver and test-media writer,
  using their later reviewed versions rather than the original unsafe writer.
- `4622519`: preserve attributed hardware-QEMU record; defer owner boot guide
  and readiness changes pending independent image review and recovery gates.
- `d20a5d9`: readiness hold already retained in the original repository.
- `834dba5`: transfer mapped-device tracing, fixture-only refusal tests and
  host-tier registration. Positive physical writes remain untested/unapproved.
- `406945e`: transfer opt-in standard VGA and whole-image USB QEMU support,
  hardware acceptance variants and argument fixtures.
- `e1ec08c` and `14e7899`: diagnostic-only kernel change and its revert cancel;
  no diagnostic kernel configuration is transferred. Preserve extracted
  evidence with its distinct diagnostic-image identity.
- `7f6bf2b`: transfer USB boot controller/device ordering fix.
- `7b74b81`: preserve corrected attributed records and committed VGA/USB
  receipts. Defer owner operational documentation/readiness claims.
- `66c50b6`: defer updater core, tests and boot-design document. Independent
  review reproduced acceptance of a format-2 writer while the confirmed
  generation uses format 1; old-generation readability and owner rollback
  compatibility are not established. Proposed replacement of global UEFI
  BootOrder also conflicts with preserving Windows firmware configuration.
  Do not independently complete or repair Muse's updater in this branch.

Eleven Muse paths are transferred. Adaptations: keep our existing
`-nodefaults` assertion, add an assertion that xhci precedes the USB boot
device in both graphical and headless modes, and omit registration of the
untransferred updater test. The sync CI workflow and this record are the
only other changed paths.

## Independent results

On the original integration checkout:

- Writer fixture tests: 15/15 pass; additionally 15/15 under each umask
  0007, 0022 and 0077. Fixture mode exits before prompt/write code; no real
  disk is accessed. Coverage includes mounted partitions, LUKS, LVM-over-LUKS,
  mapper swap, md, loop backing files and unresolved mapper refusal.
- QEMU argument fixtures: 14/14 pass, rerun with the added controller-order
  assertions. These use a fake emulator and do not prove a guest boot.
- Offline runner fixtures: 8/8 pass.
- Existing storage-transfer resolver tests: 5/5 pass; three real GPT/ext4
  layout checks unavailable locally because host tools are absent. CI
  installs these tools and executes those checks.
- Shell syntax, hardware-driver Python compilation and git whitespace checks
  pass.

The complete host suite on previous integration head `c1470cb` has now
finished successfully in both runs:
https://github.com/magicmantheace/Synveil/actions/runs/40249108268
https://github.com/magicmantheace/Synveil/actions/runs/40249102938
These results include Rust formatting, Clippy, Rust workspace tests under
three umasks, real regular-file GPT/ext4 checks and the full host tier. They
are earlier-head evidence, not results for this new transfer.

New exact-revision checks are pending at initial commit. CI explicitly runs
writer and USB/VGA argument fixtures, syntax, real storage layouts, pinned
Rust checks, three-umask fixtures and the full host tier. See PR #40 checks.

## Evidence boundaries and compatibility

The two upstream validation records and VGA/USB JSON receipts are copied
verbatim. The DRM excerpt has only CRLF line endings normalized to LF. They identify shipped image SHA-256
`6b10b482239ca3e771bd20d85a5f4e0dc414a00fc34334408eb134b150bacfc3`
and harness revision `7f6bf2b3bce0923475baf607bb4364d7f978b639`.
Their QEMU results belong to Muse's environment. Raw runtime logs and shipped
image bytes remain uncommitted and have not been independently reproduced
or checksum-verified here. Receipts and excerpts are evidence to review,
not cryptographic authentication of the events described.

Upstream records contain their author's hardware-test readiness conclusion;
that conclusion is not adopted by this integration. The original
FIRST_INSTALL.md and PHYSICAL_BOOT.md retain the pre-transfer restrictions.
No owner flashing guide is imported. Adding the experimental writer does not
authorize its use on physical storage. Backing-device fixtures are bounded
coverage, not a claim of exhaustive storage-stack safety or write-path proof.

Source-built QEMU images/SDK are absent locally, so the hardware acceptance
cannot run here. The earlier original CI packaging blocker (private native
dependency sysroot missing, run 40222757132) remains unresolved and is not
worked around with host libraries. No new expensive full compiler rebuild
is launched for this harness-only transfer.

The original architecture, production kernel configuration and credential
system are unchanged by this transfer. A/B boot, installed-system recovery,
web browsing, downloads and physical hardware remain outside its evidence.
Existing raw ext4 state media still require a reviewed migration, as recorded
in the previous synchronization. This review stops after integration.
