# Native disposable ext4 audit recovery: fcba067

- Revision: `fcba0670e8d33d169679a1fa531bdb1b337e93ab`
- [Native workflow 38725857864](https://github.com/magicmantheace/Synveil/actions/runs/38725857864): **success**, October 15, 2026 UTC.
- Every native build, guest, archive and upload step passed. API evidence is retained in `workflow-result.json`.

A fresh disposable 64 MiB ext4 state image reached real ENOSPC. The bounded
native helper preserved the seeded journal bytes, structural validity and
console/IPC service. Export failed with StorageFull without a partial copy;
main-core IPC remained healthy while full. Removing the synthetic filler
allowed valid appends, successful export and digest verification with matching
source/copy bytes. The caller's state image was never used by this scenario.

All existing native recovery, observer, tmpfs failure/recovery, rollover,
state persistence, graphical/settings, authentication/session and independent
packaged-VM checks passed. Archive verification and preview upload completed.

The authenticated preview artifact is ID 11969163810, 23900690 ZIP bytes,
with GitHub digest `sha256:b19daeab37ceff254b7094e6fb13521ce2928688006bf9e4462ef8e83f92d084`,
expiring `2026-10-22T11:49:25Z`. This is not a release signature. Use the
workflow Artifacts section and [FIRST_INSTALL.md](../../FIRST_INSTALL.md)
for exact-revision checksum and private-mode extraction requirements.

This is clean ext4 exhaustion/recovery evidence, not a production disk-full
policy. Later abrupt-emulator-stop tests, interrupted writes/rotation,
physical power loss, device I/O errors, independent authenticated integrity,
retention release and physical installation remain open.
