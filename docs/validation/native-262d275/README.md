# Authenticated preview and audit inspection: 262d275

- Revision: `262d275402832cbff8e1c87828829c4d530b77e7`
- [Native workflow 38575451675](https://github.com/magicmantheace/Synveil/actions/runs/38575451675): **success**, October 14, 2026.
- Every build, guest, archive and upload step passed. The compact API evidence is retained in `workflow-result.json`.

The pinned-source toolchain and five audited native binaries built into the
rootfs/UEFI image. All core/status/observer, recovery, malformed-IPC, storage,
graphics/input/VT, nonroot terminal, DHCP, settings-persistence and authenticated
session tests passed. `veil-audit check` accepted the persistent journal across
two independent boots. First-user setup and reboot login passed both in the
guest fixture and in separate tests of the actual packaged image.

The packaged blank state disk remained unchanged. The verified preview was
archived with private tar modes and an external checksum, then uploaded as
`authenticated-qemu-preview-262d275402832cbff8e1c87828829c4d530b77e7`.
Its artifact ID is 11903101576; the ZIP contains 23,879,945 compressed bytes.
GitHub reports its ZIP digest as
`sha256:f78b1f576d7d73d57b5cfa79f3871d596958a3c01c19d84808865108e0703f38`.
This identifies the artifact ZIP, not its internal tar or a release signature.
The recorded expiry is October 21, 2026 at 14:29:38 UTC.

Download from the workflow's Artifacts section. Follow the checksums,
private-mode extraction and exact-revision checkout instructions in
[FIRST_INSTALL.md](../../FIRST_INSTALL.md). The launcher requires a Linux
host with QEMU/OVMF and starts offline. The bundled state is blank; use the
graphical setup to create a password locally.

This is an experimental QEMU preview. It does not validate physical installation,
owner password recovery, signing or the later bounded-audit-rotation changes.
