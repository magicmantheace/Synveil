# Native rollover and graphical revalidation: 1edc09e

- Revision: `1edc09e35274405e794f499c048cd854cc5eeca4`.
- [Native workflow 38794199374](https://github.com/magicmantheace/Synveil/actions/runs/38794199374): **success**, October 15, 2026 UTC.
- Every native build, guest, archive and upload step passed. API step and artifact metadata are retained in `workflow-result.json`.

The full run validates the strict retained-digest parser and the single USB
keyboard Q35 target. Both previously failing graphical scenarios passed:
demo-terminal cleanup after launcher crash and authenticated framebuffer
login/session revocation after GUI crash. The retained guest logs contain
standalone `SYNVEIL_TERMINAL_LIFETIME_OK`, `SYNVEIL_GRAPHICAL_AUTH_ACTIVE`,
`SYNVEIL_USER_DASHBOARD_KILLED` and `SYNVEIL_GRAPHICAL_LOGIN_CLEANED` markers.
This validates this QEMU configuration, without proving the precise cause of
the earlier input failures or physical multiple-keyboard behavior.

The completed-rollover crash gate passed as part of the full successful run.
Its first boot retained distinct active/archive digests and the standalone
write-success marker; the host confirmed emulator SIGKILL and wrapper reaping.
An independent boot verified both journals and both exported snapshots against
the host-retained digests and emitted `SYNVEIL_AUDIT_CRASH_READ_OK`.
Existing audit capacity/tail, tmpfs/read-only/ext4 exhaustion, core recovery,
observer, state/settings persistence, authentication/onboarding and independent
packaged-VM checks passed as well.

The authenticated preview artifact is ID `12003698446`, 23901198 ZIP bytes,
with digest `sha256:39aeaaacacfcd79f93efe05a3e2d40c1831227e09e75e9177024da30b349e9b6`,
expiring October 22, 2026 at 20:18:02 UTC. This checksum is not a release
signature. The native evidence ZIP is ID `12003598615`; its downloaded bytes
matched `sha256:3a2dd9febd59d52fc8da7176fb5dd1d6eebd1af6f7d060f86c2d90289736f3cd`
before the guest markers were inspected. Use the workflow Artifacts section
and [FIRST_INSTALL.md](../../FIRST_INSTALL.md) for preview extraction and scope.

This run does not validate the later VT-chord hold or archive-only layout gate.
Acknowledged completed writes and rollover under emulator-process loss do not
prove interrupted rotation, host/device-cache power loss, device I/O faults,
authenticated independent integrity or retention release. Those requirements
remain open; this is an experimental VM preview, not physical-install evidence.
