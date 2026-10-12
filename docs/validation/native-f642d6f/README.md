# Native QEMU graphical preferences and demo-home evidence

- **Exact source revision:** `f642d6ffaa59c147c8bc4ecbc49014fa7a500614`
- **Date:** 2026-10-11 (workflow completed 2026-10-12 UTC)
- **Workflow:** [Native target build #38185978607](https://github.com/magicmantheace/Synveil/actions/runs/38185978607)
- **Conclusion:** SUCCESS, all reported job steps completed successfully
- **Scope:** source-built Synveil x86_64 UEFI developer image under OVMF/QEMU

The same native-image workflow built the pinned toolchain, native services,
rootfs, kernel and EFI disk image, then passed independent guest steps for:

- Boot, native core status and read-only observation
- Core-crash recovery, bounded core supervision, malformed protocol requests,
  and absent core binary
- Virtio framebuffer, evdev input, VT and graphical menu readiness
- UID/GID 1000 developer terminal with `NoNewPrivs`
- Opt-in virtual NIC DHCP
- Two separate QEMU boots preserving `/var` on the ext4 state disk
- **QMP-driven graphical Settings interaction on boot one**, observed
  private on-disk preferences, graphical `SYNVEIL_SETTINGS_LOADED` on a
  second independent boot, and a persisted UID 1000-owned demo-home file

## Boundaries of this result

This exact workflow **did not include** the later
`recovery-smoke --case desktop` graphical crash-restart guest test.
It also predates stricter state-directory checks committed at `62b835b`.
Those changes require fresh native-image validation.

Neither authenticated login, a composited desktop, a safe hardware installer,
nor a production user-session security model was exercised by this QEMU run.
The guest's recovery shell and demo terminal remain developer-only.
