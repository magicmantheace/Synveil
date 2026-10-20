# Per-logical-CPU frequency: 376e2b4

Revision `376e2b43fe70a3550009a6d02e267a98e5fe1d34` passed all 45 steps of
[native run 39300793735](https://github.com/magicmantheace/Synveil/actions/runs/39300793735),
job `118225548157`. Step 11 booted the native image and exercised the real
`veil-core` and `synctl --json observe` path in QEMU.

The native evidence ZIP (artifact `12213757543`, 16828953 bytes) was downloaded
and matched SHA256
`c79d660fa30519ad71b3bde64d796dacb154ba4ab2f41cf8b5f7cce7788715da`.
Both the native binary manifest and image manifest identify the exact revision
with a clean source tree. The retained `qemu-observe-smoke.log` contains
`SYNVEIL_BOOT_OK`, `SYNVEIL_CORE_READY`, and `SYNVEIL_OBSERVE_OK` after the guest
required the `frequency` object from the real command response.

Exact-revision [host run 39300793592](https://github.com/magicmantheace/Synveil/actions/runs/39300793592),
job `118225151192`, passed all 143 Rust tests, formatting, clippy with warnings
denied, and tracing of the actual built release CLI. Bootstrap lint run
[39300808440](https://github.com/magicmantheace/Synveil/actions/runs/39300808440)
also passed. Local validation included all 31 recovery control-flow tests.

Host fixtures exercise primary and fallback cpufreq files, numeric ordering,
missing drivers, inactive-policy errors, and malformed values. QEMU exposed no
cpufreq driver, so this native evidence establishes the bounded unavailable
schema and its integration into the real guest command. It does not establish a
positive frequency reading or accuracy on physical hardware, and it does not
change governors or frequency policy.
