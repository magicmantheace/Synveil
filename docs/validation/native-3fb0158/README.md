# Audit failure recovery and archive integrity: 3fb0158

- Revision: `3fb0158bf19cad640e59e48c1b9b4720596e0866`
- [Native workflow 38607950928](https://github.com/magicmantheace/Synveil/actions/runs/38607950928): **success**, October 14, 2026.
- All native build, guest, archive and upload steps passed; API evidence is retained in `workflow-result.json`.

The native audit gate validated real rollover, exhausted archive capacity and
an incomplete active journal. Existing journal/archive bytes were preserved,
core status and console events remained available, explicit sink refusals were
observed, and the target inspector rejected the incomplete tail.

All existing core, observer, recovery, storage, framebuffer/input, terminal,
networking, settings, session revocation and authenticated enrollment/reboot
checks passed. The actual packaged VM passed independent enrollment and
reboot login. Streamed archive-byte verification and private-mode packaging
then succeeded before preview upload.

The preview artifact is `authenticated-qemu-preview-3fb0158bf19cad640e59e48c1b9b4720596e0866`,
ID 11920450083, 23886185 ZIP bytes, with GitHub digest
`sha256:6796a33d3f111898f7ae4596cf8ab81c47bab53f3b3224f8999635d5b932bf5d`.
It expires October 21, 2026 at 18:25:48 UTC. This is a ZIP identity, not a
release signature. Use the workflow's Artifacts section and
[FIRST_INSTALL.md](../../FIRST_INSTALL.md) for exact-revision Linux/QEMU use.

This does not validate the later isolated ENOSPC gate, physical installation,
owner recovery, cryptographic audit integrity or power-loss durability.
