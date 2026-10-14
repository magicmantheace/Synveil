# Full authenticated QEMU validation: e8f371a

- Revision: `e8f371a2009ae141e80b9f1e5c8cc1cff737e1df`
- [Native target workflow 38556585136](https://github.com/magicmantheace/Synveil/actions/runs/38556585136): **success**, completed October 14, 2026.
- All build and guest steps passed in this pipeline; the retained API result is in `workflow-result.json`.

The isolated pinned-source toolchain, native ELF/link audit, rootfs and UEFI
image built successfully. QEMU passed core status and observation, crash and
restart-exhaustion recovery, absent-core and malformed-IPC cases, independent
read-only recovery with core/seat/desktop absent, and missing/invalid state
storage. Framebuffer/input/VT readiness, graphical crash recovery, nonroot
terminal cleanup, DHCP and independent-boot storage/preferences passed.

Authenticated guest checks passed locked serial, one-time enrollment,
reboot authentication, actual graphical login and terminal revocation after
seat/GUI failure. Graphical first-user setup passed two separate QEMU boots.
The authenticated packager then created a new state disk, and a separate test
booted the actual packaged image twice using a disposable copy of that disk.
Both packaged-image enrollment/login and preservation of the distributable
blank disk passed.

The CI evidence ZIP is `native-target-e8f371a2009ae141e80b9f1e5c8cc1cff737e1df`,
artifact ID 11892602443. GitHub reports its archive SHA256 as
`ff3a4cc8c4c2f48c0c2cbc4a684883889a35fb53127cafb810be2b14464e8f71`.
Its recorded expiry is October 21, 2026 at 12:16:34 UTC. This checksum identifies
the evidence archive, not a downloadable VM image or a release signature.

This proves the experimental authenticated QEMU prototype at the stated
revision. Later audit changes are excluded. Physical installation, persistent
installed root, owner password recovery, signing, broader hardware support
and a production compositor remain open. The baseline workflow retained build
evidence, not the complete authenticated VM bundle as a user download.
