# Native audit storage-exhaustion recovery: ebc9f4e

- Revision: `ebc9f4e3f90f7969fd17421d14ef21b05861fd00`
- [Native workflow 38624726956](https://github.com/magicmantheace/Synveil/actions/runs/38624726956): **success**, October 14, 2026.
- All native build, guest, archive and upload steps passed. API evidence is retained in `workflow-result.json`.

The isolated 1 MiB tmpfs guest gate forced real ENOSPC during native audit
appends. Core IPC and console request/shutdown events remained available;
the seeded journal hash remained unchanged and the target inspector accepted
its one valid record. Removing the synthetic filler allowed a fresh core to
append valid records without sink warnings. The temporary filesystem unmounted
successfully and main-core status remained healthy.

All existing rollover/capacity/incomplete-tail, recovery, observer, storage,
graphics/input, terminal, networking, settings, authentication/session and
independent packaged-VM enrollment/reboot checks also passed. Streamed archive
verification and preview upload completed.

The downloadable preview artifact is `authenticated-qemu-preview-ebc9f4e3f90f7969fd17421d14ef21b05861fd00`,
ID 11925024944, 23886602 ZIP bytes. GitHub's ZIP digest is
`sha256:09bcb8ddbd449d3aed6cea8c05d31373239c3719eab0916c708cf1bd511f52d9`, expiring `2026-10-21T20:23:32Z`.
It is not a release signature. Download through the workflow's Artifacts
section and follow [FIRST_INSTALL.md](../../FIRST_INSTALL.md) for the required
exact source revision, checksum verification and private-mode extraction.

This validates tmpfs storage exhaustion and recovery only. Later read-only
remount gates require their own exact-revision evidence. Ext4-specific disk-full,
power-loss, cryptographic audit integrity, owner recovery and physical
installation remain open.
