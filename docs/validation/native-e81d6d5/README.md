# Initial native archive release candidate: e81d6d5

- Revision: `e81d6d5249e5cf4416a37d24f0ba56cafe7df88f`.
- [Native workflow 38995115558](https://github.com/magicmantheace/Synveil/actions/runs/38995115558): **success**, October 17, 2026 UTC.
- All 44 build, guest, packaging and upload steps passed. API evidence is retained in `workflow-result.json`.

The new release gate retained separate active/archive digests, cut and reaped
QEMU after native writes/exports, verified both journals and snapshots on reboot,
released the copied archive, checked both receipt phases and reverified the
active journal and retained copy. The downloaded release-read guest log contains
a standalone `SYNVEIL_AUDIT_CRASH_READ_OK` marker. Every existing audit gate,
graphical terminal return/session revocation, onboarding, packaged-VM checks,
observer, settings and recovery gate also passed.

The native evidence ZIP (artifact `12087907486`, 15342564 bytes) was downloaded
and SHA256 verified:
`ed8ee5a0dc808793558418eca44d1173c380bc7793620435ff1e10efd8ffc4d9`.
The preview artifact is `12088221153`, 23914040 ZIP bytes,
digest `sha256:f4cdc7144606db8801df425e8ec50bcb78e0eb4baf24f7225f9ced9d96e79b05`,
expiring October 24, 2026 at 03:11:20 UTC. These checksums are not release signatures.

Review subsequently identified that this revision could release an archive
while leaving an unsafe active-journal alias. Its successful native run covers
the configured healthy-active scenario, not that unsafe input. The later fix
requires a healthy, locked and distinct active journal and adds release storage
error/partial-receipt tests. Those changes require separate validation; this
record must not be used as their proof.

Interrupted release, real storage-device faults, volatile-cache/physical power
loss, independent authenticated integrity and physical-install readiness remain
open. The durable-audit roadmap item stays unchecked.
