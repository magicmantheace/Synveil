# Native acknowledged audit recovery after abrupt emulator loss: 00476e5

- Revision: `00476e5ea14e0a09d730aed63f6c205730f5f2e0`
- [Native workflow 38742356752](https://github.com/magicmantheace/Synveil/actions/runs/38742356752): **success**, October 15, 2026 UTC.
- Every native build, guest, archive and upload step passed. API evidence is retained in `workflow-result.json`.

A fresh private ext4 image received an isolated native audit journal and
verified snapshot without a global guest sync. The host retained the digest,
identified and killed the emulator through a pidfd, and required the wrapper
to reap it with status 137. A second independent boot verified both files
against that retained digest and required healthy main-core IPC.

All existing core recovery, observer, tmpfs/ext4 exhaustion, audit rollover,
state persistence, graphical/settings, authentication/session and independent
packaged-VM checks passed. Archive verification and preview upload completed.

The authenticated preview artifact is ID 11978579554, 23901235 ZIP bytes,
with GitHub digest `sha256:eea0c29cef2c435be1b9ca9c5fe983c44878c0690babf8dcd2b09001ee273dbd`,
expiring `2026-10-22T14:05:50Z`. This is not a release signature. Use the
workflow Artifacts section and [FIRST_INSTALL.md](../../FIRST_INSTALL.md)
for exact-revision checksum and private-mode extraction requirements.

This validates acknowledged writes under emulator-process loss only. The later
completed-rollover extension requires separate native evidence. Interrupted
append/rotation, host power loss, volatile device-cache loss, device I/O errors,
authenticated independent integrity and retention release remain open.
