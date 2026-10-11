# Native guest recovery, framebuffer, DHCP and persistence — partial evidence

- Revision: `810f66d787ffaa87520dd7e1b2b089e8fa8068da`
- Test date: 2026-10-11
- Workflow: [Native target build 38164327223](https://github.com/magicmantheace/Synveil/actions/runs/38164327223)
- Artifact: `native-target-810f66d787ffaa87520dd7e1b2b089e8fa8068da` (ID 11714110399)
- **Workflow result: FAILURE.** The graphical desktop smoke assertion failed; this is *partial component evidence* only.

## Passed guest steps

The following GitHub Actions steps passed on the same source-built x86_64 UEFI
kernel, BusyBox initramfs and native Rust core:

- native core startup and versioned `synctl status`;
- `synctl --json observe` schema with CPU/memory measurements;
- core crash with usable serial recovery shell;
- worker restart and bounded restart-exhaustion recovery;
- malformed IPC refusal followed by healthy status;
- core binary absent with a usable serial recovery shell;
- QEMU DHCP assignment with `SYNVEIL_NETWORK_READY`;
- separate ext4 `/var` write, terminate guest, reboot second QEMU, read back.

The **graphical QEMU step** emitted
`SYNVEIL_DISPLAY_READY`, `SYNVEIL_INPUT_READY` and
`SYNVEIL_VT_READY`. It also emitted `SYNVEIL_DESKTOP_READY`, but the
serial transcript joined the shell prompt and the marker as
`~ # SYNVEIL_DESKTOP_READY`. The strict standalone-line predicate correctly
failed. Later source corrected the line break. This does not validate the
corrected desktop boot or demo shell in the guest.

## Limitations

This proves bootstrap behavior in QEMU, not an authenticated login,
production filesystem, installer, real hardware support or AI automation.
The DHCP check is constrained to the opt-in QEMU virtual NIC.
The framebuffer is only a direct-draw developer session with optional
keyboard/mouse input; it is not a validated compositor or installed desktop.
The root-debug shell requires explicit serial console flags; demo terminal
must be tested independently under UID 1000 before user installation.
