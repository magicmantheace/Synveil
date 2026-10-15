# Native unnamed audit snapshots and storage failures: 9f722d0

- Revision: `9f722d0405299ff2a56c76f5fdf466ef5fd24548`
- [Native workflow 38665309157](https://github.com/magicmantheace/Synveil/actions/runs/38665309157): **success**, October 15, 2026 UTC.
- Every native build, guest, archive and upload step passed. API evidence is retained in `workflow-result.json`.

Native audit export successfully wrote and verified an unnamed inode before
exclusive publication. The rollover gate verified the copy digest, refused
replacement and detected a structurally valid edit. The isolated 1 MiB tmpfs
gate required failed exports under full and read-only storage, the specific
StorageFull/ReadOnlyFilesystem error categories, no partial named snapshot,
and unchanged source bytes. Export and verification passed after recovery.

All existing core recovery, observer, state storage, graphics/input, settings
reboot, authentication/session and independent packaged-VM tests passed.
Streamed archive verification and preview upload completed.

The authenticated preview artifact is ID 11942362798, 23900140 ZIP bytes,
with GitHub digest `sha256:1a7ff955419356af0e140c27563fc91e624a04267302772f8c715bf233ce67e9`,
expiring `2026-10-22T01:57:30Z`. This is not a release signature. Download
through the workflow Artifacts section and follow
[FIRST_INSTALL.md](../../FIRST_INSTALL.md) for exact-revision checksum and
private-mode extraction requirements.

This evidence does not validate the later disposable ext4 exhaustion gate.
Power-loss durability, device I/O failures, authenticated audit origin,
independent integrity anchoring, retention release and physical installation
remain open.
