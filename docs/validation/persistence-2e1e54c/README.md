# Opt-in QEMU persistence — partial native build evidence

- Tested source revision: `2e1e54c521060de5deb3afa68b2b5bc235203047`
- Date: 2026-10-11
- Workflow: [Native target build 38156709811](https://github.com/magicmantheace/Synveil/actions/runs/38156709811)
- Artifact: `native-target-2e1e54c521060de5deb3afa68b2b5bc235203047`
- Evidence class: guest runtime test of experimental developer state volume
- **Full workflow conclusion: failure** because the *separate framebuffer test*
  did not find `/dev/fb0`. It is not correct to label the complete image green.

## Passing guest contract

The `Verify state survives two separate QEMU boots` step passed at
2026-10-11 18:02 UTC. Host tooling created a separate 128 MiB ext4 state
image and the test booted two independent QEMU processes with that volume.

The archived `out/logs/qemu-persistence-write.log` contains standalone
`SYNVEIL_STATE_READY`, `SYNVEIL_CORE_READY`, `SYNVEIL_BOOT_OK`, and
`SYNVEIL_PERSIST_WRITE_OK` markers after writing and syncing a known
sentinel to `/var/lib/synveil/roundtrip`.

The independent second run's
`out/logs/qemu-persistence-read.log` contains those three readiness
markers and standalone `SYNVEIL_PERSIST_READ_OK` after reading the same
sentinel value. The verification does not rely on echoed test command text.

## Limits

This validates persistence of one QEMU developer data volume. It does not
validate an installed root filesystem, hardware disk selection, unattended
fsck, on-disk encryption, update/rollback, or user desktop settings.
The test volume is explicitly created on the host and mounted from a fixed
QEMU second-disk path; it must not be used as an unguarded physical installer.

The framebuffer failure in the same workflow was separately investigated:
the saved `out/manifests/kernel.config` showed
`# CONFIG_FB_DEVICE is not set`. The subsequent source fix explicitly
sets `CONFIG_FB_DEVICE=y` and checks the resolved kernel configuration.
Actual framebuffer guest verification remains pending.
