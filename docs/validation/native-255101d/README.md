# Live aggregate CPU utilization: 255101d

Revision `255101d0c5329bba12546466480ec748ec1e3097` passed all 45 steps of
[native run 39253717232](https://github.com/magicmantheace/Synveil/actions/runs/39253717232),
job `118064035542`. Step 11 booted the native image and exercised the real
`veil-core` and `synctl --json observe` path in QEMU.

The native evidence ZIP (artifact `12190032603`, 16817923 bytes) was downloaded
and matched SHA256
`2dcbe56355ebf968a25edba24dc2a42722370e8eb3f50eaa0988e267d28f070d`.
Both native and image manifests identify the exact revision with a clean source
tree. The retained `qemu-observe-smoke.log` contains boot and core-ready markers,
then `SYNVEIL_OBSERVE_OK` after the guest required the observe schema plus
`busy_basis_points` and `sample_window_ms` from the real command response.

Exact-revision [host run 39253717313](https://github.com/magicmantheace/Synveil/actions/runs/39253717313),
job `118063620860`, passed all 140 Rust tests, formatting, clippy with warnings
denied, and tracing of the actual built release CLI. Bootstrap lint run
[39253724705](https://github.com/magicmantheace/Synveil/actions/runs/39253724705)
also passed. Local validation matched those checks and included all 31 recovery
control-flow tests.

The implementation excludes guest counters already included by Linux in
user/nice accounting. A permitted iowait decrease, zero delta or logical CPU
topology change withholds the utilization window rather than inventing a value.
This evidence establishes aggregate CPU busy time across a 100 ms minimum guest
window. It does not establish per-CPU or per-process attribution, historical
telemetry, or physical hardware behavior outside QEMU.
