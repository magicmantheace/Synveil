# Native audit export and settings revalidation: 612bd80

- Revision: `612bd80b2baf02521e90d4e7e11db6c75594e7e5`
- [Native workflow 38651245256](https://github.com/magicmantheace/Synveil/actions/runs/38651245256): **success**, October 14, 2026.
- Every native build, guest, archive and upload step passed. API evidence is retained in `workflow-result.json`.

The audit rollover guest gate exported the retained archive, verified the copy
against its receipt digest, refused destination replacement, preserved the
source bytes and detected a structurally valid edit. The tmpfs ENOSPC and
read-only/read-write recovery gate passed alongside capacity and incomplete-tail
checks. The graphical settings write/reboot gate also passed with the bounded
QMP key hold, resolving the failed gate in the earlier ffd0b0a run.

All existing core recovery, observer, state storage, graphics/input, networking,
authentication/session and independent packaged-VM enrollment/reboot gates
passed. Streamed archive checks and preview upload completed.

The authenticated preview artifact is ID 11936542718, 23899455 ZIP bytes,
with GitHub digest `sha256:53cb8c54bb5d947bccc8ee6b41cb778785395238c502cfcdf72e01b85ee9b209`,
expiring `2026-10-21T23:47:48Z`. This is not a release signature. Download
through the workflow Artifacts section and follow
[FIRST_INSTALL.md](../../FIRST_INSTALL.md) for exact-revision checksum and
private-mode extraction requirements.

This evidence does not validate later descriptor-relative/unnamed-inode export
changes or export attempts under full/read-only storage. Ext4-specific failure
handling, power-loss durability, authenticated audit origin, independent
integrity anchoring, retention release and physical installation remain open.
