# Bounded Linux process snapshot: 2a0a90e

Revision `2a0a90edfaeae0b215d734a822653ee7cfea1d15` passed all 45 steps of
[native run 39361102130](https://github.com/magicmantheace/Synveil/actions/runs/39361102130),
job `118427643608`. Step 11 booted the native image and exercised the real
`veil-core` and `synctl --json observe` path in QEMU.

The native evidence ZIP (artifact `12239128065`, 16852043 bytes) was downloaded
and matched SHA256
`5e092e61da7555495b8faccdcbf4b5d5b43e00144da839bc9e2d09736e2ebcfc`.
Both the native binary manifest and image manifest identify the exact revision
with a clean source tree. The retained `qemu-observe-smoke.log` contains
`SYNVEIL_BOOT_OK`, `SYNVEIL_CORE_READY`, and `SYNVEIL_OBSERVE_OK` after the guest
required the `processes` object and `start_time_ticks` from the real command
response.

Exact-revision [host run 39361102094](https://github.com/magicmantheace/Synveil/actions/runs/39361102094),
job `118427237377`, passed all 152 Rust tests, formatting, clippy with warnings
denied, and tracing of the actual built release CLI. Bootstrap lint run
[39361106822](https://github.com/magicmantheace/Synveil/actions/runs/39361106822),
job `118427251912`, also passed. Local validation included all 31 recovery
control-flow tests, and automated review returned no findings.

The QEMU gate establishes that the real guest returns a bounded process object
with stable start-time identity through the structurally validating native
client. Host fixtures cover PID ordering and reuse identity, exit and permission
races, non-UTF-8 command names, oversized and malformed stat data, and response
frame trimming. This snapshot does not expose argv, environments, executable
paths, lifecycle deltas, history, interval utilization, or process control.
