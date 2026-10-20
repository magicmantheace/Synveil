# Per-logical-CPU utilization: 23f3233

Revision `23f323345b073008357a4ca283b9ae86d1e3ec2f` passed all 45 steps of
[native run 39301950501](https://github.com/magicmantheace/Synveil/actions/runs/39301950501),
job `118229718317`. Step 11 booted the native image and exercised the real
`veil-core` and `synctl --json observe` path in QEMU.

The native evidence ZIP (artifact `12215401944`, 16838089 bytes) was downloaded
and matched SHA256
`397c37e834bc260af37785e493418138e2161d9b7978e9367df317788b37cf4d`.
Both the native binary manifest and image manifest identify the exact revision
with a clean source tree. The retained `qemu-observe-smoke.log` contains
`SYNVEIL_BOOT_OK`, `SYNVEIL_CORE_READY`, and `SYNVEIL_OBSERVE_OK` after the guest
required `logical_cpus` and `busy_basis_points` from the real command response.
The native client validates the versioned utilization object, strictly
increasing CPU IDs, record statuses, and basis-point bounds before a successful
exit.

Exact-revision [host run 39301950391](https://github.com/magicmantheace/Synveil/actions/runs/39301950391),
job `118229133693`, passed all 145 Rust tests, formatting, clippy with warnings
denied, and tracing of the actual built release CLI. Bootstrap lint run
[39302022741](https://github.com/magicmantheace/Synveil/actions/runs/39302022741)
also passed. Local validation included all 31 recovery control-flow tests.

Host fixtures exercise distinct busy and idle CPUs, numeric ordering, malformed
and duplicate rows, topology changes, counter rollback, guest-time accounting,
and permitted iowait decreases. This evidence establishes per-logical-CPU busy
time over the observer's live sample window in QEMU. It does not establish
per-process attribution, historical telemetry, or physical-hardware behavior.
