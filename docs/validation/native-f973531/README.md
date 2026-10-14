# Native audit rollover: f973531

- Revision: `f9735312948644f1bfbdd857494609d7aea09825`
- [Native workflow 38596931064](https://github.com/magicmantheace/Synveil/actions/runs/38596931064): **success**, October 14, 2026.
- Every build, guest, archive and upload step passed. API evidence is retained in `workflow-result.json`.

The isolated source toolchain and audited native binaries built into the
rootfs/UEFI image. The new guest audit rollover gate seeded 512 valid records
(4 MiB), triggered a real core request, preserved the complete journal in the
no-overwrite archive, checked its hash/private metadata and structural record
count, and verified a valid new journal and healthy independent core status.

All existing recovery, observer, storage, graphics/input, terminal, networking,
settings, session revocation, locked serial, enrollment and two-boot checks
passed. The actual packaged VM independently passed first-user enrollment and
reboot login before archive creation and upload.

The downloadable preview artifact is
`authenticated-qemu-preview-f9735312948644f1bfbdd857494609d7aea09825`.
Its artifact ID is 11914772222; the ZIP is 23,886,308 bytes.
GitHub reports its ZIP digest as
`sha256:23bcbf370fb6417b6f6a907d105cb820786d2a29af99a5ad825458d5a991d01e`, expiring October 21, 2026 at 17:07:16 UTC.
This digest identifies the artifact ZIP; it is not a release signature.
Download through the workflow's Artifacts section and follow
[FIRST_INSTALL.md](../../FIRST_INSTALL.md) for checksums, private-mode extraction
and the exact source revision required by the offline Linux/QEMU launcher.

This validates the original rollover gate only. Later exhausted-capacity and
incomplete-tail guest gates and streamed archive-byte checks require their
own exact-revision run. Physical installation, owner recovery, cryptographic
audit integrity and power-loss durability remain open.
