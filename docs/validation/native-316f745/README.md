# Corrected process snapshot after energy composition: 316f745

Revision `316f74518335fe4df32ba0bef225547b1c8c6d01` passed all 43 named steps of
[native run 39426616145](https://github.com/magicmantheace/Synveil/actions/runs/39426616145),
job `118640984874`. Step 11 booted the native image and exercised the real
`veil-core` and `synctl --json observe` path in QEMU.

The native evidence ZIP (artifact `12268262839`, 16891482 bytes) was downloaded
and matched SHA256
`0a0e12a21d8d9d2fb39658983131740128c3978ad233a73828ef07a4645a4d11`.
Both the native binary manifest and image manifest identify the exact revision
with a clean source tree. The retained `qemu-observe-smoke.log` contains
`SYNVEIL_BOOT_OK`, `SYNVEIL_CORE_READY`, and `SYNVEIL_OBSERVE_OK` after the guest
required the `processes`, `start_time_ticks`, and composed `energy` fields from
the real command response.

Exact-revision [host run 39426616090](https://github.com/magicmantheace/Synveil/actions/runs/39426616090),
job `118640685144`, passed all 154 Rust tests, formatting, clippy with warnings
denied, and tracing of the actual built release CLI. Bootstrap lint run
[39426627316](https://github.com/magicmantheace/Synveil/actions/runs/39426627316),
job `118640721960`, also passed. Local validation included all 31 recovery
control-flow tests, and automated review of the exact revision returned no
findings.

This run supersedes the process-only evidence at `2a0a90e` for the composed
implementation. It includes the correction that makes `synctl` reject a
successful process snapshot above the producer's 128-record cap. The QEMU gate
establishes that the real guest returns a bounded process object with stable
start-time identity through the structurally validating native client. Host
fixtures cover PID ordering and reuse identity, exit and permission races,
non-UTF-8 command names, oversized and malformed stat data, response frame
trimming, and rejection of 129 otherwise-valid records. This snapshot does not
expose argv, environments, executable paths, lifecycle deltas, history,
interval utilization, or process control.
