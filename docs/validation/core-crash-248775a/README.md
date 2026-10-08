# Guest core-crash recovery validation

Validated revision: `248775a616786873f7e6c999490172950d547964`.

[Native target build run 37725158245](https://github.com/magicmantheace/Synveil/actions/runs/37725158245)
completed successfully on 2026-10-08 at 04:36:26 UTC, job `113141539642`.
The run built and audited the native binaries, built the native rootfs/kernel
and GPT image, and passed the boot/status smoke.

The separate `Require recovery shell after core crash` step passed between
04:36:18 and 04:36:24 UTC. The committed harness booted a fresh QEMU guest,
waited for core readiness, killed the guest core, required its process to be
gone and status requests to fail, then required shell file write/read operations
to succeed. It accepted only the standalone `SYNVEIL_RECOVERY_OK` marker.

The successful artifact-upload step retained the native identities and logs,
including `qemu-recovery-smoke.log`, in
`native-target-248775a616786873f7e6c999490172950d547964` for seven days.
This permanent summary identifies the tested contract and result, without
replacing the archived manifests or console logs.

This proves crash recovery for the bootstrap shell. It does not prove
absent-core boot, permanent supervision, restart policy, persistent storage,
installation, or a desktop. Those requirements remain separate.
