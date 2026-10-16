# Native archive-only recovery and held VT chord: c97f554

- Revision: `c97f554da6927c19e8651b7388444a3599cb8a2b`.
- [Native workflow 38833990492](https://github.com/magicmantheace/Synveil/actions/runs/38833990492): **success**, October 16, 2026 UTC.
- Every build, guest, package and upload step passed; API evidence is retained in `workflow-result.json`.

The new archive-only gate prepared a valid 4 MiB synthetic retained archive
with no active journal and synchronized that seed before starting the native
helper. Native recovery created an active journal without changing retained
history. After native append/export/verification, no global sync or clean guest
shutdown preceded the emulator cut. Separate active/archive digests were retained
in the host log, the emulator was killed and reaped, and both originals and
both snapshots verified on independent reboot. Downloaded logs contain actual
standalone write/read success markers and host cut/reap confirmation; their
receipts also pass the strict retained-digest parser.

The held Ctrl+Alt+F2 chord was included in this revision. Native VT-terminal,
GUI login/crash/session revocation, first-user enrollment, settings persistence
and independent packaged-VM checks all passed, along with core recovery,
observer, audit capacity/tails, completed-rollover and tmpfs/ext4 exhaustion.

The native evidence ZIP (artifact `12019951224`, 15318649 bytes) was downloaded
and its SHA256 verified as
`34bc6fd647b502ea968acd27ace94d992cf4d6d1ad2a89a7c288335a5e698d02`.
The authenticated preview artifact is `12020055563`, 23900745 ZIP bytes,
with digest `sha256:2213ee2b3dbc913ce441853c1a91d982ea2a1539d90ba0c3b2e02c6c5505a59f`,
expiring October 23, 2026 at 01:50:28 UTC. This checksum is not a release
signature. See workflow Artifacts and [FIRST_INSTALL.md](../../FIRST_INSTALL.md)
for preview extraction and test scope.

Constructed archive-only recovery does not establish a live rename/fsync crash
window, real block-device I/O failure, volatile-cache loss or physical power
loss. The later storage-adapter/fault-injection change is not part of this
revision and requires fresh native validation. Authenticated independent
integrity, retention release and physical-install readiness remain open.
