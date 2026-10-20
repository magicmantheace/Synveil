# Linux thermal-zone observation: b5f5cae

Revision `b5f5cae2d0971b200ce7e7ea76fd7c2965a221a1` passed all 45 steps of
[native run 39302160871](https://github.com/magicmantheace/Synveil/actions/runs/39302160871),
job `118229859913`. Step 11 booted the native image and exercised the real
`veil-core` and `synctl --json observe` path in QEMU.

The native evidence ZIP (artifact `12214464742`, 16838914 bytes) was downloaded
and matched SHA256
`eb233ccbed3f328722c97d426f7031e18ffbfffede2cd47a1d7e36c95ee7bd83`.
Both the native binary manifest and image manifest identify the exact revision
with a clean source tree. The retained `qemu-observe-smoke.log` contains
`SYNVEIL_BOOT_OK`, `SYNVEIL_CORE_READY`, and `SYNVEIL_OBSERVE_OK` after the guest
required the `thermal` object from the real command response.

Exact-revision [host run 39302160972](https://github.com/magicmantheace/Synveil/actions/runs/39302160972),
job `118229861039`, passed all 147 Rust tests, formatting, clippy with warnings
denied, and tracing of the actual built release CLI. Bootstrap lint run
[39302186683](https://github.com/magicmantheace/Synveil/actions/runs/39302186683)
also passed. Local validation included all 31 recovery control-flow tests, and
automated review returned no findings.

Host fixtures exercise numeric zone ordering, signed millidegree values, partial
availability, missing support, and malformed values. QEMU exposed no thermal
zones, so this native evidence establishes the bounded unavailable schema and
its integration into the real guest command. It does not establish positive
sensor readings, hardware alarm interpretation, cooling behavior, or physical-
hardware accuracy.
