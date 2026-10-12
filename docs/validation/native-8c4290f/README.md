# Full native QEMU validation: 8c4290f

- Commit: `8c4290fb0ce9aa64dbd423715c913be286f442c7`
- [Native target workflow, run 38192383503](https://github.com/magicmantheace/Synveil/actions/runs/38192383503) — **success**
- [Native core host checks, run 38192383545](https://github.com/magicmantheace/Synveil/actions/runs/38192383545) — **success**

## What passed in this one pipeline

Pinned-source toolchain, Rust native binaries, root filesystem and
UEFI image built. QEMU boot passed read-only core status/observation,
core crash and absent binary recovery, bounded supervisor exhaustion,
malformed IPC rejection and an independent serial recovery console.

The optional QEMU state device was tested both absent and with an
intentionally invalid ext4 image. Both guest cases retained the serial
shell and a working core. The framebuffer, keyboard, pointer and
non-root VT demo shell passed. A host QMP keyboard sequence opened
the graphical Terminal tile; killing its graphical launcher terminated
the demo shell while core status remained accessible and graphics
restarted. A separate guest checked DHCP.

State survival was checked across two independent QEMU processes.
The graphical preferences test changed settings through real QMP input
in the first VM, checked their saved representation, and verified their
load in a second VM alongside the private demo home file.

## Strict limits

This is **developer VM** validation at the stated revision, not a
user-installable authenticated desktop, physical-driver validation,
signed image, or verified disk installation. Security changes made
after 8c4290f (including audit append path checks) are not covered by
this run. The downloadable CI artifact has limited retention.
